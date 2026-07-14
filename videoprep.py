import os
import sys
import base64
import gc
import json
import math
import re
import shutil
import subprocess
import tempfile
import threading
import time
import webbrowser
import socket
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from flask import Flask, jsonify, redirect, render_template_string, request, send_from_directory, url_for
import requests

if sys.platform.startswith("win"):
    import multiprocessing
    multiprocessing.freeze_support()

APP_DIR = Path(__file__).resolve().parent
SETTINGS_DIR = APP_DIR / "settings"
LAST_APP_FILE = SETTINGS_DIR / ".dataset_forge_last_app"
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v")
VIDEO_MIME_EXTENSIONS = {
    "video/mp4": ".mp4",
    "video/x-matroska": ".mkv",
    "video/webm": ".webm",
    "video/quicktime": ".mov",
    "video/x-msvideo": ".avi",
}
BUCKET_STEP = 64
FFPROBE_EXE = shutil.which("ffprobe") or "ffprobe"
FFMPEG_EXE = shutil.which("ffmpeg") or "ffmpeg"
FINAL_VIDEO_CRF = "18"
FINAL_VIDEO_PRESET = "medium"
WORK_VIDEO_CRF = "0"
WORK_VIDEO_PRESET = "ultrafast"
X264_PRESETS = {"ultrafast", "superfast", "veryfast", "faster", "fast", "medium", "slow", "slower", "veryslow"}
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
VIDEO_QWEN3_VL_DEFAULT_PROMPT = (
    "Generate only a concise comma-separated LoRA caption for the video.\n\n"
    "Start the caption with [name]. Use [name] as the character name or training trigger. "
    "Mention [name] only once.\n\n"
    "Focus on what happens over time in the video. Describe the visible sequence of actions, "
    "pose changes, gestures, gaze shifts, expression changes, body movement, clothing movement, "
    "camera movement, and scene motion.\n\n"
    "Use short visual phrases in temporal order when possible. Prefer motion-based descriptions "
    "such as turning, walking, leaning, raising an arm, looking away, smiling, blinking, "
    "hair moving, fabric moving, camera pushing in, or background motion.\n\n"
    "Do not describe body shape, body proportions, hair color, eye color, identity, story, "
    "intent, age, ethnicity, or personality. Do not invent details. Do not mention metadata, "
    "filename, timestamps, resolution, quality, or that this is a video.\n\n"
    "Output only the caption, with no intro or explanation."
)
QWEN3_VL_FULL_VIDEO_LOCAL_FRAME_LIMIT = 16
QWEN3_VL_FULL_VIDEO_API_FRAME_LIMIT = 64

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
    1280: [],
    1536: [],
}

current_folder = None
message = ""
pairs_cache = []
convert_lock = threading.Lock()
convert_process = None
convert_status = {
    "running": False,
    "done": False,
    "interrupted": False,
    "interrupt_requested": False,
    "count": 0,
    "total": 0,
    "converted": 0,
    "skipped": 0,
    "percent": 0,
    "errors": [],
    "current": "",
    "status": "Idle",
}
caption_lock = threading.Lock()
caption_status = {
    "running": False,
    "done": False,
    "interrupt_requested": False,
    "count": 0,
    "total": 0,
    "status": "Idle",
    "log": "",
    "error": "",
    "reload_pairs": False,
}

app = Flask(__name__)


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


def hidden_check_output(cmd):
    return subprocess.check_output(
        cmd,
        stdin=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **hidden_subprocess_kwargs(),
    )


def hidden_check_call(cmd):
    result = subprocess.run(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **hidden_subprocess_kwargs(),
    )
    if result.returncode != 0:
        detail = (result.stderr or result.stdout or "").strip()
        raise RuntimeError(detail or f"Command exited with code {result.returncode}")


def video_encode_args(final_compress=False, preset=None):
    if final_compress:
        final_preset = preset if preset in X264_PRESETS else FINAL_VIDEO_PRESET
        return [
            "-c:v", "libx264",
            "-preset", final_preset,
            "-crf", FINAL_VIDEO_CRF,
            "-pix_fmt", "yuv420p",
        ]
    return [
        "-c:v", "libx264",
        "-preset", WORK_VIDEO_PRESET,
        "-crf", WORK_VIDEO_CRF,
        "-pix_fmt", "yuv420p",
    ]


def audio_encode_args(final_compress=False):
    if final_compress:
        return ["-c:a", "aac", "-b:a", "192k"]
    return ["-c:a", "alac"]


def output_suffix_for_video(source_suffix, requested_format="same"):
    requested = str(requested_format or "same").strip().lower()
    if requested == "mp4":
        return ".mp4"
    if requested == "mkv":
        return ".mkv"
    source = str(source_suffix or "").lower()
    if source in {".mp4", ".mkv", ".mov", ".m4v"}:
        return source
    return ".mp4"


def parse_optional_fps(value):
    if value in (None, ""):
        return None
    try:
        fps = float(value)
    except Exception:
        return None
    if not math.isfinite(fps) or fps <= 0 or fps > 240:
        return None
    return fps


def parse_int_setting(value, fallback, minimum, maximum):
    try:
        parsed = int(float(value))
    except Exception:
        parsed = fallback
    return max(minimum, min(maximum, parsed))


def hidden_popen(cmd):
    return subprocess.Popen(
        cmd,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
        **hidden_subprocess_kwargs(),
    )


def safe_unlink(path):
    path = Path(path)
    last_error = None
    for _ in range(10):
        try:
            path.unlink(missing_ok=True)
            return
        except PermissionError as e:
            last_error = e
            time.sleep(0.12)
    if last_error:
        raise last_error


def replace_file_with_retry(src, dst):
    src = Path(src)
    dst = Path(dst)
    last_error = None
    for _ in range(10):
        try:
            src.replace(dst)
            return
        except PermissionError as e:
            last_error = e
            time.sleep(0.12)
    try:
        shutil.copy2(src, dst)
        try:
            safe_unlink(src)
        except Exception:
            pass
        return
    except Exception:
        pass
    if last_error:
        raise last_error


def caption_interrupt_requested():
    return bool(caption_status.get("interrupt_requested"))


def append_caption_log(text):
    with caption_lock:
        caption_status["log"] = str(caption_status.get("log") or "") + str(text)


def set_caption_status(**updates):
    with caption_lock:
        caption_status.update(updates)


def caption_status_snapshot():
    with caption_lock:
        data = dict(caption_status)
    return data


def clear_torch_cuda_cache():
    try:
        gc.collect()
        import torch
        if torch.cuda.is_available():
            torch.cuda.empty_cache()
    except Exception:
        pass


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


def ffprobe_json(video_path: str) -> dict:
    cmd = [
        FFPROBE_EXE, "-v", "error",
        "-print_format", "json",
        "-show_streams", "-show_format",
        video_path,
    ]
    out = hidden_check_output(cmd)
    return json.loads(out)


def probe_video(video_path: str) -> dict:
    data = ffprobe_json(video_path)
    streams = data.get("streams", [])
    video_stream = next((s for s in streams if s.get("codec_type") == "video"), {})
    audio_stream = next((s for s in streams if s.get("codec_type") == "audio"), None)

    width = int(video_stream.get("width") or 0)
    height = int(video_stream.get("height") or 0)
    duration = float(video_stream.get("duration") or data.get("format", {}).get("duration") or 0.0)

    fps_raw = video_stream.get("avg_frame_rate") or video_stream.get("r_frame_rate") or "0/1"
    try:
        a, b = fps_raw.split("/")
        fps = float(a) / float(b) if float(b) else 0.0
    except Exception:
        fps = 0.0
    if fps <= 0:
        fps = 24.0

    try:
        total_frames = int(float(video_stream.get("nb_frames") or 0))
    except Exception:
        total_frames = 0
    if total_frames <= 0 and duration > 0:
        total_frames = int(round(duration * fps))

    return {
        "width": width,
        "height": height,
        "duration": duration,
        "fps": fps,
        "frames": total_frames,
        "has_audio": audio_stream is not None,
    }


def validate_converted_video(video_path: Path):
    meta = probe_video(str(video_path))
    if int(meta.get("width") or 0) <= 0 or int(meta.get("height") or 0) <= 0:
        raise RuntimeError("Converted video has invalid dimensions.")
    if float(meta.get("duration") or 0.0) <= 0:
        raise RuntimeError("Converted video has no duration.")
    if video_path.suffix.lower() == ".mp4":
        data = ffprobe_json(str(video_path))
        video_stream = next((s for s in data.get("streams", []) if s.get("codec_type") == "video"), {})
        codec = str(video_stream.get("codec_name") or "").lower()
        pix_fmt = str(video_stream.get("pix_fmt") or "").lower()
        if codec and codec != "h264":
            raise RuntimeError(f"Converted MP4 uses unsupported video codec: {codec}.")
        if pix_fmt and pix_fmt != "yuv420p":
            raise RuntimeError(f"Converted MP4 uses unsupported pixel format: {pix_fmt}.")


def clean_caption_text(text):
    value = str(text or "").strip()
    value = re.sub(r"\s+", " ", value)
    return value


def write_video_caption_result(txt_path, caption, options):
    caption = clean_caption_text(caption)
    if not caption:
        return
    append = bool((options or {}).get("append_existing"))
    existing = ""
    if os.path.exists(txt_path):
        existing = Path(txt_path).read_text(encoding="utf-8", errors="replace").strip()
    if append and existing:
        text = existing.rstrip()
        if not text.endswith((".", ",", ";", ":")):
            text += "."
        text = f"{text} {caption}"
    else:
        text = caption
    Path(txt_path).write_text(text.strip(), encoding="utf-8")


def format_whisperx_caption(caption):
    caption = clean_caption_text(caption)
    if not caption:
        return ""
    return f'Transcribed audio: "{caption.replace(chr(34), chr(39))}"'


def qwen3vl_model_id(model_name):
    if model_name not in QWEN3_VL_MODELS:
        raise ValueError(f"Unknown Qwen3-VL model: {model_name}")
    return QWEN3_VL_MODELS[model_name]


def load_qwen3_vl_local_model(model_name, options):
    global QWEN3_VL_LOCAL_MODEL_ID, QWEN3_VL_LOCAL_PROCESSOR, QWEN3_VL_LOCAL_MODEL
    model_id = qwen3vl_model_id(model_name)
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
                "Qwen3-VL local mode requires torch, transformers, accelerate, and qwen-vl-utils. "
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
                f"This environment has transformers {transformers.__version__}. "
                "Run install.bat again and allow it to recreate .venv."
            )

        QWEN3_VL_LOCAL_CACHE_DIR.mkdir(parents=True, exist_ok=True)
        append_caption_log(f"Loading local Qwen3-VL model: {model_id}\n")
        append_caption_log("First startup downloads the model and may take a while.\n")

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
            model = AutoModelForImageTextToText.from_pretrained(model_id, **model_kwargs)
        except TypeError:
            model_kwargs.pop("dtype", None)
            model_kwargs["torch_dtype"] = "auto"
            model = AutoModelForImageTextToText.from_pretrained(model_id, **model_kwargs)

        try:
            model.eval()
        except Exception:
            pass

        QWEN3_VL_LOCAL_MODEL_ID = model_id
        QWEN3_VL_LOCAL_PROCESSOR = processor
        QWEN3_VL_LOCAL_MODEL = model
        append_caption_log(f"Local Qwen3-VL ready on {getattr(model, 'device', None) or 'auto device map'}.\n")
        return processor, model


def evenly_thin_indices(indices, limit):
    indices = list(indices)
    if len(indices) <= limit:
        return indices
    if limit <= 1:
        return [indices[len(indices) // 2]]
    return [
        indices[int(round(i * (len(indices) - 1) / max(1, limit - 1)))]
        for i in range(limit)
    ]


def extract_video_frame_paths(video_path, options):
    options = options or {}
    legacy_use_all_frames = bool(options.get("qwen3vl_use_all_frames"))
    sampling_mode = str(options.get("qwen3vl_sampling_mode") or ("auto" if legacy_use_all_frames else "even")).strip().lower()
    if sampling_mode not in {"auto", "even", "nth"}:
        sampling_mode = "auto"
    manual_frame_count = parse_int_setting(options.get("qwen3vl_frame_count"), 12, 1, 256)
    every_nth_frame = parse_int_setting(options.get("qwen3vl_every_nth_frame"), 12, 1, 100000)
    backend_frame_limit = (
        QWEN3_VL_FULL_VIDEO_API_FRAME_LIMIT
        if str(options.get("backend") or "").strip().lower() == "external_api"
        else QWEN3_VL_FULL_VIDEO_LOCAL_FRAME_LIMIT
    )
    max_sampled_frames = parse_int_setting(
        options.get("qwen3vl_max_sampled_frames"),
        backend_frame_limit,
        1,
        backend_frame_limit,
    )
    max_side = max(128, min(4096, int(float(options.get("qwen3vl_max_image_side") or 512))))
    temp_dir = Path(tempfile.mkdtemp(prefix="video_caption_frames_"))
    frames = []
    try:
        import cv2
    except Exception as e:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError(f"OpenCV is required for video frame extraction: {e}")

    cap = cv2.VideoCapture(str(video_path))
    try:
        if not cap.isOpened():
            raise RuntimeError("Could not open video for frame extraction.")
        total_frames = int(cap.get(cv2.CAP_PROP_FRAME_COUNT) or 0)
        video_fps = float(cap.get(cv2.CAP_PROP_FPS) or 0.0)
        if not math.isfinite(video_fps) or video_fps <= 0:
            video_fps = 24.0
        if total_frames > 1:
            if sampling_mode == "nth":
                candidates = list(range(0, total_frames, every_nth_frame))
                indices = evenly_thin_indices(candidates, max_sampled_frames)
                append_caption_log(
                    f"Frame sampling: every {every_nth_frame} frame(s), "
                    f"using {len(indices)}/{len(candidates)} candidate frame(s) from {Path(video_path).name}.\n"
                )
            elif sampling_mode == "auto":
                target_sample_fps = 2.0
                nth = max(1, int(round(video_fps / target_sample_fps)))
                candidates = list(range(0, total_frames, nth))
                indices = evenly_thin_indices(candidates, max_sampled_frames)
                append_caption_log(
                    f"Frame sampling: auto at about {target_sample_fps:g} fps "
                    f"(every {nth} frame(s)), using {len(indices)}/{len(candidates)} candidate frame(s) from {Path(video_path).name}.\n"
                )
            else:
                frame_count = min(total_frames, manual_frame_count, max_sampled_frames)
                if frame_count >= total_frames:
                    indices = list(range(total_frames))
                elif frame_count == 1:
                    indices = [max(0, total_frames // 2)]
                else:
                    indices = [
                        int(round(i * (total_frames - 1) / max(1, frame_count - 1)))
                        for i in range(frame_count)
                    ]
                append_caption_log(
                    f"Frame sampling: {frame_count} evenly sampled frame(s) from {Path(video_path).name}.\n"
                )
        else:
            fallback_count = min(manual_frame_count, max_sampled_frames)
            if sampling_mode == "nth":
                fallback_count = min(max_sampled_frames, max(1, manual_frame_count))
            if total_frames > 0:
                indices = list(range(total_frames))
            else:
                indices = list(range(fallback_count))
            append_caption_log(f"Frame sampling: using {len(indices)} frame(s) from {Path(video_path).name}.\n")
        seen = set()
        for out_index, frame_index in enumerate(indices, start=1):
            if frame_index in seen and total_frames > 1:
                continue
            seen.add(frame_index)
            if total_frames > 1:
                cap.set(cv2.CAP_PROP_POS_FRAMES, frame_index)
            ok, frame = cap.read()
            if not ok or frame is None:
                continue
            height, width = frame.shape[:2]
            if max(width, height) > max_side:
                scale = max_side / max(width, height)
                frame = cv2.resize(
                    frame,
                    (max(1, int(round(width * scale))), max(1, int(round(height * scale)))),
                    interpolation=cv2.INTER_AREA,
                )
            target = temp_dir / f"frame_{out_index:03d}.jpg"
            if not cv2.imwrite(str(target), frame, [int(cv2.IMWRITE_JPEG_QUALITY), 92]):
                raise RuntimeError(f"Could not write extracted frame: {target}")
            frames.append(target)
    finally:
        cap.release()

    if not frames:
        shutil.rmtree(temp_dir, ignore_errors=True)
        raise RuntimeError("Could not extract frames from video.")
    return temp_dir, frames


def caption_video_with_qwen3_vl_local(video_path, options):
    model_name = options.get("qwen3vl_model", "Qwen3-VL-4B-Instruct")
    processor, model = load_qwen3_vl_local_model(model_name, options)
    try:
        import torch
        from PIL import Image
    except Exception as e:
        raise RuntimeError(f"Could not import Qwen3-VL local dependencies: {e}")

    system_prompt = str(options.get("qwen3vl_system_prompt") or VIDEO_QWEN3_VL_DEFAULT_PROMPT).strip()
    temperature = float(options.get("qwen3vl_temperature") or 0.2)
    max_tokens = int(float(options.get("qwen3vl_max_tokens") or 256))
    temp_dir, frames = extract_video_frame_paths(video_path, options)
    try:
        images = [Image.open(frame).convert("RGB") for frame in frames]
        user_prompt = f"{system_prompt}\n\nDescribe the video based on these evenly sampled frames."
        content = [
            {"type": "image", "image": image}
            for image in images
        ]
        content.append({"type": "text", "text": user_prompt})
        messages = [{"role": "user", "content": content}]
        append_caption_log(f"Preparing Qwen3-VL inputs from {len(images)} frame(s)...\n")
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

        generate_kwargs = {"max_new_tokens": max_tokens}
        if temperature > 0:
            generate_kwargs.update({"do_sample": True, "temperature": temperature})
        else:
            generate_kwargs["do_sample"] = False
        try:
            from transformers import StoppingCriteria, StoppingCriteriaList

            class CaptionInterruptCriteria(StoppingCriteria):
                def __call__(self, input_ids, scores, **kwargs):
                    return caption_interrupt_requested()

            generate_kwargs["stopping_criteria"] = StoppingCriteriaList([CaptionInterruptCriteria()])
        except Exception:
            pass

        try:
            with torch.inference_mode():
                output_ids = model.generate(**inputs, **generate_kwargs)
        except RuntimeError as e:
            if "out of memory" in str(e).lower():
                clear_torch_cuda_cache()
                raise RuntimeError(
                    "CUDA out of memory while captioning video frames. "
                    "Use fewer Frames, lower Max frame side, or disable full-video sampling."
                ) from e
            raise
        input_ids = inputs["input_ids"]
        trimmed = [out_ids[len(in_ids):] for in_ids, out_ids in zip(input_ids, output_ids)]
        output_text = processor.batch_decode(
            trimmed,
            skip_special_tokens=True,
            clean_up_tokenization_spaces=False,
        )
        return clean_caption_text(output_text[0] if output_text else "")
    finally:
        for image in locals().get("images", []):
            try:
                image.close()
            except Exception:
                pass
        shutil.rmtree(temp_dir, ignore_errors=True)


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
    system_prompt = str(
        options.get("external_api_system_prompt") or VIDEO_QWEN3_VL_DEFAULT_PROMPT
    ).strip()
    temperature = float(options.get("external_api_temperature") or 0.2)
    max_tokens = max(1, int(float(options.get("external_api_max_tokens") or 256)))
    return api_url, model_id, api_key, system_prompt, temperature, max_tokens


def caption_video_with_external_api(video_path, options):
    api_url, model_id, api_key, system_prompt, temperature, max_tokens = (
        external_api_generation_settings(options)
    )
    temp_dir, frames = extract_video_frame_paths(video_path, options)
    try:
        content = [{"type": "text", "text": "Describe the video based on these evenly sampled frames."}]
        for frame in frames:
            b64 = base64.b64encode(frame.read_bytes()).decode("ascii")
            content.append({
                "type": "image_url",
                "image_url": {"url": f"data:image/jpeg;base64,{b64}"},
            })
        payload = {
            "model": model_id,
            "messages": [
                {"role": "system", "content": system_prompt},
                {"role": "user", "content": content},
            ],
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        headers = {"Authorization": f"Bearer {api_key}"} if api_key else None
        r = requests.post(api_url, json=payload, headers=headers, timeout=900)
        if not r.ok:
            raise RuntimeError(f"External API error {r.status_code}: {r.text[:500]}")
        data = r.json()
        content = data["choices"][0]["message"]["content"]
        if isinstance(content, list):
            content = "".join(part.get("text", "") for part in content if isinstance(part, dict))
        return clean_caption_text(content)
    finally:
        shutil.rmtree(temp_dir, ignore_errors=True)


def caption_video_with_qwen3_vl(video_path, options):
    return caption_video_with_qwen3_vl_local(video_path, options)


def load_whisperx_runtime(options):
    try:
        import torch
        import whisperx
    except Exception as e:
        raise RuntimeError(
            "WhisperX is not installed. Run install.bat after updating, or install WhisperX into .venv. "
            f"Original error: {e}"
        )
    model_name = str((options or {}).get("whisperx_model") or "large-v3").strip()
    language = str((options or {}).get("whisperx_language") or "").strip() or None
    batch_size = max(1, int(float((options or {}).get("whisperx_batch_size") or 8)))
    vad_method = str((options or {}).get("whisperx_vad_method") or "silero").strip().lower()
    if vad_method not in {"silero", "pyannote"}:
        vad_method = "silero"
    device = "cuda" if torch.cuda.is_available() else "cpu"
    compute_type = "float16" if device == "cuda" else "int8"
    append_caption_log(f"Loading WhisperX {model_name} on {device} with {vad_method} VAD...\n")
    try:
        model = whisperx.load_model(
            model_name,
            device,
            compute_type=compute_type,
            language=language,
            vad_method=vad_method,
        )
    except ModuleNotFoundError as e:
        missing = getattr(e, "name", "") or str(e)
        raise RuntimeError(
            f"WhisperX dependency is missing: {missing}. "
            "Install video transcription dependencies with: "
            ".\\.venv\\Scripts\\python.exe -m pip install ctranslate2 faster-whisper omegaconf pandas nltk pyannote.audio torchcodec"
        ) from e
    return {
        "model": model,
        "batch_size": batch_size,
        "language": language,
    }


def transcribe_video_with_whisperx(video_path, options, runtime=None):
    try:
        if not probe_video(str(video_path)).get("has_audio"):
            append_caption_log(f"Skipping video without audio: {Path(video_path).name}\n")
            return ""
    except Exception:
        pass
    runtime = runtime or load_whisperx_runtime(options)
    if caption_interrupt_requested():
        return ""
    append_caption_log(f"Transcribing {Path(video_path).name}...\n")
    progress_state = {"last": -1}

    def progress_callback(percent):
        current = int(float(percent))
        if current >= progress_state["last"] + 25 or current >= 100:
            progress_state["last"] = current
            set_caption_status(status=f"Transcribing {Path(video_path).name} ({current}%)")

    result = runtime["model"].transcribe(
        str(video_path),
        batch_size=runtime["batch_size"],
        language=runtime["language"],
        print_progress=False,
        progress_callback=progress_callback,
    )
    segments = result.get("segments") or []
    text = " ".join(str(seg.get("text") or "").strip() for seg in segments).strip()
    if not text:
        append_caption_log(f"No speech transcript produced for {Path(video_path).name}.\n")
    return clean_caption_text(text)


def get_bucket_options(base: int):
    if base in KOHYA_BUCKETS and KOHYA_BUCKETS[base]:
        return [{"w": w, "h": h, "label": f"{w}x{h}"} for w, h in KOHYA_BUCKETS[base]]

    # fallback synthetic set for 1280 and 1536
    presets = []
    if base == 1280:
        presets = [(1280, 1280), (1152, 1408), (1408, 1152), (1024, 1536), (1536, 1024)]
    elif base == 1536:
        presets = [(1536, 1536), (1344, 1728), (1728, 1344), (1216, 1856), (1856, 1216)]
    return [{"w": w, "h": h, "label": f"{w}x{h}"} for w, h in presets]


def nearest_bucket(width: int, height: int, base: int):
    opts = get_bucket_options(base)
    if not opts:
        return width, height
    target_ratio = width / max(height, 1)
    best = None
    best_key = None
    for item in opts:
        w, h = item["w"], item["h"]
        ratio = w / h
        key = (abs(ratio - target_ratio), abs((w * h) - (width * height)))
        if best is None or key < best_key:
            best = (w, h)
            best_key = key
    return best


def get_aspect_label(width: int, height: int):
    width = int(width or 0)
    height = int(height or 0)
    if width <= 0 or height <= 0:
        return "???"
    divisor = math.gcd(width, height)
    return f"{width // divisor}:{height // divisor}"


def detect_bucket_base(width: int, height: int):
    width = int(width or 0)
    height = int(height or 0)
    for base in [512, 768, 1024, 1280, 1536]:
        for item in get_bucket_options(base):
            if width == int(item.get("w") or 0) and height == int(item.get("h") or 0):
                return base
    return None


def safe_stem_path(folder: str, stem: str, suffix: str):
    candidate = Path(folder) / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = Path(folder) / f"{stem}_{n}{suffix}"
        n += 1
    return candidate


def clean_upload_stem(filename, fallback="video"):
    stem = Path(filename or "").stem.strip() or fallback
    stem = re.sub(r'[\\/:*?"<>|]+', "_", stem)
    stem = re.sub(r"\s+", " ", stem).strip(" ._")
    return stem or fallback


def upload_video_extension(storage):
    name = storage.filename or ""
    ext = Path(name).suffix.lower()
    if ext in VIDEO_EXTENSIONS:
        return ext
    mime = (storage.mimetype or "").lower()
    return VIDEO_MIME_EXTENSIONS.get(mime, "")


def pair_for_video_path(video_path: Path, idx=0):
    folder = video_path.parent
    name = video_path.name
    try:
        stat = video_path.stat()
        cache_buster = f"{stat.st_mtime_ns}-{stat.st_size}"
    except Exception:
        cache_buster = "0"
    txt = video_path.with_suffix(".txt")
    if not txt.exists():
        txt.write_text("", encoding="utf-8")
    try:
        meta = probe_video(str(video_path))
    except Exception:
        meta = {"width": 0, "height": 0, "duration": 0.0, "fps": 24.0, "frames": 0, "has_audio": False}
    text = txt.read_text(encoding="utf-8", errors="replace")
    return {
        "index": idx,
        "name": name,
        "cache_buster": cache_buster,
        "caption": text,
        "width": meta["width"],
        "height": meta["height"],
        "duration": meta["duration"],
        "fps": meta["fps"],
        "frames": meta["frames"],
        "has_audio": meta["has_audio"],
    }


def load_pairs(folder: str):
    pairs = []
    idx = 0
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(VIDEO_EXTENSIONS):
            continue
        stem = Path(name).stem
        if stem.endswith("_fps_tmp") or stem.endswith("_edited"):
            continue
        if Path(name).suffix.lower() == ".webm" and (Path(folder) / f"{stem}.mp4").exists():
            continue
        pairs.append(pair_for_video_path(Path(folder) / name, idx))
        idx += 1
    return pairs


def refresh_pairs():
    global pairs_cache
    if current_folder:
        pairs_cache = load_pairs(current_folder)
    else:
        pairs_cache = []


def refresh_caption_cache():
    if not current_folder:
        return
    folder = Path(current_folder)
    for pair in pairs_cache:
        txt = folder / Path(pair.get("name", "")).with_suffix(".txt").name
        if txt.exists():
            pair["caption"] = txt.read_text(encoding="utf-8", errors="replace")
        else:
            pair["caption"] = ""


def choose_folder():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    folder = filedialog.askdirectory()
    root.destroy()
    return folder


def choose_files():
    root = tk.Tk()
    root.withdraw()
    root.attributes("-topmost", True)
    files = filedialog.askopenfilenames(
        filetypes=[("Video files", "*.mp4 *.mkv *.webm *.mov *.avi *.m4v")]
    )
    root.destroy()
    return list(files)


@app.route("/video/<path:filename>")
def serve_video(filename):
    if not current_folder:
        return "", 404
    response = send_from_directory(current_folder, filename, conditional=True, max_age=0)
    response.headers["Cache-Control"] = "no-store, no-cache, must-revalidate, max-age=0"
    response.headers["Pragma"] = "no-cache"
    response.headers["Expires"] = "0"
    return response


@app.route("/category_icon/<path:filename>")
def category_icon(filename):
    return send_from_directory(APP_DIR / "images", filename)




@app.route("/")
def index():
    pair_dicts = pairs_cache if current_folder else []
    return render_template_string(
        TEMPLATE,
        pairs=pair_dicts,
        current_folder=current_folder or "",
        message=message,
        bucket_options_json=json.dumps({str(b): get_bucket_options(b) for b in [512, 768, 1024, 1280, 1536]}),
    )


@app.post("/open_folder")
def open_folder():
    global current_folder, message
    folder = choose_folder()
    ajax = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if not folder:
        if ajax:
            return jsonify({"ok": True, "selected": False})
        return redirect(url_for("index"))
    current_folder = folder
    refresh_pairs()
    message = ""
    if ajax:
        return jsonify({"ok": True, "selected": True})
    return redirect(url_for("index"))


@app.post("/close_folder")
def close_folder():
    global current_folder, message, pairs_cache
    current_folder = None
    pairs_cache = []
    message = "Folder closed."
    return redirect(url_for("index"))


@app.post("/add_files")
def add_files():
    global message
    if not current_folder:
        message = "No folder is open."
        return redirect(url_for("index"))
    files = choose_files()
    if not files:
        return redirect(url_for("index"))
    added = 0
    for src in files:
        ext = Path(src).suffix.lower()
        if ext not in VIDEO_EXTENSIONS:
            continue
        dst = Path(current_folder) / Path(src).name
        if Path(src).resolve() != dst.resolve():
            if dst.exists():
                dst = safe_stem_path(current_folder, dst.stem, dst.suffix)
            shutil.copy2(src, dst)
        txt = dst.with_suffix(".txt")
        txt.touch(exist_ok=True)
        added += 1
    refresh_pairs()
    message = f"Added {added} file(s)."
    return redirect(url_for("index"))


@app.post("/upload_videos")
def upload_videos():
    global message
    if not current_folder or not os.path.isdir(current_folder):
        return jsonify({"ok": False, "error": "Open a folder before adding videos."}), 400

    uploads = request.files.getlist("videos")
    if not uploads:
        return jsonify({"ok": False, "error": "No video files were uploaded."}), 400

    added = []
    skipped = []
    for i, storage in enumerate(uploads, start=1):
        ext = upload_video_extension(storage)
        if not ext:
            skipped.append(storage.filename or f"file_{i}")
            continue

        stem = clean_upload_stem(storage.filename, fallback="pasted_video" if not storage.filename else "video")
        target_path = safe_stem_path(current_folder, stem, ext)
        try:
            storage.save(target_path)
            if not target_path.exists() or target_path.stat().st_size <= 0:
                raise RuntimeError("Empty upload.")
        except Exception:
            skipped.append(storage.filename or f"file_{i}")
            try:
                target_path.unlink(missing_ok=True)
            except Exception:
                pass
            continue

        txt = target_path.with_suffix(".txt")
        txt.touch(exist_ok=True)
        added.append(target_path.name)

    refresh_pairs()
    message = f"Added {len(added)} video(s)." if added else "No supported video files were added."
    return jsonify({
        "ok": True,
        "added": added,
        "skipped": skipped,
        "message": message,
        "total": len(pairs_cache),
    })


@app.post("/save_caption")
def save_caption():
    global message
    data = request.get_json(force=True)
    name = data["name"]
    caption = data.get("caption", "")
    skip_refresh = bool(data.get("skip_refresh", False))
    txt = Path(current_folder) / Path(name).with_suffix(".txt")
    txt.write_text(caption, encoding="utf-8")
    if not skip_refresh:
        refresh_pairs()
    return jsonify({"ok": True})


@app.post("/refresh_pairs")
def refresh_pairs_route():
    refresh_pairs()
    return jsonify({"ok": True})


@app.post("/refresh_folder")
def refresh_folder():
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder is open."}), 400
    before = {str(pair.get("name") or "") for pair in pairs_cache}
    refresh_pairs()
    after = {str(pair.get("name") or "") for pair in pairs_cache}
    added = len(after - before)
    return jsonify({"ok": True, "added": added, "total": len(pairs_cache)})


@app.post("/rename_pair")
def rename_pair():
    global message
    data = request.get_json(force=True)
    old_name = data["old_name"]
    new_stem = data["new_stem"].strip()
    if not new_stem:
        return jsonify({"ok": False, "error": "Missing name."}), 400
    old_video = Path(current_folder) / old_name
    old_txt = old_video.with_suffix(".txt")
    new_video = old_video.with_name(new_stem + old_video.suffix)
    new_txt = new_video.with_suffix(".txt")
    if new_video.exists() and new_video != old_video:
        return jsonify({"ok": False, "error": "Target already exists."}), 400
    old_video.rename(new_video)
    if old_txt.exists():
        old_txt.rename(new_txt)
    refresh_pairs()
    return jsonify({"ok": True, "new_name": new_video.name})


@app.post("/clone_pair")
def clone_pair():
    data = request.get_json(force=True)
    old_name = data["name"]
    skip_refresh = bool(data.get("skip_refresh", False))
    src_video = Path(current_folder) / old_name
    src_txt = src_video.with_suffix(".txt")
    dst_video = safe_stem_path(current_folder, src_video.stem, src_video.suffix)
    dst_txt = dst_video.with_suffix(".txt")
    shutil.copy2(src_video, dst_video)
    if src_txt.exists():
        shutil.copy2(src_txt, dst_txt)
    else:
        dst_txt.write_text("", encoding="utf-8")
    if not skip_refresh:
        refresh_pairs()
    return jsonify({"ok": True, "new_name": dst_video.name})


@app.post("/delete_pair")
def delete_pair():
    data = request.get_json(force=True)
    name = data["name"]
    skip_refresh = bool(data.get("skip_refresh", False))
    video = Path(current_folder) / name
    txt = video.with_suffix(".txt")
    if video.exists():
        video.unlink()
    if txt.exists():
        txt.unlink()
    if not skip_refresh:
        refresh_pairs()
    return jsonify({"ok": True})


@app.post("/backup")
def backup():
    global message
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest" or "application/json" in request.headers.get("Accept", "")
    if not current_folder:
        message = "No folder is open."
        if wants_json:
            return jsonify({"ok": False, "error": message}), 400
        return redirect(url_for("index"))
    backup_dir = Path(current_folder) / "_backup_videoprep"
    backup_dir.mkdir(exist_ok=True)
    count = 0
    for p in Path(current_folder).iterdir():
        if p.is_file() and (p.suffix.lower() in VIDEO_EXTENSIONS or p.suffix.lower() == ".txt"):
            shutil.copy2(p, backup_dir / p.name)
            count += 1
    message = f"Backup complete: {count} file(s)."
    if wants_json:
        return jsonify({"ok": True, "copied": count, "message": message})
    return redirect(url_for("index"))


def prepend_triggerword(caption, triggerword):
    triggerword = str(triggerword or '').strip()
    caption = str(caption or '')
    if not triggerword:
        return caption
    if not caption:
        return triggerword
    if caption.startswith(triggerword):
        return caption
    return f"{triggerword}{caption}"


def replace_in_all_captions(folder, match_string, replace_with, use_regex=False):
    count = 0
    for txt in Path(folder).glob("*.txt"):
        old = txt.read_text(encoding="utf-8", errors="replace")
        if use_regex:
            regex = re.compile(match_string, re.MULTILINE | re.DOTALL)
            new, changed = regex.subn(replace_with, old)
            if changed:
                count += changed
        else:
            occurrences = old.count(match_string)
            new = old.replace(match_string, replace_with)
            count += occurrences
        if new != old:
            txt.write_text(new, encoding="utf-8")
    return count


def count_in_all_captions(folder, count_string):
    regex = re.compile(count_string, re.MULTILINE | re.DOTALL)
    count = 0
    for txt in Path(folder).glob("*.txt"):
        content = txt.read_text(encoding="utf-8", errors="replace")
        count += len(regex.findall(content))
    return count


@app.get("/captions_json")
def captions_json():
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder is open."}), 400
    pairs = []
    for pair in pairs_cache:
        name = str(pair.get("name") or "")
        if not name:
            continue
        txt_path = (Path(current_folder) / name).with_suffix(".txt")
        try:
            text = txt_path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            text = ""
        pairs.append({"name": name, "text": text})
    return jsonify({
        "ok": True,
        "pairs": pairs,
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


@app.post("/text_replace")
def text_replace():
    global message
    find_text = request.form.get("find", "")
    repl_text = request.form.get("replace", "")
    if not current_folder:
        message = "No folder is open."
        return redirect(url_for("index"))
    count = 0
    for txt in Path(current_folder).glob("*.txt"):
        old = txt.read_text(encoding="utf-8", errors="replace")
        new = old.replace(find_text, repl_text)
        if new != old:
            txt.write_text(new, encoding="utf-8")
            count += 1
    refresh_pairs()
    message = f"Replaced text in {count} file(s)."
    return redirect(url_for("index"))


@app.post("/text_count")
def text_count():
    global message
    term = request.form.get("count", "")
    total = 0
    if current_folder:
        for txt in Path(current_folder).glob("*.txt"):
            total += txt.read_text(encoding="utf-8", errors="replace").count(term)
    message = f"Count for '{term}': {total}"
    return redirect(url_for("index"))


@app.post("/replace_all")
def replace_all():
    global message
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if not current_folder:
        error = "No folder is open."
        if wants_json:
            return jsonify({"ok": False, "error": error}), 400
        message = error
        return redirect(url_for("index"))
    match_string = request.form.get("match_string", "")
    replace_with = request.form.get("replace_with", "")
    use_regex = request.form.get("use_regex") == "1"
    if not match_string:
        error = "Missing search string."
        if wants_json:
            return jsonify({"ok": False, "error": error}), 400
        message = error
        return redirect(url_for("index"))
    try:
        count = replace_in_all_captions(current_folder, match_string, replace_with, use_regex=use_regex)
    except re.error as e:
        error = f"Regex error: {e}"
        if wants_json:
            return jsonify({"ok": False, "error": error}), 400
        message = error
        return redirect(url_for("index"))
    refresh_caption_cache()
    result_text = f"Replaced {count} match(es)."
    message = result_text
    if wants_json:
        return jsonify({"ok": True, "message": result_text, "count": count})
    return redirect(url_for("index"))


@app.post("/add_triggerword_all")
def add_triggerword_all():
    global message
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if not current_folder:
        error = "No folder is open."
        if wants_json:
            return jsonify({"ok": False, "error": error}), 400
        message = error
        return redirect(url_for("index"))
    trigger_word = request.form.get("trigger_word", "")
    if trigger_word is None or trigger_word == "":
        error = "Missing trigger word."
        if wants_json:
            return jsonify({"ok": False, "error": error}), 400
        message = error
        return redirect(url_for("index"))
    changed = 0
    for txt in Path(current_folder).glob("*.txt"):
        existing = txt.read_text(encoding="utf-8", errors="replace")
        new_text = prepend_triggerword(existing, trigger_word)
        if new_text != existing:
            txt.write_text(new_text, encoding="utf-8")
            changed += 1
    refresh_caption_cache()
    result_text = f"Added trigger word to {changed} file(s)."
    message = result_text
    if wants_json:
        return jsonify({"ok": True, "message": result_text, "changed": changed})
    return redirect(url_for("index"))


@app.post("/count_string")
def count_string():
    global message
    wants_json = request.headers.get("X-Requested-With") == "XMLHttpRequest"
    if not current_folder:
        error = "No folder is open."
        if wants_json:
            return jsonify({"ok": False, "error": error}), 400
        message = error
        return redirect(url_for("index"))
    count_regex = request.form.get("count_string", "")
    try:
        count = count_in_all_captions(current_folder, count_regex)
    except re.error as e:
        error = f"Regex error: {e}"
        if wants_json:
            return jsonify({"ok": False, "error": error}), 400
        message = error
        return redirect(url_for("index"))
    result_text = f"Count for regex '{count_regex}': {count}"
    message = result_text
    if wants_json:
        return jsonify({"ok": True, "message": result_text, "count": count})
    return redirect(url_for("index"))


def video_caption_worker(folder, options):
    global message
    backend = str((options or {}).get("backend") or "qwen3_vl").strip().lower()
    set_caption_status(
        running=True,
        done=False,
        interrupt_requested=False,
        count=0,
        total=0,
        status="Preparing",
        log="",
        error="",
        reload_pairs=False,
    )
    try:
        videos = []
        for pair in pairs_cache:
            name = str(pair.get("name") or "")
            if not name:
                continue
            path = Path(folder) / name
            if path.exists() and path.suffix.lower() in VIDEO_EXTENSIONS:
                videos.append((path, bool(pair.get("has_audio"))))
        target_videos = []
        no_overwrite = bool((options or {}).get("no_overwrite"))
        append_existing = bool((options or {}).get("append_existing"))
        for video_path, has_audio in videos:
            if backend == "whisperx" and not has_audio:
                append_caption_log(f"Skipping video without audio: {video_path.name}\n")
                continue
            txt_path = video_path.with_suffix(".txt")
            if no_overwrite and not append_existing and txt_path.exists() and txt_path.read_text(encoding="utf-8", errors="replace").strip():
                append_caption_log(f"Skipping existing caption: {video_path.name}\n")
                continue
            target_videos.append(video_path)
        set_caption_status(total=len(target_videos), status="Running")
        whisperx_runtime = None
        if backend == "qwen3_vl":
            append_caption_log("Backend: Qwen3-VL visual video captioning\n")
            load_qwen3_vl_local_model(options.get("qwen3vl_model", "Qwen3-VL-4B-Instruct"), options)
        elif backend == "external_api":
            api_url, model_id, _api_key, _prompt, _temperature, _max_tokens = (
                external_api_generation_settings(options)
            )
            append_caption_log(f"Backend: External API visual video captioning ({model_id})\n")
            append_caption_log(f"Endpoint: {api_url}\n")
        elif backend == "whisperx":
            append_caption_log("Backend: WhisperX audio transcription\n")
            if target_videos:
                whisperx_runtime = load_whisperx_runtime(options)
        else:
            raise RuntimeError(f"Unknown video caption backend: {backend}")

        for video_path in target_videos:
            if caption_interrupt_requested():
                set_caption_status(status="Interrupted")
                append_caption_log("Captioning interrupted.\n")
                break
            set_caption_status(status=f"Captioning {video_path.name}")
            append_caption_log(f"Captioning {video_path.name}...\n")
            if backend == "qwen3_vl":
                caption = caption_video_with_qwen3_vl(video_path, options)
            elif backend == "external_api":
                caption = caption_video_with_external_api(video_path, options)
            else:
                caption = transcribe_video_with_whisperx(video_path, options, whisperx_runtime)
            if caption_interrupt_requested():
                set_caption_status(status="Interrupted")
                append_caption_log("Captioning interrupted.\n")
                break
            if backend == "whisperx" and not str(caption or "").strip():
                append_caption_log(f"No transcript saved for {video_path.name}.\n")
                continue
            if backend == "whisperx":
                caption = format_whisperx_caption(caption)
            write_video_caption_result(video_path.with_suffix(".txt"), caption, options)
            with caption_lock:
                caption_status["count"] = int(caption_status.get("count") or 0) + 1
            append_caption_log(f"Captioned {video_path.name}\n")

        refresh_caption_cache()
        message = f"Captioned {caption_status.get('count', 0)} video(s)."
        set_caption_status(reload_pairs=True)
        if not caption_interrupt_requested():
            set_caption_status(status="Complete")
    except Exception as e:
        set_caption_status(status="Failed", error=str(e))
        append_caption_log(f"Error: {e}\n")
    finally:
        set_caption_status(running=False, done=True)


@app.post("/video_caption_start")
def video_caption_start():
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder is open."}), 400
    with caption_lock:
        if caption_status.get("running"):
            return jsonify({"ok": False, "error": "Captioning is already running."}), 409
    options = request.get_json(force=True, silent=True) or {}
    thread = threading.Thread(
        target=video_caption_worker,
        args=(current_folder, options),
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True})


@app.get("/video_caption_status")
def video_caption_status():
    data = caption_status_snapshot()
    data["ok"] = True
    return jsonify(data)


@app.post("/video_caption_interrupt")
def video_caption_interrupt():
    with caption_lock:
        caption_status["interrupt_requested"] = True
        if caption_status.get("running"):
            caption_status["status"] = "Interrupt requested..."
    return jsonify({"ok": True})


@app.get("/stats")
def stats():
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder is open."}), 400

    videos = list(pairs_cache)
    captions = 0
    audio = 0
    resolutions = {}
    bucket_base_counts = {}
    fps_counts = {}
    length_rows = []
    for p in videos:
        if str(p.get("caption") or "").strip():
            captions += 1
        if p.get("has_audio"):
            audio += 1
        duration = float(p.get("duration") or 0.0)
        frames = int(p.get("frames") or 0)
        length_rows.append({
            "name": p.get("name") or "",
            "seconds": duration,
            "frames": frames,
        })
        width = int(p.get('width') or 0)
        height = int(p.get('height') or 0)
        aspect = get_aspect_label(width, height)
        bucket_base = detect_bucket_base(width, height)
        base_suffix = f" • {bucket_base}" if bucket_base else ""
        res = f"{width}x{height} ({aspect}){base_suffix}"
        resolutions[res] = resolutions.get(res, 0) + 1
        if bucket_base:
            bucket_base_counts[bucket_base] = bucket_base_counts.get(bucket_base, 0) + 1
        fps = float(p.get("fps") or 0.0)
        if fps > 0:
            fps_label = f"{fps:.3f}".rstrip("0").rstrip(".")
            fps_counts[fps_label] = fps_counts.get(fps_label, 0) + 1

    resolution_rows = [
        {"resolution": key, "count": value}
        for key, value in sorted(resolutions.items(), key=lambda item: (-item[1], item[0]))
    ]
    bucket_base_rows = [
        {"base": key, "count": value}
        for key, value in sorted(bucket_base_counts.items())
    ]
    fps_rows = [
        {"fps": key, "count": value}
        for key, value in sorted(fps_counts.items(), key=lambda item: (-item[1], float(item[0]) if item[0] else 0.0))
    ]
    return jsonify({
        "ok": True,
        "total_videos": len(videos),
        "total_captions": captions,
        "audio_videos": audio,
        "mute_videos": max(0, len(videos) - audio),
        "lengths": length_rows,
        "resolutions": resolution_rows,
        "bucket_bases": bucket_base_rows,
        "fps_values": fps_rows,
    })


@app.post("/rename_all_pairs")
def rename_all_pairs():
    global message
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder is open."}), 400
    data = request.get_json(force=True) or {}
    prefix = str(data.get("prefix") or "").strip()
    if not prefix:
        return jsonify({"ok": False, "error": "Prefix is required."}), 400
    if re.search(r'[\\/:*?"<>|]', prefix):
        return jsonify({"ok": False, "error": "Prefix contains invalid filename characters."}), 400

    videos = [
        Path(current_folder) / p["name"]
        for p in pairs_cache
        if (Path(current_folder) / p["name"]).exists()
    ]
    if not videos:
        return jsonify({"ok": False, "error": "No videos found."}), 400

    temp_records = []
    try:
        for i, src_video in enumerate(videos):
            src_txt = src_video.with_suffix(".txt")
            temp_video = src_video.with_name(f"__renaming__{i:05d}{src_video.suffix}")
            temp_txt = src_video.with_name(f"__renaming__{i:05d}.txt")
            src_video.rename(temp_video)
            if src_txt.exists():
                src_txt.rename(temp_txt)
            temp_records.append((src_video, src_txt, temp_video, temp_txt))

        for i, (src_video, src_txt, temp_video, temp_txt) in enumerate(temp_records):
            final_video = src_video.with_name(f"{prefix}{i:05d}{src_video.suffix}")
            final_txt = final_video.with_suffix(".txt")
            if final_video.exists() and final_video not in [r[2] for r in temp_records]:
                raise RuntimeError(f"Target already exists: {final_video.name}")
            temp_video.rename(final_video)
            if temp_txt.exists():
                temp_txt.rename(final_txt)
    except Exception as e:
        for src_video, src_txt, temp_video, temp_txt in temp_records:
            if temp_video.exists() and not src_video.exists():
                temp_video.rename(src_video)
            if temp_txt.exists() and not src_txt.exists():
                temp_txt.rename(src_txt)
        refresh_pairs()
        return jsonify({"ok": False, "error": str(e)}), 500

    refresh_pairs()
    message = f"Renamed {len(videos)} video pair(s)."
    return jsonify({"ok": True, "renamed": len(videos)})


@app.post("/save_edit")
def save_edit():
    data = request.get_json(force=True)
    name = data["name"]
    crop_w = int(data["crop_w"])
    crop_h = int(data["crop_h"])
    start_frame = int(data["start_frame"])
    end_frame = int(data["end_frame"])
    include_end_frame = bool(data.get("include_end_frame", False))
    mute = bool(data.get("mute", False))
    flip_horizontal = bool(data.get("flip_horizontal", False))
    flip_vertical = bool(data.get("flip_vertical", False))
    try:
        rotate_degrees = int(data.get("rotate_degrees") or 0) % 360
    except Exception:
        rotate_degrees = 0
    if rotate_degrees not in {0, 90, 180, 270}:
        rotate_degrees = 0
    skip_refresh = bool(data.get("skip_refresh", False))
    final_compress = bool(data.get("final_compress", False))
    output_suffix = output_suffix_for_video(Path(name).suffix, data.get("output_format", "same"))
    final_fps = parse_optional_fps(data.get("final_fps")) if final_compress else None
    final_preset = str(data.get("final_preset") or FINAL_VIDEO_PRESET).strip().lower()
    if final_preset not in X264_PRESETS:
        final_preset = FINAL_VIDEO_PRESET
    try:
        final_crf = int(data.get("final_crf", FINAL_VIDEO_CRF))
    except Exception:
        final_crf = int(FINAL_VIDEO_CRF)
    final_crf = max(0, min(30, final_crf))

    crop_x_ratio = float(data.get("crop_x_ratio", 0.0))
    crop_y_ratio = float(data.get("crop_y_ratio", 0.0))
    crop_rect_w_ratio = float(data.get("crop_rect_w_ratio", 1.0))
    crop_rect_h_ratio = float(data.get("crop_rect_h_ratio", 1.0))
    has_crop = bool(data.get("has_crop", True))

    video_path = Path(current_folder) / name
    meta = probe_video(str(video_path))
    fps = meta["fps"] or 24.0
    has_audio = bool(meta.get("has_audio"))
    total_frames = max(1, int(meta.get("frames") or 0) or int(round((meta.get("duration") or 0.0) * fps)))
    start_frame = max(0, min(start_frame, total_frames - 1))
    if include_end_frame:
        end_frame = max(start_frame, min(end_frame, total_frames - 1))
        effective_end_frame = min(total_frames, end_frame + 1)
    else:
        end_frame = max(start_frame + 1, min(end_frame, total_frames))
        effective_end_frame = end_frame
    start_sec = max(0.0, start_frame / fps)
    end_sec = max(start_sec + (1.0 / fps), effective_end_frame / fps)

    src_w, src_h = int(meta["width"]), int(meta["height"])
    if src_w <= 0 or src_h <= 0:
        return jsonify({"ok": False, "error": "Invalid source dimensions."}), 400

    trim_changed = start_frame != 0 or effective_end_frame != total_frames
    transform_changed = flip_horizontal or flip_vertical or rotate_degrees != 0
    if mute and not has_crop and not trim_changed and not transform_changed:
        temp = video_path.with_name(video_path.stem + "_edited" + video_path.suffix)
        if temp.exists():
            temp.unlink(missing_ok=True)
        cmd = [
            FFMPEG_EXE, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-i", str(video_path),
            "-map", "0:v:0",
            "-c:v", "copy",
            "-an",
            str(temp),
        ]
        try:
            hidden_check_call(cmd)
        except Exception as e:
            if temp.exists():
                temp.unlink(missing_ok=True)
            return jsonify({"ok": False, "error": str(e)}), 500

        replace_file_with_retry(temp, video_path)
        pair = pair_for_video_path(video_path)
        if not skip_refresh:
            refresh_pairs()
        return jsonify({"ok": True, "stream_copy": True, "name": video_path.name, "pair": pair})

    target_path = video_path.with_suffix(output_suffix)
    if target_path != video_path and target_path.exists():
        return jsonify({"ok": False, "error": f"Target already exists: {target_path.name}"}), 400
    temp = video_path.with_name(video_path.stem + "_edited" + output_suffix)
    format_changed = target_path != video_path

    work_w, work_h = src_w, src_h
    if rotate_degrees in {90, 270}:
        work_w, work_h = src_h, src_w

    video_steps = [
        f"trim=start_frame={start_frame}:end_frame={effective_end_frame}",
        "setpts=PTS-STARTPTS",
    ]
    if flip_horizontal:
        video_steps.append("hflip")
    if flip_vertical:
        video_steps.append("vflip")
    if rotate_degrees == 90:
        video_steps.append("transpose=clock")
    elif rotate_degrees == 180:
        video_steps.extend(["hflip", "vflip"])
    elif rotate_degrees == 270:
        video_steps.append("transpose=cclock")
    if has_crop:
        target_ratio = crop_w / max(crop_h, 1)
        crop_x_ratio = max(0.0, min(crop_x_ratio, 1.0))
        crop_y_ratio = max(0.0, min(crop_y_ratio, 1.0))
        crop_rect_w_ratio = max(0.01, min(crop_rect_w_ratio, 1.0))
        crop_rect_h_ratio = max(0.01, min(crop_rect_h_ratio, 1.0))

        rect_w = max(2, int(round(work_w * crop_rect_w_ratio)))
        rect_h = max(2, int(round(work_h * crop_rect_h_ratio)))

        rect_ratio = rect_w / max(rect_h, 1)
        if rect_ratio > target_ratio:
            rect_w = max(2, int(round(rect_h * target_ratio)))
        else:
            rect_h = max(2, int(round(rect_w / target_ratio)))

        crop_x = int(round(work_w * crop_x_ratio))
        crop_y = int(round(work_h * crop_y_ratio))

        crop_x = max(0, min(crop_x, work_w - rect_w))
        crop_y = max(0, min(crop_y, work_h - rect_h))

        video_steps.extend([
            f"crop={rect_w}:{rect_h}:{crop_x}:{crop_y}",
            f"scale={crop_w}:{crop_h}",
        ])
    else:
        video_steps.append("scale=2*trunc(iw/2):2*trunc(ih/2)")
    video_steps.append("setsar=1")
    video_filter = "[0:v:0]" + ",".join(video_steps)
    if final_fps:
        video_filter += f",fps={final_fps:g}"
    video_filter += "[v]"
    filter_parts = [video_filter]
    if has_audio and not mute:
        filter_parts.append(
            f"[0:a:0]atrim=start={start_sec:.9f}:end={end_sec:.9f},asetpts=PTS-STARTPTS[a]"
        )
    cmd = [
        FFMPEG_EXE, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-i", str(video_path),
        "-filter_complex", ";".join(filter_parts),
        "-map", "[v]",
    ] + video_encode_args(final_compress=final_compress, preset=final_preset)
    if final_compress:
        crf_index = cmd.index("-crf") + 1
        cmd[crf_index] = str(final_crf)
    if has_audio and not mute:
        cmd += ["-map", "[a]"] + audio_encode_args(final_compress=final_compress)
    else:
        cmd += ["-an"]
    cmd += ["-metadata:s:v:0", "rotate=0"]
    if output_suffix.lower() in {".mp4", ".mov", ".m4v"}:
        cmd += ["-movflags", "+faststart"]
    cmd += [str(temp)]

    try:
        hidden_check_call(cmd)
    except Exception as e:
        if temp.exists():
            temp.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": str(e)}), 500

    if format_changed:
        replace_file_with_retry(temp, target_path)
        old_txt = video_path.with_suffix(".txt")
        new_txt = target_path.with_suffix(".txt")
        if old_txt.exists() and not new_txt.exists():
            old_txt.replace(new_txt)
        safe_unlink(video_path)
    else:
        replace_file_with_retry(temp, video_path)
    saved_path = target_path if format_changed else video_path
    pair = pair_for_video_path(saved_path)
    if not skip_refresh:
        refresh_pairs()
    return jsonify({"ok": True, "name": saved_path.name, "pair": pair})


@app.post("/slice_video")
def slice_video():
    global message
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder is open."}), 400
    data = request.get_json(force=True) or {}
    name = str(data.get("name") or "")
    segments = data.get("segments") or []
    output_suffix = output_suffix_for_video(Path(name).suffix, data.get("output_format", "mp4"))
    delete_source = bool(data.get("delete_source", False))
    include_end_frame = bool(data.get("include_end_frame", False))
    video_path = Path(current_folder) / name
    if not video_path.exists() or video_path.suffix.lower() not in VIDEO_EXTENSIONS:
        return jsonify({"ok": False, "error": "Video not found."}), 404
    meta = probe_video(str(video_path))
    fps = float(meta.get("fps") or 24.0) or 24.0
    has_audio = bool(meta.get("has_audio"))
    total_frames = max(1, int(meta.get("frames") or 0) or int(round((meta.get("duration") or 0.0) * fps)))
    kept = []
    for segment_index, segment in enumerate(segments):
        if not bool(segment.get("keep", True)):
            continue
        start_frame = int(segment.get("start") or 0)
        end_frame = int(segment.get("end") or 0)
        if include_end_frame:
            if segment_index > 0:
                start_frame += 1
            if end_frame < total_frames:
                end_frame += 1
        start_frame = max(0, min(start_frame, total_frames - 1))
        end_frame = max(start_frame + 1, min(end_frame, total_frames))
        if end_frame > start_frame:
            kept.append((start_frame, end_frame))
    if not kept:
        return jsonify({"ok": False, "error": "No kept slices selected."}), 400

    src_txt = video_path.with_suffix(".txt")
    source_caption = src_txt.read_text(encoding="utf-8", errors="replace") if src_txt.exists() else ""
    created = []
    try:
        for index, (start_frame, end_frame) in enumerate(kept, start=1):
            start_sec = max(0.0, start_frame / fps)
            end_sec = max(start_sec + (1.0 / fps), end_frame / fps)
            output = safe_stem_path(current_folder, f"{video_path.stem}_slice{index:03d}", output_suffix)
            filter_parts = [
                f"[0:v:0]trim=start_frame={start_frame}:end_frame={end_frame},setpts=PTS-STARTPTS[v]"
            ]
            if has_audio:
                filter_parts.append(
                    f"[0:a:0]atrim=start={start_sec:.9f}:end={end_sec:.9f},asetpts=PTS-STARTPTS[a]"
                )
            cmd = [
                FFMPEG_EXE, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
                "-i", str(video_path),
                "-filter_complex", ";".join(filter_parts),
                "-map", "[v]",
            ] + video_encode_args(final_compress=False)
            if output.suffix.lower() in {".mp4", ".mov", ".m4v"}:
                cmd += ["-movflags", "+faststart"]
            if has_audio:
                cmd += ["-map", "[a]"] + audio_encode_args(final_compress=False)
            else:
                cmd += ["-an"]
            cmd.append(str(output))
            hidden_check_call(cmd)
            output.with_suffix(".txt").write_text(source_caption, encoding="utf-8")
            created.append(output.name)
    except Exception as e:
        for filename in created:
            p = Path(current_folder) / filename
            p.unlink(missing_ok=True)
            p.with_suffix(".txt").unlink(missing_ok=True)
        return jsonify({"ok": False, "error": str(e)}), 500

    if delete_source:
        try:
            video_path.unlink(missing_ok=True)
            src_txt.unlink(missing_ok=True)
        except Exception as e:
            return jsonify({"ok": False, "error": f"Slices were created, but source delete failed: {e}"}), 500

    refresh_pairs()
    message = f"Created {len(created)} slice(s) from {video_path.name}."
    if delete_source:
        message += " Removed original."
    return jsonify({"ok": True, "created": created, "deleted_source": delete_source})


def set_convert_status(**updates):
    with convert_lock:
        convert_status.update(updates)


def convert_status_snapshot():
    with convert_lock:
        data = dict(convert_status)
        data["errors"] = list(convert_status.get("errors") or [])
    return data


def convert_fps_worker(folder, videos, duration_by_name, fps_by_name, target_fps, crf, make_backup):
    global message, convert_process
    converted = 0
    skipped = 0
    errors = []
    completed_duration = 0.0
    total_duration = sum(max(1.0, float(duration_by_name.get(p.name, 0.0) or 0.0)) for p in videos)
    backup_dir = folder / "BACKUP"
    if make_backup:
        backup_dir.mkdir(exist_ok=True)

    try:
        for index, video_path in enumerate(videos, start=1):
            source_fps = float(fps_by_name.get(video_path.name, 0.0) or 0.0)
            duration = max(0.0, float(duration_by_name.get(video_path.name, 0.0)))
            if duration <= 0:
                duration = 1.0
            with convert_lock:
                if convert_status.get("interrupt_requested"):
                    convert_status.update({
                        "interrupted": True,
                        "status": f"Interrupted at {converted} converted, {skipped} skipped.",
                    })
                    break
                convert_status.update({
                    "count": index - 1,
                    "current": video_path.name,
                    "percent": round((completed_duration / total_duration) * 100),
                    "status": f"Converting {video_path.name}",
                })

            output_suffix = ".mp4" if video_path.suffix.lower() == ".webm" else video_path.suffix
            output_path = video_path.with_suffix(output_suffix)

            if source_fps <= 0 or source_fps <= target_fps + 0.01:
                skipped += 1
                completed_duration += duration
                if source_fps <= 0:
                    skip_reason = "unknown fps"
                elif abs(source_fps - target_fps) < 0.01:
                    skip_reason = f"{source_fps:g} fps already matches"
                else:
                    skip_reason = f"{source_fps:g} fps is below target"
                set_convert_status(
                    count=index,
                    skipped=skipped,
                    percent=round((completed_duration / total_duration) * 100),
                    status=f"Skipped {video_path.name} ({skip_reason}).",
                )
                continue

            temp = video_path.with_name(video_path.stem + "_fps_tmp" + output_suffix)
            backup = backup_dir / video_path.name if make_backup else None
            video_filter = f"fps={target_fps:g}"
            if output_suffix.lower() == ".mp4":
                video_filter += ",format=yuv420p"
            cmd = [
                FFMPEG_EXE, "-hide_banner", "-nostdin", "-loglevel", "error",
                "-progress", "pipe:1", "-nostats", "-y",
                "-i", str(video_path),
                "-map", "0:v:0",
                "-map", "0:a?",
                "-vf", video_filter,
                "-c:v", "libx264",
                "-preset", "medium",
                "-crf", str(crf),
                "-c:a", "aac" if output_suffix.lower() == ".mp4" else "copy",
            ]
            if output_suffix.lower() == ".mp4":
                cmd += ["-movflags", "+faststart"]
            cmd.append(str(temp))
            try:
                proc = hidden_popen(cmd)
                with convert_lock:
                    convert_process = proc
                stdout_lines = []
                if proc.stdout is not None:
                    for line in proc.stdout:
                        stdout_lines.append(line)
                        key, _, value = line.strip().partition("=")
                        if key == "out_time_ms":
                            try:
                                elapsed = max(0.0, float(value) / 1000000.0)
                            except Exception:
                                elapsed = 0.0
                            percent = round(((completed_duration + min(elapsed, duration)) / total_duration) * 100)
                            set_convert_status(
                                count=index - 1,
                                current=video_path.name,
                                percent=max(0, min(99, percent)),
                                status=f"Converting {video_path.name}",
                            )
                stderr = proc.stderr.read() if proc.stderr is not None else ""
                proc.wait()
                with convert_lock:
                    interrupted = bool(convert_status.get("interrupt_requested"))
                    convert_process = None
                if interrupted:
                    if temp.exists():
                        safe_unlink(temp)
                    set_convert_status(
                        interrupted=True,
                        status=f"Interrupted at {converted} converted, {skipped} skipped.",
                        skipped=skipped,
                    )
                    break
                if proc.returncode != 0:
                    detail = (stderr or "".join(stdout_lines) or "").strip()
                    raise RuntimeError(detail or f"Command exited with code {proc.returncode}")
                validate_converted_video(temp)
                if backup is not None and not backup.exists():
                    shutil.copy2(video_path, backup)
                if output_path != video_path:
                    target_txt = output_path.with_suffix(".txt")
                    source_txt = video_path.with_suffix(".txt")
                    if output_path.exists():
                        safe_unlink(output_path)
                    replace_file_with_retry(temp, output_path)
                    if source_txt.exists() and target_txt != source_txt:
                        replace_file_with_retry(source_txt, target_txt)
                    if video_path.exists():
                        try:
                            safe_unlink(video_path)
                        except Exception:
                            pass
                else:
                    replace_file_with_retry(temp, video_path)
                converted += 1
                completed_duration += duration
                set_convert_status(
                    count=index,
                    converted=converted,
                    skipped=skipped,
                    percent=round((completed_duration / total_duration) * 100),
                    status=f"Converted {converted}/{len(videos)}.",
                )
            except Exception as e:
                with convert_lock:
                    convert_process = None
                if temp.exists():
                    try:
                        safe_unlink(temp)
                    except Exception:
                        pass
                completed_duration += duration
                errors.append(f"{video_path.name}: {e}")
                set_convert_status(
                    count=index,
                    converted=converted,
                    skipped=skipped,
                    percent=round((completed_duration / total_duration) * 100),
                    errors=list(errors),
                    status=f"Converted {converted}/{len(videos)}. {len(errors)} failed.",
                )

        if current_folder == str(folder):
            refresh_pairs()

        snapshot = convert_status_snapshot()
        if snapshot.get("interrupted"):
            message = f"Convert interrupted after {converted} converted, {skipped} skipped."
        elif errors:
            message = f"Converted FPS for {converted} video(s). Skipped {skipped}. {len(errors)} failed."
        else:
            message = f"Converted FPS for {converted} video(s) to {target_fps:g}. Skipped {skipped} already matching."
    finally:
        with convert_lock:
            convert_status.update({
                "running": False,
                "done": True,
                "converted": converted,
                "skipped": skipped,
                "percent": 100 if not convert_status.get("interrupted") else convert_status.get("percent", 0),
                "errors": list(errors),
            })
            if not convert_status.get("interrupted") and not errors:
                convert_status["count"] = len(videos)
                convert_status["status"] = f"Converted {converted}, skipped {skipped}."
            elif errors:
                convert_status["status"] = f"Converted {converted}, skipped {skipped}. {len(errors)} failed."
            convert_process = None


@app.post("/convert_fps")
def convert_fps():
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder is open."}), 400

    data = request.get_json(force=True, silent=True) or {}
    try:
        target_fps = float(data.get("fps"))
    except Exception:
        return jsonify({"ok": False, "error": "Invalid FPS value."}), 400
    try:
        crf = int(data.get("crf", 18))
    except Exception:
        return jsonify({"ok": False, "error": "Invalid quality value."}), 400
    make_backup = bool(data.get("backup", False))

    if not math.isfinite(target_fps) or target_fps <= 0 or target_fps > 240:
        return jsonify({"ok": False, "error": "FPS must be between 0 and 240."}), 400
    if crf < 0 or crf > 30:
        return jsonify({"ok": False, "error": "CRF must be between 0 and 30."}), 400

    folder = Path(current_folder)
    videos = [
        p for p in sorted(folder.iterdir())
        if p.is_file() and p.suffix.lower() in VIDEO_EXTENSIONS
    ]
    if not videos:
        return jsonify({"ok": False, "error": "No videos found."}), 400
    pair_durations = {str(p.get("name") or ""): float(p.get("duration") or 0.0) for p in pairs_cache}
    pair_fps = {str(p.get("name") or ""): float(p.get("fps") or 0.0) for p in pairs_cache}
    duration_by_name = {p.name: pair_durations.get(p.name, 0.0) for p in videos}
    fps_by_name = {p.name: pair_fps.get(p.name, 0.0) for p in videos}

    with convert_lock:
        if convert_status.get("running"):
            return jsonify({"ok": False, "error": "Convert is already running."}), 409
        convert_status.update({
            "running": True,
            "done": False,
            "interrupted": False,
            "interrupt_requested": False,
            "count": 0,
            "total": len(videos),
            "converted": 0,
            "skipped": 0,
            "percent": 0,
            "errors": [],
            "current": "",
            "status": "Starting convert...",
        })

    thread = threading.Thread(
        target=convert_fps_worker,
        args=(folder, videos, duration_by_name, fps_by_name, target_fps, crf, make_backup),
        daemon=True,
    )
    thread.start()
    return jsonify({"ok": True, "running": True, "total": len(videos)})


@app.get("/convert_fps_status")
def convert_fps_status():
    data = convert_status_snapshot()
    data["ok"] = True
    data["errors"] = data.get("errors", [])[:5]
    return jsonify(data)


@app.post("/convert_fps_interrupt")
def convert_fps_interrupt():
    global convert_process
    with convert_lock:
        if not convert_status.get("running"):
            return jsonify({"ok": True, "running": False})
        convert_status["interrupt_requested"] = True
        convert_status["status"] = "Interrupt requested..."
        proc = convert_process
    if proc is not None and proc.poll() is None:
        try:
            proc.terminate()
        except Exception:
            pass
    return jsonify({"ok": True, "interrupt_requested": True})


def open_browser():
    webbrowser.open("http://127.0.0.1:5002/")


TEMPLATE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>DataPrep</title>
<link rel="icon" href="/category_icon/btn_dataprep.svg" type="image/svg+xml">
<style>
:root{
  --bg:#050505;
  --panel:#141414;
  --panel2:#202020;
  --panel3:#262626;
  --text:#f1f5f9;
  --muted:#9ca3af;
  --faint:#737373;
  --accent:#3b82f6;
  --accent-soft:rgba(59,130,246,.18);
  --danger:#f87171;
  --danger-soft:rgba(248,113,113,.14);
  --ok:#22c55e;
  --border:#2a2a2a;
  --border-strong:#353535;
  --shadow:0 18px 44px rgba(0,0,0,.45);
  --radius:8px;
  --caption-text-height:110px;
  --video-card-width:430px;
  --video-display-height:320px;
  font-family: Inter, Segoe UI, Roboto, Arial, sans-serif;
}
*{box-sizing:border-box}
html{background:var(--bg)}
body{margin:0;padding-bottom:32px;background:var(--bg);color:var(--text);font-size:14px;line-height:1.4}
.top{
  position:sticky;top:0;z-index:100;
  background:#141414;
  border-bottom:1px solid var(--border);
  padding:8px 12px;
  box-shadow:0 1px 0 rgba(255,255,255,.03);
}
.mode-stack{
  position:absolute;
  top:10px;
  right:12px;
  display:grid;
  justify-items:end;
  gap:6px;
  z-index:1;
}
.mode-head{
  display:inline-flex;
  align-items:center;
  gap:7px;
}
.mode-label{
  color:var(--muted);
  font-size:12px;
  font-weight:750;
  text-transform:uppercase;
  letter-spacing:.04em;
  pointer-events:none;
}
.mode-icon{
  width:16px;
  height:16px;
  object-fit:contain;
}
.top > .row:first-of-type{padding-right:116px}
.row{display:flex;gap:6px;flex-wrap:wrap;align-items:center}
.top-controls{margin-top:2px}
.top form{display:inline-block;margin:0}
button,input,select,textarea{
  background:#1f1f1f;color:var(--text);border:1px solid var(--border-strong);
  border-radius:6px;padding:7px 10px;font:inherit;
}
button{
  cursor:pointer;
  min-height:32px;
  font-weight:650;
  transition:border-color .12s ease, background .12s ease, color .12s ease, transform .08s ease;
}
button:hover{border-color:#4b5563;background:#272727}
button:active{transform:translateY(1px)}
button:disabled{cursor:not-allowed;color:#666;background:#171717;border-color:#242424}
.toolbar-btn-content{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  gap:7px;
  white-space:nowrap;
}
.toolbar-btn-icon{
  width:16px;
  height:16px;
  object-fit:contain;
  image-rendering:auto;
  opacity:.95;
}
.range-wrap{
  display:inline-flex;
  align-items:center;
  gap:8px;
  background:#1b1b1b;
  border:1px solid var(--border);
  border-radius:8px;
  padding:5px 9px;
  color:var(--muted);
  font-size:12px;
}
.range-wrap label{
  color:var(--muted);
  font-size:12px;
}
.range-wrap input[type="range"]{
  width:150px;
  min-height:0;
  padding:0;
  border:0;
  background:transparent;
}
.range-wrap span{
  color:var(--text);
  min-width:28px;
  text-align:right;
  font-variant-numeric:tabular-nums;
}
input:focus,select:focus,textarea:focus,button:focus-visible{
  outline:none;
  border-color:var(--accent);
  box-shadow:0 0 0 2px rgba(59,130,246,.22);
}
.muted{color:var(--muted)}
.main{padding:12px 12px 44px;background:#050505;min-height:calc(100vh - 65px)}
.grid{
  display:grid;
  grid-template-columns: repeat(auto-fill,minmax(min(100%, var(--video-card-width, 430px)),var(--video-card-width, 430px)));
  gap:12px;
  align-items:start;
}
.statusbar{
  position:fixed;
  left:0;
  right:0;
  bottom:0;
  z-index:150;
  min-height:28px;
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:16px;
  padding:5px 12px;
  background:#141414;
  border-top:1px solid var(--border);
  color:var(--muted);
  font-size:12px;
  font-weight:650;
  box-shadow:0 -1px 0 rgba(255,255,255,.03);
}
.statusbar-folder{
  flex:1 1 auto;
  min-width:0;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}
.statusbar-message{
  flex:0 1 45%;
  min-width:80px;
  overflow:hidden;
  text-align:right;
  text-overflow:ellipsis;
  white-space:nowrap;
  color:var(--text);
}
.card{
  background:#141414;border:1px solid var(--border);border-radius:8px;
  box-shadow:none;padding:0;overflow:hidden;display:flex;flex-direction:column;gap:10px;
}
.card.selected{border-color:var(--ok);box-shadow:0 0 0 1px rgba(34,197,94,.45)}
.card-head{
  display:flex;justify-content:space-between;align-items:center;gap:8px;
  padding:10px 12px;background:var(--panel);border-bottom:1px solid #202020;
  cursor:default;
  position:relative;
}
.name{
  font-weight:750;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;
  padding:4px 2px;border-radius:6px;color:#e5e7eb;
}
.card-head-actions{
  margin-left:auto;
  display:inline-flex;
  align-items:center;
  gap:4px;
  flex:0 0 auto;
}
.card-head-action{
  position:relative;
  width:16px;
  height:16px;
  min-width:16px;
  min-height:16px;
  padding:0;
  border:1px solid #fff;
  border-radius:999px;
  font-size:0;
  line-height:1;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  box-shadow:0 0 0 1px rgba(0,0,0,.45);
}
.card-head-action::before,
.card-head-action::after{
  content:"";
  position:absolute;
  left:3px;
  top:6px;
  width:8px;
  height:2px;
  background:#fff;
  border-radius:1px;
}
.card-head-action.clone-btn{
  background:#248a2b;
}
.card-head-action.clone-btn::after{
  transform:rotate(90deg);
}
.card-head-action.delete-btn{
  background:#f01818;
}
.card-head-action.delete-btn::before{
  transform:rotate(45deg);
}
.card-head-action.delete-btn::after{
  transform:rotate(-45deg);
}
.name.editing{background:#1f1f1f;outline:1px solid var(--accent);padding-inline:6px}
video{
  width:100%;
  height:100%;
  background:#000;
  border-radius:0;
  display:block;
  object-fit:contain;
}
.video-stage{
  position:relative;
  width:100%;
  height:var(--video-display-height);
  display:inline-block;
  overflow:hidden;
  border-radius:0;
  border:1px solid #242424;
  background:#000;
  cursor:crosshair;
}
.media-zoom-row{
  display:flex;
  justify-content:flex-end;
  align-items:center;
  gap:3px;
  margin:0 10px -6px;
}
.media-zoom-btn{
  min-width:22px;
  min-height:18px;
  height:18px;
  padding:0 4px;
  border-radius:4px;
  font-size:10px;
  line-height:1;
}
.zoom-readout{
  position:absolute;
  right:0;
  top:0;
  z-index:20;
  pointer-events:none;
  opacity:0;
  transition:opacity .18s ease;
  padding:2px 7px;
  border-radius:0 0 0 4px;
  background:rgba(0,0,0,.42);
  color:#fff;
  font-size:11px;
  font-weight:750;
  box-sizing:border-box;
  display:inline-block;
  line-height:1.25;
  min-width:max-content;
  white-space:nowrap;
}
.zoom-readout.show{opacity:1}
.video-loading{
  position:absolute;
  inset:0;
  z-index:18;
  display:none;
  align-items:center;
  justify-content:center;
  pointer-events:none;
  color:#e5e7eb;
  font-size:12px;
  font-weight:750;
  letter-spacing:.02em;
  text-transform:lowercase;
  background:rgba(0,0,0,.34);
}
.video-loading.show{display:flex}
.video-stage video{
  position:absolute;
  object-fit:contain;
}
.video-stage.panning,
.video-stage.panning *{
  cursor:grabbing !important;
}
.crop-overlay{
  position:absolute;
  border:2px solid var(--ok);
  box-sizing:border-box;
  box-shadow:0 0 0 9999px rgba(0,0,0,.42);
  border-radius:4px;
  cursor:default;
  user-select:none;
  display:none;
}
.crop-overlay::after{
  content:attr(data-label);
  position:absolute;
  left:8px;
  top:8px;
  background:rgba(20,20,20,.92);
  color:var(--text);
  border:1px solid var(--border);
  border-radius:999px;
  font-size:11px;
  line-height:1;
  padding:5px 8px;
}
.crop-handle{
  position:absolute;
  width:12px;height:12px;
  border-radius:50%;
  background:var(--accent);
  border:2px solid #e5e7eb;
  box-shadow:0 1px 8px rgba(0,0,0,.35);
}
.crop-handle.nw{left:-7px;top:-7px;cursor:nwse-resize}
.crop-handle.ne{right:-7px;top:-7px;cursor:nesw-resize}
.crop-handle.sw{left:-7px;bottom:-7px;cursor:nesw-resize}
.crop-handle.se{right:-7px;bottom:-7px;cursor:nwse-resize}
.frame-tools{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.frame-readout{min-width:96px;color:var(--muted);font-size:12px}
.seek-bar{width:100%;margin-top:6px;margin-bottom:0;padding:0;background:transparent;border:none;position:relative;z-index:5}
.seek-bar{width:calc(100% - 20px);box-sizing:border-box}
.play-toggle-btn{min-width:34px;text-align:center}
.controls{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
.video-stage,.seek-bar,.controls,.trim-editor,.meta,.caption,.caption-stats,hr{margin-left:10px;margin-right:10px}
.video-stage{width:calc(100% - 20px)}
.caption{width:calc(100% - 20px);box-sizing:border-box}
.caption-stats{margin-left:10px;margin-right:10px;margin-bottom:10px}
.playback-row{position:relative}
.trim-editor{
  display:flex;
  flex-direction:column;
  gap:8px;
}
.trim-head{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:12px;
  color:var(--muted);
  font-size:12px;
}
.trim-readout{
  color:var(--text);
  font-weight:650;
}
.trim-slider{
  position:relative;
  height:32px;
  display:flex;
  align-items:center;
}
.trim-track,
.trim-range-fill{
  position:absolute;
  left:0;
  right:0;
  height:5px;
  border-radius:999px;
}
.trim-track{
  background:#2a2a2a;
}
.trim-range-fill{
  background:var(--accent);
  box-shadow:0 0 0 1px rgba(96,165,250,.18);
  pointer-events:none;
}
.trim-range{
  position:absolute;
  left:0;
  width:100%;
  height:32px;
  margin:0;
  padding:0;
  background:transparent;
  border:0;
  pointer-events:none;
  appearance:none;
  -webkit-appearance:none;
}
.trim-start{z-index:2}
.trim-end{z-index:3}
.trim-range::-webkit-slider-runnable-track{
  height:5px;
  background:transparent;
}
.trim-range::-webkit-slider-thumb{
  -webkit-appearance:none;
  pointer-events:auto;
  width:16px;
  height:16px;
  margin-top:-5px;
  border-radius:50%;
  background:#dbeafe;
  border:3px solid var(--accent);
  box-shadow:0 2px 8px rgba(0,0,0,.45);
}
.trim-range::-moz-range-track{
  height:5px;
  background:transparent;
}
.trim-range::-moz-range-thumb{
  pointer-events:auto;
  width:16px;
  height:16px;
  border-radius:50%;
  background:#dbeafe;
  border:3px solid var(--accent);
  box-shadow:0 2px 8px rgba(0,0,0,.45);
}
.trim-endpoints{
  display:flex;
  justify-content:space-between;
  gap:12px;
  color:var(--faint);
  font-size:12px;
}
.trim-count{
  color:var(--muted);
  text-align:center;
  flex:1;
}
.icon-only-btn{
  display:inline-flex;
  align-items:center;
  justify-content:center;
  width:32px;
  height:32px;
  padding:0;
  position:relative;
  flex:0 0 32px;
}
.icon-btn{
  width:31px;
  height:31px;
  min-width:31px;
  min-height:31px;
  border-radius:7px;
  padding:0;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  background:#202020;
  border-color:#343434;
}
.icon-btn:hover{background:#292929;border-color:#4b4b4b}
.card-btn-icon{
  width:16px;
  height:16px;
  object-fit:contain;
  image-rendering:auto;
  pointer-events:none;
}
.media-icon{
  position:relative;
  display:inline-block;
  width:16px;
  height:16px;
}
.media-play::before,
.media-pause::before,
.media-pause::after,
.media-stop::before{
  content:"";
  position:absolute;
}
.media-play::before{
  left:4px;
  top:2px;
  width:0;
  height:0;
  border-top:6px solid transparent;
  border-bottom:6px solid transparent;
  border-left:9px solid var(--text);
}
.media-pause::before{
  left:3px;
  top:2px;
  width:4px;
  height:12px;
  background:var(--text);
  border-radius:1px;
}
.media-pause::after{
  right:3px;
  top:2px;
  width:4px;
  height:12px;
  background:var(--text);
  border-radius:1px;
}
.media-stop::before{
  left:2px;
  top:2px;
  width:12px;
  height:12px;
  background:var(--text);
  border-radius:2px;
}
.small{padding:5px 8px;font-size:12px;min-height:28px}
.transform-btn.active{
  border-color:var(--ok);
  box-shadow:0 0 0 1px rgba(34,197,94,.32) inset;
}
.caption{
  width:calc(100% - 20px);height:var(--caption-text-height);min-height:60px;resize:none;
  box-sizing:border-box;
  background:#0a0a0a;
  color:#e5e7eb;
  border-color:#252525;
  line-height:1.4;
}
.caption.unsaved-caption{border-color:rgba(248,113,113,.9);box-shadow:0 0 0 1px rgba(248,113,113,.25) inset;}
#saveAllBtn.has-unsaved{
  color:#fff;
  background:#b4233c;
  border-color:#ef5f76;
}
#saveAllBtn.has-unsaved:hover{
  color:#fff;
  background:#c92b47;
  border-color:#ff7890;
}
.caption-stats{
  margin-top:-6px;
  display:flex;
  justify-content:flex-end;
  gap:10px;
  color:var(--muted);
  font-size:11px;
  font-weight:650;
  font-variant-numeric:tabular-nums;
}
.meta{display:flex;gap:10px;flex-wrap:wrap;color:var(--muted);font-size:13px}
.group{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
label{font-size:13px;color:var(--muted)}
hr{border:none;border-top:1px solid var(--border);margin:0}
.notice{
  padding:14px 16px;color:var(--muted);
  background:#141414;border:1px solid var(--border);border-radius:8px;
}
.main{
  position:relative;
}
.main-loading-videos{
  display:none;
  min-height:220px;
  place-items:center;
  color:#e5e7eb;
  font-size:15px;
  font-weight:800;
  letter-spacing:.02em;
}
.main.loading-videos > :not(.main-loading-videos){
  visibility:hidden;
}
.main.loading-videos .main-loading-videos{
  display:grid;
}
.drop-paste-overlay {
  position: fixed;
  inset: 0;
  z-index: 10000;
  display: none;
  align-items: center;
  justify-content: center;
  pointer-events: none;
  background: rgba(5,5,5,.55);
  color: var(--text);
  font-size: 22px;
  font-weight: 800;
  letter-spacing: .02em;
  text-shadow: 0 2px 18px rgba(0,0,0,.7);
}
.drop-paste-overlay.show {
  display: flex;
}
.modal{
  position:fixed;inset:0;background:rgba(0,0,0,.72);display:none;
  align-items:center;justify-content:center;z-index:200;
  padding:18px;
}
.modal.open{display:flex}
.modal-card{
  width:min(720px,calc(100vw - 24px));background:#171717;border:1px solid #303030;
  border-radius:6px;box-shadow:0 18px 36px rgba(0,0,0,.30);overflow:hidden;
}
.caption-modal-card{
  width:min(820px,calc(100vw - 24px));
}
.modal-head{
  display:flex;
  justify-content:space-between;
  align-items:center;
  gap:12px;
  padding:10px 12px;
  background:#252525;
  border-bottom:1px solid #202020;
  cursor:move;
  user-select:none;
}
.modal-head h3{
  margin:0;
  color:#f5f5f5;
  font-size:16px;
  font-weight:800;
  min-width:0;
  overflow:hidden;
  text-overflow:ellipsis;
  white-space:nowrap;
}
.modal-body{padding:12px}
.slice-window-actions{
  margin-left:auto;
  display:inline-flex;
  gap:6px;
  flex:0 0 auto;
}
.slice-window-actions button{
  width:28px;
  height:28px;
  min-width:28px;
  padding:0;
  border-radius:999px;
  display:grid;
  place-items:center;
  line-height:1;
}
.slice-maximize-btn{
  position:relative;
  background:transparent;
  border-color:transparent;
}
.slice-maximize-btn::before{
  content:"";
  width:11px;
  height:11px;
  border:2px solid currentColor;
  box-sizing:border-box;
}
.slice-maximize-btn:hover{
  background:#303030;
  border-color:#404040;
  color:#fff;
}
.modal-close-btn{
  width:28px;
  height:28px;
  display:grid;
  place-items:center;
  padding:0;
  line-height:1;
  background:transparent;
  border-color:transparent;
  color:#d4d4d8;
  font-size:18px;
  font-weight:800;
}
.modal-close-btn:hover{
  background:#303030;
  color:#fff;
}
.two{display:grid;grid-template-columns:1fr 1fr;gap:10px}
.joy-grid{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(220px,1fr));
  gap:8px;
}
.caption-settings{
  display:grid;
  gap:10px;
}
.caption-section{
  background:#151515;
  border:1px solid #303030;
  border-radius:8px;
  padding:10px;
}
.caption-section-title{
  margin:0 0 9px;
  color:#f5f5f5;
  font-size:13px;
  font-weight:800;
  text-transform:uppercase;
  letter-spacing:.04em;
}
.caption-section-grid{
  display:grid;
  grid-template-columns:repeat(auto-fit,minmax(190px,1fr));
  gap:9px;
}
.caption-section-grid label{
  display:grid;
  gap:5px;
}
.caption-section-grid input,
.caption-section-grid select,
.caption-section-grid textarea{
  width:100%;
  resize:vertical;
}
.caption-full{
  grid-column:1 / -1;
}
.check-row{
  display:flex !important;
  align-items:center;
  gap:8px;
  color:#b5b5b5;
}
.check-row input{
  width:auto;
}
.caption-field-disabled{
  opacity:.55;
}
.tool-box{
  background:#151515;
  border:1px solid #303030;
  border-radius:8px;
  padding:9px;
}
.tool-box h3{
  margin:0 0 6px;
  color:#f5f5f5;
  font-size:14px;
  font-weight:800;
}
.tool-box form{
  display:flex;
  flex-wrap:wrap;
  gap:8px;
}
.joy-grid input,
.joy-grid select,
.joy-grid textarea,
.tool-box input,
.tool-box select,
.tool-box textarea{
  font-size:13px;
  color:var(--text);
}
.tool-box label{
  color:#b5b5b5;
}
.logbox{
  white-space:pre-wrap;
  background:#050505;
  border:1px solid #282828;
  border-radius:8px;
  padding:8px;
  min-height:110px;
  max-height:320px;
  overflow:auto;
  margin-top:10px;
  font-family:Consolas, monospace;
  font-size:12px;
  color:#e7e7e7;
}
.regex-help-icon{
  width:18px;
  height:18px;
  border:1px solid var(--border);
  border-radius:999px;
  padding:0;
  display:inline-flex;
  align-items:center;
  justify-content:center;
  background:#1f1f1f;
  color:var(--muted);
  font-size:12px;
  font-weight:700;
  line-height:1;
  cursor:help;
}
.regex-help-icon:hover{
  border-color:var(--accent);
  color:var(--text);
  background:#262626;
}
.regex-help-tooltip{
  position:fixed;
  z-index:20000;
  display:none;
  max-width:min(320px,calc(100vw - 24px));
  padding:8px 10px;
  border:1px solid var(--border);
  border-radius:8px;
  background:#151515;
  color:var(--text);
  box-shadow:0 10px 26px rgba(0,0,0,.5);
  font-size:12px;
  line-height:1.45;
  white-space:pre-line;
  pointer-events:none;
}
.regex-help-tooltip.open{display:block}
.modal-actions{
  display:flex;
  justify-content:flex-end;
  align-items:center;
  gap:8px;
  margin-top:12px;
}
.modal-status{
  margin-right:auto;
  color:var(--muted);
  font-size:13px;
}
.modal-progress{
  grid-column:1 / -1;
  display:grid;
  grid-template-columns:minmax(0,1fr) auto;
  gap:6px 10px;
  align-items:center;
  margin-top:10px;
}
.modal-progress-track{
  grid-column:1 / -1;
  height:8px;
  overflow:hidden;
  background:#0b0b0b;
  border:1px solid #2d2d2d;
  border-radius:999px;
}
.modal-progress-fill{
  width:0%;
  height:100%;
  background:var(--accent);
  border-radius:inherit;
  transition:width .18s ease;
}
.modal-progress-label,
.modal-progress-percent{
  color:var(--muted);
  font-size:12px;
  font-weight:650;
}
.modal-progress-percent{
  color:#d4d4d8;
  font-variant-numeric:tabular-nums;
}
#convertInterruptBtn{
  color:#ff9aae;
  border-color:rgba(248,113,113,.42);
}
.app-dialog{width:min(460px,calc(100vw - 24px))}
.slice-modal-card{
  width:min(960px,calc(100vw - 24px));
  height:min(760px,calc(100vh - 24px));
  min-width:min(520px,calc(100vw - 24px));
  min-height:min(420px,calc(100vh - 24px));
  max-height:calc(100vh - 24px);
  display:flex;
  flex-direction:column;
  position:relative;
}
.slice-modal-card.maximized{
  position:fixed !important;
  left:12px !important;
  top:12px !important;
  width:calc(100vw - 24px) !important;
  height:calc(100vh - 24px) !important;
  max-width:none !important;
  max-height:none !important;
  margin:0 !important;
}
.slice-modal-card .modal-body{
  flex:1 1 auto;
  overflow:auto;
}
.slice-resize-handle{
  position:absolute;
  right:0;
  bottom:0;
  width:18px;
  height:18px;
  cursor:nwse-resize;
  z-index:2;
  background:
    linear-gradient(135deg, transparent 0 50%, rgba(255,255,255,.10) 50% 58%, transparent 58% 100%),
    linear-gradient(135deg, transparent 0 64%, rgba(255,255,255,.16) 64% 72%, transparent 72% 100%);
}
.slice-layout{
  display:grid;
  grid-template-rows:minmax(160px,1fr) auto auto auto auto;
  gap:10px;
  height:100%;
  min-height:0;
}
.slice-video-wrap{
  background:#050505;
  border:1px solid #2a2a2a;
  border-radius:8px;
  overflow:hidden;
  min-height:160px;
}
.slice-video{
  width:100%;
  height:100%;
  display:block;
  background:#000;
  object-fit:contain;
}
.slice-toolbar{
  display:flex;
  align-items:center;
  gap:8px;
  flex-wrap:wrap;
}
.slice-toolbar .modal-status{
  margin-right:0;
}
.slice-timeline{
  position:relative;
  height:46px;
  border:1px solid #303030;
  border-radius:8px;
  background:#0a0a0a;
  overflow:hidden;
  cursor:pointer;
}
.slice-segment{
  position:absolute;
  top:0;
  bottom:0;
  border-right:1px solid rgba(255,255,255,.12);
  background:rgba(34,197,94,.32);
}
.slice-segment.remove{
  background:rgba(248,113,113,.24);
}
.slice-segment span{
  position:absolute;
  left:6px;
  top:50%;
  transform:translateY(-50%);
  color:#f8fafc;
  font-size:11px;
  font-weight:800;
  text-shadow:0 1px 2px rgba(0,0,0,.9);
  pointer-events:none;
}
.slice-cut{
  position:absolute;
  top:0;
  bottom:0;
  width:2px;
  background:#fbbf24;
  box-shadow:0 0 0 1px rgba(0,0,0,.55);
  transform:translateX(-1px);
}
.slice-playhead{
  position:absolute;
  top:0;
  bottom:0;
  width:2px;
  background:#60a5fa;
  transform:translateX(-1px);
  pointer-events:none;
}
.slice-list{
  display:grid;
  gap:4px;
  max-height:170px;
  overflow:auto;
  border:1px solid #303030;
  border-radius:8px;
  padding:6px;
  background:#101010;
}
.slice-row{
  display:grid;
  grid-template-columns:auto 1fr auto;
  align-items:center;
  gap:8px;
  padding:5px 6px;
  border-radius:6px;
  background:#171717;
  color:#d4d4d8;
  font-size:12px;
}
.slice-row button{
  min-height:24px;
  padding:3px 8px;
  font-size:12px;
}
.app-dialog-message{
  white-space:pre-wrap;
  line-height:1.45;
  padding:10px 12px;
  background:transparent;
  border:0;
}
::-webkit-scrollbar{width:10px;height:10px}
::-webkit-scrollbar-track{background:#080808}
::-webkit-scrollbar-thumb{background:#2f2f2f;border-radius:999px;border:2px solid #080808}
::-webkit-scrollbar-thumb:hover{background:#3f3f3f}
::selection{background:rgba(59,130,246,.35)}
@media (max-width: 760px){
  .grid{grid-template-columns:1fr}
  .top{position:relative}
  .mode-stack{position:static;margin-bottom:6px;justify-items:end}
  .top > .row:first-of-type{padding-right:0}
  .two{grid-template-columns:1fr}
}
</style>
</head>
<body>
<div class="drop-paste-overlay" id="dropPasteOverlay">Drop videos to add them</div>
<div class="top">
  <div class="mode-stack">
    <div class="mode-head"><img class="mode-icon" src="/category_icon/btn_dataprep.svg" alt=""><div class="mode-label">DataPrep - Video</div></div>
  </div>
  <div class="row" style="margin-bottom:8px;">
    <form method="post" action="/open_folder" id="openFolderForm"><button type="submit" title="Open a video folder"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_open_folder.png" alt="">Open</span></button></form>
    <form method="post" action="/add_files"><button type="submit" title="Add video files"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_add_files.png" alt="">Add</span></button></form>
    <button id="openFolderInExplorerBtn" type="button" title="Show the opened folder in File Explorer"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_open_file_manager.png" alt="">Show</span></button>
    <button id="refreshFolderBtn" type="button" title="Refresh the opened folder"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_refresh.png" alt="">Refresh</span></button>
    <button id="settingsBtn" type="button" title="Video settings"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_settings.png" alt="">Settings</span></button>
    <form method="post" action="/backup" class="backup-form"><button type="submit" title="Back up video and caption pairs"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_backup.png" alt="">Backup</span></button></form>
    <button id="captionStubBtn" type="button" title="Generate captions"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_caption.png" alt="">Caption</span></button>
    <button id="textToolsBtn" type="button" title="Batch edit caption text"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_text_tools.png" alt="">Text</span></button>
    <button type="button" id="openStatsModalBtn" title="Show dataset statistics"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_statistics.png" alt="">Stats</span></button>
    <button type="button" id="autoCropAllBtn" title="Auto crop every video"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_auto_crop_all.png" alt="">Auto crop</span></button>
    <button type="button" id="saveAllBtn" title="Save all changed video and caption pairs"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_save_all.png" alt="">Save</span></button>
    <button type="button" id="resetAllBtn" title="Reset unsaved edits"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_reset_all.png" alt="">Reset</span></button>
    <button type="button" id="renameAllBtn" title="Rename all video and caption pairs"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_rename_all.png" alt="">Rename</span></button>
    <form method="post" action="/close_folder"><button type="submit" title="Close Folder"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_close_folder.png" alt="">Close</span></button></form>
  </div>
  <div class="row top-controls">
    <div class="range-wrap">
      <label for="textHeightSlider">Text height</label>
      <input type="range" id="textHeightSlider" min="60" max="360" step="10" value="110">
      <span id="textHeightValue">110</span>px
    </div>
    <div class="range-wrap">
      <label for="videoSizeSlider">Video size</label>
      <input type="range" id="videoSizeSlider" min="220" max="720" step="10" value="320">
      <span id="videoSizeValue">320</span>px
    </div>
    <div class="range-wrap">
      <label for="previewVolumeSlider">Volume</label>
      <input type="range" id="previewVolumeSlider" min="0" max="100" step="1" value="100">
      <span id="previewVolumeValue">100</span>%
    </div>
    <div class="range-wrap">
      <span>Crop base</span>
      <label><input type="radio" name="crop_base" value="512"> 512</label>
      <label><input type="radio" name="crop_base" value="768"> 768</label>
      <label><input type="radio" name="crop_base" value="1024" checked> 1024</label>
      <label><input type="radio" name="crop_base" value="1280"> 1280</label>
      <label><input type="radio" name="crop_base" value="1536"> 1536</label>
    </div>
  </div>
</div>

<div class="main" id="mainContent">
  <div class="main-loading-videos">Loading videos...</div>
  {% if not pairs %}
    <div class="notice">No folder is open or the folder contains no supported videos.</div>
  {% else %}
    <div class="grid">
      {% for pair in pairs %}
      <div class="card" data-name="{{ pair.name }}" data-fps="{{ "%.6f"|format(pair.fps) }}" data-frames="{{ pair.frames }}" data-width="{{ pair.width }}" data-height="{{ pair.height }}" data-video-version="{{ pair.cache_buster }}">
        <div class="card-head">
          <div class="name" data-name="{{ pair.name }}">{{ pair.name }}</div>
          <div class="card-head-actions">
            <button type="button" class="card-head-action delete-btn" title="Delete" aria-label="Delete">×</button>
            <button type="button" class="card-head-action clone-btn" title="Clone" aria-label="Clone">+</button>
          </div>
        </div>

        <div class="media-zoom-row">
          <button type="button" class="media-zoom-btn zoom-in-btn" title="Zoom in">+</button>
          <button type="button" class="media-zoom-btn zoom-default-btn" title="Fit zoom">fit</button>
          <button type="button" class="media-zoom-btn zoom-actual-btn" title="100% zoom">100%</button>
          <button type="button" class="media-zoom-btn zoom-out-btn" title="Zoom out">-</button>
        </div>
        <div class="video-stage" data-zoom="1">
          <video src="/video/{{ pair.name | urlencode }}?v={{ pair.cache_buster | urlencode }}" preload="metadata"></video>
          <div class="video-loading">loading</div>
          <div class="crop-overlay" data-label="">
            <div class="crop-handle nw"></div>
            <div class="crop-handle ne"></div>
            <div class="crop-handle sw"></div>
            <div class="crop-handle se"></div>
          </div>
          <div class="zoom-readout">100%</div>
        </div>
        <input class="seek-bar" type="range" min="0" max="{{ pair.frames }}" step="1" value="0">

        <div class="controls playback-row">
          <button class="small play-toggle-btn icon-only-btn" title="Play/Pause" aria-label="Play/Pause">
            <span class="media-icon media-play" aria-hidden="true"></span>
          </button>
          <button class="small stop-btn icon-only-btn" title="Stop" aria-label="Stop">
            <span class="media-icon media-stop" aria-hidden="true"></span>
          </button>
          <button class="small slice-btn icon-only-btn" title="Slice" aria-label="Slice">
            <img class="card-btn-icon" src="/category_icon/btn_card_slice.png" alt="">
          </button>
          <button class="small flip-h-btn icon-only-btn transform-btn" title="Flip horizontally" aria-label="Flip horizontally">
            <img class="card-btn-icon" src="/category_icon/btn_card_flip_h.png" alt="">
          </button>
          <button class="small flip-v-btn icon-only-btn transform-btn" title="Flip vertically" aria-label="Flip vertically">
            <img class="card-btn-icon" src="/category_icon/btn_card_flip_v.png" alt="">
          </button>
          <button class="small rotate-90-btn transform-btn" title="Rotate 90 degrees" aria-label="Rotate 90 degrees">90°</button>
          <button class="small save-combined-btn icon-only-btn" title="Save video and caption" aria-label="Save video and caption">
            <img class="card-btn-icon" src="/category_icon/btn_card_save.png" alt="">
          </button>
        </div>

        <div class="meta">
          <span>{{ pair.width }}x{{ pair.height }}</span>
          <span>{{ "%.2f"|format(pair.duration) }} s</span>
          <span>{{ "%.3f"|format(pair.fps) }} fps</span>
          <span>{{ pair.frames }} frames</span>
          <span>{% if pair.has_audio %}audio{% else %}silent{% endif %}</span>
        </div>

        <hr>

        <div class="trim-editor">
          <div class="trim-head">
            <span class="frame-readout">Frame: 0</span>
            <span class="trim-readout">Trim: 0 - {{ pair.frames }}</span>
          </div>
          <div class="trim-slider">
            <div class="trim-track"></div>
            <div class="trim-range-fill"></div>
            <input class="trim-range trim-start start-frame" type="range" min="0" max="{{ pair.frames }}" step="1" value="0" aria-label="Trim start frame">
            <input class="trim-range trim-end end-frame" type="range" min="0" max="{{ pair.frames }}" step="1" value="{{ pair.frames }}" aria-label="Trim end frame">
          </div>
          <div class="trim-endpoints">
            <span>0</span>
            <span class="trim-count">{{ pair.frames }} frames selected ({{ "%.2f"|format(pair.duration) }} s)</span>
            <span>{{ pair.frames }}</span>
          </div>
        </div>

        <div class="controls">
          <label><input type="checkbox" class="mute-export"> Export muted</label>
        </div>

        <textarea class="caption">{{ pair.caption }}</textarea>
        <div class="caption-stats">
          <span class="caption-char-count">0 chars</span>
          <span class="caption-token-count">0 tokens</span>
        </div>
      </div>
      {% endfor %}
    </div>
  {% endif %}
</div>

<div class="statusbar">
  <span class="statusbar-folder">
    {% if current_folder %}Opened folder: {{ current_folder }} - {{ pairs|length }} video{% if pairs|length != 1 %}s{% endif %}.{% else %}No folder opened{% endif %}
  </span>
  <span class="statusbar-message">{% if message %}{{ message }}{% endif %}</span>
</div>

<div id="textModal" class="modal">
  <div class="modal-card">
    <div class="modal-head">
      <h3 id="toolsModalTitle">Text tools</h3>
      <button type="button" class="modal-close-btn" id="closeTextModalBtn" aria-label="Close">×</button>
    </div>
    <div class="modal-body">
      <div class="joy-grid">
        <div class="tool-box" style="margin:0;">
          <h3>Replace in captions</h3>
          <form method="POST" action="/replace_all" id="replaceForm">
            <input type="text" name="match_string" placeholder="Search string or regex" required id="sr_match">
            <input type="text" name="replace_with" placeholder="Replace with" id="sr_replace">
            <label style="display:flex; align-items:center; gap:6px;">
              <input type="checkbox" name="use_regex" value="1" id="sr_use_regex">
              Use regex
              <span class="regex-help-icon" role="img" aria-label="Regex help" data-tooltip="Examples of Regexes:&#10;add string to start:  \A&#10;add string to end:  \Z&#10;target last tag:  ,[^,]*$&#10;replace all: .*">?</span>
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
            <button type="submit">Add</button>
          </form>
        </div>
      </div>

      <div id="toolsResult" class="logbox" style="min-height:80px; max-height:180px;"></div>
    </div>
  </div>
</div>

<div id="captionModal" class="modal">
  <div class="modal-card caption-modal-card">
    <div class="modal-head">
      <h3>Caption</h3>
      <button type="button" class="modal-close-btn" id="closeCaptionModalBtn" aria-label="Close">×</button>
    </div>
    <div class="modal-body">
      <div class="caption-settings">
        <section class="caption-section">
          <h4 class="caption-section-title">Backend</h4>
          <div class="caption-section-grid">
            <label>
              Caption backend
              <select id="video_caption_backend">
                <option value="qwen3_vl">Qwen3-VL visual caption</option>
                <option value="external_api">External API visual caption</option>
                <option value="whisperx">WhisperX audio transcript</option>
              </select>
            </label>
            <label class="check-row">
              <input type="checkbox" id="video_caption_append_existing">
              <span>Append to existing caption</span>
            </label>
            <label class="check-row">
              <input type="checkbox" id="video_caption_no_overwrite">
              <span>Skip existing captions</span>
            </label>
          </div>
        </section>

        <section class="caption-section visual-caption-only">
          <h4 class="caption-section-title">Video sampling</h4>
          <div class="caption-section-grid">
            <label id="videoQwenFrameCountField">
              Frames
              <input type="number" id="video_qwen3vl_frame_count" min="1" max="64" step="1" value="12">
            </label>
            <label>
              Frame sampling mode
              <select id="video_qwen3vl_sampling_mode">
                <option value="auto" selected>Auto</option>
                <option value="even">Evenly sampled count</option>
                <option value="nth">Every nth frame</option>
              </select>
            </label>
            <label id="videoQwenNthFrameField">
              Caption every nth frame
              <input type="number" id="video_qwen3vl_every_nth_frame" min="1" max="100000" step="1" value="12">
            </label>
            <label>
              Max sampled frames
              <input type="number" id="video_qwen3vl_max_sampled_frames" min="1" max="64" step="1" value="16">
            </label>
            <label>
              Max frame side
              <input type="number" id="video_qwen3vl_max_image_side" min="128" max="4096" step="64" value="512">
            </label>
          </div>
        </section>

        <section class="caption-section qwen3vl-only">
          <h4 class="caption-section-title">Qwen3-VL</h4>
          <div class="caption-section-grid">
            <label>
              Model
              <select id="video_qwen3vl_model">
                <option>Qwen3-VL-4B-Instruct</option>
                <option>Qwen3-VL-8B-Instruct</option>
                <option>Huihui-Qwen3-VL-8B-Instruct-abliterated</option>
              </select>
            </label>
            <label>
              Temperature
              <input type="number" id="video_qwen3vl_temperature" min="0" max="2" step="0.05" value="0.2">
            </label>
            <label>
              Max tokens
              <input type="number" id="video_qwen3vl_max_tokens" min="1" step="1" value="256">
            </label>
            <label class="caption-full">
              System prompt
              <textarea id="video_qwen3vl_system_prompt" rows="8">Generate only a concise comma-separated LoRA caption for the video.

Start the caption with [name]. Use [name] as the character name or training trigger. Mention [name] only once.

Focus on what happens over time in the video. Describe the visible sequence of actions, pose changes, gestures, gaze shifts, expression changes, body movement, clothing movement, camera movement, and scene motion.

Use short visual phrases in temporal order when possible. Prefer motion-based descriptions such as turning, walking, leaning, raising an arm, looking away, smiling, blinking, hair moving, fabric moving, camera pushing in, or background motion.

Do not describe body shape, body proportions, hair color, eye color, identity, story, intent, age, ethnicity, or personality. Do not invent details. Do not mention metadata, filename, timestamps, resolution, quality, or that this is a video.

Output only the caption, with no intro or explanation.</textarea>
            </label>
          </div>
        </section>

        <section class="caption-section external-api-only" style="display:none;">
          <h4 class="caption-section-title">External API</h4>
          <div class="caption-section-grid">
            <label class="caption-full">
              API URL
              <input type="text" id="video_external_api_url" placeholder="http://127.0.0.1:1234">
            </label>
            <label>
              Model ID
              <input type="text" id="video_external_api_model" placeholder="Any model ID accepted by the server">
            </label>
            <label>
              API key
              <input type="password" id="video_external_api_key" placeholder="Optional" autocomplete="off">
            </label>
            <label>
              Temperature
              <input type="number" id="video_external_api_temperature" min="0" max="2" step="0.05" value="0.2">
            </label>
            <label>
              Max tokens
              <input type="number" id="video_external_api_max_tokens" min="1" step="1" value="256">
            </label>
            <label class="caption-full">
              System prompt
              <textarea id="video_external_api_system_prompt" rows="8">Generate only a concise comma-separated LoRA caption for the video.

Start the caption with [name]. Use [name] as the character name or training trigger. Mention [name] only once.

Focus on what happens over time in the video. Describe the visible sequence of actions, pose changes, gestures, gaze shifts, expression changes, body movement, clothing movement, camera movement, and scene motion.

Use short visual phrases in temporal order when possible. Do not invent details. Do not mention metadata, filename, timestamps, resolution, quality, or that this is a video.

Output only the caption, with no intro or explanation.</textarea>
            </label>
          </div>
        </section>

        <section class="caption-section whisperx-only" style="display:none;">
          <h4 class="caption-section-title">WhisperX</h4>
          <div class="caption-section-grid">
            <label>
              Model
              <select id="video_whisperx_model">
                <option>large-v3</option>
                <option>medium</option>
                <option>small</option>
                <option>base</option>
                <option>tiny</option>
              </select>
            </label>
            <label>
              Language
              <input type="text" id="video_whisperx_language" placeholder="Auto">
            </label>
            <label>
              VAD method
              <select id="video_whisperx_vad_method">
                <option value="silero">Silero</option>
                <option value="pyannote">Pyannote</option>
              </select>
            </label>
            <label>
              Batch size
              <input type="number" id="video_whisperx_batch_size" min="1" step="1" value="8">
            </label>
          </div>
        </section>
      </div>
      <div class="modal-progress" id="videoCaptionProgress" style="display:none;">
        <span class="modal-progress-label" id="videoCaptionProgressLabel">Captions: 0/0</span>
        <span class="modal-progress-percent" id="videoCaptionProgressPercent">0%</span>
        <div class="modal-progress-track" aria-hidden="true">
          <div class="modal-progress-fill" id="videoCaptionProgressFill"></div>
        </div>
      </div>
      <div class="modal-actions">
        <span class="modal-status" id="videoCaptionStatusText">Idle</span>
        <button type="button" id="videoCaptionStartBtn">Start</button>
        <button type="button" id="videoCaptionInterruptBtn" disabled>Interrupt</button>
      </div>
      <div class="logbox" id="videoCaptionLogBox"></div>
    </div>
  </div>
</div>

<div id="convertModal" class="modal">
  <div class="modal-card">
    <div class="modal-head">
      <h3>Convert</h3>
      <button type="button" class="modal-close-btn" id="closeConvertModalBtn" aria-label="Close">×</button>
    </div>
    <div class="modal-body">
      <form id="convertFpsForm" class="two">
        <label>
          Target FPS
          <input id="convertFpsInput" type="number" min="1" max="240" step="0.001" value="24">
        </label>
        <label>
          Quality
          <select id="convertQualitySelect">
            <option value="16">High quality (CRF 16)</option>
            <option value="18" selected>Default (CRF 18)</option>
            <option value="20">Smaller file (CRF 20)</option>
            <option value="0">Near lossless (CRF 0)</option>
          </select>
        </label>
        <label style="grid-column:1 / -1;display:flex;align-items:center;gap:8px;">
          <input id="convertBackupCheckbox" type="checkbox" checked>
          <span>Create backups in BACKUP folder</span>
        </label>
        <div class="notice" style="padding:10px;grid-column:1 / -1;">Changes every video in the opened folder to the selected FPS. Converted videos replace the opened files.</div>
        <div class="modal-progress" id="convertProgress" style="display:none;">
          <span class="modal-progress-label" id="convertProgressLabel">Convert: 0/0</span>
          <span class="modal-progress-percent" id="convertProgressPercent">0%</span>
          <div class="modal-progress-track" aria-hidden="true">
            <div class="modal-progress-fill" id="convertProgressFill"></div>
          </div>
        </div>
        <div class="modal-actions" style="grid-column:1 / -1;">
          <span class="modal-status" id="convertStatusText">Idle</span>
          <button type="submit" id="convertFpsStartBtn">Convert FPS</button>
          <button type="button" id="convertInterruptBtn" disabled>Interrupt</button>
        </div>
      </form>
    </div>
  </div>
</div>

<div id="settingsModal" class="modal">
  <div class="modal-card">
    <div class="modal-head">
      <h3>Settings</h3>
      <button type="button" class="modal-close-btn" id="closeSettingsModalBtn" aria-label="Close">X</button>
    </div>
    <div class="modal-body">
      <form id="videoSettingsForm" class="caption-settings">
        <section class="caption-section">
          <h4 class="caption-section-title">Save</h4>
          <div class="caption-section-grid">
            <label>
              Compression CRF
              <input id="settingsSaveCrf" type="number" min="0" max="30" step="1" value="18">
            </label>
            <label>
              Compression preset
              <select id="settingsSavePreset">
                <option value="ultrafast">ultrafast</option>
                <option value="superfast">superfast</option>
                <option value="veryfast">veryfast</option>
                <option value="faster">faster</option>
                <option value="fast">fast</option>
                <option value="medium" selected>medium</option>
                <option value="slow">slow</option>
                <option value="slower">slower</option>
                <option value="veryslow">veryslow</option>
              </select>
            </label>
            <label>
              Final FPS
              <input id="settingsSaveFps" type="number" min="1" max="240" step="0.001" placeholder="Keep source">
            </label>
            <label>
              Video format
              <select id="settingsVideoFormat">
                <option value="same">Keep source format</option>
                <option value="mp4">MP4</option>
                <option value="mkv">MKV</option>
              </select>
            </label>
          </div>
        </section>
        <section class="caption-section">
          <h4 class="caption-section-title">Slicing</h4>
          <label class="check-row">
            <input id="settingsSliceKeepSource" type="checkbox" checked>
            <span>Keep original video after slicing</span>
          </label>
          <label class="check-row">
            <input id="settingsIncludeEndFrameWhenTrimming" type="checkbox">
            <span>Include end frame when trimming</span>
          </label>
        </section>
        <div class="modal-actions">
          <span class="modal-status" id="settingsStatusText">Saved locally</span>
          <button type="submit" id="settingsSaveBtn">Save settings</button>
        </div>
      </form>
    </div>
  </div>
</div>

<div id="statsModal" class="modal">
  <div class="modal-card">
    <div class="modal-head">
      <h3>Stats</h3>
      <button type="button" class="modal-close-btn" id="closeStatsModalBtn" aria-label="Close">×</button>
    </div>
    <div class="modal-body">
      <div id="statsContent" class="notice">Loading...</div>
    </div>
  </div>
</div>

<div id="renameAllModal" class="modal">
  <div class="modal-card">
    <div class="modal-head">
      <h3>Rename</h3>
      <button type="button" class="modal-close-btn" id="closeRenameAllModalBtn" aria-label="Close">×</button>
    </div>
    <div class="modal-body">
      <form id="renameAllForm" class="two">
        <label style="grid-column:1 / -1;">
          Prefix
          <input id="renameAllPrefixInput" type="text" value="video_">
        </label>
        <div class="notice" style="padding:10px;grid-column:1 / -1;">Renames every opened video and matching caption as prefix00000, prefix00001, and so on.</div>
        <div class="modal-actions" style="grid-column:1 / -1;">
          <span class="modal-status" id="renameAllStatusText">Idle</span>
          <button type="submit" id="renameAllStartBtn">Rename</button>
        </div>
      </form>
    </div>
  </div>
</div>

<div id="sliceModal" class="modal">
  <div class="modal-card slice-modal-card">
    <div class="modal-head">
      <h3 id="sliceModalTitle">Slice</h3>
      <div class="slice-window-actions">
        <button type="button" class="slice-maximize-btn" id="sliceMaximizeBtn" title="Maximize" aria-label="Maximize"></button>
        <button type="button" class="modal-close-btn" id="closeSliceModalBtn" aria-label="Close">X</button>
      </div>
    </div>
    <div class="modal-body">
      <div class="slice-layout">
        <div class="slice-video-wrap">
          <video id="sliceVideo" class="slice-video" preload="metadata"></video>
        </div>
        <input id="sliceSeek" type="range" min="0" max="0" step="1" value="0">
        <div class="slice-toolbar">
          <button type="button" id="slicePlayBtn" class="icon-only-btn" title="Play/Pause" aria-label="Play/Pause">
            <span class="media-icon media-play" aria-hidden="true"></span>
          </button>
          <button type="button" id="sliceStopBtn" class="icon-only-btn" title="Stop" aria-label="Stop">
            <span class="media-icon media-stop" aria-hidden="true"></span>
          </button>
          <button type="button" id="sliceAddCutBtn">Add cut</button>
          <button type="button" id="sliceRemoveCutBtn">Remove nearest cut</button>
          <span class="modal-status" id="sliceStatusText">Frame: 0 / 0</span>
        </div>
        <div id="sliceTimeline" class="slice-timeline"></div>
        <div id="sliceList" class="slice-list"></div>
        <div class="modal-actions">
          <span class="modal-status" id="sliceSaveStatusText">Select cut points, then toggle segments to Keep or Remove.</span>
          <button type="button" id="sliceCancelBtn">Cancel</button>
          <button type="button" id="sliceSaveBtn">Save</button>
        </div>
      </div>
    </div>
    <div class="slice-resize-handle" id="sliceResizeHandle" aria-hidden="true"></div>
  </div>
</div>

<div id="appDialogBackdrop" class="modal">
  <div class="modal-card app-dialog">
    <div class="modal-head">
      <h3 id="appDialogTitle">Message</h3>
      <button type="button" class="modal-close-btn" id="appDialogCloseBtn" aria-label="Close">×</button>
    </div>
    <div class="modal-body">
      <div id="appDialogMessage" class="notice app-dialog-message"></div>
      <input type="text" id="appDialogInput" style="display:none;margin-top:10px;">
      <div class="modal-actions">
        <button type="button" id="appDialogCancelBtn">Cancel</button>
        <button type="button" id="appDialogOkBtn">OK</button>
      </div>
    </div>
  </div>
</div>

<script>
const BUCKETS = {{ bucket_options_json | safe }};
const HAS_OPEN_FOLDER = {{ 'true' if current_folder else 'false' }};
const VIDEO_EXTENSIONS_JS = ['.mp4', '.mkv', '.webm', '.mov', '.avi', '.m4v'];
const VIDEO_MIME_EXTENSIONS = {
  'video/mp4': '.mp4',
  'video/x-matroska': '.mkv',
  'video/webm': '.webm',
  'video/quicktime': '.mov',
  'video/x-msvideo': '.avi',
};
const dropPasteOverlay = document.getElementById('dropPasteOverlay');
const appDialogBackdrop = document.getElementById('appDialogBackdrop');
const appDialogTitle = document.getElementById('appDialogTitle');
const appDialogMessage = document.getElementById('appDialogMessage');
const appDialogInput = document.getElementById('appDialogInput');
const appDialogOkBtn = document.getElementById('appDialogOkBtn');
const appDialogCancelBtn = document.getElementById('appDialogCancelBtn');
const appDialogCloseBtn = document.getElementById('appDialogCloseBtn');

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
      if (event.key === 'Escape') cancel();
      if (event.key === 'Enter' && (event.ctrlKey || !isPrompt)) ok();
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

function showAppBusy(message, title = 'Working') {
  if (!appDialogBackdrop || !appDialogTitle || !appDialogMessage) return;
  appDialogTitle.textContent = title;
  appDialogMessage.textContent = message;
  if (appDialogInput) appDialogInput.style.display = 'none';
  if (appDialogCancelBtn) appDialogCancelBtn.style.display = 'none';
  if (appDialogOkBtn) appDialogOkBtn.style.display = 'none';
  if (appDialogCloseBtn) appDialogCloseBtn.style.display = 'none';
  appDialogBackdrop.classList.add('open');
}

function hideAppBusy() {
  if (!appDialogBackdrop) return;
  appDialogBackdrop.classList.remove('open');
  if (appDialogOkBtn) appDialogOkBtn.style.display = '';
  if (appDialogCloseBtn) appDialogCloseBtn.style.display = '';
}

function setVideoStatusbarMessage(text) {
  const el = document.querySelector('.statusbar-message');
  if (el) el.textContent = text || '';
}

function videoFileExtension(file) {
  const name = String(file?.name || '').toLowerCase();
  const type = String(file?.type || '').toLowerCase();
  const ext = name.includes('.') ? name.slice(name.lastIndexOf('.')) : '';
  if (VIDEO_EXTENSIONS_JS.includes(ext)) return ext;
  return VIDEO_MIME_EXTENSIONS[type] || '';
}

function isVideoFile(file) {
  return !!file && !!videoFileExtension(file);
}

function videoFilesFromFileList(fileList) {
  const seen = new Set();
  return Array.from(fileList || []).filter(file => {
    if (!isVideoFile(file)) return false;
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

function videoFilesFromClipboard(clipboardData) {
  const files = videoFilesFromFileList(clipboardData?.files || []);
  if (files.length) return files;
  return Array.from(clipboardData?.items || [])
    .filter(item => item.kind === 'file')
    .map(item => item.getAsFile())
    .filter(isVideoFile);
}

function hasVideoDrag(event) {
  const types = Array.from(event.dataTransfer?.types || []);
  if (types.includes('Files')) return true;
  const items = Array.from(event.dataTransfer?.items || []);
  if (items.some(item => item.kind === 'file' && String(item.type || '').startsWith('video/'))) return true;
  return Array.from(event.dataTransfer?.files || []).some(isVideoFile);
}

function hasUnsavedVideoChanges() {
  return videoCardControllers.some(controller => controller.hasVideoChanges() || controller.hasCaptionChanges());
}

async function uploadVideoFiles(files, sourceLabel = 'selected') {
  const videoFiles = videoFilesFromFileList(files);
  if (!videoFiles.length) return;
  if (!HAS_OPEN_FOLDER) {
    await appAlert('Open a folder before adding videos.');
    return;
  }
  if (hasUnsavedVideoChanges()) {
    const ok = await appConfirm('Add videos and refresh the view? Unsaved edits will be discarded.');
    if (!ok) return;
  }

  const formData = new FormData();
  videoFiles.forEach((file, i) => {
    const ext = videoFileExtension(file) || '.mp4';
    const fallbackName = sourceLabel === 'pasted' ? `pasted_video_${i + 1}${ext}` : `video_${i + 1}${ext}`;
    formData.append('videos', file, file.name || fallbackName);
  });

  showAppBusy(`Adding ${videoFiles.length} video file(s)...`, 'Add videos');
  try {
    const res = await fetch('/upload_videos', {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      body: formData,
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      hideAppBusy();
      await appAlert(data.error || 'Failed to add videos.');
      return;
    }
    setVideoStatusbarMessage(`Added ${data.added?.length || 0} video(s).`);
    location.reload();
  } catch (err) {
    hideAppBusy();
    await appAlert(`Failed to add videos: ${err}`);
  }
}

const VIEWPORT_RESTORE_KEY = 'video_prep_viewport_restore';

function reloadPreservingViewport(anchorCard = null) {
  const card = anchorCard?.closest?.('.card') || null;
  const payload = {
    x: window.scrollX,
    y: window.scrollY,
    name: card?.dataset?.name || '',
    cardTop: card ? card.getBoundingClientRect().top : 0,
  };
  sessionStorage.setItem(VIEWPORT_RESTORE_KEY, JSON.stringify(payload));
  location.reload();
}

function cardViewportAnchorAfterRemoval(card) {
  const next = card?.nextElementSibling?.classList?.contains('card') ? card.nextElementSibling : null;
  const previous = card?.previousElementSibling?.classList?.contains('card') ? card.previousElementSibling : null;
  return next || previous || card;
}

function restoreViewportAfterReload() {
  let payload = null;
  try {
    payload = JSON.parse(sessionStorage.getItem(VIEWPORT_RESTORE_KEY) || 'null');
  } catch (e) {
    payload = null;
  }
  sessionStorage.removeItem(VIEWPORT_RESTORE_KEY);
  if (!payload) return;
  const restore = () => {
    const cardName = String(payload.name || '');
    const card = cardName
      ? Array.from(document.querySelectorAll('.card')).find(item => item.dataset.name === cardName)
      : null;
    if (card) {
      const rect = card.getBoundingClientRect();
      window.scrollTo(Number(payload.x || 0), window.scrollY + rect.top - Number(payload.cardTop || 0));
    } else {
      window.scrollTo(Number(payload.x || 0), Number(payload.y || 0));
    }
  };
  requestAnimationFrame(() => {
    restore();
    setTimeout(restore, 80);
    setTimeout(restore, 250);
  });
}

if ('scrollRestoration' in history) {
  history.scrollRestoration = 'manual';
}
restoreViewportAfterReload();

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

function makeVideoModalDraggable(backdrop) {
  const modal = backdrop?.querySelector('.modal-card');
  const head = modal?.querySelector('.modal-head');
  if (!modal || !head || head.dataset.draggableBound) return;
  head.dataset.draggableBound = '1';
  head.addEventListener('pointerdown', event => {
    if (event.button !== 0 || event.target.closest('button, input, textarea, select, a')) return;
    event.preventDefault();
    const rect = modal.getBoundingClientRect();
    modal.style.position = 'fixed';
    modal.style.left = `${rect.left}px`;
    modal.style.top = `${rect.top}px`;
    modal.style.width = `${rect.width}px`;
    modal.style.height = `${rect.height}px`;
    modal.style.maxWidth = 'none';
    modal.style.maxHeight = 'none';
    modal.style.margin = '0';
    const startX = event.clientX;
    const startY = event.clientY;
    const startLeft = rect.left;
    const startTop = rect.top;
    let dragging = false;
    head.setPointerCapture?.(event.pointerId);
    const move = moveEvent => {
      moveEvent.preventDefault();
      const dx = moveEvent.clientX - startX;
      const dy = moveEvent.clientY - startY;
      if (!dragging && Math.hypot(dx, dy) < 3) return;
      if (!dragging) {
        dragging = true;
        modal.classList.remove('maximized');
      }
      const maxLeft = Math.max(4, window.innerWidth - 80);
      const maxTop = Math.max(4, window.innerHeight - 48);
      modal.style.left = `${clamp(startLeft + dx, 4, maxLeft)}px`;
      modal.style.top = `${clamp(startTop + dy, 4, maxTop)}px`;
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

function makeSliceModalResizable() {
  const modal = document.querySelector('#sliceModal .slice-modal-card');
  const handle = document.getElementById('sliceResizeHandle');
  if (!modal || !handle || handle.dataset.resizeBound) return;
  handle.dataset.resizeBound = '1';
  handle.addEventListener('pointerdown', event => {
    if (event.button !== 0) return;
    event.preventDefault();
    event.stopPropagation();
    const rect = modal.getBoundingClientRect();
    modal.style.position = 'fixed';
    modal.style.left = `${rect.left}px`;
    modal.style.top = `${rect.top}px`;
    modal.style.width = `${rect.width}px`;
    modal.style.height = `${rect.height}px`;
    modal.style.maxWidth = 'none';
    modal.style.maxHeight = 'none';
    modal.style.margin = '0';
    modal.classList.remove('maximized');
    const startX = event.clientX;
    const startY = event.clientY;
    const startWidth = rect.width;
    const startHeight = rect.height;
    handle.setPointerCapture?.(event.pointerId);
    const move = moveEvent => {
      moveEvent.preventDefault();
      const maxWidth = Math.max(520, window.innerWidth - rect.left - 8);
      const maxHeight = Math.max(360, window.innerHeight - rect.top - 8);
      const nextWidth = clamp(startWidth + (moveEvent.clientX - startX), 520, maxWidth);
      const nextHeight = clamp(startHeight + (moveEvent.clientY - startY), 420, maxHeight);
      modal.style.width = `${nextWidth}px`;
      modal.style.height = `${nextHeight}px`;
    };
    const up = () => {
      handle.removeEventListener('pointermove', move);
      handle.removeEventListener('pointerup', up);
      handle.removeEventListener('pointercancel', up);
    };
    handle.addEventListener('pointermove', move, { passive: false });
    handle.addEventListener('pointerup', up, { once: true });
    handle.addEventListener('pointercancel', up, { once: true });
  });
}

function toggleSliceMaximized() {
  const modal = document.querySelector('#sliceModal .slice-modal-card');
  const btn = document.getElementById('sliceMaximizeBtn');
  if (!modal) return;
  const isMaximized = modal.classList.contains('maximized');
  if (isMaximized) {
    modal.classList.remove('maximized');
    const previous = modal.dataset.previousRect ? JSON.parse(modal.dataset.previousRect) : null;
    if (previous) {
      modal.style.position = 'fixed';
      modal.style.left = `${previous.left}px`;
      modal.style.top = `${previous.top}px`;
      modal.style.width = `${previous.width}px`;
      modal.style.height = `${previous.height}px`;
      modal.style.maxWidth = 'none';
      modal.style.maxHeight = 'none';
      modal.style.margin = '0';
    }
    if (btn) {
      btn.title = 'Maximize';
      btn.setAttribute('aria-label', 'Maximize');
      btn.setAttribute('aria-pressed', 'false');
    }
    return;
  }
  const rect = modal.getBoundingClientRect();
  modal.dataset.previousRect = JSON.stringify({
    left: rect.left,
    top: rect.top,
    width: rect.width,
    height: rect.height,
  });
  modal.classList.add('maximized');
  if (btn) {
    btn.title = 'Restore';
    btn.setAttribute('aria-label', 'Restore');
    btn.setAttribute('aria-pressed', 'true');
  }
}

document.querySelectorAll('.modal').forEach(makeVideoModalDraggable);
makeSliceModalResizable();
document.getElementById('sliceMaximizeBtn')?.addEventListener('click', event => {
  event.preventDefault();
  event.stopPropagation();
  toggleSliceMaximized();
});

const cropEditors = [];
const videoCardControllers = [];

function updateSaveAllButtonState() {
  const saveAllBtn = document.getElementById('saveAllBtn');
  if (!saveAllBtn) return;
  const hasUnsaved = videoCardControllers.some(controller => (
    controller.hasVideoChanges() || controller.hasCaptionChanges()
  ));
  saveAllBtn.classList.toggle('has-unsaved', hasUnsaved);
}

function readStoredNumber(key, fallback, min, max) {
  const parsed = parseInt(localStorage.getItem(key) || String(fallback), 10);
  if (!Number.isFinite(parsed)) return fallback;
  return clamp(parsed, min, max);
}

function setTextHeight(value) {
  const height = clamp(value, 60, 360);
  localStorage.setItem('video_prep_text_height', String(height));
  document.documentElement.style.setProperty('--caption-text-height', `${height}px`);
  const slider = document.getElementById('textHeightSlider');
  const valueEl = document.getElementById('textHeightValue');
  if (slider) slider.value = String(height);
  if (valueEl) valueEl.textContent = String(height);
  document.querySelectorAll('.caption').forEach(ta => { ta.style.height = `${height}px`; });
}

function redrawVideoCards() {
  cropEditors.forEach(editor => editor.redraw && editor.redraw());
}

function scheduleVideoCardRedraw() {
  window.requestAnimationFrame(() => {
    redrawVideoCards();
    window.requestAnimationFrame(redrawVideoCards);
    setTimeout(redrawVideoCards, 80);
  });
}

function directVideoMediaBox(card) {
  const stage = card?.querySelector('.video-stage');
  const video = card?.querySelector('video');
  if (!stage || !video) return null;
  const boxW = stage.clientWidth || 1;
  const boxH = stage.clientHeight || 1;
  const rawSrcW = video.videoWidth || parseFloat(card.dataset.width || '0') || boxW;
  const rawSrcH = video.videoHeight || parseFloat(card.dataset.height || '0') || boxH;
  const transform = directVideoTransformState(card);
  const rotated = transform.rotation % 180 !== 0;
  const srcW = rotated ? rawSrcH : rawSrcW;
  const srcH = rotated ? rawSrcW : rawSrcH;
  const fitScale = Math.min(boxW / Math.max(srcW, 1), boxH / Math.max(srcH, 1));
  const zoom = clamp(parseFloat(stage.dataset.zoom || '1') || 1, 0.25, 8);
  const w = srcW * fitScale * zoom;
  const h = srcH * fitScale * zoom;
  const panX = parseFloat(stage.dataset.panX || '0') || 0;
  const panY = parseFloat(stage.dataset.panY || '0') || 0;
  const maxX = Math.max(0, (w - boxW) / 2);
  const maxY = Math.max(0, (h - boxH) / 2);
  const x = (boxW - w) / 2 + clamp(panX, -maxX, maxX);
  const y = (boxH - h) / 2 + clamp(panY, -maxY, maxY);
  return { stage, video, rawSrcW, rawSrcH, srcW, srcH, boxW, boxH, zoom, w, h, x, y, rotated, transform };
}

function applyVideoPreviewTransform(video, media, transform = {}) {
  if (!video || !media) return;
  const rotation = ((Number(transform.rotation) || 0) % 360 + 360) % 360;
  const rotated = rotation % 180 !== 0;
  const videoW = rotated ? media.h : media.w;
  const videoH = rotated ? media.w : media.h;
  video.style.left = `${media.x + media.w / 2}px`;
  video.style.top = `${media.y + media.h / 2}px`;
  video.style.width = `${videoW}px`;
  video.style.height = `${videoH}px`;
  video.style.maxWidth = 'none';
  video.style.maxHeight = 'none';
  video.style.objectFit = 'fill';
  video.style.transformOrigin = 'center center';
  video.style.transform = `translate(-50%, -50%) rotate(${rotation}deg) scaleX(${transform.flipHorizontal ? -1 : 1}) scaleY(${transform.flipVertical ? -1 : 1})`;
}

function drawDirectVideoZoom(card, show = true) {
  const box = directVideoMediaBox(card);
  if (!box) return;
  const { stage, srcW, w, transform } = box;
  applyVideoPreviewTransform(box.video, box, transform);
  if (show) {
    const readout = stage.querySelector('.zoom-readout');
    if (readout) {
      readout.textContent = `${Math.round((w / Math.max(srcW, 1)) * 100)}%`;
      readout.style.left = 'auto';
      readout.style.top = '4px';
      readout.style.right = '4px';
      readout.classList.add('show');
      clearTimeout(readout._zoomTimer);
      readout._zoomTimer = setTimeout(() => readout.classList.remove('show'), 850);
    }
  }
}

function directVideoTransformState(card) {
  const stage = card?.querySelector('.video-stage');
  return {
    flipHorizontal: stage?.dataset.flipHorizontal === '1',
    flipVertical: stage?.dataset.flipVertical === '1',
    rotation: parseInt(stage?.dataset.rotation || '0', 10) || 0,
  };
}

function setDirectVideoTransform(card, transform = {}) {
  const stage = card?.querySelector('.video-stage');
  if (!stage) return;
  stage.dataset.flipHorizontal = transform.flipHorizontal ? '1' : '0';
  stage.dataset.flipVertical = transform.flipVertical ? '1' : '0';
  stage.dataset.rotation = String(((Number(transform.rotation) || 0) % 360 + 360) % 360);
  drawDirectVideoZoom(card, false);
}

function syncDirectTransformButtons(card) {
  const transform = directVideoTransformState(card);
  card?.querySelector('.flip-h-btn')?.classList.toggle('active', !!transform.flipHorizontal);
  card?.querySelector('.flip-v-btn')?.classList.toggle('active', !!transform.flipVertical);
  const rotateBtn = card?.querySelector('.rotate-90-btn');
  rotateBtn?.classList.toggle('active', !!transform.rotation);
  if (rotateBtn) rotateBtn.textContent = transform.rotation ? `${transform.rotation}°` : '90°';
}

function handleDirectVideoTransformButton(button) {
  const card = button?.closest?.('.card');
  if (!card) return;
  const transform = directVideoTransformState(card);
  if (button.classList.contains('flip-h-btn')) transform.flipHorizontal = !transform.flipHorizontal;
  else if (button.classList.contains('flip-v-btn')) transform.flipVertical = !transform.flipVertical;
  else if (button.classList.contains('rotate-90-btn')) transform.rotation = (transform.rotation + 90) % 360;
  else return;
  setDirectVideoTransform(card, transform);
  syncDirectTransformButtons(card);
}

function cropSizeFromOverlayLabel(label) {
  const match = String(label || '').match(/(\d+)\s*x\s*(\d+)/i);
  if (!match) {
    const base = Number(selectedCropBase()) || 1024;
    return { w: base, h: base };
  }
  return {
    w: Math.max(2, parseInt(match[1], 10) || 1024),
    h: Math.max(2, parseInt(match[2], 10) || 1024),
  };
}

function cropPayloadFromVisibleOverlay(card) {
  const stage = card?.querySelector('.video-stage');
  const video = card?.querySelector('video');
  const overlay = card?.querySelector('.crop-overlay');
  if (!stage || !video || !overlay) return null;
  const style = window.getComputedStyle(overlay);
  if (style.display === 'none' || style.visibility === 'hidden') return null;

  const media = directVideoMediaBox(card);
  if (!media || media.w < 4 || media.h < 4) return null;

  const overlayLeft = parseFloat(overlay.style.left || '0') || 0;
  const overlayTop = parseFloat(overlay.style.top || '0') || 0;
  const overlayWidth = parseFloat(overlay.style.width || '0') || overlay.offsetWidth || 0;
  const overlayHeight = parseFloat(overlay.style.height || '0') || overlay.offsetHeight || 0;
  if (overlayWidth < 4 || overlayHeight < 4) return null;

  const cropSize = cropSizeFromOverlayLabel(overlay.dataset.label);
  return {
    has_crop: true,
    crop_w: cropSize.w,
    crop_h: cropSize.h,
    crop_x_ratio: clamp((overlayLeft - media.x) / Math.max(media.w, 1), 0, 1),
    crop_y_ratio: clamp((overlayTop - media.y) / Math.max(media.h, 1), 0, 1),
    crop_rect_w_ratio: clamp(overlayWidth / Math.max(media.w, 1), 0.0001, 1),
    crop_rect_h_ratio: clamp(overlayHeight / Math.max(media.h, 1), 0.0001, 1),
  };
}

function ensureVisibleCropPayload(card, crop) {
  if (crop?.has_crop) return crop;
  return cropPayloadFromVisibleOverlay(card) || crop;
}

function setDirectVideoZoom(card, zoom, show = true) {
  const stage = card?.querySelector('.video-stage');
  if (!stage) return;
  stage.dataset.zoom = String(clamp(Number(zoom) || 1, 0.25, 8));
  if (stage.dataset.zoom === '1') {
    stage.dataset.panX = '0';
    stage.dataset.panY = '0';
  }
  drawDirectVideoZoom(card, show);
}

function directVideoZoomActual(card) {
  const box = directVideoMediaBox(card);
  if (!box) return;
  const fitW = box.srcW * Math.min(box.boxW / Math.max(box.srcW, 1), box.boxH / Math.max(box.srcH, 1));
  const fitH = box.srcH * Math.min(box.boxW / Math.max(box.srcW, 1), box.boxH / Math.max(box.srcH, 1));
  const zoom = Math.max(box.srcW / Math.max(fitW, 1), box.srcH / Math.max(fitH, 1));
  setDirectVideoZoom(card, zoom);
}

function handleDirectVideoZoomButton(button) {
  const card = button?.closest?.('.card');
  if (!card) return;
  const stage = card.querySelector('.video-stage');
  const current = clamp(parseFloat(stage?.dataset.zoom || '1') || 1, 0.25, 8);
  if (button.classList.contains('zoom-in-btn')) setDirectVideoZoom(card, current * 1.25);
  else if (button.classList.contains('zoom-out-btn')) setDirectVideoZoom(card, current / 1.25);
  else if (button.classList.contains('zoom-default-btn')) setDirectVideoZoom(card, 1);
  else if (button.classList.contains('zoom-actual-btn')) directVideoZoomActual(card);
}

function setVideoSize(value) {
  const size = clamp(value, 220, 720);
  localStorage.setItem('video_prep_video_size', String(size));
  document.documentElement.style.setProperty('--video-display-height', `${size}px`);
  document.documentElement.style.setProperty('--video-card-width', `${size + 110}px`);
  const slider = document.getElementById('videoSizeSlider');
  const valueEl = document.getElementById('videoSizeValue');
  if (slider) slider.value = String(size);
  if (valueEl) valueEl.textContent = String(size);
  scheduleVideoCardRedraw();
}

function setPreviewVolume(value) {
  const volumePercent = clamp(value, 0, 100);
  localStorage.setItem('video_prep_preview_volume', String(volumePercent));
  const slider = document.getElementById('previewVolumeSlider');
  const valueEl = document.getElementById('previewVolumeValue');
  if (slider) slider.value = String(volumePercent);
  if (valueEl) valueEl.textContent = String(volumePercent);
  const volume = volumePercent / 100;
  document.querySelectorAll('video').forEach(video => {
    video.volume = volume;
    video.muted = volume === 0;
  });
}

const textHeightSlider = document.getElementById('textHeightSlider');
const videoSizeSlider = document.getElementById('videoSizeSlider');
const previewVolumeSlider = document.getElementById('previewVolumeSlider');
setTextHeight(readStoredNumber('video_prep_text_height', 110, 60, 360));
setVideoSize(readStoredNumber('video_prep_video_size', 320, 220, 720));
setPreviewVolume(readStoredNumber('video_prep_preview_volume', 100, 0, 100));
const storedCropBase = localStorage.getItem('video_prep_crop_base') || '1024';
const storedCropBaseRadio = document.querySelector(`input[name="crop_base"][value="${storedCropBase}"]`);
if (storedCropBaseRadio) storedCropBaseRadio.checked = true;
if (textHeightSlider) {
  textHeightSlider.addEventListener('input', e => setTextHeight(parseInt(e.target.value, 10)));
}
if (videoSizeSlider) {
  videoSizeSlider.addEventListener('input', e => setVideoSize(parseInt(e.target.value, 10)));
}
if (previewVolumeSlider) {
  previewVolumeSlider.addEventListener('input', e => setPreviewVolume(parseInt(e.target.value, 10)));
}
document.querySelectorAll('input[name="crop_base"]').forEach(radio => {
  radio.addEventListener('change', () => {
    localStorage.setItem('video_prep_crop_base', selectedCropBase());
    cropEditors.forEach(editor => editor.resnapToCropBase && editor.resnapToCropBase());
  });
});

function gcd(a, b) {
  return b ? gcd(b, a % b) : a;
}
function aspectLabel(w, h) {
  const g = gcd(w, h) || 1;
  return `${Math.round(w / g)}:${Math.round(h / g)}`;
}
function chooseNearestBucket(base, ratio, areaHint) {
  const buckets = BUCKETS[String(base)] || [];
  if (!buckets.length) {
    const fallbackW = parseInt(base, 10) || 1024;
    const fallbackH = Math.max(64, Math.round((fallbackW / Math.max(ratio, 0.0001)) / 64) * 64);
    return { w: fallbackW, h: fallbackH, label: `${fallbackW}x${fallbackH} (${aspectLabel(fallbackW, fallbackH)})` };
  }
  let best = buckets[0];
  let bestKey = [Infinity, Infinity];
  for (const b of buckets) {
    const br = b.w / b.h;
    const key = [Math.abs(br - ratio), Math.abs((b.w * b.h) - areaHint)];
    if (key[0] < bestKey[0] || (key[0] === bestKey[0] && key[1] < bestKey[1])) {
      best = b;
      bestKey = key;
    }
  }
  return { ...best, label: `${best.w}x${best.h} (${aspectLabel(best.w, best.h)})` };
}

function selectedCropBase() {
  return document.querySelector('input[name="crop_base"]:checked')?.value || '1024';
}

function estimateCaptionTokens(text) {
  const value = String(text || '').trim();
  if (!value) return 0;
  return (value.match(/\p{L}+|\p{N}+|[^\s\p{L}\p{N}]/gu) || []).length;
}

function updateCaptionStats(ta) {
  if (!ta) return;
  const card = ta.closest('.card');
  if (!card) return;
  const chars = String(ta.value || '').length;
  const tokens = estimateCaptionTokens(ta.value);
  const charEl = card.querySelector('.caption-char-count');
  const tokenEl = card.querySelector('.caption-token-count');
  if (charEl) charEl.textContent = `${chars} chars`;
  if (tokenEl) tokenEl.textContent = `${tokens} tokens`;
}

function createCropEditor(card) {
  const video = card.querySelector('video');
  const stage = card.querySelector('.video-stage');
  const overlay = card.querySelector('.crop-overlay');
  const startFrameInput = card.querySelector('.start-frame');
  const endFrameInput = card.querySelector('.end-frame');
  const trimRangeFill = card.querySelector('.trim-range-fill');
  const trimReadout = card.querySelector('.trim-readout');
  const trimCount = card.querySelector('.trim-count');
  const frameReadout = card.querySelector('.frame-readout');
  const seek = card.querySelector('.seek-bar');

  const state = {
    hasCrop: false,
    rect: { x: 0, y: 0, w: 0, h: 0 }, // stage coordinates
    cropRatios: { x: 0, y: 0, w: 1, h: 1 },
    flipHorizontal: false,
    flipVertical: false,
    rotation: 0,
    dragMode: null, // create | move | nw | ne | sw | se
    pointerStart: null,
    rectStart: null,
    fps: parseFloat(card.dataset.fps || '24') || 24,
    frames: parseInt(card.dataset.frames || '0', 10) || 0,
    bucket: { w: 1024, h: 1024, label: '1024x1024 (1:1)' },
  };

  function mediaZoom() {
    const value = parseFloat(stage.dataset.zoom || '1');
    return Number.isFinite(value) && value > 0 ? value : 1;
  }

  function mediaPan() {
    return {
      x: parseFloat(stage.dataset.panX || '0') || 0,
      y: parseFloat(stage.dataset.panY || '0') || 0,
    };
  }

  function setMediaPan(x, y) {
    stage.dataset.panX = String(Number(x) || 0);
    stage.dataset.panY = String(Number(y) || 0);
  }

  function clampMediaPan(width, height) {
    const pan = mediaPan();
    const maxX = Math.max(0, (width - stage.clientWidth) / 2);
    const maxY = Math.max(0, (height - stage.clientHeight) / 2);
    const clamped = {
      x: clamp(pan.x, -maxX, maxX),
      y: clamp(pan.y, -maxY, maxY),
    };
    setMediaPan(clamped.x, clamped.y);
    return clamped;
  }

  function showZoomReadout() {
    const readout = stage.querySelector('.zoom-readout');
    if (!readout) return;
    const box = mediaBox();
    const srcW = video.videoWidth || box.w || 1;
    readout.textContent = `${Math.round((box.w / Math.max(srcW, 1)) * 100)}%`;
    readout.style.width = 'max-content';
    readout.style.whiteSpace = 'nowrap';
    const readoutW = readout.offsetWidth || readout.getBoundingClientRect().width || 0;
    const readoutH = readout.offsetHeight || readout.getBoundingClientRect().height || 0;
    const inset = 4;
    readout.style.left = `${clamp(box.x + box.w - readoutW - inset, inset, stage.clientWidth - readoutW - inset)}px`;
    readout.style.top = `${clamp(box.y + inset, inset, stage.clientHeight - readoutH - inset)}px`;
    readout.style.right = 'auto';
    readout.classList.add('show');
    clearTimeout(readout._zoomTimer);
    readout._zoomTimer = setTimeout(() => readout.classList.remove('show'), 850);
  }

  function applyVideoZoom(zoom, show = true) {
    const next = clamp(Number(zoom) || 1, 0.25, 8);
    stage.dataset.zoom = String(next);
    if (next === 1) setMediaPan(0, 0);
    draw();
    if (show) showZoomReadout();
  }

  function setActualVideoZoom() {
    const currentZoom = mediaZoom();
    stage.dataset.zoom = '1';
    const base = mediaBox();
    const srcW = video.videoWidth || parseFloat(card.dataset.width || '0') || base.w || 1;
    const srcH = video.videoHeight || parseFloat(card.dataset.height || '0') || base.h || 1;
    const zoom = Math.max(srcW / Math.max(base.w, 1), srcH / Math.max(base.h, 1));
    stage.dataset.zoom = String(currentZoom);
    applyVideoZoom(zoom);
  }

  function displayedSize() {
    return { w: stage.clientWidth, h: stage.clientHeight };
  }

  function mediaBox() {
    const boxW = stage.clientWidth;
    const boxH = stage.clientHeight;
    const rawSrcW = video.videoWidth || parseFloat(card.dataset.width || '0') || boxW || 1;
    const rawSrcH = video.videoHeight || parseFloat(card.dataset.height || '0') || boxH || 1;
    const rotated = state.rotation % 180 !== 0;
    const srcW = rotated ? rawSrcH : rawSrcW;
    const srcH = rotated ? rawSrcW : rawSrcH;
    const scale = Math.min(boxW / srcW, boxH / srcH);
    const zoom = mediaZoom();
    const w = srcW * scale * zoom;
    const h = srcH * scale * zoom;
    const pan = clampMediaPan(w, h);
    const x = (boxW - w) / 2 + pan.x;
    const y = (boxH - h) / 2 + pan.y;
    return { x, y, w, h };
  }

  function syncCropRatiosFromRect() {
    const m = mediaBox();
    state.cropRatios = {
      x: clamp((state.rect.x - m.x) / Math.max(m.w, 1), 0, 1),
      y: clamp((state.rect.y - m.y) / Math.max(m.h, 1), 0, 1),
      w: clamp(state.rect.w / Math.max(m.w, 1), 0.0001, 1),
      h: clamp(state.rect.h / Math.max(m.h, 1), 0.0001, 1),
    };
  }

  function rectFromCropRatios() {
    const m = mediaBox();
    return {
      x: m.x + state.cropRatios.x * m.w,
      y: m.y + state.cropRatios.y * m.h,
      w: state.cropRatios.w * m.w,
      h: state.cropRatios.h * m.h,
    };
  }

  function setCropRect(rect) {
    state.rect = clampRectToMedia(rect);
    syncCropRatiosFromRect();
  }

  function currentFrame() {
    return clamp(Math.round(video.currentTime * state.fps), 0, Math.max(0, state.frames));
  }

  function frameToTime(frame) {
    return clamp(frame, 0, Math.max(0, state.frames)) / state.fps;
  }

  function seekToFrame(frame) {
    video.pause();
    video.currentTime = frameToTime(frame);
  }

  function updateFrameReadout() {
    if (frameReadout) frameReadout.textContent = `Frame: ${currentFrame()} / ${state.frames}`;
  }

  function updateSeekBar() {
    if (seek) {
      seek.max = String(Math.max(0, state.frames));
      seek.step = "1";
      seek.value = String(currentFrame());
    }
  }

  function formatDurationSeconds(seconds) {
    const value = Number(seconds) || 0;
    if (value >= 10) return value.toFixed(2).replace(/\.?0+$/, '');
    return value.toFixed(2);
  }

  function trimFrames() {
    const includeEndFrame = !!getVideoSettings().includeEndFrameWhenTrimming;
    const maxFrame = Math.max(0, state.frames);
    const maxEndFrame = includeEndFrame ? Math.max(0, maxFrame - 1) : maxFrame;
    const minGap = includeEndFrame ? 0 : (maxFrame > 0 ? 1 : 0);
    let start = parseInt(startFrameInput?.value || '0', 10) || 0;
    let end = parseInt(endFrameInput?.value || String(maxEndFrame), 10) || maxEndFrame;
    start = clamp(start, 0, Math.max(0, maxEndFrame - minGap));
    end = clamp(end, start + minGap, maxEndFrame);
    return { start, end, maxFrame, maxEndFrame, includeEndFrame };
  }

  function updateTrimTimeline() {
    if (!startFrameInput || !endFrameInput) return;
    const { start, end, maxFrame, maxEndFrame, includeEndFrame } = trimFrames();
    startFrameInput.min = "0";
    startFrameInput.max = String(maxEndFrame);
    startFrameInput.step = "1";
    startFrameInput.value = String(start);
    endFrameInput.min = "0";
    endFrameInput.max = String(maxEndFrame);
    endFrameInput.step = "1";
    endFrameInput.value = String(end);
    if (trimReadout) trimReadout.textContent = `Trim: ${start} - ${end}`;
    if (trimCount) {
      const selectedFrames = includeEndFrame ? Math.max(0, end - start + 1) : Math.max(0, end - start);
      const selectedSeconds = selectedFrames / Math.max(state.fps, 0.0001);
      trimCount.textContent = `${selectedFrames} frames selected (${formatDurationSeconds(selectedSeconds)} s)`;
    }
    if (trimRangeFill) {
      const denom = Math.max(includeEndFrame ? maxEndFrame : maxFrame, 1);
      trimRangeFill.style.left = `${(start / denom) * 100}%`;
      trimRangeFill.style.right = `${100 - ((end / denom) * 100)}%`;
    }
  }

  function stagePoint(clientX, clientY) {
    const rect = stage.getBoundingClientRect();
    return {
      x: clientX - rect.left,
      y: clientY - rect.top,
    };
  }

  function clampPointToMedia(clientX, clientY) {
    const p = stagePoint(clientX, clientY);
    const m = mediaBox();
    return {
      x: clamp(p.x, m.x, m.x + m.w),
      y: clamp(p.y, m.y, m.y + m.h),
    };
  }

  function updateBucketFromRect(rawRect) {
    const ratio = rawRect.w / Math.max(rawRect.h, 1);
    const areaHint = rawRect.w * rawRect.h;
    state.bucket = chooseNearestBucket(selectedCropBase(), ratio, areaHint);
  }

  function rectFromCorners(x1, y1, x2, y2) {
    const left = Math.min(x1, x2);
    const top = Math.min(y1, y2);
    const right = Math.max(x1, x2);
    const bottom = Math.max(y1, y2);
    return { x: left, y: top, w: right - left, h: bottom - top };
  }

  function snappedRectFromRaw(rawRect, anchorMode) {
    const m = mediaBox();
    updateBucketFromRect(rawRect);
    const ratio = state.bucket.w / state.bucket.h;

    let w = rawRect.w;
    let h = w / ratio;
    if (h > rawRect.h) {
      h = rawRect.h;
      w = h * ratio;
    }

    w = Math.max(4, w);
    h = Math.max(4, h);

    if (anchorMode === 'se') {
      return { x: rawRect.x, y: rawRect.y, w, h };
    } else if (anchorMode === 'sw') {
      return { x: rawRect.x + rawRect.w - w, y: rawRect.y, w, h };
    } else if (anchorMode === 'ne') {
      return { x: rawRect.x, y: rawRect.y + rawRect.h - h, w, h };
    } else if (anchorMode === 'nw') {
      return { x: rawRect.x + rawRect.w - w, y: rawRect.y + rawRect.h - h, w, h };
    } else {
      // create: keep the start corner anchored and fit inside the dragged box
      return { x: rawRect.x, y: rawRect.y, w, h };
    }
  }

  function clampRectToMedia(rect) {
    const m = mediaBox();
    const w = clamp(rect.w, 4, m.w);
    const h = clamp(rect.h, 4, m.h);
    const x = clamp(rect.x, m.x, m.x + m.w - w);
    const y = clamp(rect.y, m.y, m.y + m.h - h);
    return { x, y, w, h };
  }

  function sourceCropSize() {
    const m = mediaBox();
    const rotated = state.rotation % 180 !== 0;
    const rawSrcW = video.videoWidth || parseFloat(card.dataset.width || '0') || m.w || 1;
    const rawSrcH = video.videoHeight || parseFloat(card.dataset.height || '0') || m.h || 1;
    const sourceW = rotated ? rawSrcH : rawSrcW;
    const sourceH = rotated ? rawSrcW : rawSrcH;
    return {
      w: sourceW * ((state.rect.w) / Math.max(m.w, 1)),
      h: sourceH * ((state.rect.h) / Math.max(m.h, 1)),
    };
  }

  function draw() {
    const media = mediaBox();
    applyVideoPreviewTransform(video, media, {
      flipHorizontal: state.flipHorizontal,
      flipVertical: state.flipVertical,
      rotation: state.rotation,
    });
    if (!state.hasCrop) {
      overlay.style.display = 'none';
      return;
    }

    state.rect = clampRectToMedia(rectFromCropRatios());
    syncCropRatiosFromRect();

    overlay.style.display = 'block';
    overlay.style.left = `${state.rect.x}px`;
    overlay.style.top = `${state.rect.y}px`;
    overlay.style.width = `${state.rect.w}px`;
    overlay.style.height = `${state.rect.h}px`;
    overlay.dataset.label = state.bucket.label;

    const src = sourceCropSize();
    const needsUpscale = src.w + 0.5 < state.bucket.w || src.h + 0.5 < state.bucket.h;
    overlay.classList.toggle('upscale-needed', needsUpscale);
    overlay.style.borderColor = needsUpscale ? 'var(--danger)' : 'var(--ok)';
  }

  function autoCrop() {
    const m = mediaBox();
    if (!m.w || !m.h) return;
    const rotated = state.rotation % 180 !== 0;
    const srcW = (rotated ? video.videoHeight : video.videoWidth) || m.w || 1;
    const srcH = (rotated ? video.videoWidth : video.videoHeight) || m.h || 1;
    const bucket = chooseNearestBucket(selectedCropBase(), srcW / Math.max(srcH, 1), srcW * srcH);
    const ratio = bucket.w / Math.max(bucket.h, 1);
    let w = m.w;
    let h = w / ratio;
    if (h > m.h) {
      h = m.h;
      w = h * ratio;
    }
    state.bucket = bucket;
    state.hasCrop = true;
    setCropRect({
      x: m.x + (m.w - w) / 2,
      y: m.y + (m.h - h) / 2,
      w,
      h,
    });
    draw();
  }

  function beginCreate(clientX, clientY) {
    state.dragMode = 'create';
    state.pointerStart = clampPointToMedia(clientX, clientY);
    state.rectStart = null;
    state.hasCrop = false;
    draw();
  }

  function applyCreate(clientX, clientY) {
    const p = clampPointToMedia(clientX, clientY);
    const rawRect = rectFromCorners(state.pointerStart.x, state.pointerStart.y, p.x, p.y);
    if (rawRect.w < 4 || rawRect.h < 4) {
      state.hasCrop = false;
      draw();
      return;
    }

    // anchor to the direction user dragged
    const horizontal = p.x >= state.pointerStart.x ? 'e' : 'w';
    const vertical = p.y >= state.pointerStart.y ? 's' : 'n';
    const anchorMode = vertical + horizontal;

    state.hasCrop = true
    setCropRect(snappedRectFromRaw(rawRect, anchorMode.toLowerCase()));
    draw();
  }

  function applyMove(clientX, clientY) {
    const p = stagePoint(clientX, clientY);
    const dx = p.x - state.pointerStart.x;
    const dy = p.y - state.pointerStart.y;
    setCropRect({
      x: state.rectStart.x + dx,
      y: state.rectStart.y + dy,
      w: state.rectStart.w,
      h: state.rectStart.h,
    });
    draw();
  }

  function applyResize(clientX, clientY, handle) {
    const p = clampPointToMedia(clientX, clientY);
    const rs = state.rectStart;
    let rawRect;
    if (handle === 'se') rawRect = rectFromCorners(rs.x, rs.y, p.x, p.y);
    else if (handle === 'sw') rawRect = rectFromCorners(rs.x + rs.w, rs.y, p.x, p.y);
    else if (handle === 'ne') rawRect = rectFromCorners(rs.x, rs.y + rs.h, p.x, p.y);
    else rawRect = rectFromCorners(rs.x + rs.w, rs.y + rs.h, p.x, p.y);

    if (rawRect.w < 4 || rawRect.h < 4) return;

    setCropRect(snappedRectFromRaw(rawRect, handle));
    draw();
  }

  function pointerDown(e) {
    if (beginMediaPan(e)) return;
    if (e.button !== 0) return;
    const handle = e.target.classList.contains('crop-handle')
      ? [...e.target.classList].find(c => ['nw','ne','sw','se'].includes(c))
      : null;

    if (handle) {
      if (!state.hasCrop) return;
      state.dragMode = handle;
      state.rectStart = { ...state.rect };
      state.pointerStart = stagePoint(e.clientX, e.clientY);
    } else if (e.target === overlay || overlay.contains(e.target)) {
      if (!state.hasCrop) return;
      state.dragMode = 'move';
      state.rectStart = { ...state.rect };
      state.pointerStart = stagePoint(e.clientX, e.clientY);
    } else if (e.target === stage || e.target === video) {
      beginCreate(e.clientX, e.clientY);
    } else {
      return;
    }

    e.preventDefault();
    e.stopPropagation();
    window.addEventListener('pointermove', pointerMove);
    window.addEventListener('pointerup', pointerUp, { once: true });
  }

  function pointerMove(e) {
    if (!state.dragMode) return;
    if (state.dragMode === 'create') applyCreate(e.clientX, e.clientY);
    else if (state.dragMode === 'move') applyMove(e.clientX, e.clientY);
    else applyResize(e.clientX, e.clientY, state.dragMode);
  }

  function pointerUp() {
    state.dragMode = null;
    window.removeEventListener('pointermove', pointerMove);
    draw();
  }

  stage.addEventListener('pointerdown', pointerDown);
  stage.addEventListener('auxclick', e => {
    if (e.button === 1) e.preventDefault();
  });
  overlay.addEventListener('pointerdown', pointerDown);

  function resnapToCropBase() {
      if (!state.hasCrop) return;
      updateBucketFromRect(state.rect);
      // snap current crop to new nearest bucket while keeping center
      const cx = state.rect.x + state.rect.w / 2;
      const cy = state.rect.y + state.rect.h / 2;
      const raw = { x: 0, y: 0, w: state.rect.w, h: state.rect.h };
      const snapped = snappedRectFromRaw(raw, 'se');
      setCropRect({ x: cx - snapped.w / 2, y: cy - snapped.h / 2, w: snapped.w, h: snapped.h });
      draw();
  }

  function visibleCropPayload() {
    const style = window.getComputedStyle(overlay);
    if (style.display === 'none' || style.visibility === 'hidden') return null;
    const m = mediaBox();
    if (!m.w || !m.h) return null;
    const left = parseFloat(overlay.style.left || '0') || 0;
    const top = parseFloat(overlay.style.top || '0') || 0;
    const width = parseFloat(overlay.style.width || '0') || overlay.offsetWidth || 0;
    const height = parseFloat(overlay.style.height || '0') || overlay.offsetHeight || 0;
    if (width < 4 || height < 4) return null;
    const cropSize = cropSizeFromOverlayLabel(overlay.dataset.label || state.bucket.label);
    return {
      has_crop: true,
      crop_w: cropSize.w,
      crop_h: cropSize.h,
      crop_x_ratio: clamp((left - m.x) / Math.max(m.w, 1), 0, 1),
      crop_y_ratio: clamp((top - m.y) / Math.max(m.h, 1), 0, 1),
      crop_rect_w_ratio: clamp(width / Math.max(m.w, 1), 0.0001, 1),
      crop_rect_h_ratio: clamp(height / Math.max(m.h, 1), 0.0001, 1),
    };
  }

  function beginMediaPan(e) {
    if (e.button !== 1) return false;
    const box = mediaBox();
    const canPan = box.w > stage.clientWidth + 1 || box.h > stage.clientHeight + 1;
    if (!canPan) return false;
    e.preventDefault();
    e.stopPropagation();
    const start = { x: e.clientX, y: e.clientY };
    const startPan = mediaPan();
    stage.classList.add('panning');
    const move = moveEvent => {
      moveEvent.preventDefault();
      setMediaPan(startPan.x + moveEvent.clientX - start.x, startPan.y + moveEvent.clientY - start.y);
      draw();
    };
    const stop = () => {
      stage.classList.remove('panning');
      window.removeEventListener('pointermove', move);
      window.removeEventListener('pointerup', stop);
      window.removeEventListener('pointercancel', stop);
    };
    window.addEventListener('pointermove', move, { passive: false });
    window.addEventListener('pointerup', stop, { once: true });
    window.addEventListener('pointercancel', stop, { once: true });
    return true;
  }

  const stageResizeObserver = window.ResizeObserver
    ? new ResizeObserver(() => draw())
    : null;
  stageResizeObserver?.observe(stage);

  video.addEventListener('loadedmetadata', () => {
    setPreviewVolume(readStoredNumber('video_prep_preview_volume', 100, 0, 100));
    updateFrameReadout();
    updateSeekBar();
    updateTrimTimeline();
    draw();
  });
  video.addEventListener('timeupdate', () => { updateFrameReadout(); updateSeekBar(); });
  video.addEventListener('seeked', () => { updateFrameReadout(); updateSeekBar(); });

  if (seek) {
    const seekToValue = () => {
      seekToFrame(parseInt(seek.value || '0', 10) || 0);
      updateFrameReadout();
    };
    seek.addEventListener('input', seekToValue);
    seek.addEventListener('change', seekToValue);
  }

  if (startFrameInput) {
    startFrameInput.addEventListener('input', () => {
      const { start } = trimFrames();
      startFrameInput.value = String(start);
      updateTrimTimeline();
      seekToFrame(start);
    });
  }
  if (endFrameInput) {
    endFrameInput.addEventListener('input', () => {
      const { end } = trimFrames();
      endFrameInput.value = String(end);
      updateTrimTimeline();
      seekToFrame(end);
    });
  }
  window.addEventListener('video-settings-updated', updateTrimTimeline);
  updateTrimTimeline();

  return {
    redraw() {
      draw();
    },
    autoCrop,
    resnapToCropBase,
    flipHorizontal() {
      state.flipHorizontal = !state.flipHorizontal;
      draw();
    },
    flipVertical() {
      state.flipVertical = !state.flipVertical;
      draw();
    },
    rotate90() {
      state.rotation = (state.rotation + 90) % 360;
      if (state.hasCrop) {
        state.rect = clampRectToMedia(rectFromCropRatios());
        syncCropRatiosFromRect();
      }
      draw();
    },
    transformState() {
      return {
        flipHorizontal: state.flipHorizontal,
        flipVertical: state.flipVertical,
        rotation: state.rotation,
      };
    },
    markVideoSaved(pair = {}) {
      state.hasCrop = false;
      state.rect = { x: 0, y: 0, w: 0, h: 0 };
      state.cropRatios = { x: 0, y: 0, w: 1, h: 1 };
      state.flipHorizontal = false;
      state.flipVertical = false;
      state.rotation = 0;
      stage.dataset.zoom = '1';
      state.fps = parseFloat(pair.fps || card.dataset.fps || '24') || 24;
      state.frames = parseInt(pair.frames || card.dataset.frames || '0', 10) || 0;
      if (startFrameInput) {
        startFrameInput.max = String(state.frames);
        startFrameInput.value = '0';
      }
      if (endFrameInput) {
        endFrameInput.max = String(state.frames);
        endFrameInput.value = String(state.frames);
      }
      const mute = card.querySelector('.mute-export');
      if (mute) mute.checked = false;
      setMediaPan(0, 0);
      overlay.style.display = 'none';
      overlay.dataset.label = '';
      updateTrimTimeline();
      updateFrameReadout();
      updateSeekBar();
      draw();
    },
    clearCropSelection() {
      state.hasCrop = false;
      state.rect = { x: 0, y: 0, w: 0, h: 0 };
      state.cropRatios = { x: 0, y: 0, w: 1, h: 1 };
      overlay.style.display = 'none';
      overlay.dataset.label = '';
      draw();
    },
    zoomIn() {
      applyVideoZoom(mediaZoom() * 1.25);
    },
    zoomOut() {
      applyVideoZoom(mediaZoom() / 1.25);
    },
    zoomDefault() {
      applyVideoZoom(1);
    },
    zoomActual() {
      setActualVideoZoom();
    },
    hasVideoEditChanges() {
      const { start, end, maxFrame, maxEndFrame, includeEndFrame } = trimFrames();
      const fullEnd = includeEndFrame ? maxEndFrame : maxFrame;
      return (
        state.hasCrop ||
        state.flipHorizontal ||
        state.flipVertical ||
        state.rotation !== 0 ||
        start !== 0 ||
        end !== fullEnd ||
        !!card.querySelector('.mute-export')?.checked
      );
    },
    hasVisibleCrop() {
      return !!visibleCropPayload();
    },
    getVisibleCropPayload() {
      return visibleCropPayload();
    },
    getCropPayload() {
      if (!state.hasCrop) {
        const rotated = state.rotation % 180 !== 0;
        const sourceWRaw = video.videoWidth || parseFloat(card.dataset.width || '0') || 1;
        const sourceHRaw = video.videoHeight || parseFloat(card.dataset.height || '0') || 1;
        const sourceW = rotated ? sourceHRaw : sourceWRaw;
        const sourceH = rotated ? sourceWRaw : sourceHRaw;
        const bucket = chooseNearestBucket(selectedCropBase(), sourceW / Math.max(sourceH, 1), sourceW * sourceH);
        return {
          has_crop: false,
          crop_w: bucket.w,
          crop_h: bucket.h,
          crop_x_ratio: 0,
          crop_y_ratio: 0,
          crop_rect_w_ratio: 1,
          crop_rect_h_ratio: 1,
        };
      }

      const ratios = state.cropRatios;
      return {
        has_crop: true,
        crop_w: state.bucket.w,
        crop_h: state.bucket.h,
        crop_x_ratio: ratios.x,
        crop_y_ratio: ratios.y,
        crop_rect_w_ratio: ratios.w,
        crop_rect_h_ratio: ratios.h,
      };
    }
  };
}

function createFallbackCropEditor(card, error) {
  const name = card?.dataset?.name || 'video';
  console.error(`Video card editor failed for ${name}:`, error);
  setVideoStatusbarMessage(`Video controls partially disabled for ${name}.`);
  return {
    redraw() {},
    autoCrop() {},
    resnapToCropBase() {},
    flipHorizontal() {
      const transform = directVideoTransformState(card);
      transform.flipHorizontal = !transform.flipHorizontal;
      setDirectVideoTransform(card, transform);
    },
    flipVertical() {
      const transform = directVideoTransformState(card);
      transform.flipVertical = !transform.flipVertical;
      setDirectVideoTransform(card, transform);
    },
    rotate90() {
      const transform = directVideoTransformState(card);
      transform.rotation = (transform.rotation + 90) % 360;
      setDirectVideoTransform(card, transform);
    },
    transformState() {
      return directVideoTransformState(card);
    },
    markVideoSaved() {
      setDirectVideoTransform(card, { flipHorizontal: false, flipVertical: false, rotation: 0 });
      setDirectVideoZoom(card, 1, false);
      const overlay = card?.querySelector('.crop-overlay');
      if (overlay) {
        overlay.style.display = 'none';
        overlay.dataset.label = '';
      }
      syncDirectTransformButtons(card);
    },
    clearCropSelection() {
      const overlay = card?.querySelector('.crop-overlay');
      if (overlay) {
        overlay.style.display = 'none';
        overlay.dataset.label = '';
      }
    },
    zoomIn() {
      const stage = card?.querySelector('.video-stage');
      const current = clamp(parseFloat(stage?.dataset.zoom || '1') || 1, 0.25, 8);
      setDirectVideoZoom(card, current * 1.25);
    },
    zoomOut() {
      const stage = card?.querySelector('.video-stage');
      const current = clamp(parseFloat(stage?.dataset.zoom || '1') || 1, 0.25, 8);
      setDirectVideoZoom(card, current / 1.25);
    },
    zoomDefault() {
      setDirectVideoZoom(card, 1);
    },
    zoomActual() {
      directVideoZoomActual(card);
    },
    hasVideoEditChanges() {
      const transform = directVideoTransformState(card);
      return (
        !!transform.flipHorizontal ||
        !!transform.flipVertical ||
        !!transform.rotation ||
        !!card?.querySelector('.mute-export')?.checked
      );
    },
    hasVisibleCrop() {
      return !!cropPayloadFromVisibleOverlay(card);
    },
    getVisibleCropPayload() {
      return cropPayloadFromVisibleOverlay(card);
    },
    getCropPayload() {
      return {
        has_crop: false,
        crop_w: Number(selectedCropBase()) || 1024,
        crop_h: Number(selectedCropBase()) || 1024,
        crop_x_ratio: 0,
        crop_y_ratio: 0,
        crop_rect_w_ratio: 1,
        crop_rect_h_ratio: 1,
      };
    },
  };
}

const sliceState = {
  card: null,
  name: '',
  fps: 24,
  frames: 0,
  cuts: [],
  keep: [],
};

function sliceElements() {
  return {
    modal: document.getElementById('sliceModal'),
    title: document.getElementById('sliceModalTitle'),
    video: document.getElementById('sliceVideo'),
    seek: document.getElementById('sliceSeek'),
    timeline: document.getElementById('sliceTimeline'),
    list: document.getElementById('sliceList'),
    status: document.getElementById('sliceStatusText'),
    saveStatus: document.getElementById('sliceSaveStatusText'),
    playBtn: document.getElementById('slicePlayBtn'),
    saveBtn: document.getElementById('sliceSaveBtn'),
  };
}

function sliceCurrentFrame() {
  const { video } = sliceElements();
  return clamp(Math.round((video?.currentTime || 0) * sliceState.fps), 0, sliceState.frames);
}

function sliceFrameToTime(frame) {
  return clamp(frame, 0, sliceState.frames) / Math.max(sliceState.fps, 0.0001);
}

function sliceSegments() {
  const points = [0, ...sliceState.cuts, sliceState.frames].filter((value, index, arr) => index === 0 || value > arr[index - 1]);
  const segments = [];
  for (let i = 0; i < points.length - 1; i += 1) {
    segments.push({
      start: points[i],
      end: points[i + 1],
      keep: sliceState.keep[i] !== false,
    });
  }
  sliceState.keep = segments.map((segment, index) => sliceState.keep[index] !== false);
  return segments;
}

function syncSlicePlayButton() {
  const { video, playBtn } = sliceElements();
  if (!playBtn || !video) return;
  const iconClass = video.paused ? 'media-play' : 'media-pause';
  playBtn.innerHTML = `<span class="media-icon ${iconClass}" aria-hidden="true"></span>`;
}

function updateSliceStatus() {
  const { seek, status, timeline } = sliceElements();
  const frame = sliceCurrentFrame();
  if (seek) {
    seek.max = String(sliceState.frames);
    seek.value = String(frame);
  }
  if (status) status.textContent = `Frame: ${frame} / ${sliceState.frames}`;
  const playhead = timeline?.querySelector('.slice-playhead');
  if (playhead) playhead.style.left = `${(frame / Math.max(sliceState.frames, 1)) * 100}%`;
}

function renderSliceEditor() {
  const { timeline, list, saveStatus } = sliceElements();
  const segments = sliceSegments();
  const total = Math.max(sliceState.frames, 1);
  if (timeline) {
    timeline.innerHTML = '';
    segments.forEach((segment, index) => {
      const div = document.createElement('div');
      div.className = `slice-segment${segment.keep ? '' : ' remove'}`;
      div.dataset.segmentIndex = String(index);
      div.style.left = `${(segment.start / total) * 100}%`;
      div.style.width = `${((segment.end - segment.start) / total) * 100}%`;
      const label = document.createElement('span');
      label.textContent = segment.keep ? 'Keep' : 'Remove';
      div.appendChild(label);
      timeline.appendChild(div);
    });
    sliceState.cuts.forEach(cut => {
      const marker = document.createElement('div');
      marker.className = 'slice-cut';
      marker.style.left = `${(cut / total) * 100}%`;
      timeline.appendChild(marker);
    });
    const playhead = document.createElement('div');
    playhead.className = 'slice-playhead';
    timeline.appendChild(playhead);
  }
  if (list) {
    list.innerHTML = '';
    segments.forEach((segment, index) => {
      const row = document.createElement('div');
      row.className = 'slice-row';
      const state = document.createElement('strong');
      state.textContent = segment.keep ? 'Keep' : 'Remove';
      const frames = document.createElement('span');
      frames.textContent = `${segment.start} - ${Math.max(segment.start, segment.end - 1)} (${segment.end - segment.start} frames)`;
      const toggle = document.createElement('button');
      toggle.type = 'button';
      toggle.dataset.segmentIndex = String(index);
      toggle.textContent = segment.keep ? 'Remove' : 'Keep';
      row.append(state, frames, toggle);
      list.appendChild(row);
    });
  }
  const keptCount = segments.filter(segment => segment.keep).length;
  if (saveStatus) saveStatus.textContent = `${keptCount} kept segment(s), ${segments.length - keptCount} removed.`;
  updateSliceStatus();
}

function toggleSliceSegment(index) {
  const segments = sliceSegments();
  if (index < 0 || index >= segments.length) return;
  sliceState.keep[index] = segments[index].keep === false;
  renderSliceEditor();
}

function openSliceEditor(card) {
  const { modal, title, video, seek, saveStatus } = sliceElements();
  sliceState.card = card;
  sliceState.name = card.dataset.name || '';
  sliceState.fps = parseFloat(card.dataset.fps || '24') || 24;
  sliceState.frames = parseInt(card.dataset.frames || '0', 10) || 0;
  sliceState.cuts = [];
  sliceState.keep = [true];
  if (title) title.textContent = `Slice: ${sliceState.name}`;
  if (saveStatus) saveStatus.textContent = 'Select cut points, then toggle segments to Keep or Remove.';
  const { saveBtn } = sliceElements();
  if (saveBtn) saveBtn.disabled = false;
  if (seek) {
    seek.min = '0';
    seek.max = String(sliceState.frames);
    seek.step = '1';
    seek.value = '0';
  }
  if (video) {
    video.pause();
    const cacheToken = sliceState.card?.dataset.videoVersion || Date.now();
    video.src = `/video/${encodeURIComponent(sliceState.name)}?v=${encodeURIComponent(cacheToken)}`;
    video.currentTime = 0;
    video.load();
  }
  modal?.classList.add('open');
  renderSliceEditor();
  syncSlicePlayButton();
}

function closeSliceEditor() {
  const { modal, video } = sliceElements();
  if (video) {
    video.pause();
    video.removeAttribute('src');
    video.load();
  }
  modal?.classList.remove('open');
}

function addSliceCut() {
  const frame = sliceCurrentFrame();
  if (frame <= 0 || frame >= sliceState.frames) return;
  if (sliceState.cuts.some(cut => Math.abs(cut - frame) <= 0)) return;
  sliceState.cuts.push(frame);
  sliceState.cuts.sort((a, b) => a - b);
  renderSliceEditor();
}

function removeNearestSliceCut() {
  if (!sliceState.cuts.length) return;
  const frame = sliceCurrentFrame();
  let bestIndex = 0;
  let bestDistance = Infinity;
  sliceState.cuts.forEach((cut, index) => {
    const distance = Math.abs(cut - frame);
    if (distance < bestDistance) {
      bestDistance = distance;
      bestIndex = index;
    }
  });
  sliceState.cuts.splice(bestIndex, 1);
  renderSliceEditor();
}

function toggleSlicePlayback() {
  const { video } = sliceElements();
  if (!video) return;
  if (video.paused) video.play().catch(() => {});
  else video.pause();
  syncSlicePlayButton();
}

function stepSliceFrame(direction) {
  const { video } = sliceElements();
  if (!video) return;
  const current = sliceCurrentFrame();
  const nextFrame = clamp(current + direction, 0, sliceState.frames);
  video.pause();
  video.currentTime = sliceFrameToTime(nextFrame);
  syncSlicePlayButton();
  updateSliceStatus();
}

document.getElementById('closeSliceModalBtn')?.addEventListener('click', closeSliceEditor);
document.getElementById('sliceCancelBtn')?.addEventListener('click', closeSliceEditor);
document.getElementById('slicePlayBtn')?.addEventListener('click', () => {
  toggleSlicePlayback();
});
document.getElementById('sliceStopBtn')?.addEventListener('click', () => {
  const { video } = sliceElements();
  if (!video) return;
  video.pause();
  video.currentTime = 0;
  syncSlicePlayButton();
  updateSliceStatus();
});
document.getElementById('sliceAddCutBtn')?.addEventListener('click', addSliceCut);
document.getElementById('sliceRemoveCutBtn')?.addEventListener('click', removeNearestSliceCut);
document.addEventListener('keydown', event => {
  const { modal } = sliceElements();
  if (!modal?.classList.contains('open')) return;
  if (event.ctrlKey || event.altKey || event.metaKey) return;
  const target = event.target;
  const isSliceSeek = target?.id === 'sliceSeek';
  if (target?.closest?.('textarea, select, [contenteditable="true"]')) return;
  if (target?.closest?.('input') && !isSliceSeek) return;
  if (event.key.toLowerCase() === 'x') {
    event.preventDefault();
    addSliceCut();
  } else if (event.key === 'Delete') {
    event.preventDefault();
    removeNearestSliceCut();
  } else if (event.key === ' ' || event.code === 'Space' || event.key === 'Spacebar') {
    event.preventDefault();
    toggleSlicePlayback();
  } else if (event.key === 'ArrowLeft') {
    event.preventDefault();
    stepSliceFrame(-1);
  } else if (event.key === 'ArrowRight') {
    event.preventDefault();
    stepSliceFrame(1);
  }
});
document.getElementById('sliceSeek')?.addEventListener('input', event => {
  const { video } = sliceElements();
  if (!video) return;
  video.pause();
  video.currentTime = sliceFrameToTime(parseInt(event.target.value || '0', 10) || 0);
  syncSlicePlayButton();
  updateSliceStatus();
});
document.getElementById('sliceVideo')?.addEventListener('timeupdate', updateSliceStatus);
document.getElementById('sliceVideo')?.addEventListener('play', syncSlicePlayButton);
document.getElementById('sliceVideo')?.addEventListener('pause', syncSlicePlayButton);
document.getElementById('sliceVideo')?.addEventListener('ended', syncSlicePlayButton);
document.getElementById('sliceTimeline')?.addEventListener('click', event => {
  const segment = event.target.closest('.slice-segment');
  if (segment) {
    toggleSliceSegment(parseInt(segment.dataset.segmentIndex || '-1', 10));
    return;
  }
  const timeline = event.currentTarget;
  const rect = timeline.getBoundingClientRect();
  const ratio = clamp((event.clientX - rect.left) / Math.max(rect.width, 1), 0, 1);
  const { video } = sliceElements();
  if (video) video.currentTime = sliceFrameToTime(Math.round(ratio * sliceState.frames));
  updateSliceStatus();
});
document.getElementById('sliceList')?.addEventListener('click', event => {
  const button = event.target.closest('[data-segment-index]');
  if (!button) return;
  toggleSliceSegment(parseInt(button.dataset.segmentIndex || '-1', 10));
});
document.getElementById('sliceSaveBtn')?.addEventListener('click', async () => {
  const { saveBtn, saveStatus } = sliceElements();
  const segments = sliceSegments();
  if (!segments.some(segment => segment.keep)) {
    if (saveStatus) saveStatus.textContent = 'No kept segments selected.';
    return;
  }
  if (saveBtn) saveBtn.disabled = true;
  if (saveStatus) saveStatus.textContent = 'Saving slices...';
  showAppBusy('Saving slices...', 'Slice');
  try {
    const settings = getVideoSettings();
    const res = await fetch('/slice_video', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name: sliceState.name,
        segments,
        output_format: settings.videoFormat,
        delete_source: !settings.sliceKeepSource,
        include_end_frame: settings.includeEndFrameWhenTrimming,
      }),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Slice save failed');
    hideAppBusy();
    location.reload();
  } catch (err) {
    hideAppBusy();
    if (saveStatus) saveStatus.textContent = err?.message || 'Slice save failed';
    if (saveBtn) saveBtn.disabled = false;
  }
});

const selectedVideoCards = new Set();
let copiedVideoNames = [];
const videoStatusbarFolder = document.querySelector('.statusbar-folder');
const videoStatusbarDefaultFolderText = videoStatusbarFolder?.textContent.trim() || '';

function isTypingTarget(target = document.activeElement) {
  return !!target?.closest?.('input, textarea, select, [contenteditable="true"]');
}

function syncVideoCardSelection() {
  const selectedCount = selectedVideoCardList().length;
  document.querySelectorAll('.card').forEach(card => {
    card.classList.toggle('selected', selectedVideoCards.has(card));
  });
  if (videoStatusbarFolder) {
    videoStatusbarFolder.textContent = selectedCount
      ? `${videoStatusbarDefaultFolderText} ${selectedCount} selected.`
      : videoStatusbarDefaultFolderText;
  }
}

function clearVideoCardSelection() {
  selectedVideoCards.clear();
  syncVideoCardSelection();
}

function selectVideoCard(card, additive = false) {
  if (!card) return;
  if (!additive) selectedVideoCards.clear();
  if (additive && selectedVideoCards.has(card)) selectedVideoCards.delete(card);
  else selectedVideoCards.add(card);
  syncVideoCardSelection();
}

function selectedVideoCardList() {
  return Array.from(selectedVideoCards).filter(card => card.isConnected);
}

function selectAllVideoCards() {
  selectedVideoCards.clear();
  document.querySelectorAll('.card').forEach(card => selectedVideoCards.add(card));
  window.getSelection?.()?.removeAllRanges?.();
  syncVideoCardSelection();
}

async function deleteVideoCards(cards) {
  const selected = cards.filter(card => card?.isConnected);
  if (!selected.length) return;
  const ok = await appConfirm(`Delete ${selected.length} selected video/caption pair(s)?`);
  if (!ok) return;
  const anchor = cardViewportAnchorAfterRemoval(selected[selected.length - 1]);
  showAppBusy(`Deleting ${selected.length} video pair(s)...`, 'Delete');
  for (let i = 0; i < selected.length; i += 1) {
    const name = selected[i].dataset.name;
    if (appDialogMessage) appDialogMessage.textContent = `Deleting ${i + 1}/${selected.length}: ${name}`;
    const res = await fetch('/delete_pair', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name, skip_refresh: true}),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Delete failed');
  }
  if (appDialogMessage) appDialogMessage.textContent = 'Refreshing video list...';
  await refreshVideoPairsBeforeReload();
  setVideoStatusbarMessage(`Deleted ${selected.length} video/caption pair(s).`);
  reloadPreservingViewport(anchor);
}

async function refreshVideoPairsBeforeReload() {
  const res = await fetch('/refresh_pairs', {method: 'POST'});
  const data = await res.json();
  if (!res.ok || !data.ok) throw new Error(data.error || 'Refresh failed');
}

document.querySelectorAll('.card').forEach(card => {
  const video = card.querySelector('video');
  const playToggleBtn = card.querySelector('.play-toggle-btn');
  const stopBtn = card.querySelector('.stop-btn');
  const sliceBtn = card.querySelector('.slice-btn');
  const flipHBtn = card.querySelector('.flip-h-btn');
  const flipVBtn = card.querySelector('.flip-v-btn');
  const rotate90Btn = card.querySelector('.rotate-90-btn');
  const caption = card.querySelector('.caption');
  const initialCaptionValue = caption ? caption.value : '';
  const saveCombinedBtn = card.querySelector('.save-combined-btn');
  const cloneBtn = card.querySelector('.clone-btn');
  const deleteBtn = card.querySelector('.delete-btn');
  const startFrame = card.querySelector('.start-frame');
  const endFrame = card.querySelector('.end-frame');
  const nameEl = card.querySelector('.name');
  const cardHead = card.querySelector('.card-head');
  const loadingEl = card.querySelector('.video-loading');
  const videoLoadingGraceStartedAt = performance.now();

  if (cardHead) {
    cardHead.addEventListener('click', event => {
      if (event.target.closest('button, input, textarea, select, a, [contenteditable="true"]')) return;
      selectVideoCard(card, event.ctrlKey || event.metaKey);
    });
  }

  function syncPlayLabel() {
    if (!playToggleBtn) return;
    const iconClass = video.paused ? 'media-play' : 'media-pause';
    playToggleBtn.innerHTML = `<span class="media-icon ${iconClass}" aria-hidden="true"></span>`;
  }

  function syncVideoLoading(forceVisible = null) {
    if (!loadingEl) return;
    try {
      const duration = Number(video.duration);
      const hasMetadata = video.readyState >= 1 || !!video.videoWidth || Number.isFinite(duration);
      const activelyLoading = Number(video.networkState) === 2;
      const loadingGraceActive = (performance.now() - videoLoadingGraceStartedAt) < 1800;
      const userWaiting = !video.paused || video.seeking;
      const visible = !hasMetadata && activelyLoading && (loadingGraceActive || userWaiting || (!!forceVisible && userWaiting));
      loadingEl.classList.toggle('show', visible);
    } catch (err) {
      loadingEl.classList.remove('show');
    }
  }

  if (playToggleBtn) playToggleBtn.addEventListener('click', () => {
    try {
      if (video.paused) video.play().catch(err => {
        setVideoStatusbarMessage(`Play failed for ${card.dataset.name}.`);
        console.error('Video play failed:', err);
      });
      else video.pause();
    } catch (err) {
      setVideoStatusbarMessage(`Play failed for ${card.dataset.name}.`);
      console.error('Video play failed:', err);
    }
  });
  if (stopBtn) stopBtn.addEventListener('click', () => {
    try {
      video.pause();
      video.currentTime = 0;
      syncPlayLabel();
    } catch (err) {
      setVideoStatusbarMessage(`Stop failed for ${card.dataset.name}.`);
      console.error('Video stop failed:', err);
    }
  });
  if (sliceBtn) sliceBtn.addEventListener('click', () => {
    try {
      video.pause();
      syncPlayLabel();
      openSliceEditor(card);
    } catch (err) {
      setVideoStatusbarMessage(`Slice failed for ${card.dataset.name}.`);
      console.error('Slice failed:', err);
    }
  });
  video.addEventListener('play', syncPlayLabel);
  video.addEventListener('pause', syncPlayLabel);
  video.addEventListener('ended', syncPlayLabel);
  ['loadstart', 'waiting', 'stalled', 'emptied'].forEach(eventName => {
    video.addEventListener(eventName, () => syncVideoLoading(true));
  });
  ['durationchange', 'loadedmetadata', 'loadeddata', 'canplay', 'playing', 'timeupdate'].forEach(eventName => {
    video.addEventListener(eventName, () => syncVideoLoading(false));
  });
  video.addEventListener('error', () => syncVideoLoading(false));
  syncPlayLabel();
  syncVideoLoading();
  requestAnimationFrame(syncVideoLoading);
  setTimeout(syncVideoLoading, 250);
  setTimeout(syncVideoLoading, 1000);
  setTimeout(syncVideoLoading, 2000);
  setTimeout(syncVideoLoading, 3000);

  let cropEditor;
  try {
    cropEditor = createCropEditor(card);
  } catch (err) {
    cropEditor = createFallbackCropEditor(card, err);
  }
  cropEditors.push(cropEditor);

  function syncTransformButtons() {
    const transform = cropEditor.transformState();
    flipHBtn?.classList.toggle('active', !!transform.flipHorizontal);
    flipVBtn?.classList.toggle('active', !!transform.flipVertical);
    rotate90Btn?.classList.toggle('active', !!transform.rotation);
    if (rotate90Btn) {
      rotate90Btn.textContent = transform.rotation ? `${transform.rotation}°` : '90°';
    }
  }

  flipHBtn?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    cropEditor.flipHorizontal();
    syncTransformButtons();
  });
  flipVBtn?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    cropEditor.flipVertical();
    syncTransformButtons();
  });
  rotate90Btn?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    cropEditor.rotate90();
    syncTransformButtons();
  });
  syncTransformButtons();

  card.querySelector('.zoom-in-btn')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    cropEditor.zoomIn();
  });
  card.querySelector('.zoom-out-btn')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    cropEditor.zoomOut();
  });
  card.querySelector('.zoom-default-btn')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    cropEditor.zoomDefault();
  });
  card.querySelector('.zoom-actual-btn')?.addEventListener('click', event => {
    event.preventDefault();
    event.stopPropagation();
    cropEditor.zoomActual();
  });

  function syncCaptionDirty() {
    if (!caption) return;
    const dirty = caption.value !== (caption.dataset.original ?? '');
    caption.classList.toggle('unsaved-caption', dirty);
    updateCaptionStats(caption);
    updateSaveAllButtonState();
  }

  function applySavedPair(pair = {}) {
    const nextName = pair.name || card.dataset.name;
    const cacheToken = pair.cache_buster || Date.now();
    card.dataset.name = nextName;
    card.dataset.fps = String(pair.fps || card.dataset.fps || '24');
    card.dataset.frames = String(pair.frames || card.dataset.frames || '0');
    card.dataset.videoVersion = String(cacheToken);
    if (nameEl) {
      nameEl.dataset.name = nextName;
      nameEl.textContent = nextName;
    }
    const meta = card.querySelector('.meta');
    if (meta && pair.name) {
      meta.innerHTML = `
        <span>${htmlEscape(pair.width)}x${htmlEscape(pair.height)}</span>
        <span>${Number(pair.duration || 0).toFixed(2)} s</span>
        <span>${Number(pair.fps || 0).toFixed(3)} fps</span>
        <span>${Number(pair.frames || 0)} frames</span>
        <span>${pair.has_audio ? 'audio' : 'silent'}</span>
      `;
    }
    video.pause();
    syncPlayLabel();
    syncVideoLoading(true);
    video.src = `/video/${encodeURIComponent(nextName)}?v=${encodeURIComponent(cacheToken)}`;
    video.load();
    cropEditor.markVideoSaved(pair);
    syncTransformButtons();
    updateSaveAllButtonState();
  }

  if (caption) {
    caption.dataset.original = initialCaptionValue;
    caption.addEventListener('input', syncCaptionDirty);
    syncCaptionDirty();
  }

  function hasCaptionChanges() {
    return !!caption && caption.value !== (caption.dataset.original ?? '');
  }

  async function saveCaptionOnly({skipRefresh = false} = {}) {
    const res = await fetch('/save_caption', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: card.dataset.name, caption: caption.value, skip_refresh: skipRefresh})
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Caption save failed');
    caption.dataset.original = caption.value;
    syncCaptionDirty();
    return data;
  }

  async function saveVideoOnly({skipRefresh = false} = {}) {
    let crop = cropEditor.getCropPayload();
    if (!crop?.has_crop) {
      crop = cropEditor.getVisibleCropPayload?.() || ensureVisibleCropPayload(card, crop);
    }
    const transform = cropEditor.transformState();
    const settings = getVideoSettings();
    const res = await fetch('/save_edit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name: card.dataset.name,
        has_crop: !!crop.has_crop,
        crop_w: crop.crop_w,
        crop_h: crop.crop_h,
        crop_x_ratio: crop.crop_x_ratio,
        crop_y_ratio: crop.crop_y_ratio,
        crop_rect_w_ratio: crop.crop_rect_w_ratio,
        crop_rect_h_ratio: crop.crop_rect_h_ratio,
        start_frame: parseInt(startFrame.value || '0', 10),
        end_frame: parseInt(endFrame.value || '0', 10),
        mute: card.querySelector('.mute-export').checked,
        flip_horizontal: !!transform.flipHorizontal,
        flip_vertical: !!transform.flipVertical,
        rotate_degrees: transform.rotation,
        final_compress: true,
        final_crf: settings.saveCrf,
        final_preset: settings.savePreset,
        final_fps: settings.saveFps,
        output_format: settings.videoFormat,
        include_end_frame: settings.includeEndFrameWhenTrimming,
        skip_refresh: skipRefresh
      })
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Save failed');
    if (data.name && data.name !== card.dataset.name) {
      card.dataset.name = data.name;
      if (nameEl) {
        nameEl.dataset.name = data.name;
        nameEl.textContent = data.name;
      }
    }
    return data;
  }

  const cardController = {
    card,
    get name() {
      return card.dataset.name || '';
    },
    hasVideoChanges() {
      return cropEditor.hasVideoEditChanges() || !!cropEditor.hasVisibleCrop?.() || !!cropPayloadFromVisibleOverlay(card);
    },
    hasCaptionChanges,
    async saveVideo(options = {}) {
      return saveVideoOnly(options);
    },
    async saveCaption(options = {}) {
      return saveCaptionOnly(options);
    },
    applySavedPair,
    clearCropSelection() {
      cropEditor.clearCropSelection?.();
    },
    applyCaptionFromDisk(text, preserveDirty = true) {
      if (!caption) return;
      if (preserveDirty && hasCaptionChanges()) return;
      const latest = String(text ?? '');
      if (caption.value === latest && caption.dataset.original === latest) return;
      caption.value = latest;
      caption.dataset.original = latest;
      syncCaptionDirty();
    },
    setSaving(saving) {
      if (saveCombinedBtn) saveCombinedBtn.disabled = !!saving;
    },
  };
  videoCardControllers.push(cardController);
  updateSaveAllButtonState();

  if (cloneBtn) cloneBtn.addEventListener('click', async () => {
    showAppBusy('Cloning video...', 'Clone');
    try {
      const res = await fetch('/clone_pair', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({name: card.dataset.name})
      });
      const data = await res.json();
      if (!res.ok || !data.ok) throw new Error(data.error || 'Clone failed');
      location.reload();
    } catch (err) {
      hideAppBusy();
      await appAlert(err?.message || 'Clone failed');
    }
  });

  if (deleteBtn) deleteBtn.addEventListener('click', async () => {
    const selectedForDelete = selectedVideoCards.has(card) ? selectedVideoCardList() : [card];
    try {
      await deleteVideoCards(selectedForDelete);
    } catch (err) {
      hideAppBusy();
      await appAlert(err?.message || 'Delete failed');
    }
  });

  if (saveCombinedBtn) saveCombinedBtn.addEventListener('click', async () => {
    const videoChanged = cardController.hasVideoChanges();
    const captionChanged = cardController.hasCaptionChanges();
    if (!videoChanged && !captionChanged) {
      await appAlert('No changes to save.');
      return;
    }
    if (videoChanged) {
      const ok = await appConfirm(`Save edits to ${card.dataset.name}?\n\nThe original video file will be replaced.`);
      if (!ok) return;
    }
    saveCombinedBtn.disabled = true;
    showAppBusy(videoChanged ? 'Saving video...' : 'Saving caption...', 'Save');
    try {
      let videoSaveData = null;
      if (videoChanged) videoSaveData = await cardController.saveVideo();
      if (videoChanged && videoSaveData?.pair) {
        cardController.applySavedPair(videoSaveData.pair);
      } else if (videoChanged) {
        cardController.clearCropSelection();
      }
      if (captionChanged) await cardController.saveCaption();
      hideAppBusy();
      setVideoStatusbarMessage(`Saved ${card.dataset.name}.`);
      saveCombinedBtn.disabled = false;
    } catch (err) {
      hideAppBusy();
      await appAlert(err?.message || 'Save failed');
      saveCombinedBtn.disabled = false;
    }
  });

  if (nameEl) nameEl.addEventListener('dblclick', () => {
    const oldName = card.dataset.name;
    const stem = oldName.replace(/\.[^.]+$/, '');
    nameEl.classList.add('editing');
    nameEl.contentEditable = 'true';
    nameEl.textContent = stem;
    nameEl.focus();
    document.execCommand && document.execCommand('selectAll', false, null);

    const finish = async (commit) => {
      nameEl.contentEditable = 'false';
      nameEl.classList.remove('editing');
      if (!commit) {
        nameEl.textContent = oldName;
        return;
      }
      const newStem = nameEl.textContent.trim();
      showAppBusy('Renaming video...', 'Rename');
      try {
        const res = await fetch('/rename_pair', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({old_name: oldName, new_stem: newStem})
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || 'Rename failed');
        location.reload();
      } catch (err) {
        hideAppBusy();
        await appAlert(err?.message || 'Rename failed');
        nameEl.textContent = oldName;
      }
    };

    const onKey = (e) => {
      if (e.key === 'Enter') { e.preventDefault(); cleanup(); finish(true); }
      if (e.key === 'Escape') { e.preventDefault(); cleanup(); finish(false); }
    };
    const onBlur = () => { cleanup(); finish(true); };
    function cleanup() {
      nameEl.removeEventListener('keydown', onKey);
      nameEl.removeEventListener('blur', onBlur);
    }
    nameEl.addEventListener('keydown', onKey);
    nameEl.addEventListener('blur', onBlur);
  });
});

let videoDragDepth = 0;
document.addEventListener('dragenter', event => {
  if (!hasVideoDrag(event)) return;
  videoDragDepth += 1;
  dropPasteOverlay?.classList.add('show');
});
document.addEventListener('dragover', event => {
  if (!hasVideoDrag(event)) return;
  event.preventDefault();
  if (event.dataTransfer) event.dataTransfer.dropEffect = 'copy';
});
document.addEventListener('dragleave', event => {
  if (!hasVideoDrag(event)) return;
  videoDragDepth = Math.max(0, videoDragDepth - 1);
  if (videoDragDepth === 0) dropPasteOverlay?.classList.remove('show');
});
document.addEventListener('dragend', () => {
  videoDragDepth = 0;
  dropPasteOverlay?.classList.remove('show');
});
document.addEventListener('drop', async event => {
  if (!hasVideoDrag(event)) return;
  event.preventDefault();
  videoDragDepth = 0;
  dropPasteOverlay?.classList.remove('show');
  await uploadVideoFiles(event.dataTransfer?.files || [], 'dropped');
});
document.addEventListener('paste', async event => {
  const files = videoFilesFromClipboard(event.clipboardData);
  if (!files.length) return;
  event.preventDefault();
  await uploadVideoFiles(files, 'pasted');
});

document.addEventListener('click', event => {
  const button = event.target.closest('.media-zoom-btn');
  if (!button) return;
  event.preventDefault();
  handleDirectVideoZoomButton(button);
});

document.addEventListener('click', event => {
  const button = event.target.closest('.transform-btn');
  if (!button) return;
  event.preventDefault();
  handleDirectVideoTransformButton(button);
});

document.addEventListener('click', event => {
  if (event.target.closest('.card')) return;
  if (event.target.closest('.modal')) return;
  clearVideoCardSelection();
});

document.addEventListener('keydown', async event => {
  if (isTypingTarget()) return;
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'a') {
    event.preventDefault();
    event.stopPropagation();
    selectAllVideoCards();
    return;
  }
  const selected = selectedVideoCardList();
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'c') {
    if (!selected.length) return;
    event.preventDefault();
    copiedVideoNames = selected.map(card => card.dataset.name).filter(Boolean);
    setVideoStatusbarMessage(`Copied ${copiedVideoNames.length} selected video/caption pair(s).`);
    return;
  }
  if ((event.ctrlKey || event.metaKey) && event.key.toLowerCase() === 'v') {
    if (!copiedVideoNames.length) return;
    event.preventDefault();
    showAppBusy(`Copying ${copiedVideoNames.length} video pair(s)...`, 'Copy');
    try {
      for (let i = 0; i < copiedVideoNames.length; i += 1) {
        if (appDialogMessage) appDialogMessage.textContent = `Copying ${i + 1}/${copiedVideoNames.length}: ${copiedVideoNames[i]}`;
        const res = await fetch('/clone_pair', {
          method: 'POST',
          headers: {'Content-Type': 'application/json'},
          body: JSON.stringify({name: copiedVideoNames[i], skip_refresh: true}),
        });
        const data = await res.json();
        if (!res.ok || !data.ok) throw new Error(data.error || 'Copy failed');
      }
      if (appDialogMessage) appDialogMessage.textContent = 'Refreshing video list...';
      await refreshVideoPairsBeforeReload();
      setVideoStatusbarMessage(`Copied ${copiedVideoNames.length} video/caption pair(s).`);
      reloadPreservingViewport(selected[0] || document.querySelector('.card'));
    } catch (err) {
      hideAppBusy();
      await appAlert(err?.message || 'Copy failed');
    }
    return;
  }
  if (event.key === 'Delete') {
    if (!selected.length) return;
    event.preventDefault();
    try {
      await deleteVideoCards(selected);
    } catch (err) {
      hideAppBusy();
      await appAlert(err?.message || 'Delete failed');
    }
  }
});

document.getElementById('saveAllBtn')?.addEventListener('click', async () => {
  const changed = videoCardControllers
    .map(controller => ({
      controller,
      videoChanged: controller.hasVideoChanges(),
      captionChanged: controller.hasCaptionChanges(),
    }))
    .filter(item => item.videoChanged || item.captionChanged);

  if (!changed.length) {
    await appAlert('No changes to save.');
    return;
  }

  const videoCount = changed.filter(item => item.videoChanged).length;
  const captionCount = changed.filter(item => item.captionChanged).length;
  if (videoCount) {
    const ok = await appConfirm(
      `Save changes to ${changed.length} video/caption pair(s)?\n\n` +
      `${videoCount} video file(s) will be replaced.`
    );
    if (!ok) return;
  }

  const saveAllBtn = document.getElementById('saveAllBtn');
  if (saveAllBtn) saveAllBtn.disabled = true;
  changed.forEach(item => item.controller.setSaving(true));
  showAppBusy(`Saving 0/${changed.length} changed pair(s)...`, 'Save');

  try {
    for (let i = 0; i < changed.length; i += 1) {
      const item = changed[i];
      if (appDialogMessage) {
        appDialogMessage.textContent =
          `Saving ${i + 1}/${changed.length}: ${item.controller.name}`;
      }
      if (item.videoChanged) {
        const videoSaveData = await item.controller.saveVideo({skipRefresh: true});
        if (videoSaveData?.pair) {
          item.controller.applySavedPair(videoSaveData.pair);
        } else {
          item.controller.clearCropSelection();
        }
      }
      if (item.captionChanged) await item.controller.saveCaption({skipRefresh: true});
      item.controller.setSaving(false);
    }
    if (appDialogMessage) appDialogMessage.textContent = 'Refreshing saved metadata...';
    await refreshVideoPairsBeforeReload();
    videoCardControllers.forEach(controller => controller.clearCropSelection?.());
    hideAppBusy();
    setVideoStatusbarMessage(`Saved ${changed.length} changed video/caption pair(s).`);
    if (saveAllBtn) saveAllBtn.disabled = false;
  } catch (err) {
    hideAppBusy();
    await appAlert(err?.message || 'Save failed');
    if (saveAllBtn) saveAllBtn.disabled = false;
    changed.forEach(item => item.controller.setSaving(false));
  }
});

document.getElementById('openFolderInExplorerBtn').onclick = async () => {
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
};

const textModal = document.getElementById('textModal');
const captionModal = document.getElementById('captionModal');
const convertModal = document.getElementById('convertModal');
const settingsModal = document.getElementById('settingsModal');
const statsModal = document.getElementById('statsModal');
const renameAllModal = document.getElementById('renameAllModal');
const videoCaptionBackend = document.getElementById('video_caption_backend');
const videoCaptionStatusText = document.getElementById('videoCaptionStatusText');
const videoCaptionStartBtn = document.getElementById('videoCaptionStartBtn');
const videoCaptionInterruptBtn = document.getElementById('videoCaptionInterruptBtn');
const videoCaptionProgress = document.getElementById('videoCaptionProgress');
const videoCaptionProgressLabel = document.getElementById('videoCaptionProgressLabel');
const videoCaptionProgressPercent = document.getElementById('videoCaptionProgressPercent');
const videoCaptionProgressFill = document.getElementById('videoCaptionProgressFill');
const videoCaptionLogBox = document.getElementById('videoCaptionLogBox');
const toolsResult = document.getElementById('toolsResult');
const replaceForm = document.getElementById('replaceForm');
const countForm = document.getElementById('countForm');
const triggerForm = document.getElementById('triggerForm');
const convertStatusText = document.getElementById('convertStatusText');
const convertStartBtn = document.getElementById('convertFpsStartBtn');
const convertInterruptBtn = document.getElementById('convertInterruptBtn');
const convertProgress = document.getElementById('convertProgress');
const convertProgressLabel = document.getElementById('convertProgressLabel');
const convertProgressPercent = document.getElementById('convertProgressPercent');
const convertProgressFill = document.getElementById('convertProgressFill');
const VIDEO_SETTINGS_KEY = 'video_prep_settings';
const VIDEO_GRID_LOADING_KEY = 'video_prep_grid_loading';
const VIDEO_SETTINGS_DEFAULTS = {
  saveCrf: 18,
  savePreset: 'medium',
  saveFps: '',
  videoFormat: 'same',
  sliceKeepSource: true,
  includeEndFrameWhenTrimming: false,
};
let convertPollTimer = null;
let videoCaptionPollTimer = null;

function setVideoGridLoading(loading) {
  document.getElementById('mainContent')?.classList.toggle('loading-videos', !!loading);
}

function normalizeVideoSettings(settings = {}) {
  const saveCrfNumber = Number(settings.saveCrf);
  const saveCrf = Number.isFinite(saveCrfNumber) ? clamp(Math.round(saveCrfNumber), 0, 30) : VIDEO_SETTINGS_DEFAULTS.saveCrf;
  const savePreset = ['ultrafast', 'superfast', 'veryfast', 'faster', 'fast', 'medium', 'slow', 'slower', 'veryslow'].includes(String(settings.savePreset || ''))
    ? String(settings.savePreset)
    : VIDEO_SETTINGS_DEFAULTS.savePreset;
  const saveFpsNumber = Number(settings.saveFps);
  const saveFps = Number.isFinite(saveFpsNumber) && saveFpsNumber > 0 && saveFpsNumber <= 240
    ? String(saveFpsNumber)
    : '';
  const videoFormat = ['same', 'mp4', 'mkv'].includes(String(settings.videoFormat || ''))
    ? String(settings.videoFormat)
    : VIDEO_SETTINGS_DEFAULTS.videoFormat;
  const sliceKeepSource = settings.sliceKeepSource === undefined
    ? VIDEO_SETTINGS_DEFAULTS.sliceKeepSource
    : !!settings.sliceKeepSource;
  const includeEndFrameWhenTrimming = settings.includeEndFrameWhenTrimming === undefined
    ? VIDEO_SETTINGS_DEFAULTS.includeEndFrameWhenTrimming
    : !!settings.includeEndFrameWhenTrimming;
  return {saveCrf, savePreset, saveFps, videoFormat, sliceKeepSource, includeEndFrameWhenTrimming};
}

function getVideoSettings() {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem(VIDEO_SETTINGS_KEY) || '{}') || {};
  } catch (e) {
    saved = {};
  }
  return normalizeVideoSettings({...VIDEO_SETTINGS_DEFAULTS, ...saved});
}

function saveVideoSettings(settings) {
  const normalized = normalizeVideoSettings(settings);
  localStorage.setItem(VIDEO_SETTINGS_KEY, JSON.stringify(normalized));
  return normalized;
}

function loadVideoSettingsForm() {
  const settings = getVideoSettings();
  const saveCrf = document.getElementById('settingsSaveCrf');
  const savePreset = document.getElementById('settingsSavePreset');
  const saveFps = document.getElementById('settingsSaveFps');
  const videoFormat = document.getElementById('settingsVideoFormat');
  const sliceKeepSource = document.getElementById('settingsSliceKeepSource');
  const includeEndFrameWhenTrimming = document.getElementById('settingsIncludeEndFrameWhenTrimming');
  if (saveCrf) saveCrf.value = String(settings.saveCrf);
  if (savePreset) savePreset.value = settings.savePreset;
  if (saveFps) saveFps.value = settings.saveFps;
  if (videoFormat) videoFormat.value = settings.videoFormat;
  if (sliceKeepSource) sliceKeepSource.checked = !!settings.sliceKeepSource;
  if (includeEndFrameWhenTrimming) includeEndFrameWhenTrimming.checked = !!settings.includeEndFrameWhenTrimming;
}

function collectVideoSettingsForm() {
  return saveVideoSettings({
    saveCrf: document.getElementById('settingsSaveCrf')?.value,
    savePreset: document.getElementById('settingsSavePreset')?.value,
    saveFps: document.getElementById('settingsSaveFps')?.value,
    videoFormat: document.getElementById('settingsVideoFormat')?.value,
    sliceKeepSource: document.getElementById('settingsSliceKeepSource')?.checked,
    includeEndFrameWhenTrimming: document.getElementById('settingsIncludeEndFrameWhenTrimming')?.checked,
  });
}

function openSettingsModal() {
  loadVideoSettingsForm();
  settingsModal?.classList.add('open');
}

function closeSettingsModal() {
  settingsModal?.classList.remove('open');
}

if (sessionStorage.getItem(VIDEO_GRID_LOADING_KEY) === '1') {
  sessionStorage.removeItem(VIDEO_GRID_LOADING_KEY);
  setVideoGridLoading(true);
  requestAnimationFrame(() => requestAnimationFrame(() => setVideoGridLoading(false)));
}

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
      setVideoGridLoading(true);
      sessionStorage.setItem(VIDEO_GRID_LOADING_KEY, '1');
      location.assign('/');
    }
  } catch (err) {
    setVideoGridLoading(false);
    sessionStorage.removeItem(VIDEO_GRID_LOADING_KEY);
    hideAppBusy();
    await appAlert(err?.message || 'Open folder failed.');
  }
});
document.getElementById('refreshFolderBtn')?.addEventListener('click', async () => {
  const hasUnsaved = videoCardControllers.some(controller => (
    controller.hasVideoChanges() || controller.hasCaptionChanges()
  ));
  if (hasUnsaved) {
    const ok = await appConfirm('Refresh folder and discard unsaved changes?');
    if (!ok) return;
  }
  showAppBusy('Refreshing folder...', 'Refresh');
  try {
    const res = await fetch('/refresh_folder', {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Refresh failed.');
    reloadPreservingViewport();
  } catch (err) {
    hideAppBusy();
    await appAlert(err?.message || 'Refresh failed.');
  }
});
function openToolsModal() {
  textModal?.classList.add('open');
}

function closeToolsModal() {
  textModal?.classList.remove('open');
}

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

function refreshCaptionTextareas(pairs) {
  const byName = new Map((pairs || []).map(p => [p.name, p.text]));
  document.querySelectorAll('.card').forEach(card => {
    const name = card.dataset.name;
    const caption = card.querySelector('.caption');
    if (!caption || !byName.has(name)) return;
    const value = byName.get(name) || '';
    caption.value = value;
    caption.dataset.original = value;
    caption.classList.remove('unsaved-caption');
    updateCaptionStats(caption);
  });
}

async function reloadCaptionsIntoCards() {
  const captionsRes = await fetch('/captions_json', {
    headers: { 'X-Requested-With': 'XMLHttpRequest' }
  });
  const captionsData = await captionsRes.json();
  if (captionsRes.ok && captionsData.ok && Array.isArray(captionsData.pairs)) {
    refreshCaptionTextareas(captionsData.pairs);
  }
}

async function confirmReplace() {
  return appConfirm('Apply this search/replace to all caption files in the opened folder?');
}

loadToolsSettings();
document.getElementById('sr_use_regex')?.addEventListener('change', saveToolsSettings);
document.querySelectorAll('.regex-help-icon').forEach(icon => {
  icon.addEventListener('mouseenter', () => showRegexTooltip(icon));
  icon.addEventListener('mousemove', () => {
    if (regexTooltipEl?.classList.contains('open')) positionRegexTooltip(icon);
  });
  icon.addEventListener('mouseleave', hideRegexTooltip);
  icon.addEventListener('focus', () => showRegexTooltip(icon));
  icon.addEventListener('blur', hideRegexTooltip);
});

replaceForm?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const ok = await confirmReplace();
  if (!ok) return;
  const formData = new FormData(replaceForm);
  try {
    if (toolsResult) toolsResult.textContent = 'Replacing...';
    showAppBusy('Replacing captions...', 'Text tools');
    const res = await fetch('/replace_all', {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      body: formData
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      hideAppBusy();
      if (toolsResult) toolsResult.textContent = data.error || 'Replace failed.';
      return;
    }
    if (toolsResult) toolsResult.textContent = data.message || 'Replace complete.';
    await reloadCaptionsIntoCards();
    hideAppBusy();
    openToolsModal();
  } catch (err) {
    hideAppBusy();
    if (toolsResult) toolsResult.textContent = `Replace failed: ${err}`;
  }
});

countForm?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const formData = new FormData(countForm);
  try {
    if (toolsResult) toolsResult.textContent = 'Counting...';
    showAppBusy('Counting matches...', 'Text tools');
    const res = await fetch('/count_string', {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      body: formData
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      hideAppBusy();
      if (toolsResult) toolsResult.textContent = data.error || 'Count failed.';
      return;
    }
    if (toolsResult) toolsResult.textContent = data.message || 'Count complete.';
    hideAppBusy();
  } catch (err) {
    hideAppBusy();
    if (toolsResult) toolsResult.textContent = `Count failed: ${err}`;
  }
});

triggerForm?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const formData = new FormData(triggerForm);
  try {
    if (toolsResult) toolsResult.textContent = 'Adding trigger word...';
    showAppBusy('Adding trigger word...', 'Text tools');
    const res = await fetch('/add_triggerword_all', {
      method: 'POST',
      headers: { 'X-Requested-With': 'XMLHttpRequest' },
      body: formData
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      hideAppBusy();
      if (toolsResult) toolsResult.textContent = data.error || 'Add trigger word failed.';
      return;
    }
    if (toolsResult) toolsResult.textContent = data.message || 'Add trigger word complete.';
    await reloadCaptionsIntoCards();
    hideAppBusy();
    openToolsModal();
  } catch (err) {
    hideAppBusy();
    if (toolsResult) toolsResult.textContent = `Add trigger word failed: ${err}`;
  }
});

document.getElementById('textToolsBtn').onclick = openToolsModal;
document.getElementById('closeTextModalBtn').onclick = () => textModal.classList.remove('open');
const VIDEO_CAPTION_DEFAULTS = {
  backend: 'qwen3_vl',
  qwen3vl_model: 'Qwen3-VL-4B-Instruct',
  qwen3vl_sampling_mode: 'auto',
  qwen3vl_frame_count: '12',
  qwen3vl_every_nth_frame: '12',
  qwen3vl_max_sampled_frames: '16',
  qwen3vl_use_all_frames: false,
  qwen3vl_max_image_side: '512',
  qwen3vl_temperature: '0.2',
  qwen3vl_max_tokens: '256',
  qwen3vl_system_prompt: `Generate only a concise comma-separated LoRA caption for the video.

Start the caption with [name]. Use [name] as the character name or training trigger. Mention [name] only once.

Focus on what happens over time in the video. Describe the visible sequence of actions, pose changes, gestures, gaze shifts, expression changes, body movement, clothing movement, camera movement, and scene motion.

Use short visual phrases in temporal order when possible. Prefer motion-based descriptions such as turning, walking, leaning, raising an arm, looking away, smiling, blinking, hair moving, fabric moving, camera pushing in, or background motion.

Do not describe body shape, body proportions, hair color, eye color, identity, story, intent, age, ethnicity, or personality. Do not invent details. Do not mention metadata, filename, timestamps, resolution, quality, or that this is a video.

Output only the caption, with no intro or explanation.`,
  external_api_url: '',
  external_api_model: '',
  external_api_key: '',
  external_api_temperature: '0.2',
  external_api_max_tokens: '256',
  external_api_system_prompt: `Generate only a concise comma-separated LoRA caption for the video.

Start the caption with [name]. Use [name] as the character name or training trigger. Mention [name] only once.

Focus on what happens over time in the video. Describe the visible sequence of actions, pose changes, gestures, gaze shifts, expression changes, body movement, clothing movement, camera movement, and scene motion.

Use short visual phrases in temporal order when possible. Do not invent details. Do not mention metadata, filename, timestamps, resolution, quality, or that this is a video.

Output only the caption, with no intro or explanation.`,
  whisperx_model: 'large-v3',
  whisperx_language: '',
  whisperx_vad_method: 'silero',
  whisperx_batch_size: '8',
  append_existing: false,
  no_overwrite: false,
};
const VIDEO_CAPTION_OLD_QWEN3_PROMPTS = [
  'Create a concise LoRA training caption for this video. Use comma-separated descriptive tags and short phrases. Focus on visible human figure traits, action, pose, expression, gaze, framing, camera angle, clothing, hair, lighting, environment, motion, and video style. Do not invent details. Do not mention file metadata.',
  'Create a concise LoRA training caption for this video. Use short phrases. Focus on visible human figure action, pose, expression, gaze, clothing, environment and motion. Do not invent details. Do not mention file metadata.',
];

function updateVideoCaptionBackendUI() {
  const backend = videoCaptionBackend?.value || 'qwen3_vl';
  document.querySelectorAll('.visual-caption-only').forEach(el => {
    el.style.display = backend === 'qwen3_vl' || backend === 'external_api' ? '' : 'none';
  });
  document.querySelectorAll('.qwen3vl-only').forEach(el => {
    el.style.display = backend === 'qwen3_vl' ? '' : 'none';
  });
  document.querySelectorAll('.external-api-only').forEach(el => {
    el.style.display = backend === 'external_api' ? '' : 'none';
  });
  document.querySelectorAll('.whisperx-only').forEach(el => {
    el.style.display = backend === 'whisperx' ? '' : 'none';
  });
  updateVideoFrameMode();
}

function updateVideoFrameMode() {
  const mode = document.getElementById('video_qwen3vl_sampling_mode')?.value || 'auto';
  const frameInput = document.getElementById('video_qwen3vl_frame_count');
  const frameField = document.getElementById('videoQwenFrameCountField');
  const nthInput = document.getElementById('video_qwen3vl_every_nth_frame');
  const nthField = document.getElementById('videoQwenNthFrameField');
  const showFrameCount = mode === 'even';
  const showNth = mode === 'nth';
  if (frameInput) frameInput.disabled = !showFrameCount;
  if (frameField) {
    frameField.style.display = showFrameCount ? '' : 'none';
    frameField.classList.toggle('caption-field-disabled', !showFrameCount);
  }
  if (nthInput) nthInput.disabled = !showNth;
  if (nthField) {
    nthField.style.display = showNth ? '' : 'none';
    nthField.classList.toggle('caption-field-disabled', !showNth);
  }
}

function getVideoCaptionSettings() {
  return {
    backend: document.getElementById('video_caption_backend')?.value || VIDEO_CAPTION_DEFAULTS.backend,
    qwen3vl_model: document.getElementById('video_qwen3vl_model')?.value || VIDEO_CAPTION_DEFAULTS.qwen3vl_model,
    qwen3vl_sampling_mode: document.getElementById('video_qwen3vl_sampling_mode')?.value || VIDEO_CAPTION_DEFAULTS.qwen3vl_sampling_mode,
    qwen3vl_frame_count: document.getElementById('video_qwen3vl_frame_count')?.value || VIDEO_CAPTION_DEFAULTS.qwen3vl_frame_count,
    qwen3vl_every_nth_frame: document.getElementById('video_qwen3vl_every_nth_frame')?.value || VIDEO_CAPTION_DEFAULTS.qwen3vl_every_nth_frame,
    qwen3vl_max_sampled_frames: document.getElementById('video_qwen3vl_max_sampled_frames')?.value || VIDEO_CAPTION_DEFAULTS.qwen3vl_max_sampled_frames,
    qwen3vl_use_all_frames: false,
    qwen3vl_max_image_side: document.getElementById('video_qwen3vl_max_image_side')?.value || VIDEO_CAPTION_DEFAULTS.qwen3vl_max_image_side,
    qwen3vl_temperature: document.getElementById('video_qwen3vl_temperature')?.value || VIDEO_CAPTION_DEFAULTS.qwen3vl_temperature,
    qwen3vl_max_tokens: document.getElementById('video_qwen3vl_max_tokens')?.value || VIDEO_CAPTION_DEFAULTS.qwen3vl_max_tokens,
    qwen3vl_system_prompt: document.getElementById('video_qwen3vl_system_prompt')?.value || VIDEO_CAPTION_DEFAULTS.qwen3vl_system_prompt,
    external_api_url: document.getElementById('video_external_api_url')?.value || '',
    external_api_model: document.getElementById('video_external_api_model')?.value || '',
    external_api_key: document.getElementById('video_external_api_key')?.value || '',
    external_api_temperature: document.getElementById('video_external_api_temperature')?.value || VIDEO_CAPTION_DEFAULTS.external_api_temperature,
    external_api_max_tokens: document.getElementById('video_external_api_max_tokens')?.value || VIDEO_CAPTION_DEFAULTS.external_api_max_tokens,
    external_api_system_prompt: document.getElementById('video_external_api_system_prompt')?.value || VIDEO_CAPTION_DEFAULTS.external_api_system_prompt,
    whisperx_model: document.getElementById('video_whisperx_model')?.value || VIDEO_CAPTION_DEFAULTS.whisperx_model,
    whisperx_language: document.getElementById('video_whisperx_language')?.value || '',
    whisperx_vad_method: document.getElementById('video_whisperx_vad_method')?.value || VIDEO_CAPTION_DEFAULTS.whisperx_vad_method,
    whisperx_batch_size: document.getElementById('video_whisperx_batch_size')?.value || VIDEO_CAPTION_DEFAULTS.whisperx_batch_size,
    append_existing: !!document.getElementById('video_caption_append_existing')?.checked,
    no_overwrite: !!document.getElementById('video_caption_no_overwrite')?.checked,
  };
}

function saveVideoCaptionSettings() {
  const settings = getVideoCaptionSettings();
  delete settings.external_api_key;
  localStorage.setItem('video_prep_caption_settings', JSON.stringify(settings));
}

function loadVideoCaptionSettings() {
  let saved = {};
  try {
    saved = JSON.parse(localStorage.getItem('video_prep_caption_settings') || '{}') || {};
  } catch (e) {
    saved = {};
  }
  const merged = {...VIDEO_CAPTION_DEFAULTS, ...saved};
  if (String(saved.qwen3vl_base_url || '').trim() && !String(saved.external_api_url || '').trim()) {
    const legacyModels = {
      'Qwen3-VL-4B-Instruct': 'Qwen/Qwen3-VL-4B-Instruct',
      'Qwen3-VL-8B-Instruct': 'Qwen/Qwen3-VL-8B-Instruct',
      'Huihui-Qwen3-VL-8B-Instruct-abliterated': 'huihui-ai/Huihui-Qwen3-VL-8B-Instruct-abliterated',
    };
    merged.external_api_url = saved.qwen3vl_base_url;
    merged.external_api_model = legacyModels[merged.qwen3vl_model] || merged.qwen3vl_model || '';
    merged.external_api_temperature = merged.qwen3vl_temperature;
    merged.external_api_max_tokens = merged.qwen3vl_max_tokens;
    merged.external_api_system_prompt = merged.qwen3vl_system_prompt;
    if (merged.backend === 'qwen3_vl') merged.backend = 'external_api';
  }
  if (!['qwen3_vl', 'external_api', 'whisperx'].includes(String(merged.backend || ''))) {
    merged.backend = VIDEO_CAPTION_DEFAULTS.backend;
  }
  if (!saved.qwen3vl_sampling_mode && saved.qwen3vl_use_all_frames) {
    merged.qwen3vl_sampling_mode = 'auto';
  }
  if (VIDEO_CAPTION_OLD_QWEN3_PROMPTS.includes(String(saved.qwen3vl_system_prompt || ''))) {
    merged.qwen3vl_system_prompt = VIDEO_CAPTION_DEFAULTS.qwen3vl_system_prompt;
  }
  const setValue = (id, value) => {
    const el = document.getElementById(id);
    if (el) el.value = String(value ?? '');
  };
  setValue('video_caption_backend', merged.backend);
  setValue('video_qwen3vl_model', merged.qwen3vl_model);
  setValue('video_qwen3vl_sampling_mode', merged.qwen3vl_sampling_mode || VIDEO_CAPTION_DEFAULTS.qwen3vl_sampling_mode);
  setValue('video_qwen3vl_frame_count', merged.qwen3vl_frame_count);
  setValue('video_qwen3vl_every_nth_frame', merged.qwen3vl_every_nth_frame || VIDEO_CAPTION_DEFAULTS.qwen3vl_every_nth_frame);
  setValue('video_qwen3vl_max_sampled_frames', merged.qwen3vl_max_sampled_frames || VIDEO_CAPTION_DEFAULTS.qwen3vl_max_sampled_frames);
  setValue('video_qwen3vl_max_image_side', merged.qwen3vl_max_image_side);
  setValue('video_qwen3vl_temperature', merged.qwen3vl_temperature);
  setValue('video_qwen3vl_max_tokens', merged.qwen3vl_max_tokens);
  setValue('video_qwen3vl_system_prompt', merged.qwen3vl_system_prompt);
  setValue('video_external_api_url', merged.external_api_url);
  setValue('video_external_api_model', merged.external_api_model);
  setValue('video_external_api_temperature', merged.external_api_temperature);
  setValue('video_external_api_max_tokens', merged.external_api_max_tokens);
  setValue('video_external_api_system_prompt', merged.external_api_system_prompt);
  setValue('video_whisperx_model', merged.whisperx_model);
  setValue('video_whisperx_language', merged.whisperx_language);
  setValue('video_whisperx_vad_method', merged.whisperx_vad_method || VIDEO_CAPTION_DEFAULTS.whisperx_vad_method);
  setValue('video_whisperx_batch_size', merged.whisperx_batch_size);
  const appendEl = document.getElementById('video_caption_append_existing');
  if (appendEl) appendEl.checked = !!merged.append_existing;
  const noOverwriteEl = document.getElementById('video_caption_no_overwrite');
  if (noOverwriteEl) noOverwriteEl.checked = !!merged.no_overwrite;
  updateVideoCaptionBackendUI();
}

function setVideoCaptionProgress(data = {}) {
  const total = Math.max(0, Number(data.total || 0));
  const count = Math.max(0, Math.min(total || 0, Number(data.count || 0)));
  const percent = total ? Math.round((count / total) * 100) : 0;
  if (videoCaptionProgress) videoCaptionProgress.style.display = total || data.running || data.done ? '' : 'none';
  if (videoCaptionProgressLabel) videoCaptionProgressLabel.textContent = total ? `Captions: ${count}/${total}` : 'Captions: 0/0';
  if (videoCaptionProgressPercent) videoCaptionProgressPercent.textContent = `${percent}%`;
  if (videoCaptionProgressFill) videoCaptionProgressFill.style.width = `${percent}%`;
  if (videoCaptionStatusText) videoCaptionStatusText.textContent = data.status || (data.running ? 'Running' : 'Idle');
  if (videoCaptionStartBtn) videoCaptionStartBtn.disabled = !!data.running;
  if (videoCaptionInterruptBtn) videoCaptionInterruptBtn.disabled = !data.running || !!data.interrupt_requested;
  if (videoCaptionLogBox) videoCaptionLogBox.textContent = data.log || '';
}

async function refreshVideoCaptionsFromDisk(preserveDirty = true) {
  try {
    const res = await fetch('/captions_json', {
      headers: {'X-Requested-With': 'XMLHttpRequest'}
    });
    const data = await res.json();
    if (!res.ok || !data.ok || !Array.isArray(data.pairs)) return;
    const byName = new Map(data.pairs.map(pair => [pair.name, pair.text || '']));
    videoCardControllers.forEach(controller => {
      if (byName.has(controller.name)) {
        controller.applyCaptionFromDisk(byName.get(controller.name), preserveDirty);
      }
    });
    updateSaveAllButtonState();
  } catch (error) {}
}

async function pollVideoCaptionStatus({once = false, reloadOnDone = false} = {}) {
  if (videoCaptionPollTimer) {
    clearTimeout(videoCaptionPollTimer);
    videoCaptionPollTimer = null;
  }
  try {
    const res = await fetch('/video_caption_status');
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Caption status failed');
    setVideoCaptionProgress(data);
    await refreshVideoCaptionsFromDisk(true);
    if (data.running && !once) {
      videoCaptionPollTimer = setTimeout(() => pollVideoCaptionStatus({reloadOnDone}), 1000);
      return;
    }
    if (data.done && data.reload_pairs && reloadOnDone) {
      await refreshVideoCaptionsFromDisk(true);
    }
  } catch (err) {
    if (videoCaptionStatusText) videoCaptionStatusText.textContent = err.message || 'Caption status failed';
  }
}

loadVideoCaptionSettings();
[
  'video_caption_backend','video_qwen3vl_model','video_qwen3vl_sampling_mode','video_qwen3vl_frame_count','video_qwen3vl_every_nth_frame','video_qwen3vl_max_sampled_frames','video_qwen3vl_max_image_side',
  'video_qwen3vl_temperature','video_qwen3vl_max_tokens','video_qwen3vl_system_prompt',
  'video_external_api_url','video_external_api_model','video_external_api_key','video_external_api_temperature','video_external_api_max_tokens','video_external_api_system_prompt',
  'video_whisperx_model','video_whisperx_language','video_whisperx_vad_method','video_whisperx_batch_size',
  'video_caption_append_existing','video_caption_no_overwrite'
].forEach(id => {
  const el = document.getElementById(id);
  const eventName = el?.tagName === 'SELECT' || el?.type === 'checkbox' ? 'change' : 'input';
  el?.addEventListener(eventName, () => {
    if (id === 'video_caption_backend') updateVideoCaptionBackendUI();
    if (id === 'video_qwen3vl_sampling_mode') updateVideoFrameMode();
    saveVideoCaptionSettings();
  });
});

document.getElementById('captionStubBtn').onclick = () => {
  captionModal.classList.add('open');
  pollVideoCaptionStatus({once: true});
};
document.getElementById('closeCaptionModalBtn').onclick = () => captionModal.classList.remove('open');
videoCaptionStartBtn?.addEventListener('click', async () => {
  const options = getVideoCaptionSettings();
  saveVideoCaptionSettings();
  setVideoCaptionProgress({running: true, total: 0, count: 0, status: 'Starting...', log: ''});
  try {
    const res = await fetch('/video_caption_start', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify(options),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Caption start failed');
    pollVideoCaptionStatus({reloadOnDone: true});
  } catch (err) {
    if (videoCaptionStatusText) videoCaptionStatusText.textContent = err.message || 'Caption start failed';
    if (videoCaptionStartBtn) videoCaptionStartBtn.disabled = false;
    if (videoCaptionInterruptBtn) videoCaptionInterruptBtn.disabled = true;
  }
});
videoCaptionInterruptBtn?.addEventListener('click', async () => {
  if (videoCaptionInterruptBtn) videoCaptionInterruptBtn.disabled = true;
  if (videoCaptionStatusText) videoCaptionStatusText.textContent = 'Interrupt requested...';
  await fetch('/video_caption_interrupt', {method: 'POST'});
  pollVideoCaptionStatus({reloadOnDone: true});
});
document.getElementById('convertBtn')?.addEventListener('click', () => {
  convertModal?.classList.add('open');
  pollConvertStatus({once: true});
});
document.getElementById('closeConvertModalBtn')?.addEventListener('click', () => convertModal?.classList.remove('open'));
document.getElementById('settingsBtn').onclick = openSettingsModal;
document.getElementById('closeSettingsModalBtn').onclick = closeSettingsModal;
document.getElementById('videoSettingsForm')?.addEventListener('submit', event => {
  event.preventDefault();
  collectVideoSettingsForm();
  window.dispatchEvent(new CustomEvent('video-settings-updated'));
  setVideoStatusbarMessage('Settings saved.');
  closeSettingsModal();
});
document.getElementById('closeStatsModalBtn').onclick = () => statsModal.classList.remove('open');
document.getElementById('renameAllBtn').onclick = () => renameAllModal.classList.add('open');
document.getElementById('closeRenameAllModalBtn').onclick = () => renameAllModal.classList.remove('open');

function htmlEscape(value) {
  return String(value ?? '').replace(/[&<>"']/g, ch => ({
    '&': '&amp;',
    '<': '&lt;',
    '>': '&gt;',
    '"': '&quot;',
    "'": '&#39;',
  }[ch]));
}

let statsDataCache = null;
let statsLengthSort = {direction: 'desc'};
let statsResolutionSort = {direction: 'desc'};

function lengthSortValue() {
  return `length:${statsLengthSort.direction}`;
}

function resolutionSortValue() {
  return `area:${statsResolutionSort.direction}`;
}

function resolutionArea(value) {
  const match = String(value || '').match(/^(\d+)x(\d+)/i);
  if (!match) return 0;
  return Number(match[1]) * Number(match[2]);
}

function renderStats(data) {
  const sortedResolutions = [...(data.resolutions || [])].sort((a, b) => {
    const dir = statsResolutionSort.direction === 'asc' ? 1 : -1;
    const areaDiff = resolutionArea(a.resolution) - resolutionArea(b.resolution);
    if (areaDiff) return areaDiff * dir;
    return String(a.resolution || '').localeCompare(String(b.resolution || ''), undefined, {numeric: true}) * dir;
  });
  const resolutionRows = sortedResolutions.map(item =>
    `<div style="display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;border-bottom:1px solid var(--border);padding:3px 0;"><span>${htmlEscape(item.resolution)}</span><b>${item.count}</b></div>`
  ).join('') || '<div class="muted">No resolution data</div>';
  const bucketBaseRows = (data.bucket_bases || []).map(item =>
    `<div style="display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;border-bottom:1px solid var(--border);padding:3px 0;"><span>${htmlEscape(item.base)}</span><b>${item.count}</b></div>`
  ).join('') || '<div class="muted">No bucket base data</div>';
  const fpsRows = (data.fps_values || []).map(item =>
    `<div style="display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;border-bottom:1px solid var(--border);padding:3px 0;"><span>${htmlEscape(item.fps)} FPS</span><b>${item.count}</b></div>`
  ).join('') || '<div class="muted">No FPS data</div>';
  const sortedLengths = [...(data.lengths || [])].sort((a, b) => {
    const dir = statsLengthSort.direction === 'desc' ? -1 : 1;
    const secondsDiff = Number(a.seconds || 0) - Number(b.seconds || 0);
    if (secondsDiff) return secondsDiff * dir;
    return String(a.name || '').localeCompare(String(b.name || ''), undefined, {numeric: true, sensitivity: 'base'});
  });
  const lengthRows = sortedLengths.map(item =>
    `<div style="display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:8px;border-bottom:1px solid var(--border);padding:3px 0;"><span title="${htmlEscape(item.name)}">${htmlEscape(item.name)}</span><b>${Number(item.frames || 0)}</b><b>${Number(item.seconds || 0).toFixed(2)} s</b></div>`
  ).join('') || '<div class="muted">No length data</div>';
  return `
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-bottom:12px;">
      <div><div class="muted">Videos</div><strong>${data.total_videos || 0}</strong></div>
      <div><div class="muted">Captions</div><strong>${data.total_captions || 0}</strong></div>
      <div><div class="muted">Audio</div><strong>${data.audio_videos || 0}</strong></div>
      <div><div class="muted">Mute</div><strong>${data.mute_videos || 0}</strong></div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;">
      <div>
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;">
          <strong>Resolutions</strong>
          <label style="display:flex;align-items:center;gap:6px;">
            Sort
            <select id="statsResolutionSortSelect" style="min-height:28px;padding:4px 8px;font-size:12px;">
              <option value="area:desc" ${resolutionSortValue() === 'area:desc' ? 'selected' : ''}>Largest first</option>
              <option value="area:asc" ${resolutionSortValue() === 'area:asc' ? 'selected' : ''}>Smallest first</option>
            </select>
          </label>
        </div>
        <div style="margin-top:6px;">${resolutionRows}</div>
      </div>
      <div><strong>Bucket bases</strong><div style="margin-top:6px;">${bucketBaseRows}</div></div>
      <div><strong>FPS</strong><div style="margin-top:6px;">${fpsRows}</div></div>
      <div style="grid-column:1 / -1;">
        <div style="display:flex;align-items:center;justify-content:space-between;gap:10px;flex-wrap:wrap;">
          <strong>Lengths</strong>
          <label style="display:flex;align-items:center;gap:6px;">
            Sort
            <select id="statsLengthSortSelect" style="min-height:28px;padding:4px 8px;font-size:12px;">
              <option value="length:asc" ${lengthSortValue() === 'length:asc' ? 'selected' : ''}>Length shortest</option>
              <option value="length:desc" ${lengthSortValue() === 'length:desc' ? 'selected' : ''}>Length longest</option>
            </select>
          </label>
        </div>
        <div class="muted" style="display:grid;grid-template-columns:minmax(0,1fr) auto auto;gap:8px;margin-top:8px;"><span>File</span><span>Frames</span><span>Seconds</span></div>
        <div style="margin-top:2px;max-height:260px;overflow:auto;">${lengthRows}</div>
      </div>
    </div>
  `;
}

document.getElementById('statsContent')?.addEventListener('change', event => {
  if (!statsDataCache) return;
  if (event.target?.id === 'statsLengthSortSelect') {
    const [, direction] = String(event.target.value || 'length:desc').split(':');
    statsLengthSort = {direction: direction || 'desc'};
  } else if (event.target?.id === 'statsResolutionSortSelect') {
    const [, direction] = String(event.target.value || 'area:desc').split(':');
    statsResolutionSort = {direction: direction || 'desc'};
  } else {
    return;
  }
  event.currentTarget.innerHTML = renderStats(statsDataCache);
});

document.getElementById('openStatsModalBtn')?.addEventListener('click', async () => {
  const content = document.getElementById('statsContent');
  statsModal.classList.add('open');
  if (content) content.textContent = 'Loading...';
  try {
    const res = await fetch('/stats');
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Stats failed');
    statsDataCache = data;
    if (content) content.innerHTML = renderStats(data);
  } catch (err) {
    if (content) content.textContent = err.message || 'Stats failed';
  }
});

document.querySelectorAll('.backup-form').forEach(form => {
  form.addEventListener('submit', async event => {
    event.preventDefault();
    const ok = await appConfirm('Create a backup of all video and caption pairs?');
    if (!ok) return;
    showAppBusy('Creating backup...', 'Backup');
    try {
      const res = await fetch(form.action || '/backup', {
        method: 'POST',
        headers: { 'X-Requested-With': 'XMLHttpRequest', 'Accept': 'application/json' },
      });
      const data = await res.json();
      hideAppBusy();
      if (!res.ok || !data.ok) {
        await appAlert(data.error || 'Backup failed.');
        return;
      }
      setVideoStatusbarMessage(data.message || 'Backup complete.');
    } catch (err) {
      hideAppBusy();
      await appAlert(`Backup failed: ${err}`);
    }
  });
});

document.getElementById('autoCropAllBtn')?.addEventListener('click', () => {
  cropEditors.forEach(editor => editor.autoCrop && editor.autoCrop());
});

document.getElementById('resetAllBtn')?.addEventListener('click', async () => {
  if (await appConfirm('Reset unsaved video edits and captions?')) {
    showAppBusy('Resetting...', 'Reset');
    location.reload();
  }
});

document.getElementById('renameAllForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const prefix = document.getElementById('renameAllPrefixInput')?.value || '';
  const status = document.getElementById('renameAllStatusText');
  const startBtn = document.getElementById('renameAllStartBtn');
  if (!prefix.trim()) {
    if (status) status.textContent = 'Prefix is required.';
    return;
  }
  if (!await appConfirm(`Rename every opened video pair using prefix "${prefix}"?`)) return;
  if (status) status.textContent = 'Renaming...';
  if (startBtn) startBtn.disabled = true;
  showAppBusy('Renaming videos...', 'Rename');
  try {
    const res = await fetch('/rename_all_pairs', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({prefix}),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Rename failed');
    if (status) status.textContent = `Renamed ${data.renamed || 0} pair(s).`;
    location.reload();
  } catch (err) {
    hideAppBusy();
    if (status) status.textContent = err.message || 'Rename failed';
    if (startBtn) startBtn.disabled = false;
  }
});

function setConvertProgress(data = {}) {
  const total = Math.max(0, Number(data.total || 0));
  const count = Math.max(0, Math.min(total || 0, Number(data.count || 0)));
  const converted = Math.max(0, Number(data.converted || 0));
  const reportedPercent = Number(data.percent);
  const percent = Number.isFinite(reportedPercent)
    ? Math.max(0, Math.min(100, Math.round(reportedPercent)))
    : (total ? Math.round((count / total) * 100) : 0);
  if (convertProgress) convertProgress.style.display = total || data.running || data.done ? '' : 'none';
  if (convertProgressLabel) convertProgressLabel.textContent = total ? `Convert: ${count}/${total}` : 'Convert: 0/0';
  if (convertProgressPercent) convertProgressPercent.textContent = `${percent}%`;
  if (convertProgressFill) convertProgressFill.style.width = `${percent}%`;
  if (convertStatusText) {
    const fallback = data.running ? 'Converting...' : 'Idle';
    convertStatusText.textContent = data.status || fallback;
  }
  if (convertStartBtn) convertStartBtn.disabled = !!data.running;
  if (convertInterruptBtn) convertInterruptBtn.disabled = !data.running || !!data.interrupt_requested;
  return {total, count, converted};
}

async function pollConvertStatus({once = false, reloadOnDone = false} = {}) {
  if (convertPollTimer) {
    clearTimeout(convertPollTimer);
    convertPollTimer = null;
  }
  try {
    const res = await fetch('/convert_fps_status');
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Convert status failed');
    const progress = setConvertProgress(data);
    if (data.running && !once) {
      convertPollTimer = setTimeout(() => pollConvertStatus({reloadOnDone}), 700);
      return;
    }
    if (data.done && reloadOnDone && (progress.converted > 0 || (!data.interrupted && !(data.errors || []).length))) {
      setTimeout(() => location.reload(), 700);
    }
  } catch (err) {
    if (convertStatusText) convertStatusText.textContent = err.message || 'Convert status failed';
    if (convertStartBtn) convertStartBtn.disabled = false;
    if (convertInterruptBtn) convertInterruptBtn.disabled = true;
  }
}

document.getElementById('convertFpsForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const fps = parseFloat(document.getElementById('convertFpsInput')?.value || '0');
  const crf = parseInt(document.getElementById('convertQualitySelect')?.value || '18', 10);
  const backup = !!document.getElementById('convertBackupCheckbox')?.checked;
  if (!Number.isFinite(fps) || fps <= 0 || fps > 240) {
    if (convertStatusText) convertStatusText.textContent = 'FPS must be between 0 and 240.';
    return;
  }
  if (!Number.isFinite(crf) || crf < 0 || crf > 30) {
    if (convertStatusText) convertStatusText.textContent = 'Select a valid quality setting.';
    return;
  }
  const backupText = backup ? 'Backups will be saved to the BACKUP folder.' : 'No backups will be created.';
  if (!await appConfirm(`Convert every opened video to ${fps} FPS using CRF ${crf}?\n\n${backupText}`)) return;
  setConvertProgress({running: true, total: 0, count: 0, status: 'Starting convert...'});
  try {
    const res = await fetch('/convert_fps', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({fps, crf, backup}),
    });
    const data = await res.json();
    if (!res.ok || !data.ok) {
      throw new Error(data.error || 'Convert failed');
    }
    setConvertProgress({running: true, total: data.total || 0, count: 0, status: 'Converting...'});
    pollConvertStatus({reloadOnDone: true});
  } catch (err) {
    if (convertStatusText) convertStatusText.textContent = err.message || 'Convert failed';
    if (convertStartBtn) convertStartBtn.disabled = false;
    if (convertInterruptBtn) convertInterruptBtn.disabled = true;
  }
});

convertInterruptBtn?.addEventListener('click', async () => {
  if (convertInterruptBtn) convertInterruptBtn.disabled = true;
  if (convertStatusText) convertStatusText.textContent = 'Interrupt requested...';
  try {
    await fetch('/convert_fps_interrupt', {method: 'POST'});
    pollConvertStatus({reloadOnDone: true});
  } catch (err) {
    if (convertStatusText) convertStatusText.textContent = err.message || 'Interrupt failed';
  }
});

window.addEventListener('keydown', (e) => {
  if (e.ctrlKey && e.shiftKey && e.key.toLowerCase() === 'f') {
    e.preventDefault();
    textModal.classList.add('open');
  }
  if (e.key === 'Escape') {
    textModal.classList.remove('open');
    captionModal.classList.remove('open');
    convertModal?.classList.remove('open');
    settingsModal?.classList.remove('open');
    statsModal.classList.remove('open');
    renameAllModal.classList.remove('open');
    closeSliceEditor();
  }
});

document.addEventListener('input', () => requestAnimationFrame(updateSaveAllButtonState), true);
document.addEventListener('change', () => requestAnimationFrame(updateSaveAllButtonState), true);
document.addEventListener('pointerup', () => requestAnimationFrame(updateSaveAllButtonState), true);
document.addEventListener('click', () => requestAnimationFrame(updateSaveAllButtonState), true);
updateSaveAllButtonState();
</script>
</body>
</html>
"""


if __name__ == "__main__":
    print("Starting Video Dataset Prep Tool...")
    app.run("127.0.0.1", 5002, debug=False)
