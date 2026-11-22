"""Download mutual fund data from AMFI (www.amfiindia.com / portal.amfiindia.com).

Usage:
  python scripts/amfi_fetch.py daily               latest NAVs + last 10 days + expense ratios + fund size + benchmark returns
  python scripts/amfi_fetch.py history [--minutes N] [--start 2006-04-01]
                                                   backfill full NAV history (90-day pieces, resumable)
  python scripts/amfi_fetch.py diagnose            try every AMFI source and print what came back

Only AMFI's own published files are used. Optional sources (expense ratio, fund size,
benchmark returns) fail softly: the dashboard shows "-" for anything missing.
"""
from __future__ import annotations

import argparse
import datetime as dt
import gzip
import json
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

sys.path.insert(0, str(__import__("pathlib").Path(__file__).resolve().parent))
from common import (RAW, amfi_date, meta_get, meta_set, num_day, open_store, parse_nav_text,  # noqa: E402
                    store_put, write_json, read_json, group_of, plan_of)

WWW = "https://www.amfiindia.com"
PORTAL = "https://portal.amfiindia.com"
POLL = "https://www.amfiindia.com/gateway/pollingsebi"

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36")

LOG: list[str] = []


def log(msg: str):
    line = f"[{dt.datetime.now().strftime('%H:%M:%S')}] {msg}"
    print(line, flush=True)
    LOG.append(line)


# --------------------------------------------------------------------------- http

def http(url: str, params: dict | None = None, referer: str | None = None, body: dict | None = None,
         timeout: int = 120, retries: int = 4, accept: str = "*/*") -> bytes:
    if params:
        url = url + ("&" if "?" in url else "?") + urllib.parse.urlencode(params)
    headers = {"User-Agent": UA, "Accept": accept, "Accept-Encoding": "gzip"}
    if referer:
        headers["Referer"] = f"{WWW}/{referer.lstrip('/')}"
    data = None
    if body is not None:
        data = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    last = None
    for attempt in range(retries):
        try:
            req = urllib.request.Request(url, data=data, headers=headers, method="POST" if body is not None else "GET")
            with urllib.request.urlopen(req, timeout=timeout) as r:
                raw = r.read()
                if r.headers.get("Content-Encoding") == "gzip":
                    raw = gzip.decompress(raw)
                return raw
        except (urllib.error.URLError, TimeoutError, ConnectionError, OSError) as e:
            last = e
            code = getattr(e, "code", None)
            if code in (400, 401, 403, 404):
                break
            time.sleep(3 * (attempt + 1))
    raise RuntimeError(f"GET {url} failed: {last}")


def http_json(url, **kw):
    raw = http(url, accept="application/json, text/plain, */*", **kw)
    return json.loads(raw.decode("utf-8", "replace"))


def records_in(obj, ctx=None):
    """Walk any JSON shape; yield flat dicts of scalar fields, each carrying its parents' scalar fields."""
    ctx = dict(ctx or {})
    if isinstance(obj, list):
        for x in obj:
            yield from records_in(x, ctx)
        return
    if not isinstance(obj, dict):
        return
    scalars = {k: v for k, v in obj.items() if not isinstance(v, (dict, list))}
    kids = [v for v in obj.values() if isinstance(v, (dict, list))]
    merged = {**ctx, **scalars}
    if kids:
        yielded = False
        for v in kids:
            for r in records_in(v, merged):
                yielded = True
                yield r
        if not yielded and scalars:
            yield merged
    elif scalars:
        yield merged


def norm(k: str) -> str:
    return "".join(ch for ch in k.lower() if ch.isalnum())


def pick(rec: dict, *cands, contains: tuple = (), avoid: tuple = ()):
    keys = {norm(k): k for k in rec}
    for c in cands:
        if norm(c) in keys:
            v = rec[keys[norm(c)]]
            if v not in (None, "", "-", "NA", "N.A."):
                return v
    if contains:
        for nk, k in keys.items():
            if all(c in nk for c in contains) and not any(a in nk for a in avoid):
                v = rec[k]
                if v not in (None, "", "-", "NA", "N.A."):
                    return v
    return None


def to_float(v):
    try:
        return float(str(v).replace(",", "").replace("%", "").strip())
    except (TypeError, ValueError):
        return None


# --------------------------------------------------------------------------- latest NAV

def fetch_latest():
    text = None
    for url in (f"{PORTAL}/spages/NAVAll.txt", f"{WWW}/spages/NAVAll.txt"):
        try:
            text = http(url).decode("utf-8", "replace")
            if "Scheme Code" in text:
                log(f"latest NAV file from {url} ({len(text)//1024} KB)")
                break
        except RuntimeError as e:
            log(f"latest NAV: {e}")
            text = None
    if not text:
        raise SystemExit("Could not download AMFI's latest NAV file. Keeping the previous dashboard.")
    rows = list(parse_nav_text(text))
    schemes = []
    for r in rows:
        if "open" not in (r["type"] or "open").lower():
            continue
        schemes.append({**r, "date": r["date"].isoformat()})
    if len(schemes) < 500:
        raise SystemExit(f"Latest NAV file looked wrong ({len(schemes)} open-ended schemes). Stopping.")
    write_json(RAW / "latest.json", schemes)
    log(f"latest: {len(schemes)} open-ended schemes, NAV date {max(s['date'] for s in schemes)}")
    return schemes


def universe_codes(schemes) -> set[int]:
    out = set()
    for s in schemes:
        g = group_of(s["category"])
        if plan_of(s["name"], g):
            out.add(int(s["code"]))
    return out


# --------------------------------------------------------------------------- history

def download_history(frm: dt.date, to: dt.date) -> str:
    params = {"frmdt": amfi_date(frm)}
    if to != frm:
        params["todt"] = amfi_date(to)
    return http(f"{PORTAL}/DownloadNAVHistoryReport_Po.aspx", params=params, timeout=300).decode("utf-8", "replace")


def ingest(con, text: str, codes: set[int]) -> tuple[int, set]:
    rows, dates = [], set()
    for r in parse_nav_text(text):
        if r["code"] in codes:
            rows.append((r["code"], r["date"], r["nav"]))
            dates.add(r["date"])
    n = store_put(con, rows)
    return n, dates


def fetch_range(con, frm: dt.date, to: dt.date, codes: set[int]) -> int:
    """Fetch [frm, to] (<= 90 days). Falls back to one request per weekday if AMFI ignores the end date."""
    text = download_history(frm, to)
    n, dates = ingest(con, text, codes)
    span = (to - frm).days
    if span > 6 and len(dates) <= 1:
        log(f"  range request returned {len(dates)} date(s); fetching {frm}..{to} day by day")
        n = 0
        d = frm
        while d <= to:
            if d.weekday() < 5:
                try:
                    k, _ = ingest(con, download_history(d, d), codes)
                    n += k
                except RuntimeError as e:
                    log(f"  {d}: {e}")
                time.sleep(0.4)
            d += dt.timedelta(days=1)
    return n


def backfill(start: dt.date, minutes: float):
    schemes = read_json(RAW / "latest.json") or fetch_latest()
    codes = universe_codes(schemes)
    con = open_store()
    done = set(meta_get(con, "chunks_done", []))
    t0 = time.time()
    today = dt.date.today()
    chunk = start
    pending = []
    while chunk <= today:
        end = min(chunk + dt.timedelta(days=89), today)
        if chunk.isoformat() not in done:
            pending.append((chunk, end))
        chunk = end + dt.timedelta(days=1)
    log(f"history: {len(pending)} of {len(pending) + len(done)} pieces still to fetch")
    for frm, to in reversed(pending):  # newest first: recent years matter most
        if (time.time() - t0) / 60 > minutes:
            log("history: time budget used; the next run continues from here")
            break
        try:
            n = fetch_range(con, frm, to, codes)
            if to < today - dt.timedelta(days=20):   # recent pieces are fetched again next run
                done.add(frm.isoformat())
            meta_set(con, "chunks_done", sorted(done))
            con.commit()
            log(f"history {frm}..{to}: {n} NAVs")
        except RuntimeError as e:
            log(f"history {frm}..{to}: failed ({e}); will retry next run")
        time.sleep(1.0)
    remaining = len([p for p in pending if p[0].isoformat() not in done and p[1] < today - dt.timedelta(days=20)])
    meta_set(con, "history_remaining", remaining)
    con.commit()
    con.close()
    log(f"history: {remaining} pieces remaining")
    return remaining


def recent(days: int = 12):
    schemes = read_json(RAW / "latest.json")
    codes = universe_codes(schemes)
    con = open_store()
    store_put(con, [(s["code"], dt.date.fromisoformat(s["date"]), s["nav"]) for s in schemes if s["code"] in codes])
    today = dt.date.today()
    try:
        n = fetch_range(con, today - dt.timedelta(days=days), today, codes)
        log(f"recent {days} days: {n} NAVs")
    except RuntimeError as e:
        log(f"recent days: {e} (latest NAVs were still saved)")
    con.commit()
    last = con.execute("SELECT MAX(d) FROM nav").fetchone()[0]
    con.close()
    if last:
        log(f"store now runs to {num_day(last)}")


# --------------------------------------------------------------------------- expense ratio (TER)

def fin_year(d: dt.date) -> str:
    y = d.year if d.month >= 4 else d.year - 1
    return f"{y}-{y + 1}"


TER_PAGE = 100          # AMFI returns at most 100 rows per page
MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]


def amc_list() -> list[tuple[str, str]]:
    """AMFI fund-house ids (the same MF_ID AMFI uses on every page) from portfolio_sources.json."""
    src = read_json(Path(__file__).with_name("portfolio_sources.json")) or {}
    return [(str(a["mf_id"]), a["name"]) for a in src.get("amcs", []) if a.get("mf_id")]


def ter_month(today: dt.date) -> str:
    """TER is taken for the last complete month, as 'MM-YYYY' (AMFI's own format)."""
    prev = today.replace(day=1) - dt.timedelta(days=1)
    return f"{prev.month:02d}-{prev.year}"


def ter_rows_for(mf_id: str, amc: str, month: str, deadline: float) -> list[dict]:
    """All TER rows of one fund house for one month, keeping each scheme's last day."""
    latest: dict[str, dict] = {}
    page, pages = 1, 1
    while page <= pages:
        if time.time() > deadline:
            raise TimeoutError("out of time")
        data = http_json(f"{WWW}/api/populate-te-rdata-revised",
                         params={"MF_ID": mf_id, "Month": month, "strCat": "-1", "strType": "-1",
                                 "page": page, "pageSize": TER_PAGE},
                         referer="ter-of-mf-schemes", timeout=120)
        if not isinstance(data, dict) or data.get("error"):
            raise RuntimeError(str(data.get("message") if isinstance(data, dict) else data)[:200])
        pages = int((data.get("meta") or {}).get("pageCount") or 1)
        for rec in data.get("data") or []:
            key = rec.get("NSDLSchemeCode") or rec.get("Scheme_Name")
            if key and (key not in latest or str(rec.get("TER_Date")) > str(latest[key].get("TER_Date"))):
                latest[key] = rec
        page += 1
        time.sleep(0.2)
    rows = []
    for rec in latest.values():
        name = rec.get("Scheme_Name")
        if not name:
            continue
        for plan, field in (("R", "R_TER"), ("D", "D_TER")):
            v = to_float(rec.get(field))
            if v is not None and 0 < v < 5:
                rows.append({"name": name, "amc": amc, "mf": mf_id, "plan": plan, "ter": v,
                             "date": str(rec.get("TER_Date") or "")[:10], "isin": None})
    return rows


def fetch_ter(minutes: float | None = None):
    """Expense ratios from AMFI 'TER of MF Schemes', one fund house at a time.

    AMFI times out when asked for all fund houses at once, and sends one row per scheme per day,
    100 rows a page. So this runs once a month (for the last complete month), resumes where it
    stopped if it runs out of time, and keeps each scheme's TER on the month's last day.
    """
    minutes = float(os.environ.get("TER_MINUTES", 25)) if minutes is None else minutes
    today = dt.date.today()
    month = ter_month(today)
    old = read_json(RAW / "ter.json") or {}
    if old.get("month") == month and old.get("complete"):
        log(f"TER: already have {month}")
        return
    listed = []
    for fy in sorted({fin_year(today), fin_year(today.replace(day=1) - dt.timedelta(days=1))}):
        try:
            m = http_json(f"{WWW}/api/populate-ter-month", params={"year": fy}, referer="ter-of-mf-schemes")
            m = m.get("data", m) if isinstance(m, dict) else m
            listed += [str(x.get("MonthNumber")) for x in m if isinstance(x, dict) and x.get("MonthNumber")]
        except Exception as e:  # noqa: BLE001
            log(f"TER months {fy}: {e}")
    if listed and month not in listed:
        log(f"TER: AMFI has not listed {month} yet; keeping previous file if any")
        return
    part_path = RAW / "ter_partial.json"
    part = read_json(part_path) or {}
    if part.get("month") != month:
        part = {"month": month, "done": {}}
    amcs = amc_list()
    todo = [(i, n) for i, n in amcs if i not in part["done"]]
    deadline = time.time() + minutes * 60
    failed = []

    def one(item):
        mf_id, name = item
        return mf_id, ter_rows_for(mf_id, name, month, deadline)

    with ThreadPoolExecutor(max_workers=3) as ex:
        futs = {ex.submit(one, it): it for it in todo}
        for fu in as_completed(futs):
            mf_id, name = futs[fu]
            try:
                _, rows = fu.result()
                part["done"][mf_id] = rows
            except Exception as e:  # noqa: BLE001
                failed.append(name)
                log(f"TER {name}: {e}")
    write_json(part_path, part)
    complete = len(part["done"]) >= len(amcs)
    rows = []
    if old.get("rows") and not complete:
        # until every fund house is in, keep the older month's rows for the ones still missing
        rows = [r for r in old["rows"] if str(r.get("mf")) not in part["done"]]
    rows += [r for v in part["done"].values() for r in v]   # newer rows last, so they win when matched
    if rows:
        write_json(RAW / "ter.json", {"month": month, "fetched": today.isoformat(), "complete": complete,
                                      "rows": rows})
        log(f"TER: {len(rows)} rows for {month}, {len(part['done'])} of {len(amcs)} fund houses"
            + (f"; will retry {', '.join(failed[:5])}{'...' if len(failed) > 5 else ''}" if failed else ""))
    else:
        log("TER: nothing usable; keeping previous file if any")


# --------------------------------------------------------------------------- fund size (average AUM)

def newest_first(items: list[dict], label: str) -> list[dict]:
    """AMFI numbers its years and quarters newest = 1. Sort by the dates in the label, then by that id."""
    def key(x):
        t = str(x.get(label) or "").lower()
        years = [int(y) for y in re.findall(r"20\d\d", t)]
        mons = [i for i, m in enumerate(MONTHS) if m in t]
        last_mon = max(((t.rfind(MONTHS[i]), i) for i in mons), default=(-1, -1))[1]
        return (max(years) if years else 0, last_mon, -int(x["id"]))
    return sorted([x for x in items if isinstance(x, dict) and x.get("id") is not None], key=key, reverse=True)


def aum_rows_from(data) -> list[dict]:
    """AMFI 'average-aum-schemewise': [{Mfname, SchemeCat_Desc, schemes:[{SchemeNAVName, AMFI_Code,
    AverageAumForTheMonth:{...}}]}], amounts in Rs lakh. Returns one row per plan/option, in Rs crore."""
    groups = data.get("data") if isinstance(data, dict) else data
    rows = []
    for g in groups or []:
        if not isinstance(g, dict) or not isinstance(g.get("schemes"), list):
            continue
        for s in g["schemes"]:
            v = s.get("AverageAumForTheMonth")
            lakh = sum(to_float(x) or 0 for x in v.values()) if isinstance(v, dict) else to_float(v)
            name = s.get("SchemeNAVName") or s.get("SchemeName")
            if name and lakh is not None and lakh >= 0:
                rows.append({"code": s.get("AMFI_Code"), "name": name, "amc": g.get("Mfname"),
                             "cat": g.get("SchemeCat_Desc"), "aum": round(lakh / 100, 2)})
    return rows


def fetch_aum():
    ref = "research-information/aum-data/average-aum"
    try:
        fys = http_json(f"{WWW}/api/average-aum-fundwise", referer=ref)
        fys = newest_first(fys.get("data", fys) if isinstance(fys, dict) else fys, "financial_year")
    except Exception as e:  # noqa: BLE001
        log(f"AUM years: {e}")
        return
    for fy in fys[:2]:
        try:
            per = http_json(f"{WWW}/api/average-aum-fundwise", params={"fyId": fy["id"]}, referer=ref)
            per = per.get("data", per) if isinstance(per, dict) else per
            periods = newest_first(per.get("periods", []) if isinstance(per, dict) else per, "period")
        except Exception as e:  # noqa: BLE001
            log(f"AUM periods: {e}")
            continue
        for p in periods[:2]:
            try:
                data = http_json(f"{WWW}/api/average-aum-schemewise",
                                 params={"strType": "Categorywise", "fyId": fy["id"], "periodId": p["id"], "MF_ID": "0"},
                                 referer=ref, timeout=300)
            except Exception as e:  # noqa: BLE001
                log(f"AUM {fy.get('financial_year')} {p.get('period')}: {e}")
                continue
            rows = aum_rows_from(data)
            if len(rows) > 200:
                write_json(RAW / "aum.json", {"period": f"{p.get('period')}", "unit": "crore",
                                              "fetched": dt.date.today().isoformat(), "rows": rows})
                log(f"AUM: {len(rows)} plans and options for {p.get('period')}")
                return
            log(f"AUM {p.get('period')}: only {len(rows)} rows")
    log("AUM: nothing usable; keeping previous file if any")


# --------------------------------------------------------------------------- benchmark returns (fund performance)

def fetch_perf():
    try:
        f = http_json(f"{POLL}/api/amfi/fundperformancefilters", body={}, referer="otherdata/fund-performance")
        f = f.get("data", f)
        cats = f.get("investmentTypeList") or []
    except Exception as e:  # noqa: BLE001
        log(f"performance filters: {e}")
        return
    d = dt.date.today()
    rows = []
    for back in range(0, 6):
        day = d - dt.timedelta(days=back)
        if day.weekday() >= 5:
            continue
        rows = []
        for c in cats:
            try:
                subs = http_json(f"{POLL}/api/amfi/getsubcategory", body={"category": c["id"]},
                                 referer="otherdata/fund-performance")
                subs = subs.get("data", subs)
            except Exception as e:  # noqa: BLE001
                log(f"performance subcategories {c}: {e}")
                continue
            for s in subs or []:
                try:
                    data = http_json(f"{POLL}/api/amfi/fundperformance", referer="otherdata/fund-performance",
                                     body={"maturityType": 1, "category": c["id"], "subCategory": s["id"],
                                           "mfid": 0, "reportDate": amfi_date(day)})
                except Exception as e:  # noqa: BLE001
                    log(f"performance {s.get('name')}: {e}")
                    continue
                for rec in records_in(data):
                    name = pick(rec, "Scheme_Name", "schemeName", contains=("scheme", "name"))
                    if not name:
                        continue
                    rows.append({
                        "name": name,
                        "code": pick(rec, "Scheme_Code", "schemeCode", contains=("scheme", "code")),
                        "amc": pick(rec, "MF_Name", "mutualFundName", contains=("mf", "name")),
                        "benchmark": pick(rec, "Benchmark", "benchmarkName", "Benchmark_Name",
                                          contains=("benchmark",), avoid=("return", "ret", "additional", "addl")),
                        "bm1": to_float(pick(rec, "Benchmark_Returns_1yr", contains=("benchmark", "1"), avoid=("additional", "addl"))),
                        "bm3": to_float(pick(rec, "Benchmark_Returns_3yr", contains=("benchmark", "3"), avoid=("additional", "addl"))),
                        "bm5": to_float(pick(rec, "Benchmark_Returns_5yr", contains=("benchmark", "5"), avoid=("additional", "addl"))),
                        "aum": to_float(pick(rec, "DailyAUM", "Daily_AUM", contains=("aum",))),
                    })
                time.sleep(0.3)
        if len(rows) > 200:
            write_json(RAW / "perf.json", {"date": day.isoformat(), "rows": rows})
            log(f"benchmark returns: {len(rows)} rows for {day}")
            return
    log("benchmark returns: nothing usable; keeping previous file if any")


# --------------------------------------------------------------------------- diagnose

def diagnose():
    tests = [
        ("latest NAV (portal)", lambda: http(f"{PORTAL}/spages/NAVAll.txt")[:400]),
        ("NAV history, 10 days", lambda: download_history(dt.date.today() - dt.timedelta(days=12),
                                                           dt.date.today() - dt.timedelta(days=2))[:400]),
        ("TER months", lambda: http(f"{WWW}/api/populate-ter-month", params={"year": fin_year(dt.date.today())},
                                    referer="ter-of-mf-schemes")[:400]),
        ("TER, one fund house, page 1", lambda: http(f"{WWW}/api/populate-te-rdata-revised",
                                                     params={"MF_ID": amc_list()[0][0], "Month": ter_month(dt.date.today()),
                                                             "strCat": "-1", "strType": "-1", "page": 1, "pageSize": TER_PAGE},
                                                     referer="ter-of-mf-schemes")[:400]),
        ("AUM years", lambda: http(f"{WWW}/api/average-aum-fundwise", referer="research-information/aum-data/average-aum")[:400]),
        ("performance filters", lambda: http(f"{POLL}/api/amfi/fundperformancefilters", body={},
                                             referer="otherdata/fund-performance")[:400]),
    ]
    for name, fn in tests:
        try:
            out = fn()
            print(f"OK   {name}: {out[:300]!r}\n")
        except Exception as e:  # noqa: BLE001
            print(f"FAIL {name}: {e}\n")


# --------------------------------------------------------------------------- main

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("cmd", choices=["daily", "history", "diagnose", "latest", "ter", "aum", "perf"])
    ap.add_argument("--minutes", type=float, default=300)
    ap.add_argument("--start", default="2006-04-01")
    a = ap.parse_args()
    RAW.mkdir(parents=True, exist_ok=True)
    if a.cmd == "diagnose":
        diagnose()
        return
    if a.cmd in ("daily", "latest"):
        fetch_latest()
        recent()
    if a.cmd in ("daily", "ter"):
        fetch_ter()
    if a.cmd in ("daily", "aum"):
        fetch_aum()
    if a.cmd in ("daily", "perf"):
        fetch_perf()
    if a.cmd == "history":
        if not (RAW / "latest.json").exists():
            fetch_latest()
        backfill(dt.date.fromisoformat(a.start), a.minutes)
    (RAW / "fetch-log.txt").write_text("\n".join(LOG), encoding="utf-8")


if __name__ == "__main__":
    main()
