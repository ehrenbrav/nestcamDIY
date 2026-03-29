#!/usr/bin/env python3
"""
NestCamDIY installer.

This is a system installer for Raspberry Pi OS / Debian-based systems. It is
not a setuptools packaging script.

What it does:
- installs required OS packages with apt
- enables I2C support for Raspberry Pi hardware
- installs the current NestCam files into standard locations
- creates the NestCam config and state directories
- installs a default environment file if one does not already exist
- installs and enables the systemd service and retention timer
- optionally starts or restarts the service and timer

Intended usage:
    sudo python3 setup.py
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Iterable

APP_NAME = "nestcam"
INSTALL_ROOT = Path("/opt/nestcam")
CONFIG_DIR = Path("/etc/nestcam")
STATE_DIR = Path("/var/lib/nestcam")
RECORDINGS_DIR = STATE_DIR / "recordings"
SYSTEMD_DIR = Path("/etc/systemd/system")
ENV_FILE = CONFIG_DIR / "nestcam.env"
BOOT_CONFIG = Path("/boot/firmware/config.txt")

APT_PACKAGES = [
    "python3-picamera2",
    "python3-numpy",
    "python3-smbus",
    "python3-smbus2",
    "i2c-tools",
    "iproute2",
]

DEFAULT_ENV = """# NestCam configuration
# This file is read by nestcam.service and nestcam-retention.service.

LIVE_BIND=0.0.0.0
LIVE_PORT=8080

LORES_W=640
LORES_H=480

VIDEO_W=1280
VIDEO_H=720
FPS=20
BITRATE=2000000

RECORDINGS_ROOT=/var/lib/nestcam/recordings
STATUS_FILE=/run/nestcam/status.txt

# LAN-only is the primary default security model. Authentication is disabled by
# default. If you later enable it, do not expose this service directly to the
# internet.
AUTH_ENABLED=0
# LIVE_USER=
# LIVE_PASS=

# Leave blank to auto-detect via the default route.
WIFI_IFACE=

# Recording is disabled by default for initial field testing.
RECORDING_ENABLED=0
MIN_FREE_GB=2.0

# Optional retention tuning. The retention service currently passes explicit
# values in its unit file, so these are informational unless you customize the
# unit.
# RETENTION_SCRIPT=/opt/nestcam/retention.py
# RETENTION_MAX_GB=80.0
# RETENTION_MIN_FREE_GB=15.0
"""

REBOOT_REQUIRED = False


try:
    from typing import NoReturn  # type: ignore
except Exception:  # pragma: no cover
    NoReturn = None  # type: ignore


def die(msg: str, code: int = 1) -> "NoReturn":
    print(f"ERROR: {msg}", file=sys.stderr)
    raise SystemExit(code)


def run(cmd: list[str], *, check: bool = True, env: dict[str, str] | None = None) -> subprocess.CompletedProcess[str]:
    print("+", " ".join(cmd))
    return subprocess.run(cmd, text=True, check=check, env=env)


def require_root() -> None:
    if os.geteuid() != 0:
        die("run this installer as root, for example: sudo python3 setup.py")


def repo_root_from_script() -> Path:
    return Path(__file__).resolve().parent


def ensure_dir(path: Path, mode: int = 0o755) -> None:
    path.mkdir(parents=True, exist_ok=True)
    try:
        path.chmod(mode)
    except PermissionError:
        pass


def install_file(src: Path, dst: Path, mode: int) -> None:
    if not src.exists():
        die(f"required source file not found: {src}")
    ensure_dir(dst.parent)
    shutil.copy2(src, dst)
    dst.chmod(mode)
    print(f"Installed {src} -> {dst}")


def maybe_install_file(src: Path, dst: Path, mode: int) -> None:
    if src.exists():
        install_file(src, dst, mode)


def write_default_env(path: Path, *, overwrite: bool) -> None:
    ensure_dir(path.parent, 0o755)
    if path.exists() and not overwrite:
        print(f"Preserving existing env file: {path}")
        return
    path.write_text(DEFAULT_ENV, encoding="utf-8")
    path.chmod(0o640)
    print(f"Wrote default env file: {path}")


def apt_install(packages: Iterable[str], *, update: bool) -> None:
    apt_get = shutil.which("apt-get")
    if not apt_get:
        die("apt-get not found; this installer currently supports Debian-based systems only")

    env = os.environ.copy()
    env.setdefault("DEBIAN_FRONTEND", "noninteractive")

    if update:
        run([apt_get, "update"], env=env)

    install_cmd = [apt_get, "install", "-y", "--no-install-recommends", *packages]
    run(install_cmd, env=env)


def set_reboot_required() -> None:
    global REBOOT_REQUIRED
    REBOOT_REQUIRED = True


def enable_i2c_via_raspi_config() -> bool:
    raspi_config = shutil.which("raspi-config")
    if not raspi_config:
        return False

    try:
        run([raspi_config, "nonint", "do_i2c", "0"])
        set_reboot_required()
        print("Enabled I2C using raspi-config.")
        return True
    except subprocess.CalledProcessError as exc:
        print(f"Warning: raspi-config failed to enable I2C ({exc}). Falling back to config.txt edit.")
        return False


def ensure_line_present(text: str, line: str) -> tuple[str, bool]:
    lines = text.splitlines()
    if line in lines:
        return text, False
    if text and not text.endswith("\n"):
        text += "\n"
    text += line + "\n"
    return text, True


def enable_i2c_via_config_txt() -> None:
    if not BOOT_CONFIG.exists():
        die(f"could not find Raspberry Pi boot config: {BOOT_CONFIG}")

    original = BOOT_CONFIG.read_text(encoding="utf-8")
    updated = original

    updated = updated.replace("dtparam=i2c_arm=off", "dtparam=i2c_arm=on")
    updated = updated.replace("dtparam=i2c_arm=false", "dtparam=i2c_arm=on")
    updated = updated.replace("#dtparam=i2c_arm=on", "dtparam=i2c_arm=on")

    updated, added_line = ensure_line_present(updated, "dtparam=i2c_arm=on")
    changed = (updated != original) or added_line

    if changed:
        backup = BOOT_CONFIG.with_suffix(".txt.bak")
        shutil.copy2(BOOT_CONFIG, backup)
        BOOT_CONFIG.write_text(updated, encoding="utf-8")
        set_reboot_required()
        print(f"Enabled I2C in {BOOT_CONFIG} (backup written to {backup}).")
    else:
        print(f"I2C already appears enabled in {BOOT_CONFIG}.")


def enable_i2c() -> None:
    if enable_i2c_via_raspi_config():
        return
    enable_i2c_via_config_txt()


def install_system_files(repo_root: Path) -> None:
    service_root = repo_root / "services"

    install_file(service_root / "nestcam_daemon.py", INSTALL_ROOT / "nestcam_daemon.py", 0o755)
    install_file(service_root / "retention.py", INSTALL_ROOT / "retention.py", 0o755)
    maybe_install_file(repo_root / "power_stats.py", INSTALL_ROOT / "power_stats.py", 0o755)

    install_file(service_root / "nestcam.service", SYSTEMD_DIR / "nestcam.service", 0o644)
    install_file(
        service_root / "nestcam-retention.service",
        SYSTEMD_DIR / "nestcam-retention.service",
        0o644,
    )
    install_file(
        service_root / "nestcam-retention.timer",
        SYSTEMD_DIR / "nestcam-retention.timer",
        0o644,
    )


def create_directories() -> None:
    ensure_dir(INSTALL_ROOT, 0o755)
    ensure_dir(CONFIG_DIR, 0o755)
    ensure_dir(STATE_DIR, 0o755)
    ensure_dir(RECORDINGS_DIR, 0o755)


def systemd_reload() -> None:
    run(["systemctl", "daemon-reload"])


def systemd_enable(*, start_now: bool) -> None:
    run(["systemctl", "enable", "nestcam.service"])
    run(["systemctl", "enable", "nestcam-retention.timer"])

    if start_now:
        run(["systemctl", "restart", "nestcam.service"])
        run(["systemctl", "restart", "nestcam-retention.timer"])


def validate_repo_layout(repo_root: Path) -> None:
    required = [
        repo_root / "services" / "nestcam_daemon.py",
        repo_root / "services" / "retention.py",
        repo_root / "services" / "nestcam.service",
        repo_root / "services" / "nestcam-retention.service",
        repo_root / "services" / "nestcam-retention.timer",
    ]
    missing = [str(p) for p in required if not p.exists()]
    if missing:
        die(
            "repository layout not recognized. Missing required file(s):\n  - "
            + "\n  - ".join(missing)
        )


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Install NestCamDIY on a Raspberry Pi")
    p.add_argument(
        "--repo-root",
        default=str(repo_root_from_script()),
        help="Path to the repository root (default: directory containing this script)",
    )
    p.add_argument(
        "--skip-apt",
        action="store_true",
        help="Skip apt package installation",
    )
    p.add_argument(
        "--skip-apt-update",
        action="store_true",
        help="Skip apt-get update before package installation",
    )
    p.add_argument(
        "--skip-i2c",
        action="store_true",
        help="Do not enable I2C in raspi-config or /boot/firmware/config.txt",
    )
    p.add_argument(
        "--replace-env",
        action="store_true",
        help="Overwrite /etc/nestcam/nestcam.env if it already exists",
    )
    p.add_argument(
        "--no-start",
        action="store_true",
        help="Enable services but do not start or restart them",
    )
    return p.parse_args()


def main() -> int:
    args = parse_args()
    require_root()

    repo_root = Path(args.repo_root).resolve()
    validate_repo_layout(repo_root)

    create_directories()

    if not args.skip_apt:
        apt_install(APT_PACKAGES, update=not args.skip_apt_update)

    if not args.skip_i2c:
        enable_i2c()

    install_system_files(repo_root)
    write_default_env(ENV_FILE, overwrite=args.replace_env)

    systemd_reload()
    systemd_enable(start_now=not args.no_start)

    print()
    print("NestCam installation complete.")
    print(f"Code installed to: {INSTALL_ROOT}")
    print(f"Config file:        {ENV_FILE}")
    print(f"Recordings dir:     {RECORDINGS_DIR}")
    print()
    print("Recommended checks:")
    print("  systemctl status nestcam.service")
    print("  systemctl status nestcam-retention.timer")
    print("  journalctl -u nestcam.service -b --no-pager")
    print("  i2cdetect -y 1")
    if REBOOT_REQUIRED:
        print()
        print("A reboot is recommended before using the INA219 / I2C features.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
