"""CPU tests for the interchange boundary; no network, checkpoint, or renderer."""

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import threading
from types import SimpleNamespace
import unittest

import numpy as np
from scipy.spatial.transform import Rotation

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("unisharp_worker", ROOT / "nuke" / "worker.py")
worker = importlib.util.module_from_spec(spec)
spec.loader.exec_module(worker)


class CoordinateTests(unittest.TestCase):
    def test_covariance_and_projection_survive_coordinate_change(self):
        rng = np.random.default_rng(581)
        centres = rng.normal(size=(20, 3)).astype(np.float32)
        centres[:, 2] = abs(centres[:, 2]) + 1
        rotations = Rotation.random(20, random_state=rng)
        q = rotations.as_quat()[:, [3, 0, 1, 2]]
        xyz_nuke, q_nuke = worker.opencv_to_nuke(centres, q)
        r_nuke = Rotation.from_quat(q_nuke[:, [1, 2, 3, 0]]).as_matrix()
        basis = np.diag([1.0, -1.0, -1.0])
        np.testing.assert_allclose(r_nuke, basis @ rotations.as_matrix(), atol=3e-7)
        scales_squared = np.diag([0.01, 0.04, 0.09])
        cov = rotations.as_matrix() @ scales_squared @ rotations.as_matrix().transpose(0, 2, 1)
        cov_nuke = r_nuke @ scales_squared @ r_nuke.transpose(0, 2, 1)
        np.testing.assert_allclose(cov_nuke, basis @ cov @ basis.T, atol=3e-8)
        np.testing.assert_allclose(xyz_nuke[:, 0] / -xyz_nuke[:, 2], centres[:, 0] / centres[:, 2])
        np.testing.assert_allclose(-xyz_nuke[:, 1] / -xyz_nuke[:, 2], centres[:, 1] / centres[:, 2])

    def test_invalid_quaternion_rejected(self):
        with self.assertRaises(ValueError):
            worker.opencv_to_nuke(np.zeros((1, 3)), np.zeros((1, 4)))

    def test_camera_retains_integer_pixel_centres(self):
        k = np.array([[800, 0, 399.5], [0, 800, 299.5], [0, 0, 1]])
        camera = worker.camera_metadata(k, 800, 600)
        self.assertEqual(camera["focal_length_mm"], 36.0)
        self.assertEqual(camera["cx"], 399.5)
        self.assertEqual(camera["pixel_centres"], "integer_top_left")
        json.dumps(camera, allow_nan=False)


class PlyTests(unittest.TestCase):
    def test_binary_ply_attributes_roundtrip(self):
        gaussians = SimpleNamespace(
            mean_vectors=np.array([[[1., 2., 3.], [0., 0., 1.]]]),
            quaternions=np.array([[[1., 0., 0., 0.], [1., 0., 0., 0.]]]),
            singular_values=np.array([[[0.01, 0.02, 0.03], [0.1, 0.2, 0.3]]]),
            colors=np.array([[[0.02, 0.18, 0.8], [0., 0.5, 1.]]]),
            opacities=np.array([[0.25, 0.9]]))
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "gaussians.ply"
            self.assertEqual(worker.write_gaussian_ply(path, gaussians), 2)
            raw = path.read_bytes()
        header, binary = raw.split(b"end_header\n", 1)
        names = [line.split()[-1].decode() for line in header.splitlines() if line.startswith(b"property float ")]
        rows = np.frombuffer(binary, dtype=[(name, "<f4") for name in names])
        self.assertEqual(len(rows), 2)
        self.assertEqual(sum(line.startswith(b"element ") for line in header.splitlines()), 1)
        self.assertEqual(float(rows["y"][0]), -2.)
        self.assertEqual(float(rows["z"][0]), -3.)
        np.testing.assert_allclose(np.column_stack([rows[f"f_dc_{i}"] for i in range(3)]) * worker.SH_C0 + 0.5,
                                   gaussians.colors[0], atol=1e-7)
        np.testing.assert_allclose(np.exp(np.column_stack([rows[f"scale_{i}"] for i in range(3)])),
                                   gaussians.singular_values[0], atol=1e-7)
        np.testing.assert_allclose(1.0 / (1 + np.exp(-rows["opacity"])), gaussians.opacities[0], atol=1e-7)
        np.testing.assert_allclose(np.column_stack([rows[f"rot_{i}"] for i in range(4)]), [[0, 1, 0, 0], [0, 1, 0, 0]])


class ProgressTests(unittest.TestCase):
    @unittest.skipUnless(os.name == "nt", "Windows denies replacement while a reader holds the file open.")
    def test_windows_ui_read_lock_does_not_abort_worker(self):
        with tempfile.TemporaryDirectory() as folder:
            progress = worker.ProgressReporter(Path(folder))
            progress.update("preparing", "Ready")
            # Reproduce Nuke polling during an atomic writer replacement.
            held = progress.path.open("rb")
            release = threading.Timer(0.05, held.close)
            release.start()
            try:
                progress.update("loading_model", "Loading")
            finally:
                release.join()
                held.close()
            self.assertEqual(json.loads(progress.path.read_text())["stage"], "loading_model")
            # A reader holding the file unusually long may skip telemetry,
            # but cannot kill an expensive inference in progress.
            with progress.path.open("rb"):
                with self.assertLogs(worker.LOGGER, level="WARNING"):
                    progress.update("inferring_gaussians", "Inferring")
            progress.update("complete", "Finished")
            self.assertEqual(json.loads(progress.path.read_text())["stage"], "complete")
            self.assertFalse(list(Path(folder).glob("*.tmp")))

    def test_cli_failure_publishes_readable_terminal_progress(self):
        # Deliberately fail before any model or CUDA work. The Nuke UI must
        # receive the failure stage even when no inference result exists yet.
        with tempfile.TemporaryDirectory() as folder:
            completed = subprocess.run(
                [sys.executable, str(ROOT / "nuke" / "worker.py"),
                 "--image", str(Path(folder) / "missing.png"),
                 "--checkpoint", str(Path(folder) / "missing.pt"),
                 "--output-dir", folder, "--device", "cpu"],
                capture_output=True, text=True, timeout=60,
            )
            self.assertEqual(completed.returncode, 1)
            progress = json.loads((Path(folder) / "progress.json").read_text(encoding="utf-8"))
            result = json.loads((Path(folder) / "result.json").read_text(encoding="utf-8"))
            self.assertEqual(progress["stage"], "error")
            self.assertEqual(progress["failed_stage"], "preparing")
            self.assertEqual(result["failed_stage"], "preparing")
            self.assertEqual(progress["error_type"], "FileNotFoundError")
            self.assertGreaterEqual(progress["elapsed_seconds"], 0)
            self.assertIsNone(result["cuda_memory"])
            self.assertFalse((Path(folder) / "gaussians.ply").exists())
            self.assertFalse(list(Path(folder).glob("*.tmp")))


if __name__ == "__main__":
    unittest.main()
