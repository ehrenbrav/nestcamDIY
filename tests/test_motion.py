#!/usr/bin/env python3
"""Simple motion sensor test for NestCamDIY.

This script reads the AM312 motion sensor on BCM GPIO23 (physical pin 16)
and mirrors the motion state to the LED control line on BCM GPIO18 (physical
pin 12). When motion is detected, the LED is turned on. When no motion is
present, the LED is turned off.

It also prints the detected motion values to the console. By default it prints
only when the value changes. Use --print-all to print every sample.

Usage:
    ./test_motion.py
    ./test_motion.py --print-all
    ./test_motion.py --poll-interval 0.1 --led-brightness 0.5
"""

from __future__ import annotations

import argparse
import signal
import sys
import time
from datetime import datetime
from typing import Optional

LED_GPIO = 18      # BCM numbering; physical pin 12
MOTION_GPIO = 23   # BCM numbering; physical pin 16
PWM_HZ = 500


class LedDriverBase:
    def set_brightness(self, value: float) -> None:
        raise NotImplementedError

    def off(self) -> None:
        self.set_brightness(0.0)

    def close(self) -> None:
        raise NotImplementedError


class MotionReaderBase:
    def read(self) -> int:
        raise NotImplementedError

    def close(self) -> None:
        raise NotImplementedError


class GpioZeroLedDriver(LedDriverBase):
    def __init__(self, gpio: int) -> None:
        from gpiozero import PWMLED

        self.led = PWMLED(gpio, active_high=True, initial_value=0.0)

    def set_brightness(self, value: float) -> None:
        self.led.value = max(0.0, min(1.0, value))

    def close(self) -> None:
        self.led.close()


class RPiGPIOLedDriver(LedDriverBase):
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
            self.GPIO.cleanup(LED_GPIO)


class GpioZeroMotionReader(MotionReaderBase):
    def __init__(self, gpio: int) -> None:
        from gpiozero import DigitalInputDevice

        # Default to an internal pull-down so the Pi input does not float if the
        # PIR output is disconnected, weakly driven, or the module is faulty.
        self.sensor = DigitalInputDevice(gpio, pull_up=False, active_state=True)

    def read(self) -> int:
        return int(self.sensor.value)

    def close(self) -> None:
        self.sensor.close()


class RPiGPIOMotionReader(MotionReaderBase):
    def __init__(self, gpio: int) -> None:
        import RPi.GPIO as GPIO

        self.GPIO = GPIO
        GPIO.setwarnings(False)
        GPIO.setmode(GPIO.BCM)
        GPIO.setup(gpio, GPIO.IN, pull_up_down=GPIO.PUD_DOWN)
        self.gpio = gpio

    def read(self) -> int:
        return int(self.GPIO.input(self.gpio))

    def close(self) -> None:
        self.GPIO.cleanup(self.gpio)


_LED: Optional[LedDriverBase] = None
_MOTION: Optional[MotionReaderBase] = None


def timestamp() -> str:
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def build_led_driver() -> LedDriverBase:
    errors = []

    try:
        return GpioZeroLedDriver(LED_GPIO)
    except Exception as exc:
        errors.append(f"gpiozero LED failed: {exc}")

    try:
        return RPiGPIOLedDriver(LED_GPIO, PWM_HZ)
    except Exception as exc:
        errors.append(f"RPi.GPIO LED failed: {exc}")

    raise RuntimeError("Could not initialize LED GPIO backend. " + "; ".join(errors))



def build_motion_reader() -> MotionReaderBase:
    errors = []

    try:
        return GpioZeroMotionReader(MOTION_GPIO)
    except Exception as exc:
        errors.append(f"gpiozero motion failed: {exc}")

    try:
        return RPiGPIOMotionReader(MOTION_GPIO)
    except Exception as exc:
        errors.append(f"RPi.GPIO motion failed: {exc}")

    raise RuntimeError("Could not initialize motion GPIO backend. " + "; ".join(errors))



def cleanup_and_exit(code: int = 0) -> None:
    global _LED, _MOTION

    if _LED is not None:
        try:
            _LED.off()
            time.sleep(0.05)
        except Exception:
            pass
        try:
            _LED.close()
        except Exception:
            pass
        _LED = None

    if _MOTION is not None:
        try:
            _MOTION.close()
        except Exception:
            pass
        _MOTION = None

    raise SystemExit(code)



def handle_signal(signum, frame) -> None:  # type: ignore[no-untyped-def]
    print(f"\nReceived signal {signum}; turning LED off and exiting.", flush=True)
    cleanup_and_exit(0)



def parse_args() -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Test the NestCamDIY motion sensor and mirror its state to the LED."
    )
    parser.add_argument(
        "--poll-interval",
        type=float,
        default=0.05,
        help="Seconds between sensor reads (default: 0.05)",
    )
    parser.add_argument(
        "--led-brightness",
        type=float,
        default=1.0,
        help="LED brightness from 0.0 to 1.0 when motion is active (default: 1.0)",
    )
    parser.add_argument(
        "--print-all",
        action="store_true",
        help="Print every sampled sensor value instead of only changes",
    )
    parser.add_argument(
        "--no-led-test",
        action="store_true",
        help="Test motion sensor only, skip LED initialization (use when daemon is running)",
    )
    return parser.parse_args()



def main() -> int:
    global _LED, _MOTION

    args = parse_args()
    if args.poll_interval <= 0:
        print("--poll-interval must be greater than zero", file=sys.stderr)
        return 2
    if not args.no_led_test and not (0.0 <= args.led_brightness <= 1.0):
        print("--led-brightness must be between 0.0 and 1.0", file=sys.stderr)
        return 2

    signal.signal(signal.SIGINT, handle_signal)
    signal.signal(signal.SIGTERM, handle_signal)

    if not args.no_led_test:
        _LED = build_led_driver()
        _LED.off()

    _MOTION = build_motion_reader()

    if args.no_led_test:
        print(f"Monitoring motion on BCM{MOTION_GPIO} (physical pin 16) - LED test disabled.")
        print("Wave your hand in front of the sensor. Press Ctrl-C to stop.")
    else:
        print(f"Monitoring motion on BCM{MOTION_GPIO} (physical pin 16).")
        print(f"Driving LED on BCM{LED_GPIO} (physical pin 12).")
        print("Wave your hand in front of the sensor. Press Ctrl-C to stop.")

    last_value: Optional[int] = None

    try:
        while True:
            value = _MOTION.read()
            if not args.no_led_test:
                if value:
                    _LED.set_brightness(args.led_brightness)
                else:
                    _LED.off()

            if args.print_all or value != last_value:
                state = "MOTION" if value else "idle"
                print(f"[{timestamp()}] motion={value} state={state}", flush=True)
                last_value = value

            time.sleep(args.poll_interval)
    except KeyboardInterrupt:
        if args.no_led_test:
            print("\nInterrupted. Exiting.")
        else:
            print("\nInterrupted. Turning LED off.")
        cleanup_and_exit(0)


if __name__ == "__main__":
    sys.exit(main())
