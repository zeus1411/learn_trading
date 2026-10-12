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
from .labeling import SkippedSignal, Trade
from .models import Bar, Signal


SETUP_A_PRE_SIGNAL_BARS = 60


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
    bars_before_signal = SETUP_A_PRE_SIGNAL_BARS if trade.setup == "A" else 40
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
        ax.axvline(bars[signal.reference_index].timestamp, color="#7C3AED", linestyle=":",
                   linewidth=1.0, label="Confirmed pivot")
    if signal.sweep_index is not None and start <= signal.sweep_index < end:
        ax.axvline(bars[signal.sweep_index].timestamp, color="#EA580C", linestyle=":",
                   linewidth=1.0, label="Sweep")
    ax.set_title(
        "Setup %s %s | %s | signal %s | source %s"
        % (trade.setup, trade.direction.upper(), trade.outcome,
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
            "B": {"pre_signal_bars": 40},
        },
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
