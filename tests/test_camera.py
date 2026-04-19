#!/usr/bin/env python3
"""Simple camera connectivity test for NestCamDIY.

This script opens a camera through Picamera2, waits briefly for the camera
pipeline to settle, captures a frame to memory, and optionally saves a JPEG.
If those steps succeed, the camera is connected and usable.

The test is intentionally generic so it works with either camera currently
mentioned in the project README:
- Arducam Camera Module 3 / IMX708-based module
- Waveshare IMX462 2MP IR-CUT camera

Usage:
    ./test_camera.py
    ./test_camera.py --width 1920 --height 1080
    ./test_camera.py --warmup 3 --output /tmp/test_camera.jpg
    ./test_camera.py --list-cameras
    ./test_camera.py --no-save
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

try:
    from picamera2 import Picamera2
except Exception as exc:  # pragma: no cover - depends on target system packages
    print(
        "ERROR: Could not import Picamera2. Is python3-picamera2 installed?\n"
        f"Details: {exc}",
        file=sys.stderr,
    )
    raise SystemExit(1)

try:
    from libcamera import controls as libcamera_controls
except Exception:  # pragma: no cover - optional at runtime
    libcamera_controls = None


_PICAM2: Picamera2 | None = None


def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test whether the NestCamDIY camera is detected and usable."
    )
    parser.add_argument(
        "--camera",
        type=int,
        default=0,
        help="Camera index to open (default: 0)",
    )
    parser.add_argument(
        "--width",
        type=int,
        default=1280,
        help="Requested capture width (default: 1280)",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=720,
        help="Requested capture height (default: 720)",
    )
    parser.add_argument(
        "--warmup",
        type=float,
        default=2.0,
        help="Seconds to wait after starting the camera (default: 2.0)",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=None,
        help="Optional JPEG output path (default: ./test_camera_<timestamp>.jpg)",
    )
    parser.add_argument(
        "--no-save",
        action="store_true",
        help="Do not save a JPEG; only test in-memory capture",
    )
    parser.add_argument(
        "--list-cameras",
        action="store_true",
        help="List the cameras Picamera2 can see and exit",
    )
    return parser.parse_args()


def format_camera_info(info: dict[str, Any], fallback_index: int) -> str:
    num = info.get("Num", fallback_index)
    model = info.get("Model", "unknown")
    ident = info.get("Id", "unknown")
    location = info.get("Location", "unknown")
    rotation = info.get("Rotation", "unknown")
    return (
        f"[{num}] model={model!r}, id={ident!r}, "
        f"location={location!r}, rotation={rotation!r}"
    )


def list_cameras() -> list[dict[str, Any]]:
    try:
        infos = Picamera2.global_camera_info()
    except Exception as exc:
        print(f"Could not query camera list: {exc}", file=sys.stderr)
        return []

    if not infos:
        print("No cameras detected by Picamera2.")
        return []

    print("Detected cameras:")
    for idx, info in enumerate(infos):
        print(f"  {format_camera_info(info, idx)}")
    return infos


def build_output_path(requested: Path | None) -> Path:
    if requested is not None:
        return requested
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    return Path.cwd() / f"test_camera_{stamp}.jpg"


def cleanup() -> None:
    global _PICAM2
    if _PICAM2 is not None:
        try:
            _PICAM2.stop()
        except Exception:
            pass
        try:
            _PICAM2.close()
        except Exception:
            pass
        _PICAM2 = None


def cleanup_and_exit(code: int = 0) -> None:
    cleanup()
    raise SystemExit(code)


def handle_signal(signum, frame) -> None:  # type: ignore[no-untyped-def]
    print(f"\nReceived signal {signum}; stopping camera.", flush=True)
    cleanup_and_exit(0)


def maybe_enable_autofocus(picam2: Picamera2) -> str:
    """Enable continuous autofocus when the camera supports it.

    The Arducam module based on Camera Module 3 hardware may expose autofocus
    controls, while the Waveshare IMX462 generally will not. Lack of autofocus
    support is not a failure condition.
    """
    if libcamera_controls is None:
        return "Autofocus status: libcamera controls not available; leaving defaults unchanged."

    try:
        camera_controls = getattr(picam2, "camera_controls", {}) or {}
        if "AfMode" not in camera_controls:
            return "Autofocus status: not supported by this camera."

        picam2.set_controls({"AfMode": libcamera_controls.AfModeEnum.Continuous})
        return "Autofocus status: continuous autofocus enabled."
    except Exception as exc:
        return f"Autofocus status: could not configure autofocus ({exc})."


def extract_frame_stats(frame: Any) -> tuple[tuple[int, ...], str, int | None, int | None]:
    shape = tuple(int(x) for x in getattr(frame, "shape", ()))
    dtype = str(getattr(frame, "dtype", "unknown"))

    min_value = None
    max_value = None
    try:
        min_value = int(frame.min())
        max_value = int(frame.max())
    except Exception:
        pass

    return shape, dtype, min_value, max_value


def main() -> int:
    global _PICAM2

    args = parse_args()

    if args.camera < 0:
        print("--camera must be zero or greater", file=sys.stderr)
        return 2
    if args.width <= 0 or args.height <= 0:
        print("--width and --height must be positive integers", file=sys.stderr)
        return 2
    if args.warmup < 0:
        print("--warmup must be non-negative", file=sys.stderr)
        return 2

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    infos = list_cameras()
    if args.list_cameras:
        return 0 if infos else 1

    if not infos:
        print(
            "\nNo camera was detected. If you are using the Waveshare IMX462, "
            "verify /boot/firmware/config.txt contains the required overlay "
            "settings from the README, then reboot.",
            file=sys.stderr,
        )
        return 1

    if args.camera >= len(infos):
        print(
            f"Requested camera index {args.camera}, but only {len(infos)} camera(s) were detected.",
            file=sys.stderr,
        )
        return 2

    print(f"\nOpening camera {args.camera}...")
    print(f"Selected: {format_camera_info(infos[args.camera], args.camera)}")

    try:
        _PICAM2 = Picamera2(args.camera)
    except Exception as exc:
        print(f"ERROR: Failed to open camera {args.camera}: {exc}", file=sys.stderr)
        return 1

    print(maybe_enable_autofocus(_PICAM2))

    config = _PICAM2.create_preview_configuration(
        main={"size": (args.width, args.height), "format": "RGB888"},
        queue=False,
    )
    _PICAM2.configure(config)

    actual_config = _PICAM2.camera_configuration()
    print(f"Requested main stream: {args.width}x{args.height} RGB888")
    print(f"Configured main stream: {actual_config.get('main')}")

    try:
        _PICAM2.start()
        print(f"Camera started. Waiting {args.warmup:.1f} second(s) for auto controls to settle...")
        if args.warmup > 0:
            time.sleep(args.warmup)

        metadata = _PICAM2.capture_metadata()
        frame = _PICAM2.capture_array("main")
    except Exception as exc:
        print(f"ERROR: Camera started but capture failed: {exc}", file=sys.stderr)
        return 1

    shape, dtype, min_value, max_value = extract_frame_stats(frame)
    if len(shape) < 2 or shape[0] <= 0 or shape[1] <= 0:
        print(
            f"ERROR: Capture returned an invalid frame shape: {shape!r}",
            file=sys.stderr,
        )
        return 1

    print("\nCapture succeeded.")
    print(f"Frame shape: {shape}")
    print(f"Frame dtype: {dtype}")
    if min_value is not None and max_value is not None:
        print(f"Pixel value range: {min_value}..{max_value}")
        if min_value == max_value:
            print(
                "Warning: the frame has no visible variation. The camera is responding, "
                "but the scene may be completely dark, overexposed, or blocked."
            )

    if metadata:
        print("Metadata:")
        for key in (
            "ExposureTime",
            "AnalogueGain",
            "Lux",
            "ColourTemperature",
            "AfState",
            "LensPosition",
        ):
            if key in metadata:
                print(f"  {key}: {metadata[key]}")

    if not args.no_save:
        output_path = build_output_path(args.output)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        try:
            _PICAM2.capture_file(str(output_path))
            size_bytes = output_path.stat().st_size
            print(f"Saved JPEG: {output_path} ({size_bytes} bytes)")
        except Exception as exc:
            print(
                f"WARNING: In-memory capture worked, but saving JPEG failed: {exc}",
                file=sys.stderr,
            )
            print("Treating this as a failed test because the camera output was not fully usable.", file=sys.stderr)
            return 1

    print("\nPASS: The camera was detected, opened successfully, and produced an image.")
    return 0


if __name__ == "__main__":
    exit_code = 1
    try:
        exit_code = main()
    finally:
        cleanup()
    sys.exit(exit_code)
