"""Stage 1 output writers and deterministic sanity-check charts."""

import csv
import json
import random
from collections import Counter, defaultdict
from datetime import datetime
from pathlib import Path
from typing import Dict, List, Mapping, Sequence, Tuple

import matplotlib

matplotlib.use("Agg")
import matplotlib.dates as mdates
import matplotlib.image as mpimg
import matplotlib.pyplot as plt
from matplotlib.patches import Rectangle

from .labeling import SkippedSignal, Trade
from .models import Bar, Signal


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


def _draw_trade_chart(
    path: Path, bars: Sequence[Bar], trade: Trade, signal: Signal
) -> None:
    start = max(0, trade.signal_index - 40)
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
    ax.legend(loc="upper left", ncol=6, fontsize=8, frameon=False)
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
) -> Dict[str, object]:
    trade_rows = [trade.to_dict() for trade in trades]
    skipped_rows = [item.to_dict() for item in skipped]
    _write_rows(reports_dir / "trades_stage1.csv", trade_rows)
    _write_rows(reports_dir / "skipped_signals_stage1.csv", skipped_rows)
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
            _draw_trade_chart(path, bars, trade, signal)
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
