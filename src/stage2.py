"""Stage 2 orchestrator and review artifacts; Stage 3 is never invoked."""

import csv
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt
import numpy as np

from .evaluate import evaluate_development
from .stage2_data import SCENARIO_FILES, assert_locked_integrity, file_sha256, load_development_trades
from .stage2_metrics import BLOCK_SIZE, GATES, RESAMPLES, setup_decisions
from .walkforward import rolling_oos


def assert_approval(root: Path, config):
    approval = json.loads((root / "reports" / "stage1_approval.json").read_text(encoding="utf-8"))
    if not approval.get("stage2_authorized") or set(approval.get("approved_setups", [])) != {"A", "B_v2"}:
        raise ValueError("Explicit Stage 1 approval for both setups is required")
    for relative, expected in approval["artifact_sha256"].items():
        if file_sha256(root / relative) != expected:
            raise ValueError("Approved artifact changed: " + relative)
    if config["setup_b"]["version"] != "v2":
        raise ValueError("Only approved B_v2 may be evaluated")
    for filename in ("stage1_signals.json", "stage1_label.json"):
        prior = json.loads((root / "reports" / filename).read_text(encoding="utf-8"))
        if prior["status"] != "PASS":
            raise ValueError("Stage 1 automated checks must PASS")
    return approval


def _f(value, digits=3):
    return "NA" if value is None else ("%.*f" % (digits, value))


def _pct(value):
    return "NA" if value is None else "%0.1f%%" % (value * 100)


def _ci(bounds):
    return "[%s, %s]" % (_f(bounds[0]), _f(bounds[1]))


def _cells(cube):
    for setup in ("A", "B"):
        for scenario in SCENARIO_FILES:
            for view in ("include_truncated", "exclude_truncated"):
                for direction in ("combined", "long", "short"):
                    yield setup, scenario, view, direction, cube[setup][scenario][view][direction]


def _metric_tables(lines, cube):
    lines += ["", "Win rate = realized R > 0 / all trades (positive TIMEOUTs count as wins).",
              "Average win/loss use positive/negative R, including TIMEOUTs. MAE is signed negative R.",
              "", "| Setup | Cost | View | Direction | n | Truncated | Win rate | Wilson 95% | Avg win R | Avg loss R | E[R] | PF | Max DD R | Losing streak | Bars | MAE R | MFE R |",
              "|---|---|---|---|---:|---:|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|"]
    for setup, scenario, view, direction, m in _cells(cube):
        wilson = "[%s, %s]" % tuple(_pct(v) for v in m["win_rate_wilson95"])
        pf = "infinity" if m["profit_factor_infinite"] else _f(m["profit_factor"])
        lines.append("| %s | %s | %s | %s | %d | %d | %s | %s | %s | %s | %s | %s | %s | %d | %s | %s | %s |" % (
            setup if setup == "A" else "B_v2", scenario, view, direction, m["n_trades"], m["n_truncated"],
            _pct(m["win_rate"]), wilson, _f(m["average_win_r"]), _f(m["average_loss_r"]),
            _f(m["expectancy_r"]), pf, _f(m["max_drawdown_r"]), m["longest_losing_streak"],
            _f(m["average_bars_held"]), _f(m["average_mae_r"]), _f(m["average_mfe_r"])))
    lines += ["", "| Setup | Cost | View | Direction | Raw 95% E[R] CI | Adjusted setup CI | Adjusted global CI | Raw p | Setup-adjusted p | Global-adjusted p | TP win rate | Wilson 95% (TP) |",
              "|---|---|---|---|---|---|---|---:|---:|---:|---:|---|"]
    for setup, scenario, view, direction, m in _cells(cube):
        b = m["bootstrap"]
        lines.append("| %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s | %s |" % (
            setup if setup == "A" else "B_v2", scenario, view, direction,
            _ci(b["ci95_raw"]), _ci(b["ci95_adjusted_setup"]), _ci(b["ci95_adjusted_global"]),
            _f(b["p_positive_raw"], 4), _f(b["p_positive_adjusted_setup"], 4),
            _f(b["p_positive_adjusted_global"], 4), _pct(m["barrier_win_rate"]),
            "[%s, %s]" % tuple(_pct(v) for v in m["barrier_win_rate_wilson95"])))


def _calendar_tables(lines, cube):
    for field, name in (("by_year", "year"), ("by_quarter", "quarter")):
        lines += ["", "#### Calendar %s expectancy" % name, "",
                  "| Setup | Cost | View | Direction | Period | n | E[R] | Total R |",
                  "|---|---|---|---|---|---:|---:|---:|"]
        for setup, scenario, view, direction, m in _cells(cube):
            for period, values in m[field].items():
                lines.append("| %s | %s | %s | %s | %s | %d | %s | %s |" % (
                    setup if setup == "A" else "B_v2", scenario, view, direction, period,
                    values["n_trades"], _f(values["expectancy_r"]), _f(values["total_r"])))


def plot_equity(report, directory):
    directory.mkdir(parents=True, exist_ok=True)
    paths = []
    scopes = [("Train", report["evaluation"]["train"]),
              ("Validation", report["evaluation"]["validation"]),
              ("Concatenated complete OOS", report["walk_forward"]["primary_oos"])]
    colors = {"normal": "#155f99", "plus_50_percent": "#c17121", "plus_100_percent": "#b63f52"}
    for setup in ("A", "B"):
        for direction in ("combined", "long", "short"):
            fig, axes = plt.subplots(3, 2, figsize=(14, 10))
            for row, (name, cube) in enumerate(scopes):
                for col, view in enumerate(("include_truncated", "exclude_truncated")):
                    ax = axes[row, col]
                    for scenario in SCENARIO_FILES:
                        points = cube[setup][scenario][view][direction]["equity_curve_r"]
                        if points:
                            times = [datetime.fromisoformat(p["exit_timestamp"]) for p in points]
                            origin = datetime.fromisoformat(report["scope"]["train_start_utc"] if name == "Train"
                                                           else report["scope"]["validation_start_utc"])
                            ax.step([origin] + times, [0.0] + [p["equity_r"] for p in points],
                                    where="post", label=scenario, color=colors[scenario], linewidth=1.1)
                    ax.axhline(0, color="#777777", linewidth=0.6)
                    ax.set_title(name + " | " + view.replace("_", " "), fontsize=10)
                    ax.set_ylabel("Realized equity (R)")
                    ax.grid(alpha=0.18)
                    ax.tick_params(axis="x", labelrotation=20, labelsize=8)
                    if row == 0:
                        ax.legend(fontsize=8)
            label = "A" if setup == "A" else "B_v2"
            fig.suptitle("Setup %s | %s | development only | no capital compounding" % (label, direction))
            fig.tight_layout(rect=(0, 0, 1, 0.96))
            path = directory / ("setup_%s_%s_equity.png" % (label.lower(), direction))
            fig.savefig(str(path), dpi=140)
            plt.close(fig)
            paths.append(str(path.relative_to(directory.parent)))
    return paths


def write_summary(report, reports_dir):
    lines = ["# Stage 2 research results", "", "Run UTC: " + report["run_utc"], "",
             "Stage 1 chart approval: 2026-10-14. Development only; Stage 3 has not been run.", "",
             "## Decisions", "", "| Setup | Status | Numerical frozen-gate result | Stage 3 |",
             "|---|---|---|---|"]
    for setup, decision in report["decisions"].items():
        lines.append("| %s | %s | %s | Not authorized / not run |" % (
            "A" if setup == "A" else "B_v2", decision["status"], decision["numeric_frozen_gate_result"]))
    lines += ["", report["conclusion"], "", "B_v2 remains INSUFFICIENT_DATA regardless of descriptive statistics.",
              "", "## Frozen gates and decision detail", "",
              "Decisions are combined per setup. Long and short are descriptive, never selected post hoc.",
              "Both validation and primary OOS must satisfy n >= 300, E[R] >= +0.10, raw 95% CI lower > 0,",
              "PF >= 1.15, >= 70% positive years AND quarters, and E[R] > 0 at +50% costs.",
              "Both truncation views must satisfy those checks for an overall PASS; each view is shown independently.",
              "Adjusted inference is disclosed separately, without replacing the frozen raw-95% gate.", "",
              "| Setup | Scope | View | n | E[R] | Raw CI | PF | Positive years | Positive quarters | +50% E[R] | Gate result | Failed checks |",
              "|---|---|---|---:|---:|---|---:|---:|---:|---:|---|---|"]
    for setup, d in report["decisions"].items():
        for scope, views in d["scopes"].items():
            cube = report["evaluation"]["validation"] if scope == "validation" else report["walk_forward"]["primary_oos"]
            for view, gate in views.items():
                m = cube[setup]["normal"][view]["combined"]
                stress = cube[setup]["plus_50_percent"][view]["combined"]
                lines.append("| %s | %s | %s | %d | %s | %s | %s | %s | %s | %s | %s | %s |" % (
                    "A" if setup == "A" else "B_v2", scope, view, m["n_trades"], _f(m["expectancy_r"]),
                    _ci(m["bootstrap"]["ci95_raw"]), _f(m["profit_factor"]),
                    _pct(m["positive_year_fraction"]), _pct(m["positive_quarter_fraction"]),
                    _f(stress["expectancy_r"]), gate["status"], ", ".join(gate["failed_gates"])))
    lines += ["", "## Data isolation and provenance", "",
              "- Active dataset: `data/XAUUSD_M15_UTC_RECONSTRUCTED_2023.csv` (159,471 rows).",
              "- Train: 2020-01-01 23:00 through 2024-01-22 10:00 UTC.",
              "- Validation: 2024-01-22 10:15 through 2025-05-28 09:15 UTC.",
              "- No 2009-2019 data loaded or changed. Setup parameters, costs, gates, splits and n_trials are unchanged.",
              "- Evaluator consumes only the three approved Stage 1 development trade CSVs.",
              "- A separate streaming integrity checker reads locked strings solely for hashing; it exposes no test OHLC",
              "  or trades. Verifying SHA-256 necessarily reads bytes, but no test result or feature is computed.",
              "- Test hash before/after: `" + report["integrity_before"]["test_partition_sha256"] + "` (both PASS).",
              "- Frozen dataset hash before/after: `" + report["integrity_before"]["dataset_sha256"] + "` (both PASS).",
              "- Tests reject test timestamps/indexes before reading P&L and reject B_v1. Code and input hashes are in JSON.",
              "- Train trades with exits in validation are excluded from train: `" +
              json.dumps(report["evaluation"]["split_crossing_trades_excluded_from_train"], sort_keys=True) + "`.",
              "", "## Statistical conventions", "",
              "- Returns are net R from the frozen BID/ASK spread/slippage labeler; costs are already included.",
              "- Primary win rate means R > 0, including profitable TIMEOUTs. TP-hit win rate (WIN label / all trades)",
              "  is also reported with its own Wilson interval, avoiding ambiguity about timeout classification.",
              "- Average win/loss are means of strictly positive/negative R. Zero returns break losing streaks.",
              "- Wilson two-sided 95% score interval; this binomial interval does not model serial dependence.",
              "- Expectancy CI: 10,000 circular moving-block resamples, 10 trades per block, chronological entry order;",
              "  final block truncated to exactly n observations. Base seed 20261010; SHA-256-derived cell seeds in JSON.",
              "- Raw percentile bounds: 2.5/97.5%. Bonferroni bounds: alpha = 0.05 / m, m=1 for A, 2 for B and 3 globally.",
              "  Adjustments apply to the declared hypothesis trials, not every descriptive slice. No B_v1 edge computed.",
              "- p values: one-sided null-centred block bootstrap for E[R] <= 0; finite-resample correction (hits+1)/10001;",
              "  adjusted p = min(1, m * raw p). They are approximate, not exact significance probabilities.",
              "- Equity: 1R per trade, no compounding, grouped by exit timestamp; simultaneous exits are added together.",
              "  DD is realized close-to-close peak-to-trough from initial 0R, not intratrade portfolio drawdown.",
              "- Losing streak: negative realized trade R in exit order; tied exits use entry time then direction.",
              "- Calendar groups use signal UTC timestamps. Populated years and quarters, including partial endpoints,",
              "  each have equal weight for the >=70% gates. Empty periods are absent; n per period is disclosed.",
              "- PF with gains and no losses is infinite (JSON null plus an explicit flag); undefined values are NA/null.",
              "- Each cost scenario uses its own Stage 1 trade set; higher costs can change eligibility and overlap timing.",
              "", "Wilson method reference: [NIST](https://www.itl.nist.gov/div898/handbook/prc/section2/prc241.htm).",
              "Bonferroni reference: [NIST](https://itl.nist.gov/div898/handbook/prc/section4/prc473.htm).", "",
              "## Equity curves", ""]
    for path in report["equity_charts"]:
        lines += ["![" + Path(path).stem + "](" + path.replace("\\", "/") + ")", ""]
    for name, cube in (("Train (descriptive)", report["evaluation"]["train"]),
                       ("Validation", report["evaluation"]["validation"]),
                       ("Concatenated complete walk-forward OOS", report["walk_forward"]["primary_oos"])):
        lines += ["", "## " + name]
        _metric_tables(lines, cube)
        _calendar_tables(lines, cube)
    lines += ["", "## Rolling 3-month OOS stability", "",
              "Windows are [start,end), anchored at validation start, stride 3 months, no fitting/tuning.",
              "Only complete windows enter primary concatenated OOS. The final partial window is descriptive.",
              "Validation and OOS overlap in data; they are not independent replications. Train is not OOS.",
              "Sample size gate applies to concatenated OOS, not to each individual 3-month block.",
              "Trades crossing a window end are omitted, never truncated/relabelled to manufacture a result."]
    for w in report["walk_forward"]["windows"]:
        lines += ["", "### " + w["id"], "",
                  "%s to %s (exclusive); complete=%s; primary OOS=%s." % (
                      w["start_utc"], w["end_exclusive_utc"], w["complete_3_months"], w["included_in_primary_oos"]),
                  "Boundary-crossing trades omitted: `" + json.dumps(w["boundary_crossing_trades_excluded"], sort_keys=True) + "`."]
        _metric_tables(lines, w["metrics"])
        # Calendar detail for each window remains in the complete machine-readable JSON/CSV.
    lines += ["", "## B_v2 sweep-to-confirmation audit (counts only)", "",
              "All 1,222 kept signals: weekend 6 (long 2, short 4), holiday 0, unexpected 0, union 6.",
              "Daily breaks separately: 13. Signal code unchanged. These are known implementation-context limitations.",
              "", "## Known weaknesses", ""]
    lines += ["- " + weakness for weakness in report["known_weaknesses"]]
    lines += ["", "## Review boundary", "",
              "No parameter changes are proposed or made. No setup advances to Stage 3 in this cycle.",
              "Stop for human review; final test, probability tables, Pine alerts and backend remain unrun.",
              "Git push blocked: cookie filename occurs in history (510cfd0, 5749cd4). No credential contents disclosed.",
              "", "Complete metrics, per-window calendar breakdowns, per-trade equity points and source hashes:",
              "`stage2_results.json`, `stage2_metrics.csv`, `stage2_calendar.csv` and `stage2_equity.csv`.", ""]
    (reports_dir / "STAGE2_SUMMARY.md").write_text("\n".join(lines), encoding="utf-8")


def write_flat_csvs(report, reports_dir):
    metrics, periods, equity = [], [], []
    scopes = [("train", report["evaluation"]["train"]), ("validation", report["evaluation"]["validation"]),
              ("walk_forward_oos", report["walk_forward"]["primary_oos"])]
    scopes += [(w["id"], w["metrics"]) for w in report["walk_forward"]["windows"]]
    for scope, cube in scopes:
        for setup, scenario, view, direction, m in _cells(cube):
            base = {"scope": scope, "setup": "A" if setup == "A" else "B_v2",
                    "cost_scenario": scenario, "truncation_view": view, "direction": direction}
            flat = dict(base)
            for key, value in m.items():
                if not isinstance(value, (dict, list)):
                    flat[key] = value
            for prefix, ci in (("win_rate_wilson95", m["win_rate_wilson95"]),
                               ("barrier_win_rate_wilson95", m["barrier_win_rate_wilson95"])):
                flat[prefix + "_lower"], flat[prefix + "_upper"] = ci
            for key, value in m["bootstrap"].items():
                if isinstance(value, list):
                    flat[key + "_lower"], flat[key + "_upper"] = value
                else:
                    flat["bootstrap_" + key] = value
            metrics.append(flat)
            for field in ("by_year", "by_quarter"):
                for period, values in m[field].items():
                    periods.append(dict(base, period_type=field, period=period, **values))
            for point in m["equity_curve_r"]:
                equity.append(dict(base, **point))
    for filename, rows in (("stage2_metrics.csv", metrics), ("stage2_calendar.csv", periods), ("stage2_equity.csv", equity)):
        fields = sorted({key for row in rows for key in row})
        with (reports_dir / filename).open("w", encoding="utf-8", newline="") as handle:
            writer = csv.DictWriter(handle, fieldnames=fields)
            writer.writeheader()
            writer.writerows(rows)


def run_stage2(root: Path):
    config = json.loads((root / "config" / "research.json").read_text(encoding="utf-8"))
    approval = assert_approval(root, config)
    reports_dir = root / "reports"
    data_path = root / config["data_source"]["normalized_file"]
    before = assert_locked_integrity(data_path, config)
    bundle = load_development_trades(reports_dir, config)
    evaluation = evaluate_development(bundle, config)
    wf = rolling_oos(bundle, config)
    decisions = setup_decisions(evaluation["validation"], wf["primary_oos"])
    after = assert_locked_integrity(data_path, config)
    if before != after:
        raise ValueError("Integrity changed during Stage 2")
    assert_approval(root, config)
    report = {
        "schema_version": 1, "stage": 2, "status": "COMPLETED", "run_utc": datetime.now(timezone.utc).isoformat(),
        "scope": {"train_start_utc": config["split"]["train"]["start_utc"],
                  "train_end_utc": config["split"]["train"]["end_utc"],
                  "validation_start_utc": config["split"]["validation"]["start_utc"],
                  "development_end_utc": config["split"]["validation"]["end_utc"],
                  "test_results_evaluated": False, "expanded_2009_2019_data_loaded": False},
        "integrity_before": before, "integrity_after": after,
        "stage1_approval": approval, "frozen_gates": GATES, "n_trials": config["trials"],
        "statistics": {"bootstrap_resamples": RESAMPLES, "block_trades": BLOCK_SIZE,
                       "base_seed": config["random_seed"], "multiplicity": "Bonferroni, setup and global",
                       "win_rate_definition": "R > 0 / all trades; TP WIN rate also reported",
                       "decision_scope": "combined setup; validation AND concatenated complete OOS; both truncation views",
                       "calendar_definition": "signal UTC; populated years AND quarters including partial endpoints",
                       "equity_definition": "realized 1R/trade, simultaneous exits aggregated; no compounding"},
        "environment": {"python": sys.version.split()[0], "numpy": np.__version__,
                        "matplotlib": matplotlib.__version__,
                        "requirements_sha256": file_sha256(root / "requirements.txt")},
        "input_sha256": approval["artifact_sha256"],
        "code_sha256": {str(p.relative_to(root)).replace("\\", "/"): file_sha256(p)
                        for p in [root / "run.py"] + sorted((root / "src").glob("*.py"))},
        "evaluation": evaluation, "walk_forward": wf, "decisions": decisions,
        "conclusion": "Setup A " + decisions["A"]["status"] +
                      ". B_v2 is INSUFFICIENT_DATA (248 validation trades vs required 300). "
                      "For Setup A, normal-cost validation is approximately break-even, its confidence "
                      "interval includes negative returns, and +50% costs produce negative expectancy. "
                      "The complete walk-forward blocks also have negative combined expectancy. "
                      "B_v2's descriptive validation results are negative in both truncation views. "
                      "The development results do not justify advancing either setup to the locked final test.",
        "known_weaknesses": [
            "B_v2 has fewer than 300 validation trades; descriptive estimates cannot qualify it for PASS.",
            "Six B_v2 confirmations span weekend gaps; 13 span daily breaks. The approved signal implementation remains unchanged.",
            "Fixed synthetic ASK spread and fixed slippage do not represent all intraday/news liquidity or broker execution conditions.",
            "M15 OHLC cannot resolve intrabar path. MAE/MFE include the whole exit candle and may extend beyond the executed barrier.",
            "Independent per-direction positions can overlap; combined equity adds 1R outcomes without portfolio exposure limits.",
            "Realized drawdown understates possible intratrade portfolio drawdown; same-time losing-streak ties have a defined but arbitrary order.",
            "Block length 10 is frozen for this run; long-range dependence or overlapping trades can exceed it. Sparse windows have few effective blocks.",
            "Wilson intervals assume Bernoulli independence; they are descriptive when trades are dependent.",
            "Bonferroni uses the declared 1/2/3 trials; it does not protect post-hoc selection across every displayed direction, cost, view or window.",
            "Only two calendar years are represented in validation/OOS, both partial. Positive-year/quarter proportions are imprecise.",
            "OOS stability reuses validation observations and is not a second independent holdout; the last partial window is outside primary OOS gates.",
            "Gap-truncated trades, reconstructed 2023 bars and remaining missing data affect interpretation; both truncation views are supplied.",
        ],
        "stage3_run": False, "push_performed": False,
    }
    report["equity_charts"] = plot_equity(report, reports_dir / "stage2_charts")
    (reports_dir / "stage2_results.json").write_text(
        json.dumps(report, indent=2, sort_keys=True, allow_nan=False), encoding="utf-8")
    write_flat_csvs(report, reports_dir)
    write_summary(report, reports_dir)
    return report
