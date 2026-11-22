"""Market strip for nsfinancialservices.co.in - the only thing this repository publishes now.

  python scripts/market.py   ->  out/data/market.json, out/index.html, out/robots.txt

Each index's change on the last trading day (end of day, not live), worked out as the median
day's change in NAV of the regular-plan index funds that track it. AMFI data only: AMFI's latest
NAV file plus AMFI's NAV history report for the last few days. No other source is used.

If no figure can be worked out, the script stops with an error so the workflow does not publish,
and the website keeps showing the previous day's strip (it hides itself when data is over a week old).
"""
from __future__ import annotations

import datetime as dt
import re
import statistics
import sys
from collections import Counter
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, asset_kind, clean_name, group_of, num_day, open_store, plan_of, sub_name, write_json  # noqa: E402
import amfi_fetch as AF  # noqa: E402

OUT = ROOT / "out"
SITE = "https://nsfinancialservices.co.in/"

# (label shown, words the fund name must have, words it must not have)
MARKET = [
    ("Nifty 50", r"\bnifty 50\b", r"next|equal|weight|value|alpha|vol|quality|momentum|shariah|dividend|arbitrage|top"),
    ("Sensex", r"\bsensex\b", r"next|50|100|200|500"),
    ("Nifty Next 50", r"\bnifty next 50\b", r"equal|weight"),
    ("Nifty 500", r"\bnifty 500\b", r"multicap|momentum|value|quality|equal|weight|shariah|vol|alpha|\d+:\d+"),
    ("Nifty Bank", r"\bnifty bank\b", r"psu|private|equal"),
    ("Nifty Midcap 150", r"\bnifty midcap 150\b", r"momentum|quality|value|alpha"),
    ("Nifty Smallcap 250", r"\bnifty smallcap 250\b", r"momentum|quality|value|alpha"),
]


def market_name(n: str) -> str:
    n = n.lower().replace("&", " and ")
    n = re.sub(r"nifty\s*50\b", "nifty 50", n)
    n = re.sub(r"\bmid\s+cap\b", "midcap", n)
    n = re.sub(r"\bsmall\s+cap\b", "smallcap", n)
    n = re.sub(r"\b(midcap|smallcap|next)\s*(\d+)", r"\1 \2", n)
    return re.sub(r"\s+", " ", n)


def index_funds(schemes: list[dict]) -> dict[int, str]:
    """AMFI code -> cleaned name, for regular-plan growth options of equity index funds."""
    out = {}
    for s in schemes:
        g = group_of(s["category"])
        if g != "Index funds" or plan_of(s["name"], g) != "R":
            continue
        if asset_kind(g, sub_name(s["category"]), s["name"]) != "equity":
            continue
        out[int(s["code"])] = clean_name(s["name"])
    return out


def day_moves(con, funds: dict[int, str]) -> list[tuple[str, int, int, float]]:
    """(fund name, previous NAV day, last NAV day, day's change) from the two latest NAVs of each fund."""
    moves = []
    for code, name in funds.items():
        rows = con.execute("SELECT d, nav FROM nav WHERE code=? ORDER BY d DESC LIMIT 2", (code,)).fetchall()
        if len(rows) == 2 and rows[1][1] > 0:
            (d1, v1), (d0, v0) = rows
            moves.append((name, int(d0), int(d1), v1 / v0 - 1))
    return moves


def market_strip(moves: list[tuple[str, int, int, float]]) -> dict | None:
    """Uses only funds priced on the most common last day, against the most common previous day,
    so every figure is for the same trading day."""
    if not moves:
        return None
    last = Counter(m[2] for m in moves).most_common(1)[0][0]
    prev = Counter(m[1] for m in moves if m[2] == last).most_common(1)[0][0]
    items = []
    for label, inc, exc in MARKET:
        vals = [m[3] for m in moves if m[2] == last and m[1] == prev
                and re.search(inc, market_name(m[0])) and not re.search(exc, market_name(m[0])) and abs(m[3]) < 0.2]
        if vals:
            items.append({"n": label, "c": round(statistics.median(vals) * 100, 2), "k": len(vals)})
    if not items:
        return None
    return {"date": num_day(last).isoformat(), "prev": num_day(prev).isoformat(), "items": items,
            "src": "Day's change, worked out from AMFI NAV data at the end of the day",
            "note": "Not live index values. For education only."}


INDEX_HTML = f"""<!doctype html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="robots" content="noindex">
<meta http-equiv="refresh" content="0; url={SITE}">
<link rel="canonical" href="{SITE}">
<title>NS Financial Services</title>
</head>
<body>
<p>This address only supplies data to <a href="{SITE}">nsfinancialservices.co.in</a>.</p>
</body>
</html>
"""


def main():
    AF.RAW.mkdir(parents=True, exist_ok=True)
    schemes = AF.fetch_latest()
    AF.recent(days=10)
    funds = index_funds(schemes)
    con = open_store()
    mk = market_strip(day_moves(con, funds))
    con.close()
    if not mk:
        raise SystemExit("No market figures could be worked out from AMFI today. Not publishing.")
    age = (dt.date.today() - dt.date.fromisoformat(mk["date"])).days
    print(f"market strip for {mk['date']} (vs {mk['prev']}, {age} days old): "
          + ", ".join(f"{i['n']} {i['c']:+.2f}% ({i['k']} funds)" for i in mk["items"]))
    write_json(OUT / "data" / "market.json", mk, compact=False)
    (OUT / "index.html").write_text(INDEX_HTML, encoding="utf-8")
    (OUT / "robots.txt").write_text("User-agent: *\nDisallow: /\n", encoding="utf-8")


if __name__ == "__main__":
    main()
