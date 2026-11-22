"""Turn the AMFI NAV store into the dashboard's data files.

  python scripts/build_data.py            -> site/data/funds.json + site/data/d/<category>-<plan>.json

All figures are computed from AMFI NAVs (growth options). Expense ratio, fund size and
benchmark returns are attached from AMFI's own disclosures when available.
"""
from __future__ import annotations

import datetime as dt
import math
import re
import sys
from collections import defaultdict
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from common import (DATA, RAW, SITE_DATA, amc_key, asset_kind, clean_name, dedupe_key, group_of,  # noqa: E402
                    horizon_for, is_legacy, match_key, num_day, open_store, plan_of, read_json,
                    sub_name, sub_rank, write_json, GROUP_ORDER)
import ratios as RT  # noqa: E402
from namematch import best_match  # noqa: E402
from portfolio_build import attach as attach_portfolios  # noqa: E402

PLANS_SHOWN = ("R",)                   # NS Financial Services shows regular plans only

YEAR = 365.25
WINDOWS = [1, 2, 3, 5, 7, 10]          # rolling windows computed (years)
CHART_WINDOWS = [1, 3, 5, 7, 10]       # rolling windows shown in charts
SIP_YEARS = [3, 5, 10]
PCTS = list(range(0, 101, 5))

EVENTS = [
    {"n": "2008 global crisis", "s": "2008-01-01", "e": "2009-03-31"},
    {"n": "2018-19 mid and small cap fall", "s": "2018-01-01", "e": "2019-12-31"},
    {"n": "2020 Covid crash", "s": "2020-01-15", "e": "2020-04-30"},
    {"n": "2021-22 fall", "s": "2021-10-15", "e": "2022-06-30"},
    {"n": "2024-25 correction", "s": "2024-09-25", "e": "2025-04-30"},
]

INDEX_PROXY = [
    ("large & mid", r"large\s*mid\s*cap\s*250|largemidcap\s*250"),
    ("large and mid", r"large\s*mid\s*cap\s*250|largemidcap\s*250"),
    ("large cap", r"\bnifty\s*50\b(?!\s*equal)|\bsensex\b"),
    ("mid cap", r"midcap\s*150"),
    ("small cap", r"smallcap\s*250"),
    ("multi cap", r"multicap\s*50|nifty\s*500\b"),
    ("", r"nifty\s*500\b"),                      # other equity categories
]
PROXY_AVOID = re.compile(r"equal|next|value|momentum|quality|alpha|low vol|shariah|esg|arbitrage|"
                         r"smallcap\s*50\b|midcap\s*50\b|etf|top\s*10|dividend|multicap\s*50.*momentum", re.I)


# --------------------------------------------------------------------------- series helpers

def clean_series(d: np.ndarray, v: np.ndarray, etf: bool):
    """Drop bad points; undo one-day spikes; treat lasting jumps as unit splits."""
    ok = np.isfinite(v) & (v > 0)
    d, v = d[ok], v[ok]
    if len(d) < 2:
        return d, v, 0
    order = np.argsort(d, kind="stable")
    d, v = d[order], v[order]
    keep = np.r_[True, np.diff(d) > 0]
    d, v = d[keep], v[keep]
    fixes = 0
    lim_up, lim_dn = (math.log(1.6), math.log(0.62))
    i = 1
    v = v.copy()
    while i < len(v):
        r = math.log(v[i] / v[i - 1])
        if r > lim_up or r < lim_dn:
            if i + 1 < len(v) and abs(math.log(v[i + 1] / v[i - 1])) < 0.1:
                v[i] = v[i - 1]          # one-day spike in AMFI data
            else:
                v[:i] *= v[i] / v[i - 1]  # lasting jump: split / re-denomination
            fixes += 1
        i += 1
    return d, v, fixes


def months_of(d: np.ndarray) -> np.ndarray:
    return d.astype("datetime64[D]").astype("datetime64[M]").astype(np.int64)


def cagr(v1, v0, days):
    with np.errstate(divide="ignore", invalid="ignore"):
        return (np.power(v1 / v0, YEAR / days) - 1) * 100


def rolling(d, v, years):
    """Annualised return for every end date that has a start date exactly `years` before it."""
    span = years * YEAR
    tgt = d - span
    i = np.searchsorted(d, tgt, side="right") - 1
    ok = (i >= 0)
    out = np.full(len(d), np.nan)
    if not ok.any():
        return out
    ii = np.where(ok, i, 0)
    gap = (d - d[ii]) - span
    ok &= (gap >= 0) & (gap <= 10)
    days = (d - d[ii]).astype(float)
    if years == 1:
        val = (v / v[ii] - 1) * 100
    else:
        val = cagr(v, v[ii], days)
    out[ok] = val[ok]
    return out


def trailing(d, v, years):
    t = d[-1] - years * YEAR
    i = np.searchsorted(d, t, side="right") - 1
    if i < 0 or (t - d[i]) > 10:
        return None
    if years == 1:
        return float((v[-1] / v[i] - 1) * 100)
    return float(cagr(v[-1], v[i], float(d[-1] - d[i])))


def drawdown_info(d, v, start_idx=0):
    sv = v[start_idx:]
    sd = d[start_idx:]
    if len(sv) < 5:
        return None
    rm = np.maximum.accumulate(sv)
    dd = sv / rm - 1
    k = int(np.argmin(dd))
    if dd[k] >= 0:
        return {"fall": 0.0, "peak": int(sd[0]), "trough": int(sd[0]), "rec": int(sd[0]), "rdays": 0}
    p = int(np.argmax(sv[: k + 1]))
    after = np.nonzero(sv[k:] >= rm[k])[0]
    rec = int(sd[k + after[0]]) if len(after) else None
    return {"fall": float(dd[k] * 100), "peak": int(sd[p]), "trough": int(sd[k]), "rec": rec,
            "rdays": (rec - int(sd[k])) if rec is not None else None}


def sip_xirr(navs_first: np.ndarray, months: int):
    """Monthly SIP of 1 on the first NAV of each month; value on the first NAV `months` later.
    Returns annualised XIRR (%) for every possible start month."""
    n = len(navs_first)
    if n < months + 1:
        return np.array([])
    inv = np.cumsum(1.0 / navs_first)
    inv = np.r_[0.0, inv]
    starts = np.arange(0, n - months)
    units = inv[starts + months] - inv[starts]
    F = units * navs_first[starts + months]                   # value of SIP of 1 per month
    e = np.arange(months, 0, -1, dtype=float)                 # months each instalment compounds
    r = np.full(len(starts), 0.008)
    for _ in range(60):
        g = np.power(1 + r[:, None], e[None, :])
        f = g.sum(1) - F
        fp = (e[None, :] * g / (1 + r[:, None])).sum(1)
        step = f / fp
        r = np.clip(r - step, -0.2, 0.2)
        if np.nanmax(np.abs(step)) < 1e-10:
            break
    return (np.power(1 + r, 12) - 1) * 100


def r1(x, nd=1):
    if x is None:
        return None
    try:
        if not math.isfinite(x):
            return None
    except TypeError:
        return None
    return round(float(x), nd)


def sig4(x):
    if not (x and math.isfinite(x)):
        return None
    return float(f"{x:.4g}")


def iso(day):
    return num_day(day).isoformat() if day is not None else None


# --------------------------------------------------------------------------- per-fund metrics

def fund_metrics(d, v, H, m_grid0):
    out = {}
    det = {}
    last = d[-1]
    out["r1"] = r1(trailing(d, v, 1))
    out["r3"] = r1(trailing(d, v, 3))
    out["r5"] = r1(trailing(d, v, 5))
    out["r10"] = r1(trailing(d, v, 10))

    mon = months_of(d)
    # last NAV of each month (for charts) and first NAV of each month (for SIPs)
    chg = np.r_[np.nonzero(np.diff(mon))[0], len(mon) - 1]
    firsts = np.r_[0, chg[:-1] + 1]
    m_last = mon[chg]
    det["m0"] = int(m_last[0] - m_grid0)
    # fill month gaps so index == month offset
    full_m = np.arange(m_last[0], m_last[-1] + 1)
    pos = np.searchsorted(m_last, full_m, side="right") - 1
    last_idx = chg[pos]
    first_idx = firsts[np.clip(np.searchsorted(m_last, full_m), 0, len(firsts) - 1)]

    det["nav"] = [sig4(x) for x in v[last_idx]]
    raw_month_nav = v[last_idx].astype(float)

    rr = {}
    pc = {}
    lo = {}
    monthly_r = {}
    for w in WINDOWS:
        roll = rolling(d, v, w)
        good = roll[np.isfinite(roll)]
        if len(good) >= 20:
            pc[str(w)] = [r1(x) for x in np.percentile(good, PCTS)]
            lo[str(w)] = r1(float((good < 0).mean() * 100))
            if w == H:
                out["rh"] = r1(float(np.median(good)))
                out["rhLo"] = r1(float(good.min()))
                out["rhHi"] = r1(float(good.max()))
                out["ls"] = lo[str(w)]
            if w == 5:
                out["r5m"] = r1(float(np.median(good)))
            if w in CHART_WINDOWS:
                out[f"rr{w}"] = r1(float(np.median(good)))
        ms = roll[last_idx]
        monthly_r[w] = ms
        if w in CHART_WINDOWS:
            fin = np.nonzero(np.isfinite(ms))[0]
            if len(fin):
                o = int(fin[0])
                rr[str(w)] = [det["m0"] + o, [r1(x) for x in ms[o:]]]
    det["rr"], det["pc"], det["lo"] = rr, pc, lo

    # falls
    rm = np.maximum.accumulate(v)
    dd = v / rm - 1
    mins = np.minimum.reduceat(dd, firsts)
    det["dd"] = [r1(x * 100) for x in mins[pos]]
    allinfo = drawdown_info(d, v)
    s5 = int(np.searchsorted(d, last - 5 * YEAR))
    info5 = drawdown_info(d, v, s5)
    out["ddA"] = r1(allinfo["fall"]) if allinfo else None
    if info5 and (last - d[0]) >= 1 * YEAR:
        out["dd"] = r1(info5["fall"])
        out["rc"] = info5["rdays"]
        if info5["rec"] is None:
            out["nr"] = 1
    out["cur"] = r1(float((v[-1] / v.max() - 1) * 100))
    det["ddi"] = {}
    for key, inf in (("all", allinfo), ("5y", info5)):
        if inf:
            det["ddi"][key] = [iso(inf["peak"]), iso(inf["trough"]), iso(inf["rec"]), r1(inf["fall"])]

    # volatility (3y, annualised, daily NAV changes)
    s3 = int(np.searchsorted(d, last - 3 * YEAR))
    if len(v) - s3 > 200:
        lr = np.diff(np.log(v[s3:]))
        out["vol"] = r1(float(lr.std() * math.sqrt(250) * 100))

    # calendar years
    yrs = []
    years = d.astype("datetime64[D]").astype("datetime64[Y]").astype(int) + 1970
    for y in range(int(years[0]) + 1, int(years[-1]) + 1):
        i0 = np.nonzero(years == y - 1)[0]
        i1 = np.nonzero(years == y)[0]
        if not len(i0) or not len(i1):
            continue
        dec = d[i0[-1]]
        if (num_day(int(dec)) - dt.date(y - 1, 12, 31)).days < -12:
            continue
        ret = (v[i1[-1]] / v[i0[-1]] - 1) * 100
        yrs.append([y, r1(ret)])
    det["yr"] = yrs

    # crash replay
    ev = []
    for k, e in enumerate(EVENTS):
        s = np.datetime64(e["s"]).astype(np.int64)
        en = np.datetime64(e["e"]).astype(np.int64)
        if d[0] > s + 7 or last < en:
            continue
        i0 = int(np.searchsorted(d, s))
        i1 = int(np.searchsorted(d, en, side="right")) - 1
        sub = v[i0: i1 + 1]
        if len(sub) < 5:
            continue
        rmw = np.maximum.accumulate(sub)
        ddw = sub / rmw - 1
        kk = int(np.argmin(ddw))
        aft = np.nonzero(v[i0 + kk:] >= rmw[kk])[0]
        rdays = int(d[i0 + kk + aft[0]] - d[i0 + kk]) if len(aft) else -1
        ev.append([k, r1(float(ddw[kk] * 100)), rdays])
    det["ev"] = ev

    # SIP range
    nf = v[first_idx]
    sip = {}
    for y in SIP_YEARS:
        x = sip_xirr(nf, y * 12)
        x = x[np.isfinite(x)]
        if len(x) >= 6:
            sip[str(y)] = [r1(float((x < 0).mean() * 100)), int(len(x)), [r1(q) for q in np.percentile(x, PCTS)]]
    det["sip"] = sip
    return out, det, monthly_r, full_m, raw_month_nav


# --------------------------------------------------------------------------- disclosures matching

def index_rows(rows, plan_field=True):
    by_isin, by_key, by_name = {}, {}, {}
    for r in rows or []:
        val = r
        if r.get("isin"):
            by_isin[str(r["isin"]).strip().upper()] = val
        name = r.get("name") or ""
        plan = r.get("plan")
        if plan is None and plan_field:
            low = name.lower()
            plan = "D" if "direct" in low else ("R" if "regular" in low else None)
        k = match_key(name)
        by_key[(amc_key(r.get("amc") or ""), k, plan)] = val
        by_name[(k, plan)] = val
    return by_isin, by_key, by_name


def lookup(idx, s, plan):
    by_isin, by_key, by_name = idx
    for i in (s.get("isin"), s.get("isin2")):
        if i and str(i).strip().upper() in by_isin:
            return by_isin[str(i).strip().upper()], "s"
    k = match_key(s["name"])
    a = amc_key(s["amc"])
    for p, lvl in ((plan, "s"), (None, "f")):
        if (a, k, p) in by_key:
            return by_key[(a, k, p)], lvl
        if (k, p) in by_name:
            return by_name[(k, p)], lvl
    return None, None


# --------------------------------------------------------------------------- main build

def main():
    latest = read_json(RAW / "latest.json")
    if not latest:
        raise SystemExit("No data/raw/latest.json. Run amfi_fetch.py daily (or make_sample.py) first.")
    sample = (RAW / "SAMPLE").exists()
    ter = (read_json(RAW / "ter.json") or {})
    aum = (read_json(RAW / "aum.json") or {})
    perf = (read_json(RAW / "perf.json") or {})
    ter_idx = index_rows(ter.get("rows"))
    aum_idx = index_rows(aum.get("rows"))
    perf_rows = perf.get("rows") or []
    perf_idx = index_rows(perf_rows)
    perf_code = {str(r.get("code")): r for r in perf_rows if r.get("code")}

    data_date = max(dt.date.fromisoformat(s["date"]) for s in latest)

    # ---- universe: open-ended growth options, one per fund and plan
    cand = {}
    for s in latest:
        g = group_of(s["category"])
        p = plan_of(s["name"], g)
        if not p or p not in PLANS_SHOWN:
            continue
        if (data_date - dt.date.fromisoformat(s["date"])).days > 30:
            continue
        key = (s["amc"], s["category"], dedupe_key(s["name"]), p)
        score = (0 if is_legacy(s["name"]) else 4) + (2 if re.search(r"regular|direct", s["name"], re.I) else 0) \
            + (1 if s["date"] == data_date.isoformat() else 0)
        if key not in cand or score > cand[key][0] or (score == cand[key][0] and len(s["name"]) < len(cand[key][1]["name"])):
            cand[key] = (score, s)
    schemes = [s for _, s in cand.values()]

    # ---- categories (index funds, ETFs and FoFs are split by asset type so like is ranked with like)
    KLAB = {"e": "equity", "d": "debt", "o": "gold, silver and global"}
    kinds_in = defaultdict(set)
    for s in schemes:
        g = group_of(s["category"])
        s["_g"], s["_sub"] = g, sub_name(s["category"])
        s["_kind"] = asset_kind(g, s["_sub"], s["name"])
        kinds_in[s["category"]].add(s["_kind"])
    for s in schemes:
        s["_cat"], s["_cn"] = s["category"], s["_sub"]
        if s["_g"] in ("Index funds", "ETFs", "Fund of funds") and len(kinds_in[s["category"]]) > 1:
            lab = KLAB[s["_kind"][0]] if s["_g"] != "Fund of funds" or s["_kind"] != "other" else "other"
            s["_cat"] = f"{s['category']} | {s['_kind']}"
            s["_cn"] = f"{s['_sub']}: {lab}"
    cat_ids = {}
    cats = []
    for s in schemes:
        if s["_cat"] not in cat_ids:
            cat_ids[s["_cat"]] = None
            cats.append({"raw": s["_cat"], "g": s["_g"], "n": s["_cn"], "b": s["_sub"],
                         "ko": {"equity": 0, "debt": 1, "other": 2}[s["_kind"]] if s["_cat"] != s["category"] else 0})
    cats.sort(key=lambda c: (GROUP_ORDER.index(c["g"]) if c["g"] in GROUP_ORDER else 99, sub_rank(c["g"], c["b"]), c["b"], c["ko"]))
    for i, c in enumerate(cats):
        cat_ids[c["raw"]] = i
        c["id"] = re.sub(r"[^a-z0-9]+", "-", c["raw"].lower()).strip("-")

    amcs = sorted({s["amc"] for s in schemes}, key=lambda a: a.lower())
    amc_ids = {a: i for i, a in enumerate(amcs)}

    con = open_store()
    rows_min = con.execute("SELECT MIN(d) FROM nav").fetchone()[0]
    if rows_min is None:
        raise SystemExit("NAV store is empty.")
    m_grid0 = int(months_of(np.array([rows_min]))[0])

    funds, details, monthly, month_nav = [], {}, {}, {}
    fixes_total = 0
    for s in schemes:
        code = int(s["code"])
        res = con.execute("SELECT d, nav FROM nav WHERE code=? ORDER BY d", (code,)).fetchall()
        if len(res) < 30:
            continue
        arr = np.array(res, dtype=float)
        d = arr[:, 0].astype(np.int64)
        v = arr[:, 1]
        g = group_of(s["category"])
        p = plan_of(s["name"], g)
        d, v, fx = clean_series(d, v, etf=(g == "ETFs"))
        fixes_total += fx
        if len(d) < 30:
            continue
        sub = sub_name(s["category"])
        kind = asset_kind(g, sub, s["name"])
        H = horizon_for(g, sub, kind)
        m, det, mr, full_m, mnav = fund_metrics(d, v, H, m_grid0)
        f = {"id": code, "n": clean_name(s["name"]), "a": amc_ids[s["amc"]], "c": cat_ids[s["_cat"]], "p": p,
             "nav": sig4(v[-1]), "nd": iso(int(d[-1])), "st": iso(int(d[0])),
             "age": r1((d[-1] - d[0]) / YEAR), "k": kind[0], "H": H, **m}
        if s.get("isin"):
            f["isin"] = s["isin"]
        t, _ = lookup(ter_idx, s, p if p != "E" else None)
        if t and t.get("ter") is not None:
            f["ter"] = r1(t["ter"], 2)
        a_, lvl = lookup(aum_idx, s, p if p != "E" else None)
        if a_ and a_.get("aum") is not None:
            f["aum"] = r1(a_["aum"], 0)
            f["al"] = lvl
        pr = perf_code.get(str(code)) or lookup(perf_idx, s, p if p != "E" else None)[0]
        if pr:
            if pr.get("benchmark"):
                f["bm"] = str(pr["benchmark"])[:90]
            for kk in ("bm1", "bm3", "bm5"):
                if pr.get(kk) is not None:
                    f["b" + kk[-1]] = r1(pr[kk])
            if "aum" not in f and pr.get("aum"):
                f["aum"] = r1(pr["aum"], 0)
                f["al"] = "s"
        funds.append(f)
        details[code] = det
        monthly[code] = (mr, int(full_m[0] - m_grid0))
        month_nav[code] = (int(full_m[0] - m_grid0), mnav)

    con.close()
    last_day = np.datetime64(data_date.isoformat()).astype(np.int64)
    n_months = int(months_of(np.array([last_day]))[0]) - m_grid0 + 1

    # ---- category-level: medians, beat rates, quartiles, crash medians, costs
    groups = defaultdict(list)
    for f in funds:
        groups[(f["c"], f["p"])].append(f)

    def grid_series(code, w):
        mr, off = monthly[code]
        s = np.full(n_months, np.nan)
        x = mr.get(w)
        if x is None:
            return s
        s[off: off + len(x)] = x[: n_months - off]
        return s

    cat_payload = {}
    for (c, p), fl in groups.items():
        H = fl[0]["H"]
        med = {}
        for w in CHART_WINDOWS + ([H] if H not in CHART_WINDOWS else []):
            M = np.vstack([grid_series(f["id"], w) for f in fl])
            cnt = np.isfinite(M).sum(0)
            mdn = np.full(n_months, np.nan)
            cols = np.nonzero(cnt >= 3)[0]
            if len(cols):
                mdn[cols] = np.nanmedian(M[:, cols], axis=0)
            fin = np.nonzero(np.isfinite(mdn))[0]
            if len(fin):
                med[str(w)] = [int(fin[0]), [r1(x) for x in mdn[fin[0]: fin[-1] + 1]]]
            if w == H:
                for f, row in zip(fl, M):
                    both = np.isfinite(row) & np.isfinite(mdn)
                    if both.sum() >= 12:
                        f["bt"] = r1(float((row[both] > mdn[both]).mean() * 100), 0)
        # calendar-year quartiles
        by_year = defaultdict(list)
        for f in fl:
            for y, ret in details[f["id"]]["yr"]:
                if ret is not None:
                    by_year[y].append((ret, f["id"]))
        yq = {}
        for y, lst in by_year.items():
            if len(lst) >= 4:
                lst.sort(reverse=True)
                n = len(lst)
                for rank, (_, fid) in enumerate(lst):
                    yq[(y, fid)] = 1 + int(4 * rank / n)
        ymed = {y: r1(float(np.median([r for r, _ in lst]))) for y, lst in by_year.items() if len(lst) >= 3}
        for f in fl:
            det = details[f["id"]]
            det["yr"] = [[y, ret, yq.get((y, f["id"]))] for y, ret in det["yr"]]
        # crash medians
        ev_vals = defaultdict(list)
        for f in fl:
            for k, fall, _ in details[f["id"]]["ev"]:
                ev_vals[k].append(fall)
        ev_med = {k: r1(float(np.median(x))) for k, x in ev_vals.items() if len(x) >= 3}
        # cost
        ters = [f["ter"] for f in fl if f.get("ter") is not None]
        ter_med = float(np.median(ters)) if len(ters) >= 4 else None
        ter_p75 = float(np.percentile(ters, 75)) if len(ters) >= 4 else None
        for f in fl:
            flags = []
            if f["age"] is not None and f["age"] < max(3, H):
                flags.append("young")
            if f.get("aum") is not None and f["aum"] < 100:
                flags.append("small")
            if ter_med is not None and f.get("ter") is not None and f["ter"] > ter_p75 and f["ter"] - ter_med >= 0.25:
                flags.append("cost")
            if f.get("cur") is not None and f["cur"] <= -15:
                flags.append("below")
            if flags:
                f["fl"] = flags
        cat_payload[(c, p)] = {"med": med, "ym": ymed, "em": ev_med,
                               "tm": r1(ter_med, 2) if ter_med is not None else None, "H": H, "count": len(fl)}

    # ---- index fund stand-ins for equity categories
    idx_funds = [f for f in funds if cats[f["c"]]["g"] == "Index funds" and f["k"] == "e"]
    for (c, p), payload in cat_payload.items():
        cat = cats[c]
        if cat["g"] != "Equity":
            continue
        sl = cat["n"].lower()
        pat = None
        for key, rx in INDEX_PROXY:
            if key in sl:
                pat = rx
                break
        cands = [f for f in idx_funds if re.search(pat, f["n"], re.I) and not PROXY_AVOID.search(f["n"])] if pat else []
        if not cands and pat != INDEX_PROXY[2][1]:
            cands = [f for f in idx_funds if re.search(INDEX_PROXY[2][1], f["n"], re.I) and not PROXY_AVOID.search(f["n"])]
        if not cands:
            continue
        cands.sort(key=lambda f: (f["st"], 0 if f["p"] == p else 1, 0 if f["p"] == "D" else 1))
        px = cands[0]
        H = payload["H"]
        prr = {}
        for w in CHART_WINDOWS:
            s = grid_series(px["id"], w)
            fin = np.nonzero(np.isfinite(s))[0]
            if len(fin):
                prr[str(w)] = [int(fin[0]), [r1(x) for x in s[fin[0]: fin[-1] + 1]]]
        payload["px"] = {"id": px["id"], "n": px["n"] + (" (Direct)" if px["p"] == "D" else " (Regular)"), "rr": prr}
        ps = grid_series(px["id"], H)
        for f in groups[(c, p)]:
            if f["id"] == px["id"]:
                continue
            row = grid_series(f["id"], H)
            both = np.isfinite(row) & np.isfinite(ps)
            if both.sum() >= 12:
                f["bi"] = r1(float((row[both] > ps[both]).mean() * 100), 0)
        payload["pxH"] = H

    # ---- key ratios from month-end NAVs (risk-free = overnight funds, market = the category's index fund)
    def nav_grid(code):
        off, arr = month_nav[code]
        g_ = np.full(n_months, np.nan)
        g_[off: off + len(arr)] = arr[: n_months - off]
        return g_

    def mret(a):
        r_ = np.full(len(a), np.nan)
        with np.errstate(divide="ignore", invalid="ignore"):
            r_[1:] = a[1:] / a[:-1] - 1
        return r_

    ratio_end = n_months - 1 if data_date.day >= 28 else n_months - 2      # last complete month
    on_ids = [f["id"] for f in funds if cats[f["c"]]["g"] == "Debt" and "overnight" in cats[f["c"]]["n"].lower()]
    rf = None
    if on_ids:
        with np.errstate(all="ignore"):
            rf = np.nanmedian(np.vstack([mret(nav_grid(i)) for i in on_ids]), axis=0)
    if rf is None or not np.isfinite(rf[max(0, ratio_end - 35): ratio_end + 1]).all():
        print("ratios: overnight funds missing for part of the window; using 0.5% a month there")
    rf = np.where(np.isfinite(rf), rf, 0.005) if rf is not None else np.full(n_months, 0.005)
    rf_ann = float((np.prod(1 + rf[ratio_end - 35: ratio_end + 1]) - 1) * 100 / 3) if ratio_end >= 35 else None
    for (c, p), payload in cat_payload.items():
        px = payload.get("px")
        br = mret(nav_grid(px["id"])) if px else None
        per = {"3": defaultdict(list), "5": defaultdict(list)}
        for f in groups[(c, p)]:
            fr = mret(nav_grid(f["id"]))
            use_b = br if (px and f["id"] != px["id"]) else None
            res = RT.fund_ratios(fr, rf, use_b, ratio_end)
            if not res:
                continue
            details[f["id"]]["ra"] = res
            f.update(RT.summary_fields(res))
            for w, vals in res.items():
                for k, v in vals.items():
                    if v is not None:
                        per[w][k].append(v)
        payload["rmed"] = {w: {k: r1(float(np.median(v)), 2) for k, v in d.items() if len(v) >= 3} for w, d in per.items() if d}

    # ---- AMFI risk parameters (mid cap and small cap funds only)
    risk = read_json(DATA / "store" / "risk_params.json") or {}
    risk_month = None
    for cname, block in (risk.get("cats") or {}).items():
        risk_month = block.get("month")
        sl = cname.lower().split(" fund")[0]
        cat_is = [i for i, c in enumerate(cats) if c["g"] == "Equity" and sl in c["n"].lower()]
        pool = [f for f in funds if f["c"] in cat_is]
        for rec in block.get("schemes", []):
            cands = {f["id"]: f["n"] for f in pool if amc_key(amcs[f["a"]]) == amc_key(rec.get("amc") or "")}
            fid, _ = best_match(rec.get("name") or "", cands, rec.get("amc"), threshold=0.7)
            if fid is None:
                continue
            details[fid]["rp"] = {k: rec.get(k) for k in ("d50", "d25", "top10inv", "lc", "mc", "sc", "cash", "sd", "bsd",
                                                         "beta", "pe", "bpe", "bpe1", "bpe2", "turn") if rec.get(k) is not None}
            details[fid]["rp"]["m"] = block.get("month")
            fd = next(f for f in pool if f["id"] == fid)
            if rec.get("d50") is not None:
                fd["d50"] = rec["d50"]

    # ---- portfolios from AMC monthly files
    psum, pdet, prep = attach_portfolios(funds, amcs)
    pdir = out_dir_p = SITE_DATA / "p"
    pdir.mkdir(parents=True, exist_ok=True)
    for old in pdir.glob("*.json"):
        old.unlink()
    by_cat_p = defaultdict(dict)
    for f in funds:
        if f["id"] in psum:
            f.update(psum[f["id"]])
            by_cat_p[cats[f["c"]]["id"]][str(f["id"])] = pdet[f["id"]]
    for cid, payload_p in by_cat_p.items():          # one file per category keeps requests and files few
        write_json(out_dir_p / f"{cid}.json", payload_p)
    pmonths = sorted({v["month"] for v in prep["amcs"].values() if v.get("matched")})
    write_json(SITE_DATA / "portfolio_report.json", prep, compact=False)
    print(f"portfolios: {len(psum)} funds matched; unmatched schemes in AMC files: {len(prep['unmatched'])}")

    # ---- write
    out_dir = SITE_DATA
    (out_dir / "d").mkdir(parents=True, exist_ok=True)
    for old in (out_dir / "d").glob("*.json"):
        old.unlink()
    cats_out = []
    for i, c in enumerate(cats):
        counts = {p: cat_payload[(i, p)]["count"] for p in ("R", "D", "E") if (i, p) in cat_payload}
        if not counts:
            continue
        any_p = next(iter(counts))
        kinds = defaultdict(int)
        for f in funds:
            if f["c"] == i:
                kinds[f["k"]] += 1
        cats_out.append({"i": i, "id": c["id"], "g": c["g"], "n": c["n"], "raw": c["raw"].split(" | ")[0], "H": cat_payload[(i, any_p)]["H"],
                         "cnt": counts, "k": max(kinds, key=kinds.get) if kinds else "o",
                         "tm": {p: cat_payload[(i, p)]["tm"] for p in counts},
                         "px": next((cat_payload[(i, p)]["px"]["n"] for p in counts if "px" in cat_payload[(i, p)]), None)})
        for p in counts:
            pl = cat_payload[(i, p)]
            fd = {str(f["id"]): details[f["id"]] for f in groups[(i, p)]}
            write_json(out_dir / "d" / f"{c['id']}-{p}.json",
                       {"med": pl["med"], "ym": pl["ym"], "em": pl["em"], "px": pl.get("px"), "rmed": pl.get("rmed"), "f": fd})

    meta = {
        "asOf": data_date.isoformat(),
        "built": dt.datetime.now(dt.timezone.utc).strftime("%Y-%m-%d %H:%M UTC"),
        "sample": sample,
        "m0": int(m_grid0),
        "months": n_months,
        "cats": cats_out,
        "amcs": amcs,
        "events": EVENTS,
        "sources": {
            "nav": "AMFI NAV files (portal.amfiindia.com), growth options, open-ended schemes",
            "ter": ("AMFI TER of MF Schemes, " + str(ter.get("month"))) if ter.get("rows") else None,
            "aum": ("AMFI Average AUM (scheme-wise), " + str(aum.get("period"))) if aum.get("rows") else None,
            "perf": ("AMFI Fund Performance, " + str(perf.get("date"))) if perf.get("rows") else None,
            "ratios": "AMFI month-end NAVs; risk-free rate = median return of overnight funds; market = the category's index fund",
            "risk": ("AMFI Disclosure of Risk Parameters (mid cap and small cap funds), " + str(risk_month)) if risk_month else None,
            "port": ("Monthly portfolio files published by each fund house (SEBI format), " + ", ".join(pmonths)) if pmonths else None,
            "mcap": ("AMFI average market capitalisation list, " + str(prep.get("mcap"))) if prep.get("mcap") else None,
        },
        "ratioTo": int(ratio_end),
        "rf": r1(rf_ann, 2) if rf_ann is not None else None,
        "plans": list(PLANS_SHOWN),
        "fixes": fixes_total,
    }
    write_json(out_dir / "funds.json", {"meta": meta, "funds": funds})
    size = sum(p.stat().st_size for p in out_dir.rglob("*.json"))
    print(f"built {len(funds)} schemes in {len(cats_out)} categories, {len(amcs)} AMCs, data to {data_date}; "
          f"{size / 1e6:.1f} MB; {fixes_total} NAV fixes")


if __name__ == "__main__":
    main()
