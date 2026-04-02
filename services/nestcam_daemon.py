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
    from gpiozero import DigitalInputDevice
except Exception:  # pragma: no cover - depends on target system packages
    DigitalInputDevice = None


# -------------------- Helpers --------------------
def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int | None = None) -> int | None:
    raw = (os.getenv(name) or "").strip()
    if not raw:
        return default
    return int(raw)


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


def build_page(lores_size: tuple[int, int]) -> str:
    width, height = lores_size
    return f"""\
<html>
<head><title>NestCam Live</title></head>
<body>
<h2>NestCam Live</h2>
<p><a href=\"/status.txt\">status</a> | <a href=\"/recordings\">recordings</a></p>
<img src=\"/stream.mjpg\" width=\"{width}\" height=\"{height}\" />
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
    name = path.name
    try:
        ts_text = name.split("_", 1)[0]
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
        except OSError as exc:
            logging.warning("Could not stat recording %s: %s", path, exc)
            continue
        try:
            rel = path.relative_to(root).as_posix()
        except ValueError:
            continue

        recorded_at = parse_recording_datetime(path)
        sort_ts = recorded_at.timestamp() if recorded_at is not None else stat.st_mtime

        entries.append(
            {
                "path": path,
                "rel": rel,
                "mtime": stat.st_mtime,
                "size": stat.st_size,
                "recorded_at": recorded_at,
                "sort_ts": sort_ts,
            }
        )
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
            f'<li><a href="/recordings/view?f={rel_quoted}">{filename}</a> '
            f'({html.escape(dt_text)}, {size_text}) '
            f'<a href="/recordings/download?f={rel_quoted}">download</a></li>'
        )

    if rows:
        listing = "\n".join(rows)
    else:
        listing = "<li>No recordings found.</li>"

    page = f"""\
<html>
<head><title>NestCam Recordings</title></head>
<body>
<h2>NestCam Recordings</h2>
<p><a href="/index.html">live</a> | <a href="/status.txt">status</a></p>
<ul>
{listing}
</ul>
</body>
</html>
"""
    return page.encode("utf-8")


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

LORES_SIZE = (int(os.getenv("LORES_W", "320")), int(os.getenv("LORES_H", "240")))

MIN_CLIP_SECONDS = max(0.0, float(os.getenv("MIN_CLIP_SECONDS", "8")))
MOTION_COOLDOWN_SECONDS = max(0.0, float(os.getenv("MOTION_COOLDOWN_SECONDS", os.getenv("COOLDOWN_SECONDS", "8"))))
SAMPLE_HZ = max(0.1, float(os.getenv("SAMPLE_HZ", "8")))
START_RECORD_RETRY_SECONDS = max(0.0, float(os.getenv("START_RECORD_RETRY_SECONDS", "2")))

MOTION_GPIO_PIN = env_int("MOTION_GPIO_PIN")
MOTION_ACTIVE_HIGH = env_bool("MOTION_ACTIVE_HIGH", True)
MOTION_PULL = parse_motion_pull(os.getenv("MOTION_PULL", "none"))
MOTION_STARTUP_GRACE_SECONDS = max(0.0, float(os.getenv("MOTION_STARTUP_GRACE_SECONDS", "20")))

LIVE_BIND = os.getenv("LIVE_BIND", "0.0.0.0")
LIVE_PORT = int(os.getenv("LIVE_PORT", "8000"))
LIVE_USER = os.getenv("LIVE_USER", "")
LIVE_PASS = os.getenv("LIVE_PASS", "")

LIVE_STOP_GRACE = float(os.getenv("LIVE_STOP_GRACE", "2.0"))

ALLOW_LOCAL_NET_ONLY = env_bool("ALLOW_LOCAL_NET_ONLY", True)
LAN_ONLY_FAIL_CLOSED = env_bool("LAN_ONLY_FAIL_CLOSED", True)
LOCAL_NETS_TTL_SECONDS = float(os.getenv("LOCAL_NETS_TTL_SECONDS", "60"))
WIFI_IFACE_ENV = (os.getenv("WIFI_IFACE") or "").strip()

ensure_dir(RECORDINGS_ROOT)
ensure_dir(STATUS_FILE.parent)

PAGE = build_page(LORES_SIZE)


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
    def __init__(self, picam2: Picamera2, output: StreamingOutput):
        self.picam2 = picam2
        self.output = output
        self.lock = threading.Lock()
        self.clients = 0
        self.last_client_left = 0.0
        self.mjpeg_encoder = MJPEGEncoder()
        self.mjpeg_running = False

    def client_connected(self):
        with self.lock:
            self.clients += 1
            if not self.mjpeg_running:
                logging.info("Starting MJPEG encoder (lores)")
                timing_safe_start_encoder(
                    self.picam2,
                    self.mjpeg_encoder,
                    FileOutput(self.output),
                    name="lores",
                )
                self.mjpeg_running = True

    def client_disconnected(self):
        with self.lock:
            self.clients = max(0, self.clients - 1)
            if self.clients == 0:
                self.last_client_left = time.time()

    def live_active(self) -> bool:
        with self.lock:
            return self.clients > 0

    def client_count(self) -> int:
        with self.lock:
            return self.clients

    def maintenance_loop(self, stop_event: threading.Event):
        while not stop_event.is_set():
            time.sleep(0.5)
            with self.lock:
                if self.mjpeg_running and self.clients == 0 and self.last_client_left:
                    if (time.time() - self.last_client_left) >= LIVE_STOP_GRACE:
                        logging.info("Stopping MJPEG encoder (no clients)")
                        try:
                            timing_safe_stop_encoder(self.picam2, self.mjpeg_encoder)
                        except Exception as exc:
                            logging.warning("stop_encoder(MJPEG) error: %s", exc)
                        self.mjpeg_running = False
                        self.last_client_left = 0.0


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
            self.device = DigitalInputDevice(
                pin=pin,
                pull_up=pull,
                active_state=active_high,
            )
        except Exception as exc:
            raise RuntimeError(f"Failed to initialize motion input on GPIO{pin}: {exc}") from exc

        logging.info(
            "Motion input ready on GPIO%s (active_high=%s pull=%s startup_grace=%.1fs)",
            pin,
            active_high,
            pull,
            self.startup_grace_seconds,
        )

    def enabled(self) -> bool:
        return self.device is not None

    def ready(self) -> bool:
        return (time.time() - self.start_time) >= self.startup_grace_seconds

    def detected(self) -> bool:
        if self.device is None:
            return False
        if not self.ready():
            return False
        try:
            return bool(self.device.is_active)
        except Exception as exc:
            logging.warning("Failed to read motion input: %s", exc)
            return False

    def close(self):
        if self.device is not None:
            try:
                self.device.close()
            except Exception:
                pass
            self.device = None



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


def guess_recording_content_type(path: Path) -> str:
    suffix = path.suffix.lower()
    if suffix in {".h264", ".264"}:
        return "video/h264"
    if suffix in {".mjpg", ".mjpeg"}:
        return "video/x-motion-jpeg"

    content_type, _ = mimetypes.guess_type(path.name)
    return content_type or "application/octet-stream"


class StreamingHandler(server.BaseHTTPRequestHandler):
    live_controller: LiveController = None
    streaming_output: StreamingOutput = None
    status_provider = None

    def do_GET(self):
        if not client_allowed(self.client_address[0]):
            self.send_response(403)
            self.end_headers()
            return

        if not authorized(self.headers):
            self.send_response(401)
            self.send_header("WWW-Authenticate", 'Basic realm="NestCam"')
            self.end_headers()
            return

        parsed = urlparse(self.path)
        route = parsed.path
        query = parse_qs(parsed.query)

        if route == "/":
            self.send_response(301)
            self.send_header("Location", "/index.html")
            self.end_headers()
            return

        if route == "/index.html":
            content = self._index_page_bytes()
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

        if route == "/status.txt":
            content = self.status_provider() if self.status_provider else b"status unavailable\n"
            self.send_response(200)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(content)))
            self.end_headers()
            self.wfile.write(content)
            return

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

        if route == "/stream.mjpg":
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

        content_type = guess_recording_content_type(path)

        disposition = "attachment" if as_attachment else "inline"
        filename = path.name.replace("\\", "_").replace('"', "_")

        self.send_response(200)
        self.send_header("Content-Type", content_type)
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

    def _index_page_bytes(self) -> bytes:
        if INDEX_HTML.exists():
            try:
                return INDEX_HTML.read_bytes()
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

        self.recording = False
        self.record_start = 0.0
        self.last_motion = 0.0
        self.last_clip_end = 0.0
        self.record_reason = None
        self.h264_encoder = None
        self.last_record_start_failure = 0.0

        self.motion_input = MotionInput(
            MOTION_GPIO_PIN,
            active_high=MOTION_ACTIVE_HIGH,
            pull=MOTION_PULL,
            startup_grace_seconds=MOTION_STARTUP_GRACE_SECONDS,
        )
        if RECORDING_ENABLED and not self.motion_input.enabled():
            logging.error(
                "Recording is enabled but motion input is unavailable; motion-triggered recording will not occur"
            )

        self.stream_output = StreamingOutput()
        self.live = LiveController(self.picam2, self.stream_output)

        self.stop_event = threading.Event()
        self.state_lock = threading.Lock()

    def start_camera(self):
        ensure_dir(RECORDINGS_ROOT)
        config = self.picam2.create_video_configuration(
            main={"size": VIDEO_SIZE, "format": "YUV420"},
            lores={"size": LORES_SIZE, "format": "YUV420"},
            controls={"FrameRate": FPS},
        )
        self.picam2.configure(config)
        self.picam2.start()
        logging.info("Camera started (main=%s lores=%s fps=%s)", VIDEO_SIZE, LORES_SIZE, FPS)

    def motion_detected(self) -> bool:
        return self.motion_input.detected()

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
            timing_safe_start_encoder(
                self.picam2,
                encoder,
                FileOutput(str(filename)),
                name="main",
            )
        except Exception as exc:
            logging.exception("Failed to start H264 encoder for %s: %s", filename, exc)
            with self.state_lock:
                self.h264_encoder = None
                self.last_record_start_failure = time.time()
            return False

        with self.state_lock:
            self.h264_encoder = encoder
            self.recording = True
            self.record_reason = reason
            self.record_start = time.time()
            self.last_motion = self.record_start
            self.last_record_start_failure = 0.0
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
            self.last_clip_end = time.time()

        logging.info("REC stop")

    def status_text(self) -> bytes:
        ensure_dir(RECORDINGS_ROOT)
        free_bytes = free_bytes_for_path(RECORDINGS_ROOT)
        motion_now = self.motion_detected()
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
            f"free_gb={free_bytes / (1024 ** 3):.2f}\n"
            f"min_free_gb={MIN_FREE_GB:.2f}\n"
            f"wifi_iface={wifi_iface()}\n"
            f"auth_enabled={AUTH_ENABLED}\n"
            f"motion_gpio_pin={MOTION_GPIO_PIN}\n"
            f"motion_enabled={self.motion_input.enabled()}\n"
            f"motion_ready={self.motion_input.ready()}\n"
            f"motion_detected={motion_now}\n"
            f"motion_cooldown_seconds={MOTION_COOLDOWN_SECONDS:.1f}\n"
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
            motion = self.motion_detected()
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

            time.sleep(sleep_dt)

    def start_http_server(self):
        StreamingHandler.live_controller = self.live
        StreamingHandler.streaming_output = self.stream_output
        StreamingHandler.status_provider = self.status_text

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
            if self.live.mjpeg_running:
                timing_safe_stop_encoder(self.picam2, self.live.mjpeg_encoder)
        except Exception:
            pass
        try:
            self.motion_input.close()
        except Exception:
            pass
        try:
            self.picam2.stop()
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

    daemon.start_camera()
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
