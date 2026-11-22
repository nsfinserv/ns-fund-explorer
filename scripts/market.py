"""Market strip for nsfinancialservices.co.in - the only thing this repository publishes now.

  python scripts/market.py   ->  out/data/market.json, out/index.html, out/robots.txt

Each index's change on the last trading day (end of day, not live), worked out as the median
day's change in NAV of the regular-plan growth options of the index funds that track it.
AMFI data only: AMFI's latest NAV file (to find the funds) and AMFI's NAV history report for the
last few days (one fund house at a time). No other source is used.

If no figure can be worked out, the script stops with an error so the workflow does not publish,
and the website keeps showing the previous day's strip (it hides itself when data is over a week old).
"""
from __future__ import annotations

import datetime as dt
import re
import statistics
import sys
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import ROOT, day_num, num_day, parse_nav_text, write_json  # noqa: E402
import amfi_fetch as AF  # noqa: E402

OUT = ROOT / "out"
SITE = "https://nsfinancialservices.co.in/"
HISTORY_DAYS = 10

# Equity index fund categories in AMFI's NAV file (old and new SEBI category names)
EQUITY_INDEX = re.compile(r"index funds?\s*-\s*equity|other scheme\s*-\s*index funds?", re.I)

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


def pick_index_funds(text: str) -> dict[int, str]:
    """AMFI code -> scheme name, for regular-plan growth options of equity index funds.
    Reads the Plan and Option columns of AMFI's NAV file when present, else the scheme name."""
    cols: dict[str, int] = {}
    delim, category, out = ";", "", {}
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line:
            continue
        if not cols:
            if "scheme code" in line.lower():
                delim = "|" if line.count("|") > line.count(";") else ";"
                for i, h in enumerate(x.strip().lower() for x in line.split(delim)):
                    if "scheme code" in h:
                        cols["code"] = i
                    elif "scheme name" in h or h == "nav name":
                        cols["name"] = i
                    elif h == "plan":
                        cols["plan"] = i
                    elif h == "option":
                        cols["option"] = i
            continue
        if delim not in line:
            m = re.match(r"^.*?Schemes?\s*\((.+)\)\s*$", line, re.I)
            if m:
                category = re.sub(r"\s+", " ", m.group(1)).strip()
            continue
        if not EQUITY_INDEX.search(category):
            continue
        p = [x.strip() for x in line.split(delim)]
        get = lambda k: p[cols[k]] if k in cols and cols[k] < len(p) else ""  # noqa: E731
        try:
            code = int(get("code"))
        except ValueError:
            continue
        name = re.sub(r"\s+", " ", get("name"))
        full = " ".join((name, get("plan"), get("option"))).lower()
        if "direct" in full or "regular" not in full:
            continue                                    # regular plans only
        if "growth" not in full or re.search(r"idcw|dividend|payout|bonus", full):
            continue                                    # growth option only
        out[code] = name
    return out


def latest_index_funds() -> dict[int, str]:
    for url in (f"{AF.PORTAL}/spages/NAVAll.txt", f"{AF.WWW}/spages/NAVAll.txt"):
        try:
            text = AF.http(url).decode("utf-8", "replace")
        except RuntimeError as e:
            print(f"latest NAV file: {e}")
            continue
        if "Scheme Code" in text:
            funds = pick_index_funds(text)
            print(f"latest NAV file from {url}: {len(funds)} regular-plan equity index funds")
            return funds
    raise SystemExit("Could not download AMFI's latest NAV file. Not publishing.")


def nav_history(codes: set[int], days: int = HISTORY_DAYS) -> dict[int, dict[int, float]]:
    """code -> {day number: NAV} for the last few days, from AMFI's NAV history report."""
    to = dt.date.today()
    frm = to - dt.timedelta(days=days)
    hist: dict[int, dict[int, float]] = {c: {} for c in codes}
    failed = []
    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(AF.download_history, frm, to, mf): name for mf, name in AF.amc_list()}
        for fu in as_completed(futs):
            try:
                for r in parse_nav_text(fu.result()):
                    if r["code"] in hist:
                        hist[r["code"]][day_num(r["date"])] = r["nav"]
            except Exception as e:  # noqa: BLE001
                failed.append(f"{futs[fu]} ({str(e)[:60]})")
    got = sum(1 for v in hist.values() if len(v) >= 2)
    print(f"NAV history {frm} to {to}: {got} of {len(codes)} funds have two or more NAVs"
          + (f"; failed: {', '.join(failed)}" if failed else ""))
    return hist


def day_moves(funds: dict[int, str], hist: dict[int, dict[int, float]]) -> list[tuple[str, int, int, float]]:
    """(fund name, previous NAV day, last NAV day, day's change) from the two latest NAVs of each fund."""
    moves = []
    for code, name in funds.items():
        days = sorted(hist.get(code, {}))
        if len(days) >= 2:
            d0, d1 = days[-2], days[-1]
            v0, v1 = hist[code][d0], hist[code][d1]
            if v0 > 0:
                moves.append((name, d0, d1, v1 / v0 - 1))
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
    funds = latest_index_funds()
    if not funds:
        raise SystemExit("No index funds found in AMFI's NAV file. Not publishing.")
    mk = market_strip(day_moves(funds, nav_history(set(funds))))
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
