"""Create SAMPLE data (fictional fund houses, simulated NAVs) for previewing the dashboard.

Never used once real AMFI data is available. Writes data/raw/*.json, data/raw/SAMPLE and the NAV store.
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import RAW, STORE_PATH, open_store, write_json  # noqa: E402

rng = np.random.default_rng(20261120)
START = dt.date(2006, 4, 3)
END = dt.date(2026, 11, 19)

days = np.arange(np.datetime64(START), np.datetime64(END) + np.timedelta64(1, "D"))
days = days[np.is_busday(days)]
N = len(days)
DT = 1 / 250


def idx(date_str):
    return int(np.searchsorted(days, np.datetime64(date_str)))


def overlay(arr, s, e, total_log):
    i0, i1 = idx(s), idx(e)
    if i1 > i0:
        arr[i0:i1] += total_log / (i1 - i0)


def path(mu, vol, shocks=()):
    r = rng.normal(mu * DT, vol * math.sqrt(DT), N)
    for s, e, tl in shocks:
        overlay(r, s, e, tl)
    return r


L = path(0.18, 0.15, [("2008-01-08", "2008-10-27", -0.95), ("2009-03-09", "2009-12-31", 0.45),
                       ("2011-01-01", "2011-12-20", -0.22), ("2015-03-01", "2016-02-29", -0.18),
                       ("2020-02-20", "2020-03-24", -0.50), ("2020-03-25", "2020-12-31", 0.35),
                       ("2021-10-19", "2022-06-17", -0.18), ("2024-09-27", "2025-03-04", -0.18)])
M = 1.05 * L + path(0.02, 0.09, [("2018-01-15", "2019-08-23", -0.30), ("2024-09-27", "2025-03-04", -0.08)])
S = 1.15 * L + path(0.02, 0.12, [("2018-01-15", "2019-08-23", -0.45), ("2024-09-27", "2025-03-04", -0.14),
                                 ("2020-04-01", "2021-12-31", 0.25)])
Dbt = path(0.068, 0.012, [("2013-07-15", "2013-08-30", -0.03), ("2020-03-10", "2020-04-10", -0.015),
                          ("2022-01-01", "2022-10-31", -0.035)])
Liq = path(0.064, 0.0015)
Gold = path(0.105, 0.15)
US = path(0.185, 0.18, [("2008-06-01", "2009-03-09", -0.55), ("2020-02-20", "2020-03-23", -0.30),
                       ("2022-01-01", "2022-10-12", -0.28)])

HOUSES = [f"Fund House {c}" for c in "ABCDEFGHIJKL"]

E, D, H, O = "Equity Scheme", "Debt Scheme", "Hybrid Scheme", "Other Scheme"
# (category, how many funds, exposure function, idiosyncratic vol, extra yearly return, regular TER range)
CATS = [
    (f"{E} - Large Cap Fund", 11, lambda: 0.95 * L, 0.035, 0.005, (1.5, 2.1)),
    (f"{E} - Large & Mid Cap Fund", 10, lambda: 0.5 * L + 0.5 * M, 0.045, 0.01, (1.6, 2.2)),
    (f"{E} - Mid Cap Fund", 11, lambda: M, 0.05, 0.015, (1.6, 2.2)),
    (f"{E} - Small Cap Fund", 11, lambda: S, 0.06, 0.02, (1.6, 2.3)),
    (f"{E} - Multi Cap Fund", 8, lambda: (L + M + S) / 3, 0.045, 0.012, (1.7, 2.3)),
    (f"{E} - Flexi Cap Fund", 12, lambda: 0.7 * L + 0.2 * M + 0.1 * S, 0.045, 0.01, (1.5, 2.2)),
    (f"{E} - Focused Fund", 9, lambda: 0.8 * L + 0.2 * M, 0.06, 0.008, (1.7, 2.3)),
    (f"{E} - Value Fund", 7, lambda: 0.9 * L + 0.1 * M, 0.05, 0.01, (1.7, 2.3)),
    (f"{E} - Contra Fund", 4, lambda: 0.85 * L + 0.15 * M, 0.05, 0.012, (1.7, 2.2)),
    (f"{E} - Dividend Yield Fund", 6, lambda: 0.85 * L, 0.045, 0.004, (1.8, 2.4)),
    (f"{E} - ELSS", 12, lambda: 0.75 * L + 0.15 * M + 0.1 * S, 0.045, 0.01, (1.5, 2.2)),
    (f"{E} - Sectoral Fund", 12, lambda: 1.0 * L, 0.13, 0.0, (1.8, 2.4)),
    (f"{E} - Thematic Fund", 12, lambda: 0.8 * L + 0.2 * M, 0.10, 0.004, (1.8, 2.4)),
    (f"{D} - Overnight Fund", 8, lambda: 0.95 * Liq - 0.004 * DT, 0.0005, 0.0, (0.1, 0.2)),
    (f"{D} - Liquid Fund", 12, lambda: Liq, 0.001, 0.002, (0.2, 0.35)),
    (f"{D} - Ultra Short Duration Fund", 9, lambda: 0.8 * Liq + 0.2 * Dbt, 0.003, 0.002, (0.5, 1.0)),
    (f"{D} - Low Duration Fund", 9, lambda: 0.65 * Liq + 0.35 * Dbt, 0.004, 0.003, (0.6, 1.1)),
    (f"{D} - Money Market Fund", 9, lambda: 0.75 * Liq + 0.25 * Dbt, 0.003, 0.003, (0.3, 0.7)),
    (f"{D} - Short Duration Fund", 9, lambda: 0.4 * Liq + 0.6 * Dbt, 0.006, 0.003, (0.8, 1.2)),
    (f"{D} - Medium Duration Fund", 6, lambda: Dbt, 0.008, 0.004, (1.0, 1.6)),
    (f"{D} - Medium to Long Duration Fund", 5, lambda: 1.3 * Dbt - 0.02 * DT, 0.01, 0.002, (1.2, 1.8)),
    (f"{D} - Long Duration Fund", 4, lambda: 1.9 * Dbt - 0.06 * DT, 0.012, 0.0, (0.6, 1.0)),
    (f"{D} - Dynamic Bond", 8, lambda: 1.5 * Dbt - 0.03 * DT, 0.01, 0.002, (1.0, 1.6)),
    (f"{D} - Corporate Bond Fund", 10, lambda: 0.8 * Dbt + 0.2 * Liq, 0.005, 0.004, (0.5, 0.9)),
    (f"{D} - Credit Risk Fund", 6, lambda: 0.7 * Dbt + 0.3 * Liq, 0.012, 0.012, (1.4, 1.9)),
    (f"{D} - Banking and PSU Fund", 9, lambda: 0.75 * Dbt + 0.25 * Liq, 0.005, 0.002, (0.5, 0.9)),
    (f"{D} - Gilt Fund", 8, lambda: 1.8 * Dbt - 0.05 * DT, 0.01, 0.0, (0.8, 1.3)),
    (f"{D} - Gilt Fund with 10 year constant duration", 3, lambda: 2.0 * Dbt - 0.065 * DT, 0.008, 0.0, (0.4, 0.7)),
    (f"{D} - Floater Fund", 5, lambda: 0.6 * Liq + 0.4 * Dbt, 0.004, 0.004, (0.5, 1.0)),
    (f"{D} - Sectoral Debt Fund", 3, lambda: 0.8 * Dbt + 0.2 * Liq, 0.005, 0.004, (0.5, 0.9)),
    (f"{H} - Conservative Hybrid Fund", 8, lambda: 0.2 * L + 0.8 * Dbt, 0.01, 0.0, (1.3, 2.0)),
    (f"{H} - Balanced Hybrid Fund", 3, lambda: 0.5 * L + 0.5 * Dbt, 0.02, 0.0, (1.6, 2.1)),
    (f"{H} - Aggressive Hybrid Fund", 10, lambda: 0.72 * L + 0.28 * Dbt, 0.03, 0.006, (1.5, 2.2)),
    (f"{H} - Dynamic Asset Allocation or Balanced Advantage", 10, lambda: 0.5 * L + 0.5 * Dbt, 0.025, 0.004, (1.5, 2.2)),
    (f"{H} - Multi Asset Allocation", 8, lambda: 0.5 * L + 0.3 * Dbt + 0.2 * Gold, 0.025, 0.006, (1.5, 2.1)),
    (f"{H} - Arbitrage Fund", 11, lambda: 1.03 * Liq, 0.004, 0.0, (0.8, 1.1)),
    (f"{H} - Equity Savings", 7, lambda: 0.35 * L + 0.65 * Liq, 0.015, 0.0, (1.0, 1.6)),
    ("Life Cycle Scheme - Life Cycle Fund", 4, lambda: 0.6 * L + 0.4 * Dbt, 0.02, 0.0, (1.0, 1.6)),
    (f"{O} - Index Funds", 0, None, 0, 0, (0, 0)),
    (f"{O} - Other ETFs", 0, None, 0, 0, (0, 0)),
    (f"{O} - Gold ETF", 0, None, 0, 0, (0, 0)),
    (f"{O} - FoF Overseas", 6, lambda: US, 0.03, -0.005, (0.9, 1.6)),
    (f"{O} - FoF Domestic", 5, lambda: 0.6 * L + 0.4 * Dbt, 0.02, 0.0, (0.6, 1.2)),
]

SECTORS = ["Banking and Financial Services", "Technology", "Pharma and Healthcare", "Infrastructure",
           "Consumption", "Energy", "PSU Equity", "Auto", "Manufacturing", "FMCG", "Realty", "Metals"]
THEMES = ["Business Cycle", "Innovation", "Quant", "ESG", "MNC", "Special Opportunities", "Momentum",
          "Rural Opportunities", "Defence", "Transportation and Logistics", "Digital India", "Housing"]

INDEX_FUNDS = [("Nifty 50 Index Fund", lambda: L, 0.1), ("Nifty Midcap 150 Index Fund", lambda: M, 0.2),
               ("Nifty Smallcap 250 Index Fund", lambda: S, 0.3), ("Nifty LargeMidcap 250 Index Fund", lambda: 0.5 * L + 0.5 * M, 0.25),
               ("Nifty 500 Index Fund", lambda: 0.75 * L + 0.15 * M + 0.1 * S, 0.25), ("Nifty Next 50 Index Fund", lambda: 1.1 * L, 0.3),
               ("Nifty 50 Equal Weight Index Fund", lambda: 1.02 * L, 0.35), ("Nifty G-Sec Index Fund", lambda: 1.7 * Dbt - 0.05 * DT, 0.15),
               ("BSE Sensex Index Fund", lambda: 0.98 * L, 0.15), ("Nasdaq 100 Index Fund", lambda: US, 0.4)]
ETFS = [("Nifty 50 ETF", lambda: L, 0.04, "Other ETFs"), ("Nifty Bank ETF", lambda: L + path(0.0, 0.08), 0.15, "Other ETFs"),
        ("Nifty IT ETF", lambda: 0.8 * L + path(0.02, 0.12), 0.2, "Other ETFs"), ("Midcap 150 ETF", lambda: M, 0.15, "Other ETFs"),
        ("Gold ETF", lambda: Gold, 0.5, "Gold ETF"), ("Silver ETF", lambda: path(0.09, 0.25), 0.5, "Other ETFs")]

BENCH = {"Large Cap": "Nifty 100 TRI", "Large & Mid": "Nifty LargeMidcap 250 TRI", "Mid Cap": "Nifty Midcap 150 TRI",
         "Small Cap": "Nifty Smallcap 250 TRI", "Multi Cap": "Nifty 500 Multicap 50:25:25 TRI", "Liquid": "CRISIL Liquid Debt A-I Index",
         "Gilt": "CRISIL Dynamic Gilt Index"}


def sim(base, idio, alpha, start_i):
    r = base.copy() + rng.normal(alpha * DT, idio * math.sqrt(DT), N)
    r[:start_i] = 0
    return r


def main():
    if STORE_PATH.exists():
        STORE_PATH.unlink()
    for ext in ("-wal", "-shm"):
        p = Path(str(STORE_PATH) + ext)
        if p.exists():
            p.unlink()
    con = open_store()
    latest, ter_rows, aum_rows, perf_rows = [], [], [], []
    code = 300000
    dstart_direct = idx("2013-01-01")
    day_nums = days.astype(np.int64)

    def add(cat, house, name, logret_gross, start_i, ter_reg, ter_dir, single=False, aum=None, bench=None):
        nonlocal code
        plans = [("", ter_reg)] if single else [(" - Regular Plan - Growth", ter_reg), (" - Direct Plan - Growth", ter_dir)]
        for suffix, ter in plans:
            si = start_i if (single or "Regular" in suffix) else max(start_i, dstart_direct)
            if si >= N - 30:
                continue
            code += 1
            lr = logret_gross[si:] - ter / 100 * DT
            nav = 10.0 * np.exp(np.cumsum(lr))
            if "Direct" in suffix and si > start_i:
                reg_lr = logret_gross[start_i:si] - ter_reg / 100 * DT
                nav = nav * 10.0 * math.exp(reg_lr.sum()) / 10.0
            full = f"{house} {name}{suffix}"
            con.executemany("INSERT INTO nav VALUES (?,?,?)",
                            zip([code] * len(nav), day_nums[si:].tolist(), np.round(nav, 4).tolist()))
            latest.append({"code": code, "name": full, "amc": f"{house} Mutual Fund", "category": cat, "type": "Open Ended",
                           "isin": "", "isin2": "", "nav": round(float(nav[-1]), 4), "date": END.isoformat()})
            plan = "D" if "Direct" in suffix else ("R" if suffix else None)
            ter_rows.append({"name": full, "amc": f"{house} Mutual Fund", "plan": plan, "ter": round(ter, 2), "date": None, "isin": None})
            if bench:
                perf_rows.append({"name": full, "code": str(code), "amc": f"{house} Mutual Fund", "benchmark": bench})
        if aum is not None:
            aum_rows.append({"name": f"{house} {name}", "amc": f"{house} Mutual Fund", "aum": aum})

    for cat, n, fn, idio, alpha, (t0, t1) in CATS:
        if n == 0:
            continue
        houses = list(rng.choice(HOUSES, size=min(n, len(HOUSES)), replace=False))
        while len(houses) < n:
            houses.append(rng.choice(HOUSES))
        sub = cat.split(" - ", 1)[1]
        bench = next((b for k, b in BENCH.items() if sub.startswith(k)), None)
        for j, house in enumerate(houses):
            if "Life Cycle" in cat:
                si = idx(f"2026-0{5 + j % 3}-01")
                name = f"Life Cycle Fund {2035 + 5 * j}"
            else:
                si = 0 if rng.random() < 0.35 else idx(f"{rng.integers(2006, 2025)}-{rng.integers(1, 13):02d}-01")
                if "Sectoral" in cat and "Debt" not in cat:
                    name = f"{SECTORS[j % len(SECTORS)]} Fund"
                elif "Thematic" in cat:
                    name = f"{THEMES[j % len(THEMES)]} Fund"
                elif "FoF Overseas" in cat:
                    name = ["US Equity FoF", "Global Innovation FoF", "Asia Pacific FoF", "Emerging Markets FoF",
                            "Global Equity Opportunities FoF", "Nasdaq 100 FoF"][j % 6]
                elif "FoF Domestic" in cat:
                    name = ["Asset Allocator FoF", "Multi Manager FoF", "Gold FoF", "Passive Hybrid FoF", "Income Plus FoF"][j % 5]
                else:
                    name = sub if sub.endswith("Fund") else f"{sub} Fund"
            base = fn()
            if "Gold FoF" in name:
                base = Gold
            if "Credit Risk" in cat and j < 2:
                base = base.copy()
                base[idx("2019-06-04")] -= 0.07       # a credit event
            ter_reg = float(rng.uniform(t0, t1))
            ter_dir = max(0.08, ter_reg - float(rng.uniform(0.35, 0.9)) * min(1.0, ter_reg / 1.5))
            size = float(np.exp(rng.normal(8.3 if "Equity" in cat else 8.0, 1.3)))
            if rng.random() < 0.08:
                size = float(rng.uniform(15, 95))
            add(cat, house, name, sim(base, idio, alpha, si), si, ter_reg, ter_dir, aum=round(size, 0), bench=bench)

    for j, (nm, fn, ter) in enumerate(INDEX_FUNDS):
        for house in rng.choice(HOUSES, size=2, replace=False):
            si = idx(f"{rng.integers(2010, 2022)}-0{rng.integers(1, 10)}-01") if "Nifty 50 " not in nm else idx("2010-03-01")
            add(f"{O} - Index Funds", house, nm, sim(fn(), 0.004, 0, si), si, ter + 0.3, ter,
                aum=round(float(np.exp(rng.normal(7.5, 1.2))), 0))
    for nm, fn, ter, cat in ETFS:
        for house in rng.choice(HOUSES, size=2, replace=False):
            si = idx(f"{rng.integers(2007, 2020)}-0{rng.integers(1, 10)}-01")
            add(f"{O} - {cat}", house, nm, sim(fn(), 0.003, 0, si), si, ter, ter, single=True,
                aum=round(float(np.exp(rng.normal(7.8, 1.3))), 0))
    con.commit()
    con.close()

    # benchmark returns from the simulated index paths
    def bret(series, yrs):
        i = idx((END - dt.timedelta(days=int(365.25 * yrs))).isoformat())
        tot = series[i:].sum()
        return round((math.exp(tot) ** (1 / yrs) - 1) * 100, 1)

    bseries = {"Nifty 100 TRI": L, "Nifty LargeMidcap 250 TRI": 0.5 * L + 0.5 * M, "Nifty Midcap 150 TRI": M,
               "Nifty Smallcap 250 TRI": S, "Nifty 500 Multicap 50:25:25 TRI": (L + M + S) / 3,
               "CRISIL Liquid Debt A-I Index": Liq, "CRISIL Dynamic Gilt Index": 1.8 * Dbt}
    for r in perf_rows:
        s = bseries[r["benchmark"]]
        r.update({"bm1": bret(s, 1), "bm3": bret(s, 3), "bm5": bret(s, 5)})

    RAW.mkdir(parents=True, exist_ok=True)
    write_json(RAW / "latest.json", latest)
    write_json(RAW / "ter.json", {"month": "11-2026 (sample)", "rows": ter_rows})
    write_json(RAW / "aum.json", {"period": "Jul - Sep 2026 (sample)", "rows": aum_rows})
    write_json(RAW / "perf.json", {"date": END.isoformat(), "rows": perf_rows})
    (RAW / "SAMPLE").write_text("sample data: fictional fund houses, simulated NAVs\n")
    print(f"sample: {len(latest)} schemes, {N} trading days")


if __name__ == "__main__":
    main()
