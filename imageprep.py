import os
import threading
import webbrowser
import sys
import math
import subprocess
import shutil
import base64
import json
import requests
import re
import csv
import time
import traceback
import socket
import hashlib
import io
import queue
from pathlib import Path
from collections import defaultdict

from flask import Flask, render_template_string, request, jsonify, send_from_directory, redirect, url_for
from PIL import Image, ImageFilter, ImageOps
try:
    import pillow_avif  # Registers AVIF support with Pillow when available
except Exception:
    pillow_avif = None
import tkinter as tk
from tkinter import filedialog

if sys.platform.startswith("win"):
    import multiprocessing
    multiprocessing.freeze_support()

IMAGE_EXTENSIONS = (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".avif")
IMAGE_MIME_TO_EXTENSION = {
    "image/png": ".png",
    "image/jpeg": ".jpg",
    "image/gif": ".gif",
    "image/bmp": ".bmp",
    "image/webp": ".webp",
    "image/avif": ".avif",
}
APP_DIR = Path(__file__).resolve().parent
SETTINGS_DIR = APP_DIR / "settings"
LAST_APP_FILE = SETTINGS_DIR / ".dataset_forge_last_app"
IMAGE_FOLDER_HANDOFF_FILE = SETTINGS_DIR / ".dataset_forge_image_folder_handoff"
CATEGORY_META_FILENAME = ".dataprep_categories.json"
CATEGORY_DEFS = [
    {"name": "Close-up Front", "icon": "portrait_front.png"},
    {"name": "Close-up Left", "icon": "portrait_left.png"},
    {"name": "Close-up Right", "icon": "portrait_right.png"},
    {"name": "Close-up Front-left", "icon": "portrait_front_left.png"},
    {"name": "Close-up Front-right", "icon": "portrait_front_right.png"},
    {"name": "Close-up Back", "icon": "portrait_back.png"},
    {"name": "Close-up From Above", "icon": "portrait_above.png"},
    {"name": "Close-up From Below", "icon": "portrait_below.png"},
    {"name": "Medium Front", "icon": "kneeup_front.png"},
    {"name": "Medium Profile", "icon": "kneeup_profile.png"},
    {"name": "Medium Back", "icon": "kneeup_back.png"},
    {"name": "Full body Front", "icon": "fullbody_front.png"},
    {"name": "Full body Profile", "icon": "fullbody_profile.png"},
    {"name": "Full body Back", "icon": "fullbody_back.png"},
    {"name": "Undefined", "icon": "undefined.png"},
]
CATEGORY_NAME_TO_ICON = {item["name"]: item["icon"] for item in CATEGORY_DEFS}
DEFAULT_CATEGORY = "Undefined"
DEFAULT_CATEGORY_GROUP_ID = "uncategorized"
DEFAULT_CATEGORY_GROUPS = [
    {"id": "close-up", "name": "Close-up", "color": "#22c55e"},
    {"id": "medium", "name": "Medium", "color": "#eab308"},
    {"id": "full-body", "name": "Full body", "color": "#ef4444"},
    {"id": DEFAULT_CATEGORY_GROUP_ID, "name": "Uncategorized", "color": "#9ca3af"},
]
DEFAULT_CATEGORY_COLORS = [
    "#22c55e", "#16a34a", "#15803d", "#34d399",
    "#10b981", "#0f766e", "#14b8a6", "#06b6d4",
    "#eab308", "#ca8a04", "#a16207", "#ef4444",
    "#dc2626", "#991b1b", "#9ca3af",
]
BUCKET_STEP = 64
DEFAULT_AUTO_MASK_MODEL = "silueta"
REMBG_SESSIONS = {}
SIMPLE_LAMA_MODEL = None


def remember_app(kind):
    try:
        SETTINGS_DIR.mkdir(exist_ok=True)
        LAST_APP_FILE.write_text(kind, encoding="utf-8")
    except Exception:
        pass


def write_image_folder_handoff(folder):
    try:
        SETTINGS_DIR.mkdir(exist_ok=True)
        if folder and os.path.isdir(folder):
            IMAGE_FOLDER_HANDOFF_FILE.write_text(os.path.abspath(folder), encoding="utf-8")
        elif IMAGE_FOLDER_HANDOFF_FILE.exists():
            try:
                IMAGE_FOLDER_HANDOFF_FILE.unlink()
            except Exception:
                IMAGE_FOLDER_HANDOFF_FILE.write_text("", encoding="utf-8")
    except Exception:
        pass


def read_image_folder_handoff():
    try:
        if not IMAGE_FOLDER_HANDOFF_FILE.exists():
            return None
        folder = IMAGE_FOLDER_HANDOFF_FILE.read_text(encoding="utf-8").strip()
        try:
            IMAGE_FOLDER_HANDOFF_FILE.unlink()
        except Exception:
            try:
                IMAGE_FOLDER_HANDOFF_FILE.write_text("", encoding="utf-8")
            except Exception:
                pass
        if folder and os.path.isdir(folder):
            return folder
    except Exception:
        pass
    return None


def local_port_open(port):
    try:
        with socket.create_connection(("127.0.0.1", int(port)), timeout=0.4):
            return True
    except Exception:
        return False


def hidden_subprocess_kwargs():
    if not sys.platform.startswith("win"):
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = getattr(subprocess, "SW_HIDE", 0)
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NO_WINDOW", 0),
    }


def app_window_subprocess_kwargs():
    if not sys.platform.startswith("win"):
        return {}
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = getattr(subprocess, "SW_SHOWMINNOACTIVE", 7)
    return {
        "startupinfo": startupinfo,
        "creationflags": getattr(subprocess, "CREATE_NEW_CONSOLE", 0),
    }


def launch_local_app(script_name, port):
    if local_port_open(port):
        return
    executable = Path(sys.executable)
    if sys.platform.startswith("win"):
        python_console = executable.with_name("python.exe")
        if python_console.exists():
            executable = python_console
    kwargs = {
        "cwd": str(APP_DIR),
        **app_window_subprocess_kwargs(),
    }
    subprocess.Popen([str(executable), str(APP_DIR / script_name)], **kwargs)


def launch_local_app_after_port_closes(script_name, port):
    executable = Path(sys.executable)
    if sys.platform.startswith("win"):
        python_console = executable.with_name("python.exe")
        if python_console.exists():
            executable = python_console
    helper_code = r"""
import socket
import subprocess
import sys
import time

exe, script, cwd, port = sys.argv[1], sys.argv[2], sys.argv[3], int(sys.argv[4])
deadline = time.time() + 15
while time.time() < deadline:
    try:
        sock = socket.create_connection(("127.0.0.1", port), timeout=0.25)
        sock.close()
        time.sleep(0.2)
    except OSError:
        break
kwargs = {"cwd": cwd}
if sys.platform.startswith("win"):
    startupinfo = subprocess.STARTUPINFO()
    startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
    startupinfo.wShowWindow = getattr(subprocess, "SW_SHOWMINNOACTIVE", 7)
    kwargs["startupinfo"] = startupinfo
    kwargs["creationflags"] = getattr(subprocess, "CREATE_NEW_CONSOLE", 0)
subprocess.Popen([exe, script], **kwargs)
"""
    subprocess.Popen(
        [str(executable), "-B", "-c", helper_code, str(executable), str(APP_DIR / script_name), str(APP_DIR), str(port)],
        cwd=str(APP_DIR),
        **hidden_subprocess_kwargs(),
    )


def exit_soon():
    threading.Timer(1.5, lambda: os._exit(0)).start()


def switch_page(target_url, label, initial_delay_ms=300):
    target_json = json.dumps(target_url)
    label_json = json.dumps(label)
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Switching</title>
<style>body{{margin:0;background:#050505;color:#f1f5f9;font-family:Inter,Segoe UI,Arial,sans-serif;display:grid;place-items:center;min-height:100vh}}div{{background:#141414;border:1px solid #2a2a2a;border-radius:8px;padding:18px 22px;min-width:240px}}.muted{{color:#94a3b8;font-size:13px;margin-top:8px}}a{{color:#93c5fd}}</style>
</head><body><div><div id="switchTitle"></div><div class="muted" id="switchStatus">Starting...</div></div>
<script>
const targetUrl = {target_json};
const label = {label_json};
const title = document.getElementById('switchTitle');
const statusEl = document.getElementById('switchStatus');
let attempts = 0;
title.textContent = `Switching to ${{label}}...`;
async function waitForTarget() {{
  attempts += 1;
  statusEl.textContent = `Waiting for server... (${{attempts}})`;
  try {{
    await fetch(`${{targetUrl}}?switch_ready=${{Date.now()}}`, {{mode: 'no-cors', cache: 'no-store'}});
    location.replace(targetUrl);
    return;
  }} catch (err) {{
    if (attempts >= 80) {{
      statusEl.innerHTML = `Still waiting. <a href="${{targetUrl}}">Open manually</a>`;
      return;
    }}
    setTimeout(waitForTarget, 500);
  }}
}}
setTimeout(waitForTarget, {int(initial_delay_ms)});
</script></body></html>"""

KOHYA_BUCKETS = {
    512: [
        (256, 832), (256, 896), (256, 960), (256, 1024),
        (320, 704), (320, 768),
        (384, 640),
        (448, 576),
        (512, 512),
        (576, 448),
        (640, 384),
        (704, 320), (768, 320),
        (832, 256), (896, 256), (960, 256), (1024, 256),
    ],
    768: [
        (384, 1344), (384, 1408), (384, 1472), (384, 1536),
        (448, 1216), (448, 1280),
        (512, 1088), (512, 1152),
        (576, 960), (576, 1024),
        (640, 896),
        (704, 832),
        (768, 768),
        (832, 704),
        (896, 640),
        (960, 576), (1024, 576),
        (1088, 512), (1152, 512),
        (1216, 448), (1280, 448),
        (1344, 384), (1408, 384), (1472, 384), (1536, 384),
    ],
    1024: [
        (512, 1856), (512, 1920), (512, 1984), (512, 2048),
        (576, 1664), (576, 1728), (576, 1792),
        (640, 1536), (640, 1600),
        (704, 1408), (704, 1472),
        (768, 1280), (768, 1344),
        (832, 1216),
        (896, 1152),
        (960, 1088),
        (1024, 1024),
        (1088, 960),
        (1152, 896),
        (1216, 832),
        (1280, 768), (1344, 768),
        (1408, 704), (1472, 704),
        (1536, 640), (1600, 640),
        (1664, 576), (1728, 576), (1792, 576),
        (1856, 512), (1920, 512), (1984, 512), (2048, 512),
    ],
}

pairs_cache = []
current_folder = None
message = ""
folder_name = ""
selected_crop_base = 1024
category_assignments = {}
category_folders = []
category_groups = []

JOY_MODAL_OPEN_KEY = "caption_app_joy_modal_open"
JOY_GGUF_DEFAULTS_PATH = SETTINGS_DIR / "joycaption_gguf_defaults.json"
KOBOLD_PROCESS = None
KOBOLD_PROCESS_LOCK = threading.Lock()
BUCKET_STEP = 64

KOHYA_BUCKETS = {
    512: [
        (256, 832), (256, 896), (256, 960), (256, 1024),
        (320, 704), (320, 768),
        (384, 640),
        (448, 576),
        (512, 512),
        (576, 448),
        (640, 384),
        (704, 320), (768, 320),
        (832, 256), (896, 256), (960, 256), (1024, 256),
    ],
    768: [
        (384, 1344), (384, 1408), (384, 1472), (384, 1536),
        (448, 1216), (448, 1280),
        (512, 1088), (512, 1152),
        (576, 960), (576, 1024),
        (640, 896),
        (704, 832),
        (768, 768),
        (832, 704),
        (896, 640),
        (960, 576), (1024, 576),
        (1088, 512), (1152, 512),
        (1216, 448), (1280, 448),
        (1344, 384), (1408, 384), (1472, 384), (1536, 384),
    ],
    1024: [
        (512, 1856), (512, 1920), (512, 1984), (512, 2048),
        (576, 1664), (576, 1728), (576, 1792),
        (640, 1536), (640, 1600),
        (704, 1408), (704, 1472),
        (768, 1280), (768, 1344),
        (832, 1216),
        (896, 1152),
        (960, 1088),
        (1024, 1024),
        (1088, 960),
        (1152, 896),
        (1216, 832),
        (1280, 768), (1344, 768),
        (1408, 704), (1472, 704),
        (1536, 640), (1600, 640),
        (1664, 576), (1728, 576), (1792, 576),
        (1856, 512), (1920, 512), (1984, 512), (2048, 512),
    ],
}

pairs_cache = []
current_folder = None
message = ""
folder_name = ""
selected_crop_base = 1024
category_assignments = {}
category_folders = []
category_groups = []
joycaption_status = {
    "running": False,
    "status": "Idle",
    "log": "",
    "process": None,
    "count": 0,
    "total": 0,
    "last_rc": None,
    "interrupt_requested": False,
    "reload_pairs": False,
}
app = Flask(__name__)


def _append_joy_log(text: str):
    log = joycaption_status.get("log", "") + str(text)
    joycaption_status["log"] = log[-25000:]


def prepend_triggerword(caption, triggerword):
    caption = str(caption or '').strip()
    triggerword = str(triggerword or '')
    if not triggerword:
        return caption
    return f"{triggerword}{caption}"

    triggerword = str(triggerword or '').strip()
    if not triggerword:
        return caption
    if not caption:
        return triggerword
    return f"{triggerword}{caption}"


def should_skip_caption_file(txt_path, options):
    if options.get('append_existing'):
        return False
    if not (options.get('no_overwrite') and os.path.exists(txt_path)):
        return False
    try:
        existing = Path(txt_path).read_text(encoding='utf-8').strip()
    except Exception:
        existing = ''
    return bool(existing)


def normalize_generated_caption(caption):
    text = str(caption or '').strip()
    if not text:
        return ''

    # Some chat backends occasionally return JSON-escaped text as literal text.
    # Decode only explicit escape sequences so normal non-ASCII text stays intact.
    for _ in range(2):
        stripped = text.strip()
        if len(stripped) >= 2 and stripped[0] == stripped[-1] and stripped[0] in ("'", '"'):
            if stripped[0] == '"':
                try:
                    decoded = json.loads(stripped)
                    if isinstance(decoded, str):
                        text = decoded
                        continue
                except Exception:
                    pass
            text = stripped[1:-1].strip()
            continue
        break

    def decode_escape(match):
        try:
            return chr(int(match.group(1), 16))
        except Exception:
            return match.group(0)

    text = re.sub(r'\\U([0-9a-fA-F]{8})', decode_escape, text)
    text = re.sub(r'\\u([0-9a-fA-F]{4})', decode_escape, text)
    text = (
        text
        .replace('\\r\\n', ' ')
        .replace('\\n', ' ')
        .replace('\\r', ' ')
        .replace('\\t', ' ')
        .replace('\\"', '"')
        .replace("\\'", "'")
        .replace('\\\\', '\\')
    )
    text = re.sub(r'[\r\n\t]+', ' ', text)
    text = re.sub(r'\s+', ' ', text).strip()
    return text


CAPTION_FORMAT_STANDARD = "standard_text"
CAPTION_FORMAT_IDEOGRAM4_JSON = "ideogram4_json"
IDEOGRAM4_JSON_SYSTEM_PROMPT = """Analyze the image and return only one valid JSON object for an Ideogram 4 training caption. Do not use Markdown or explanatory text. Preserve this exact top-level key order: high_level_description, style_description, compositional_deconstruction. high_level_description must be a one- or two-sentence string. style_description must describe the visible image. For a photograph use the exact key order aesthetics, lighting, photo, medium, color_palette. For non-photographic art use the exact key order aesthetics, lighting, medium, art_style, color_palette. style_description must contain exactly one of photo or art_style, never both; if unsure, use photo. The color_palette field may be omitted, but when present its key name must be exactly color_palette. compositional_deconstruction is required and must contain background followed by elements. background must be a string. elements must be a list. Object elements use type, bbox, desc, color_palette in that order, with type set to obj. Text elements use type, bbox, text, desc, color_palette in that order, with type set to text. The bbox and color_palette fields may be omitted, but never rename them to optional_bbox or optional_color_palette. Bounding boxes must follow Ideogram 4 order: [y_min,x_min,y_max,x_max], normalized to 0-1000 from the top-left. The first and third values are vertical top/bottom y coordinates; the second and fourth values are horizontal left/right x coordinates. Do not use [x_min,y_min,x_max,y_max]. Example: an object in the lower-left area should use a large first y_min value and a small second x_min value, such as [620,80,930,420], not [80,620,420,930]. Include a bbox only when its location can be estimated reliably. Hex colors must be uppercase #RRGGBB strings, with at most 16 colors in style_description and at most 5 per element. Describe only visible details. Do not invent identity, text, objects, colors, or scene details."""
IDEOGRAM4_COMMON_MEDIA = (
    "photograph",
    "illustration",
    "3d_render",
    "painting",
    "graphic_design",
    "digital_art",
    "screen_print",
    "collage",
)
IDEOGRAM4_JSON_SYSTEM_PROMPT += (
    " style_description.medium must be a concise string naming the visible medium, for example: "
    + ", ".join(IDEOGRAM4_COMMON_MEDIA)
    + ". For photographic captions use exactly photograph."
)
IDEOGRAM4_HEX_RE = re.compile(r"^#[0-9A-F]{6}$")


def normalize_ideogram4_medium(value, prefer_photo=False):
    if prefer_photo:
        return "photograph"
    text = str(value or "").strip()
    return text or "illustration"


def normalize_caption_format(value):
    value = str(value or CAPTION_FORMAT_STANDARD).strip().lower()
    if value == CAPTION_FORMAT_IDEOGRAM4_JSON:
        return CAPTION_FORMAT_IDEOGRAM4_JSON
    return CAPTION_FORMAT_STANDARD


def caption_sidecar_path(image_path, caption_format):
    suffix = ".json" if normalize_caption_format(caption_format) == CAPTION_FORMAT_IDEOGRAM4_JSON else ".txt"
    return os.path.splitext(str(image_path))[0] + suffix


def caption_sidecar_paths(image_path):
    stem = os.path.splitext(str(image_path))[0]
    return [stem + ".txt", stem + ".json"]


def _check_ideogram4_key_order(obj, expected_order, path):
    actual = list(obj.keys())
    expected = [key for key in expected_order if key in obj]
    if actual != expected:
        raise ValueError(f"{path} keys must be ordered as: {', '.join(expected_order)}")


def _validate_ideogram4_palette(value, path, limit):
    if not isinstance(value, list):
        raise ValueError(f"{path} must be a list.")
    if len(value) > limit:
        raise ValueError(f"{path} may contain at most {limit} colors.")
    for color in value:
        if not isinstance(color, str) or not IDEOGRAM4_HEX_RE.fullmatch(color):
            raise ValueError(f"{path} colors must use uppercase #RRGGBB format.")


def repair_ideogram4_caption(caption):
    if not isinstance(caption, dict):
        return caption

    def rename_aliases(obj, aliases):
        if not isinstance(obj, dict):
            return obj
        result = dict(obj)
        for alias, canonical in aliases.items():
            if alias in result:
                if canonical not in result and result[alias] is not None:
                    result[canonical] = result[alias]
                result.pop(alias, None)
        return result

    def normalize_palette(value, limit):
        if not isinstance(value, list):
            return value
        normalized = []
        for color in value:
            if not isinstance(color, str):
                continue
            color = color.strip().upper()
            if IDEOGRAM4_HEX_RE.fullmatch(color):
                normalized.append(color)
            if len(normalized) >= limit:
                break
        return normalized

    def prefer_art_style(style):
        text = " ".join(
            str(style.get(key, ""))
            for key in ("aesthetics", "medium", "photo", "art_style")
        ).lower()
        art_words = (
            "anime", "cartoon", "drawing", "illustration", "painting", "sketch",
            "render", "3d", "pixel", "vector", "digital art", "concept art",
        )
        return any(word in text for word in art_words)

    style = caption.get("style_description")
    if isinstance(style, dict):
        style = rename_aliases(style, {
            "optional_color_palette": "color_palette",
            "colour_palette": "color_palette",
            "photography": "photo",
            "art": "art_style",
        })
        has_photo = "photo" in style
        has_art = "art_style" in style
        if has_photo and has_art:
            style.pop("photo" if prefer_art_style(style) else "art_style", None)
        elif not has_photo and not has_art:
            variant_value = str(style.get("medium") or style.get("aesthetics") or "").strip()
            if prefer_art_style(style):
                style["art_style"] = variant_value
            else:
                style["photo"] = variant_value
        style["medium"] = normalize_ideogram4_medium(
            style.get("medium"),
            prefer_photo="photo" in style,
        )
        if "color_palette" in style:
            style["color_palette"] = normalize_palette(style["color_palette"], 16)
        style_order = ["aesthetics", "lighting", "photo", "medium", "color_palette"] if "photo" in style else ["aesthetics", "lighting", "medium", "art_style", "color_palette"]
        style = {key: style[key] for key in style_order if key in style} | {key: value for key, value in style.items() if key not in style_order}

    composition = caption.get("compositional_deconstruction")
    if isinstance(composition, dict):
        elements = composition.get("elements")
        if isinstance(elements, list):
            repaired_elements = []
            for element in elements:
                if not isinstance(element, dict):
                    repaired_elements.append(element)
                    continue
                element = rename_aliases(element, {
                    "optional_bbox": "bbox",
                    "bounding_box": "bbox",
                    "description": "desc",
                    "optional_color_palette": "color_palette",
                    "colour_palette": "color_palette",
                })
                if element.get("type") == "object":
                    element["type"] = "obj"
                if isinstance(element.get("bbox"), list):
                    element["bbox"] = [round(value) if isinstance(value, float) else value for value in element["bbox"]]
                if "color_palette" in element:
                    element["color_palette"] = normalize_palette(element["color_palette"], 5)
                order = ["type", "bbox", "text", "desc", "color_palette"] if element.get("type") == "text" else ["type", "bbox", "desc", "color_palette"]
                element = {key: element[key] for key in order if key in element} | {key: value for key, value in element.items() if key not in order}
                repaired_elements.append(element)
            elements = repaired_elements
        composition_values = dict(composition)
        if isinstance(elements, list):
            composition_values["elements"] = elements
        composition = {key: composition_values[key] for key in ("background", "elements") if key in composition_values} | {key: value for key, value in composition_values.items() if key not in {"background", "elements"}}

    values = dict(caption)
    if isinstance(style, dict):
        values["style_description"] = style
    if isinstance(composition, dict):
        values["compositional_deconstruction"] = composition
    order = ["high_level_description", "style_description", "compositional_deconstruction"]
    return {key: values[key] for key in order if key in values} | {key: value for key, value in values.items() if key not in order}


def validate_ideogram4_caption(caption):
    if not isinstance(caption, dict):
        raise ValueError("Ideogram 4 caption root must be a JSON object.")
    allowed_top = {"high_level_description", "style_description", "compositional_deconstruction"}
    unknown_top = set(caption) - allowed_top
    if unknown_top:
        raise ValueError(f"Unknown Ideogram 4 top-level key(s): {', '.join(sorted(unknown_top))}")
    _check_ideogram4_key_order(
        caption,
        ["high_level_description", "style_description", "compositional_deconstruction"],
        "root",
    )
    if "high_level_description" in caption and not isinstance(caption["high_level_description"], str):
        raise ValueError("high_level_description must be a string.")

    style = caption.get("style_description")
    if style is not None:
        if not isinstance(style, dict):
            raise ValueError("style_description must be an object.")
        unknown_style = set(style) - {"aesthetics", "lighting", "photo", "medium", "art_style", "color_palette"}
        if unknown_style:
            raise ValueError(f"Unknown style_description key(s): {', '.join(sorted(unknown_style))}")
        has_photo = "photo" in style
        has_art = "art_style" in style
        if has_photo == has_art:
            raise ValueError("style_description must contain exactly one of photo or art_style.")
        for key in ("aesthetics", "lighting", "medium"):
            if not isinstance(style.get(key), str):
                raise ValueError(f"style_description.{key} must be a string.")
        variant_key = "photo" if has_photo else "art_style"
        if not isinstance(style.get(variant_key), str):
            raise ValueError(f"style_description.{variant_key} must be a string.")
        order = (
            ["aesthetics", "lighting", "photo", "medium", "color_palette"]
            if has_photo
            else ["aesthetics", "lighting", "medium", "art_style", "color_palette"]
        )
        _check_ideogram4_key_order(style, order, "style_description")
        if "color_palette" in style:
            _validate_ideogram4_palette(style["color_palette"], "style_description.color_palette", 16)

    composition = caption.get("compositional_deconstruction")
    if not isinstance(composition, dict):
        raise ValueError("compositional_deconstruction is required and must be an object.")
    if set(composition) - {"background", "elements"}:
        raise ValueError("compositional_deconstruction contains unknown keys.")
    _check_ideogram4_key_order(composition, ["background", "elements"], "compositional_deconstruction")
    if not isinstance(composition.get("background"), str):
        raise ValueError("compositional_deconstruction.background must be a string.")
    elements = composition.get("elements")
    if not isinstance(elements, list):
        raise ValueError("compositional_deconstruction.elements must be a list.")
    for index, element in enumerate(elements):
        path = f"elements[{index}]"
        if not isinstance(element, dict):
            raise ValueError(f"{path} must be an object.")
        element_type = element.get("type")
        if element_type not in {"obj", "text"}:
            raise ValueError(f"{path}.type must be obj or text.")
        allowed = {"type", "bbox", "desc", "color_palette"}
        order = ["type", "bbox", "desc", "color_palette"]
        if element_type == "text":
            allowed.add("text")
            order = ["type", "bbox", "text", "desc", "color_palette"]
            if not isinstance(element.get("text"), str):
                raise ValueError(f"{path}.text must be a string.")
        if set(element) - allowed:
            raise ValueError(f"{path} contains unknown keys.")
        _check_ideogram4_key_order(element, order, path)
        if not isinstance(element.get("desc"), str):
            raise ValueError(f"{path}.desc must be a string.")
        if "bbox" in element:
            bbox = element["bbox"]
            if (
                not isinstance(bbox, list)
                or len(bbox) != 4
                or any(isinstance(value, bool) or not isinstance(value, int) for value in bbox)
                or any(value < 0 or value > 1000 for value in bbox)
            ):
                raise ValueError(f"{path}.bbox must contain four integers from 0 to 1000.")
            y_min, x_min, y_max, x_max = bbox
            if y_min > y_max or x_min > x_max:
                raise ValueError(f"{path}.bbox minimum coordinates must not exceed maximum coordinates.")
        if "color_palette" in element:
            _validate_ideogram4_palette(element["color_palette"], f"{path}.color_palette", 5)
    return caption


def swap_ideogram4_bbox_xyxy_to_yxyx(caption):
    if not isinstance(caption, dict):
        return caption
    elements = caption.get("compositional_deconstruction", {}).get("elements")
    if not isinstance(elements, list):
        return caption
    for element in elements:
        if not isinstance(element, dict):
            continue
        bbox = element.get("bbox")
        if isinstance(bbox, list) and len(bbox) == 4:
            element["bbox"] = [bbox[1], bbox[0], bbox[3], bbox[2]]
    return caption


def normalize_ideogram4_caption(raw_caption, swap_bbox_xyxy_to_yxyx=False):
    text = str(raw_caption or "").strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?\s*", "", text, flags=re.IGNORECASE)
        text = re.sub(r"\s*```$", "", text)
    start = text.find("{")
    end = text.rfind("}")
    if start < 0 or end < start:
        raise ValueError("Qwen3-VL did not return a JSON object.")
    try:
        caption = json.loads(text[start:end + 1])
    except json.JSONDecodeError as exc:
        raise ValueError(f"Qwen3-VL returned invalid JSON: {exc.msg} at line {exc.lineno}, column {exc.colno}.") from exc
    caption = repair_ideogram4_caption(caption)
    if swap_bbox_xyxy_to_yxyx:
        caption = swap_ideogram4_bbox_xyxy_to_yxyx(caption)
    validate_ideogram4_caption(caption)
    return json.dumps(caption, ensure_ascii=False, separators=(",", ":"))


def write_caption_result(txt_path, caption, options):
    if normalize_caption_format(options.get("caption_format")) == CAPTION_FORMAT_IDEOGRAM4_JSON:
        caption = normalize_ideogram4_caption(
            caption,
            swap_bbox_xyxy_to_yxyx=bool(options.get("ideogram4_swap_bbox_xyxy_to_yxyx")),
        )
        Path(txt_path).write_text(caption, encoding="utf-8")
        return caption
    caption = normalize_generated_caption(caption)
    if options.get('append_existing') and os.path.exists(txt_path):
        try:
            existing = Path(txt_path).read_text(encoding='utf-8').rstrip()
        except Exception:
            existing = ''
        if existing and caption:
            caption = f"{existing}\n{caption}"
        elif existing:
            caption = existing
    with open(txt_path, 'w', encoding='utf-8') as f:
        f.write(caption)
    return caption


def caption_interrupt_requested():
    return (
        not joycaption_status.get('running')
        or joycaption_status.get('interrupt_requested')
    )


class CaptionInterrupted(RuntimeError):
    pass


def raise_if_caption_interrupted():
    if caption_interrupt_requested():
        joycaption_status['status'] = 'Interrupted'
        raise CaptionInterrupted('Caption interrupted.')


def run_interruptible_caption_step(description, func, *args, **kwargs):
    raise_if_caption_interrupted()
    result_queue = queue.Queue(maxsize=1)

    def target():
        try:
            result_queue.put((True, func(*args, **kwargs)))
        except BaseException as e:
            result_queue.put((False, e))

    worker = threading.Thread(target=target, daemon=True)
    worker.start()
    while worker.is_alive():
        worker.join(0.15)
        if caption_interrupt_requested():
            joycaption_status['status'] = 'Interrupted'
            _append_joy_log(f"\nInterrupted while {description}.\n")
            raise CaptionInterrupted('Caption interrupted.')

    if result_queue.empty():
        raise RuntimeError(f'{description} ended without a result.')
    ok, payload = result_queue.get()
    if ok:
        raise_if_caption_interrupted()
        return payload
    if isinstance(payload, CaptionInterrupted):
        raise payload
    raise payload


JOYCLI_MODEL_OPTIONS = {}
WD14_MODEL_OPTIONS = {
    "convnextv2": {
        "label": "WD-14 ConvNextV2 v2",
        "repo_id": "SmilingWolf/wd-v1-4-convnextv2-tagger-v2",
        "model_file": "model.onnx",
        "tags_file": "selected_tags.csv",
    },
    "convnext": {
        "label": "WD-14 ConvNext v2",
        "repo_id": "SmilingWolf/wd-v1-4-convnext-tagger-v2",
        "model_file": "model.onnx",
        "tags_file": "selected_tags.csv",
    },
}
WD14_SESSION_CACHE = {}
WD14_TAGS_CACHE = {}
WD14_CACHE_LOCK = threading.Lock()

QWEN3_VL_MODELS = {
    "Qwen3-VL-4B-Instruct": "Qwen/Qwen3-VL-4B-Instruct",
    "Qwen3-VL-8B-Instruct": "Qwen/Qwen3-VL-8B-Instruct",
    "Huihui-Qwen3-VL-8B-Instruct-abliterated": "huihui-ai/Huihui-Qwen3-VL-8B-Instruct-abliterated",
}
QWEN3_VL_LOCAL_LOCK = threading.Lock()
QWEN3_VL_LOCAL_MODEL_ID = None
QWEN3_VL_LOCAL_PROCESSOR = None
QWEN3_VL_LOCAL_MODEL = None
QWEN3_VL_LOCAL_CACHE_DIR = APP_DIR / "models" / "qwen3_vl"
QWEN3_VL_LOCAL_DEFAULT_MAX_IMAGE_SIDE = 512
QWEN3_VL_DEFAULT_SYSTEM_PROMPT = (
    "Create a natural-language image caption for LoRA training.\n\n"
    "Write exactly one concise sentence. Start the caption with [name]. Use [name] as the subject name or training trigger, and mention [name] only once.\n\n"
    "Describe only visible details in the image. Focus on expression, gaze, pose, hair, clothing, framing, setting, lighting, background, and image style when visible.\n\n"
    "Write in natural language, not as comma-separated tags. Do not use bullet points. Do not invent details. Do not describe identity, age, ethnicity, personality, story, intent, body shape, or body proportions unless clearly required by the visible image.\n\n"
    "Do not mention file names, metadata, resolution, image quality, camera model, or that this is an image.\n\n"
    "Keep the caption short and direct, usually 12-30 words. Output only the caption."
)


def qwen3_vl_generation_settings(options):
    ideogram_json = normalize_caption_format((options or {}).get("caption_format")) == CAPTION_FORMAT_IDEOGRAM4_JSON
    system_prompt = IDEOGRAM4_JSON_SYSTEM_PROMPT if ideogram_json else str(
        (options or {}).get("qwen3vl_system_prompt") or QWEN3_VL_DEFAULT_SYSTEM_PROMPT
    ).strip()
    if ideogram_json:
        ideogram_name = " ".join(str((options or {}).get("ideogram4_name") or "").split())
        if ideogram_name:
            system_prompt = (
                f'{system_prompt} Provided subject name: "{ideogram_name}". '
                "This name is user-supplied, not an invented identity. Use it for the main visible subject when appropriate, "
                "inside existing description string fields only, without adding extra JSON keys."
            )
    else:
        qwen_name = " ".join(str((options or {}).get("qwen3vl_name") or "").split())
        if qwen_name:
            system_prompt = system_prompt.replace("[name]", qwen_name)
    temperature = float((options or {}).get("qwen3vl_temperature") or 0.2)
    max_tokens = int((options or {}).get("qwen3vl_max_tokens") or 256)
    if ideogram_json:
        temperature = min(temperature, 0.2)
        max_tokens = max(max_tokens, 1536)
    return system_prompt, temperature, max_tokens


def load_joy_gguf_defaults():
    default = {
        "repo_id": "lmstudio-community/JoyCaption-Beta-One-GGUF",
        "model_dir": "joycaption_models",
        "koboldcpp_exe": "koboldcpp.exe" if sys.platform.startswith("win") else "koboldcpp",
        "api_host": "127.0.0.1",
        "api_port": 5001,
        "mmproj_file": "mmproj-Q8_0.gguf",
        "models": {
            "Q4_K": "JoyCaption-Beta-One-Q4_K.gguf",
            "Q5_K_M": "JoyCaption-Beta-One-Q5_K_M.gguf",
            "Q6_K": "JoyCaption-Beta-One-Q6_K.gguf",
            "Q8_0": "JoyCaption-Beta-One-Q8_0.gguf",
        },
    }
    try:
        if JOY_GGUF_DEFAULTS_PATH.exists():
            data = json.loads(JOY_GGUF_DEFAULTS_PATH.read_text(encoding="utf-8"))
            if isinstance(data, dict):
                merged = default.copy()
                merged.update({k: v for k, v in data.items() if k != "models"})
                models = default["models"].copy()
                if isinstance(data.get("models"), dict):
                    models.update(data["models"])
                merged["models"] = models
                return merged
    except Exception:
        pass
    return default


def build_joycaption_prompt(caption_type, caption_length, extra_options_text, extra_options_selected=None, person_name=""):
    extra_options_selected = list(extra_options_selected or [])
    ct = re.sub(r"[^a-z0-9]+", "_", str(caption_type or "descriptive").strip().lower()).strip("_")
    cl_raw = str(caption_length or "long").strip().lower()
    cl = re.sub(r"[^a-z0-9]+", "_", cl_raw).strip("_")
    style_map = {
        "descriptive": (
            "Write a detailed description for this image.",
            "Write a detailed description for this image in {word_count} words or less.",
            "Write a {length} detailed description for this image.",
        ),
        "descriptive_casual": (
            "Write a descriptive caption for this image in a casual tone.",
            "Write a descriptive caption for this image in a casual tone within {word_count} words.",
            "Write a {length} descriptive caption for this image in a casual tone.",
        ),
        "straightforward": (
            "Write a straightforward, objective caption for this image. Begin with the main subject and medium, focus on concrete visible details, and avoid vague mood language.",
            "Write a straightforward, objective caption for this image within {word_count} words. Begin with the main subject and medium, focus on concrete visible details, and avoid vague mood language.",
            "Write a {length} straightforward, objective caption for this image. Begin with the main subject and medium, focus on concrete visible details, and avoid vague mood language.",
        ),
        "stable_diffusion_prompt": (
            "Output a stable diffusion prompt that could plausibly generate this image.",
            "Output a stable diffusion prompt that could plausibly generate this image in {word_count} words or less.",
            "Output a {length} stable diffusion prompt that could plausibly generate this image.",
        ),
        "midjourney": (
            "Write a MidJourney prompt for this image.",
            "Write a MidJourney prompt for this image within {word_count} words.",
            "Write a {length} MidJourney prompt for this image.",
        ),
        "danbooru_tag_list": (
            "Generate only comma-separated Danbooru tags for this image using lowercase underscores and conventional namespaces when relevant. Do not add extra prose.",
            "Generate only comma-separated Danbooru tags for this image using lowercase underscores and conventional namespaces when relevant. Keep it under {word_count} words. Do not add extra prose.",
            "Generate a {length} comma-separated Danbooru tag list for this image using lowercase underscores and conventional namespaces when relevant. Do not add extra prose.",
        ),
        "e621_tag_list": (
            "Write a comma-separated list of e621 tags in alphabetical order for this image, using namespaced tags when relevant.",
            "Write a comma-separated list of e621 tags in alphabetical order for this image, using namespaced tags when relevant. Keep it under {word_count} words.",
            "Write a {length} comma-separated list of e621 tags in alphabetical order for this image, using namespaced tags when relevant.",
        ),
        "rule34_tag_list": (
            "Write a comma-separated list of rule34 tags in alphabetical order for this image, using artist, copyright, character, and meta prefixes when relevant.",
            "Write a comma-separated list of rule34 tags in alphabetical order for this image, using artist, copyright, character, and meta prefixes when relevant. Keep it under {word_count} words.",
            "Write a {length} comma-separated list of rule34 tags in alphabetical order for this image, using artist, copyright, character, and meta prefixes when relevant.",
        ),
        "rul34_tag_list": (
            "Write a comma-separated list of rule34 tags in alphabetical order for this image, using artist, copyright, character, and meta prefixes when relevant.",
            "Write a comma-separated list of rule34 tags in alphabetical order for this image, using artist, copyright, character, and meta prefixes when relevant. Keep it under {word_count} words.",
            "Write a {length} comma-separated list of rule34 tags in alphabetical order for this image, using artist, copyright, character, and meta prefixes when relevant.",
        ),
        "booru_like_tag_list": (
            "Write a list of Booru-like tags for this image.",
            "Write a list of Booru-like tags for this image within {word_count} words.",
            "Write a {length} list of Booru-like tags for this image.",
        ),
        "art_critic": (
            "Analyze this image like an art critic, including composition, style, symbolism, color, light, and artistic context.",
            "Analyze this image like an art critic within {word_count} words, including composition, style, symbolism, color, light, and artistic context.",
            "Analyze this image like an art critic in a {length} response, including composition, style, symbolism, color, light, and artistic context.",
        ),
        "product_listing": (
            "Write a caption for this image as though it were a product listing.",
            "Write a caption for this image as though it were a product listing. Keep it under {word_count} words.",
            "Write a {length} caption for this image as though it were a product listing.",
        ),
        "social_media_post": (
            "Write a caption for this image as if it were being used for a social media post.",
            "Write a caption for this image as if it were being used for a social media post. Limit the caption to {word_count} words.",
            "Write a {length} caption for this image as if it were being used for a social media post.",
        ),
    }
    length_tokens = {
        "any": 512,
        "very_short": 80,
        "short": 120,
        "medium": 220,
        "medium_length": 220,
        "long": 360,
        "very_long": 520,
    }
    templates = style_map.get(ct, style_map["descriptive"])
    if cl == "any":
        prompt = templates[0]
        max_tokens = length_tokens["any"]
    elif cl_raw.isdigit():
        word_count = max(1, min(1000, int(cl_raw)))
        prompt = templates[1].format(word_count=word_count)
        max_tokens = max(32, min(2048, int(word_count * 2.2) + 32))
    else:
        prompt = templates[2].format(length=cl_raw.replace("_", " "))
        max_tokens = length_tokens.get(cl, length_tokens["long"])
    selected = []
    for item in extra_options_selected:
        item = str(item)
        if "{name}" in item and person_name:
            item = item.replace("{name}", person_name.strip())
        selected.append(item)
    extra_options_text = (extra_options_text or "").strip()
    if selected or extra_options_text:
        prompt += " " + " ".join(selected + ([extra_options_text] if extra_options_text else []))
    return prompt, max_tokens


def ensure_joy_model_files(quantization, hf_token=None):
    from huggingface_hub import hf_hub_download
    cfg = load_joy_gguf_defaults()
    repo_id = cfg["repo_id"]
    model_dir = APP_DIR / cfg["model_dir"]
    model_dir.mkdir(parents=True, exist_ok=True)

    quant_key = str(quantization or "Q4_K").upper()
    model_name = cfg["models"].get(quant_key, cfg["models"]["Q4_K"])
    mmproj_name = cfg["mmproj_file"]

    model_path = hf_hub_download(repo_id=repo_id, filename=model_name, token=(hf_token or None), local_dir=str(model_dir), local_dir_use_symlinks=False)
    mmproj_path = hf_hub_download(repo_id=repo_id, filename=mmproj_name, token=(hf_token or None), local_dir=str(model_dir), local_dir_use_symlinks=False)
    return model_path, mmproj_path, cfg


def ensure_wd14_model_files(model_key, hf_token=None):
    from huggingface_hub import hf_hub_download

    key = str(model_key or "convnextv2").strip().lower()
    cfg = WD14_MODEL_OPTIONS.get(key, WD14_MODEL_OPTIONS["convnextv2"])
    model_dir = APP_DIR / "wd14_models" / key
    model_dir.mkdir(parents=True, exist_ok=True)
    model_path = hf_hub_download(
        repo_id=cfg["repo_id"],
        filename=cfg["model_file"],
        token=(hf_token or None),
        local_dir=str(model_dir),
        local_dir_use_symlinks=False,
    )
    tags_path = hf_hub_download(
        repo_id=cfg["repo_id"],
        filename=cfg["tags_file"],
        token=(hf_token or None),
        local_dir=str(model_dir),
        local_dir_use_symlinks=False,
    )
    return model_path, tags_path, cfg


def load_wd14_tags(tags_path):
    key = str(tags_path)
    cached = WD14_TAGS_CACHE.get(key)
    if cached:
        return cached

    tag_names = []
    rating_indexes = []
    general_indexes = []
    character_indexes = []
    with open(tags_path, 'r', encoding='utf-8', newline='') as f:
        reader = csv.DictReader(f)
        for idx, row in enumerate(reader):
            name = str(row.get('name', '')).strip()
            if not name:
                continue
            tag_names.append(name)
            try:
                cat = int(row.get('category', 0))
            except Exception:
                cat = 0
            if cat == 9:
                rating_indexes.append(idx)
            elif cat == 4:
                character_indexes.append(idx)
            else:
                general_indexes.append(idx)

    data = {
        'tag_names': tag_names,
        'rating_indexes': rating_indexes,
        'general_indexes': general_indexes,
        'character_indexes': character_indexes,
    }
    WD14_TAGS_CACHE[key] = data
    return data


def get_wd14_session(model_key, hf_token=None):
    key = str(model_key or 'convnextv2').strip().lower()
    with WD14_CACHE_LOCK:
        cached = WD14_SESSION_CACHE.get(key)
        if cached is not None:
            return cached

        try:
            import onnxruntime as ort
        except Exception as e:
            raise RuntimeError('WD-14 requires onnxruntime or onnxruntime-gpu to be installed.') from e

        model_path, tags_path, cfg = ensure_wd14_model_files(key, hf_token)
        providers = []
        try:
            available = list(ort.get_available_providers())
        except Exception:
            available = []
        for provider in ['CUDAExecutionProvider', 'CPUExecutionProvider']:
            if provider in available:
                providers.append(provider)
        if not providers:
            providers = None
        session = ort.InferenceSession(model_path, providers=providers)
        tags = load_wd14_tags(tags_path)
        payload = (session, tags, cfg, model_path, tags_path)
        WD14_SESSION_CACHE[key] = payload
        return payload


def prepare_wd14_image(image_path, target_size):
    with Image.open(image_path) as img:
        if img.mode != 'RGBA':
            img = img.convert('RGBA')
        canvas = Image.new('RGBA', img.size, (255, 255, 255, 255))
        canvas.alpha_composite(img)
        rgb = canvas.convert('RGB')
        w, h = rgb.size
        max_dim = max(w, h)
        padded = Image.new('RGB', (max_dim, max_dim), (255, 255, 255))
        padded.paste(rgb, ((max_dim - w) // 2, (max_dim - h) // 2))
        if max_dim != target_size:
            padded = padded.resize((target_size, target_size), Image.Resampling.BICUBIC)
        try:
            import numpy as np
        except Exception as e:
            raise RuntimeError('WD-14 requires numpy to be installed.') from e
        image_array = np.asarray(padded, dtype=np.float32)
        image_array = image_array[:, :, ::-1]
        return np.expand_dims(image_array, axis=0)


def caption_image_with_wd14(image_path, options):
    model_key = options.get('wd14_model', 'convnextv2')
    hf_token = (options.get('hf_token') or '').strip()
    session, tags, cfg, model_path, tags_path = get_wd14_session(model_key, hf_token)
    input_meta = session.get_inputs()[0]
    output_meta = session.get_outputs()[0]
    shape = list(input_meta.shape)
    target_size = 448
    try:
        if len(shape) >= 3 and isinstance(shape[1], int) and shape[1] > 0:
            target_size = int(shape[1])
    except Exception:
        target_size = 448

    image = prepare_wd14_image(image_path, target_size)
    preds = session.run([output_meta.name], {input_meta.name: image})[0]
    probs = preds[0]

    general_threshold = float(options.get('wd14_general_threshold') or 0.35)
    character_threshold = float(options.get('wd14_character_threshold') or 0.85)
    include_rating = bool(options.get('wd14_include_rating'))
    include_characters = bool(options.get('wd14_include_characters'))
    replace_underscores = bool(options.get('wd14_replace_underscores', True))
    undesired_tags_raw = str(options.get('wd14_undesired_tags') or '').strip()

    def fmt(name):
        if replace_underscores and name not in {'0_0', '(o)_(o)', '+_+', '+_-', '._.', '_', '<|>_<|>', '=_=', '>_<', '3_3', '6_9', '>_o', '@_@', '^_^', 'o_o', 'u_u', 'x_x', '|_|', '||_||'}:
            return name.replace('_', ' ')
        return name

    selected = []
    if include_rating and tags['rating_indexes']:
        best_idx = max(tags['rating_indexes'], key=lambda i: float(probs[i]))
        selected.append((fmt(tags['tag_names'][best_idx]), float(probs[best_idx])))

    for idx in tags['general_indexes']:
        score = float(probs[idx])
        if score >= general_threshold:
            selected.append((fmt(tags['tag_names'][idx]), score))

    if include_characters:
        for idx in tags['character_indexes']:
            score = float(probs[idx])
            if score >= character_threshold:
                selected.append((fmt(tags['tag_names'][idx]), score))

    selected.sort(key=lambda x: x[1], reverse=True)
    seen = set()
    tag_list = []
    blocked = {tag.strip().lower() for tag in undesired_tags_raw.split(',') if tag.strip()}
    for tag, _score in selected:
        if tag in seen:
            continue
        seen.add(tag)
        if blocked and tag.strip().lower() in blocked:
            continue
        tag_list.append(tag)
    return ', '.join(tag_list)


def run_wd14_captioning(folder, options):
    model_key = options.get('wd14_model', 'convnextv2')
    _append_joy_log(f'Loading {WD14_MODEL_OPTIONS.get(str(model_key).lower(), WD14_MODEL_OPTIONS["convnextv2"])["label"]}...\n')
    session, tags, cfg, model_path, tags_path = run_interruptible_caption_step(
        'loading WD-14 model',
        get_wd14_session,
        model_key,
        (options.get('hf_token') or '').strip(),
    )
    _append_joy_log(f'Model: {model_path}\n')
    _append_joy_log(f'Tags: {tags_path}\n')

    images = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(IMAGE_EXTENSIONS)]
    target_images = []
    for img_name in images:
        txt_path = os.path.splitext(os.path.join(folder, img_name))[0] + '.txt'
        if should_skip_caption_file(txt_path, options):
            continue
        target_images.append(img_name)
    joycaption_status['total'] = len(target_images)
    joycaption_status['status'] = 'Running'
    for img_name in images:
        if caption_interrupt_requested():
            joycaption_status['status'] = 'Interrupted'
            break
        txt_path = os.path.splitext(os.path.join(folder, img_name))[0] + '.txt'
        if should_skip_caption_file(txt_path, options):
            _append_joy_log(f'Skipping existing caption: {img_name}\n')
            continue
        image_path = os.path.join(folder, img_name)
        caption = run_interruptible_caption_step(
            f'tagging {img_name} with WD-14',
            caption_image_with_wd14,
            image_path,
            options,
        )
        if caption_interrupt_requested():
            joycaption_status['status'] = 'Interrupted'
            break
        write_caption_result(txt_path, caption, options)
        joycaption_status['count'] += 1
        _append_joy_log(f'Tag-captioned {img_name}\n')


def _stream_kobold_output(proc):
    try:
        if not proc or not proc.stdout:
            return
        for line in proc.stdout:
            _append_joy_log(line)
    except Exception:
        pass


def stop_kobold_process():
    global KOBOLD_PROCESS
    with KOBOLD_PROCESS_LOCK:
        proc = KOBOLD_PROCESS
        KOBOLD_PROCESS = None
    if not proc:
        return
    try:
        proc.terminate()
        proc.wait(timeout=10)
    except Exception:
        try:
            proc.kill()
        except Exception:
            pass


def start_kobold_process(model_path, mmproj_path, visionmaxres):
    global KOBOLD_PROCESS
    cfg = load_joy_gguf_defaults()
    kobold_exe = str(APP_DIR / cfg["koboldcpp_exe"])
    host = cfg.get("api_host", "127.0.0.1")
    port = int(cfg.get("api_port", 5001))
    if not os.path.exists(kobold_exe):
        raise FileNotFoundError(f"KoboldCpp executable not found: {kobold_exe}")

    raise_if_caption_interrupted()
    stop_kobold_process()
    args = [kobold_exe, '--model', model_path, '--mmproj', mmproj_path, '--host', host, '--port', str(port), '--visionmaxres', str(int(visionmaxres or 512)), '--quiet']
    proc = subprocess.Popen(args, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, encoding='utf-8', errors='replace', cwd=str(APP_DIR), bufsize=1, **hidden_subprocess_kwargs())
    with KOBOLD_PROCESS_LOCK:
        KOBOLD_PROCESS = proc
    threading.Thread(target=_stream_kobold_output, args=(proc,), daemon=True).start()

    base_url = f'http://{host}:{port}'
    deadline = time.time() + 300
    last_err = None
    while time.time() < deadline:
        if caption_interrupt_requested():
            stop_kobold_process()
            raise CaptionInterrupted('Caption interrupted.')
        if proc.poll() is not None:
            raise RuntimeError(f'KoboldCpp exited early with code {proc.returncode}')
        try:
            r = requests.get(base_url + '/api/v1/info/version', timeout=5)
            if r.ok:
                return proc, base_url
        except Exception as e:
            last_err = e
        time.sleep(2)
    raise RuntimeError(f'KoboldCpp did not become ready in time. Last error: {last_err}')


def openai_chat_completions_url(base_url):
    url = str(base_url or "").strip().rstrip("/")
    if not url:
        raise ValueError("External API URL is required.")
    if url.endswith("/v1/chat/completions") or url.endswith("/chat/completions"):
        return url
    if url.endswith("/v1"):
        return url + "/chat/completions"
    return url + "/v1/chat/completions"


def external_api_generation_settings(options):
    options = options or {}
    model_id = str(options.get("external_api_model") or "").strip()
    if not model_id:
        raise ValueError("External API model ID is required.")
    api_url = openai_chat_completions_url(options.get("external_api_url"))
    api_key = str(options.get("external_api_key") or "").strip()
    ideogram_json = normalize_caption_format(options.get("caption_format")) == CAPTION_FORMAT_IDEOGRAM4_JSON
    system_prompt = IDEOGRAM4_JSON_SYSTEM_PROMPT if ideogram_json else str(
        options.get("external_api_system_prompt") or QWEN3_VL_DEFAULT_SYSTEM_PROMPT
    ).strip()
    if ideogram_json:
        ideogram_name = " ".join(str(options.get("ideogram4_name") or "").split())
        if ideogram_name:
            system_prompt = (
                f'{system_prompt} Provided subject name: "{ideogram_name}". '
                "Use it for the main visible subject when appropriate inside existing description fields only, "
                "without adding extra JSON keys."
            )
    else:
        external_name = " ".join(str(options.get("external_api_name") or "").split())
        if external_name:
            system_prompt = system_prompt.replace("[name]", external_name)
    temperature = float(options.get("external_api_temperature") or 0.2)
    max_tokens = max(1, int(float(options.get("external_api_max_tokens") or 256)))
    return api_url, model_id, api_key, system_prompt, temperature, max_tokens


def get_qwen3_vl_model_id(model_name):
    if model_name not in QWEN3_VL_MODELS:
        raise ValueError(f"Unknown Qwen3-VL model: {model_name}")
    return QWEN3_VL_MODELS[model_name]


def get_qwen3_vl_local_max_image_side(options):
    try:
        value = int(float((options or {}).get("qwen3vl_max_image_side") or QWEN3_VL_LOCAL_DEFAULT_MAX_IMAGE_SIDE))
    except Exception:
        value = QWEN3_VL_LOCAL_DEFAULT_MAX_IMAGE_SIDE
    return max(128, min(4096, value))


def load_qwen3_vl_local_model(model_name, options):
    global QWEN3_VL_LOCAL_MODEL_ID
    global QWEN3_VL_LOCAL_PROCESSOR
    global QWEN3_VL_LOCAL_MODEL

    model_id = get_qwen3_vl_model_id(model_name)
    hf_token = str((options or {}).get("hf_token") or "").strip() or None

    with QWEN3_VL_LOCAL_LOCK:
        if (
            QWEN3_VL_LOCAL_MODEL is not None
            and QWEN3_VL_LOCAL_PROCESSOR is not None
            and QWEN3_VL_LOCAL_MODEL_ID == model_id
        ):
            return QWEN3_VL_LOCAL_PROCESSOR, QWEN3_VL_LOCAL_MODEL

        try:
            import torch
            import transformers
            from transformers import AutoModelForImageTextToText, AutoProcessor
        except Exception as e:
            raise RuntimeError(
                "Qwen3-VL local mode requires torch and transformers. "
                "Run install.bat to update the environment. "
                f"Original error: {e}"
            )

        try:
            transformers_major = int(str(transformers.__version__).split(".", 1)[0])
        except Exception:
            transformers_major = 0
        if transformers_major >= 5:
            raise RuntimeError(
                "Qwen3-VL local mode currently requires transformers 4.57.x. "
                f"This environment has transformers {transformers.__version__}, "
                "which can fail while loading the Qwen3-VL processor. "
                "Run install.bat again and allow it to recreate .venv."
            )

        QWEN3_VL_LOCAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        _append_joy_log(f"Loading local Qwen3-VL model: {model_id}\n")
        _append_joy_log("First startup downloads the model and may take a while.\n")

        common_kwargs = {
            "cache_dir": str(QWEN3_VL_LOCAL_CACHE_DIR),
            "trust_remote_code": True,
        }
        if hf_token:
            common_kwargs["token"] = hf_token

        processor = AutoProcessor.from_pretrained(model_id, **common_kwargs)

        model_kwargs = {
            **common_kwargs,
            "device_map": "auto",
            "dtype": "auto",
        }
        try:
            model = AutoModelForImageTextToText.from_pretrained(
                model_id,
                **model_kwargs,
            )
        except TypeError:
            model_kwargs.pop("dtype", None)
            model_kwargs["torch_dtype"] = "auto"
            model = AutoModelForImageTextToText.from_pretrained(
                model_id,
                **model_kwargs,
            )

        try:
            model.eval()
        except Exception:
            pass

        QWEN3_VL_LOCAL_MODEL_ID = model_id
        QWEN3_VL_LOCAL_PROCESSOR = processor
        QWEN3_VL_LOCAL_MODEL = model

        device = getattr(model, "device", None)
        _append_joy_log(f"Local Qwen3-VL ready on {device or 'auto device map'}.\n")
        device_map = getattr(model, "hf_device_map", None)
        if isinstance(device_map, dict):
            devices = sorted({str(value) for value in device_map.values()})
            _append_joy_log(f"Qwen3-VL device map: {', '.join(devices)}.\n")
            if any(value in {"cpu", "disk"} for value in devices):
                _append_joy_log("CPU/disk offload is active; first captions can be very slow.\n")
        return processor, model


def caption_image_with_qwen3_vl_local(image_path, options):
    model_name = options.get("qwen3vl_model", "Qwen3-VL-4B-Instruct")
    processor, model = load_qwen3_vl_local_model(model_name, options)

    try:
        import torch
    except Exception as e:
        raise RuntimeError(f"Could not import torch for Qwen3-VL local mode: {e}")

    system_prompt, temperature, max_tokens = qwen3_vl_generation_settings(options)
    max_image_side = get_qwen3_vl_local_max_image_side(options)
    max_pixels = max_image_side * max_image_side

    with Image.open(image_path) as im:
        image = ImageOps.exif_transpose(im).convert("RGB")
        original_size = image.size
        if max(image.size) > max_image_side:
            image = ImageOps.contain(
                image,
                (max_image_side, max_image_side),
                Image.Resampling.LANCZOS,
            )
            _append_joy_log(
                f"Resized image for local Qwen3-VL: {original_size[0]}x{original_size[1]} -> {image.width}x{image.height}.\n"
            )

    user_prompt = "Describe this image."
    if system_prompt:
        user_prompt = f"{system_prompt}\n\n{user_prompt}"

    messages = [
        {
            "role": "user",
            "content": [
                {
                    "type": "image",
                    "image": image,
                    "max_pixels": max_pixels,
                },
                {"type": "text", "text": user_prompt},
            ],
        },
    ]

    _append_joy_log(f"Preparing local Qwen3-VL inputs for {os.path.basename(image_path)}...\n")
    try:
        inputs = processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_dict=True,
            return_tensors="pt",
        )
    except TypeError:
        inputs = processor.apply_chat_template(
            messages,
            tokenize=True,
            add_generation_prompt=True,
            return_tensors="pt",
        )

    try:
        inputs = inputs.to(model.device)
    except Exception:
        pass

    _append_joy_log(f"Generating local Qwen3-VL caption for {os.path.basename(image_path)}...\n")
    generate_kwargs = {
        "max_new_tokens": max_tokens,
    }
    if temperature > 0:
        generate_kwargs.update({
            "do_sample": True,
            "temperature": temperature,
        })
    else:
        generate_kwargs["do_sample"] = False

    try:
        from transformers import StoppingCriteria, StoppingCriteriaList

        class CaptionInterruptCriteria(StoppingCriteria):
            def __call__(self, input_ids, scores, **kwargs):
                return caption_interrupt_requested()

        generate_kwargs["stopping_criteria"] = StoppingCriteriaList([
            CaptionInterruptCriteria()
        ])
    except Exception:
        pass

    with torch.inference_mode():
        output_ids = model.generate(**inputs, **generate_kwargs)

    input_ids = inputs["input_ids"]
    trimmed = [
        out_ids[len(in_ids):]
        for in_ids, out_ids in zip(input_ids, output_ids)
    ]
    output_text = processor.batch_decode(
        trimmed,
        skip_special_tokens=True,
        clean_up_tokenization_spaces=False,
    )
    return str(output_text[0] if output_text else "").strip()


def caption_image_with_kobold(image_path, prompt, max_tokens, temperature, top_p, base_url):
    mime = 'image/png'
    ext = Path(image_path).suffix.lower()
    if ext in ['.jpg', '.jpeg']:
        mime = 'image/jpeg'
    elif ext == '.webp':
        mime = 'image/webp'
    elif ext == '.gif':
        mime = 'image/gif'
    elif ext == '.bmp':
        mime = 'image/bmp'
    with open(image_path, 'rb') as f:
        b64 = base64.b64encode(f.read()).decode('ascii')
    data_url = f'data:{mime};base64,{b64}'
    payload = {
        'model': 'koboldcpp',
        'messages': [
            {'role': 'user', 'content': [
                {'type': 'text', 'text': prompt},
                {'type': 'image_url', 'image_url': {'url': data_url}},
            ]}
        ],
        'max_tokens': int(max_tokens),
        'temperature': float(temperature),
        'top_p': float(top_p),
    }
    r = requests.post(base_url + '/v1/chat/completions', json=payload, timeout=600)
    if not r.ok:
        raise RuntimeError(f'KoboldCpp API error {r.status_code}: {r.text[:500]}')
    data = r.json()
    try:
        content = data['choices'][0]['message']['content']
    except Exception:
        raise RuntimeError(f'Unexpected KoboldCpp response: {data}')
    if isinstance(content, list):
        content = ''.join(part.get('text', '') for part in content if isinstance(part, dict))
    return str(content).strip()


def caption_image_with_qwen3_vl(image_path, options):
    return caption_image_with_qwen3_vl_local(image_path, options)


def caption_image_with_external_api(image_path, options):
    api_url, model_id, api_key, system_prompt, temperature, max_tokens = (
        external_api_generation_settings(options)
    )

    ext = Path(image_path).suffix.lower()
    mime = "image/png"
    if ext in [".jpg", ".jpeg"]:
        mime = "image/jpeg"
    elif ext == ".webp":
        mime = "image/webp"
    elif ext == ".gif":
        mime = "image/gif"
    elif ext == ".bmp":
        mime = "image/bmp"
    elif ext == ".avif":
        mime = "image/avif"

    with open(image_path, "rb") as f:
        b64 = base64.b64encode(f.read()).decode("ascii")
    data_url = f"data:{mime};base64,{b64}"

    payload = {
        "model": model_id,
        "messages": [
            {
                "role": "system",
                "content": system_prompt,
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "text",
                        "text": "Describe this image.",
                    },
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": data_url,
                        },
                    },
                ],
            },
        ],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    if options.get("external_api_disable_thinking", True):
        payload["chat_template_kwargs"] = {"enable_thinking": False}
        payload["reasoning_budget"] = 0

    headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
    r = requests.post(api_url, json=payload, headers=headers, timeout=600)
    if not r.ok:
        raise RuntimeError(
            f"External API error {r.status_code}: {r.text[:500]}"
        )

    data = r.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError(f"Unexpected External API response: {data}")

    if isinstance(content, list):
        content = "".join(
            x.get("text", "")
            for x in content
            if isinstance(x, dict)
        )

    content = str(content).strip()
    if not content:
        raise RuntimeError(
            "External API returned an empty caption. Disable thinking/reasoning "
            "or increase Max tokens, then try again."
        )
    return content


def run_qwen3_vl_captioning(folder, options):
    model_name = options.get("qwen3vl_model", "Qwen3-VL-4B-Instruct")
    caption_format = normalize_caption_format(options.get("caption_format"))
    write_options = dict(options)
    if caption_format == CAPTION_FORMAT_IDEOGRAM4_JSON:
        write_options["ideogram4_swap_bbox_xyxy_to_yxyx"] = True
        _append_joy_log("Ideogram JSON: converting Qwen bbox order to [y_min,x_min,y_max,x_max] before saving.\n")

    _append_joy_log(f"Preparing {model_name}...\n")
    _append_joy_log("Using built-in Qwen3-VL Transformers backend.\n")
    run_interruptible_caption_step(
        f"loading {model_name}",
        load_qwen3_vl_local_model,
        model_name,
        options,
    )

    images = [
        f for f in sorted(os.listdir(folder))
        if f.lower().endswith(IMAGE_EXTENSIONS)
    ]

    target_images = []
    for img_name in images:
        txt_path = caption_sidecar_path(os.path.join(folder, img_name), caption_format)

        if should_skip_caption_file(txt_path, options):
            _append_joy_log(f"Skipping existing caption: {img_name}\n")
            continue

        target_images.append(img_name)

    joycaption_status["total"] = len(target_images)
    joycaption_status["status"] = "Running"

    for img_name in target_images:
        if caption_interrupt_requested():
            joycaption_status["status"] = "Interrupted"
            break

        image_path = os.path.join(folder, img_name)
        txt_path = caption_sidecar_path(image_path, caption_format)

        joycaption_status["status"] = f"Captioning {img_name}"
        _append_joy_log(f"Captioning {img_name} with Qwen3-VL...\n")
        caption = run_interruptible_caption_step(
            f'captioning {img_name} with Qwen3-VL',
            caption_image_with_qwen3_vl,
            image_path,
            options,
        )
        if caption_interrupt_requested():
            joycaption_status["status"] = "Interrupted"
            break
        try:
            write_caption_result(txt_path, caption, write_options)
        except ValueError as exc:
            _append_joy_log(f"Skipped invalid Ideogram JSON for {img_name}: {exc}\n")
            joycaption_status["count"] += 1
            continue

        joycaption_status["count"] += 1
        _append_joy_log(f"Captioned {img_name} -> {Path(txt_path).name}\n")


def run_external_api_captioning(folder, options):
    api_url, model_id, _api_key, _prompt, _temperature, _max_tokens = (
        external_api_generation_settings(options)
    )
    _append_joy_log(f"Using External API model: {model_id}\n")
    _append_joy_log(f"Endpoint: {api_url}\n")

    caption_format = normalize_caption_format(options.get("caption_format"))
    images = [
        name for name in sorted(os.listdir(folder))
        if name.lower().endswith(IMAGE_EXTENSIONS)
    ]
    target_images = []
    for img_name in images:
        txt_path = caption_sidecar_path(os.path.join(folder, img_name), caption_format)
        if should_skip_caption_file(txt_path, options):
            _append_joy_log(f"Skipping existing caption: {img_name}\n")
            continue
        target_images.append(img_name)

    joycaption_status["total"] = len(target_images)
    joycaption_status["status"] = "Running"
    for img_name in target_images:
        if caption_interrupt_requested():
            joycaption_status["status"] = "Interrupted"
            break
        image_path = os.path.join(folder, img_name)
        txt_path = caption_sidecar_path(image_path, caption_format)
        joycaption_status["status"] = f"Captioning {img_name}"
        _append_joy_log(f"Captioning {img_name} with External API...\n")
        caption = run_interruptible_caption_step(
            f"captioning {img_name} with External API",
            caption_image_with_external_api,
            image_path,
            options,
        )
        if caption_interrupt_requested():
            joycaption_status["status"] = "Interrupted"
            break
        try:
            write_caption_result(txt_path, caption, options)
        except ValueError as exc:
            _append_joy_log(f"Skipped invalid Ideogram JSON for {img_name}: {exc}\n")
            joycaption_status["count"] += 1
            continue
        joycaption_status["count"] += 1
        _append_joy_log(f"Captioned {img_name} -> {Path(txt_path).name}\n")


def joycaption_worker(folder, options):
    global joycaption_status, pairs_cache
    joycaption_status['running'] = True
    joycaption_status['status'] = 'Preparing model'
    joycaption_status['log'] = ''
    joycaption_status['count'] = 0
    joycaption_status['total'] = 0
    joycaption_status['interrupt_requested'] = False
    joycaption_status['reload_pairs'] = False
    joycaption_status['process'] = None
    backend = str((options or {}).get('backend') or 'joycaption').strip().lower()
    options['caption_format'] = normalize_caption_format(options.get('caption_format'))
    if options['caption_format'] == CAPTION_FORMAT_IDEOGRAM4_JSON and backend != 'external_api':
        backend = 'qwen3_vl'
        options['backend'] = backend
        options['append_existing'] = False
    try:
        if backend == 'wd14':
            run_wd14_captioning(folder, options)
        elif backend == 'qwen3_vl':
            run_qwen3_vl_captioning(folder, options)
        elif backend == 'external_api':
            run_external_api_captioning(folder, options)
        else:
            prompt, auto_max_tokens = build_joycaption_prompt(options.get('caption_type', 'descriptive'), options.get('caption_length', 'long'), options.get('extra_options', ''), options.get('extra_options_selected', []), options.get('person_name', ''))
            max_tokens = int(options.get('max_tokens') or 0)
            if max_tokens <= 0:
                max_tokens = auto_max_tokens
            temperature = float(options.get('temperature') or 0.6)
            top_p = float(options.get('top_p') or 0.9)
            visionmaxres = int(options.get('visionmaxres') or 512)
            quantization = options.get('quantization', 'Q4_K')
            hf_token = (options.get('hf_token') or '').strip()

            _append_joy_log(f'Loading JoyCaption Beta One GGUF ({quantization})...\n')
            model_path, mmproj_path, cfg = run_interruptible_caption_step(
                'loading JoyCaption model files',
                ensure_joy_model_files,
                quantization,
                hf_token,
            )
            _append_joy_log(f'Model: {model_path}\n')
            _append_joy_log(f'mmproj: {mmproj_path}\n')

            joycaption_status['status'] = 'Starting KoboldCpp'
            proc, base_url = run_interruptible_caption_step(
                'starting KoboldCpp',
                start_kobold_process,
                model_path,
                mmproj_path,
                visionmaxres,
            )
            joycaption_status['process'] = proc
            _append_joy_log(f'KoboldCpp ready at {base_url}\n')

            images = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(IMAGE_EXTENSIONS)]
            target_images = []
            for img_name in images:
                txt_path = os.path.splitext(os.path.join(folder, img_name))[0] + '.txt'
                if should_skip_caption_file(txt_path, options):
                    continue
                target_images.append(img_name)
            joycaption_status['total'] = len(target_images)
            joycaption_status['status'] = 'Running'
            for img_name in images:
                if caption_interrupt_requested():
                    joycaption_status['status'] = 'Interrupted'
                    break
                txt_path = os.path.splitext(os.path.join(folder, img_name))[0] + '.txt'
                if should_skip_caption_file(txt_path, options):
                    _append_joy_log(f'Skipping existing caption: {img_name}\n')
                    continue
                image_path = os.path.join(folder, img_name)
                caption = run_interruptible_caption_step(
                    f'captioning {img_name} with JoyCaption',
                    caption_image_with_kobold,
                    image_path,
                    prompt,
                    max_tokens,
                    temperature,
                    top_p,
                    base_url,
                )
                if caption_interrupt_requested():
                    joycaption_status['status'] = 'Interrupted'
                    break
                write_caption_result(txt_path, caption, options)
                joycaption_status['count'] += 1
                _append_joy_log(f'Captioned {img_name}\n')

        if (
            joycaption_status.get('running')
            and joycaption_status.get('status') != 'Interrupted'
        ):
            joycaption_status['status'] = 'Finished'
        pairs_cache = load_pairs(folder)
        joycaption_status['reload_pairs'] = True
    except CaptionInterrupted:
        joycaption_status['status'] = 'Interrupted'
        _append_joy_log('\nCaption interrupted.\n')
    except Exception as e:
        if joycaption_status.get('interrupt_requested'):
            joycaption_status['status'] = 'Interrupted'
            _append_joy_log('\nCaption interrupted.\n')
        else:
            joycaption_status['status'] = f'Error: {e}'
            _append_joy_log(f'\n{e}\n')
            _append_joy_log(traceback.format_exc(limit=8))
    finally:
        stop_kobold_process()
        joycaption_status['running'] = False
        joycaption_status['process'] = None



def make_kohya_bucket_resolutions(base_resolution, divisible=64):
    max_reso = (base_resolution, base_resolution)
    min_size = base_resolution // 2
    max_size = base_resolution * 2
    max_width, max_height = max_reso
    max_area = max_width * max_height

    resos = set()

    width = int(math.sqrt(max_area) // divisible) * divisible
    resos.add((width, width))

    width = min_size
    while width <= max_size:
        height = min(max_size, int((max_area // width) // divisible) * divisible)
        if height >= min_size:
            resos.add((width, height))
            resos.add((height, width))
        width += divisible

    resos = list(resos)
    resos.sort()
    return resos


def get_bucket_options(base_resolution):
    if base_resolution in KOHYA_BUCKETS:
        return KOHYA_BUCKETS[base_resolution]

    if base_resolution == 1280:
        return make_kohya_bucket_resolutions(1280, 64)

    if base_resolution == 1536:
        return make_kohya_bucket_resolutions(1536, 64)

    return KOHYA_BUCKETS[1024]


def detect_base_resolution(width, height):
    for base in [512, 768, 1024, 1280, 1536]:
        if (width, height) in get_bucket_options(base):
            return base
    return None


def get_aspect_label(width, height):
    gcd = math.gcd(width, height) or 1
    ratio_w = width // gcd
    ratio_h = height // gcd
    return f"{ratio_w}:{ratio_h}"


def get_predefined_kohya_aspect_labels():
    aspect_labels = set()
    for base in [512, 768, 1024, 1280, 1536]:
        for width, height in get_bucket_options(base):
            aspect_labels.add(get_aspect_label(width, height))
    return sorted(aspect_labels, key=lambda label: (lambda parts: (parts[0] / parts[1]) if parts[1] else 0)(tuple(int(x) for x in label.split(':'))))



def choose_auto_crop_base_resolution(folder):
    image_names = [f for f in sorted(os.listdir(folder)) if f.lower().endswith(IMAGE_EXTENSIONS)]
    if not image_names:
        return selected_crop_base

    base_options = [512, 768, 1024, 1280, 1536]
    votes = defaultdict(int)

    for img_name in image_names:
        try:
            with Image.open(os.path.join(folder, img_name)) as img:
                width, height = img.size
        except Exception:
            continue

        exact_base = detect_base_resolution(width, height)
        if exact_base:
            votes[exact_base] += 1
            continue

        best_base = None
        best_score = None
        src_aspect = width / max(height, 1)

        for base in base_options:
            for bw, bh in get_bucket_options(base):
                bucket_aspect = bw / max(bh, 1)
                aspect_diff = abs(src_aspect - bucket_aspect)
                size_diff = (((width - bw) ** 2 + (height - bh) ** 2) ** 0.5) / max(base, 1)
                score = aspect_diff * 8.0 + size_diff
                if best_score is None or score < best_score:
                    best_score = score
                    best_base = base

        if best_base is not None:
            votes[best_base] += 1

    if not votes:
        return selected_crop_base

    return max(votes.items(), key=lambda item: (item[1], item[0]))[0]

def load_pairs(folder):
    pairs = []
    for img_name in sorted(os.listdir(folder)):
        if img_name.lower().endswith(IMAGE_EXTENSIONS):
            img_path = os.path.join(folder, img_name)
            txt_path = os.path.splitext(img_path)[0] + ".txt"
            caption = ""
            if os.path.exists(txt_path):
                with open(txt_path, "r", encoding="utf-8") as f:
                    caption = f.read().strip()
            pairs.append((img_name, caption))
    return pairs


def pair_exists(folder, img_name):
    return os.path.exists(os.path.join(folder, img_name))

def make_unique_image_name(folder, stem, ext, exclude_name=None):
    ext = str(ext or "")
    stem = re.sub(r'\s+', ' ', str(stem or '')).strip().rstrip('.')
    if not stem:
        stem = 'image'
    candidate = f"{stem}{ext}"
    counter = 2
    while True:
        candidate_path = os.path.join(folder, candidate)
        if (exclude_name and candidate == exclude_name) or not os.path.exists(candidate_path):
            txt_path = os.path.splitext(candidate_path)[0] + ".txt"
            if exclude_name and candidate == exclude_name:
                return candidate
            if not os.path.exists(txt_path):
                return candidate
        candidate = f"{stem}_copy" + (f"_{counter}" if counter > 2 else "") + f"{ext}"
        counter += 1


def upload_image_extension(storage):
    filename = storage.filename or ""
    ext = Path(filename).suffix.lower()
    if ext in IMAGE_EXTENSIONS:
        return ext
    return IMAGE_MIME_TO_EXTENSION.get((storage.mimetype or "").lower())


def clean_upload_stem(filename, fallback="image"):
    stem = Path(filename or "").stem
    stem = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', '_', stem)
    stem = re.sub(r'\s+', ' ', stem).strip().strip('.')
    return stem or fallback


def save_uploaded_image(target_path, data, convert_to_png=False):
    if convert_to_png:
        with Image.open(io.BytesIO(data)) as im:
            save_im = ImageOps.exif_transpose(im)
            if getattr(save_im, 'mode', None) not in ('RGB', 'RGBA', 'L', 'LA'):
                has_alpha = 'A' in save_im.getbands() or 'transparency' in getattr(save_im, 'info', {})
                save_im = save_im.convert('RGBA' if has_alpha else 'RGB')
            save_im.save(target_path, format='PNG', compress_level=0, optimize=False)
        return
    target_path.write_bytes(data)


def get_image_info(img_file):
    img_path = os.path.join(current_folder, img_file)
    with Image.open(img_path) as img:
        width, height = img.size
        aspect = width / height if height else 1
    return width, height, aspect


def build_pair_dict(index, img_name, text):
    width, height, aspect = get_image_info(img_name)
    aspect_label = get_aspect_label(width, height)
    detected_base = detect_base_resolution(width, height)
    ratio_display = f"{width}×{height} ({aspect_label})"
    if detected_base and detected_base != selected_crop_base:
        ratio_display += f" • {detected_base}"
    category = get_pair_category(img_name)
    return {
        "index": index,
        "img_name": img_name,
        "text": text,
        "width": width,
        "height": height,
        "aspect": aspect,
        "aspect_label": aspect_label,
        "ratio_display": ratio_display,
        "category": category,
        "category_icon": category_icon_for_name(category),
    }


def get_category_meta_path(folder):
    return Path(folder) / CATEGORY_META_FILENAME


def default_category_groups():
    return [dict(item) for item in DEFAULT_CATEGORY_GROUPS]


def normalize_category_group_id(value):
    value = re.sub(r"[^a-z0-9_-]+", "-", str(value or "").strip().lower()).strip("-")
    return value[:80]


def normalize_category_groups(raw_groups):
    source = raw_groups if isinstance(raw_groups, list) else default_category_groups()
    groups = []
    existing_ids = set()
    existing_names = set()
    for index, raw in enumerate(source):
        item = raw if isinstance(raw, dict) else {}
        fallback = DEFAULT_CATEGORY_GROUPS[index] if index < len(DEFAULT_CATEGORY_GROUPS) else {}
        name = re.sub(r"\s+", " ", str(item.get("name") or fallback.get("name") or f"Group {index + 1}")).strip()
        if not name:
            name = f"Group {index + 1}"
        base_name = name
        counter = 2
        while name.casefold() in existing_names:
            name = f"{base_name} {counter}"
            counter += 1
        existing_names.add(name.casefold())
        group_id = normalize_category_group_id(item.get("id") or fallback.get("id") or name)
        if not group_id:
            group_id = f"group-{index + 1}"
        base_id = group_id
        counter = 2
        while group_id in existing_ids:
            group_id = f"{base_id}-{counter}"
            counter += 1
        existing_ids.add(group_id)
        color = str(item.get("color") or fallback.get("color") or DEFAULT_CATEGORY_COLORS[index % len(DEFAULT_CATEGORY_COLORS)])
        if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
            color = DEFAULT_CATEGORY_COLORS[index % len(DEFAULT_CATEGORY_COLORS)]
        groups.append({"id": group_id, "name": name, "color": color})

    if not any(item["id"] == DEFAULT_CATEGORY_GROUP_ID for item in groups):
        groups.append(dict(DEFAULT_CATEGORY_GROUPS[-1]))
    return groups


def infer_category_group_id(name, groups=None):
    name = str(name or "").strip().casefold()
    candidate = DEFAULT_CATEGORY_GROUP_ID
    if name.startswith("close-up"):
        candidate = "close-up"
    elif name.startswith("medium"):
        candidate = "medium"
    elif name.startswith("full body") or name.startswith("fullbody"):
        candidate = "full-body"
    valid_ids = {item.get("id") for item in (groups or category_groups or default_category_groups())}
    return candidate if candidate in valid_ids else DEFAULT_CATEGORY_GROUP_ID


def category_group_for_id(group_id, groups=None):
    source = groups if groups is not None else category_groups
    return next((item for item in source if item.get("id") == group_id), None)


def default_category_folders(groups=None):
    groups = groups or default_category_groups()
    folders = []
    columns = 5
    for i, item in enumerate(CATEGORY_DEFS):
        folders.append({
            "id": f"folder-{hashlib.sha1(item['name'].encode('utf-8')).hexdigest()[:12]}",
            "name": item["name"],
            "icon": item["icon"],
            "color": DEFAULT_CATEGORY_COLORS[i % len(DEFAULT_CATEGORY_COLORS)],
            "group_id": infer_category_group_id(item["name"], groups),
            "x": (i % columns) * 112,
            "y": (i // columns) * 120,
        })
    return folders


def category_names_from_folders(folders=None):
    source = folders if folders is not None else category_folders
    if not source:
        source = default_category_folders()
    names = set()
    for item in source:
        name = item.get("name") if isinstance(item, dict) else None
        if name:
            names.add(name)
    return names


def category_icon_for_name(name):
    for item in category_folders:
        if item.get("name") == name:
            return item.get("icon") or CATEGORY_NAME_TO_ICON.get(DEFAULT_CATEGORY)
    return CATEGORY_NAME_TO_ICON.get(name, CATEGORY_NAME_TO_ICON[DEFAULT_CATEGORY])


def is_valid_category_icon(icon):
    raw = str(icon or "")
    icon = Path(raw).name
    if not icon or icon != raw:
        return False
    if Path(icon).suffix.lower() not in IMAGE_EXTENSIONS:
        return False
    return (APP_DIR / "images" / icon).exists()


def normalize_category_name(name, folders=None):
    legacy_map = {
        "Front Portrait": "Close-up Front",
        "Profile Portrait": "Close-up Right",
        "Close-up Profile": "Close-up Right",
        "Front Knee-up": "Medium Front",
        "Profile Knee-up": "Medium Profile",
        "Back Knee-up": "Medium Back",
        "Front Fullbody": "Full body Front",
        "Profile Fullbody": "Full body Profile",
        "Back Fullbody": "Full body Back",
    }
    if name in legacy_map:
        name = legacy_map[name]
    source = folders if folders is not None else category_folders
    item = next((entry for entry in source if entry.get("id") == name or entry.get("name") == name), None)
    if item:
        return item.get("name")
    if name in category_names_from_folders(folders):
        return name
    return DEFAULT_CATEGORY


def normalize_category_folder(item, index, existing_names, existing_ids=None, groups=None):
    source = item if isinstance(item, dict) else {}
    existing_ids = existing_ids if existing_ids is not None else set()
    groups = groups or category_groups or default_category_groups()
    fallback = CATEGORY_DEFS[index]["name"] if index < len(CATEGORY_DEFS) else f"Category {index + 1}"
    raw_name = str(source.get("name") or fallback).strip() or fallback
    name = raw_name
    counter = 2
    while name in existing_names and name != DEFAULT_CATEGORY:
        name = f"{raw_name} {counter}"
        counter += 1
    existing_names.add(name)
    folder_id = normalize_category_group_id(source.get("id"))
    if not folder_id:
        folder_id = f"folder-{hashlib.sha1(name.encode('utf-8')).hexdigest()[:12]}"
    base_id = folder_id
    counter = 2
    while folder_id in existing_ids:
        folder_id = f"{base_id}-{counter}"
        counter += 1
    existing_ids.add(folder_id)
    icon = str(source.get("icon") or CATEGORY_NAME_TO_ICON.get(name) or CATEGORY_NAME_TO_ICON[DEFAULT_CATEGORY])
    known_icons = {entry["icon"] for entry in CATEGORY_DEFS}
    if icon not in known_icons and not is_valid_category_icon(icon):
        icon = CATEGORY_NAME_TO_ICON[DEFAULT_CATEGORY]
    color = str(source.get("color") or DEFAULT_CATEGORY_COLORS[index % len(DEFAULT_CATEGORY_COLORS)])
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        color = DEFAULT_CATEGORY_COLORS[index % len(DEFAULT_CATEGORY_COLORS)]
    try:
        x = int(source.get("x", (index % 5) * 112))
        y = int(source.get("y", (index // 5) * 120))
    except Exception:
        x = (index % 5) * 112
        y = (index // 5) * 120
    group_id = normalize_category_group_id(source.get("group_id"))
    if not category_group_for_id(group_id, groups):
        group_id = infer_category_group_id(name, groups)
    return {
        "id": folder_id,
        "name": name,
        "icon": icon,
        "color": color,
        "group_id": group_id,
        "x": max(0, x),
        "y": max(0, y),
    }


def normalize_category_folders(raw_folders, groups=None):
    groups = groups or category_groups or default_category_groups()
    source = raw_folders if isinstance(raw_folders, list) else default_category_folders(groups)
    folders = []
    existing_names = set()
    existing_ids = set()
    for i, item in enumerate(source):
        folder = normalize_category_folder(item, i, existing_names, existing_ids, groups)
        folders.append(folder)
    if not any(item.get("name") == DEFAULT_CATEGORY for item in folders):
        undefined = normalize_category_folder(
            {"name": DEFAULT_CATEGORY, "icon": CATEGORY_NAME_TO_ICON[DEFAULT_CATEGORY], "color": "#9ca3af"},
            len(folders),
            existing_names,
            existing_ids,
            groups,
        )
        folders.append(undefined)
    return folders


def load_category_state(folder):
    groups = default_category_groups()
    if not folder:
        return {}, default_category_folders(groups), groups
    path = get_category_meta_path(folder)
    if not path.exists():
        return {}, default_category_folders(groups), groups
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}, default_category_folders(groups), groups
    if not isinstance(raw, dict):
        return {}, default_category_folders(groups), groups
    has_new_schema = isinstance(raw.get("assignments"), dict) or isinstance(raw.get("folders"), list)
    groups = normalize_category_groups(raw.get("groups") if isinstance(raw.get("groups"), list) else None)
    folders = normalize_category_folders(raw.get("folders") if has_new_schema else None, groups)
    raw_assignments = raw.get("assignments") if has_new_schema else raw
    if not isinstance(raw_assignments, dict):
        raw_assignments = {}
    out = {}
    for key, value in raw_assignments.items():
        if isinstance(key, str):
            out[key] = category_id_for_value(value, folders)
    if raw.get("version") != 3:
        save_category_state(folder, out, folders, groups)
    return out, folders, groups


def load_category_assignments(folder):
    return load_category_state(folder)[0]


def category_folder_for_value(value, folders=None):
    source = folders if folders is not None else category_folders
    value = str(value or "")
    return next((item for item in source if item.get("id") == value or item.get("name") == value), None)


def category_id_for_value(value, folders=None):
    source = folders if folders is not None else category_folders
    item = category_folder_for_value(value, source)
    if item:
        return item.get("id")
    fallback = category_folder_for_value(DEFAULT_CATEGORY, source)
    return fallback.get("id") if fallback else DEFAULT_CATEGORY


def save_category_state(folder, assignments, folders, groups=None):
    if not folder:
        return
    path = get_category_meta_path(folder)
    clean_groups = normalize_category_groups(groups if groups is not None else category_groups)
    clean_folders = normalize_category_folders(folders, clean_groups)
    clean = {}
    for key, value in sorted(assignments.items()):
        if isinstance(key, str):
            clean[key] = category_id_for_value(value, clean_folders)
    payload = {
        "version": 3,
        "groups": clean_groups,
        "folders": clean_folders,
        "assignments": clean,
    }
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def save_category_assignments(folder, assignments):
    save_category_state(folder, assignments, category_folders)


def get_pair_category(img_name):
    item = category_folder_for_value(category_assignments.get(img_name, DEFAULT_CATEGORY))
    return item.get("name") if item else DEFAULT_CATEGORY


def get_pair_category_id(img_name):
    return category_id_for_value(category_assignments.get(img_name, DEFAULT_CATEGORY))


def ensure_missing_txt(folder):
    missing = []
    for fname in os.listdir(folder):
        if fname.lower().endswith(IMAGE_EXTENSIONS):
            img_path = os.path.join(folder, fname)
            txt_path = os.path.splitext(img_path)[0] + ".txt"
            if not os.path.exists(txt_path):
                missing.append(txt_path)
    return missing


def mask_dir_for_folder(folder):
    return Path(folder) / "mask"


def mask_path_for_image(folder, img_name):
    safe_name = Path(str(img_name or "")).name
    if not safe_name.lower().endswith(IMAGE_EXTENSIONS):
        return None
    return mask_dir_for_folder(folder) / safe_name


def ensure_mask_for_image(folder, img_name):
    mask_path = mask_path_for_image(folder, img_name)
    if mask_path is None:
        return None
    src_path = Path(folder) / Path(img_name).name
    if not src_path.exists():
        return None
    mask_path.parent.mkdir(exist_ok=True)
    if mask_path.exists():
        try:
            with Image.open(src_path) as src, Image.open(mask_path) as existing_mask:
                if existing_mask.size != src.size:
                    fixed = existing_mask.convert("L").resize(src.size, Image.NEAREST)
                    fixed.save(mask_path)
        except Exception:
            pass
        return mask_path
    with Image.open(src_path) as src:
        mask = Image.new("L", src.size, 0)
    try:
        mask.save(mask_path)
    except Exception:
        mask.save(mask_path, format="PNG")
    return mask_path


def ensure_masks_for_folder(folder):
    created = 0
    existing = 0
    if not folder or not os.path.isdir(folder):
        return created, existing
    for img_name in sorted(os.listdir(folder)):
        if not img_name.lower().endswith(IMAGE_EXTENSIONS):
            continue
        mask_path = mask_path_for_image(folder, img_name)
        had_mask = bool(mask_path and mask_path.exists())
        if ensure_mask_for_image(folder, img_name):
            if had_mask:
                existing += 1
            else:
                created += 1
    return created, existing


def get_rembg_session(model_name=DEFAULT_AUTO_MASK_MODEL):
    model_name = str(model_name or DEFAULT_AUTO_MASK_MODEL).strip() or DEFAULT_AUTO_MASK_MODEL
    if model_name not in REMBG_SESSIONS:
        try:
            from rembg import new_session
        except Exception as e:
            raise RuntimeError(
                "Auto mask requires rembg. Run install.bat or install rembg in the virtual environment."
            ) from e
        REMBG_SESSIONS[model_name] = new_session(model_name)
    return REMBG_SESSIONS[model_name]


def auto_mask_image_bytes(
    folder,
    img_name,
    model_name=DEFAULT_AUTO_MASK_MODEL,
    post_process_mask=True,
    expand_pixels=0,
    feather_pixels=0,
):
    safe_name = Path(str(img_name or "")).name
    img_path = Path(folder) / safe_name
    if not img_path.exists():
        raise FileNotFoundError("Image no longer exists.")
    try:
        from rembg import remove
    except Exception as e:
        raise RuntimeError(
            "Auto mask requires rembg. Run install.bat or install rembg in the virtual environment."
        ) from e

    session = get_rembg_session(model_name)
    with Image.open(img_path) as src:
        src = ImageOps.exif_transpose(src).convert("RGBA")
        result = remove(src, session=session, only_mask=True, post_process_mask=bool(post_process_mask))

    if isinstance(result, Image.Image):
        mask = result.convert("L")
    elif isinstance(result, (bytes, bytearray)):
        with Image.open(io.BytesIO(result)) as mask_img:
            mask = mask_img.convert("L")
    else:
        try:
            mask = Image.fromarray(result).convert("L")
        except Exception as e:
            raise RuntimeError(f"Unexpected rembg output type: {type(result).__name__}") from e

    with Image.open(img_path) as src_check:
        size = src_check.size
    if mask.size != size:
        mask = mask.resize(size, Image.NEAREST)

    try:
        expand_pixels = max(0, min(256, int(float(expand_pixels or 0))))
    except Exception:
        expand_pixels = 0
    if expand_pixels:
        mask = mask.filter(ImageFilter.MaxFilter(expand_pixels * 2 + 1))

    try:
        feather_pixels = max(0, min(256, int(float(feather_pixels or 0))))
    except Exception:
        feather_pixels = 0
    if feather_pixels:
        mask = mask.filter(ImageFilter.GaussianBlur(radius=feather_pixels))

    out = io.BytesIO()
    mask.save(out, format="PNG")
    return out.getvalue()


def decode_png_data_url(data_url, label="image"):
    value = str(data_url or "")
    if not value.startswith("data:image/png;base64,"):
        raise ValueError(f"Missing {label} data.")
    try:
        raw = base64.b64decode(value.split(",", 1)[1], validate=True)
        with Image.open(io.BytesIO(raw)) as loaded:
            return loaded.copy()
    except Exception as exc:
        raise ValueError(f"Invalid {label} data.") from exc


def get_simple_lama_model():
    global SIMPLE_LAMA_MODEL
    if SIMPLE_LAMA_MODEL is None:
        try:
            from simple_lama_inpainting import SimpleLama
        except Exception as exc:
            raise RuntimeError(
                "LaMa watermark removal is not installed. Run install.bat/install.sh, "
                "or select an OpenCV backend."
            ) from exc
        SIMPLE_LAMA_MODEL = SimpleLama()
    return SIMPLE_LAMA_MODEL


def remove_watermark_image(folder, img_name, mask_data, backend="lama", radius=3, expand_pixels=2, source_data=""):
    safe_name = Path(str(img_name or "")).name
    img_path = Path(folder) / safe_name
    if not img_path.exists() or not safe_name.lower().endswith(IMAGE_EXTENSIONS):
        raise FileNotFoundError("Image no longer exists.")

    if source_data:
        source = decode_png_data_url(source_data, "watermark preview")
    else:
        with Image.open(img_path) as loaded:
            source = ImageOps.exif_transpose(loaded).copy()
    source = source.convert("RGBA")
    mask = decode_png_data_url(mask_data, "watermark mask").convert("L")
    if mask.size != source.size:
        mask = mask.resize(source.size, Image.NEAREST)

    try:
        expand_pixels = max(0, min(128, int(float(expand_pixels or 0))))
    except Exception:
        expand_pixels = 0
    mask = mask.point(lambda value: 255 if value >= 8 else 0)
    if expand_pixels:
        mask = mask.filter(ImageFilter.MaxFilter(expand_pixels * 2 + 1))
    if not mask.getbbox():
        raise ValueError("Paint over the watermark before applying removal.")

    backend = str(backend or "lama").strip().lower()
    if backend == "lama":
        result_rgb = get_simple_lama_model()(source.convert("RGB"), mask).convert("RGB")
    elif backend in {"opencv_telea", "opencv_ns"}:
        try:
            import cv2
            import numpy as np
        except Exception as exc:
            raise RuntimeError("OpenCV watermark removal is not installed.") from exc
        try:
            radius = max(1.0, min(64.0, float(radius or 3)))
        except Exception:
            radius = 3.0
        rgb = np.asarray(source.convert("RGB"))
        bgr = cv2.cvtColor(rgb, cv2.COLOR_RGB2BGR)
        mask_array = np.asarray(mask, dtype=np.uint8)
        method = cv2.INPAINT_TELEA if backend == "opencv_telea" else cv2.INPAINT_NS
        result_bgr = cv2.inpaint(bgr, mask_array, radius, method)
        result_rgb = Image.fromarray(cv2.cvtColor(result_bgr, cv2.COLOR_BGR2RGB))
    else:
        raise ValueError("Unknown watermark removal backend.")

    if result_rgb.size != source.size:
        result_rgb = result_rgb.crop((0, 0, source.width, source.height))
    result = result_rgb.convert("RGBA")
    result.putalpha(source.getchannel("A"))
    return result


def image_names_for_category(category=None):
    category = normalize_category_name(category) if category else None
    names = []
    for img_name, _ in pairs_cache:
        if not pair_exists(current_folder, img_name):
            continue
        if category and get_pair_category(img_name) != category:
            continue
        names.append(img_name)
    return names


def replace_in_all_captions(folder, match_string, replace_with, use_regex=False, category=None):
    if not match_string:
        raise ValueError("Search string cannot be empty.")

    total_count = 0

    for img_name in image_names_for_category(category):
        path = os.path.splitext(os.path.join(folder, img_name))[0] + ".txt"
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        if use_regex:
            regex = re.compile(match_string, re.MULTILINE | re.DOTALL)
            new_content, n = regex.subn(lambda _match: replace_with, content)
        else:
            n = content.count(match_string)
            new_content = content.replace(match_string, replace_with)

        if n > 0:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
            total_count += n

    return total_count


def count_in_all_captions(folder, count_string, category=None):
    regex = re.compile(count_string, re.MULTILINE | re.DOTALL)
    count = 0
    for img_name in image_names_for_category(category):
        path = os.path.splitext(os.path.join(folder, img_name))[0] + ".txt"
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        count += len(regex.findall(content))
    return count


def find_caption_matches(folder, count_string, category=None):
    regex = re.compile(count_string, re.MULTILINE | re.DOTALL)
    matches = []
    for img_name in image_names_for_category(category):
        path = os.path.splitext(os.path.join(folder, img_name))[0] + ".txt"
        if not os.path.exists(path):
            continue
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()
        for match in regex.finditer(content):
            start, end = match.span()
            matches.append({
                "img_name": img_name,
                "category": get_pair_category(img_name),
                "start": start,
                "end": end,
            })
    return matches

TEMPLATE = r'''
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>DataPrep - Workspace mode</title>
<link rel="icon" href="/category_icon/btn_dataprep.svg" type="image/svg+xml">
<style>
body { font-family: Inter, Segoe UI, Roboto, Arial, sans-serif; font-size: 14px; line-height: 1.4; margin: 12px; background: var(--bg); color: var(--fg); }
:root {
  --bg: #f5f7fb;
  --fg: #111827;
  --muted: #5f6b7a;
  --card: #ffffff;
  --border: #d7deea;
  --accent: #2563eb;
  --danger: #d14b4b;
  --danger-bg: #fff0f0;
  --danger-border: #efb3b3;
  --ok: #188a49;
  --ok-bg: #eaf8f0;
  --ok-border: #9dd8b3;
  --shadow: 0 6px 18px rgba(15,23,42,0.06);
}
body.dark {
  --bg: #1d2026;
  --fg: #edf2f7;
  --muted: #a6b0bc;
  --card: #262b33;
  --border: #3a404b;
  --accent: #8db6ff;
  --danger: #ffb3b3;
  --danger-bg: #442a2a;
  --danger-border: #845050;
  --ok: #91e0b0;
  --ok-bg: #183425;
  --ok-border: #2f6947;
  --shadow: 0 10px 28px rgba(0,0,0,0.32);
}
button, input, textarea, select {
  background: var(--card);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 10px;
  padding: 7px 9px;
  font: inherit;
}

button {
  cursor: pointer;
  transition: border-color .16s ease, background-color .16s ease, box-shadow .16s ease, transform .04s ease;
}
button:hover {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px color-mix(in srgb, var(--accent) 18%, transparent);
}
button:active { transform: translateY(1px); }
input:focus, textarea:focus, select:focus, button:focus-visible {
  outline: none;
  border-color: var(--accent);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--accent) 20%, transparent);
}
.toolbar-btn-icon {
  width: auto;
  height: 20px;
  max-width: 20px;
  object-fit: contain;
  vertical-align: middle;
  margin-right: 8px;
  flex: 0 0 auto;
}
.toolbar-btn-content {
  display: inline-flex;
  align-items: center;
}

.page-title {
  font-size: 22px;
  font-weight: 700;
  letter-spacing: -0.02em;
  margin: 0 0 10px;
  line-height: 1.15;
}
.page-version {
  font-size: 12px;
  font-weight: 500;
  color: var(--muted);
  margin-left: 8px;
}
.page-folder-inline{
  margin-left: 12px;
  color: var(--muted);
  font-size: 14px;
  font-weight: 400;
}
.topbar {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin-bottom: 10px;

  position: sticky;
  top: 0;
  z-index: 100;
  background: rgba(17,21,28,.95);
  backdrop-filter: blur(10px);
  border-bottom: 1px solid var(--border);
  padding: 10px 12px;
}
.info-text { color: var(--muted); font-size: 12px; }
.controls {
  display: flex;
  flex-wrap: wrap;
  gap: 10px;
  align-items: center;
  margin: 8px 0 12px;
  width: 100%;
  box-sizing: border-box;
}
.topbar-close-inline{
  margin-left:auto;
  display:inline-flex;
  align-items:center;
}

.topbar-spacer{
  flex: 1 1 auto;
}
.topbar-close-inline .topbar-close-floating{
  width:28px;
  height:28px;
  min-width:28px;
  padding:0;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  border-radius:999px;
  font-size:18px;
  line-height:1;
}
.grid {
  display: grid;
  grid-template-columns: repeat(auto-fill, minmax(450px, 1fr));
  gap: 12px;
}
.pair-card {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 16px;
  padding: 10px;
  box-shadow: var(--shadow);
}
.pair-card.unsaved {
  border-color: var(--danger-border);
  box-shadow: 0 0 0 1px var(--danger-border), var(--shadow);
}
.card-head {
  display: flex;
  justify-content: space-between;
  gap: 10px;
  align-items: center;
  margin-bottom: 6px;
  position: relative;
  user-select: none;
}
.filename {
  flex: 1 1 auto;
  min-width: 0;
  font-weight: 700;
  max-width: 100%;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  word-break: normal;
  cursor: text;
}
.pair-card.unsaved .filename {
  flex: 0 1 calc(50% - 42px);
  max-width: calc(50% - 42px);
}
.filename-input {
  width: 100%;
  font: inherit;
  color: inherit;
  background: transparent;
  border: 1px solid var(--accent);
  border-radius: 8px;
  padding: 3px 6px;
  outline: none;
}
.status-wrap {
  position: absolute;
  left: 50%;
  top: 50%;
  display: flex;
  align-items: center;
  gap: 6px;
  transform: translate(-50%, -50%);
  pointer-events: none;
}
.unsaved-label {
  font-size: 11px;
  color: var(--danger);
  font-weight: 800;
  text-transform: lowercase;
  display: none;
}
.unsaved-label.show {
  display: inline;
}
.status-dot {
  display: none;
}
.card-head-actions {
  margin-left: auto;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  flex: 0 0 auto;
  position: relative;
  z-index: 1;
}
.card-head-action {
  position: relative;
  width: 16px;
  height: 16px;
  min-width: 16px;
  min-height: 16px;
  padding: 0;
  border: 1px solid #fff;
  border-radius: 999px;
  font-size: 0;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  box-shadow: 0 0 0 1px rgba(0,0,0,.45);
}
.card-head-action::before,
.card-head-action::after {
  content: "";
  position: absolute;
  left: 3px;
  top: 6px;
  width: 8px;
  height: 2px;
  background: #fff;
  border-radius: 1px;
}
.card-head-action.clone-btn {
  background: #248a2b;
}
.card-head-action.clone-btn::after {
  transform: rotate(90deg);
}
.card-head-action.delete-btn {
  background: #f01818;
}
.card-head-action.delete-btn::before {
  transform: rotate(45deg);
}
.card-head-action.delete-btn::after {
  transform: rotate(-45deg);
}
.meta-row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-bottom: 6px;
}
.badge {
  font-size: 11px;
  padding: 3px 8px;
  border-radius: 999px;
  border: 1px solid var(--border);
}
.badge.ok {
  background: var(--ok-bg);
  border-color: var(--ok-border);
  color: var(--ok);
}
.badge.warn {
  background: rgba(245, 158, 11, 0.12);
  border-color: rgba(245, 158, 11, 0.42);
  color: #fbbf24;
}
.badge.bad {
  background: var(--danger-bg);
  border-color: var(--danger-border);
  color: var(--danger);
}
.caption-textarea {
  display: block;
  width: 100%;
  max-width: 100%;
  min-height: 84px;
  resize: none;
  box-sizing: border-box;
  margin-top: 6px;
}
.caption-textarea.unsaved {
  border-color: var(--danger-border);
  box-shadow: inset 0 0 0 1px var(--danger-border);
}
.card-actions {
  display: flex;
  gap: 8px;
  flex-wrap: nowrap;
  margin-top: 6px;
  align-items: center;
}
.icon-btn {
  width: 36px;
  height: 36px;
  min-width: 36px;
  min-height: 36px;
  border-radius: 999px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
}
.icon-btn svg {
  width: 18px;
  height: 18px;
  display: block;
}

.icon-btn img {
  width: 18px;
  height: 18px;
  display: block;
  object-fit: contain;
}

.category-btn {
  margin-left: auto;
  position: relative;
  overflow: hidden;
}
.category-btn img, .category-option-btn img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  display: block;
}
.category-btn img {
  border-radius: inherit;
}
.category-popover {
  position: fixed;
  z-index: 1100;
  min-width: 280px;
  max-width: min(520px, calc(100vw - 24px));
  background: var(--card);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 16px;
  box-shadow: 0 24px 44px rgba(0,0,0,0.24);
  backdrop-filter: blur(10px);
  padding: 10px;
}
.category-popover[hidden] { display: none; }
.category-popover-head {
  display: flex;
  align-items: center;
  justify-content: space-between;
  gap: 12px;
  margin-bottom: 10px;
}
.category-popover-title { font-weight: 600; }
.category-popover-close {
  width: 32px;
  height: 32px;
  min-width: 32px;
  min-height: 32px;
  border-radius: 999px;
  font-size: 18px;
  line-height: 1;
}
.category-option-grid {
  display: flex;
  flex-direction: column;
  gap: 8px;
}
.category-option-row {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(86px, 1fr));
  gap: 8px;
}
.category-option-btn {
  display: flex;
  flex-direction: column;
  align-items: center;
  justify-content: center;
  gap: 6px;
  text-align: center;
  min-height: 78px;
  padding: 8px 6px;
  border-radius: 14px;
}
.category-option-btn .category-icon-circle {
  width: 40px;
  height: 40px;
  border-radius: 999px;
  border: 1px solid var(--border);
  display: inline-flex;
  align-items: center;
  justify-content: center;
  overflow: hidden;
  background: var(--bg);
}
.category-option-btn.active {
  border-color: var(--ok);
  box-shadow: inset 0 0 0 1px var(--ok);
}
.category-option-label {
  display: grid;
  gap: 1px;
  font-size: 12px;
  line-height: 1.2;
}
.category-option-label-prefix {
  color: var(--muted);
  font-size: 11px;
}
.category-option-label-suffix {
  color: var(--fg);
  font-weight: 750;
}
.category-hidden .category-btn { display: none; }
.icon-btn.delete-btn:hover {
  border-color: var(--danger-border);
  color: var(--danger);
}
.save-btn.unsaved {
  border-color: var(--danger-border);
}
.save-btn.upscale-warning {
  background: var(--danger-bg);
  color: var(--danger);
  border-color: var(--danger-border);
}
.tool-box {
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 10px;
  margin-bottom: 10px;
}
.tool-box h3 {
  margin: 0 0 10px;
}
.tool-box form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
}
.range-wrap {
  display: flex;
  align-items: center;
  gap: 8px;
}
.crop-wrap {
  margin-top: 6px;
}
.media-top-row {
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto minmax(0, 1fr);
  align-items: center;
  gap: 8px;
  margin: 0 0 4px;
}
.media-top-row .meta-row {
  margin: 0;
  min-width: 0;
}
.media-top-row .crop-label {
  text-align: center;
  white-space: nowrap;
  overflow: hidden;
  text-overflow: ellipsis;
  font-size: 12px;
}
.media-zoom-row {
  display: flex;
  justify-content: flex-end;
  align-items: center;
  gap: 3px;
  min-width: 0;
}
.media-zoom-btn {
  min-width: 22px;
  min-height: 18px;
  height: 18px;
  padding: 0 4px;
  border-radius: 4px;
  font-size: 10px;
  line-height: 1;
}
.crop-stage {
  position: relative;
  display: block;
  width: min(420px, 100%);
  aspect-ratio: 1 / 1;
  max-width: 100%;
  margin: 0 auto;
  background: var(--bg);
  border: 1px solid var(--border);
  overflow: hidden;
  line-height: 0;
}
.zoom-readout {
  position: absolute;
  right: 0;
  top: 0;
  z-index: 20;
  pointer-events: none;
  opacity: 0;
  transition: opacity .18s ease;
  padding: 2px 7px;
  border-radius: 0 0 0 4px;
  background: rgba(0,0,0,.42);
  color: #fff;
  font-size: 11px;
  font-weight: 750;
  box-sizing: border-box;
  display: inline-block;
  line-height: 1.25;
  min-width: max-content;
  white-space: nowrap;
}
.zoom-readout.show {
  opacity: 1;
}
.crop-stage img {
  position: absolute;
  inset: 0;
  display: block;
  width: 100%;
  height: 100%;
  object-fit: contain;
  object-position: center;
  border: 0;
  border-radius: 0;
  user-select: none;
  -webkit-user-drag: none;
}
.crop-stage.panning,
.crop-stage.panning * {
  cursor: grabbing !important;
}

.mask-canvas {
  position: absolute;
  display: none;
  opacity: var(--mask-overlay-opacity, .48);
  pointer-events: none;
  image-rendering: auto;
  z-index: 3;
}

body.mask-mode .mask-canvas {
  display: block;
  pointer-events: auto;
  cursor: crosshair;
}

.mask-brush-cursor {
  position: fixed;
  z-index: 10001;
  display: none;
  pointer-events: none;
  border: 1px solid rgba(255,255,255,.95);
  border-radius: 999px;
  box-shadow: 0 0 0 1px rgba(0,0,0,.65), 0 0 10px rgba(0,0,0,.5);
  transform: translate(-50%, -50%);
  mix-blend-mode: difference;
}

.mask-brush-cursor.visible {
  display: block;
}

body.mask-mode .crop-stage {
  cursor: crosshair;
}

body.mask-mode .crop-overlay,
body.mask-mode .crop-box,
body.mask-mode .auto-crop-btn,
body.mask-mode .ratio-lock-btn {
  display: none !important;
}

body.mask-mode .crop-label {
  visibility: hidden;
}

.mask-size-row {
  display: none;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
  min-height: 26px;
  visibility: hidden;
}

body.mask-mode .mask-size-row {
  display: none;
}

body.mask-mode .rotate-row {
  visibility: hidden;
}

.mask-size-row label {
  font-size: 12px;
  color: var(--muted);
  min-width: 42px;
}

.mask-size-slider {
  flex: 1 1 auto;
}

.mask-size-value {
  width: 48px;
  text-align: right;
  font-size: 12px;
  color: var(--muted);
}

.mask-tool-btn {
  display: none;
}

body.mask-mode .mask-tool-btn {
  display: inline-flex;
}

.redo-btn {
  display: none;
}

body.mask-mode .automask-btn,
body.mask-mode .redo-btn {
  display: inline-flex;
}

.automask-btn {
  display: none;
}

.watermark-apply-btn {
  display: none;
}

body.watermark-mode .automask-btn {
  display: none;
}

body.watermark-mode .flip-h-btn,
body.watermark-mode .flip-v-btn {
  display: none !important;
}

body.watermark-mode .watermark-apply-btn {
  display: inline-flex;
  border-color: #eab308;
}

.mask-tool-btn.active {
  border-color: var(--ok);
  background: rgba(22,163,74,.18);
  box-shadow: 0 0 0 1px rgba(34,197,94,.55) inset;
}

.mask-size-popover {
  position: fixed;
  z-index: 10000;
  display: none;
  flex-direction: column;
  align-items: center;
  gap: 3px;
  width: 48px;
  height: 286px;
  padding: 6px 5px;
  box-sizing: border-box;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #151515;
  box-shadow: 0 10px 24px rgba(0,0,0,.45);
}

.mask-size-popover.open {
  display: flex;
}

.mask-size-popover input[type="range"] {
  width: 28px;
  height: 94px;
  flex: 0 0 94px;
  margin: 0;
  box-sizing: border-box;
  writing-mode: vertical-lr;
  direction: rtl;
  accent-color: var(--accent);
}

.mask-size-popover span {
  font-size: 11px;
  line-height: 1;
  color: var(--muted);
}

.mask-size-popover .mask-popover-label {
  color: var(--fg);
  font-size: 10px;
  font-weight: 650;
}

.fill-tolerance-popover {
  height: 150px;
}

.crop-overlay {
  position: absolute;
  inset: 0;
  cursor: crosshair;
}
.crop-box {
  position: absolute;
  border: 2px solid var(--accent);
  background: rgba(37,99,235,0.12);
  display: none;
  box-sizing: border-box;
  cursor: move;
}
.crop-box.show { display: block; }
.crop-box.upscale {
  border-color: var(--danger);
  background: rgba(220,38,38,0.12);
}
.crop-box.valid {
  border-color: var(--ok);
  background: rgba(22,163,74,0.14);
}
.crop-label {
  margin-top: 4px;
  font-size: 11px;
  color: var(--muted);
}
.rotate-row {
  display: flex;
  align-items: center;
  gap: 8px;
  margin-top: 6px;
}
.rotate-row label {
  font-size: 12px;
  color: var(--muted);
  min-width: 42px;
}
.rotate-slider {
  flex: 1 1 auto;
}
.rotate-value {
  width: 48px;
  text-align: right;
  font-size: 12px;
  color: var(--muted);
}
.handle {
  position: absolute;
  width: 10px;
  height: 10px;
  background: var(--accent);
  border: 1px solid white;
  border-radius: 999px;
}
.crop-box.valid .handle {
  background: var(--ok);
}
.crop-box.upscale .handle {
  background: var(--danger);
}
.handle.nw { left: -6px; top: -6px; cursor: nwse-resize; }
.handle.ne { right: -6px; top: -6px; cursor: nesw-resize; }
.handle.sw { left: -6px; bottom: -6px; cursor: nesw-resize; }
.handle.se { right: -6px; bottom: -6px; cursor: nwse-resize; }
.handle.n { left: calc(50% - 5px); top: -6px; cursor: ns-resize; }
.handle.s { left: calc(50% - 5px); bottom: -6px; cursor: ns-resize; }
.handle.w { left: -6px; top: calc(50% - 5px); cursor: ew-resize; }
.handle.e { right: -6px; top: calc(50% - 5px); cursor: ew-resize; }
.notice {
  padding: 10px 12px;
  border-radius: 10px;
  margin: 10px 0;
  border: 1px solid var(--border);
  background: var(--card);
}
.joy-modal-backdrop {
  position: fixed;
  inset: 0;
  background: rgba(0,0,0,0.5);
  display: none;
  align-items: center;
  justify-content: center;
  z-index: 1000;
  padding: 16px;
}
.joy-modal-backdrop.open {
  display: flex;
}
.joy-modal {
  box-sizing: border-box;
  width: min(940px, 100%);
  max-height: calc(100vh - 32px);
  overflow: auto;
  background: var(--card);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 6px;
  padding: 14px;
  box-shadow: 0 18px 36px rgba(0,0,0,0.30);
}
.joy-modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
  cursor: move;
  user-select: none;
}
.joy-grid {
  display: grid;
  grid-template-columns: repeat(auto-fit, minmax(220px, 1fr));
  gap: 8px;
}
.joy-grid > label {
  display: flex;
  flex-direction: column;
  gap: 6px;
}
.joy-extra-list-flat {
  display: flex;
  flex-direction: column;
  gap: 0;
}
.joy-extra-item {
  display: flex;
  align-items: flex-start;
  justify-content: space-between;
  gap: 12px;
  padding: 6px 0;
  border-top: 1px solid var(--border);
}
.joy-extra-item:first-child {
  border-top: 0;
}
.joy-extra-item span {
  flex: 1 1 auto;
}
.joy-extra-item input[type="checkbox"] {
  flex: 0 0 auto;
  margin: 2px 0 0 12px;
}
.joy-actions {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  margin-top: 10px;
  align-items: center;
}
.logbox {
  white-space: pre-wrap;
  background: var(--bg);
  border: 1px solid var(--border);
  border-radius: 12px;
  padding: 8px;
  min-height: 110px;
  max-height: 320px;
  overflow: auto;
  margin-top: 10px;
  font-family: Consolas, monospace;
  font-size: 12px;
}
.small { font-size: 12px; color: var(--muted); }
.watermark-help-text {
  box-sizing: border-box;
  margin: 10px 0;
  padding: 0 12px;
}

/* Force card control icons to render at 100% (no scaling) */
.icon-btn img {
  width: auto !important;
  height: auto !important;
  max-width: none !important;
  max-height: none !important;
}
.toolbar-btn-icon {
  width: auto !important;
  height: auto !important;
  max-width: none !important;
  max-height: none !important;
}

/* Category colors */
#autoCropAllBtn .toolbar-btn-icon,
#resetAllBtn .toolbar-btn-icon,
#saveAllBtn .toolbar-btn-icon {
  width: auto !important;
  height: auto !important;
  max-width: none !important;
  max-height: none !important;
  flex: 0 0 auto !important;
}


/* Category colors shown behind transparent PNG icons */
.category-btn[data-category="Undefined"] {
  background: #6b7280;
}

.category-option-btn[data-category="Undefined"] .category-icon-circle {
  background: #6b7280;
}

.category-btn[data-category="Close-up Front"],
.category-option-btn[data-category="Close-up Front"] .category-icon-circle {
  background: #4ade80;
}
.category-btn[data-category="Close-up Front-left"],
.category-option-btn[data-category="Close-up Front-left"] .category-icon-circle,
.category-btn[data-category="Close-up Front-right"],
.category-option-btn[data-category="Close-up Front-right"] .category-icon-circle {
  background: #34d399;
}
.category-btn[data-category="Close-up Right"],
.category-option-btn[data-category="Close-up Right"] .category-icon-circle,
.category-btn[data-category="Close-up Left"],
.category-option-btn[data-category="Close-up Left"] .category-icon-circle {
  background: #22c55e;
}
.category-btn[data-category="Close-up From Above"],
.category-option-btn[data-category="Close-up From Above"] .category-icon-circle,
.category-btn[data-category="Close-up From Below"],
.category-option-btn[data-category="Close-up From Below"] .category-icon-circle {
  background: #16a34a;
}
.category-btn[data-category="Close-up Back"],
.category-option-btn[data-category="Close-up Back"] .category-icon-circle {
  background: #166534;
}

.category-btn[data-category="Medium Front"],
.category-option-btn[data-category="Medium Front"] .category-icon-circle {
  background: #fde047;
}
.category-btn[data-category="Medium Profile"],
.category-option-btn[data-category="Medium Profile"] .category-icon-circle {
  background: #eab308;
}
.category-btn[data-category="Medium Back"],
.category-option-btn[data-category="Medium Back"] .category-icon-circle {
  background: #854d0e;
}

.category-btn[data-category="Full body Front"],
.category-option-btn[data-category="Full body Front"] .category-icon-circle {
  background: #f87171;
}
.category-btn[data-category="Full body Profile"],
.category-option-btn[data-category="Full body Profile"] .category-icon-circle {
  background: #dc2626;
}
.category-btn[data-category="Full body Back"],
.category-option-btn[data-category="Full body Back"] .category-icon-circle {
  background: #7f1d1d;
}


.topbar button,
.controls button {
  min-height: 32px;
  padding-top: 5px;
  padding-bottom: 5px;
}
.pair-card,
.tool-box,
.joy-modal,
.category-popover {
  backdrop-filter: blur(10px);
}
.filename,
.category-popover-title,
.tool-box h3,
.joy-modal-head strong,
.joy-modal-head h2 {
  letter-spacing: -0.01em;
}
.badge,
.small,
.info-text,
.crop-label,
.rotate-row label,
.rotate-value,
.category-option-label {
  font-weight: 500;
}
.crop-stage {
  border-radius: 12px;
}

/* Compact UI clarity pass: visual structure only, no behavior changes. */
body {
  margin: 0;
  font-size: 13px;
  background: var(--bg);
}

button, input, textarea, select {
  border-radius: 8px;
  padding: 6px 8px;
}

button {
  min-height: 30px;
}

.topbar {
  display: block;
  margin: 0 0 12px;
  padding: 10px 12px;
  color: var(--fg);
  background: color-mix(in srgb, var(--card) 92%, transparent);
  border-bottom: 1px solid var(--border);
  box-shadow: 0 8px 20px rgba(15,23,42,0.08);
}

body.dark .topbar {
  background: color-mix(in srgb, var(--card) 90%, #111827 10%);
}

.topbar-stack {
  display: grid;
  gap: 8px;
}

.page-title {
  display: flex;
  align-items: baseline;
  gap: 8px;
  margin: 0;
  font-size: 18px;
  line-height: 1.2;
  letter-spacing: 0;
}

.page-version {
  margin: 0;
  padding: 2px 6px;
  border: 1px solid var(--border);
  border-radius: 999px;
  color: var(--muted);
  font-size: 11px;
}

.topbar-row {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 6px;
}

.topbar-actions {
  display: flex;
  flex-wrap: wrap;
  gap: 4px;
  align-items: center;
  padding: 4px;
  border: 1px solid var(--border);
  border-radius: 10px;
  background: color-mix(in srgb, var(--bg) 72%, var(--card));
}

.action-group {
  display: flex;
  flex-wrap: wrap;
  align-items: center;
  gap: 4px;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
}

.action-group button {
  border-color: transparent;
  background: transparent;
}

.toolbar-btn-content {
  gap: 6px;
  white-space: nowrap;
}

.toolbar-btn-icon {
  margin-right: 0;
  max-width: 18px !important;
  max-height: 18px !important;
}

.controls {
  margin: 0;
  gap: 8px;
}

.range-wrap,
.folder-pill,
.folder-close-group {
  min-height: 30px;
  padding: 4px 8px;
  border: 1px solid var(--border);
  border-radius: 999px;
  background: color-mix(in srgb, var(--bg) 70%, var(--card));
}

.range-wrap input[type="range"] {
  width: 130px;
}

.range-wrap label,
.range-wrap > span:first-child {
  color: var(--muted);
  font-weight: 600;
}

.info-text.folder-pill {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  color: var(--fg);
  max-width: min(520px, 100%);
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  line-height: 1;
}

.folder-close-group {
  display: inline-flex;
  align-items: center;
  gap: 6px;
  max-width: min(560px, 100%);
}

.folder-close-group .folder-pill {
  min-height: 0;
  padding: 0;
  border: 0;
  border-radius: 0;
  background: transparent;
  max-width: min(500px, 100%);
}

.folder-close-group .topbar-close-inline {
  flex: 0 0 auto;
}

.notice {
  margin: 12px;
  padding: 9px 11px;
  border-radius: 8px;
}

.grid {
  padding: 0 12px 16px;
  grid-template-columns: repeat(auto-fill, minmax(min(100%, var(--card-min-width, 460px)), 1fr));
  gap: 10px;
}

.pair-card {
  display: block;
  padding: 9px;
  border-radius: 8px;
}

.card-head {
  margin: 0;
  min-width: 0;
}

.filename {
  font-size: 13px;
  line-height: 1.25;
}

.meta-row {
  margin: 6px 0;
}

.badge {
  border-radius: 6px;
  padding: 2px 6px;
}

.crop-wrap {
  margin: 0;
}

.crop-stage {
  width: 100%;
  border-radius: 8px;
}

.crop-label {
  margin-top: 3px;
}

.rotate-row {
  margin-top: 4px;
}

.card-actions {
  margin-top: 6px;
  gap: 5px;
}

.icon-btn {
  width: 30px;
  height: 30px;
  min-width: 30px;
  min-height: 30px;
  border-radius: 8px;
}

.icon-btn img {
  max-width: 18px !important;
  max-height: 18px !important;
}

.caption-textarea {
  display: block;
  width: 100%;
  max-width: 100%;
  box-sizing: border-box;
  margin-top: 6px;
  min-height: 96px;
  height: auto;
  line-height: 1.35;
}

.joy-modal-backdrop {
  padding: 10px;
}

.joy-modal {
  width: min(980px, 100%);
  border-radius: 10px;
  padding: 0;
}

.joy-modal-head {
  position: sticky;
  top: 0;
  z-index: 2;
  margin: 0;
  padding: 10px 12px;
  background: color-mix(in srgb, var(--card) 96%, transparent);
  border-bottom: 1px solid var(--border);
}

.joy-modal-head h3 {
  margin: 0;
  font-size: 16px;
}

.joy-grid {
  grid-template-columns: repeat(auto-fit, minmax(190px, 1fr));
  gap: 7px;
  padding: 12px;
}

.joy-grid > label,
.tool-box label {
  font-size: 12px;
  font-weight: 600;
  color: var(--muted);
}

.regex-help-icon {
  width: 18px;
  height: 18px;
  border: 1px solid var(--border);
  border-radius: 999px;
  padding: 0;
  display: inline-flex;
  align-items: center;
  justify-content: center;
  background: #1f1f1f;
  color: var(--muted);
  font-size: 12px;
  font-weight: 700;
  line-height: 1;
  cursor: help;
}

.regex-help-icon:hover {
  border-color: var(--accent);
  color: var(--fg);
  background: #262626;
}

.regex-help-tooltip {
  position: fixed;
  z-index: 20000;
  display: none;
  max-width: min(320px, calc(100vw - 24px));
  padding: 8px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #151515;
  color: var(--fg);
  box-shadow: 0 10px 26px rgba(0,0,0,.5);
  font-size: 12px;
  line-height: 1.45;
  white-space: pre-line;
  pointer-events: none;
}

.regex-help-tooltip.open {
  display: block;
}

.joy-grid input,
.joy-grid select,
.joy-grid textarea,
.tool-box input,
.tool-box select,
.tool-box textarea {
  font-size: 13px;
  color: var(--fg);
}

.tool-box {
  border-radius: 8px;
  padding: 9px;
}

.joy-modal > .tool-box {
  margin: 0 12px 10px !important;
}

.joy-modal > .text-tools-help {
  box-sizing: border-box;
  margin: 0;
  padding: 10px 12px 0;
  line-height: 1.4;
}

.tool-box h3 {
  margin-bottom: 6px !important;
  font-size: 14px;
}

.joy-extra-list-flat {
  max-height: 260px;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 8px;
}

.joy-extra-item {
  padding: 7px 8px;
  gap: 10px;
}

.joy-actions {
  position: sticky;
  bottom: 0;
  z-index: 2;
  margin: 0;
  padding: 10px 12px;
  background: color-mix(in srgb, var(--card) 96%, transparent);
  border-top: 1px solid var(--border);
}

.logbox {
  margin: 0 12px 12px;
  border-radius: 8px;
}

#summaryContent,
#toolsResult {
  margin-top: 0 !important;
}

#joy_ideogram4_name,
#joy_qwen3vl_name,
#joy_qwen3vl_system_prompt {
  box-sizing: border-box;
  max-width: 100%;
}

#joy_qwen3vl_system_prompt {
  resize: vertical;
}

.qwen-name-title {
  display: inline-flex;
  align-items: center;
  gap: 6px;
}

.category-popover {
  border-radius: 8px;
}

.category-option-btn {
  border-radius: 8px;
  min-height: 70px;
}

/* OSTRIS AI-Toolkit inspired dark visual theme. Visual-only overrides. */
body.dark {
  --bg: #080808;
  --fg: #f4f4f5;
  --muted: #9ca3af;
  --card: #171717;
  --panel: #242424;
  --panel-strong: #2a2a2a;
  --border: #2d2d2d;
  --border-strong: #373737;
  --accent: #3b82f6;
  --accent-soft: rgba(59, 130, 246, 0.18);
  --danger: #ff5d7d;
  --danger-bg: rgba(255, 93, 125, 0.12);
  --danger-border: rgba(255, 93, 125, 0.46);
  --ok: #24d07a;
  --ok-bg: rgba(36, 208, 122, 0.12);
  --ok-border: rgba(36, 208, 122, 0.42);
  --warning: #f4b400;
  --shadow: none;
}

html {
  background: #080808;
  height: 100%;
}

body {
  font-family: Inter, "Segoe UI Variable", "Segoe UI", Roboto, Arial, sans-serif;
  font-size: 13px;
  line-height: 1.45;
  letter-spacing: 0;
  min-height: 100%;
}

button,
input,
textarea,
select {
  background: #121212;
  color: var(--fg);
  border-color: var(--border-strong);
  border-radius: 7px;
}

button {
  font-weight: 650;
}

button:hover {
  background: #1d1d1d;
  border-color: #4a4a4a;
  box-shadow: none;
}

button:active {
  background: #202020;
}

input,
textarea,
select {
  box-shadow: inset 0 1px 0 rgba(255,255,255,0.025);
}

input:focus,
textarea:focus,
select:focus,
button:focus-visible {
  border-color: var(--accent);
  box-shadow: 0 0 0 1px var(--accent);
}

input[type="range"] {
  accent-color: var(--accent);
}

input[type="radio"],
input[type="checkbox"] {
  accent-color: var(--accent);
}

.topbar {
  margin: 0;
  padding: 0;
  background: #151515;
  border-bottom: 1px solid #242424;
  box-shadow: none;
  backdrop-filter: none;
}

body.dark .topbar {
  background: #151515;
}

.topbar-stack {
  gap: 0;
}

.page-title {
  min-height: 45px;
  padding: 0 16px;
  align-items: center;
  color: #f7f7f7;
  background: #151515;
  border-bottom: 1px solid #242424;
  font-size: 17px;
  font-weight: 750;
  letter-spacing: 0;
}

.page-title::before {
  content: none;
}

.page-version {
  position: absolute;
  width: 1px;
  height: 1px;
  overflow: hidden;
  clip: rect(0, 0, 0, 0);
  white-space: nowrap;
}

.topbar-actions {
  gap: 0;
  min-height: 32px;
  padding: 0 16px;
  background: #242424;
  border: 0;
  border-radius: 0;
}

.action-group {
  gap: 0;
}

.action-group button,
.action-group a button {
  min-height: 32px;
  padding: 0 12px;
  color: #f1f1f1;
  background: transparent;
  border: 0;
  border-left: 1px solid rgba(255,255,255,0.05);
  border-radius: 0;
  font-size: 12px;
  font-weight: 700;
}

.action-group:first-child button:first-child {
  border-left: 0;
}

.action-group button:hover,
.action-group a button:hover {
  background: #303030;
  color: #ffffff;
}

.toolbar-btn-content {
  gap: 7px;
}

.toolbar-btn-icon {
  opacity: 1;
  filter: none;
}

.controls {
  padding: 12px 16px;
  background: #080808;
  border-bottom: 1px solid #181818;
}

.range-wrap,
.folder-pill,
.folder-close-group {
  min-height: 34px;
  padding: 5px 10px;
  background: #151515;
  border-color: #262626;
  border-radius: 7px;
}

.range-wrap label,
.range-wrap > span:first-child,
.info-text,
.small {
  color: var(--muted);
  font-weight: 650;
}

.info-text.folder-pill,
.folder-close-group .folder-pill {
  color: #d4d4d8;
}

.topbar-close-inline .topbar-close-floating,
.joy-close-btn,
.category-popover-close {
  background: transparent;
  border-color: transparent;
  color: #d4d4d8;
}

.topbar-close-inline .topbar-close-floating:hover,
.joy-close-btn:hover,
.category-popover-close:hover {
  background: #303030;
  color: #ffffff;
}

.notice {
  margin: 14px 16px;
  background: #171717;
  border-color: #2e2e2e;
  border-radius: 8px;
  color: #e7e7e7;
}

.drop-paste-overlay {
  position: fixed;
  inset: 0;
  z-index: 2200;
  display: none;
  align-items: center;
  justify-content: center;
  background: rgba(5, 5, 5, 0.72);
  border: 2px dashed var(--accent);
  color: #f4f4f5;
  font-size: 18px;
  font-weight: 800;
  pointer-events: none;
}

.drop-paste-overlay.show {
  display: flex;
}

.grid {
  padding: 16px;
  gap: 12px;
}

.pair-card,
.tool-box,
.joy-modal,
.category-popover {
  background: var(--card);
  border-color: var(--border);
  border-radius: 8px;
  box-shadow: none;
  backdrop-filter: none;
}

.pair-card {
  padding: 0;
  overflow: hidden;
}

.pair-card > * {
  min-width: 0;
  max-width: 100%;
  box-sizing: border-box;
}

.pair-card.unsaved {
  border-color: var(--danger-border);
  box-shadow: inset 0 0 0 1px rgba(255, 93, 125, 0.18);
}

.card-head {
  padding: 10px 12px;
  background: var(--panel);
  border-bottom: 1px solid #202020;
}

.filename {
  color: #f7f7f7;
  font-size: 14px;
  font-weight: 750;
}

.status-dot {
  width: 8px;
  height: 8px;
  border-color: #555;
  display: none;
}

.unsaved-label {
  color: var(--danger);
  font-weight: 700;
}

.meta-row,
.crop-wrap,
.card-actions,
.caption-textarea {
  margin-left: 10px;
  margin-right: 10px;
}

.meta-row {
  margin-top: 9px;
}

.badge {
  color: #d4d4d8;
  background: #202020;
  border-color: #333;
  border-radius: 999px;
  font-weight: 650;
}

.badge.ok {
  color: #7ee2a8;
  background: rgba(36, 208, 122, 0.09);
  border-color: rgba(36, 208, 122, 0.32);
}

.badge.warn {
  color: #fbbf24;
  background: rgba(245, 158, 11, 0.12);
  border-color: rgba(245, 158, 11, 0.42);
}

.badge.bad {
  color: #ff9aae;
  background: rgba(255, 93, 125, 0.1);
  border-color: rgba(255, 93, 125, 0.34);
}

.crop-stage {
  background: #050505;
  border-color: #272727;
  border-radius: 6px;
}

.crop-label,
.rotate-row label,
.rotate-value {
  color: #9a9a9a;
}

.crop-box {
  border-color: var(--accent);
  background: rgba(59, 130, 246, 0.14);
}

.handle {
  background: var(--accent);
  border-color: #e8eefc;
}

.card-actions {
  padding-bottom: 0;
}

.icon-btn {
  width: 31px;
  height: 31px;
  min-width: 31px;
  min-height: 31px;
  background: #202020;
  border-color: #343434;
  border-radius: 7px;
}

.icon-btn:hover {
  background: #292929;
  border-color: #4b4b4b;
}

.icon-btn.active {
  background: rgba(59, 130, 246, 0.18);
  border-color: var(--accent);
}

.icon-btn img {
  filter: brightness(1.18) contrast(1.03);
}

.caption-textarea {
  width: calc(100% - 20px);
  margin-bottom: 10px;
  background: #0b0b0b;
  border-color: #2a2a2a;
  border-radius: 6px;
  color: #eeeeee;
  font-family: "Cascadia Mono", Consolas, "Courier New", monospace;
  font-size: 12px;
}

.caption-stats {
  margin: -4px 10px 10px;
  display: flex;
  justify-content: flex-end;
  gap: 10px;
  color: var(--muted);
  font-size: 11px;
  font-weight: 650;
  font-variant-numeric: tabular-nums;
}

.caption-textarea::placeholder,
input::placeholder,
textarea::placeholder {
  color: #666;
}

.category-popover,
.joy-modal {
  background: #171717;
  border-color: #303030;
}

.category-popover-head,
.joy-modal-head {
  background: #252525;
  border-bottom-color: #202020;
}

.category-popover-title,
.joy-modal-head h3,
.tool-box h3 {
  color: #f5f5f5;
  font-weight: 800;
}

.category-option-btn {
  background: #151515;
  border-color: #303030;
}

.category-option-btn:hover {
  background: #222;
}

.category-option-btn.active {
  border-color: #a879ff;
  box-shadow: inset 0 0 0 1px #a879ff;
}

.tool-box {
  background: #151515;
}

.joy-grid > label,
.tool-box label {
  color: #b5b5b5;
}

.joy-extra-list-flat {
  background: #101010;
  border-color: #2d2d2d;
}

.joy-extra-item {
  border-top-color: #262626;
}

.joy-actions {
  background: #171717;
  border-top-color: #2d2d2d;
}

.joy-progress {
  margin: 0 12px 10px;
  display: grid;
  grid-template-columns: minmax(0, 1fr) auto;
  gap: 6px 10px;
  align-items: center;
}

.joy-progress-track {
  grid-column: 1 / -1;
  height: 8px;
  overflow: hidden;
  background: #0b0b0b;
  border: 1px solid #2d2d2d;
  border-radius: 999px;
}

.joy-progress-fill {
  width: 0%;
  height: 100%;
  background: var(--accent);
  border-radius: inherit;
  transition: width .18s ease;
}

.joy-progress-label,
.joy-progress-percent {
  color: var(--muted);
  font-size: 12px;
  font-weight: 650;
}

.joy-progress-percent {
  color: #d4d4d8;
  font-variant-numeric: tabular-nums;
}

#joyStartBtn {
  color: #dffbea;
  background: rgba(36, 208, 122, 0.13);
}

#joyStartBtn:hover {
  background: rgba(36, 208, 122, 0.2);
}

#saveAllBtn.has-unsaved {
  color: #ffd7df;
  background: rgba(255, 93, 125, 0.16);
}

#saveAllBtn.has-unsaved:hover {
  color: #ffe7ec;
  background: rgba(255, 93, 125, 0.24);
}

#joyInterruptBtn,
.delete-btn:hover {
  color: #ff9aae;
  border-color: var(--danger-border);
}

.logbox,
#summaryContent,
#toolsResult {
  background: #050505;
  border-color: #282828;
  border-radius: 6px;
  color: #e7e7e7;
}

.json-modal {
  width: min(1180px, 100%);
}

.json-workspace {
  display: grid;
  grid-template-columns: minmax(300px, 0.95fr) minmax(360px, 1.05fr);
  gap: 12px;
  min-height: 560px;
}

.json-side,
.json-editor-side {
  min-width: 0;
  display: flex;
  flex-direction: column;
  gap: 8px;
}

.json-toolbar {
  display: flex;
  gap: 8px;
  align-items: center;
  flex-wrap: wrap;
}

.json-toolbar select {
  min-width: min(360px, 100%);
  flex: 1 1 260px;
}

.json-image-frame {
  position: relative;
  display: grid;
  place-items: center;
  min-height: 420px;
  overflow: hidden;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #111;
}

.json-image-frame img {
  display: block;
  max-width: 100%;
  max-height: 68vh;
  object-fit: contain;
}

.json-bbox-layer {
  position: absolute;
  inset: 0;
  pointer-events: none;
}

.json-bbox {
  position: absolute;
  border: 2px solid #22c55e;
  background: rgba(34,197,94,.12);
  box-sizing: border-box;
  pointer-events: auto;
  cursor: move;
  touch-action: none;
}

.json-bbox.active {
  border-color: #facc15;
  background: rgba(250,204,21,.18);
}

.json-bbox-handle {
  position: absolute;
  display: none;
  width: 9px;
  height: 9px;
  border: 1px solid #020617;
  background: #facc15;
  border-radius: 50%;
  box-shadow: 0 0 0 1px rgba(255,255,255,.75);
  pointer-events: auto;
}

.json-bbox.active .json-bbox-handle,
.json-bbox:hover .json-bbox-handle {
  display: block;
}

.json-bbox-handle.nw { left: -6px; top: -6px; cursor: nwse-resize; }
.json-bbox-handle.n { left: 50%; top: -6px; transform: translateX(-50%); cursor: ns-resize; }
.json-bbox-handle.ne { right: -6px; top: -6px; cursor: nesw-resize; }
.json-bbox-handle.e { right: -6px; top: 50%; transform: translateY(-50%); cursor: ew-resize; }
.json-bbox-handle.se { right: -6px; bottom: -6px; cursor: nwse-resize; }
.json-bbox-handle.s { left: 50%; bottom: -6px; transform: translateX(-50%); cursor: ns-resize; }
.json-bbox-handle.sw { left: -6px; bottom: -6px; cursor: nesw-resize; }
.json-bbox-handle.w { left: -6px; top: 50%; transform: translateY(-50%); cursor: ew-resize; }

.json-element-list {
  max-height: 170px;
  overflow: auto;
  border: 1px solid var(--border);
  border-radius: 6px;
  background: color-mix(in srgb, var(--bg) 72%, var(--card));
}

.json-element-item {
  display: block;
  width: 100%;
  padding: 6px 8px;
  border: 0;
  border-bottom: 1px solid var(--border);
  background: transparent;
  color: var(--fg);
  text-align: left;
  font: inherit;
  cursor: pointer;
}

.json-element-item:last-child {
  border-bottom: 0;
}

.json-element-item.active {
  background: rgba(37,99,235,.24);
}

.json-editor {
  flex: 1 1 auto;
  min-height: 460px;
  resize: none;
  font-family: Consolas, ui-monospace, SFMono-Regular, Menlo, Monaco, monospace;
  line-height: 1.35;
  white-space: pre;
}

.json-status {
  min-height: 22px;
  color: var(--muted);
}

.json-status.ok {
  color: var(--ok);
}

.json-status.error {
  color: var(--danger);
}

@media (max-width: 860px) {
  .json-workspace {
    grid-template-columns: 1fr;
  }
}

body.dark ::selection {
  background: rgba(59, 130, 246, 0.45);
}

body.dark ::-webkit-scrollbar {
  width: 10px;
  height: 10px;
}

body.dark ::-webkit-scrollbar-track {
  background: #0b0b0b;
}

body.dark ::-webkit-scrollbar-thumb {
  background: #3a3a3a;
  border: 2px solid #0b0b0b;
  border-radius: 999px;
}

body.dark ::-webkit-scrollbar-thumb:hover {
  background: #4b4b4b;
}

.top {
  position: sticky;
  top: 0;
  z-index: 100;
  background: #141414;
  border-bottom: 0;
  padding: 8px 12px;
  box-shadow: none;
}

.row {
  display: flex;
  gap: 6px;
  flex-wrap: wrap;
  align-items: center;
}

.top-controls {
  margin-top: 2px;
}

.top .top-controls .top-control-action {
  align-self: stretch;
  min-height: 0;
  padding: 5px 10px;
  border: 1px solid var(--border);
  border-radius: 8px;
  background: #1b1b1b;
  font-size: 12px;
}

.top-menu {
  position: relative;
}

.top-menu > summary {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: 32px;
  box-sizing: border-box;
  padding: 7px 10px;
  color: var(--fg);
  background: #1f1f1f;
  border: 1px solid #353535;
  border-radius: 6px;
  font-weight: 650;
  cursor: pointer;
  list-style: none;
  user-select: none;
}

.top-menu > summary::-webkit-details-marker {
  display: none;
}

.top-menu > summary::after {
  content: "";
  width: 6px;
  height: 6px;
  border-right: 1.5px solid var(--muted);
  border-bottom: 1.5px solid var(--muted);
  transform: translateY(-2px) rotate(45deg);
}

.top-menu[open] > summary,
.top-menu > summary:hover {
  background: #272727;
  border-color: #4b5563;
}

.top-menu[open] > summary::after {
  transform: translateY(2px) rotate(225deg);
}

.top-menu-popover {
  position: absolute;
  top: calc(100% + 5px);
  left: 0;
  z-index: 160;
  display: grid;
  min-width: 190px;
  padding: 5px;
  background: #171717;
  border: 1px solid #353535;
  border-radius: 7px;
  box-shadow: 0 10px 28px rgba(0,0,0,.45);
}

.top .top-menu-popover form {
  display: block;
  width: 100%;
}

.top .top-menu-popover button {
  width: 100%;
  min-height: 32px;
  padding: 6px 8px;
  border: 0;
  background: transparent;
  text-align: left;
}

.top .top-menu-popover button:hover {
  background: #262626;
}

.top .top-menu-popover .toolbar-btn-content {
  width: 100%;
  justify-content: flex-start;
}

.help-modal {
  width: min(760px, 100%);
}

.help-content {
  padding: 14px 16px 18px;
  color: var(--fg);
  line-height: 1.55;
}

.help-content h4 {
  margin: 16px 0 5px;
  color: #f5f5f5;
  font-size: 13px;
}

.help-content h4:first-child {
  margin-top: 0;
}

.help-content p,
.help-content ul {
  margin: 0 0 8px;
}

.help-content ul {
  padding-left: 20px;
}

.help-content kbd {
  padding: 1px 5px;
  border: 1px solid #3a3a3a;
  border-radius: 4px;
  background: #111;
  color: #f5f5f5;
  font: inherit;
  font-size: 11px;
}

.top form {
  display: inline-block;
  margin: 0;
}

.top a {
  display: inline-block;
  color: inherit;
  text-decoration: none;
}

.top button,
.top input,
.top select,
.top textarea {
  background: #1f1f1f;
  color: var(--fg);
  border: 1px solid #353535;
  border-radius: 6px;
  padding: 7px 10px;
  font: inherit;
}

.top button {
  cursor: pointer;
  min-height: 32px;
  font-weight: 650;
  transition: border-color .12s ease, background .12s ease, color .12s ease, transform .08s ease;
}

.top button:hover {
  border-color: #4b5563;
  background: #272727;
  box-shadow: none;
}

.top button:active {
  transform: translateY(1px);
}

.top button:disabled {
  cursor: not-allowed;
  color: #666;
  background: #171717;
  border-color: #242424;
}

.top button.mask-mode-btn.is-active {
  border-color: var(--ok);
  box-shadow: 0 0 0 1px rgba(34,197,94,.45) inset;
}

.top #saveAllBtn.has-unsaved {
  color: #fff;
  background: #b4233c;
  border-color: #ef5f76;
}

.top #saveAllBtn.has-unsaved:hover {
  color: #fff;
  background: #c92b47;
  border-color: #ff7890;
}

.top .toolbar-btn-content {
  display: inline-flex;
  align-items: center;
  justify-content: center;
  gap: 7px;
  white-space: nowrap;
}

.top .toolbar-btn-icon {
  width: 16px !important;
  height: 16px !important;
  max-width: 16px !important;
  max-height: 16px !important;
  object-fit: contain;
  image-rendering: auto;
  opacity: .95;
  filter: none;
  margin-right: 0;
  flex: 0 0 auto;
}

.top .range-wrap {
  display: inline-flex;
  align-items: center;
  gap: 8px;
  min-height: auto;
  background: #1b1b1b;
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 5px 9px;
  color: var(--muted);
  font-size: 12px;
}

.top .range-wrap label,
.top .range-wrap > span:first-child {
  color: var(--muted);
  font-size: 12px;
  font-weight: 650;
}

.top .range-wrap input[type="range"] {
  width: 150px;
  min-height: 0;
  padding: 0;
  border: 0;
  background: transparent;
}

.top .range-wrap span:not(:first-child) {
  color: var(--fg);
  min-width: 28px;
  text-align: right;
  font-variant-numeric: tabular-nums;
}

.top .muted {
  color: var(--muted);
}

.statusbar {
  position: fixed;
  left: 0;
  right: 0;
  bottom: 0;
  z-index: 150;
  min-height: 28px;
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 16px;
  padding: 5px 12px;
  background: #141414;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 12px;
  font-weight: 650;
  box-shadow: 0 -1px 0 rgba(255,255,255,.03);
}
.statusbar-folder {
  flex: 1 1 auto;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.statusbar-message {
  flex: 0 1 45%;
  min-width: 80px;
  overflow: hidden;
  text-align: right;
  text-overflow: ellipsis;
  white-space: nowrap;
  color: var(--fg);
}
.category-taskbar {
  position: relative;
  z-index: 1;
  height: 28px;
  display: flex;
  align-items: stretch;
  gap: 4px;
  margin-top: 0;
  margin-bottom: 0;
  padding: 0;
  overflow-x: auto;
  overflow-y: hidden;
  background: #141414;
}
.category-taskbar-tab {
  flex: 0 0 auto;
  width: 168px;
  height: 28px;
  padding: 0 7px;
  border: 1px solid var(--border);
  border-bottom: 0;
  border-radius: 7px 7px 0 0;
  background: #2b2b2b;
  color: var(--fg);
  font: inherit;
  font-size: 11px;
  line-height: 1;
  display: inline-flex;
  align-items: center;
  gap: 7px;
  overflow: hidden;
  pointer-events: auto;
  cursor: default;
  user-select: none;
}
.category-taskbar-tab.desktop-tab {
  width: 128px;
}
.category-taskbar-tab-icon {
  width: 14px;
  height: 14px;
  border: 1px solid color-mix(in srgb, var(--window-color, var(--accent)) 70%, var(--border));
  background: color-mix(in srgb, var(--window-color, var(--accent)) 26%, #222);
  border-radius: 3px;
  object-fit: cover;
  flex: 0 0 auto;
}
.category-taskbar-tab-label {
  min-width: 0;
  flex: 1 1 auto;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
  text-align: left;
  cursor: default;
  user-select: none;
}
.category-taskbar-tab-close {
  width: 17px;
  height: 17px;
  min-width: 17px;
  padding: 0;
  border: 0;
  border-radius: 999px;
  background: transparent;
  color: var(--fg);
  display: grid;
  place-items: center;
  font-size: 14px;
  line-height: 1;
  flex: 0 0 auto;
}
.category-taskbar-tab-close:hover {
  background: rgba(255,255,255,.12);
}
.category-taskbar-tab.active {
  color: var(--fg);
  border-color: #505050;
  background: #3a3a3a;
}
.category-taskbar-tab.minimized {
  background: #242424;
  color: var(--muted);
}

.category-taskbar-tab.minimized.active {
  background: #303030;
}

body {
  padding-bottom: 32px;
  min-height: 100vh;
  box-sizing: border-box;
  display: flex;
  flex-direction: column;
}

.category-desktop {
  position: relative;
  flex: 1 1 auto;
  min-height: 0;
  margin: 0;
  border: 0;
  background: color-mix(in srgb, var(--bg) 82%, var(--card));
  overflow: hidden;
}
.desktop-icon-layer,
.window-layer {
  position: absolute;
  inset: 0;
}
.desktop-icon-layer {
  top: 12px;
}
.desktop-folder {
  position: absolute;
  width: 112px;
  height: 120px;
  display: grid;
  justify-items: center;
  align-content: start;
  padding: 0;
  border: 0;
  background: transparent;
  outline: 0;
  cursor: default;
  user-select: none;
  touch-action: none;
  z-index: 3;
}
.desktop-folder.dragging {
  opacity: .82;
  z-index: 8;
}
.desktop-folder.active {
  background: transparent;
  border: 0;
  outline: 0;
}
.desktop-folder-inner {
  display: inline-grid;
  justify-items: center;
  align-content: start;
  gap: 3px;
  width: 104px;
  height: 116px;
  padding: 3px 4px;
  border: 0;
  border-radius: 0;
  background: transparent;
  box-sizing: border-box;
}
.desktop-folder.active .desktop-folder-inner,
.desktop-folder.selected .desktop-folder-inner {
  background: transparent;
  border: 0;
}
.desktop-folder-icon {
  width: 58px;
  height: 58px;
  border-radius: 8px;
  border: 2px solid color-mix(in srgb, var(--folder-color) 78%, var(--border));
  background: color-mix(in srgb, var(--folder-color) 24%, var(--card));
  overflow: hidden;
  display: grid;
  place-items: center;
}
.desktop-folder.active .desktop-folder-icon,
.desktop-folder.selected .desktop-folder-icon {
  border-color: color-mix(in srgb, var(--folder-color) 94%, white);
  background: color-mix(in srgb, var(--folder-color) 34%, var(--card));
  filter: brightness(1.18);
}
.desktop-folder-icon img {
  width: 100%;
  height: 100%;
  object-fit: cover;
  -webkit-user-drag: none;
  user-select: none;
}
.desktop-folder-name,
.desktop-folder-count {
  width: 100%;
  text-align: center;
  line-height: 1.15;
  text-shadow: 0 1px 2px rgba(0,0,0,.35);
}
.desktop-folder-name {
  font-weight: 800;
  font-size: 11px;
  min-height: 26px;
  display: flex;
  align-items: center;
  justify-content: center;
  overflow: visible;
  white-space: normal;
  overflow-wrap: normal;
  word-break: normal;
}
.desktop-folder-count {
  color: var(--muted);
  font-size: 10px;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.desktop-selection-box {
  position: absolute;
  z-index: 6;
  border: 1px solid color-mix(in srgb, var(--accent) 78%, white);
  background: color-mix(in srgb, var(--accent) 20%, transparent);
  pointer-events: none;
}
.category-window {
  box-sizing: border-box;
  position: absolute;
  min-width: 420px;
  min-height: 300px;
  width: min(860px, calc(100% - 24px));
  height: min(680px, calc(100% - 24px));
  border: 1px solid var(--border);
  border-radius: 6px;
  background: #121212;
  box-shadow: 0 20px 45px rgba(0,0,0,.58), 0 4px 12px rgba(0,0,0,.42);
  display: grid;
  grid-template-rows: 38px 34px minmax(0, 1fr) 30px;
  resize: both;
  overflow: hidden;
  z-index: 10;
  filter: brightness(.42);
}
.category-window[hidden] {
  display: none;
}
.category-window.maximized {
  left: 10px !important;
  top: 10px !important;
  width: calc(100% - 20px) !important;
  height: calc(100% - 20px) !important;
}
.category-window.active {
  border-color: var(--border);
  box-shadow: 0 30px 70px rgba(0,0,0,.68), 0 8px 22px rgba(0,0,0,.52);
  filter: brightness(1);
}
.category-window-head {
  display: flex;
  align-items: center;
  gap: 8px;
  padding: 0 8px;
  background: color-mix(in srgb, var(--card) 88%, var(--window-color, #64748b));
  border-bottom: 1px solid var(--border);
  cursor: move;
}
.category-window-title {
  font-weight: 800;
  min-width: 0;
  overflow: hidden;
  text-overflow: ellipsis;
  white-space: nowrap;
}
.category-window-actions {
  margin-left: auto;
  display: inline-flex;
  gap: 6px;
}
.category-window-actions button {
  width: 28px;
  height: 28px;
  min-width: 28px;
  padding: 0;
  border-radius: 999px;
}
.category-window-tools {
  display: flex;
  align-items: center;
  gap: 5px;
  padding: 4px 8px;
  background: #171717;
  border-bottom: 1px solid var(--border);
  overflow-x: auto;
  overflow-y: hidden;
}
.category-window-tool-btn {
  min-height: 23px;
  padding: 3px 6px;
  font-size: 11px;
  border-radius: 6px;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  white-space: nowrap;
}
.category-window-tool-btn img {
  width: 13px !important;
  height: 13px !important;
  max-width: 13px !important;
  max-height: 13px !important;
  object-fit: contain;
}
.category-window-range {
  min-height: 23px;
  padding: 0 2px;
  border: 0;
  border-radius: 0;
  background: transparent;
  display: inline-flex;
  align-items: center;
  gap: 4px;
  font-size: 10px;
  white-space: nowrap;
}
.category-window-range label {
  font-size: 10px;
  font-weight: 650;
}
.category-window-range input[type="range"] {
  width: 70px;
  min-width: 70px;
}
.category-window-range-value {
  min-width: 24px;
  text-align: right;
  color: var(--muted);
  font-size: 10px;
}
.category-window-body {
  overflow: auto;
  padding: 12px;
  background: #121212;
}
.category-window-body .grid {
  grid-template-columns: repeat(auto-fill, minmax(min(100%, var(--card-min-width, 460px)), 1fr));
}
.category-window-status {
  border-top: 1px solid var(--border);
  color: var(--muted);
  display: flex;
  align-items: center;
  padding: 0 10px;
  font-size: 12px;
  background: var(--card);
}
.pair-card.selected {
  border-color: var(--ok);
  box-shadow: 0 0 0 2px color-mix(in srgb, var(--ok) 72%, transparent), var(--shadow);
}
.pair-card.cut-pending {
  position: relative;
}
.pair-card.cut-pending::after {
  content: "";
  position: absolute;
  inset: 0;
  background: rgba(0,0,0,.48);
  border-radius: inherit;
  pointer-events: none;
  z-index: 8;
}
.desktop-context-menu {
  position: fixed;
  z-index: 1700;
  min-width: 160px;
  background: var(--card);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 8px;
  box-shadow: 0 18px 34px rgba(0,0,0,.34);
  padding: 5px;
  display: none;
}
.desktop-context-menu.open {
  display: grid;
}
.desktop-context-menu button {
  width: 100%;
  border: 0;
  background: transparent;
  color: inherit;
  padding: 7px 9px;
  text-align: left;
  border-radius: 6px;
  font: inherit;
}
.desktop-context-menu button:hover {
  background: color-mix(in srgb, var(--accent) 18%, transparent);
}
.desktop-context-menu button:disabled {
  color: var(--muted);
  opacity: .5;
}
.category-dialog-backdrop {
  position: fixed;
  inset: 0;
  z-index: 1600;
  background: rgba(0,0,0,.45);
  display: none;
  align-items: center;
  justify-content: center;
  padding: 16px;
}
.category-dialog-backdrop.open {
  display: flex;
}
.category-dialog {
  width: min(340px, 100%);
  background: var(--card);
  border: 1px solid var(--border);
  border-radius: 8px;
  padding: 12px;
  box-shadow: 0 24px 44px rgba(0,0,0,.35);
}
.category-dialog h3 {
  margin: 0 0 10px;
  font-size: 16px;
}
.category-dialog label {
  display: grid;
  gap: 4px;
  margin-bottom: 8px;
  font-size: 12px;
  color: var(--muted);
}
.category-dialog input,
.category-dialog select {
  width: 100%;
  min-height: 30px;
  padding: 4px 7px;
  box-sizing: border-box;
}
.category-dialog input[type="color"] {
  width: 56px;
  min-height: 28px;
  padding: 2px;
}
#categoryIconInput {
  max-width: 220px;
}
.category-dialog-actions {
  display: flex;
  justify-content: flex-end;
  gap: 6px;
}
.app-dialog {
  width: min(460px, 100%);
}
.app-dialog-message {
  white-space: pre-wrap;
  line-height: 1.45;
  color: var(--fg);
  padding: 10px 12px;
}
.app-dialog-input {
  width: calc(100% - 24px);
  box-sizing: border-box;
  margin: 8px 12px 0;
}
.app-dialog-actions {
  justify-content: flex-end;
  background: transparent !important;
  border-top: 0 !important;
}
.mask-inline-options {
  display: flex;
  align-items: center;
  gap: 14px;
  flex-wrap: wrap;
}
.mask-inline-options label {
  display: inline-flex;
  flex-direction: row;
  align-items: center;
  gap: 7px;
}
.mask-mode-row {
  display: flex;
  justify-content: flex-start;
  padding: 6px 12px 10px;
}
#maskToggleModeBtn {
  box-sizing: border-box;
  width: 166px;
  text-align: center;
  white-space: nowrap;
}
.mask-actions {
  border-top: 0 !important;
}

@media (max-width: 720px) {
  .top {
    position: relative;
  }
  .top .range-wrap {
    width: 100%;
    box-sizing: border-box;
  }
  .topbar-actions {
    grid-template-columns: 1fr;
  }
  .action-group,
  .topbar-row {
    width: 100%;
  }
  .action-group button {
    flex: 1 1 auto;
  }
  .grid {
    grid-template-columns: 1fr;
    padding-inline: 8px;
  }
  .caption-textarea {
    min-height: 110px;
  }
  .range-wrap {
    border-radius: 8px;
    width: 100%;
    box-sizing: border-box;
  }
  .category-desktop {
    min-height: 0;
    margin-inline: 0;
  }
  .category-window {
    min-width: min(360px, calc(100% - 16px));
    width: calc(100% - 16px);
    left: 8px !important;
  }
}


</style>
</head>
<body>
<div class="drop-paste-overlay" id="dropPasteOverlay">Drop images to add them</div>
<div class="top">
  <div class="row" style="margin-bottom:8px;">
    <details class="top-menu">
      <summary>File</summary>
      <div class="top-menu-popover">
        <form method="POST" action="/open_folder" id="openFolderForm"><button type="submit" title="Open an image folder"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_open_folder.svg" alt="">Open Folder</span></button></form>
        <form method="POST" action="/add_files" id="addFilesForm"><input type="hidden" name="category" id="addFilesCategoryInput" value="Undefined"><button type="submit" title="Add image files"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_add_files.svg" alt="">Add Images</span></button></form>
        <button type="button" id="openFileManagerBtn" title="Show the opened folder in File Explorer"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_open_file_manager.svg" alt="">Show Folder</span></button>
        <button type="button" id="convertBtn" title="Convert images to PNG"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_convert_png.svg" alt="">Convert to PNG</span></button>
        <form method="GET" action="/backup" class="backup-form"><button type="submit" title="Back up image and caption pairs"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_backup.svg" alt="">Backup</span></button></form>
        <form method="POST" action="/close_folder" id="closeFolderForm"><button type="submit" id="closeFolderBtn" title="Close Folder"><span class="toolbar-btn-content">Close Folder</span></button></form>
      </div>
    </details>
    <details class="top-menu">
      <summary>Edit</summary>
      <div class="top-menu-popover">
        <button type="button" id="maskModeBtn" class="mask-mode-btn" title="Open mask tools" aria-pressed="false"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_masking.svg" alt="">Masking</span></button>
        <button type="button" id="renameAllBtn" title="Rename all image and caption pairs"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_rename_all.svg" alt="">Rename</span></button>
        <button type="button" id="openWatermarkModalBtn" title="Remove watermarks with an inpainting mask"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_watermark_removal.svg" alt="">Remove watermark</span></button>
      </div>
    </details>
    <details class="top-menu">
      <summary>Tools</summary>
      <div class="top-menu-popover">
        <button type="button" id="openJoyModalBtn" title="Generate captions"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_caption.svg" alt="">Auto-caption</span></button>
        <button type="button" id="openToolsModalBtnInline" title="Batch edit caption text"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_text_tools.svg" alt="">Text tools</span></button>
        <button type="button" id="openSummaryModalBtn" title="Show dataset statistics"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_statistics.svg" alt="">Stats</span></button>
        <button type="button" id="openJsonModalBtn" title="Inspect and edit Ideogram 4 JSON captions"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_json_captions.svg" alt="">JSON captions</span></button>
      </div>
    </details>
    <details class="top-menu">
      <summary>Mode</summary>
      <div class="top-menu-popover">
        <form method="POST" action="/switch/simple"><button type="submit" title="Switch to Default mode"><span class="toolbar-btn-content">Default mode</span></button></form>
        <button type="button" disabled aria-current="page"><span class="toolbar-btn-content">Workspace mode</span></button>
      </div>
    </details>
    <details class="top-menu">
      <summary>Help</summary>
      <div class="top-menu-popover">
        <button type="button" id="openHelpModalBtn"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_quick_guide.svg" alt="">Quick guide</span></button>
      </div>
    </details>
  </div>
  <div class="row top-controls">
    <div class="range-wrap">
      <label for="textHeightSlider">Text height</label>
      <input type="range" id="textHeightSlider" min="60" max="360" step="10" value="110">
      <span id="textHeightValue">110</span>px
    </div>
    <div class="range-wrap">
      <label for="imageHeightSlider">Image size</label>
      <input type="range" id="imageHeightSlider" min="180" max="720" step="20" value="420">
      <span id="imageHeightValue">420</span>px
    </div>
    <div class="range-wrap">
      <span>Crop base</span>
      <label><input type="radio" name="crop_base" value="512" {% if selected_crop_base == 512 %}checked{% endif %}> 512</label>
      <label><input type="radio" name="crop_base" value="768" {% if selected_crop_base == 768 %}checked{% endif %}> 768</label>
      <label><input type="radio" name="crop_base" value="1024" {% if selected_crop_base == 1024 %}checked{% endif %}> 1024</label>
      <label><input type="radio" name="crop_base" value="1280" {% if selected_crop_base == 1280 %}checked{% endif %}> 1280</label>
      <label><input type="radio" name="crop_base" value="1536" {% if selected_crop_base == 1536 %}checked{% endif %}> 1536</label>
    </div>
    <button type="button" id="refreshFolderBtn" class="top-control-action" title="Refresh the opened folder"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_refresh.svg" alt="">Refresh</span></button>
    <button type="button" id="saveAllBtn" class="top-control-action" title="Save every unsaved item"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_save_all.svg" alt="">Save</span></button>
  </div>
</div>

{% if not folder_name %}
<div class="notice">
  No folder is open. Select <b>File &gt; Open Folder</b> to load images and captions.
</div>
{% elif not pairs %}
<div class="notice">
  Folder <b>({{ folder_name }})</b> is open but does not contain any images.
</div>
{% endif %}

{% if folder_name %}
<div class="category-taskbar" id="statusbarTabs" aria-label="Open category folders"></div>
<div class="category-desktop" id="categoryDesktop">
  <div class="desktop-icon-layer" id="desktopIconLayer">
    {% for category in category_folders %}
      <div class="desktop-folder"
           data-category="{{ category.name }}"
           data-group-id="{{ category.group_id }}"
           data-icon="{{ category.icon }}"
           data-color="{{ category.color }}"
           style="left:{{ category.x }}px; top:{{ category.y }}px; --folder-color: {{ category.color }};">
        <div class="desktop-folder-inner">
          <div class="desktop-folder-icon"><img src="/category_icon/{{ category.icon }}" alt="" draggable="false"></div>
          <div class="desktop-folder-name">{{ category.name }}</div>
          <div class="desktop-folder-count">{{ category.count }} images</div>
        </div>
      </div>
    {% endfor %}
  </div>
  <div class="window-layer" id="windowLayer"></div>
</div>
{% endif %}

<div id="pairPool" hidden>
<div class="grid">
{% for pair in pairs %}
  <div class="pair-card" data-index="{{ pair.index }}" data-img="{{ pair.img_name }}" data-category="{{ pair.category }}">
    <div class="card-head">
      <div class="filename" data-index="{{ pair.index }}" title="Double-click to rename">{{ pair.img_name }}</div>
      <div class="status-wrap">
        <span class="unsaved-label" id="unsaved-label-{{ pair.index }}">unsaved</span>
        <div class="status-dot" id="status-dot-{{ pair.index }}"></div>
      </div>
      <div class="card-head-actions">
        <button type="button" class="card-head-action delete-btn" data-index="{{ pair.index }}" data-img="{{ pair.img_name }}" title="Delete" aria-label="Delete">×</button>
        <button type="button" class="card-head-action clone-btn" data-index="{{ pair.index }}" data-img="{{ pair.img_name }}" title="Clone" aria-label="Clone">+</button>
      </div>
    </div>

    <div class="crop-wrap">
      <div class="media-top-row" data-index="{{ pair.index }}">
        <div class="meta-row">
          <span class="badge dims-badge" id="dims-badge-{{ pair.index }}" data-width="{{ pair.width }}" data-height="{{ pair.height }}"></span>
        </div>
        <div class="crop-label" id="crop-label-{{ pair.index }}">No crop selected</div>
        <div class="media-zoom-row">
          <button type="button" class="media-zoom-btn zoom-in-btn" data-index="{{ pair.index }}" title="Zoom in">+</button>
          <button type="button" class="media-zoom-btn zoom-default-btn" data-index="{{ pair.index }}" title="Fit zoom">fit</button>
          <button type="button" class="media-zoom-btn zoom-actual-btn" data-index="{{ pair.index }}" title="100% zoom">100%</button>
          <button type="button" class="media-zoom-btn zoom-out-btn" data-index="{{ pair.index }}" title="Zoom out">-</button>
        </div>
      </div>
      <div class="crop-stage" id="crop-stage-{{ pair.index }}" data-index="{{ pair.index }}" data-width="{{ pair.width }}" data-height="{{ pair.height }}">
        <img src="/image/{{ pair.img_name }}" id="crop-image-{{ pair.index }}" alt="crop {{ pair.img_name }}">
        <canvas class="mask-canvas" id="mask-canvas-{{ pair.index }}" data-index="{{ pair.index }}"></canvas>
        <div class="crop-overlay" id="crop-overlay-{{ pair.index }}"></div>
        <div class="zoom-readout" id="zoom-readout-{{ pair.index }}">100%</div>
        <div class="crop-box" id="crop-box-{{ pair.index }}">
          <div class="handle nw" data-handle="nw"></div>
          <div class="handle ne" data-handle="ne"></div>
          <div class="handle sw" data-handle="sw"></div>
          <div class="handle se" data-handle="se"></div>
          <div class="handle n" data-handle="n"></div>
          <div class="handle s" data-handle="s"></div>
          <div class="handle w" data-handle="w"></div>
          <div class="handle e" data-handle="e"></div>
        </div>
      </div>

      <div class="rotate-row">
        <label for="rotate-slider-{{ pair.index }}">Rotate</label>
        <input type="range" class="rotate-slider" id="rotate-slider-{{ pair.index }}" data-index="{{ pair.index }}" min="-180" max="180" step="1" value="0">
        <span class="rotate-value" id="rotate-value-{{ pair.index }}">0°</span>
      </div>
      <div class="mask-size-row" aria-hidden="true"></div>
    </div>

    <div class="card-actions">
      <button type="button" class="icon-btn mask-tool-btn mask-brush-btn active" data-index="{{ pair.index }}" data-tool="brush" title="Brush" aria-label="Brush">
        <img src="/category_icon/btn_mask_brush.svg" alt="">
      </button>
      <button type="button" class="icon-btn mask-tool-btn mask-fill-btn" data-index="{{ pair.index }}" data-tool="fill" title="Fill" aria-label="Fill">
        <img src="/category_icon/btn_mask_fill.svg" alt="">
      </button>
      <button type="button" class="icon-btn auto-crop-btn" data-index="{{ pair.index }}" title="Auto crop" aria-label="Auto crop">
        <img src="/category_icon/btn_card_autocrop.svg" alt="">
      </button>
      <button type="button" class="icon-btn ratio-lock-btn" data-index="{{ pair.index }}" title="Aspect ratio lock" aria-label="Aspect ratio lock">
        <img src="/category_icon/btn_card_ratio_lock.svg" alt="">
      </button>
      <button type="button" class="icon-btn undo-btn" data-index="{{ pair.index }}" title="Undo" aria-label="Undo">
        <img src="/category_icon/btn_card_undo.svg" alt="">
      </button>
      <button type="button" class="icon-btn redo-btn" data-index="{{ pair.index }}" title="Redo" aria-label="Redo">
        <img src="/category_icon/btn_card_redo.svg" alt="">
      </button>
      <button type="button" class="icon-btn flip-h-btn" data-index="{{ pair.index }}" data-img="{{ pair.img_name }}" title="Flip horizontally" aria-label="Flip horizontally">
        <img src="/category_icon/btn_card_flip_h.svg" alt="">
      </button>
      <button type="button" class="icon-btn flip-v-btn" data-index="{{ pair.index }}" data-img="{{ pair.img_name }}" title="Flip vertically" aria-label="Flip vertically">
        <img src="/category_icon/btn_card_flip_v.svg" alt="">
      </button>
      <button type="button" class="icon-btn automask-btn" data-index="{{ pair.index }}" data-img="{{ pair.img_name }}" title="Auto mask" aria-label="Auto mask">
        <img src="/category_icon/btn_masking.svg" alt="">
      </button>
      <button type="button" class="icon-btn watermark-apply-btn" data-index="{{ pair.index }}" title="Remove" aria-label="Remove">
        <img src="/category_icon/btn_card_watermark_remove.svg" alt="">
      </button>
      <button type="button" class="icon-btn save-btn" id="save-btn-{{ pair.index }}" data-index="{{ pair.index }}" title="Save" aria-label="Save">
        <img src="/category_icon/btn_card_save.svg" alt="">
      </button>
    </div>

    <textarea class="caption-textarea" data-index="{{ pair.index }}" data-img="{{ pair.img_name }}" data-original={{ pair.text | tojson }} placeholder="Enter caption here...">{{- pair.text -}}</textarea>
    <div class="caption-stats" data-index="{{ pair.index }}">
      <span class="caption-char-count">0 chars</span>
      <span class="caption-token-count">0 tokens</span>
    </div>
  </div>
{% endfor %}
</div>
</div>

<div class="statusbar">
  <span class="statusbar-folder">
    {% if folder_name %}
      Opened folder: {{ folder_name }} - {{ pairs|length }} image{% if pairs|length != 1 %}s{% endif %}.
    {% else %}
      No folder opened
    {% endif %}
  </span>
  <span class="statusbar-message" id="statusbarMessage">{% if message %}{{ message|safe }}{% endif %}</span>
</div>

<div class="mask-size-popover" id="maskSizePopover" aria-hidden="true">
  <span class="mask-popover-label">Feather</span>
        <input type="range" id="popupMaskFeatherSlider" min="0" max="100" step="1" value="0" title="Feather" aria-label="Feather">
  <span id="popupMaskFeatherValue">0px</span>
  <span class="mask-popover-label">Size</span>
  <input type="range" id="popupMaskSizeSlider" min="2" max="160" step="1" value="32" title="Brush size" aria-label="Brush size">
  <span id="popupMaskSizeValue">32px</span>
</div>
<div class="mask-size-popover fill-tolerance-popover" id="maskFillTolerancePopover" aria-hidden="true">
  <span class="mask-popover-label">Tolerance</span>
  <input type="range" id="popupMaskFillToleranceSlider" min="0" max="255" step="1" value="32" title="Fill tolerance" aria-label="Fill tolerance">
  <span id="popupMaskFillToleranceValue">32</span>
</div>

<div class="desktop-context-menu" id="desktopContextMenu" role="menu">
  <button type="button" data-action="new-folder">New Folder</button>
  <button type="button" data-action="new-category-group">New Category Group</button>
  <button type="button" data-action="manage-category-groups">Manage Category Groups</button>
</div>
<div class="desktop-context-menu" id="folderContextMenu" role="menu">
  <button type="button" data-action="edit-folder">Edit Folder</button>
  <button type="button" data-action="delete-folder">Delete Folder</button>
</div>
<div class="desktop-context-menu" id="cardContextMenu" role="menu">
  <button type="button" data-action="copy-card">Copy</button>
  <button type="button" data-action="cut-card">Cut</button>
  <button type="button" data-action="delete-card">Delete</button>
</div>

<div class="category-dialog-backdrop" id="categoryDialogBackdrop">
  <form class="category-dialog" id="categoryDialog">
    <h3 id="categoryDialogTitle">Category folder</h3>
    <input type="hidden" id="categoryOldName">
    <label>
      Name
      <input type="text" id="categoryNameInput" required>
    </label>
    <label>
      Color
      <input type="color" id="categoryColorInput" value="#9ca3af">
    </label>
    <label>
      Category group
      <select id="categoryGroupInput">
        {% for group in category_groups %}
          <option value="{{ group.id }}">{{ group.name }}</option>
        {% endfor %}
      </select>
    </label>
    <label>
      Icon
      <select id="categoryIconInput">
        {% for category in icon_options %}
          <option value="{{ category.icon }}">{{ category.name }}</option>
        {% endfor %}
        <option value="__upload_icon__">Add icon from disk...</option>
      </select>
      <input type="file" id="categoryIconFileInput" accept="image/*" hidden>
    </label>
    <div class="category-dialog-actions">
      <button type="button" id="cancelCategoryDialogBtn">Cancel</button>
      <button type="submit">Save</button>
    </div>
  </form>
</div>

<div class="category-dialog-backdrop" id="categoryGroupDialogBackdrop">
  <form class="category-dialog" id="categoryGroupDialog">
    <h3>Category groups</h3>
    <label>
      Group
      <select id="categoryGroupManageSelect">
        <option value="__new__">New category group</option>
        {% for group in category_groups %}
          <option value="{{ group.id }}">{{ group.name }}</option>
        {% endfor %}
      </select>
    </label>
    <label>
      Name
      <input type="text" id="categoryGroupNameInput" required>
    </label>
    <label>
      Color
      <input type="color" id="categoryGroupColorInput" value="#9ca3af">
    </label>
    <div class="category-dialog-actions">
      <button type="button" id="deleteCategoryGroupBtn">Delete</button>
      <button type="button" id="cancelCategoryGroupDialogBtn">Cancel</button>
      <button type="submit">Save</button>
    </div>
  </form>
</div>

<div class="joy-modal-backdrop" id="joyModalBackdrop">
  <div class="joy-modal" role="dialog" aria-modal="true" aria-labelledby="joyModalTitle">
    <div class="joy-modal-head">
      <h3 id="joyModalTitle">Caption</h3>
      <button type="button" class="joy-close-btn" id="closeJoyModalBtn">×</button>
    </div>

    <div class="joy-grid">
      <label>
        Backend
        <select id="joy_backend">
          <option value="joycaption">JoyCaption</option>
          <option value="wd14">WD-14</option>
          <option value="qwen3_vl">Qwen3-VL</option>
          <option value="external_api">External API</option>
        </select>
      </label>
      <label>
        HF token
        <input type="password" id="joy_hf_token" placeholder="Optional HF token">
      </label>
      <label class="qwen3vl-only" style="display:none;">
        Qwen3-VL model
        <select id="joy_qwen3vl_model">
          <option>Qwen3-VL-4B-Instruct</option>
          <option>Qwen3-VL-8B-Instruct</option>
          <option>Huihui-Qwen3-VL-8B-Instruct-abliterated</option>
        </select>
      </label>
      <label>
        Caption format
        <select id="joy_caption_format">
          <option value="standard_text">Standard text (.txt)</option>
          <option value="ideogram4_json">Ideogram 4 JSON (.json)</option>
        </select>
      </label>
      <label class="joy-only">
        Quantization
        <select id="joy_quantization">
          <option value="Q4_K">Q4_K</option>
          <option value="Q5_K_M">Q5_K_M</option>
          <option value="Q6_K">Q6_K</option>
          <option value="Q8_0">Q8_0</option>
        </select>
      </label>
      <label class="joy-only">
        Style
        <select id="joy_caption_type">
          <option value="descriptive">Descriptive</option>
          <option value="descriptive_casual">Descriptive (Casual)</option>
          <option value="straightforward">Straightforward</option>
          <option value="stable_diffusion_prompt">Stable Diffusion Prompt</option>
          <option value="midjourney">MidJourney</option>
          <option value="danbooru_tag_list">Danbooru tag list</option>
          <option value="e621_tag_list">e621 tag list</option>
          <option value="rule34_tag_list">Rule34 tag list</option>
          <option value="booru_like_tag_list">Booru-like tag list</option>
          <option value="art_critic">Art Critic</option>
          <option value="product_listing">Product Listing</option>
          <option value="social_media_post">Social Media Post</option>
        </select>
      </label>
      <label class="joy-only">
        Length
        <select id="joy_caption_length">
          <option value="any">any</option>
          <option value="very short">very short</option>
          <option value="short">short</option>
          <option value="medium-length">medium-length</option>
          <option value="long">long</option>
          <option value="very long">very long</option>
          {% for word_count in range(20, 261, 10) %}
            <option value="{{ word_count }}">{{ word_count }} words</option>
          {% endfor %}
        </select>
      </label>
      <label class="joy-only">
        Vision resolution
        <input type="number" id="joy_visionmaxres" min="128" step="64" value="384">
      </label>
      <label class="joy-only">
        Max tokens
        <input type="number" id="joy_max_tokens" min="0" step="1" value="0">
      </label>
      <label class="joy-only">
        Temperature
        <input type="number" id="joy_temperature" min="0" max="2" step="0.05" value="0.6">
      </label>
      <label class="joy-only">
        Top-p
        <input type="number" id="joy_top_p" min="0" max="1" step="0.01" value="0.9">
      </label>
      <label class="wd14-only" style="display:none;">
        WD-14 model
        <select id="joy_wd14_model">
          <option value="convnextv2">ConvNextV2 v2</option>
          <option value="convnext">ConvNext v2</option>
        </select>
      </label>
      <label class="wd14-only" style="display:none;">
        General tags
        <input type="number" id="joy_wd14_general_threshold" min="0" max="1" step="0.01" value="0.35">
      </label>
      <label class="wd14-only" style="display:none;">
        Character tags
        <input type="number" id="joy_wd14_character_threshold" min="0" max="1" step="0.01" value="0.85">
      </label>
      <label class="wd14-only" style="display:none;">
        <span>Include rating tag</span>
        <input type="checkbox" id="joy_wd14_include_rating">
      </label>
      <label class="wd14-only" style="display:none;">
        <span>Include character tags</span>
        <input type="checkbox" id="joy_wd14_include_characters">
      </label>
      <label class="wd14-only" style="display:none;">
        <span>Replace underscores with spaces</span>
        <input type="checkbox" id="joy_wd14_replace_underscores" checked>
      </label>
      <label class="wd14-only" style="display:none; grid-column: 1 / -1;">
        Undesired tags
        <input type="text" id="joy_wd14_undesired_tags" placeholder="eg. tag, tag2, tag3">
      </label>
      <label class="qwen3vl-only" style="display:none;">
        Temperature
        <input type="number" id="joy_qwen3vl_temperature" min="0" max="2" step="0.05" value="0.2">
      </label>
      <label class="qwen3vl-only" style="display:none;">
        Max tokens
        <input type="number" id="joy_qwen3vl_max_tokens" min="1" step="1" value="256">
      </label>
      <label class="qwen3vl-only" style="display:none;">
        Max image side
        <input type="number" id="joy_qwen3vl_max_image_side" min="128" max="4096" step="64" value="512">
      </label>
      <label>
        <span>Skip existing captions</span>
        <input type="checkbox" id="joy_no_overwrite">
      </label>
      <label>
        <span>Append to existing caption</span>
        <input type="checkbox" id="joy_append_existing">
      </label>
      <label>
        <span>Auto scroll log</span>
        <input type="checkbox" id="joyAutoScroll" checked>
      </label>
    </div>

    <div class="tool-box joy-only" id="joyExtraOptionsBox" style="margin-top:12px;">
      <h3 style="margin-bottom:8px;">JoyCaption options</h3>
      <div class="joy-extra-list joy-extra-list-flat" id="joy_extra_options_group">
        <label class="joy-extra-item"><span>If there is a person/character in the image you must refer to them as {name}.</span><input type="checkbox" class="joy-extra-option" value="If there is a person/character in the image you must refer to them as {name}."></label>
        <label class="joy-extra-item"><span>Do NOT include information about people/characters that cannot be changed (like ethnicity, gender, etc), but do still include changeable attributes (like hair style).</span><input type="checkbox" class="joy-extra-option" value="Do NOT include information about people/characters that cannot be changed (like ethnicity, gender, etc), but do still include changeable attributes (like hair style)."></label>
        <label class="joy-extra-item"><span>Include information about the ages of any people/characters when applicable.</span><input type="checkbox" class="joy-extra-option" value="Include information about the ages of any people/characters when applicable."></label>
        <label class="joy-extra-item"><span>Do NOT include anything sexual; keep it PG.</span><input type="checkbox" class="joy-extra-option" value="Do NOT include anything sexual; keep it PG."></label>
        <label class="joy-extra-item"><span>Include whether the image is sfw, suggestive, or nsfw.</span><input type="checkbox" class="joy-extra-option" value="Include whether the image is sfw, suggestive, or nsfw."></label>
        <label class="joy-extra-item"><span>Include information about lighting.</span><input type="checkbox" class="joy-extra-option" value="Include information about lighting."></label>
        <label class="joy-extra-item"><span>Include information about camera angle.</span><input type="checkbox" class="joy-extra-option" value="Include information about camera angle."></label>
        <label class="joy-extra-item"><span>If it is a photo you MUST include information about what camera was likely used and details such as aperture, shutter speed, ISO, etc.</span><input type="checkbox" class="joy-extra-option" value="If it is a photo you MUST include information about what camera was likely used and details such as aperture, shutter speed, ISO, etc."></label>
        <label class="joy-extra-item"><span>Specify the depth of field and whether the background is in focus or blurred.</span><input type="checkbox" class="joy-extra-option" value="Specify the depth of field and whether the background is in focus or blurred."></label>
        <label class="joy-extra-item"><span>If applicable, mention the likely use of artificial or natural lighting sources.</span><input type="checkbox" class="joy-extra-option" value="If applicable, mention the likely use of artificial or natural lighting sources."></label>
        <label class="joy-extra-item"><span>Explicitly specify the vantage height (eye-level, low-angle worm’s-eye, bird’s-eye, drone, rooftop, etc.).</span><input type="checkbox" class="joy-extra-option" value="Explicitly specify the vantage height (eye-level, low-angle worm’s-eye, bird’s-eye, drone, rooftop, etc.)."></label>
        <label class="joy-extra-item"><span>Mention whether the image depicts an extreme close-up, close-up, medium close-up, medium shot, cowboy shot, medium wide shot, wide shot, or extreme wide shot.</span><input type="checkbox" class="joy-extra-option" value="Mention whether the image depicts an extreme close-up, close-up, medium close-up, medium shot, cowboy shot, medium wide shot, wide shot, or extreme wide shot."></label>
        <label class="joy-extra-item"><span>Include information about whether there is a watermark or not.</span><input type="checkbox" class="joy-extra-option" value="Include information about whether there is a watermark or not."></label>
        <label class="joy-extra-item"><span>If there is a watermark, you must mention it.</span><input type="checkbox" class="joy-extra-option" value="If there is a watermark, you must mention it."></label>
        <label class="joy-extra-item"><span>Include information about whether there are JPEG artifacts or not.</span><input type="checkbox" class="joy-extra-option" value="Include information about whether there are JPEG artifacts or not."></label>
        <label class="joy-extra-item"><span>Do NOT mention the image's resolution.</span><input type="checkbox" class="joy-extra-option" value="Do NOT mention the image's resolution."></label>
        <label class="joy-extra-item"><span>Do NOT mention any text that is in the image.</span><input type="checkbox" class="joy-extra-option" value="Do NOT mention any text that is in the image."></label>
        <label class="joy-extra-item"><span>Identify the image orientation (portrait, landscape, or square) and aspect ratio if obvious.</span><input type="checkbox" class="joy-extra-option" value="Identify the image orientation (portrait, landscape, or square) and aspect ratio if obvious."></label>
        <label class="joy-extra-item"><span>You MUST include information about the subjective aesthetic quality of the image from low to very high.</span><input type="checkbox" class="joy-extra-option" value="You MUST include information about the subjective aesthetic quality of the image from low to very high."></label>
        <label class="joy-extra-item"><span>Include information on the image's composition style, such as leading lines, rule of thirds, or symmetry.</span><input type="checkbox" class="joy-extra-option" value="Include information on the image's composition style, such as leading lines, rule of thirds, or symmetry."></label>
        <label class="joy-extra-item"><span>Do NOT use any ambiguous language.</span><input type="checkbox" class="joy-extra-option" value="Do NOT use any ambiguous language."></label>
        <label class="joy-extra-item"><span>ONLY describe the most important elements of the image.</span><input type="checkbox" class="joy-extra-option" value="ONLY describe the most important elements of the image."></label>
        <label class="joy-extra-item"><span>If it is a work of art, do not include the artist's name or the title of the work.</span><input type="checkbox" class="joy-extra-option" value="If it is a work of art, do not include the artist's name or the title of the work."></label>
        <label class="joy-extra-item"><span>Do not mention the mood/feeling/etc of the image.</span><input type="checkbox" class="joy-extra-option" value="Do not mention the mood/feeling/etc of the image."></label>
        <label class="joy-extra-item"><span>Your response will be used by a text-to-image model, so avoid useless meta phrases like “This image shows…”, &quot;You are looking at...&quot;, etc.</span><input type="checkbox" class="joy-extra-option" value="Your response will be used by a text-to-image model, so avoid useless meta phrases like “This image shows…”, "You are looking at...", etc."></label>
        <label class="joy-extra-item"><span>Use vulgar slang and profanity, such as (but not limited to) &quot;fucking,&quot; &quot;slut,&quot; &quot;cock,&quot; etc.</span><input type="checkbox" class="joy-extra-option" value="Use vulgar slang and profanity, such as (but not limited to) "fucking," "slut," "cock," etc."></label>
        <label class="joy-extra-item"><span>Do NOT use polite euphemisms—lean into blunt, casual phrasing.</span><input type="checkbox" class="joy-extra-option" value="Do NOT use polite euphemisms—lean into blunt, casual phrasing."></label>
      </div>
<label id="joy_name_wrap" style="display:none; margin-top:10px;">
        Name used for {name}
        <input type="text" id="joy_person_name" placeholder="Enter name">
      </label>
      <label style="margin-top:10px; display:flex; flex-direction:column; gap:6px;">
        Custom extra instructions
        <textarea id="joy_extra_options" rows="3" placeholder="Optional extra instructions"></textarea>
      </label>
</div>

    <div class="tool-box ideogram4-only" id="ideogram4Settings" style="margin-top:12px; display:none;">
      <h3 style="margin-bottom:8px;">Ideogram 4 JSON options</h3>
      <label style="margin-top:10px; display:flex; flex-direction:column; gap:6px;">
        <span class="qwen-name-title">
          Name
          <span class="regex-help-icon" role="img" aria-label="Ideogram 4 JSON name help" data-tooltip="Adds a user-supplied subject name to the Ideogram 4 JSON caption prompt. The name may be used in existing JSON description fields, but it does not add new JSON keys.">?</span>
        </span>
        <input type="text" id="joy_ideogram4_name" placeholder="Enter character name or trigger word">
      </label>
    </div>

    <div class="tool-box qwen3vl-only qwen3vl-text-only" id="qwen3vlSettings" style="margin-top:12px; display:none;">
      <h3 style="margin-bottom:8px;">Qwen3-VL options</h3>
      <div class="small">The first local run downloads the selected model.</div>
      <label style="margin-top:10px; display:flex; flex-direction:column; gap:6px;">
        <span class="qwen-name-title">
          Name
          <span class="regex-help-icon" role="img" aria-label="Qwen3-VL name help" data-tooltip="Replaces every [name] placeholder in the Qwen3-VL system prompt before captioning. Use a character name or LoRA training trigger. If left empty, [name] is left unchanged.">?</span>
        </span>
        <input type="text" id="joy_qwen3vl_name" placeholder="Enter character name or trigger word">
      </label>
      <label style="margin-top:10px; display:flex; flex-direction:column; gap:6px;">
        System prompt
        <textarea id="joy_qwen3vl_system_prompt" rows="8">Create a natural-language image caption for LoRA training.

Write exactly one concise sentence. Start the caption with [name]. Use [name] as the subject name or training trigger, and mention [name] only once.

Describe only visible details in the image. Focus on expression, gaze, pose, hair, clothing, framing, setting, lighting, background, and image style when visible.

Write in natural language, not as comma-separated tags. Do not use bullet points. Do not invent details. Do not describe identity, age, ethnicity, personality, story, intent, body shape, or body proportions unless clearly required by the visible image.

Do not mention file names, metadata, resolution, image quality, camera model, or that this is an image.

Keep the caption short and direct, usually 12-30 words. Output only the caption.</textarea>
      </label>
    </div>

    <div class="tool-box external-api-only" id="externalApiSettings" style="margin-top:12px; display:none;">
      <h3 style="margin-bottom:8px;">External API options</h3>
      <div class="small">Uses an OpenAI-compatible chat completions API with image input.</div>
      <div class="joy-grid" style="margin-top:10px;">
        <label style="grid-column:1 / -1;">
          API URL
          <input type="text" id="joy_external_api_url" placeholder="http://127.0.0.1:1234">
        </label>
        <label>
          Model ID
          <input type="text" id="joy_external_api_model" placeholder="Any model ID accepted by the server">
        </label>
        <label>
          <span class="qwen-name-title">
            Name
            <span class="regex-help-icon" role="img" aria-label="External API name help" data-tooltip="Replaces every [name] placeholder in the External API system prompt before captioning. Use a character name or LoRA training trigger. If left empty, [name] is left unchanged.">?</span>
          </span>
          <input type="text" id="joy_external_api_name" placeholder="Enter character name or trigger word">
        </label>
        <label>
          API key
          <input type="password" id="joy_external_api_key" placeholder="Optional" autocomplete="off">
        </label>
        <label>
          Temperature
          <input type="number" id="joy_external_api_temperature" min="0" max="2" step="0.05" value="0.2">
        </label>
        <label>
          Max tokens
          <input type="number" id="joy_external_api_max_tokens" min="1" step="1" value="256">
        </label>
        <label>
          <span>Disable thinking/reasoning</span>
          <input type="checkbox" id="joy_external_api_disable_thinking" checked>
        </label>
      </div>
      <label style="margin-top:10px; display:flex; flex-direction:column; gap:6px;">
        System prompt
        <textarea id="joy_external_api_system_prompt" rows="8">Create a natural-language image caption for LoRA training.

Write exactly one concise sentence. Start the caption with [name]. Use [name] as the subject name or training trigger, and mention [name] only once.

Describe only visible details in the image. Focus on expression, gaze, pose, hair, clothing, framing, setting, lighting, background, and image style when visible.

Write in natural language, not as comma-separated tags. Do not use bullet points. Do not invent details. Do not describe identity, age, ethnicity, personality, story, intent, body shape, or body proportions unless clearly required by the visible image.

Do not mention file names, metadata, resolution, image quality, camera model, or that this is an image.

Keep the caption short and direct, usually 12-30 words. Output only the caption.</textarea>
      </label>
    </div>

    <div class="joy-actions">
      <button type="button" id="joyStartBtn">Start</button>
      <button type="button" id="joyInterruptBtn">Interrupt</button>
      <button type="button" id="joyResetSettingsBtn">Reset settings</button>
    </div>

    <div class="joy-progress" id="joyProgress">
      <span class="joy-progress-label" id="joyProgressLabel">Captions: 0</span>
      <span class="joy-progress-percent" id="joyProgressPercent">0%</span>
      <div class="joy-progress-track" aria-hidden="true">
        <div class="joy-progress-fill" id="joyProgressFill"></div>
      </div>
    </div>

    <div class="logbox" id="joyLogBox"></div>
  </div>
</div>

<div class="joy-modal-backdrop" id="maskModalBackdrop">
  <div class="joy-modal" role="dialog" aria-modal="true" aria-labelledby="maskModalTitle">
    <div class="joy-modal-head">
      <h3 id="maskModalTitle">Mask</h3>
      <button type="button" class="joy-close-btn" id="closeMaskModalBtn">x</button>
    </div>

    <div class="mask-mode-row">
      <button type="button" id="maskToggleModeBtn">Enable masking mode</button>
    </div>

    <div class="joy-grid">
      <label>
        REMBG model
        <select id="mask_model">
          <option value="silueta">silueta</option>
          <option value="u2net_human_seg">u2net_human_seg</option>
          <option value="isnet-general-use">isnet-general-use</option>
          <option value="birefnet-general">birefnet-general</option>
          <option value="birefnet-portrait">birefnet-portrait</option>
          <option value="u2net">u2net</option>
          <option value="u2netp">u2netp</option>
        </select>
      </label>
      <label>
        Scope
        <select id="mask_scope">
          <option value="active_category">Active category</option>
          <option value="open_windows">Open category windows</option>
          <option value="all">All images</option>
        </select>
      </label>
      <label>
        Mask expansion
        <input type="number" id="mask_expand_pixels" min="0" max="256" step="1" value="0">
      </label>
      <label>
        Feather
        <input type="number" id="mask_feather_pixels" min="0" max="256" step="1" value="0">
      </label>
      <label>
        Mask opacity
        <input type="range" id="mask_opacity" min="0" max="100" step="1" value="48">
        <span id="mask_opacity_value">48%</span>
      </label>
      <div class="mask-inline-options">
        <label><span>Post-process mask</span><input type="checkbox" id="mask_post_process" checked></label>
        <label><span>Auto scroll log</span><input type="checkbox" id="mask_auto_scroll" checked></label>
      </div>
    </div>

    <div class="joy-actions mask-actions">
      <button type="button" id="maskStartBtn">Start</button>
      <button type="button" id="maskInterruptBtn">Interrupt</button>
      <button type="button" id="maskResetSettingsBtn">Reset settings</button>
      <span class="small" id="maskStatusText" hidden></span>
    </div>

    <div class="joy-progress" id="maskProgress">
      <span class="joy-progress-label" id="maskProgressLabel">Masks: 0</span>
      <span class="joy-progress-percent" id="maskProgressPercent">0%</span>
      <div class="joy-progress-track" aria-hidden="true">
        <div class="joy-progress-fill" id="maskProgressFill"></div>
      </div>
    </div>

    <div class="logbox" id="maskLogBox"></div>
  </div>
</div>

<div class="joy-modal-backdrop" id="watermarkModalBackdrop">
  <div class="joy-modal" role="dialog" aria-modal="true" aria-labelledby="watermarkModalTitle">
    <div class="joy-modal-head">
      <h3 id="watermarkModalTitle">Remove watermark</h3>
      <button type="button" class="joy-close-btn" id="closeWatermarkModalBtn">x</button>
    </div>
    <p class="small watermark-help-text">Enable the editor, paint over the watermark, then use the removal button on that card. The preview is written to disk only when you save the card.</p>
    <div class="mask-mode-row">
      <button type="button" id="watermarkToggleModeBtn">Enable watermark editor</button>
    </div>
    <div class="joy-grid">
      <label>
        Backend
        <select id="watermarkBackend">
          <option value="lama">LaMa (recommended)</option>
          <option value="opencv_telea">OpenCV Telea (fast)</option>
          <option value="opencv_ns">OpenCV Navier-Stokes (fast)</option>
        </select>
      </label>
      <label>
        Mask expansion
        <input type="number" id="watermarkExpandPixels" min="0" max="128" step="1" value="2">
      </label>
      <label id="watermarkRadiusLabel">
        OpenCV radius
        <input type="number" id="watermarkRadius" min="1" max="64" step="1" value="3">
      </label>
    </div>
  </div>
</div>

<div class="joy-modal-backdrop" id="summaryModalBackdrop">
  <div class="joy-modal" role="dialog" aria-modal="true" aria-labelledby="summaryModalTitle">
    <div class="joy-modal-head">
      <h3 id="summaryModalTitle">Statistics</h3>
      <button type="button" class="joy-close-btn" id="closeSummaryModalBtn">×</button>
    </div>
    <div id="summaryContent" class="logbox" style="display:block; max-height:calc(100vh - 180px);"></div>
  </div>
</div>

<div class="joy-modal-backdrop" id="toolsModalBackdrop">
  <div class="joy-modal" role="dialog" aria-modal="true" aria-labelledby="toolsModalTitle">
    <div class="joy-modal-head">
      <h3 id="toolsModalTitle">Text tools</h3>
      <button type="button" class="joy-close-btn" id="closeToolsModalBtn">×</button>
    </div>
    <p class="small text-tools-help">Text tools update the cards only. Use a card Save button or Save all to write the changes; Undo and Reset discard them.</p>

    <div class="joy-grid">
      <div class="tool-box" style="margin:0;">
        <h3>Replace in captions</h3>
        <form method="POST" action="/replace_all" id="replaceForm">
          <input type="hidden" name="category" class="tools-category-input">
          <input type="text" name="match_string" placeholder="Search string or regex" required id="sr_match">
          <input type="text" name="replace_with" placeholder="Replace with" id="sr_replace">
          <label style="display:flex; align-items:center; gap:6px;">
            <input type="checkbox" name="use_regex" value="1" id="sr_use_regex">
            Use regex
            <span class="regex-help-icon" role="img" aria-label="Regex help" data-tooltip="Examples of Regexes:&#10;add string to start:  \A&#10;add string to end:  \Z&#10;target last tag:  ,[^,]*$&#10;replace all: \A.*\Z">?</span>
          </label>
          <button type="submit">Replace all</button>
        </form>
      </div>

      <div class="tool-box" style="margin:0;">
        <h3>Count matches</h3>
        <form method="POST" action="/count_string" id="countForm">
          <input type="hidden" name="category" class="tools-category-input">
          <input type="text" name="count_string" placeholder="Count regex" required id="count_regex">
          <button type="submit">Count</button>
          <button type="button" id="countNextMatchBtn" disabled>Go to next</button>
        </form>
      </div>

      <div class="tool-box" style="margin:0;">
        <h3>Add trigger word</h3>
        <form method="POST" action="/add_triggerword_all" id="triggerForm">
          <input type="hidden" name="category" class="tools-category-input">
          <input type="text" name="trigger_word" placeholder="Trigger word" required id="trigger_word">
          <button type="submit">Add</button>
        </form>
      </div>
    </div>

    <div id="toolsResult" class="logbox" style="min-height:80px; max-height:180px;"></div>
  </div>
</div>

<div class="joy-modal-backdrop" id="jsonModalBackdrop">
  <div class="joy-modal json-modal" role="dialog" aria-modal="true" aria-labelledby="jsonModalTitle">
    <div class="joy-modal-head">
      <h3 id="jsonModalTitle">JSON captions</h3>
      <button type="button" class="joy-close-btn" id="closeJsonModalBtn">x</button>
    </div>
    <div class="json-workspace">
      <div class="json-side">
        <div class="json-toolbar">
          <button type="button" id="jsonPrevBtn">Prev</button>
          <button type="button" id="jsonNextBtn">Next</button>
          <select id="jsonImageSelect" aria-label="Image"></select>
        </div>
        <div class="json-image-frame" id="jsonImageFrame">
          <img id="jsonPreviewImage" alt="">
          <div class="json-bbox-layer" id="jsonBboxLayer"></div>
        </div>
        <div class="json-element-list" id="jsonElementList"></div>
      </div>
      <div class="json-editor-side">
        <textarea class="json-editor" id="jsonEditor" spellcheck="false"></textarea>
        <div class="json-status" id="jsonStatus">Open a folder with Ideogram 4 JSON captions.</div>
        <div class="joy-actions">
          <button type="button" id="jsonValidateBtn">Validate</button>
          <button type="button" id="jsonValidateAllBtn">Validate all</button>
          <button type="button" id="jsonSwapBboxBtn">Swap bbox order</button>
          <button type="button" id="jsonSaveBtn">Save</button>
        </div>
        <div class="logbox" id="jsonValidationLog" style="min-height:90px; max-height:170px;"></div>
      </div>
    </div>
  </div>
</div>

<div class="joy-modal-backdrop" id="helpModalBackdrop">
  <div class="joy-modal help-modal" role="dialog" aria-modal="true" aria-labelledby="helpModalTitle">
    <div class="joy-modal-head">
      <h3 id="helpModalTitle">Quick guide</h3>
      <button type="button" class="joy-close-btn" id="closeHelpModalBtn" aria-label="Close quick guide">x</button>
    </div>
    <div class="help-content">
      <h4>Getting started</h4>
      <p>Select <b>File &gt; Open Folder</b> to open an image dataset. Missing caption files are created beside their images.</p>

      <h4>Folders and categories</h4>
      <p>Double-click a category icon to open its image window. Drag icons to arrange the Folders view. Right-click the Folders area or an icon to create, edit, or delete folders and category groups. Assign each folder to a category group in Edit Folder.</p>
      <p>Dropping or adding images places them in the active category. Open category windows remain available through the tabs.</p>

      <h4>Selecting and moving cards</h4>
      <p>Click a card title to select it. Hold <kbd>Ctrl</kbd> to select several cards, or press <kbd>Ctrl+A</kbd> to select all cards in the active window.</p>
      <ul>
        <li><kbd>Ctrl+X</kbd> cuts selected cards and <kbd>Ctrl+V</kbd> moves them to the active category.</li>
        <li><kbd>Ctrl+C</kbd> copies selected cards and <kbd>Ctrl+V</kbd> pastes copies.</li>
        <li><kbd>Delete</kbd> removes the selected cards after confirmation.</li>
      </ul>

      <h4>Editing images</h4>
      <p>Use the card controls to crop, rotate, flip, zoom, clone, or delete an image. The red Save button indicates unsaved changes. Category-window controls affect only that category.</p>

      <h4>Masking</h4>
      <p>Open <b>Edit &gt; Masking</b> to configure automatic masks or enable masking mode. With Brush selected, the left mouse button paints the mask and the right mouse button erases it. Fill supports the same left and right button behavior.</p>

      <h4>Captions and text</h4>
      <p><b>Tools &gt; Auto-caption</b> generates captions, <b>Edit &gt; Text tools</b> applies batch text changes to the cards, and <b>Tools &gt; JSON captions</b> opens the Ideogram JSON editor. Text tools changes are written only when you use a card Save button or Save all; Undo and Reset discard them.</p>
      <p><b>Tools &gt; Remove watermark</b> opens a temporary inpainting editor. Paint the watermark, apply the preview on its card, and use Save to write the result.</p>

      <h4>Application mode</h4>
      <p>Use the <b>Mode</b> menu to switch between the Default and Workspace image workflows while keeping the current folder open.</p>
    </div>
  </div>
</div>

<div class="joy-modal-backdrop" id="appDialogBackdrop">
  <div class="joy-modal app-dialog" role="dialog" aria-modal="true" aria-labelledby="appDialogTitle">
    <div class="joy-modal-head">
      <h3 id="appDialogTitle">Message</h3>
      <button type="button" class="joy-close-btn" id="appDialogCloseBtn">x</button>
    </div>
    <div class="app-dialog-message" id="appDialogMessage"></div>
    <input type="text" id="appDialogInput" class="app-dialog-input" style="display:none;">
    <div class="joy-actions app-dialog-actions">
      <button type="button" id="appDialogCancelBtn">Cancel</button>
      <button type="button" id="appDialogOkBtn">OK</button>
    </div>
  </div>
</div>

<script id="bucket-data" type="application/json">{{ bucket_options_json|safe }}</script>
<script id="joy-model-data" type="application/json">{{ joy_model_data_json|safe }}</script>
<script id="category-defs-data" type="application/json">{{ category_defs_json|safe }}</script>
<script id="category-groups-data" type="application/json">{{ category_groups|tojson }}</script>
<script>
const BUCKET_OPTIONS = JSON.parse(document.getElementById('bucket-data').textContent);
const topMenus = Array.from(document.querySelectorAll('.top-menu'));
topMenus.forEach(menu => {
  menu.addEventListener('toggle', () => {
    if (!menu.open) return;
    topMenus.forEach(other => {
      if (other !== menu) other.open = false;
    });
  });
  menu.addEventListener('click', event => {
    if (event.target.closest('button')) menu.open = false;
  });
});
document.addEventListener('pointerdown', event => {
  if (event.target.closest('.top-menu')) return;
  topMenus.forEach(menu => { menu.open = false; });
});
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') topMenus.forEach(menu => { menu.open = false; });
});
const helpModalBackdrop = document.getElementById('helpModalBackdrop');
const openHelpModalBtn = document.getElementById('openHelpModalBtn');
const closeHelpModalBtn = document.getElementById('closeHelpModalBtn');
function openHelpModal() {
  helpModalBackdrop?.classList.add('open');
}
function closeHelpModal() {
  helpModalBackdrop?.classList.remove('open');
}
openHelpModalBtn?.addEventListener('click', openHelpModal);
closeHelpModalBtn?.addEventListener('click', closeHelpModal);
document.addEventListener('keydown', event => {
  if (event.key === 'Escape') closeHelpModal();
});
const JOY_MODEL_OPTIONS = JSON.parse(document.getElementById('joy-model-data').textContent);
const CATEGORY_DEFS = JSON.parse(document.getElementById('category-defs-data').textContent);
const CATEGORY_GROUPS = JSON.parse(document.getElementById('category-groups-data').textContent);
const CATEGORY_ICON_BY_NAME = Object.fromEntries(CATEGORY_DEFS.map(item => [item.name, item.icon]));
const CATEGORY_VISIBILITY_KEY = 'caption_app_categories_visible';
const HAS_OPEN_FOLDER = {{ 'true' if folder_name else 'false' }};
const FOLDER_KEY = {{ folder_name|tojson }};
const IMAGE_FILE_PATTERN = /\.(png|jpe?g|gif|bmp|webp|avif)$/i;
const DESKTOP_ICON_GRID_X = 112;
const DESKTOP_ICON_GRID_Y = 120;
const CATEGORY_WINDOW_MIN_WIDTH = 420;
const CATEGORY_WINDOW_MIN_HEIGHT = 300;
const dropPasteOverlay = document.getElementById('dropPasteOverlay');
let currentCropBase = {{ selected_crop_base|int }};
const cropStates = new Map();
let maskModeActive = false;
let maskModePurpose = 'training';
let currentMaskTool = 'brush';
let currentMaskSize = 32;
let currentMaskFeather = 0;
let currentMaskFillTolerance = 32;
const maskBrushStampCache = new Map();
let activeBrushCursorCanvas = null;
let lastBrushCursorPoint = null;
const maskCanvasLoaded = new Set();
const maskStates = new Map();
const watermarkMaskStates = new Map();
const watermarkResults = new Map();
let joySavedConfig = {};
const categoryDesktop = document.getElementById('categoryDesktop');
const desktopIconLayer = document.getElementById('desktopIconLayer');
const windowLayer = document.getElementById('windowLayer');
const pairPool = document.getElementById('pairPool');
const addFilesForm = document.getElementById('addFilesForm');
const addFilesCategoryInput = document.getElementById('addFilesCategoryInput');
const categoryDialogBackdrop = document.getElementById('categoryDialogBackdrop');
const categoryDialog = document.getElementById('categoryDialog');
const categoryOldName = document.getElementById('categoryOldName');
const categoryNameInput = document.getElementById('categoryNameInput');
const categoryColorInput = document.getElementById('categoryColorInput');
const categoryGroupInput = document.getElementById('categoryGroupInput');
const categoryIconInput = document.getElementById('categoryIconInput');
const categoryIconFileInput = document.getElementById('categoryIconFileInput');
const categoryGroupDialogBackdrop = document.getElementById('categoryGroupDialogBackdrop');
const categoryGroupDialog = document.getElementById('categoryGroupDialog');
const categoryGroupManageSelect = document.getElementById('categoryGroupManageSelect');
const categoryGroupNameInput = document.getElementById('categoryGroupNameInput');
const categoryGroupColorInput = document.getElementById('categoryGroupColorInput');
const deleteCategoryGroupBtn = document.getElementById('deleteCategoryGroupBtn');
const desktopContextMenu = document.getElementById('desktopContextMenu');
const folderContextMenu = document.getElementById('folderContextMenu');
const cardContextMenu = document.getElementById('cardContextMenu');
const statusbarMessage = document.getElementById('statusbarMessage');
const statusbarTabs = document.getElementById('statusbarTabs');
const appDialogBackdrop = document.getElementById('appDialogBackdrop');
const appDialogTitle = document.getElementById('appDialogTitle');
const appDialogMessage = document.getElementById('appDialogMessage');
const appDialogInput = document.getElementById('appDialogInput');
const appDialogOkBtn = document.getElementById('appDialogOkBtn');
const appDialogCancelBtn = document.getElementById('appDialogCancelBtn');
const appDialogCloseBtn = document.getElementById('appDialogCloseBtn');
const selectedImgNames = new Set();
let pairClipboard = null;
let activeCategory = localStorage.getItem(`caption_app_active_category_${FOLDER_KEY}`) || 'Undefined';
let categoryWindowZ = 20;
let categoryTabOrderCounter = 0;
let pendingNewFolderPosition = null;
let contextFolderName = '';
let contextCard = null;
let lastDesktopFolderClick = { name: '', time: 0 };
let previousCategoryIconValue = 'undefined.png';
const selectedDesktopFolders = new Set();
let textHeight = parseInt(localStorage.getItem('caption_app_text_height') || '110', 10);
let imageHeight = parseInt(localStorage.getItem('caption_app_image_height') || '420', 10);
const categoryTextHeights = new Map();
const categoryImageHeights = new Map();
const categoryWindowResizeObserver = 'ResizeObserver' in window
  ? new ResizeObserver(entries => {
      entries.forEach(entry => scheduleClampCategoryWindow(entry.target, true));
    })
  : null;

function showAppDialog({ title = 'Message', message = '', mode = 'alert', defaultValue = '' } = {}) {
  return new Promise(resolve => {
    if (!appDialogBackdrop || !appDialogOkBtn || !appDialogCancelBtn || !appDialogCloseBtn) {
      if (mode === 'confirm') resolve(window.confirm(message));
      else if (mode === 'prompt') resolve(window.prompt(message, defaultValue));
      else { window.alert(message); resolve(true); }
      return;
    }
    const isPrompt = mode === 'prompt';
    const isConfirm = mode === 'confirm';
    appDialogTitle.textContent = title;
    appDialogMessage.textContent = message;
    appDialogInput.style.display = isPrompt ? '' : 'none';
    appDialogInput.value = defaultValue ?? '';
    appDialogCancelBtn.style.display = (isConfirm || isPrompt) ? '' : 'none';
    appDialogBackdrop.classList.add('open');
    const finish = value => {
      appDialogBackdrop.classList.remove('open');
      appDialogOkBtn.removeEventListener('click', ok);
      appDialogCancelBtn.removeEventListener('click', cancel);
      appDialogCloseBtn.removeEventListener('click', cancel);
      document.removeEventListener('keydown', keydown);
      resolve(value);
    };
    const ok = () => finish(isPrompt ? appDialogInput.value : true);
    const cancel = () => finish(isConfirm ? false : (isPrompt ? null : true));
    const keydown = event => {
      if (event.key === 'Escape') {
        event.preventDefault();
        cancel();
      } else if (event.key === 'Enter' && !event.isComposing) {
        event.preventDefault();
        ok();
      }
    };
    appDialogOkBtn.addEventListener('click', ok);
    appDialogCancelBtn.addEventListener('click', cancel);
    appDialogCloseBtn.addEventListener('click', cancel);
    document.addEventListener('keydown', keydown);
    requestAnimationFrame(() => (isPrompt ? appDialogInput : appDialogOkBtn).focus());
  });
}

function appAlert(message, title = 'Message') {
  return showAppDialog({ title, message, mode: 'alert' });
}

function appConfirm(message, title = 'Confirm') {
  return showAppDialog({ title, message, mode: 'confirm' });
}

function appPrompt(message, defaultValue = '', title = 'Input') {
  return showAppDialog({ title, message, mode: 'prompt', defaultValue });
}

function categoryExists(name) {
  return CATEGORY_DEFS.some(item => item.name === name);
}

function categoryData(name) {
  return CATEGORY_DEFS.find(item => item.name === name) || CATEGORY_DEFS.find(item => item.name === 'Undefined') || CATEGORY_DEFS[0];
}

function setActiveCategory(name) {
  activeCategory = categoryExists(name) ? name : 'Undefined';
  localStorage.setItem(`caption_app_active_category_${FOLDER_KEY}`, activeCategory);
  if (addFilesCategoryInput) addFilesCategoryInput.value = activeCategory;
  document.querySelectorAll('.desktop-folder').forEach(icon => {
    icon.classList.toggle('active', icon.dataset.category === activeCategory);
  });
  document.querySelectorAll('.category-window').forEach(win => {
    win.classList.toggle('active', win.dataset.category === activeCategory);
  });
  renderStatusbarTabs();
}

window.addEventListener('resize', () => {
  document.querySelectorAll('.category-window').forEach(win => scheduleClampCategoryWindow(win, true));
});

function setDesktopFolderSelected(icon, selected) {
  if (!icon?.dataset?.category) return;
  icon.classList.toggle('selected', selected);
  if (selected) {
    selectedDesktopFolders.add(icon.dataset.category);
  } else {
    selectedDesktopFolders.delete(icon.dataset.category);
  }
}

function clearDesktopFolderSelection() {
  selectedDesktopFolders.clear();
  document.querySelectorAll('.desktop-folder.selected').forEach(icon => icon.classList.remove('selected'));
}

function selectOnlyDesktopFolder(icon) {
  clearDesktopFolderSelection();
  setDesktopFolderSelected(icon, true);
}

function selectedDesktopFolderIcons() {
  return Array.from(document.querySelectorAll('.desktop-folder'))
    .filter(icon => selectedDesktopFolders.has(icon.dataset.category));
}

function categoryWindowStorageKey(name) {
  return `caption_app_category_window_${FOLDER_KEY}_${name}`;
}

function openCategoryWindowsStorageKey() {
  return `caption_app_open_category_windows_${FOLDER_KEY}`;
}

function activeCategoryWindowStorageKey() {
  return `caption_app_front_category_window_${FOLDER_KEY}`;
}

function saveOpenCategoryWindows() {
  const items = Array.from(document.querySelectorAll('.category-window'))
    .filter(win => win.dataset.open === '1')
    .sort((a, b) => (parseInt(a.dataset.tabOrder || '0', 10) || 0) - (parseInt(b.dataset.tabOrder || '0', 10) || 0))
    .map(win => ({
      name: win.dataset.category,
      minimized: win.dataset.minimized === '1',
      order: parseInt(win.dataset.tabOrder || '0', 10) || 0,
    }))
    .filter(item => item.name);
  localStorage.setItem(openCategoryWindowsStorageKey(), JSON.stringify(items));
  localStorage.setItem(activeCategoryWindowStorageKey(), activeCategory);
  renderStatusbarTabs();
}

function loadOpenCategoryWindows() {
  try {
    const items = JSON.parse(localStorage.getItem(openCategoryWindowsStorageKey()) || '[]');
    if (!Array.isArray(items)) return [];
    return items
      .map((item, index) => typeof item === 'string'
        ? { name: item, minimized: false, order: index + 1 }
        : { name: item.name, minimized: !!item.minimized, order: parseInt(item.order || index + 1, 10) || index + 1 })
      .filter(item => item && categoryExists(item.name));
  } catch (e) {
    return [];
  }
}

function loadCategoryWindowRect(name, index) {
  try {
    const saved = JSON.parse(localStorage.getItem(categoryWindowStorageKey(name)) || '{}');
    if (Number.isFinite(saved.left) && Number.isFinite(saved.top)) return saved;
  } catch (e) {}
  return {
    left: 170 + (index % 5) * 24,
    top: 70 + (index % 5) * 22,
    width: Math.min(860, Math.max(420, (categoryDesktop?.clientWidth || 900) - 220)),
    height: Math.min(680, Math.max(320, (categoryDesktop?.clientHeight || 700) - 120)),
  };
}

function saveCategoryWindowRect(win) {
  if (!win || win.hidden || win.classList.contains('maximized')) return;
  localStorage.setItem(categoryWindowStorageKey(win.dataset.category), JSON.stringify({
    left: Math.max(0, Math.round(parseFloat(win.style.left) || win.offsetLeft || 0)),
    top: Math.max(0, Math.round(parseFloat(win.style.top) || win.offsetTop || 0)),
    width: Math.round(win.offsetWidth),
    height: Math.round(win.offsetHeight),
  }));
}

function categoryWindowBounds() {
  const rect = (windowLayer || categoryDesktop)?.getBoundingClientRect?.();
  return {
    width: Math.max(0, rect?.width || categoryDesktop?.clientWidth || window.innerWidth || 0),
    height: Math.max(0, rect?.height || categoryDesktop?.clientHeight || window.innerHeight || 0),
  };
}

function clampCategoryWindowToDesktop(win, save = false) {
  if (!win || win.hidden || win.classList.contains('maximized')) return;
  const bounds = categoryWindowBounds();
  if (!bounds.width || !bounds.height) return;
  const minWidth = Math.min(CATEGORY_WINDOW_MIN_WIDTH, bounds.width);
  const minHeight = Math.min(CATEGORY_WINDOW_MIN_HEIGHT, bounds.height);
  win.style.minWidth = `${minWidth}px`;
  win.style.minHeight = `${minHeight}px`;
  const width = Math.min(Math.max(minWidth, win.offsetWidth), bounds.width);
  const height = Math.min(Math.max(minHeight, win.offsetHeight), bounds.height);
  if (Math.abs(width - win.offsetWidth) > 1) win.style.width = `${width}px`;
  if (Math.abs(height - win.offsetHeight) > 1) win.style.height = `${height}px`;
  const maxLeft = Math.max(0, bounds.width - width);
  const maxTop = Math.max(0, bounds.height - height);
  const left = Math.max(0, Math.min(maxLeft, parseFloat(win.style.left) || win.offsetLeft || 0));
  const top = Math.max(0, Math.min(maxTop, parseFloat(win.style.top) || win.offsetTop || 0));
  win.style.left = `${left}px`;
  win.style.top = `${top}px`;
  if (save) saveCategoryWindowRect(win);
}

function scheduleClampCategoryWindow(win, save = false) {
  if (!win || win.dataset.clampScheduled === '1') {
    if (save && win) win.dataset.clampSave = '1';
    return;
  }
  win.dataset.clampScheduled = '1';
  if (save) win.dataset.clampSave = '1';
  requestAnimationFrame(() => {
    const shouldSave = win.dataset.clampSave === '1';
    delete win.dataset.clampScheduled;
    delete win.dataset.clampSave;
    clampCategoryWindowToDesktop(win, shouldSave);
  });
}

function cardMinWidthForImageHeight(value) {
  return Math.max(220, value + 24);
}

function getCategoryTextHeight(category) {
  return categoryTextHeights.get(category) || textHeight;
}

function getCategoryImageHeight(category) {
  return categoryImageHeights.get(category) || imageHeight;
}

function updateCategorySizingControls(category) {
  const win = document.querySelector(`.category-window[data-category="${CSS.escape(category)}"]`);
  if (!win) return;
  const textValue = getCategoryTextHeight(category);
  const imageValue = getCategoryImageHeight(category);
  const textSlider = win.querySelector('.category-text-height-slider');
  const textLabel = win.querySelector('.category-text-height-value');
  const imageSlider = win.querySelector('.category-image-height-slider');
  const imageLabel = win.querySelector('.category-image-height-value');
  if (textSlider) textSlider.value = String(textValue);
  if (textLabel) textLabel.textContent = String(textValue);
  if (imageSlider) imageSlider.value = String(imageValue);
  if (imageLabel) imageLabel.textContent = String(imageValue);
}

function applyTextHeightToCards(cards, value) {
  (cards || []).forEach(card => {
    const ta = card.querySelector('.caption-textarea');
    if (ta) ta.style.height = `${value}px`;
  });
}

function applyImageHeightToCards(cards, value) {
  (cards || []).forEach(card => {
    const stage = card.querySelector('.crop-stage');
    if (stage) {
      stage.style.width = `${value}px`;
      stage.style.maxWidth = '100%';
    }
    const index = parseInt(card.dataset.index, 10);
    if (Number.isFinite(index)) {
      renderImageTransform(index);
      renderCrop(index);
      positionMaskCanvas(index);
    }
  });
}

function applyCategorySizing(category) {
  const cards = getCardsInCategory(category);
  const imageValue = getCategoryImageHeight(category);
  const win = document.querySelector(`.category-window[data-category="${CSS.escape(category)}"]`);
  const grid = win?.querySelector('.grid');
  applyTextHeightToCards(cards, getCategoryTextHeight(category));
  applyImageHeightToCards(cards, imageValue);
  if (grid) grid.style.setProperty('--card-min-width', `${cardMinWidthForImageHeight(imageValue)}px`);
  updateCategorySizingControls(category);
}

function applyCategorySizingToCard(card) {
  if (!card) return;
  const category = categoryExists(card.dataset.category) ? card.dataset.category : 'Undefined';
  applyTextHeightToCards([card], getCategoryTextHeight(category));
  applyImageHeightToCards([card], getCategoryImageHeight(category));
}

function setCategoryTextHeight(category, value) {
  if (!categoryExists(category)) return;
  categoryTextHeights.set(category, value);
  applyTextHeightToCards(getCardsInCategory(category), value);
  updateCategorySizingControls(category);
}

function setCategoryImageHeight(category, value) {
  if (!categoryExists(category)) return;
  categoryImageHeights.set(category, value);
  const win = document.querySelector(`.category-window[data-category="${CSS.escape(category)}"]`);
  const grid = win?.querySelector('.grid');
  if (grid) grid.style.setProperty('--card-min-width', `${cardMinWidthForImageHeight(value)}px`);
  applyImageHeightToCards(getCardsInCategory(category), value);
  updateCategorySizingControls(category);
}

function createCategoryRangeControl(id, kind, label, min, max, step, value, onInput) {
  const wrap = document.createElement('div');
  wrap.className = 'category-window-range';
  const labelEl = document.createElement('label');
  labelEl.htmlFor = id;
  labelEl.textContent = label;
  const input = document.createElement('input');
  input.type = 'range';
  input.id = id;
  input.min = String(min);
  input.max = String(max);
  input.step = String(step);
  input.value = String(value);
  input.className = kind === 'text' ? 'category-text-height-slider' : 'category-image-height-slider';
  const valueEl = document.createElement('span');
  valueEl.className = `category-window-range-value ${kind === 'text' ? 'category-text-height-value' : 'category-image-height-value'}`;
  valueEl.textContent = String(value);
  input.addEventListener('input', (event) => {
    const nextValue = parseInt(event.target.value, 10);
    valueEl.textContent = String(nextValue);
    onInput(nextValue);
  });
  wrap.append(labelEl, input, valueEl);
  return wrap;
}

function renderStatusbarTabs() {
  if (!statusbarTabs) return;
  statusbarTabs.innerHTML = '';
  const wins = Array.from(document.querySelectorAll('.category-window'))
    .filter(win => win.dataset.open === '1')
    .sort((a, b) => (parseInt(a.dataset.tabOrder || '0', 10) || 0) - (parseInt(b.dataset.tabOrder || '0', 10) || 0));
  const desktopTab = document.createElement('div');
  desktopTab.role = 'button';
  desktopTab.tabIndex = 0;
  desktopTab.className = 'category-taskbar-tab desktop-tab';
  desktopTab.title = 'Folders';
  desktopTab.classList.toggle('active', !wins.some(win => win.dataset.minimized !== '1' && !win.hidden));
  desktopTab.addEventListener('click', minimizeAllCategoryWindows);
  desktopTab.addEventListener('keydown', (event) => {
    if (event.key === 'Enter' || event.key === ' ') {
      event.preventDefault();
      minimizeAllCategoryWindows();
    }
  });
  const desktopLabel = document.createElement('span');
  desktopLabel.className = 'category-taskbar-tab-label';
  desktopLabel.textContent = 'Folders';
  desktopTab.append(desktopLabel);
  statusbarTabs.append(desktopTab);
  wins.forEach(win => {
    const category = categoryData(win.dataset.category);
    const tab = document.createElement('div');
    tab.role = 'button';
    tab.tabIndex = 0;
    tab.className = 'category-taskbar-tab';
    tab.title = win.dataset.category;
    tab.style.setProperty('--window-color', category?.color || '#64748b');
    tab.classList.toggle('active', win.dataset.category === activeCategory && win.dataset.minimized !== '1' && !win.hidden);
    tab.classList.toggle('minimized', win.dataset.minimized === '1');
    tab.addEventListener('click', () => openCategoryWindow(win.dataset.category));
    tab.addEventListener('keydown', (event) => {
      if (event.key === 'Enter' || event.key === ' ') {
        event.preventDefault();
        openCategoryWindow(win.dataset.category);
      }
    });
    const icon = document.createElement('img');
    icon.className = 'category-taskbar-tab-icon';
    icon.src = `/category_icon/${category?.icon || 'undefined.png'}`;
    icon.alt = '';
    const label = document.createElement('span');
    label.className = 'category-taskbar-tab-label';
    label.textContent = win.dataset.category;
    const close = document.createElement('button');
    close.type = 'button';
    close.className = 'category-taskbar-tab-close';
    close.title = 'Close';
    close.textContent = '×';
    close.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      closeCategoryWindow(win);
    });
    tab.append(icon, label, close);
    statusbarTabs.append(tab);
  });
}

function closeCategoryWindow(win) {
  if (!win) return;
  saveCategoryWindowRect(win);
  win.dataset.open = '0';
  win.dataset.minimized = '0';
  win.hidden = true;
  saveOpenCategoryWindows();
}

function minimizeCategoryWindow(win) {
  if (!win) return;
  saveCategoryWindowRect(win);
  win.dataset.open = '1';
  win.dataset.minimized = '1';
  win.hidden = true;
  saveOpenCategoryWindows();
}

function minimizeAllCategoryWindows() {
  document.querySelectorAll('.category-window[data-open="1"]').forEach(win => {
    saveCategoryWindowRect(win);
    win.dataset.minimized = '1';
    win.hidden = true;
  });
  saveOpenCategoryWindows();
}

function bringCategoryWindowToFront(win) {
  if (!win) return;
  win.style.zIndex = String(++categoryWindowZ);
  setActiveCategory(win.dataset.category);
  localStorage.setItem(activeCategoryWindowStorageKey(), win.dataset.category);
  renderStatusbarTabs();
}

function updateWindowStatus(win) {
  const count = win?.querySelectorAll('.pair-card').length || 0;
  const selectedCount = win?.querySelectorAll('.pair-card.selected').length || 0;
  const status = win?.querySelector('.category-window-status');
  if (status) {
    const totalText = `${count} image${count === 1 ? '' : 's'}`;
    const selectedText = selectedCount ? ` | selected: ${selectedCount}` : '';
    status.textContent = `${totalText}${selectedText}`;
  }
}

function updateAllWindowStatuses() {
  document.querySelectorAll('.category-window').forEach(updateWindowStatus);
}

function desktopIconLayerRect() {
  return desktopIconLayer?.getBoundingClientRect() || categoryDesktop?.getBoundingClientRect();
}

function snapDesktopCoord(value, step) {
  return Math.round(value / step) * step;
}

function clampDesktopIconPosition(x, y, icon = null) {
  const layerRect = desktopIconLayerRect();
  const maxX = Math.max(0, (layerRect?.width || 0) - (icon?.offsetWidth || 112));
  const maxY = Math.max(0, (layerRect?.height || 0) - (icon?.offsetHeight || DESKTOP_ICON_GRID_Y));
  return {
    x: Math.max(0, Math.min(maxX, x)),
    y: Math.max(0, Math.min(maxY, y)),
  };
}

function snapDesktopIconPosition(x, y, icon = null) {
  const clamped = clampDesktopIconPosition(x, y, icon);
  return clampDesktopIconPosition(
    snapDesktopCoord(clamped.x, DESKTOP_ICON_GRID_X),
    snapDesktopCoord(clamped.y, DESKTOP_ICON_GRID_Y),
    icon,
  );
}

function desktopGridKey(pos) {
  return `${Math.round(pos.x / DESKTOP_ICON_GRID_X)},${Math.round(pos.y / DESKTOP_ICON_GRID_Y)}`;
}

function occupiedDesktopGridKeys(excludeIcon = null) {
  const keys = new Set();
  document.querySelectorAll('.desktop-folder').forEach(icon => {
    if (icon === excludeIcon) return;
    const pos = snapDesktopIconPosition(parseFloat(icon.style.left) || 0, parseFloat(icon.style.top) || 0, icon);
    keys.add(desktopGridKey(pos));
  });
  return keys;
}

function nearestFreeDesktopIconPosition(x, y, icon = null) {
  const snapped = snapDesktopIconPosition(x, y, icon);
  const occupied = occupiedDesktopGridKeys(icon);
  if (!occupied.has(desktopGridKey(snapped))) return snapped;

  const layerRect = desktopIconLayerRect();
  const maxCol = Math.max(0, Math.floor(Math.max(0, (layerRect?.width || 0) - (icon?.offsetWidth || 112)) / DESKTOP_ICON_GRID_X));
  const maxRow = Math.max(0, Math.floor(Math.max(0, (layerRect?.height || 0) - (icon?.offsetHeight || DESKTOP_ICON_GRID_Y)) / DESKTOP_ICON_GRID_Y));
  const startCol = Math.round(snapped.x / DESKTOP_ICON_GRID_X);
  const startRow = Math.round(snapped.y / DESKTOP_ICON_GRID_Y);

  let best = null;
  let bestScore = Infinity;
  const maxRadius = Math.max(maxCol, maxRow) + 1;
  for (let radius = 1; radius <= maxRadius; radius += 1) {
    for (let row = Math.max(0, startRow - radius); row <= Math.min(maxRow, startRow + radius); row += 1) {
      for (let col = Math.max(0, startCol - radius); col <= Math.min(maxCol, startCol + radius); col += 1) {
        if (Math.max(Math.abs(col - startCol), Math.abs(row - startRow)) !== radius) continue;
        const pos = { x: col * DESKTOP_ICON_GRID_X, y: row * DESKTOP_ICON_GRID_Y };
        if (occupied.has(desktopGridKey(pos))) continue;
        const score = ((pos.x - snapped.x) ** 2) + ((pos.y - snapped.y) ** 2);
        if (score < bestScore) {
          best = pos;
          bestScore = score;
        }
      }
    }
    if (best) return best;
  }
  return snapped;
}

function occupiedDesktopGridKeysForGroup(groupIcons) {
  const group = new Set(groupIcons || []);
  const keys = new Set();
  document.querySelectorAll('.desktop-folder').forEach(icon => {
    if (group.has(icon)) return;
    const pos = snapDesktopIconPosition(parseFloat(icon.style.left) || 0, parseFloat(icon.style.top) || 0, icon);
    keys.add(desktopGridKey(pos));
  });
  return keys;
}

function clampDesktopGroupDelta(entries, dx, dy) {
  const layerRect = desktopIconLayerRect();
  const width = layerRect?.width || 0;
  const height = layerRect?.height || 0;
  let minDx = -Infinity;
  let maxDx = Infinity;
  let minDy = -Infinity;
  let maxDy = Infinity;
  entries.forEach(entry => {
    const iconWidth = entry.icon.offsetWidth || DESKTOP_ICON_GRID_X;
    const iconHeight = entry.icon.offsetHeight || DESKTOP_ICON_GRID_Y;
    minDx = Math.max(minDx, -entry.left);
    maxDx = Math.min(maxDx, Math.max(0, width - iconWidth) - entry.left);
    minDy = Math.max(minDy, -entry.top);
    maxDy = Math.min(maxDy, Math.max(0, height - iconHeight) - entry.top);
  });
  return {
    dx: Math.max(minDx, Math.min(maxDx, dx)),
    dy: Math.max(minDy, Math.min(maxDy, dy)),
  };
}

function findFreeDesktopGroupPositions(entries, anchorIcon) {
  const anchorEntry = entries.find(entry => entry.icon === anchorIcon) || entries[0];
  const anchorX = parseFloat(anchorIcon.style.left) || anchorEntry.left;
  const anchorY = parseFloat(anchorIcon.style.top) || anchorEntry.top;
  const snapped = snapDesktopIconPosition(anchorX, anchorY, anchorIcon);
  const occupied = occupiedDesktopGridKeysForGroup(entries.map(entry => entry.icon));
  const layerRect = desktopIconLayerRect();
  const maxCol = Math.max(0, Math.floor(Math.max(0, (layerRect?.width || 0) - (anchorIcon?.offsetWidth || DESKTOP_ICON_GRID_X)) / DESKTOP_ICON_GRID_X));
  const maxRow = Math.max(0, Math.floor(Math.max(0, (layerRect?.height || 0) - (anchorIcon?.offsetHeight || DESKTOP_ICON_GRID_Y)) / DESKTOP_ICON_GRID_Y));
  const startCol = Math.round(snapped.x / DESKTOP_ICON_GRID_X);
  const startRow = Math.round(snapped.y / DESKTOP_ICON_GRID_Y);
  const offsets = entries.map(entry => ({
    entry,
    dx: Math.round((entry.left - anchorEntry.left) / DESKTOP_ICON_GRID_X) * DESKTOP_ICON_GRID_X,
    dy: Math.round((entry.top - anchorEntry.top) / DESKTOP_ICON_GRID_Y) * DESKTOP_ICON_GRID_Y,
  }));

  function positionsFor(anchorPos) {
    const keys = new Set();
    const out = [];
    for (const item of offsets) {
      const raw = { x: anchorPos.x + item.dx, y: anchorPos.y + item.dy };
      const pos = snapDesktopIconPosition(raw.x, raw.y, item.entry.icon);
      if (Math.abs(pos.x - raw.x) > 1 || Math.abs(pos.y - raw.y) > 1) return null;
      const key = desktopGridKey(pos);
      if (keys.has(key) || occupied.has(key)) return null;
      keys.add(key);
      out.push({ icon: item.entry.icon, pos });
    }
    return out;
  }

  const direct = positionsFor(snapped);
  if (direct) return direct;

  let best = null;
  let bestScore = Infinity;
  const maxRadius = Math.max(maxCol, maxRow) + 1;
  for (let radius = 1; radius <= maxRadius; radius += 1) {
    for (let row = Math.max(0, startRow - radius); row <= Math.min(maxRow, startRow + radius); row += 1) {
      for (let col = Math.max(0, startCol - radius); col <= Math.min(maxCol, startCol + radius); col += 1) {
        if (Math.max(Math.abs(col - startCol), Math.abs(row - startRow)) !== radius) continue;
        const anchorPos = { x: col * DESKTOP_ICON_GRID_X, y: row * DESKTOP_ICON_GRID_Y };
        const positions = positionsFor(anchorPos);
        if (!positions) continue;
        const score = ((anchorPos.x - snapped.x) ** 2) + ((anchorPos.y - snapped.y) ** 2);
        if (score < bestScore) {
          best = positions;
          bestScore = score;
        }
      }
    }
    if (best) return best;
  }
  return entries.map(entry => ({
    icon: entry.icon,
    pos: nearestFreeDesktopIconPosition(parseFloat(entry.icon.style.left) || entry.left, parseFloat(entry.icon.style.top) || entry.top, entry.icon),
  }));
}

function nearestFreeDesktopIconPositionFromOccupied(x, y, icon, occupied) {
  const snapped = snapDesktopIconPosition(x, y, icon);
  if (!occupied.has(desktopGridKey(snapped))) return snapped;
  const layerRect = desktopIconLayerRect();
  const maxCol = Math.max(0, Math.floor(Math.max(0, (layerRect?.width || 0) - (icon?.offsetWidth || DESKTOP_ICON_GRID_X)) / DESKTOP_ICON_GRID_X));
  const maxRow = Math.max(0, Math.floor(Math.max(0, (layerRect?.height || 0) - (icon?.offsetHeight || DESKTOP_ICON_GRID_Y)) / DESKTOP_ICON_GRID_Y));
  const startCol = Math.round(snapped.x / DESKTOP_ICON_GRID_X);
  const startRow = Math.round(snapped.y / DESKTOP_ICON_GRID_Y);
  let best = null;
  let bestScore = Infinity;
  const maxRadius = Math.max(maxCol, maxRow) + 1;
  for (let radius = 1; radius <= maxRadius; radius += 1) {
    for (let row = Math.max(0, startRow - radius); row <= Math.min(maxRow, startRow + radius); row += 1) {
      for (let col = Math.max(0, startCol - radius); col <= Math.min(maxCol, startCol + radius); col += 1) {
        if (Math.max(Math.abs(col - startCol), Math.abs(row - startRow)) !== radius) continue;
        const pos = { x: col * DESKTOP_ICON_GRID_X, y: row * DESKTOP_ICON_GRID_Y };
        if (occupied.has(desktopGridKey(pos))) continue;
        const score = ((pos.x - snapped.x) ** 2) + ((pos.y - snapped.y) ** 2);
        if (score < bestScore) {
          best = pos;
          bestScore = score;
        }
      }
    }
    if (best) return best;
  }
  return snapped;
}

function normalizeDesktopFolderGridPositions() {
  const occupied = new Set();
  document.querySelectorAll('.desktop-folder').forEach(icon => {
    const oldX = parseFloat(icon.style.left) || 0;
    const oldY = parseFloat(icon.style.top) || 0;
    const pos = nearestFreeDesktopIconPositionFromOccupied(oldX, oldY, icon, occupied);
    occupied.add(desktopGridKey(pos));
    if (Math.abs(pos.x - oldX) > 1 || Math.abs(pos.y - oldY) > 1) {
      icon.style.left = `${pos.x}px`;
      icon.style.top = `${pos.y}px`;
      saveDesktopFolderPosition(icon);
    }
  });
}

function hideDesktopMenus() {
  desktopContextMenu?.classList.remove('open');
  folderContextMenu?.classList.remove('open');
  cardContextMenu?.classList.remove('open');
  contextCard = null;
}

function placeContextMenu(menu, clientX, clientY) {
  if (!menu) return;
  menu.style.left = `${clientX}px`;
  menu.style.top = `${clientY}px`;
  menu.classList.add('open');
  const rect = menu.getBoundingClientRect();
  const left = Math.max(4, Math.min(clientX, window.innerWidth - rect.width - 4));
  const top = Math.max(4, Math.min(clientY, window.innerHeight - rect.height - 4));
  menu.style.left = `${left}px`;
  menu.style.top = `${top}px`;
}

function openDesktopContextMenu(event) {
  if (!categoryDesktop || event.target.closest('.desktop-folder') || event.target.closest('.category-window')) return;
  event.preventDefault();
  hideDesktopMenus();
  const layerRect = desktopIconLayerRect();
  pendingNewFolderPosition = nearestFreeDesktopIconPosition(
    event.clientX - layerRect.left,
    event.clientY - layerRect.top,
  );
  placeContextMenu(desktopContextMenu, event.clientX, event.clientY);
}

function openFolderContextMenu(event, name) {
  event.preventDefault();
  event.stopPropagation();
  hideDesktopMenus();
  contextFolderName = name;
  const deleteBtn = folderContextMenu?.querySelector('[data-action="delete-folder"]');
  if (deleteBtn) deleteBtn.disabled = name === 'Undefined';
  placeContextMenu(folderContextMenu, event.clientX, event.clientY);
}

function setStatusbarMessage(text) {
  if (statusbarMessage) statusbarMessage.textContent = text || '';
}

function selectedCardCount() {
  return selectedImgNames.size;
}

function setPairClipboardFromSelection(mode) {
  if (!selectedImgNames.size) return false;
  pairClipboard = {
    mode,
    img_names: Array.from(selectedImgNames),
  };
  if (mode === 'move') {
    markCutPendingCards(pairClipboard.img_names);
    setStatusbarMessage(`Cut ${selectedCardCount()} card${selectedCardCount() === 1 ? '' : 's'} to clipboard.`);
  } else {
    clearCutPendingCards();
    setStatusbarMessage(`Copied ${selectedCardCount()} card${selectedCardCount() === 1 ? '' : 's'} to clipboard.`);
  }
  return true;
}

function openCardContextMenu(event, card) {
  if (!card || isTypingElement(event.target) || event.target.closest('button, input, textarea, select')) return;
  event.preventDefault();
  event.stopPropagation();
  hideDesktopMenus();
  contextCard = card;
  const win = card.closest('.category-window');
  if (win) bringCategoryWindowToFront(win);
  if (!card.classList.contains('selected')) selectOnlyPair(card);
  placeContextMenu(cardContextMenu, event.clientX, event.clientY);
}

async function deleteCategoryByName(name) {
  if (name === 'Undefined') {
    await appAlert('Undefined cannot be deleted.');
    return;
  }
  if (!await appConfirm(`Delete category "${name}"? Its images move to Undefined.`)) return;
  const res = await fetch('/delete_category', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ name }),
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    await appAlert(data.error || 'Delete failed.');
    return;
  }
  suppressBeforeUnload = true;
  window.location.reload();
}

function buildCategoryWindow(category, index) {
  if (!windowLayer) return null;
  const win = document.createElement('section');
  win.className = 'category-window';
  win.dataset.category = category.name;
  win.dataset.open = '0';
  win.dataset.minimized = '0';
  win.hidden = true;
  win.style.setProperty('--window-color', category.color || '#64748b');
  const rect = loadCategoryWindowRect(category.name, index);
  win.style.left = `${rect.left}px`;
  win.style.top = `${rect.top}px`;
  if (rect.width) win.style.width = `${rect.width}px`;
  if (rect.height) win.style.height = `${rect.height}px`;

  const head = document.createElement('div');
  head.className = 'category-window-head';
  const title = document.createElement('div');
  title.className = 'category-window-title';
  title.textContent = category.name;
  const actions = document.createElement('div');
  actions.className = 'category-window-actions';
  const minimizeBtn = document.createElement('button');
  minimizeBtn.type = 'button';
  minimizeBtn.title = 'Minimize';
  minimizeBtn.textContent = '_';
  const maxBtn = document.createElement('button');
  maxBtn.type = 'button';
  maxBtn.title = 'Maximize';
  maxBtn.textContent = '□';
  const closeBtn = document.createElement('button');
  closeBtn.type = 'button';
  closeBtn.title = 'Close';
  closeBtn.textContent = 'x';
  actions.append(minimizeBtn, maxBtn, closeBtn);
  head.append(title, actions);

  const tools = document.createElement('div');
  tools.className = 'category-window-tools';
  const toolButtons = [
    ['text', 'Text', 'btn_text_tools.svg', () => openToolsModal(category.name)],
    ['auto-crop', 'Auto crop', 'btn_auto_crop_all.svg', () => autoCropCategory(category.name)],
    ['auto-mask', 'Auto mask', 'btn_masking.svg', () => autoMaskCategory(category.name)],
    ['reset', 'Reset', 'btn_reset_all.svg', () => resetCategoryUnsavedChanges(category.name)],
    ['save', 'Save', 'btn_save_all.svg', () => saveCategoryCards(category.name)],
    ['rename', 'Rename', 'btn_rename_all.svg', () => renameCategoryPairs(category.name)],
  ];
  toolButtons.forEach(([key, label, icon, handler]) => {
    const btn = document.createElement('button');
    btn.type = 'button';
    btn.className = 'category-window-tool-btn';
    btn.dataset.tool = key;
    btn.title = label;
    btn.innerHTML = `<img src="/category_icon/${icon}" alt="">${label}`;
    btn.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      bringCategoryWindowToFront(win);
      handler();
    });
    tools.append(btn);
  });
  tools.append(
    createCategoryRangeControl(
      `category-text-height-${index}`,
      'text',
      'Text height',
      60,
      360,
      10,
      getCategoryTextHeight(category.name),
      value => setCategoryTextHeight(category.name, value),
    ),
    createCategoryRangeControl(
      `category-image-height-${index}`,
      'image',
      'Image size',
      180,
      720,
      20,
      getCategoryImageHeight(category.name),
      value => setCategoryImageHeight(category.name, value),
    ),
  );

  const body = document.createElement('div');
  body.className = 'category-window-body';
  const grid = document.createElement('div');
  grid.className = 'grid';
  body.append(grid);
  const status = document.createElement('div');
  status.className = 'category-window-status';
  status.textContent = '0 images';
  win.append(head, tools, body, status);
  windowLayer.append(win);
  clampCategoryWindowToDesktop(win);
  categoryWindowResizeObserver?.observe(win);

  win.addEventListener('pointerdown', () => bringCategoryWindowToFront(win));
  closeBtn.addEventListener('click', () => {
    closeCategoryWindow(win);
  });
  minimizeBtn.addEventListener('click', () => {
    minimizeCategoryWindow(win);
  });
  maxBtn.addEventListener('click', () => {
    win.classList.toggle('maximized');
    bringCategoryWindowToFront(win);
    if (!win.classList.contains('maximized')) clampCategoryWindowToDesktop(win, true);
    setTimeout(() => document.querySelectorAll('.crop-stage img').forEach(img => img.dispatchEvent(new Event('load'))), 60);
  });
  head.addEventListener('pointerdown', (event) => {
    if (event.target.closest('button')) return;
    bringCategoryWindowToFront(win);
    const startX = event.clientX;
    const startY = event.clientY;
    const startLeft = parseFloat(win.style.left) || win.offsetLeft || 0;
    const startTop = parseFloat(win.style.top) || win.offsetTop || 0;
    let dragging = false;
    head.setPointerCapture(event.pointerId);
    const move = (moveEvent) => {
      const dx = moveEvent.clientX - startX;
      const dy = moveEvent.clientY - startY;
      if (!dragging && Math.hypot(dx, dy) < 3) return;
      if (!dragging) {
        dragging = true;
        win.classList.remove('maximized');
      }
      const desktopRect = categoryDesktop.getBoundingClientRect();
      const maxLeft = Math.max(0, desktopRect.width - win.offsetWidth);
      const maxTop = Math.max(0, desktopRect.height - win.offsetHeight);
      const nextLeft = Math.max(0, Math.min(maxLeft, startLeft + dx));
      const nextTop = Math.max(0, Math.min(maxTop, startTop + dy));
      win.style.left = `${nextLeft}px`;
      win.style.top = `${nextTop}px`;
    };
    const up = () => {
      head.removeEventListener('pointermove', move);
      head.removeEventListener('pointerup', up);
      head.removeEventListener('pointercancel', up);
      if (dragging) saveCategoryWindowRect(win);
    };
    head.addEventListener('pointermove', move);
    head.addEventListener('pointerup', up, { once: true });
    head.addEventListener('pointercancel', up, { once: true });
  });

  for (const dropTarget of [win, body]) {
    dropTarget.addEventListener('dragover', (event) => {
      if (!hasImageDrag(event)) return;
      event.preventDefault();
      event.stopPropagation();
      if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
    });
    dropTarget.addEventListener('drop', async (event) => {
      if (!hasImageDrag(event)) return;
      event.preventDefault();
      event.stopPropagation();
      imageDragDepth = 0;
      dropPasteOverlay?.classList.remove('show');
      bringCategoryWindowToFront(win);
      await uploadImageFiles(event.dataTransfer?.files || [], 'dropped', win.dataset.category);
    });
  }

  return win;
}

function openCategoryWindow(name) {
  const win = document.querySelector(`.category-window[data-category="${CSS.escape(name)}"]`);
  if (!win) return;
  if (win.dataset.open !== '1' && !win.dataset.tabOrder) {
    categoryTabOrderCounter += 1;
    win.dataset.tabOrder = String(categoryTabOrderCounter);
  }
  win.dataset.open = '1';
  win.dataset.minimized = '0';
  win.hidden = false;
  clampCategoryWindowToDesktop(win, true);
  bringCategoryWindowToFront(win);
  updateWindowStatus(win);
  saveOpenCategoryWindows();
  setTimeout(() => document.querySelectorAll('.crop-stage img').forEach(img => img.dispatchEvent(new Event('load'))), 60);
  if (maskModeActive) {
    setTimeout(() => {
      win.querySelectorAll('.pair-card').forEach(card => {
        const index = parseInt(card.dataset.index, 10);
        if (Number.isFinite(index)) loadMaskCanvas(index);
      });
    }, 80);
  }
}

function restoreOpenCategoryWindows() {
  loadOpenCategoryWindows().forEach(item => {
    categoryTabOrderCounter = Math.max(categoryTabOrderCounter, parseInt(item.order || '0', 10) || 0);
    const win = document.querySelector(`.category-window[data-category="${CSS.escape(item.name)}"]`);
    if (win) win.dataset.tabOrder = String(item.order || categoryTabOrderCounter);
    openCategoryWindow(item.name);
    if (item.minimized) {
      minimizeCategoryWindow(win);
    }
  });
}

function restoreFrontCategoryWindow(fallbackName = activeCategory) {
  const name = localStorage.getItem(activeCategoryWindowStorageKey()) || fallbackName;
  const win = document.querySelector(`.category-window[data-category="${CSS.escape(name)}"]`);
  if (win && !win.hidden) {
    bringCategoryWindowToFront(win);
  } else {
    setActiveCategory(fallbackName);
  }
}

function distributePairCardsToWindows() {
  if (!windowLayer || !pairPool) return;
  CATEGORY_DEFS.forEach((category, index) => buildCategoryWindow(category, index));
  document.querySelectorAll('.pair-card').forEach(card => {
    const category = categoryExists(card.dataset.category) ? card.dataset.category : 'Undefined';
    card.dataset.category = category;
    const grid = document.querySelector(`.category-window[data-category="${CSS.escape(category)}"] .grid`);
    if (grid) grid.append(card);
  });
  document.querySelectorAll('.category-window').forEach(updateWindowStatus);
}

async function saveDesktopFolderPosition(icon) {
  if (!icon || !HAS_OPEN_FOLDER) return;
  const category = categoryData(icon.dataset.category);
  try {
    await fetch('/update_category', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        old_name: category.name,
        name: category.name,
        color: category.color,
        icon: category.icon,
        x: Math.max(0, Math.round(parseFloat(icon.style.left) || 0)),
        y: Math.max(0, Math.round(parseFloat(icon.style.top) || 0)),
      }),
    });
  } catch (e) {}
}

function initializeDesktopFolders() {
  if (!desktopIconLayer) return;
  normalizeDesktopFolderGridPositions();
  document.querySelectorAll('.desktop-folder').forEach(icon => {
    icon.addEventListener('click', (event) => {
      if (icon.dataset.dragSuppressClick === '1') {
        delete icon.dataset.dragSuppressClick;
        return;
      }
      selectOnlyDesktopFolder(icon);
      setActiveCategory(icon.dataset.category);
    });
    icon.addEventListener('dblclick', (event) => {
      if (icon.dataset.dragSuppressClick === '1') return;
      openCategoryWindow(icon.dataset.category);
    });
    icon.addEventListener('contextmenu', (event) => openFolderContextMenu(event, icon.dataset.category));
    icon.addEventListener('pointerdown', (event) => {
      if (event.button !== 0) return;
      event.preventDefault();
      event.stopPropagation();
      hideDesktopMenus();
      if (!icon.classList.contains('selected')) {
        selectOnlyDesktopFolder(icon);
      }
      setActiveCategory(icon.dataset.category);
      const startX = event.clientX;
      const startY = event.clientY;
      const dragIcons = selectedDesktopFolderIcons();
      const dragEntries = (dragIcons.length ? dragIcons : [icon]).map(item => ({
        icon: item,
        left: parseFloat(item.style.left) || 0,
        top: parseFloat(item.style.top) || 0,
      }));
      let moved = false;
      const move = (moveEvent) => {
        moveEvent.preventDefault();
        const dx = moveEvent.clientX - startX;
        const dy = moveEvent.clientY - startY;
        if (!moved && (Math.abs(dx) > 1 || Math.abs(dy) > 1)) {
          moved = true;
          dragEntries.forEach(entry => entry.icon.classList.add('dragging'));
        }
        const clamped = clampDesktopGroupDelta(dragEntries, dx, dy);
        dragEntries.forEach(entry => {
          entry.icon.style.left = `${entry.left + clamped.dx}px`;
          entry.icon.style.top = `${entry.top + clamped.dy}px`;
        });
      };
      const up = () => {
        document.removeEventListener('pointermove', move);
        document.removeEventListener('pointerup', up);
        document.removeEventListener('pointercancel', up);
        dragEntries.forEach(entry => entry.icon.classList.remove('dragging'));
        if (moved) {
          const positions = findFreeDesktopGroupPositions(dragEntries, icon);
          positions.forEach(({ icon: movedIcon, pos }) => {
            movedIcon.style.left = `${pos.x}px`;
            movedIcon.style.top = `${pos.y}px`;
            movedIcon.dataset.dragSuppressClick = '1';
            saveDesktopFolderPosition(movedIcon);
          });
        } else {
          const now = Date.now();
          selectOnlyDesktopFolder(icon);
          setActiveCategory(icon.dataset.category);
          if (lastDesktopFolderClick.name === icon.dataset.category && now - lastDesktopFolderClick.time < 350) {
            openCategoryWindow(icon.dataset.category);
            lastDesktopFolderClick = { name: '', time: 0 };
          } else {
            lastDesktopFolderClick = { name: icon.dataset.category, time: now };
          }
        }
      };
      document.addEventListener('pointermove', move, { passive: false });
      document.addEventListener('pointerup', up, { once: true });
      document.addEventListener('pointercancel', up, { once: true });
    });
  });
}

function initializeDesktopSelectionBox() {
  if (!categoryDesktop) return;
  categoryDesktop.addEventListener('pointerdown', (event) => {
    if (event.button !== 0) return;
    if (event.target.closest('.desktop-folder') || event.target.closest('.category-window') || event.target.closest('.desktop-context-menu')) return;
    event.preventDefault();
    hideDesktopMenus();
    clearDesktopFolderSelection();
    const desktopRect = categoryDesktop.getBoundingClientRect();
    const startX = event.clientX - desktopRect.left;
    const startY = event.clientY - desktopRect.top;
    const box = document.createElement('div');
    box.className = 'desktop-selection-box';
    box.style.left = `${startX}px`;
    box.style.top = `${startY}px`;
    box.style.width = '0px';
    box.style.height = '0px';
    categoryDesktop.append(box);
    let moved = false;

    const updateSelection = (moveEvent) => {
      moveEvent.preventDefault();
      const currentX = moveEvent.clientX - desktopRect.left;
      const currentY = moveEvent.clientY - desktopRect.top;
      const left = Math.max(0, Math.min(startX, currentX));
      const top = Math.max(0, Math.min(startY, currentY));
      const right = Math.min(desktopRect.width, Math.max(startX, currentX));
      const bottom = Math.min(desktopRect.height, Math.max(startY, currentY));
      moved = moved || Math.abs(currentX - startX) > 2 || Math.abs(currentY - startY) > 2;
      box.style.left = `${left}px`;
      box.style.top = `${top}px`;
      box.style.width = `${Math.max(0, right - left)}px`;
      box.style.height = `${Math.max(0, bottom - top)}px`;
      document.querySelectorAll('.desktop-folder').forEach(icon => {
        const iconLeft = (desktopIconLayer?.offsetLeft || 0) + (parseFloat(icon.style.left) || 0);
        const iconTop = (desktopIconLayer?.offsetTop || 0) + (parseFloat(icon.style.top) || 0);
        const iconRight = iconLeft + (icon.offsetWidth || DESKTOP_ICON_GRID_X);
        const iconBottom = iconTop + (icon.offsetHeight || DESKTOP_ICON_GRID_Y);
        const intersects = iconLeft < right && iconRight > left && iconTop < bottom && iconBottom > top;
        setDesktopFolderSelected(icon, intersects);
      });
    };

    const finishSelection = () => {
      document.removeEventListener('pointermove', updateSelection);
      document.removeEventListener('pointerup', finishSelection);
      document.removeEventListener('pointercancel', finishSelection);
      box.remove();
      if (!moved) clearDesktopFolderSelection();
      const firstSelected = selectedDesktopFolderIcons()[0];
      if (firstSelected) setActiveCategory(firstSelected.dataset.category);
    };

    document.addEventListener('pointermove', updateSelection, { passive: false });
    document.addEventListener('pointerup', finishSelection, { once: true });
    document.addEventListener('pointercancel', finishSelection, { once: true });
  });
}

function openCategoryDialog(name = '', position = null) {
  const category = name ? categoryData(name) : null;
  categoryOldName.value = category?.name || '';
  categoryNameInput.value = category?.name || 'New Category';
  categoryColorInput.value = category?.color || '#9ca3af';
  if (categoryGroupInput) categoryGroupInput.value = category?.group_id || 'uncategorized';
  const iconValue = category?.icon || 'undefined.png';
  if (categoryIconInput && !Array.from(categoryIconInput.options).some(option => option.value === iconValue)) {
    const option = document.createElement('option');
    option.value = iconValue;
    option.textContent = `Custom: ${iconValue}`;
    const uploadOption = categoryIconInput.querySelector('option[value="__upload_icon__"]');
    categoryIconInput.insertBefore(option, uploadOption || null);
  }
  categoryIconInput.value = iconValue;
  previousCategoryIconValue = iconValue;
  pendingNewFolderPosition = category ? null : position;
  categoryDialogBackdrop?.classList.add('open');
  categoryNameInput.focus();
}

function closeCategoryDialog() {
  categoryDialogBackdrop?.classList.remove('open');
  pendingNewFolderPosition = null;
}

function selectedCategoryGroupData() {
  const id = categoryGroupManageSelect?.value || '__new__';
  return CATEGORY_GROUPS.find(item => item.id === id) || null;
}

function syncCategoryGroupDialog() {
  const group = selectedCategoryGroupData();
  if (categoryGroupNameInput) categoryGroupNameInput.value = group?.name || 'New Group';
  if (categoryGroupColorInput) categoryGroupColorInput.value = group?.color || '#9ca3af';
  if (deleteCategoryGroupBtn) {
    deleteCategoryGroupBtn.disabled = !group || group.id === 'uncategorized';
    deleteCategoryGroupBtn.style.display = group ? '' : 'none';
  }
}

function openCategoryGroupDialog(createNew = false) {
  if (categoryGroupManageSelect) {
    categoryGroupManageSelect.value = createNew ? '__new__' : (CATEGORY_GROUPS[0]?.id || '__new__');
  }
  syncCategoryGroupDialog();
  categoryGroupDialogBackdrop?.classList.add('open');
  categoryGroupNameInput?.focus();
  categoryGroupNameInput?.select();
}

function closeCategoryGroupDialog() {
  categoryGroupDialogBackdrop?.classList.remove('open');
}

function initializeCategoryDesktop() {
  distributePairCardsToWindows();
  initializeDesktopFolders();
  initializeDesktopSelectionBox();
  setActiveCategory(activeCategory);
  const restoredActiveCategory = activeCategory;
  restoreOpenCategoryWindows();
  restoreFrontCategoryWindow(restoredActiveCategory);
}

categoryDesktop?.addEventListener('contextmenu', openDesktopContextMenu);
desktopContextMenu?.addEventListener('click', (event) => {
  const action = event.target.closest('button')?.dataset.action;
  if (action === 'new-folder') {
    const position = pendingNewFolderPosition;
    hideDesktopMenus();
    openCategoryDialog('', position);
  }
  if (action === 'new-category-group') {
    hideDesktopMenus();
    openCategoryGroupDialog(true);
  }
  if (action === 'manage-category-groups') {
    hideDesktopMenus();
    openCategoryGroupDialog(false);
  }
});
folderContextMenu?.addEventListener('click', async (event) => {
  const action = event.target.closest('button')?.dataset.action;
  const name = contextFolderName;
  hideDesktopMenus();
  if (action === 'edit-folder') openCategoryDialog(name);
  if (action === 'delete-folder') await deleteCategoryByName(name);
});
cardContextMenu?.addEventListener('click', (event) => {
  const action = event.target.closest('button')?.dataset.action;
  const card = contextCard;
  hideDesktopMenus();
  if (card && !card.classList.contains('selected')) selectOnlyPair(card);
  if (action === 'copy-card') setPairClipboardFromSelection('copy');
  if (action === 'cut-card') setPairClipboardFromSelection('move');
  if (action === 'delete-card') deleteSelectedCards();
});
document.addEventListener('contextmenu', (event) => {
  const card = event.target.closest('.pair-card');
  if (card) openCardContextMenu(event, card);
});
document.addEventListener('click', (event) => {
  if (!event.target.closest('.desktop-context-menu')) hideDesktopMenus();
  if (!event.target.closest('.pair-card')) clearPairSelection();
});
document.addEventListener('keydown', (event) => {
  if (event.key === 'Escape') {
    hideDesktopMenus();
    closeCategoryGroupDialog();
  }
});
document.getElementById('cancelCategoryDialogBtn')?.addEventListener('click', closeCategoryDialog);
document.getElementById('cancelCategoryGroupDialogBtn')?.addEventListener('click', closeCategoryGroupDialog);
categoryGroupManageSelect?.addEventListener('change', syncCategoryGroupDialog);
categoryGroupDialog?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const group = selectedCategoryGroupData();
  const payload = {
    id: group?.id || '',
    name: categoryGroupNameInput?.value || '',
    color: categoryGroupColorInput?.value || '#9ca3af',
  };
  const res = await fetch(group ? '/update_category_group' : '/create_category_group', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    await appAlert(data.error || 'Saving category group failed.');
    return;
  }
  suppressBeforeUnload = true;
  window.location.reload();
});
deleteCategoryGroupBtn?.addEventListener('click', async () => {
  const group = selectedCategoryGroupData();
  if (!group || group.id === 'uncategorized') return;
  const confirmed = await appConfirm(
    `Delete category group "${group.name}"? Its folders will be moved to Uncategorized.`,
    'Delete category group',
  );
  if (!confirmed) return;
  const res = await fetch('/delete_category_group', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id: group.id }),
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    await appAlert(data.error || 'Deleting category group failed.');
    return;
  }
  suppressBeforeUnload = true;
  window.location.reload();
});
categoryDialog?.addEventListener('submit', async (event) => {
  event.preventDefault();
  const oldName = categoryOldName.value;
  const payload = {
    old_name: oldName,
    name: categoryNameInput.value,
    color: categoryColorInput.value,
    group_id: categoryGroupInput?.value || 'uncategorized',
    icon: categoryIconInput.value,
  };
  if (!oldName && pendingNewFolderPosition) {
    payload.x = pendingNewFolderPosition.x;
    payload.y = pendingNewFolderPosition.y;
  }
  const url = oldName ? '/update_category' : '/create_category';
  const res = await fetch(url, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    await appAlert(data.error || 'Saving category failed.');
    return;
  }
  suppressBeforeUnload = true;
  window.location.reload();
});
addFilesForm?.addEventListener('submit', () => {
  if (addFilesCategoryInput) addFilesCategoryInput.value = activeCategory;
});

categoryIconInput?.addEventListener('focus', () => {
  if (categoryIconInput.value !== '__upload_icon__') previousCategoryIconValue = categoryIconInput.value;
});
categoryIconInput?.addEventListener('change', () => {
  if (categoryIconInput.value !== '__upload_icon__') {
    previousCategoryIconValue = categoryIconInput.value;
    return;
  }
  categoryIconFileInput?.click();
});
categoryIconFileInput?.addEventListener('change', async () => {
  const file = categoryIconFileInput.files?.[0];
  categoryIconFileInput.value = '';
  if (!file) {
    categoryIconInput.value = previousCategoryIconValue || 'undefined.png';
    return;
  }
  const formData = new FormData();
  formData.append('icon', file, file.name || 'category_icon.png');
  try {
    const res = await fetch('/upload_category_icon', {
      method: 'POST',
      body: formData,
    });
    const data = await res.json();
    if (!res.ok || !data.ok || !data.icon) throw new Error(data.error || 'Icon upload failed.');
    if (!Array.from(categoryIconInput.options).some(option => option.value === data.icon)) {
      const option = document.createElement('option');
      option.value = data.icon;
      option.textContent = `Custom: ${data.icon}`;
      const uploadOption = categoryIconInput.querySelector('option[value="__upload_icon__"]');
      categoryIconInput.insertBefore(option, uploadOption || null);
    }
    categoryIconInput.value = data.icon;
    previousCategoryIconValue = data.icon;
  } catch (err) {
    await appAlert(err?.message || 'Icon upload failed.');
    categoryIconInput.value = previousCategoryIconValue || 'undefined.png';
  }
});
initializeCategoryDesktop();

function ensureState(index) {
  const existing = cropStates.get(index) || {};
  const normalized = {
    crop: existing.crop || null,
    upscale: !!existing.upscale,
    rotation: Number.isFinite(existing.rotation) ? existing.rotation : 0,
    flipH: !!existing.flipH,
    flipV: !!existing.flipV,
    ratioLocked: !!existing.ratioLocked,
    lockedAspect: Number.isFinite(existing.lockedAspect) && existing.lockedAspect > 0 ? existing.lockedAspect : null,
  };
  cropStates.set(index, normalized);
  return normalized;
}

function getMaskCanvas(index) {
  return document.getElementById(`mask-canvas-${index}`);
}

function getMaskState(index) {
  const stateMap = maskModePurpose === 'watermark' ? watermarkMaskStates : maskStates;
  const existing = stateMap.get(index) || {};
  const state = {
    undo: Array.isArray(existing.undo) ? existing.undo : [],
    redo: Array.isArray(existing.redo) ? existing.redo : [],
    dirty: !!existing.dirty,
    savedSnapshot: typeof existing.savedSnapshot === 'string' ? existing.savedSnapshot : '',
  };
  stateMap.set(index, state);
  return state;
}

function snapshotMaskCanvas(index) {
  const canvas = getMaskCanvas(index);
  if (!canvas || !canvas.width || !canvas.height) return '';
  return canvas.toDataURL('image/png');
}

function restoreMaskSnapshot(index, snapshot) {
  const canvas = getMaskCanvas(index);
  if (!canvas || !snapshot) return Promise.resolve(false);
  return new Promise(resolve => {
    const img = new Image();
    img.onload = () => {
      const ctx = canvas.getContext('2d');
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      ctx.drawImage(img, 0, 0, canvas.width, canvas.height);
      resolve(true);
    };
    img.onerror = () => resolve(false);
    img.src = snapshot;
  });
}

function setMaskDirty(index, dirty = true) {
  const state = getMaskState(index);
  state.dirty = !!dirty;
  (maskModePurpose === 'watermark' ? watermarkMaskStates : maskStates).set(index, state);
  updateMaskHistoryButtons(index);
  markUnsaved(index);
}

function pushMaskUndoSnapshot(index, snapshot) {
  if (!snapshot) return;
  const state = getMaskState(index);
  const last = state.undo[state.undo.length - 1];
  if (last !== snapshot) {
    state.undo.push(snapshot);
    if (state.undo.length > 10) state.undo.shift();
  }
  state.redo = [];
  (maskModePurpose === 'watermark' ? watermarkMaskStates : maskStates).set(index, state);
  setMaskDirty(index, true);
}

function clearMaskHistory(index, savedSnapshot = snapshotMaskCanvas(index)) {
  const state = getMaskState(index);
  state.undo = [];
  state.redo = [];
  state.dirty = false;
  state.savedSnapshot = savedSnapshot || '';
  (maskModePurpose === 'watermark' ? watermarkMaskStates : maskStates).set(index, state);
  updateMaskHistoryButtons(index);
}

function updateMaskHistoryButtons(index) {
  const state = getMaskState(index);
  const card = getCardByIndex(index);
  const undoBtn = card?.querySelector('.undo-btn');
  const redoBtn = card?.querySelector('.redo-btn');
  if (undoBtn && maskModeActive) undoBtn.disabled = state.undo.length === 0;
  if (redoBtn) redoBtn.disabled = !maskModeActive || state.redo.length === 0;
}

function updateAllMaskHistoryButtons() {
  document.querySelectorAll('.pair-card').forEach(card => {
    const index = parseInt(card.dataset.index, 10);
    if (Number.isFinite(index)) updateMaskHistoryButtons(index);
  });
}

async function undoMaskChange(index) {
  const state = getMaskState(index);
  if (!state.undo.length) return;
  const current = snapshotMaskCanvas(index);
  const previous = state.undo.pop();
  if (current) state.redo.push(current);
  if (state.redo.length > 10) state.redo.shift();
  const restored = await restoreMaskSnapshot(index, previous);
  if (restored) {
    state.dirty = previous !== state.savedSnapshot;
    (maskModePurpose === 'watermark' ? watermarkMaskStates : maskStates).set(index, state);
    updateMaskHistoryButtons(index);
    markUnsaved(index);
  }
}

async function redoMaskChange(index) {
  const state = getMaskState(index);
  if (!state.redo.length) return;
  const current = snapshotMaskCanvas(index);
  const next = state.redo.pop();
  if (current) {
    state.undo.push(current);
    if (state.undo.length > 10) state.undo.shift();
  }
  const restored = await restoreMaskSnapshot(index, next);
  if (restored) {
    state.dirty = next !== state.savedSnapshot;
    (maskModePurpose === 'watermark' ? watermarkMaskStates : maskStates).set(index, state);
    updateMaskHistoryButtons(index);
    markUnsaved(index);
  }
}

function resetMaskUnsaved(index) {
  const state = getMaskState(index);
  if (!state.dirty) return;
  const savedSnapshot = state.savedSnapshot;
  clearMaskHistory(index, savedSnapshot);
  if (savedSnapshot) {
    restoreMaskSnapshot(index, savedSnapshot).then(() => {
      updateMaskHistoryButtons(index);
      markUnsaved(index);
    });
  } else {
    markUnsaved(index);
  }
}

function ensureMaskCanvasElement(card) {
  const index = parseInt(card?.dataset.index || '', 10);
  if (!Number.isFinite(index)) return null;
  const stage = card.querySelector('.crop-stage');
  if (!stage) return null;
  let canvas = stage.querySelector('.mask-canvas');
  if (!canvas) {
    canvas = document.createElement('canvas');
    canvas.className = 'mask-canvas';
    stage.insertBefore(canvas, stage.querySelector('.crop-overlay'));
  }
  canvas.id = `mask-canvas-${index}`;
  canvas.dataset.index = String(index);
  return canvas;
}

function positionMaskCanvas(index) {
  const canvas = getMaskCanvas(index);
  const stage = document.getElementById(`crop-stage-${index}`);
  if (!canvas || !stage) return;
  const box = getRenderedImageBox(index);
  canvas.style.left = `${box.left}px`;
  canvas.style.top = `${box.top}px`;
  canvas.style.width = `${box.width}px`;
  canvas.style.height = `${box.height}px`;
}

function prepareMaskCanvasForEdit(index) {
  const card = getCardByIndex(index);
  if (!card) return null;
  const canvas = ensureMaskCanvasElement(card);
  const stage = document.getElementById(`crop-stage-${index}`);
  if (!canvas || !stage) return null;
  const width = parseInt(stage.dataset.width, 10) || 1;
  const height = parseInt(stage.dataset.height, 10) || 1;
  const needsFill = canvas.width !== width || canvas.height !== height;
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  if (needsFill && !getMaskState(index).savedSnapshot) {
    const ctx = canvas.getContext('2d', { willReadFrequently: true });
    ctx.fillStyle = '#000';
    ctx.fillRect(0, 0, canvas.width, canvas.height);
  }
  positionMaskCanvas(index);
  return canvas;
}

function loadMaskCanvas(index, force = false) {
  const card = getCardByIndex(index);
  if (!card) return;
  const canvas = ensureMaskCanvasElement(card);
  const stage = document.getElementById(`crop-stage-${index}`);
  if (!canvas || !stage) return;
  const imgName = card.dataset.img;
  const width = parseInt(stage.dataset.width, 10) || 1;
  const height = parseInt(stage.dataset.height, 10) || 1;
  if (canvas.width !== width) canvas.width = width;
  if (canvas.height !== height) canvas.height = height;
  positionMaskCanvas(index);
  if (maskCanvasLoaded.has(imgName) && !force) return;

  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, canvas.width, canvas.height);

  if (maskModePurpose === 'watermark') {
    const state = getMaskState(index);
    if (state.savedSnapshot && !force) {
      restoreMaskSnapshot(index, state.savedSnapshot);
    } else {
      clearMaskHistory(index, snapshotMaskCanvas(index));
    }
    return;
  }

  const maskImg = new Image();
  maskImg.onload = () => {
    ctx.clearRect(0, 0, canvas.width, canvas.height);
    ctx.drawImage(maskImg, 0, 0, canvas.width, canvas.height);
    maskCanvasLoaded.add(imgName);
    const state = getMaskState(index);
    if (!state.dirty || force) {
      clearMaskHistory(index, snapshotMaskCanvas(index));
    } else {
      updateMaskHistoryButtons(index);
    }
  };
  maskImg.onerror = () => {
    maskCanvasLoaded.delete(imgName);
  };
  maskImg.src = `/mask/${encodeURIComponent(imgName)}?t=${Date.now()}`;
}

function setMaskTool(tool) {
  currentMaskTool = ['brush', 'fill'].includes(tool) ? tool : 'brush';
  document.querySelectorAll('.mask-tool-btn').forEach(btn => {
    btn.classList.toggle('active', btn.dataset.tool === currentMaskTool);
    btn.setAttribute('aria-pressed', btn.dataset.tool === currentMaskTool ? 'true' : 'false');
  });
  if (currentMaskTool === 'brush') {
    updateBrushCursor();
  } else {
    hideBrushCursor();
  }
}

function syncMaskSizeControls() {
  const popupSlider = document.getElementById('popupMaskSizeSlider');
  const popupValue = document.getElementById('popupMaskSizeValue');
  const popupFeatherSlider = document.getElementById('popupMaskFeatherSlider');
  const popupFeatherValue = document.getElementById('popupMaskFeatherValue');
  if (popupSlider) popupSlider.value = String(currentMaskSize);
  if (popupValue) popupValue.textContent = `${currentMaskSize}px`;
  if (popupFeatherSlider) popupFeatherSlider.value = String(featherPixelsToSlider(currentMaskFeather));
  if (popupFeatherValue) popupFeatherValue.textContent = `${currentMaskFeather}px`;
  document.querySelectorAll('.mask-size-slider').forEach(slider => {
    slider.value = String(currentMaskSize);
  });
  document.querySelectorAll('.mask-size-value').forEach(valueEl => {
    valueEl.textContent = `${currentMaskSize}px`;
  });
  updateBrushCursor();
}

function syncMaskFillToleranceControls() {
  const popupSlider = document.getElementById('popupMaskFillToleranceSlider');
  const popupValue = document.getElementById('popupMaskFillToleranceValue');
  if (popupSlider) popupSlider.value = String(currentMaskFillTolerance);
  if (popupValue) popupValue.textContent = String(currentMaskFillTolerance);
}

function setCurrentMaskSize(value) {
  currentMaskSize = Math.round(Math.max(2, Math.min(160, Number(value) || 32)));
  syncMaskSizeControls();
}

function featherSliderToPixels(value) {
  const t = Math.max(0, Math.min(100, Number(value) || 0)) / 100;
  return Math.round(160 * Math.pow(t, 2.32));
}

function featherPixelsToSlider(value) {
  const px = Math.max(0, Math.min(160, Number(value) || 0));
  return Math.round(Math.pow(px / 160, 1 / 2.32) * 100);
}

function setCurrentMaskFeather(value) {
  currentMaskFeather = featherSliderToPixels(value);
  syncMaskSizeControls();
}

function setCurrentMaskFillTolerance(value) {
  currentMaskFillTolerance = Math.round(Math.max(0, Math.min(255, Number(value) || 0)));
  syncMaskFillToleranceControls();
}

function getMaskSizePopoverAnchor() {
  const popover = document.getElementById('maskSizePopover');
  const index = popover?.dataset.anchorIndex;
  return index ? document.querySelector(`.mask-brush-btn[data-index="${CSS.escape(index)}"]`) : null;
}

function positionMaskSizePopover(anchor) {
  const popover = document.getElementById('maskSizePopover');
  if (!popover || !anchor || !popover.classList.contains('open')) return;
  const rect = anchor.getBoundingClientRect();
  const popRect = popover.getBoundingClientRect();
  const gap = 8;
  let top = rect.top - popRect.height - gap;
  if (top < gap) top = rect.bottom + gap;
  let left = rect.left + rect.width / 2 - popRect.width / 2;
  left = Math.max(gap, Math.min(window.innerWidth - popRect.width - gap, left));
  popover.style.left = `${left}px`;
  popover.style.top = `${top}px`;
}

function openMaskSizePopover(anchor) {
  const popover = document.getElementById('maskSizePopover');
  if (!popover || !anchor) return;
  closeMaskFillTolerancePopover();
  popover.dataset.anchorIndex = anchor.dataset.index || '';
  popover.classList.add('open');
  popover.setAttribute('aria-hidden', 'false');
  syncMaskSizeControls();
  positionMaskSizePopover(anchor);
}

function closeMaskSizePopover() {
  const popover = document.getElementById('maskSizePopover');
  if (!popover) return;
  popover.classList.remove('open');
  popover.setAttribute('aria-hidden', 'true');
  popover.style.left = '';
  popover.style.top = '';
}

function getMaskFillTolerancePopoverAnchor() {
  const popover = document.getElementById('maskFillTolerancePopover');
  const index = popover?.dataset.anchorIndex;
  return index ? document.querySelector(`.mask-fill-btn[data-index="${CSS.escape(index)}"]`) : null;
}

function positionMaskFillTolerancePopover(anchor) {
  const popover = document.getElementById('maskFillTolerancePopover');
  if (!popover || !anchor || !popover.classList.contains('open')) return;
  const rect = anchor.getBoundingClientRect();
  const popRect = popover.getBoundingClientRect();
  const gap = 8;
  let top = rect.top - popRect.height - gap;
  if (top < gap) top = rect.bottom + gap;
  let left = rect.left + rect.width / 2 - popRect.width / 2;
  left = Math.max(gap, Math.min(window.innerWidth - popRect.width - gap, left));
  popover.style.left = `${left}px`;
  popover.style.top = `${top}px`;
}

function openMaskFillTolerancePopover(anchor) {
  const popover = document.getElementById('maskFillTolerancePopover');
  if (!popover || !anchor) return;
  closeMaskSizePopover();
  popover.dataset.anchorIndex = anchor.dataset.index || '';
  popover.classList.add('open');
  popover.setAttribute('aria-hidden', 'false');
  syncMaskFillToleranceControls();
  positionMaskFillTolerancePopover(anchor);
}

function closeMaskFillTolerancePopover() {
  const popover = document.getElementById('maskFillTolerancePopover');
  if (!popover) return;
  popover.classList.remove('open');
  popover.setAttribute('aria-hidden', 'true');
  popover.style.left = '';
  popover.style.top = '';
}

function toggleMaskFillTolerancePopover(anchor) {
  const popover = document.getElementById('maskFillTolerancePopover');
  if (!popover || !anchor) return;
  if (popover.classList.contains('open') && popover.dataset.anchorIndex === (anchor.dataset.index || '')) {
    closeMaskFillTolerancePopover();
  } else {
    openMaskFillTolerancePopover(anchor);
  }
}

function toggleMaskSizePopover(anchor) {
  const popover = document.getElementById('maskSizePopover');
  if (!popover || !anchor) return;
  if (popover.classList.contains('open') && popover.dataset.anchorIndex === (anchor.dataset.index || '')) {
    closeMaskSizePopover();
  } else {
    openMaskSizePopover(anchor);
  }
}

function getBrushCursorElement() {
  let cursor = document.getElementById('maskBrushCursor');
  if (!cursor) {
    cursor = document.createElement('div');
    cursor.id = 'maskBrushCursor';
    cursor.className = 'mask-brush-cursor';
    document.body.appendChild(cursor);
  }
  return cursor;
}

function hideBrushCursor() {
  const cursor = document.getElementById('maskBrushCursor');
  cursor?.classList.remove('visible');
  activeBrushCursorCanvas = null;
  lastBrushCursorPoint = null;
}

function updateBrushCursor(canvas = activeBrushCursorCanvas, point = lastBrushCursorPoint) {
  if (!maskModeActive || currentMaskTool !== 'brush' || !canvas || !point) {
    const cursor = document.getElementById('maskBrushCursor');
    cursor?.classList.remove('visible');
    return;
  }
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height || !canvas.width || !canvas.height || point.clientX < rect.left || point.clientX > rect.right || point.clientY < rect.top || point.clientY > rect.bottom) {
    hideBrushCursor();
    return;
  }
  const scale = ((rect.width / canvas.width) + (rect.height / canvas.height)) / 2;
  const size = Math.max(2, (currentMaskSize + currentMaskFeather * 2) * scale);
  const cursor = getBrushCursorElement();
  cursor.style.width = `${size}px`;
  cursor.style.height = `${size}px`;
  cursor.style.left = `${point.clientX}px`;
  cursor.style.top = `${point.clientY}px`;
  cursor.classList.add('visible');
}

function showBrushCursor(canvas, event) {
  activeBrushCursorCanvas = canvas;
  lastBrushCursorPoint = { clientX: event.clientX, clientY: event.clientY };
  updateBrushCursor();
}

async function saveMaskCanvas(index) {
  const canvas = getMaskCanvas(index);
  const card = getCardByIndex(index);
  if (!canvas || !card) return true;
  try {
    const res = await fetch('/save_mask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        img_name: card.dataset.img,
        mask_data: canvas.toDataURL('image/png'),
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Mask save failed.');
    clearMaskHistory(index, snapshotMaskCanvas(index));
    markUnsaved(index);
    return true;
  } catch (err) {
    setStatusbarMessage(err?.message || 'Mask save failed.');
    return false;
  }
}

async function applyMaskDataUrl(index, dataUrl) {
  const before = snapshotMaskCanvas(index);
  const restored = await restoreMaskSnapshot(index, dataUrl);
  if (!restored) return false;
  pushMaskUndoSnapshot(index, before);
  return true;
}

async function autoMaskCard(index, options = {}) {
  const silent = !!options.silent;
  const setMessage = options.setMessage !== false;
  autoMaskCard.lastError = '';
  if (!maskModeActive && options.ensureMaskMode !== false) {
    await setMaskMode(true);
    if (!maskModeActive) {
      autoMaskCard.lastError = 'Masking mode could not be enabled.';
      return false;
    }
  }
  const card = getCardByIndex(index);
  const canvas = prepareMaskCanvasForEdit(index);
  if (!card || !canvas) return false;
  const btn = card.querySelector('.automask-btn');
  btn?.setAttribute('aria-busy', 'true');
  if (btn) btn.disabled = true;
  try {
    const res = await fetch('/auto_mask', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        img_name: card.dataset.img,
        model: options.model || getCurrentMaskModel(),
        post_process_mask: options.postProcessMask !== false,
        expand_pixels: Number.isFinite(Number(options.expandPixels)) ? Number(options.expandPixels) : getMaskSettings().expand_pixels,
        feather_pixels: Number.isFinite(Number(options.featherPixels)) ? Number(options.featherPixels) : getMaskSettings().feather_pixels,
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok || !data.mask_data) {
      throw new Error(data.error || 'Auto mask failed.');
    }
    const applied = await applyMaskDataUrl(index, data.mask_data);
    if (!applied) throw new Error('Auto mask result could not be loaded.');
    if (setMessage) {
      setStatusbarMessage(`Auto mask created with ${data.model || 'silueta'}. Save the card to write it to disk.`);
    }
    return true;
  } catch (err) {
  const message = err?.message || 'Auto mask failed.';
    autoMaskCard.lastError = message;
    if (!silent) await appAlert(message);
    return false;
  } finally {
    btn?.removeAttribute('aria-busy');
    if (btn) btn.disabled = false;
  }
}

async function autoMaskCategory(category) {
  await openMaskModalForCategory(category);
}

function maskPointFromEvent(index, event) {
  const canvas = getMaskCanvas(index);
  if (!canvas) return null;
  const rect = canvas.getBoundingClientRect();
  if (!rect.width || !rect.height) return null;
  const x = ((event.clientX - rect.left) / rect.width) * canvas.width;
  const y = ((event.clientY - rect.top) / rect.height) * canvas.height;
  if (x < 0 || y < 0 || x > canvas.width || y > canvas.height) return null;
  return { x, y };
}

function getMaskBrushStamp() {
  const coreRadius = Math.max(0.5, currentMaskSize / 2);
  const feather = Math.max(0, currentMaskFeather);
  const radius = Math.max(coreRadius, coreRadius + feather);
  const key = `${currentMaskSize}:${currentMaskFeather}`;
  const cached = maskBrushStampCache.get(key);
  if (cached) return cached;

  const size = Math.max(3, Math.ceil(radius * 2 + 3));
  const center = size / 2;
  const data = new Uint8ClampedArray(size * size);
  const smoothstep = (value) => value * value * (3 - 2 * value);

  for (let y = 0; y < size; y += 1) {
    for (let x = 0; x < size; x += 1) {
      const distance = Math.hypot((x + 0.5) - center, (y + 0.5) - center);
      let coverage = 0;
      if (feather > 0) {
        if (distance <= coreRadius) {
          coverage = 1;
        } else if (distance < radius) {
          const t = clamp((distance - coreRadius) / feather, 0, 1);
          coverage = 1 - smoothstep(t);
        }
      } else {
        coverage = clamp(coreRadius + 0.5 - distance, 0, 1);
      }
      data[y * size + x] = Math.round(clamp(coverage, 0, 1) * 255);
    }
  }

  const stamp = { data, size, center, radius };
  maskBrushStampCache.set(key, stamp);
  if (maskBrushStampCache.size > 24) {
    const firstKey = maskBrushStampCache.keys().next().value;
    maskBrushStampCache.delete(firstKey);
  }
  return stamp;
}

function applyBrushStampToData(data, width, height, left, top, x, y, stamp, erase = false) {
  const stampLeft = Math.round(x - stamp.center) - left;
  const stampTop = Math.round(y - stamp.center) - top;
  for (let sy = 0; sy < stamp.size; sy += 1) {
    const py = stampTop + sy;
    if (py < 0 || py >= height) continue;
    for (let sx = 0; sx < stamp.size; sx += 1) {
      const target = stamp.data[sy * stamp.size + sx];
      if (target <= 0) continue;
      const px = stampLeft + sx;
      if (px < 0 || px >= width) continue;
      const offset = (py * width + px) * 4;
      const current = data[offset];
      const next = erase
        ? Math.min(current, 255 - target)
        : Math.max(current, target);
      data[offset] = next;
      data[offset + 1] = next;
      data[offset + 2] = next;
      data[offset + 3] = 255;
    }
  }
}

function drawMaskPoint(index, point, previousPoint = null, erase = false) {
  const canvas = getMaskCanvas(index);
  if (!canvas || !point) return;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const stamp = getMaskBrushStamp();
  const start = previousPoint || point;
  const dx = point.x - start.x;
  const dy = point.y - start.y;
  const distance = Math.hypot(dx, dy);
  const spacing = Math.max(0.75, Math.min(5, stamp.radius * 0.14));
  const steps = Math.max(1, Math.ceil(distance / spacing));
  const points = previousPoint ? [] : [point];
  if (previousPoint) {
    for (let i = 1; i <= steps; i += 1) {
      const t = steps ? i / steps : 1;
      points.push({ x: start.x + dx * t, y: start.y + dy * t });
    }
  }
  if (!points.length) points.push(point);

  const pad = stamp.radius + 2;
  let minX = points[0].x;
  let minY = points[0].y;
  let maxX = points[0].x;
  let maxY = points[0].y;
  for (const p of points) {
    minX = Math.min(minX, p.x);
    minY = Math.min(minY, p.y);
    maxX = Math.max(maxX, p.x);
    maxY = Math.max(maxY, p.y);
  }
  const left = Math.max(0, Math.floor(minX - pad));
  const top = Math.max(0, Math.floor(minY - pad));
  const right = Math.min(canvas.width, Math.ceil(maxX + pad));
  const bottom = Math.min(canvas.height, Math.ceil(maxY + pad));
  const width = right - left;
  const height = bottom - top;
  if (width <= 0 || height <= 0) return;

  const imageData = ctx.getImageData(left, top, width, height);
  const data = imageData.data;
  for (const stampPoint of points) {
    applyBrushStampToData(data, width, height, left, top, stampPoint.x, stampPoint.y, stamp, erase);
  }
  ctx.putImageData(imageData, left, top);
}

function fillMaskArea(index, point, erase = false) {
  const canvas = getMaskCanvas(index);
  if (!canvas || !point) return false;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  const width = canvas.width;
  const height = canvas.height;
  const startX = Math.floor(clamp(point.x, 0, width - 1));
  const startY = Math.floor(clamp(point.y, 0, height - 1));
  const imageData = ctx.getImageData(0, 0, width, height);
  const data = imageData.data;
  const startOffset = (startY * width + startX) * 4;
  const startValue = data[startOffset];
  const fillValue = erase ? 0 : 255;
  const tolerance = Math.round(clamp(currentMaskFillTolerance, 0, 255));
  if (startValue === fillValue) return false;

  const stack = [startY * width + startX];
  const visited = new Uint8Array(width * height);
  let changed = false;

  while (stack.length) {
    const pixelIndex = stack.pop();
    if (pixelIndex < 0 || pixelIndex >= visited.length) continue;
    if (visited[pixelIndex]) continue;
    visited[pixelIndex] = 1;

    const offset = pixelIndex * 4;
    if (Math.abs(data[offset] - startValue) > tolerance) continue;

    data[offset] = fillValue;
    data[offset + 1] = fillValue;
    data[offset + 2] = fillValue;
    data[offset + 3] = 255;
    changed = true;

    const x = pixelIndex % width;
    if (x < width - 1) stack.push(pixelIndex + 1);
    if (x > 0) stack.push(pixelIndex - 1);
    if (pixelIndex + width < visited.length) stack.push(pixelIndex + width);
    if (pixelIndex - width >= 0) stack.push(pixelIndex - width);
  }

  if (changed) ctx.putImageData(imageData, 0, 0);
  return changed;
}

function attachMaskCanvasListeners(card) {
  const canvas = ensureMaskCanvasElement(card);
  if (!canvas || canvas.dataset.boundMask) return;
  canvas.dataset.boundMask = '1';
  let drawing = false;
  let lastPoint = null;
  let strokeStartSnapshot = '';
  let strokeErases = false;
  let suppressRightContextMenu = false;
  const suppressContextMenu = (event) => {
    if (maskModeActive && suppressRightContextMenu) event.preventDefault();
  };
  canvas.addEventListener('contextmenu', (event) => {
    if (maskModeActive) event.preventDefault();
  });
  canvas.addEventListener('pointerenter', (event) => {
    if (maskModeActive && currentMaskTool === 'brush') showBrushCursor(canvas, event);
  });
  canvas.addEventListener('pointerleave', () => {
    hideBrushCursor();
  });
  canvas.addEventListener('pointerdown', (event) => {
    if (!maskModeActive) return;
    if (![0, 2].includes(event.button)) return;
    if (currentMaskTool === 'brush') showBrushCursor(canvas, event);
    closeMaskSizePopover();
    closeMaskFillTolerancePopover();
    event.preventDefault();
    event.stopPropagation();
    const index = parseInt(canvas.dataset.index, 10);
    const point = maskPointFromEvent(index, event);
    if (!point) return;
    if (currentMaskTool === 'fill') {
      const eraseFill = event.button === 2;
      if (eraseFill) {
        suppressRightContextMenu = true;
        document.addEventListener('contextmenu', suppressContextMenu, true);
        window.setTimeout(() => {
          suppressRightContextMenu = false;
          document.removeEventListener('contextmenu', suppressContextMenu, true);
        }, 350);
      }
      const before = snapshotMaskCanvas(index);
      if (fillMaskArea(index, point, eraseFill)) pushMaskUndoSnapshot(index, before);
      return;
    }
    drawing = true;
    lastPoint = point;
    strokeErases = currentMaskTool === 'brush' && event.button === 2;
    if (strokeErases) {
      suppressRightContextMenu = true;
      document.addEventListener('contextmenu', suppressContextMenu, true);
    }
    strokeStartSnapshot = snapshotMaskCanvas(index);
    canvas.setPointerCapture?.(event.pointerId);
    drawMaskPoint(index, point, null, strokeErases);
  });
  canvas.addEventListener('pointermove', (event) => {
    if (maskModeActive && currentMaskTool === 'brush') showBrushCursor(canvas, event);
    if (!drawing || !maskModeActive) return;
    event.preventDefault();
    const index = parseInt(canvas.dataset.index, 10);
    const point = maskPointFromEvent(index, event);
    if (!point) return;
    drawMaskPoint(index, point, lastPoint, strokeErases);
    lastPoint = point;
  });
  const finish = async (event) => {
    if (!drawing) return;
    drawing = false;
    canvas.releasePointerCapture?.(event.pointerId);
    const index = parseInt(canvas.dataset.index, 10);
    pushMaskUndoSnapshot(index, strokeStartSnapshot);
    strokeStartSnapshot = '';
    if (strokeErases) {
      window.setTimeout(() => {
        suppressRightContextMenu = false;
        document.removeEventListener('contextmenu', suppressContextMenu, true);
      }, 350);
      strokeErases = false;
    }
  };
  canvas.addEventListener('pointerup', finish);
  canvas.addEventListener('pointercancel', finish);
}

async function setMaskMode(active, purpose = 'training') {
  const nextActive = !!active;
  const nextPurpose = purpose === 'watermark' ? 'watermark' : 'training';
  if (nextActive === maskModeActive && (!nextActive || nextPurpose === maskModePurpose)) return;
  if (nextActive && maskModeActive && nextPurpose !== maskModePurpose) {
    if (maskModePurpose === 'training' && Array.from(maskStates.values()).some(state => state?.dirty)) {
      await appAlert('Save or reset the unsaved training masks before opening the watermark editor.');
      return;
    }
    if (maskModePurpose === 'watermark' && Array.from(watermarkMaskStates.values()).some(state => state?.dirty)) {
      const discard = await appConfirm('Discard the unapplied watermark selections and switch modes?');
      if (!discard) return;
    }
    await setMaskMode(false, maskModePurpose);
  }
  const btn = document.getElementById('maskModeBtn');
  if (nextActive) {
    if (!HAS_OPEN_FOLDER) {
      await appAlert('Open a folder before using Masking mode.');
      return;
    }
    btn?.setAttribute('aria-busy', 'true');
    if (nextPurpose === 'training') {
      try {
        const res = await fetch('/ensure_masks', { method: 'POST' });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || 'Failed to create masks.');
        if (data.message) setStatusbarMessage(data.message);
      } catch (err) {
        await appAlert(err?.message || 'Failed to enter Masking mode.');
        btn?.removeAttribute('aria-busy');
        return;
      }
    }
    maskModePurpose = nextPurpose;
    maskModeActive = true;
    document.body.classList.add('mask-mode');
    document.body.classList.toggle('watermark-mode', maskModePurpose === 'watermark');
    btn?.classList.toggle('is-active', maskModePurpose === 'training');
    btn?.setAttribute('aria-pressed', maskModePurpose === 'training' ? 'true' : 'false');
    updateMaskModeModalButton();
    updateWatermarkModeButton();
    setMaskTool(currentMaskTool);
    syncMaskSizeControls();
    syncMaskFillToleranceControls();
    document.querySelectorAll('.pair-card').forEach(card => {
      const index = parseInt(card.dataset.index, 10);
      if (Number.isFinite(index)) loadMaskCanvas(index, true);
    });
    updateAllMaskHistoryButtons();
    btn?.removeAttribute('aria-busy');
    return;
  }

  maskModeActive = false;
  closeMaskSizePopover();
  closeMaskFillTolerancePopover();
  hideBrushCursor();
  document.body.classList.remove('mask-mode');
  document.body.classList.remove('watermark-mode');
  btn?.classList.remove('is-active');
  btn?.setAttribute('aria-pressed', 'false');
  updateMaskModeModalButton();
  updateWatermarkModeButton();
  document.querySelectorAll('.undo-btn').forEach(button => { button.disabled = false; });
  document.querySelectorAll('.redo-btn').forEach(button => { button.disabled = true; });
  setStatusbarMessage(maskModePurpose === 'watermark' ? 'Watermark editor closed.' : 'Masking mode closed.');
  maskModePurpose = 'training';
}

function toggleMaskMode() {
  setMaskMode(!maskModeActive);
}

function isImageFile(file) {
  return !!file && (
    String(file.type || '').startsWith('image/') ||
    IMAGE_FILE_PATTERN.test(String(file.name || ''))
  );
}

function imageFilesFromFileList(fileList) {
  const seen = new Set();
  return Array.from(fileList || []).filter(file => {
    if (!isImageFile(file)) return false;
    const key = [
      String(file.name || ''),
      String(file.type || ''),
      String(file.size || 0),
      String(file.lastModified || 0),
    ].join('|');
    if (seen.has(key)) return false;
    seen.add(key);
    return true;
  });
}

function imageFilesFromClipboard(clipboardData) {
  const files = imageFilesFromFileList(clipboardData?.files || []);
  if (files.length) return files;

  const itemFiles = Array.from(clipboardData?.items || [])
    .filter(item => item.kind === 'file' && String(item.type || '').startsWith('image/'))
    .map(item => item.getAsFile())
    .filter(isImageFile);
  return itemFiles;
}

function isPngImageFile(file) {
  const name = String(file?.name || '');
  const type = String(file?.type || '').toLowerCase();
  return /\.png$/i.test(name) || type === 'image/png';
}

function nonPngImageFileCount(files) {
  return Array.from(files || []).filter(file => !isPngImageFile(file)).length;
}

function hasImageDrag(event) {
  const items = Array.from(event.dataTransfer?.items || []);
  if (items.some(item => item.kind === 'file' && String(item.type || '').startsWith('image/'))) {
    return true;
  }
  return Array.from(event.dataTransfer?.files || []).some(isImageFile);
}

async function uploadImageFiles(files, sourceLabel = 'selected', targetCategory = activeCategory) {
  const imageFiles = imageFilesFromFileList(files);
  if (!imageFiles.length) return;
  if (!HAS_OPEN_FOLDER) {
    await appAlert('Open a folder before adding images.');
    return;
  }
  if (typeof hasUnsavedChanges === 'function' && hasUnsavedChanges()) {
    const ok = await appConfirm('Add images? Unsaved edits remain in the current view.');
    if (!ok) return;
  }
  const convertToPng = nonPngImageFileCount(imageFiles) > 0;

  const formData = new FormData();
  imageFiles.forEach((file, i) => {
    const fallbackName = sourceLabel === 'pasted' ? `pasted_image_${i + 1}.png` : `image_${i + 1}.png`;
    formData.append('images', file, file.name || fallbackName);
  });
  formData.append('category', categoryExists(targetCategory) ? targetCategory : 'Undefined');
  formData.append('convert_to_png', convertToPng ? '1' : '0');

  try {
    const res = await fetch('/upload_images', {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      body: formData,
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      await appAlert(data.error || 'Failed to add images.');
      return;
    }
    const added = data.added || [];
    const imported = await importRenderedPairCards(added);
    if (added.length && imported.length !== added.length) {
      suppressBeforeUnload = true;
      window.location.reload();
      return;
    }
    if (data.message) setStatusbarMessage(data.message);
  } catch (err) {
    await appAlert(`Failed to add images: ${err}`);
  }
}

let imageDragDepth = 0;
document.addEventListener('dragenter', (event) => {
  if (!hasImageDrag(event)) return;
  imageDragDepth += 1;
  dropPasteOverlay?.classList.add('show');
});
document.addEventListener('dragover', (event) => {
  if (!hasImageDrag(event)) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
});
document.addEventListener('dragleave', (event) => {
  if (!hasImageDrag(event)) return;
  imageDragDepth = Math.max(0, imageDragDepth - 1);
  if (imageDragDepth === 0) dropPasteOverlay?.classList.remove('show');
});
document.addEventListener('dragend', () => {
  imageDragDepth = 0;
  dropPasteOverlay?.classList.remove('show');
});
document.addEventListener('drop', async (event) => {
  if (!hasImageDrag(event)) return;
  event.preventDefault();
  imageDragDepth = 0;
  dropPasteOverlay?.classList.remove('show');
  await uploadImageFiles(event.dataTransfer?.files || [], 'dropped', activeCategory);
});
document.addEventListener('paste', async (event) => {
  const files = imageFilesFromClipboard(event.clipboardData);
  if (!files.length) return;
  event.preventDefault();
  await uploadImageFiles(files, 'pasted', activeCategory);
});

let suppressBeforeUnload = false;
document.body.classList.add('dark');
localStorage.setItem('caption_app_theme', 'dark');

function loadToolsSettings() {
  const regexChecked = localStorage.getItem('caption_app_use_regex');
  if (regexChecked !== null) {
    const el = document.getElementById('sr_use_regex');
    if (el) el.checked = regexChecked === '1';
  }
}

function saveToolsSettings() {
  const el = document.getElementById('sr_use_regex');
  if (!el) return;
  localStorage.setItem('caption_app_use_regex', el.checked ? '1' : '0');
}

function setTextHeight(value) {
  textHeight = value;
  categoryTextHeights.clear();
  localStorage.setItem('caption_app_text_height', String(value));
  document.getElementById('textHeightSlider').value = value;
  document.getElementById('textHeightValue').textContent = value;
  applyTextHeightToCards(Array.from(document.querySelectorAll('.pair-card')), value);
  CATEGORY_DEFS.forEach(category => updateCategorySizingControls(category.name));
}
setTextHeight(textHeight);
document.getElementById('textHeightSlider').addEventListener('input', e => setTextHeight(parseInt(e.target.value, 10)));

function setImageHeight(value) {
  imageHeight = value;
  categoryImageHeights.clear();
  const cardMinWidth = cardMinWidthForImageHeight(value);
  localStorage.setItem('caption_app_image_height', String(value));
  document.getElementById('imageHeightSlider').value = value;
  document.getElementById('imageHeightValue').textContent = value;
  document.documentElement.style.setProperty('--card-min-width', cardMinWidth + 'px');
  document.querySelectorAll('.category-window .grid').forEach(grid => {
    grid.style.removeProperty('--card-min-width');
  });
  applyImageHeightToCards(Array.from(document.querySelectorAll('.pair-card')), value);
  if (maskModeActive) {
    requestAnimationFrame(() => {
      document.querySelectorAll('.pair-card').forEach(card => {
        const index = parseInt(card.dataset.index, 10);
        if (Number.isFinite(index)) positionMaskCanvas(index);
      });
    });
  }
  CATEGORY_DEFS.forEach(category => updateCategorySizingControls(category.name));
}
setImageHeight(imageHeight);
document.getElementById('imageHeightSlider').addEventListener('input', e => setImageHeight(parseInt(e.target.value, 10)));
document.getElementById('popupMaskSizeSlider')?.addEventListener('input', e => {
  setCurrentMaskSize(e.target.value);
});
document.getElementById('popupMaskFeatherSlider')?.addEventListener('input', e => {
  setCurrentMaskFeather(e.target.value);
});
document.getElementById('popupMaskFillToleranceSlider')?.addEventListener('input', e => {
  setCurrentMaskFillTolerance(e.target.value);
});
syncMaskSizeControls();
syncMaskFillToleranceControls();
document.addEventListener('pointerdown', event => {
  const popover = document.getElementById('maskSizePopover');
  const fillPopover = document.getElementById('maskFillTolerancePopover');
  const sizeOpen = popover?.classList.contains('open');
  const fillOpen = fillPopover?.classList.contains('open');
  if (!sizeOpen && !fillOpen) return;
  if (popover?.contains(event.target) || fillPopover?.contains(event.target)) return;
  if (event.target.closest?.('.mask-brush-btn')) return;
  if (event.target.closest?.('.mask-fill-btn')) return;
  closeMaskSizePopover();
  closeMaskFillTolerancePopover();
});
window.addEventListener('resize', () => positionMaskSizePopover(getMaskSizePopoverAnchor()));
window.addEventListener('scroll', () => positionMaskSizePopover(getMaskSizePopoverAnchor()), true);
window.addEventListener('resize', () => positionMaskFillTolerancePopover(getMaskFillTolerancePopoverAnchor()));
window.addEventListener('scroll', () => positionMaskFillTolerancePopover(getMaskFillTolerancePopoverAnchor()), true);

async function convertImagesToPngRequest() {
    const res = await fetch('/convert_images_to_png', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json', 'X-Requested-With': 'XMLHttpRequest' }
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Convert failed');
    return data;
}

document.getElementById('convertBtn')?.addEventListener('click', async () => {
  const ok = await appConfirm('Convert images to uncompressed PNG?');
  if (!ok) return;
  try {
    const data = await convertImagesToPngRequest();
    const convertedPairs = data.converted_pairs || [];
    const oldNames = convertedPairs.map(item => item.old_name).filter(Boolean);
    const newNames = convertedPairs.map(item => item.new_name).filter(Boolean);
    if (newNames.length) {
      await importRenderedPairCards(newNames);
      oldNames.forEach(imgName => {
        document.querySelector(`.pair-card[data-img="${CSS.escape(imgName)}"]`)?.remove();
        selectedImgNames.delete(imgName);
      });
      updateAllWindowStatuses();
      refreshDesktopCategoryCountsFromCards();
      updateSaveAllButtonState();
    }
    setStatusbarMessage(data.message || 'Convert complete.');
  } catch (e) {
    await appAlert(e?.message || 'Convert failed');
  }
});

document.getElementById('openFileManagerBtn')?.addEventListener('click', async () => {
  try {
    const res = await fetch('/open_in_file_manager', {
      headers: {
        'X-Requested-With': 'XMLHttpRequest',
        'Accept': 'application/json'
      }
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      await appAlert(data.error || 'Failed to open folder.');
    }
  } catch (e) {
    await appAlert('Failed to open folder.');
  }
});

function categoriesVisible() {
  return true;
}

function applyCategoryVisibility() {
  document.body.classList.toggle('category-hidden', !categoriesVisible());
}

applyCategoryVisibility();

document.querySelectorAll('input[name="crop_base"]').forEach(r => {
  const saved = localStorage.getItem('caption_app_crop_base');
  if (saved && r.value === saved) {
    r.checked = true;
    currentCropBase = parseInt(saved, 10);
  }
  r.addEventListener('change', () => {
    currentCropBase = parseInt(r.value, 10);
    localStorage.setItem('caption_app_crop_base', r.value);
    updateDimsColors();
    document.querySelectorAll('.pair-card').forEach(card => {
      const index = parseInt(card.dataset.index, 10);
      const state = ensureState(index);
      if (state.crop) {
        if (!state.ratioLocked) state.crop = snapCropToAllowed(index, state.crop);
        renderImageTransform(index);
        renderCrop(index);
        markUnsaved(index);
      }
    });
  });
});

function getAllowedBuckets() { return BUCKET_OPTIONS[String(currentCropBase)] || []; }

function getBucketStatus(width, height) {
  const key = `${width}x${height}`;
  const selected = new Set(getAllowedBuckets().map(([w, h]) => `${w}x${h}`));
  if (selected.has(key)) return 'selected';
  for (const buckets of Object.values(BUCKET_OPTIONS || {})) {
    if ((buckets || []).some(([w, h]) => `${w}x${h}` === key)) return 'other';
  }
  return 'invalid';
}

function getBucketBaseForResolution(width, height) {
  const key = `${width}x${height}`;
  for (const [base, buckets] of Object.entries(BUCKET_OPTIONS || {})) {
    if ((buckets || []).some(([w, h]) => `${w}x${h}` === key)) return base;
  }
  return '';
}

function getCardsInCategory(category) {
  return Array.from(document.querySelectorAll(`.category-window[data-category="${CSS.escape(category)}"] .pair-card`));
}

function getUnsavedCardIndexes(cards = null) {
  const out = [];
  (cards || Array.from(document.querySelectorAll('.pair-card'))).forEach(card => {
    const index = parseInt(card.dataset.index, 10);
    const ta = card.querySelector('.caption-textarea');
    const state = ensureState(index);
    const captionChanged = (ta.value !== (ta.dataset.original ?? ''));
    const cropChanged = !!state.crop;
    const transformChanged = !!state.rotation || state.flipH || state.flipV;
    const maskChanged = !!maskStates.get(index)?.dirty;
    const watermarkChanged = watermarkResults.has(index);
    if (captionChanged || cropChanged || transformChanged || maskChanged || watermarkChanged) out.push(index);
  });
  return out;
}

function estimateCaptionTokens(text) {
  const value = String(text || '').trim();
  if (!value) return 0;
  return (value.match(/\p{L}+|\p{N}+|[^\s\p{L}\p{N}]/gu) || []).length;
}

function updateCaptionStats(ta) {
  if (!ta) return;
  const card = ta.closest('.pair-card');
  if (!card) return;
  const chars = String(ta.value || '').length;
  const tokens = estimateCaptionTokens(ta.value);
  const charEl = card.querySelector('.caption-char-count');
  const tokenEl = card.querySelector('.caption-token-count');
  if (charEl) charEl.textContent = `${chars} chars`;
  if (tokenEl) tokenEl.textContent = `${tokens} tokens`;
}

function updateAllCaptionStats() {
  document.querySelectorAll('.caption-textarea').forEach(updateCaptionStats);
}

function markUnsaved(index) {
  const card = document.querySelector(`.pair-card[data-index="${index}"]`);
  if (!card) return;
  const ta = card.querySelector('.caption-textarea');
  const dot = document.getElementById(`status-dot-${index}`);
  const label = document.getElementById(`unsaved-label-${index}`);
  const saveBtn = document.getElementById(`save-btn-${index}`);
  const state = ensureState(index);
  const unsaved =
    (ta.value !== (ta.dataset.original ?? '')) ||
    !!state.crop ||
    !!state.rotation ||
    state.flipH ||
    state.flipV ||
    !!maskStates.get(index)?.dirty ||
    watermarkResults.has(index);

  card.classList.toggle('unsaved', unsaved);
  ta.classList.toggle('unsaved', ta.value !== (ta.dataset.original ?? ''));
  dot.classList.toggle('unsaved', unsaved);
  label.classList.toggle('show', unsaved);
  saveBtn.classList.toggle('unsaved', unsaved);
  saveBtn.classList.toggle('upscale-warning', !!state.upscale);
  updateMaskHistoryButtons(index);
  updateCaptionStats(ta);
  updateSaveAllButtonState();
}

function updateSaveAllButtonState() {
  const saveAllBtn = document.getElementById('saveAllBtn');
  if (!saveAllBtn) return;
  saveAllBtn.classList.toggle('has-unsaved', hasUnsavedChanges());
}

function updateCardIdentity(card, pair) {
  const oldIndex = parseInt(card.dataset.index, 10);
  let index = Number.parseInt(pair.index, 10);
  if (!Number.isFinite(index)) index = getNextCardIndex();
  const conflictingStage = document.getElementById(`crop-stage-${index}`);
  if (conflictingStage && !card.contains(conflictingStage)) {
    index = getNextCardIndex();
  }
  const imgName = pair.img_name;
  const category = pair.category || 'Undefined';

  card.dataset.index = String(index);
  card.dataset.img = imgName;
  card.dataset.category = category;

  const filenameEl = card.querySelector('.filename');
  if (filenameEl) {
    filenameEl.dataset.index = String(index);
    filenameEl.textContent = imgName;
    filenameEl.title = 'Double-click to rename';
  }

  const unsavedLabel = card.querySelector('.unsaved-label');
  if (unsavedLabel) {
    unsavedLabel.id = `unsaved-label-${index}`;
    unsavedLabel.classList.remove('show');
  }
  const statusDot = card.querySelector('.status-dot');
  if (statusDot) {
    statusDot.id = `status-dot-${index}`;
    statusDot.classList.remove('unsaved');
  }

  const dimsBadge = card.querySelector('.dims-badge');
  if (dimsBadge) {
    dimsBadge.id = `dims-badge-${index}`;
    dimsBadge.dataset.width = pair.width;
    dimsBadge.dataset.height = pair.height;
  }

  const stage = card.querySelector('.crop-stage');
  if (stage) {
    stage.id = `crop-stage-${index}`;
    stage.dataset.index = String(index);
    stage.dataset.width = pair.width;
    stage.dataset.height = pair.height;
  }

  const cropImg = card.querySelector('.crop-stage img');
  if (cropImg) {
    cropImg.id = `crop-image-${index}`;
    cropImg.alt = `crop ${imgName}`;
    cropImg.src = `/image/${encodeURIComponent(imgName)}?t=${Date.now()}`;
  }

  const maskCanvas = card.querySelector('.mask-canvas');
  if (maskCanvas) {
    maskCanvas.id = `mask-canvas-${index}`;
    maskCanvas.dataset.index = String(index);
    maskCanvasLoaded.delete(imgName);
  }

  const overlay = card.querySelector('.crop-overlay');
  if (overlay) overlay.id = `crop-overlay-${index}`;
  const cropBox = card.querySelector('.crop-box');
  if (cropBox) cropBox.id = `crop-box-${index}`;
  const cropLabel = card.querySelector('.crop-label');
  if (cropLabel) {
    cropLabel.id = `crop-label-${index}`;
    cropLabel.textContent = 'No crop selected';
  }

  const rotateSlider = card.querySelector('.rotate-slider');
  if (rotateSlider) {
    rotateSlider.id = `rotate-slider-${index}`;
    rotateSlider.dataset.index = String(index);
    rotateSlider.value = '0';
  }
  const rotateValue = card.querySelector('.rotate-value');
  if (rotateValue) {
    rotateValue.id = `rotate-value-${index}`;
    rotateValue.textContent = '0°';
  }

  card.querySelectorAll('[data-index]').forEach(el => { el.dataset.index = String(index); });
  card.querySelectorAll('[data-img]').forEach(el => { el.dataset.img = imgName; });

  const saveBtn = card.querySelector('.save-btn');
  if (saveBtn) {
    saveBtn.id = `save-btn-${index}`;
    saveBtn.classList.remove('unsaved', 'upscale-warning');
  }

  const ta = card.querySelector('.caption-textarea');
  if (ta) {
    ta.dataset.index = String(index);
    ta.dataset.img = imgName;
    ta.dataset.original = pair.text || '';
    ta.value = pair.text || '';
    ta.style.height = `${getCategoryTextHeight(card.dataset.category)}px`;
    ta.classList.remove('unsaved');
  }

  if (Number.isFinite(oldIndex) && oldIndex !== index) {
    cropStates.delete(oldIndex);
    maskStates.delete(oldIndex);
    watermarkMaskStates.delete(oldIndex);
    watermarkResults.delete(oldIndex);
  }
  cropStates.set(index, { crop: null, upscale: false, rotation: 0, flipH: false, flipV: false, ratioLocked: false, lockedAspect: null });
  maskStates.set(index, { undo: [], redo: [], dirty: false, savedSnapshot: '' });
  watermarkMaskStates.delete(index);
  watermarkResults.delete(index);
  card.classList.remove('unsaved');
  if (card.isConnected) {
    updateDimsColors();
    renderImageTransform(index);
    renderCrop(index);
    markUnsaved(index);
  }
}

function getNextCardIndex() {
  const indexes = Array.from(document.querySelectorAll('.pair-card'))
    .map(card => parseInt(card.dataset.index, 10))
    .filter(Number.isFinite);
  return indexes.length ? Math.max(...indexes) + 1 : 0;
}

function resetClonedCardBindings(card) {
  card.classList.remove('selected');
  card.querySelectorAll('[data-bound], [data-bound-input], [data-bound-click], [data-bound-dblclick], [data-bound-mask]').forEach(el => {
    delete el.dataset.bound;
    delete el.dataset.boundInput;
    delete el.dataset.boundClick;
    delete el.dataset.boundDblclick;
    delete el.dataset.boundMask;
  });
}

function attachCropImageLoadListener(img) {
  if (!img || img.dataset.boundLoad) return;
  img.dataset.boundLoad = '1';
  img.addEventListener('load', () => {
    const m = img.id.match(/crop-image-(\d+)/);
    if (m) {
      const index = parseInt(m[1], 10);
      renderImageTransform(index);
      renderCrop(index);
      if (maskModeActive) loadMaskCanvas(index);
    }
  });
}

function prepareInsertedPairCard(card) {
  if (!card) return;
  resetClonedCardBindings(card);
  const ta = card.querySelector('.caption-textarea');
  if (ta) {
    const original = decodeCaptionFieldValue(ta.dataset.original ?? "");
    ta.dataset.original = original;
    ta.value = decodeCaptionFieldValue(ta.value);
    if (ta.value !== original) ta.value = original;
  }
  attachCardEventListeners(card);
  attachCropImageLoadListener(card.querySelector('.crop-stage img'));
  applyCategorySizingToCard(card);
  const index = parseInt(card.dataset.index, 10);
  if (Number.isFinite(index)) {
    ensureState(index);
    renderImageTransform(index);
    renderCrop(index);
    markUnsaved(index);
  }
}

function refreshDesktopCategoryCountsFromCards() {
  const counts = new Map();
  document.querySelectorAll('.pair-card').forEach(card => {
    const category = categoryExists(card.dataset.category) ? card.dataset.category : 'Undefined';
    if (!counts.has(category)) counts.set(category, new Set());
    if (card.dataset.img) counts.get(category).add(card.dataset.img);
  });
  document.querySelectorAll('.desktop-folder').forEach(icon => {
    const count = counts.get(icon.dataset.category)?.size || 0;
    const countEl = icon.querySelector('.desktop-folder-count');
    if (countEl) countEl.textContent = `${count} images`;
  });
}

async function importRenderedPairCards(imgNames) {
  const wanted = new Set(imgNames || []);
  if (!wanted.size) return [];
  const res = await fetch(window.location.href, {
    headers: { 'X-Requested-With': 'XMLHttpRequest' },
    cache: 'no-store',
  });
  if (!res.ok) throw new Error(`HTTP ${res.status}`);
  const html = await res.text();
  const doc = new DOMParser().parseFromString(html, 'text/html');
  const imported = [];
  Array.from(doc.querySelectorAll('.pair-card')).forEach(card => {
    if (!wanted.has(card.dataset.img)) return;
    document.querySelector(`.pair-card[data-img="${CSS.escape(card.dataset.img)}"]`)?.remove();
    const category = categoryExists(card.dataset.category) ? card.dataset.category : 'Undefined';
    const grid = document.querySelector(`.category-window[data-category="${CSS.escape(category)}"] .grid`);
    if (!grid) return;
    const nextCard = document.importNode(card, true);
    grid.append(nextCard);
    prepareInsertedPairCard(nextCard);
    imported.push(nextCard);
  });
  updateDimsColors();
  updateAllCaptionStats();
  updateSaveAllButtonState();
  updateAllWindowStatuses();
  refreshDesktopCategoryCountsFromCards();
  return imported;
}

async function handleDeleteButton(btn, event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  const card = btn.closest('.pair-card');
  const img = card?.dataset.img || btn.dataset.img;
  const index = parseInt(card?.dataset.index || btn.dataset.index, 10);
  if (!img) {
    await appAlert('Delete failed: missing image name.');
    return;
  }
  if (!await appConfirm(`Delete ${img} and its caption?`)) return;

  btn.disabled = true;

  try {
    const res = await fetch('/delete_pair', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ img_name: img }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.error || 'Delete failed');
    }

    cropStates.delete(index);
    maskStates.delete(index);
    watermarkMaskStates.delete(index);
    watermarkResults.delete(index);

    selectedImgNames.delete(img);
    card?.remove();
    updateAllWindowStatuses();
    refreshDesktopCategoryCountsFromCards();
    updateSaveAllButtonState();
    setStatusbarMessage(`Deleted 1 card.`);
  } catch (err) {
    await appAlert(err?.message || 'Delete failed');
  } finally {
    if (btn.isConnected) btn.disabled = false;
  }
}

async function deleteSelectedCards() {
  const imgNames = Array.from(selectedImgNames).filter(Boolean);
  if (!imgNames.length) return;
  const label = imgNames.length === 1 ? imgNames[0] : `${imgNames.length} selected cards`;
  if (!await appConfirm(`Delete ${label} and their captions?`)) return;

  let deleted = 0;
  for (const imgName of imgNames) {
    const card = document.querySelector(`.pair-card[data-img="${CSS.escape(imgName)}"]`);
    const index = parseInt(card?.dataset.index || '', 10);
    try {
      const res = await fetch('/delete_pair', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ img_name: imgName }),
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Delete failed');
      if (Number.isFinite(index)) {
        cropStates.delete(index);
        maskStates.delete(index);
        watermarkMaskStates.delete(index);
        watermarkResults.delete(index);
      }
      selectedImgNames.delete(imgName);
      card?.remove();
      deleted += 1;
    } catch (err) {
      await appAlert(`Delete failed for ${imgName}: ${err?.message || err}`);
      break;
    }
  }
  clearPairClipboard();
  updateAllWindowStatuses();
  refreshDesktopCategoryCountsFromCards();
  updateSaveAllButtonState();
  if (deleted) setStatusbarMessage(`Deleted ${deleted} card${deleted === 1 ? '' : 's'}.`);
}

async function handleCloneButton(btn, event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  const img = btn.dataset.img;
  const sourceCard = btn.closest('.pair-card');
  btn.disabled = true;
  try {
    const res = await fetch('/clone_pair', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ img_name: img }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok || !data.pair) {
      throw new Error(data.error || 'Clone failed');
    }
    const newCard = sourceCard.cloneNode(true);
    resetClonedCardBindings(newCard);
    updateCardIdentity(newCard, data.pair);
    sourceCard.insertAdjacentElement('afterend', newCard);
    attachCardEventListeners(newCard);
    applyCategorySizingToCard(newCard);
    updateDimsColors();
    renderCrop(parseInt(newCard.dataset.index, 10));
    markUnsaved(parseInt(newCard.dataset.index, 10));
    updateAllWindowStatuses();
  } catch (err) {
    await appAlert(err?.message || 'Clone failed');
  } finally {
    btn.disabled = false;
  }
}

async function finishInlineRename(card, input, cancelOnly = false) {
  const oldName = card.dataset.img;
  const filenameEl = card.querySelector('.filename');
  if (!filenameEl) return;
  const dotIndex = oldName.lastIndexOf('.');
  const ext = dotIndex > 0 ? oldName.slice(dotIndex) : '';
  const stem = (input?.value || '').trim();

  if (cancelOnly) {
    filenameEl.textContent = oldName;
    return;
  }
  if (!stem) {
    await appAlert('Filename cannot be empty.');
    input?.focus();
    return;
  }
  if (/[\\/:*?"<>|]/.test(stem)) {
    await appAlert('Filename contains invalid characters.');
    input?.focus();
    return;
  }

  try {
    const res = await fetch('/rename_pair', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ img_name: oldName, new_stem: stem }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok || !data.pair) {
      throw new Error(data.error || 'Rename failed');
    }
    updateCardIdentity(card, data.pair);
    attachCardEventListeners(card);
  } catch (err) {
    await appAlert(err?.message || 'Rename failed');
    filenameEl.textContent = oldName;
  }
}

function selectOnlyPair(card) {
  if (!card) return;
  const imgName = card.dataset.img;
  if (!imgName) return;
  window.getSelection?.()?.removeAllRanges?.();
  selectedImgNames.clear();
  document.querySelectorAll('.pair-card.selected').forEach(item => item.classList.remove('selected'));
  selectedImgNames.add(imgName);
  card.classList.add('selected');
  updateAllWindowStatuses();
}

function togglePairSelection(card) {
  if (!card) return;
  const imgName = card.dataset.img;
  if (!imgName) return;
  window.getSelection?.()?.removeAllRanges?.();
  if (selectedImgNames.has(imgName)) {
    selectedImgNames.delete(imgName);
    card.classList.remove('selected');
  } else {
    selectedImgNames.add(imgName);
    card.classList.add('selected');
  }
  updateAllWindowStatuses();
}

function clearPairSelection() {
  selectedImgNames.clear();
  document.querySelectorAll('.pair-card.selected').forEach(card => card.classList.remove('selected'));
  updateAllWindowStatuses();
}

function selectAllVisiblePairCards() {
  selectedImgNames.clear();
  document.querySelectorAll('.pair-card.selected').forEach(card => card.classList.remove('selected'));
  const cards = Array.from(document.querySelectorAll('.pair-card')).filter(card => {
    if (card.closest('#pairPool')) return false;
    if (card.closest('[hidden]')) return false;
    return !!card.dataset.img;
  });
  cards.forEach(card => {
    selectedImgNames.add(card.dataset.img);
    card.classList.add('selected');
  });
  window.getSelection?.()?.removeAllRanges?.();
  updateAllWindowStatuses();
  return cards.length;
}

function clearCutPendingCards() {
  document.querySelectorAll('.pair-card.cut-pending').forEach(card => card.classList.remove('cut-pending'));
}

function markCutPendingCards(imgNames) {
  clearCutPendingCards();
  const names = new Set(imgNames || []);
  document.querySelectorAll('.pair-card').forEach(card => {
    if (names.has(card.dataset.img)) card.classList.add('cut-pending');
  });
}

function clearPairClipboard() {
  pairClipboard = null;
  clearCutPendingCards();
}

function beginInlineRename(card) {
  const filenameEl = card.querySelector('.filename');
  if (!filenameEl || filenameEl.querySelector('input')) return;

  const currentName = card.dataset.img || filenameEl.textContent.trim();
  const dotIndex = currentName.lastIndexOf('.');
  const stem = dotIndex > 0 ? currentName.slice(0, dotIndex) : currentName;
  const input = document.createElement('input');
  input.type = 'text';
  input.className = 'filename-input';
  input.value = stem;
  input.setAttribute('aria-label', 'Rename file');

  filenameEl.textContent = '';
  filenameEl.appendChild(input);
  input.focus();
  input.select();

  let done = false;
  const finish = async (cancelOnly = false) => {
    if (done) return;
    done = true;
    await finishInlineRename(card, input, cancelOnly);
  };

  input.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') {
      e.preventDefault();
      finish(false);
    } else if (e.key === 'Escape') {
      e.preventDefault();
      finish(true);
    }
  });
  input.addEventListener('blur', () => finish(true));
}

function attachCardEventListeners(card) {
  const index = parseInt(card.dataset.index, 10);
  const ta = card.querySelector('.caption-textarea');
  if (ta && !ta.dataset.boundInput) {
    ta.dataset.boundInput = '1';
    ta.addEventListener('input', () => markUnsaved(parseInt(ta.dataset.index, 10)));
    ta.style.height = `${getCategoryTextHeight(card.dataset.category)}px`;
    updateCaptionStats(ta);
  }

  const rotateSlider = card.querySelector('.rotate-slider');
  if (rotateSlider && !rotateSlider.dataset.boundInput) {
    rotateSlider.dataset.boundInput = '1';
    const apply = () => setRotation(parseInt(rotateSlider.dataset.index, 10), rotateSlider.value);
    rotateSlider.addEventListener('input', apply);
    rotateSlider.addEventListener('change', apply);
  }

  const autoCropBtn = card.querySelector('.auto-crop-btn');
  if (autoCropBtn && !autoCropBtn.dataset.boundClick) {
    autoCropBtn.dataset.boundClick = '1';
    autoCropBtn.addEventListener('click', () => autoCrop(parseInt(autoCropBtn.dataset.index, 10)));
  }

  const ratioLockBtn = card.querySelector('.ratio-lock-btn');
  if (ratioLockBtn && !ratioLockBtn.dataset.boundClick) {
    ratioLockBtn.dataset.boundClick = '1';
    ratioLockBtn.addEventListener('click', () => toggleRatioLock(parseInt(ratioLockBtn.dataset.index, 10)));
  }

  const maskSizeSlider = card.querySelector('.mask-size-slider');
  if (maskSizeSlider && !maskSizeSlider.dataset.boundInput) {
    maskSizeSlider.dataset.boundInput = '1';
    maskSizeSlider.value = String(currentMaskSize);
    maskSizeSlider.addEventListener('input', () => {
      currentMaskSize = Math.round(clamp(parseFloat(maskSizeSlider.value) || 32, 2, 160));
      syncMaskSizeControls();
    });
  }

  card.querySelectorAll('.mask-tool-btn').forEach(btn => {
    if (btn.dataset.boundClick) return;
    btn.dataset.boundClick = '1';
    btn.addEventListener('click', () => {
      setMaskTool(btn.dataset.tool);
      if (btn.dataset.tool === 'brush') {
        toggleMaskSizePopover(btn);
      } else if (btn.dataset.tool === 'fill') {
        toggleMaskFillTolerancePopover(btn);
      } else {
        closeMaskSizePopover();
        closeMaskFillTolerancePopover();
      }
    });
  });

  const autoMaskBtn = card.querySelector('.automask-btn');
  if (autoMaskBtn && !autoMaskBtn.dataset.boundClick) {
    autoMaskBtn.dataset.boundClick = '1';
    autoMaskBtn.addEventListener('click', () => autoMaskCard(parseInt(autoMaskBtn.dataset.index, 10)));
  }

  const watermarkApplyBtn = card.querySelector('.watermark-apply-btn');
  if (watermarkApplyBtn && !watermarkApplyBtn.dataset.boundClick) {
    watermarkApplyBtn.dataset.boundClick = '1';
    watermarkApplyBtn.addEventListener('click', () => applyWatermarkRemoval(parseInt(watermarkApplyBtn.dataset.index, 10)));
  }

  const undoBtn = card.querySelector('.undo-btn');
  if (undoBtn && !undoBtn.dataset.boundClick) {
    undoBtn.dataset.boundClick = '1';
    undoBtn.addEventListener('click', () => undoCard(parseInt(undoBtn.dataset.index, 10)));
  }

  const redoBtn = card.querySelector('.redo-btn');
  if (redoBtn && !redoBtn.dataset.boundClick) {
    redoBtn.dataset.boundClick = '1';
    redoBtn.addEventListener('click', () => redoMaskChange(parseInt(redoBtn.dataset.index, 10)));
  }

  const flipHBtn = card.querySelector('.flip-h-btn');
  if (flipHBtn && !flipHBtn.dataset.boundClick) {
    flipHBtn.dataset.boundClick = '1';
    flipHBtn.addEventListener('click', () => toggleFlip(parseInt(flipHBtn.dataset.index, 10), 'h'));
  }

  const flipVBtn = card.querySelector('.flip-v-btn');
  if (flipVBtn && !flipVBtn.dataset.boundClick) {
    flipVBtn.dataset.boundClick = '1';
    flipVBtn.addEventListener('click', () => toggleFlip(parseInt(flipVBtn.dataset.index, 10), 'v'));
  }

  const saveBtn = card.querySelector('.save-btn');
  if (saveBtn && !saveBtn.dataset.boundClick) {
    saveBtn.dataset.boundClick = '1';
    saveBtn.addEventListener('click', () => saveCard(parseInt(saveBtn.dataset.index, 10)));
  }

  const deleteBtn = card.querySelector('.delete-btn');
  if (deleteBtn && !deleteBtn.dataset.boundClick) {
    deleteBtn.dataset.boundClick = '1';
    deleteBtn.addEventListener('click', async (event) => { await handleDeleteButton(deleteBtn, event); });
  }

  const cloneBtn = card.querySelector('.clone-btn');
  if (cloneBtn && !cloneBtn.dataset.boundClick) {
    cloneBtn.dataset.boundClick = '1';
    cloneBtn.addEventListener('click', async (event) => { await handleCloneButton(cloneBtn, event); });
  }

  const bindZoomButton = (selector, handler) => {
    const btn = card.querySelector(selector);
    if (!btn || btn.dataset.boundClick) return;
    btn.dataset.boundClick = '1';
    btn.addEventListener('click', event => {
      event.preventDefault();
      event.stopPropagation();
      handler();
    });
  };
  bindZoomButton('.zoom-in-btn', () => applyMediaZoom(index, getMediaZoom(index) * 1.25));
  bindZoomButton('.zoom-out-btn', () => applyMediaZoom(index, getMediaZoom(index) / 1.25));
  bindZoomButton('.zoom-default-btn', () => applyMediaZoom(index, 1));
  bindZoomButton('.zoom-actual-btn', () => setActualMediaZoom(index));

  const cardHead = card.querySelector('.card-head');
  if (cardHead && !cardHead.dataset.boundClick) {
    cardHead.dataset.boundClick = '1';
    cardHead.addEventListener('click', (event) => {
      if (event.target.closest('input, button')) return;
      if (event.ctrlKey || event.metaKey) {
        togglePairSelection(card);
      } else {
        selectOnlyPair(card);
      }
    });
  }

  const filenameEl = card.querySelector('.filename');
  if (filenameEl && !filenameEl.dataset.boundDblclick) {
    filenameEl.dataset.boundDblclick = '1';
    filenameEl.addEventListener('dblclick', () => beginInlineRename(card));
  }

  ensureState(index);
  attachMaskCanvasListeners(card);
  if (maskModeActive) loadMaskCanvas(index);
  renderImageTransform(index);
  attachCropper(index);
  markUnsaved(index);
}


function updateDimsColors() {
  document.querySelectorAll('.dims-badge').forEach(el => {
    const width = parseInt(el.dataset.width, 10);
    const height = parseInt(el.dataset.height, 10);
    const status = getBucketStatus(width, height);
    const isSelected = status === 'selected';
    const isOther = status === 'other';
    const isInvalid = status === 'invalid';

    el.classList.toggle('ok', isSelected);
    el.classList.toggle('warn', isOther);
    el.classList.toggle('bad', isInvalid);

    const aspectLabel = isInvalid ? "???" : getAspectLabel(width, height);
    const otherBase = isOther ? getBucketBaseForResolution(width, height) : '';
    el.textContent = `${width}×${height} (${aspectLabel})${otherBase ? ` • ${otherBase}` : ''}`;
  });
}

function clamp(v, min, max) { return Math.max(min, Math.min(max, v)); }
const CROP_EPSILON = 0.01;

function nearlyEqual(a, b, epsilon = CROP_EPSILON) {
  return Math.abs(a - b) <= epsilon;
}

function cropAspect(crop) {
  if (!crop || !crop.w || !crop.h) return null;
  const ratio = crop.w / crop.h;
  return Number.isFinite(ratio) && ratio > 0 ? ratio : null;
}

function clampCropToImage(index, crop) {
  const stage = document.getElementById(`crop-stage-${index}`);
  if (!stage || !crop) return crop;
  const imgW = parseFloat(stage.dataset.width);
  const imgH = parseFloat(stage.dataset.height);
  let w = clamp(crop.w, 1, imgW);
  let h = clamp(crop.h, 1, imgH);
  let x = clamp(crop.x, 0, Math.max(0, imgW - w));
  let y = clamp(crop.y, 0, Math.max(0, imgH - h));
  return { ...crop, x, y, w, h };
}

function applyLockedAspectToResize(index, orig, handle, p, aspect) {
  const stage = document.getElementById(`crop-stage-${index}`);
  const imgW = parseFloat(stage.dataset.width);
  const imgH = parseFloat(stage.dataset.height);
  const minSize = 1;
  let x = orig.x;
  let y = orig.y;
  let w = orig.w;
  let h = orig.h;

  if (handle.includes('e')) w = Math.max(minSize, p.x - orig.x);
  if (handle.includes('w')) w = Math.max(minSize, orig.x + orig.w - p.x);
  if (handle.includes('s')) h = Math.max(minSize, p.y - orig.y);
  if (handle.includes('n')) h = Math.max(minSize, orig.y + orig.h - p.y);

  const horizontal = handle.includes('e') || handle.includes('w');
  const vertical = handle.includes('n') || handle.includes('s');
  if (horizontal && !vertical) h = w / aspect;
  else if (vertical && !horizontal) w = h * aspect;
  else if (w / h > aspect) h = w / aspect;
  else w = h * aspect;

  const maxW = handle.includes('w') ? orig.x + orig.w : imgW - orig.x;
  const maxH = handle.includes('n') ? orig.y + orig.h : imgH - orig.y;
  if (w > maxW) {
    w = maxW;
    h = w / aspect;
  }
  if (h > maxH) {
    h = maxH;
    w = h * aspect;
  }

  if (handle.includes('w')) x = orig.x + orig.w - w;
  if (handle.includes('n')) y = orig.y + orig.h - h;
  return clampCropToImage(index, { ...orig, x, y, w, h });
}

function createLockedAspectCrop(index, start, p, aspect, targetW = null, targetH = null) {
  const stage = document.getElementById(`crop-stage-${index}`);
  const imgW = parseFloat(stage.dataset.width);
  const imgH = parseFloat(stage.dataset.height);
  const dx = p.x - start.x;
  const dy = p.y - start.y;
  let w = Math.max(1, Math.abs(dx));
  let h = Math.max(1, Math.abs(dy));
  if (w / h > aspect) h = w / aspect;
  else w = h * aspect;

  const maxW = dx < 0 ? start.x : imgW - start.x;
  const maxH = dy < 0 ? start.y : imgH - start.y;
  if (w > maxW) {
    w = Math.max(1, maxW);
    h = w / aspect;
  }
  if (h > maxH) {
    h = Math.max(1, maxH);
    w = h * aspect;
  }

  const x = dx < 0 ? start.x - w : start.x;
  const y = dy < 0 ? start.y - h : start.y;
  const crop = {
    x,
    y,
    w,
    h,
    targetW: targetW || Math.max(1, Math.round(w)),
    targetH: targetH || Math.max(1, Math.round(h)),
  };
  return clampCropToImage(index, crop);
}

function getMediaZoom(index) {
  const stage = document.getElementById(`crop-stage-${index}`);
  const value = parseFloat(stage?.dataset.zoom || '1');
  return Number.isFinite(value) && value > 0 ? value : 1;
}

function getMediaPan(index) {
  const stage = document.getElementById(`crop-stage-${index}`);
  return {
    x: parseFloat(stage?.dataset.panX || '0') || 0,
    y: parseFloat(stage?.dataset.panY || '0') || 0,
  };
}

function setMediaPan(index, x, y) {
  const stage = document.getElementById(`crop-stage-${index}`);
  if (!stage) return;
  stage.dataset.panX = String(Number(x) || 0);
  stage.dataset.panY = String(Number(y) || 0);
}

function clampMediaPan(index, width, height) {
  const stage = document.getElementById(`crop-stage-${index}`);
  const pan = getMediaPan(index);
  if (!stage) return { x: 0, y: 0 };
  const allowFitPan = stage.dataset.allowFitPan === '1';
  const maxX = Math.max(0, (width - stage.clientWidth) / 2, allowFitPan ? stage.clientWidth / 2 : 0);
  const maxY = Math.max(0, (height - stage.clientHeight) / 2, allowFitPan ? stage.clientHeight / 2 : 0);
  const clamped = {
    x: clamp(pan.x, -maxX, maxX),
    y: clamp(pan.y, -maxY, maxY),
  };
  setMediaPan(index, clamped.x, clamped.y);
  stage.classList.toggle('pan-available', maxX > 0 || maxY > 0);
  return clamped;
}

function showZoomReadout(index) {
  const readout = document.getElementById(`zoom-readout-${index}`);
  if (!readout) return;
  const stage = document.getElementById(`crop-stage-${index}`);
  const img = document.getElementById(`crop-image-${index}`);
  const box = getRenderedImageBox(index);
  const naturalW = parseFloat(stage?.dataset.width || '0') || img?.naturalWidth || 1;
  readout.textContent = `${Math.round((box.width / Math.max(naturalW, 1)) * 100)}%`;
  readout.style.width = 'max-content';
  readout.style.whiteSpace = 'nowrap';
  const readoutW = readout.offsetWidth || readout.getBoundingClientRect().width || 0;
  const readoutH = readout.offsetHeight || readout.getBoundingClientRect().height || 0;
  const inset = 4;
  readout.style.left = `${clamp(box.left + box.width - readoutW - inset, inset, stage.clientWidth - readoutW - inset)}px`;
  readout.style.top = `${clamp(box.top + inset, inset, stage.clientHeight - readoutH - inset)}px`;
  readout.style.right = 'auto';
  readout.classList.add('show');
  clearTimeout(readout._zoomTimer);
  readout._zoomTimer = setTimeout(() => readout.classList.remove('show'), 850);
}

function applyMediaZoom(index, zoom, show = true) {
  const stage = document.getElementById(`crop-stage-${index}`);
  if (!stage) return;
  const next = clamp(Number(zoom) || 1, 0.25, 8);
  stage.dataset.zoom = String(next);
  stage.dataset.allowFitPan = '0';
  if (next === 1) setMediaPan(index, 0, 0);
  renderImageTransform(index);
  renderCrop(index);
  positionMaskCanvas(index);
  if (show) showZoomReadout(index);
}

function setActualMediaZoom(index) {
  const stage = document.getElementById(`crop-stage-${index}`);
  const img = document.getElementById(`crop-image-${index}`);
  if (!stage || !img) return;
  const currentZoom = getMediaZoom(index);
  stage.dataset.zoom = '1';
  const box = getRenderedImageBox(index);
  const naturalW = parseFloat(stage.dataset.width) || img.naturalWidth || 1;
  const naturalH = parseFloat(stage.dataset.height) || img.naturalHeight || 1;
  const zoom = Math.max(naturalW / Math.max(box.width, 1), naturalH / Math.max(box.height, 1));
  stage.dataset.zoom = String(currentZoom);
  applyMediaZoom(index, zoom);
}

function getRenderedImageBox(index) {
  const stage = document.getElementById(`crop-stage-${index}`);
  const img = document.getElementById(`crop-image-${index}`);

  const stageW = stage.clientWidth;
  const stageH = stage.clientHeight;

  const naturalW = parseFloat(stage.dataset.width) || img.naturalWidth || 1;
  const naturalH = parseFloat(stage.dataset.height) || img.naturalHeight || 1;

  const scale = Math.min(stageW / naturalW, stageH / naturalH);
  const zoom = getMediaZoom(index);
  const width = naturalW * scale * zoom;
  const height = naturalH * scale * zoom;
  const pan = clampMediaPan(index, width, height);
  const left = (stageW - width) / 2 + pan.x;
  const top = (stageH - height) / 2 + pan.y;

  return { left, top, width, height };
}

function snapCropToAllowed(index, crop) {
  const stage = document.getElementById(`crop-stage-${index}`);
  const imgW = parseFloat(stage.dataset.width);
  const imgH = parseFloat(stage.dataset.height);
  const allowed = getAllowedBuckets();
  if (!allowed.length || !crop) return crop;

  for (const [bw, bh] of allowed) {
    if (nearlyEqual(crop.w, bw) && nearlyEqual(crop.h, bh)) {
      const x = clamp(crop.x, 0, Math.max(0, imgW - bw));
      const y = clamp(crop.y, 0, Math.max(0, imgH - bh));
      return { x, y, w: bw, h: bh, targetW: bw, targetH: bh };
    }
  }

  const currentRatio = crop.w / crop.h;
  let best = allowed[0];
  let bestDiff = Infinity;

  for (const [bw, bh] of allowed) {
    const diff = Math.abs((bw / bh) - currentRatio);
    if (diff < bestDiff) {
      bestDiff = diff;
      best = [bw, bh];
    }
  }

  const [targetW, targetH] = best;
  const ratio = targetW / targetH;
  let x = crop.x, y = crop.y, w = crop.w, h = crop.h;

  if (w / h > ratio) w = h * ratio;
  else h = w / ratio;

  if (nearlyEqual(w, targetW) && nearlyEqual(h, targetH)) {
    w = targetW;
    h = targetH;
  }

  if (x + w > imgW) x = imgW - w;
  if (y + h > imgH) y = imgH - h;
  x = clamp(x, 0, imgW - w);
  y = clamp(y, 0, imgH - h);

  return { x, y, w, h, targetW, targetH };
}

function getAspectLabel(width, height) {
  const gcd = (a, b) => b ? gcd(b, a % b) : a;
  const g = gcd(width, height) || 1;
  const rw = Math.round(width / g);
  const rh = Math.round(height / g);
  return `${rw}:${rh}`;
}

function renderCrop(index) {
  const state = ensureState(index);
  const box = document.getElementById(`crop-box-${index}`);
  const label = document.getElementById(`crop-label-${index}`);
  const stage = document.getElementById(`crop-stage-${index}`);

  if (!state.crop) {
    box.classList.remove('show', 'upscale', 'valid');
    label.textContent = 'No crop selected';
    renderRatioLockButton(index);
    return;
  }

  const renderBox = getRenderedImageBox(index);
  const imgW = parseFloat(stage.dataset.width);
  const imgH = parseFloat(stage.dataset.height);

  const scaleX = renderBox.width / imgW;
  const scaleY = renderBox.height / imgH;

  box.style.left = (renderBox.left + state.crop.x * scaleX) + 'px';
  box.style.top = (renderBox.top + state.crop.y * scaleY) + 'px';
  box.style.width = (state.crop.w * scaleX) + 'px';
  box.style.height = (state.crop.h * scaleY) + 'px';
  box.classList.add('show');

  const upscale = (state.crop.w + CROP_EPSILON) < state.crop.targetW || (state.crop.h + CROP_EPSILON) < state.crop.targetH;
  state.upscale = upscale;
  box.classList.toggle('upscale', upscale);
  box.classList.toggle('valid', !upscale);

  const cropAspect = getAspectLabel(state.crop.targetW, state.crop.targetH);
  label.textContent = `${state.crop.targetW}×${state.crop.targetH} (${cropAspect})${upscale ? ' • upscale' : ''}`;
  cropStates.set(index, state);
  renderRatioLockButton(index);
}

function renderImageTransform(index) {
  const img = document.getElementById(`crop-image-${index}`);
  if (!img) return;
  const state = ensureState(index);
  const box = getRenderedImageBox(index);

  img.style.left = `${box.left}px`;
  img.style.top = `${box.top}px`;
  img.style.width = `${box.width}px`;
  img.style.height = `${box.height}px`;
  img.style.right = 'auto';
  img.style.bottom = 'auto';
  img.style.objectFit = 'fill';

  const transforms = [];
  if (state.flipH) transforms.push('scaleX(-1)');
  if (state.flipV) transforms.push('scaleY(-1)');
  if (state.rotation) transforms.push(`rotate(${state.rotation}deg)`);

  img.style.transform = transforms.length ? transforms.join(' ') : 'none';
  img.style.transformOrigin = 'center center';
  const maskCanvas = getMaskCanvas(index);
  if (maskCanvas) {
    maskCanvas.style.transform = transforms.length ? transforms.join(' ') : 'none';
    maskCanvas.style.transformOrigin = 'center center';
  }

  const slider = document.getElementById(`rotate-slider-${index}`);
  const valueEl = document.getElementById(`rotate-value-${index}`);
  if (slider) slider.value = String(state.rotation);
  if (valueEl) valueEl.textContent = `${state.rotation}°`;
}

function setRotation(index, value) {
  const state = ensureState(index);
  state.rotation = Math.round(clamp(parseFloat(value) || 0, -180, 180));
  cropStates.set(index, state);
  renderImageTransform(index);
  renderCrop(index);
  markUnsaved(index);
}

function renderRatioLockButton(index) {
  const btn = document.querySelector(`.ratio-lock-btn[data-index="${index}"]`);
  if (!btn) return;
  const state = ensureState(index);
  btn.classList.toggle('active', !!state.ratioLocked);
}

function toggleRatioLock(index) {
  const state = ensureState(index);
  state.ratioLocked = !state.ratioLocked;
  state.lockedAspect = state.ratioLocked ? cropAspect(state.crop) : null;
  if (state.ratioLocked && !state.lockedAspect) {
    const stage = document.getElementById(`crop-stage-${index}`);
    const imgW = parseFloat(stage?.dataset?.width) || 1;
    const imgH = parseFloat(stage?.dataset?.height) || 1;
    state.lockedAspect = imgW / imgH;
  }
  cropStates.set(index, state);
  renderRatioLockButton(index);
}

function stagePointFromEvent(index, e) {
  const stage = document.getElementById(`crop-stage-${index}`);
  const stageRect = stage.getBoundingClientRect();
  const renderBox = getRenderedImageBox(index);
  const imgW = parseFloat(stage.dataset.width);
  const imgH = parseFloat(stage.dataset.height);

  const stageX = e.clientX - stageRect.left;
  const stageY = e.clientY - stageRect.top;

  const clampedX = clamp(stageX, renderBox.left, renderBox.left + renderBox.width);
  const clampedY = clamp(stageY, renderBox.top, renderBox.top + renderBox.height);

  const x = ((clampedX - renderBox.left) / renderBox.width) * imgW;
  const y = ((clampedY - renderBox.top) / renderBox.height) * imgH;

  return {
    x: clamp(x, 0, imgW),
    y: clamp(y, 0, imgH)
  };
}

function beginMediaPan(index, e) {
  const stage = document.getElementById(`crop-stage-${index}`);
  if (!stage || e.button !== 1) return false;
  const box = getRenderedImageBox(index);
  const canPan = box.width > stage.clientWidth + 1 || box.height > stage.clientHeight + 1 || getMediaZoom(index) <= 1.0001;
  if (!canPan) return false;
  e.preventDefault();
  e.stopPropagation();
  if (getMediaZoom(index) <= 1.0001) stage.dataset.allowFitPan = '1';
  const start = { x: e.clientX, y: e.clientY };
  const startPan = getMediaPan(index);
  stage.classList.add('panning');
  const move = moveEvent => {
    moveEvent.preventDefault();
    setMediaPan(index, startPan.x + moveEvent.clientX - start.x, startPan.y + moveEvent.clientY - start.y);
    renderImageTransform(index);
    renderCrop(index);
    positionMaskCanvas(index);
  };
  const stop = () => {
    stage.classList.remove('panning');
    window.removeEventListener('mousemove', move);
    window.removeEventListener('mouseup', stop);
  };
  window.addEventListener('mousemove', move);
  window.addEventListener('mouseup', stop);
  return true;
}

function attachCropper(index) {
  const stage = document.getElementById(`crop-stage-${index}`);
  const box = document.getElementById(`crop-box-${index}`);
  if (!stage || stage.dataset.bound === '1') return;
  stage.dataset.bound = '1';

  ensureState(index);
  let drag = null;

  stage.addEventListener('contextmenu', (e) => {
    if (maskModeActive) return;
    e.preventDefault();
    const state = ensureState(index);
    state.crop = null;
    state.upscale = false;
    cropStates.set(index, state);
    renderCrop(index);
    markUnsaved(index);
  });
  stage.addEventListener('auxclick', (e) => {
    if (e.button === 1) e.preventDefault();
  });

  stage.addEventListener('mousedown', (e) => {
    if (beginMediaPan(index, e)) return;
    if (maskModeActive) return;
    if (e.button !== 0) return;

    const state = ensureState(index);
    const start = stagePointFromEvent(index, e);
    const handle = e.target?.dataset?.handle;

    if (handle && state.crop) {
      drag = { mode: 'resize', handle, start, orig: { ...state.crop } };
      e.preventDefault();
      return;
    }

    if (e.target === box && state.crop) {
      drag = { mode: 'move', start, orig: { ...state.crop } };
      e.preventDefault();
      return;
    }

    if (state.crop) {
      const c = state.crop;
      if (
        start.x >= c.x && start.x <= c.x + c.w &&
        start.y >= c.y && start.y <= c.y + c.h
      ) {
        drag = { mode: 'move', start, orig: { ...c } };
        e.preventDefault();
        return;
      }
    }

    if (e.target.id === `crop-overlay-${index}` || e.target === stage || e.target.id === `crop-image-${index}`) {
      drag = { mode: 'new', start, orig: null };
      state.crop = {
        x: start.x,
        y: start.y,
        w: 1,
        h: 1,
        targetW: currentCropBase,
        targetH: currentCropBase
      };
      cropStates.set(index, state);
      renderCrop(index);
      markUnsaved(index);
      e.preventDefault();
    }
  });

  window.addEventListener('mousemove', (e) => {
    if (!drag) return;

    const state = ensureState(index);
    const p = stagePointFromEvent(index, e);
    let crop = state.crop ? { ...state.crop } : null;

    if (drag.mode === 'new') {
      const x1 = Math.min(drag.start.x, p.x);
      const x2 = Math.max(drag.start.x, p.x);
      const y1 = Math.min(drag.start.y, p.y);
      const y2 = Math.max(drag.start.y, p.y);
      if (state.ratioLocked && state.lockedAspect) {
        crop = createLockedAspectCrop(index, drag.start, p, state.lockedAspect);
      } else {
        crop = {
          x: x1,
          y: y1,
          w: Math.max(1, x2 - x1),
          h: Math.max(1, y2 - y1),
          targetW: currentCropBase,
          targetH: currentCropBase
        };
      }
    } else if (drag.mode === 'move' && crop) {
      const dx = p.x - drag.start.x;
      const dy = p.y - drag.start.y;
      const imgW = parseFloat(stage.dataset.width);
      const imgH = parseFloat(stage.dataset.height);
      crop.x = clamp(drag.orig.x + dx, 0, imgW - drag.orig.w);
      crop.y = clamp(drag.orig.y + dy, 0, imgH - drag.orig.h);
      crop.w = drag.orig.w;
      crop.h = drag.orig.h;
    } else if (drag.mode === 'resize' && crop) {
      const o = drag.orig;
      let x = o.x, y = o.y, w = o.w, h = o.h;

      if (state.ratioLocked && state.lockedAspect) {
        crop = applyLockedAspectToResize(index, o, drag.handle, p, state.lockedAspect);
      } else {
        if (drag.handle.includes('w')) {
          x = Math.min(p.x, o.x + o.w - 1);
          w = o.x + o.w - x;
        }
        if (drag.handle.includes('e')) {
          w = Math.max(1, p.x - o.x);
        }
        if (drag.handle.includes('n')) {
          y = Math.min(p.y, o.y + o.h - 1);
          h = o.y + o.h - y;
        }
        if (drag.handle.includes('s')) {
          h = Math.max(1, p.y - o.y);
        }

        crop = { ...crop, x, y, w, h };
      }
    }

    if (state.ratioLocked) {
      if (!state.lockedAspect) state.lockedAspect = cropAspect(crop);
      crop = clampCropToImage(index, crop);
    } else {
      crop = snapCropToAllowed(index, crop);
    }
    state.crop = crop;
    cropStates.set(index, state);
    renderCrop(index);
    markUnsaved(index);
  });

  window.addEventListener('mouseup', () => {
    if (!drag) return;
    drag = null;
  });

  window.addEventListener('resize', () => {
    renderImageTransform(index);
    renderCrop(index);
    if (maskModeActive) positionMaskCanvas(index);
  });
}

function autoCrop(index) {
  const stage = document.getElementById(`crop-stage-${index}`);
  const imgW = parseFloat(stage.dataset.width);
  const imgH = parseFloat(stage.dataset.height);
  const allowed = getAllowedBuckets();
  if (!allowed.length) return;

  for (const [bw, bh] of allowed) {
    if (nearlyEqual(imgW, bw) && nearlyEqual(imgH, bh)) {
      const state = ensureState(index);
      state.crop = { x: 0, y: 0, w: bw, h: bh, targetW: bw, targetH: bh };
      state.upscale = false;
      if (state.ratioLocked) state.lockedAspect = bw / bh;
      cropStates.set(index, state);
      renderCrop(index);
      markUnsaved(index);
      return;
    }
  }

  let best = null;
  let bestArea = -1;

  for (const [bw, bh] of allowed) {
    const ratio = bw / bh;
    let w = imgW;
    let h = w / ratio;
    if (h > imgH) {
      h = imgH;
      w = h * ratio;
    }
    if (nearlyEqual(w, bw) && nearlyEqual(h, bh)) {
      w = bw;
      h = bh;
    }
    const area = w * h;
    if (area > bestArea) {
      bestArea = area;
      best = { x: (imgW - w) / 2, y: (imgH - h) / 2, w, h, targetW: bw, targetH: bh };
    }
  }

  const state = ensureState(index);
  state.crop = best;
  if (state.ratioLocked && best) state.lockedAspect = best.w / best.h;
  cropStates.set(index, state);
  renderCrop(index);
  markUnsaved(index);
}

function autoCropAll() {
  document.querySelectorAll('.pair-card').forEach(card => {
    const index = parseInt(card.dataset.index, 10);
    autoCrop(index);
  });
}

function autoCropCategory(category) {
  getCardsInCategory(category).forEach(card => {
    const index = parseInt(card.dataset.index, 10);
    autoCrop(index);
  });
}

function undoCard(index) {
  if (maskModeActive) {
    undoMaskChange(index);
    return;
  }
  const card = document.querySelector(`.pair-card[data-index="${index}"]`);
  const ta = card.querySelector('.caption-textarea');
  const state = ensureState(index);

  ta.value = ta.dataset.original ?? "";
  state.crop = null;
  state.upscale = false;
  state.rotation = 0;
  state.flipH = false;
  state.flipV = false;
  state.ratioLocked = false;
  state.lockedAspect = null;

  cropStates.set(index, state);
  renderImageTransform(index);
  renderCrop(index);
  markUnsaved(index);
}

function toggleFlip(index, axis) {
  const state = ensureState(index);
  if (axis === 'h') state.flipH = !state.flipH;
  if (axis === 'v') state.flipV = !state.flipV;
  cropStates.set(index, state);
  renderImageTransform(index);
  renderCrop(index);
  markUnsaved(index);
}

function getCardByIndex(index) {
  return document.querySelector(`.pair-card[data-index="${index}"]`);
}

function clearWatermarkMask(index) {
  const canvas = prepareMaskCanvasForEdit(index);
  if (!canvas) return;
  const ctx = canvas.getContext('2d', { willReadFrequently: true });
  ctx.fillStyle = '#000';
  ctx.fillRect(0, 0, canvas.width, canvas.height);
  clearMaskHistory(index, snapshotMaskCanvas(index));
}

async function applyWatermarkRemoval(index) {
  if (!maskModeActive || maskModePurpose !== 'watermark') return;
  const card = getCardByIndex(index);
  const canvas = prepareMaskCanvasForEdit(index);
  const button = card?.querySelector('.watermark-apply-btn');
  if (!card || !canvas) return;
  button?.setAttribute('aria-busy', 'true');
  if (button) button.disabled = true;
  setStatusbarMessage(`Removing watermark from ${card.dataset.img}...`);
  try {
    const res = await fetch('/remove_watermark', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        img_name: card.dataset.img,
        mask_data: canvas.toDataURL('image/png'),
        source_data: watermarkResults.get(index) || '',
        backend: document.getElementById('watermarkBackend')?.value || 'lama',
        radius: document.getElementById('watermarkRadius')?.value || '3',
        expand_pixels: document.getElementById('watermarkExpandPixels')?.value || '2',
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok || !data.result_data) throw new Error(data.error || 'Watermark removal failed.');
    watermarkResults.set(index, data.result_data);
    const image = document.getElementById(`crop-image-${index}`);
    if (image) image.src = data.result_data;
    clearWatermarkMask(index);
    markUnsaved(index);
    setStatusbarMessage(`Watermark removal preview ready for ${card.dataset.img}. Save the card to write it to disk.`);
  } catch (err) {
    await appAlert(err?.message || 'Watermark removal failed.');
    setStatusbarMessage(err?.message || 'Watermark removal failed.');
  } finally {
    button?.removeAttribute('aria-busy');
    if (button) button.disabled = false;
  }
}

async function saveCard(index) {
  const card = document.querySelector(`.pair-card[data-index="${index}"]`);
  const ta = card.querySelector('.caption-textarea');
  const imgName = ta.dataset.img;
  const state = ensureState(index);
  const maskState = getMaskState(index);

  if (state.upscale) {
    const ok = await appConfirm('This crop will upscale the image and may reduce quality. Continue?');
    if (!ok) return;
  }

  if (maskModePurpose === 'training' && maskState.dirty) {
    const maskSaved = await saveMaskCanvas(index);
    if (!maskSaved) return;
  }

  const payload = {
    index,
    img_name: imgName,
    caption: ta.value,
    caption_format: ta.dataset.captionFormat || 'standard_text',
    crop: state.crop ? {
      x: state.crop.x,
      y: state.crop.y,
      w: state.crop.w,
      h: state.crop.h,
      targetW: state.crop.targetW,
      targetH: state.crop.targetH,
    } : null,
    transforms: {
      rotation: state.rotation || 0,
      flipH: !!state.flipH,
      flipV: !!state.flipV,
    },
    watermark_result: watermarkResults.get(index) || '',
  };

  const res = await fetch('/save_pair', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();

  if (!res.ok || !data.ok) {
    await appAlert(data.error || 'Save failed');
    return;
  }

  ta.dataset.original = ta.value;
  watermarkResults.delete(index);

  if (data.updated_pair) {
    const badge = document.getElementById(`dims-badge-${index}`);
    badge.dataset.width = data.updated_pair.width;
    badge.dataset.height = data.updated_pair.height;

    const stage = document.getElementById(`crop-stage-${index}`);
    stage.dataset.width = data.updated_pair.width;
    stage.dataset.height = data.updated_pair.height;
    setMediaPan(index, 0, 0);

    const cropImg = document.getElementById(`crop-image-${index}`);
    cropImg.src = `/image/${imgName}?t=${Date.now()}`;
    maskCanvasLoaded.delete(imgName);
    if (maskModeActive) loadMaskCanvas(index, true);
  }

  state.crop = null;
  state.upscale = false;
  state.rotation = 0;
  state.flipH = false;
  state.flipV = false;
  renderImageTransform(index);
  cropStates.set(index, state);
  renderCrop(index);
  requestAnimationFrame(() => {
    renderImageTransform(index);
    renderCrop(index);
    positionMaskCanvas(index);
  });
  updateDimsColors();
  markUnsaved(index);
  setStatusbarMessage(`Saved ${imgName}.`);
}

async function saveAllCards() {
  const indexes = getUnsavedCardIndexes();
  if (!indexes.length) return;

  const hasUpscale = indexes.some(i => ensureState(i).upscale);
  if (hasUpscale) {
    const ok = await appConfirm('Some selected crops will upscale the image and may reduce quality. Continue saving all?');
    if (!ok) return;
  }

  for (const i of indexes) {
    await saveCard(i);
  }
  updateSaveAllButtonState();
  setStatusbarMessage(`Saved ${indexes.length} image${indexes.length === 1 ? '' : 's'}.`);
}

async function saveCategoryCards(category) {
  const indexes = getUnsavedCardIndexes(getCardsInCategory(category));
  if (!indexes.length) return;

  const hasUpscale = indexes.some(i => ensureState(i).upscale);
  if (hasUpscale) {
    const ok = await appConfirm('Some selected crops in this category will upscale the image and may reduce quality. Continue saving?');
    if (!ok) return;
  }

  for (const i of indexes) {
    await saveCard(i);
  }
  updateSaveAllButtonState();
  setStatusbarMessage(`Saved ${indexes.length} image${indexes.length === 1 ? '' : 's'} in ${category}.`);
}

async function renameAllPairs() {
  const prefix = await appPrompt('Enter filename prefix for all images and captions:');
  if (prefix === null) return;

  const res = await fetch('/rename_all_pairs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prefix })
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    await appAlert(data.error || 'Rename all failed');
    return;
  }
  suppressBeforeUnload = true;
  window.location.reload();
}

async function renameCategoryPairs(category) {
  const prefix = await appPrompt(`Enter filename prefix for ${category}:`);
  if (prefix === null) return;

  saveOpenCategoryWindows();
  localStorage.setItem(activeCategoryWindowStorageKey(), category);

  const res = await fetch('/rename_all_pairs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prefix, category })
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    await appAlert(data.error || 'Rename category failed');
    return;
  }
  suppressBeforeUnload = true;
  window.location.reload();
}

async function confirmReplace() {
  const scope = toolsCategoryScope ? `the ${toolsCategoryScope} category` : 'all caption cards';
  return appConfirm(`Apply this search/replace to ${scope}? Changes will remain unsaved until you use Save.`);
}

document.querySelectorAll('.pair-card').forEach(card => attachCardEventListeners(card));
document.getElementById('autoCropAllBtn')?.addEventListener('click', autoCropAll);
document.getElementById('maskModeBtn')?.addEventListener('click', openMaskModal);
document.getElementById('openFolderForm')?.addEventListener('submit', async event => {
  event.preventDefault();
  try {
    const res = await fetch('/open_folder', {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Open folder failed.');
    if (data.selected) {
      const nonPngCount = Number(data.non_png_count || 0);
      if (nonPngCount > 0) {
        const ok = await appConfirm(`Convert ${nonPngCount} non-PNG image${nonPngCount === 1 ? '' : 's'} to uncompressed PNG?`);
        if (ok) {
          try {
            setStatusbarMessage('Converting images to PNG...');
            await convertImagesToPngRequest();
          } catch (convertErr) {
            await appAlert(convertErr?.message || 'Convert failed');
          }
        }
      }
      suppressBeforeUnload = true;
      window.location.assign('/');
    }
  } catch (err) {
    await appAlert(err?.message || 'Open folder failed.');
  }
});
document.getElementById('refreshFolderBtn')?.addEventListener('click', async () => {
  if (hasUnsavedChanges()) {
    const ok = await appConfirm('Refresh folder and discard unsaved changes?');
    if (!ok) return;
  }
  try {
    setStatusbarMessage('Refreshing folder...');
    const res = await fetch('/refresh_folder', {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Refresh failed.');
    suppressBeforeUnload = true;
    window.location.assign('/');
  } catch (err) {
    await appAlert(err?.message || 'Refresh failed.');
  }
});
document.getElementById('saveAllBtn').addEventListener('click', saveAllCards);
document.getElementById('renameAllBtn')?.addEventListener('click', renameAllPairs);
document.getElementById('resetAllBtn')?.addEventListener('click', async () => {
  if (!hasUnsavedChanges()) return;
  const ok = await appConfirm('Reset all unsaved captions, crops, and transforms?');
  if (!ok) return;
  resetAllUnsavedChanges();
});

document.querySelectorAll('form').forEach(form => {
  form.addEventListener('submit', () => {
    suppressBeforeUnload = true;
  });
});

document.querySelectorAll('a').forEach(link => {
  link.addEventListener('click', () => {
    suppressBeforeUnload = true;
  });
});

document.querySelectorAll('.backup-form').forEach(form => {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    suppressBeforeUnload = false;
    const ok = await appConfirm('Create a backup of all image and caption pairs?');
    if (!ok) return;
    try {
      const res = await fetch(form.action || '/backup', {
        method: 'GET',
        headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
      });
      const data = await res.json();
      if (!res.ok || !data.ok) {
        await appAlert(data.error || 'Backup failed.');
        return;
      }
      setStatusbarMessage(data.message || 'Backup complete.');
    } catch (err) {
      await appAlert(`Backup failed: ${err}`);
    }
  });
});

document.getElementById('closeFolderForm')?.addEventListener('submit', async (e) => {
  if (hasUnsavedChanges()) {
    e.preventDefault();
    const ok = await appConfirm('Close the folder and discard all unsaved changes?');
    if (!ok) {
      return;
    }
    suppressBeforeUnload = true;
    e.currentTarget.submit();
    return;
  }
  suppressBeforeUnload = true;
});

const joyModalBackdrop = document.getElementById('joyModalBackdrop');
const openJoyModalBtn = document.getElementById('openJoyModalBtn');
const closeJoyModalBtn = document.getElementById('closeJoyModalBtn');
const joyLogBox = document.getElementById('joyLogBox');
const joyAutoScroll = document.getElementById('joyAutoScroll');
const joyProgressLabel = document.getElementById('joyProgressLabel');
const joyProgressPercent = document.getElementById('joyProgressPercent');
const joyProgressFill = document.getElementById('joyProgressFill');
if (joyAutoScroll) {
  joyAutoScroll.checked = localStorage.getItem('caption_app_joy_autoscroll') !== '0';
  joyAutoScroll.addEventListener('change', () => {
    localStorage.setItem('caption_app_joy_autoscroll', joyAutoScroll.checked ? '1' : '0');
  });
}

let joyStatusPollingEnabled = false;
let joyStatusPollTimer = null;

function scheduleJoyStatusPoll(delay = 1200) {
  if (joyStatusPollTimer) clearTimeout(joyStatusPollTimer);
  joyStatusPollTimer = setTimeout(pollJoyStatus, delay);
}

function updateJoyProgress(count = 0, total = 0) {
  const safeCount = Math.max(0, Number.isFinite(Number(count)) ? Number(count) : 0);
  const safeTotal = Math.max(0, Number.isFinite(Number(total)) ? Number(total) : 0);
  const pct = safeTotal > 0 ? Math.min(100, Math.round((safeCount / safeTotal) * 100)) : 0;
  if (joyProgressLabel) {
    joyProgressLabel.textContent = safeTotal > 0
      ? `Captions: ${Math.min(safeCount, safeTotal)}/${safeTotal}`
      : `Captions: ${safeCount}`;
  }
  if (joyProgressPercent) joyProgressPercent.textContent = `${pct}%`;
  if (joyProgressFill) joyProgressFill.style.width = `${pct}%`;
}

function openJoyModal() {
  joyModalBackdrop?.classList.add('open');
  joyStatusPollingEnabled = true;
  pollJoyStatus();
}
function closeJoyModal() {
  joyModalBackdrop?.classList.remove('open');
}
openJoyModalBtn?.addEventListener('click', openJoyModal);
closeJoyModalBtn?.addEventListener('click', closeJoyModal);

const maskModalBackdrop = document.getElementById('maskModalBackdrop');
const closeMaskModalBtn = document.getElementById('closeMaskModalBtn');
const maskStatusText = document.getElementById('maskStatusText');
const maskLogBox = document.getElementById('maskLogBox');
const maskProgressLabel = document.getElementById('maskProgressLabel');
const maskProgressPercent = document.getElementById('maskProgressPercent');
const maskProgressFill = document.getElementById('maskProgressFill');
let maskRunActive = false;
let maskRunCancelled = false;
let maskCategoryOverride = '';

const MASK_DEFAULTS = {
  model: 'silueta',
  scope: 'active_category',
  post_process: true,
  expand_pixels: '0',
  feather_pixels: '0',
  opacity: '48',
  auto_scroll: true,
};

function updateMaskProgress(count = 0, total = 0) {
  const safeCount = Math.max(0, Number.isFinite(Number(count)) ? Number(count) : 0);
  const safeTotal = Math.max(0, Number.isFinite(Number(total)) ? Number(total) : 0);
  const pct = safeTotal > 0 ? Math.min(100, Math.round((safeCount / safeTotal) * 100)) : 0;
  if (maskProgressLabel) {
    maskProgressLabel.textContent = safeTotal > 0
      ? `Masks: ${Math.min(safeCount, safeTotal)}/${safeTotal}`
      : `Masks: ${safeCount}`;
  }
  if (maskProgressPercent) maskProgressPercent.textContent = `${pct}%`;
  if (maskProgressFill) maskProgressFill.style.width = `${pct}%`;
}

function appendMaskLog(text) {
  if (!maskLogBox) return;
  maskLogBox.textContent += String(text || '');
  if (document.getElementById('mask_auto_scroll')?.checked) {
    maskLogBox.scrollTop = maskLogBox.scrollHeight;
  }
}

function setMaskStatus(text) {
  if (maskStatusText) maskStatusText.textContent = text || '';
  setStatusbarMessage(text || '');
}

function openMaskModal(options = {}) {
  const category = typeof options.category === 'string' && categoryExists(options.category) ? options.category : '';
  maskCategoryOverride = category;
  if (category) {
    setActiveCategory(category);
    const scope = document.getElementById('mask_scope');
    if (scope) scope.value = 'active_category';
    saveMaskSettings();
  }
  maskModalBackdrop?.classList.add('open');
  updateMaskModeModalButton();
  updateMaskProgress(0, 0);
  if (category) setMaskStatus(`Mask target: ${category}.`);
}

async function openMaskModalForCategory(category) {
  openMaskModal({ category });
  await setMaskMode(true);
}

function closeMaskModal() {
  maskModalBackdrop?.classList.remove('open');
}

function getMaskSettings() {
  const opacityValue = Number.parseFloat(document.getElementById('mask_opacity')?.value);
  return {
    model: document.getElementById('mask_model')?.value || MASK_DEFAULTS.model,
    scope: document.getElementById('mask_scope')?.value || MASK_DEFAULTS.scope,
    post_process: !!document.getElementById('mask_post_process')?.checked,
    expand_pixels: Math.max(0, Math.min(256, Math.round(parseFloat(document.getElementById('mask_expand_pixels')?.value || '0') || 0))),
    feather_pixels: Math.max(0, Math.min(256, Math.round(parseFloat(document.getElementById('mask_feather_pixels')?.value || '0') || 0))),
    opacity: String(Math.max(0, Math.min(100, Math.round(Number.isFinite(opacityValue) ? opacityValue : Number(MASK_DEFAULTS.opacity))))),
    auto_scroll: !!document.getElementById('mask_auto_scroll')?.checked,
  };
}

function getCurrentMaskModel() {
  return getMaskSettings().model || MASK_DEFAULTS.model;
}

function saveMaskSettings() {
  localStorage.setItem('caption_app_mask_settings', JSON.stringify(getMaskSettings()));
}

function applyMaskOpacity() {
  const settings = getMaskSettings();
  const opacity = Math.max(0, Math.min(100, Number(settings.opacity) || 0));
  document.documentElement.style.setProperty('--mask-overlay-opacity', String(opacity / 100));
  const value = document.getElementById('mask_opacity_value');
  if (value) value.textContent = `${opacity}%`;
}

function loadMaskSettings() {
  try {
    const raw = localStorage.getItem('caption_app_mask_settings');
    const merged = { ...MASK_DEFAULTS, ...(raw ? JSON.parse(raw) : {}) };
    Object.entries(merged).forEach(([key, value]) => {
      const el = document.getElementById(`mask_${key}`);
      if (!el) return;
      if (el.type === 'checkbox') el.checked = !!value;
      else el.value = value;
    });
  } catch (e) {}
  applyMaskOpacity();
}

function resetMaskSettings() {
  Object.entries(MASK_DEFAULTS).forEach(([key, value]) => {
    const el = document.getElementById(`mask_${key}`);
    if (!el) return;
    if (el.type === 'checkbox') el.checked = !!value;
    else el.value = value;
  });
  applyMaskOpacity();
  saveMaskSettings();
}

function updateMaskModeModalButton() {
  const btn = document.getElementById('maskToggleModeBtn');
  if (!btn) return;
  const active = maskModeActive && maskModePurpose === 'training';
  btn.textContent = active ? 'Disable masking mode' : 'Enable masking mode';
  btn.classList.toggle('is-active', active);
  btn.setAttribute('aria-pressed', active ? 'true' : 'false');
}

const watermarkModalBackdrop = document.getElementById('watermarkModalBackdrop');

function updateWatermarkModeButton() {
  const btn = document.getElementById('watermarkToggleModeBtn');
  if (!btn) return;
  const active = maskModeActive && maskModePurpose === 'watermark';
  btn.textContent = active ? 'Disable watermark editor' : 'Enable watermark editor';
  btn.classList.toggle('is-active', active);
  btn.setAttribute('aria-pressed', active ? 'true' : 'false');
}

function updateWatermarkBackendUi() {
  const backend = document.getElementById('watermarkBackend')?.value || 'lama';
  const radiusLabel = document.getElementById('watermarkRadiusLabel');
  if (radiusLabel) radiusLabel.style.display = backend.startsWith('opencv_') ? '' : 'none';
  localStorage.setItem('dataprep_watermark_backend', backend);
  localStorage.setItem('dataprep_watermark_expand', document.getElementById('watermarkExpandPixels')?.value || '2');
  localStorage.setItem('dataprep_watermark_radius', document.getElementById('watermarkRadius')?.value || '3');
}

function openWatermarkModal() {
  watermarkModalBackdrop?.classList.add('open');
  updateWatermarkModeButton();
  updateWatermarkBackendUi();
}

function closeWatermarkModal() {
  watermarkModalBackdrop?.classList.remove('open');
}

function loadWatermarkSettings() {
  const backend = document.getElementById('watermarkBackend');
  const expansion = document.getElementById('watermarkExpandPixels');
  const radius = document.getElementById('watermarkRadius');
  if (backend) backend.value = localStorage.getItem('dataprep_watermark_backend') || 'lama';
  if (expansion) expansion.value = localStorage.getItem('dataprep_watermark_expand') || '2';
  if (radius) radius.value = localStorage.getItem('dataprep_watermark_radius') || '3';
  updateWatermarkBackendUi();
}

function uniqueCards(cards) {
  const seen = new Set();
  const out = [];
  cards.forEach(card => {
    const key = card?.dataset?.img || card?.dataset?.index;
    if (!key || seen.has(key)) return;
    seen.add(key);
    out.push(card);
  });
  return out;
}

function getMaskRunCards(scope) {
  if (scope === 'open_windows') {
    return uniqueCards(Array.from(document.querySelectorAll('.category-window .pair-card')));
  }
  if (scope === 'all') {
    return uniqueCards(Array.from(document.querySelectorAll('.pair-card')));
  }
  const targetCategory = maskCategoryOverride || activeCategory;
  return uniqueCards(Array.from(document.querySelectorAll('.pair-card')).filter(card => {
    const category = categoryExists(card.dataset.category) ? card.dataset.category : 'Undefined';
    return category === targetCategory;
  }));
}

function maskScopeLabel(scope) {
  if (scope === 'open_windows') return 'open category windows';
  if (scope === 'all') return 'all images';
  return maskCategoryOverride || activeCategory;
}

async function runMaskBatch() {
  if (maskRunActive) return;
  if (!HAS_OPEN_FOLDER) {
    await appAlert('Open a folder before running Auto mask.');
    return;
  }
  const settings = getMaskSettings();
  saveMaskSettings();
  await setMaskMode(true);
  if (!maskModeActive) return;

  const cards = getMaskRunCards(settings.scope);
  if (!cards.length) {
    await appAlert('No cards found for the selected mask scope.');
    return;
  }

  maskRunActive = true;
  maskRunCancelled = false;
  document.getElementById('maskStartBtn')?.setAttribute('disabled', 'disabled');
  updateMaskProgress(0, cards.length);
  if (maskLogBox) maskLogBox.textContent = '';
  setMaskStatus(`Mask: running ${cards.length} image(s) in ${maskScopeLabel(settings.scope)}.`);
  appendMaskLog(`Model: ${settings.model}\nScope: ${maskScopeLabel(settings.scope)}\nExpansion: ${settings.expand_pixels}px\nFeather: ${settings.feather_pixels}px\n\n`);

  let done = 0;
  let failed = 0;
  for (const card of cards) {
    if (maskRunCancelled) break;
    const index = parseInt(card.dataset.index, 10);
    if (!Number.isFinite(index)) continue;
    appendMaskLog(`Masking ${card.dataset.img}...\n`);
    const ok = await autoMaskCard(index, {
      silent: true,
      setMessage: false,
      model: settings.model,
      postProcessMask: settings.post_process,
      expandPixels: settings.expand_pixels,
      featherPixels: settings.feather_pixels,
    });
    if (ok) {
      const saved = await saveMaskCanvas(index);
      appendMaskLog(saved ? `Saved ${card.dataset.img}\n` : `Save failed: ${card.dataset.img}\n`);
      if (!saved) failed += 1;
    } else {
      failed += 1;
      appendMaskLog(`Failed: ${card.dataset.img} - ${autoMaskCard.lastError || 'Auto mask failed.'}\n`);
    }
    done += 1;
    updateMaskProgress(done, cards.length);
  }

  maskRunActive = false;
  document.getElementById('maskStartBtn')?.removeAttribute('disabled');
  const interrupted = maskRunCancelled;
  maskRunCancelled = false;
  const status = interrupted
    ? `Mask: interrupted at ${done}/${cards.length}.`
    : `Mask: finished ${done - failed}/${cards.length} image(s).`;
  setMaskStatus(status);
  appendMaskLog(`\n${status}\n`);
  updateAllWindowStatuses();
  refreshDesktopCategoryCountsFromCards();
  updateSaveAllButtonState();
}

loadMaskSettings();
updateMaskModeModalButton();
['mask_model','mask_scope','mask_post_process','mask_expand_pixels','mask_feather_pixels','mask_opacity','mask_auto_scroll'].forEach(id => {
  const el = document.getElementById(id);
  if (!el) return;
  const eventName = (el.type === 'checkbox' || el.tagName === 'SELECT') ? 'change' : 'input';
  el.addEventListener(eventName, () => {
    applyMaskOpacity();
    saveMaskSettings();
  });
});
document.getElementById('maskToggleModeBtn')?.addEventListener('click', () => toggleMaskMode());
document.getElementById('maskStartBtn')?.addEventListener('click', runMaskBatch);
document.getElementById('maskInterruptBtn')?.addEventListener('click', () => {
  maskRunCancelled = true;
  setMaskStatus('Mask: interrupt requested...');
});
document.getElementById('maskResetSettingsBtn')?.addEventListener('click', resetMaskSettings);
closeMaskModalBtn?.addEventListener('click', closeMaskModal);
document.getElementById('openWatermarkModalBtn')?.addEventListener('click', openWatermarkModal);
document.getElementById('closeWatermarkModalBtn')?.addEventListener('click', closeWatermarkModal);
document.getElementById('watermarkToggleModeBtn')?.addEventListener('click', async () => {
  const active = maskModeActive && maskModePurpose === 'watermark';
  await setMaskMode(!active, 'watermark');
  updateWatermarkModeButton();
});
['watermarkBackend', 'watermarkExpandPixels', 'watermarkRadius'].forEach(id => {
  document.getElementById(id)?.addEventListener('change', updateWatermarkBackendUi);
});
loadWatermarkSettings();

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeJoyModal();
    closeMaskModal();
    closeWatermarkModal();
    closeSummaryModal();
    closeToolsModal();
  }
});

const JOY_DEFAULTS = {
  backend: 'joycaption',
  caption_format: 'standard_text',
  quantization: 'Q4_K',
  caption_type: 'descriptive',
  caption_length: 'long',
  visionmaxres: '384',
  max_tokens: '0',
  temperature: '0.6',
  top_p: '0.9',
  extra_options: '',
  person_name: '',
  no_overwrite: false,
  append_existing: false,
  ideogram4_name: '',
  wd14_model: 'convnextv2',
  wd14_general_threshold: '0.35',
  wd14_character_threshold: '0.85',
  wd14_include_rating: false,
  wd14_include_characters: false,
  wd14_replace_underscores: true,
  qwen3vl_model: 'Qwen3-VL-4B-Instruct',
  qwen3vl_name: '',
  qwen3vl_system_prompt: `Create a natural-language image caption for LoRA training.

Write exactly one concise sentence. Start the caption with [name]. Use [name] as the subject name or training trigger, and mention [name] only once.

Describe only visible details in the image. Focus on expression, gaze, pose, hair, clothing, framing, setting, lighting, background, and image style when visible.

Write in natural language, not as comma-separated tags. Do not use bullet points. Do not invent details. Do not describe identity, age, ethnicity, personality, story, intent, body shape, or body proportions unless clearly required by the visible image.

Do not mention file names, metadata, resolution, image quality, camera model, or that this is an image.

Keep the caption short and direct, usually 12-30 words. Output only the caption.`,
  qwen3vl_temperature: '0.2',
  qwen3vl_max_tokens: '256',
  qwen3vl_max_image_side: '512',
  external_api_url: '',
  external_api_model: '',
  external_api_name: '',
  external_api_key: '',
  external_api_temperature: '0.2',
  external_api_max_tokens: '256',
  external_api_disable_thinking: true,
  external_api_system_prompt: `Create a natural-language image caption for LoRA training.

Write exactly one concise sentence. Start the caption with [name]. Use [name] as the subject name or training trigger, and mention [name] only once.

Describe only visible details in the image. Focus on expression, gaze, pose, hair, clothing, framing, setting, lighting, background, and image style when visible.

Write in natural language, not as comma-separated tags. Do not use bullet points. Do not invent details. Do not describe identity, age, ethnicity, personality, story, intent, body shape, or body proportions unless clearly required by the visible image.

Do not mention file names, metadata, resolution, image quality, camera model, or that this is an image.

Keep the caption short and direct, usually 12-30 words. Output only the caption.`,
  auto_scroll: true,
};


function updateCaptionBackendUI() {
  const backendSelect = document.getElementById('joy_backend');
  const captionFormat = document.getElementById('joy_caption_format')?.value || 'standard_text';
  const ideogramJson = captionFormat === 'ideogram4_json';
  if (ideogramJson && backendSelect && !['qwen3_vl', 'external_api'].includes(backendSelect.value)) {
    backendSelect.value = 'qwen3_vl';
  }
  if (backendSelect) {
    backendSelect.disabled = false;
    Array.from(backendSelect.options).forEach(option => {
      option.disabled = ideogramJson && !['qwen3_vl', 'external_api'].includes(option.value);
    });
  }
  const appendExisting = document.getElementById('joy_append_existing');
  if (ideogramJson && appendExisting) appendExisting.checked = false;
  if (appendExisting) appendExisting.disabled = ideogramJson;
  const qwenPrompt = document.getElementById('joy_qwen3vl_system_prompt');
  if (qwenPrompt) qwenPrompt.disabled = ideogramJson;
  const qwenName = document.getElementById('joy_qwen3vl_name');
  if (qwenName) qwenName.disabled = ideogramJson;
  const externalPrompt = document.getElementById('joy_external_api_system_prompt');
  if (externalPrompt) externalPrompt.disabled = ideogramJson;
  const externalName = document.getElementById('joy_external_api_name');
  if (externalName) externalName.disabled = ideogramJson;
  const backend = backendSelect?.value || 'joycaption';
  document.querySelectorAll('.joy-only').forEach(el => {
    el.style.display = backend === 'joycaption' ? '' : 'none';
  });
  document.querySelectorAll('.wd14-only').forEach(el => {
    el.style.display = backend === 'wd14' ? '' : 'none';
  });
  document.querySelectorAll('.qwen3vl-only').forEach(el => {
    el.style.display = backend === 'qwen3_vl' ? '' : 'none';
  });
  document.querySelectorAll('.qwen3vl-text-only').forEach(el => {
    el.style.display = backend === 'qwen3_vl' && !ideogramJson ? '' : 'none';
  });
  document.querySelectorAll('.external-api-only').forEach(el => {
    el.style.display = backend === 'external_api' ? '' : 'none';
  });
  document.querySelectorAll('.ideogram4-only').forEach(el => {
    el.style.display = ideogramJson ? '' : 'none';
  });
}

function getSelectedExtraOptions() {
  return Array.from(document.querySelectorAll('.joy-extra-option:checked')).map(el => el.value);
}

function updateJoyNameVisibility() {
  const show = getSelectedExtraOptions().includes('If there is a person/character in the image you must refer to them as {name}.');
  const wrap = document.getElementById('joy_name_wrap');
  if (wrap) wrap.style.display = show ? '' : 'none';
}

function joySettings() {
  return {
    backend: document.getElementById('joy_backend').value,
    caption_format: document.getElementById('joy_caption_format').value,
    quantization: document.getElementById('joy_quantization').value,
    caption_type: document.getElementById('joy_caption_type').value,
    caption_length: document.getElementById('joy_caption_length').value,
    visionmaxres: document.getElementById('joy_visionmaxres').value,
    max_tokens: document.getElementById('joy_max_tokens').value,
    temperature: document.getElementById('joy_temperature').value,
    top_p: document.getElementById('joy_top_p').value,
    extra_options: document.getElementById('joy_extra_options').value,
    extra_options_selected: getSelectedExtraOptions(),
    person_name: document.getElementById('joy_person_name').value,
    hf_token: document.getElementById('joy_hf_token').value,
    no_overwrite: document.getElementById('joy_no_overwrite').checked,
    append_existing: document.getElementById('joy_append_existing').checked,
    ideogram4_name: document.getElementById('joy_ideogram4_name').value,
    wd14_model: document.getElementById('joy_wd14_model').value,
    wd14_general_threshold: document.getElementById('joy_wd14_general_threshold').value,
    wd14_character_threshold: document.getElementById('joy_wd14_character_threshold').value,
    wd14_include_rating: document.getElementById('joy_wd14_include_rating').checked,
    wd14_include_characters: document.getElementById('joy_wd14_include_characters').checked,
    wd14_replace_underscores: document.getElementById('joy_wd14_replace_underscores').checked,
    wd14_undesired_tags: document.getElementById('joy_wd14_undesired_tags').value,
    qwen3vl_model: document.getElementById('joy_qwen3vl_model').value,
    qwen3vl_name: document.getElementById('joy_qwen3vl_name').value,
    qwen3vl_system_prompt: document.getElementById('joy_qwen3vl_system_prompt').value,
    qwen3vl_temperature: document.getElementById('joy_qwen3vl_temperature').value,
    qwen3vl_max_tokens: document.getElementById('joy_qwen3vl_max_tokens').value,
    qwen3vl_max_image_side: document.getElementById('joy_qwen3vl_max_image_side').value,
    external_api_url: document.getElementById('joy_external_api_url').value,
    external_api_model: document.getElementById('joy_external_api_model').value,
    external_api_name: document.getElementById('joy_external_api_name').value,
    external_api_key: document.getElementById('joy_external_api_key').value,
    external_api_temperature: document.getElementById('joy_external_api_temperature').value,
    external_api_max_tokens: document.getElementById('joy_external_api_max_tokens').value,
    external_api_disable_thinking: document.getElementById('joy_external_api_disable_thinking').checked,
    external_api_system_prompt: document.getElementById('joy_external_api_system_prompt').value,
  };
}

function loadJoySettings() {
  try {
    const raw = localStorage.getItem('caption_app_joy_settings');
    const cfg = raw ? JSON.parse(raw) : {};
    const merged = { ...JOY_DEFAULTS, ...cfg };
    if (!['standard_text', 'ideogram4_json'].includes(String(merged.caption_format || ''))) {
      merged.caption_format = JOY_DEFAULTS.caption_format;
    }
    if (!['joycaption', 'wd14', 'qwen3_vl', 'external_api'].includes(String(merged.backend || ''))) {
      merged.backend = JOY_DEFAULTS.backend;
    }
    if (String(merged.qwen3vl_base_url || '').trim() && !String(merged.external_api_url || '').trim()) {
      const legacyModels = {
        'Qwen3-VL-4B-Instruct': 'Qwen/Qwen3-VL-4B-Instruct',
        'Qwen3-VL-8B-Instruct': 'Qwen/Qwen3-VL-8B-Instruct',
        'Huihui-Qwen3-VL-8B-Instruct-abliterated': 'huihui-ai/Huihui-Qwen3-VL-8B-Instruct-abliterated',
      };
      merged.external_api_url = merged.qwen3vl_base_url;
      merged.external_api_model = legacyModels[merged.qwen3vl_model] || merged.qwen3vl_model || '';
      merged.external_api_name = merged.qwen3vl_name || '';
      merged.external_api_temperature = merged.qwen3vl_temperature;
      merged.external_api_max_tokens = merged.qwen3vl_max_tokens;
      merged.external_api_system_prompt = merged.qwen3vl_system_prompt;
      if (merged.backend === 'qwen3_vl' && merged.caption_format !== 'ideogram4_json') merged.backend = 'external_api';
    }
    if (String(merged.max_tokens ?? '') === '512' && localStorage.getItem('caption_app_joy_max_tokens_default_migrated') !== '1') {
      merged.max_tokens = JOY_DEFAULTS.max_tokens;
      localStorage.setItem('caption_app_joy_max_tokens_default_migrated', '1');
    }
    if (String(merged.qwen3vl_max_tokens ?? '') === '512' && localStorage.getItem('caption_app_qwen3vl_max_tokens_default_migrated') !== '1') {
      merged.qwen3vl_max_tokens = JOY_DEFAULTS.qwen3vl_max_tokens;
      localStorage.setItem('caption_app_qwen3vl_max_tokens_default_migrated', '1');
    }
    const legacyQwenPrompts = [
      'Describe this image in detailed tags and natural language.',
      'Create a concise LoRA training caption for a human figure image. Use comma-separated descriptive tags and short phrases. Focus on visible identity-neutral traits, pose, expression, gaze, body framing, camera angle, clothing, hairstyle, lighting, background, composition, and image style. Do not invent details. Do not mention image resolution or file metadata.',
    ];
    if (legacyQwenPrompts.includes(String(merged.qwen3vl_system_prompt ?? '').trim())) {
      merged.qwen3vl_system_prompt = JOY_DEFAULTS.qwen3vl_system_prompt;
      localStorage.setItem('caption_app_qwen3vl_prompt_default_migrated', '1');
    }
    const legacyExternalPrompt = `Create a natural-language image caption for LoRA training.

Write exactly one concise sentence. Describe only visible details in the image. Focus on expression, gaze, pose, hair, clothing, framing, setting, lighting, background, and image style when visible.

Write in natural language, not as comma-separated tags. Do not invent details. Do not mention file names, metadata, resolution, image quality, camera model, or that this is an image.

Keep the caption short and direct. Output only the caption.`;
    if (String(merged.external_api_system_prompt ?? '').trim() === legacyExternalPrompt) {
      merged.external_api_system_prompt = JOY_DEFAULTS.external_api_system_prompt;
    }
    for (const [k, v] of Object.entries(merged)) {
      const el = document.getElementById('joy_' + k);
      if (!el) continue;
      if (el.type === 'checkbox') el.checked = !!v;
      else el.value = v;
    }
    const selected = Array.isArray(merged.extra_options_selected) ? merged.extra_options_selected : [];
    document.querySelectorAll('.joy-extra-option').forEach(el => {
      el.checked = selected.includes(el.value);
    });
    if (joyAutoScroll) {
      joyAutoScroll.checked = merged.auto_scroll !== false;
      localStorage.setItem('caption_app_joy_autoscroll', joyAutoScroll.checked ? '1' : '0');
    }
    [['joy_quantization', JOY_DEFAULTS.quantization], ['joy_caption_type', JOY_DEFAULTS.caption_type], ['joy_caption_length', JOY_DEFAULTS.caption_length]].forEach(([id, fallback]) => {
      const el = document.getElementById(id);
      if (el && !Array.from(el.options).some(option => option.value === el.value)) el.value = fallback;
    });
    updateJoyNameVisibility();
    updateCaptionBackendUI();
  } catch (e) {}
}

function saveJoySettings() {
  const settings = joySettings();
  settings.auto_scroll = !!(joyAutoScroll && joyAutoScroll.checked);
  const storedSettings = {...settings};
  delete storedSettings.external_api_key;
  localStorage.setItem('caption_app_joy_settings', JSON.stringify(storedSettings));
}

function resetJoySettings() {
  for (const [k, v] of Object.entries(JOY_DEFAULTS)) {
    const el = document.getElementById('joy_' + k);
    if (!el) continue;
    if (el.type === 'checkbox') el.checked = !!v;
    else el.value = v;
  }
  document.querySelectorAll('.joy-extra-option').forEach(el => { el.checked = false; });
  if (joyAutoScroll) {
    joyAutoScroll.checked = true;
    localStorage.setItem('caption_app_joy_autoscroll', '1');
  }
  updateJoyNameVisibility();
  updateCaptionBackendUI();
  saveJoySettings();
}

loadJoySettings();
['joy_backend','joy_caption_format','joy_quantization','joy_caption_type','joy_caption_length','joy_visionmaxres','joy_max_tokens','joy_temperature','joy_top_p','joy_extra_options','joy_person_name','joy_hf_token','joy_no_overwrite','joy_append_existing','joy_ideogram4_name','joy_wd14_model','joy_wd14_general_threshold','joy_wd14_character_threshold','joy_wd14_include_rating','joy_wd14_include_characters','joy_wd14_replace_underscores','joy_wd14_undesired_tags','joy_qwen3vl_model','joy_qwen3vl_name','joy_qwen3vl_system_prompt','joy_qwen3vl_temperature','joy_qwen3vl_max_tokens','joy_qwen3vl_max_image_side','joy_external_api_url','joy_external_api_model','joy_external_api_name','joy_external_api_key','joy_external_api_temperature','joy_external_api_max_tokens','joy_external_api_disable_thinking','joy_external_api_system_prompt'].forEach(id => {
  const el = document.getElementById(id);
  if (!el) return;
  const eventName = (el.type === 'checkbox' || el.tagName === 'SELECT') ? 'change' : 'input';
  el.addEventListener(eventName, () => {
    if (id === 'joy_backend' || id === 'joy_caption_format') updateCaptionBackendUI();
    saveJoySettings();
    if (id === 'joy_caption_format') refreshCaptionsFromDisk(true);
  });
});
document.querySelectorAll('.joy-extra-option').forEach(el => {
  el.addEventListener('change', () => {
    updateJoyNameVisibility();
    saveJoySettings();
  });
});
document.getElementById('joy_append_existing')?.addEventListener('change', () => {
  const append = document.getElementById('joy_append_existing');
  const skip = document.getElementById('joy_no_overwrite');
  if (append?.checked && skip) skip.checked = false;
  saveJoySettings();
});
document.getElementById('joy_no_overwrite')?.addEventListener('change', () => {
  const append = document.getElementById('joy_append_existing');
  const skip = document.getElementById('joy_no_overwrite');
  if (skip?.checked && append) append.checked = false;
  saveJoySettings();
});
document.getElementById('joyResetSettingsBtn')?.addEventListener('click', resetJoySettings);
updateCaptionBackendUI();
refreshCaptionsFromDisk(true);

document.getElementById('joyStartBtn').addEventListener('click', async () => {
  joyStatusPollingEnabled = true;
  updateJoyProgress(0, 0);
  const res = await fetch('/joycaption_start', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(joySettings()),
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    await appAlert(data.error || 'Failed to start Caption');
    return;
  }
  pollJoyStatus();
});

document.getElementById('joyInterruptBtn').addEventListener('click', async () => {
  await fetch('/joycaption_interrupt', { method: 'POST' });
  joyStatusPollingEnabled = true;
  pollJoyStatus();
});

async function refreshCaptionsFromDisk(preserveDirty = true) {
  try {
    const captionFormat = document.getElementById('joy_caption_format')?.value || 'standard_text';
    const res = await fetch(`/captions_json?caption_format=${encodeURIComponent(captionFormat)}`, {
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    });
    const data = await res.json();
    if (!res.ok || !data.ok || !Array.isArray(data.pairs)) return;

    const byName = new Map(data.pairs.map(p => [
      p.img_name,
      captionFormat === 'ideogram4_json' ? String(p.text || '') : decodeCaptionFieldValue(p.text),
    ]));

    document.querySelectorAll('.caption-textarea').forEach(ta => {
      const imgName = ta.dataset.img;
      if (!byName.has(imgName)) return;

      const latest = byName.get(imgName);
      const index = parseInt(ta.dataset.index, 10);
      const original = ta.dataset.original ?? '';
      const dirty = ta.value !== original;

      if (dirty && preserveDirty) return;
      ta.dataset.captionFormat = captionFormat;
      if (ta.value === latest && original === latest) return;

      ta.value = latest;
      ta.dataset.original = latest;
      markUnsaved(index);
    });
  } catch (e) {}
}

async function pollJoyStatus() {
  if (!joyStatusPollingEnabled) return;
  try {
    const res = await fetch('/joycaption_status');
    const data = await res.json();
    const total = Number.isFinite(Number(data.total)) ? Number(data.total) : 0;
    updateJoyProgress(data.count, total);
    joyLogBox.textContent = data.log || '';
    if (!joyAutoScroll || joyAutoScroll.checked) {
      joyLogBox.scrollTop = joyLogBox.scrollHeight;
    }
    if (data.running) {
      await refreshCaptionsFromDisk(true);
    } else if (data.reload_pairs) {
      await refreshCaptionsFromDisk(true);
    } else if (!joyModalBackdrop?.classList.contains('open')) {
      joyStatusPollingEnabled = false;
      if (joyStatusPollTimer) {
        clearTimeout(joyStatusPollTimer);
        joyStatusPollTimer = null;
      }
      return;
    }
  } catch (e) {}
  scheduleJoyStatusPoll(1200);
}


const summaryModalBackdrop = document.getElementById("summaryModalBackdrop");
const openSummaryModalBtn = document.getElementById("openSummaryModalBtn");
const closeSummaryModalBtn = document.getElementById("closeSummaryModalBtn");
const summaryContent = document.getElementById("summaryContent");

function openSummaryModal() {
  summaryModalBackdrop?.classList.add("open");
}
function closeSummaryModal() {
  summaryModalBackdrop?.classList.remove("open");
}

function renderAspectBarChart(data) {
  const items = (Array.isArray(data?.aspect_chart) ? data.aspect_chart : [])
    .filter(item => (Number(item.count) || 0) > 0);
  const maxCount = Math.max(1, ...items.map(item => Number(item.count) || 0));
  if (!items.length) {
    return `
      <div class="summary-chart-title">Aspect ratios</div>
      <div class="summary-empty-chart">No aspect ratio data</div>
    `;
  }
  const bars = items.map(item => {
    const count = Number(item.count) || 0;
    const height = Math.max(2, Math.round((count / maxCount) * 96));
    const isUnknown = String(item.label) === '???';
    return `
      <div class="summary-bar-wrap" title="${item.label}: ${count}">
        <div class="summary-bar-count">${count}</div>
        <div class="summary-bar-area">
          <div class="summary-bar ${isUnknown ? 'summary-bar-unknown' : ''}" style="height:${height}px;"></div>
        </div>
        <div class="summary-bar-label">${item.label}</div>
      </div>
    `;
  }).join('');
  return `
<div class="summary-chart-title">Aspect ratios</div>
    <div class="summary-bar-chart">${bars}</div>
  `;
}

function renderCategoryPieChart(data) {
  const allItems = Array.isArray(data?.category_chart) ? data.category_chart : [];
  const items = allItems.filter(item => Number(item.count) > 0);
  const total = items.reduce((acc, item) => acc + (Number(item.count) || 0), 0);
  if (!total) {
    return `
      <div class="summary-chart-title">Folders</div>
      <div class="summary-empty-chart">No category data</div>
    `;
  }

  const neutralColor = '#9ca3af';

  function colorForCategory(name) {
    const item = allItems.find(entry => entry.name === name);
    return item?.color || neutralColor;
  }

  let start = 0;
  const gradients = [];
  const iconNodes = [];
  const labelNodes = [];

  items.forEach((item) => {
    const value = Number(item.count) || 0;
    const percent = total ? (value / total) : 0;
    const end = start + percent * 360;
    const color = colorForCategory(item.name);
    gradients.push(`${color} ${start}deg ${end}deg`);

    const mid = (start + end) / 2;
    const radians = (mid - 90) * Math.PI / 180;
    const sweepRadians = (end - start) * Math.PI / 180;
    const iconRadius = sweepRadians > 0
      ? Math.max(26, Math.min(72, (4 * 120 * Math.sin(sweepRadians / 2)) / (3 * sweepRadians)))
      : 60;
    const labelRadius = 150;
    const x = 120 + Math.cos(radians) * iconRadius;
    const y = 120 + Math.sin(radians) * iconRadius;
    const lx = 150 + Math.cos(radians) * labelRadius;
    const ly = 150 + Math.sin(radians) * labelRadius;

    iconNodes.push(`
      <img
        class="summary-pie-icon"
        src="/category_icon/${encodeURIComponent(item.icon)}"
        alt="${item.name}"
        title="${item.name}"
        style="left:${x}px; top:${y}px;"
      >
    `);

    labelNodes.push(`
      <div
        class="summary-pie-percent-label"
        title="${item.name}"
        style="left:${lx}px; top:${ly}px; border:1px solid ${color};"
      >${Math.round(percent * 100)}%</div>
    `);
    start = end;
  });

  const legendItems = (allItems.length ? allItems : items);
  const legend = legendItems.map(item => {
    const value = Number(item.count) || 0;
    const percent = total ? Math.round((value / total) * 100) : 0;
    const hasItems = value > 0;
    const color = hasItems ? colorForCategory(item.name) : '#6b7280';
    return `
      <div class="summary-pie-legend-item ${hasItems ? '' : 'summary-pie-legend-empty'}" title="${item.name}" style="color:${color};">
        <img src="/category_icon/${encodeURIComponent(item.icon)}" alt="${item.name}">
        <span>${item.name}: ${item.count} (${percent}%)</span>
      </div>
    `;
  }).join('');

  return `
    <div class="summary-chart-title">Folders</div>
    <div class="summary-pie-layout">
      <div class="summary-pie-chart-wrap">
        <div class="summary-pie-chart" style="background:conic-gradient(${gradients.join(', ')});">
          ${iconNodes.join('')}
        </div>
        ${labelNodes.join('')}
      </div>
      <div class="summary-pie-legend-wrap">
        <div class="summary-pie-legend">${legend}</div>
      </div>
    </div>
  `;
}

function renderCategoryGroupStats(data) {
  const items = Array.isArray(data?.category_groups) ? data.category_groups : [];
  if (!items.length) {
    return '<div class="summary-chart-title">Category groups</div><div class="summary-empty-chart">No category group data</div>';
  }
  return `
    <div class="summary-chart-title">Category groups</div>
    <div class="summary-category-groups">
      ${items.map(item => `
        <div class="summary-category-group-item">
          <span class="summary-category-group-color" style="background:${item.color || '#9ca3af'}"></span>
          <span class="summary-category-group-name">${item.name}</span>
          <b>${Number(item.count) || 0} image${Number(item.count) === 1 ? '' : 's'}</b>
          <span>${Number(item.percent) || 0}%</span>
        </div>
      `).join('')}
    </div>
  `;
}

function buildSummaryHtml(data) {
  const resolutionLines = (data.items || []).map(item => {
    const match = String(item.bucket || '').match(/^(\d+)x(\d+)/);
    const width = match ? parseInt(match[1], 10) : 0;
    const height = match ? parseInt(match[2], 10) : 0;
    const status = width && height ? getBucketStatus(width, height) : (item.status || 'invalid');
    const color = status === 'invalid' ? 'var(--danger)' : (status === 'other' ? '#fbbf24' : '');
    const bucketHtml = color
      ? `<span style="color: ${color};">${item.bucket}</span>`
      : item.bucket;
    return `<div class="summary-resolution-row"><span>${bucketHtml}</span><b>${item.count}</b></div>`;
  }).join('');
  const bucketBaseLines = (data.bucket_bases || []).map(item =>
    `<div class="summary-resolution-row"><span>${item.base}</span><b>${item.count}</b></div>`
  ).join('') || '<div class="summary-empty-chart">No bucket base data</div>';
  const totalImages = Number(data.total_images ?? 0);
  const totalCaptions = Number(data.total_captions ?? 0);
  const invalidBuckets = (data.items || []).filter(item => {
    const match = String(item.bucket || '').match(/^(\d+)x(\d+)/);
    return !match || getBucketStatus(parseInt(match[1], 10), parseInt(match[2], 10)) === 'invalid';
  }).length;

  let html = `
    <style>
      .summary-grid{display:grid;grid-template-columns:minmax(280px,.9fr) minmax(360px,1.1fr);gap:10px;align-items:start;}
      .summary-overview{grid-column:1 / -1;display:grid;grid-template-columns:repeat(auto-fit,minmax(130px,1fr));gap:8px;}
      .summary-tile{border:1px solid var(--border);border-radius:8px;padding:8px 10px;background:color-mix(in srgb,var(--bg) 72%,var(--card));}
      .summary-tile-label{font-size:11px;color:var(--muted);font-weight:700;text-transform:uppercase;letter-spacing:.02em;}
      .summary-tile-value{font-size:22px;font-weight:800;line-height:1.1;margin-top:2px;}
      .summary-chart-card{background:color-mix(in srgb,var(--bg) 72%,var(--card));border:1px solid var(--border);border-radius:8px;padding:10px;min-width:0;}
      .summary-chart-title{font-weight:800;margin-bottom:8px;font-size:14px;}
      .summary-bar-chart{display:flex;align-items:flex-end;gap:5px;min-height:150px;padding:4px 4px 0;border-left:1px solid var(--border);border-bottom:1px solid var(--border);overflow-x:auto;}
      .summary-bar-wrap{display:grid;grid-template-rows:auto 96px 46px;align-items:end;justify-items:center;min-width:28px;}
      .summary-bar-area{height:96px;width:22px;display:flex;align-items:flex-end;justify-content:center;}
      .summary-bar{width:22px;border-radius:5px 5px 0 0;background:#2d7fb8;}
      .summary-bar-unknown{background:#7f8c8d;}
      .summary-bar-count{font-size:11px;margin-bottom:3px;color:var(--fg);}
      .summary-bar-label{font-size:10px;margin-top:4px;white-space:nowrap;writing-mode:vertical-rl;transform:rotate(180deg);height:46px;color:var(--muted);}
      .summary-pie-layout{display:grid;grid-template-columns:180px minmax(180px,1fr);gap:12px;align-items:center;}
      .summary-pie-chart-wrap{position:relative;width:180px;height:180px;flex:0 0 auto;}
      .summary-pie-chart{position:absolute;left:18px;top:18px;width:144px;height:144px;border-radius:50%;border:1px solid var(--border);overflow:hidden;}
      .summary-pie-icon{position:absolute;width:20px;height:20px;transform:translate(-50%,-50%);border-radius:50%;background:rgba(0,0,0,.45);padding:2px;object-fit:cover;border:1px solid rgba(255,255,255,.35);}
      .summary-pie-percent-label{display:none;}
      .summary-pie-legend-wrap{display:grid;grid-template-columns:1fr;gap:8px;align-items:start;min-width:0;}.summary-pie-legend{display:grid;grid-template-columns:1fr;gap:4px;}.summary-category-group-block{display:flex;gap:8px;flex-wrap:wrap;font-weight:700;font-size:12px;}.summary-category-group-block div{display:inline-flex;align-items:center;gap:5px;}.summary-category-group-block span{width:9px;height:9px;border-radius:999px;display:inline-block;}
      .summary-pie-legend-item{display:grid;grid-template-columns:18px 1fr;align-items:center;gap:6px;font-size:12px;line-height:1.25;}
      .summary-pie-legend-item img{width:18px;height:18px;border-radius:50%;object-fit:cover;}
      .summary-pie-legend-empty{color:#6b7280 !important;}
      .summary-pie-legend-empty img{opacity:.42;filter:grayscale(1);}
      .summary-category-groups{display:grid;grid-template-columns:repeat(auto-fit,minmax(190px,1fr));gap:7px;}
      .summary-category-group-item{display:grid;grid-template-columns:10px minmax(0,1fr) auto auto;gap:7px;align-items:center;border:1px solid color-mix(in srgb,var(--border) 65%,transparent);border-radius:6px;padding:7px 8px;font-size:12px;}
      .summary-category-group-color{width:10px;height:10px;border-radius:50%;}
      .summary-category-group-name{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-weight:700;}
      .summary-empty-chart{opacity:.8;font-size:13px;}
      .summary-stats-block{line-height:1.2;}
      .summary-stats-left{padding-left:0 !important; margin-left:0 !important; text-indent:0; display:block; width:100%; align-self:flex-start;}
      .summary-resolution-lines{display:grid;gap:3px;max-height:190px;overflow:auto;margin-top:4px;}
      .summary-resolution-row{display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;align-items:center;font-size:12px;border-bottom:1px solid color-mix(in srgb,var(--border) 55%,transparent);padding:2px 0;}
      .summary-resolution-row span{overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
      .summary-resolution-lines br{display:none;}
      @media (max-width: 760px){.summary-grid{grid-template-columns:1fr;}.summary-pie-layout{grid-template-columns:1fr;}.summary-pie-chart-wrap{margin:auto;}}
    
/* Force card control icons to render at 100% (no scaling) */
.icon-btn img {
  width: auto !important;
  height: auto !important;
  max-width: none !important;
  max-height: none !important;
}
.toolbar-btn-icon {
  width: auto !important;
  height: auto !important;
  max-width: none !important;
  max-height: none !important;
}

/* Category colors */
</style>
    <div class="summary-grid">
      <div class="summary-overview">
        <div class="summary-tile"><div class="summary-tile-label">Images</div><div class="summary-tile-value">${totalImages}</div></div>
        <div class="summary-tile"><div class="summary-tile-label">Captions</div><div class="summary-tile-value">${totalCaptions}</div></div>
        <div class="summary-tile"><div class="summary-tile-label">Aspect ratios</div><div class="summary-tile-value">${(data.aspect_chart || []).filter(item => Number(item.count) > 0).length}</div></div>
        <div class="summary-tile"><div class="summary-tile-label">Not in buckets</div><div class="summary-tile-value">${invalidBuckets}</div></div>
      </div>
      ${categoriesVisible() ? `<div class="summary-chart-card" style="grid-column:1 / -1;">${renderCategoryGroupStats(data)}</div>` : ''}
      <div class="summary-chart-card">
        ${renderAspectBarChart(data)}
        <div class="summary-stats-block summary-stats-left" style="margin-top:4px;">
          <div class="summary-chart-title" style="margin-top:10px;">Resolutions</div>
          <div class="summary-resolution-lines">${resolutionLines}</div>
          <div class="summary-chart-title" style="margin-top:10px;">Bucket bases</div>
          <div class="summary-resolution-lines">${bucketBaseLines}</div>
        </div>
      </div>
      ${categoriesVisible() ? `<div class="summary-chart-card">${renderCategoryPieChart(data)}</div>` : ''}
    </div>
  `;
  return html;
}

openSummaryModalBtn?.addEventListener("click", async () => {
  openSummaryModal();
  if (summaryContent) summaryContent.innerHTML = "Loading statistics…";
  try {
    const res = await fetch("/summary", {
      headers: {
        "X-Requested-With": "XMLHttpRequest",
        "Accept": "application/json"
      },
      redirect: "follow"
    });

    const contentType = res.headers.get("content-type") || "";
    const raw = await res.text();

    if (!res.ok) {
      throw new Error(`HTTP ${res.status}`);
    }

    if (contentType.includes("application/json")) {
      const data = JSON.parse(raw);
      if (!data?.ok) throw new Error(data?.error || "Statistics request failed");
      if (summaryContent) summaryContent.innerHTML = buildSummaryHtml(data);
    } else {
      if (summaryContent) summaryContent.innerHTML = raw;
    }
  } catch (err) {
    if (summaryContent) summaryContent.textContent = `Failed to load statistics: ${err}`;
  }
});
closeSummaryModalBtn?.addEventListener("click", closeSummaryModal);
const toolsModalBackdrop = document.getElementById("toolsModalBackdrop");
const openToolsModalBtnInline = document.getElementById("openToolsModalBtnInline");
const closeToolsModalBtn = document.getElementById("closeToolsModalBtn");
const toolsResult = document.getElementById("toolsResult");
const replaceForm = document.getElementById("replaceForm");
const countForm = document.getElementById("countForm");
const countNextMatchBtn = document.getElementById("countNextMatchBtn");
const triggerForm = document.getElementById("triggerForm");
let toolsCategoryScope = '';
let countMatches = [];
let countMatchCursor = -1;

loadToolsSettings();
document.getElementById('sr_use_regex')?.addEventListener('change', saveToolsSettings);

let regexTooltipTimer = null;
let regexTooltipEl = null;

function getRegexTooltip() {
  if (!regexTooltipEl) {
    regexTooltipEl = document.createElement('div');
    regexTooltipEl.className = 'regex-help-tooltip';
    document.body.appendChild(regexTooltipEl);
  }
  return regexTooltipEl;
}

function positionRegexTooltip(anchor) {
  const tooltip = getRegexTooltip();
  const rect = anchor.getBoundingClientRect();
  const tipRect = tooltip.getBoundingClientRect();
  const gap = 8;
  let left = rect.left + rect.width / 2 - tipRect.width / 2;
  let top = rect.bottom + gap;
  if (top + tipRect.height > window.innerHeight - gap) {
    top = rect.top - tipRect.height - gap;
  }
  left = Math.max(gap, Math.min(window.innerWidth - tipRect.width - gap, left));
  top = Math.max(gap, Math.min(window.innerHeight - tipRect.height - gap, top));
  tooltip.style.left = `${left}px`;
  tooltip.style.top = `${top}px`;
}

function showRegexTooltip(anchor) {
  clearTimeout(regexTooltipTimer);
  regexTooltipTimer = setTimeout(() => {
    const tooltip = getRegexTooltip();
    tooltip.textContent = anchor.dataset.tooltip || '';
    tooltip.classList.add('open');
    positionRegexTooltip(anchor);
  }, 120);
}

function hideRegexTooltip() {
  clearTimeout(regexTooltipTimer);
  regexTooltipTimer = null;
  regexTooltipEl?.classList.remove('open');
}

document.querySelectorAll('.regex-help-icon').forEach(icon => {
  icon.addEventListener('mouseenter', () => showRegexTooltip(icon));
  icon.addEventListener('mousemove', () => {
    if (regexTooltipEl?.classList.contains('open')) positionRegexTooltip(icon);
  });
  icon.addEventListener('mouseleave', hideRegexTooltip);
  icon.addEventListener('focus', () => showRegexTooltip(icon));
  icon.addEventListener('blur', hideRegexTooltip);
});

function openToolsModal(category = '') {
  toolsCategoryScope = categoryExists(category) ? category : '';
  document.querySelectorAll('.tools-category-input').forEach(input => { input.value = toolsCategoryScope; });
  const title = document.getElementById('toolsModalTitle');
  if (title) title.textContent = toolsCategoryScope ? `Text tools - ${toolsCategoryScope}` : 'Text tools';
  toolsModalBackdrop?.classList.add("open");
}
function closeToolsModal() {
  toolsModalBackdrop?.classList.remove("open");
  toolsCategoryScope = '';
  document.querySelectorAll('.tools-category-input').forEach(input => { input.value = ''; });
  const title = document.getElementById('toolsModalTitle');
  if (title) title.textContent = 'Text tools';
}
openToolsModalBtnInline?.addEventListener("click", () => openToolsModal(''));
closeToolsModalBtn?.addEventListener("click", closeToolsModal);

const jsonModalBackdrop = document.getElementById("jsonModalBackdrop");
const openJsonModalBtn = document.getElementById("openJsonModalBtn");
const closeJsonModalBtn = document.getElementById("closeJsonModalBtn");
const jsonImageSelect = document.getElementById("jsonImageSelect");
const jsonPreviewImage = document.getElementById("jsonPreviewImage");
const jsonBboxLayer = document.getElementById("jsonBboxLayer");
const jsonImageFrame = document.getElementById("jsonImageFrame");
const jsonEditor = document.getElementById("jsonEditor");
const jsonStatus = document.getElementById("jsonStatus");
const jsonValidationLog = document.getElementById("jsonValidationLog");
const jsonElementList = document.getElementById("jsonElementList");
const jsonPrevBtn = document.getElementById("jsonPrevBtn");
const jsonNextBtn = document.getElementById("jsonNextBtn");
const jsonValidateBtn = document.getElementById("jsonValidateBtn");
const jsonValidateAllBtn = document.getElementById("jsonValidateAllBtn");
const jsonSwapBboxBtn = document.getElementById("jsonSwapBboxBtn");
const jsonSaveBtn = document.getElementById("jsonSaveBtn");
let jsonCaptionItems = [];
let jsonCaptionIndex = 0;
let jsonActiveElementIndex = -1;

function setJsonStatus(text, kind = "") {
  if (!jsonStatus) return;
  jsonStatus.textContent = text || "";
  jsonStatus.classList.toggle("ok", kind === "ok");
  jsonStatus.classList.toggle("error", kind === "error");
}

function parseJsonEditorValue() {
  if (!jsonEditor) return null;
  try {
    return JSON.parse(jsonEditor.value || "{}");
  } catch {
    return null;
  }
}

function currentJsonItem() {
  return jsonCaptionItems[jsonCaptionIndex] || null;
}

function getIdeogramElements(data) {
  const elements = data?.compositional_deconstruction?.elements;
  return Array.isArray(elements) ? elements : [];
}

function escapeHtml(value) {
  return String(value ?? "")
    .replaceAll("&", "&amp;")
    .replaceAll("<", "&lt;")
    .replaceAll(">", "&gt;")
    .replaceAll('"', "&quot;")
    .replaceAll("'", "&#39;");
}

function renderJsonElementList(data) {
  if (!jsonElementList) return;
  const elements = getIdeogramElements(data);
  if (!elements.length) {
    jsonElementList.innerHTML = '<div class="small" style="padding:8px;">No elements.</div>';
    return;
  }
  jsonElementList.innerHTML = elements.map((element, index) => {
    const label = `${index + 1}. ${element.type || "obj"}${Array.isArray(element.bbox) ? " bbox" : ""} - ${String(element.desc || element.text || "").slice(0, 90)}`;
    return `<button type="button" class="json-element-item${index === jsonActiveElementIndex ? " active" : ""}" data-json-element="${index}">${escapeHtml(label)}</button>`;
  }).join("");
}

function renderJsonBboxes() {
  if (!jsonBboxLayer || !jsonPreviewImage || !jsonImageFrame) return;
  jsonBboxLayer.innerHTML = "";
  const data = parseJsonEditorValue();
  renderJsonElementList(data);
  if (!data || !jsonPreviewImage.complete || !jsonPreviewImage.naturalWidth) return;
  const frameRect = jsonImageFrame.getBoundingClientRect();
  const imageRect = jsonPreviewImage.getBoundingClientRect();
  const offsetLeft = imageRect.left - frameRect.left;
  const offsetTop = imageRect.top - frameRect.top;
  getIdeogramElements(data).forEach((element, index) => {
    const bbox = element?.bbox;
    if (!Array.isArray(bbox) || bbox.length !== 4) return;
    const [yMin, xMin, yMax, xMax] = bbox.map(Number);
    if (![yMin, xMin, yMax, xMax].every(Number.isFinite)) return;
    const box = document.createElement("button");
    box.type = "button";
    box.className = `json-bbox${index === jsonActiveElementIndex ? " active" : ""}`;
    box.dataset.jsonElement = String(index);
    box.title = element.desc || element.text || `Element ${index + 1}`;
    box.style.left = `${offsetLeft + (xMin / 1000) * imageRect.width}px`;
    box.style.top = `${offsetTop + (yMin / 1000) * imageRect.height}px`;
    box.style.width = `${Math.max(2, ((xMax - xMin) / 1000) * imageRect.width)}px`;
    box.style.height = `${Math.max(2, ((yMax - yMin) / 1000) * imageRect.height)}px`;
    ["nw", "n", "ne", "e", "se", "s", "sw", "w"].forEach(handle => {
      const handleEl = document.createElement("span");
      handleEl.className = `json-bbox-handle ${handle}`;
      handleEl.dataset.handle = handle;
      box.appendChild(handleEl);
    });
    jsonBboxLayer.appendChild(box);
  });
}

function selectJsonElement(index) {
  jsonActiveElementIndex = Number.isFinite(index) ? index : -1;
  renderJsonBboxes();
}

function clampJsonCoord(value) {
  if (!Number.isFinite(value)) return 0;
  return Math.max(0, Math.min(1000, Math.round(value)));
}

function normalizeJsonBbox(bbox) {
  let [yMin, xMin, yMax, xMax] = bbox.map(Number);
  yMin = clampJsonCoord(yMin);
  xMin = clampJsonCoord(xMin);
  yMax = clampJsonCoord(yMax);
  xMax = clampJsonCoord(xMax);
  if (yMax < yMin) [yMin, yMax] = [yMax, yMin];
  if (xMax < xMin) [xMin, xMax] = [xMax, xMin];
  if (yMax === yMin) {
    if (yMin > 0) yMin -= 1;
    else yMax = 1;
  }
  if (xMax === xMin) {
    if (xMin > 0) xMin -= 1;
    else xMax = 1;
  }
  return [yMin, xMin, yMax, xMax];
}

function writeJsonElementBbox(index, bbox) {
  const data = parseJsonEditorValue();
  const elements = getIdeogramElements(data);
  if (!elements[index]) return false;
  elements[index].bbox = normalizeJsonBbox(bbox);
  jsonEditor.value = JSON.stringify(data, null, 2);
  jsonActiveElementIndex = index;
  setJsonStatus("Unsaved JSON changes.");
  renderJsonBboxes();
  return true;
}

function moveJsonBbox(startBbox, dx, dy) {
  const height = startBbox[2] - startBbox[0];
  const width = startBbox[3] - startBbox[1];
  let yMin = startBbox[0] + dy;
  let xMin = startBbox[1] + dx;
  yMin = Math.max(0, Math.min(1000 - height, yMin));
  xMin = Math.max(0, Math.min(1000 - width, xMin));
  return [yMin, xMin, yMin + height, xMin + width];
}

function resizeJsonBbox(startBbox, handle, dx, dy) {
  let [yMin, xMin, yMax, xMax] = startBbox;
  const minSize = 1;
  if (handle.includes("n")) yMin = Math.min(yMax - minSize, yMin + dy);
  if (handle.includes("s")) yMax = Math.max(yMin + minSize, yMax + dy);
  if (handle.includes("w")) xMin = Math.min(xMax - minSize, xMin + dx);
  if (handle.includes("e")) xMax = Math.max(xMin + minSize, xMax + dx);
  return [yMin, xMin, yMax, xMax];
}

function startJsonBboxEdit(event) {
  if (event.button !== 0) return;
  const box = event.target.closest(".json-bbox");
  if (!box || !jsonPreviewImage || !jsonEditor) return;
  event.preventDefault();
  event.stopPropagation();
  const index = parseInt(box.dataset.jsonElement || "-1", 10);
  const data = parseJsonEditorValue();
  const bbox = getIdeogramElements(data)[index]?.bbox;
  if (!Array.isArray(bbox) || bbox.length !== 4) return;
  const imageRect = jsonPreviewImage.getBoundingClientRect();
  if (!imageRect.width || !imageRect.height) return;
  const handle = event.target.closest(".json-bbox-handle")?.dataset.handle || "move";
  const startBbox = normalizeJsonBbox(bbox);
  const startX = event.clientX;
  const startY = event.clientY;
  selectJsonElement(index);

  const onMove = moveEvent => {
    moveEvent.preventDefault();
    const dx = ((moveEvent.clientX - startX) / imageRect.width) * 1000;
    const dy = ((moveEvent.clientY - startY) / imageRect.height) * 1000;
    const nextBbox = handle === "move"
      ? moveJsonBbox(startBbox, dx, dy)
      : resizeJsonBbox(startBbox, handle, dx, dy);
    writeJsonElementBbox(index, nextBbox);
  };

  const onUp = upEvent => {
    upEvent.preventDefault();
    document.removeEventListener("pointermove", onMove);
    document.removeEventListener("pointerup", onUp);
    document.removeEventListener("pointercancel", onUp);
  };

  document.addEventListener("pointermove", onMove);
  document.addEventListener("pointerup", onUp);
  document.addEventListener("pointercancel", onUp);
}

function renderJsonCaptionItem() {
  const item = currentJsonItem();
  if (!item) {
    if (jsonImageSelect) jsonImageSelect.innerHTML = "";
    if (jsonEditor) jsonEditor.value = "";
    if (jsonPreviewImage) jsonPreviewImage.removeAttribute("src");
    if (jsonBboxLayer) jsonBboxLayer.innerHTML = "";
    if (jsonElementList) jsonElementList.innerHTML = "";
    setJsonStatus("No images found.", "error");
    return;
  }
  jsonActiveElementIndex = -1;
  if (jsonImageSelect) jsonImageSelect.value = String(jsonCaptionIndex);
  if (jsonEditor) jsonEditor.value = item.text || "";
  if (jsonPreviewImage) {
    jsonPreviewImage.src = `/image/${encodeURIComponent(item.img_name)}?json_view=${Date.now()}`;
    jsonPreviewImage.alt = item.img_name;
  }
  setJsonStatus(`${item.img_name} (${jsonCaptionIndex + 1}/${jsonCaptionItems.length})`);
  requestAnimationFrame(renderJsonBboxes);
}

function fillJsonImageSelect() {
  if (!jsonImageSelect) return;
  jsonImageSelect.innerHTML = jsonCaptionItems.map((item, index) => (
    `<option value="${index}">${escapeHtml(item.img_name)}</option>`
  )).join("");
}

async function loadJsonCaptions(preferredName = "") {
  const res = await fetch("/captions_json?caption_format=ideogram4_json", {
    headers: { "X-Requested-With": "XMLHttpRequest" }
  });
  const data = await res.json();
  if (!res.ok || !data.ok) throw new Error(data.error || "Could not load JSON captions.");
  jsonCaptionItems = Array.isArray(data.pairs) ? data.pairs : [];
  const preferredIndex = preferredName ? jsonCaptionItems.findIndex(item => item.img_name === preferredName) : -1;
  jsonCaptionIndex = preferredIndex >= 0 ? preferredIndex : Math.min(jsonCaptionIndex, Math.max(0, jsonCaptionItems.length - 1));
  fillJsonImageSelect();
  renderJsonCaptionItem();
}

async function openJsonModal() {
  jsonModalBackdrop?.classList.add("open");
  setJsonStatus("Loading JSON captions...");
  if (jsonValidationLog) jsonValidationLog.textContent = "";
  try {
    await loadJsonCaptions(currentJsonItem()?.img_name || "");
  } catch (err) {
    setJsonStatus(err?.message || "Could not load JSON captions.", "error");
  }
}

function closeJsonModal() {
  jsonModalBackdrop?.classList.remove("open");
}

async function validateCurrentJson({ applyNormalized = false } = {}) {
  if (!jsonEditor) return false;
  const res = await fetch("/ideogram_json_validate", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ caption: jsonEditor.value })
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    setJsonStatus(data.error || "Invalid Ideogram 4 JSON.", "error");
    return false;
  }
  if (applyNormalized) jsonEditor.value = data.caption || jsonEditor.value;
  setJsonStatus("Valid Ideogram 4 JSON.", "ok");
  renderJsonBboxes();
  return true;
}

function swapCurrentJsonBboxOrder() {
  const data = parseJsonEditorValue();
  if (!data) {
    setJsonStatus("JSON parse failed. Fix syntax before swapping bbox order.", "error");
    return;
  }
  let changed = 0;
  getIdeogramElements(data).forEach(element => {
    const bbox = element?.bbox;
    if (!Array.isArray(bbox) || bbox.length !== 4) return;
    element.bbox = [bbox[1], bbox[0], bbox[3], bbox[2]];
    changed += 1;
  });
  if (!changed) {
    setJsonStatus("No bbox fields found to swap.");
    return;
  }
  jsonEditor.value = JSON.stringify(data, null, 2);
  setJsonStatus(`Swapped ${changed} bbox field${changed === 1 ? "" : "s"}. Save to write the change.`, "ok");
  renderJsonBboxes();
}

async function saveCurrentJsonCaption() {
  const item = currentJsonItem();
  if (!item || !jsonEditor) return;
  const valid = await validateCurrentJson({ applyNormalized: true });
  if (!valid) return;
  const res = await fetch("/save_pair", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      img_name: item.img_name,
      caption: jsonEditor.value,
      caption_format: "ideogram4_json"
    })
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    setJsonStatus(data.error || "Save failed.", "error");
    return;
  }
  const ta = document.querySelector(`.caption-textarea[data-img="${CSS.escape(item.img_name)}"]`);
  if (ta && (ta.dataset.captionFormat || "standard_text") === "ideogram4_json") {
    ta.value = jsonEditor.value;
    ta.dataset.original = jsonEditor.value;
    markUnsaved(parseInt(ta.dataset.index, 10));
  }
  item.text = jsonEditor.value;
  setJsonStatus(`Saved ${item.img_name}.`, "ok");
  setStatusbarMessage(`Saved JSON caption for ${item.img_name}.`);
}

async function validateAllJsonCaptions() {
  if (jsonValidationLog) jsonValidationLog.textContent = "Validating...";
  const res = await fetch("/ideogram_json_validate_all", {
    headers: { "X-Requested-With": "XMLHttpRequest" }
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    if (jsonValidationLog) jsonValidationLog.textContent = data.error || "Validate all failed.";
    return;
  }
  const counts = data.counts || {};
  const problems = (data.items || []).filter(item => item.status !== "valid");
  const lines = [
    `Valid: ${counts.valid || 0}`,
    `Repairable: ${counts.repairable || 0}`,
    `Invalid: ${counts.invalid || 0}`,
    `Missing: ${counts.missing || 0}`,
    ""
  ];
  problems.forEach(item => lines.push(`${item.status}: ${item.img_name} - ${item.message}`));
  if (!problems.length) lines.push("All JSON captions are valid.");
  if (jsonValidationLog) jsonValidationLog.textContent = lines.join("\n");
}

openJsonModalBtn?.addEventListener("click", openJsonModal);
closeJsonModalBtn?.addEventListener("click", closeJsonModal);
jsonPreviewImage?.addEventListener("load", renderJsonBboxes);
window.addEventListener("resize", () => {
  if (jsonModalBackdrop?.classList.contains("open")) renderJsonBboxes();
});
jsonImageSelect?.addEventListener("change", () => {
  jsonCaptionIndex = parseInt(jsonImageSelect.value || "0", 10) || 0;
  renderJsonCaptionItem();
});
jsonPrevBtn?.addEventListener("click", () => {
  if (!jsonCaptionItems.length) return;
  jsonCaptionIndex = (jsonCaptionIndex - 1 + jsonCaptionItems.length) % jsonCaptionItems.length;
  renderJsonCaptionItem();
});
jsonNextBtn?.addEventListener("click", () => {
  if (!jsonCaptionItems.length) return;
  jsonCaptionIndex = (jsonCaptionIndex + 1) % jsonCaptionItems.length;
  renderJsonCaptionItem();
});
jsonValidateBtn?.addEventListener("click", () => validateCurrentJson({ applyNormalized: true }));
jsonValidateAllBtn?.addEventListener("click", validateAllJsonCaptions);
jsonSwapBboxBtn?.addEventListener("click", swapCurrentJsonBboxOrder);
jsonSaveBtn?.addEventListener("click", saveCurrentJsonCaption);
jsonEditor?.addEventListener("input", () => {
  setJsonStatus("Unsaved JSON changes.");
  renderJsonBboxes();
});
jsonBboxLayer?.addEventListener("click", event => {
  const box = event.target.closest(".json-bbox");
  if (!box) return;
  selectJsonElement(parseInt(box.dataset.jsonElement || "-1", 10));
});
jsonBboxLayer?.addEventListener("pointerdown", startJsonBboxEdit);
jsonElementList?.addEventListener("click", event => {
  const item = event.target.closest(".json-element-item");
  if (!item) return;
  selectJsonElement(parseInt(item.dataset.jsonElement || "-1", 10));
});

function makeModalDraggable(backdrop) {
  const modal = backdrop?.querySelector('.joy-modal');
  const head = modal?.querySelector('.joy-modal-head');
  if (!modal || !head || head.dataset.draggableBound) return;
  head.dataset.draggableBound = '1';
  head.addEventListener('pointerdown', (event) => {
    if (event.button !== 0 || event.target.closest('button, input, textarea, select, a')) return;
    event.preventDefault();
    const rect = modal.getBoundingClientRect();
    modal.style.position = 'fixed';
    modal.style.left = `${rect.left}px`;
    modal.style.top = `${rect.top}px`;
    modal.style.width = `${rect.width}px`;
    modal.style.maxWidth = 'none';
    modal.style.margin = '0';
    const startX = event.clientX;
    const startY = event.clientY;
    const startLeft = rect.left;
    const startTop = rect.top;
    let dragging = false;
    head.setPointerCapture?.(event.pointerId);
    const move = (moveEvent) => {
      moveEvent.preventDefault();
      const dx = moveEvent.clientX - startX;
      const dy = moveEvent.clientY - startY;
      if (!dragging && Math.hypot(dx, dy) < 3) return;
      dragging = true;
      const nextLeft = Math.max(4, Math.min(window.innerWidth - 80, startLeft + dx));
      const nextTop = Math.max(4, Math.min(window.innerHeight - 48, startTop + dy));
      modal.style.left = `${nextLeft}px`;
      modal.style.top = `${nextTop}px`;
    };
    const up = () => {
      head.removeEventListener('pointermove', move);
      head.removeEventListener('pointerup', up);
      head.removeEventListener('pointercancel', up);
    };
    head.addEventListener('pointermove', move, { passive: false });
    head.addEventListener('pointerup', up, { once: true });
    head.addEventListener('pointercancel', up, { once: true });
  });
}

[joyModalBackdrop, maskModalBackdrop, summaryModalBackdrop, toolsModalBackdrop, jsonModalBackdrop, helpModalBackdrop, appDialogBackdrop].forEach(makeModalDraggable);

function textToolCaptionTextareas() {
  return Array.from(document.querySelectorAll('.caption-textarea')).filter(textarea => {
    if (!toolsCategoryScope) return true;
    return textarea.closest('.pair-card')?.dataset.category === toolsCategoryScope;
  });
}

function compileTextToolRegex(pattern) {
  const source = String(pattern || '')
    .replaceAll('\\A', '(?<![\\s\\S])')
    .replaceAll('\\Z', '(?![\\s\\S])');
  return new RegExp(source, 'gms');
}

function setTextToolCaptionValue(textarea, value) {
  const next = String(value ?? '');
  if (!textarea || textarea.value === next) return false;
  textarea.value = next;
  textarea.dispatchEvent(new Event('input', { bubbles: true }));
  suppressBeforeUnload = false;
  return true;
}

function replaceTextToolCaptions(matchString, replaceWith, useRegex) {
  let occurrences = 0;
  let changedCards = 0;
  const regex = useRegex ? compileTextToolRegex(matchString) : null;
  textToolCaptionTextareas().forEach(textarea => {
    const current = textarea.value;
    let next = current;
    if (regex) {
      regex.lastIndex = 0;
      next = current.replace(regex, () => {
        occurrences += 1;
        return replaceWith;
      });
    } else {
      occurrences += current.split(matchString).length - 1;
      next = current.split(matchString).join(replaceWith);
    }
    if (setTextToolCaptionValue(textarea, next)) changedCards += 1;
  });
  return { occurrences, changedCards };
}

function findTextToolMatches(pattern) {
  const matches = [];
  textToolCaptionTextareas().forEach(textarea => {
    const regex = compileTextToolRegex(pattern);
    const card = textarea.closest('.pair-card');
    for (const match of textarea.value.matchAll(regex)) {
      matches.push({
        img_name: textarea.dataset.img || '',
        category: card?.dataset.category || '',
        start: match.index || 0,
        end: (match.index || 0) + String(match[0] || '').length,
      });
    }
  });
  return matches;
}

replaceForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  suppressBeforeUnload = false;

  const ok = await confirmReplace();
  if (!ok) return;

  const formData = new FormData(replaceForm);
  try {
    const matchString = String(formData.get('match_string') || '');
    const replaceWith = String(formData.get('replace_with') || '');
    const useRegex = formData.get('use_regex') === '1';
    if (!matchString) throw new Error('Search string cannot be empty.');
    const result = replaceTextToolCaptions(matchString, replaceWith, useRegex);
    if (toolsResult) {
      toolsResult.textContent =
        `Replaced ${result.occurrences} occurrence(s) on ${result.changedCards} card(s).\n` +
        'Changes are unsaved. Use a card Save button or Save all to write them to disk.';
    }
  } catch (err) {
    if (toolsResult) toolsResult.textContent = `Replace failed: ${err}`;
  }
});

function resetCountMatches() {
  countMatches = [];
  countMatchCursor = -1;
  if (countNextMatchBtn) countNextMatchBtn.disabled = true;
}

function selectCountMatch(match) {
  if (!match) return false;
  const category = categoryExists(match.category) ? match.category : '';
  if (category) openCategoryWindow(category);
  const card = document.querySelector(`.pair-card[data-img="${CSS.escape(match.img_name)}"]`);
  if (!card) return false;
  const win = card.closest('.category-window');
  if (win) bringCategoryWindowToFront(win);
  selectOnlyPair(card);
  card.scrollIntoView({ behavior: 'smooth', block: 'center', inline: 'nearest' });
  const ta = card.querySelector('.caption-textarea');
  if (ta) {
    const start = Math.max(0, Math.min(Number(match.start) || 0, ta.value.length));
    const end = Math.max(start, Math.min(Number(match.end) || start, ta.value.length));
    setTimeout(() => {
      ta.focus({ preventScroll: true });
      ta.setSelectionRange(start, end);
    }, 180);
  }
  setStatusbarMessage(`Match ${countMatchCursor + 1}/${countMatches.length}: ${match.img_name}`);
  return true;
}

function goToNextCountMatch() {
  if (!countMatches.length) return;
  for (let attempt = 0; attempt < countMatches.length; attempt += 1) {
    countMatchCursor = (countMatchCursor + 1) % countMatches.length;
    if (selectCountMatch(countMatches[countMatchCursor])) return;
  }
}

countNextMatchBtn?.addEventListener('click', goToNextCountMatch);

countForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  suppressBeforeUnload = false;

  const formData = new FormData(countForm);

  try {
    resetCountMatches();
    const pattern = String(formData.get('count_string') || '');
    if (!pattern) throw new Error('Count regex cannot be empty.');
    countMatches = findTextToolMatches(pattern);
    countMatchCursor = -1;
    if (countNextMatchBtn) countNextMatchBtn.disabled = !countMatches.length;
    if (toolsResult) {
      toolsResult.textContent = countMatches.length
        ? `Found ${countMatches.length} occurrence(s) in the current card text.\nUse Go to next to jump through matches.`
        : 'Found 0 occurrences in the current card text.';
    }
  } catch (err) {
    resetCountMatches();
    if (toolsResult) toolsResult.textContent = `Count failed: ${err}`;
  }
});


triggerForm?.addEventListener("submit", async (e) => {
  e.preventDefault();
  suppressBeforeUnload = false;

  const formData = new FormData(triggerForm);

  try {
    const triggerWord = String(formData.get('trigger_word') || '');
    if (!triggerWord) throw new Error('Trigger word cannot be empty.');
    let changedCards = 0;
    textToolCaptionTextareas().forEach(textarea => {
      const next = `${triggerWord}${String(textarea.value || '').trim()}`;
      if (setTextToolCaptionValue(textarea, next)) changedCards += 1;
    });
    if (toolsResult) {
      toolsResult.textContent =
        `Added the trigger word to ${changedCards} card(s).\n` +
        'Changes are unsaved. Use a card Save button or Save all to write them to disk.';
    }
  } catch (err) {
    if (toolsResult) toolsResult.textContent = `Add trigger word failed: ${err}`;
  }
});


function hasUnsavedChanges() {
  return getUnsavedCardIndexes().length > 0;
}

function resetAllUnsavedChanges() {
  resetCardsUnsavedChanges(Array.from(document.querySelectorAll('.pair-card')));
}

function resetCardsUnsavedChanges(cards) {
  (cards || []).forEach(card => {
    const index = parseInt(card.dataset.index, 10);
    const ta = card.querySelector('.caption-textarea');
    ta.value = ta.dataset.original ?? "";

    const state = ensureState(index);
    state.crop = null;
    state.upscale = false;
    state.rotation = 0;
    state.flipH = false;
    state.flipV = false;
    state.ratioLocked = false;
    state.lockedAspect = null;
    cropStates.set(index, state);
    resetMaskUnsaved(index);
    if (watermarkResults.has(index)) {
      watermarkResults.delete(index);
      const image = document.getElementById(`crop-image-${index}`);
      if (image) image.src = `/image/${encodeURIComponent(card.dataset.img)}?t=${Date.now()}`;
    }

    renderImageTransform(index);
    renderCrop(index);
    markUnsaved(index);
  });
}

function resetCategoryUnsavedChanges(category) {
  const cards = getCardsInCategory(category);
  if (!getUnsavedCardIndexes(cards).length) return;
  resetCardsUnsavedChanges(cards);
}

window.addEventListener('beforeunload', (e) => {
  if (suppressBeforeUnload) return;
  if (!hasUnsavedChanges()) return;
  e.preventDefault();
  e.returnValue = '';
});

function isTypingElement(element = document.activeElement) {
  const tag = (element?.tagName || '').toLowerCase();
  return (
    ['input', 'textarea', 'select'].includes(tag) ||
    !!element?.isContentEditable
  );
}

document.addEventListener('cut', (event) => {
  if (isTypingElement(event.target)) return;
  if (!selectedImgNames.size) return;
  event.preventDefault();
  event.stopPropagation();
  window.getSelection?.()?.removeAllRanges?.();
});

document.addEventListener('copy', (event) => {
  if (isTypingElement(event.target)) return;
  if (!selectedImgNames.size) return;
  event.preventDefault();
  event.stopPropagation();
  window.getSelection?.()?.removeAllRanges?.();
});

document.addEventListener('keydown', async (e) => {
  if (e.key === 'Escape') {
    closeJoyModal();
    closeMaskModal();
    closeSummaryModal();
    closeToolsModal();
    closeCategoryDialog();
    closeCategoryGroupDialog();
  }
  const isMod = e.ctrlKey || e.metaKey;
  const typingIntoField = isTypingElement();

  const isSave = (e.key === 's' || e.key === 'S');
  const isCopy = (e.key === 'c' || e.key === 'C');
  const isCut = (e.key === 'x' || e.key === 'X');
  const isPaste = (e.key === 'v' || e.key === 'V');
  const isSelectAll = (e.key === 'a' || e.key === 'A');
  if (isMod && !typingIntoField && isSelectAll) {
    e.preventDefault();
    e.stopPropagation();
    selectAllVisiblePairCards();
    return;
  }
  if (!typingIntoField && e.key === 'Delete' && selectedImgNames.size) {
    e.preventDefault();
    await deleteSelectedCards();
    return;
  }
  if (isMod && !typingIntoField && (isCopy || isCut)) {
    if (selectedImgNames.size) {
      e.preventDefault();
      e.stopPropagation();
      window.getSelection?.()?.removeAllRanges?.();
      setPairClipboardFromSelection(isCut ? 'move' : 'copy');
    }
    return;
  }
  if (isMod && !typingIntoField && isPaste) {
  if (pairClipboard?.img_names?.length) {
      e.preventDefault();
      if (typeof hasUnsavedChanges === 'function' && hasUnsavedChanges()) {
        const ok = await appConfirm('Move or copy selected pairs? Unsaved edits remain in the current view.');
        if (!ok) return;
      }
      try {
        const res = await fetch('/move_pairs', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            img_names: pairClipboard.img_names,
            category: activeCategory,
            mode: pairClipboard.mode,
          }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) {
          await appAlert(data.error || 'Move/copy failed.');
          return;
        }
        const targetCategory = data.category || activeCategory;
        if (data.mode === 'copy') {
          await importRenderedPairCards(data.changed || []);
        } else {
          const targetGrid = document.querySelector(`.category-window[data-category="${CSS.escape(targetCategory)}"] .grid`);
          (data.changed || []).forEach(imgName => {
            const card = document.querySelector(`.pair-card[data-img="${CSS.escape(imgName)}"]`);
            if (!card || !targetGrid) return;
            card.dataset.category = targetCategory;
            card.classList.remove('selected', 'cut-pending');
            targetGrid.append(card);
            applyCategorySizingToCard(card);
          });
          selectedImgNames.clear();
          updateAllWindowStatuses();
          refreshDesktopCategoryCountsFromCards();
        }
        clearPairClipboard();
        setStatusbarMessage(`${data.mode === 'copy' ? 'Copied' : 'Moved'} ${(data.changed || []).length} card${(data.changed || []).length === 1 ? '' : 's'} to ${targetCategory}.`);
      } catch (err) {
        await appAlert(`Move/copy failed: ${err}`);
      }
    }
    return;
  }

  if (isMod && isSave) {
    e.preventDefault();
    await saveAllCards();
    return;
  }

  const isFind = (e.key === 'f' || e.key === 'F');
  if (isMod && e.shiftKey && isFind) {
    if (!typingIntoField) {
      e.preventDefault();
      openToolsModal();
      document.getElementById('sr_match')?.focus();
    }
    return;
  }

  const isUndo = (e.key === 'z' || e.key === 'Z');
  if (isMod && isUndo) {
    if (!typingIntoField) {
      e.preventDefault();
      resetAllUnsavedChanges();
    }
    return;
  }

  if (e.key === 'Escape' && !typingIntoField) {
    clearPairClipboard();
    return;
  }
});

document.querySelectorAll('.crop-stage img').forEach(img => {
  img.addEventListener('load', () => {
    const m = img.id.match(/crop-image-(\d+)/);
    if (m) {
      const index = parseInt(m[1], 10);
      renderImageTransform(index);
      renderCrop(index);
    }
  });
});

function decodeCaptionFieldValue(value) {
  if (typeof value !== 'string') return '';
  try {
    return JSON.parse(value);
  } catch (e) {
    return value
      .replace(/\u0027/g, "'")
      .replace(/\u0022/g, '"')
      .replace(/\"/g, '"');
  }
}

document.querySelectorAll('.caption-textarea').forEach(ta => {
  const original = decodeCaptionFieldValue(ta.dataset.original ?? "");
  ta.dataset.original = original;
  ta.value = decodeCaptionFieldValue(ta.value);
  if (ta.value !== original) ta.value = original;
});
updateDimsColors();
updateAllCaptionStats();
updateSaveAllButtonState();

document.querySelectorAll(".pair-card").forEach(card => attachCardEventListeners(card));
</script>
</body>
</html>
'''


def build_pairs_context():
    pair_dicts = []
    for i, (img_name, text) in enumerate(pairs_cache):
        if not pair_exists(current_folder, img_name):
            continue
        pair_dicts.append(build_pair_dict(i, img_name, text))
    return pair_dicts


def build_category_icon_options(folders):
    options = []
    seen = set()
    for item in CATEGORY_DEFS:
        icon = item.get("icon")
        if icon and icon not in seen:
            options.append({"name": item.get("name", icon), "icon": icon})
            seen.add(icon)
    for item in folders or []:
        icon = item.get("icon") if isinstance(item, dict) else None
        if icon and icon not in seen and is_valid_category_icon(icon):
            options.append({"name": f"Custom: {icon}", "icon": icon})
            seen.add(icon)
    return options


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/", methods=["GET"])
def index():
    pair_dicts = build_pairs_context() if current_folder else []
    folders = category_folders or default_category_folders()
    category_counts = defaultdict(int)
    for pair in pair_dicts:
        category_counts[pair.get("category", DEFAULT_CATEGORY)] += 1
    category_context = []
    for item in folders:
        folder_item = dict(item)
        folder_item["count"] = category_counts.get(item.get("name"), 0)
        category_context.append(folder_item)
    bucket_options_json = json.dumps({
        str(base): get_bucket_options(base)
        for base in [512, 768, 1024, 1280, 1536]
    })
    return render_template_string(
        TEMPLATE,
        pairs=pair_dicts,
        message=message,
        folder_name=folder_name,
        selected_crop_base=selected_crop_base,
        bucket_options_json=bucket_options_json,
        joy_model_data_json=json.dumps(JOYCLI_MODEL_OPTIONS),
        category_defs_json=json.dumps(category_context),
        category_folders=category_context,
        category_groups=category_groups or default_category_groups(),
        icon_options=build_category_icon_options(category_context),
    )



@app.route("/add_files", methods=["POST"])
def add_files():
    global current_folder, pairs_cache, message, folder_name, category_assignments

    if not current_folder or not os.path.isdir(current_folder):
        message = "No folder is open. Open a folder first."
        return redirect(url_for("index"))

    root = tk.Tk()
    root.withdraw()
    try:
        root.attributes("-topmost", True)
    except Exception:
        pass

    filetypes = [
        ("Supported images", "*.png *.jpg *.jpeg *.gif *.bmp *.webp *.avif"),
        ("PNG", "*.png"),
        ("JPEG", "*.jpg *.jpeg"),
        ("GIF", "*.gif"),
        ("BMP", "*.bmp"),
        ("WEBP", "*.webp"),
        ("AVIF", "*.avif"),
        ("All files", "*.*"),
    ]

    selected_files = filedialog.askopenfilenames(
        title="Add Files",
        filetypes=filetypes,
    )

    try:
        root.destroy()
    except Exception:
        pass

    if not selected_files:
        return redirect(url_for("index"))

    target_category = normalize_category_name(request.form.get("category"))
    folder_path = Path(current_folder)
    added_count = 0

    for raw_path in selected_files:
        src_path = Path(raw_path)
        if not src_path.exists():
            continue
        if src_path.suffix.lower() not in IMAGE_EXTENSIONS:
            continue

        dest_path = folder_path / src_path.name

        try:
            same_file = dest_path.exists() and src_path.resolve() == dest_path.resolve()
        except Exception:
            same_file = False

        if same_file:
            txt_path = dest_path.with_suffix(".txt")
            if not txt_path.exists():
                txt_path.write_text("", encoding="utf-8")
            continue

        if dest_path.exists():
            continue

        shutil.copy2(src_path, dest_path)
        txt_path = dest_path.with_suffix(".txt")
        if not txt_path.exists():
            txt_path.write_text("", encoding="utf-8")
        category_assignments[dest_path.name] = category_id_for_value(target_category)
        added_count += 1

    pairs_cache = load_pairs(current_folder)
    save_category_assignments(current_folder, category_assignments)
    folder_name = os.path.basename(current_folder) if current_folder else ""
    message = f"Added {added_count} file(s)." if added_count else "No new files were added."
    return redirect(url_for("index"))


@app.route("/upload_images", methods=["POST"])
def upload_images():
    global current_folder, pairs_cache, message, folder_name, category_assignments

    if not current_folder or not os.path.isdir(current_folder):
        return jsonify({"ok": False, "error": "Open a folder before adding images."}), 400

    uploads = request.files.getlist("images")
    if not uploads:
        return jsonify({"ok": False, "error": "No image files were uploaded."}), 400

    target_category = normalize_category_name(request.form.get("category"))
    convert_to_png = str(request.form.get("convert_to_png") or "").lower() in {"1", "true", "yes", "on"}
    folder_path = Path(current_folder)
    added = []
    skipped = []
    converted = 0
    seen_upload_hashes = set()

    for i, storage in enumerate(uploads, start=1):
        ext = upload_image_extension(storage)
        if not ext:
            skipped.append(storage.filename or f"file_{i}")
            continue

        data = storage.read()
        if not data:
            skipped.append(storage.filename or f"file_{i}")
            continue

        digest = hashlib.sha256(data).hexdigest()
        if digest in seen_upload_hashes:
            skipped.append(storage.filename or f"file_{i}")
            continue
        seen_upload_hashes.add(digest)

        fallback = "pasted_image" if not storage.filename else "image"
        stem = clean_upload_stem(storage.filename, fallback=fallback)
        convert_current = convert_to_png and ext.lower() != ".png"
        target_ext = ".png" if convert_current else ext
        target_name = make_unique_image_name(current_folder, stem, target_ext)
        target_path = folder_path / target_name

        try:
            save_uploaded_image(target_path, data, convert_to_png=convert_current)
        except Exception:
            skipped.append(storage.filename or f"file_{i}")
            try:
                if target_path.exists():
                    target_path.unlink()
            except Exception:
                pass
            continue
        txt_path = target_path.with_suffix(".txt")
        if not txt_path.exists():
            txt_path.write_text("", encoding="utf-8")
        category_assignments[target_name] = category_id_for_value(target_category)
        added.append(target_name)
        if convert_current:
            converted += 1

    pairs_cache = load_pairs(current_folder)
    save_category_assignments(current_folder, category_assignments)
    folder_name = os.path.basename(current_folder) if current_folder else ""

    if added:
        message = f"Added {len(added)} image(s)."
        if converted:
            message += f" Converted {converted} to PNG."
    else:
        message = "No supported image files were added."

    return jsonify({
        "ok": True,
        "added": added,
        "skipped": skipped,
        "converted": converted,
        "message": message,
    })


@app.route("/open_folder", methods=["POST"])
def open_folder():
    global current_folder, pairs_cache, message, folder_name, category_assignments, category_folders, category_groups, selected_crop_base
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory()
    root.destroy()
    ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"

    if not folder:
        if ajax:
            return jsonify({"ok": True, "selected": False})
        return redirect(url_for("index"))

    current_folder = folder
    folder_name = folder
    write_image_folder_handoff(current_folder)
    category_assignments, category_folders, category_groups = load_category_state(folder)
    missing = ensure_missing_txt(folder)
    if missing:
        default_caption = request.form.get("default_caption", "")
        for txt_path in missing:
            with open(txt_path, "w", encoding="utf-8") as f:
                f.write(default_caption)
        message = f"Created {len(missing)} missing caption file(s)."
    else:
        message = ""

    pairs_cache = load_pairs(folder)
    selected_crop_base = choose_auto_crop_base_resolution(folder)
    non_png_count = sum(1 for img_name, _ in pairs_cache if os.path.splitext(img_name)[1].lower() != ".png")
    if ajax:
        return jsonify({"ok": True, "selected": True, "non_png_count": non_png_count})
    return redirect(url_for("index"))


@app.route("/refresh_folder", methods=["POST"])
def refresh_folder():
    global pairs_cache, message, category_assignments, selected_crop_base
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    before = {name for name, _ in pairs_cache}
    missing = ensure_missing_txt(current_folder)
    for txt_path in missing:
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write("")

    pairs_cache = load_pairs(current_folder)
    after = {name for name, _ in pairs_cache}
    added_names = sorted(after - before)
    if added_names:
        updated = dict(category_assignments)
        for img_name in added_names:
            updated.setdefault(img_name, DEFAULT_CATEGORY)
        category_assignments = updated
        save_category_assignments(current_folder, category_assignments)
    selected_crop_base = choose_auto_crop_base_resolution(current_folder)
    message = f"Refreshed folder. Added {len(added_names)} image(s)."
    if missing:
        message += f" Created {len(missing)} caption file(s)."
    return jsonify({"ok": True, "added": len(added_names), "captions_created": len(missing), "total": len(pairs_cache)})


@app.route("/convert_images_to_png", methods=["POST"])
def convert_images_to_png():
    global pairs_cache, message, category_assignments
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    image_names = [f for f in sorted(os.listdir(current_folder)) if f.lower().endswith(IMAGE_EXTENSIONS)]
    if not image_names:
        return jsonify({"ok": False, "error": "No images found."}), 400

    converted = 0
    skipped = []
    converted_pairs = []
    updated_categories = dict(category_assignments)

    for img_name in image_names:
        src_path = os.path.join(current_folder, img_name)
        stem, ext = os.path.splitext(img_name)
        ext_lower = ext.lower()

        if ext_lower == '.png':
            continue

        target_name = stem + '.png'
        target_path = os.path.join(current_folder, target_name)

        if os.path.exists(target_path):
            skipped.append(img_name)
            continue

        try:
            with Image.open(src_path) as im:
                save_im = im
                if getattr(im, 'mode', None) not in ('RGB', 'RGBA', 'L', 'LA'):
                    save_im = im.convert('RGBA' if 'A' in im.getbands() else 'RGB')
                save_im.save(target_path, format='PNG', compress_level=0, optimize=False)
            src_mask = mask_path_for_image(current_folder, img_name)
            target_mask = mask_path_for_image(current_folder, target_name)
            if src_mask and target_mask and src_mask.exists():
                target_mask.parent.mkdir(exist_ok=True)
                with Image.open(src_mask) as mask_img:
                    mask_img.convert("L").save(target_mask, format="PNG", compress_level=0, optimize=False)
                os.remove(src_mask)
            os.remove(src_path)
            if img_name in updated_categories:
                updated_categories[target_name] = updated_categories.pop(img_name)
            converted += 1
            converted_pairs.append({"old_name": img_name, "new_name": target_name})
        except Exception:
            skipped.append(img_name)
            try:
                if os.path.exists(target_path):
                    os.remove(target_path)
            except Exception:
                pass

    category_assignments.clear()
    category_assignments.update(updated_categories)
    save_category_assignments(current_folder, category_assignments)
    pairs_cache = load_pairs(current_folder)

    msg = f"Converted {converted} image(s) to PNG."
    if skipped:
        preview = ', '.join(skipped[:5])
        if len(skipped) > 5:
            preview += f" and {len(skipped) - 5} more"
        msg += f" Skipped {len(skipped)} image(s): {preview}."
    message = msg
    return jsonify({"ok": True, "converted": converted, "converted_pairs": converted_pairs, "skipped": skipped, "message": msg})


@app.route("/close_folder", methods=["POST"])
def close_folder():
    global current_folder, pairs_cache, message, folder_name, category_assignments, category_folders, category_groups

    if joycaption_status.get("running"):
        joycaption_status["interrupt_requested"] = True
        stop_kobold_process()
        joycaption_status["status"] = "Caption: interrupting…"

    current_folder = None
    pairs_cache = []
    message = ""
    folder_name = ""
    category_assignments = {}
    category_folders = []
    category_groups = []
    write_image_folder_handoff(None)
    return redirect(url_for("index"))


@app.route("/image/<path:filename>")
def image(filename):
    return send_from_directory(current_folder, filename)


@app.route("/mask/<path:filename>")
def mask_image(filename):
    if not current_folder:
        return "No folder opened.", 404
    mask_path = ensure_mask_for_image(current_folder, filename)
    if not mask_path or not mask_path.exists():
        return "Mask not found.", 404
    return send_from_directory(mask_path.parent, mask_path.name)


@app.route("/ensure_masks", methods=["POST"])
def ensure_masks():
    global message
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400
    try:
        created, existing = ensure_masks_for_folder(current_folder)
    except Exception as e:
        return jsonify({"ok": False, "error": f"Failed to create masks: {e}"}), 500
    message = f"Masking mode: {created} mask file(s) created, {existing} existing."
    return jsonify({"ok": True, "created": created, "existing": existing, "message": message})


@app.route("/save_mask", methods=["POST"])
def save_mask():
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400
    data = request.get_json(force=True) or {}
    img_name = Path(str(data.get("img_name") or "")).name
    data_url = str(data.get("mask_data") or "")
    if not img_name:
        return jsonify({"ok": False, "error": "Missing image name."}), 400
    if not data_url.startswith("data:image/png;base64,"):
        return jsonify({"ok": False, "error": "Missing mask data."}), 400
    mask_path = ensure_mask_for_image(current_folder, img_name)
    if not mask_path:
        return jsonify({"ok": False, "error": "Mask target not found."}), 404
    try:
        raw = base64.b64decode(data_url.split(",", 1)[1])
        with Image.open(io.BytesIO(raw)) as mask_img:
            mask_img = mask_img.convert("L")
            src_path = Path(current_folder) / img_name
            with Image.open(src_path) as src_img:
                if mask_img.size != src_img.size:
                    mask_img = mask_img.resize(src_img.size, Image.NEAREST)
            try:
                mask_img.save(mask_path)
            except Exception:
                mask_img.save(mask_path, format="PNG")
    except Exception as e:
        return jsonify({"ok": False, "error": f"Mask save failed: {e}"}), 500
    return jsonify({"ok": True})


@app.route("/auto_mask", methods=["POST"])
def auto_mask():
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400
    data = request.get_json(force=True) or {}
    img_name = Path(str(data.get("img_name") or "")).name
    model_name = str(data.get("model") or DEFAULT_AUTO_MASK_MODEL).strip() or DEFAULT_AUTO_MASK_MODEL
    post_process_mask = bool(data.get("post_process_mask", True))
    expand_pixels = data.get("expand_pixels", 0)
    feather_pixels = data.get("feather_pixels", 0)
    if not img_name:
        return jsonify({"ok": False, "error": "Missing image name."}), 400
    if not pair_exists(current_folder, img_name):
        return jsonify({"ok": False, "error": "Image no longer exists."}), 404
    try:
        mask_bytes = auto_mask_image_bytes(
            current_folder,
            img_name,
            model_name,
            post_process_mask=post_process_mask,
            expand_pixels=expand_pixels,
            feather_pixels=feather_pixels,
        )
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500
    data_url = "data:image/png;base64," + base64.b64encode(mask_bytes).decode("ascii")
    return jsonify({"ok": True, "model": model_name, "mask_data": data_url})


@app.route("/remove_watermark", methods=["POST"])
def remove_watermark():
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400
    data = request.get_json(force=True) or {}
    img_name = Path(str(data.get("img_name") or "")).name
    if not img_name or not pair_exists(current_folder, img_name):
        return jsonify({"ok": False, "error": "Image no longer exists."}), 404
    try:
        result = remove_watermark_image(
            current_folder,
            img_name,
            data.get("mask_data"),
            backend=data.get("backend", "lama"),
            radius=data.get("radius", 3),
            expand_pixels=data.get("expand_pixels", 2),
            source_data=data.get("source_data", ""),
        )
        output = io.BytesIO()
        result.save(output, format="PNG", compress_level=1)
        result_data = "data:image/png;base64," + base64.b64encode(output.getvalue()).decode("ascii")
    except Exception as exc:
        return jsonify({"ok": False, "error": str(exc)}), 500
    return jsonify({"ok": True, "result_data": result_data})


@app.route("/category_icon/<path:filename>")
def category_icon(filename):
    return send_from_directory(APP_DIR / "images", filename)


@app.route("/upload_category_icon", methods=["POST"])
def upload_category_icon():
    storage = request.files.get("icon")
    if not storage:
        return jsonify({"ok": False, "error": "No icon file was uploaded."}), 400

    ext = upload_image_extension(storage)
    if not ext:
        return jsonify({"ok": False, "error": "Unsupported icon file type."}), 400

    data = storage.read()
    if not data:
        return jsonify({"ok": False, "error": "Icon file is empty."}), 400

    try:
        with Image.open(io.BytesIO(data)) as img:
            img.verify()
    except Exception:
        return jsonify({"ok": False, "error": "The selected file is not a valid image."}), 400

    stem = clean_upload_stem(storage.filename, fallback="category_icon")
    digest = hashlib.sha256(data).hexdigest()[:10]
    target_name = f"category_icon_{stem}_{digest}{ext}"
    target_path = APP_DIR / "images" / target_name
    if not target_path.exists():
        target_path.write_bytes(data)
    return jsonify({"ok": True, "icon": target_name})


@app.route("/switch/simple", methods=["POST", "GET"])
def switch_to_simple():
    remember_app("simple")
    write_image_folder_handoff(current_folder)
    launch_local_app_after_port_closes("imageprep_simple.py", 5000)
    exit_soon()
    return switch_page("http://127.0.0.1:5000/", "Default mode", initial_delay_ms=2200)


@app.route("/rename_all_pairs", methods=["POST"])
def rename_all_pairs():
    global pairs_cache, message, category_assignments
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    data = request.get_json(force=True) or {}
    prefix = str(data.get("prefix", ""))
    category = data.get("category") or None
    category = normalize_category_name(category) if category else None

    ordered_pairs = [
        pair for pair in pairs_cache
        if pair_exists(current_folder, pair[0])
        and (not category or get_pair_category(pair[0]) == category)
    ]
    if not ordered_pairs:
        return jsonify({"ok": False, "error": "No image/text pairs found."}), 400

    temp_records = []
    renamed_categories = dict(category_assignments)
    try:
        for i, (img_name, _) in enumerate(ordered_pairs):
            img_path = os.path.join(current_folder, img_name)
            txt_path = os.path.splitext(img_path)[0] + ".txt"
            json_path = os.path.splitext(img_path)[0] + ".json"
            ext = os.path.splitext(img_name)[1]
            target_stem = f"{prefix}{i:05d}"
            temp_img = os.path.join(current_folder, f"__renaming__{i:05d}{ext}")
            temp_txt = os.path.join(current_folder, f"__renaming__{i:05d}.txt")
            temp_json = os.path.join(current_folder, f"__renaming__{i:05d}.json")
            mask_path = mask_path_for_image(current_folder, img_name)
            temp_mask = mask_dir_for_folder(current_folder) / f"__renaming__{i:05d}{ext}"

            os.replace(img_path, temp_img)
            had_txt = os.path.exists(txt_path)
            if had_txt:
                os.replace(txt_path, temp_txt)
            had_json = os.path.exists(json_path)
            if had_json:
                os.replace(json_path, temp_json)
            had_mask = bool(mask_path and mask_path.exists())
            if had_mask:
                temp_mask.parent.mkdir(exist_ok=True)
                os.replace(mask_path, temp_mask)
            temp_records.append((temp_img, temp_txt, temp_json, temp_mask, target_stem, ext, had_txt, had_json, had_mask, img_name))

        for temp_img, temp_txt, temp_json, temp_mask, target_stem, ext, had_txt, had_json, had_mask, old_img_name in temp_records:
            final_img = os.path.join(current_folder, f"{target_stem}{ext}")
            final_txt = os.path.join(current_folder, f"{target_stem}.txt")
            final_json = os.path.join(current_folder, f"{target_stem}.json")
            os.replace(temp_img, final_img)
            if had_txt:
                os.replace(temp_txt, final_txt)
            if had_json:
                os.replace(temp_json, final_json)
            final_name = os.path.basename(final_img)
            if had_mask:
                final_mask = mask_path_for_image(current_folder, final_name)
                if final_mask:
                    final_mask.parent.mkdir(exist_ok=True)
                    os.replace(temp_mask, final_mask)
            renamed_categories[final_name] = category_id_for_value(category_assignments.get(old_img_name, DEFAULT_CATEGORY))
            if final_name != old_img_name:
                renamed_categories.pop(old_img_name, None)

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    pairs_cache = load_pairs(current_folder)
    category_assignments = renamed_categories
    save_category_assignments(current_folder, category_assignments)
    message = f"Renamed {len(ordered_pairs)} image/text pair(s)" + (f" in {category}." if category else ".")
    return jsonify({"ok": True})


@app.route("/captions_json")
def captions_json():
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    caption_format = normalize_caption_format(request.args.get("caption_format"))
    pairs = []
    for i, (img_name, text) in enumerate(pairs_cache):
        if not pair_exists(current_folder, img_name):
            continue
        txt_path = caption_sidecar_path(os.path.join(current_folder, img_name), caption_format)
        try:
            text = Path(txt_path).read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""
        if caption_format == CAPTION_FORMAT_IDEOGRAM4_JSON and text:
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except (TypeError, json.JSONDecodeError):
                pass
        pairs.append({
            "index": i,
            "img_name": img_name,
            "text": text,
        })

    return jsonify({"ok": True, "pairs": pairs})


@app.route("/ideogram_json_validate", methods=["POST"])
def ideogram_json_validate():
    data = request.get_json(force=True) or {}
    raw_caption = data.get("caption", "")
    try:
        normalized = normalize_ideogram4_caption(raw_caption)
        pretty = json.dumps(json.loads(normalized), ensure_ascii=False, indent=2)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400
    return jsonify({"ok": True, "caption": pretty})


@app.route("/ideogram_json_validate_all")
def ideogram_json_validate_all():
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    items = []
    counts = {"valid": 0, "repairable": 0, "invalid": 0, "missing": 0}
    for img_name, _ in pairs_cache:
        if not pair_exists(current_folder, img_name):
            continue
        json_path = caption_sidecar_path(os.path.join(current_folder, img_name), CAPTION_FORMAT_IDEOGRAM4_JSON)
        if not os.path.exists(json_path):
            counts["missing"] += 1
            items.append({"img_name": img_name, "status": "missing", "message": "Missing JSON caption."})
            continue
        try:
            raw = Path(json_path).read_text(encoding="utf-8")
            validate_ideogram4_caption(json.loads(raw))
            counts["valid"] += 1
            items.append({"img_name": img_name, "status": "valid", "message": "Valid."})
        except Exception as strict_exc:
            try:
                normalize_ideogram4_caption(raw)
                counts["repairable"] += 1
                items.append({"img_name": img_name, "status": "repairable", "message": str(strict_exc)})
            except Exception as repair_exc:
                counts["invalid"] += 1
                items.append({"img_name": img_name, "status": "invalid", "message": str(repair_exc)})

    return jsonify({"ok": True, "counts": counts, "items": items})


@app.route("/save_pair", methods=["POST"])
def save_pair():
    global pairs_cache, message
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    data = request.get_json(force=True)
    img_name = data.get("img_name")
    caption = data.get("caption", "")
    caption_format = normalize_caption_format(data.get("caption_format"))
    crop = data.get("crop")
    transforms = data.get("transforms") or {}
    watermark_result = data.get("watermark_result") or ""

    if not img_name:
        return jsonify({"ok": False, "error": "Missing image name."}), 400

    img_path = os.path.join(current_folder, img_name)
    txt_path = caption_sidecar_path(img_path, caption_format)

    try:
        if caption_format == CAPTION_FORMAT_IDEOGRAM4_JSON:
            caption = normalize_ideogram4_caption(caption)
        with open(txt_path, "w", encoding="utf-8") as f:
            f.write(caption)
    except ValueError as exc:
        return jsonify({"ok": False, "error": str(exc)}), 400

    if watermark_result:
        try:
            watermark_img = decode_png_data_url(watermark_result, "watermark preview")
            with Image.open(img_path) as original:
                src_format = original.format
            if src_format in {"JPEG", "BMP"}:
                watermark_img = watermark_img.convert("RGB")
            save_kwargs = {"compress_level": 4} if src_format == "PNG" else {}
            watermark_img.save(img_path, **save_kwargs)
        except Exception as e:
            return jsonify({"ok": False, "error": f"Watermark save failed: {e}"}), 500

    has_transform = bool(
        transforms.get("flipH") or transforms.get("flipV") or float(transforms.get("rotation", 0) or 0)
    )
    if crop or has_transform:
        try:
            with Image.open(img_path) as img:
                src_format = img.format

                if transforms.get("flipH"):
                    img = img.transpose(Image.FLIP_LEFT_RIGHT)
                if transforms.get("flipV"):
                    img = img.transpose(Image.FLIP_TOP_BOTTOM)

                rotation = float(transforms.get("rotation", 0) or 0)
                if rotation:
                    # When a crop is selected, keep the rotation within the original
                    # image canvas so the saved crop matches the crop box the user
                    # saw in the UI. The crop UI operates in the unexpanded stage
                    # coordinate space, so expand=True would shift the crop region.
                    img = img.rotate(
                        -rotation,
                        expand=not bool(crop),
                        resample=Image.BICUBIC,
                    )

                if crop:
                    x = int(round(crop["x"]))
                    y = int(round(crop["y"]))
                    w = int(round(crop["w"]))
                    h = int(round(crop["h"]))
                    target_w = int(crop["targetW"])
                    target_h = int(crop["targetH"])
                    img = img.crop((x, y, x + w, y + h)).resize((target_w, target_h), Image.LANCZOS)

                save_kwargs = {}
                if src_format == "PNG":
                    save_kwargs["compress_level"] = 4
                img.save(img_path, **save_kwargs)

            mask_path = mask_path_for_image(current_folder, img_name)
            if mask_path and mask_path.exists():
                with Image.open(mask_path) as mask_img:
                    mask_img = mask_img.convert("L")

                    if transforms.get("flipH"):
                        mask_img = mask_img.transpose(Image.FLIP_LEFT_RIGHT)
                    if transforms.get("flipV"):
                        mask_img = mask_img.transpose(Image.FLIP_TOP_BOTTOM)

                    rotation = float(transforms.get("rotation", 0) or 0)
                    if rotation:
                        mask_img = mask_img.rotate(
                            -rotation,
                            expand=not bool(crop),
                            resample=Image.NEAREST,
                        )

                    if crop:
                        x = int(round(crop["x"]))
                        y = int(round(crop["y"]))
                        w = int(round(crop["w"]))
                        h = int(round(crop["h"]))
                        target_w = int(crop["targetW"])
                        target_h = int(crop["targetH"])
                        mask_img = mask_img.crop((x, y, x + w, y + h)).resize((target_w, target_h), Image.NEAREST)

                    try:
                        mask_img.save(mask_path)
                    except Exception:
                        mask_img.save(mask_path, format="PNG")
        except Exception as e:
            return jsonify({"ok": False, "error": f"Save failed: {e}"}), 500

    pairs_cache = load_pairs(current_folder)
    updated_pair = None
    for i, (name, text) in enumerate(pairs_cache):
        if name == img_name:
            updated_pair = build_pair_dict(i, name, text)
            break
    return jsonify({"ok": True, "updated_pair": updated_pair})



@app.route("/clone_pair", methods=["POST"])
def clone_pair():
    global pairs_cache, message, category_assignments
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    data = request.get_json(force=True) or {}
    img_name = data.get("img_name")
    if not img_name:
        return jsonify({"ok": False, "error": "Missing image name."}), 400

    src_img = os.path.join(current_folder, img_name)
    if not os.path.exists(src_img):
        return jsonify({"ok": False, "error": "Image no longer exists."}), 404

    stem, ext = os.path.splitext(img_name)
    target_name = make_unique_image_name(current_folder, stem, ext)
    target_img = os.path.join(current_folder, target_name)
    src_txt = os.path.splitext(src_img)[0] + ".txt"
    target_txt = os.path.splitext(target_img)[0] + ".txt"
    src_json = os.path.splitext(src_img)[0] + ".json"
    target_json = os.path.splitext(target_img)[0] + ".json"

    try:
        shutil.copy2(src_img, target_img)
        if os.path.exists(src_txt):
            shutil.copy2(src_txt, target_txt)
        else:
            Path(target_txt).write_text("", encoding="utf-8")
        if os.path.exists(src_json):
            shutil.copy2(src_json, target_json)
        src_mask = mask_path_for_image(current_folder, img_name)
        target_mask = mask_path_for_image(current_folder, target_name)
        if src_mask and target_mask and src_mask.exists():
            target_mask.parent.mkdir(exist_ok=True)
            shutil.copy2(src_mask, target_mask)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    category_assignments[target_name] = category_id_for_value(category_assignments.get(img_name, DEFAULT_CATEGORY))
    save_category_assignments(current_folder, category_assignments)
    pairs_cache = load_pairs(current_folder)

    pair = None
    for i, (name, text_value) in enumerate(pairs_cache):
        if name == target_name:
            pair = build_pair_dict(i, name, text_value)
            break
    return jsonify({"ok": True, "pair": pair})


@app.route("/rename_pair", methods=["POST"])
def rename_pair():
    global pairs_cache, message, category_assignments
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    data = request.get_json(force=True) or {}
    img_name = data.get("img_name")
    new_stem = str(data.get("new_stem", "")).strip()
    if not img_name:
        return jsonify({"ok": False, "error": "Missing image name."}), 400
    if not new_stem:
        return jsonify({"ok": False, "error": "Filename cannot be empty."}), 400
    if re.search(r'[\\/:*?"<>|]', new_stem):
        return jsonify({"ok": False, "error": "Filename contains invalid characters."}), 400

    src_img = os.path.join(current_folder, img_name)
    if not os.path.exists(src_img):
        return jsonify({"ok": False, "error": "Image no longer exists."}), 404

    ext = os.path.splitext(img_name)[1]
    target_name = make_unique_image_name(current_folder, new_stem, ext, exclude_name=img_name)
    src_txt = os.path.splitext(src_img)[0] + ".txt"
    src_json = os.path.splitext(src_img)[0] + ".json"
    target_img = os.path.join(current_folder, target_name)
    target_txt = os.path.splitext(target_img)[0] + ".txt"
    target_json = os.path.splitext(target_img)[0] + ".json"

    if target_name != img_name:
        try:
            os.replace(src_img, target_img)
            if os.path.exists(src_txt):
                os.replace(src_txt, target_txt)
            if os.path.exists(src_json):
                os.replace(src_json, target_json)
            src_mask = mask_path_for_image(current_folder, img_name)
            target_mask = mask_path_for_image(current_folder, target_name)
            if src_mask and target_mask and src_mask.exists():
                target_mask.parent.mkdir(exist_ok=True)
                os.replace(src_mask, target_mask)
        except Exception as e:
            return jsonify({"ok": False, "error": str(e)}), 500

        if img_name in category_assignments:
            category_assignments[target_name] = category_assignments.pop(img_name)
        save_category_assignments(current_folder, category_assignments)

    pairs_cache = load_pairs(current_folder)
    pair = None
    for i, (name, text_value) in enumerate(pairs_cache):
        if name == target_name:
            pair = build_pair_dict(i, name, text_value)
            break
    return jsonify({"ok": True, "pair": pair})

@app.route("/delete_pair", methods=["POST"])
def delete_pair():
    global pairs_cache, message, category_assignments
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    data = request.get_json(force=True)
    img_name = data.get("img_name")
    if not img_name:
        return jsonify({"ok": False, "error": "Missing image name."}), 400

    img_path = os.path.join(current_folder, img_name)
    try:
        if os.path.exists(img_path):
            os.remove(img_path)
        for sidecar_path in caption_sidecar_paths(img_path):
            if os.path.exists(sidecar_path):
                os.remove(sidecar_path)
        mask_path = mask_path_for_image(current_folder, img_name)
        if mask_path and mask_path.exists():
            os.remove(mask_path)
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    category_assignments.pop(img_name, None)
    save_category_assignments(current_folder, category_assignments)
    pairs_cache = load_pairs(current_folder)
    return jsonify({"ok": True})


@app.route("/set_category", methods=["POST"])
def set_category():
    global category_assignments
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    data = request.get_json(force=True) or {}
    img_name = data.get("img_name")
    category = normalize_category_name(data.get("category"))
    if not img_name:
        return jsonify({"ok": False, "error": "Missing image name."}), 400
    if not pair_exists(current_folder, img_name):
        return jsonify({"ok": False, "error": "Image no longer exists."}), 404

    category_assignments[img_name] = category_id_for_value(category)
    save_category_assignments(current_folder, category_assignments)
    return jsonify({
        "ok": True,
        "category": category,
        "icon": category_icon_for_name(category),
    })


def unique_category_name(base_name, exclude_name=None):
    base = re.sub(r"\s+", " ", str(base_name or "")).strip() or "New Category"
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", base).strip() or "New Category"
    names = category_names_from_folders()
    if exclude_name:
        names.discard(exclude_name)
    candidate = base
    counter = 2
    while candidate in names:
        candidate = f"{base} {counter}"
        counter += 1
    return candidate


def unique_category_group_name(base_name, exclude_id=None):
    base = re.sub(r"\s+", " ", str(base_name or "")).strip() or "New Group"
    base = re.sub(r'[<>:"/\\|?*\x00-\x1f]+', "_", base).strip() or "New Group"
    names = {
        str(item.get("name") or "").casefold()
        for item in category_groups
        if item.get("id") != exclude_id
    }
    candidate = base
    counter = 2
    while candidate.casefold() in names:
        candidate = f"{base} {counter}"
        counter += 1
    return candidate


@app.route("/create_category_group", methods=["POST"])
def create_category_group():
    global category_groups
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400
    data = request.get_json(force=True) or {}
    name = unique_category_group_name(data.get("name") or "New Group")
    base_id = normalize_category_group_id(name) or "group"
    group_id = base_id
    existing_ids = {item.get("id") for item in category_groups}
    counter = 2
    while group_id in existing_ids:
        group_id = f"{base_id}-{counter}"
        counter += 1
    color = str(data.get("color") or DEFAULT_CATEGORY_COLORS[len(category_groups) % len(DEFAULT_CATEGORY_COLORS)])
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        color = DEFAULT_CATEGORY_COLORS[len(category_groups) % len(DEFAULT_CATEGORY_COLORS)]
    group = {"id": group_id, "name": name, "color": color}
    category_groups.append(group)
    save_category_state(current_folder, category_assignments, category_folders, category_groups)
    return jsonify({"ok": True, "group": group})


@app.route("/update_category_group", methods=["POST"])
def update_category_group():
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400
    data = request.get_json(force=True) or {}
    group_id = normalize_category_group_id(data.get("id"))
    group = category_group_for_id(group_id)
    if not group:
        return jsonify({"ok": False, "error": "Category group not found."}), 404
    color = str(data.get("color") or group.get("color") or "#9ca3af")
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        color = group.get("color") or "#9ca3af"
    group.update({
        "name": unique_category_group_name(data.get("name") or group.get("name"), exclude_id=group_id),
        "color": color,
    })
    save_category_state(current_folder, category_assignments, category_folders, category_groups)
    return jsonify({"ok": True, "group": group})


@app.route("/delete_category_group", methods=["POST"])
def delete_category_group():
    global category_groups
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400
    data = request.get_json(force=True) or {}
    group_id = normalize_category_group_id(data.get("id"))
    if group_id == DEFAULT_CATEGORY_GROUP_ID:
        return jsonify({"ok": False, "error": "Uncategorized cannot be deleted."}), 400
    if not category_group_for_id(group_id):
        return jsonify({"ok": False, "error": "Category group not found."}), 404
    category_groups = [item for item in category_groups if item.get("id") != group_id]
    for folder in category_folders:
        if folder.get("group_id") == group_id:
            folder["group_id"] = DEFAULT_CATEGORY_GROUP_ID
    save_category_state(current_folder, category_assignments, category_folders, category_groups)
    return jsonify({"ok": True})


@app.route("/create_category", methods=["POST"])
def create_category():
    global category_folders
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    data = request.get_json(force=True) or {}
    index = len(category_folders)
    name = unique_category_name(data.get("name") or "New Category")
    icon = str(data.get("icon") or CATEGORY_NAME_TO_ICON[DEFAULT_CATEGORY])
    if icon not in {item["icon"] for item in CATEGORY_DEFS} and not is_valid_category_icon(icon):
        icon = CATEGORY_NAME_TO_ICON[DEFAULT_CATEGORY]
    color = str(data.get("color") or DEFAULT_CATEGORY_COLORS[index % len(DEFAULT_CATEGORY_COLORS)])
    folder = normalize_category_folder(
        {
            "name": name,
            "icon": icon,
            "color": color,
            "group_id": data.get("group_id") if category_group_for_id(data.get("group_id"), category_groups) else DEFAULT_CATEGORY_GROUP_ID,
            "x": data.get("x", (index % 5) * 112),
            "y": data.get("y", (index // 5) * 120),
        },
        index,
        {item.get("name") for item in category_folders},
    )
    category_folders.append(folder)
    save_category_state(current_folder, category_assignments, category_folders, category_groups)
    return jsonify({"ok": True, "category": folder})


@app.route("/update_category", methods=["POST"])
def update_category():
    global category_folders, category_assignments
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    data = request.get_json(force=True) or {}
    old_name = str(data.get("old_name") or "").strip()
    if not old_name:
        return jsonify({"ok": False, "error": "Missing category name."}), 400

    target = None
    for item in category_folders:
        if item.get("name") == old_name:
            target = item
            break
    if target is None:
        return jsonify({"ok": False, "error": "Category not found."}), 404

    new_name = unique_category_name(data.get("name") or old_name, exclude_name=old_name)
    icon = str(data.get("icon") or target.get("icon") or CATEGORY_NAME_TO_ICON[DEFAULT_CATEGORY])
    if icon not in {item["icon"] for item in CATEGORY_DEFS} and not is_valid_category_icon(icon):
        icon = CATEGORY_NAME_TO_ICON[DEFAULT_CATEGORY]
    color = str(data.get("color") or target.get("color") or "#9ca3af")
    if not re.fullmatch(r"#[0-9A-Fa-f]{6}", color):
        color = target.get("color") or "#9ca3af"

    target.update({
        "name": new_name,
        "icon": icon,
        "color": color,
        "group_id": data.get("group_id") if category_group_for_id(data.get("group_id"), category_groups) else target.get("group_id", DEFAULT_CATEGORY_GROUP_ID),
    })
    if "x" in data:
        target["x"] = max(0, int(data.get("x") or 0))
    if "y" in data:
        target["y"] = max(0, int(data.get("y") or 0))

    save_category_state(current_folder, category_assignments, category_folders, category_groups)
    return jsonify({"ok": True, "category": target})


@app.route("/delete_category", methods=["POST"])
def delete_category():
    global category_folders, category_assignments
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    data = request.get_json(force=True) or {}
    name = str(data.get("name") or "").strip()
    if not name:
        return jsonify({"ok": False, "error": "Missing category name."}), 400
    if name == DEFAULT_CATEGORY:
        return jsonify({"ok": False, "error": "Undefined cannot be deleted."}), 400

    target = category_folder_for_value(name)
    target_id = target.get("id") if target else name
    original_len = len(category_folders)
    category_folders = [item for item in category_folders if item.get("name") != name]
    if len(category_folders) == original_len:
        return jsonify({"ok": False, "error": "Category not found."}), 404
    for img_name, value in list(category_assignments.items()):
        if value in {name, target_id}:
            category_assignments[img_name] = category_id_for_value(DEFAULT_CATEGORY)
    save_category_state(current_folder, category_assignments, category_folders, category_groups)
    return jsonify({"ok": True})


@app.route("/move_pairs", methods=["POST"])
def move_pairs():
    global category_assignments, pairs_cache
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    data = request.get_json(force=True) or {}
    img_names = [str(name) for name in data.get("img_names") or [] if isinstance(name, str)]
    category = normalize_category_name(data.get("category"))
    mode = str(data.get("mode") or "move").lower()
    if not img_names:
        return jsonify({"ok": False, "error": "No images selected."}), 400

    changed = []
    if mode == "copy":
        folder_path = Path(current_folder)
        for img_name in img_names:
            src_img = folder_path / img_name
            if not src_img.exists() or src_img.suffix.lower() not in IMAGE_EXTENSIONS:
                continue
            target_name = make_unique_image_name(current_folder, src_img.stem, src_img.suffix)
            target_img = folder_path / target_name
            shutil.copy2(src_img, target_img)
            src_txt = src_img.with_suffix(".txt")
            target_txt = target_img.with_suffix(".txt")
            if src_txt.exists():
                shutil.copy2(src_txt, target_txt)
            else:
                target_txt.write_text("", encoding="utf-8")
            src_json = src_img.with_suffix(".json")
            target_json = target_img.with_suffix(".json")
            if src_json.exists():
                shutil.copy2(src_json, target_json)
            category_assignments[target_name] = category_id_for_value(category)
            changed.append(target_name)
    else:
        for img_name in img_names:
            if pair_exists(current_folder, img_name):
                category_assignments[img_name] = category_id_for_value(category)
                changed.append(img_name)

    save_category_assignments(current_folder, category_assignments)
    pairs_cache = load_pairs(current_folder)
    return jsonify({"ok": True, "changed": changed, "category": category, "mode": mode})


@app.route("/replace_all", methods=["POST"])
def replace_all():
    global message, pairs_cache
    if not current_folder:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": "No folder opened."}), 400
        message = "No folder opened."
        return redirect(url_for("index"))

    match_string = request.form.get("match_string", "")
    replace_with = request.form.get("replace_with", "")
    use_regex = request.form.get("use_regex") == "1"
    category = request.form.get("category") or None

    try:
        count = replace_in_all_captions(current_folder, match_string, replace_with, use_regex=use_regex, category=category)
        pairs_cache = load_pairs(current_folder)
        scope_text = f" in {normalize_category_name(category)}" if category else ""
        result_text = f"Replaced {count} occurrence(s){scope_text}."

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": True, "message": result_text})

        message = result_text
    except (re.error, ValueError) as e:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": f"Replace error: {e}"}), 400
        message = f"Replace error: {e}"

    return redirect(url_for("index"))



@app.route("/add_triggerword_all", methods=["POST"])
def add_triggerword_all():
    global message, pairs_cache
    if not current_folder:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": "No folder opened."}), 400
        message = "No folder opened."
        return redirect(url_for("index"))

    trigger_word = request.form.get("trigger_word", "")
    category = request.form.get("category") or None
    if trigger_word is None or trigger_word == "":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": "Missing trigger word."}), 400
        message = "Missing trigger word."
        return redirect(url_for("index"))

    changed = 0
    for img_name in image_names_for_category(category):
        txt_path = os.path.splitext(os.path.join(current_folder, img_name))[0] + '.txt'
        existing = ""
        if os.path.exists(txt_path):
            try:
                existing = Path(txt_path).read_text(encoding='utf-8')
            except Exception:
                existing = ""
        new_text = prepend_triggerword(existing, trigger_word)
        with open(txt_path, 'w', encoding='utf-8') as f:
            f.write(new_text)
        changed += 1

    pairs_cache = load_pairs(current_folder)
    scope_text = f" in {normalize_category_name(category)}" if category else ""
    result_text = f"Added trigger word to {changed} file(s){scope_text}."

    if request.headers.get("X-Requested-With") == "XMLHttpRequest":
        return jsonify({"ok": True, "message": result_text})

    message = result_text
    return redirect(url_for("index"))


@app.route("/count_string", methods=["POST"])
def count_string():
    global message
    if not current_folder:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": "No folder opened."}), 400
        message = "No folder opened."
        return redirect(url_for("index"))

    count_regex = request.form.get("count_string", "")
    category = request.form.get("category") or None
    try:
        matches = find_caption_matches(current_folder, count_regex, category=category)
        count = len(matches)
        scope_text = f" in {normalize_category_name(category)}" if category else ""
        result_text = f"Found {count} occurrence(s){scope_text}."

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": True, "message": result_text, "matches": matches})

        message = result_text
    except re.error as e:
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": f"Regex error: {e}"}), 400
        message = f"Regex error: {e}"

    return redirect(url_for("index"))


@app.route("/summary")
def summary():
    global message
    bucket_counts = defaultdict(int)
    bucket_base_counts = defaultdict(int)
    category_counts = defaultdict(int)
    allowed_selected = set(get_bucket_options(selected_crop_base))
    predefined_aspects = get_predefined_kohya_aspect_labels()
    predefined_aspect_set = set(predefined_aspects)
    aspect_ratio_counts = defaultdict(int)

    total_captions = sum(1 for _, text in pairs_cache if str(text or "").strip())

    for img_file, _ in pairs_cache:
        width, height, _ = get_image_info(img_file)
        category_counts[get_pair_category(img_file)] += 1

        bucket_w = width
        bucket_h = height
        aspect_label = get_aspect_label(bucket_w, bucket_h)
        in_selected = (bucket_w, bucket_h) in allowed_selected
        known_base = detect_base_resolution(bucket_w, bucket_h)
        bucket_status = "selected" if in_selected else ("other" if known_base else "invalid")
        bucket_label = aspect_label if bucket_status != "invalid" else "???"
        bucket_base_suffix = f" • {known_base}" if known_base else ""
        bucket_key = f"{bucket_w}x{bucket_h} ({bucket_label}){bucket_base_suffix}"
        bucket_counts[(bucket_key, bucket_status)] += 1
        if known_base:
            bucket_base_counts[known_base] += 1

        exact_aspect = get_aspect_label(width, height)
        if exact_aspect in predefined_aspect_set:
            aspect_ratio_counts[exact_aspect] += 1
        else:
            aspect_ratio_counts["???"] += 1

    summary_items = [
        {"bucket": bucket, "count": count, "valid": status == "selected", "status": status}
        for (bucket, status), count in sorted(bucket_counts.items(), key=lambda x: x[0][0])
    ]
    bucket_base_items = [
        {"base": base, "count": count}
        for base, count in sorted(bucket_base_counts.items())
    ]
    summary_text = "<div><b>Resolution distribution:</b></div>"
    for item in summary_items:
        color = "inherit" if item["status"] == "selected" else ("#fbbf24" if item["status"] == "other" else "#dc2626")
        summary_text += f"<span style='padding-left:16px; color:{color};'>{item['bucket']}: {item['count']}</span><br>"
    if bucket_base_items:
        summary_text += "<br><b>Bucket bases:</b><br>"
        for item in bucket_base_items:
            summary_text += f"<span style='padding-left:16px;'>{item['base']}: {item['count']}</span><br>"
    summary_text += f"<br><b>Total Images:</b> {len(pairs_cache)}"
    summary_text += f"<br><b>Total Captions:</b> {total_captions}"
    total_images = len(pairs_cache)
    folders = category_folders or default_category_folders()
    groups = category_groups or default_category_groups()
    folder_group_ids = {item.get("name"): item.get("group_id", DEFAULT_CATEGORY_GROUP_ID) for item in folders}
    group_counts = defaultdict(int)
    for category_name, count in category_counts.items():
        group_counts[folder_group_ids.get(category_name, DEFAULT_CATEGORY_GROUP_ID)] += count

    summary_text += "<br><br><b>Category groups:</b><br>"

    def _cat_line(label, value, indent=False):
        match = re.match(r"\d+", str(value))
        numeric = value if isinstance(value, int) else int(match.group(0) if match else 0)
        color = '#dc2626' if numeric == 0 and label != 'Undefined' else 'inherit'
        pad = 'padding-left:16px; ' if indent else ''
        return f"<span style='{pad}color:{color};'>{label}: {value}</span><br>"

    for group in groups:
        count = group_counts.get(group.get("id"), 0)
        percent = round((count / total_images) * 100) if total_images else 0
        summary_text += _cat_line(group.get("name"), f"{count} ({percent}%)", False)

    summary_text += "<br><b>Folders:</b><br>"
    for category in [item["name"] for item in folders]:
        summary_text += _cat_line(category, category_counts.get(category, 0), False)

    aspect_chart = [{"label": label, "count": aspect_ratio_counts.get(label, 0)} for label in predefined_aspects]
    aspect_chart.append({"label": "???", "count": aspect_ratio_counts.get("???", 0)})

    category_chart = [
        {"name": item["name"], "count": category_counts.get(item["name"], 0), "percent": (round((category_counts.get(item["name"], 0) / total_images) * 100) if total_images else 0), "icon": item["icon"], "color": item.get("color", "#9ca3af")}
        for item in folders
    ]
    category_group_chart = [
        {
            "id": item.get("id"),
            "name": item.get("name"),
            "count": group_counts.get(item.get("id"), 0),
            "percent": round((group_counts.get(item.get("id"), 0) / total_images) * 100) if total_images else 0,
            "color": item.get("color", "#9ca3af"),
        }
        for item in groups
    ]

    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in (request.headers.get("Accept") or "")
    if wants_json:
        return jsonify({
            "ok": True,
            "items": summary_items,
            "bucket_bases": bucket_base_items,
            "total_images": len(pairs_cache),
            "total_captions": total_captions,
            "html": summary_text,
            "categories": [{"name": item["name"], "count": category_counts.get(item["name"], 0)} for item in folders],
            "aspect_chart": aspect_chart,
            "category_chart": category_chart,
            "category_groups": category_group_chart,
        })

    message = summary_text
    return redirect(url_for("index"))


@app.route("/backup")
def backup():
    global message
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if not current_folder:
        message = "No folder opened."
        if wants_json:
            return jsonify({"ok": False, "error": message}), 400
        return redirect(url_for("index"))

    backup_dir = os.path.join(current_folder, "BACKUP")
    if os.path.isdir(backup_dir) and os.listdir(backup_dir):
        message = "BACKUP folder already exists and is not empty."
        if wants_json:
            return jsonify({"ok": False, "error": message}), 400
        return redirect(url_for("index"))

    os.makedirs(backup_dir, exist_ok=True)
    copied = 0
    for img_name, _ in pairs_cache:
        src_img = os.path.join(current_folder, img_name)
        src_txt = os.path.splitext(src_img)[0] + ".txt"
        shutil.copy2(src_img, os.path.join(backup_dir, img_name))
        if os.path.exists(src_txt):
            shutil.copy2(src_txt, os.path.join(backup_dir, os.path.basename(src_txt)))
        src_json = os.path.splitext(src_img)[0] + ".json"
        if os.path.exists(src_json):
            shutil.copy2(src_json, os.path.join(backup_dir, os.path.basename(src_json)))
        copied += 1

    message = f"Backed up {copied} image/caption pair(s) to BACKUP."
    if wants_json:
        return jsonify({"ok": True, "copied": copied, "message": message})
    return redirect(url_for("index"))


@app.route("/joycaption_start", methods=["POST"])
def joycaption_start():
    global message

    if joycaption_status.get("running"):
        return jsonify({"ok": False, "error": "Caption is already running."}), 400

    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    options = request.get_json(force=True) or {}

    t = threading.Thread(
        target=joycaption_worker,
        args=(current_folder, options),
        daemon=True,
    )
    joycaption_status["process"] = t
    t.start()

    message = "Caption: started…"
    joycaption_status["status"] = "Caption: running…"
    return jsonify({"ok": True})


@app.route("/joycaption_interrupt", methods=["POST"])
def joycaption_interrupt():
    if not joycaption_status.get("running"):
        return jsonify({"ok": False, "error": "Not running"}), 400

    joycaption_status["interrupt_requested"] = True
    joycaption_status["status"] = "Caption: interrupting…"
    _append_joy_log("\nInterrupt requested.\n")
    stop_kobold_process()
    return jsonify({"ok": True})


@app.route("/joycaption_status")
def joycaption_status_route():
    reload_pairs = bool(joycaption_status.get("reload_pairs", False))
    if reload_pairs and not joycaption_status.get("running"):
        joycaption_status["reload_pairs"] = False

    return jsonify({
        "running": joycaption_status.get("running", False),
        "status": joycaption_status.get("status", "Idle"),
        "log": joycaption_status.get("log", ""),
        "count": joycaption_status.get("count", 0),
        "total": joycaption_status.get("total", 0),
        "reload_pairs": reload_pairs,
    })


@app.route("/open_in_file_manager")
def open_in_file_manager():
    global message
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in (request.headers.get("Accept") or "")

    if not current_folder:
        error = "No folder opened."
        if wants_json:
            return jsonify({"ok": False, "error": error}), 400
        message = error
        return redirect(url_for("index"))

    try:
        folder = os.path.abspath(current_folder)
        if sys.platform.startswith("win"):
            subprocess.Popen(["explorer", folder])
        elif sys.platform == "darwin":
            subprocess.Popen(["open", folder])
        else:
            subprocess.Popen(["xdg-open", folder])
    except Exception as e:
        error = f"Failed to open file manager: {e}"
        if wants_json:
            return jsonify({"ok": False, "error": error}), 500
        message = error
        return redirect(url_for("index"))

    if wants_json:
        return jsonify({"ok": True})
    return redirect(url_for("index"))


if __name__ == "__main__":
    current_folder = None
    pairs_cache = []
    message = ""
    folder_name = ""
    category_assignments = {}
    category_folders = []
    category_groups = []
    joycaption_status["running"] = False
    joycaption_status["status"] = "Idle"
    joycaption_status["log"] = ""
    joycaption_status["process"] = None
    joycaption_status["count"] = 0
    joycaption_status["total"] = 0
    joycaption_status["last_rc"] = None
    joycaption_status["interrupt_requested"] = False
    joycaption_status["reload_pairs"] = False

    handoff_folder = read_image_folder_handoff()
    if handoff_folder:
        try:
            current_folder = handoff_folder
            folder_name = handoff_folder
            category_assignments, category_folders, category_groups = load_category_state(handoff_folder)
            pairs_cache = load_pairs(handoff_folder)
            selected_crop_base = choose_auto_crop_base_resolution(handoff_folder)
        except Exception:
            current_folder = None
            pairs_cache = []
            folder_name = ""
            category_assignments = {}
            category_folders = []
            category_groups = []

    app.run(host="127.0.0.1", port=5000, debug=False)
