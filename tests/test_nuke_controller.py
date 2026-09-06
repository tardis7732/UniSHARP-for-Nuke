"""Validate the external Python boundary without importing or licensing Nuke."""
import importlib.util
import json
import math
import os
from pathlib import Path
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("unisharp_nuke", ROOT / "nuke" / "unisharp_nuke.py")
controller = importlib.util.module_from_spec(spec)
spec.loader.exec_module(controller)


class WorkerIsolationTests(unittest.TestCase):
    def test_external_python_boots_despite_inherited_nuke_python_settings(self):
        with tempfile.TemporaryDirectory() as temporary:
            folder = Path(temporary)
            inherited = folder / "Nuke17.0v1" / "pythonextensions"
            inherited.mkdir(parents=True)
            (inherited / "nuke_only_marker.py").write_text("VALUE = 1\n", encoding="utf-8")
            clean_tools = folder / "normal_tools"
            initial_path = os.environ.get("PATH", "")
            poison = {"PYTHONHOME": str(inherited), "PYTHONPATH": str(inherited),
                      "NUKE_PATH": str(inherited), "QT_PLUGIN_PATH": str(inherited),
                      "QT_QPA_PLATFORM_PLUGIN_PATH": str(inherited),
                      "PATH": os.pathsep.join([str(inherited), str(clean_tools), initial_path])}
            with patch.dict(os.environ, poison):
                isolated = controller.worker_environment(folder)
                self.assertEqual(os.environ["PYTHONHOME"], str(inherited))
            self.assertNotIn(str(inherited), isolated["PATH"].split(os.pathsep))
            self.assertIn(str(clean_tools), isolated["PATH"].split(os.pathsep))
            code = (
                "import importlib.util,json,os,sys;"
                "print(json.dumps({'prefix':sys.prefix,'marker':importlib.util.find_spec('nuke_only_marker') is not None,"
                "'nuke_path':os.environ.get('NUKE_PATH'),'user_site':sys.flags.no_user_site}))"
            )
            completed = subprocess.run([sys.executable, "-c", code], env=isolated, cwd=ROOT,
                                       capture_output=True, text=True, timeout=30)
            self.assertEqual(completed.returncode, 0, completed.stderr)
            result = json.loads(completed.stdout)
            self.assertEqual(Path(result["prefix"]).resolve(), Path(sys.prefix).resolve())
            self.assertFalse(result["marker"])
            self.assertIsNone(result["nuke_path"])
            self.assertEqual(result["user_site"], 1)


class CameraBoundaryTests(unittest.TestCase):
    def camera(self):
        return {"width": 512, "height": 384, "fx": 320., "fy": 320., "cx": 256., "cy": 192.}

    def test_physical_camera_preserves_horizontal_field_of_view(self):
        result = controller.camera_values(self.camera())
        expected_fov = 2 * math.atan(512 / (2 * 320))
        camera_fov = 2 * math.atan(result["haperture"] / (2 * result["focal"]))
        self.assertAlmostEqual(camera_fov, expected_fov, places=12)
        self.assertTrue(all(math.isfinite(value) for value in result.values()))

    def test_invalid_camera_data_is_rejected_before_native_node_creation(self):
        cases = [("width", 0), ("height", -1), ("fx", 0), ("fy", -20),
                 ("cx", float("nan")), ("cy", float("inf"))]
        for field, value in cases:
            with self.subTest(field=field, value=value):
                camera = self.camera()
                camera[field] = value
                with self.assertRaises(ValueError):
                    controller.camera_values(camera)

    def test_full_size_render_preserves_resized_source_pixel_centres(self):
        camera = self.camera()
        camera.update(cx=243.2, cy=187.8)
        width, height = 3072, 2304
        result = controller.camera_values(camera, width, height)
        focal_pixels = result["focal"] / result["haperture"] * width
        for x, y in [(-.4, -.3), (0., 0.), (.4, .3)]:
            expected_x = (camera["fx"] * x + camera["cx"] + .5) * 6 - .5
            expected_y = (camera["fy"] * y + camera["cy"] + .5) * 6 - .5
            raster_x = width / 2 + focal_pixels * x - width / 2 * result["win_x"]
            raster_y = height - 1 - (height / 2 - focal_pixels * y - width / 2 * result["win_y"])
            self.assertAlmostEqual(raster_x, expected_x, places=9)
            self.assertAlmostEqual(raster_y, expected_y, places=9)


if __name__ == "__main__":
    unittest.main()
