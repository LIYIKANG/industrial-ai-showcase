from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
import pandas as pd


EXPECTED_COLUMNS = [
    "timestamp",
    "feed_flow",
    "feed_temp",
    "feed_tds",
    "feed_ph",
    "evap_temp",
    "evap_pressure",
    "compressor_freq",
    "compressor_power",
    "pump_power",
    "condensate_flow",
    "concentrate_flow",
    "total_power",
    "cip_flag",
    "alarm_flag",
]


def generate_sample_data(
    n_rows: int = 720,
    seed: int = 42,
    start_time: str = "2025-01-01 00:00:00",
    freq: str = "h",
) -> pd.DataFrame:
    """Generate demo data that roughly follows MVR wastewater evaporation behavior."""
    rng = np.random.default_rng(seed)
    n_rows = max(int(n_rows), 48)
    idx = np.arange(n_rows)
    timestamps = pd.date_range(start=start_time, periods=n_rows, freq=freq)

    daily_cycle = np.sin(2 * np.pi * idx / 24)
    weekly_cycle = np.sin(2 * np.pi * idx / (24 * 7))
    slow_cycle = np.sin(2 * np.pi * idx / (24 * 10))

    cip_interval = 168
    cip_flag = ((idx % cip_interval) == 0).astype(int)
    hours_since_cip = idx % cip_interval
    cip_age = hours_since_cip / cip_interval

    feed_flow = 12 + 3.5 * weekly_cycle + 1.2 * daily_cycle + rng.normal(0, 0.9, n_rows)
    feed_flow = np.clip(feed_flow, 5, 20)

    feed_temp = 33 + 4 * daily_cycle + 1.5 * weekly_cycle + rng.normal(0, 1.2, n_rows)
    feed_temp = np.clip(feed_temp, 22, 48)

    tds_base = 65000 + 26000 * slow_cycle + 18000 * weekly_cycle
    feed_tds = tds_base + 28000 * rng.beta(2, 6, n_rows) + rng.normal(0, 4500, n_rows)
    feed_tds = np.clip(feed_tds, 20000, 150000)

    feed_ph = 7.0 + 0.35 * np.sin(2 * np.pi * idx / 72) + rng.normal(0, 0.25, n_rows)
    feed_ph = np.clip(feed_ph, 5, 9)

    tds_factor = (feed_tds - 20000) / 130000
    fouling_factor = np.clip(0.72 * cip_age + 0.28 * tds_factor + rng.normal(0, 0.035, n_rows), 0, 1)

    evap_temp = 78 + 3.5 * daily_cycle + 4.0 * tds_factor + rng.normal(0, 1.1, n_rows)
    evap_temp = np.clip(evap_temp, 60, 95)

    evap_pressure = -73 + 0.35 * (evap_temp - 78) + 3.0 * fouling_factor
    evap_pressure += 0.9 * np.sin(2 * np.pi * idx / 36) + rng.normal(0, 0.9, n_rows)
    evap_pressure = np.clip(evap_pressure, -90, -50)

    compressor_freq = 36 + 0.9 * (feed_flow - 10) + 7.5 * tds_factor + 7.0 * fouling_factor
    compressor_freq += rng.normal(0, 1.4, n_rows)
    compressor_freq = np.clip(compressor_freq, 30, 60)

    condensate_ratio = 0.9 - 0.08 * fouling_factor - 0.05 * tds_factor + rng.normal(0, 0.025, n_rows)
    condensate_flow = feed_flow * condensate_ratio
    condensate_flow = np.clip(condensate_flow, 4, 18)
    condensate_flow = np.minimum(condensate_flow, feed_flow * 0.96)

    concentrate_flow = np.clip(feed_flow - condensate_flow + rng.normal(0, 0.15, n_rows), 0.3, 5.5)

    target_specific_energy = 24 + 0.6 * (compressor_freq - 35) + 16 * fouling_factor + 13 * tds_factor
    target_specific_energy += 0.18 * np.maximum(32 - feed_temp, 0) + rng.normal(0, 2.5, n_rows)
    target_specific_energy = np.clip(target_specific_energy, 20, 80)

    target_total_power = target_specific_energy * condensate_flow
    pump_power = 30 + 3.2 * feed_flow + 0.35 * np.maximum(compressor_freq - 40, 0) ** 1.25
    pump_power += rng.normal(0, 6, n_rows)
    pump_power = np.clip(pump_power, 30, 160)

    compressor_power = target_total_power - pump_power + rng.normal(0, 18, n_rows)
    compressor_power = np.clip(compressor_power, 100, 800)
    total_power = compressor_power + pump_power

    high_risk_signal = (fouling_factor > 0.75) | (compressor_freq > 55) | (feed_tds > 130000)
    random_alarm = rng.random(n_rows) < 0.025
    alarm_flag = (high_risk_signal & (rng.random(n_rows) < 0.22) | random_alarm).astype(int)

    df = pd.DataFrame(
        {
            "timestamp": timestamps,
            "feed_flow": feed_flow,
            "feed_temp": feed_temp,
            "feed_tds": feed_tds,
            "feed_ph": feed_ph,
            "evap_temp": evap_temp,
            "evap_pressure": evap_pressure,
            "compressor_freq": compressor_freq,
            "compressor_power": compressor_power,
            "pump_power": pump_power,
            "condensate_flow": condensate_flow,
            "concentrate_flow": concentrate_flow,
            "total_power": total_power,
            "cip_flag": cip_flag,
            "alarm_flag": alarm_flag,
        }
    )

    numeric_cols = [col for col in EXPECTED_COLUMNS if col != "timestamp"]
    df[numeric_cols] = df[numeric_cols].round(2)
    df["cip_flag"] = df["cip_flag"].astype(int)
    df["alarm_flag"] = df["alarm_flag"].astype(int)
    return df[EXPECTED_COLUMNS]


def save_sample_data(output_path: str | Path, n_rows: int = 720, seed: int = 42) -> Path:
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    df = generate_sample_data(n_rows=n_rows, seed=seed)
    df.to_csv(output_path, index=False, encoding="utf-8-sig")
    return output_path


def main() -> None:
    parser = argparse.ArgumentParser(description="Generate sample MVR demo data.")
    parser.add_argument("--output", default="data/sample_mvr_data.csv")
    parser.add_argument("--rows", type=int, default=720)
    parser.add_argument("--seed", type=int, default=42)
    args = parser.parse_args()
    output_path = save_sample_data(args.output, n_rows=args.rows, seed=args.seed)
    print(f"Sample data saved to {output_path}")


if __name__ == "__main__":
    main()

