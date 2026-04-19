#!/usr/bin/env python3
# NOTE: This software is intended for private LAN use only and is not designed
# to be exposed directly to the public internet. If remote access is needed,
# place it behind a properly secured reverse proxy, VPN, or other access
# control layer rather than exposing this service itself.

import base64
import datetime as dt
import io
import ipaddress
import json
import logging
import html
import os
import shutil
import signal
import socketserver
import subprocess
import sys
import threading
import time
import mimetypes
from http import server
from pathlib import Path
from threading import Condition
from urllib.parse import parse_qs, quote, urlparse

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder, MJPEGEncoder
from picamera2.outputs import FileOutput

try:
    from gpiozero import DigitalInputDevice, OutputDevice, PWMLED
except Exception:  # pragma: no cover - depends on target system packages
    DigitalInputDevice = None
    OutputDevice = None
    PWMLED = None

try:
    import RPi.GPIO as RPI_GPIO
except Exception:  # pragma: no cover - depends on target system packages
    RPI_GPIO = None


# -------------------- Helpers --------------------
def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def optional_bool_env(name: str):
    raw = os.getenv(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {name}: {raw!r}")


def env_int(name: str, default: int | None = None) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    return int(raw)


def parse_time_of_day(raw: str, *, name: str) -> dt.time:
    value = raw.strip()
    for fmt in ("%H:%M", "%H:%M:%S"):
        try:
            return dt.datetime.strptime(value, fmt).time()
        except ValueError:
            pass
    raise ValueError(f"Invalid time value for {name}: {raw!r}. Expected HH:MM or HH:MM:SS")


def seconds_since_midnight(value: dt.time) -> int:
    return (value.hour * 3600) + (value.minute * 60) + value.second


def night_mode_active_now(start: dt.time, end: dt.time, *, now: dt.datetime | None = None) -> bool:
    current = (now or dt.datetime.now().astimezone()).time()
    current_seconds = seconds_since_midnight(current)
    start_seconds = seconds_since_midnight(start)
    end_seconds = seconds_since_midnight(end)

    if start_seconds == end_seconds:
        return False
    if start_seconds < end_seconds:
        return start_seconds <= current_seconds < end_seconds
    return current_seconds >= start_seconds or current_seconds < end_seconds


def gb_to_bytes(x: float) -> int:
    return int(x * (1024 ** 3))


def ensure_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)


def daily_dir(root: Path) -> Path:
    now = dt.datetime.now()
    path = root / f"{now.year:04d}" / f"{now.month:02d}" / f"{now.day:02d}"
    ensure_dir(path)
    return path


def new_filename(root: Path, reason: str, ext: str) -> Path:
    now = dt.datetime.now().astimezone()
    ts = now.strftime("%Y-%m-%dT%H%M%S%z")
    return daily_dir(root) / f"{ts}_{reason}.{ext}"


def timing_safe_stop_encoder(picam2: Picamera2, encoder_obj):
    try:
        picam2.stop_encoder(encoders=encoder_obj)
    except TypeError:
        picam2.stop_encoder(encoder_obj)


def timing_safe_start_encoder(picam2: Picamera2, encoder_obj, output_obj, *, name: str):
    try:
        picam2.start_encoder(encoder_obj, output_obj, name=name)
    except TypeError:
        picam2.start_encoder(encoder_obj, output_obj)


def default_route_iface() -> str:
    try:
        out = subprocess.check_output(["ip", "-4", "route", "show", "default"], text=True).strip()
        parts = out.split()
        if "dev" in parts:
            return parts[parts.index("dev") + 1]
    except Exception:
        pass
    return "wlan0"


def wifi_iface() -> str:
    return WIFI_IFACE_ENV or default_route_iface()


def get_local_ipv4_networks(iface_name: str):
    nets = [ipaddress.ip_network("127.0.0.0/8")]
    out = subprocess.check_output(["ip", "-j", "-4", "addr"], text=True)
    data = json.loads(out)
    for iface in data:
        if iface.get("ifname", "") != iface_name:
            continue
        for addr_info in iface.get("addr_info", []):
            if addr_info.get("family") != "inet":
                continue
            ip = addr_info.get("local")
            prefix_len = addr_info.get("prefixlen")
            if ip and prefix_len is not None:
                nets.append(ipaddress.ip_network(f"{ip}/{prefix_len}", strict=False))
    return nets


def build_page(stream_size: tuple[int, int]) -> str:
    width, height = stream_size
    return f"""\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>NestCam Live</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f172a;
      --bg2: #111827;
      --panel: #111827;
      --border: #334155;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --accent: #60a5fa;
      --accent2: #93c5fd;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: linear-gradient(180deg, var(--bg), var(--bg2));
      color: var(--text);
    }}
    .wrap {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 20px 40px;
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 20px;
      flex-wrap: wrap;
    }}
    h1 {{
      margin: 0;
      font-size: 1.8rem;
      font-weight: 700;
      letter-spacing: 0.01em;
    }}
    .subtitle {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 0.98rem;
    }}
    .nav {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .nav a {{
      color: var(--text);
      text-decoration: none;
      background: rgba(17, 24, 39, 0.88);
      border: 1px solid var(--border);
      padding: 9px 14px;
      border-radius: 999px;
      font-size: 0.95rem;
    }}
    .nav a:hover {{
      border-color: var(--accent);
      color: white;
    }}
    .panel {{
      background: rgba(17, 24, 39, 0.88);
      border: 1px solid var(--border);
      border-radius: 20px;
      padding: 16px;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.25);
    }}
    .stream-frame {{
      background: black;
      border-radius: 14px;
      overflow: hidden;
      border: 1px solid rgba(148, 163, 184, 0.2);
    }}
    .stream-frame img {{
      display: block;
      width: 100%;
      height: auto;
      aspect-ratio: {width} / {height};
      background: black;
    }}
    .meta {{
      margin-top: 14px;
      display: flex;
      justify-content: space-between;
      gap: 12px;
      flex-wrap: wrap;
      color: var(--muted);
      font-size: 0.94rem;
    }}
    .meta strong {{ color: var(--accent2); font-weight: 600; }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div>
        <h1>NestCam</h1>
        <div class="subtitle">Live from the NestCamDIY camera.</div>
      </div>
      <nav class="nav">
        <a href="/index.html">Live</a>
        <a href="/recordings">Recordings</a>
        <a href="/status.txt">Status</a>
      </nav>
    </div>
    <section class="panel">
      <div class="stream-frame">
        <img src="/stream.mjpg" alt="NestCamDIY live stream" />
      </div>
      <div class="meta">
        <span><strong>Preview resolution:</strong> {width} × {height}</span>
        <span>Refreshes continuously while this page is open.</span>
      </div>
    </section>
  </div>
</body>
</html>
"""


def parse_motion_pull(raw: str):
    value = raw.strip().lower()
    if value in {"up", "pullup", "pull_up", "true", "1"}:
        return True
    if value in {"down", "pulldown", "pull_down", "false", "0"}:
        return False
    if value in {"none", "off", "floating", ""}:
        return None
    raise ValueError(f"Unsupported MOTION_PULL value: {raw!r}")


def motion_pull_label(pull) -> str:
    if pull is True:
        return "up"
    if pull is False:
        return "down"
    return "none"


def validate_motion_input_config(*, pin: int | None, active_high: bool, pull) -> None:
    if pin is None:
        return
    if pull is None:
        return
    expected_active_high = (pull is False)
    if active_high != expected_active_high:
        raise ValueError(
            "Unsupported motion polarity/pull combination: "
            f"MOTION_PULL={motion_pull_label(pull)!r} requires "
            f"MOTION_ACTIVE_HIGH={1 if expected_active_high else 0} when using gpiozero DigitalInputDevice. "
            "Use MOTION_PULL=none for a floating input if you need the opposite polarity."
        )


def format_size(num_bytes: int) -> str:
    value = float(num_bytes)
    for unit in ["B", "KB", "MB", "GB", "TB"]:
        if value < 1024.0 or unit == "TB":
            if unit == "B":
                return f"{int(value)} {unit}"
            return f"{value:.1f} {unit}"
        value /= 1024.0
    return f"{num_bytes} B"


def parse_recording_datetime(path: Path):
    try:
        ts_text = path.name.split("_", 1)[0]
        return dt.datetime.strptime(ts_text, "%Y-%m-%dT%H%M%S%z")
    except Exception:
        return None


def recording_entries(root: Path):
    ensure_dir(root)
    entries = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        try:
            stat = path.stat()
            rel = path.relative_to(root).as_posix()
        except Exception as exc:
            logging.warning("Could not inspect recording %s: %s", path, exc)
            continue

        recorded_at = parse_recording_datetime(path)
        sort_ts = recorded_at.timestamp() if recorded_at is not None else stat.st_mtime
        entries.append({
            "path": path,
            "rel": rel,
            "size": stat.st_size,
            "mtime": stat.st_mtime,
            "recorded_at": recorded_at,
            "sort_ts": sort_ts,
        })

    entries.sort(key=lambda item: item["sort_ts"], reverse=True)
    return entries


def safe_recording_path(root: Path, rel_path: str) -> Path:
    candidate = (root / rel_path).resolve(strict=True)
    root_resolved = root.resolve(strict=True)
    candidate.relative_to(root_resolved)
    if not candidate.is_file():
        raise FileNotFoundError(candidate)
    return candidate


def build_recordings_page(root: Path) -> bytes:
    rows = []
    for entry in recording_entries(root):
        shown_dt = entry["recorded_at"] or dt.datetime.fromtimestamp(entry["mtime"]).astimezone()
        dt_text = shown_dt.astimezone().strftime("%Y-%m-%d %H:%M:%S %z")
        filename = html.escape(Path(entry["rel"]).name)
        rel_quoted = quote(entry["rel"], safe="/")
        size_text = html.escape(format_size(entry["size"]))
        rows.append(
            "<tr>"
            f'<td class="name"><a href="/recordings/view?f={rel_quoted}">{filename}</a></td>'
            f'<td class="date">{html.escape(dt_text)}</td>'
            f'<td class="size">{size_text}</td>'
            f'<td class="actions">'
            f'<a class="icon-link" href="/recordings/download?f={rel_quoted}" title="Download" aria-label="Download">{DOWNLOAD_ICON}</a>'
            f'<form class="inline-form" method="post" action="/recordings/delete">'
            f'<input type="hidden" name="f" value="{html.escape(entry["rel"], quote=True)}" />'
            f'<button type="submit" class="icon-button delete-button" title="Delete" aria-label="Delete">{DELETE_ICON}</button>'
            f'</form>'
            f'</td>'
            "</tr>"
        )

    if rows:
        listing = "\n".join(rows)
    else:
        listing = '<tr><td colspan="4" class="empty">No recordings found.</td></tr>'

    page = f"""\
<!doctype html>
<html lang="en">
<head>
  <meta charset="utf-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1" />
  <title>NestCam Recordings</title>
  <style>
    :root {{
      color-scheme: dark;
      --bg: #0f172a;
      --bg2: #111827;
      --panel: rgba(17, 24, 39, 0.88);
      --border: #334155;
      --text: #e5e7eb;
      --muted: #94a3b8;
      --accent: #60a5fa;
    }}
    * {{ box-sizing: border-box; }}
    body {{
      margin: 0;
      font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, Helvetica, Arial, sans-serif;
      background: linear-gradient(180deg, var(--bg), var(--bg2));
      color: var(--text);
    }}
    .wrap {{
      max-width: 1180px;
      margin: 0 auto;
      padding: 28px 20px 40px;
    }}
    .topbar {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      gap: 16px;
      margin-bottom: 20px;
      flex-wrap: wrap;
    }}
    h1 {{
      margin: 0;
      font-size: 1.8rem;
      font-weight: 700;
    }}
    .subtitle {{
      margin-top: 6px;
      color: var(--muted);
      font-size: 0.98rem;
    }}
    .nav {{
      display: flex;
      gap: 10px;
      flex-wrap: wrap;
    }}
    .nav a {{
      color: var(--text);
      text-decoration: none;
      background: rgba(17, 24, 39, 0.88);
      border: 1px solid var(--border);
      padding: 9px 14px;
      border-radius: 999px;
      font-size: 0.95rem;
    }}
    .nav a:hover {{
      border-color: var(--accent);
      color: white;
    }}
    .panel {{
      background: var(--panel);
      border: 1px solid var(--border);
      border-radius: 20px;
      overflow: hidden;
      box-shadow: 0 10px 30px rgba(0, 0, 0, 0.25);
    }}
    table {{
      width: 100%;
      border-collapse: collapse;
    }}
    th, td {{
      padding: 14px 16px;
      text-align: left;
      border-bottom: 1px solid rgba(148, 163, 184, 0.16);
      vertical-align: top;
    }}
    th {{
      color: var(--muted);
      font-size: 0.82rem;
      text-transform: uppercase;
      letter-spacing: 0.05em;
      background: rgba(15, 23, 42, 0.55);
    }}
    td.name a {{
      color: #bfdbfe;
      text-decoration: none;
    }}
    td.name a:hover {{
      text-decoration: underline;
    }}
    td.date, td.size {{
      white-space: nowrap;
      color: var(--text);
    }}
    td.actions {{
      white-space: nowrap;
      display: flex;
      gap: 10px;
      align-items: center;
    }}
    .icon-link, .icon-button {{
      display: inline-flex;
      align-items: center;
      justify-content: center;
      width: 34px;
      height: 34px;
      border-radius: 999px;
      color: #bfdbfe;
      border: 1px solid rgba(148, 163, 184, 0.22);
      background: rgba(15, 23, 42, 0.45);
      text-decoration: none;
    }}
    .icon-button {{
      padding: 0;
      cursor: pointer;
      font: inherit;
    }}
    .icon-link:hover, .icon-button:hover {{
      border-color: var(--accent);
      background: rgba(37, 99, 235, 0.14);
    }}
    .icon-link svg, .icon-button svg {{
      width: 18px;
      height: 18px;
      fill: currentColor;
      display: block;
    }}
    .inline-form {{
      display: inline;
      margin: 0;
      padding: 0;
    }}
    .delete-button {{
      color: #fca5a5;
    }}
    .delete-button:hover {{
      border-color: #ef4444;
      background: rgba(239, 68, 68, 0.12);
    }}
    .empty {{
      color: var(--muted);
    }}
    @media (max-width: 760px) {{
      th:nth-child(2), th:nth-child(3), td.date, td.size {{
        display: none;
      }}
    }}
  </style>
</head>
<body>
  <div class="wrap">
    <div class="topbar">
      <div>
        <h1>Recordings</h1>
        <div class="subtitle">Newest clips first. Click a filename to open it in the browser.</div>
      </div>
      <nav class="nav">
        <a href="/index.html">Live</a>
        <a href="/recordings">Recordings</a>
        <a href="/status.txt">Status</a>
      </nav>
    </div>
    <section class="panel">
      <table>
        <thead>
          <tr>
            <th>File</th>
            <th>Date</th>
            <th>Size</th>
            <th></th>
          </tr>
        </thead>
        <tbody>
{listing}
        </tbody>
      </table>
    </section>
  </div>
</body>
</html>
"""
    return page.encode("utf-8")


def guess_recording_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".h264", ".264"}:
        return "video/h264"
    if suffix in {".mjpg", ".mjpeg"}:
        return "video/x-motion-jpeg"

    content_type, _ = mimetypes.guess_type(path.name)
    return content_type or "application/octet-stream"


DOWNLOAD_ICON = (
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<path d="M12 3a1 1 0 0 1 1 1v8.59l2.3-2.29a1 1 0 1 1 1.4 1.41l-4 4a1 1 0 0 1-1.4 0l-4-4a1 1 0 1 1 1.4-1.41L11 12.59V4a1 1 0 0 1 1-1Z"/>'
    '<path d="M5 19a1 1 0 0 1 1-1h12a1 1 0 1 1 0 2H6a1 1 0 0 1-1-1Z"/>'
    '</svg>'
)

DELETE_ICON = (
    '<svg viewBox="0 0 24 24" aria-hidden="true" focusable="false">'
    '<path d="M9 3a1 1 0 0 0-.9.55L7.38 5H5a1 1 0 1 0 0 2h14a1 1 0 1 0 0-2h-2.38l-.72-1.45A1 1 0 0 0 15 3H9Z"/>'
    '<path d="M7 9a1 1 0 0 1 1 1v8a2 2 0 0 0 2 2h4a2 2 0 0 0 2-2v-8a1 1 0 1 1 2 0v8a4 4 0 0 1-4 4h-4a4 4 0 0 1-4-4v-8a1 1 0 0 1 1-1Z" transform="translate(0 -2)"/>'
    '<path d="M10 10a1 1 0 0 1 1 1v6a1 1 0 1 1-2 0v-6a1 1 0 0 1 1-1Zm4 0a1 1 0 0 1 1 1v6a1 1 0 1 1-2 0v-6a1 1 0 0 1 1-1Z"/>'
    '</svg>'
)


# -------------------- Config (env-overridable) --------------------
APP_DIR = Path(__file__).resolve().parent

RECORDINGS_ROOT = Path(os.getenv("RECORDINGS_ROOT", "/var/lib/nestcam/recordings"))
STATUS_FILE = Path(os.getenv("STATUS_FILE", "/run/nestcam/status.txt"))
INDEX_HTML = Path(os.getenv("INDEX_HTML", str(APP_DIR / "index.html")))
RETENTION_SCRIPT = Path(os.getenv("RETENTION_SCRIPT", str(APP_DIR / "retention.py")))

RECORDING_ENABLED = env_bool("RECORDING_ENABLED", True)
AUTH_ENABLED = env_bool("AUTH_ENABLED", False)

MIN_FREE_GB = float(os.getenv("MIN_FREE_GB", "2.0"))
RETENTION_MAX_GB = float(os.getenv("RETENTION_MAX_GB", "4.0"))
RETENTION_MIN_FREE_GB = float(os.getenv("RETENTION_MIN_FREE_GB", str(MIN_FREE_GB)))
RETENTION_COOLDOWN_SECONDS = float(os.getenv("RETENTION_COOLDOWN_SECONDS", "60"))

VIDEO_SIZE = (int(os.getenv("VIDEO_W", "1280")), int(os.getenv("VIDEO_H", "720")))
FPS = int(os.getenv("FPS", "20"))
BITRATE = int(os.getenv("BITRATE", "2000000"))

AE_ENABLE = optional_bool_env("AE_ENABLE")
EXPOSURE_TIME = os.getenv("EXPOSURE_TIME")
EXPOSURE_TIME = int(EXPOSURE_TIME) if EXPOSURE_TIME not in (None, "") else None
ANALOGUE_GAIN = os.getenv("ANALOGUE_GAIN")
ANALOGUE_GAIN = float(ANALOGUE_GAIN) if ANALOGUE_GAIN not in (None, "") else None
SATURATION = os.getenv("SATURATION")
SATURATION = float(SATURATION) if SATURATION not in (None, "") else None

CAMERA_IDLE_STOP_GRACE = max(0.0, float(os.getenv("CAMERA_IDLE_STOP_GRACE", "5.0")))

MIN_CLIP_SECONDS = max(0.0, float(os.getenv("MIN_CLIP_SECONDS", "8")))
MOTION_COOLDOWN_SECONDS = max(0.0, float(os.getenv("MOTION_COOLDOWN_SECONDS", os.getenv("COOLDOWN_SECONDS", "8"))))
SAMPLE_HZ = max(0.1, float(os.getenv("SAMPLE_HZ", "8")))
START_RECORD_RETRY_SECONDS = max(0.0, float(os.getenv("START_RECORD_RETRY_SECONDS", "2")))
MOTION_TRIGGER_CONSECUTIVE_SAMPLES = max(1, int(os.getenv("MOTION_TRIGGER_CONSECUTIVE_SAMPLES", "4")))
MOTION_CLEAR_CONSECUTIVE_SAMPLES = max(1, int(os.getenv("MOTION_CLEAR_CONSECUTIVE_SAMPLES", "2")))

MOTION_GPIO_PIN = env_int("MOTION_GPIO_PIN")
MOTION_ACTIVE_HIGH = env_bool("MOTION_ACTIVE_HIGH", True)
MOTION_PULL = parse_motion_pull(os.getenv("MOTION_PULL", "down"))
MOTION_STARTUP_GRACE_SECONDS = max(0.0, float(os.getenv("MOTION_STARTUP_GRACE_SECONDS", "20")))
validate_motion_input_config(
    pin=MOTION_GPIO_PIN,
    active_high=MOTION_ACTIVE_HIGH,
    pull=MOTION_PULL,
)

LIVE_BIND = os.getenv("LIVE_BIND", "0.0.0.0")
LIVE_PORT = int(os.getenv("LIVE_PORT", "8000"))
LIVE_USER = os.getenv("LIVE_USER", "")
LIVE_PASS = os.getenv("LIVE_PASS", "")

LIVE_STOP_GRACE = float(os.getenv("LIVE_STOP_GRACE", "2.0"))

ALLOW_LOCAL_NET_ONLY = env_bool("ALLOW_LOCAL_NET_ONLY", True)
LAN_ONLY_FAIL_CLOSED = env_bool("LAN_ONLY_FAIL_CLOSED", True)
LOCAL_NETS_TTL_SECONDS = float(os.getenv("LOCAL_NETS_TTL_SECONDS", "60"))
WIFI_IFACE_ENV = (os.getenv("WIFI_IFACE") or "").strip()

IR_GPIO = int(os.getenv("IR_GPIO", "18"))
IR_ACTIVE_HIGH = env_bool("IR_ACTIVE_HIGH", True)
IR_BRIGHTNESS = max(0.0, min(1.0, float(os.getenv("IR_BRIGHTNESS", "1.0"))))
IR_PWM_FREQUENCY = float(os.getenv("IR_PWM_FREQUENCY", "500"))
NIGHT_MODE_START = parse_time_of_day(os.getenv("NIGHT_MODE_START", "20:00"), name="NIGHT_MODE_START")
NIGHT_MODE_END = parse_time_of_day(os.getenv("NIGHT_MODE_END", "06:00"), name="NIGHT_MODE_END")
IR_CUT_GPIO = env_int("IR_CUT_GPIO")
IR_CUT_ENABLED = env_bool("IR_CUT_ENABLED", IR_CUT_GPIO is not None)
IR_CUT_DAY_HIGH = env_bool("IR_CUT_DAY_HIGH", True)

ensure_dir(RECORDINGS_ROOT)
ensure_dir(STATUS_FILE.parent)

PAGE = build_page(VIDEO_SIZE)

if IR_CUT_ENABLED and IR_CUT_GPIO is None:
    logging.warning("IR_CUT is enabled but IR_CUT_GPIO is not set; disabling IR-cut control")
    IR_CUT_ENABLED = False
if seconds_since_midnight(NIGHT_MODE_START) == seconds_since_midnight(NIGHT_MODE_END):
    logging.warning("NIGHT_MODE_START and NIGHT_MODE_END are the same; night mode will be disabled")
if IR_CUT_GPIO is not None and IR_CUT_GPIO == IR_GPIO:
    logging.warning("IR_CUT_GPIO and IR_GPIO are the same GPIO pin; IR-cut and IR light control will conflict")


# -------------------- LAN-only cache --------------------
LOCAL_NETS = None
LOCAL_NETS_LAST_REFRESH = 0.0
LOCAL_NETS_LOCK = threading.Lock()


def refresh_local_nets_if_needed():
    global LOCAL_NETS, LOCAL_NETS_LAST_REFRESH
    now = time.time()
    with LOCAL_NETS_LOCK:
        if LOCAL_NETS is not None and (now - LOCAL_NETS_LAST_REFRESH) < LOCAL_NETS_TTL_SECONDS:
            return
        iface = wifi_iface()
        try:
            LOCAL_NETS = get_local_ipv4_networks(iface)
            LOCAL_NETS_LAST_REFRESH = now
            logging.info(
                "LAN-only networks (iface=%s): %s",
                iface,
                ", ".join(str(net) for net in LOCAL_NETS),
            )
        except Exception as exc:
            logging.warning("Could not derive local networks for %s: %s", iface, exc)
            LOCAL_NETS = []
            LOCAL_NETS_LAST_REFRESH = now


def client_allowed(client_ip: str) -> bool:
    if not ALLOW_LOCAL_NET_ONLY:
        return True

    refresh_local_nets_if_needed()
    with LOCAL_NETS_LOCK:
        nets = list(LOCAL_NETS) if LOCAL_NETS is not None else []

    if not nets:
        return not LAN_ONLY_FAIL_CLOSED

    try:
        addr = ipaddress.ip_address(client_ip)
    except ValueError:
        return False
    return any(addr in net for net in nets)


# -------------------- Disk guard + retention trigger --------------------
RETENTION_LOCK = threading.Lock()
LAST_RETENTION_RUN = 0.0


def free_bytes_for_path(path: Path) -> int:
    return shutil.disk_usage(path).free


def maybe_run_retention():
    global LAST_RETENTION_RUN
    now = time.time()
    with RETENTION_LOCK:
        if (now - LAST_RETENTION_RUN) < RETENTION_COOLDOWN_SECONDS:
            return
        LAST_RETENTION_RUN = now

    script = RETENTION_SCRIPT
    if not script.exists():
        logging.warning("Retention script not found: %s", script)
        return

    cmd = [
        sys.executable,
        str(script),
        "--root",
        str(RECORDINGS_ROOT),
        "--max-gb",
        str(RETENTION_MAX_GB),
        "--min-free-gb",
        str(RETENTION_MIN_FREE_GB),
    ]
    logging.warning("Low disk: running retention: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, timeout=60, check=False)
    except Exception as exc:
        logging.warning("Retention run failed: %s", exc)


def disk_ok_for_recording() -> bool:
    ensure_dir(RECORDINGS_ROOT)
    free_before = free_bytes_for_path(RECORDINGS_ROOT)
    if free_before >= gb_to_bytes(MIN_FREE_GB):
        return True

    maybe_run_retention()
    free_after = free_bytes_for_path(RECORDINGS_ROOT)
    if free_after >= gb_to_bytes(MIN_FREE_GB):
        return True

    logging.error(
        "Disk too low to record: free=%.2f GB required>=%.2f GB",
        free_after / (1024 ** 3),
        MIN_FREE_GB,
    )
    return False


# -------------------- MJPEG streaming --------------------
class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()


class LiveController:
    def __init__(self, *, on_first_client=None, on_idle_grace_elapsed=None, on_active_change=None):
        self.lock = threading.Lock()
        self.clients = 0
        self.last_client_left = 0.0
        self.on_first_client = on_first_client
        self.on_idle_grace_elapsed = on_idle_grace_elapsed
        self.on_active_change = on_active_change

    def client_connected(self):
        first_client = False
        active = False
        with self.lock:
            self.clients += 1
            active = self.clients > 0
            if self.clients == 1:
                first_client = True
                self.last_client_left = 0.0

        try:
            if first_client and self.on_first_client:
                self.on_first_client()
        except Exception:
            with self.lock:
                self.clients = max(0, self.clients - 1)
                active = self.clients > 0
                if self.clients == 0:
                    self.last_client_left = time.time()
            if self.on_active_change:
                self.on_active_change(active)
            raise

        if self.on_active_change:
            self.on_active_change(active)

    def client_disconnected(self):
        active = False
        with self.lock:
            self.clients = max(0, self.clients - 1)
            active = self.clients > 0
            if self.clients == 0:
                self.last_client_left = time.time()
        if self.on_active_change:
            self.on_active_change(active)

    def live_active(self) -> bool:
        with self.lock:
            return self.clients > 0

    def client_count(self) -> int:
        with self.lock:
            return self.clients

    def maintenance_loop(self, stop_event: threading.Event):
        while not stop_event.is_set():
            time.sleep(0.5)
            should_stop_live = False
            with self.lock:
                if self.clients == 0 and self.last_client_left:
                    if (time.time() - self.last_client_left) >= LIVE_STOP_GRACE:
                        should_stop_live = True
                        self.last_client_left = 0.0

            if should_stop_live and self.on_idle_grace_elapsed:
                self.on_idle_grace_elapsed()


class LedDriverBase:
    backend_name = "unknown"

    def set_brightness(self, value: float) -> None:
        raise NotImplementedError

    def off(self) -> None:
        self.set_brightness(0.0)

    def close(self) -> None:
        raise NotImplementedError


class RPiGPIODriver(LedDriverBase):
    backend_name = "rpi_gpio_pwm"

    def __init__(self, gpio: int, pwm_hz: float, active_high: bool) -> None:
        if RPI_GPIO is None:
            raise RuntimeError("RPi.GPIO module not available")

        self.GPIO = RPI_GPIO
        self.gpio = gpio
        self.pwm_hz = pwm_hz
        self.active_high = active_high
        self.pwm = None

        self.GPIO.setwarnings(False)
        self.GPIO.setmode(self.GPIO.BCM)
        self.GPIO.setup(self.gpio, self.GPIO.OUT, initial=self._off_level())
        self.GPIO.output(self.gpio, self._off_level())

    def _on_level(self):
        return self.GPIO.HIGH if self.active_high else self.GPIO.LOW

    def _off_level(self):
        return self.GPIO.LOW if self.active_high else self.GPIO.HIGH

    def _stop_pwm(self) -> None:
        if self.pwm is not None:
            try:
                self.pwm.stop()
            finally:
                self.pwm = None

    def _ensure_pwm(self) -> None:
        if self.pwm is None:
            self.pwm = self.GPIO.PWM(self.gpio, self.pwm_hz)
            start_duty = 0.0 if self.active_high else 100.0
            self.pwm.start(start_duty)

    def _set_digital(self, on: bool) -> None:
        self._stop_pwm()
        self.GPIO.output(self.gpio, self._on_level() if on else self._off_level())

    def set_brightness(self, value: float) -> None:
        value = max(0.0, min(1.0, value))
        if value <= 0.001:
            self._set_digital(False)
            return
        if value >= 0.999:
            self._set_digital(True)
            return

        self._ensure_pwm()
        duty = value * 100.0 if self.active_high else (100.0 - (value * 100.0))
        self.pwm.ChangeDutyCycle(duty)

    def close(self) -> None:
        try:
            self._set_digital(False)
        finally:
            try:
                self.GPIO.cleanup(self.gpio)
            except Exception:
                pass


class GpioZeroPWMLEDDriver(LedDriverBase):
    backend_name = "gpiozero_pwmled"

    def __init__(self, gpio: int, active_high: bool) -> None:
        if PWMLED is None:
            raise RuntimeError("gpiozero PWMLED unavailable")
        self.led = PWMLED(gpio, active_high=active_high, initial_value=0.0)

    def set_brightness(self, value: float) -> None:
        value = max(0.0, min(1.0, value))
        if value >= 0.999:
            self.led.on()
        elif value <= 0.001:
            self.led.off()
        else:
            self.led.value = value

    def close(self) -> None:
        self.led.close()


class GpioZeroDigitalDriver(LedDriverBase):
    backend_name = "gpiozero_digital"

    def __init__(self, gpio: int, active_high: bool, *, initial_on: bool = False) -> None:
        if OutputDevice is None:
            raise RuntimeError("gpiozero OutputDevice unavailable")
        self.device = OutputDevice(gpio, active_high=active_high, initial_value=initial_on)

    def set_brightness(self, value: float) -> None:
        if value > 0.0:
            self.device.on()
        else:
            self.device.off()

    def close(self) -> None:
        self.device.close()


class RPiGPIOBinaryDriver(LedDriverBase):
    backend_name = "rpi_gpio_binary"

    def __init__(self, gpio: int, active_high: bool, *, initial_on: bool = False) -> None:
        if RPI_GPIO is None:
            raise RuntimeError("RPi.GPIO module not available")
        self.GPIO = RPI_GPIO
        self.gpio = gpio
        self.active_high = active_high
        self.GPIO.setwarnings(False)
        self.GPIO.setmode(self.GPIO.BCM)
        self.GPIO.setup(self.gpio, self.GPIO.OUT)
        self.set_brightness(1.0 if initial_on else 0.0)

    def set_brightness(self, value: float) -> None:
        on = value > 0.0
        level = self.GPIO.HIGH if (on == self.active_high) else self.GPIO.LOW
        self.GPIO.output(self.gpio, level)

    def close(self) -> None:
        try:
            self.GPIO.cleanup(self.gpio)
        except Exception:
            pass


def build_ir_driver():
    errors = []

    try:
        driver = RPiGPIODriver(IR_GPIO, IR_PWM_FREQUENCY, IR_ACTIVE_HIGH)
        logging.info(
            "IR lights configured on GPIO%d using RPi.GPIO PWM (brightness=%.2f freq=%.1f Hz)",
            IR_GPIO,
            IR_BRIGHTNESS,
            IR_PWM_FREQUENCY,
        )
        return driver
    except Exception as exc:
        errors.append(f"RPi.GPIO failed: {exc}")

    try:
        driver = GpioZeroPWMLEDDriver(IR_GPIO, IR_ACTIVE_HIGH)
        logging.info(
            "IR lights configured on GPIO%d using gpiozero PWMLED (brightness=%.2f)",
            IR_GPIO,
            IR_BRIGHTNESS,
        )
        return driver
    except Exception as exc:
        errors.append(f"gpiozero PWMLED failed: {exc}")

    try:
        driver = GpioZeroDigitalDriver(IR_GPIO, IR_ACTIVE_HIGH)
        logging.info("IR lights configured on GPIO%d using gpiozero digital output only", IR_GPIO)
        return driver
    except Exception as exc:
        errors.append(f"gpiozero digital failed: {exc}")

    logging.warning(
        "Failed to initialize IR lights on GPIO%d. Details: %s",
        IR_GPIO,
        "; ".join(errors) if errors else "no GPIO backend available",
    )
    return None


class IRLightController:
    def __init__(self):
        self.lock = threading.Lock()
        self.live_active = False
        self.recording_active = False
        self.night_mode = False
        self.driver = build_ir_driver()
        self.driver_backend = self.driver.backend_name if self.driver is not None else "unavailable"

    def _apply_locked(self):
        if self.driver is None:
            return

        want_on = self.night_mode and (self.live_active or self.recording_active)
        try:
            self.driver.set_brightness(IR_BRIGHTNESS if want_on else 0.0)
        except Exception as exc:
            logging.warning("Failed to update IR lights via %s: %s", self.driver_backend, exc)

    def set_live_active(self, active: bool):
        with self.lock:
            self.live_active = bool(active)
            self._apply_locked()

    def set_recording_active(self, active: bool):
        with self.lock:
            self.recording_active = bool(active)
            self._apply_locked()

    def set_night_mode(self, enabled: bool):
        with self.lock:
            self.night_mode = bool(enabled)
            self._apply_locked()

    def is_on(self) -> bool:
        with self.lock:
            return (
                self.driver is not None
                and self.night_mode
                and (self.live_active or self.recording_active)
                and IR_BRIGHTNESS > 0.0
            )

    def night_mode_enabled(self) -> bool:
        with self.lock:
            return self.night_mode

    def brightness(self) -> float:
        return IR_BRIGHTNESS

    def backend(self) -> str:
        return self.driver_backend

    def close(self):
        with self.lock:
            self.live_active = False
            self.recording_active = False
            self.night_mode = False
            if self.driver is None:
                return
            try:
                self.driver.off()
                self.driver.close()
            except Exception as exc:
                logging.warning("Failed to close IR light device via %s: %s", self.driver_backend, exc)
            finally:
                self.driver = None


class IRCutController:
    def __init__(self):
        self.lock = threading.Lock()
        self.enabled = IR_CUT_ENABLED and IR_CUT_GPIO is not None
        self.driver = None
        self.driver_backend = "disabled"
        self.mode = "night" if night_mode_active_now(NIGHT_MODE_START, NIGHT_MODE_END) else "day"

        if not self.enabled:
            return

        errors = []
        try:
            self.driver = GpioZeroDigitalDriver(IR_CUT_GPIO, IR_CUT_DAY_HIGH, initial_on=(self.mode == "day"))
            self.driver_backend = self.driver.backend_name
            logging.info(
                "IR-cut configured on GPIO%d using %s (day_high=%s)",
                IR_CUT_GPIO,
                self.driver_backend,
                IR_CUT_DAY_HIGH,
            )
            return
        except Exception as exc:
            errors.append(f"gpiozero digital failed: {exc}")

        try:
            self.driver = RPiGPIOBinaryDriver(IR_CUT_GPIO, IR_CUT_DAY_HIGH, initial_on=(self.mode == "day"))
            self.driver_backend = self.driver.backend_name
            logging.info(
                "IR-cut configured on GPIO%d using %s (day_high=%s)",
                IR_CUT_GPIO,
                self.driver_backend,
                IR_CUT_DAY_HIGH,
            )
            return
        except Exception as exc:
            errors.append(f"RPi.GPIO failed: {exc}")

        self.enabled = False
        self.driver_backend = "unavailable"
        logging.warning(
            "Failed to initialize IR-cut on GPIO%d. Details: %s",
            IR_CUT_GPIO,
            "; ".join(errors) if errors else "no GPIO backend available",
        )

    def set_day_mode(self):
        with self.lock:
            self.mode = "day"
            if self.driver is None:
                return
            try:
                self.driver.set_brightness(1.0)
            except Exception as exc:
                logging.warning("Failed to set IR-cut day mode via %s: %s", self.driver_backend, exc)

    def set_night_mode(self):
        with self.lock:
            self.mode = "night"
            if self.driver is None:
                return
            try:
                self.driver.set_brightness(0.0)
            except Exception as exc:
                logging.warning("Failed to set IR-cut night mode via %s: %s", self.driver_backend, exc)

    def current_mode(self) -> str:
        with self.lock:
            return self.mode

    def backend(self) -> str:
        return self.driver_backend

    def is_enabled(self) -> bool:
        return self.enabled

    def close(self):
        with self.lock:
            if self.driver is None:
                return
            try:
                self.driver.close()
            except Exception as exc:
                logging.warning("Failed to close IR-cut device via %s: %s", self.driver_backend, exc)
            finally:
                self.driver = None

# -------------------- Motion input --------------------
class MotionInput:
    def __init__(self, pin: int | None, *, active_high: bool, pull, startup_grace_seconds: float):
        self.pin = pin
        self.active_high = active_high
        self.pull = pull
        self.startup_grace_seconds = max(0.0, startup_grace_seconds)
        self.start_time = time.time()
        self.device = None

        if pin is None:
            logging.warning("Motion input disabled: MOTION_GPIO_PIN is not set")
            return

        if DigitalInputDevice is None:
            raise RuntimeError(
                "gpiozero is not installed, but MOTION_GPIO_PIN is configured. "
                "Install python3-gpiozero (and a supported pin backend such as python3-lgpio)."
            )

        try:
            kwargs = {"pin": pin, "pull_up": pull}
            if pull is None:
                kwargs["active_state"] = active_high
            self.device = DigitalInputDevice(**kwargs)
        except Exception as exc:
            raise RuntimeError(f"Failed to initialize motion input on GPIO{pin}: {exc}") from exc

        logging.info(
            "Motion input ready on GPIO%s (active_high=%s pull=%s startup_grace=%.1fs)",
            pin,
            active_high,
            motion_pull_label(pull),
            self.startup_grace_seconds,
        )

    def enabled(self) -> bool:
        return self.device is not None

    def ready(self) -> bool:
        return (time.time() - self.start_time) >= self.startup_grace_seconds

    def raw_detected(self) -> bool:
        if self.device is None:
            return False
        if not self.ready():
            return False
        try:
            return bool(self.device.is_active)
        except Exception as exc:
            logging.warning("Failed to read motion input: %s", exc)
            return False

    def detected(self) -> bool:
        return self.raw_detected()

    def close(self):
        if self.device is not None:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None


class DebouncedMotion:
    def __init__(self, trigger_samples: int, clear_samples: int):
        self.trigger_samples = max(1, int(trigger_samples))
        self.clear_samples = max(1, int(clear_samples))
        self.active_samples = 0
        self.inactive_samples = 0
        self.confirmed = False
        self.last_raw = False

    def update(self, raw_motion: bool) -> bool:
        self.last_raw = bool(raw_motion)
        if self.last_raw:
            self.active_samples += 1
            self.inactive_samples = 0
            if not self.confirmed and self.active_samples >= self.trigger_samples:
                self.confirmed = True
        else:
            self.inactive_samples += 1
            self.active_samples = 0
            if self.confirmed and self.inactive_samples >= self.clear_samples:
                self.confirmed = False
        return self.confirmed

    def reset(self) -> None:
        self.active_samples = 0
        self.inactive_samples = 0
        self.confirmed = False
        self.last_raw = False



def auth_config_valid() -> bool:
    return bool(LIVE_USER and LIVE_PASS)


def authorized(headers) -> bool:
    if not AUTH_ENABLED:
        return True

    auth = headers.get("Authorization", "")
    if not auth.startswith("Basic "):
        return False

    try:
        raw = base64.b64decode(auth.split(" ", 1)[1]).decode("utf-8")
    except Exception:
        return False
    return raw == f"{LIVE_USER}:{LIVE_PASS}"


class StreamingHandler(server.BaseHTTPRequestHandler):
    live_controller: LiveController = None
    streaming_output: StreamingOutput = None
    status_provider = None
    active_recording_path_provider = None

    def _check_request_access(self) -> bool:
        if not client_allowed(self.client_address[0]):
            self.send_response(403)
            self.end_headers()
            return False

        if not authorized(self.headers):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="NestCam"')
            self.end_headers()
            return False

        return True

    def do_GET(self):
        if not self._check_request_access():
            return

        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if route == "/recordings":
            content = build_recordings_page(RECORDINGS_ROOT)
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if route in {"/recordings/view", "/recordings/download"}:
            rel_path = (query.get("f") or [""])[0]
            if not rel_path:
                self.send_error(400, "Missing recording path")
                return
            as_attachment = (route == "/recordings/download")
            self._serve_recording(rel_path, as_attachment=as_attachment)
            return

        if route == "/recordings/delete":
            self.send_error(405, "Use POST for deletion")
            return

        if self.path == "/":
            self.send_response(301)
            self.send_header("Location", "/index.html")
            self.end_headers()
            return

        if self.path == "/index.html":
            content = self._index_page_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if self.path == "/status.txt":
            content = self.status_provider() if self.status_provider else b"status unavailable\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if self.path == "/stream.mjpg":
            self.send_response(200)
            self.send_header("Age", "0")
            self.send_header("Cache-Control", "no-cache, private")
            self.send_header("Pragma", "no-cache")
            self.send_header("Content-Type", "multipart/x-mixed-replace; boundary=FRAME")
            self.end_headers()

            self.live_controller.client_connected()
            try:
                while True:
                    with self.streaming_output.condition:
                        self.streaming_output.condition.wait()
                        frame = self.streaming_output.frame

                    if frame is None:
                        continue

                    self.wfile.write(b"--FRAME\r\n")
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(frame)))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
            except Exception as exc:
                logging.info("Live client disconnected: %s (%s)", self.client_address, exc)
            finally:
                self.live_controller.client_disconnected()
            return

        self.send_error(404)
        self.end_headers()

    def do_POST(self):
        if not self._check_request_access():
            return

        parsed = urlparse(self.path)
        route = parsed.path
        if route != "/recordings/delete":
            self.send_error(404)
            return

        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            self.send_error(400, "Invalid Content-Length")
            return

        if content_length < 0:
            self.send_error(400, "Invalid Content-Length")
            return
        if content_length > 8192:
            self.send_error(413, "Request body too large")
            return

        try:
            body = self.rfile.read(content_length)
        except Exception:
            self.send_error(400, "Could not read request body")
            return

        try:
            form = parse_qs(body.decode("utf-8"), keep_blank_values=True)
        except UnicodeDecodeError:
            self.send_error(400, "Invalid request encoding")
            return

        rel_path = (form.get("f") or [""])[0]
        if not rel_path:
            self.send_error(400, "Missing recording path")
            return

        self._delete_recording(rel_path)

    def _serve_recording(self, rel_path: str, *, as_attachment: bool) -> None:
        try:
            path = safe_recording_path(RECORDINGS_ROOT, rel_path)
            stat = path.stat()
        except FileNotFoundError:
            self.send_error(404, "Recording not found")
            return
        except ValueError:
            self.send_error(400, "Invalid recording path")
            return
        except OSError as exc:
            logging.warning("Could not access recording %s: %s", rel_path, exc)
            self.send_error(500, "Could not access recording")
            return

        disposition = "attachment" if as_attachment else "inline"
        filename = path.name.replace("\\", "_").replace('"', "_")

        self.send_response(200)
        self.send_header("Content-Type", guess_recording_content_type(path))
        self.send_header("Content-Length", str(stat.st_size))
        self.send_header("Content-Disposition", f'{disposition}; filename="{filename}"')
        self.end_headers()

        try:
            with path.open("rb") as f:
                shutil.copyfileobj(f, self.wfile)
        except BrokenPipeError:
            logging.info("Recording client disconnected: %s (%s)", self.client_address, rel_path)
        except Exception as exc:
            logging.warning("Failed while serving recording %s: %s", rel_path, exc)

    def _delete_recording(self, rel_path: str) -> None:
        try:
            path = safe_recording_path(RECORDINGS_ROOT, rel_path)
        except FileNotFoundError:
            self.send_error(404, "Recording not found")
            return
        except ValueError:
            self.send_error(400, "Invalid recording path")
            return
        except OSError as exc:
            logging.warning("Could not access recording %s for deletion: %s", rel_path, exc)
            self.send_error(500, "Could not access recording")
            return

        active_path = None
        if self.active_recording_path_provider is not None:
            try:
                active_path = self.active_recording_path_provider()
            except Exception as exc:
                logging.warning("Could not determine active recording path: %s", exc)

        if active_path is not None:
            try:
                if path.resolve(strict=True) == active_path.resolve(strict=False):
                    self.send_error(409, "Cannot delete recording that is still in progress")
                    return
            except OSError:
                pass

        try:
            path.unlink()
            self._cleanup_empty_recording_dirs(path.parent)
            logging.info("Deleted recording: %s", path)
        except FileNotFoundError:
            self.send_error(404, "Recording not found")
            return
        except OSError as exc:
            logging.warning("Failed to delete recording %s: %s", rel_path, exc)
            self.send_error(500, "Could not delete recording")
            return

        self.send_response(303)
        self.send_header("Location", "/recordings")
        self.end_headers()

    def _cleanup_empty_recording_dirs(self, start_dir: Path) -> None:
        try:
            root = RECORDINGS_ROOT.resolve(strict=True)
        except OSError:
            return

        current = start_dir
        while True:
            try:
                resolved = current.resolve(strict=True)
            except OSError:
                break
            if resolved == root:
                break
            try:
                current.rmdir()
            except OSError:
                break
            current = current.parent

    def _index_page_bytes(self) -> bytes:
        if INDEX_HTML.exists():
            try:
                raw = INDEX_HTML.read_bytes()
                try:
                    text = raw.decode("utf-8")
                except UnicodeDecodeError:
                    return raw

                if "/recordings" not in text:
                    nav = '<p><a href="/recordings">Recordings</a></p>'
                    if "</body>" in text:
                        text = text.replace("</body>", nav + "\n</body>", 1)
                    else:
                        text += "\n" + nav + "\n"
                return text.encode("utf-8")
            except Exception as exc:
                logging.warning("Could not read %s: %s", INDEX_HTML, exc)
        return PAGE.encode("utf-8")

    def log_message(self, fmt, *args):
        logging.debug("%s - %s", self.address_string(), fmt % args)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


# -------------------- Motion + recording --------------------
class NestCamDaemon:
    def __init__(self):
        self.picam2 = Picamera2()
        self.camera_lock = threading.RLock()
        self.camera_running = False
        self.camera_configured = False
        self.mjpeg_encoder = MJPEGEncoder()
        self.mjpeg_running = False
        self.last_camera_idle = time.time()

        self.recording = False
        self.record_start = 0.0
        self.last_motion = 0.0
        self.last_clip_end = 0.0
        self.record_reason = None
        self.h264_encoder = None
        self.current_record_path = None
        self.last_record_start_failure = 0.0

        self.motion_input = MotionInput(
            MOTION_GPIO_PIN,
            active_high=MOTION_ACTIVE_HIGH,
            pull=MOTION_PULL,
            startup_grace_seconds=MOTION_STARTUP_GRACE_SECONDS,
        )
        self.debounced_motion = DebouncedMotion(
            MOTION_TRIGGER_CONSECUTIVE_SAMPLES,
            MOTION_CLEAR_CONSECUTIVE_SAMPLES,
        )
        if RECORDING_ENABLED and not self.motion_input.enabled():
            logging.error(
                "Recording is enabled but motion input is unavailable; motion-triggered recording will not occur"
            )

        self.stream_output = StreamingOutput()
        self.ir = IRLightController()
        self.ir_cut = IRCutController()
        self.day_night_mode = self.determine_day_night_mode()
        self.day_night_mode_chosen_at = None
        self.live = LiveController(
            on_first_client=self.start_live_streaming,
            on_idle_grace_elapsed=self.stop_live_streaming_if_idle,
            on_active_change=self.ir.set_live_active,
        )

        self.stop_event = threading.Event()
        self.state_lock = threading.Lock()

    def determine_day_night_mode(self) -> str:
        if night_mode_active_now(NIGHT_MODE_START, NIGHT_MODE_END):
            return "night"
        return "day"

    def apply_day_night_mode_at_startup(self, mode: str | None = None):
        mode = mode or self.determine_day_night_mode()
        self.day_night_mode = mode
        self.day_night_mode_chosen_at = dt.datetime.now().astimezone()
        self.ir.set_night_mode(mode == "night")
        if mode == "night":
            self.ir_cut.set_night_mode()
        else:
            self.ir_cut.set_day_mode()
        logging.info(
            "Camera mode at startup: %s (night window %s-%s, ir_cut_enabled=%s, effective_saturation=%s)",
            mode,
            NIGHT_MODE_START.strftime("%H:%M"),
            NIGHT_MODE_END.strftime("%H:%M"),
            self.ir_cut.is_enabled(),
            self.effective_saturation_for_mode(mode),
        )

    def effective_saturation_for_mode(self, mode: str | None = None):
        selected_mode = mode or self.day_night_mode
        if selected_mode == "night":
            return 0.0
        return SATURATION

    def build_camera_controls(self, mode: str | None = None):
        controls = {"FrameRate": FPS}
        if AE_ENABLE is not None:
            controls["AeEnable"] = AE_ENABLE
        if EXPOSURE_TIME is not None:
            controls["ExposureTime"] = EXPOSURE_TIME
        if ANALOGUE_GAIN is not None:
            controls["AnalogueGain"] = ANALOGUE_GAIN

        effective_saturation = self.effective_saturation_for_mode(mode)
        if effective_saturation is not None:
            controls["Saturation"] = effective_saturation
        return controls

    def ensure_camera_started(self):
        with self.camera_lock:
            if self.camera_running:
                return

            ensure_dir(RECORDINGS_ROOT)
            mode = self.determine_day_night_mode()
            controls = self.build_camera_controls(mode)
            if not self.camera_configured:
                config = self.picam2.create_video_configuration(
                    main={"size": VIDEO_SIZE, "format": "YUV420"},
                    controls=controls,
                )
                self.picam2.configure(config)
                self.camera_configured = True

            self.picam2.start()
            self.camera_running = True
            self.last_camera_idle = 0.0
            try:
                self.picam2.set_controls(controls)
            except Exception as exc:
                logging.warning("Failed to apply camera controls after start: %s", exc)
            self.apply_day_night_mode_at_startup(mode)
            logging.info("Camera started (main=%s controls=%s)", VIDEO_SIZE, controls)

    def maybe_stop_camera_if_idle(self):
        with self.state_lock:
            recording = self.recording
        live_active = self.live.live_active()

        with self.camera_lock:
            if recording or live_active or self.mjpeg_running:
                self.last_camera_idle = 0.0
                return

            now = time.time()
            if not self.camera_running:
                self.last_camera_idle = now
                return

            if not self.last_camera_idle:
                self.last_camera_idle = now
                return

            if (now - self.last_camera_idle) < CAMERA_IDLE_STOP_GRACE:
                return

            try:
                self.picam2.stop()
                logging.info("Camera stopped (idle)")
            except Exception as exc:
                logging.warning("Failed to stop camera while idle: %s", exc)
            finally:
                self.camera_running = False
                self.last_camera_idle = 0.0

    def start_live_streaming(self):
        with self.camera_lock:
            self.ensure_camera_started()
            if self.mjpeg_running:
                return
            logging.info("Starting MJPEG encoder (main)")
            try:
                timing_safe_start_encoder(
                    self.picam2,
                    self.mjpeg_encoder,
                    FileOutput(self.stream_output),
                    name="main",
                )
            except Exception:
                self.last_camera_idle = time.time()
                raise
            self.mjpeg_running = True

    def stop_live_streaming_if_idle(self):
        with self.camera_lock:
            if self.live.live_active() or not self.mjpeg_running:
                return
            logging.info("Stopping MJPEG encoder (no clients)")
            try:
                timing_safe_stop_encoder(self.picam2, self.mjpeg_encoder)
            except Exception as exc:
                logging.warning("stop_encoder(MJPEG) error: %s", exc)
            finally:
                self.mjpeg_running = False
                self.last_camera_idle = time.time()
        self.maybe_stop_camera_if_idle()

    def motion_raw_detected(self) -> bool:
        return self.motion_input.raw_detected()

    def motion_detected(self) -> bool:
        return self.debounced_motion.confirmed

    def start_recording(self, reason: str) -> bool:
        with self.state_lock:
            if self.recording:
                return True

        if not RECORDING_ENABLED:
            logging.info("Recording disabled; skipping start_recording(%s)", reason)
            with self.state_lock:
                self.last_record_start_failure = time.time()
            return False

        if not disk_ok_for_recording():
            with self.state_lock:
                self.last_record_start_failure = time.time()
            return False

        filename = new_filename(RECORDINGS_ROOT, reason, "h264")
        encoder = H264Encoder(bitrate=BITRATE)

        logging.info("REC start (%s): %s", reason, filename)
        try:
            self.ensure_camera_started()
            self.ir.set_recording_active(True)
            timing_safe_start_encoder(
                self.picam2,
                encoder,
                FileOutput(str(filename)),
                name="main",
            )
        except Exception as exc:
            self.ir.set_recording_active(False)
            logging.exception("Failed to start H264 encoder for %s: %s", filename, exc)
            with self.state_lock:
                self.h264_encoder = None
                self.last_record_start_failure = time.time()
            self.last_camera_idle = time.time()
            self.maybe_stop_camera_if_idle()
            return False

        with self.state_lock:
            self.h264_encoder = encoder
            self.recording = True
            self.record_reason = reason
            self.current_record_path = filename
            self.record_start = time.time()
            self.last_motion = self.record_start
            self.last_record_start_failure = 0.0
        with self.camera_lock:
            self.last_camera_idle = 0.0
        return True

    def stop_recording(self):
        with self.state_lock:
            if not self.recording:
                return
        try:
            timing_safe_stop_encoder(self.picam2, self.h264_encoder)
        except Exception as exc:
            logging.warning("stop_encoder(H264) error: %s", exc)

        with self.state_lock:
            self.h264_encoder = None
            self.recording = False
            self.record_reason = None
            self.current_record_path = None
            self.last_clip_end = time.time()

        self.ir.set_recording_active(False)
        with self.camera_lock:
            self.last_camera_idle = time.time()
        logging.info("REC stop")
        self.maybe_stop_camera_if_idle()

    def active_recording_path(self):
        with self.state_lock:
            return self.current_record_path

    def status_text(self) -> bytes:
        ensure_dir(RECORDINGS_ROOT)
        free_bytes = free_bytes_for_path(RECORDINGS_ROOT)
        raw_motion_now = self.debounced_motion.last_raw
        motion_now = self.debounced_motion.confirmed
        with self.state_lock:
            recording = self.recording
            reason = self.record_reason
            last_motion = self.last_motion
            last_start_failure = self.last_record_start_failure

        seconds_since_motion = "never"
        if last_motion > 0:
            seconds_since_motion = f"{max(0.0, time.time() - last_motion):.1f}"

        seconds_since_start_failure = "never"
        if last_start_failure > 0:
            seconds_since_start_failure = f"{max(0.0, time.time() - last_start_failure):.1f}"

        txt = (
            f"recording={recording}\n"
            f"record_reason={reason}\n"
            f"live_clients={self.live.client_count()}\n"
            f"recordings_root={RECORDINGS_ROOT}\n"
            f"live_size={VIDEO_SIZE[0]}x{VIDEO_SIZE[1]}\n"
            f"camera_running={self.camera_running}\n"
            f"live_stream_running={self.mjpeg_running}\n"
            f"camera_idle_stop_grace={CAMERA_IDLE_STOP_GRACE:.1f}\n"
            f"fps={FPS}\n"
            f"ae_enable={AE_ENABLE}\n"
            f"exposure_time={EXPOSURE_TIME}\n"
            f"analogue_gain={ANALOGUE_GAIN}\n"
            f"saturation={SATURATION}\n"
            f"effective_saturation={self.effective_saturation_for_mode()}\n"
            f"free_gb={free_bytes / (1024 ** 3):.2f}\n"
            f"min_free_gb={MIN_FREE_GB:.2f}\n"
            f"wifi_iface={wifi_iface()}\n"
            f"auth_enabled={AUTH_ENABLED}\n"
            f"day_night_mode={self.day_night_mode}\n"
            f"day_night_mode_chosen_at={self.day_night_mode_chosen_at.isoformat() if self.day_night_mode_chosen_at else 'never'}\n"
            f"night_mode_start={NIGHT_MODE_START.strftime('%H:%M')}\n"
            f"night_mode_end={NIGHT_MODE_END.strftime('%H:%M')}\n"
            f"ir_on={self.ir.is_on()}\n"
            f"ir_night_mode={self.ir.night_mode_enabled()}\n"
            f"ir_brightness={self.ir.brightness():.2f}\n"
            f"ir_backend={self.ir.backend()}\n"
            f"ir_cut_enabled={self.ir_cut.is_enabled()}\n"
            f"ir_cut_gpio={IR_CUT_GPIO}\n"
            f"ir_cut_day_high={IR_CUT_DAY_HIGH}\n"
            f"ir_cut_backend={self.ir_cut.backend()}\n"
            f"ir_cut_mode={self.ir_cut.current_mode()}\n"
            f"motion_gpio_pin={MOTION_GPIO_PIN}\n"
            f"motion_active_high={MOTION_ACTIVE_HIGH}\n"
            f"motion_pull={motion_pull_label(MOTION_PULL)}\n"
            f"motion_enabled={self.motion_input.enabled()}\n"
            f"motion_ready={self.motion_input.ready()}\n"
            f"motion_raw_detected={raw_motion_now}\n"
            f"motion_trigger_consecutive_samples={MOTION_TRIGGER_CONSECUTIVE_SAMPLES}\n"
            f"motion_clear_consecutive_samples={MOTION_CLEAR_CONSECUTIVE_SAMPLES}\n"
            f"motion_active_samples={self.debounced_motion.active_samples}\n"
            f"motion_inactive_samples={self.debounced_motion.inactive_samples}\n"
            f"motion_detected={motion_now}\n"
            f"motion_cooldown_seconds={MOTION_COOLDOWN_SECONDS:.1f}\n"
            f"sample_hz={SAMPLE_HZ:.2f}\n"
            f"start_record_retry_seconds={START_RECORD_RETRY_SECONDS:.1f}\n"
            f"seconds_since_motion={seconds_since_motion}\n"
            f"seconds_since_start_failure={seconds_since_start_failure}\n"
        )
        data = txt.encode("utf-8")
        try:
            STATUS_FILE.write_bytes(data)
        except Exception as exc:
            logging.debug("Could not write status file %s: %s", STATUS_FILE, exc)
        return data

    def run_loop(self):
        sleep_dt = 1.0 / SAMPLE_HZ

        while not self.stop_event.is_set():
            now = time.time()
            raw_motion = self.motion_raw_detected()
            motion = self.debounced_motion.update(raw_motion)
            if motion:
                with self.state_lock:
                    self.last_motion = now

            with self.state_lock:
                recording = self.recording
                record_start = self.record_start
                last_motion = self.last_motion
                last_start_failure = self.last_record_start_failure

            if not recording:
                can_retry_start = (now - last_start_failure) >= START_RECORD_RETRY_SECONDS
                if motion and can_retry_start:
                    self.start_recording("motion")
            else:
                clip_len = now - record_start
                quiet_for = now - last_motion
                if clip_len >= MIN_CLIP_SECONDS and quiet_for >= MOTION_COOLDOWN_SECONDS:
                    self.stop_recording()

            self.maybe_stop_camera_if_idle()
            time.sleep(sleep_dt)


    def start_http_server(self):
        StreamingHandler.live_controller = self.live
        StreamingHandler.streaming_output = self.stream_output
        StreamingHandler.status_provider = self.status_text
        StreamingHandler.active_recording_path_provider = self.active_recording_path

        httpd = ThreadedHTTPServer((LIVE_BIND, LIVE_PORT), StreamingHandler)
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        auth_msg = "LAN-only + BasicAuth" if AUTH_ENABLED else "LAN-only"
        logging.info("HTTP live server on http://<pi-ip>:%d/ (%s)", LIVE_PORT, auth_msg)
        return httpd

    def shutdown(self):
        self.stop_event.set()
        try:
            self.stop_recording()
        except Exception:
            pass
        try:
            with self.camera_lock:
                if self.mjpeg_running:
                    timing_safe_stop_encoder(self.picam2, self.mjpeg_encoder)
                    self.mjpeg_running = False
        except Exception:
            pass
        try:
            self.motion_input.close()
        except Exception:
            pass
        try:
            with self.camera_lock:
                if self.camera_running:
                    self.picam2.stop()
                    self.camera_running = False
        except Exception:
            pass
        try:
            self.ir.close()
        except Exception:
            pass
        try:
            self.ir_cut.close()
        except Exception:
            pass


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")

    if AUTH_ENABLED and not auth_config_valid():
        raise SystemExit("AUTH_ENABLED=1 requires both LIVE_USER and LIVE_PASS")

    daemon = NestCamDaemon()

    def handle_exit(signum, frame):
        logging.info("Signal %s, exiting", signum)
        daemon.shutdown()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

    daemon.status_text()

    maint = threading.Thread(
        target=daemon.live.maintenance_loop,
        args=(daemon.stop_event,),
        daemon=True,
    )
    maint.start()

    httpd = daemon.start_http_server()
    try:
        daemon.run_loop()
    finally:
        try:
            httpd.shutdown()
        except Exception:
            pass
        daemon.shutdown()


if __name__ == "__main__":
    main()
