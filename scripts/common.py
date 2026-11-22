"""Shared helpers: AMFI NAV text parsing, scheme classification, name cleaning, NAV store.

Every number in the dashboard starts from AMFI files parsed here.
"""
from __future__ import annotations

import datetime as dt
import json
import os
import re
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DATA = ROOT / "data"
RAW = DATA / "raw"
STORE_PATH = DATA / "store" / "navstore.sqlite"
SITE_DATA = ROOT / "site" / "data"

EPOCH = dt.date(1970, 1, 1)


# --------------------------------------------------------------------------- dates

def parse_date(s: str) -> dt.date | None:
    s = (s or "").strip()
    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d-%m-%Y", "%d/%m/%Y", "%d-%B-%Y"):
        try:
            return dt.datetime.strptime(s, fmt).date()
        except ValueError:
            pass
    return None


def day_num(d: dt.date) -> int:
    return (d - EPOCH).days


def num_day(n: int) -> dt.date:
    return EPOCH + dt.timedelta(days=int(n))


def amfi_date(d: dt.date) -> str:
    return d.strftime("%d-%b-%Y")


# --------------------------------------------------------------------------- AMFI NAV text

_CAT_RE = re.compile(r"^(.*?Schemes?)\s*\((.+)\)\s*$", re.I)


def parse_nav_text(text: str):
    """Parse an AMFI NAV text file (NAVAll.txt or NAV history report).

    Works with both the classic ';' format and the newer '|' format, and with
    either column order. Yields dicts: code, name, amc, category, type, isin, isin2, nav, date.
    """
    delim = None
    cols: dict[str, int] = {}
    amc = ""
    category = ""
    stype = ""
    for raw in text.splitlines():
        line = raw.strip().lstrip("﻿")
        if not line:
            continue
        if delim is None or not cols:
            if "scheme code" in line.lower():
                delim = "|" if line.count("|") > line.count(";") else ";"
                heads = [h.strip().lower() for h in line.split(delim)]
                for i, h in enumerate(heads):
                    if "scheme code" in h:
                        cols["code"] = i
                    elif "scheme name" in h:
                        cols["name"] = i
                    elif "net asset value" in h:
                        cols["nav"] = i
                    elif h == "date" or h.endswith(" date") or h == "nav date":
                        cols["date"] = i
                    elif "isin" in h and "reinvest" in h:
                        cols["isin2"] = i
                    elif "isin" in h:
                        cols["isin"] = i
                continue
            # header not seen yet: skip preamble
            continue
        if delim not in line:
            m = _CAT_RE.match(line)
            if m:
                stype = m.group(1).replace("Schemes", "").replace("Scheme", "").strip()
                category = re.sub(r"\s+", " ", m.group(2)).strip()
            else:
                amc = re.sub(r"\s+", " ", line).strip()
            continue
        parts = [p.strip() for p in line.split(delim)]
        try:
            code = int(parts[cols["code"]])
        except (KeyError, ValueError, IndexError):
            continue
        try:
            nav = float(parts[cols["nav"]].replace(",", ""))
        except (KeyError, ValueError, IndexError):
            continue
        if not (nav > 0):
            continue
        d = parse_date(parts[cols["date"]]) if "date" in cols and cols["date"] < len(parts) else None
        if d is None:
            continue
        yield {
            "code": code,
            "name": re.sub(r"\s+", " ", parts[cols["name"]]).strip() if "name" in cols else "",
            "amc": amc,
            "category": category,
            "type": stype,
            "isin": parts[cols["isin"]] if "isin" in cols and cols["isin"] < len(parts) else "",
            "isin2": parts[cols["isin2"]] if "isin2" in cols and cols["isin2"] < len(parts) else "",
            "nav": nav,
            "date": d,
        }


# --------------------------------------------------------------------------- classification

GROUP_ORDER = ["Equity", "Debt", "Hybrid", "Life cycle", "Index funds", "ETFs",
               "Fund of funds", "Solution oriented", "Other"]

SUB_ORDER = {
    "Equity": ["large cap", "large & mid", "large and mid", "mid cap", "small cap", "multi cap",
               "flexi cap", "focused", "value", "contra", "dividend yield", "elss",
               "sectoral", "thematic"],
    "Debt": ["overnight", "liquid", "ultra short", "low duration", "money market",
             "short duration", "medium duration", "medium to long", "long duration",
             "dynamic bond", "corporate bond", "credit risk", "banking and psu", "banking & psu",
             "gilt fund with 10", "gilt", "floater", "sectoral debt"],
    "Hybrid": ["conservative", "balanced hybrid", "aggressive", "dynamic asset", "balanced advantage",
               "multi asset", "arbitrage", "equity savings"],
}

_DEBT_LIKE = re.compile(r"gilt|\bsdl\b|bond|g-?sec|crisil|ibx|liquid|1d rate|overnight|money market|"
                        r"\bpsu\b|t-?bill|target maturity|debt|corporate|treasury|cpse bond|bharat bond|"
                        r"\bgsec\b|state development", re.I)
_GOLD_LIKE = re.compile(r"gold|silver|commodit", re.I)
_INTL_LIKE = re.compile(r"nasdaq|s&p|\bus\b|u\.s\.|hang seng|global|world|international|overseas|nyse|"
                        r"msci|china|japan|taiwan|europe|emerging|asia|fang", re.I)


def split_category(category: str) -> tuple[str, str]:
    c = re.sub(r"\s+", " ", category or "").strip()
    if " - " in c:
        g, s = c.split(" - ", 1)
    else:
        g, s = c, c
    return g.strip(), s.strip()


def group_of(category: str) -> str:
    g, s = split_category(category)
    gl, sl = g.lower(), s.lower()
    if "life cycle" in gl or "life cycle" in sl or "lifecycle" in sl:
        return "Life cycle"
    if gl.startswith("equity"):
        return "Equity"
    if gl.startswith("debt"):
        return "Debt"
    if gl.startswith("hybrid"):
        return "Hybrid"
    if gl.startswith("solution"):
        return "Solution oriented"
    if "index" in sl:
        return "Index funds"
    if "etf" in sl:
        return "ETFs"
    if "fof" in sl or "fund of fund" in sl:
        return "Fund of funds"
    return "Other"


def sub_name(category: str) -> str:
    g, s = split_category(category)
    return s


def sub_rank(group: str, sub: str) -> int:
    order = SUB_ORDER.get(group)
    sl = sub.lower()
    if not order:
        return 0
    for i, key in enumerate(order):
        if key in sl:
            return i
    return len(order)


def asset_kind(group: str, sub: str, name: str) -> str:
    """'equity' | 'debt' | 'other' — used for tax treatment and the main holding period."""
    sl, nl = sub.lower(), name.lower()
    if group == "Debt":
        return "debt"
    if group == "Equity":
        return "equity"
    if group == "Hybrid":
        if any(k in sl for k in ("arbitrage", "equity savings", "aggressive", "dynamic asset", "balanced advantage")):
            return "equity"
        if "conservative" in sl:
            return "debt"
        return "other"
    if group in ("Index funds", "ETFs", "Fund of funds", "Other"):
        if _GOLD_LIKE.search(nl) or _GOLD_LIKE.search(sl):
            return "other"
        if _DEBT_LIKE.search(nl) or _DEBT_LIKE.search(sl):
            return "debt"
        if _INTL_LIKE.search(nl) or "overseas" in sl:
            return "other"
        if group == "Fund of funds":
            return "other"
        return "equity"
    return "other"


def horizon_for(group: str, sub: str, kind: str) -> int:
    """Main holding period (years) used for rolling-return ranking in this category."""
    sl = sub.lower()
    if kind == "debt" or "arbitrage" in sl:
        return 1
    return 3


# --------------------------------------------------------------------------- names and plans

_IDCW = re.compile(r"idcw|dividend|bonus|payout|re-?invest|income distribution|\bdiv\b|"
                   r"segregat|unclaimed|half yearly|quarterly|monthly|weekly|daily|annual|fortnightly", re.I)
_GROWTH = re.compile(r"growth|\bgr\b|cumulative|accumulat", re.I)
_DIRECT = re.compile(r"\bdirect\b|\bdir\b", re.I)
_LEGACY = re.compile(r"institutional|\bretail\b|super inst|premium|\binst\b|\bplan a\b|\bplan b\b|old plan", re.I)

_PLAN_TOKENS = re.compile(
    r"\b(direct|regular|plan|option|growth|gr|retail|institutional|super|premium|inst|cumulative|"
    r"accumulation|fund\s*-\s*$)\b", re.I)


def plan_of(name: str, group: str) -> str | None:
    """'R' regular, 'D' direct, 'E' single-plan ETF; None if not a growth option we show."""
    if group == "ETFs":
        if re.search(r"idcw|dividend|payout", name, re.I):
            return None
        return "E"
    if _IDCW.search(name) and not _GROWTH.search(name):
        return None
    if re.search(r"idcw|dividend|bonus|payout|segregat|unclaimed", name, re.I):
        return None
    if not _GROWTH.search(name):
        return None
    return "D" if _DIRECT.search(name) else "R"


def clean_name(name: str) -> str:
    """Fund name without plan/option words, for display and matching."""
    n = re.sub(r"\(.*?\)", " ", name)
    parts = re.split(r"\s+-\s+|\s*-\s*(?=(?:direct|regular|growth|plan|option)\b)", n, flags=re.I)
    keep = []
    for p in parts:
        pl = p.lower().strip()
        if not pl:
            continue
        if re.fullmatch(r"(direct|regular|retail|institutional)?\s*(plan)?\s*(growth)?\s*(option|plan)?\s*"
                        r"(direct|regular)?\s*(plan)?", pl):
            continue
        keep.append(p.strip())
    n = " - ".join(keep) if keep else name
    n = re.sub(r"\b(direct|regular)\s+plan\b|\bgrowth\s+(option|plan)\b|\b(direct|regular)\s+growth\b", " ", n, flags=re.I)
    n = re.sub(r"\s+-\s*$", "", n)
    n = re.sub(r"\s+", " ", n).strip(" -")
    return n


def match_key(name: str) -> str:
    n = clean_name(name).lower()
    n = n.replace("&", " and ")
    n = re.sub(r"\bfund\b|\bscheme\b|\bthe\b|\bplan\b|\boption\b|\bgrowth\b|\bdirect\b|\bregular\b", " ", n)
    n = re.sub(r"[^a-z0-9]+", " ", n)
    return re.sub(r"\s+", " ", n).strip()


def dedupe_key(name: str) -> str:
    k = re.sub(r"\b(retail|institutional|super|premium|inst|plan a|plan b|old)\b", " ", match_key(name))
    return re.sub(r"\s+", " ", k).strip()


def amc_key(amc: str) -> str:
    a = (amc or "").lower().replace("&", " and ")
    a = re.sub(r"mutual fund|asset management|company|limited|ltd|private|pvt|amc|\bmf\b", " ", a)
    return re.sub(r"[^a-z0-9]+", "", a)


def is_legacy(name: str) -> bool:
    return bool(_LEGACY.search(name))


# --------------------------------------------------------------------------- store

def open_store(path: Path = STORE_PATH) -> sqlite3.Connection:
    path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(path)
    con.execute("PRAGMA journal_mode=WAL")
    con.execute("CREATE TABLE IF NOT EXISTS nav (code INTEGER, d INTEGER, nav REAL, PRIMARY KEY(code, d)) WITHOUT ROWID")
    con.execute("CREATE TABLE IF NOT EXISTS meta (k TEXT PRIMARY KEY, v TEXT)")
    return con


def store_put(con: sqlite3.Connection, rows) -> int:
    """rows: iterable of (code, date, nav)."""
    data = [(int(c), day_num(d), float(v)) for c, d, v in rows]
    if data:
        con.executemany("INSERT OR REPLACE INTO nav VALUES (?,?,?)", data)
    return len(data)


def meta_get(con, k, default=None):
    r = con.execute("SELECT v FROM meta WHERE k=?", (k,)).fetchone()
    return json.loads(r[0]) if r else default


def meta_set(con, k, v):
    con.execute("INSERT OR REPLACE INTO meta VALUES (?,?)", (k, json.dumps(v)))


def read_json(path: Path, default=None):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def write_json(path: Path, obj, compact=True):
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        if compact:
            json.dump(obj, f, separators=(",", ":"), ensure_ascii=False)
        else:
            json.dump(obj, f, indent=1, ensure_ascii=False)
    os.replace(tmp, path)
