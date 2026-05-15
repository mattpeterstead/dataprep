import os
import sys
import json
import math
import re
import shutil
import subprocess
import threading
import webbrowser
import socket
import tkinter as tk
from pathlib import Path
from tkinter import filedialog

from flask import Flask, jsonify, redirect, render_template_string, request, send_from_directory, url_for

if sys.platform.startswith("win"):
    import multiprocessing
    multiprocessing.freeze_support()

APP_DIR = Path(__file__).resolve().parent
SETTINGS_DIR = APP_DIR / "settings"
LAST_APP_FILE = SETTINGS_DIR / ".dataset_forge_last_app"
VIDEO_EXTENSIONS = (".mp4", ".mkv", ".webm", ".mov", ".avi", ".m4v")
BUCKET_STEP = 64
FFPROBE_EXE = shutil.which("ffprobe") or "ffprobe"
FFMPEG_EXE = shutil.which("ffmpeg") or "ffmpeg"

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

    total_frames = int(round(duration * fps)) if duration > 0 else int(video_stream.get("nb_frames") or 0)

    return {
        "width": width,
        "height": height,
        "duration": duration,
        "fps": fps,
        "frames": total_frames,
        "has_audio": audio_stream is not None,
    }


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


def safe_stem_path(folder: str, stem: str, suffix: str):
    candidate = Path(folder) / f"{stem}{suffix}"
    n = 2
    while candidate.exists():
        candidate = Path(folder) / f"{stem}_{n}{suffix}"
        n += 1
    return candidate


def load_pairs(folder: str):
    pairs = []
    idx = 0
    for name in sorted(os.listdir(folder)):
        if not name.lower().endswith(VIDEO_EXTENSIONS):
            continue
        full = os.path.join(folder, name)
        txt = os.path.splitext(full)[0] + ".txt"
        if not os.path.exists(txt):
            Path(txt).write_text("", encoding="utf-8")
        try:
            meta = probe_video(full)
        except Exception:
            meta = {"width": 0, "height": 0, "duration": 0.0, "fps": 24.0, "frames": 0, "has_audio": False}
        text = Path(txt).read_text(encoding="utf-8", errors="replace")
        pairs.append({
            "index": idx,
            "name": name,
            "caption": text,
            "width": meta["width"],
            "height": meta["height"],
            "duration": meta["duration"],
            "fps": meta["fps"],
            "frames": meta["frames"],
            "has_audio": meta["has_audio"],
        })
        idx += 1
    return pairs


def refresh_pairs():
    global pairs_cache
    if current_folder:
        pairs_cache = load_pairs(current_folder)
    else:
        pairs_cache = []


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
    return send_from_directory(current_folder, filename)


@app.route("/category_icon/<path:filename>")
def category_icon(filename):
    return send_from_directory(APP_DIR / "images", filename)


@app.route("/switch/image", methods=["POST", "GET"])
def switch_to_image():
    remember_app("image")
    launch_local_app("imageprep.py", 5000)
    exit_soon()
    return switch_page("http://127.0.0.1:5000/", "Image Prep")


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
    if not folder:
        return redirect(url_for("index"))
    current_folder = folder
    refresh_pairs()
    message = f"Opened folder: {folder}"
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


@app.post("/save_caption")
def save_caption():
    global message
    data = request.get_json(force=True)
    name = data["name"]
    caption = data.get("caption", "")
    txt = Path(current_folder) / Path(name).with_suffix(".txt")
    txt.write_text(caption, encoding="utf-8")
    refresh_pairs()
    return jsonify({"ok": True})


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
    src_video = Path(current_folder) / old_name
    src_txt = src_video.with_suffix(".txt")
    dst_video = safe_stem_path(current_folder, src_video.stem, src_video.suffix)
    dst_txt = dst_video.with_suffix(".txt")
    shutil.copy2(src_video, dst_video)
    if src_txt.exists():
        shutil.copy2(src_txt, dst_txt)
    else:
        dst_txt.write_text("", encoding="utf-8")
    refresh_pairs()
    return jsonify({"ok": True, "new_name": dst_video.name})


@app.post("/delete_pair")
def delete_pair():
    data = request.get_json(force=True)
    name = data["name"]
    video = Path(current_folder) / name
    txt = video.with_suffix(".txt")
    if video.exists():
        video.unlink()
    if txt.exists():
        txt.unlink()
    refresh_pairs()
    return jsonify({"ok": True})


@app.post("/backup")
def backup():
    global message
    if not current_folder:
        message = "No folder is open."
        return redirect(url_for("index"))
    backup_dir = Path(current_folder) / "_backup_videoprep"
    backup_dir.mkdir(exist_ok=True)
    count = 0
    for p in Path(current_folder).iterdir():
        if p.is_file() and (p.suffix.lower() in VIDEO_EXTENSIONS or p.suffix.lower() == ".txt"):
            shutil.copy2(p, backup_dir / p.name)
            count += 1
    message = f"Backup complete: {count} file(s)."
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


@app.get("/stats")
def stats():
    if not current_folder:
        return jsonify({"ok": False, "error": "No folder is open."}), 400

    videos = list(pairs_cache)
    captions = 0
    audio = 0
    resolutions = {}
    fps_counts = {}
    for p in videos:
        if str(p.get("caption") or "").strip():
            captions += 1
        if p.get("has_audio"):
            audio += 1
        res = f"{int(p.get('width') or 0)}x{int(p.get('height') or 0)}"
        resolutions[res] = resolutions.get(res, 0) + 1
        fps = float(p.get("fps") or 0.0)
        if fps > 0:
            fps_label = f"{fps:.3f}".rstrip("0").rstrip(".")
            fps_counts[fps_label] = fps_counts.get(fps_label, 0) + 1

    resolution_rows = [
        {"resolution": key, "count": value}
        for key, value in sorted(resolutions.items(), key=lambda item: (-item[1], item[0]))
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
        "resolutions": resolution_rows,
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
    mute = bool(data.get("mute", False))

    crop_x_ratio = float(data.get("crop_x_ratio", 0.0))
    crop_y_ratio = float(data.get("crop_y_ratio", 0.0))
    crop_rect_w_ratio = float(data.get("crop_rect_w_ratio", 1.0))
    crop_rect_h_ratio = float(data.get("crop_rect_h_ratio", 1.0))

    video_path = Path(current_folder) / name
    meta = probe_video(str(video_path))
    fps = meta["fps"] or 24.0
    total_frames = max(1, int(meta.get("frames") or 0) or int(round((meta.get("duration") or 0.0) * fps)))
    start_frame = max(0, min(start_frame, total_frames - 1))
    end_frame = max(start_frame + 1, min(end_frame, total_frames))
    start_sec = max(0.0, start_frame / fps)
    end_sec = max(start_sec + (1.0 / fps), end_frame / fps)

    src_w, src_h = int(meta["width"]), int(meta["height"])
    if src_w <= 0 or src_h <= 0:
        return jsonify({"ok": False, "error": "Invalid source dimensions."}), 400

    target_ratio = crop_w / max(crop_h, 1)

    crop_x_ratio = max(0.0, min(crop_x_ratio, 1.0))
    crop_y_ratio = max(0.0, min(crop_y_ratio, 1.0))
    crop_rect_w_ratio = max(0.01, min(crop_rect_w_ratio, 1.0))
    crop_rect_h_ratio = max(0.01, min(crop_rect_h_ratio, 1.0))

    rect_w = max(2, int(round(src_w * crop_rect_w_ratio)))
    rect_h = max(2, int(round(src_h * crop_rect_h_ratio)))

    rect_ratio = rect_w / max(rect_h, 1)
    if rect_ratio > target_ratio:
        rect_w = max(2, int(round(rect_h * target_ratio)))
    else:
        rect_h = max(2, int(round(rect_w / target_ratio)))

    crop_x = int(round(src_w * crop_x_ratio))
    crop_y = int(round(src_h * crop_y_ratio))

    crop_x = max(0, min(crop_x, src_w - rect_w))
    crop_y = max(0, min(crop_y, src_h - rect_h))

    temp = video_path.with_name(video_path.stem + "_edited" + video_path.suffix)
    cmd = [
        FFMPEG_EXE, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
        "-ss", f"{start_sec:.6f}",
        "-to", f"{end_sec:.6f}",
        "-i", str(video_path),
        "-vf", f"crop={rect_w}:{rect_h}:{crop_x}:{crop_y},scale={crop_w}:{crop_h}",
        "-c:v", "libx264",
        "-preset", "medium",
        "-crf", "18",
    ]
    if mute:
        cmd += ["-an"]
    else:
        cmd += ["-c:a", "aac", "-b:a", "192k"]
    cmd += [str(temp)]

    try:
        hidden_check_call(cmd)
    except Exception as e:
        if temp.exists():
            temp.unlink(missing_ok=True)
        return jsonify({"ok": False, "error": str(e)}), 500

    backup = video_path.with_name(video_path.stem + "_original" + video_path.suffix)
    if not backup.exists():
        shutil.copy2(video_path, backup)
    temp.replace(video_path)
    refresh_pairs()
    return jsonify({"ok": True})


@app.post("/convert_fps")
def convert_fps():
    global message
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

    converted = 0
    errors = []
    backup_dir = folder / "BACKUP"
    if make_backup:
        backup_dir.mkdir(exist_ok=True)

    for video_path in videos:
        temp = video_path.with_name(video_path.stem + "_fps_tmp" + video_path.suffix)
        backup = backup_dir / video_path.name if make_backup else None
        cmd = [
            FFMPEG_EXE, "-hide_banner", "-nostdin", "-loglevel", "error", "-y",
            "-i", str(video_path),
            "-map", "0:v:0",
            "-map", "0:a?",
            "-vf", f"fps={target_fps:g}",
            "-c:v", "libx264",
            "-preset", "medium",
            "-crf", str(crf),
            "-c:a", "copy",
            str(temp),
        ]
        try:
            hidden_check_call(cmd)
            if backup is not None and not backup.exists():
                shutil.copy2(video_path, backup)
            temp.replace(video_path)
            converted += 1
        except Exception as e:
            if temp.exists():
                temp.unlink(missing_ok=True)
            errors.append(f"{video_path.name}: {e}")

    refresh_pairs()
    if errors:
        message = f"Converted FPS for {converted} video(s). {len(errors)} failed."
        return jsonify({
            "ok": False,
            "converted": converted,
            "error": "\n".join(errors[:5]),
        }), 500

    message = f"Converted FPS for {converted} video(s) to {target_fps:g}."
    return jsonify({"ok": True, "converted": converted, "backup": make_backup})


def open_browser():
    webbrowser.open("http://127.0.0.1:5001/")


TEMPLATE = r"""
<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>Video Dataset Preparation</title>
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
  --video-card-min-width:430px;
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
  grid-template-columns: repeat(auto-fill,minmax(min(100%, var(--video-card-min-width, 430px)),1fr));
  gap:12px;
}
.statusbar{
  position:fixed;
  left:0;
  right:0;
  bottom:0;
  z-index:150;
  min-height:28px;
  display:flex;
  justify-content:flex-end;
  align-items:center;
  padding:5px 12px;
  background:#141414;
  border-top:1px solid var(--border);
  color:var(--muted);
  font-size:12px;
  font-weight:650;
  box-shadow:0 -1px 0 rgba(255,255,255,.03);
}
.card{
  background:#141414;border:1px solid var(--border);border-radius:8px;
  box-shadow:none;padding:10px;display:flex;flex-direction:column;gap:10px;
}
.card-head{
  display:flex;justify-content:space-between;align-items:center;gap:8px;
  padding-bottom:2px;
}
.name{
  font-weight:750;white-space:nowrap;overflow:hidden;text-overflow:ellipsis;flex:1;
  padding:4px 2px;border-radius:6px;color:#e5e7eb;
}
.name.editing{background:#1f1f1f;outline:1px solid var(--accent);padding-inline:6px}
video{
  width:100%;background:#000;border-radius:6px;max-height:var(--video-display-height);display:block;
}
.video-stage{
  position:relative;
  width:100%;
  display:inline-block;
  overflow:hidden;
  border-radius:6px;
  border:1px solid #242424;
  background:#000;
  cursor:crosshair;
  max-height:var(--video-display-height);
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
.play-toggle-btn{min-width:34px;text-align:center}
.controls{display:flex;gap:6px;align-items:center;flex-wrap:wrap}
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
textarea{
  width:100%;height:var(--caption-text-height);min-height:60px;resize:none;
  background:#0a0a0a;
  color:#e5e7eb;
  border-color:#252525;
  line-height:1.4;
}
textarea.unsaved-caption{border-color:rgba(248,113,113,.9);box-shadow:0 0 0 1px rgba(248,113,113,.25) inset;}
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
.modal{
  position:fixed;inset:0;background:rgba(0,0,0,.72);display:none;
  align-items:center;justify-content:center;z-index:200;
  padding:18px;
}
.modal.open{display:flex}
.modal-card{
  width:min(720px,calc(100vw - 24px));background:#141414;border:1px solid var(--border);
  border-radius:8px;box-shadow:var(--shadow);padding:14px;
}
.two{display:grid;grid-template-columns:1fr 1fr;gap:10px}
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
<div class="top">
  <div class="mode-stack">
    <div class="mode-head"><img class="mode-icon" src="/category_icon/btn_switch_video.png" alt=""><div class="mode-label">video mode</div></div>
    <form method="post" action="/switch/image"><button type="submit" title="Switch to Image Prep"><span class="toolbar-btn-content">Switch</span></button></form>
  </div>
  <div class="row" style="margin-bottom:8px;">
    <form method="post" action="/open_folder"><button type="submit" title="Open a video folder"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_open_folder.png" alt="">Open</span></button></form>
    <form method="post" action="/add_files"><button type="submit" title="Add video files"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_add_files.png" alt="">Add</span></button></form>
    <button id="openFolderInExplorerBtn" type="button" title="Show the opened folder in File Explorer"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_open_file_manager.png" alt="">Show</span></button>
    <form method="post" action="/close_folder"><button type="submit" title="Close Folder"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_close_folder.png" alt="">Close</span></button></form>
    <button id="convertBtn" type="button">Convert</button>
    <form method="post" action="/backup"><button type="submit" title="Back up video and caption pairs"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_backup.png" alt="">Backup</span></button></form>
    <button id="captionStubBtn" type="button" title="Generate captions"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_caption.png" alt="">Caption</span></button>
    <button id="textToolsBtn" type="button" title="Batch edit caption text"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_text_tools.png" alt="">Text</span></button>
    <button type="button" id="openStatsModalBtn" title="Show dataset statistics"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_statistics.png" alt="">Stats</span></button>
    <button type="button" id="autoCropAllBtn" title="Auto crop every video"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_auto_crop_all.png" alt="">Auto crop</span></button>
    <button type="button" id="resetAllBtn" title="Reset unsaved edits"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_reset_all.png" alt="">Reset</span></button>
    <button type="button" id="renameAllBtn" title="Rename all video and caption pairs"><span class="toolbar-btn-content"><img class="toolbar-btn-icon" src="/category_icon/btn_rename_all.png" alt="">Rename</span></button>
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
  {% if current_folder or message %}
    <div class="row" style="margin-top:8px;">
      {% if current_folder %}<span class="muted">Opened folder: {{ current_folder }}</span>{% elif message %}<span class="muted">{{ message }}</span>{% endif %}
    </div>
  {% endif %}
</div>

<div class="main">
  {% if not pairs %}
    <div class="notice">No folder is open or the folder contains no supported videos.</div>
  {% else %}
    <div class="grid">
      {% for pair in pairs %}
      <div class="card" data-name="{{ pair.name }}" data-fps="{{ "%.6f"|format(pair.fps) }}" data-frames="{{ pair.frames }}">
        <div class="card-head">
          <div class="name" data-name="{{ pair.name }}">{{ pair.name }}</div>
          <div class="group">
            <button class="small clone-btn">Clone</button>
            <button class="small delete-btn">Delete</button>
          </div>
        </div>

        <div class="video-stage">
          <video src="/video/{{ pair.name | urlencode }}" preload="metadata"></video>
          <div class="crop-overlay" data-label="">
            <div class="crop-handle nw"></div>
            <div class="crop-handle ne"></div>
            <div class="crop-handle sw"></div>
            <div class="crop-handle se"></div>
          </div>
        </div>
        <input class="seek-bar" type="range" min="0" max="{{ pair.frames }}" step="1" value="0">

        <div class="controls playback-row">
          <button class="small play-toggle-btn icon-only-btn" title="Play/Pause" aria-label="Play/Pause">
            <span class="media-icon media-play" aria-hidden="true"></span>
          </button>
          <button class="small stop-btn icon-only-btn" title="Stop" aria-label="Stop">
            <span class="media-icon media-stop" aria-hidden="true"></span>
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
            <span class="trim-count">{{ pair.frames }} frames selected</span>
            <span>{{ pair.frames }}</span>
          </div>
        </div>

        <div class="controls">
          <button class="small save-edit-btn">Save video</button>
          <label><input type="checkbox" class="mute-export"> Export muted</label>
        </div>

        <textarea class="caption">{{ pair.caption }}</textarea>
        <div class="caption-stats">
          <span class="caption-char-count">0 chars</span>
          <span class="caption-token-count">0 tokens</span>
        </div>
        <div class="controls">
          <button class="small save-caption-btn">Save caption</button>
        </div>
      </div>
      {% endfor %}
    </div>
  {% endif %}
</div>

<div class="statusbar">{{ pairs|length }} video{% if pairs|length != 1 %}s{% endif %}.</div>

<div id="textModal" class="modal">
  <div class="modal-card">
    <div class="row" style="justify-content:space-between;margin-bottom:10px;">
      <strong>Text tools</strong>
      <button id="closeTextModalBtn">Close</button>
    </div>
    <form method="post" action="/text_replace" class="two" style="margin-bottom:12px;">
      <div><label>Find</label><input name="find"></div>
      <div><label>Replace</label><input name="replace"></div>
      <div style="grid-column:1 / -1;"><button>Replace in all captions</button></div>
    </form>
    <form method="post" action="/text_count" class="two">
      <div><label>Count text</label><input name="count"></div>
      <div style="align-self:end;"><button>Count in all captions</button></div>
    </form>
  </div>
</div>

<div id="captionModal" class="modal">
  <div class="modal-card">
    <div class="row" style="justify-content:space-between;margin-bottom:10px;">
      <strong>Caption</strong>
      <button id="closeCaptionModalBtn">Close</button>
    </div>
    <div class="notice">Caption backend is a placeholder in this version. Text tools and caption fields already work.</div>
  </div>
</div>

<div id="convertModal" class="modal">
  <div class="modal-card">
    <div class="row" style="justify-content:space-between;margin-bottom:10px;">
      <strong>Convert</strong>
      <button id="closeConvertModalBtn">Close</button>
    </div>
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
      <div class="modal-actions" style="grid-column:1 / -1;">
        <span class="modal-status" id="convertStatusText">Idle</span>
        <button type="submit" id="convertFpsStartBtn">Convert FPS</button>
      </div>
    </form>
  </div>
</div>

<div id="statsModal" class="modal">
  <div class="modal-card">
    <div class="row" style="justify-content:space-between;margin-bottom:10px;">
      <strong>Stats</strong>
      <button id="closeStatsModalBtn">Close</button>
    </div>
    <div id="statsContent" class="notice">Loading...</div>
  </div>
</div>

<div id="renameAllModal" class="modal">
  <div class="modal-card">
    <div class="row" style="justify-content:space-between;margin-bottom:10px;">
      <strong>Rename</strong>
      <button id="closeRenameAllModalBtn">Close</button>
    </div>
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

<script>
const BUCKETS = {{ bucket_options_json | safe }};

function clamp(v, min, max) {
  return Math.max(min, Math.min(max, v));
}

const cropEditors = [];

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

function setVideoSize(value) {
  const size = clamp(value, 220, 720);
  localStorage.setItem('video_prep_video_size', String(size));
  document.documentElement.style.setProperty('--video-display-height', `${size}px`);
  document.documentElement.style.setProperty('--video-card-min-width', `${size + 110}px`);
  const slider = document.getElementById('videoSizeSlider');
  const valueEl = document.getElementById('videoSizeValue');
  if (slider) slider.value = String(size);
  if (valueEl) valueEl.textContent = String(size);
  window.requestAnimationFrame(() => {
    cropEditors.forEach(editor => editor.redraw && editor.redraw());
  });
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
    dragMode: null, // create | move | nw | ne | sw | se
    pointerStart: null,
    rectStart: null,
    fps: parseFloat(card.dataset.fps || '24') || 24,
    frames: parseInt(card.dataset.frames || '0', 10) || 0,
    bucket: { w: 1024, h: 1024, label: '1024x1024 (1:1)' },
  };

  function displayedSize() {
    return { w: video.clientWidth, h: video.clientHeight };
  }

  function mediaBox() {
    const boxW = video.clientWidth;
    const boxH = video.clientHeight;
    const srcW = video.videoWidth || boxW || 1;
    const srcH = video.videoHeight || boxH || 1;
    const scale = Math.min(boxW / srcW, boxH / srcH);
    const w = srcW * scale;
    const h = srcH * scale;
    const x = (boxW - w) / 2;
    const y = (boxH - h) / 2;
    return { x, y, w, h };
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

  function trimFrames() {
    const maxFrame = Math.max(0, state.frames);
    const minGap = maxFrame > 0 ? 1 : 0;
    let start = parseInt(startFrameInput?.value || '0', 10) || 0;
    let end = parseInt(endFrameInput?.value || String(maxFrame), 10) || maxFrame;
    start = clamp(start, 0, Math.max(0, maxFrame - minGap));
    end = clamp(end, start + minGap, maxFrame);
    return { start, end, maxFrame };
  }

  function updateTrimTimeline() {
    if (!startFrameInput || !endFrameInput) return;
    const { start, end, maxFrame } = trimFrames();
    startFrameInput.min = "0";
    startFrameInput.max = String(maxFrame);
    startFrameInput.step = "1";
    startFrameInput.value = String(start);
    endFrameInput.min = "0";
    endFrameInput.max = String(maxFrame);
    endFrameInput.step = "1";
    endFrameInput.value = String(end);
    if (trimReadout) trimReadout.textContent = `Trim: ${start} - ${end}`;
    if (trimCount) trimCount.textContent = `${Math.max(0, end - start)} frames selected`;
    if (trimRangeFill) {
      const denom = Math.max(maxFrame, 1);
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
    return {
      w: video.videoWidth * ((state.rect.w) / Math.max(m.w, 1)),
      h: video.videoHeight * ((state.rect.h) / Math.max(m.h, 1)),
    };
  }

  function draw() {
    if (!state.hasCrop) {
      overlay.style.display = 'none';
      return;
    }

    state.rect = clampRectToMedia(state.rect);

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
    const srcW = video.videoWidth || m.w || 1;
    const srcH = video.videoHeight || m.h || 1;
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
    state.rect = clampRectToMedia({
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
    state.rect = clampRectToMedia(snappedRectFromRaw(rawRect, anchorMode.toLowerCase()));
    draw();
  }

  function applyMove(clientX, clientY) {
    const p = stagePoint(clientX, clientY);
    const dx = p.x - state.pointerStart.x;
    const dy = p.y - state.pointerStart.y;
    state.rect = clampRectToMedia({
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

    state.rect = clampRectToMedia(snappedRectFromRaw(rawRect, handle));
    draw();
  }

  function pointerDown(e) {
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
  overlay.addEventListener('pointerdown', pointerDown);

  function resnapToCropBase() {
      if (!state.hasCrop) return;
      updateBucketFromRect(state.rect);
      // snap current crop to new nearest bucket while keeping center
      const cx = state.rect.x + state.rect.w / 2;
      const cy = state.rect.y + state.rect.h / 2;
      const raw = { x: 0, y: 0, w: state.rect.w, h: state.rect.h };
      const snapped = snappedRectFromRaw(raw, 'se');
      state.rect = clampRectToMedia({ x: cx - snapped.w / 2, y: cy - snapped.h / 2, w: snapped.w, h: snapped.h });
      draw();
  }

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
  updateTrimTimeline();

  return {
    redraw() {
      draw();
    },
    autoCrop,
    resnapToCropBase,
    getCropPayload() {
      if (!state.hasCrop) {
        const bucket = chooseNearestBucket(selectedCropBase(), video.videoWidth / Math.max(video.videoHeight, 1), video.videoWidth * video.videoHeight);
        return {
          crop_w: bucket.w,
          crop_h: bucket.h,
          crop_x_ratio: 0,
          crop_y_ratio: 0,
          crop_rect_w_ratio: 1,
          crop_rect_h_ratio: 1,
        };
      }

      const m = mediaBox();
      return {
        crop_w: state.bucket.w,
        crop_h: state.bucket.h,
        crop_x_ratio: (state.rect.x - m.x) / Math.max(m.w, 1),
        crop_y_ratio: (state.rect.y - m.y) / Math.max(m.h, 1),
        crop_rect_w_ratio: state.rect.w / Math.max(m.w, 1),
        crop_rect_h_ratio: state.rect.h / Math.max(m.h, 1),
      };
    }
  };
}

document.querySelectorAll('.card').forEach(card => {
  const video = card.querySelector('video');
  const playToggleBtn = card.querySelector('.play-toggle-btn');
  const stopBtn = card.querySelector('.stop-btn');
  const caption = card.querySelector('.caption');
  const initialCaptionValue = caption ? caption.value : '';
  const saveCaptionBtn = card.querySelector('.save-caption-btn');
  const cloneBtn = card.querySelector('.clone-btn');
  const deleteBtn = card.querySelector('.delete-btn');
  const saveEditBtn = card.querySelector('.save-edit-btn');
  const startFrame = card.querySelector('.start-frame');
  const endFrame = card.querySelector('.end-frame');
  const nameEl = card.querySelector('.name');

  function syncPlayLabel() {
    if (!playToggleBtn) return;
    const iconClass = video.paused ? 'media-play' : 'media-pause';
    playToggleBtn.innerHTML = `<span class="media-icon ${iconClass}" aria-hidden="true"></span>`;
  }

  if (playToggleBtn) playToggleBtn.addEventListener('click', () => {
    if (video.paused) video.play().catch(() => {});
    else video.pause();
  });
  if (stopBtn) stopBtn.addEventListener('click', () => {
    video.pause();
    video.currentTime = 0;
    syncPlayLabel();
  });
  video.addEventListener('play', syncPlayLabel);
  video.addEventListener('pause', syncPlayLabel);
  video.addEventListener('ended', syncPlayLabel);
  syncPlayLabel();

  const cropEditor = createCropEditor(card);
  cropEditors.push(cropEditor);

  function syncCaptionDirty() {
    if (!caption) return;
    const dirty = caption.value !== initialCaptionValueRef.value;
    caption.classList.toggle('unsaved-caption', dirty);
    updateCaptionStats(caption);
  }

  const initialCaptionValueRef = { value: initialCaptionValue };
  if (caption) {
    caption.addEventListener('input', syncCaptionDirty);
    syncCaptionDirty();
  }

  if (saveCaptionBtn) saveCaptionBtn.addEventListener('click', async () => {
    await fetch('/save_caption', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: card.dataset.name, caption: caption.value})
    });
    initialCaptionValueRef.value = caption.value;
    syncCaptionDirty();
  });

  if (cloneBtn) cloneBtn.addEventListener('click', async () => {
    const res = await fetch('/clone_pair', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: card.dataset.name})
    });
    const data = await res.json();
    if (data.ok) location.reload();
  });

  if (deleteBtn) deleteBtn.addEventListener('click', async () => {
    if (!confirm(`Delete ${card.dataset.name}?`)) return;
    const res = await fetch('/delete_pair', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({name: card.dataset.name})
    });
    const data = await res.json();
    if (data.ok) location.reload();
  });

  if (saveEditBtn) saveEditBtn.addEventListener('click', async () => {
    const crop = cropEditor.getCropPayload();
    const res = await fetch('/save_edit', {
      method: 'POST',
      headers: {'Content-Type': 'application/json'},
      body: JSON.stringify({
        name: card.dataset.name,
        crop_w: crop.crop_w,
        crop_h: crop.crop_h,
        crop_x_ratio: crop.crop_x_ratio,
        crop_y_ratio: crop.crop_y_ratio,
        crop_rect_w_ratio: crop.crop_rect_w_ratio,
        crop_rect_h_ratio: crop.crop_rect_h_ratio,
        start_frame: parseInt(startFrame.value || '0', 10),
        end_frame: parseInt(endFrame.value || '0', 10),
        mute: card.querySelector('.mute-export').checked
      })
    });
    const data = await res.json();
    if (!data.ok) alert(data.error || 'Save failed');
    else location.reload();
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
      const res = await fetch('/rename_pair', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({old_name: oldName, new_stem: newStem})
      });
      const data = await res.json();
      if (!data.ok) {
        alert(data.error || 'Rename failed');
        nameEl.textContent = oldName;
        return;
      }
      location.reload();
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

document.getElementById('openFolderInExplorerBtn').onclick = async () => {
  {% if current_folder %}
  const path = {{ current_folder|tojson }};
  const a = document.createElement('a');
  a.href = 'file:///' + path.replace(/\\/g, '/');
  a.click();
  {% endif %}
};

const textModal = document.getElementById('textModal');
const captionModal = document.getElementById('captionModal');
const convertModal = document.getElementById('convertModal');
const statsModal = document.getElementById('statsModal');
const renameAllModal = document.getElementById('renameAllModal');
document.getElementById('textToolsBtn').onclick = () => textModal.classList.add('open');
document.getElementById('closeTextModalBtn').onclick = () => textModal.classList.remove('open');
document.getElementById('captionStubBtn').onclick = () => captionModal.classList.add('open');
document.getElementById('closeCaptionModalBtn').onclick = () => captionModal.classList.remove('open');
document.getElementById('convertBtn').onclick = () => convertModal.classList.add('open');
document.getElementById('closeConvertModalBtn').onclick = () => convertModal.classList.remove('open');
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

function renderStats(data) {
  const resolutionRows = (data.resolutions || []).map(item =>
    `<div style="display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;border-bottom:1px solid var(--border);padding:3px 0;"><span>${htmlEscape(item.resolution)}</span><b>${item.count}</b></div>`
  ).join('') || '<div class="muted">No resolution data</div>';
  const fpsRows = (data.fps_values || []).map(item =>
    `<div style="display:grid;grid-template-columns:minmax(0,1fr) auto;gap:8px;border-bottom:1px solid var(--border);padding:3px 0;"><span>${htmlEscape(item.fps)} FPS</span><b>${item.count}</b></div>`
  ).join('') || '<div class="muted">No FPS data</div>';
  return `
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(120px,1fr));gap:8px;margin-bottom:12px;">
      <div><div class="muted">Videos</div><strong>${data.total_videos || 0}</strong></div>
      <div><div class="muted">Captions</div><strong>${data.total_captions || 0}</strong></div>
      <div><div class="muted">Audio</div><strong>${data.audio_videos || 0}</strong></div>
      <div><div class="muted">Mute</div><strong>${data.mute_videos || 0}</strong></div>
    </div>
    <div style="display:grid;grid-template-columns:repeat(auto-fit,minmax(220px,1fr));gap:12px;">
      <div><strong>Resolutions</strong><div style="margin-top:6px;">${resolutionRows}</div></div>
      <div><strong>FPS</strong><div style="margin-top:6px;">${fpsRows}</div></div>
    </div>
  `;
}

document.getElementById('openStatsModalBtn')?.addEventListener('click', async () => {
  const content = document.getElementById('statsContent');
  statsModal.classList.add('open');
  if (content) content.textContent = 'Loading...';
  try {
    const res = await fetch('/stats');
    const data = await res.json();
    if (!res.ok || !data.ok) throw new Error(data.error || 'Stats failed');
    if (content) content.innerHTML = renderStats(data);
  } catch (err) {
    if (content) content.textContent = err.message || 'Stats failed';
  }
});

document.getElementById('autoCropAllBtn')?.addEventListener('click', () => {
  cropEditors.forEach(editor => editor.autoCrop && editor.autoCrop());
});

document.getElementById('resetAllBtn')?.addEventListener('click', () => {
  if (confirm('Reset unsaved video edits and captions?')) location.reload();
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
  if (!confirm(`Rename every opened video pair using prefix "${prefix}"?`)) return;
  if (status) status.textContent = 'Renaming...';
  if (startBtn) startBtn.disabled = true;
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
    if (status) status.textContent = err.message || 'Rename failed';
    if (startBtn) startBtn.disabled = false;
  }
});

document.getElementById('convertFpsForm')?.addEventListener('submit', async (e) => {
  e.preventDefault();
  const fps = parseFloat(document.getElementById('convertFpsInput')?.value || '0');
  const crf = parseInt(document.getElementById('convertQualitySelect')?.value || '18', 10);
  const backup = !!document.getElementById('convertBackupCheckbox')?.checked;
  const status = document.getElementById('convertStatusText');
  const startBtn = document.getElementById('convertFpsStartBtn');
  if (!Number.isFinite(fps) || fps <= 0 || fps > 240) {
    if (status) status.textContent = 'FPS must be between 0 and 240.';
    return;
  }
  if (!Number.isFinite(crf) || crf < 0 || crf > 30) {
    if (status) status.textContent = 'Select a valid quality setting.';
    return;
  }
  const backupText = backup ? 'Backups will be saved to the BACKUP folder.' : 'No backups will be created.';
  if (!confirm(`Convert every opened video to ${fps} FPS using CRF ${crf}?\n\n${backupText}`)) return;
  if (status) status.textContent = 'Converting...';
  if (startBtn) startBtn.disabled = true;
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
    if (status) status.textContent = `Converted ${data.converted || 0} video(s).`;
    location.reload();
  } catch (err) {
    if (status) status.textContent = err.message || 'Convert failed';
    if (startBtn) startBtn.disabled = false;
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
    convertModal.classList.remove('open');
    statsModal.classList.remove('open');
    renameAllModal.classList.remove('open');
  }
});
</script>
</body>
</html>
"""


if __name__ == "__main__":
    print("Starting Video Dataset Prep Tool...")
    app.run("127.0.0.1", 5001, debug=False)
