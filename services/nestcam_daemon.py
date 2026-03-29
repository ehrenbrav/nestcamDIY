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
import os
import shutil
import signal
import socketserver
import subprocess
import sys
import threading
import time
from http import server
from pathlib import Path
from threading import Condition

import numpy as np
from picamera2 import Picamera2
from picamera2.encoders import H264Encoder, MJPEGEncoder
from picamera2.outputs import FileOutput


# -------------------- Helpers --------------------
def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


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
<p><a href=\"/status.txt\">status</a></p>
<img src=\"/stream.mjpg\" width=\"{width}\" height=\"{height}\" />
</body>
</html>
"""


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

PIXEL_DIFF = int(os.getenv("PIXEL_DIFF", "12"))
MOTION_FRACTION_TRIGGER = float(os.getenv("MOTION_FRACTION_TRIGGER", "0.02"))

MIN_CLIP_SECONDS = float(os.getenv("MIN_CLIP_SECONDS", "8"))
QUIET_SECONDS_TO_STOP = float(os.getenv("QUIET_SECONDS_TO_STOP", "6"))
COOLDOWN_SECONDS = float(os.getenv("COOLDOWN_SECONDS", "8"))
SAMPLE_HZ = float(os.getenv("SAMPLE_HZ", "8"))

LIVE_BIND = os.getenv("LIVE_BIND", "0.0.0.0")
LIVE_PORT = int(os.getenv("LIVE_PORT", "8000"))
LIVE_USER = os.getenv("LIVE_USER", "")
LIVE_PASS = os.getenv("LIVE_PASS", "")

LIVE_STOP_GRACE = float(os.getenv("LIVE_STOP_GRACE", "2.0"))
LIVE_RECORD_STOP_GRACE = float(os.getenv("LIVE_RECORD_STOP_GRACE", "2.0"))

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
        self.prev_y = None

        self.recording = False
        self.record_start = 0.0
        self.last_motion = 0.0
        self.last_clip_end = 0.0
        self.record_reason = None
        self.h264_encoder = None

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

    def motion_score(self) -> float:
        frame = self.picam2.capture_array("lores")
        height = LORES_SIZE[1]
        y_plane = frame[:height, :].astype(np.int16)

        if self.prev_y is None:
            self.prev_y = y_plane
            return 0.0

        diff = np.abs(y_plane - self.prev_y)
        self.prev_y = y_plane
        return float(np.mean(diff > PIXEL_DIFF))

    def start_recording(self, reason: str) -> bool:
        if not RECORDING_ENABLED:
            logging.info("Recording disabled; skipping start_recording(%s)", reason)
            return False
        if not disk_ok_for_recording():
            return False

        filename = new_filename(RECORDINGS_ROOT, reason, "h264")
        self.h264_encoder = H264Encoder(bitrate=BITRATE)

        logging.info("REC start (%s): %s", reason, filename)
        timing_safe_start_encoder(
            self.picam2,
            self.h264_encoder,
            FileOutput(str(filename)),
            name="main",
        )

        with self.state_lock:
            self.recording = True
            self.record_reason = reason
            self.record_start = time.time()
            self.last_motion = self.record_start
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
        with self.state_lock:
            recording = self.recording
            reason = self.record_reason

        txt = (
            f"recording={recording}\n"
            f"record_reason={reason}\n"
            f"live_clients={self.live.client_count()}\n"
            f"recordings_root={RECORDINGS_ROOT}\n"
            f"free_gb={free_bytes / (1024 ** 3):.2f}\n"
            f"min_free_gb={MIN_FREE_GB:.2f}\n"
            f"wifi_iface={wifi_iface()}\n"
            f"auth_enabled={AUTH_ENABLED}\n"
        )
        data = txt.encode("utf-8")
        try:
            STATUS_FILE.write_bytes(data)
        except Exception as exc:
            logging.debug("Could not write status file %s: %s", STATUS_FILE, exc)
        return data

    def run_loop(self):
        sleep_dt = 1.0 / max(1.0, SAMPLE_HZ)
        live_ended_at = None

        while not self.stop_event.is_set():
            now = time.time()
            live_active = self.live.live_active()

            if live_active:
                motion = False
            else:
                frac = self.motion_score()
                motion = frac >= MOTION_FRACTION_TRIGGER
                if motion:
                    with self.state_lock:
                        self.last_motion = now

            live_active = self.live.live_active()
            if not live_active:
                if live_ended_at is None:
                    live_ended_at = now
            else:
                live_ended_at = None

            with self.state_lock:
                recording = self.recording
                record_reason = self.record_reason
                record_start = self.record_start
                last_motion = self.last_motion
                last_clip_end = self.last_clip_end

            if not recording:
                if live_active:
                    if RECORDING_ENABLED:
                        self.start_recording("live")
                else:
                    in_cooldown = (now - last_clip_end) < COOLDOWN_SECONDS
                    if motion and not in_cooldown:
                        if RECORDING_ENABLED:
                            self.start_recording("motion")
            else:
                if live_active and record_reason != "live":
                    self.stop_recording()
                    if RECORDING_ENABLED:
                        self.start_recording("live")

            with self.state_lock:
                recording = self.recording
                record_reason = self.record_reason
                record_start = self.record_start
                last_motion = self.last_motion

            if recording:
                if record_reason == "live" and not live_active:
                    if live_ended_at is not None and (now - live_ended_at) >= LIVE_RECORD_STOP_GRACE:
                        self.stop_recording()
                elif not live_active:
                    clip_len = now - record_start
                    quiet_for = now - last_motion
                    if clip_len >= MIN_CLIP_SECONDS and quiet_for >= QUIET_SECONDS_TO_STOP:
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
