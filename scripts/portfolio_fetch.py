"""Download each fund house's latest monthly portfolio file, plus AMFI's market-cap list.

AMFI's Portfolio Disclosure page only links to each AMC's own page, so a small
headless browser (Playwright + Chromium) opens those pages the way a visitor
would and collects the spreadsheet links. It handles plain links (HDFC, SBI,
ABSL, Nippon, PPFAS), pages that build links with JavaScript, and "Download"
buttons that open the file in a new window (ICICI).

  python scripts/portfolio_fetch.py pending      -> prints how many fund houses still need last month's file
  python scripts/portfolio_fetch.py              -> downloads what is missing (and AMFI's market-cap list)
  python scripts/portfolio_fetch.py diagnose     -> shows what the robot finds on every page, downloads nothing
  python scripts/portfolio_fetch.py marketcap    -> only AMFI's large / mid / small cap list

Files go to data/raw/portfolios/<amc>/<YYYY-MM>/ and are read by portfolio_parse.py.
If a fund house's site blocks the robot, put its Excel or zip in portfolios/inbox/
(through the GitHub website) and the next run reads it.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sys
import time
from pathlib import Path
from urllib.parse import unquote, urljoin, urlparse

ROOT = Path(__file__).resolve().parent.parent
CFG = json.loads((Path(__file__).resolve().parent / "portfolio_sources.json").read_text())
RAW = ROOT / "data" / "raw" / "portfolios"
STATE = ROOT / "data" / "store" / "portfolios" / "fetch_state.json"
MCAP = ROOT / "data" / "store" / "marketcap.json"
FILE_RE = re.compile(r"\.(xlsx|xlsm|xls|xlsb|zip)(\?|#|$)", re.I)
UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) "
      "Chrome/130.0 Safari/537.36")

MONTHS = ["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"]
MON_RE = r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)"


def log(m):
    print(m, flush=True)


def load_state() -> dict:
    try:
        return json.loads(STATE.read_text())
    except (OSError, ValueError):
        return {}


def save_state(s: dict):
    STATE.parent.mkdir(parents=True, exist_ok=True)
    STATE.write_text(json.dumps(s, indent=1))


def expected_month(today: dt.date | None = None) -> str:
    """The month whose portfolio should be out: last month (AMCs publish by the 10th)."""
    t = today or dt.date.today()
    y, m = (t.year, t.month - 1) if t.month > 1 else (t.year - 1, 12)
    return f"{y:04d}-{m:02d}"


def month_in(text: str) -> str | None:
    """Find a portfolio month in a link's URL or text. Returns 'YYYY-MM'.

    Month names win over plain numbers, because upload folders like '/2026-11/'
    often hold last month's file."""
    s = unquote(text).lower().replace("_", " ").replace("-", " ").replace("%20", " ")
    s = re.sub(r"\s+", " ", s)

    today = dt.date.today()

    def ok(y, m):   # a real month, and not in the future
        return 2015 <= y and 1 <= m <= 12 and (y, m) <= (today.year, today.month)

    tiers = []
    named = []
    for mm, dd, yy in re.findall(MON_RE + r"\s?(\d{1,2})(?:st|nd|rd|th)?,? (\d{4})\b", s):    # October 31, 2026
        named.append((int(yy), MONTHS.index(mm[:3]) + 1))
    for dd, mm, yy in re.findall(r"\b(\d{1,2})(?:st|nd|rd|th)? ?" + MON_RE + r" ?(\d{4}|\d{2})\b(?! ?\d)", s):  # 31 Oct 26
        named.append((int(yy) + (2000 if len(yy) == 2 else 0), MONTHS.index(mm[:3]) + 1))
    for mm, yy in re.findall(MON_RE + r"[ ,.']*(\d{4}|\d{2})\b(?![ ,.']*\d)", s):              # October 2026 / Oct 26
        named.append((int(yy) + (2000 if len(yy) == 2 else 0), MONTHS.index(mm[:3]) + 1))
    tiers.append(named)
    tiers.append([(int(y), int(m)) for d, m, y in re.findall(r"\b(\d{2}) ?(\d{2}) ?(20\d{2})\b", s) if 1 <= int(d) <= 31])
    tiers.append([(int(y), int(m)) for y, m in re.findall(r"\b(20\d{2}) ?(\d{2})\b", s)])
    for t in tiers:
        good = [f"{y:04d}-{m:02d}" for y, m in t if ok(y, m)]
        if good:
            return max(good)
    return None


def pick(cands: list[tuple[str, str]], amc: dict) -> dict[str, list[str]]:
    """Group usable portfolio links by month. cands: [(url, link text)]."""
    exc = re.compile(CFG["exclude_default"] + ("|" + amc["exclude"] if amc.get("exclude") else ""), re.I)
    inc = re.compile(amc["include"], re.I) if amc.get("include") else None
    by_month: dict[str, list[str]] = {}
    for url, text in cands:
        blob = unquote(url) + " " + text
        if exc.search(blob):
            continue
        if inc and not inc.search(blob):
            continue
        if not inc and not re.search(r"portfolio|monthly|disclosure|holding", blob, re.I):
            continue
        m = month_in(blob)
        if not m:
            continue
        lst = by_month.setdefault(m, [])
        if url not in lst:
            lst.append(url)
    return by_month


HARVEST_JS = r"""
async () => {
  const FILE = /\.(xlsx|xlsm|xls|xlsb|zip)(\?|#|$)/i;
  const out = [];
  for (const a of document.querySelectorAll('a[href]')) {
    if (FILE.test(a.href)) out.push([a.href, (a.textContent || a.title || '').trim().replace(/\s+/g, ' ').slice(0, 160)]);
  }
  if (!out.length) {
    const caught = [];
    const wo = window.open; window.open = function (u) { caught.push(String(u)); return null; };
    const ac = HTMLAnchorElement.prototype.click; HTMLAnchorElement.prototype.click = function () { caught.push(this.href); };
    const btns = [...document.querySelectorAll('button,a,[role=button],span,div')]
      .filter(b => /^\s*download\s*$/i.test(b.textContent || '') && b.children.length < 3).slice(0, 6);
    for (const b of btns) {
      let label = '';
      let p = b;
      for (let i = 0; i < 5 && p && !label; i++) { p = p.parentElement; if (p && /portfolio|monthly/i.test(p.textContent)) label = p.textContent.trim().replace(/\s+/g, ' ').slice(0, 160); }
      const n0 = caught.length;
      try { b.click(); } catch (e) {}
      await new Promise(r => setTimeout(r, 1200));
      for (let k = n0; k < caught.length; k++) out.push([caught[k], label]);
    }
    window.open = wo; HTMLAnchorElement.prototype.click = ac;
  }
  return out;
}
"""


def harvest(page, url: str) -> list[tuple[str, str]]:
    seen_dl: list[str] = []
    page.on("download", lambda d: (seen_dl.append(d.url), d.cancel()))
    page.goto(url, wait_until="domcontentloaded", timeout=60000)
    try:
        page.wait_for_load_state("networkidle", timeout=20000)
    except Exception:  # noqa: BLE001
        pass
    page.wait_for_timeout(3000)
    found = page.evaluate(HARVEST_JS)
    found += [[u, ""] for u in seen_dl]
    return [(urljoin(url, u), t) for u, t in found if u and FILE_RE.search(u)]


def looks_like_sheet(b: bytes) -> bool:
    return b[:2] == b"PK" or b[:8] == b"\xd0\xcf\x11\xe0\xa1\xb1\x1a\xe1"


def download(ctx, url: str, referer: str, dest: Path) -> bool:
    for attempt in range(3):
        try:
            r = ctx.request.get(url, headers={"Referer": referer}, timeout=120000)
            body = r.body()
            if r.ok and looks_like_sheet(body):
                dest.write_bytes(body)
                return True
            log(f"    not a spreadsheet ({r.status}, {len(body)} bytes): {url[:120]}")
            return False
        except Exception as e:  # noqa: BLE001
            log(f"    retry {attempt + 1}: {e}")
            time.sleep(3)
    return False


def fname_for(url: str, i: int) -> str:
    n = unquote(urlparse(url).path.rsplit("/", 1)[-1]) or f"file{i}"
    n = re.sub(r"[^\w.\- ]+", "_", n)[:140]
    return n


def have_month(slug: str, month: str) -> bool:
    d = RAW / slug / month
    return d.exists() and any(p.suffix.lower() in (".xlsx", ".xls", ".xlsb", ".xlsm", ".zip") for p in d.iterdir())


def pending(state: dict) -> list[dict]:
    exp = expected_month()
    now = dt.date.today().strftime("%Y-%m")

    def have(a):
        m = state.get(a["slug"], {}).get("month", "")
        return exp <= m <= now        # a month after today is a misread date, not a download
    return [a for a in CFG["amcs"] if not have(a)]


def run(diagnose: bool = False, only: str | None = None):
    from playwright.sync_api import sync_playwright

    state = load_state()
    todo = CFG["amcs"] if diagnose else pending(state)
    if only:
        todo = [a for a in CFG["amcs"] if only.lower() in (a["slug"] + " " + a["name"]).lower()]
    exp = expected_month()
    log(f"[pf] {len(todo)} fund houses to check (looking for {exp})")
    t_end = time.time() + float(os.environ.get("PF_MINUTES", "40")) * 60
    with sync_playwright() as p:
        browser = p.chromium.launch(args=["--disable-blink-features=AutomationControlled"])
        ctx = browser.new_context(user_agent=UA, accept_downloads=True, locale="en-IN", timezone_id="Asia/Kolkata")
        for amc in todo:
            if time.time() > t_end:
                log("[pf] time limit reached; the next run continues")
                break
            slug = amc["slug"]
            st = state.setdefault(slug, {})
            page = ctx.new_page()
            try:
                cands = harvest(page, amc["url"])
            except Exception as e:  # noqa: BLE001
                log(f"[pf] {amc['name']}: page did not load ({str(e)[:120]})")
                st.update({"checked": dt.date.today().isoformat(), "error": "page did not load"})
                page.close()
                continue
            by_month = pick(cands, amc)
            months = sorted(by_month, reverse=True)
            if diagnose:
                log(f"[pf] {amc['name']}: {len(cands)} file links, months {months[:3]}"
                    + (f", e.g. {by_month[months[0]][0][:110]}" if months else "")
                    + ("" if months else f", sample links: {[c[0][:80] for c in cands[:3]]}"))
                page.close()
                continue
            if not months:
                log(f"[pf] {amc['name']}: no monthly portfolio link found ({len(cands)} file links on page)")
                st.update({"checked": dt.date.today().isoformat(), "error": "no link found"})
                page.close()
                continue
            # newest month, plus the one before if we have never stored it (for 'what changed')
            want = [months[0]]
            if len(months) > 1 and not have_month(slug, months[1]):
                want.append(months[1])
            for m in want:
                if have_month(slug, m):
                    continue
                urls = by_month[m][:250]
                d = RAW / slug / m
                d.mkdir(parents=True, exist_ok=True)
                ok = 0
                for i, u in enumerate(urls):
                    if download(ctx, u, amc["url"], d / fname_for(u, i)):
                        ok += 1
                (d / "_source.json").write_text(json.dumps({"amc": amc["name"], "page": amc["url"], "files": urls,
                                                            "fetched": dt.datetime.utcnow().isoformat(timespec="seconds") + "Z"}))
                log(f"[pf] {amc['name']}: {m} -> {ok} of {len(urls)} files")
                if ok == 0:
                    for f in d.iterdir():
                        f.unlink()
                    d.rmdir()
            st.update({"checked": dt.date.today().isoformat(), "month": months[0] if have_month(slug, months[0]) else st.get("month", ""),
                       "error": None if have_month(slug, months[0]) else "download failed"})
            page.close()
            save_state(state)
        browser.close()
    save_state(state)


# ------------------------------------------------------------------ AMFI market-cap list

def marketcap():
    """AMFI's half-yearly list of listed companies ranked by average market cap
    (1-100 large, 101-250 mid, rest small), from amfiindia.com/otherdata/categorisation-of-stocks."""
    import io
    import requests
    base = "https://www.amfiindia.com"
    h = {"User-Agent": UA, "RSC": "1", "Next-Router-State-Tree": "%5B%22%22%2C%7B%7D%5D", "Accept": "text/html, */*",
         "Referer": base + "/otherdata/categorisation-of-stocks"}
    r = requests.get(base + "/otherdata/categorisation-of-stocks", headers=h, timeout=60)
    r.raise_for_status()
    i = r.text.find('"stockData":')
    if i < 0:
        raise RuntimeError("stockData not found on AMFI page")
    obj, _ = json.JSONDecoder().raw_decode(r.text, i + len('"stockData":'))
    items = []
    for year, lst in obj.items():
        for it in lst or []:
            if it.get("excelUrl"):
                items.append((str(year), str(it.get("period") or ""), it["excelUrl"]))
    if not items:
        raise RuntimeError("no Excel list on AMFI page")

    def rank(it):
        y, per, _ = it
        yy = int(re.findall(r"\d{4}", y + per)[-1]) if re.findall(r"\d{4}", y + per) else 0
        h2 = 1 if re.search(r"dec|jul|second|h2", per, re.I) else 0
        return (yy, h2)
    items.sort(key=rank, reverse=True)
    year, period, url = items[0]
    url = urljoin(base, url)
    data = requests.get(url, headers={"User-Agent": UA, "Referer": base}, timeout=120).content
    from python_calamine import CalamineWorkbook
    wb = CalamineWorkbook.from_filelike(io.BytesIO(data))
    out = {}
    for sn in wb.sheet_names:
        rows = wb.get_sheet_by_name(sn).to_python()
        hi = ii = ci = None
        for k, row in enumerate(rows[:30]):
            low = [str(c).lower() for c in row]
            if any("isin" in c for c in low) and any("categor" in c for c in low):
                hi, ii = k, next(j for j, c in enumerate(low) if "isin" in c)
                ci = max(j for j, c in enumerate(low) if "categor" in c)
                break
        if hi is None:
            continue
        for row in rows[hi + 1:]:
            if len(row) <= max(ii, ci):
                continue
            isin = str(row[ii]).strip().upper()
            cat = str(row[ci]).lower()
            if len(isin) == 12 and isin.startswith("IN"):
                out[isin] = "L" if "large" in cat else "M" if "mid" in cat else "S" if "small" in cat else None
    if len(out) < 500:
        raise RuntimeError(f"market-cap list looks too short ({len(out)} rows)")
    MCAP.parent.mkdir(parents=True, exist_ok=True)
    MCAP.write_text(json.dumps({"period": f"{period} {year}".strip(), "source": url, "isin": out}, separators=(",", ":")))
    log(f"[mcap] {len(out)} companies, {period} {year}")


def main(argv):
    cmd = argv[0] if argv else "fetch"
    if cmd == "pending":
        try:
            if not MCAP.exists() or (time.time() - MCAP.stat().st_mtime) > 20 * 86400:
                marketcap()
        except Exception as e:  # noqa: BLE001
            log(f"[mcap] could not refresh the market-cap list: {e}")
        n = len(pending(load_state()))
        log(f"[pf] {n} fund houses still need {expected_month()}")
        out = os.environ.get("GITHUB_OUTPUT")
        if out:
            with open(out, "a") as f:
                f.write(f"pending={n}\n")
        return
    if cmd == "marketcap":
        marketcap()
        return
    if cmd == "diagnose":
        run(diagnose=True, only=argv[1] if len(argv) > 1 else None)
        return
    try:
        if not MCAP.exists() or (time.time() - MCAP.stat().st_mtime) > 20 * 86400:
            marketcap()
    except Exception as e:  # noqa: BLE001
        log(f"[mcap] could not refresh the market-cap list: {e}")
    run(only=argv[1] if len(argv) > 1 else None)


if __name__ == "__main__":
    main(sys.argv[1:])
