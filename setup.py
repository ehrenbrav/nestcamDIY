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
import pwd
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
    "python3-smbus",
    "python3-smbus2",
    "i2c-tools",
    "iproute2",
]

DEFAULT_ENV_SOURCE = Path("services") / "nestcam.env"

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


def write_default_env(src: Path, dst: Path, *, overwrite: bool) -> None:
    if not src.exists():
        die(f"default env file not found: {src}")

    ensure_dir(dst.parent, 0o755)
    if dst.exists() and not overwrite:
        print(f"Preserving existing env file: {dst}")
        return

    shutil.copy2(src, dst)
    dst.chmod(0o640)
    print(f"Installed default env file: {src} -> {dst}")


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
    install_file(service_root / "index.html", INSTALL_ROOT / "index.html", 0o644)
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


def resolve_invoking_user_home() -> Path | None:
    sudo_user = os.environ.get("SUDO_USER")
    if sudo_user and sudo_user != "root":
        try:
            candidate = Path(pwd.getpwnam(sudo_user).pw_dir)
            if candidate.exists():
                return candidate
        except KeyError:
            pass

    home = Path.home()
    if home.exists():
        return home
    return None


def create_recordings_symlink() -> None:
    home_dir = resolve_invoking_user_home()
    if home_dir is None:
        print("Warning: could not determine a home directory for the recordings symlink.")
        return

    symlink_path = home_dir / "recordings"

    if symlink_path.is_symlink():
        current_target_raw = Path(os.readlink(symlink_path))
        if not current_target_raw.is_absolute():
            current_target_raw = (symlink_path.parent / current_target_raw)

        current_target_resolved = current_target_raw.resolve(strict=False)
        expected_target_resolved = RECORDINGS_DIR.resolve(strict=False)

        if current_target_resolved == expected_target_resolved:
            print(
                f"Recordings symlink already exists: {symlink_path} -> "
                f"{current_target_raw} (resolved: {current_target_resolved})"
            )
            return

        die(
            f"existing symlink points elsewhere: {symlink_path} -> "
            f"{current_target_raw} (resolved: {current_target_resolved}); "
            f"expected {RECORDINGS_DIR} (resolved: {expected_target_resolved})"
        )

    if symlink_path.exists():
        die(f"cannot create recordings symlink because this path already exists: {symlink_path}")

    symlink_path.symlink_to(RECORDINGS_DIR, target_is_directory=True)
    print(f"Created recordings symlink: {symlink_path} -> {RECORDINGS_DIR}")



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
        repo_root / "services" / "index.html",
        repo_root / DEFAULT_ENV_SOURCE,
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
    write_default_env(repo_root / DEFAULT_ENV_SOURCE, ENV_FILE, overwrite=args.replace_env)
    create_recordings_symlink()

    systemd_reload()
    systemd_enable(start_now=not args.no_start)

    print()
    print("NestCam installation complete.")
    print(f"Code installed to: {INSTALL_ROOT}")
    print(f"Config file:        {ENV_FILE}")
    recordings_link = resolve_invoking_user_home()
    recordings_link_str = str(recordings_link / "recordings") if recordings_link else "(not created)"
    print(f"Recordings dir:     {RECORDINGS_DIR}")
    print(f"Home symlink:       {recordings_link_str}")
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
