#!/usr/bin/env python3
"""Simple IR LED PWM test for NestCamDIY.

This script drives the LED control line on BCM GPIO18 (physical pin 12),
ramps the LEDs from off to full brightness using PWM, briefly holds at full
brightness, then ramps back down and turns them fully off.

Usage:
    ./test_led.py
    ./test_led.py --steps 80 --step-delay 0.03 --hold-full 2.0 --cycles 2

Notes:
- IR LEDs are not easily visible to the human eye. To verify them, either use
  the visible test LED pigtail described in the README, or look through a
  phone camera.
- Press Ctrl-C to stop the test early. The script will turn the LEDs off and
  clean up the GPIO state before exiting.
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from typing import Optional

LED_GPIO = 18   # BCM numbering; physical pin 12 per project README
PWM_HZ = 500


class LedDriverBase:
    def set_brightness(self, value: float) -> None:
        raise NotImplementedError

    def off(self) -> None:
        self.set_brightness(0.0)

    def close(self) -> None:
        raise NotImplementedError


class GpioZeroDriver(LedDriverBase):
    def __init__(self, gpio: int) -> None:
        from gpiozero import PWMLED

        self.led = PWMLED(gpio, active_high=True, initial_value=0.0)

    def set_brightness(self, value: float) -> None:
        self.led.value = max(0.0, min(1.0, value))

    def close(self) -> None:
        self.led.close()


class RPiGPIODriver(LedDriverBase):
    def __init__(self, gpio: int, pwm_hz: int) -> None:
        import RPi.GPIO as GPIO

        self.GPIO = GPIO
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(gpio, GPIO.OUT, initial=GPIO.LOW)
        self.pwm = GPIO.PWM(gpio, pwm_hz)
        self.pwm.start(0)

    def set_brightness(self, value: float) -> None:
        duty = max(0.0, min(100.0, value * 100.0))
        self.pwm.ChangeDutyCycle(duty)

    def close(self) -> None:
        try:
            self.pwm.ChangeDutyCycle(0)
            self.pwm.stop()
        finally:
            self.GPIO.cleanup()


_DRIVER: Optional[LedDriverBase] = None


def build_driver() -> LedDriverBase:
    errors = []

    try:
        return GpioZeroDriver(LED_GPIO)
    except Exception as exc:
        errors.append(f"gpiozero failed: {exc}")

    try:
        return RPiGPIODriver(LED_GPIO, PWM_HZ)
    except Exception as exc:
        errors.append(f"RPi.GPIO failed: {exc}")

    joined = "; ".join(errors) if errors else "no GPIO backend available"
    raise RuntimeError(
        "Could not initialize a PWM-capable GPIO backend on this Pi. "
        f"Details: {joined}"
    )


def cleanup_and_exit(code: int = 0) -> None:
    global _DRIVER
    if _DRIVER is not None:
        try:
            _DRIVER.off()
            time.sleep(0.05)
        except Exception:
            pass
        try:
            _DRIVER.close()
        except Exception:
            pass
        _DRIVER = None
    raise SystemExit(code)


def handle_signal(signum, frame) -> None:  # type: ignore[no-untyped-def]
    print(f"\nReceived signal {signum}; turning LEDs off.", flush=True)
    cleanup_and_exit(0)


def ramp(driver: LedDriverBase, start: int, stop: int, step: int, step_delay: float) -> None:
    for pct in range(start, stop + step, step):
        bounded_pct = max(0, min(100, pct))
        driver.set_brightness(bounded_pct / 100.0)
        print(f"Brightness: {bounded_pct:3d}%", flush=True)
        time.sleep(step_delay)



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Test the NestCamDIY IR LED PWM output.")
    parser.add_argument("--steps", type=int, default=50,
                        help="Number of brightness steps from 0%% to 100%% (default: 50)")
    parser.add_argument("--step-delay", type=float, default=0.04,
                        help="Delay in seconds between brightness steps (default: 0.04)")
    parser.add_argument("--hold-full", type=float, default=1.0,
                        help="Seconds to hold at full brightness (default: 1.0)")
    parser.add_argument("--cycles", type=int, default=1,
                        help="Number of ramp up/down cycles to run (default: 1)")
    return parser.parse_args()



def main() -> int:
    global _DRIVER
    args = parse_args()

    if args.steps <= 0:
        print("--steps must be a positive integer", file=sys.stderr)
        return 2
    if args.step_delay < 0 or args.hold_full < 0 or args.cycles <= 0:
        print("step timing values must be non-negative and --cycles must be positive", file=sys.stderr)
        return 2

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    _DRIVER = build_driver()

    print(f"Using LED control GPIO BCM{LED_GPIO} (physical pin 12).")
    print("Ramping LEDs from off to full brightness using PWM.")
    print("Tip: for IR LEDs, verify using a phone camera or the visible test LED.")

    step_pct = max(1, round(100 / args.steps))

    try:
        for cycle in range(1, args.cycles + 1):
            print(f"\nCycle {cycle} of {args.cycles}")
            _DRIVER.off()
            time.sleep(0.2)
            ramp(_DRIVER, 0, 100, step_pct, args.step_delay)
            if args.hold_full > 0:
                print(f"Holding at 100% for {args.hold_full:.2f} seconds")
                time.sleep(args.hold_full)
            ramp(_DRIVER, 100, 0, -step_pct, args.step_delay)

        print("\nTest complete. Turning LEDs off.")
        cleanup_and_exit(0)
    except KeyboardInterrupt:
        print("\nInterrupted. Turning LEDs off.")
        cleanup_and_exit(0)


if __name__ == "__main__":
    sys.exit(main())
