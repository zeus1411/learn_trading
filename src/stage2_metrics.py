"""Frozen Stage 2 metrics on guarded development trades only."""

import hashlib
from collections import Counter, defaultdict
from math import sqrt

import numpy as np

from .stage2_data import DevelopmentBoundary, DevelopmentTrades


RESAMPLES = 10000
BLOCK_SIZE = 10
GATES = {"min_trades": 300, "min_expectancy_r": 0.10, "min_ci_lower_r_exclusive": 0.0,
         "min_profit_factor": 1.15, "min_positive_calendar_fraction": 0.70,
         "stress_cost_multiplier": 1.5}


def cell_seed(seed: int, key: str) -> int:
    return int.from_bytes(hashlib.sha256((str(seed) + ":" + key).encode()).digest()[:4], "big")


def wilson_interval(successes: int, n: int):
    if n == 0:
        return [None, None]
    z = 1.959963984540054
    p = successes / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    half = z * sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / denominator
    return [max(0.0, centre - half), min(1.0, centre + half)]


def block_bootstrap(values, seed: int, n_trials: int = 1, global_trials: int = 3,
                    resamples: int = RESAMPLES, block_size: int = BLOCK_SIZE):
    """Circular moving-block percentile CI; final block truncated to exactly n."""
    data = np.asarray(values, dtype=float)
    if not len(data):
        return {"ci95_raw": [None, None], "ci95_adjusted_setup": [None, None],
                "ci95_adjusted_global": [None, None], "p_positive_raw": None,
                "p_positive_adjusted_setup": None, "p_positive_adjusted_global": None,
                "resamples": resamples, "block_size": block_size, "seed": seed}
    if not np.isfinite(data).all() or n_trials < 1 or global_trials < 1:
        raise ValueError("Invalid bootstrap inputs")
    n = len(data)
    full_blocks, tail = divmod(n, block_size)
    starts = np.arange(n)
    sums = data[(starts[:, None] + np.arange(block_size)) % n].sum(axis=1)
    tails = data[(starts[:, None] + np.arange(tail)) % n].sum(axis=1) if tail else None
    rng = np.random.RandomState(seed)
    means = np.empty(resamples)
    for begin in range(0, resamples, 256):
        size = min(256, resamples - begin)
        selected = rng.randint(0, n, size=(size, full_blocks + bool(tail)))
        total = sums[selected[:, :full_blocks]].sum(axis=1)
        if tail:
            total += tails[selected[:, -1]]
        means[begin:begin + size] = total / n
    def interval(trials):
        alpha = 0.05 / trials
        return [float(v) for v in np.quantile(means, [alpha / 2, 1 - alpha / 2])]
    observed = float(data.mean())
    # Null-centred one-sided bootstrap H0: mean <= 0, alternative: mean > 0.
    p = float((1 + np.count_nonzero(means - observed >= observed)) / (resamples + 1))
    return {"ci95_raw": interval(1), "ci95_adjusted_setup": interval(n_trials),
            "ci95_adjusted_global": interval(global_trials), "p_positive_raw": p,
            "p_positive_adjusted_setup": min(1.0, p * n_trials),
            "p_positive_adjusted_global": min(1.0, p * global_trials),
            "setup_n_trials": n_trials, "global_n_trials": global_trials,
            "resamples": resamples, "block_size": block_size, "seed": seed,
            "method": "circular moving-block percentile; Bonferroni alpha=0.05/n_trials",
            "few_blocks_warning": n < block_size * 10}


def _calendar_breakdown(trades, quarterly=False):
    groups = defaultdict(list)
    for trade in trades:
        time = trade.signal_timestamp
        key = "%04d-Q%d" % (time.year, (time.month - 1) // 3 + 1) if quarterly else str(time.year)
        groups[key].append(trade.result_r)
    return {key: {"n_trades": len(values), "expectancy_r": sum(values) / len(values),
                  "total_r": sum(values)} for key, values in sorted(groups.items())}


def equity_points(trades):
    """Realized equity; same-timestamp exits aggregated to avoid arbitrary DD."""
    by_exit = defaultdict(float)
    for trade in trades:
        by_exit[trade.exit_timestamp] += trade.result_r
    points = []
    total = 0.0
    for timestamp, value in sorted(by_exit.items()):
        total += value
        points.append({"exit_timestamp": timestamp.isoformat(), "equity_r": total})
    return points


def summarize(trades, seed=20261010, n_trials=1, global_trials=3, bootstrap=True):
    ordered = sorted(trades, key=lambda t: (t.entry_timestamp, t.direction, t.exit_timestamp))
    returns = [t.result_r for t in ordered]
    n = len(ordered)
    positive = [v for v in returns if v > 0]
    negative = [v for v in returns if v < 0]
    outcomes = Counter(t.outcome for t in ordered)
    points = equity_points(ordered)
    peak = drawdown = 0.0
    for point in points:
        peak = max(peak, point["equity_r"])
        drawdown = max(drawdown, peak - point["equity_r"])
    longest = streak = 0
    for trade in sorted(ordered, key=lambda t: (t.exit_timestamp, t.entry_timestamp, t.direction)):
        streak = streak + 1 if trade.result_r < 0 else 0
        longest = max(longest, streak)
    years, quarters = _calendar_breakdown(ordered), _calendar_breakdown(ordered, True)
    def fraction(groups):
        return sum(v["expectancy_r"] > 0 for v in groups.values()) / len(groups) if groups else None
    summary = {
        "n_trades": n, "n_truncated": sum(t.truncated_by_gap for t in ordered),
        "outcome_counts": dict(sorted(outcomes.items())),
        "win_rate": len(positive) / n if n else None,
        "win_rate_wilson95": wilson_interval(len(positive), n),
        "barrier_win_rate": outcomes["WIN"] / n if n else None,
        "barrier_win_rate_wilson95": wilson_interval(outcomes["WIN"], n),
        "average_win_r": sum(positive) / len(positive) if positive else None,
        "average_loss_r": sum(negative) / len(negative) if negative else None,
        "expectancy_r": sum(returns) / n if n else None,
        "total_r": sum(returns),
        "gross_profit_r": sum(positive), "gross_loss_r": -sum(negative),
        "profit_factor": sum(positive) / -sum(negative) if negative else None,
        "profit_factor_infinite": bool(positive) and not negative,
        "max_drawdown_r": drawdown, "longest_losing_streak": longest,
        "average_bars_held": sum(t.bars_held for t in ordered) / n if n else None,
        "average_mae_r": sum(t.mae_r for t in ordered) / n if n else None,
        "average_mfe_r": sum(t.mfe_r for t in ordered) / n if n else None,
        "by_year": years, "by_quarter": quarters,
        "positive_year_fraction": fraction(years), "positive_quarter_fraction": fraction(quarters),
        "equity_curve_r": points,
    }
    if bootstrap:
        summary["bootstrap"] = block_bootstrap(returns, seed, n_trials, global_trials)
    return summary


def metrics_cube(scenarios, config, context):
    boundary = DevelopmentBoundary.from_config(config)
    for trades in scenarios.values():
        for trade in trades:
            boundary.check_trade(trade)
    output = {}
    for setup in ("A", "B"):
        output[setup] = {}
        for scenario, trades in scenarios.items():
            output[setup][scenario] = {}
            for view in ("include_truncated", "exclude_truncated"):
                output[setup][scenario][view] = {}
                kept = [t for t in trades if t.setup == setup and
                        (view == "include_truncated" or not t.truncated_by_gap)]
                for direction in ("combined", "long", "short"):
                    selected = [t for t in kept if direction == "combined" or t.direction == direction]
                    key = ":".join((context, setup, scenario, view, direction))
                    output[setup][scenario][view][direction] = summarize(
                        selected, cell_seed(int(config["random_seed"]), key),
                        int(config["trials"]["by_setup"][setup]), int(config["trials"]["global_n_trials"]))
    return output


def evaluate_development(bundle: DevelopmentTrades, config):
    if not isinstance(bundle, DevelopmentTrades):
        raise TypeError("Evaluation requires guarded DevelopmentTrades")
    bundle.__post_init__()
    partitions = {"train": {}, "validation": {}}
    excluded = {}
    for scenario, trades in bundle.scenarios:
        partitions["train"][scenario] = [t for t in trades if t.exit_timestamp <= bundle.boundary.train_end]
        partitions["validation"][scenario] = [t for t in trades if t.signal_timestamp >= bundle.boundary.validation_start]
        excluded[scenario] = sum(t.signal_timestamp < bundle.boundary.validation_start <= t.exit_timestamp for t in trades)
    return {"train": metrics_cube(partitions["train"], config, "train"),
            "validation": metrics_cube(partitions["validation"], config, "validation"),
            "split_crossing_trades_excluded_from_train": excluded}


def frozen_gate_check(normal, stress):
    checks = {
        "n_trades_ge_300": normal["n_trades"] >= GATES["min_trades"],
        "net_expectancy_ge_0_10r": normal["expectancy_r"] is not None and normal["expectancy_r"] >= GATES["min_expectancy_r"],
        "raw_ci95_lower_gt_zero": normal["bootstrap"]["ci95_raw"][0] is not None and normal["bootstrap"]["ci95_raw"][0] > 0,
        "profit_factor_ge_1_15": normal["profit_factor_infinite"] or (normal["profit_factor"] is not None and normal["profit_factor"] >= GATES["min_profit_factor"]),
        "positive_years_ge_70_percent": normal["positive_year_fraction"] is not None and normal["positive_year_fraction"] >= GATES["min_positive_calendar_fraction"],
        "positive_quarters_ge_70_percent": normal["positive_quarter_fraction"] is not None and normal["positive_quarter_fraction"] >= GATES["min_positive_calendar_fraction"],
        "positive_with_plus_50_percent_costs": stress["expectancy_r"] is not None and stress["expectancy_r"] > 0,
    }
    adjusted = {key: normal["bootstrap"][key][0] is not None and normal["bootstrap"][key][0] > 0
                for key in ("ci95_adjusted_setup", "ci95_adjusted_global")}
    return {"status": "PASS" if all(checks.values()) else "FAIL", "checks": checks,
            "failed_gates": [key for key, passed in checks.items() if not passed],
            "adjusted_lower_bound_positive": adjusted}


def setup_decisions(validation, oos):
    decisions = {}
    for setup in ("A", "B"):
        scopes = {}
        for name, cube in (("validation", validation), ("walk_forward_oos", oos)):
            scopes[name] = {view: frozen_gate_check(cube[setup]["normal"][view]["combined"],
                                                   cube[setup]["plus_50_percent"][view]["combined"])
                            for view in ("include_truncated", "exclude_truncated")}
        numeric = "PASS" if all(g["status"] == "PASS" for scope in scopes.values() for g in scope.values()) else "FAIL"
        decisions[setup] = {"status": "INSUFFICIENT_DATA" if setup == "B" else numeric,
                            "numeric_frozen_gate_result": numeric, "scopes": scopes,
                            "stage3_allowed": False,
                            "reason": "B_v2 has only 248 normal validation trades; descriptive only"
                            if setup == "B" else "All frozen gates must pass on validation and OOS in both views"}
    return decisions
