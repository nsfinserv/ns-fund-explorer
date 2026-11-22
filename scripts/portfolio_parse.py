"""Read AMC monthly portfolio files (SEBI format) into one common shape.

SEBI asks every AMC to publish each scheme's month-end portfolio as a
spreadsheet. The layout differs a little between AMCs, but the table always
has the instrument name, ISIN, industry or rating, quantity, market value and
"% to net assets". This parser finds that table by its headings, so it works
for single-scheme files (HDFC, PPFAS), multi-sheet files (SBI) and zips of
files (ICICI, ABSL) without per-AMC code.

  python scripts/portfolio_parse.py <file-or-folder> [...]   -> prints a summary
  python scripts/portfolio_parse.py --all                    -> parses data/raw/portfolios and portfolios/inbox

Output per scheme:
  {"name", "date", "src", "rows": [[name, isin, sector_or_rating, qty, value_lakh, weight_pct, ytm_pct, cls], ...],
   "kv": {...AMC-reported figures found in the sheet...}, "sum": total weight}
"""
from __future__ import annotations

import datetime as dt
import io
import json
import re
import sys
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RAW_DIR = ROOT / "data" / "raw" / "portfolios"
INBOX = ROOT / "portfolios" / "inbox"
STORE = ROOT / "data" / "store" / "portfolios"

EXCEL = (".xlsx", ".xlsm", ".xls", ".xlsb", ".ods")

# ------------------------------------------------------------------ reading cells

def sheets_of(data: bytes, name: str):
    """Yield (sheet_name, rows) for a spreadsheet given as bytes."""
    try:
        from python_calamine import CalamineWorkbook
        wb = CalamineWorkbook.from_filelike(io.BytesIO(data))
        for sn in wb.sheet_names:
            try:
                rows = wb.get_sheet_by_name(sn).to_python(skip_empty_area=False)
            except Exception:  # noqa: BLE001
                continue
            yield sn, rows
        return
    except ImportError:
        pass
    if name.lower().endswith((".xlsx", ".xlsm")):
        import openpyxl
        wb = openpyxl.load_workbook(io.BytesIO(data), read_only=True, data_only=True)
        for ws in wb.worksheets:
            yield ws.title, [list(r) for r in ws.iter_rows(values_only=True)]
    elif name.lower().endswith(".xls"):
        import xlrd  # type: ignore
        wb = xlrd.open_workbook(file_contents=data)
        for sh in wb.sheets():
            yield sh.name, [sh.row_values(i) for i in range(sh.nrows)]


def files_in(path: Path):
    """Yield (display_name, bytes) for spreadsheets in a file, folder or zip (zips can nest)."""
    if path.is_dir():
        for p in sorted(path.rglob("*")):
            if p.is_file() and p.suffix.lower() in EXCEL + (".zip",):
                yield from files_in(p)
        return
    data = path.read_bytes()
    yield from _expand(path.name, data)


def _expand(name: str, data: bytes, depth: int = 0):
    if name.lower().endswith(".zip") or (data[:2] == b"PK" and not name.lower().endswith(EXCEL)):
        if depth > 2:
            return
        try:
            z = zipfile.ZipFile(io.BytesIO(data))
        except zipfile.BadZipFile:
            return
        for info in z.infolist():
            if info.is_dir() or "__MACOSX" in info.filename:
                continue
            low = info.filename.lower()
            if low.endswith(EXCEL + (".zip",)):
                yield from _expand(f"{name}/{info.filename}", z.read(info), depth + 1)
        return
    if name.lower().endswith(EXCEL):
        yield name, data


# ------------------------------------------------------------------ helpers

def txt(v) -> str:
    if v is None:
        return ""
    if isinstance(v, float) and v.is_integer():
        v = int(v)
    return re.sub(r"\s+", " ", str(v)).strip()


def num(v):
    if v is None or v == "":
        return None
    if isinstance(v, bool):
        return None
    if isinstance(v, (int, float)):
        return float(v)
    s = str(v).strip().replace(",", "").replace("$", "").replace("₹", "").replace("^", "").replace("#", "")
    s = s.replace("(", "-").replace(")", "")
    neg_pct = s.endswith("%")
    s = s.rstrip("%").strip()
    if s in ("", "-", "NIL", "Nil", "nil", "NA", "N.A.", "--"):
        return None
    try:
        x = float(s)
    except ValueError:
        return None
    return x if not neg_pct else x  # a "%"-suffixed cell is already in percent


ISIN_RE = re.compile(r"^(IN[A-Z0-9]{10}|[A-Z]{2}[A-Z0-9]{9}\d)$")
MONTHS = {m: i for i, m in enumerate(["jan", "feb", "mar", "apr", "may", "jun", "jul", "aug", "sep", "oct", "nov", "dec"], 1)}


def as_date(v) -> dt.date | None:
    if isinstance(v, dt.datetime):
        return v.date()
    if isinstance(v, dt.date):
        return v
    if isinstance(v, (int, float)) and 40000 < v < 60000:          # Excel serial
        return dt.date(1899, 12, 30) + dt.timedelta(days=int(v))
    s = txt(v).lower()
    m = re.search(r"(\d{1,2})(?:st|nd|rd|th)?[\s\-/.,]*([a-z]{3,9})[\s\-/.,]*(\d{4})", s)
    if m and m.group(2)[:3] in MONTHS:
        try:
            return dt.date(int(m.group(3)), MONTHS[m.group(2)[:3]], int(m.group(1)))
        except ValueError:
            pass
    m = re.search(r"([a-z]{3,9})\s+(\d{1,2}),?\s+(\d{4})", s)
    if m and m.group(1)[:3] in MONTHS:
        try:
            return dt.date(int(m.group(3)), MONTHS[m.group(1)[:3]], int(m.group(2)))
        except ValueError:
            pass
    m = re.search(r"(\d{1,2})[/\-.](\d{1,2})[/\-.](\d{4})", s)
    if m:
        try:
            return dt.date(int(m.group(3)), int(m.group(2)), int(m.group(1)))
        except ValueError:
            pass
    return None


# section words -> asset class
SECTIONS = [
    ("deriv", r"derivative|futures?\b|options?\b|interest rate swap|\birs\b"),
    ("arb", r"arbitrage|hedged"),
    ("feq", r"foreign|overseas|international|global|adr|gdr"),
    ("reit", r"\breits?\b|\binvits?\b|real estate investment|infrastructure investment trust"),
    ("mf", r"mutual fund units|units of mutual|exchange traded fund|\betf\b|fund units|investment funds|\bmf units"),
    ("gold", r"gold|silver|commodit"),
    ("cash", r"treps|tri-?party|reverse repo|\brepo\b|cblo|cash|net receivable|net current|current assets|margin|"
             r"call money|net payable|others? \(|corporate debt market development|cdmdf"),
    ("gsec", r"government securit|g-?sec|state development|\bsdl\b|treasury bill|t-?bill|sovereign|central government"),
    ("mm", r"money market|certificate of deposit|commercial paper|\bcd\b|\bcp\b"),
    ("debt", r"debt|bond|debenture|ncd|non convertible|securitis|pass through|ptc|zero coupon|fixed income|"
             r"privately placed"),
    ("eq", r"equity|shares|preference|warrants"),
]
SEC_RE = [(k, re.compile(p, re.I)) for k, p in SECTIONS]
STOP_RE = re.compile(r"^(grand\s*total|total\s*net\s*assets|net\s*assets)\b", re.I)
SKIP_RE = re.compile(r"^(sub[\s-]*total|total\b|\(?[a-z]\)\s*(unlisted|listed)|nil$)", re.I)
SUBHEAD_RE = re.compile(r"^\(?[a-z0-9]{1,3}[).]\s*(listed|unlisted|privately|securiti[sz]ed|awaiting)|^(listed|unlisted)\b", re.I)
CASH_NAME_RE = re.compile(r"treps|tri-?party|reverse repo|\brepo\b|cblo|net receivable|net current asset|"
                          r"cash|margin|call money|net payable|current liabilit", re.I)

KV_PATTERNS = {
    "turnover": r"portfolio turnover(?! ratio \(excluding)",
    "turnover_ex": r"portfolio turnover ratio \(excluding",
    "avgmat": r"average maturity|residual maturity|weighted average maturity",
    "moddur": r"modified duration",
    "macdur": r"macaulay duration",
    "ytm": r"(annuali[sz]ed )?(portfolio )?(yield to maturity|ytm)(?! ~)|portfolio yield",
    "sd": r"^standard deviation(?!.*bench)",
    "sharpe": r"^sharpe",
    "beta": r"^beta\b",
}
KV_RE = {k: re.compile(p, re.I) for k, p in KV_PATTERNS.items()}


def find_cols(row) -> dict | None:
    cells = [txt(c).lower() for c in row]
    col = {}
    for i, c in enumerate(cells):
        if not c:
            continue
        if "isin" in c and "isin" not in col:
            col["isin"] = i
        elif re.search(r"%\s*(to|of)\s*(the\s*)?(net\s*assets?|nav|aum|net asset value)|% to net|% of net|"
                       r"percentage (to|of) (net|nav|aum)|% to aum|% of aum|% to nav|% of nav|^%$", c) and "pct" not in col:
            col["pct"] = i
        elif re.search(r"name of (the )?(instrument|security|issuer)|^instrument|^security|^issuer|^company|"
                       r"^particulars|^name$|scrip", c) and "name" not in col:
            col["name"] = i
        elif re.search(r"industry|rating|sector", c) and "sec" not in col:
            col["sec"] = i
        elif re.search(r"quantity|no\.? of (shares|units)|^units$|^qty", c) and "qty" not in col:
            col["qty"] = i
        elif re.search(r"market\s*/?\s*fair value|market value|fair value|value \(rs|value \(₹|amount \(rs", c) and "val" not in col:
            col["val"] = i
        elif re.search(r"\bytm\b|yield to maturity|^yield", c) and "ytm" not in col:
            col["ytm"] = i
    if "pct" in col and ("isin" in col or "name" in col) and len(col) >= 3:
        if "name" not in col:
            # name column is usually just left of ISIN
            col["name"] = max(0, col["isin"] - 1)
        return col
    return None


def scheme_title(rows, start: int, end: int, sheet: str) -> tuple[str, dt.date | None]:
    """Find the scheme name and portfolio date in the rows above a table."""
    name, date = "", None
    for r in rows[max(0, start - 14): end]:
        cells = [c for c in r if txt(c)]
        for j, c in enumerate(cells):
            t = txt(c)
            tl = t.lower()
            if date is None and ("as on" in tl or "as at" in tl or "statement" in tl):
                date = as_date(t) or next((as_date(x) for x in cells[j + 1: j + 3] if as_date(x)), None)
            if not name and re.search(r"scheme\s*name|name of (the )?scheme", tl):
                rest = re.split(r"scheme\s*name\s*:?|name of (?:the )?scheme\s*:?", t, flags=re.I)[-1].strip(" :-")
                name = rest or (txt(cells[j + 1]) if j + 1 < len(cells) else "")
        if not name:
            for c in cells[:3]:
                t = txt(c)
                if re.search(r"\b(fund|etf|fof|plan|scheme)\b", t, re.I) and not re.search(
                        r"mutual fund$|^portfolio|statement|as on|asset management|^sr\.? ?no", t, re.I) and len(t) < 160:
                    name = t
                    break
    name = re.split(r"\s*\((?:an |a )?open[- ]ended|\s*\(an? ", name, flags=re.I)[0].strip(" :-")
    if not name:
        name = sheet
    return name, date


def classify(section: str, name: str, sec: str, isin: str) -> str:
    nl, sl = name.lower(), sec.lower()
    if CASH_NAME_RE.search(nl) and not isin:
        return "cash"
    if isin.startswith("INF"):
        return "mf"
    if section in ("deriv", "arb", "feq", "reit", "mf", "gold", "cash", "gsec", "mm", "debt"):
        if section in ("debt", "mm") and re.search(r"sovereign", sl):
            return "gsec"
        return section
    if re.search(r"sovereign", sl):
        return "gsec"
    if re.search(r"\b(crisil|icra|care|ind|fitch|brickwork|acuite|infomerics)\b|a1\+|\baaa\b|\baa\b", sl):
        return "debt"
    if isin.startswith("IN") and len(isin) == 12 and isin[6:8] in ("16", "14"):
        return "mm"                     # CD (16) / CP (14) ISIN pattern
    if isin and not isin.startswith("IN"):
        return "feq"
    return "eq"


def parse_block(rows, h: int, end: int, col: dict, force_scale: float | None = None) -> tuple[list, float, float]:
    out = []
    section = "eq"
    for r in rows[h + 1: end]:
        def g(k):
            i = col.get(k)
            return r[i] if i is not None and i < len(r) else None
        name = txt(g("name"))
        isin = txt(g("isin")).upper().replace(" ", "")
        pct = num(g("pct"))
        first = next((txt(c) for c in r if txt(c)), "")
        if STOP_RE.search(name or first):
            break
        if not name and first and pct is None:
            name = first
        if not name:
            continue
        if pct is None or SKIP_RE.search(name):
            if not isin and len(name) < 120 and not SUBHEAD_RE.search(name):
                for k, rx in SEC_RE:
                    if rx.search(name):
                        section = k
                        break
            continue
        if not ISIN_RE.match(isin):
            isin = ""
        sec = txt(g("sec"))
        out.append([name[:90], isin, sec[:60], num(g("qty")), num(g("val")), pct, num(g("ytm")), classify(section, name, sec, isin)])
    total = sum(x[5] for x in out if x[5] and x[5] > 0)
    # weights given as fractions (0.0749) instead of percent (7.49)
    scale = force_scale if force_scale is not None else (100.0 if 0 < total < 2.5 else 1.0)
    if scale != 1.0:
        for x in out:
            if x[5] is not None:
                x[5] *= scale
        total *= scale
    for x in out:
        if x[6] is not None and 0 < abs(x[6]) < 0.5:     # YTM given as a fraction
            x[6] *= 100
        x[5] = round(x[5], 4) if x[5] is not None else None
        x[6] = round(x[6], 3) if x[6] is not None else None
    return out, total, scale


def key_values(rows, start: int, end: int) -> dict:
    kv = {}
    for ri in range(start, min(end, len(rows))):
        r = rows[ri]
        cells = [c for c in r if txt(c) != ""]
        for j, c in enumerate(cells):
            t = txt(c)
            if not isinstance(c, str) or len(t) > 140:
                continue
            for k, rx in KV_RE.items():
                if k in kv or not rx.search(t):
                    continue
                v = next((num(x) for x in cells[j + 1: j + 4] if num(x) is not None), None)
                if v is None and ri + 1 < len(rows):
                    nxt = [x for x in rows[ri + 1] if txt(x) != ""]
                    if nxt and all(num(x) is not None for x in nxt[:1]):
                        v = num(nxt[0])
                if v is None:
                    m = re.search(r"[:\-]\s*([0-9]+(?:\.[0-9]+)?)\s*(times|%|years?|days)?\s*$", t)
                    v = float(m.group(1)) if m else None
                if v is None:
                    continue
                if k in ("ytm",) and 0 < v < 0.5:
                    v *= 100
                if k in ("sd",) and 0 < v < 1:
                    v *= 100
                if "year" in t.lower() or "days" not in t.lower():
                    kv[k] = round(v, 4)
                else:
                    kv[k] = round(v / 365.25, 4)        # given in days
    return kv


def parse_sheet(sheet: str, rows) -> list[dict]:
    heads = []
    for i, r in enumerate(rows):
        col = find_cols(r)
        if col:
            heads.append((i, col))
    blocks, extras = [], []
    for n, (h, col) in enumerate(heads):
        end = heads[n + 1][0] if n + 1 < len(heads) else len(rows)
        hold, total, scale = parse_block(rows, h, end, col)
        if len(hold) < 1 or total < 5:
            extras.append((h, end, col))     # derivative / disclosure tables, not a portfolio
            continue
        blocks.append((h, hold, total, scale))
    out = []
    for n, (h, hold, total, scale) in enumerate(blocks):
        nxt = blocks[n + 1][0] if n + 1 < len(blocks) else len(rows)
        kv_end = nxt - 14 if n + 1 < len(blocks) else len(rows)
        name, date = scheme_title(rows, h, h, sheet)
        # futures sold against shares the fund holds (arbitrage / hedging), from the main table or a derivatives table
        shorts = [[r[0], r[5]] for r in hold if r[7] == "deriv" and (r[5] or 0) < 0]
        for eh, eend, ecol in extras:
            if h < eh < nxt:
                drows, _, _ = parse_block(rows, eh, eend, ecol, force_scale=scale)
                shorts += [[r[0], r[5]] for r in drows if (r[5] or 0) < 0 and re.search(r"fut|short", r[0], re.I)]
        out.append({"name": name, "date": date.isoformat() if date else None, "sheet": sheet,
                    "rows": hold, "sum": round(total, 2), "kv": key_values(rows, h, max(h + 1, kv_end)),
                    "shorts": shorts})
    return out


def parse_file(path: Path) -> list[dict]:
    res = []
    for fname, data in files_in(path):
        try:
            for sn, rows in sheets_of(data, fname):
                for s in parse_sheet(sn, rows):
                    s["src"] = fname.split("/")[-1][:120]
                    res.append(s)
        except Exception as e:  # noqa: BLE001
            print(f"[parse] could not read {fname}: {e}")
    # same scheme twice (consolidated + single files): keep the fuller one
    best = {}
    for s in res:
        k = (s["name"].lower(), s["date"])
        if k not in best or len(s["rows"]) > len(best[k]["rows"]):
            best[k] = s
    return list(best.values())


# ------------------------------------------------------------------ batch: raw folders and inbox

def month_of(schemes: list[dict], fallback: str | None) -> str | None:
    ds = sorted(s["date"] for s in schemes if s.get("date"))
    if ds:
        return ds[len(ds) // 2][:7]
    return fallback


def parse_all() -> int:
    """data/raw/portfolios/<amc>/<YYYY-MM>/* and portfolios/inbox/** -> data/store/portfolios/<YYYY-MM>/<amc>.json"""
    STORE.mkdir(parents=True, exist_ok=True)
    done = 0
    if RAW_DIR.exists():
        for amc_dir in sorted(p for p in RAW_DIR.iterdir() if p.is_dir()):
            for mdir in sorted(p for p in amc_dir.iterdir() if p.is_dir()):
                out = STORE / mdir.name / f"{amc_dir.name}.json"
                stamp = max((f.stat().st_mtime for f in mdir.rglob("*") if f.is_file()), default=0)
                if out.exists() and out.stat().st_mtime >= stamp:
                    continue
                schemes = []
                for f in sorted(mdir.iterdir()):
                    if f.is_file():
                        schemes += parse_file(f)
                meta = json.loads((mdir / "_source.json").read_text()) if (mdir / "_source.json").exists() else {}
                out.parent.mkdir(parents=True, exist_ok=True)
                out.write_text(json.dumps({"amc": meta.get("amc", amc_dir.name), "month": mdir.name,
                                           "files": meta.get("files", []), "schemes": schemes}, separators=(",", ":")))
                print(f"[parse] {amc_dir.name} {mdir.name}: {len(schemes)} schemes")
                done += 1
    if INBOX.exists():
        files = [p for p in INBOX.rglob("*") if p.is_file() and p.suffix.lower() in EXCEL + (".zip",)]
        for f in files:
            schemes = parse_file(f)
            if not schemes:
                print(f"[parse] inbox {f.name}: no portfolio table found")
                continue
            m = month_of(schemes, None)
            if not m:
                print(f"[parse] inbox {f.name}: no 'as on' date found, skipped")
                continue
            slug = "inbox-" + re.sub(r"[^a-z0-9]+", "-", f.stem.lower()).strip("-")[:60]
            out = STORE / m / f"{slug}.json"
            out.parent.mkdir(parents=True, exist_ok=True)
            out.write_text(json.dumps({"amc": None, "month": m, "files": [f.name], "schemes": schemes, "inbox": True},
                                      separators=(",", ":")))
            print(f"[parse] inbox {f.name}: {len(schemes)} schemes for {m}")
            done += 1
    return done


def main(argv):
    if argv and argv[0] == "--all":
        parse_all()
        return
    for a in argv:
        for s in parse_file(Path(a)):
            cls = {}
            for r in s["rows"]:
                cls[r[7]] = cls.get(r[7], 0) + (r[5] or 0)
            print(f"{s['src']} | {s['sheet']} | {s['name']} | {s['date']} | {len(s['rows'])} rows | sum {s['sum']} | "
                  + ", ".join(f"{k} {v:.1f}" for k, v in sorted(cls.items(), key=lambda x: -x[1])) + f" | kv {s['kv']}")


if __name__ == "__main__":
    main(sys.argv[1:])
