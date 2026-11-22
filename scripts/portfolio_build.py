"""Turn parsed AMC portfolios into what each fund page shows.

Called from build_data.py. For every fund house it takes the newest month we
have (and the month before, for 'what changed'), matches each scheme in the
AMC file to the AMFI scheme, and works out:

* asset mix (equity, foreign equity, debt, government securities, money
  market, REITs/InvITs, fund units, gold/silver, cash), with hedged
  (arbitrage) equity shown separately
* number of stocks, share of the top 10 holdings, sector split (industry as
  given in the AMC file), market-cap split using AMFI's own large/mid/small list
* for debt: rating mix and the weighted yield (YTM) of the papers that carry one
* what changed since last month, by quantity held (bought, sold, added, cut)
* figures the AMC printed in the file itself (turnover, duration, YTM ...)
"""
from __future__ import annotations

import json
import re
from collections import defaultdict
from pathlib import Path

from namematch import best_match

ROOT = Path(__file__).resolve().parent.parent
STORE = ROOT / "data" / "store" / "portfolios"
MCAP = ROOT / "data" / "store" / "marketcap.json"

CLS_LABEL = {"eq": "Equity", "arb": "Arbitrage", "feq": "Foreign equity", "debt": "Corporate debt",
             "gsec": "Government securities", "mm": "Money market", "reit": "REITs and InvITs", "mf": "Fund units",
             "gold": "Gold and silver", "cash": "Cash and others", "deriv": "Derivatives"}
SECURITY = {"eq", "feq", "debt", "gsec", "mm", "reit", "mf", "gold"}      # arbitrage is shown as one line
CHANGE_CLS = {"eq", "feq", "debt", "gsec", "reit"}


def amc_key(a: str) -> str:
    a = (a or "").lower().replace("&", " and ")
    a = re.sub(r"mutual fund|asset management|company|limited|ltd|private|pvt|amc|\bmf\b", " ", a)
    return re.sub(r"[^a-z0-9]+", "", a)


def load_store():
    """{amc_key or 'inbox': {month: [file payloads]}}"""
    out = defaultdict(lambda: defaultdict(list))
    if not STORE.exists():
        return out
    for mdir in sorted(p for p in STORE.iterdir() if p.is_dir() and re.fullmatch(r"\d{4}-\d{2}", p.name)):
        for f in mdir.glob("*.json"):
            try:
                d = json.loads(f.read_text())
            except ValueError:
                continue
            key = "inbox" if d.get("inbox") else amc_key(d.get("amc") or f.stem)
            out[key][mdir.name].append(d)
    return out


def load_mcap():
    try:
        d = json.loads(MCAP.read_text())
        return d.get("isin", {}), d.get("period")
    except (OSError, ValueError):
        return {}, None


def rating_bucket(sec: str, cls: str) -> str:
    s = (sec or "").upper()
    if cls == "gsec" or "SOV" in s:
        return "Sovereign"
    s = re.sub(r"CRISIL|ICRA|CARE|\bIND\b|FITCH|BRICKWORK|BWR|ACUITE|INFOMERICS|IVR|PROVISIONAL|\(.*?\)|[\[\]]|\bCE\b|\bSO\b",
               " ", s)
    m = re.search(r"(?<![A-Z0-9])(AAA|AA|A1\+|A1|A2\+?|A3|A4|A|BBB|BB|B|C|D)(?=[+\-]?(?:\s|$|/|,))", s)
    if not m:
        return "Unrated / other"
    r = m.group(1)
    if r in ("AAA", "A1+"):
        return "AAA / A1+"
    if r == "AA":
        return "AA"
    if r in ("A", "A1"):
        return "A"
    return "Below A"


def sector_name(s: str) -> str:
    s = re.sub(r"[#^*~$@]+", "", s or "").strip()
    s = re.sub(r"\s+", " ", s)
    return s[:48] if s else "Not given"


MONTH_FUT = re.compile(r"\s+(jan|feb|mar|apr|may|jun|jul|aug|sep|oct|nov|dec)[a-z]*[\s\-']*\d{2,4}.*$", re.I)


def _nm(x: str) -> str:
    x = MONTH_FUT.sub("", x or "")
    x = re.sub(r"\b(futures?|fut|limited|ltd|the|stock|short)\b|[.,()&']", " ", x.lower())
    return " ".join(x.split())


def net_arbitrage(rows: list, shorts: list) -> tuple[list, float]:
    """Arbitrage: shares bought while the same shares' futures are sold. Some AMCs list these
    under an 'Arbitrage' heading; others list the shares with the normal equity and the futures
    in a derivatives table. Either way, move the hedged part out of equity into one 'arbitrage'
    figure. Returns (rows with adjusted weights, arbitrage % of the fund)."""
    rows = [list(r) for r in rows]
    arb = sum(r[5] for r in rows if r[7] == "arb" and r[5])
    if not shorts:
        return rows, arb
    eq_idx = {}
    for i, r in enumerate(rows):
        if r[7] in ("eq", "arb") and (r[5] or 0) > 0:
            eq_idx.setdefault(_nm(r[0]), []).append(i)
    for name, w in shorts:
        k = _nm(name)
        hits = eq_idx.get(k)
        if not hits and len(k) >= 6:
            hits = next((v for kk, v in eq_idx.items() if kk.startswith(k) or k.startswith(kk)), None)
        if not hits:
            continue
        if any(rows[i][7] == "arb" for i in hits):
            continue                                    # already under the Arbitrage heading
        left = abs(w or 0)
        for i in hits:                                   # the same share can appear on two lines
            move = min(left, rows[i][5])
            rows[i][5] -= move
            arb += move
            left -= move
            if left <= 1e-9:
                break
    return rows, arb


def analyse(rows: list, kv: dict, mcap: dict, shorts: list | None = None) -> dict:
    rows, arb = net_arbitrage(rows, shorts or [])
    mix = defaultdict(float)
    for r in rows:
        if r[5] is not None and r[7] not in ("arb", "deriv"):
            mix[r[7]] += r[5]
    if arb >= 0.01:
        mix["arb"] = arb
    sec_rows = [r for r in rows if r[7] in SECURITY and (r[5] or 0) > 0.004]
    sec_rows.sort(key=lambda r: -r[5])
    stocks = [r for r in sec_rows if r[7] in ("eq", "feq")]
    eq_w = sum(r[5] for r in stocks)
    out = {
        "mix": [[CLS_LABEL[k], round(v, 2)] for k, v in sorted(mix.items(), key=lambda x: -abs(x[1])) if abs(v) >= 0.05],
        "n": len(sec_rows),
        "ns": len({r[1] or r[0] for r in stocks}),
        "t10": round(sum(r[5] for r in sec_rows[:10]), 2),
        "eq": round(eq_w, 2),
        "hedged": round(arb, 2),
        "cash": round(mix.get("cash", 0.0), 2),
    }
    # holdings for the page and for overlap: [isin, name, weight, sector/rating, class, qty]
    out["h"] = [[r[1], r[0], round(r[5], 3), sector_name(r[2]) if r[7] in ("eq", "feq", "reit") else (r[2] or "")[:24],
                 r[7]] for r in sec_rows]
    if arb >= 0.01:                                     # one line, placed by its size
        out["h"].append(["", "Arbitrage positions", round(arb, 3), "Shares bought with their futures sold", "arb"])
        out["h"].sort(key=lambda h: -h[2])
    if stocks:
        sect = defaultdict(float)
        for r in stocks:
            sect[sector_name(r[2])] += r[5]
        top = sorted(sect.items(), key=lambda x: -x[1])
        out["sect"] = [[k, round(v, 2)] for k, v in top[:9]]
        if len(top) > 9:
            out["sect"].append(["Others", round(sum(v for _, v in top[9:]), 2)])
        caps = defaultdict(float)
        for r in stocks:
            c = "Foreign" if r[7] == "feq" else {"L": "Large cap", "M": "Mid cap", "S": "Small cap"}.get(mcap.get(r[1]), "Not in AMFI list")
            caps[c] += r[5]
        order = ["Large cap", "Mid cap", "Small cap", "Foreign", "Not in AMFI list"]
        out["caps"] = [[k, round(caps[k], 2)] for k in order if caps.get(k)] if mcap else []
    debt = [r for r in sec_rows if r[7] in ("debt", "gsec", "mm")]
    if debt:
        dw = sum(r[5] for r in debt)
        rb = defaultdict(float)
        for r in debt:
            rb[rating_bucket(r[2] or "", r[7])] += r[5]
        order = ["Sovereign", "AAA / A1+", "AA", "A", "Below A", "Unrated / other"]
        out["rate"] = [[k, round(rb[k], 2)] for k in order if rb.get(k)]
        with_y = [r for r in debt if r[6] is not None and 0 < r[6] < 25]
        cov = sum(r[5] for r in with_y)
        if with_y and cov >= 0.5 * dw:
            out["ytm"] = round(sum(r[5] * r[6] for r in with_y) / cov, 2)
        out["debtw"] = round(dw, 2)
    if kv:
        out["kv"] = kv
    return out


def changes(cur: list, prev: list) -> dict | None:
    def idx(rows):
        d = {}
        for r in rows:
            if r[7] in CHANGE_CLS and r[1] and (r[5] or 0) > 0:
                k = r[1]
                if k in d:
                    d[k] = [d[k][0], (d[k][1] or 0) + (r[3] or 0), d[k][2] + r[5]]
                else:
                    d[k] = [r[0], r[3], r[5]]
        return d
    a, b = idx(cur), idx(prev)
    if not a or not b:
        return None
    new = sorted(([v[0], round(v[2], 2)] for k, v in a.items() if k not in b), key=lambda x: -x[1])
    gone = sorted(([v[0], round(v[2], 2)] for k, v in b.items() if k not in a), key=lambda x: -x[1])
    add, cut = [], []
    for k, v in a.items():
        if k in b and v[1] and b[k][1]:
            ch = v[1] / b[k][1] - 1
            if ch >= 0.10:
                add.append([v[0], round(v[2], 2), round(ch * 100)])
            elif ch <= -0.10:
                cut.append([v[0], round(v[2], 2), round(ch * 100)])
    add.sort(key=lambda x: -x[1])
    cut.sort(key=lambda x: -x[1])
    return {"new": new[:10], "gone": gone[:10], "add": add[:10], "cut": cut[:10],
            "nn": len(new), "ng": len(gone), "na": len(add), "nc": len(cut)}


def attach(funds: list[dict], amcs: list[str]) -> tuple[dict, dict, dict]:
    """funds: build_data fund dicts (id, n, a). Returns (summary_by_id, detail_by_id, report)."""
    store = load_store()
    mcap, mperiod = load_mcap()
    by_amc = defaultdict(dict)
    for f in funds:
        by_amc[amc_key(amcs[f["a"]])][f["id"]] = f["n"]
    summary, detail = {}, {}
    report = {"amcs": {}, "unmatched": [], "mcap": mperiod}

    def take(key, cands, amc_name):
        months = sorted(store.get(key, {}), reverse=True)
        if not months:
            return
        cur_m = months[0]
        prev_m = months[1] if len(months) > 1 else None
        prev_by_name = {}
        if prev_m:
            for pay in store[key][prev_m]:
                for s in pay["schemes"]:
                    prev_by_name.setdefault(s["name"].lower(), s)
        hit = 0
        for pay in store[key][cur_m]:
            for s in pay["schemes"]:
                fid, sc = best_match(s["name"], cands, amc_name, threshold=0.72 if amc_name else 0.85)
                if fid is None:
                    report["unmatched"].append([amc_name or "inbox", s["name"], round(sc, 2)])
                    continue
                if fid in detail and detail[fid]["n"] >= len([r for r in s["rows"] if r[7] in SECURITY]):
                    continue
                a = analyse(s["rows"], s.get("kv") or {}, mcap, s.get("shorts"))
                a["month"] = s.get("date") or cur_m
                a["src"] = (amc_name or "Uploaded file") + ": " + (s.get("src") or "")
                p = prev_by_name.get(s["name"].lower())
                if p is None and prev_m:
                    names = {k: v["name"] for k, v in prev_by_name.items()}
                    k2, _ = best_match(s["name"], names, amc_name, threshold=0.9)
                    p = prev_by_name.get(k2) if k2 else None
                if p:
                    ch = changes(s["rows"], p["rows"])
                    if ch:
                        ch["from"] = p.get("date") or prev_m
                        a["chg"] = ch
                detail[fid] = a
                hit += 1
        report["amcs"][amc_name or "inbox"] = {"month": cur_m, "matched": hit}

    name_of = {amc_key(a): a for a in amcs}
    for key in list(store):
        if key == "inbox":
            continue
        if key in by_amc:
            take(key, by_amc[key], name_of.get(key, key))
        else:
            report["amcs"][key] = {"month": max(store[key]), "matched": 0, "note": "fund house not in AMFI NAV list"}
    if "inbox" in store:
        allc = {fid: n for d in by_amc.values() for fid, n in d.items() if fid not in detail}
        take("inbox", allc, None)

    for fid, a in detail.items():
        s = {"pm": (a.get("month") or "")[:7], "nh": a["n"], "t10": a["t10"], "eqw": a["eq"], "cash": a["cash"]}
        caps = dict(a.get("caps") or [])
        if a["eq"] > 0 and caps:
            for k, short in (("Large cap", "lcp"), ("Mid cap", "mcp"), ("Small cap", "scp")):
                s[short] = round(caps.get(k, 0) / a["eq"] * 100, 1)
        if a.get("ytm") is not None:
            s["ytm"] = a["ytm"]
        summary[fid] = s
    return summary, detail, report
