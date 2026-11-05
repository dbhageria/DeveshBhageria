"""COMEX gold options from the CME Daily Bulletin PDFs (free, manual download).

Inputs (dropped into ./inbox by hand -- this module never touches the network):
  * Section 64  "Metal Options"   strike-level settle / volume / OI / delta
  * Section 62  "Metal Futures"   GC settlements by contract month
  * Section 02B "Summary Volume and Open Interest, Metals" (optional cross-check)

load_gold(next_session) returns (book, meta) with the same keys as the
Databento path (cme_gold.build_gold_book):

  book: kref, k, S, T, iv, oi, call, q, exp, S_ref, asof, session
  meta: ref_ticker, tv_ref, hi, lo, iv_fallback, stamps

Parsing is driven by the column headings printed in the PDF (each value is
assigned to the heading nearest it on the page), and every load is checked:
no rows, strikes not clustered near the futures price, unmapped series or a
bad Black-76 delta match all raise BulletinError.  Run

    python bulletin_gold.py --inspect inbox/

to dump block titles, series, column layout, trade date and raw rows.
"""
from __future__ import annotations

import argparse
import datetime as dt
import re
import sys
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path
from zoneinfo import ZoneInfo

import numpy as np

import cme_gold as cg

NY = ZoneInfo("America/New_York")
UTC = dt.timezone.utc
STRIKE_BAND = 0.15        # strikes kept within +-15% of the reference future
DELTA_MAD_MAX = 0.10      # median |model delta - bulletin delta| allowed
OI_TOL = 0.02             # Section 02B / TOTAL-line cross-check tolerance


class BulletinError(RuntimeError):
    """The bulletin could not be parsed into a trustworthy gold book."""


class NoBulletinData(BulletinError):
    """The required PDFs are not in the inbox."""


# --------------------------------------------------------------------------
# PDF -> positioned lines
# --------------------------------------------------------------------------
@dataclass
class Word:
    x0: float
    x1: float
    text: str

    @property
    def xc(self):
        return 0.5 * (self.x0 + self.x1)


@dataclass
class Line:
    page: int
    top: float
    words: list = field(default_factory=list)

    @property
    def text(self):
        return " ".join(w.text for w in self.words)

    @property
    def tokens(self):
        return [w.text for w in self.words]


def read_lines(path, first_page_only=False) -> list[Line]:
    p = Path(path)
    st = p.stat()
    return list(_read_lines(str(p.resolve()), st.st_mtime_ns, st.st_size, first_page_only))


@lru_cache(maxsize=16)
def _read_lines(path, _mtime, _size, first_page_only) -> tuple:
    import pdfplumber

    out: list[Line] = []
    with pdfplumber.open(path) as pdf:
        pages = pdf.pages[:1] if first_page_only else pdf.pages
        for pno, page in enumerate(pages):
            words = page.extract_words(x_tolerance=1.5, y_tolerance=2,
                                       keep_blank_chars=False)
            words.sort(key=lambda w: (round(w["top"], 1), w["x0"]))
            cur: Line | None = None
            for w in words:
                if cur is None or abs(w["top"] - cur.top) > 2.0:
                    cur = Line(pno, w["top"])
                    out.append(cur)
                cur.words.append(Word(w["x0"], w["x1"], w["text"]))
    for ln in out:
        ln.words.sort(key=lambda w: w.x0)
    return tuple(out)


def first_page_text(path, n_lines=12) -> str:
    lines = read_lines(path, first_page_only=True)[:n_lines]
    return "\n".join(ln.text for ln in lines)


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------
MONTHS = {m: i + 1 for i, m in enumerate(cg.MONTH_ABBR)}
_MONTH_RE = re.compile(r"^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2}|\d{4})?$")
_NUM_RE = re.compile(r"^[+-]?(\d[\d,]*\.?\d*|\.\d+)")
_EMPTY = {"", "----", "---", "--", "-", "N/A", "NA"}


def num(tok) -> float:
    """'1,204.20B' -> 1204.2, '----' -> nan, 'UNCH' -> 0, '-.45' -> -0.45."""
    if tok is None:
        return np.nan
    t = str(tok).strip().upper()
    if t in _EMPTY:
        return np.nan
    if t in ("UNCH", "UNC"):
        return 0.0
    m = _NUM_RE.match(t)
    if not m:
        return np.nan
    s = m.group(0).replace(",", "")
    try:
        return float(s)
    except ValueError:
        return np.nan


def is_number(tok) -> bool:
    return bool(_NUM_RE.match(str(tok).strip())) and not np.isnan(num(tok))


def month_label(tokens, i=0):
    """('DEC26') or ('DEC', '26') at tokens[i] -> ((2026, 12), n_consumed)."""
    if i >= len(tokens):
        return None, 0
    t = tokens[i].upper()
    m = _MONTH_RE.match(t)
    if not m:
        return None, 0
    yr, used = m.group(2), 1
    if yr is None:
        if i + 1 < len(tokens) and re.fullmatch(r"\d{2}|\d{4}", tokens[i + 1]):
            yr, used = tokens[i + 1], 2
        else:
            return None, 0
    y = int(yr)
    y = 2000 + y if y < 100 else y
    return (y, MONTHS[m.group(1)]), used


_DATE_RES = [
    re.compile(r"\b(MON|TUE|WED|THU|FRI|SAT|SUN)[A-Z]*,?\s+([A-Z]{3})[A-Z]*\.?\s+(\d{1,2}),?\s+(\d{4})\b"),
    re.compile(r"\b([A-Z]{3})[A-Z]*\.?\s+(\d{1,2}),\s+(\d{4})\b"),
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
]


def trade_date_of(path) -> dt.date:
    """Trade date printed in the bulletin page header (falls back to a
    YYYYMMDD stamp in the filename)."""
    txt = first_page_text(path).upper()
    for i, rx in enumerate(_DATE_RES):
        m = rx.search(txt)
        if not m:
            continue
        try:
            if i == 0 and m.group(2) in MONTHS:
                return dt.date(int(m.group(4)), MONTHS[m.group(2)], int(m.group(3)))
            if i == 1 and m.group(1) in MONTHS:
                return dt.date(int(m.group(3)), MONTHS[m.group(1)], int(m.group(2)))
            if i == 2:
                return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
        except ValueError:
            pass
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", Path(path).name)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    raise BulletinError(f"{Path(path).name}: no trade date found in page header:\n{txt}")


# --------------------------------------------------------------------------
# Column headings
# --------------------------------------------------------------------------
HEADER_VOCAB = {
    "STRIKE", "OPEN", "HIGH", "LOW", "CLOSE", "CLOSING", "RANGE", "SETT", "SETT.",
    "SETTLE", "SETTLEMENT", "PRICE", "PT", "PT.", "CHGE", "CHGE.", "CHG", "CHANGE",
    "DELTA", "EXERCISES", "EXER", "VOLUME", "VOL", "TRADES", "CLEARED", "RTH",
    "GLOBEX", "PNT", "CLEARPORT", "FLOOR", "INTEREST", "INT", "CONTRACT", "OI",
    "+/-", "NET", "EST", "EST.", "TOTAL", "BLOCK", "EFP", "EFR", "EOO", "EFS",
    "TAS", "AFTER", "HOURS", "OUTCRY", "LIFE", "DELIVERY", "MONTH", "PREVIOUS",
    "DAY", "FUTURES", "OPTIONS", "PRODUCT", "CODE", "NAME", "COMBINED", "CALLS",
    "PUTS", "#", "CHG.",
}
_KEY_HEADS = ("SETT", "INTEREST", "VOLUME", "DELTA", "STRIKE")


def _clean(t):
    return t.upper().strip(".:()")


def is_header_line(ln: Line) -> bool:
    toks = [_clean(t) for t in ln.tokens]
    if len(toks) < 3 or any(is_number(t) for t in ln.tokens):
        return False
    vocab = sum((t in HEADER_VOCAB) or (t + "." in HEADER_VOCAB) for t in toks)
    return vocab / len(toks) >= 0.6 and any(any(k in t for k in _KEY_HEADS) for t in toks)


@dataclass
class Column:
    x0: float
    x1: float
    label: str
    field: str

    @property
    def xc(self):
        return 0.5 * (self.x0 + self.x1)


def _classify(label: str) -> str:
    L = label.upper()
    if "STRIKE" in L:
        return "strike"
    if "SETT" in L:
        return "settle"
    if "DELTA" in L:
        return "delta"
    if "EXER" in L:
        return "exercises"
    if "INTEREST" in L or re.search(r"\bOI\b", L):
        if "CHANGE" in L or "CHG" in L or "+/-" in L:
            return "oi_chg"
        return "oi"
    if "VOL" in L or "TRADES" in L or "CLEARED" in L:
        return "volume"
    if "CONTRACT" in L or "LIFE" in L:
        return "contract_hilo"
    if "CHGE" in L or L.startswith("PT") or "CHANGE" in L:
        return "chg"
    if "CLOS" in L:
        return "close"
    if "HIGH" in L:
        return "high"
    if "LOW" in L:
        return "low"
    if "OPEN" in L:
        return "open"
    return "other"


def build_columns(header_lines: list[Line]) -> list[Column]:
    """Merge heading words into column labels: horizontally when the gap is
    about one space, vertically when their x-extents overlap."""
    labels = []
    for ln in header_lines:
        grp: list[Word] = []
        for w in ln.words:
            if grp:
                cw = (grp[-1].x1 - grp[-1].x0) / max(len(grp[-1].text), 1)
                if w.x0 - grp[-1].x1 <= 1.6 * cw:
                    grp.append(w)
                    continue
                labels.append(grp)
            grp = [w]
        if grp:
            labels.append(grp)
    cols: list[list] = []  # [x0, x1, [texts]]
    for grp in labels:
        x0, x1 = grp[0].x0, grp[-1].x1
        txt = " ".join(w.text for w in grp)
        for c in cols:
            if x0 <= c[1] + 1 and x1 >= c[0] - 1:
                c[0], c[1] = min(c[0], x0), max(c[1], x1)
                c[2].append(txt)
                break
        else:
            cols.append([x0, x1, [txt]])
    cols.sort(key=lambda c: c[0])
    out = [Column(c[0], c[1], " ".join(c[2]), _classify(" ".join(c[2]))) for c in cols]
    for a, b in zip(out, out[1:]):   # 'CONTRACT HIGH' | 'LOW'  -> both contract range
        if a.field == "contract_hilo" and b.label.upper() in ("LOW", "HIGH"):
            b.field = "contract_hilo"
    return out


def assign(words: list[Word], cols: list[Column]) -> dict:
    """Give each value word to the nearest column (interval distance, then
    centre distance).  Returns {field: [raw tokens...]}."""
    out: dict = {}
    for w in words:
        best = min(cols, key=lambda c: (max(c.x0 - w.x1, w.x0 - c.x1, 0.0), abs(c.xc - w.xc)))
        out.setdefault(best.field, []).append(w.text)
    return out


def _val(fields, name, how="first"):
    toks = fields.get(name) or []
    vals = [num(t) for t in toks]
    vals = [v for v in vals if not np.isnan(v)]
    if not vals:
        return np.nan
    return float(sum(vals)) if how == "sum" else vals[0]


class HeaderTracker:
    """Keeps the most recent column layout seen (headings repeat per page)."""

    def __init__(self):
        self.cols: list[Column] | None = None
        self._pending: list[Line] = []
        self.layouts: list[list[Column]] = []

    def feed(self, ln: Line) -> bool:
        if is_header_line(ln):
            if self._pending and (ln.page != self._pending[-1].page
                                  or ln.top - self._pending[-1].top > 25):
                self._pending = []
            self._pending.append(ln)
            self.cols = build_columns(self._pending)
            sig = [(c.label, c.field) for c in self.cols]
            if len(self._pending) > 1 or not self.layouts:
                self.layouts = [lay for lay in self.layouts
                                if [(c.label, c.field) for c in lay] != sig] + [self.cols]
            return True
        self._pending = []
        return False


def _is_banner(ln: Line) -> bool:
    t = ln.text.upper()
    return ("BULLETIN" in t or re.search(r"\bPG\s?\d", t) is not None
            or "DAILY INFORMATION" in t or "CME GROUP" in t
            or any(rx.search(t) for rx in _DATE_RES[:2]))


# --------------------------------------------------------------------------
# Gold option series
# --------------------------------------------------------------------------
_WEEKDAY_LETTER = {"M": 0, "T": 1, "W": 2, "R": 3, "H": 3}
GOLD_CODE_RE = re.compile(r"^(OG|OG[1-5]|G[1-5][MTWRH])$")
# Gold-titled products that are NOT 100 oz options on GC futures.
EXCLUDE_WORDS = ("MICRO", "E-MINI", "EMINI", "MINI", "KILO", "AVERAGE", "APO",
                 "SPREAD", "CSO", "CALENDAR", "ASIAN", "1OZ", "1-OUNCE", "OUNCE",
                 "TAS", "SWAP")


def series_info(code: str):
    """'OG' -> ('monthly', None, None); 'OG2' -> ('weekly', 4, 2) (Friday wk 2);
    'G3W' -> ('weekly', 2, 3) (Wednesday wk 3)."""
    if code == "OG":
        return "monthly", None, None
    if code.startswith("OG"):
        return "weekly", 4, int(code[2])
    return "weekly", _WEEKDAY_LETTER[code[2]], int(code[1])


def series_expiry_and_underlying(code, ym):
    """(expiry date, (gc_year, gc_month)) for the block month label ym."""
    kind, wd, wk = series_info(code)
    y, m = ym
    if kind == "monthly":
        return cg.og_monthly_expiry(y, m), cg.gc_underlying_month(y, m)
    e = cg.weekly_expiry(y, m, wd, wk)
    if e is None:
        raise BulletinError(f"{code} {cg.MONTH_ABBR[m-1]}{y % 100:02d}: month has no week {wk}")
    return e, cg.weekly_underlying(e)


def _title_kind(ln: Line):
    """Classify a non-data line as a product title.
    Returns (kind, code, cp, gold) where kind in {'opt', 'fut', None}."""
    toks = [_clean(t) for t in ln.tokens]
    if not toks or is_number(ln.tokens[0]) or month_label(toks)[0] or toks[0] == "TOTAL":
        return None, None, None, False
    T = " ".join(toks)
    cp = "C" if ("CALL" in toks or "CALLS" in toks) else ("P" if ("PUT" in toks or "PUTS" in toks) else None)
    gold = "GOLD" in T and not any(x in T for x in EXCLUDE_WORDS)
    if cp:
        code = next((t for t in toks if GOLD_CODE_RE.match(t)), None)
        return "opt", code, cp, gold
    if "FUT" in T or "FUTURE" in T or "FUTURES" in T:
        code = next((t for t in toks if t == "GC"), None)
        return "fut", code, None, gold
    return None, None, None, False


@dataclass
class OptRow:
    code: str
    cp: str
    ym: tuple
    strike: float
    settle: float
    delta: float
    volume: float
    oi: float
    page: int
    raw: str


def parse_options(path):
    """Section 64 -> (rows, diag)."""
    lines = read_lines(path)
    hdr = HeaderTracker()
    rows: list[OptRow] = []
    diag = {"titles": [], "skipped_titles": [], "unknown_gold_titles": [],
            "totals": [], "unparsed_in_block": 0, "raw_samples": []}
    block = None   # (code, cp, title)
    ym = None
    for ln in lines:
        if hdr.feed(ln) or _is_banner(ln):
            continue
        kind, code, cp, gold = _title_kind(ln)
        if kind == "opt":
            ym = None
            if gold and code:
                block = (code, cp, ln.text)
                diag["titles"].append(ln.text)
            else:
                block = None
                if gold:
                    diag["unknown_gold_titles"].append(ln.text)
                elif "GOLD" in ln.text.upper():
                    diag["skipped_titles"].append(ln.text)
            continue
        if kind == "fut":
            block = None
            continue
        if block is None:
            continue
        toks = ln.tokens
        if _clean(toks[0]) == "TOTAL":
            if hdr.cols:
                f = assign(ln.words[1:], hdr.cols)
                diag["totals"].append((block[0], block[1], _val(f, "oi", "sum"), ln.text))
            continue
        lab, used = month_label([t.upper() for t in toks])
        words = ln.words
        if lab:
            ym = lab
            words = words[used:]
            if not words:
                continue
        if ym is None or not words or not is_number(words[0].text):
            diag["unparsed_in_block"] += 1
            continue
        if hdr.cols is None:
            raise BulletinError(f"{Path(path).name}: strike rows before any column headings "
                                f"(page {ln.page + 1}): {ln.text}")
        strike = num(words[0].text)
        f = assign(words[1:], [c for c in hdr.cols if c.field != "strike"])
        r = OptRow(block[0], block[1], ym, strike, _val(f, "settle"), _val(f, "delta"),
                   _val(f, "volume", "sum"), _val(f, "oi"), ln.page, ln.text)
        rows.append(r)
        if len(diag["raw_samples"]) < 400:
            diag["raw_samples"].append(r)
    diag["layouts"] = hdr.layouts
    return rows, diag


def parse_futures(path):
    """Section 62 -> ({(y, m): {settle, high, low, oi, volume}}, diag)."""
    lines = read_lines(path)
    hdr = HeaderTracker()
    futs: dict = {}
    diag = {"titles": [], "raw": []}
    in_gc = False
    for ln in lines:
        if hdr.feed(ln) or _is_banner(ln):
            continue
        kind, code, _, gold = _title_kind(ln)
        if kind:
            in_gc = kind == "fut" and code == "GC" and gold
            if in_gc:
                diag["titles"].append(ln.text)
            continue
        if not in_gc:
            continue
        toks = [t.upper() for t in ln.tokens]
        if toks[0] == "TOTAL":
            in_gc = False
            continue
        lab, used = month_label(toks)
        if not lab:
            continue
        if hdr.cols is None:
            raise BulletinError(f"{Path(path).name}: GC rows before any column headings")
        f = assign(ln.words[used:], [c for c in hdr.cols if c.field != "strike"])
        futs[lab] = {"settle": _val(f, "settle"), "high": _val(f, "high"),
                     "low": _val(f, "low"), "oi": _val(f, "oi"),
                     "volume": _val(f, "volume", "sum"), "raw": ln.text}
        diag["raw"].append(ln.text)
    diag["layouts"] = hdr.layouts
    return futs, diag


def parse_02b_gold_options_oi(path) -> float:
    """Total open interest of 100 oz gold options (OG + weeklies) from the
    Section 02B summary."""
    lines = read_lines(path)
    hdr = HeaderTracker()
    total, hits = 0.0, []
    for ln in lines:
        if hdr.feed(ln) or _is_banner(ln) or hdr.cols is None:
            continue
        T = ln.text.upper()
        toks = [_clean(t) for t in ln.tokens]
        code = next((t for t in toks if GOLD_CODE_RE.match(t)), None)
        named = "GOLD" in T and "OPT" in T and not any(x in T for x in EXCLUDE_WORDS)
        if not (code or named) or toks[0] == "TOTAL":
            continue
        vals = [w for w in ln.words if is_number(w.text)]
        oi = _val(assign(vals, hdr.cols), "oi")
        if not np.isnan(oi):
            total += oi
            hits.append(ln.text)
    if not hits:
        raise BulletinError(f"{Path(path).name}: no gold option lines found in Section 02B")
    return total


# --------------------------------------------------------------------------
# Inbox
# --------------------------------------------------------------------------
def section_of(path) -> str | None:
    n = Path(path).name.upper().replace(" ", "").replace("_", "")
    if "SECTION64" in n or "PG64" in n:
        return "s64"
    if "SECTION62" in n or "PG62" in n:
        return "s62"
    if "SECTION02B" in n or "SECTION2B" in n or "PG02B" in n:
        return "s02b"
    t = first_page_text(path).upper()
    if "SUMMARY" in t and "INTEREST" in t and "METAL" in t:
        return "s02b"
    if "METAL" in t and "OPTION" in t:
        return "s64"
    if "METAL" in t and "FUTURE" in t:
        return "s62"
    return None


def find_inputs(inbox) -> dict:
    """{'s64': (path, trade_date), 's62': ..., 's02b': ...} newest by trade date."""
    inbox = Path(inbox)
    best: dict = {}
    for p in sorted(inbox.glob("*.pdf")) + sorted(inbox.glob("*.PDF")):
        try:
            sec = section_of(p)
        except Exception as e:  # unreadable PDF
            raise BulletinError(f"{p.name}: cannot read PDF ({e})") from e
        if not sec:
            continue
        td = trade_date_of(p)
        if sec not in best or td > best[sec][1]:
            best[sec] = (p, td)
    return best


def session_utc(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(13, 30), UTC)


def expiry_utc(day: dt.date) -> dt.datetime:
    """Gold options expire at the 13:30 ET GC settlement."""
    return dt.datetime.combine(day, dt.time(13, 30), NY).astimezone(UTC)


def _years(a: dt.datetime, b: np.ndarray) -> np.ndarray:
    secs = np.array([(x - a).total_seconds() for x in b])
    return np.maximum(secs / (365.0 * 86400.0), cg.YEAR_MIN)


# --------------------------------------------------------------------------
# Book
# --------------------------------------------------------------------------
def build_book(s64, s62, next_session, s02b=None, log=print):
    """Parse the PDFs and return (book, meta).  Raises BulletinError."""
    td64, td62 = trade_date_of(s64), trade_date_of(s62)
    if td64 != td62:
        raise BulletinError(f"trade dates differ: Section 64 {td64} vs Section 62 {td62}")
    trade_date = td64

    futs, _ = parse_futures(s62)
    futs = {k: v for k, v in futs.items() if not np.isnan(v["settle"])}
    if not futs:
        raise BulletinError(f"{Path(s62).name}: zero GC futures rows parsed")
    ref_ym = max(futs, key=lambda k: np.nan_to_num(futs[k]["oi"]))
    if np.isnan(futs[ref_ym]["oi"]):
        raise BulletinError(f"{Path(s62).name}: GC open interest column not found")
    F_ref = futs[ref_ym]["settle"]
    ref_ticker = cg.gc_ticker(*ref_ym)

    rows, diag = parse_options(s64)
    if diag["unknown_gold_titles"]:
        raise BulletinError("unrecognised gold option series (no OG/weekly code): "
                            + " | ".join(diag["unknown_gold_titles"]))
    if not rows:
        raise BulletinError(f"{Path(s64).name}: zero gold option rows parsed")

    # map every series to its expiry and GC future
    ser_cache, series = {}, {}
    for r in rows:
        key = (r.code, r.ym)
        if key not in ser_cache:
            exp, uym = series_expiry_and_underlying(*key)
            if uym not in futs:
                raise BulletinError(f"{r.code} {r.ym}: underlying GC {cg.gc_ticker(*uym)} "
                                    f"not found in Section 62")
            ser_cache[key] = (exp, uym)
            series[f"{r.code} {cg.MONTH_ABBR[r.ym[1]-1]}{r.ym[0] % 100:02d}"] = (
                exp.isoformat(), cg.gc_ticker(*uym))

    exp = np.array([ser_cache[(r.code, r.ym)][0] for r in rows])
    uym = [ser_cache[(r.code, r.ym)][1] for r in rows]
    stale = [r for r, e in zip(rows, exp) if e < trade_date and np.nan_to_num(r.oi) > 0]
    if stale:
        raise BulletinError(f"{len(stale)} rows with open interest have a derived expiry before "
                            f"the trade date {trade_date} (expiry rule mismatch?), e.g. "
                            f"{stale[0].code} {stale[0].ym} {stale[0].raw}")

    K = np.array([r.strike for r in rows])
    settle = np.array([r.settle for r in rows])
    oi = np.nan_to_num(np.array([r.oi for r in rows]))
    bdelta = np.array([r.delta for r in rows])
    call = np.array([r.cp == "C" for r in rows])
    Fu = np.array([futs[u]["settle"] for u in uym])
    kref = K * F_ref / Fu

    # clustering check on everything parsed (before filtering)
    live = (exp > trade_date) & (oi > 0)
    if not live.any():
        raise BulletinError("no live gold option rows with open interest")
    rel = kref[live] / F_ref - 1.0
    w = oi[live]
    order = np.argsort(rel)
    med = rel[order][np.searchsorted(np.cumsum(w[order]), 0.5 * w.sum())]
    in_band = w[np.abs(rel) <= STRIKE_BAND].sum() / w.sum()
    if abs(med) > STRIKE_BAND or in_band < 0.5:
        raise BulletinError(f"strikes are not clustered near the futures price "
                            f"{F_ref:g}: OI-weighted median strike is {100*med:+.1f}% away and "
                            f"{100*in_band:.0f}% of OI is within +-15% -- check strike/price columns")

    # implied vol from the settlement (T measured from the trade-date settle)
    t_settle = expiry_utc(trade_date)
    exp_dt = np.array([expiry_utc(e) for e in exp])
    T_set = _years(t_settle, exp_dt)
    ok = live & np.isfinite(settle) & (settle > 0)
    iv = np.zeros(len(rows))
    iv[ok] = cg.b76_iv(settle[ok], Fu[ok], K[ok], T_set[ok], cg.RATE, call[ok])

    # delta sanity check
    chk = ok & (iv > 0) & np.isfinite(bdelta)
    if chk.sum() < 20:
        raise BulletinError(f"only {int(chk.sum())} rows have both an IV and a bulletin delta "
                            f"-- the delta or settle column was not found")
    dd = np.abs(cg.b76_delta(Fu[chk], K[chk], T_set[chk], iv[chk], cg.RATE, call[chk]) - bdelta[chk])
    du = np.abs(cg.b76_delta(Fu[chk], K[chk], T_set[chk], iv[chk], cg.RATE, call[chk],
                             discounted=False) - bdelta[chk])
    delta_mad = float(min(np.median(dd), np.median(du)))
    if delta_mad > DELTA_MAD_MAX:
        raise BulletinError(f"Black-76 delta vs bulletin delta: median |diff| {delta_mad:.3f} "
                            f"> {DELTA_MAD_MAX} -- settle, delta or expiry mapping is off")

    # open-interest cross-checks
    oi_total = float(oi[exp >= trade_date].sum())
    warnings = []
    tot = [t for t in diag["totals"] if not np.isnan(t[2])]
    if tot:
        tsum = sum(t[2] for t in tot)
        if tsum > 0 and abs(oi.sum() - tsum) / tsum > OI_TOL:
            warnings.append(f"parsed OI {oi.sum():,.0f} vs block TOTAL lines {tsum:,.0f}")
    oi_02b = None
    if s02b is not None:
        oi_02b = parse_02b_gold_options_oi(s02b)
        if oi_02b > 0 and abs(oi.sum() - oi_02b) / oi_02b > OI_TOL:
            warnings.append(f"parsed OI {oi.sum():,.0f} vs Section 02B {oi_02b:,.0f}")
    for wmsg in warnings:
        log(f"  WARNING (gold): {wmsg}")

    # the book: live rows within the strike band
    sel = live & (np.abs(kref / F_ref - 1.0) <= STRIKE_BAND)
    sess = next_session(trade_date)
    if isinstance(sess, dt.date) and not isinstance(sess, dt.datetime):
        sess = session_utc(sess)
    T = _years(sess, exp_dt[sel])

    atm = sel & (iv > 0) & (np.abs(kref / F_ref - 1.0) < 0.02)
    pool = iv[atm] if atm.any() else iv[sel & (iv > 0)]
    if pool.size == 0:
        raise BulletinError("no implied vols could be backed out of the settlements")
    iv_fallback = float(np.median(pool))

    ref = futs[ref_ym]
    hi = ref["high"] if np.isfinite(ref["high"]) else F_ref
    lo = ref["low"] if np.isfinite(ref["low"]) else F_ref

    book = {
        "kref": kref[sel], "k": K[sel], "S": Fu[sel], "T": T, "iv": iv[sel],
        "oi": oi[sel], "call": call[sel], "q": np.full(int(sel.sum()), cg.RATE),
        "exp": np.array([e.toordinal() for e in exp[sel]], dtype=int),
        "S_ref": float(F_ref), "asof": t_settle, "session": sess,
    }
    front = min(e for e in exp[live])
    meta = {
        "ref_ticker": ref_ticker,
        "tv_ref": cg.tv_symbol(ref_ticker, trade_date.year),
        "hi": float(hi), "lo": float(lo), "iv_fallback": iv_fallback,
        "stamps": {
            "source": "cme-bulletin", "trade_date": trade_date.isoformat(),
            "s64": str(s64), "s62": str(s62), "s02b": str(s02b) if s02b else None,
            "rows_parsed": len(rows), "rows_book": int(sel.sum()),
            "oi_total": oi_total, "oi_02b": oi_02b, "delta_mad": delta_mad,
            "front_expiry": front.isoformat(), "series": series,
            "futures": {cg.gc_ticker(*k): v["settle"] for k, v in sorted(futs.items())},
            "warnings": warnings,
            "synthetic": "SYNTHETIC" in first_page_text(s64, 2).upper(),
        },
    }
    return book, meta


def load_gold(next_session, inbox="inbox", log=print):
    """Newest Section 64 + 62 (+ optional 02B) in `inbox` -> (book, meta)."""
    found = find_inputs(inbox)
    missing = [s for s in ("s64", "s62") if s not in found]
    if missing:
        names = {"s64": "Section 64 (Metal Options)", "s62": "Section 62 (Metal Futures)"}
        raise NoBulletinData("missing " + " and ".join(names[m] for m in missing)
                             + f" PDF in {Path(inbox).resolve()}")
    s02b = found.get("s02b")
    if s02b and s02b[1] != found["s64"][1]:
        log(f"  note: ignoring Section 02B for {s02b[1]} (options bulletin is {found['s64'][1]})")
        s02b = None
    book, meta = build_book(found["s64"][0], found["s62"][0], next_session,
                            s02b[0] if s02b else None, log=log)
    meta["stamps"]["files"] = [str(found[s][0]) for s in ("s64", "s62")] + (
        [str(s02b[0])] if s02b else [])
    return book, meta


# --------------------------------------------------------------------------
# Step-1 inspection
# --------------------------------------------------------------------------
def inspect(inbox_or_files, n_rows=6):
    paths = []
    for p in inbox_or_files:
        p = Path(p)
        paths += sorted(p.glob("*.pdf")) if p.is_dir() else [p]
    if not paths:
        print("no PDFs found")
        return
    for p in paths:
        sec = section_of(p)
        print("=" * 100)
        print(f"{p.name}: section={sec}  trade date={trade_date_of(p)}")
        print("--- page header ---")
        print(first_page_text(p, 6))
        if sec == "s64":
            rows, d = parse_options(p)
            print("--- column layouts (label -> field @ x) ---")
            for lay in d["layouts"]:
                print("  " + " | ".join(f"{c.label}->{c.field}@{c.x0:.0f}" for c in lay))
            print(f"--- gold option block titles ({len(d['titles'])}) ---")
            for t in dict.fromkeys(d["titles"]):
                print("  " + t)
            if d["unknown_gold_titles"]:
                print("--- GOLD titles with no recognised code (would ERROR) ---")
                for t in dict.fromkeys(d["unknown_gold_titles"]):
                    print("  " + t)
            if d["skipped_titles"]:
                print("--- gold-named titles skipped (micro/kilo/APO/spreads...) ---")
                for t in dict.fromkeys(d["skipped_titles"]):
                    print("  " + t)
            print("--- series -> derived expiry -> GC underlying ---")
            seen = {}
            for r in rows:
                seen.setdefault((r.code, r.ym), [0, 0.0])
                seen[(r.code, r.ym)][0] += 1
                seen[(r.code, r.ym)][1] += np.nan_to_num(r.oi)
            for (code, ym), (n, o) in sorted(seen.items(), key=lambda kv: series_expiry_and_underlying(*kv[0])[0]):
                try:
                    e, u = series_expiry_and_underlying(code, ym)
                    eu = f"{e} {e.strftime('%a')} -> {cg.gc_ticker(*u)}"
                except BulletinError as ex:
                    eu = f"ERROR {ex}"
                print(f"  {code:4s} {cg.MONTH_ABBR[ym[1]-1]}{ym[0] % 100:02d}  rows={n:4d}  OI={o:10,.0f}  {eu}")
            print(f"--- raw rows (first {n_rows} + parsed fields) ---")
            for r in rows[:n_rows]:
                print(f"  RAW: {r.raw}")
                print(f"       {r.code} {r.cp} {r.ym} K={r.strike} settle={r.settle} "
                      f"delta={r.delta} vol={r.volume} oi={r.oi}")
            print(f"total gold option rows parsed: {len(rows)}; "
                  f"in-block lines not parsed: {d['unparsed_in_block']}")
        elif sec == "s62":
            futs, d = parse_futures(p)
            print("--- column layouts ---")
            for lay in d["layouts"]:
                print("  " + " | ".join(f"{c.label}->{c.field}@{c.x0:.0f}" for c in lay))
            print(f"--- GC titles: {d['titles']}")
            for k, v in sorted(futs.items()):
                print(f"  {cg.gc_ticker(*k)}  settle={v['settle']} hi={v['high']} lo={v['low']} "
                      f"oi={v['oi']}   RAW: {v['raw']}")
        elif sec == "s02b":
            try:
                print(f"gold options OI (02B): {parse_02b_gold_options_oi(p):,.0f}")
            except BulletinError as e:
                print(f"ERROR {e}")
        else:
            for ln in read_lines(p)[:40]:
                print("  " + ln.text)


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inspect", nargs="+", metavar="PATH", help="PDFs or folders to dump")
    a = ap.parse_args()
    if a.inspect:
        inspect(a.inspect)
    else:
        ap.print_help()
        sys.exit(1)
