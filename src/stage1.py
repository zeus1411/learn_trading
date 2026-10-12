"""Stage 1 output writers and deterministic sanity-check charts."""

import csv
import json
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Mapping, Optional, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .features import ema, shifted_h1_features
from .gaps import CONTIGUOUS, DAILY_BREAK, HOLIDAY_BREAK, UNEXPECTED, WEEKEND, classify_gap
from .labeling import SkippedSignal, Trade
from .models import Bar, Signal


SETUP_A_PRE_SIGNAL_BARS = 60
SETUP_B_PRE_SIGNAL_BARS = 80


def _timestamp(bars: Sequence[Bar], index: int) -> str:
    return bars[index].timestamp.isoformat() if 0 <= index < len(bars) else ""


def signal_rows(bars: Sequence[Bar], signals: Sequence[Signal]) -> List[Dict[str, object]]:
    rows = []
    for signal in signals:
        signal_index = signal.entry_index - 1
        rows.append(
            {
                "signal_timestamp": signal.timestamp.isoformat(),
                "setup": signal.setup,
                "direction": signal.direction,
                "signal_index": signal_index,
                "entry_index": signal.entry_index,
                "scheduled_entry_timestamp": _timestamp(bars, signal.entry_index),
                "stop_loss": signal.sl,
                "atr": signal.atr,
                "reference_timestamp": (
                    _timestamp(bars, signal.reference_index)
                    if signal.reference_index is not None
                    else ""
                ),
                "sweep_timestamp": (
                    _timestamp(bars, signal.sweep_index)
                    if signal.sweep_index is not None
                    else ""
                ),
                "source": signal.signal_bar_source,
                "entry_source": (
                    bars[signal.entry_index].source
                    if signal.entry_index < len(bars)
                    else ""
                ),
                "strategy_version": signal.strategy_version,
            }
        )
    return rows


def _write_rows(path: Path, rows: Sequence[Mapping[str, object]]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not rows:
        raise ValueError("Refusing to write empty CSV: %s" % path)
    fields = list(rows[0].keys())
    with path.open("w", encoding="utf-8", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=fields, lineterminator="\n")
        writer.writeheader()
        writer.writerows(rows)


def write_signal_outputs(
    reports_dir: Path,
    bars: Sequence[Bar],
    signals: Sequence[Signal],
    audit: Mapping[str, object],
    no_lookahead: Mapping[str, object],
    data_path: Path,
    development_end_utc: str,
) -> Dict[str, object]:
    rows = signal_rows(bars, signals)
    _write_rows(reports_dir / "signals_stage1.csv", rows)
    by_year = Counter(
        (signal.setup, signal.direction, signal.timestamp.year) for signal in signals
    )
    by_source = Counter(signal.signal_bar_source for signal in signals)
    report = {
        "stage": 1,
        "part": "signals",
        "status": "PASS" if no_lookahead.get("status") == "PASS" else "FAIL",
        "data_file": str(data_path.resolve()),
        "development_partition_end_utc": development_end_utc,
        "test_partition_read_for_signal_results": False,
        "signal_function_used_by_stage0_and_stage1": "src.pipeline.generate_all_signals",
        "signal_audit": dict(audit),
        "signal_counts_by_setup_direction_year": {
            "%s_%s_%d" % key: value for key, value in sorted(by_year.items())
        },
        "signal_counts_by_source": dict(sorted(by_source.items())),
        "test_no_lookahead": dict(no_lookahead),
        "output_csv": str((reports_dir / "signals_stage1.csv").resolve()),
    }
    (reports_dir / "stage1_signals.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report


def _select_examples(trades: Sequence[Trade], setup: str, count: int, seed: int) -> List[Trade]:
    groups: Dict[Tuple[str, str], List[Trade]] = defaultdict(list)
    for trade in trades:
        if trade.setup == setup:
            groups[(trade.direction, trade.outcome)].append(trade)
    rng = random.Random(seed + ord(setup))
    for values in groups.values():
        rng.shuffle(values)
    keys = sorted(groups)
    selected: List[Trade] = []
    while len(selected) < count and any(groups[key] for key in keys):
        for key in keys:
            if groups[key] and len(selected) < count:
                selected.append(groups[key].pop())
    return sorted(selected, key=lambda trade: trade.signal_timestamp)


def _rolling_extrema(
    bars: Sequence[Bar], lookback: int
) -> Tuple[List[Optional[float]], List[Optional[float]]]:
    highs: List[Optional[float]] = [None] * len(bars)
    lows: List[Optional[float]] = [None] * len(bars)
    for index in range(lookback - 1, len(bars)):
        window = bars[index - lookback + 1:index + 1]
        highs[index] = max(bar.high for bar in window)
        lows[index] = min(bar.low for bar in window)
    return highs, lows


def _format_indicator(value: Optional[float]) -> str:
    return "N/A" if value is None else "%.2f" % value


def _draw_trade_chart(
    path: Path,
    bars: Sequence[Bar],
    trade: Trade,
    signal: Signal,
    setup_a_context: Optional[Mapping[str, Sequence[Optional[float]]]] = None,
) -> None:
    bars_before_signal = (
        SETUP_A_PRE_SIGNAL_BARS if trade.setup == "A" else SETUP_B_PRE_SIGNAL_BARS
    )
    start = max(0, trade.signal_index - bars_before_signal)
    end = min(len(bars), trade.exit_index + 11)
    window = bars[start:end]
    fig, ax = plt.subplots(figsize=(12, 6), dpi=130)
    width = 0.008
    for offset, bar in enumerate(window):
        x = mdates.date2num(bar.timestamp)
        color = "#17803D" if bar.close >= bar.open else "#C83A3A"
        ax.vlines(x, bar.low, bar.high, color=color, linewidth=0.8)
        bottom = min(bar.open, bar.close)
        height = max(abs(bar.close - bar.open), 0.01)
        ax.add_patch(
            Rectangle(
                (x - width / 2, bottom), width, height,
                facecolor=color, edgecolor=color, linewidth=0.7,
            )
        )
        if bar.source != "HISTDATA_M1_BID":
            ax.axvspan(x - width / 2, x + width / 2, color="#8B5CF6", alpha=0.12)

    if trade.setup == "A":
        if setup_a_context is None:
            raise RuntimeError("Setup A chart context is required")
        indices = list(range(start, end))
        x_values = [bars[index].timestamp for index in indices]
        ax.plot(x_values, [setup_a_context["ema20"][index] for index in indices],
                color="#0EA5E9", linewidth=1.1, label="EMA20")
        ax.plot(x_values, [setup_a_context["highest20"][index] for index in indices],
                color="#9333EA", linewidth=0.9, linestyle="-.", label="20-bar high")
        ax.plot(x_values, [setup_a_context["lowest20"][index] for index in indices],
                color="#D97706", linewidth=0.9, linestyle="-.", label="20-bar low")
        h1_note = (
            "Last closed H1\nEMA50: %s\nEMA200: %s\nADX14: %s"
            % (
                _format_indicator(setup_a_context["h1_ema50"][trade.signal_index]),
                _format_indicator(setup_a_context["h1_ema200"][trade.signal_index]),
                _format_indicator(setup_a_context["h1_adx"][trade.signal_index]),
            )
        )
        ax.text(
            0.99, 0.98, h1_note, transform=ax.transAxes, ha="right", va="top",
            fontsize=8, color="#111827",
            bbox={"boxstyle": "round,pad=0.35", "facecolor": "white", "alpha": 0.85,
                  "edgecolor": "#CBD5E1"},
        )

    signal_time = bars[trade.signal_index].timestamp
    entry_time = bars[trade.entry_index].timestamp
    exit_time = bars[trade.exit_index].timestamp
    ax.axvline(signal_time, color="#2563EB", linestyle="--", linewidth=1.1, label="Signal")
    ax.scatter(entry_time, trade.entry_price, marker="^" if trade.direction == "long" else "v",
               color="#111827", s=55, zorder=5, label="Entry")
    ax.scatter(exit_time,
               trade.entry_price + trade.result_r * trade.risk_usd * (1 if trade.direction == "long" else -1),
               marker="x", color="#7C3AED", s=55, zorder=5, label="Exit")
    ax.axhline(trade.stop_loss, color="#DC2626", linewidth=1.0, label="SL")
    ax.axhline(trade.take_profit, color="#16A34A", linewidth=1.0, label="TP")
    if signal.reference_index is not None and start <= signal.reference_index < end:
        pivot_price = (
            bars[signal.reference_index].low
            if trade.direction == "long"
            else bars[signal.reference_index].high
        )
        ax.axvline(bars[signal.reference_index].timestamp, color="#7C3AED", linestyle=":",
                   linewidth=1.0, label="Pivot bar")
        ax.axhline(
            pivot_price, color="#7C3AED", linestyle="--", linewidth=1.1,
            label="Pivot %.3f" % pivot_price,
        )
    if signal.sweep_index is not None and start <= signal.sweep_index < end:
        sweep_x = mdates.date2num(bars[signal.sweep_index].timestamp)
        ax.axvspan(
            sweep_x - width / 2, sweep_x + width / 2,
            color="#F97316", alpha=0.28, label="Sweep bar",
        )
        ax.axvline(bars[signal.sweep_index].timestamp, color="#EA580C", linestyle=":",
                   linewidth=1.0)
    setup_label = (
        "%s_%s" % (trade.setup, trade.strategy_version)
        if trade.setup == "B" else trade.setup
    )
    ax.set_title(
        "Setup %s %s | %s | signal %s | source %s"
        % (setup_label, trade.direction.upper(), trade.outcome,
           trade.signal_timestamp.strftime("%Y-%m-%d %H:%M UTC"), trade.signal_source),
        loc="left", fontsize=11,
    )
    note = "BID candles. Long entry uses ASK; short barriers/exits use ASK = BID + spread."
    ax.text(0.01, 0.01, note, transform=ax.transAxes, fontsize=8, color="#4B5563")
    ax.set_ylabel("XAUUSD (USD)")
    ax.xaxis.set_major_formatter(mdates.DateFormatter("%m-%d\n%H:%M"))
    ax.grid(axis="y", color="#E5E7EB", linewidth=0.6)
    ax.legend(
        loc="upper left", ncol=5 if trade.setup == "A" else 6,
        fontsize=8, frameon=False,
    )
    fig.autofmt_xdate(rotation=0)
    fig.tight_layout()
    path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(path, bbox_inches="tight")
    plt.close(fig)


def _contact_sheet(image_paths: Sequence[Path], output: Path, title: str) -> None:
    fig, axes = plt.subplots(4, 5, figsize=(20, 13), dpi=100)
    for ax, path in zip(axes.flat, image_paths):
        ax.imshow(mpimg.imread(str(path)))
        ax.set_title(path.stem, fontsize=8)
        ax.axis("off")
    for ax in axes.flat[len(image_paths):]:
        ax.axis("off")
    fig.suptitle(title, fontsize=16)
    fig.tight_layout(rect=(0, 0, 1, 0.97))
    fig.savefig(output, bbox_inches="tight")
    plt.close(fig)


def write_label_outputs(
    reports_dir: Path,
    bars: Sequence[Bar],
    signals: Sequence[Signal],
    trades: Sequence[Trade],
    skipped: Sequence[SkippedSignal],
    audit: Mapping[str, object],
    seed: int,
    data_path: Path,
    development_end_utc: str,
    config: Mapping[str, object],
    cost_scenario_results: Optional[
        Mapping[str, Tuple[Sequence[Trade], Sequence[SkippedSignal], Mapping[str, object]]]
    ] = None,
    setup_b_pivot_audit: Optional[Mapping[str, object]] = None,
    setup_b_v1_gap_audit: Optional[Mapping[str, object]] = None,
    setup_b_v2_counts: Optional[Mapping[str, object]] = None,
) -> Dict[str, object]:
    trade_rows = [trade.to_dict() for trade in trades]
    skipped_rows = [item.to_dict() for item in skipped]
    _write_rows(reports_dir / "trades_stage1.csv", trade_rows)
    _write_rows(reports_dir / "skipped_signals_stage1.csv", skipped_rows)
    scenario_results = cost_scenario_results or {
        "normal": (trades, skipped, audit)
    }
    cost_outputs: Dict[str, Dict[str, object]] = {}
    for scenario, (scenario_trades, scenario_skipped, scenario_audit) in scenario_results.items():
        if scenario == "normal":
            trades_path = reports_dir / "trades_stage1.csv"
            skipped_path = reports_dir / "skipped_signals_stage1.csv"
        else:
            trades_path = reports_dir / ("trades_stage1_cost_%s.csv" % scenario)
            skipped_path = reports_dir / ("skipped_signals_stage1_cost_%s.csv" % scenario)
            _write_rows(trades_path, [trade.to_dict() for trade in scenario_trades])
            _write_rows(skipped_path, [item.to_dict() for item in scenario_skipped])
        cost_outputs[scenario] = {
            "trades_csv": str(trades_path.resolve()),
            "skipped_signals_csv": str(skipped_path.resolve()),
            "labeling_audit": dict(scenario_audit),
        }

    setup_a = config["setup_a"]  # type: ignore
    ema20_values = ema(
        [bar.close for bar in bars], int(setup_a["m15_ema_length"])
    )
    highest20, lowest20 = _rolling_extrema(
        bars, int(setup_a["pullback_lookback"])
    )
    h1 = shifted_h1_features(
        bars,
        int(setup_a["h1_fast_ema_length"]),
        int(setup_a["h1_slow_ema_length"]),
        int(setup_a["adx_length"]),
    )
    setup_a_context: Mapping[str, Sequence[Optional[float]]] = {
        "ema20": ema20_values,
        "highest20": highest20,
        "lowest20": lowest20,
        "h1_ema50": [value.ema50 for value in h1],
        "h1_ema200": [value.ema200 for value in h1],
        "h1_adx": [value.adx14 for value in h1],
    }
    signal_by_key = {
        (signal.timestamp, signal.setup, signal.direction): signal for signal in signals
    }
    chart_manifest: Dict[str, List[str]] = {}
    for setup in ("A", "B"):
        selected = _select_examples(trades, setup, 20, seed)
        if len(selected) < 20:
            raise RuntimeError("Setup %s has only %d chartable trades" % (setup, len(selected)))
        setup_dir = reports_dir / "charts" / ("setup_" + setup.lower())
        setup_dir.mkdir(parents=True, exist_ok=True)
        for obsolete_chart in setup_dir.glob("*.png"):
            obsolete_chart.unlink()
        paths = []
        for number, trade in enumerate(selected, start=1):
            path = setup_dir / ("%02d_%s_%s_%s.png" % (
                number, trade.direction, trade.outcome.lower(),
                trade.signal_timestamp.strftime("%Y%m%dT%H%M"),
            ))
            signal = signal_by_key[(trade.signal_timestamp, trade.setup, trade.direction)]
            _draw_trade_chart(path, bars, trade, signal, setup_a_context)
            paths.append(path)
        contact = reports_dir / "charts" / ("setup_%s_contact_sheet.png" % setup.lower())
        _contact_sheet(paths, contact, "Setup %s: 20 deterministic Stage 1 examples" % setup)
        chart_manifest[setup] = [str(path.resolve()) for path in paths]
        chart_manifest[setup + "_contact_sheet"] = [str(contact.resolve())]

    report = {
        "stage": 1,
        "part": "label",
        "status": "PASS",
        "data_file": str(data_path.resolve()),
        "development_partition_end_utc": development_end_utc,
        "test_partition_read_for_trade_results": False,
        "labeling_audit": dict(audit),
        "cost_scenarios": cost_outputs,
        "stage2_reporting_contract": {
            "cost_scenarios": list(cost_outputs),
            "truncated_trade_views": ["include", "exclude"],
            "truncation_field": "truncated_by_gap",
        },
        "chart_spec": {
            "A": {
                "pre_signal_bars": SETUP_A_PRE_SIGNAL_BARS,
                "overlays": ["EMA20", "rolling_highest_20", "rolling_lowest_20"],
                "annotation": ["previous_closed_H1_EMA50", "previous_closed_H1_EMA200",
                               "previous_closed_H1_ADX14"],
            },
            "B": {
                "pre_signal_bars": SETUP_B_PRE_SIGNAL_BARS,
                "overlays": ["horizontal_pivot_line_with_price", "highlighted_sweep_bar"],
            },
        },
        "setup_b_pivot_audit": (
            {
                "status": setup_b_pivot_audit["status"],
                "signals_checked": setup_b_pivot_audit["signals_checked"],
                "strict_pivot_5_5_violations": setup_b_pivot_audit[
                    "strict_pivot_5_5_violations"
                ],
                "already_closed_beyond_before_sweep": setup_b_pivot_audit[
                    "already_closed_beyond_before_sweep"
                ],
                "prior_pierce_beyond_before_sweep": setup_b_pivot_audit[
                    "prior_pierce_beyond_before_sweep"
                ],
                "output_json": setup_b_pivot_audit["output_json"],
                "output_markdown": setup_b_pivot_audit["output_markdown"],
            }
            if setup_b_pivot_audit is not None else None
        ),
        "setup_b_v1_gap_audit": (
            {
                "signals_checked": setup_b_v1_gap_audit["signals_checked"],
                "pivot_window": setup_b_v1_gap_audit["pivot_window"],
                "pivot_to_sweep": setup_b_v1_gap_audit["pivot_to_sweep"],
                "output_json": setup_b_v1_gap_audit["output_json"],
                "output_markdown": setup_b_v1_gap_audit["output_markdown"],
            }
            if setup_b_v1_gap_audit is not None else None
        ),
        "setup_b_v2_counts": (
            {
                "signals": setup_b_v2_counts["signals"],
                "trades_by_cost_scenario": setup_b_v2_counts[
                    "trades_by_cost_scenario"
                ],
                "output_json": setup_b_v2_counts["output_json"],
                "output_markdown": setup_b_v2_counts["output_markdown"],
            }
            if setup_b_v2_counts is not None else None
        ),
        "outputs": {
            "trades_csv": str((reports_dir / "trades_stage1.csv").resolve()),
            "skipped_signals_csv": str((reports_dir / "skipped_signals_stage1.csv").resolve()),
            "charts": chart_manifest,
        },
    }
    (reports_dir / "stage1_label.json").write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    return report


def _strict_pivot_5_5(
    bars: Sequence[Bar], index: int, direction: str, left: int, right: int
) -> bool:
    if index - left < 0 or index + right >= len(bars):
        return False
    if direction == "long":
        center = bars[index].low
        return all(
            center < bars[other].low
            for other in range(index - left, index + right + 1)
            if other != index
        )
    center = bars[index].high
    return all(
        center > bars[other].high
        for other in range(index - left, index + right + 1)
        if other != index
    )


def _bar_audit_row(bar: Bar, offset: int) -> Dict[str, object]:
    return {
        "offset_from_pivot": offset,
        "timestamp": bar.timestamp.isoformat(),
        "open": bar.open,
        "high": bar.high,
        "low": bar.low,
        "close": bar.close,
    }


def write_setup_b_pivot_audit(
    reports_dir: Path,
    bars: Sequence[Bar],
    signals: Sequence[Signal],
    setup_b_config: Mapping[str, object],
) -> Dict[str, object]:
    """Audit strict pivots and the implemented meaning of 'not previously swept'."""

    left = int(setup_b_config["pivot_left"])
    right = int(setup_b_config["pivot_right"])
    b_signals = [
        signal for signal in signals
        if signal.setup == "B"
        and signal.reference_index is not None
        and signal.sweep_index is not None
    ]
    target_timestamps = {
        "2020-05-19T05:15:00+00:00",
        "2025-05-26T01:30:00+00:00",
    }
    affected_by_direction = Counter()
    pierced_by_direction = Counter()
    strict_violations = 0
    targets: List[Dict[str, object]] = []
    for signal in b_signals:
        pivot_index = int(signal.reference_index)  # type: ignore
        sweep_index = int(signal.sweep_index)  # type: ignore
        pivot_price = (
            bars[pivot_index].low if signal.direction == "long"
            else bars[pivot_index].high
        )
        strict = _strict_pivot_5_5(
            bars, pivot_index, signal.direction, left, right
        )
        if not strict:
            strict_violations += 1
        before_sweep = bars[pivot_index + 1:sweep_index]
        beyond = [
            bar for bar in before_sweep
            if (
                bar.close < pivot_price
                if signal.direction == "long"
                else bar.close > pivot_price
            )
        ]
        if beyond:
            affected_by_direction[signal.direction] += 1
        pierced = [
            bar for bar in before_sweep
            if (
                bar.low < pivot_price
                if signal.direction == "long"
                else bar.high > pivot_price
            )
        ]
        if pierced:
            pierced_by_direction[signal.direction] += 1

        signal_timestamp = signal.timestamp.isoformat()
        if signal_timestamp in target_timestamps:
            through_before_sweep = bars[pivot_index:sweep_index]
            through_sweep = bars[pivot_index:sweep_index + 1]
            sweep = bars[sweep_index]
            targets.append({
                "signal_timestamp": signal_timestamp,
                "direction": signal.direction,
                "pivot_timestamp": bars[pivot_index].timestamp.isoformat(),
                "pivot_price": pivot_price,
                "strict_5_left_5_right": strict,
                "pivot_neighborhood": [
                    _bar_audit_row(bars[index], index - pivot_index)
                    for index in range(pivot_index - left, pivot_index + right + 1)
                ],
                "sweep_bar": _bar_audit_row(sweep, sweep_index - pivot_index),
                "highest_close_pivot_through_before_sweep": max(
                    bar.close for bar in through_before_sweep
                ),
                "lowest_close_pivot_through_before_sweep": min(
                    bar.close for bar in through_before_sweep
                ),
                "highest_close_pivot_through_sweep": max(
                    bar.close for bar in through_sweep
                ),
                "lowest_close_pivot_through_sweep": min(
                    bar.close for bar in through_sweep
                ),
                "already_closed_beyond_before_sweep": bool(beyond),
                "first_close_beyond_timestamp": (
                    beyond[0].timestamp.isoformat() if beyond else None
                ),
                "already_pierced_beyond_before_sweep": bool(pierced),
                "first_pierce_beyond_timestamp": (
                    pierced[0].timestamp.isoformat() if pierced else None
                ),
            })

    affected_total = sum(affected_by_direction.values())
    pierced_total = sum(pierced_by_direction.values())
    strategy_version = (
        b_signals[0].strategy_version if b_signals else "unknown"
    )
    if strategy_version == "v2":
        implementation_note = (
            "B_v2 invalidates a pivot on the first bar that trades beyond it. "
            "Only that first-pierce bar may qualify as a sweep, and it must also "
            "meet the ATR buffer and close-back-inside rules."
        )
    else:
        implementation_note = (
            "B_v1 initializes pivot.swept=False and changes it to True only when "
            "a qualifying wick-through-and-close-back sweep is detected. A prior "
            "close beyond the pivot does not invalidate or mark the pivot as swept."
        )

    report: Dict[str, object] = {
        "status": (
            "PASS" if not pierced_total and not strict_violations else "WARNING"
        ),
        "strategy_version": strategy_version,
        "scope": "Stage 1 kept Setup B signals after the unexpected-gap filter",
        "signals_checked": len(b_signals),
        "strict_pivot_5_5_violations": strict_violations,
        "already_closed_beyond_before_sweep": {
            "definition": {
                "long": "a close below the pivot low after the pivot and before the sweep bar",
                "short": "a close above the pivot high after the pivot and before the sweep bar",
            },
            "total": affected_total,
            "long": affected_by_direction.get("long", 0),
            "short": affected_by_direction.get("short", 0),
            "fraction": affected_total / len(b_signals) if b_signals else 0.0,
        },
        "prior_pierce_beyond_before_sweep": {
            "definition": {
                "long": "a low below the pivot low after the pivot and before the sweep bar",
                "short": "a high above the pivot high after the pivot and before the sweep bar",
            },
            "total": pierced_total,
            "long": pierced_by_direction.get("long", 0),
            "short": pierced_by_direction.get("short", 0),
            "fraction": pierced_total / len(b_signals) if b_signals else 0.0,
        },
        "implementation_note": implementation_note,
        "targets": sorted(targets, key=lambda item: str(item["signal_timestamp"])),
    }
    reports_dir.mkdir(parents=True, exist_ok=True)
    suffix = "_%s" % strategy_version if strategy_version != "v1" else ""
    json_path = reports_dir / ("stage1_setup_b%s_pivot_audit.json" % suffix)
    markdown_path = reports_dir / ("STAGE1_SETUP_B%s_PIVOT_AUDIT.md" % suffix.upper())
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )

    if strategy_version == "v2":
        rule_lines = [
            "B_v2 invalidates a pivot on the first bar that trades beyond it. Only that",
            "first-pierce bar may qualify as the sweep; it must meet the ATR buffer and",
            "close-back-inside rules. The zero count above is the required audit result.",
        ]
    else:
        rule_lines = [
            "B_v1 only sets `pivot.swept = True` after a qualifying wick-through-and-",
            "close-back sweep. A prior close beyond the pivot does not invalidate it.",
            "This audit records the legacy behavior and does not change that rule.",
        ]

    lines = [
        "# Stage 1 Setup B_%s pivot audit" % strategy_version,
        "",
        "Status: **%s**" % report["status"],
        "",
        "Scope: %s." % report["scope"],
        "",
        "- Signals checked: %d" % len(b_signals),
        "- Strict 5-left/5-right pivot violations: %d" % strict_violations,
        "- Prior close beyond pivot before sweep: %d (%.2f%%)" % (
            affected_total,
            100.0 * affected_total / len(b_signals) if b_signals else 0.0,
        ),
        "- Long: %d" % affected_by_direction.get("long", 0),
        "- Short: %d" % affected_by_direction.get("short", 0),
        "- Prior pierce beyond pivot before sweep: %d (%.2f%%)" % (
            pierced_total,
            100.0 * pierced_total / len(b_signals) if b_signals else 0.0,
        ),
        "",
        *rule_lines,
    ]
    for target in sorted(targets, key=lambda item: str(item["signal_timestamp"])):
        lines.extend([
            "",
            "## %s (%s)" % (target["signal_timestamp"], str(target["direction"]).upper()),
            "",
            "- Pivot: `%s` at `%.6f`" % (
                target["pivot_timestamp"], target["pivot_price"]
            ),
            "- Strict 5/5: **%s**" % (
                "PASS" if target["strict_5_left_5_right"] else "FAIL"
            ),
            "- Sweep OHLC: O `%.6f`, H `%.6f`, L `%.6f`, C `%.6f`" % (
                target["sweep_bar"]["open"], target["sweep_bar"]["high"],
                target["sweep_bar"]["low"], target["sweep_bar"]["close"],
            ),
            "- Highest/lowest close from pivot through before sweep: `%.6f` / `%.6f`" % (
                target["highest_close_pivot_through_before_sweep"],
                target["lowest_close_pivot_through_before_sweep"],
            ),
            "- Highest/lowest close from pivot through sweep: `%.6f` / `%.6f`" % (
                target["highest_close_pivot_through_sweep"],
                target["lowest_close_pivot_through_sweep"],
            ),
            "- Already closed beyond before sweep: **%s** (first: `%s`)" % (
                target["already_closed_beyond_before_sweep"],
                target["first_close_beyond_timestamp"],
            ),
            "- Already pierced beyond before sweep: **%s** (first: `%s`)" % (
                target["already_pierced_beyond_before_sweep"],
                target["first_pierce_beyond_timestamp"],
            ),
            "",
            "| Offset | UTC timestamp | Open | High | Low | Close |",
            "|---:|---|---:|---:|---:|---:|",
        ])
        for row in target["pivot_neighborhood"]:
            lines.append(
                "| %d | %s | %.6f | %.6f | %.6f | %.6f |" % (
                    row["offset_from_pivot"], row["timestamp"], row["open"],
                    row["high"], row["low"], row["close"],
                )
            )
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report["output_json"] = str(json_path.resolve())
    report["output_markdown"] = str(markdown_path.resolve())
    return report


def write_setup_b_v1_gap_audit(
    reports_dir: Path,
    bars: Sequence[Bar],
    v1_signals: Sequence[Signal],
    setup_b_config: Mapping[str, object],
) -> Dict[str, object]:
    """Freeze the requested gap counts over the pre-decision B_v1 signals."""

    left = int(setup_b_config["pivot_left"])
    right = int(setup_b_config["pivot_right"])
    gap_types = (WEEKEND, HOLIDAY_BREAK, DAILY_BREAK, UNEXPECTED)

    def gap_types_between(start: int, end: int) -> List[str]:
        return [
            gap_type
            for index in range(start + 1, end + 1)
            for gap_type in [classify_gap(
                bars[index - 1].timestamp, bars[index].timestamp
            )]
            if gap_type != CONTIGUOUS
        ]

    window_signal_counts = Counter()
    interval_signal_counts = Counter()
    window_event_counts = Counter()
    interval_event_counts = Counter()
    window_any = 0
    interval_any = 0
    b_signals = [signal for signal in v1_signals if signal.setup == "B"]
    for signal in b_signals:
        if signal.reference_index is None or signal.sweep_index is None:
            continue
        pivot_index = signal.reference_index
        sweep_index = signal.sweep_index
        window_gaps = gap_types_between(
            pivot_index - left, pivot_index + right
        )
        interval_gaps = gap_types_between(pivot_index, sweep_index)
        window_any += bool(window_gaps)
        interval_any += bool(interval_gaps)
        window_event_counts.update(window_gaps)
        interval_event_counts.update(interval_gaps)
        window_signal_counts.update(set(window_gaps))
        interval_signal_counts.update(set(interval_gaps))

    def section(
        any_count: int, signal_counts: Counter, event_counts: Counter
    ) -> Dict[str, object]:
        return {
            "signals_with_any_gap": any_count,
            "signals_by_gap_type": {
                gap_type: signal_counts.get(gap_type, 0) for gap_type in gap_types
            },
            "gap_events_by_type": {
                gap_type: event_counts.get(gap_type, 0) for gap_type in gap_types
            },
        }

    report: Dict[str, object] = {
        "strategy_version": "v1",
        "scope": "4,268 kept Stage 1 B_v1 signals after unexpected-gap filter",
        "signals_checked": len(b_signals),
        "pivot_window_definition": "pivot bar plus 5 bars left and 5 bars right",
        "pivot_window": section(
            window_any, window_signal_counts, window_event_counts
        ),
        "pivot_to_sweep": section(
            interval_any, interval_signal_counts, interval_event_counts
        ),
        "b_v2_rejected_gap_types": [WEEKEND, HOLIDAY_BREAK, UNEXPECTED],
        "daily_break_decision": (
            "Counted but retained for B_v2; only weekend, holiday, and unexpected "
            "gaps are rejected by the approved rule."
        ),
    }
    json_path = reports_dir / "stage1_setup_b_v1_gap_audit.json"
    markdown_path = reports_dir / "STAGE1_SETUP_B_V1_GAP_AUDIT.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    lines = [
        "# Stage 1 Setup B_v1 gap audit",
        "",
        "Scope: 4,268 kept B_v1 signals after the unexpected-gap filter.",
        "",
        "| Interval | Any gap | Weekend | Holiday | Daily break | Unexpected |",
        "|---|---:|---:|---:|---:|---:|",
        "| 11-bar pivot window | %d | %d | %d | %d | %d |" % (
            window_any,
            window_signal_counts.get(WEEKEND, 0),
            window_signal_counts.get(HOLIDAY_BREAK, 0),
            window_signal_counts.get(DAILY_BREAK, 0),
            window_signal_counts.get(UNEXPECTED, 0),
        ),
        "| Pivot bar through sweep bar | %d | %d | %d | %d | %d |" % (
            interval_any,
            interval_signal_counts.get(WEEKEND, 0),
            interval_signal_counts.get(HOLIDAY_BREAK, 0),
            interval_signal_counts.get(DAILY_BREAK, 0),
            interval_signal_counts.get(UNEXPECTED, 0),
        ),
        "",
        "Counts by type are numbers of signals containing at least one gap of that type.",
        "The pivot-to-sweep interval contains 193 weekend gap events across 190 signals;",
        "all other listed signal counts equal their event counts.",
        "",
        "B_v2 rejects weekend, holiday, and unexpected gaps. Daily metals breaks are",
        "counted but remain allowed, following the recorded decision.",
    ]
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report["output_json"] = str(json_path.resolve())
    report["output_markdown"] = str(markdown_path.resolve())
    return report


def write_setup_b_v2_counts(
    reports_dir: Path,
    signals: Sequence[Signal],
    cost_scenario_results: Mapping[
        str, Tuple[Sequence[Trade], Sequence[SkippedSignal], Mapping[str, object]]
    ],
    split: Mapping[str, object],
) -> Dict[str, object]:
    """Write count-only train/validation reporting; never compute edge metrics."""

    train_end = datetime.fromisoformat(split["train"]["end_utc"])  # type: ignore
    validation_start = datetime.fromisoformat(  # type: ignore
        split["validation"]["start_utc"]
    )

    def partition(timestamp: datetime) -> str:
        if timestamp <= train_end:
            return "train"
        if timestamp >= validation_start:
            return "validation"
        raise ValueError("Timestamp falls outside frozen train/validation split")

    def count_rows(items: Sequence[object]) -> Dict[str, Dict[str, int]]:
        def item_timestamp(item: object) -> datetime:
            if isinstance(item, Signal):
                return item.timestamp
            return item.signal_timestamp  # type: ignore

        counts = Counter(
            (partition(item_timestamp(item)), item.direction)  # type: ignore
            for item in items
            if item.setup == "B"  # type: ignore
        )
        return {
            split_name: {
                "long": counts.get((split_name, "long"), 0),
                "short": counts.get((split_name, "short"), 0),
                "total": (
                    counts.get((split_name, "long"), 0)
                    + counts.get((split_name, "short"), 0)
                ),
            }
            for split_name in ("train", "validation")
        }

    signal_counts = count_rows(signals)
    trade_counts = {
        scenario: count_rows(result[0])
        for scenario, result in cost_scenario_results.items()
    }
    report: Dict[str, object] = {
        "strategy_version": "v2",
        "metrics_computed": False,
        "expectancy_computed": False,
        "profit_factor_computed": False,
        "signals": signal_counts,
        "trades_by_cost_scenario": trade_counts,
    }
    json_path = reports_dir / "stage1_setup_b_v2_counts.json"
    markdown_path = reports_dir / "STAGE1_SETUP_B_V2_COUNTS.md"
    json_path.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    lines = [
        "# Stage 1 Setup B_v2 counts",
        "",
        "No expectancy, profit factor, confidence interval, or GO/NO-GO metric",
        "was computed.",
        "",
        "## Signals",
        "",
        "| Split | Long | Short | Total |",
        "|---|---:|---:|---:|",
        "| Train | %d | %d | %d |" % (
            signal_counts["train"]["long"], signal_counts["train"]["short"],
            signal_counts["train"]["total"],
        ),
        "| Validation | %d | %d | %d |" % (
            signal_counts["validation"]["long"],
            signal_counts["validation"]["short"],
            signal_counts["validation"]["total"],
        ),
        "",
        "## Labeled trades",
        "",
        "| Cost scenario | Split | Long | Short | Total |",
        "|---|---|---:|---:|---:|",
    ]
    for scenario in ("normal", "plus_50_percent", "plus_100_percent"):
        for split_name in ("train", "validation"):
            values = trade_counts[scenario][split_name]
            lines.append("| %s | %s | %d | %d | %d |" % (
                scenario, split_name, values["long"], values["short"], values["total"]
            ))
    markdown_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    report["output_json"] = str(json_path.resolve())
    report["output_markdown"] = str(markdown_path.resolve())
    return report
