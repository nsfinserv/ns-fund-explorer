"""Match scheme names from AMC files / AMFI risk data to AMFI NAV scheme names.

AMC portfolio files name a scheme once ("HDFC Value Fund"), while AMFI's NAV
file lists each plan and option ("HDFC Value Fund - Growth Option - Direct Plan").
We strip plan/option words, normalise spelling, and pick the closest scheme of
the same fund house. Anything below the score threshold is left unmatched and
reported, never guessed.
"""
from __future__ import annotations

import re
from difflib import SequenceMatcher

PLAN_WORDS = r"""
 regular|direct|plan|plans|option|options|growth|idcw|dividend|payout|reinvestment|reinvest|
 bonus|daily|weekly|fortnightly|monthly|quarterly|half[- ]?yearly|annual|yearly|retail|institutional|
 super|premium|sweep|transfer|of|capital|withdrawal|income|distribution|cum|scheme|the|an|a|open|ended|
 erstwhile|formerly|known|as|earlier|renamed|series|segregated|portfolio|main
"""
PLAN_RE = re.compile(r"\b(?:" + PLAN_WORDS.replace("\n", "").replace(" ", "") + r")\b", re.I)
SYNONYMS = [
    (r"\b(bluechip|blue chip)\b", "large cap"),
    (r"\b(largecap)\b", "large cap"), (r"\b(midcap)\b", "mid cap"), (r"\b(smallcap)\b", "small cap"),
    (r"\b(multicap)\b", "multi cap"), (r"\b(flexicap)\b", "flexi cap"),
    (r"\btax saver\b", "elss"), (r"\btax saving\b", "elss"), (r"\btax relief\b", "elss"),
    (r"\bbal(anced)? adv(antage)?\b", "balanced advantage"),
    (r"\bpsu\b", "psu"), (r"&", " and "), (r"\bfof\b", "fund of fund"), (r"\betf\b", "etf"),
    (r"\bopp(s|ortunities|ortunity)?\b", "opportunities"),
    (r"\bmgmt\b", "management"), (r"\bltd\b", "limited"), (r"\bpru\b", "prudential"),
    (r"\bmf\b", ""), (r"\bmutual fund\b", ""), (r"\bfund\b", ""),
]


def amc_stem(amc: str) -> str:
    s = re.sub(r"\b(mutual fund|mf|asset management|company|limited|ltd|amc)\b", " ", amc.lower())
    return re.sub(r"[^a-z0-9 ]", " ", s).split()[0] if s.strip() else ""


def norm(name: str, amc: str | None = None) -> str:
    s = name.lower()
    s = re.sub(r"\(.*?\)", " ", s)                         # (erstwhile ...), (G)
    s = re.sub(r"\[.*?\]", " ", s)
    for pat, rep in SYNONYMS:
        s = re.sub(pat, rep, s)
    s = re.sub(r"[^a-z0-9 ]", " ", s)
    s = PLAN_RE.sub(" ", s)
    if amc:
        for w in re.sub(r"[^a-z0-9 ]", " ", amc.lower()).split():
            if w not in {"mutual", "fund", "india"}:
                s = re.sub(rf"\b{re.escape(w)}\b", " ", s)
    return " ".join(s.split())


def score(a: str, b: str) -> float:
    if not a or not b:
        return 0.0
    ta, tb = set(a.split()), set(b.split())
    jac = len(ta & tb) / len(ta | tb)
    seq = SequenceMatcher(None, a, b).ratio()
    exact = 1.0 if a == b else 0.0
    # numbers (e.g. "nifty 50" vs "nifty next 50", "2027" target maturity) must agree
    na, nb = set(re.findall(r"\d+", a)), set(re.findall(r"\d+", b))
    num_pen = 0.35 if na != nb else 0.0
    return max(exact, 0.55 * jac + 0.45 * seq) - num_pen


def best_match(name: str, candidates: dict[str, str], amc: str | None = None, threshold: float = 0.72):
    """candidates: {key: scheme name}. Returns (key, score) or (None, best_score)."""
    n = norm(name, amc)
    best, bs, bn = None, 0.0, None
    second = 0.0
    for k, v in candidates.items():
        nv = norm(v, amc)
        sc = score(n, nv)
        if sc > bs:
            if nv != bn:
                second = bs
            best, bs, bn = k, sc, nv
        elif sc > second and nv != bn:
            second = sc
    # reject ties between two different schemes (e.g. "Nifty 50 Index" vs "Nifty 50 ETF")
    if best is None or bs < threshold or (bs - second < 0.03 and bs < 0.98):
        return None, bs
    return best, bs
