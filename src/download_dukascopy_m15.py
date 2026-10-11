"""Download Dukascopy XAUUSD M15 BID bars from its chart data endpoint.

This small standard-library downloader exists only to make the 2026 timezone
verification reproducible on the project's Python 3.8 runtime. Closed years
use the already-downloaded EV Trading Labs annual files derived from Dukascopy
ticks; the current year is fetched directly from Dukascopy.
"""

import argparse
import gzip
import hashlib
import json
import random
import string
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Dict, List


ROOT = Path(__file__).resolve().parents[1]
ENDPOINT = "https://freeserv.dukascopy.com/2.0/index.php"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(block)
    return digest.hexdigest()


def _request(cursor_ms: int) -> List[List[float]]:
    token = "_callbacks____" + "".join(
        random.choice(string.ascii_letters + string.digits) for _ in range(9)
    )
    query = urllib.parse.urlencode(
        {
            "path": "chart/json3",
            "splits": "true",
            "stocks": "true",
            "time_direction": "N",
            "jsonp": token,
            "last_update": str(cursor_ms),
            "offer_side": "B",
            "instrument": "XAU/USD",
            "interval": "15MIN",
            "limit": "30000",
        }
    )
    request = urllib.request.Request(
        ENDPOINT + "?" + query,
        headers={
            "User-Agent": "Mozilla/5.0",
            "Referer": "https://freeserv.dukascopy.com/2.0/",
        },
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        text = response.read().decode("utf-8")
    prefix = token + "("
    if not text.startswith(prefix) or not text.endswith(");"):
        raise RuntimeError("Unexpected Dukascopy JSONP response")
    payload = json.loads(text[len(prefix) : -2])
    if not isinstance(payload, list):
        raise RuntimeError("Dukascopy response is not an array")
    return payload


def download(start: datetime, end: datetime, output: Path) -> Dict[str, object]:
    start_ms = int(start.timestamp() * 1000)
    end_ms = int(end.timestamp() * 1000)
    cursor = start_ms
    by_timestamp: Dict[int, Dict[str, float]] = {}
    requests = 0
    while cursor < end_ms:
        batch = None
        for attempt in range(5):
            try:
                batch = _request(cursor)
                break
            except Exception:
                if attempt == 4:
                    raise
                time.sleep(2 ** attempt)
        assert batch is not None
        requests += 1
        accepted = 0
        for row in batch:
            timestamp_ms = int(row[0])
            if timestamp_ms < start_ms or timestamp_ms >= end_ms:
                continue
            by_timestamp[timestamp_ms] = {
                "ts": timestamp_ms // 1000,
                "o": float(row[1]),
                "h": float(row[2]),
                "l": float(row[3]),
                "c": float(row[4]),
                "v": float(row[5]),
            }
            accepted += 1
        if not batch or int(batch[-1][0]) <= cursor:
            break
        cursor = int(batch[-1][0])
        if cursor >= end_ms or accepted == 0:
            break
        time.sleep(0.2)

    rows = [by_timestamp[key] for key in sorted(by_timestamp)]
    output.parent.mkdir(parents=True, exist_ok=True)
    with gzip.open(str(output), "wt", encoding="utf-8", newline="") as handle:
        json.dump(rows, handle, separators=(",", ":"))
    return {
        "status": "PASS" if rows else "FAIL",
        "source": ENDPOINT,
        "instrument": "XAU/USD",
        "price_side": "BID",
        "timeframe": "M15",
        "timestamp_semantics": "UTC bar open",
        "requested_start_utc": start.isoformat(),
        "requested_end_exclusive_utc": end.isoformat(),
        "first_bar_utc": (
            datetime.fromtimestamp(rows[0]["ts"], timezone.utc).isoformat()
            if rows
            else None
        ),
        "last_bar_utc": (
            datetime.fromtimestamp(rows[-1]["ts"], timezone.utc).isoformat()
            if rows
            else None
        ),
        "rows": len(rows),
        "requests": requests,
        "output": str(output.resolve()),
        "sha256": _sha256(output),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--year", type=int, required=True)
    parser.add_argument("--end", required=True, help="Exclusive UTC date, YYYY-MM-DD")
    args = parser.parse_args()
    start = datetime(args.year, 1, 1, tzinfo=timezone.utc)
    end = datetime.fromisoformat(args.end).replace(tzinfo=timezone.utc)
    output = ROOT / "rawdata" / (
        "XAUUSD_M15_DUKASCOPY_DIRECT_%d.json.gz" % args.year
    )
    report = download(start, end, output)
    report_path = ROOT / "reports" / (
        "dukascopy_direct_download_%d.json" % args.year
    )
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(
        json.dumps(report, indent=2, sort_keys=True), encoding="utf-8"
    )
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if report["status"] == "PASS" else 1


if __name__ == "__main__":
    raise SystemExit(main())
