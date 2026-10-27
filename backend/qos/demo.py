"""Run a seeded baseline/adaptive comparison and export reproducible evidence."""
from __future__ import annotations

import argparse
import csv
import json
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .experiments import run_comparison
from .engine import SCENARIOS


def _write_csv(path: Path, report: dict[str, Any]) -> None:
    with path.open("x", newline="", encoding="utf-8") as handle:
        writer = csv.writer(handle)
        writer.writerow(["scope", "policy", "tick", "metric", "value"])
        for policy in ("baseline", "adaptive"):
            for metric, value in report[policy].items():
                writer.writerow(["summary", policy, "", metric, value])
            for row in report["timeseries"][policy]:
                tick = row.get("tick", "")
                for metric, value in row.items():
                    if isinstance(value, (int, float)) and not isinstance(value, bool):
                        writer.writerow(["timeseries", policy, tick, metric, value])


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--scenario", choices=SCENARIOS, default="capacity_drop")
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--ticks", type=int, default=40)
    parser.add_argument("--out", type=Path, default=Path("reports"))
    args = parser.parse_args()
    if not 10 <= args.ticks <= 300:
        parser.error("--ticks must be between 10 and 300")

    args.out.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    stem = f"{args.scenario}-seed{args.seed}-ticks{args.ticks}-{stamp}"
    json_path, csv_path = args.out / f"{stem}.json", args.out / f"{stem}.csv"
    report = run_comparison(args.scenario, args.seed, args.ticks)
    report["demo_generated_at_utc"] = datetime.now(timezone.utc).isoformat()
    with json_path.open("x", encoding="utf-8") as handle:
        json.dump(report, handle, indent=2, allow_nan=False)
        handle.write("\n")
    _write_csv(csv_path, report)
    print(f"Scenario: {report['scenario_name']}")
    print(f"Baseline mean latency: {report['baseline']['latency']:.2f} ms")
    print(f"Adaptive mean latency: {report['adaptive']['latency']:.2f} ms")
    print(f"JSON report: {json_path.resolve()}")
    print(f"CSV time series: {csv_path.resolve()}")


if __name__ == "__main__":
    main()
