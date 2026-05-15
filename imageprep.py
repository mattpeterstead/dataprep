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
from pathlib import Path
from collections import defaultdict

from flask import Flask, render_template_string, request, jsonify, send_from_directory, redirect, url_for
from PIL import Image, ImageOps
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
BUCKET_STEP = 64


def remember_app(kind):
    try:
        SETTINGS_DIR.mkdir(exist_ok=True)
        LAST_APP_FILE.write_text(kind, encoding="utf-8")
    except Exception:
        pass


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


def launch_local_app(script_name, port):
    if local_port_open(port):
        return
    executable = Path(sys.executable)
    if sys.platform.startswith("win"):
        pythonw = executable.with_name("pythonw.exe")
        if pythonw.exists():
            executable = pythonw
    kwargs = {
        "cwd": str(APP_DIR),
        "stdout": subprocess.DEVNULL,
        "stderr": subprocess.DEVNULL,
    }
    if sys.platform.startswith("win"):
        startupinfo = subprocess.STARTUPINFO()
        startupinfo.dwFlags |= subprocess.STARTF_USESHOWWINDOW
        startupinfo.wShowWindow = 0
        kwargs["startupinfo"] = startupinfo
        kwargs["creationflags"] = (
            subprocess.CREATE_NEW_PROCESS_GROUP
            | subprocess.DETACHED_PROCESS
            | getattr(subprocess, "CREATE_NO_WINDOW", 0)
        )
    subprocess.Popen([str(executable), str(APP_DIR / script_name)], **kwargs)


def exit_soon():
    threading.Timer(1.5, lambda: os._exit(0)).start()


def switch_page(target_url, label):
    return f"""<!doctype html>
<html><head><meta charset="utf-8"><title>Switching</title>
<meta http-equiv="refresh" content="2; url={target_url}">
<style>body{{margin:0;background:#050505;color:#f1f5f9;font-family:Inter,Segoe UI,Arial,sans-serif;display:grid;place-items:center;min-height:100vh}}div{{background:#141414;border:1px solid #2a2a2a;border-radius:8px;padding:18px 22px}}</style>
</head><body><div>Switching to {label}...</div><script>setTimeout(() => location.href = {json.dumps(target_url)}, 1600);</script></body></html>"""

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


def write_caption_result(txt_path, caption, options):
    caption = str(caption or '').strip()
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


def caption_interrupt_requested():
    return (
        not joycaption_status.get('running')
        or joycaption_status.get('interrupt_requested')
    )

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

FLORENCE2_MODEL_OPTIONS = {
    "base": {
        "label": "Florence-2 Base",
        "model_id": "microsoft/Florence-2-base",
    },
    "large": {
        "label": "Florence-2 Large",
        "model_id": "microsoft/Florence-2-large",
    },
    "base_ft": {
        "label": "Florence-2 Base FT",
        "model_id": "microsoft/Florence-2-base-ft",
    },
    "large_ft": {
        "label": "Florence-2 Large FT",
        "model_id": "microsoft/Florence-2-large-ft",
    },
}
FLORENCE2_TASK_OPTIONS = {
    "caption": "<CAPTION>",
    "detailed": "<DETAILED_CAPTION>",
    "more_detailed": "<MORE_DETAILED_CAPTION>",
}
FLORENCE2_CACHE = {}
FLORENCE2_CACHE_LOCK = threading.Lock()

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
    ct = str(caption_type or "descriptive").strip().lower()
    cl = str(caption_length or "long").strip().lower()
    ct_map = {
        "descriptive": "Write a descriptive caption for this image.",
    }
    length_map = {
        "very_short": ("Keep it very short.", 80),
        "short": ("Keep it short.", 120),
        "medium": ("Use medium length.", 220),
        "long": ("Be detailed.", 360),
        "very_long": ("Be very detailed.", 520),
    }
    prompt = ct_map.get(ct, ct_map["descriptive"]) + " " + length_map.get(cl, length_map["long"])[0]
    max_tokens = length_map.get(cl, length_map["long"])[1]
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
    session, tags, cfg, model_path, tags_path = get_wd14_session(model_key, (options.get('hf_token') or '').strip())
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
        caption = caption_image_with_wd14(image_path, options)
        if caption_interrupt_requested():
            joycaption_status['status'] = 'Interrupted'
            break
        write_caption_result(txt_path, caption, options)
        joycaption_status['count'] += 1
        _append_joy_log(f'Tag-captioned {img_name}\n')


def get_florence2_bundle(model_key, hf_token=None):
    key = str(model_key or 'base').strip().lower()
    cfg = FLORENCE2_MODEL_OPTIONS.get(key, FLORENCE2_MODEL_OPTIONS['base'])
    model_id = cfg['model_id']
    with FLORENCE2_CACHE_LOCK:
        cached = FLORENCE2_CACHE.get(model_id)
        if cached is not None:
            return cached

        try:
            import torch
            from transformers import AutoModelForCausalLM, AutoProcessor
        except Exception as e:
            raise RuntimeError('Florence-2 requires torch and transformers to be installed.') from e

        device = 'cuda:0' if torch.cuda.is_available() else 'cpu'
        torch_dtype = torch.float16 if torch.cuda.is_available() else torch.float32
        kwargs = {
            'torch_dtype': torch_dtype,
            'trust_remote_code': True,
        }
        token = (hf_token or '').strip() or None
        if token is not None:
            kwargs['token'] = token

        processor = AutoProcessor.from_pretrained(model_id, trust_remote_code=True, token=token)
        model = AutoModelForCausalLM.from_pretrained(model_id, **kwargs).to(device)
        model.eval()
        payload = {
            'model': model,
            'processor': processor,
            'device': device,
            'torch_dtype': torch_dtype,
            'cfg': cfg,
            'model_id': model_id,
        }
        FLORENCE2_CACHE[model_id] = payload
        return payload


def caption_image_with_florence2(image_path, options):
    bundle = get_florence2_bundle(options.get('florence2_model', 'base'), (options.get('hf_token') or '').strip())
    model = bundle['model']
    processor = bundle['processor']
    device = bundle['device']
    torch_dtype = bundle['torch_dtype']
    task_key = str(options.get('florence2_task') or 'detailed').strip().lower()
    task_prompt = FLORENCE2_TASK_OPTIONS.get(task_key, FLORENCE2_TASK_OPTIONS['detailed'])
    steering_prompt = str(options.get('florence2_steering_prompt') or '').strip()
    # Florence-2 caption tasks require the task token to be the only token in the text.
    # Keep the steering prompt field in the UI, but do not append it to the task token here.
    max_new_tokens = int(options.get('florence2_max_new_tokens') or 256)
    num_beams = int(options.get('florence2_num_beams') or 3)

    try:
        import torch
    except Exception as e:
        raise RuntimeError('Florence-2 requires torch to be installed.') from e

    with Image.open(image_path) as image:
        image = ImageOps.exif_transpose(image).convert('RGB')
        image_size = (image.width, image.height)
        inputs = processor(text=task_prompt, images=image, return_tensors='pt')

    prepared = {}
    for key, value in inputs.items():
        if hasattr(value, 'to'):
            if getattr(value, 'dtype', None) is not None and str(getattr(value, 'dtype', '')).startswith('torch.float'):
                prepared[key] = value.to(device=device, dtype=torch_dtype)
            else:
                prepared[key] = value.to(device)
        else:
            prepared[key] = value

    with torch.inference_mode():
        generated_ids = model.generate(
            input_ids=prepared.get('input_ids'),
            attention_mask=prepared.get('attention_mask'),
            pixel_values=prepared.get('pixel_values'),
            max_new_tokens=max_new_tokens,
            num_beams=num_beams,
            do_sample=False,
        )
    generated_text = processor.batch_decode(generated_ids, skip_special_tokens=False)[0]
    try:
        parsed_answer = processor.post_process_generation(
            generated_text,
            task=FLORENCE2_TASK_OPTIONS.get(task_key, FLORENCE2_TASK_OPTIONS['detailed']),
            image_size=image_size,
        )
    except Exception:
        parsed_answer = None

    if isinstance(parsed_answer, dict):
        value = parsed_answer.get(FLORENCE2_TASK_OPTIONS.get(task_key, FLORENCE2_TASK_OPTIONS['detailed']))
        if isinstance(value, str) and value.strip():
            return value.strip()
    if isinstance(generated_text, str) and generated_text.strip():
        cleaned = generated_text.replace(task_prompt, '').replace('</s>', '').strip()
        if cleaned:
            return cleaned
    raise RuntimeError('Florence-2 returned an empty caption.')


def run_florence2_captioning(folder, options):
    model_key = str(options.get('florence2_model', 'base') or 'base').strip().lower()
    cfg = FLORENCE2_MODEL_OPTIONS.get(model_key, FLORENCE2_MODEL_OPTIONS['base'])
    _append_joy_log(f"Loading {cfg['label']}...\n")
    bundle = get_florence2_bundle(model_key, (options.get('hf_token') or '').strip())
    _append_joy_log(f"Model: {bundle['model_id']}\n")

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
        caption = caption_image_with_florence2(image_path, options)
        if caption_interrupt_requested():
            joycaption_status['status'] = 'Interrupted'
            break
        write_caption_result(txt_path, caption, options)
        joycaption_status['count'] += 1
        _append_joy_log(f'Captioned {img_name}\n')

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


def get_qwen3_vl_base_url(options):
    base_url = str((options or {}).get("qwen3vl_base_url") or "").strip()
    if base_url:
        return base_url.rstrip("/")
    return ""


def get_qwen3_vl_model_id(model_name):
    if model_name not in QWEN3_VL_MODELS:
        raise ValueError(f"Unknown Qwen3-VL model: {model_name}")
    return QWEN3_VL_MODELS[model_name]


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
        return processor, model


def caption_image_with_qwen3_vl_local(image_path, options):
    model_name = options.get("qwen3vl_model", "Qwen3-VL-4B-Instruct")
    processor, model = load_qwen3_vl_local_model(model_name, options)

    try:
        import torch
    except Exception as e:
        raise RuntimeError(f"Could not import torch for Qwen3-VL local mode: {e}")

    system_prompt = str(
        options.get("qwen3vl_system_prompt")
        or "Describe this image in detailed tags and natural language."
    ).strip()
    temperature = float(options.get("qwen3vl_temperature") or 0.2)
    max_tokens = int(options.get("qwen3vl_max_tokens") or 512)

    with Image.open(image_path) as im:
        image = im.convert("RGB")

    user_prompt = "Describe this image."
    if system_prompt:
        user_prompt = f"{system_prompt}\n\n{user_prompt}"

    messages = [
        {
            "role": "user",
            "content": [
                {"type": "image", "image": image},
                {"type": "text", "text": user_prompt},
            ],
        },
    ]

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
    model_name = options.get("qwen3vl_model", "Qwen3-VL-4B-Instruct")
    base_url = get_qwen3_vl_base_url(options)
    if not base_url:
        return caption_image_with_qwen3_vl_local(image_path, options)

    system_prompt = str(
        options.get("qwen3vl_system_prompt")
        or "Describe this image in detailed tags and natural language."
    ).strip()
    temperature = float(options.get("qwen3vl_temperature") or 0.2)
    max_tokens = int(options.get("qwen3vl_max_tokens") or 512)

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
        "model": QWEN3_VL_MODELS[model_name],
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

    r = requests.post(
        base_url + "/v1/chat/completions",
        json=payload,
        timeout=600,
    )
    if not r.ok:
        raise RuntimeError(
            f"Qwen3-VL API error {r.status_code}: {r.text[:500]}"
        )

    data = r.json()
    try:
        content = data["choices"][0]["message"]["content"]
    except Exception:
        raise RuntimeError(f"Unexpected Qwen3-VL response: {data}")

    if isinstance(content, list):
        content = "".join(
            x.get("text", "")
            for x in content
            if isinstance(x, dict)
        )

    return str(content).strip()


def run_qwen3_vl_captioning(folder, options):
    model_name = options.get("qwen3vl_model", "Qwen3-VL-4B-Instruct")
    base_url = get_qwen3_vl_base_url(options)

    _append_joy_log(f"Preparing {model_name}...\n")
    if base_url:
        _append_joy_log(
            f"Using external Qwen3-VL server: {base_url}\n"
        )
    else:
        _append_joy_log("Using built-in Qwen3-VL Transformers backend.\n")
        load_qwen3_vl_local_model(model_name, options)

    images = [
        f for f in sorted(os.listdir(folder))
        if f.lower().endswith(IMAGE_EXTENSIONS)
    ]

    target_images = []
    for img_name in images:
        txt_path = os.path.splitext(os.path.join(folder, img_name))[0] + ".txt"

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
        txt_path = os.path.splitext(image_path)[0] + ".txt"

        caption = caption_image_with_qwen3_vl(image_path, options)
        if caption_interrupt_requested():
            joycaption_status["status"] = "Interrupted"
            break
        write_caption_result(txt_path, caption, options)

        joycaption_status["count"] += 1
        _append_joy_log(f"Captioned {img_name}\n")


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
    try:
        if backend == 'wd14':
            run_wd14_captioning(folder, options)
        elif backend == 'florence2':
            run_florence2_captioning(folder, options)
        elif backend == 'qwen3_vl':
            run_qwen3_vl_captioning(folder, options)
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
            model_path, mmproj_path, cfg = ensure_joy_model_files(quantization, hf_token)
            _append_joy_log(f'Model: {model_path}\n')
            _append_joy_log(f'mmproj: {mmproj_path}\n')

            joycaption_status['status'] = 'Starting KoboldCpp'
            proc, base_url = start_kobold_process(model_path, mmproj_path, visionmaxres)
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
                caption = caption_image_with_kobold(image_path, prompt, max_tokens, temperature, top_p, base_url)
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
    if detected_base:
        ratio_display += f" • {detected_base}-bucket"
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
        "category_icon": CATEGORY_NAME_TO_ICON.get(category, CATEGORY_NAME_TO_ICON[DEFAULT_CATEGORY]),
    }


def get_category_meta_path(folder):
    return Path(folder) / CATEGORY_META_FILENAME


def normalize_category_name(name):
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
    if name in CATEGORY_NAME_TO_ICON:
        return name
    return DEFAULT_CATEGORY


def load_category_assignments(folder):
    if not folder:
        return {}
    path = get_category_meta_path(folder)
    if not path.exists():
        return {}
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(raw, dict):
        return {}
    out = {}
    for key, value in raw.items():
        if isinstance(key, str):
            out[key] = normalize_category_name(value)
    return out


def save_category_assignments(folder, assignments):
    if not folder:
        return
    path = get_category_meta_path(folder)
    clean = {}
    for key, value in sorted(assignments.items()):
        if isinstance(key, str):
            clean[key] = normalize_category_name(value)
    path.write_text(json.dumps(clean, ensure_ascii=False, indent=2), encoding="utf-8")


def get_pair_category(img_name):
    return normalize_category_name(category_assignments.get(img_name, DEFAULT_CATEGORY))


def ensure_missing_txt(folder):
    missing = []
    for fname in os.listdir(folder):
        if fname.lower().endswith(IMAGE_EXTENSIONS):
            img_path = os.path.join(folder, fname)
            txt_path = os.path.splitext(img_path)[0] + ".txt"
            if not os.path.exists(txt_path):
                missing.append(txt_path)
    return missing


def replace_in_all_captions(folder, match_string, replace_with, use_regex=False):
    if not match_string:
        raise ValueError("Search string cannot be empty.")

    total_count = 0

    for fname in os.listdir(folder):
        if not fname.lower().endswith(".txt"):
            continue

        path = os.path.join(folder, fname)
        with open(path, "r", encoding="utf-8") as f:
            content = f.read()

        if use_regex:
            regex = re.compile(match_string, re.MULTILINE | re.DOTALL)

            parts = []
            last_end = 0
            n = 0

            for m in regex.finditer(content):
                start, end = m.span()
                if start == end:
                    continue
                parts.append(content[last_end:start])
                parts.append(replace_with)
                last_end = end
                n += 1

            parts.append(content[last_end:])
            new_content = "".join(parts)
        else:
            n = content.count(match_string)
            new_content = content.replace(match_string, replace_with)

        if n > 0:
            with open(path, "w", encoding="utf-8") as f:
                f.write(new_content)
            total_count += n

    return total_count


def count_in_all_captions(folder, count_string):
    regex = re.compile(count_string, re.MULTILINE | re.DOTALL)
    count = 0
    for fname in os.listdir(folder):
        if fname.lower().endswith(".txt"):
            path = os.path.join(folder, fname)
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
            count += len(regex.findall(content))
    return count

TEMPLATE = r'''
<!DOCTYPE html>
<html>
<head>
<meta charset="UTF-8">
<title>Dataset Forge</title>
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
}
.filename {
  font-weight: 700;
  word-break: break-all;
  cursor: text;
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
  display: flex;
  align-items: center;
  gap: 6px;
}
.unsaved-label {
  font-size: 11px;
  color: var(--danger);
  display: none;
}
.unsaved-label.show {
  display: inline;
}
.status-dot {
  width: 10px;
  height: 10px;
  border-radius: 999px;
  background: transparent;
  border: 1px solid var(--border);
  flex: 0 0 auto;
}
.status-dot.unsaved {
  background: var(--danger);
  border-color: var(--danger);
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
.badge.bad {
  background: var(--danger-bg);
  border-color: var(--danger-border);
  color: var(--danger);
}
.caption-textarea {
  width: 100%;
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
  width: min(940px, 100%);
  max-height: calc(100vh - 32px);
  overflow: auto;
  background: var(--card);
  color: var(--fg);
  border: 1px solid var(--border);
  border-radius: 18px;
  padding: 14px;
  box-shadow: 0 18px 36px rgba(0,0,0,0.30);
}
.joy-modal-head {
  display: flex;
  justify-content: space-between;
  align-items: center;
  gap: 12px;
  margin-bottom: 12px;
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
}

body {
  font-family: Inter, "Segoe UI Variable", "Segoe UI", Roboto, Arial, sans-serif;
  font-size: 13px;
  line-height: 1.45;
  letter-spacing: 0;
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
  border-bottom: 1px solid var(--border);
  padding: 8px 12px;
  box-shadow: 0 1px 0 rgba(255,255,255,.03);
}

.mode-stack {
  position: absolute;
  top: 10px;
  right: 12px;
  display: grid;
  justify-items: end;
  gap: 6px;
  z-index: 1;
}

.mode-head {
  display: inline-flex;
  align-items: center;
  gap: 7px;
}

.mode-label {
  color: var(--muted);
  font-size: 12px;
  font-weight: 750;
  text-transform: uppercase;
  letter-spacing: .04em;
  pointer-events: none;
}

.mode-icon {
  width: 16px;
  height: 16px;
  object-fit: contain;
}

.top > .row:first-of-type {
  padding-right: 116px;
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

.top #saveAllBtn.has-unsaved {
  color: #ffd7df;
  background: rgba(255, 93, 125, 0.16);
  border-color: rgba(255, 93, 125, 0.42);
}

.top #saveAllBtn.has-unsaved:hover {
  color: #ffe7ec;
  background: rgba(255, 93, 125, 0.24);
  border-color: rgba(255, 93, 125, 0.55);
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
  justify-content: flex-end;
  align-items: center;
  padding: 5px 12px;
  background: #141414;
  border-top: 1px solid var(--border);
  color: var(--muted);
  font-size: 12px;
  font-weight: 650;
  box-shadow: 0 -1px 0 rgba(255,255,255,.03);
}

body {
  padding-bottom: 32px;
}

@media (max-width: 720px) {
  .top {
    position: relative;
  }
  .mode-stack {
    position: static;
    margin-bottom: 6px;
    justify-items: end;
  }
  .top > .row:first-of-type {
    padding-right: 0;
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
}

</style>
</head>
<body>
<div class="drop-paste-overlay" id="dropPasteOverlay">Drop images to add them</div>
<div class="top">
  <div class="mode-stack">
    <div class="mode-head"><img class="mode-icon" src="/category_icon/btn_switch_image.png" alt=""><div class="mode-label">image mode</div></div>
    <form method="POST" action="/switch/video"><button type="submit" title="Switch to Video Prep"><span class="toolbar-btn-content">Switch</span></button></form>
  </div>
  <div class="row" style="margin-bottom:8px;">
    <form method="POST" action="/open_folder"><button type="submit" title="Open an image folder"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_open_folder.png" alt="">Open</span></button></form>
    <form method="POST" action="/add_files"><button type="submit" title="Add image files"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_add_files.png" alt="">Add</span></button></form>
    <button type="button" id="openFileManagerBtn" title="Show the opened folder in File Explorer"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_open_file_manager.png" alt="">Show</span></button>
    <form method="POST" action="/close_folder" id="closeFolderForm"><button type="submit" id="closeFolderBtn" title="Close Folder"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_close_folder.png" alt="">Close</span></button></form>
    <button type="button" id="convertBtn" title="Convert images to PNG"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_convert_png.png" alt="">PNG</span></button>
    <button type="button" id="openSummaryModalBtn" title="Show dataset statistics"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_statistics.png" alt="">Stats</span></button>
    <a href="/backup"><button type="button" title="Back up image and caption pairs"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_backup.png" alt="">Backup</span></button></a>
    <button type="button" id="openJoyModalBtn" title="Generate captions"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_caption.png" alt="">Caption</span></button>
    <button type="button" id="openToolsModalBtnInline" title="Batch edit caption text"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_text_tools.png" alt="">Text</span></button>
    <button type="button" id="autoCropAllBtn" title="Auto crop every image"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_auto_crop_all.png" alt="">Auto crop</span></button>
    <button type="button" id="resetAllBtn" title="Reset unsaved captions, crops, and transforms"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_reset_all.png" alt="">Reset</span></button>
    <button type="button" id="saveAllBtn" title="Save every unsaved item"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_save_all.png" alt="">Save</span></button>
    <button type="button" id="renameAllBtn" title="Rename all image and caption pairs"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_rename_all.png" alt="">Rename</span></button>
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
  </div>
  <div class="row" style="margin-top:8px;">
    {% if folder_name %}
      <span class="muted">Opened folder: {{ folder_name }}</span>
    {% else %}
      <span class="muted">No folder opened</span>
    {% endif %}
    {% if message %}
      <span class="muted">{{ message|safe }}</span>
    {% endif %}
  </div>
</div>

{% if not folder_name %}
<div class="notice">
  No folder is open. Press <b>Open Folder</b> at the top to load images and captions.
</div>
{% elif not pairs %}
<div class="notice">
  Folder <b>({{ folder_name }})</b> is open but does not contain any images.
</div>
{% endif %}

<div class="grid">
{% for pair in pairs %}
  <div class="pair-card" data-index="{{ pair.index }}" data-img="{{ pair.img_name }}" data-category="{{ pair.category }}">
    <div class="card-head">
      <div class="filename" data-index="{{ pair.index }}" title="Double-click to rename">{{ pair.img_name }}</div>
      <div class="status-wrap">
        <span class="unsaved-label" id="unsaved-label-{{ pair.index }}">unsaved</span>
        <div class="status-dot" id="status-dot-{{ pair.index }}"></div>
      </div>
    </div>

    <div class="meta-row">
      <span class="badge dims-badge" id="dims-badge-{{ pair.index }}" data-width="{{ pair.width }}" data-height="{{ pair.height }}"></span>
    </div>

    <div class="crop-wrap">
      <div class="crop-stage" id="crop-stage-{{ pair.index }}" data-index="{{ pair.index }}" data-width="{{ pair.width }}" data-height="{{ pair.height }}">
        <img src="/image/{{ pair.img_name }}" id="crop-image-{{ pair.index }}" alt="crop {{ pair.img_name }}">
        <div class="crop-overlay" id="crop-overlay-{{ pair.index }}"></div>
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
      <div class="crop-label" id="crop-label-{{ pair.index }}">No crop selected</div>

      <div class="rotate-row">
        <label for="rotate-slider-{{ pair.index }}">Rotate</label>
        <input type="range" class="rotate-slider" id="rotate-slider-{{ pair.index }}" data-index="{{ pair.index }}" min="-180" max="180" step="1" value="0">
        <span class="rotate-value" id="rotate-value-{{ pair.index }}">0°</span>
      </div>
    </div>

    <div class="card-actions">
      <button type="button" class="icon-btn auto-crop-btn" data-index="{{ pair.index }}" title="Auto crop" aria-label="Auto crop">
        <img src="/category_icon/btn_card_autocrop.png" alt="">
      </button>
      <button type="button" class="icon-btn ratio-lock-btn" data-index="{{ pair.index }}" title="Aspect ratio lock" aria-label="Aspect ratio lock">
        <img src="/category_icon/btn_card_ratio_lock.png" alt="">
      </button>
      <button type="button" class="icon-btn undo-btn" data-index="{{ pair.index }}" title="Undo" aria-label="Undo">
        <img src="/category_icon/btn_card_undo.png" alt="">
      </button>
      <button type="button" class="icon-btn flip-h-btn" data-index="{{ pair.index }}" data-img="{{ pair.img_name }}" title="Flip horizontally" aria-label="Flip horizontally">
        <img src="/category_icon/btn_card_flip_h.png" alt="">
      </button>
      <button type="button" class="icon-btn flip-v-btn" data-index="{{ pair.index }}" data-img="{{ pair.img_name }}" title="Flip vertically" aria-label="Flip vertically">
        <img src="/category_icon/btn_card_flip_v.png" alt="">
      </button>
      <button type="button" class="icon-btn save-btn" id="save-btn-{{ pair.index }}" data-index="{{ pair.index }}" title="Save" aria-label="Save">
        <img src="/category_icon/btn_card_save.png" alt="">
      </button>
      <button type="button" class="icon-btn clone-btn" data-index="{{ pair.index }}" data-img="{{ pair.img_name }}" title="Clone" aria-label="Clone">
        <img src="/category_icon/btn_card_clone.png" alt="">
      </button>
      <button type="button" class="icon-btn delete-btn" data-index="{{ pair.index }}" data-img="{{ pair.img_name }}" title="Delete" aria-label="Delete">
        <img src="/category_icon/btn_card_delete.png" alt="">
      </button>
      <button type="button" class="icon-btn category-btn" data-index="{{ pair.index }}" data-img="{{ pair.img_name }}" data-category="{{ pair.category }}" title="{{ pair.category }}" aria-label="Category">
        <img src="/category_icon/{{ pair.category_icon }}" alt="{{ pair.category }}">
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

<div class="statusbar">{{ pairs|length }} image{% if pairs|length != 1 %}s{% endif %}.</div>

<div class="category-popover" id="categoryPopover" hidden>
  <div class="category-popover-head">
    <div class="category-popover-title">Select category</div>
    <button type="button" class="category-popover-close" id="closeCategoryPopoverBtn">×</button>
  </div>
  <div class="category-option-grid" id="categoryOptionGrid"></div>
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
          <option value="florence2">Florence 2</option>
          <option value="qwen3_vl">Qwen3-VL</option>
        </select>
      </label>
      <label class="joy-only">
        Quantization
        <select id="joy_quantization">
          <option value="Q4_K">Q4_K</option>
          <option value="Q8_0">Q8_0</option>
          <option value="F16">F16</option>
        </select>
      </label>
      <label class="joy-only">
        Style
        <select id="joy_caption_type">
          <option value="descriptive">Descriptive</option>
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
        </select>
      </label>
      <label class="joy-only">
        Vision resolution
        <input type="number" id="joy_visionmaxres" min="128" step="64" value="384">
      </label>
      <label class="joy-only">
        Max tokens
        <input type="number" id="joy_max_tokens" min="1" step="1" value="512">
      </label>
      <label class="joy-only">
        Temperature
        <input type="number" id="joy_temperature" min="0" max="2" step="0.05" value="0.6">
      </label>
      <label class="joy-only">
        Top-p
        <input type="number" id="joy_top_p" min="0" max="1" step="0.01" value="0.9">
      </label>
      <label>
        HF token
        <input type="password" id="joy_hf_token" placeholder="Optional HF token">
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
      <label class="florence2-only" style="display:none;">
        Florence 2 model
        <select id="joy_florence2_model">
          <option value="base">Base</option>
          <option value="large">Large</option>
          <option value="base_ft">Base FT</option>
          <option value="large_ft">Large FT</option>
        </select>
      </label>
      <label class="florence2-only" style="display:none;">
        Caption mode
        <select id="joy_florence2_task">
          <option value="caption">Caption</option>
          <option value="detailed">Detailed caption</option>
          <option value="more_detailed">More detailed caption</option>
        </select>
      </label>
      <label class="florence2-only" style="display:none;">
        Max new tokens
        <input type="number" id="joy_florence2_max_new_tokens" min="1" step="1" value="256">
      </label>
      <label class="florence2-only" style="display:none;">
        Beams
        <input type="number" id="joy_florence2_num_beams" min="1" step="1" value="3">
      </label>
      <label class="qwen3vl-only" style="display:none;">
        Qwen3-VL model
        <select id="joy_qwen3vl_model">
          <option>Qwen3-VL-4B-Instruct</option>
          <option>Qwen3-VL-8B-Instruct</option>
          <option>Huihui-Qwen3-VL-8B-Instruct-abliterated</option>
        </select>
      </label>
      <label class="qwen3vl-only" style="display:none;">
        Temperature
        <input type="number" id="joy_qwen3vl_temperature" min="0" max="2" step="0.05" value="0.2">
      </label>
      <label class="qwen3vl-only" style="display:none;">
        Max tokens
        <input type="number" id="joy_qwen3vl_max_tokens" min="1" step="1" value="512">
      </label>
      <label class="qwen3vl-only" style="display:none; grid-column: 1 / -1;">
        External API URL
        <input type="text" id="joy_qwen3vl_base_url" placeholder="Leave empty for built-in local Qwen3-VL">
      </label>
      <label>
        <span>Skip existing captions</span>
        <input type="checkbox" id="joy_no_overwrite">
      </label>
      <label>
        <span>Append to existing caption.</span>
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

    <div class="tool-box florence2-only" id="florence2InfoBox" style="margin-top:12px; display:none;">
      <h3 style="margin-bottom:8px;">Florence 2 options</h3>
      <div class="small">Prompt-based captioning with Microsoft Florence-2 task prompts.</div>
      <label style="margin-top:10px; display:flex; flex-direction:column; gap:6px;">
        Steering prompt
        <textarea id="joy_florence2_steering_prompt" rows="3" placeholder="Optional steering prompt for Florence 2"></textarea>
      </label>
    </div>

    <div class="tool-box qwen3vl-only" id="qwen3vlSettings" style="margin-top:12px; display:none;">
      <h3 style="margin-bottom:8px;">Qwen3-VL options</h3>
      <div class="small">Leave External API URL empty to use the built-in Transformers backend. The first local run downloads the selected model.</div>
      <label style="margin-top:10px; display:flex; flex-direction:column; gap:6px;">
        System prompt
        <textarea id="joy_qwen3vl_system_prompt" rows="3">Describe this image in detailed tags and natural language.</textarea>
      </label>
    </div>

    <div class="joy-actions">
      <button type="button" id="joyStartBtn">Start</button>
      <button type="button" id="joyInterruptBtn">Interrupt</button>
      <button type="button" id="joyResetSettingsBtn">Reset settings</button>
      <span class="small" id="joyStatusText">Idle</span>
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

    <div class="joy-grid">
      <div class="tool-box" style="margin:0;">
        <h3>Replace in captions</h3>
        <form method="POST" action="/replace_all" id="replaceForm">
          <input type="text" name="match_string" placeholder="Search string or regex" required id="sr_match">
          <input type="text" name="replace_with" placeholder="Replace with" id="sr_replace">
          <label style="display:flex; align-items:center; gap:6px;" title="Examples of Regexes:
add string to start:  \A
add string to end:  \Z
target last tag:  ,[^,]*$
replace all: .*">
            <input type="checkbox" name="use_regex" value="1" id="sr_use_regex">
            Use regex
          </label>
          <button type="submit">Replace all</button>
        </form>
      </div>

      <div class="tool-box" style="margin:0;">
        <h3>Count matches</h3>
        <form method="POST" action="/count_string" id="countForm">
          <input type="text" name="count_string" placeholder="Count regex" required id="count_regex">
          <button type="submit">Count</button>
        </form>
      </div>

      <div class="tool-box" style="margin:0;">
        <h3>Add trigger word</h3>
        <form method="POST" action="/add_triggerword_all" id="triggerForm">
          <input type="text" name="trigger_word" placeholder="Trigger word" required id="trigger_word">
          <button type="submit">Add trigger word</button>
        </form>
      </div>
    </div>

    <div id="toolsResult" class="logbox" style="min-height:80px; max-height:180px;"></div>
  </div>
</div>

<script id="bucket-data" type="application/json">{{ bucket_options_json|safe }}</script>
<script id="joy-model-data" type="application/json">{{ joy_model_data_json|safe }}</script>
<script id="category-defs-data" type="application/json">{{ category_defs_json|safe }}</script>
<script>
const BUCKET_OPTIONS = JSON.parse(document.getElementById('bucket-data').textContent);
const JOY_MODEL_OPTIONS = JSON.parse(document.getElementById('joy-model-data').textContent);
const CATEGORY_DEFS = JSON.parse(document.getElementById('category-defs-data').textContent);
const CATEGORY_ICON_BY_NAME = Object.fromEntries(CATEGORY_DEFS.map(item => [item.name, item.icon]));
const CATEGORY_VISIBILITY_KEY = 'caption_app_categories_visible';
const HAS_OPEN_FOLDER = {{ 'true' if folder_name else 'false' }};
const IMAGE_FILE_PATTERN = /\.(png|jpe?g|gif|bmp|webp|avif)$/i;
const dropPasteOverlay = document.getElementById('dropPasteOverlay');
let currentCropBase = {{ selected_crop_base|int }};
const cropStates = new Map();
let joySavedConfig = {};

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

function isImageFile(file) {
  return !!file && (
    String(file.type || '').startsWith('image/') ||
    IMAGE_FILE_PATTERN.test(String(file.name || ''))
  );
}

function imageFilesFromFileList(fileList) {
  return Array.from(fileList || []).filter(isImageFile);
}

function imageFilesFromClipboard(clipboardData) {
  const files = imageFilesFromFileList(clipboardData?.files || []);
  const itemFiles = Array.from(clipboardData?.items || [])
    .filter(item => item.kind === 'file' && String(item.type || '').startsWith('image/'))
    .map(item => item.getAsFile())
    .filter(isImageFile);
  return [...files, ...itemFiles].filter((file, index, list) => list.indexOf(file) === index);
}

function hasImageDrag(event) {
  const items = Array.from(event.dataTransfer?.items || []);
  if (items.some(item => item.kind === 'file' && String(item.type || '').startsWith('image/'))) {
    return true;
  }
  return Array.from(event.dataTransfer?.files || []).some(isImageFile);
}

async function uploadImageFiles(files, sourceLabel = 'selected') {
  const imageFiles = imageFilesFromFileList(files);
  if (!imageFiles.length) return;
  if (!HAS_OPEN_FOLDER) {
    alert('Open a folder before adding images.');
    return;
  }
  if (typeof hasUnsavedChanges === 'function' && hasUnsavedChanges()) {
    const ok = window.confirm('Add images and refresh the view? Unsaved edits will be discarded.');
    if (!ok) return;
  }

  const formData = new FormData();
  imageFiles.forEach((file, i) => {
    const fallbackName = sourceLabel === 'pasted' ? `pasted_image_${i + 1}.png` : `image_${i + 1}.png`;
    formData.append('images', file, file.name || fallbackName);
  });

  try {
    const res = await fetch('/upload_images', {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      body: formData,
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      alert(data.error || 'Failed to add images.');
      return;
    }
    suppressBeforeUnload = true;
    window.location.reload();
  } catch (err) {
    alert(`Failed to add images: ${err}`);
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
  await uploadImageFiles(event.dataTransfer?.files || [], 'dropped');
});
document.addEventListener('paste', async (event) => {
  const files = imageFilesFromClipboard(event.clipboardData);
  if (!files.length) return;
  event.preventDefault();
  await uploadImageFiles(files, 'pasted');
});

let textHeight = parseInt(localStorage.getItem('caption_app_text_height') || '110', 10);
let imageHeight = parseInt(localStorage.getItem('caption_app_image_height') || '420', 10);
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
  localStorage.setItem('caption_app_text_height', String(value));
  document.getElementById('textHeightSlider').value = value;
  document.getElementById('textHeightValue').textContent = value;
  document.querySelectorAll('.caption-textarea').forEach(ta => { ta.style.height = value + 'px'; });
}
setTextHeight(textHeight);
document.getElementById('textHeightSlider').addEventListener('input', e => setTextHeight(parseInt(e.target.value, 10)));

function setImageHeight(value) {
  imageHeight = value;
  const cardMinWidth = Math.max(220, value + 24);
  localStorage.setItem('caption_app_image_height', String(value));
  document.getElementById('imageHeightSlider').value = value;
  document.getElementById('imageHeightValue').textContent = value;
  document.documentElement.style.setProperty('--card-min-width', cardMinWidth + 'px');
  document.querySelectorAll('.crop-stage').forEach(stage => {
    stage.style.width = value + 'px';
    stage.style.maxWidth = '100%';
  });
}
setImageHeight(imageHeight);
document.getElementById('imageHeightSlider').addEventListener('input', e => setImageHeight(parseInt(e.target.value, 10)));

document.getElementById('convertBtn')?.addEventListener('click', async () => {
  const ok = window.confirm('Convert images to uncompressed PNG?');
  if (!ok) return;
  try {
    const res = await fetch('/convert_images_to_png', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' }
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      alert(data.error || 'Convert failed');
      return;
    }
    suppressBeforeUnload = true;
    window.location.assign('/');
  } catch (e) {
    alert('Convert failed');
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
      alert(data.error || 'Failed to open folder.');
    }
  } catch (e) {
    alert('Failed to open folder.');
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

function getUnsavedCardIndexes() {
  const out = [];
  document.querySelectorAll('.pair-card').forEach(card => {
    const index = parseInt(card.dataset.index, 10);
    const ta = card.querySelector('.caption-textarea');
    const state = ensureState(index);
    const captionChanged = (ta.value !== (ta.dataset.original ?? ''));
    const cropChanged = !!state.crop;
    const transformChanged = !!state.rotation || state.flipH || state.flipV;
    if (captionChanged || cropChanged || transformChanged) out.push(index);
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
    state.flipV;

  card.classList.toggle('unsaved', unsaved);
  ta.classList.toggle('unsaved', ta.value !== (ta.dataset.original ?? ''));
  dot.classList.toggle('unsaved', unsaved);
  label.classList.toggle('show', unsaved);
  saveBtn.classList.toggle('unsaved', unsaved);
  saveBtn.classList.toggle('upscale-warning', !!state.upscale);
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
  const categoryIcon = pair.category_icon || CATEGORY_ICON_BY_NAME[category] || CATEGORY_ICON_BY_NAME['Undefined'];

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

  const categoryBtn = card.querySelector('.category-btn');
  if (categoryBtn) {
    categoryBtn.dataset.category = category;
    categoryBtn.title = category;
    const iconImg = categoryBtn.querySelector('img');
    if (iconImg) {
      iconImg.src = `/category_icon/${encodeURIComponent(categoryIcon)}`;
      iconImg.alt = category;
    }
  }

  const ta = card.querySelector('.caption-textarea');
  if (ta) {
    ta.dataset.index = String(index);
    ta.dataset.img = imgName;
    ta.dataset.original = pair.text || '';
    ta.value = pair.text || '';
    ta.style.height = textHeight + 'px';
    ta.classList.remove('unsaved');
  }

  if (Number.isFinite(oldIndex) && oldIndex !== index) {
    cropStates.delete(oldIndex);
  }
  cropStates.set(index, { crop: null, upscale: false, rotation: 0, flipH: false, flipV: false, ratioLocked: false, lockedAspect: null });
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
  card.querySelectorAll('[data-bound], [data-bound-input], [data-bound-click], [data-bound-dblclick]').forEach(el => {
    delete el.dataset.bound;
    delete el.dataset.boundInput;
    delete el.dataset.boundClick;
    delete el.dataset.boundDblclick;
  });
}

async function handleDeleteButton(btn, event) {
  event?.preventDefault?.();
  event?.stopPropagation?.();
  const img = btn.dataset.img;
  const index = parseInt(btn.dataset.index, 10);
  if (!window.confirm(`Delete ${img} and its caption?`)) return;

  const card = btn.closest('.pair-card');
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

    if (categoryPopoverCard === card) closeCategoryPopover();
    card?.remove();
  } catch (err) {
    btn.disabled = false;
    alert(err?.message || 'Delete failed');
  }
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
    setImageHeight(imageHeight);
    updateDimsColors();
    renderCrop(parseInt(newCard.dataset.index, 10));
    markUnsaved(parseInt(newCard.dataset.index, 10));
  } catch (err) {
    alert(err?.message || 'Clone failed');
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
    alert('Filename cannot be empty.');
    input?.focus();
    return;
  }
  if (/[\\/:*?"<>|]/.test(stem)) {
    alert('Filename contains invalid characters.');
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
    alert(err?.message || 'Rename failed');
    filenameEl.textContent = oldName;
  }
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
    ta.style.height = textHeight + 'px';
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

  const undoBtn = card.querySelector('.undo-btn');
  if (undoBtn && !undoBtn.dataset.boundClick) {
    undoBtn.dataset.boundClick = '1';
    undoBtn.addEventListener('click', () => undoCard(parseInt(undoBtn.dataset.index, 10)));
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

  const categoryBtn = card.querySelector('.category-btn');
  if (categoryBtn && !categoryBtn.dataset.boundClick) {
    categoryBtn.dataset.boundClick = '1';
    categoryBtn.addEventListener('click', (event) => {
      event.preventDefault();
      event.stopPropagation();
      openCategoryPopover(card, categoryBtn);
    });
  }

  const filenameEl = card.querySelector('.filename');
  if (filenameEl && !filenameEl.dataset.boundDblclick) {
    filenameEl.dataset.boundDblclick = '1';
    filenameEl.addEventListener('dblclick', () => beginInlineRename(card));
  }

  ensureState(index);
  renderImageTransform(index);
  attachCropper(index);
  markUnsaved(index);
}


function updateDimsColors() {
  const allowed = new Set(getAllowedBuckets().map(p => `${p[0]}x${p[1]}`));
  document.querySelectorAll('.dims-badge').forEach(el => {
    const width = parseInt(el.dataset.width, 10);
    const height = parseInt(el.dataset.height, 10);
    const key = `${width}x${height}`;
    const isAllowed = allowed.has(key);

    el.classList.toggle('ok', isAllowed);
    el.classList.toggle('bad', !isAllowed);

    const aspectLabel = isAllowed ? getAspectLabel(width, height) : "???";
    el.textContent = `${width}×${height} (${aspectLabel})`;
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

function getRenderedImageBox(index) {
  const stage = document.getElementById(`crop-stage-${index}`);
  const img = document.getElementById(`crop-image-${index}`);

  const stageW = stage.clientWidth;
  const stageH = stage.clientHeight;

  const naturalW = img.naturalWidth || parseFloat(stage.dataset.width) || 1;
  const naturalH = img.naturalHeight || parseFloat(stage.dataset.height) || 1;

  const scale = Math.min(stageW / naturalW, stageH / naturalH);
  const width = naturalW * scale;
  const height = naturalH * scale;
  const left = (stageW - width) / 2;
  const top = (stageH - height) / 2;

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

  const transforms = [];
  if (state.flipH) transforms.push('scaleX(-1)');
  if (state.flipV) transforms.push('scaleY(-1)');
  if (state.rotation) transforms.push(`rotate(${state.rotation}deg)`);

  img.style.transform = transforms.length ? transforms.join(' ') : 'none';
  img.style.transformOrigin = 'center center';

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

function attachCropper(index) {
  const stage = document.getElementById(`crop-stage-${index}`);
  const box = document.getElementById(`crop-box-${index}`);
  if (!stage || stage.dataset.bound === '1') return;
  stage.dataset.bound = '1';

  ensureState(index);
  let drag = null;

  stage.addEventListener('contextmenu', (e) => {
    e.preventDefault();
    const state = ensureState(index);
    state.crop = null;
    state.upscale = false;
    cropStates.set(index, state);
    renderCrop(index);
    markUnsaved(index);
  });

  stage.addEventListener('mousedown', (e) => {
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

function undoCard(index) {
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

const categoryPopover = document.getElementById('categoryPopover');
const categoryOptionGrid = document.getElementById('categoryOptionGrid');
const closeCategoryPopoverBtn = document.getElementById('closeCategoryPopoverBtn');
let categoryPopoverCard = null;

function getCardByIndex(index) {
  return document.querySelector(`.pair-card[data-index="${index}"]`);
}

function updateCardCategoryUi(card, category) {
  if (!card) return;
  const normalized = CATEGORY_ICON_BY_NAME[category] ? category : 'Undefined';
  card.dataset.category = normalized;
  const btn = card.querySelector('.category-btn');
  if (btn) {
    btn.dataset.category = normalized;
    btn.title = normalized;
    const img = btn.querySelector('img');
    if (img) {
      img.src = `/category_icon/${CATEGORY_ICON_BY_NAME[normalized]}?t=${Date.now()}`;
      img.alt = normalized;
    }
  }
  renderCategoryOptions();
}

function categoryDisplayParts(name) {
  const label = String(name || '');
  for (const prefix of ['Close-up', 'Medium', 'Full body']) {
    if (label === prefix) return { prefix, suffix: '' };
    if (label.startsWith(prefix + ' ')) {
      return { prefix, suffix: label.slice(prefix.length + 1) };
    }
  }
  return { prefix: '', suffix: label };
}

function renderCategoryOptions() {
  if (!categoryOptionGrid) return;
  const activeCategory = categoryPopoverCard?.dataset.category || 'Undefined';
  const rows = [
    CATEGORY_DEFS.filter(item => item.name.includes('Close-up')),
    CATEGORY_DEFS.filter(item => item.name.includes('Medium')),
    CATEGORY_DEFS.filter(item => item.name.includes('Full body')),
    CATEGORY_DEFS.filter(item => item.name === 'Undefined'),
  ].filter(row => row.length);
  categoryOptionGrid.innerHTML = rows.map(row => `
    <div class="category-option-row">
      ${row.map(item => {
        const label = categoryDisplayParts(item.name);
        return `
        <button type="button" class="category-option-btn ${item.name === activeCategory ? 'active' : ''}" data-category="${item.name}" title="${item.name}">
          <span class="category-icon-circle"><img src="/category_icon/${item.icon}" alt="${item.name}"></span>
          <span class="category-option-label">
            ${label.prefix ? `<span class="category-option-label-prefix">${label.prefix}</span>` : ''}
            <span class="category-option-label-suffix">${label.suffix}</span>
          </span>
        </button>
      `;
      }).join('')}
    </div>
  `).join('');
  categoryOptionGrid.querySelectorAll('.category-option-btn').forEach(btn => {
    btn.addEventListener('click', async () => {
      if (!categoryPopoverCard) return;
      const imgName = categoryPopoverCard.dataset.img;
      const category = btn.dataset.category || 'Undefined';
      try {
        const res = await fetch('/set_category', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ img_name: imgName, category }),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || 'Failed to set category');
        updateCardCategoryUi(categoryPopoverCard, data.category || category);
        closeCategoryPopover();
      } catch (err) {
        alert(err?.message || 'Failed to set category');
      }
    });
  });
}

function closeCategoryPopover() {
  if (categoryPopover) categoryPopover.hidden = true;
  categoryPopoverCard = null;
}

function openCategoryPopover(card, anchorBtn) {
  if (!categoryPopover || !card || !anchorBtn) return;
  categoryPopoverCard = card;
  renderCategoryOptions();
  const rect = anchorBtn.getBoundingClientRect();
  categoryPopover.hidden = false;
  requestAnimationFrame(() => {
    const popRect = categoryPopover.getBoundingClientRect();
    let left = rect.left + rect.width / 2 - popRect.width / 2;
    let top = rect.bottom + 8;
    left = Math.max(12, Math.min(left, window.innerWidth - popRect.width - 12));
    if (top + popRect.height > window.innerHeight - 12) {
      top = Math.max(12, rect.top - popRect.height - 8);
    }
    categoryPopover.style.left = `${left}px`;
    categoryPopover.style.top = `${top}px`;
  });
}

closeCategoryPopoverBtn?.addEventListener('click', closeCategoryPopover);
document.addEventListener('click', (e) => {
  const categoryBtn = e.target.closest('.category-btn');
  if (categoryBtn) {
    e.preventDefault();
    e.stopPropagation();
    const card = categoryBtn.closest('.pair-card');
    if (categoryPopoverCard === card && categoryPopover && !categoryPopover.hidden) {
      closeCategoryPopover();
    } else {
      openCategoryPopover(card, categoryBtn);
    }
    return;
  }
  if (!categoryPopover?.hidden && !e.target.closest('#categoryPopover')) {
    closeCategoryPopover();
  }
});
window.addEventListener('resize', closeCategoryPopover);
window.addEventListener('scroll', closeCategoryPopover, true);

async function saveCard(index) {
  const card = document.querySelector(`.pair-card[data-index="${index}"]`);
  const ta = card.querySelector('.caption-textarea');
  const imgName = ta.dataset.img;
  const state = ensureState(index);

  if (state.upscale) {
    const ok = window.confirm('This crop will upscale the image and may reduce quality. Continue?');
    if (!ok) return;
  }

  const payload = {
    index,
    img_name: imgName,
    caption: ta.value,
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
  };

  const res = await fetch('/save_pair', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify(payload),
  });
  const data = await res.json();

  if (!res.ok || !data.ok) {
    alert(data.error || 'Save failed');
    return;
  }

  ta.dataset.original = ta.value;

  if (data.updated_pair) {
    const badge = document.getElementById(`dims-badge-${index}`);
    badge.dataset.width = data.updated_pair.width;
    badge.dataset.height = data.updated_pair.height;

    const cropImg = document.getElementById(`crop-image-${index}`);
    cropImg.src = `/image/${imgName}?t=${Date.now()}`;

    const stage = document.getElementById(`crop-stage-${index}`);
    stage.dataset.width = data.updated_pair.width;
    stage.dataset.height = data.updated_pair.height;
  }

  state.crop = null;
  state.upscale = false;
  state.rotation = 0;
  state.flipH = false;
  state.flipV = false;
  renderImageTransform(index);
  cropStates.set(index, state);
  renderCrop(index);
  updateDimsColors();
  markUnsaved(index);
}

async function saveAllCards() {
  const indexes = getUnsavedCardIndexes();
  if (!indexes.length) return;

  const hasUpscale = indexes.some(i => ensureState(i).upscale);
  if (hasUpscale) {
    const ok = window.confirm('Some selected crops will upscale the image and may reduce quality. Continue saving all?');
    if (!ok) return;
  }

  for (const i of indexes) {
    await saveCard(i);
  }
  updateSaveAllButtonState();
}

async function renameAllPairs() {
  const prefix = window.prompt('Enter filename prefix for all pairs:');
  if (prefix === null) return;

  const res = await fetch('/rename_all_pairs', {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ prefix })
  });
  const data = await res.json();
  if (!res.ok || !data.ok) {
    alert(data.error || 'Rename all failed');
    return;
  }
  suppressBeforeUnload = true;
  window.location.reload();
}

function confirmReplace() {
  return window.confirm('Apply this search/replace to all caption files in the opened folder?');
}

document.querySelectorAll('.pair-card').forEach(card => attachCardEventListeners(card));
document.getElementById('autoCropAllBtn')?.addEventListener('click', autoCropAll);
document.getElementById('saveAllBtn').addEventListener('click', saveAllCards);
document.getElementById('renameAllBtn')?.addEventListener('click', renameAllPairs);
document.getElementById('resetAllBtn')?.addEventListener('click', () => {
  if (!hasUnsavedChanges()) return;
  const ok = window.confirm('Reset all unsaved captions, crops, and transforms?');
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

document.getElementById('closeFolderForm')?.addEventListener('submit', (e) => {
  if (hasUnsavedChanges()) {
    const ok = window.confirm('Close the folder and discard all unsaved changes?');
    if (!ok) {
      e.preventDefault();
      return;
    }
  }
  suppressBeforeUnload = true;
});

const joyModalBackdrop = document.getElementById('joyModalBackdrop');
const openJoyModalBtn = document.getElementById('openJoyModalBtn');
const closeJoyModalBtn = document.getElementById('closeJoyModalBtn');
const joyStatusText = document.getElementById('joyStatusText');
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

document.addEventListener("keydown", (e) => {
  if (e.key === "Escape") {
    closeJoyModal();
    closeSummaryModal();
    closeToolsModal();
    closeCategoryPopover();
  }
});

const JOY_DEFAULTS = {
  backend: 'joycaption',
  quantization: 'Q4_K',
  caption_type: 'descriptive',
  caption_length: 'long',
  visionmaxres: '384',
  max_tokens: '512',
  temperature: '0.6',
  top_p: '0.9',
  extra_options: '',
  person_name: '',
  no_overwrite: false,
  append_existing: false,
  wd14_model: 'convnextv2',
  wd14_general_threshold: '0.35',
  wd14_character_threshold: '0.85',
  wd14_include_rating: false,
  wd14_include_characters: false,
  wd14_replace_underscores: true,
  florence2_model: 'base',
  florence2_task: 'detailed',
  florence2_max_new_tokens: '256',
  florence2_num_beams: '3',
  florence2_steering_prompt: '',
  qwen3vl_model: 'Qwen3-VL-4B-Instruct',
  qwen3vl_system_prompt: 'Describe this image in detailed tags and natural language.',
  qwen3vl_temperature: '0.2',
  qwen3vl_max_tokens: '512',
  qwen3vl_base_url: '',
  auto_scroll: true,
};


function updateCaptionBackendUI() {
  const backend = (document.getElementById('joy_backend')?.value || 'joycaption');
  document.querySelectorAll('.joy-only').forEach(el => {
    el.style.display = backend === 'joycaption' ? '' : 'none';
  });
  document.querySelectorAll('.wd14-only').forEach(el => {
    el.style.display = backend === 'wd14' ? '' : 'none';
  });
  document.querySelectorAll('.florence2-only').forEach(el => {
    el.style.display = backend === 'florence2' ? '' : 'none';
  });
  document.querySelectorAll('.qwen3vl-only').forEach(el => {
    el.style.display = backend === 'qwen3_vl' ? '' : 'none';
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
    wd14_model: document.getElementById('joy_wd14_model').value,
    wd14_general_threshold: document.getElementById('joy_wd14_general_threshold').value,
    wd14_character_threshold: document.getElementById('joy_wd14_character_threshold').value,
    wd14_include_rating: document.getElementById('joy_wd14_include_rating').checked,
    wd14_include_characters: document.getElementById('joy_wd14_include_characters').checked,
    wd14_replace_underscores: document.getElementById('joy_wd14_replace_underscores').checked,
    wd14_undesired_tags: document.getElementById('joy_wd14_undesired_tags').value,
    florence2_model: document.getElementById('joy_florence2_model').value,
    florence2_task: document.getElementById('joy_florence2_task').value,
    florence2_max_new_tokens: document.getElementById('joy_florence2_max_new_tokens').value,
    florence2_num_beams: document.getElementById('joy_florence2_num_beams').value,
    florence2_steering_prompt: document.getElementById('joy_florence2_steering_prompt').value,
    qwen3vl_model: document.getElementById('joy_qwen3vl_model').value,
    qwen3vl_system_prompt: document.getElementById('joy_qwen3vl_system_prompt').value,
    qwen3vl_temperature: document.getElementById('joy_qwen3vl_temperature').value,
    qwen3vl_max_tokens: document.getElementById('joy_qwen3vl_max_tokens').value,
    qwen3vl_base_url: document.getElementById('joy_qwen3vl_base_url').value,
  };
}

function loadJoySettings() {
  try {
    const raw = localStorage.getItem('caption_app_joy_settings');
    const cfg = raw ? JSON.parse(raw) : {};
    const merged = { ...JOY_DEFAULTS, ...cfg };
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
    updateJoyNameVisibility();
    updateCaptionBackendUI();
  } catch (e) {}
}

function saveJoySettings() {
  const settings = joySettings();
  settings.auto_scroll = !!(joyAutoScroll && joyAutoScroll.checked);
  localStorage.setItem('caption_app_joy_settings', JSON.stringify(settings));
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
['joy_backend','joy_quantization','joy_caption_type','joy_caption_length','joy_visionmaxres','joy_max_tokens','joy_temperature','joy_top_p','joy_extra_options','joy_person_name','joy_hf_token','joy_no_overwrite','joy_append_existing','joy_wd14_model','joy_wd14_general_threshold','joy_wd14_character_threshold','joy_wd14_include_rating','joy_wd14_include_characters','joy_wd14_replace_underscores','joy_wd14_undesired_tags','joy_florence2_model','joy_florence2_task','joy_florence2_max_new_tokens','joy_florence2_num_beams','joy_florence2_steering_prompt','joy_qwen3vl_model','joy_qwen3vl_system_prompt','joy_qwen3vl_temperature','joy_qwen3vl_max_tokens','joy_qwen3vl_base_url'].forEach(id => {
  const el = document.getElementById(id);
  if (!el) return;
  const eventName = (el.type === 'checkbox' || el.tagName === 'SELECT') ? 'change' : 'input';
  el.addEventListener(eventName, () => { if (id === 'joy_backend') updateCaptionBackendUI(); saveJoySettings(); });
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
    alert(data.error || 'Failed to start Caption');
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
    const res = await fetch('/captions_json', {
      headers: { 'X-Requested-With': 'XMLHttpRequest' }
    });
    const data = await res.json();
    if (!res.ok || !data.ok || !Array.isArray(data.pairs)) return;

    const byName = new Map(data.pairs.map(p => [p.img_name, decodeCaptionFieldValue(p.text)]));

    document.querySelectorAll('.caption-textarea').forEach(ta => {
      const imgName = ta.dataset.img;
      if (!byName.has(imgName)) return;

      const latest = byName.get(imgName);
      const index = parseInt(ta.dataset.index, 10);
      const original = ta.dataset.original ?? '';
      const dirty = ta.value !== original;

      if (dirty && preserveDirty) return;
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
    joyStatusText.textContent = total > 0
      ? `${data.status} | captions: ${data.count}/${total}`
      : `${data.status} | captions: ${data.count}`;
    updateJoyProgress(data.count, total);
    joyLogBox.textContent = data.log || '';
    if (!joyAutoScroll || joyAutoScroll.checked) {
      joyLogBox.scrollTop = joyLogBox.scrollHeight;
    }
    if (data.running) {
      await refreshCaptionsFromDisk(true);
    } else if (data.reload_pairs) {
      await refreshCaptionsFromDisk(false);
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
      <div class="summary-chart-title">Categories</div>
      <div class="summary-empty-chart">No category data</div>
    `;
  }

  const closeupPalette = ['#4ade80', '#22c55e', '#22c55e', '#34d399', '#34d399', '#166534', '#16a34a', '#16a34a'];
  const mediumPalette = ['#fde047', '#eab308', '#854d0e'];
  const fullbodyPalette = ['#f87171', '#dc2626', '#7f1d1d'];
  const neutralColor = '#9ca3af';

  function colorForCategory(name) {
    const label = String(name || '');
    const closeupOrder = ['Close-up Front', 'Close-up Left', 'Close-up Right', 'Close-up Front-left', 'Close-up Front-right', 'Close-up Back', 'Close-up From Above', 'Close-up From Below'];
    const mediumOrder = ['Medium Front', 'Medium Profile', 'Medium Back'];
    const fullbodyOrder = ['Full body Front', 'Full body Profile', 'Full body Back'];

    if (label.startsWith('Close-up')) {
      const idx = closeupOrder.indexOf(label);
      return closeupPalette[idx >= 0 ? idx : 0];
    }
    if (label.startsWith('Medium')) {
      const idx = mediumOrder.indexOf(label);
      return mediumPalette[idx >= 0 ? idx : 0];
    }
    if (label.startsWith('Full body')) {
      const idx = fullbodyOrder.indexOf(label);
      return fullbodyPalette[idx >= 0 ? idx : 0];
    }
    return neutralColor;
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

  const legendItems = (allItems.length ? allItems : items)
    .filter(item => String(item.name || '') !== 'Undefined');
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

  const grouped = data?.category_group_percentages || {};
  const groupedHtml = `
    <div class="summary-category-group-block">
      <div><span style="background:${closeupPalette[1]};"></span>Close-up ${grouped.portrait ?? 0}%</div>
      <div><span style="background:${mediumPalette[1]};"></span>Medium ${grouped.kneeup ?? 0}%</div>
      <div><span style="background:${fullbodyPalette[1]};"></span>Full body ${grouped.fullbody ?? 0}%</div>
    </div>
  `;

  return `
    <div class="summary-chart-title">Categories</div>
    <div class="summary-pie-layout">
      <div class="summary-pie-chart-wrap">
        <div class="summary-pie-chart" style="background:conic-gradient(${gradients.join(', ')});">
          ${iconNodes.join('')}
        </div>
        ${labelNodes.join('')}
      </div>
      <div class="summary-pie-legend-wrap">
        <div class="summary-pie-legend">${legend}</div>
        ${groupedHtml}
      </div>
    </div>
  `;
}

function buildSummaryHtml(data) {
  const allowed = new Set(
    getAllowedBuckets().map(([w, h]) => `${w}x${h} (${getAspectLabel(w, h)})`)
  );

  const resolutionLines = (data.items || []).map(item => {
    const invalid = !allowed.has(item.bucket);
    const bucketHtml = invalid
      ? `<span style="color: var(--danger);">${item.bucket}</span>`
      : item.bucket;
    return `<div class="summary-resolution-row"><span>${bucketHtml}</span><b>${item.count}</b></div>`;
  }).join('');
  const totalImages = Number(data.total_images ?? 0);
  const totalCaptions = Number(data.total_captions ?? 0);
  const invalidBuckets = (data.items || []).filter(item => !allowed.has(item.bucket)).length;

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
        <div class="summary-tile"><div class="summary-tile-label">Invalid buckets</div><div class="summary-tile-value">${invalidBuckets}</div></div>
      </div>
      <div class="summary-chart-card">
        ${renderAspectBarChart(data)}
        <div class="summary-stats-block summary-stats-left" style="margin-top:4px;">
          <div class="summary-chart-title" style="margin-top:10px;">Resolutions</div>
          <div class="summary-resolution-lines">${resolutionLines}</div>
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
summaryModalBackdrop?.addEventListener("click", (e) => {
  if (e.target === summaryModalBackdrop) closeSummaryModal();
});

const toolsModalBackdrop = document.getElementById("toolsModalBackdrop");
const openToolsModalBtnInline = document.getElementById("openToolsModalBtnInline");
const closeToolsModalBtn = document.getElementById("closeToolsModalBtn");
const toolsResult = document.getElementById("toolsResult");
const replaceForm = document.getElementById("replaceForm");
const countForm = document.getElementById("countForm");
const triggerForm = document.getElementById("triggerForm");

loadToolsSettings();
document.getElementById('sr_use_regex')?.addEventListener('change', saveToolsSettings);

function openToolsModal() {
  toolsModalBackdrop?.classList.add("open");
}
function closeToolsModal() {
  toolsModalBackdrop?.classList.remove("open");
}
openToolsModalBtnInline?.addEventListener("click", openToolsModal);
closeToolsModalBtn?.addEventListener("click", closeToolsModal);
toolsModalBackdrop?.addEventListener("click", (e) => {
  if (e.target === toolsModalBackdrop) closeToolsModal();
});

replaceForm?.addEventListener("submit", async (e) => {
  e.preventDefault();

  const ok = confirmReplace();
  if (!ok) return;

  const formData = new FormData(replaceForm);

  try {
    if (toolsResult) toolsResult.textContent = "Replacing...";
    const res = await fetch("/replace_all", {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      body: formData
    });
    const data = await res.json();

    if (!res.ok || !data.ok) {
      if (toolsResult) toolsResult.textContent = data.error || "Replace failed.";
      return;
    }

    if (toolsResult) toolsResult.textContent = data.message || "Replace complete.";

    const captionsRes = await fetch("/captions_json", {
      headers: { "X-Requested-With": "XMLHttpRequest" }
    });
    const captionsData = await captionsRes.json();

    if (captionsRes.ok && captionsData.ok && Array.isArray(captionsData.pairs)) {
      const byName = new Map(captionsData.pairs.map(p => [p.img_name, p.text]));
      document.querySelectorAll('.caption-textarea').forEach(ta => {
        const imgName = ta.dataset.img;
        if (byName.has(imgName)) {
          ta.value = byName.get(imgName);
          ta.dataset.original = byName.get(imgName);
          const index = parseInt(ta.dataset.index, 10);
          markUnsaved(index);
        }
      });
    }

    openToolsModal();
  } catch (err) {
    if (toolsResult) toolsResult.textContent = `Replace failed: ${err}`;
  }
});

countForm?.addEventListener("submit", async (e) => {
  e.preventDefault();

  const formData = new FormData(countForm);

  try {
    if (toolsResult) toolsResult.textContent = "Counting...";
    const res = await fetch("/count_string", {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      body: formData
    });
    const data = await res.json();

    if (!res.ok || !data.ok) {
      if (toolsResult) toolsResult.textContent = data.error || "Count failed.";
      return;
    }

    if (toolsResult) toolsResult.textContent = data.message || "Count complete.";
  } catch (err) {
    if (toolsResult) toolsResult.textContent = `Count failed: ${err}`;
  }
});


triggerForm?.addEventListener("submit", async (e) => {
  e.preventDefault();

  const formData = new FormData(triggerForm);

  try {
    if (toolsResult) toolsResult.textContent = "Adding trigger word...";
    const res = await fetch("/add_triggerword_all", {
      method: "POST",
      headers: { "X-Requested-With": "XMLHttpRequest" },
      body: formData
    });
    const data = await res.json();

    if (!res.ok || !data.ok) {
      if (toolsResult) toolsResult.textContent = data.error || "Add trigger word failed.";
      return;
    }

    if (toolsResult) toolsResult.textContent = data.message || "Add trigger word complete.";

    const captionsRes = await fetch("/captions_json", {
      headers: { "X-Requested-With": "XMLHttpRequest" }
    });
    const captionsData = await captionsRes.json();

    if (captionsRes.ok && captionsData.ok && Array.isArray(captionsData.pairs)) {
      const byName = new Map(captionsData.pairs.map(p => [p.img_name, p.text]));
      document.querySelectorAll('.caption-textarea').forEach(ta => {
        const imgName = ta.dataset.img;
        if (byName.has(imgName)) {
          ta.value = byName.get(imgName);
          ta.dataset.original = byName.get(imgName);
          const index = parseInt(ta.dataset.index, 10);
          markUnsaved(index);
        }
      });
    }

    openToolsModal();
  } catch (err) {
    if (toolsResult) toolsResult.textContent = `Add trigger word failed: ${err}`;
  }
});


function hasUnsavedChanges() {
  return getUnsavedCardIndexes().length > 0;
}

function resetAllUnsavedChanges() {
  document.querySelectorAll('.pair-card').forEach(card => {
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

    renderImageTransform(index);
    renderCrop(index);
    markUnsaved(index);
  });
}

window.addEventListener('beforeunload', (e) => {
  if (suppressBeforeUnload) return;
  if (!hasUnsavedChanges()) return;
  e.preventDefault();
  e.returnValue = '';
});

document.addEventListener('keydown', async (e) => {
  const isMod = e.ctrlKey || e.metaKey;
  const tag = (document.activeElement?.tagName || '').toLowerCase();
  const typingIntoField =
    ['input', 'textarea', 'select'].includes(tag) ||
    document.activeElement?.isContentEditable;

  const isSave = (e.key === 's' || e.key === 'S');
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


@app.after_request
def add_no_cache_headers(response):
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/", methods=["GET"])
def index():
    pair_dicts = build_pairs_context() if current_folder else []
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
        category_defs_json=json.dumps(CATEGORY_DEFS),
    )



@app.route("/add_files", methods=["POST"])
def add_files():
    global current_folder, pairs_cache, message, folder_name

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
        added_count += 1

    pairs_cache = load_pairs(current_folder)
    folder_name = os.path.basename(current_folder) if current_folder else ""
    message = f"Added {added_count} file(s)." if added_count else "No new files were added."
    return redirect(url_for("index"))


@app.route("/upload_images", methods=["POST"])
def upload_images():
    global current_folder, pairs_cache, message, folder_name

    if not current_folder or not os.path.isdir(current_folder):
        return jsonify({"ok": False, "error": "Open a folder before adding images."}), 400

    uploads = request.files.getlist("images")
    if not uploads:
        return jsonify({"ok": False, "error": "No image files were uploaded."}), 400

    folder_path = Path(current_folder)
    added = []
    skipped = []

    for i, storage in enumerate(uploads, start=1):
        ext = upload_image_extension(storage)
        if not ext:
            skipped.append(storage.filename or f"file_{i}")
            continue

        fallback = "pasted_image" if not storage.filename else "image"
        stem = clean_upload_stem(storage.filename, fallback=fallback)
        target_name = make_unique_image_name(current_folder, stem, ext)
        target_path = folder_path / target_name

        storage.save(target_path)
        txt_path = target_path.with_suffix(".txt")
        if not txt_path.exists():
            txt_path.write_text("", encoding="utf-8")
        added.append(target_name)

    pairs_cache = load_pairs(current_folder)
    folder_name = os.path.basename(current_folder) if current_folder else ""

    if added:
        message = f"Added {len(added)} image(s)."
    else:
        message = "No supported image files were added."

    return jsonify({
        "ok": True,
        "added": added,
        "skipped": skipped,
        "message": message,
    })


@app.route("/open_folder", methods=["POST"])
def open_folder():
    global current_folder, pairs_cache, message, folder_name, category_assignments, selected_crop_base
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory()
    root.destroy()

    if not folder:
        current_folder = None
        pairs_cache = []
        folder_name = ""
        message = ""
        return redirect(url_for("index"))

    current_folder = folder
    folder_name = folder
    category_assignments = load_category_assignments(folder)
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
    return redirect(url_for("index"))


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
            os.remove(src_path)
            if img_name in updated_categories:
                updated_categories[target_name] = updated_categories.pop(img_name)
            converted += 1
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
    return jsonify({"ok": True, "converted": converted, "skipped": skipped, "message": msg})


@app.route("/close_folder", methods=["POST"])
def close_folder():
    global current_folder, pairs_cache, message, folder_name, category_assignments

    if joycaption_status.get("running"):
        joycaption_status["interrupt_requested"] = True
        stop_kobold_process()
        joycaption_status["status"] = "Caption: interrupting…"

    current_folder = None
    pairs_cache = []
    message = ""
    folder_name = ""
    category_assignments = {}
    return redirect(url_for("index"))


@app.route("/image/<path:filename>")
def image(filename):
    return send_from_directory(current_folder, filename)


@app.route("/category_icon/<path:filename>")
def category_icon(filename):
    return send_from_directory(APP_DIR / "images", filename)


@app.route("/switch/video", methods=["POST", "GET"])
def switch_to_video():
    remember_app("video")
    launch_local_app("videoprep.py", 5001)
    exit_soon()
    return switch_page("http://127.0.0.1:5001/", "Video Prep")


@app.route("/rename_all_pairs", methods=["POST"])
def rename_all_pairs():
    global pairs_cache, message, category_assignments
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    data = request.get_json(force=True) or {}
    prefix = str(data.get("prefix", ""))

    ordered_pairs = [pair for pair in pairs_cache if pair_exists(current_folder, pair[0])]
    if not ordered_pairs:
        return jsonify({"ok": False, "error": "No image/text pairs found."}), 400

    temp_records = []
    renamed_categories = {}
    try:
        for i, (img_name, _) in enumerate(ordered_pairs):
            img_path = os.path.join(current_folder, img_name)
            txt_path = os.path.splitext(img_path)[0] + ".txt"
            ext = os.path.splitext(img_name)[1]
            target_stem = f"{prefix}{i:05d}"
            temp_img = os.path.join(current_folder, f"__renaming__{i:05d}{ext}")
            temp_txt = os.path.join(current_folder, f"__renaming__{i:05d}.txt")

            os.replace(img_path, temp_img)
            had_txt = os.path.exists(txt_path)
            if had_txt:
                os.replace(txt_path, temp_txt)
            temp_records.append((temp_img, temp_txt, target_stem, ext, had_txt, img_name))

        for temp_img, temp_txt, target_stem, ext, had_txt, old_img_name in temp_records:
            final_img = os.path.join(current_folder, f"{target_stem}{ext}")
            final_txt = os.path.join(current_folder, f"{target_stem}.txt")
            os.replace(temp_img, final_img)
            if had_txt:
                os.replace(temp_txt, final_txt)
            renamed_categories[os.path.basename(final_img)] = normalize_category_name(category_assignments.get(old_img_name, DEFAULT_CATEGORY))

    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    pairs_cache = load_pairs(current_folder)
    category_assignments = renamed_categories
    save_category_assignments(current_folder, category_assignments)
    message = "Renamed all image/text pairs."
    return jsonify({"ok": True})


@app.route("/captions_json")
def captions_json():
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    pairs = []
    for i, (img_name, text) in enumerate(pairs_cache):
        if not pair_exists(current_folder, img_name):
            continue
        pairs.append({
            "index": i,
            "img_name": img_name,
            "text": text,
        })

    return jsonify({"ok": True, "pairs": pairs})


@app.route("/save_pair", methods=["POST"])
def save_pair():
    global pairs_cache, message
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder opened."}), 400

    data = request.get_json(force=True)
    img_name = data.get("img_name")
    caption = data.get("caption", "")
    crop = data.get("crop")
    transforms = data.get("transforms") or {}

    if not img_name:
        return jsonify({"ok": False, "error": "Missing image name."}), 400

    img_path = os.path.join(current_folder, img_name)
    txt_path = os.path.splitext(img_path)[0] + ".txt"

    with open(txt_path, "w", encoding="utf-8") as f:
        f.write(caption)

    if crop or transforms:
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

    try:
        shutil.copy2(src_img, target_img)
        if os.path.exists(src_txt):
            shutil.copy2(src_txt, target_txt)
        else:
            Path(target_txt).write_text("", encoding="utf-8")
    except Exception as e:
        return jsonify({"ok": False, "error": str(e)}), 500

    category_assignments[target_name] = normalize_category_name(category_assignments.get(img_name, DEFAULT_CATEGORY))
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
    target_img = os.path.join(current_folder, target_name)
    target_txt = os.path.splitext(target_img)[0] + ".txt"

    if target_name != img_name:
        try:
            os.replace(src_img, target_img)
            if os.path.exists(src_txt):
                os.replace(src_txt, target_txt)
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
    txt_path = os.path.splitext(img_path)[0] + ".txt"
    try:
        if os.path.exists(img_path):
            os.remove(img_path)
        if os.path.exists(txt_path):
            os.remove(txt_path)
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

    category_assignments[img_name] = category
    save_category_assignments(current_folder, category_assignments)
    return jsonify({
        "ok": True,
        "category": category,
        "icon": CATEGORY_NAME_TO_ICON.get(category, CATEGORY_NAME_TO_ICON[DEFAULT_CATEGORY]),
    })


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

    try:
        count = replace_in_all_captions(current_folder, match_string, replace_with, use_regex=use_regex)
        pairs_cache = load_pairs(current_folder)
        result_text = f"Replaced {count} occurrence(s)."

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
    if trigger_word is None or trigger_word == "":
        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": False, "error": "Missing trigger word."}), 400
        message = "Missing trigger word."
        return redirect(url_for("index"))

    changed = 0
    for img_name in [f for f in sorted(os.listdir(current_folder)) if f.lower().endswith(IMAGE_EXTENSIONS)]:
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
    result_text = f"Added trigger word to {changed} file(s)."

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
    try:
        count = count_in_all_captions(current_folder, count_regex)
        result_text = f"Found {count} occurrence(s)."

        if request.headers.get("X-Requested-With") == "XMLHttpRequest":
            return jsonify({"ok": True, "message": result_text})

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
    category_counts = defaultdict(int)
    allowed_selected = set(get_bucket_options(selected_crop_base))
    predefined_aspects = get_predefined_kohya_aspect_labels()
    predefined_aspect_set = set(predefined_aspects)
    aspect_ratio_counts = defaultdict(int)

    for img_file, _ in pairs_cache:
        width, height, _ = get_image_info(img_file)
        category_counts[get_pair_category(img_file)] += 1

        bucket_w = (width // BUCKET_STEP) * BUCKET_STEP
        bucket_h = (height // BUCKET_STEP) * BUCKET_STEP
        aspect_label = get_aspect_label(bucket_w, bucket_h)
        bucket_key = f"{bucket_w}x{bucket_h} ({aspect_label})"
        bucket_counts[(bucket_key, (bucket_w, bucket_h) in allowed_selected)] += 1

        exact_aspect = get_aspect_label(width, height)
        if exact_aspect in predefined_aspect_set:
            aspect_ratio_counts[exact_aspect] += 1
        else:
            aspect_ratio_counts["???"] += 1

    summary_items = [
        {"bucket": bucket, "count": count, "valid": valid}
        for (bucket, valid), count in sorted(bucket_counts.items(), key=lambda x: x[0][0])
    ]
    summary_text = "<div><b>Resolution distribution:</b></div>"
    for item in summary_items:
        color = "inherit" if item["valid"] else "#dc2626"
        summary_text += f"<span style='padding-left:16px; color:{color};'>{item['bucket']}: {item['count']}</span><br>"
    summary_text += f"<br><b>Total Images:</b> {len(pairs_cache)}"
    summary_text += f"<br><b>Total Captions:</b> {len(pairs_cache)}"
    total_images = len(pairs_cache)
    if total_images > 0:
        portrait_count = sum(category_counts.get(item["name"], 0) for item in CATEGORY_DEFS if item["name"].startswith("Close-up"))
        kneeup_count = sum(category_counts.get(item["name"], 0) for item in CATEGORY_DEFS if item["name"].startswith("Medium"))
        fullbody_count = sum(category_counts.get(item["name"], 0) for item in CATEGORY_DEFS if item["name"].startswith("Full body"))
        category_group_percentages = {
            "portrait": round((portrait_count / total_images) * 100),
            "kneeup": round((kneeup_count / total_images) * 100),
            "fullbody": round((fullbody_count / total_images) * 100),
        }
    else:
        category_group_percentages = {"portrait": 0, "kneeup": 0, "fullbody": 0}

    summary_text += "<br><br><b>Categories:</b><br>"

    def _cat_line(label, value, indent=False):
        numeric = value if isinstance(value, int) else int(str(value).rstrip('%') or 0)
        color = '#dc2626' if numeric == 0 and label != 'Undefined' else 'inherit'
        pad = 'padding-left:16px; ' if indent else ''
        return f"<span style='{pad}color:{color};'>{label}: {value}</span><br>"

    summary_text += _cat_line('Close-up', f"{category_group_percentages['portrait']}%")
    for category in [item["name"] for item in CATEGORY_DEFS if item["name"].startswith("Close-up")]:
        summary_text += _cat_line(category, category_counts.get(category, 0), True)

    summary_text += _cat_line('Medium', f"{category_group_percentages['kneeup']}%")
    for category in [item["name"] for item in CATEGORY_DEFS if item["name"].startswith("Medium")]:
        summary_text += _cat_line(category, category_counts.get(category, 0), True)

    summary_text += _cat_line('Full body', f"{category_group_percentages['fullbody']}%")
    for category in [item["name"] for item in CATEGORY_DEFS if item["name"].startswith("Full body")]:
        summary_text += _cat_line(category, category_counts.get(category, 0), True)

    if any(item["name"] == 'Undefined' for item in CATEGORY_DEFS):
        summary_text += _cat_line('Undefined', category_counts.get('Undefined', 0), False)

    aspect_chart = [{"label": label, "count": aspect_ratio_counts.get(label, 0)} for label in predefined_aspects]
    aspect_chart.append({"label": "???", "count": aspect_ratio_counts.get("???", 0)})

    category_chart = [
        {"name": item["name"], "count": category_counts.get(item["name"], 0), "percent": (round((category_counts.get(item["name"], 0) / total_images) * 100) if total_images else 0), "icon": item["icon"]}
        for item in CATEGORY_DEFS
    ]

    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in (request.headers.get("Accept") or "")
    if wants_json:
        return jsonify({
            "ok": True,
            "items": summary_items,
            "total_images": len(pairs_cache),
            "total_captions": len(pairs_cache),
            "html": summary_text,
            "categories": [{"name": item["name"], "count": category_counts.get(item["name"], 0)} for item in CATEGORY_DEFS],
            "category_group_percentages": category_group_percentages,
            "aspect_chart": aspect_chart,
            "category_chart": category_chart,
        })

    message = summary_text
    return redirect(url_for("index"))


@app.route("/backup")
def backup():
    global message
    if not current_folder:
        message = "No folder opened."
        return redirect(url_for("index"))

    backup_dir = os.path.join(current_folder, "BACKUP")
    if os.path.isdir(backup_dir) and os.listdir(backup_dir):
        message = "BACKUP folder already exists and is not empty."
        return redirect(url_for("index"))

    os.makedirs(backup_dir, exist_ok=True)
    copied = 0
    for img_name, _ in pairs_cache:
        src_img = os.path.join(current_folder, img_name)
        src_txt = os.path.splitext(src_img)[0] + ".txt"
        shutil.copy2(src_img, os.path.join(backup_dir, img_name))
        if os.path.exists(src_txt):
            shutil.copy2(src_txt, os.path.join(backup_dir, os.path.basename(src_txt)))
        copied += 1

    message = f"Backed up {copied} image/caption pair(s) to BACKUP."
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
    joycaption_status["running"] = False
    joycaption_status["status"] = "Idle"
    joycaption_status["log"] = ""
    joycaption_status["process"] = None
    joycaption_status["count"] = 0
    joycaption_status["total"] = 0
    joycaption_status["last_rc"] = None
    joycaption_status["interrupt_requested"] = False
    joycaption_status["reload_pairs"] = False

    app.run(host="127.0.0.1", port=5000, debug=False)
