import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

import imageprep
import imageprep_simple
import videoprep


class ReleaseSmokeTests(unittest.TestCase):
    def test_referenced_interface_assets_exist(self):
        for module in (imageprep_simple, imageprep, videoprep):
            with self.subTest(module=module.__name__):
                assets = {
                    name
                    for name in re.findall(r'/category_icon/([^"\'?#]+)', module.TEMPLATE)
                    if re.fullmatch(r"[A-Za-z0-9_.-]+", name)
                }
                self.assertTrue(assets)
                missing = [name for name in sorted(assets) if not (module.APP_DIR / "images" / name).is_file()]
                self.assertEqual(missing, [])

    def test_main_pages_render(self):
        for module in (imageprep_simple, imageprep, videoprep):
            with self.subTest(module=module.__name__):
                old_folder = module.current_folder
                try:
                    module.current_folder = None
                    response = module.app.test_client().get("/")
                    self.assertEqual(response.status_code, 200)
                    self.assertIn(b"<!doctype html>", response.data.lower())
                finally:
                    module.current_folder = old_folder

    def test_prompt_preset_ui_and_qwen_default_render(self):
        for module in (imageprep_simple, imageprep):
            with self.subTest(module=module.__name__):
                old_folder = module.current_folder
                try:
                    module.current_folder = None
                    html = module.app.test_client().get("/").get_data(as_text=True)
                finally:
                    module.current_folder = old_folder
                for element_id in (
                    "qwenPromptPresetSelect",
                    "loadQwenPromptPresetBtn",
                    "saveQwenPromptPresetBtn",
                    "deleteQwenPromptPresetBtn",
                    "externalPromptPresetSelect",
                    "loadExternalPromptPresetBtn",
                    "saveExternalPromptPresetBtn",
                    "deleteExternalPromptPresetBtn",
                ):
                    self.assertEqual(html.count(f'id="{element_id}"'), 1)
                self.assertIn("Do not mention hair color or eye color.", html)

    @unittest.skipUnless(shutil.which("node"), "Node.js is not installed")
    def test_embedded_javascript_syntax(self):
        for module in (imageprep_simple, imageprep, videoprep):
            with self.subTest(module=module.__name__):
                old_folder = module.current_folder
                try:
                    module.current_folder = None
                    html = module.app.test_client().get("/").get_data(as_text=True)
                finally:
                    module.current_folder = old_folder
                scripts = [
                    body
                    for attributes, body in re.findall(r"<script([^>]*)>(.*?)</script>", html, re.S)
                    if "application/json" not in attributes
                ]
                javascript = "\n".join(scripts)
                self.assertTrue(javascript.strip())
                result = subprocess.run(
                    ["node", "--check", "-"],
                    input=javascript,
                    text=True,
                    encoding="utf-8",
                    capture_output=True,
                    check=False,
                )
                self.assertEqual(result.returncode, 0, result.stderr)

    def test_system_prompt_preset_lifecycle_and_protection(self):
        for module in (imageprep_simple, imageprep):
            with self.subTest(module=module.__name__), tempfile.TemporaryDirectory() as temp_dir:
                original_file = module.SYSTEM_PROMPT_PRESETS_FILE
                try:
                    module.SYSTEM_PROMPT_PRESETS_FILE = Path(temp_dir) / "system_prompt_presets.json"
                    client = module.app.test_client()

                    listing = client.get("/system_prompt_presets?backend=qwen3_vl")
                    self.assertEqual(listing.status_code, 200)
                    self.assertEqual(
                        listing.get_json()["presets"][0],
                        {"name": "Simple character caption", "protected": True},
                    )

                    built_in = client.post(
                        "/load_system_prompt_preset",
                        json={"backend": "qwen3_vl", "name": "Simple character caption"},
                    )
                    self.assertEqual(built_in.status_code, 200)
                    self.assertIn("Do not mention hair color or eye color.", built_in.get_json()["prompt"])

                    protected_save = client.post(
                        "/save_system_prompt_preset",
                        json={"backend": "qwen3_vl", "name": "simple CHARACTER caption", "prompt": "Bad"},
                    )
                    self.assertEqual(protected_save.status_code, 403)
                    protected_delete = client.post(
                        "/delete_system_prompt_preset",
                        json={"backend": "external_api", "name": "Simple character caption"},
                    )
                    self.assertEqual(protected_delete.status_code, 403)

                    saved = client.post(
                        "/save_system_prompt_preset",
                        json={"backend": "qwen3_vl", "name": "Custom", "prompt": "A custom prompt."},
                    )
                    self.assertEqual(saved.status_code, 200)
                    loaded = client.post(
                        "/load_system_prompt_preset",
                        json={"backend": "qwen3_vl", "name": "custom"},
                    )
                    self.assertEqual(loaded.get_json()["prompt"], "A custom prompt.")
                    deleted = client.post(
                        "/delete_system_prompt_preset",
                        json={"backend": "qwen3_vl", "name": "CUSTOM"},
                    )
                    self.assertEqual(deleted.status_code, 200)
                finally:
                    module.SYSTEM_PROMPT_PRESETS_FILE = original_file


if __name__ == "__main__":
    unittest.main()
