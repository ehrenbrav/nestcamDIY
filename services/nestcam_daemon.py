#!/usr/bin/env python3

import base64
import datetime as dt
import io
import ipaddress
import json
import logging
import os
import pwd
import shutil
import signal
import socketserver
import subprocess
import threading
import time
from http import server
from pathlib import Path

from picamera2 import Picamera2
from picamera2.encoders import H264Encoder, MJPEGEncoder
from picamera2.outputs import FileOutput

try:
    from gpiozero import OutputDevice, PWMLED
except Exception:
    OutputDevice = None
    PWMLED = None

try:
    import RPi.GPIO as RPI_GPIO
except Exception:
    RPI_GPIO = None

# -------------------- Config (env-overridable) --------------------
INSTALL_ROOT = Path("/opt/nestcam")
STATE_DIR = Path("/var/lib/nestcam")


def safe_home() -> str:
    home = (os.environ.get("HOME") or "").strip()
    if home:
        return home
    try:
        pw = pwd.getpwuid(os.getuid())
        if pw.pw_dir:
            return pw.pw_dir
    except Exception:
        pass
    return "/root" if os.geteuid() == 0 else "/tmp"


HOME = safe_home()

# Where recordings are stored
RECORDINGS_ROOT = os.environ.get("RECORDINGS_ROOT", str(STATE_DIR / "recordings"))

# Enable or disable recording (in env file).
RECORDING_ENABLED = bool(int(os.environ.get("RECORDING_ENABLED", "1")))

# Disk guard: refuse to start new recordings if free space is too low.
MIN_FREE_GB = float(os.environ.get("MIN_FREE_GB", "2.0"))
# Optionally trigger retention when low space is detected
RETENTION_SCRIPT = os.environ.get("RETENTION_SCRIPT", str(INSTALL_ROOT / "retention.py"))
RETENTION_MAX_GB = float(os.environ.get("RETENTION_MAX_GB", "4.0"))
RETENTION_MIN_FREE_GB = float(os.environ.get("RETENTION_MIN_FREE_GB", str(MIN_FREE_GB)))
RETENTION_COOLDOWN_SECONDS = float(os.environ.get("RETENTION_COOLDOWN_SECONDS", "60"))

# Recorded video (H.264)
VIDEO_SIZE = (int(os.environ.get("VIDEO_W", "1280")), int(os.environ.get("VIDEO_H", "720")))
FPS = int(os.environ.get("FPS", "20"))
BITRATE = int(os.environ.get("BITRATE", "2000000"))


def optional_bool_env(name: str):
    raw = os.environ.get(name)
    if raw is None:
        return None
    value = raw.strip().lower()
    if value in {"1", "true", "yes", "on"}:
        return True
    if value in {"0", "false", "no", "off"}:
        return False
    raise ValueError(f"Invalid boolean value for {name}: {raw!r}")


AE_ENABLE = optional_bool_env("AE_ENABLE")
EXPOSURE_TIME = os.environ.get("EXPOSURE_TIME")
EXPOSURE_TIME = int(EXPOSURE_TIME) if EXPOSURE_TIME not in (None, "") else None
ANALOGUE_GAIN = os.environ.get("ANALOGUE_GAIN")
ANALOGUE_GAIN = float(ANALOGUE_GAIN) if ANALOGUE_GAIN not in (None, "") else None
SATURATION = os.environ.get("SATURATION")
SATURATION = float(SATURATION) if SATURATION not in (None, "") else None

# Live MJPEG stream uses the lores camera stream.
# Keep LORES_W/LORES_H for backward compatibility, but prefer LIVE_W/LIVE_H.
LIVE_SIZE = (
    int(os.environ.get("LIVE_W", os.environ.get("LORES_W", "960"))),
    int(os.environ.get("LIVE_H", os.environ.get("LORES_H", "540"))),
)

# Motion clip behavior
MIN_CLIP_SECONDS = float(os.environ.get("MIN_CLIP_SECONDS", "8"))
QUIET_SECONDS_TO_STOP = float(os.environ.get("QUIET_SECONDS_TO_STOP", "6"))
COOLDOWN_SECONDS = float(os.environ.get("COOLDOWN_SECONDS", "8"))
CONTROL_LOOP_HZ = float(os.environ.get("CONTROL_LOOP_HZ", "10"))

# Live viewing (browser MJPEG)
LIVE_BIND = os.environ.get("LIVE_BIND", "0.0.0.0")
LIVE_PORT = int(os.environ.get("LIVE_PORT", "8080"))
LIVE_USER = (os.environ.get("LIVE_USER") or "").strip()
LIVE_PASS = (os.environ.get("LIVE_PASS") or "").strip()
LIVE_STOP_GRACE = float(os.environ.get("LIVE_STOP_GRACE", "2.0"))
LIVE_RECORD_STOP_GRACE = float(os.environ.get("LIVE_RECORD_STOP_GRACE", "2.0"))

# LAN-only restriction (Wi-Fi subnet only)
ALLOW_LOCAL_NET_ONLY = True
LAN_ONLY_FAIL_CLOSED = True
LOCAL_NETS_TTL_SECONDS = float(os.environ.get("LOCAL_NETS_TTL_SECONDS", "60"))
WIFI_IFACE_ENV = (os.environ.get("WIFI_IFACE") or "").strip()

# IR lights
IR_GPIO = int(os.environ.get("IR_GPIO", "18"))
IR_ACTIVE_HIGH = bool(int(os.environ.get("IR_ACTIVE_HIGH", "1")))
IR_BRIGHTNESS = max(0.0, min(1.0, float(os.environ.get("IR_BRIGHTNESS", "1.0"))))
IR_PWM_FREQUENCY = float(os.environ.get("IR_PWM_FREQUENCY", "500"))


# -------------------- Helpers --------------------
def gb_to_bytes(x: float) -> int:
    return int(x * (1024**3))


def ensure_dir(path: str) -> None:
    os.makedirs(path, exist_ok=True)


def daily_dir(root: str) -> str:
    now = dt.datetime.now()
    path = os.path.join(root, f"{now.year:04d}", f"{now.month:02d}", f"{now.day:02d}")
    ensure_dir(path)
    return path


def new_filename(root: str, reason: str, ext: str) -> str:
    now = dt.datetime.now().astimezone()
    ts = now.strftime("%Y-%m-%dT%H%M%S%z")
    return os.path.join(daily_dir(root), f"{ts}_{reason}.{ext}")


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
    nets = [ipaddress.ip_network("127.0.0.0/8")]  # allow local testing on the Pi
    out = subprocess.check_output(["ip", "-j", "-4", "addr"], text=True)
    data = json.loads(out)
    for iface in data:
        if iface.get("ifname", "") != iface_name:
            continue
        for a in iface.get("addr_info", []):
            if a.get("family") != "inet":
                continue
            ip = a.get("local")
            plen = a.get("prefixlen")
            if ip and plen is not None:
                nets.append(ipaddress.ip_network(f"{ip}/{plen}", strict=False))
    return nets


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
            logging.info("LAN-only networks (iface=%s): %s", iface, ", ".join(str(n) for n in LOCAL_NETS))
        except Exception as e:
            logging.warning("Could not derive local networks for %s: %s", iface, e)
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
    return any(addr in n for n in nets)


# -------------------- Disk guard + retention trigger --------------------
RETENTION_LOCK = threading.Lock()
LAST_RETENTION_RUN = 0.0


def free_bytes_for_path(path: str) -> int:
    usage = shutil.disk_usage(path)
    return usage.free


def maybe_run_retention():
    global LAST_RETENTION_RUN
    now = time.time()
    with RETENTION_LOCK:
        if (now - LAST_RETENTION_RUN) < RETENTION_COOLDOWN_SECONDS:
            return
        LAST_RETENTION_RUN = now

    script = os.path.expanduser(RETENTION_SCRIPT)
    if not os.path.exists(script):
        logging.warning("Retention script not found: %s", script)
        return

    cmd = [
        "/usr/bin/env", "python3", script,
        "--root", RECORDINGS_ROOT,
        "--max-gb", str(RETENTION_MAX_GB),
        "--min-free-gb", str(RETENTION_MIN_FREE_GB),
    ]
    logging.warning("Low disk: running retention: %s", " ".join(cmd))
    try:
        subprocess.run(cmd, timeout=60, check=False)
    except Exception as e:
        logging.warning("Retention run failed: %s", e)


def disk_ok_for_recording() -> bool:
    ensure_dir(RECORDINGS_ROOT)
    free_b = free_bytes_for_path(RECORDINGS_ROOT)
    if free_b >= gb_to_bytes(MIN_FREE_GB):
        return True

    # Try retention once, then re-check
    maybe_run_retention()
    free_b2 = free_bytes_for_path(RECORDINGS_ROOT)
    if free_b2 >= gb_to_bytes(MIN_FREE_GB):
        return True

    logging.error(
        "Disk too low to record: free=%.2f GB required>=%.2f GB",
        free_b2 / (1024**3), MIN_FREE_GB,
    )
    return False


# -------------------- MJPEG streaming --------------------
PAGE = f"""\
<html>
<head><title>NestCam Live</title></head>
<body>
<h2>NestCam Live</h2>
<p><a href="/status.txt">status</a></p>
<img src="/stream.mjpg" style="max-width: 100%; height: auto;" />
</body>
</html>
"""


class StreamingOutput(io.BufferedIOBase):
    def __init__(self):
        self.frame = None
        self.condition = threading.Condition()

    def write(self, buf):
        with self.condition:
            self.frame = buf
            self.condition.notify_all()

    def clear(self):
        with self.condition:
            self.frame = None


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

    def __init__(self, gpio: int, active_high: bool) -> None:
        if OutputDevice is None:
            raise RuntimeError("gpiozero OutputDevice unavailable")
        self.device = OutputDevice(gpio, active_high=active_high, initial_value=False)

    def set_brightness(self, value: float) -> None:
        if value > 0.0:
            self.device.on()
        else:
            self.device.off()

    def close(self) -> None:
        self.device.close()


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
    except Exception as e:
        errors.append(f"RPi.GPIO failed: {e}")

    try:
        driver = GpioZeroPWMLEDDriver(IR_GPIO, IR_ACTIVE_HIGH)
        logging.info(
            "IR lights configured on GPIO%d using gpiozero PWMLED (brightness=%.2f)",
            IR_GPIO,
            IR_BRIGHTNESS,
        )
        return driver
    except Exception as e:
        errors.append(f"gpiozero PWMLED failed: {e}")

    try:
        driver = GpioZeroDigitalDriver(IR_GPIO, IR_ACTIVE_HIGH)
        logging.info("IR lights configured on GPIO%d using gpiozero digital output only", IR_GPIO)
        return driver
    except Exception as e:
        errors.append(f"gpiozero digital failed: {e}")

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
        self.driver = build_ir_driver()
        self.driver_backend = self.driver.backend_name if self.driver is not None else "unavailable"

    def _apply_locked(self):
        if self.driver is None:
            return

        want_on = self.live_active or self.recording_active
        try:
            self.driver.set_brightness(IR_BRIGHTNESS if want_on else 0.0)
        except Exception as e:
            logging.warning("Failed to update IR lights via %s: %s", self.driver_backend, e)

    def set_live_active(self, active: bool):
        with self.lock:
            self.live_active = bool(active)
            self._apply_locked()

    def set_recording_active(self, active: bool):
        with self.lock:
            self.recording_active = bool(active)
            self._apply_locked()

    def is_on(self) -> bool:
        with self.lock:
            return (
                self.driver is not None
                and (self.live_active or self.recording_active)
                and IR_BRIGHTNESS > 0.0
            )

    def brightness(self) -> float:
        return IR_BRIGHTNESS

    def backend(self) -> str:
        return self.driver_backend

    def close(self):
        with self.lock:
            self.live_active = False
            self.recording_active = False
            if self.driver is None:
                return
            try:
                self.driver.off()
                self.driver.close()
            except Exception as e:
                logging.warning("Failed to close IR light device via %s: %s", self.driver_backend, e)
            finally:
                self.driver = None


class LiveController:
    def __init__(self, on_active_change=None):
        self.lock = threading.Lock()
        self.clients = 0
        self.last_client_left = 0.0
        self.on_active_change = on_active_change

    def client_connected(self):
        with self.lock:
            self.clients += 1
            active = self.clients > 0
            self.last_client_left = 0.0
        if self.on_active_change:
            self.on_active_change(active)

    def client_disconnected(self):
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

    def seconds_since_last_client_left(self):
        with self.lock:
            if self.clients > 0 or self.last_client_left == 0.0:
                return None
            return time.time() - self.last_client_left



def auth_required() -> bool:
    return bool(LIVE_USER or LIVE_PASS)


def authorized(headers) -> bool:
    if not auth_required():
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
    status_provider = None  # callable -> bytes

    def do_GET(self):
        if not client_allowed(self.client_address[0]):
            self.send_response(403)
            self.end_headers()
            return

        if not authorized(self.headers):
            self.send_response(401)
            if auth_required():
                self.send_header("WWW-Authenticate", 'Basic realm="NestCam"')
            self.end_headers()
            return

        if self.path == "/":
            self.send_response(301)
            self.send_header("Location", "/index.html")
            self.end_headers()
            return

        if self.path == "/index.html":
            content = PAGE.encode("utf-8")
            self.send_response(200)
            self.send_header("Content-Type", "text/html")
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
                        self.streaming_output.condition.wait(timeout=1.0)
                        frame = self.streaming_output.frame
                    if frame is None:
                        continue
                    self.wfile.write(b"--FRAME\r\n")
                    self.send_header("Content-Type", "image/jpeg")
                    self.send_header("Content-Length", str(len(frame)))
                    self.end_headers()
                    self.wfile.write(frame)
                    self.wfile.write(b"\r\n")
            except Exception as e:
                logging.info("Live client disconnected: %s (%s)", self.client_address, e)
            finally:
                self.live_controller.client_disconnected()
            return

        self.send_error(404)
        self.end_headers()

    def log_message(self, fmt, *args):
        logging.debug("%s - %s", self.address_string(), fmt % args)


class ThreadedHTTPServer(socketserver.ThreadingMixIn, server.HTTPServer):
    allow_reuse_address = True
    daemon_threads = True


# -------------------- Live + recording --------------------
class NestCamDaemon:
    def __init__(self):
        self.picam2 = None
        self.camera_running = False
        self.recording = False
        self.record_start = 0.0
        self.last_motion_event = 0.0
        self.last_clip_end = 0.0
        self.record_reason = None
        self.h264_encoder = None
        self.mjpeg_encoder = None
        self.mjpeg_running = False
        self.stream_output = StreamingOutput()
        self.ir = IRLightController()
        self.live = LiveController(on_active_change=self.ir.set_live_active)
        self.stop_event = threading.Event()
        self.state_lock = threading.Lock()

    def build_camera_controls(self):
        controls = {"FrameRate": FPS}
        if AE_ENABLE is not None:
            controls["AeEnable"] = AE_ENABLE
        if EXPOSURE_TIME is not None:
            controls["ExposureTime"] = EXPOSURE_TIME
        if ANALOGUE_GAIN is not None:
            controls["AnalogueGain"] = ANALOGUE_GAIN
        if SATURATION is not None:
            controls["Saturation"] = SATURATION
        return controls

    def start_camera(self):
        with self.state_lock:
            if self.camera_running:
                return

        controls = self.build_camera_controls()
        ensure_dir(RECORDINGS_ROOT)
        picam2 = None
        try:
            picam2 = Picamera2()
            config = picam2.create_video_configuration(
                main={"size": VIDEO_SIZE, "format": "YUV420"},
                lores={"size": LIVE_SIZE, "format": "YUV420"},
                controls=controls,
            )
            picam2.configure(config)
            picam2.start()
        except Exception:
            if picam2 is not None:
                try:
                    picam2.close()
                except Exception:
                    pass
            raise

        with self.state_lock:
            if self.camera_running:
                try:
                    picam2.stop()
                except Exception:
                    pass
                try:
                    picam2.close()
                except Exception:
                    pass
                return
            self.picam2 = picam2
            self.camera_running = True

        logging.info(
            "Camera started (main=%s live=%s controls=%s)",
            VIDEO_SIZE,
            LIVE_SIZE,
            controls,
        )

    def stop_camera(self):
        with self.state_lock:
            if not self.camera_running:
                return
            picam2 = self.picam2
            self.picam2 = None
            self.camera_running = False

        self.stream_output.clear()

        try:
            picam2.stop()
        except Exception as e:
            logging.warning("Camera stop error: %s", e)
        try:
            picam2.close()
        except Exception as e:
            logging.warning("Camera close error: %s", e)
        logging.info("Camera stopped")

    def start_mjpeg(self):
        with self.state_lock:
            if self.mjpeg_running:
                return

        self.start_camera()
        self.stream_output.clear()
        encoder = MJPEGEncoder()
        with self.state_lock:
            picam2 = self.picam2
        try:
            timing_safe_start_encoder(
                picam2,
                encoder,
                FileOutput(self.stream_output),
                name="lores",
            )
        except Exception:
            try:
                timing_safe_stop_encoder(picam2, encoder)
            except Exception:
                pass
            raise

        with self.state_lock:
            self.mjpeg_encoder = encoder
            self.mjpeg_running = True
        logging.info("Starting MJPEG encoder (lores)")

    def stop_mjpeg(self):
        with self.state_lock:
            if not self.mjpeg_running:
                return
            encoder = self.mjpeg_encoder
            picam2 = self.picam2

        try:
            if picam2 is not None:
                timing_safe_stop_encoder(picam2, encoder)
        except Exception as e:
            logging.warning("stop_encoder(MJPEG) error: %s", e)

        with self.state_lock:
            self.mjpeg_encoder = None
            self.mjpeg_running = False
        self.stream_output.clear()
        logging.info("Stopping MJPEG encoder (lores)")

    def motion_event_triggered(self) -> bool:
        """Placeholder for future PIR-triggered recording.

        Replace this stub with the actual PIR event path. It should return True
        when an external motion event requests that a recording begin.

        Important: the eventual PIR integration should be latched or queued so a
        short pulse cannot be missed between control-loop iterations.
        """
        return False

    def start_recording(self, reason: str) -> bool:
        if not RECORDING_ENABLED:
            logging.info("Recording disabled; skipping start_recording(%s)", reason)
            return False

        if not disk_ok_for_recording():
            return False

        self.start_camera()
        filename = new_filename(RECORDINGS_ROOT, reason, "h264")
        encoder = H264Encoder(bitrate=BITRATE)

        logging.info("REC start (%s): %s", reason, filename)

        with self.state_lock:
            picam2 = self.picam2

        self.ir.set_recording_active(True)
        try:
            timing_safe_start_encoder(
                picam2,
                encoder,
                FileOutput(filename),
                name="main",
            )
        except Exception:
            self.ir.set_recording_active(False)
            raise

        with self.state_lock:
            self.h264_encoder = encoder
            self.recording = True
            self.record_reason = reason
            self.record_start = time.time()
            self.last_motion_event = self.record_start

        return True

    def stop_recording(self):
        with self.state_lock:
            if not self.recording:
                return
            encoder = self.h264_encoder
            picam2 = self.picam2

        try:
            if picam2 is not None:
                timing_safe_stop_encoder(picam2, encoder)
        except Exception as e:
            logging.warning("stop_encoder(H264) error: %s", e)

        with self.state_lock:
            self.h264_encoder = None
            self.recording = False
            self.record_reason = None
            self.last_clip_end = time.time()

        self.ir.set_recording_active(False)
        logging.info("REC stop")

    def maybe_stop_idle_camera(self):
        with self.state_lock:
            busy = self.recording or self.mjpeg_running
        if not busy:
            self.stop_camera()

    def status_text(self) -> bytes:
        ensure_dir(RECORDINGS_ROOT)
        free_b = free_bytes_for_path(RECORDINGS_ROOT)
        with self.state_lock:
            rec = self.recording
            reason = self.record_reason
            camera_running = self.camera_running
            mjpeg_running = self.mjpeg_running
        txt = (
            f"camera_running={camera_running}\n"
            f"recording={rec}\n"
            f"record_reason={reason}\n"
            f"live_clients={self.live.client_count()}\n"
            f"mjpeg_running={mjpeg_running}\n"
            f"recordings_root={RECORDINGS_ROOT}\n"
            f"live_size={LIVE_SIZE[0]}x{LIVE_SIZE[1]}\n"
            f"fps={FPS}\n"
            f"ae_enable={AE_ENABLE}\n"
            f"exposure_time={EXPOSURE_TIME}\n"
            f"analogue_gain={ANALOGUE_GAIN}\n"
            f"saturation={SATURATION}\n"
            f"free_gb={free_b/(1024**3):.2f}\n"
            f"min_free_gb={MIN_FREE_GB:.2f}\n"
            f"wifi_iface={wifi_iface()}\n"
            f"ir_on={self.ir.is_on()}\n"
            f"ir_brightness={self.ir.brightness():.2f}\n"
            f"ir_backend={self.ir.backend()}\n"
        )
        return txt.encode("utf-8")

    def run_loop(self):
        sleep_dt = 1.0 / max(1.0, CONTROL_LOOP_HZ)

        while not self.stop_event.is_set():
            now = time.time()
            live_active = self.live.live_active()
            motion_event = self.motion_event_triggered()

            with self.state_lock:
                recording = self.recording
                record_reason = self.record_reason
                record_start = self.record_start
                last_motion_event = self.last_motion_event
                last_clip_end = self.last_clip_end
                mjpeg_running = self.mjpeg_running

            # Start live streaming pipeline only while needed.
            if live_active and not mjpeg_running:
                try:
                    self.start_mjpeg()
                except Exception as e:
                    logging.exception("Failed to start MJPEG pipeline: %s", e)
                    self.maybe_stop_idle_camera()
                with self.state_lock:
                    mjpeg_running = self.mjpeg_running

            # Start rules
            if not recording:
                if live_active:
                    if RECORDING_ENABLED:
                        try:
                            self.start_recording("live")
                        except Exception as e:
                            logging.exception("Failed to start live recording: %s", e)
                            self.ir.set_recording_active(False)
                            self.maybe_stop_idle_camera()
                else:
                    in_cooldown = (now - last_clip_end) < COOLDOWN_SECONDS
                    if motion_event and not in_cooldown:
                        if RECORDING_ENABLED:
                            try:
                                self.start_recording("motion")
                            except Exception as e:
                                logging.exception("Failed to start motion recording: %s", e)
                                self.ir.set_recording_active(False)
                                self.maybe_stop_idle_camera()
            else:
                if motion_event:
                    with self.state_lock:
                        self.last_motion_event = now
                # If live starts while motion is recording, roll over to a "live" file
                if live_active and record_reason != "live":
                    self.stop_recording()
                    if RECORDING_ENABLED:
                        try:
                            self.start_recording("live")
                        except Exception as e:
                            logging.exception("Failed to roll over to live recording: %s", e)
                            self.ir.set_recording_active(False)
                            self.maybe_stop_idle_camera()

            # Refresh state after possible start/rollover.
            with self.state_lock:
                recording = self.recording
                record_reason = self.record_reason
                record_start = self.record_start
                last_motion_event = self.last_motion_event

            # Stop rules
            if recording:
                if record_reason == "live" and not live_active:
                    left_for = self.live.seconds_since_last_client_left()
                    if left_for is not None and left_for >= LIVE_RECORD_STOP_GRACE:
                        self.stop_recording()
                elif not live_active:
                    clip_len = now - record_start
                    quiet_for = now - last_motion_event
                    if clip_len >= MIN_CLIP_SECONDS and quiet_for >= QUIET_SECONDS_TO_STOP:
                        self.stop_recording()

            left_for = self.live.seconds_since_last_client_left()
            with self.state_lock:
                mjpeg_running = self.mjpeg_running
            if mjpeg_running and not live_active and left_for is not None and left_for >= LIVE_STOP_GRACE:
                self.stop_mjpeg()

            self.maybe_stop_idle_camera()
            time.sleep(sleep_dt)

    def start_http_server(self):
        StreamingHandler.live_controller = self.live
        StreamingHandler.streaming_output = self.stream_output
        StreamingHandler.status_provider = self.status_text
        httpd = ThreadedHTTPServer((LIVE_BIND, LIVE_PORT), StreamingHandler)
        t = threading.Thread(target=httpd.serve_forever, daemon=True)
        t.start()
        logging.info(
            "HTTP live server on http://<pi-ip>:%d/ (LAN-only%s)",
            LIVE_PORT,
            " + BasicAuth" if auth_required() else "",
        )
        return httpd

    def shutdown(self):
        self.stop_event.set()

        try:
            self.stop_recording()
        except Exception:
            pass

        try:
            self.stop_mjpeg()
        except Exception:
            pass

        try:
            self.stop_camera()
        except Exception:
            pass

        self.ir.close()


def main():
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    daemon = NestCamDaemon()

    def handle_exit(signum, frame):
        logging.info("Signal %s, exiting", signum)
        daemon.shutdown()
        raise SystemExit(0)

    signal.signal(signal.SIGINT, handle_exit)
    signal.signal(signal.SIGTERM, handle_exit)

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
