import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from src.data_quality import DataQualityError, validate_m15_csv, write_quality_outputs
from src.pipeline import load_config, test_no_lookahead


ROOT = Path(__file__).resolve().parent
CONFIG_PATH = ROOT / "config" / "research.json"
REPORTS_DIR = ROOT / "reports"


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="XAUUSD staged research pipeline")
    parser.add_argument(
        "--stage",
        required=True,
        choices=("data", "signals", "label", "evaluate", "wf", "final_test", "table"),
    )
    parser.add_argument("--data", type=Path, help="Path to M15 XAUUSD OHLC CSV")
    parser.add_argument(
        "--assume-utc",
        action="store_true",
        help="Explicitly localize naive timestamps to UTC and log the correction",
    )
    parser.add_argument(
        "--override-final-test",
        action="store_true",
        help="Reserved for Stage 3; every use will be logged",
    )
    return parser


def run_stage_data(data_path: Optional[Path], assume_utc: bool) -> int:
    if data_path is None:
        print(
            json.dumps(
                {
                    "stage": 0,
                    "status": "BLOCKED",
                    "reason": "No data path supplied. Use --data <M15 CSV path>.",
                    "synthetic_data_is_never_used_for_research_results": True,
                },
                indent=2,
            )
        )
        return 2
    config = load_config(CONFIG_PATH)
    try:
        bars, quality = validate_m15_csv(
            data_path,
            assume_utc=assume_utc,
            minimum_years=float(config["minimum_history_years"]),
        )
    except DataQualityError as exc:
        print(json.dumps({"stage": 0, "status": "FAIL", "error": str(exc)}, indent=2))
        return 1
    write_quality_outputs(quality, REPORTS_DIR)
    stage_report = {
        "stage": 0,
        "run_utc": datetime.now(timezone.utc).isoformat(),
        "data_quality": quality.to_dict(),
        "test_no_lookahead": None,
    }
    if not quality.fatal_errors:
        try:
            stage_report["test_no_lookahead"] = test_no_lookahead(bars, config, 50)
        except AssertionError as exc:
            stage_report["test_no_lookahead"] = {"status": "FAIL", "error": str(exc)}
    stage_report["status"] = (
        "PASS"
        if not quality.fatal_errors
        and stage_report["test_no_lookahead"]
        and stage_report["test_no_lookahead"]["status"] == "PASS"
        else "FAIL"
    )
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    (REPORTS_DIR / "stage0_run.json").write_text(
        json.dumps(stage_report, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(stage_report, indent=2, sort_keys=True))
    return 0 if stage_report["status"] == "PASS" else 1


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.stage == "data":
        return run_stage_data(args.data, args.assume_utc)
    print(
        json.dumps(
            {
                "stage": args.stage,
                "status": "BLOCKED",
                "reason": "The prior stage must be completed and explicitly approved first.",
            },
            indent=2,
        )
    )
    return 2


if __name__ == "__main__":
    sys.exit(main())

