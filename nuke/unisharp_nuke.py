"""UniSHARP controller for Nuke 17; ML always runs in a separate interpreter."""
from __future__ import annotations

import datetime
import json
import math
import os
from pathlib import Path
import subprocess
import time
import uuid

PLUGIN_DIR = Path(__file__).resolve().parent
REPO_ROOT = PLUGIN_DIR.parent
_JOBS = {}
_MENU_REGISTERED = False
VERSION = "1.1.4"


def _nuke():
    import nuke
    return nuke


def _timeline_frame():
    # nuke.frame() can be the stale evaluation frame inside a PyScript callback.
    return int(_nuke().root()["frame"].value())


def _make_node(kind, name, **kwargs):
    node = getattr(_nuke().nodes, kind)(**kwargs)
    # Factory name= permits duplicate names in Nuke 17 GUI.
    node.setName(name, uncollide=True)
    return node


def _config():
    path = PLUGIN_DIR / "config.json"
    return json.loads(path.read_text(encoding="utf-8-sig")) if path.is_file() else {}


def _path(value):
    return str(Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve())


def worker_environment(output_dir):
    env = dict(os.environ)
    for name in list(env):
        if name.upper() in {"PYTHONHOME", "PYTHONPATH", "PYTHONSTARTUP", "NUKE_PATH",
                            "QT_PLUGIN_PATH", "QT_QPA_PLATFORM_PLUGIN_PATH"}:
            env.pop(name, None)
    # Nuke's Python/Qt DLL directories must not be inherited by the ML runtime.
    key = next((k for k in env if k.upper() == "PATH"), "PATH")
    env[key] = os.pathsep.join(p for p in env.get(key, "").split(os.pathsep)
                              if not any(part.lower().startswith("nuke") for part in Path(p).parts))
    env.update(PYTHONUTF8="1", PYTHONIOENCODING="utf-8", PYTHONNOUSERSITE="1",
               PYTHONDONTWRITEBYTECODE="1", MPLCONFIGDIR=str(Path(output_dir) / ".matplotlib"),
               HF_HOME=str(REPO_ROOT / "checkpoints" / "huggingface"),
               TORCH_HOME=str(REPO_ROOT / "checkpoints" / "torchhub"))
    return env


def _status(node, text):
    node["us_status"].setValue(str(text))


def _message(text):
    nuke = _nuke()
    if nuke.GUI:
        nuke.message(str(text))
    else:
        nuke.tprint(str(text))


def _button(name, label, function, tip=""):
    knob = _nuke().PyScript_Knob(name, label)
    knob.setCommand("import unisharp_nuke; unisharp_nuke.%s(nuke.thisNode())" % function)
    knob.setTooltip(tip)
    return knob


def _hidden_string(node, name):
    if name not in node.knobs():
        knob = _nuke().String_Knob(name)
        knob.setVisible(False)
        node.addKnob(knob)


def ensure_controls(node, new=False):
    """Extend saved version-1 controllers without changing their stored frame."""
    nuke = _nuke()
    if "us_follow_frame" not in node.knobs():
        if not new:
            node.addKnob(nuke.Tab_Knob("us_workflow", "Workflow"))
        node.addKnob(nuke.Boolean_Knob("us_follow_frame", "Use timeline frame when generating"))
        node["us_follow_frame"].setValue(bool(new))
        node.addKnob(nuke.Boolean_Knob("us_auto_view", "Show result in Viewer when complete"))
        node["us_auto_view"].setValue(True)
        node.addKnob(_button("us_show", "Show Result", "show_result"))
        node.addKnob(_button("us_log", "Open Last Log", "open_log"))
        node.addKnob(nuke.String_Knob("us_stats", "Last result"))
        node["us_stats"].setEnabled(False)
        node["knobChanged"].setValue("import unisharp_nuke; unisharp_nuke.controls_changed(nuke.thisNode())")
    for name in ("us_last_run", "us_scene_manifest"):
        _hidden_string(node, name)
    for name in ("us_generate", "us_follow_frame", "us_auto_view", "us_show"):
        node[name].setFlag(nuke.STARTLINE)
    node["us_build"].setLabel("Build / Show Scene")
    node["us_resolution"].setLabel("AI image edge")
    node["us_resolution"].setTooltip("Processing resolution only; the generated renderer keeps the source image size. Higher settings use more GPU memory.")
    controls_changed(node)
    if not any(job["node"] == node for job in _JOBS.values()):
        node["us_cancel"].setEnabled(False)


def controls_changed(node):
    if "us_follow_frame" in node.knobs():
        follow = node["us_follow_frame"].value()
        busy = any(job["node"] == node for job in _JOBS.values())
        node["us_frame"].setEnabled(not follow and not busy)
        node["us_current"].setEnabled(not follow and not busy)


def _set_busy(node, busy):
    for name in ("us_generate", "us_check", "us_build", "us_resolution", "us_follow_frame",
                 "us_output", "us_python", "us_checkpoint", "us_device", "us_result"):
        if name in node.knobs():
            node[name].setEnabled(not busy)
    node["us_cancel"].setEnabled(busy)
    controls_changed(node)


def create_node():
    nuke = _nuke()
    if nuke.NUKE_VERSION_MAJOR < 17:
        raise RuntimeError("UniSHARP requires Nuke 17 or newer.")
    cfg = _config()
    selected = nuke.selectedNodes()
    source = selected[-1] if selected else None
    node = _make_node("NoOp", "UniSHARP", label="Image to Gaussian Splat", tile_color=0x397E95FF)
    if source:
        node.setInput(0, source)
        node.setXYpos(source.xpos(), source.ypos() + 90)
    node.addKnob(nuke.Tab_Knob("UniSHARP"))
    node.addKnob(nuke.Text_Knob("us_help", "", "Connect an image, then Generate. Creates a 3D splat, camera and render."))
    node.addKnob(nuke.Int_Knob("us_frame", "Source frame"))
    node["us_frame"].setValue(_timeline_frame())
    node.addKnob(_button("us_current", "Use current frame", "use_current_frame"))
    node.addKnob(nuke.Enumeration_Knob("us_resolution", "Max image edge", ["512", "768", "1024"]))
    node["us_resolution"].setTooltip("512 is the starting setting for an 8 GB GPU. One still image is reconstructed per run.")
    node.addKnob(nuke.File_Knob("us_output", "Output folder"))
    node["us_output"].setValue(str(cfg.get("output_root", REPO_ROOT / "outputs" / "nuke")).replace("\\", "/"))
    node.addKnob(_button("us_generate", "Generate Gaussian", "generate"))
    node.addKnob(_button("us_cancel", "Cancel", "cancel"))
    node.addKnob(_button("us_build", "Build Nuke Scene", "build_scene_button"))
    node.addKnob(_button("us_open", "Open Output", "open_output"))
    node.addKnob(nuke.String_Knob("us_status", "Status"))
    node["us_status"].setEnabled(False)
    _status(node, "Ready")
    node.addKnob(nuke.File_Knob("us_result", "Result JSON"))
    ensure_controls(node, new=True)
    node.addKnob(nuke.Tab_Knob("us_settings", "Settings"))
    node.addKnob(nuke.File_Knob("us_python", "Python executable"))
    node["us_python"].setValue(str(cfg.get("python_exe", "")).replace("\\", "/"))
    node.addKnob(nuke.File_Knob("us_checkpoint", "Model checkpoint"))
    node["us_checkpoint"].setValue(str(cfg.get("checkpoint", REPO_ROOT / "checkpoints" / "pretained_model.pt")).replace("\\", "/"))
    node.addKnob(nuke.Enumeration_Knob("us_device", "Compute", ["cuda", "cpu"]))
    node.addKnob(_button("us_check", "Check Environment", "check_environment"))
    node.addKnob(nuke.Text_Knob("us_note", "", "Perspective images only. Alpha is ignored. Input is exported as sRGB PNG.\nThe controller passes through the input; SplatRender is the generated image output."))
    if nuke.GUI:
        node.showControlPanel()
    return node


def use_current_frame(node):
    node["us_frame"].setValue(_timeline_frame())


def export_input(node, destination):
    """Capture exactly the connected frame through Nuke's color-managed Write."""
    nuke = _nuke()
    source = node.input(0)
    if source is None:
        raise ValueError("Connect an image to the UniSHARP node first.")
    parent = node.parent()
    old_proxy = nuke.root()["proxy"].value()
    old_frame = _timeline_frame()
    frame = int(node["us_frame"].value())
    writer = None
    try:
        nuke.root()["proxy"].setValue(False)
        nuke.root().setFrame(frame)
        width, height = source.width(), source.height()
        if width < 2 or height < 2:
            raise ValueError("Input has an invalid image format.")
        if abs(source.pixelAspect() - 1.0) > 1e-6:
            raise ValueError("Reformat the input to square pixels (pixel aspect 1) before UniSHARP.")
        with parent:
            writer = _make_node("Write", "UniSHARP_TemporaryExport", inputs=[source])
            writer["file"].setValue(str(destination).replace("\\", "/"))
            writer["file_type"].setValue("png")
            writer["channels"].setValue("rgb")
            if "datatype" in writer.knobs():
                writer["datatype"].setValue("8 bit")
            if "raw" in writer.knobs():
                writer["raw"].setValue(False)
            colors = [c.split("\t", 1)[0] for c in writer["colorspace"].values()]
            choice = next((c for c in ("sRGB", "sRGB Encoded Rec.709 (sRGB)", "sRGB - Texture", "Utility - sRGB - Texture", "Output - sRGB") if c in colors), None)
            if choice is None and "texture_paint" in colors:
                choice = "texture_paint"
            if choice is None:
                choice = next((c for c in colors if "srgb" in c.lower() and "texture" in c.lower()), None)
            if choice is None:
                raise ValueError("The current color config has no sRGB texture output. Add an sRGB colorspace before generating.")
            writer["colorspace"].setValue(choice)
            nuke.execute(writer, frame, frame)
    finally:
        if writer is not None:
            nuke.delete(writer)
        nuke.root()["proxy"].setValue(old_proxy)
        nuke.root().setFrame(old_frame)
    return {"width": width, "height": height, "frame": int(node["us_frame"].value())}


def _validated_paths(node):
    python = _path(node["us_python"].value())
    checkpoint = _path(node["us_checkpoint"].value())
    if not Path(python).is_file():
        raise ValueError("Python runtime not found. Run Install_Nuke17.bat or set Python executable in Settings.")
    if not Path(checkpoint).is_file():
        raise ValueError("Model checkpoint not found. Run the checkpoint download command in README.md.")
    return python, checkpoint


def start_job(node, check=False):
    nuke = _nuke()
    ensure_controls(node)
    if _JOBS:
        raise RuntimeError("A UniSHARP job is already running. Wait for it or cancel it first.")
    python, checkpoint = _validated_paths(node)
    if not check and node["us_follow_frame"].value():
        use_current_frame(node)
    if not node["us_output"].value().strip():
        raise ValueError("Choose an output folder first.")
    output = Path(_path(node["us_output"].value()))
    stamp = datetime.datetime.now().strftime("%Y%m%d_%H%M%S")
    run_dir = output / ("%s_f%s_%s_%s" % (node.name(), int(node["us_frame"].value()), stamp, uuid.uuid4().hex[:6]))
    run_dir.mkdir(parents=True, exist_ok=False)
    node["us_last_run"].setValue(run_dir.as_posix())
    command = [python, "-u", str(PLUGIN_DIR / "worker.py"), "--checkpoint", checkpoint,
               "--device", node["us_device"].value()]
    if check:
        command += ["--check"]
    else:
        _status(node, "Exporting source frame...")
        source = export_input(node, run_dir / "input.png")
        source["controller"] = node.fullName()
        (run_dir / "nuke_source.json").write_text(json.dumps(source, indent=2), encoding="utf-8")
        command += ["--image", str(run_dir / "input.png"), "--output-dir", str(run_dir),
                    "--max-long-edge", node["us_resolution"].value()]
    log_path = run_dir / "worker.log"
    with log_path.open("wb") as log:
        process = subprocess.Popen(command, cwd=str(REPO_ROOT), env=worker_environment(run_dir),
                                   stdout=log, stderr=subprocess.STDOUT,
                                   creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    token = uuid.uuid4().hex
    job = {"node": node, "process": process, "directory": run_dir, "check": check,
           "cancelled": False, "log": log_path, "started": time.monotonic()}
    _JOBS[token] = job
    _set_busy(node, True)
    _status(node, "Checking environment..." if check else "Loading model / generating... (see worker.log)")
    if nuke.GUI:
        from PySide6 import QtCore
        timer = QtCore.QTimer()
        timer.setInterval(750)
        timer.timeout.connect(lambda: poll_job(token))
        job["timer"] = timer
        timer.start()
    return token


def generate(node):
    try:
        return start_job(node)
    except Exception as exc:
        _status(node, "Failed: " + str(exc))
        _message(exc)


def check_environment(node):
    try:
        return start_job(node, check=True)
    except Exception as exc:
        _status(node, "Failed: " + str(exc))
        _message(exc)


def cancel(node):
    for job in list(_JOBS.values()):
        if job["node"] == node:
            job["cancelled"] = True
            job["process"].terminate()
            _status(node, "Cancelling...")


def poll_job(token):
    job = _JOBS.get(token)
    if job is None:
        return None
    if job["process"].poll() is None:
        if not job["cancelled"]:
            try:
                progress_path = job["directory"] / "progress.json"
                progress = json.loads(progress_path.read_text(encoding="utf-8")) if progress_path.is_file() else {}
                message = {"preparing": "Preparing image", "loading_model": "Loading model",
                           "fitting_camera": "Estimating camera", "inferring_gaussians": "Generating splats",
                           "saving": "Saving result", "complete": "Finishing"}.get(
                               progress.get("stage"), "Starting Python / loading model")
                dims = ""
                if progress.get("processed_width"):
                    dims = " | AI %sx%s" % (progress["processed_width"], progress["processed_height"])
                _status(job["node"], "%s | %.0fs%s" % (message, time.monotonic() - job["started"], dims))
            except (OSError, ValueError, RuntimeError, KeyError):
                pass
        return None
    if "timer" in job:
        job["timer"].stop()
        job["timer"].deleteLater()
    _JOBS.pop(token, None)
    node = job["node"]
    try:
        # Deleted nodes / a closed script must not create nodes in a new script.
        if not _nuke().exists(node.fullName()):
            return False
        _set_busy(node, False)
        if job["cancelled"]:
            _status(node, "Cancelled; source and log retained")
            return False
        if job["process"].returncode:
            tail = job["log"].read_text(encoding="utf-8", errors="replace")[-1800:]
            if "out of memory" in tail.lower():
                raise RuntimeError("GPU memory is full. Lower AI image edge to 768 or 512 and try again. Open Last Log shows the failed run.")
            raise RuntimeError("Worker failed. Log: %s\n\n%s" % (job["log"], tail))
        if job["check"]:
            _status(node, "Environment check passed (model inference not run)")
            return True
        result = job["directory"] / "result.json"
        if not result.is_file():
            raise RuntimeError("Worker finished without result.json. See " + str(job["log"]))
        node["us_result"].setValue(result.as_posix())
        scene = build_scene(node)
        data = json.loads(result.read_text(encoding="utf-8"))
        node["us_stats"].setValue("Input %sx%s | AI %sx%s | %s splats | %.1fs" % (
            data["source"]["width"], data["source"]["height"], data["camera"]["width"],
            data["camera"]["height"], format(data["gaussian_count"], ","), data["seconds"]))
        _status(node, "Complete: " + node["us_stats"].value())
        if _nuke().GUI and node["us_auto_view"].value():
            try:
                _show_scene(node, scene)
            except Exception as exc:
                _nuke().tprint("UniSHARP result is ready, but opening the Viewer failed: " + str(exc))
        return True
    except Exception as exc:
        try:
            _status(node, "Failed: " + str(exc).splitlines()[0])
        except Exception:
            pass
        _message(exc)
        return False


def camera_values(camera, output_width=None, output_height=None):
    """Convert integer-center intrinsics to Nuke 17 SplatRender camera values.

    Native raster tests confirm the splat renderer's integer pixel convention;
    both window translations are normalized to half the image WIDTH.
    """
    w, h = float(camera["width"]), float(camera["height"])
    fx, fy = float(camera["fx"]), float(camera["fy"])
    cx, cy = float(camera["cx"]), float(camera["cy"])
    if not all(math.isfinite(v) for v in (w, h, fx, fy, cx, cy)) or min(w, h, fx, fy) <= 0:
        raise ValueError("Invalid camera intrinsics in result JSON.")
    if output_width is not None and output_height is not None:
        ow, oh = float(output_width), float(output_height)
        if not math.isfinite(ow + oh) or min(ow, oh) <= 0:
            raise ValueError("Invalid output image dimensions.")
        sx, sy = ow / w, oh / h
        fx, fy = fx * sx, fy * sy
        cx, cy = (cx + .5) * sx - .5, (cy + .5) * sy - .5
        w, h = ow, oh
    focal = 50.0
    ha, va = focal * w / fx, focal * h / fy
    return {"focal": focal, "haperture": ha, "vaperture": va,
            "win_x": (w / 2 - cx) / (w / 2),
            "win_y": (cy + 1 - h / 2) / (w / 2)}


def _working_space_output(render, made):
    nuke = _nuke()
    if nuke.root()["colorManagement"].value() != "OCIO":
        return render
    conversion = _make_node("OCIOColorSpace", "UniSHARP_WorkingSpace", inputs=[render])
    made.append(conversion)
    choices = [c.split("\t", 1)[0] for c in conversion["in_colorspace"].values()]
    linear = next((c for c in ("Linear Rec.709 (sRGB)", "Utility - Linear - sRGB", "lin_rec709_scene", "linear") if c in choices), None)
    if linear is None:
        raise ValueError("Current OCIO config has no Linear Rec.709 (sRGB) color space for the generated splats.")
    conversion["in_colorspace"].setValue(linear)
    conversion["out_colorspace"].setValue(nuke.root()["workingSpaceLUT"].value())
    conversion["label"].setValue("Linear Rec.709 to project working space")
    return conversion


def build_scene(node):
    nuke = _nuke()
    ensure_controls(node)
    result_path = Path(_path(node["us_result"].value()))
    if not result_path.is_file():
        raise ValueError("Generate first, or select an existing result.json.")
    data = json.loads(result_path.read_text(encoding="utf-8"))
    if data.get("status") != "complete" or data.get("schema_version") != 1:
        raise ValueError("Unsupported or incomplete UniSHARP result.")
    ply = Path(data["ply"])
    if not ply.is_file():
        raise ValueError("Gaussian PLY file is missing: " + str(ply))
    existing = _existing_scene(node, result_path)
    if existing:
        return existing
    source = data.get("source", {})
    width, height = int(source.get("width", data["camera"]["width"])), int(source.get("height", data["camera"]["height"]))
    values = camera_values(data["camera"], width, height)
    made = []
    try:
        with node.parent():
            geo = _make_node("GeoReference", "UniSHARP_Gaussians")
            made.append(geo)
            geo["file_path"].setValue(ply.as_posix())
            camera = _make_node("Camera4", "UniSHARP_Camera")
            made.append(camera)
            camera["read_from_scene"].setValue(False)
            camera["focal"].setValue(values["focal"])
            camera["haperture"].setValue(values["haperture"])
            camera["vaperture"].setValue(values["vaperture"])
            camera["win_translate"].setValue(values["win_x"], 0)
            camera["win_translate"].setValue(values["win_y"], 1)
            camera["near"].setValue(0.001)
            color = _make_node("GeoColorSpace", "UniSHARP_LinearRGB", inputs=[geo])
            made.append(color)
            color["colorspace"].setValue("lin_rec709_scene")
            render = _make_node("SplatRender", "UniSHARP_Render")
            made.append(render)
            render.setInput(2, color)
            render.setInput(1, camera)
            fmt = nuke.addFormat("%d %d 1 UniSHARP_%dx%d" % (width, height, width, height))
            background = _make_node("Constant", "UniSHARP_Format")
            made.append(background)
            background["format"].setValue(fmt)
            background["color"].setValue([0, 0, 0, 0])
            render.setInput(0, background)
            render["output_colorspace"].setValue("lin_rec709_scene")
            output = _working_space_output(render, made)
            geo.setXYpos(node.xpos() + 160, node.ypos() + 90)
            color.setXYpos(node.xpos() + 160, node.ypos() + 160)
            camera.setXYpos(node.xpos() + 370, node.ypos() + 90)
            background.setXYpos(node.xpos() + 20, node.ypos() + 240)
            render.setXYpos(node.xpos() + 160, node.ypos() + 310)
            if output is not render:
                output.setXYpos(render.xpos(), render.ypos() + 70)
            source_info = result_path.parent / "nuke_source.json"
            frame_label = json.loads(source_info.read_text(encoding="utf-8")).get("frame") if source_info.is_file() else None
            geo["label"].setValue("UniSHARP" + (" | frame %s" % frame_label if frame_label is not None else ""))
            camera["label"].setValue("Estimated source camera | editable")
            render["label"].setValue("UniSHARP Gaussian render")
    except Exception:
        for item in reversed(made):
            nuke.delete(item)
        raise
    scene = {"geo": geo, "camera": camera, "render": render, "color": color,
             "background": background, "output": output}
    scene_id = uuid.uuid4().hex
    for role, item in scene.items():
        _hidden_string(item, "us_scene_id")
        _hidden_string(item, "us_scene_roles")
        item["us_scene_id"].setValue(scene_id)
        roles = [key for key, value in scene.items() if value == item]
        item["us_scene_roles"].setValue(json.dumps(roles))
    # Nuke evaluates backslash escapes in String_Knob.value() after .nk reload.
    node["us_scene_manifest"].setValue(json.dumps({"id": scene_id, "result": result_path.as_posix()}))
    _status(node, "Scene created")
    return scene


def _existing_scene(node, result_path):
    try:
        knob = node["us_scene_manifest"]
        try:
            manifest = json.loads(knob.value())
        except ValueError:
            manifest = json.loads(knob.toScript())
        if Path(manifest["result"]).resolve() != result_path.resolve():
            return None
        if "\\" in manifest["result"]:
            manifest["result"] = Path(manifest["result"]).as_posix()
            knob.setValue(json.dumps(manifest))
        scene = {}
        for item in _nuke().allNodes(group=node.parent()):
            if item.knob("us_scene_id") and item["us_scene_id"].value() == manifest["id"]:
                for role in json.loads(item["us_scene_roles"].value()):
                    if role in scene:
                        return None  # A copied graph is ambiguous; never edit it.
                    scene[role] = item
        return scene if {"geo", "camera", "render", "color", "background", "output"} <= set(scene) else None
    except (ValueError, KeyError, TypeError):
        return None


def _show_scene(node, scene):
    nuke = _nuke()
    if not nuke.GUI:
        return
    with node.parent():
        active = nuke.activeViewer()
        viewer = active.node() if active is not None and active.node().parent() == node.parent() else None
        if viewer is None:
            viewer = _make_node("Viewer", "UniSHARP_Viewer")
        viewer.setInput(0, scene["output"])
        # Remove the source connection made by the previous automatic A/B mode.
        if node.input(0) is not None and viewer.input(1) == node.input(0):
            viewer.setInput(1, None)
        active = nuke.activeViewer()
        if active is not None and active.node() == viewer:
            active.activateInput(0)


def show_result(node):
    try:
        scene = build_scene(node)
        _show_scene(node, scene)
        return scene
    except Exception as exc:
        _message(exc)


def build_scene_button(node):
    try:
        return show_result(node)
    except Exception as exc:
        _status(node, "Failed to build scene")
        _message(exc)


def open_output(node):
    ensure_controls(node)
    result = node["us_result"].value()
    last = node["us_last_run"].value()
    path = Path(_path(last)) if last else (Path(_path(result)).parent if result else Path(_path(node["us_output"].value())))
    if path.is_dir():
        os.startfile(str(path))
    else:
        _message("Output folder does not exist yet.")


def open_log(node):
    ensure_controls(node)
    folder = node["us_last_run"].value()
    if not folder and node["us_result"].value():
        folder = str(Path(_path(node["us_result"].value())).parent)
    path = Path(_path(folder)) / "worker.log" if folder else None
    if path is not None and path.is_file():
        os.startfile(str(path))
    else:
        _message("No worker log yet. Generate an image first.")


def register_menu():
    global _MENU_REGISTERED
    nuke = _nuke()
    if not _MENU_REGISTERED and nuke.GUI and nuke.NUKE_VERSION_MAJOR >= 17:
        menu = nuke.menu("Nodes")
        if menu.findItem("UniSHARP/UniSHARP (Nuke 17)") is None:
            menu.addCommand("UniSHARP/UniSHARP (Nuke 17)", create_node)
        if menu.findItem("UniSHARP/Reload Plugin") is None:
            menu.addCommand("UniSHARP/Reload Plugin", reload_plugin)
        _MENU_REGISTERED = True


def reload_plugin():
    if _JOBS:
        _message("Wait for the running UniSHARP job before reloading.")
        return
    import importlib
    import sys
    module = importlib.reload(sys.modules[__name__])
    module.register_menu()
    for node in _nuke().allNodes("NoOp", recurseGroups=True):
        if node.knob("us_generate"):
            module.ensure_controls(node)
    _nuke().tprint("UniSHARP plugin reloaded: " + VERSION)
