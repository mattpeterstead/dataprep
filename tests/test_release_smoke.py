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

                html = response.get_data(as_text=True)
                self.assertIn("Select Folder", html)
                self.assertNotIn(">Open Folder<", html)
                for element_id in (
                    "openAboutModalBtn",
                    "aboutModalTitle",
                    "closeAboutModalBtn",
                    "closeAboutModalActionBtn",
                ):
                    self.assertEqual(html.count(f'id="{element_id}"'), 1)
                self.assertIn("Current development build", html)
                self.assertIn("MIT License", html)
                self.assertIn("github.com/mattpeterstead/dataprep", html)

    def test_updated_toolbar_icons_and_json_element_colors(self):
        masking_svg = (imageprep_simple.APP_DIR / "images" / "btn_masking.svg").read_text(encoding="utf-8")
        rename_svg = (imageprep_simple.APP_DIR / "images" / "btn_rename_all.svg").read_text(encoding="utf-8")
        self.assertIn('fill="#fff" stroke="#111"', masking_svg)
        self.assertIn('fill="#f4c542"', rename_svg)

        for module in (imageprep_simple, imageprep):
            with self.subTest(module=module.__name__):
                self.assertIn("function jsonElementColorTheme(element)", module.TEMPLATE)
                self.assertIn("--json-bbox-hover-bg", module.TEMPLATE)
                self.assertEqual(module.TEMPLATE.count('id="exitMaskModeBtn"'), 1)
                self.assertEqual(module.TEMPLATE.count('id="exitMaskModeLabel"'), 1)
                self.assertIn("Enable watermark removal mode", module.TEMPLATE)
                self.assertIn("Exit ${modeName} mode", module.TEMPLATE)
                self.assertIn("boundWheelZoom", module.TEMPLATE)
                self.assertIn("Math.exp(-pixelDelta * 0.002)", module.TEMPLATE)
                self.assertIn("{ passive: false }", module.TEMPLATE)
                self.assertIn("const minNewCropDragPx = 6", module.TEMPLATE)
                self.assertIn("completedDrag.mode === 'new'", module.TEMPLATE)

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
                self.assertNotIn('id="captionBackendHelp"', html)
                self.assertIn("caption-backend-tooltip", html)
                self.assertIn("bindCaptionBackendTooltip", html)
                self.assertIn('title="Best when you want to use a remote vision model with a custom prompt;', html)
                self.assertIn("Do not mention hair color or eye color.", html)
                self.assertIn("Best for concise Danbooru-style tags", html)
                if module is imageprep_simple:
                    self.assertIn("Batch removal is experimental and may be unreliable.", html)
                    self.assertIn("event.target.closest('.top')", html)
                    for element_id in (
                        "jsonEditRawBtn",
                        "jsonRawModalBackdrop",
                        "jsonRawEditor",
                        "jsonRawSaveBtn",
                        "jsonRawCancelBtn",
                        "jsonColorMenu",
                        "jsonColorPicker",
                        "jsonColorHex",
                    ):
                        self.assertEqual(html.count(f'id="{element_id}"'), 1)
                    self.assertIn('class="json-color-palette"', html)
                    self.assertIn('id="jsonStylePalette"', html)

    def test_json_caption_clipboard_controls_and_style_examples_render(self):
        for module in (imageprep_simple, imageprep):
            with self.subTest(module=module.__name__):
                old_folder = module.current_folder
                try:
                    module.current_folder = None
                    html = module.app.test_client().get("/").get_data(as_text=True)
                finally:
                    module.current_folder = old_folder
                self.assertEqual(html.count('id="jsonCopyBtn"'), 1)
                self.assertEqual(html.count('id="jsonPasteBtn"'), 1)

        old_folder = imageprep_simple.current_folder
        try:
            imageprep_simple.current_folder = None
            html = imageprep_simple.app.test_client().get("/").get_data(as_text=True)
        finally:
            imageprep_simple.current_folder = old_folder
        for field_id in (
            "jsonStyleAesthetics",
            "jsonStyleLighting",
            "jsonStyleVariant",
            "jsonStyleMedium",
            "jsonStyleVariantDescription",
            "jsonStylePalette",
        ):
            self.assertRegex(html, rf'id="{field_id}"[^>]*title="Example: [^"]+"')

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
