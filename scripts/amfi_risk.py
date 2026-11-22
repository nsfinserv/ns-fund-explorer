"""AMFI 'Disclosure of Risk Parameters' for Mid Cap and Small Cap funds.

AMFI publishes these every month for mid cap and small cap schemes only:
stress test (days to sell 25% / 50% of the portfolio), share of the top 10
investors, large/mid/small cap split, portfolio and benchmark standard
deviation, beta, trailing 12-month PE against the benchmark, and portfolio
turnover.

Endpoint (found on https://www.amfiindia.com/risk-parameters):
  GET https://www.amfiindia.com/api/risk-parameter-data-revised?date=01-Oct-2026&strCatId=17
  strCatId 17 = Mid Cap Fund, 18 = Small Cap Fund (76 and 77 return the same lists).

Usage:
  python scripts/amfi_risk.py            -> data/store/risk_params.json
  python scripts/amfi_risk.py diagnose   -> prints what AMFI returned
"""
from __future__ import annotations

import datetime as dt
import json
import sys
import time
from pathlib import Path

import requests

BASE = "https://www.amfiindia.com"
PATH = "/api/risk-parameter-data-revised"
CATS = {17: "Mid Cap Fund", 18: "Small Cap Fund"}
OUT = Path("data/store/risk_params.json")
HEADERS = {
    "User-Agent": "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/130 Safari/537.36",
    "Accept": "application/json, text/plain, */*",
    "Referer": BASE + "/risk-parameters",
}


def month_starts(today: dt.date, back: int = 4):
    y, m = today.year, today.month
    for _ in range(back):
        yield dt.date(y, m, 1)
        m -= 1
        if m == 0:
            y, m = y - 1, 12


def fetch(cat: int, d: dt.date, s: requests.Session):
    r = s.get(BASE + PATH, params={"date": d.strftime("%d-%b-%Y"), "strCatId": str(cat)}, timeout=60)
    r.raise_for_status()
    j = r.json()
    return j if isinstance(j, list) else []


def flatten(rec: dict) -> dict:
    st = rec.get("stressTest") or {}
    co = rec.get("concentration") or {}
    a = co.get("AssetSide") or {}
    vo = rec.get("volatility") or {}
    va = rec.get("valuation") or {}
    bm = va.get("BenchMark") or {}
    return {
        "mf": str(rec.get("MF_ID") or ""),
        "amc": rec.get("MF_Name"),
        "sid": rec.get("SchemeID"),
        "name": rec.get("SchemeName"),
        "aum": rec.get("AUM"),
        "d50": st.get("StressTest_portfolio_50"),     # days to sell 50% of portfolio
        "d25": st.get("StressTest_portfolio_25"),     # days to sell 25%
        "top10inv": co.get("LiabilitySide"),            # % of AUM held by top 10 investors
        "lc": a.get("AssetSide_LargeCap"), "mc": a.get("AssetSide_MidCap"),
        "sc": a.get("AssetSide_SmallCap"), "cash": a.get("AssetSide_Cash"),
        "sd": vo.get("Volatility_PortfolioASD"), "bsd": vo.get("Volatility_BenchmarkASD"),
        "beta": vo.get("Volatility_PortfolioBeta"),
        "pe": va.get("Valuation_PortfolioTrailing12mPE"),
        "bpe": bm.get("Valuation_BenchmarkTrailing12mPE"),
        "bpe1": bm.get("Valuation_BenchmarkTrailing12mPE_1YA"),
        "bpe2": bm.get("Valuation_BenchmarkTrailing12mPE_2YA"),
        "turn": va.get("Valuation_PortfolioTurnoverRatio"),
        "date": rec.get("date"),
    }


def main(diagnose: bool = False) -> int:
    s = requests.Session()
    s.headers.update(HEADERS)
    out = {"source": BASE + "/risk-parameters", "fetched": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z", "cats": {}}
    for cat, name in CATS.items():
        got = None
        for d in month_starts(dt.date.today()):
            try:
                rows = fetch(cat, d, s)
            except Exception as e:  # noqa: BLE001
                print(f"[risk] {name} {d:%b-%Y}: {e}")
                rows = []
            if diagnose:
                print(f"[risk] {name} {d:%b-%Y}: {len(rows)} schemes")
            if rows:
                got = (d, rows)
                break
            time.sleep(1)
        if got:
            d, rows = got
            out["cats"][name] = {"month": d.strftime("%Y-%m"), "schemes": [flatten(r) for r in rows]}
            print(f"[risk] {name}: {len(rows)} schemes for {d:%b %Y}")
        else:
            print(f"[risk] {name}: nothing found in the last 4 months")
    if diagnose:
        print(json.dumps(out, indent=1)[:3000])
        return 0
    OUT.parent.mkdir(parents=True, exist_ok=True)
    if out["cats"]:
        OUT.write_text(json.dumps(out, separators=(",", ":")))
    elif OUT.exists():
        print("[risk] keeping last month's file")
    return 0


if __name__ == "__main__":
    sys.exit(main(diagnose=len(sys.argv) > 1 and sys.argv[1] == "diagnose"))
