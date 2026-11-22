"""Key ratios from monthly NAVs (AMFI data only).

Every ratio here is computed from month-end NAVs that come from AMFI's NAV
history, so the numbers can be checked by anyone with the same NAVs.

* Risk-free rate: the median monthly return of overnight funds (AMFI NAVs),
  so no outside data is needed.
* Market proxy: an index fund that tracks the category's usual benchmark
  (the same "px" fund the dashboard already uses for rolling returns).
  Beta, alpha, R-squared, capture ratios and information ratio are therefore
  "against <index fund name>", which the page says in words.

All inputs are lists of month-end NAVs aligned on the same month index.
Returns are simple monthly returns. Ratios are annualised the usual way
(standard deviation x sqrt(12), mean x 12).
"""
from __future__ import annotations

import math
from typing import Iterable, Sequence

import numpy as np

WINDOWS = (3, 5)          # years
MIN_MONTHS = {3: 36, 5: 60}


def monthly_returns(nav: Sequence[float | None]) -> np.ndarray:
    """Simple monthly returns; NaN where either month is missing."""
    a = np.array([np.nan if v is None else float(v) for v in nav], dtype=float)
    r = np.full(a.shape, np.nan)
    if len(a) > 1:
        with np.errstate(divide="ignore", invalid="ignore"):
            r[1:] = a[1:] / a[:-1] - 1.0
    r[~np.isfinite(r)] = np.nan
    return r


def align(series: dict[str, tuple[int, Sequence[float | None]]], total: int) -> dict[str, np.ndarray]:
    """Place each (m0, navs) series on a common month axis of length `total`."""
    out = {}
    for k, (m0, nav) in series.items():
        a = np.full(total, np.nan)
        vals = np.array([np.nan if v is None else float(v) for v in nav], dtype=float)
        end = min(total, m0 + len(vals))
        if end > m0 >= 0:
            a[m0:end] = vals[: end - m0]
        out[k] = a
    return out


def _r(x: float | None, nd: int = 2):
    if x is None or not math.isfinite(x):
        return None
    return round(float(x), nd)


def _geo_ann(r: np.ndarray) -> float:
    """Annualised compound return of a set of monthly returns."""
    if len(r) == 0:
        return float("nan")
    return float(np.prod(1.0 + r) ** (12.0 / len(r)) - 1.0)


def ratios_window(f: np.ndarray, rf: np.ndarray, b: np.ndarray | None) -> dict | None:
    """Ratios for one window. f, rf, b are monthly returns of equal length.

    Returns None when the fund lacks a full window.
    """
    n = len(f)
    if n == 0 or np.isnan(f).any():
        return None
    rf = np.where(np.isnan(rf), np.nanmedian(rf) if np.isfinite(rf).any() else 0.0, rf)
    ex = f - rf
    sd = float(np.std(f, ddof=1)) * math.sqrt(12)
    out: dict = {
        "sd": _r(sd * 100),
        "ret": _r(_geo_ann(f) * 100),
        "pos": _r(float((f > 0).mean()) * 100, 0),     # % of months with a gain
        "worst": _r(float(f.min()) * 100, 1),          # worst single month
        "best": _r(float(f.max()) * 100, 1),
    }
    ex_ann = float(ex.mean()) * 12
    out["sharpe"] = _r(ex_ann / sd) if sd > 0 else None
    down = np.minimum(ex, 0.0)
    dd = math.sqrt(float((down ** 2).mean())) * math.sqrt(12)
    out["sortino"] = _r(ex_ann / dd) if dd > 0 else None

    if b is not None and not np.isnan(b).any() and float(np.std(b)) > 0:
        bex = b - rf
        cov = float(np.cov(f, b, ddof=1)[0, 1])
        var = float(np.var(b, ddof=1))
        beta = cov / var
        corr = float(np.corrcoef(f, b)[0, 1])
        alpha = (float(ex.mean()) - beta * float(bex.mean())) * 12
        diff = f - b
        te = float(np.std(diff, ddof=1)) * math.sqrt(12)
        up, dn = b > 0, b < 0
        uc = (_geo_ann(f[up]) / _geo_ann(b[up]) * 100) if up.sum() >= 6 and _geo_ann(b[up]) != 0 else None
        dc = (_geo_ann(f[dn]) / _geo_ann(b[dn]) * 100) if dn.sum() >= 6 and _geo_ann(b[dn]) != 0 else None
        out.update({
            "beta": _r(beta),
            "alpha": _r(alpha * 100),
            "r2": _r(corr * corr * 100, 0),
            "te": _r(te * 100),
            "ir": _r((float(diff.mean()) * 12) / te) if te > 0 else None,
            "upcap": _r(uc, 0),
            "dncap": _r(dc, 0),
            "treynor": _r(ex_ann / beta * 100) if beta not in (0,) else None,
        })
    return out


def fund_ratios(fr: np.ndarray, rf: np.ndarray, br: np.ndarray | None, last: int) -> dict:
    """Ratios over 3Y and 5Y windows ending at month index `last` (inclusive)."""
    res = {}
    for y in WINDOWS:
        m = MIN_MONTHS[y]
        s = last - m + 1
        if s < 1:
            continue
        f = fr[s:last + 1]
        if np.isnan(f).any():
            continue
        b = br[s:last + 1] if br is not None else None
        if b is not None and np.isnan(b).any():
            b = None
        w = ratios_window(f, rf[s:last + 1], b)
        if w:
            res[str(y)] = w
    return res


def riskfree_from_overnight(navs: Iterable[np.ndarray]) -> np.ndarray:
    """Median monthly return across overnight funds, month by month."""
    rs = [monthly_returns(list(a)) for a in navs]
    if not rs:
        raise ValueError("no overnight funds to build a risk-free rate")
    m = np.vstack(rs)
    with np.errstate(all="ignore"):
        med = np.nanmedian(m, axis=0)
    # Before overnight funds existed (2019) use liquid-fund-like 0.5%/month fallback
    # only where nothing is available; this is flagged in the page notes.
    med = np.where(np.isfinite(med), med, np.nan)
    return med


def category_stats(values: list[float | None]) -> dict | None:
    v = np.array([x for x in values if x is not None and math.isfinite(x)], dtype=float)
    if len(v) < 3:
        return None
    return {"med": _r(float(np.median(v))), "q1": _r(float(np.percentile(v, 25))), "q3": _r(float(np.percentile(v, 75)))}


# Short keys used in funds.json (3-year window), so the category table can sort on them.
SUMMARY_KEYS = {
    "sd": "sd3", "sharpe": "sh3", "sortino": "so3", "beta": "be3",
    "alpha": "al3", "r2": "rq3", "upcap": "uc3", "dncap": "dc3", "ir": "ir3",
}


def summary_fields(r: dict) -> dict:
    w = r.get("3") or {}
    return {short: w.get(long) for long, short in SUMMARY_KEYS.items() if w.get(long) is not None}
