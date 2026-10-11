import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import List, Optional

from src.data_quality import DataQualityError, validate_m15_csv, write_quality_outputs
from src.labeling import label_signals
from src.pipeline import (
    generate_all_signals_with_audit,
    load_config,
    test_no_lookahead,
)
from src.split_lock import compute_split_lock
from src.stage1 import write_label_outputs, write_signal_outputs


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


def _configured_data_path(config: dict, supplied: Optional[Path]) -> Path:
    if supplied is not None:
        return supplied
    return ROOT / config["data_source"]["normalized_file"]


def _development_bars(data_path: Path, config: dict):
    bars, quality = validate_m15_csv(
        data_path,
        assume_utc=False,
        minimum_years=float(config["minimum_history_years"]),
    )
    if quality.fatal_errors:
        raise DataQualityError("Stage 1 data validation has fatal errors")
    split = config["split"]
    computed = compute_split_lock(
        data_path,
        float(split["train_fraction"]),
        float(split["validation_fraction"]),
    )
    expected_test = split["test"]
    if computed["test"]["partition_sha256"] != expected_test["partition_sha256"]:
        raise DataQualityError("Locked test partition hash does not match config")
    for partition in ("train", "validation", "test"):
        for key in ("start_utc", "end_utc", "rows"):
            if computed[partition][key] != split[partition][key]:
                raise DataQualityError(
                    "Configured split mismatch at %s.%s" % (partition, key)
                )
    development_rows = int(split["train"]["rows"]) + int(split["validation"]["rows"])
    development = bars[:development_rows]
    if development[-1].timestamp.isoformat() != split["validation"]["end_utc"]:
        raise DataQualityError("Development cutoff does not match validation end")
    return development, quality, computed


def run_stage_signals(data_path: Optional[Path]) -> int:
    config = load_config(CONFIG_PATH)
    path = _configured_data_path(config, data_path)
    try:
        bars, _, _ = _development_bars(path, config)
        signals, audit = generate_all_signals_with_audit(bars, config)
        no_lookahead = test_no_lookahead(bars, config, 50)
        report = write_signal_outputs(
            REPORTS_DIR,
            bars,
            signals,
            audit,
            no_lookahead,
            path,
            config["split"]["validation"]["end_utc"],
        )
    except (DataQualityError, AssertionError, OSError, ValueError) as exc:
        print(json.dumps({"stage": 1, "part": "signals", "status": "FAIL", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({
        "stage": 1,
        "part": "signals",
        "status": report["status"],
        "signal_audit": report["signal_audit"],
        "test_no_lookahead": {
            "status": no_lookahead["status"],
            "samples": no_lookahead["samples"],
            "signal_function": no_lookahead["signal_function"],
        },
    }, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


def run_stage_label(data_path: Optional[Path]) -> int:
    config = load_config(CONFIG_PATH)
    path = _configured_data_path(config, data_path)
    signals_report_path = REPORTS_DIR / "stage1_signals.json"
    if not signals_report_path.exists():
        print(json.dumps({
            "stage": 1,
            "part": "label",
            "status": "BLOCKED",
            "reason": "Run --stage signals successfully first.",
        }, indent=2))
        return 2
    signals_report = json.loads(signals_report_path.read_text(encoding="utf-8"))
    if signals_report.get("status") != "PASS":
        print(json.dumps({
            "stage": 1,
            "part": "label",
            "status": "BLOCKED",
            "reason": "Stage 1 signals/no-lookahead did not pass.",
        }, indent=2))
        return 2
    try:
        bars, _, _ = _development_bars(path, config)
        signals, _ = generate_all_signals_with_audit(bars, config)
        trades, skipped, audit = label_signals(bars, signals, config)
        report = write_label_outputs(
            REPORTS_DIR,
            bars,
            signals,
            trades,
            skipped,
            audit,
            int(config["random_seed"]),
            path,
            config["split"]["validation"]["end_utc"],
        )
    except (DataQualityError, OSError, RuntimeError, ValueError) as exc:
        print(json.dumps({"stage": 1, "part": "label", "status": "FAIL", "error": str(exc)}, indent=2))
        return 1
    print(json.dumps({
        "stage": 1,
        "part": "label",
        "status": report["status"],
        "labeling_audit": report["labeling_audit"],
        "chart_examples_per_setup": 20,
    }, indent=2, sort_keys=True))
    return 0


def main(argv: Optional[List[str]] = None) -> int:
    args = _parser().parse_args(argv)
    if args.stage == "data":
        return run_stage_data(args.data, args.assume_utc)
    if args.stage == "signals":
        return run_stage_signals(args.data)
    if args.stage == "label":
        return run_stage_label(args.data)
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

