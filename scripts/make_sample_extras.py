"""SAMPLE portfolios, market-cap list and AMFI risk parameters for the preview.

Fictional companies (Sample Co 001 ...) with made-up ISINs starting 'INX', so
nothing can be mistaken for a real holding. Never used once real files exist.
Run after make_sample.py and before build_data.py.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import DATA, group_of, plan_of, read_json, sub_name  # noqa: E402

rng = np.random.default_rng(7)
STORE = DATA / "store"

SECTORS = ["Banks", "IT - Software", "Finance", "Pharmaceuticals & Biotechnology", "Automobiles", "Petroleum Products",
           "Construction", "Consumer Durables", "Power", "Telecom - Services", "Cement & Cement Products",
           "Industrial Products", "Chemicals & Petrochemicals", "Healthcare Services", "Realty", "Insurance",
           "Capital Markets", "Retailing", "Auto Components", "Electrical Equipment"]
STOCKS = []
for i in range(1, 451):
    cap = "L" if i <= 100 else "M" if i <= 250 else "S"
    STOCKS.append({"n": f"Sample Co {i:03d} Ltd", "i": f"INX{i:06d}A01", "s": SECTORS[(i * 7) % len(SECTORS)], "c": cap,
                   "px": float(rng.uniform(80, 4000))})
POOL = {c: [s for s in STOCKS if s["c"] == c] for c in "LMS"}
CAPMIX = {"large cap": (.88, .07, .0), "large & mid": (.52, .40, .03), "mid cap": (.12, .70, .12), "small cap": (.05, .14, .74),
          "multi cap": (.40, .28, .27), "flexi cap": (.62, .22, .10), "focused": (.65, .22, .08), "value": (.58, .25, .12),
          "contra": (.55, .25, .15), "elss": (.60, .23, .12), "sectoral": (.55, .25, .15), "thematic": (.50, .28, .17)}
RATINGS = ["SOVEREIGN", "CRISIL AAA", "ICRA AAA", "CARE AA+", "CRISIL AA", "IND AA-", "ICRA A+", "CRISIL A1+"]


def equity_rows(mix, n_stocks, eq_total, seed):
    r = np.random.default_rng(seed)
    tot = sum(mix)
    w_all = []
    for share, cap in zip(mix, "LMS"):
        k = min(int(round(n_stocks * share / tot)), len(POOL[cap]))
        if k <= 0:
            continue
        idx = r.choice(len(POOL[cap]), size=k, replace=False)
        w = r.dirichlet(np.ones(k) * 0.8) * (share / tot) * eq_total
        w_all += [(POOL[cap][int(j)], float(ww)) for j, ww in zip(idx, w)]
    w_all.sort(key=lambda x: -x[1])
    rows = []
    for s, ww in w_all:
        val = ww * 50.0
        rows.append([s["n"], s["i"], s["s"], round(val * 1e5 / s["px"]), round(val, 2), round(ww, 4), None, "eq"])
    return rows


def debt_rows(total, seed, short=False):
    r = np.random.default_rng(seed)
    rows = []
    n = 14 if not short else 10
    w = r.dirichlet(np.ones(n)) * total
    for k, ww in enumerate(w):
        rat = RATINGS[int(r.integers(0, 3 if short else len(RATINGS)))]
        if short:
            nm, cls, rat = f"Sample Bank {k + 1} CD ({int(r.integers(1, 28)):02d}/0{int(r.integers(1, 9))}/2027)", "mm", "CRISIL A1+"
            y = float(r.uniform(6.6, 7.4))
        elif rat == "SOVEREIGN":
            nm, cls, y = f"7.{int(r.integers(10, 40))}% GOI {int(r.integers(2028, 2040))}", "gsec", float(r.uniform(6.3, 6.9))
        else:
            nm, cls, y = f"{float(r.uniform(7.2, 9.4)):.2f}% Sample Finance {k + 1} Ltd ({int(r.integers(1, 28)):02d}/06/20{int(r.integers(27, 33))})", "debt", float(r.uniform(7.0, 9.6))
        isin = f"INX{900000 + seed % 9000 * 20 + k:06d}B07"[:12]
        rows.append([nm, isin, rat, int(ww * 1000), round(ww * 50, 2), round(ww, 4), round(y, 3), cls])
    return rows


def add_arbitrage(rows, total, seed, split=False):
    """Hedged positions: shares bought and the same shares' futures sold. Two file styles:
    under an 'Arbitrage' heading, or inside the equity list with futures in a derivatives table."""
    r = np.random.default_rng(seed + 99)
    picks = r.choice(100, size=8, replace=False)
    w = r.dirichlet(np.ones(8)) * total
    shorts = []
    for j, ww in zip(picks, w):
        st = POOL["L"][int(j)]
        rows.append([st["n"], st["i"], st["s"], round(ww * 50 * 1e5 / st["px"]), round(ww * 50, 2), round(float(ww), 4), None,
                     "eq" if split else "arb"])
        shorts.append([st["n"] + " November 2026 Future", -round(float(ww), 4)])
    return rows, shorts


def evolve(rows, seed):
    """Previous month: drop some holdings, add some, change some quantities."""
    r = np.random.default_rng(seed)
    out = []
    for x in rows:
        y = list(x)
        if x[7] == "eq":
            u = r.random()
            if u < 0.06:
                continue                                    # bought this month (absent last month)
            if u < 0.16:
                y[3] = int(x[3] / r.uniform(1.15, 1.6))     # added this month
            elif u < 0.24:
                y[3] = int(x[3] * r.uniform(1.15, 1.6))     # cut this month
        out.append(y)
    extra = [s for s in STOCKS if s["i"] not in {x[1] for x in rows}]
    for k in r.choice(len(extra), size=3, replace=False):
        s = extra[int(k)]
        out.append([s["n"], s["i"], s["s"], 10000, 500.0, 0.8, None, "eq"])   # sold out this month
    return out


def main():
    latest = read_json(DATA / "raw" / "latest.json")
    by_house = {}
    for s in latest:
        g = group_of(s["category"])
        if plan_of(s["name"], g) != "R":
            continue
        by_house.setdefault(s["amc"], []).append((s, g, sub_name(s["category"]).lower()))
    risk = {"Mid Cap Fund": [], "Small Cap Fund": []}
    for m_i, month in enumerate(("2026-10", "2026-09")):
        mdir = STORE / "portfolios" / month
        mdir.mkdir(parents=True, exist_ok=True)
        for house, items in by_house.items():
            schemes = []
            for s, g, sub in items:
                seed = int(s["code"])
                nm = s["name"].split(" - ")[0]
                if seed % 5 == 0:
                    nm = nm.upper()
                rows, shorts = [], []
                if g == "Equity":
                    key = next((k for k in CAPMIX if k in sub), "flexi cap")
                    cash = float(np.random.default_rng(seed).uniform(1.5, 6))
                    rows = equity_rows(CAPMIX[key], int(np.random.default_rng(seed).integers(28, 75)), 100 - cash, seed)
                    rows.append(["TREPS", "", "", None, round(cash * 40, 2), round(cash - 0.4, 4), 6.1, "cash"])
                    rows.append(["Net Receivables / (Payables)", "", "", None, 20.0, 0.4, None, "cash"])
                    if "flexi" in sub and seed % 2:          # some flexi caps hold a little arbitrage
                        rows, shorts = add_arbitrage(rows, 2.5 + seed % 3, seed, split=bool(seed % 4 == 1))
                elif g == "Debt":
                    short = any(k in sub for k in ("overnight", "liquid", "money market", "ultra short", "low duration"))
                    rows = debt_rows(94, seed, short)
                    rows.append(["TREPS", "", "", None, 300.0, 6.0, 6.0, "cash"])
                elif g == "Hybrid":
                    arbw = 70 if "arbitrage" in sub else 25 if ("dynamic" in sub or "advantage" in sub) else 35 if "savings" in sub else 0
                    eqs = 70 if "aggressive" in sub else 35 if "balanced" in sub or "dynamic" in sub else 20
                    if "arbitrage" in sub:
                        eqs = 2
                    rows = equity_rows(CAPMIX["flexi cap"], 40, eqs, seed) + debt_rows(96 - eqs - arbw, seed)
                    rows.append(["TREPS", "", "", None, 200.0, 4.0, 6.0, "cash"])
                    if arbw:
                        rows, shorts = add_arbitrage(rows, arbw, seed, split=bool(seed % 2))
                else:
                    continue
                if m_i == 1:
                    rows = evolve(rows, seed + 1)
                kv = {"turnover": round(float(np.random.default_rng(seed).uniform(0.1, 1.2)), 2)} if g != "Debt" else \
                     {"moddur": round(float(np.random.default_rng(seed).uniform(0.1, 6)), 2)}
                schemes.append({"name": nm, "date": "2026-10-31" if m_i == 0 else "2026-09-30", "sheet": "S", "src": "sample.xlsx",
                                "rows": rows, "sum": 100.0, "kv": kv, "shorts": shorts})
                if m_i == 0 and g == "Equity" and ("mid cap" in sub or "small cap" in sub) and "large" not in sub:
                    r = np.random.default_rng(seed)
                    caps = {"L": 0.0, "M": 0.0, "S": 0.0}
                    for x in rows:
                        st = next((t for t in STOCKS if t["i"] == x[1]), None)
                        if st:
                            caps[st["c"]] += x[5]
                    small = "small cap" in sub
                    risk["Small Cap Fund" if small else "Mid Cap Fund"].append({
                        "mf": "", "amc": house, "name": nm.upper(), "aum": None,
                        "d50": int(r.integers(12, 45) if small else r.integers(4, 18)), "d25": int(r.integers(6, 22) if small else r.integers(2, 9)),
                        "top10inv": round(float(r.uniform(1.5, 6)), 2), "lc": round(caps["L"], 2), "mc": round(caps["M"], 2),
                        "sc": round(caps["S"], 2), "cash": round(float(r.uniform(1, 6)), 2), "sd": round(float(r.uniform(15, 21)), 2),
                        "bsd": 18.6 if not small else 20.9, "beta": round(float(r.uniform(0.82, 1.02)), 2),
                        "pe": round(float(r.uniform(26, 42)), 2), "bpe": 26.3 if not small else 24.8, "bpe1": 34.1, "bpe2": 42.9,
                        "turn": round(float(r.uniform(0.2, 1.1)), 2), "date": "01-Oct-2026"})
            slug = house.lower().replace(" ", "-")
            (mdir / f"{slug}.json").write_text(json.dumps({"amc": house + " Mutual Fund", "month": month, "files": ["sample.xlsx"],
                                                           "schemes": schemes}, separators=(",", ":")))
    (STORE / "risk_params.json").write_text(json.dumps({"source": "sample", "cats": {
        k: {"month": "2026-10", "schemes": v} for k, v in risk.items()}}))
    (STORE / "marketcap.json").write_text(json.dumps({"period": "Jan - June 2026 (sample)", "source": "sample",
                                                      "isin": {s["i"]: s["c"] for s in STOCKS}}))
    print(f"sample extras: portfolios for {sum(len(v) for v in by_house.values())} schemes, "
          f"risk parameters for {sum(len(v) for v in risk.values())} funds")


if __name__ == "__main__":
    main()
