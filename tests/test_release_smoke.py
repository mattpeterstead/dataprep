import json
import re
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest import mock

import imageprep
import imageprep_simple
import videoprep


class ReleaseSmokeTests(unittest.TestCase):
    def test_clipboard_source_uses_the_full_current_folder_path(self):
        for module in (imageprep_simple, imageprep):
            with self.subTest(module=module.__name__):
                with tempfile.TemporaryDirectory() as folder:
                    with (
                        mock.patch.object(module, "current_folder", folder),
                        mock.patch.object(module, "folder_name", Path(folder).name),
                        mock.patch.object(module, "pairs_cache", []),
                    ):
                        html = module.app.test_client().get("/").get_data(as_text=True)

                self.assertIn(
                    f"const FOLDER_KEY = {json.dumps(folder)};",
                    html,
                )

    def test_rename_only_changes_selected_pairs(self):
        for module in (imageprep_simple, imageprep):
            with self.subTest(module=module.__name__):
                with tempfile.TemporaryDirectory() as folder:
                    folder_path = Path(folder)
                    (folder_path / "selected.png").write_bytes(b"selected image")
                    (folder_path / "selected.txt").write_text("selected caption", encoding="utf-8")
                    (folder_path / "untouched.png").write_bytes(b"untouched image")
                    (folder_path / "untouched.txt").write_text("untouched caption", encoding="utf-8")
                    pairs = [
                        ("selected.png", "selected caption"),
                        ("untouched.png", "untouched caption"),
                    ]

                    with (
                        mock.patch.object(module, "current_folder", folder),
                        mock.patch.object(module, "pairs_cache", pairs),
                        mock.patch.object(module, "category_assignments", {}),
                    ):
                        response = module.app.test_client().post(
                            "/rename_all_pairs",
                            json={"prefix": "renamed", "img_names": ["selected.png"]},
                        )

                    self.assertEqual(response.status_code, 200)
                    self.assertEqual(response.get_json()["renamed"], 1)
                    self.assertTrue((folder_path / "renamed00000.png").is_file())
                    self.assertEqual(
                        (folder_path / "renamed00000.txt").read_text(encoding="utf-8"),
                        "selected caption",
                    )
                    self.assertFalse((folder_path / "selected.png").exists())
                    self.assertTrue((folder_path / "untouched.png").is_file())
                    self.assertEqual(
                        (folder_path / "untouched.txt").read_text(encoding="utf-8"),
                        "untouched caption",
                    )

    def test_clone_pair_can_use_a_previous_source_folder(self):
        for module in (imageprep_simple, imageprep):
            with self.subTest(module=module.__name__):
                with (
                    tempfile.TemporaryDirectory() as source,
                    tempfile.TemporaryDirectory() as target,
                ):
                    source_path = Path(source)
                    target_path = Path(target)
                    module.Image.new("RGB", (2, 2), "blue").save(source_path / "source.png")
                    (source_path / "source.txt").write_text("source caption", encoding="utf-8")

                    with (
                        mock.patch.object(module, "current_folder", target),
                        mock.patch.object(module, "pairs_cache", []),
                        mock.patch.object(module, "category_assignments", {}),
                    ):
                        response = module.app.test_client().post(
                            "/clone_pair",
                            json={
                                "img_name": "source.png",
                                "source_folder": source,
                            },
                        )

                    self.assertEqual(response.status_code, 200)
                    self.assertTrue((target_path / "source.png").is_file())
                    self.assertEqual(
                        (target_path / "source.txt").read_text(encoding="utf-8"),
                        "source caption",
                    )

    def test_card_clipboard_can_copy_pairs_between_folders(self):
        with (
            tempfile.TemporaryDirectory() as source,
            tempfile.TemporaryDirectory() as target,
        ):
            source_path = Path(source)
            target_path = Path(target)
            imageprep.Image.new("RGB", (2, 2), "green").save(source_path / "card.png")
            (source_path / "card.txt").write_text("card caption", encoding="utf-8")

            with (
                mock.patch.object(imageprep, "current_folder", target),
                mock.patch.object(imageprep, "pairs_cache", []),
                mock.patch.object(imageprep, "category_assignments", {}),
            ):
                response = imageprep.app.test_client().post(
                    "/move_pairs",
                    json={
                        "img_names": ["card.png"],
                        "source_folder": source,
                        "category": imageprep.DEFAULT_CATEGORY,
                        "mode": "copy",
                    },
                )

            self.assertEqual(response.status_code, 200)
            self.assertTrue(response.get_json()["cross_folder"])
            self.assertTrue((target_path / "card.png").is_file())
            self.assertEqual(
                (target_path / "card.txt").read_text(encoding="utf-8"),
                "card caption",
            )
            self.assertTrue((source_path / "card.png").is_file())

    def test_simple_clipboard_batch_continues_after_a_missing_pair(self):
        with (
            tempfile.TemporaryDirectory() as source,
            tempfile.TemporaryDirectory() as target,
        ):
            source_path = Path(source)
            target_path = Path(target)
            image_names = [f"source-{index}.png" for index in range(5)]
            for index, image_name in enumerate(image_names):
                imageprep_simple.Image.new("RGB", (2, 2), (index * 20, 0, 0)).save(
                    source_path / image_name
                )
                (source_path / Path(image_name).with_suffix(".txt")).write_text(
                    f"caption {index}",
                    encoding="utf-8",
                )

            requested_names = image_names[:2] + ["missing.png"] + image_names[2:]
            with (
                mock.patch.object(imageprep_simple, "current_folder", target),
                mock.patch.object(imageprep_simple, "pairs_cache", []),
                mock.patch.object(imageprep_simple, "category_assignments", {}),
            ):
                response = imageprep_simple.app.test_client().post(
                    "/clone_pairs",
                    json={
                        "img_names": requested_names,
                        "source_folder": source,
                    },
                )

            self.assertEqual(response.status_code, 200)
            data = response.get_json()
            self.assertEqual(len(data["changed"]), 5)
            self.assertEqual(len(data["errors"]), 1)
            for index, image_name in enumerate(image_names):
                self.assertTrue((target_path / image_name).is_file())
                self.assertEqual(
                    (target_path / Path(image_name).with_suffix(".txt")).read_text(
                        encoding="utf-8"
                    ),
                    f"caption {index}",
                )

    def test_character_category_presets_are_distinct_and_protected(self):
        presets = imageprep_simple.load_category_presets()
        basic = presets["character"]
        extended = presets["character extended"]
        basic_names = {item["name"] for item in basic["categories"]}
        extended_names = {item["name"] for item in extended["categories"]}

        self.assertTrue(basic["protected"])
        self.assertTrue(extended["protected"])
        self.assertEqual(
            basic_names,
            {"Close-up", "Medium", "Full body", "Undefined"},
        )
        self.assertGreater(len(extended_names), len(basic_names))
        basic_group_names = {
            item["name"]: next(
                group["name"]
                for group in basic["groups"]
                if group["id"] == item["group_id"]
            )
            for item in basic["categories"]
        }
        self.assertEqual(basic_group_names["Close-up"], "Close-up")
        self.assertEqual(basic_group_names["Medium"], "Medium")
        self.assertEqual(basic_group_names["Full body"], "Full body")
        self.assertEqual(basic_group_names["Undefined"], "Uncategorized")

        with mock.patch.object(imageprep_simple, "current_folder", "test-folder"):
            for name in ("character", "character extended"):
                with self.subTest(action="overwrite", name=name):
                    response = imageprep_simple.app.test_client().post(
                        "/save_category_preset",
                        json={"name": name, "overwrite": True},
                    )
                    self.assertEqual(response.status_code, 403)
                    self.assertTrue(response.get_json()["protected"])

                with self.subTest(action="delete", name=name):
                    response = imageprep_simple.app.test_client().post(
                        "/delete_category_preset",
                        json={"name": name},
                    )
                    self.assertEqual(response.status_code, 403)
                    self.assertTrue(response.get_json()["protected"])

    def test_character_preset_is_the_default_for_a_new_folder(self):
        with tempfile.TemporaryDirectory() as folder:
            assignments, categories, groups = imageprep_simple.load_category_state(folder)

        self.assertEqual(assignments, {})
        self.assertEqual(
            {item["name"] for item in categories},
            {"Close-up", "Medium", "Full body", "Undefined"},
        )
        self.assertEqual(
            {item["name"] for item in groups},
            {"Close-up", "Medium", "Full body", "Uncategorized"},
        )

    def test_summary_sorts_valid_resolutions_and_bucket_bases(self):
        for module in (imageprep_simple, imageprep):
            with self.subTest(module=module.__name__):
                selected_base = 1024
                selected_resolution = module.get_bucket_options(selected_base)[0]
                other_resolution = next(
                    resolution
                    for resolution in module.get_bucket_options(768)
                    if resolution not in module.get_bucket_options(selected_base)
                )
                dimensions = {
                    "invalid.png": (123, 456, ""),
                    "other.png": (*other_resolution, ""),
                    "selected.png": (*selected_resolution, ""),
                }
                pairs = [(filename, "") for filename in dimensions]

                with (
                    mock.patch.object(module, "pairs_cache", pairs),
                    mock.patch.object(module, "selected_crop_base", selected_base),
                    mock.patch.object(
                        module,
                        "get_image_info",
                        side_effect=lambda filename: dimensions[filename],
                    ),
                ):
                    response = module.app.test_client().get(
                        "/summary",
                        headers={"X-Requested-With": "XMLHttpRequest"},
                    )

                self.assertEqual(response.status_code, 200)
                data = response.get_json()
                self.assertEqual(
                    [item["status"] for item in data["items"]],
                    ["selected", "other", "invalid"],
                )
                self.assertEqual(
                    [item["base"] for item in data["bucket_bases"]],
                    [1024, 768],
                )

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
        circle_svg = (imageprep_simple.APP_DIR / "images" / "btn_mask_circle.svg").read_text(encoding="utf-8")
        watermark_svg = (imageprep_simple.APP_DIR / "images" / "btn_watermark_removal.svg").read_text(encoding="utf-8")
        self.assertIn('fill="#fff" stroke="#111"', masking_svg)
        self.assertIn('fill="#f4c542"', rename_svg)
        self.assertIn('fill="#4ea5df"', circle_svg)
        self.assertIn('width="11.5" height="3.25"', watermark_svg)

        for module in (imageprep_simple, imageprep):
            with self.subTest(module=module.__name__):
                self.assertIn("function jsonElementColorTheme(element)", module.TEMPLATE)
                self.assertIn("--json-bbox-hover-bg", module.TEMPLATE)
                self.assertEqual(module.TEMPLATE.count('id="exitMaskModeBtn"'), 1)
                self.assertEqual(module.TEMPLATE.count('id="exitMaskModeLabel"'), 1)
                self.assertIn("button.hidden = !maskModeActive || maskModePurpose === 'watermark';", module.TEMPLATE)
                self.assertIn("Enable watermark removal mode", module.TEMPLATE)
                self.assertIn("function drawMaskShape(", module.TEMPLATE)
                self.assertIn("const snapToEdge = point =>", module.TEMPLATE)
                self.assertIn("['brush', 'fill', 'rectangle', 'circle']", module.TEMPLATE)
                self.assertIn('data-tool="rectangle"', module.TEMPLATE)
                self.assertIn('data-tool="circle"', module.TEMPLATE)
                self.assertIn("boundWheelZoom", module.TEMPLATE)
                self.assertIn("if (!isCardWheelZoomEnabled()) return;", module.TEMPLATE)
                self.assertIn("Math.exp(-pixelDelta * 0.002)", module.TEMPLATE)
                self.assertIn("{ passive: false }", module.TEMPLATE)
                self.assertIn("const minNewCropDragPx = 6", module.TEMPLATE)
                self.assertIn("completedDrag.mode === 'new'", module.TEMPLATE)
                self.assertEqual(module.TEMPLATE.count('id="cardSortMenu"'), 1)
                self.assertEqual(module.TEMPLATE.count('id="cardSortBy"'), 1)
                self.assertEqual(module.TEMPLATE.count('id="cardSortDirection"'), 1)
                self.assertIn('data-added-at="{{ pair.added_at }}"', module.TEMPLATE)
                self.assertIn("function applyCardSort()", module.TEMPLATE)
                self.assertIn("dataprep_card_sort_by", module.TEMPLATE)
                self.assertIn('<option value="size">Resolution</option>', module.TEMPLATE)
                self.assertIn("const aPixels =", module.TEMPLATE)
                self.assertIn("sessionStorage", module.TEMPLATE)
                self.assertIn("source_folder", module.TEMPLATE)
                self.assertIn("Select one or more cards to rename.", module.TEMPLATE)
                self.assertIn(
                    'title="Rename selected image and caption pairs"',
                    module.TEMPLATE,
                )
                if module is imageprep_simple:
                    self.assertIn("fetch('/clone_pairs'", module.TEMPLATE)
                    self.assertIn('class="summary-categorize-btn"', module.TEMPLATE)
                    self.assertNotIn(
                        ".summary-categorize-btn:hover{background:",
                        module.TEMPLATE,
                    )

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
                self.assertIn("Best for uncensored natural-language captions for general images and LoRA training data.", html)
                self.assertIn('title="Best when you want to use a remote vision model with a custom prompt;', html)
                self.assertIn("Do not mention hair color or eye color.", html)
                self.assertIn("Best for concise Danbooru-style tags", html)
                if module is imageprep_simple:
                    self.assertEqual(html.count('id="watermarkModeBtn"'), 1)
                    self.assertIn(">Watermark</span>", html)
                    self.assertIn("await setMaskMode(!active, 'watermark');", html)
                    self.assertEqual(html.count('id="cardWheelZoomSetting"'), 1)
                    self.assertIn("Zoom card images with the mouse wheel", html)
                    self.assertIn("Batch removal is experimental and may be unreliable.", html)
                    self.assertIn("event.target.closest('.top')", html)
                    self.assertIn("selectedItemCount > 1 && clickedSelectedItem", html)
                    self.assertIn("await deleteCategorizeSelection();", html)
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
