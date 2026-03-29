#!/usr/bin/env python3
"""
INA219.py — INA219 telemetry reader with estimated battery remaining (mAh and time).

What this can do:
- Read supply/battery-side voltage and shunt voltage from INA219.
- Estimate current from shunt voltage and assumed shunt resistance.
- Estimate remaining capacity (mAh) and runtime using:
  (A) voltage-band estimate (Seengreat Pi Zero UPS HAT (B) LED bands), and/or
  (B) coulomb counting (integrating current over time).

Limitations:
- INA219 does NOT directly know state-of-charge. Everything here is an estimate.
- Voltage-based SoC is especially inaccurate under load due to voltage sag.
- Coulomb counting accuracy depends on current accuracy and a reasonable starting point.
"""

import argparse
import time

try:
    from smbus2 import SMBus
except ImportError:
    from smbus import SMBus  # type: ignore


REG_CONFIG = 0x00
REG_SHUNT_V = 0x01
REG_BUS_V = 0x02


def swap16(x: int) -> int:
    # SMBus word reads/writes are little-endian; INA219 registers are big-endian.
    return ((x & 0xFF) << 8) | ((x >> 8) & 0xFF)


def to_signed16(x: int) -> int:
    return x - 0x10000 if x & 0x8000 else x


class INA219:
    def __init__(self, bus: int = 1, addr: int = 0x43):
        self.addr = addr
        self.bus = SMBus(bus)

    def close(self) -> None:
        try:
            self.bus.close()
        except Exception:
            pass

    def read_word(self, reg: int) -> int:
        raw = self.bus.read_word_data(self.addr, reg)
        return swap16(raw)

    def write_word(self, reg: int, value: int) -> None:
        self.bus.write_word_data(self.addr, reg, swap16(value & 0xFFFF))

    def configure_defaults(self) -> None:
        """
        Continuous measurement defaults:
        - Up to 32 V bus range
        - Up to ±320 mV across the shunt
        - 12-bit conversions for bus and shunt
        """
        self.write_word(REG_CONFIG, 0x399F)

    def read_supply_voltage_volts(self) -> float:
        """
        INA219 'bus voltage' register (0x02).
        Bits [15:3] are the value; each step is 4 mV.
        """
        raw = self.read_word(REG_BUS_V)
        return ((raw >> 3) * 0.004)

    def read_shunt_voltage_volts(self) -> float:
        """
        INA219 'shunt voltage' register (0x01), signed.
        Each step is 10 microvolts.
        """
        raw = to_signed16(self.read_word(REG_SHUNT_V))
        return raw * 10e-6


def describe_current_direction(current_amps: float, threshold_amps: float = 0.001) -> str:
    if current_amps > threshold_amps:
        return "charging (current into battery)"
    if current_amps < -threshold_amps:
        return "discharging (current from battery)"
    return "near zero (not clearly charging or discharging)"


def soc_percent_from_voltage_seengreat_b(voltage: float) -> float:
    """
    Seengreat Pi Zero UPS HAT (B) LED bands (step estimate):
    - 3.87V–4.2V   -> 100%
    - 3.70V–3.87V  -> 75%
    - 3.55V–3.70V  -> 50%
    - 3.40V–3.55V  -> 25%
    - below 3.40V  -> 0%
    """
    if voltage >= 3.87:
        return 100.0
    if voltage >= 3.70:
        return 75.0
    if voltage >= 3.55:
        return 50.0
    if voltage >= 3.40:
        return 25.0
    return 0.0


def format_hours(hours: float) -> str:
    if hours < 0:
        return "n/a"
    minutes = int(round(hours * 60))
    h = minutes // 60
    m = minutes % 60
    if h <= 0:
        return f"{m} min"
    return f"{h} h {m:02d} min"


def main() -> int:
    p = argparse.ArgumentParser(description="Read INA219 telemetry and estimate battery remaining (mAh, runtime).")
    p.add_argument("--bus", type=int, default=1, help="I2C bus number (default: 1)")
    p.add_argument(
        "--addr",
        type=lambda s: int(s, 16),
        default=0x43,
        help="I2C address in hex, for example 0x43 (default: 0x43)",
    )
    p.add_argument("--interval", type=float, default=2.0, help="Seconds between updates (default: 2.0)")
    p.add_argument(
        "--shunt-ohms",
        type=float,
        default=0.01,
        help="Shunt resistor value in ohms for current estimate (default: 0.1)",
    )
    p.add_argument("--no-config", action="store_true", help="Do not write device configuration on startup")

    # Battery estimation parameters
    p.add_argument(
        "--battery-mah",
        type=float,
        default=10000.0,
        help="Battery rated capacity in mAh (default: 10,000). Use 1000 for Seengreat HAT (A) typical battery.",
    )
    p.add_argument(
        "--soc-mode",
        choices=["none", "seengreat_b_voltage_steps"],
        default="seengreat_b_voltage_steps",
        help="How to estimate starting SoC from voltage (default: seengreat_b_voltage_steps)",
    )
    p.add_argument(
        "--initial-soc",
        type=float,
        default=-1.0,
        help="Initial SoC percent (0-100). If <0, estimate from voltage using --soc-mode (default: -1).",
    )
    p.add_argument(
        "--disable-coulomb-counting",
        action="store_false",
        default=True,
        help="Track remaining mAh by integrating current over time (recommended).",
    )
    p.add_argument(
        "--charge-efficiency",
        type=float,
        default=0.95,
        help="When charging, only this fraction is credited back into remaining mAh (default: 0.95).",
    )
    p.add_argument(
        "--smooth-seconds",
        type=float,
        default=30.0,
        help="Time constant (seconds) for smoothing current used in runtime estimate (default: 30).",
    )
    p.add_argument(
        "--min-discharge-ma",
        type=float,
        default=20.0,
        help="Below this discharge current (mA), runtime estimate is suppressed (default: 20).",
    )

    args = p.parse_args()

    dev = INA219(bus=args.bus, addr=args.addr)

    remaining_mah = None  # set after we have first voltage/current sample
    avg_discharge_ma = None  # smoothed discharge current magnitude (positive mA)
    last_t = None

    try:
        if not args.no_config:
            dev.configure_defaults()

        while True:
            now = time.time()
            supply_v = dev.read_supply_voltage_volts()
            shunt_v = dev.read_shunt_voltage_volts()

            current_a = shunt_v / args.shunt_ohms  # signed
            current_ma = current_a * 1000.0
            power_w = supply_v * current_a
            direction = describe_current_direction(current_a)

            # Initialize remaining_mah once
            if remaining_mah is None:
                if args.initial_soc >= 0.0:
                    soc = max(0.0, min(100.0, args.initial_soc))
                else:
                    if args.soc_mode == "seengreat_b_voltage_steps":
                        soc = soc_percent_from_voltage_seengreat_b(supply_v)
                    else:
                        soc = 100.0  # fallback, but soc_mode "none" is expected if you do not want this
                remaining_mah = args.battery_mah * (soc / 100.0)

            # Coulomb counting update
            if last_t is not None and args.disable_coulomb_counting:
                dt = max(0.0, now - last_t)  # seconds
                delta_mah = (current_ma * dt) / 3600.0  # mA * hours = mAh
                if delta_mah >= 0:
                    # Charging: credit back with efficiency
                    remaining_mah += delta_mah * args.charge_efficiency
                else:
                    # Discharging: subtract (delta_mah is negative)
                    remaining_mah += delta_mah
                remaining_mah = max(0.0, min(args.battery_mah, remaining_mah))

            # Smoothed discharge current for runtime estimate
            discharge_ma = -current_ma if current_ma < 0 else 0.0  # magnitude, mA
            if avg_discharge_ma is None:
                avg_discharge_ma = discharge_ma
            else:
                dt = args.interval if last_t is None else max(0.0, now - last_t)
                tau = max(1.0, args.smooth_seconds)
                alpha = dt / (tau + dt)
                avg_discharge_ma = (1 - alpha) * avg_discharge_ma + alpha * discharge_ma

            # Runtime estimate (only meaningful while discharging at a reasonable current)
            if avg_discharge_ma is not None and remaining_mah is not None and avg_discharge_ma >= args.min_discharge_ma:
                hours_left = remaining_mah / avg_discharge_ma
                runtime_str = format_hours(hours_left)
            else:
                runtime_str = "n/a"

            # Optional SoC display (derived from remaining_mah vs rated capacity)
            soc_from_remaining = 100.0 * (remaining_mah / args.battery_mah) if remaining_mah is not None else 0.0

            print(f"INA219 at I2C address 0x{args.addr:02x}")
            print(f"  Supply voltage (bus voltage):       {supply_v:.3f} V")
            print(f"  Shunt voltage (across resistor):    {shunt_v * 1000:+.3f} mV")
            print(f"  Estimated current:                  {current_ma:+.1f} mA")
            print(f"  Direction:                          {direction}")
            print(f"  Estimated power:                    {power_w:+.3f} W")

            print(f"  Battery rated capacity (configured): {args.battery_mah:.0f} mAh")
            print(f"  Estimated remaining capacity:        {remaining_mah:.0f} mAh")
            print(f"  Estimated state of charge:           {soc_from_remaining:.0f} %")
            print(f"  Estimated runtime at current load:   {runtime_str}")
            print("-" * 72)

            last_t = now
            time.sleep(args.interval)

    except KeyboardInterrupt:
        return 0
    finally:
        dev.close()


if __name__ == "__main__":
    raise SystemExit(main())
