"""Perspective UniSHARP inference in a separate Python process (never Nuke Python).

Only inference dependencies are imported; no Blender, gsplat rasterizer, trainer,
browser, or custom CUDA extension is needed. Input is display-referred sRGB RGB.
The PLY contains *linear RGB* SH0 values for Nuke's linear compositing pipeline.

Coordinate convention: UniSHARP/OpenCV is +X right, +Y down, +Z forward.
PLY is +X right, +Y up, -Z forward, in predicted metres. Apply R=diag(1,-1,-1)
to centres and left-multiply scalar-first quaternions by qR=(0,1,0,0).
Camera intrinsics use integer pixel centres: top-left pixel centre is (0,0).
The Nuke controller maps these directly to the native SplatRender projection.
"""

from __future__ import annotations

import argparse
import dataclasses
import gc
import json
import logging
import math
import os
from pathlib import Path
import sys
import time
import traceback
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT))
sys.path.insert(0, str(REPO_ROOT / "UniK3D"))
LOGGER = logging.getLogger("unisharp_nuke")
SH_C0 = math.sqrt(1.0 / (4.0 * math.pi))


def _atomic_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(json.dumps(value, ensure_ascii=False, indent=2, allow_nan=False) + "\n", encoding="utf-8")
    for attempt in range(10):
        try:
            temporary.replace(path)
            return
        except PermissionError:
            # Windows readers can briefly prevent replacing an otherwise
            # writable file. Nuke polls progress with short-lived reads.
            if attempt == 9:
                raise
            time.sleep(0.025)


class ProgressReporter:
    """Small, atomic stage snapshots readable while the worker is busy."""

    def __init__(self, output_dir: Path | None) -> None:
        self.path = output_dir.resolve() / "progress.json" if output_dir else None
        self.started = time.monotonic()
        self.stage = "preparing"
        self.context: dict[str, Any] = {}
        self.torch: Any = None
        self.device: Any = None
        self.cuda_initial: dict[str, int] = {}

    def attach_device(self, torch: Any, device: Any) -> None:
        self.torch, self.device = torch, device
        self.context["device"] = str(device)
        if device.type == "cuda":
            torch.cuda.reset_peak_memory_stats(device)
            free, total = torch.cuda.mem_get_info(device)
            self.cuda_initial = {"free_bytes_at_start": int(free), "total_bytes": int(total)}

    def cuda_memory(self) -> dict[str, int] | None:
        if self.device is None or self.device.type != "cuda":
            return None
        try:
            return {**self.cuda_initial,
                    "allocated_bytes": int(self.torch.cuda.memory_allocated(self.device)),
                    "reserved_bytes": int(self.torch.cuda.memory_reserved(self.device)),
                    "peak_allocated_bytes": int(self.torch.cuda.max_memory_allocated(self.device)),
                    "peak_reserved_bytes": int(self.torch.cuda.max_memory_reserved(self.device))}
        except RuntimeError:
            # Preserve the original inference error if a failed CUDA context
            # also prevents querying its allocator statistics.
            return dict(self.cuda_initial)

    def update(self, stage: str, message: str, **details: Any) -> dict[str, Any]:
        self.stage = stage
        self.context.update(details)
        value = {"schema_version": 1, "stage": stage, "message": message,
                 "elapsed_seconds": round(time.monotonic() - self.started, 3),
                 **self.context, "cuda_memory": self.cuda_memory()}
        if self.path is not None:
            try:
                _atomic_json(self.path, value)
            except OSError as exc:
                # A UI telemetry file must not turn a successful inference
                # into an error. The next stage retries; result.json is strict.
                LOGGER.warning("Could not update progress file: %s", exc)
        LOGGER.info("%s", message)
        return value


def _backbone_metadata(model: Any, output: dict[str, Any]) -> dict[str, Any]:
    """Report the observed backbone grid rather than equating it to PLY size."""
    backbone = model.feature_extractor.unik3d
    constraints = backbone.shape_constraints
    lower, upper = float(constraints["pixels_min"]), float(constraints["pixels_max"])
    level = int(backbone.resolution_level)
    interval = (upper - lower) / 10.0
    internal = output.get("_unisharp_internal_rays")
    if internal is None:
        raise RuntimeError("UniK3D did not report its internal prediction grid.")
    height, width = (int(value) for value in internal.shape[-2:])
    return {"resolution_level": level, "width": width, "height": height,
            "pixels_min": lower + level * interval,
            "pixels_max": lower + (level + 1) * interval,
            "description": "Backbone features are resized to the processed image grid before Gaussian prediction."}


def _runtime() -> tuple[Any, Any, Any, Any]:
    """Delay imports so --help and Nuke-side discovery do not import torch."""
    import numpy as np
    import torch
    from PIL import Image, ImageOps

    cache = REPO_ROOT / "checkpoints" / "torchhub"
    cache.mkdir(parents=True, exist_ok=True)
    os.environ["TORCH_HOME"] = str(cache)
    torch.hub.set_dir(str(cache))
    return np, torch, Image, ImageOps


def opencv_to_nuke(means: Any, quaternions: Any) -> tuple[Any, Any]:
    """Rotate centres and ellipsoid frames without changing singular values."""
    import numpy as np

    means = np.asarray(means, dtype=np.float32)
    q = np.asarray(quaternions, dtype=np.float32)
    if means.ndim != 2 or means.shape[1] != 3 or q.shape != (len(means), 4):
        raise ValueError("Expected centres (N,3) and scalar-first quaternions (N,4).")
    lengths = np.linalg.norm(q, axis=1, keepdims=True)
    if not np.isfinite(means).all() or not np.isfinite(q).all() or np.any(lengths < 1e-12):
        raise ValueError("Non-finite centres or invalid Gaussian quaternion.")
    q = q / lengths
    converted_q = np.column_stack((-q[:, 1], q[:, 0], -q[:, 3], q[:, 2]))
    return means * np.array([1.0, -1.0, -1.0], dtype=np.float32), converted_q


def write_gaussian_ply(path: Path, gaussians: Any) -> int:
    """Write binary vertex-only 3DGS PLY; omit Apple's extra PLY elements."""
    import numpy as np

    def array(value: Any, columns: int) -> Any:
        if hasattr(value, "detach"):
            value = value.detach().float().cpu().numpy()
        return np.asarray(value, dtype=np.float32).reshape(-1, columns)

    xyz, rotation = opencv_to_nuke(array(gaussians.mean_vectors, 3), array(gaussians.quaternions, 4))
    scales = array(gaussians.singular_values, 3)
    colors = array(gaussians.colors, 3)
    opacity = array(gaussians.opacities, 1)
    count = len(xyz)
    if count == 0 or any(len(values) != count for values in (scales, colors, opacity)):
        raise ValueError("Empty or inconsistent Gaussian arrays.")
    if any(not np.isfinite(values).all() for values in (scales, colors, opacity)):
        raise ValueError("The model produced non-finite Gaussian attributes.")
    if np.any(scales <= 0.0):
        raise ValueError("The model produced non-positive Gaussian scales.")
    opacity = np.clip(opacity, 1e-6, 1.0 - 1e-6)
    fields = ["x", "y", "z", "nx", "ny", "nz"]
    fields += [f"f_dc_{i}" for i in range(3)]
    fields += ["opacity"] + [f"scale_{i}" for i in range(3)] + [f"rot_{i}" for i in range(4)]
    attributes = np.concatenate(
        (xyz, np.zeros_like(xyz), (colors - 0.5) / SH_C0,
         np.log(opacity) - np.log1p(-opacity), np.log(scales), rotation), axis=1,
    ).astype("<f4", copy=False)
    header = ["ply", "format binary_little_endian 1.0",
              "comment UniSHARP Nuke: +X right +Y up -Z forward; metres",
              "comment color_space linearRGB; SH0 scalar; quaternion wxyz",
              f"element vertex {count}"]
    header += [f"property float {field}" for field in fields]
    header += ["end_header", ""]
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    with temporary.open("wb") as stream:
        stream.write("\n".join(header).encode("ascii"))
        stream.write(attributes.tobytes(order="C"))
    temporary.replace(path)
    return count


def camera_metadata(intrinsics: Any, width: int, height: int) -> dict[str, Any]:
    if hasattr(intrinsics, "detach"):
        intrinsics = intrinsics.detach().float().cpu().numpy()
    import numpy as np

    k = np.asarray(intrinsics).reshape(-1, 3, 3)[0]
    fx, fy, cx, cy = (float(k[0, 0]), float(k[1, 1]), float(k[0, 2]), float(k[1, 2]))
    if min(width, height) < 1 or not all(math.isfinite(v) for v in (fx, fy, cx, cy)) or min(fx, fy) <= 0:
        raise ValueError("Invalid fitted perspective camera intrinsics.")
    return {"model": "perspective", "width": int(width), "height": int(height),
            "fx": fx, "fy": fy, "cx": cx, "cy": cy, "pixel_aspect": 1.0,
            "pixel_centres": "integer_top_left", "coordinate_system": "nuke",
            "axes": {"right": "+X", "up": "+Y", "forward": "-Z"},
            "translate": [0.0, 0.0, 0.0], "rotate": [0.0, 0.0, 0.0],
            "horizontal_aperture_mm": 36.0, "focal_length_mm": 36.0 * fx / width,
            "horizontal_fov_deg": math.degrees(2.0 * math.atan(width / (2.0 * fx)))}


def _config_from_checkpoint(path: Path, payload: dict[str, Any]) -> Any:
    from unisharp.models.unisharp_feature import UnisharpFeatureConfig

    cfg = UnisharpFeatureConfig()
    valid_names = {field.name for field in dataclasses.fields(cfg)}
    values = payload.get("config", {})
    merged = dict(values) if isinstance(values, dict) else {}
    merged.update({key: payload[key] for key in valid_names if key in payload})
    sidecar = path.parent / "config.json"
    if sidecar.is_file():
        data = json.loads(sidecar.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            merged.update({key: value for key, value in data.items() if key in valid_names})
    for key in valid_names:
        if key in merged:
            setattr(cfg, key, merged[key])
    return cfg


def _load_model(checkpoint: Path, device: Any, progress: ProgressReporter | None = None) -> tuple[Any, int]:
    import torch
    from unisharp.models.unisharp_feature import UnisharpFeatureModel

    LOGGER.info("Loading complete UniSHARP checkpoint: %s", checkpoint)
    if progress:
        progress.update("loading_model", "Reading model checkpoint into system memory.", checkpoint=str(checkpoint))
    # Checkpoints are downloaded by the installer from the project's documented
    # source. Loading a user-specified checkpoint requires trusting that file.
    payload = torch.load(checkpoint, map_location="cpu", weights_only=False)
    if not isinstance(payload, dict):
        raise ValueError("Expected a full UniSHARP checkpoint dictionary.")
    cfg = _config_from_checkpoint(checkpoint, payload)
    step = int(payload.get("step", 0))
    if progress:
        progress.update("loading_model", "Constructing UniSHARP and loading all model weights.")
    model = UnisharpFeatureModel(cfg, unik3d_pretrained=False)
    if isinstance(payload.get("feature_extractor"), dict):
        # Use the upstream compatibility mapping for Gaussian/depth head layouts.
        del payload
        gc.collect()
        missing, unexpected = model.load_from_checkpoint(str(checkpoint), strict=False)
    else:
        state = payload.get("state_dict", payload.get("model", payload))
        if not isinstance(state, dict):
            raise ValueError("Checkpoint does not contain full model weights.")
        state = {str(key).removeprefix("module."): value for key, value in state.items()}
        incompatible = model.load_state_dict(state, strict=False)
        missing, unexpected = list(incompatible.missing_keys), list(incompatible.unexpected_keys)
        del state, payload
    # Never produce plausible-looking splats from an untrained encoder or head.
    # Unknown top-level training metadata can safely be ignored.
    unexpected_weights = [name for name in unexpected if not name.startswith("payload.")]
    if missing or unexpected_weights:
        raise RuntimeError("Incomplete/incompatible UniSHARP checkpoint: "
                           f"missing={missing[:20]}, unexpected={unexpected_weights[:20]}")
    model.eval().requires_grad_(False)
    gc.collect()
    if progress:
        progress.update("loading_model", f"Moving the model to {device}.")
    model.to(device)
    LOGGER.info("All model weights loaded; checkpoint step %d", step)
    return model, step


def _device(torch: Any, name: str) -> Any:
    if name == "auto":
        name = "cuda" if torch.cuda.is_available() else "cpu"
    device = torch.device(name)
    if device.type not in {"cpu", "cuda"}:
        raise ValueError("Only CPU or NVIDIA CUDA inference is supported.")
    if device.type == "cuda" and not torch.cuda.is_available():
        raise RuntimeError("CUDA is unavailable in this Python environment. Install the CUDA torch build or select CPU.")
    return device


def check_environment(args: argparse.Namespace, progress: ProgressReporter | None = None) -> dict[str, Any]:
    progress = progress or ProgressReporter(args.output_dir)
    progress.update("preparing", "Checking Python dependencies and compute device.")
    np, torch, Image, _ = _runtime()
    from unisharp.models.unisharp_feature import UnisharpFeatureModel  # noqa: F401
    from unik3d.models import UniK3D  # noqa: F401

    device = _device(torch, args.device)
    progress.attach_device(torch, device)
    # Exercise torch on the selected device, without loading a multi-GB model.
    assert float((torch.ones(1, device=device) + 1).cpu()[0]) == 2.0
    checkpoint = args.checkpoint.resolve() if args.checkpoint else None
    if checkpoint is not None and not checkpoint.is_file():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint}")
    result = {"schema_version": 1, "status": "checked", "python": sys.executable,
              "python_version": sys.version.split()[0], "torch_version": torch.__version__,
              "numpy_version": np.__version__, "pillow_version": Image.__version__,
              "cuda_available": torch.cuda.is_available(), "device": str(device),
              "gpu_name": torch.cuda.get_device_name(device) if device.type == "cuda" else None,
              "checkpoint": str(checkpoint) if checkpoint else None,
              "checkpoint_loaded": False,
              "render_extensions_required": False}
    if args.check_model:
        if checkpoint is None:
            raise ValueError("--check-model requires --checkpoint.")
        model, step = _load_model(checkpoint, device, progress)
        result.update(checkpoint_loaded=True, checkpoint_step=step,
                      parameter_count=sum(p.numel() for p in model.parameters()))
    progress.update("complete", "Environment check completed.", checkpoint_loaded=result["checkpoint_loaded"])
    return result


def infer(args: argparse.Namespace, progress: ProgressReporter | None = None) -> dict[str, Any]:
    progress = progress or ProgressReporter(args.output_dir)
    progress.update("preparing", "Loading Python dependencies and preparing the source image.",
                    max_long_edge=args.max_long_edge, precision="float32")
    np, torch, Image, ImageOps = _runtime()
    from unisharp.utils.rayfit_camera import fit_pinhole_intrinsics_from_rays

    started = time.monotonic()
    if args.image is None or args.output_dir is None or args.checkpoint is None:
        raise ValueError("Inference requires --image, --output-dir, and --checkpoint.")
    image_path, output_dir, checkpoint = (p.resolve() for p in (args.image, args.output_dir, args.checkpoint))
    if not checkpoint.is_file():
        raise FileNotFoundError(f"Checkpoint not found: {checkpoint}")
    if not image_path.is_file():
        raise FileNotFoundError(f"Input image not found: {image_path}")
    if not 32 <= args.max_long_edge <= 4096:
        raise ValueError("--max-long-edge must be between 32 and 4096 pixels.")
    device = _device(torch, args.device)
    progress.attach_device(torch, device)
    with Image.open(image_path) as raw:
        image = ImageOps.exif_transpose(raw).convert("RGB")
    source_width, source_height = image.size
    scale = min(1.0, args.max_long_edge / max(image.size))
    if scale < 1:
        image = image.resize(tuple(max(1, round(v * scale)) for v in image.size), Image.Resampling.BILINEAR)
    width, height = image.size
    if min(width, height) < 4:
        raise ValueError("Image must have at least four pixels on each axis after resizing.")
    output_dir.mkdir(parents=True, exist_ok=True)
    _atomic_json(output_dir / "result.json", {"schema_version": 1, "status": "running"})
    progress.update("preparing", f"Preparing {width} x {height} pixels from the {source_width} x {source_height} source.",
                    source_width=source_width, source_height=source_height,
                    processed_width=width, processed_height=height)
    input_preview = output_dir / "input_srgb.png"
    image.save(input_preview)
    rgb = torch.from_numpy(np.asarray(image, dtype=np.uint8).copy()).permute(2, 0, 1).unsqueeze(0).to(device)
    model, step = _load_model(checkpoint, device, progress)
    LOGGER.info("Fitting perspective camera from %d x %d image", width, height)
    progress.update("fitting_camera", "Estimating the source perspective camera.")
    with torch.inference_mode():
        model.feature_extractor(rgb_u8=rgb, target_h=height, target_w=width, use_predicted_rays=True)
        predicted = model.feature_extractor._unisharp_last_unik3d_output
        if not isinstance(predicted, dict) or not torch.is_tensor(predicted.get("rays")):
            raise RuntimeError("UniK3D did not return camera rays.")
        backbone = _backbone_metadata(model, predicted)
        intrinsics = fit_pinhole_intrinsics_from_rays(predicted["rays"]).float()
        if not args.keep_fitted_pixel_ratio:
            intrinsics[:, 1, 1] = intrinsics[:, 0, 0]
        camera = camera_metadata(intrinsics, width, height)
        # Feature hooks retain tensors. Release the first ray-estimation pass
        # before the calibrated model pass to reduce GPU peak memory.
        del predicted
        _clear_feature_cache(model)
        if device.type == "cuda":
            torch.cuda.empty_cache()
        LOGGER.info("Inferring Gaussian splats on %s", device)
        progress.update("inferring_gaussians", f"Generating Gaussian splats at {width} x {height} pixels.",
                        backbone=backbone)
        gaussians = model(image=rgb.float() / 255.0, image_u8=rgb,
                          camera_intrinsics=intrinsics, camera_model="pinhole",
                          return_aux=False)
        # Read the actual calibrated-pass grid as well; a future model may use
        # different internal sizing depending on supplied camera intrinsics.
        backbone = _backbone_metadata(model, model.feature_extractor._unisharp_last_unik3d_output)
        count = int(gaussians.mean_vectors.numel() // 3)
        # Copy only final splats to RAM and release the model/cached features
        # before the (potentially large) PLY write and Nuke scene construction.
        # This does not change inference precision, resolution or splat values.
        progress.update("saving", f"Transferring {count:,} Gaussian splats to system memory.",
                        gaussian_count=count, backbone=backbone)
        gaussians = gaussians.to(torch.device("cpu"))
        _clear_feature_cache(model)
        del model, rgb, intrinsics
        gc.collect()
        if device.type == "cuda":
            torch.cuda.empty_cache()
        ply = output_dir / "gaussians.ply"
        progress.update("saving", f"Writing {count:,} Gaussian splats to PLY.")
        count = write_gaussian_ply(ply, gaussians)
    result = {"schema_version": 1, "status": "complete", "ply": str(ply),
              "camera": camera, "source": {"image": str(image_path), "width": source_width, "height": source_height},
              "processed": {"width": width, "height": height, "max_long_edge": args.max_long_edge},
              "backbone": backbone, "precision": "float32", "cuda_memory": progress.cuda_memory(),
              "input_preview": str(input_preview), "gaussian_count": count,
              "checkpoint": str(checkpoint), "checkpoint_step": step, "device": str(device),
              "max_long_edge": args.max_long_edge, "color_space": "linearRGB",
              "coordinate_transform": [[1, 0, 0], [0, -1, 0], [0, 0, -1]],
              "seconds": round(time.monotonic() - started, 3)}
    _atomic_json(output_dir / "camera.json", camera)
    _atomic_json(output_dir / "result.json", result)
    LOGGER.info("Saved %d Gaussians to %s", count, ply)
    progress.update("complete", f"Saved {count:,} Gaussian splats.", ply=str(ply), result=str(output_dir / "result.json"))
    return result


def _clear_feature_cache(model: Any) -> None:
    model.feature_extractor._unisharp_last_unik3d_output = None
    for module in model.feature_extractor.unik3d.modules():
        for name in ("_unisharp_last_encoder_output", "_unisharp_last_out_features",
                     "_unisharp_last_init_latents", "_unisharp_last_pred_rays_flat"):
            if hasattr(module, name):
                setattr(module, name, None)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="UniSHARP perspective image to Nuke 17 Gaussian splat PLY.")
    parser.add_argument("--image", type=Path)
    parser.add_argument("--output-dir", type=Path)
    parser.add_argument("--checkpoint", type=Path)
    parser.add_argument("--max-long-edge", type=int, default=768)
    parser.add_argument("--device", default="auto", help="auto, cuda, cuda:0, or cpu")
    parser.add_argument("--keep-fitted-pixel-ratio", action="store_true", help="Preserve independently fitted fx/fy; default forces fy=fx.")
    parser.add_argument("--check", action="store_true", help="Check dependency imports and device; do not load model weights.")
    parser.add_argument("--check-model", action="store_true", help="Also instantiate and validate every checkpoint weight, without inference.")
    return parser


def main() -> int:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s [%(levelname)s] %(message)s")
    args = build_parser().parse_args()
    progress = ProgressReporter(args.output_dir)
    try:
        result = check_environment(args, progress) if args.check or args.check_model else infer(args, progress)
        if (args.check or args.check_model) and args.output_dir:
            _atomic_json(args.output_dir.resolve() / "environment_check.json", result)
        print(json.dumps(result, ensure_ascii=True, allow_nan=False), flush=True)
        return 0
    except Exception as exc:
        error = {"schema_version": 1, "status": "error", "error": str(exc), "error_type": type(exc).__name__,
                 "failed_stage": progress.stage, "cuda_memory": progress.cuda_memory(),
                 "seconds": round(time.monotonic() - progress.started, 3),
                 "max_long_edge": args.max_long_edge}
        for key in ("source_width", "source_height", "processed_width", "processed_height", "backbone", "precision"):
            if key in progress.context:
                error[key] = progress.context[key]
        if "out of memory" in str(exc).lower():
            error["hint"] = "Close GPU-heavy applications and use --max-long-edge 512 (or 384); CPU is a slower fallback."
        LOGGER.error("%s", exc)
        traceback.print_exc()
        if args.output_dir:
            filename = "environment_check.json" if args.check or args.check_model else "result.json"
            _atomic_json(args.output_dir.resolve() / filename, error)
        progress.update("error", str(exc), failed_stage=error["failed_stage"], error_type=error["error_type"])
        print(json.dumps(error, ensure_ascii=True), flush=True)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
