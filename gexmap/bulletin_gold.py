"""COMEX gold options from the CME Daily Bulletin PDFs (free, manual download).

Inputs (dropped into ./inbox by hand -- this module never touches the network):
  * Section 64  "Metals Option Products"  strike-level settle / delta / volume / OI
  * Section 62  "Metal Futures Products"  GC settlements by contract month
  * Section 02B "Exchange Summary Volume and Open Interest"  (optional cross-check)

load_gold(next_session) returns (book, meta) with the same keys as the
Databento path (cme_gold.build_gold_book):

  book: kref, k, S, T, iv, oi, call, q, exp, S_ref, asof, session
  meta: ref_ticker, tv_ref, hi, lo, iv_fallback, stamps

Bulletin layout (Section 64, 2026):

  OG CALL COMEX GOLD OPTIONS                <- product title (code, CALL/PUT)
  DEC26                                     <- contract month
  4000 67.00 67.00 110.80B/42.60A ... 43.70 NEW .3021 ---- ---- 9 ---- 4 + 4
  ^strike  [globex/outcry open, high/low, close range]  then always the same
  9 right-hand fields: SETT  PT.CHGE  DELTA  EXERCISES  OUTCRY-VOL  GLOBEX-VOL
  PNT-VOL  OPEN-INTEREST  OI-CHANGE.   DELTA is unsigned (puts print positive).
  TOTAL 416 487+ 170                        <- month total: [volumes] OI change

Gold series:
  * OG  CALL/PUT             monthly; month label = option contract month
  * OG1..OG5 CALL/PUT        Friday weeklies; label = expiry month, n-th Friday
  * GMW MON / GWT TUE / GWW WED / GWR THU   Monday-Thursday weeklies. The
    bulletin prints every week AND both calls and puts of one expiry month in
    a single block with no marker.  Each row is classified: call/put from the
    unsigned delta and moneyness, the week from the candidate expiry whose
    total variance best matches the vol surface of the labelled series.
    Section 02B (per-week C/P open interest) is used to check the result.
Micro gold (OMG, WMG, FMG) is excluded.

Every load is checked; problems raise BulletinError rather than producing
levels: zero rows, unparseable rows, TOTAL-line mismatches, strikes not
clustered near the futures price, a series whose GC future is missing or
contradicted by put-call parity, OG expiries that disagree with the
bulletin's last-trade-date table, or a poor Black-76 delta match.

    python bulletin_gold.py --inspect inbox/

prints block titles, series, expiries, underlyings, trade date and raw rows.
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
DELTA_MAD_MAX = 0.05      # median |model delta - bulletin delta| allowed
PARITY_TOL = 0.005        # put-call-parity forward vs mapped GC settle
TOTAL_TOL = 0.0           # parsed OI must equal the bulletin TOTAL lines
OI_TOL = 0.02             # Section 02B cross-check tolerance (total gold OI)
UNPARSED_MAX = 0.005      # share of unparseable strike rows tolerated


class BulletinError(RuntimeError):
    """The bulletin could not be parsed into a trustworthy gold book."""


class NoBulletinData(BulletinError):
    """The required PDFs are not in the inbox."""


# --------------------------------------------------------------------------
# PDF text
# --------------------------------------------------------------------------
@dataclass
class Line:
    page: int
    text: str

    @property
    def tokens(self):
        return self.text.split()


def read_lines(path, first_page_only=False) -> list[Line]:
    p = Path(path)
    st = p.stat()
    return list(_read_lines(str(p.resolve()), st.st_mtime_ns, st.st_size, first_page_only))


@lru_cache(maxsize=16)
def _read_lines(path, _mtime, _size, first_page_only) -> tuple:
    """Text lines of every page.  pypdfium2 (installed with pdfplumber) is
    ~50x faster than pdfplumber's layout engine and gives the same rows."""
    out = []
    try:
        import pypdfium2 as pdfium

        pdf = pdfium.PdfDocument(path)
        try:
            n = 1 if first_page_only else len(pdf)
            pages = [(i, pdf[i].get_textpage().get_text_range()) for i in range(n)]
        finally:
            pdf.close()
    except ImportError:
        import pdfplumber

        with pdfplumber.open(path) as pdf:
            src = pdf.pages[:1] if first_page_only else pdf.pages
            pages = [(i, pg.extract_text() or "") for i, pg in enumerate(src)]
    for pno, txt in pages:
        for t in txt.splitlines():
            t = " ".join(t.split())
            if t:
                out.append(Line(pno, t))
    return tuple(out)


@lru_cache(maxsize=8)
def _read_words(path, _mtime, _size) -> tuple:
    """[(page, top, x0, x1, text)] for x-position-sensitive tables (02B)."""
    import pdfplumber

    out = []
    with pdfplumber.open(path) as pdf:
        for pno, page in enumerate(pdf.pages):
            for w in page.extract_words(x_tolerance=1.5, y_tolerance=2):
                out.append((pno, w["top"], w["x0"], w["x1"], w["text"]))
    return tuple(out)


def first_page_text(path, n_lines=12) -> str:
    return "\n".join(ln.text for ln in read_lines(path, first_page_only=True)[:n_lines])


# --------------------------------------------------------------------------
# Tokens
# --------------------------------------------------------------------------
MONTHS = {m: i + 1 for i, m in enumerate(cg.MONTH_ABBR)}
_MONTH_RE = re.compile(r"^(JAN|FEB|MAR|APR|MAY|JUN|JUL|AUG|SEP|OCT|NOV|DEC)(\d{2})$")
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
    try:
        return float(m.group(0).replace(",", ""))
    except ValueError:
        return np.nan


def is_number(tok) -> bool:
    return not np.isnan(num(tok)) and str(tok).strip().upper() not in ("UNCH", "UNC")


def month_label(tok):
    m = _MONTH_RE.match(tok.upper())
    return (2000 + int(m.group(2)), MONTHS[m.group(1)]) if m else None


def merge_signs(toks):
    """['-', '1.90'] -> ['-1.90'];  ['+', '10'] -> ['+10']."""
    out, i = [], 0
    while i < len(toks):
        if toks[i] in ("+", "-") and i + 1 < len(toks) and is_number(toks[i + 1]):
            out.append(toks[i] + toks[i + 1])
            i += 2
        else:
            out.append(toks[i])
            i += 1
    return out


_DATE_RES = [
    re.compile(r"\b(MON|TUE|WED|THU|FRI|SAT|SUN)[A-Z]*,?\s+([A-Z]{3})[A-Z]*\.?\s+(\d{1,2}),?\s+(\d{4})"),
    re.compile(r"\b(\d{1,2})/(\d{1,2})/(\d{4})\b"),
]


def trade_date_of(path) -> dt.date:
    """Trade date printed in the bulletin page header ('Wed, Nov 04, 2026');
    falls back to a YYYYMMDD stamp in the filename."""
    txt = first_page_text(path, 15).upper()
    m = _DATE_RES[0].search(txt)
    if m and m.group(2) in MONTHS:
        return dt.date(int(m.group(4)), MONTHS[m.group(2)], int(m.group(3)))
    m = _DATE_RES[1].search(txt)
    if m:
        return dt.date(int(m.group(3)), int(m.group(1)), int(m.group(2)))
    m = re.search(r"(20\d{2})(\d{2})(\d{2})", Path(path).name)
    if m:
        return dt.date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
    raise BulletinError(f"{Path(path).name}: no trade date found in page header:\n{txt}")


# --------------------------------------------------------------------------
# Gold option series
# --------------------------------------------------------------------------
_TITLE_RE = re.compile(r"^([A-Z0-9]{2,6})\s+(CALL|PUT|OPT|OOF|OP|MON|TUE|WED|THU|FRI|FUT)\b\s*(.*)$")
_WEEKDAYS = {"MON": 0, "TUE": 1, "WED": 2, "THU": 3, "FRI": 4}
_LETTER = {0: "M", 1: "T", 2: "W", 3: "R", 4: "F"}
EXCLUDE_WORDS = ("MICRO", "E-MINI", "EMINI", "KILO", "AVERAGE", "APO", "SPREAD",
                 "CALENDAR", "1 OUNCE", "1OZ", "EVENT", "EC GOLD", "SHANGHAI")
_EOO = "EOO'S AND BLOCKS"


def gold_series_kind(code, kind, desc):
    """Classify a Section 64 title.  Returns ('monthly'|'friday'|'mixed', info)
    for 100 oz gold options on GC futures, ('skip', None) for other gold
    products, (None, None) for non-gold, ('unknown', None) otherwise."""
    d = desc.upper()
    if "GOLD" not in d or kind == "FUT":
        return None, None
    if any(x in d for x in EXCLUDE_WORDS):
        return "skip", None
    if code == "OG" and kind in ("CALL", "PUT"):
        return "monthly", kind[0]
    m = re.fullmatch(r"OG([1-5])", code)
    if m and kind in ("CALL", "PUT"):
        return "friday", (int(m.group(1)), kind[0])
    if kind in _WEEKDAYS and "WEEKLY" in d:
        return "mixed", _WEEKDAYS[kind]
    return "unknown", None


def series_code(weekday: int, expiry: dt.date) -> str:
    """CME product code of a gold weekly: OG2 (2nd Friday), G3R (3rd Thursday)..."""
    n = (expiry.day - 1) // 7 + 1
    return f"OG{n}" if weekday == 4 else f"G{n}{_LETTER[weekday]}"


@dataclass
class OptRow:
    family: str          # 'OG', 'OG2', 'GWR', ...
    kind: str            # 'monthly' | 'friday' | 'mixed'
    cp: str | None       # 'C' / 'P' (None until classified for mixed rows)
    ym: tuple
    strike: float
    settle: float
    delta: float         # unsigned, as printed
    exer: float
    volume: float
    oi: float
    page: int
    raw: str
    weekday: int | None = None
    expiry: dt.date | None = None
    code: str | None = None      # 02B-style series code after classification
    cands: list = field(default_factory=list)


def _parse_tail(toks):
    """Right-anchored option row fields, or None if the row doesn't fit."""
    t = merge_signs(toks)
    if len(t) < 10:
        return None
    settle, chg, delta, exer, v_o, v_g, v_p, oi, oich = t[-9:]
    if not is_number(settle) or not is_number(oi):
        return None
    if not (is_number(delta) or delta.startswith("--")):
        return None
    if not (is_number(oich) or oich.upper() in ("UNCH", "NEW")):
        return None
    vol = np.nansum([num(v_o), num(v_g), num(v_p)])
    return num(settle), num(delta), num(exer), float(vol), num(oi)


def _parse_total(toks):
    """'TOTAL 416 487+ 170' / 'TOTAL 9 0' / 'TOTAL 2 1- 7' -> OI."""
    t = toks[1:]
    if not t:
        return np.nan
    if len(t) >= 2 and t[-2] in ("+", "-"):
        return num(t[-3]) if len(t) >= 3 else np.nan
    if len(t) >= 2 and t[-2].endswith(("+", "-")):
        return num(t[-2][:-1])
    if len(t) >= 2:          # change printed as '0' / 'UNCH'
        return num(t[-2])
    return np.nan


def parse_options(path):
    """Section 64 -> (rows, diag)."""
    rows: list[OptRow] = []
    diag = {"titles": [], "skipped_titles": [], "unknown_gold_titles": [],
            "totals": {}, "unparsed": [], "exp_table": {}}
    block = None
    ym = None
    for ln in read_lines(path):
        toks = ln.tokens
        if _EOO in ln.text.upper():
            break
        m = _TITLE_RE.match(ln.text)
        if m:
            code, kind, desc = m.groups()
            dates = re.findall(r"\b(\d{2})/(\d{2})\b", desc)
            if dates and re.fullmatch(r"[\d/ ]+", desc.strip()):   # last-trade-date table
                diag["exp_table"].setdefault((code, kind), []).extend(
                    (int(a), int(b)) for a, b in dates)
                continue
            sk, info = gold_series_kind(code, kind, desc)
            ym = None
            if sk in ("monthly", "friday", "mixed"):
                block = (code, sk, info)
                diag["titles"].append(ln.text)
            else:
                block = None
                if sk == "unknown":
                    diag["unknown_gold_titles"].append(ln.text)
                elif sk == "skip":
                    diag["skipped_titles"].append(ln.text)
            continue
        if block is None:
            continue
        lab = month_label(toks[0])
        if lab and len(toks) == 1:
            ym = lab
            continue
        if toks[0] == "TOTAL":
            if ym:
                key = (block[0], block[2][1] if block[1] == "friday" else block[2], ym)
                diag["totals"][key] = _parse_total(toks)
            continue
        if ym is None or not is_number(toks[0]):
            continue
        tail = _parse_tail(toks[1:])
        if tail is None:
            if len(toks) >= 9:          # page banners like '64 METALS OPTIONS PRODUCTS 64'
                diag["unparsed"].append(ln.text)
            continue
        settle, delta, exer, vol, oi = tail
        code, sk, info = block
        r = OptRow(code, sk, None, ym, num(toks[0]), settle, delta, exer, vol, oi, ln.page, ln.text)
        if sk == "monthly":
            r.cp = info
        elif sk == "friday":
            r.cp, r.weekday = info[1], 4
        else:
            r.weekday = info
        rows.append(r)
    return rows, diag


def parse_futures(path):
    """Section 62 -> ({(y, m): {settle, high, low, oi, volume, raw}}, diag)."""
    futs: dict = {}
    diag = {"titles": [], "unparsed": [], "exp_table": []}
    in_gc = False
    for ln in read_lines(path):
        m = _TITLE_RE.match(ln.text)
        if m:
            code, kind, desc = m.groups()
            if code == "GC" and kind == "FUT" and re.fullmatch(r"[\d/ ]+", desc.strip() or "x"):
                diag["exp_table"] = re.findall(r"\d{2}/\d{2}", desc)
                in_gc = False
                continue
            in_gc = code == "GC" and kind == "FUT" and "GOLD" in desc.upper()
            if in_gc:
                diag["titles"].append(ln.text)
            continue
        if not in_gc:
            continue
        toks = ln.tokens
        if toks[0] == "TOTAL" or toks[0].startswith("THE"):
            in_gc = in_gc and toks[0] != "TOTAL"
            continue
        lab = month_label(toks[0])
        if not lab:
            continue
        t = merge_signs(toks[1:])
        if len(t) < 6 or not is_number(t[-6]) or not is_number(t[-2]):
            diag["unparsed"].append(ln.text)
            continue
        settle, oi = num(t[-6]), num(t[-2])
        left = t[:-6]
        hi = lo = np.nan
        for i, tok in enumerate(left):
            if tok.startswith("/"):
                lo = num(tok[1:])
                hi = num(left[i - 1]) if i > 0 else np.nan
        vol = np.nansum([num(t[-4]), num(t[-3])])
        futs[lab] = {"settle": settle, "high": hi, "low": lo, "oi": oi,
                     "volume": float(vol), "raw": ln.text}
    return futs, diag


_02B_CODE = re.compile(r"^(OG|OG[1-5]|G[1-5][MTWRF])$")


def parse_02b(path) -> dict:
    """Section 02B -> {(code, 'C'|'P'): open interest} for 100 oz gold options
    (OG, OG1-5, G1M..G5R).  The open-interest column is located from the
    'OPEN INTEREST & CHGE' heading on each page (empty columns vanish from the
    text, so positions are needed)."""
    p = Path(path)
    st = p.stat()
    words = _read_words(str(p.resolve()), st.st_mtime_ns, st.st_size)
    lines: dict = {}
    for pg, top, x0, x1, t in words:
        lines.setdefault((pg, round(top)), []).append((x0, x1, t))
    oi_span: dict = {}
    for (pg, _), ws in sorted(lines.items()):
        texts = [w[2] for w in ws]
        if "CHGE" in texts and "INTEREST" in texts:
            i = texts.index("INTEREST")
            if i > 0 and texts[i - 1] == "OPEN":
                oi_span.setdefault(pg, (ws[i - 1][0] - 15, ws[i][0] + 8))
    out: dict = {}
    for (pg, _), ws in sorted(lines.items()):
        ws.sort()
        texts = [w[2] for w in ws]
        if not texts or not _02B_CODE.match(texts[0]) or pg not in oi_span:
            continue
        if not any(t in ("C", "P") for t in texts[1:]):
            continue
        desc = " ".join(texts)     # the description may sit on its own text line,
        if any(x in desc for x in EXCLUDE_WORDS):   # so the gold-specific code decides
            continue
        cp = next(t for t in texts[1:] if t in ("C", "P"))
        lo, hi = oi_span[pg]
        oi = [num(t) for x0, x1, t in ws if lo <= x1 <= hi and is_number(t)]
        out[(texts[0], cp)] = out.get((texts[0], cp), 0.0) + (oi[0] if oi else 0.0)
    if not out:
        raise BulletinError(f"{p.name}: no gold option lines found in Section 02B")
    return out


def parse_02b_gold_options_oi(path) -> float:
    return float(sum(parse_02b(path).values()))


# --------------------------------------------------------------------------
# Expiries and underlyings
# --------------------------------------------------------------------------
def _table_dates(pairs, trade_date):
    """[(mm, dd)] from the last-trade-date table -> dates on/after ~trade date."""
    out = set()
    for mm, dd in pairs:
        for y in (trade_date.year, trade_date.year + 1, trade_date.year + 2):
            try:
                d = dt.date(y, mm, dd)
            except ValueError:
                continue
            if d >= trade_date - dt.timedelta(days=40):
                out.add(d)
                break
    return out


def labelled_expiry(r: OptRow) -> dt.date:
    y, m = r.ym
    if r.kind == "monthly":
        return cg.og_monthly_expiry(y, m)
    n = int(r.family[2])
    e = cg.weekly_expiry(y, m, 4, n)
    if e is None:
        raise BulletinError(f"{r.family} {r.ym}: month has no Friday #{n}")
    return e


def underlying_of(r: OptRow, expiry: dt.date):
    if r.kind == "monthly":
        return cg.gc_underlying_month(*r.ym)
    return cg.weekly_underlying(expiry)


def mixed_candidates(weekday, ym, trade_date, table_max=None):
    out = []
    for n in range(1, 6):
        e = cg.weekly_expiry(ym[0], ym[1], weekday, n)
        if e and e > trade_date and (table_max is None or e <= table_max):
            out.append(e)
    return out


def session_utc(day: dt.date) -> dt.datetime:
    return dt.datetime.combine(day, dt.time(13, 30), UTC)


def expiry_utc(day: dt.date) -> dt.datetime:
    """Gold options expire with the 13:30 ET GC settlement."""
    return dt.datetime.combine(day, dt.time(13, 30), NY).astimezone(UTC)


def _years(a: dt.datetime, b) -> np.ndarray:
    secs = np.array([(x - a).total_seconds() for x in b], dtype=float)
    return np.maximum(secs / (365.0 * 86400.0), cg.YEAR_MIN)


class VarSurface:
    """Total implied variance w(x, T), x = ln(K/F), from labelled series."""

    def __init__(self):
        self.slices = []   # (T, xs, ws)

    def add(self, T, x, w):
        o = np.argsort(x)
        if len(o) >= 3:
            self.slices.append((T, x[o], w[o]))
            self.slices.sort(key=lambda s: s[0])

    def __call__(self, x, T):
        if not self.slices:
            return np.nan
        vals = [(Ti, float(np.interp(x, xs, ws))) for Ti, xs, ws in self.slices]
        Ts = [v[0] for v in vals]
        if T <= Ts[0]:
            return vals[0][1] * T / Ts[0]
        if T >= Ts[-1]:
            return vals[-1][1] * T / Ts[-1]
        j = int(np.searchsorted(Ts, T))
        (T0, w0), (T1, w1) = vals[j - 1], vals[j]
        return w0 + (w1 - w0) * (T - T0) / (T1 - T0)


# --------------------------------------------------------------------------
# Book
# --------------------------------------------------------------------------
def build_book(s64, s62, next_session, s02b=None, log=print):
    """Parse the PDFs and return (book, meta).  Raises BulletinError."""
    td64, td62 = trade_date_of(s64), trade_date_of(s62)
    if td64 != td62:
        raise BulletinError(f"trade dates differ: Section 64 {td64} vs Section 62 {td62}")
    trade_date = td64
    t_settle = expiry_utc(trade_date)

    # ---------------- futures
    futs, fdiag = parse_futures(s62)
    futs = {k: v for k, v in futs.items() if np.isfinite(v["settle"])}
    if not futs:
        raise BulletinError(f"{Path(s62).name}: zero GC futures rows parsed")
    ref_ym = max(futs, key=lambda k: np.nan_to_num(futs[k]["oi"]))
    F_ref = futs[ref_ym]["settle"]
    ref_ticker = cg.gc_ticker(*ref_ym)

    # ---------------- options
    rows, diag = parse_options(s64)
    if diag["unknown_gold_titles"]:
        raise BulletinError("unrecognised gold option series: "
                            + " | ".join(dict.fromkeys(diag["unknown_gold_titles"])))
    if not rows:
        raise BulletinError(f"{Path(s64).name}: zero gold option rows parsed")
    if len(diag["unparsed"]) > UNPARSED_MAX * len(rows):
        raise BulletinError(f"{len(diag['unparsed'])} gold strike rows did not parse, e.g. "
                            + " | ".join(diag["unparsed"][:3]))

    # TOTAL lines: parsed OI per block-month must equal the bulletin's total
    sums: dict = {}
    for r in rows:
        key = (r.family, r.cp if r.kind == "friday" else (r.cp if r.kind == "monthly" else r.weekday), r.ym)
        sums[key] = sums.get(key, 0.0) + np.nan_to_num(r.oi)
    bad_tot = []
    for key, tot in diag["totals"].items():
        got = sums.get(key, 0.0)
        if np.isfinite(tot) and abs(got - tot) > TOTAL_TOL * max(tot, 1):
            bad_tot.append(f"{key[0]} {key[1]} {key[2]}: rows {got:,.0f} vs TOTAL {tot:,.0f}")
    if bad_tot:
        raise BulletinError("open interest does not match the TOTAL lines: " + "; ".join(bad_tot[:5]))

    # ---------------- labelled series: expiry, underlying, checks
    exp_table = diag["exp_table"]
    labelled = [r for r in rows if r.kind != "mixed"]
    for r in labelled:
        r.expiry = labelled_expiry(r)
        r.code = r.family
    for fam in {(r.family, r.cp) for r in labelled}:
        tab = _table_dates(exp_table.get((fam[0], "CALL" if fam[1] == "C" else "PUT"), []), trade_date)
        if not tab:
            continue
        for e in {r.expiry for r in labelled if (r.family, r.cp) == fam}:
            if e <= max(tab) and e not in tab and e > trade_date:
                raise BulletinError(f"{fam[0]} expiry rule gives {e}, not in the bulletin's "
                                    f"last-trade-date table {sorted(tab)}")

    def fwd(r):
        u = underlying_of(r, r.expiry)
        if u not in futs:
            raise BulletinError(f"{r.family} {r.ym}: underlying {cg.gc_ticker(*u)} not in Section 62")
        return u

    # put-call parity on labelled series -> confirms each series' GC future
    parity = {}
    by_series: dict = {}
    for r in labelled:
        if r.expiry > trade_date and r.settle > 0:
            by_series.setdefault((r.family, r.ym), {}).setdefault(r.strike, {})[r.cp] = r.settle
    for key, strikes in by_series.items():
        pairs = [(k, v["C"], v["P"]) for k, v in strikes.items() if "C" in v and "P" in v]
        if len(pairs) < 3:
            continue
        r0 = next(r for r in labelled if (r.family, r.ym) == key)
        u = fwd(r0)
        F_u = futs[u]["settle"]
        pairs.sort(key=lambda p: abs(p[0] - F_u))
        T = _years(t_settle, [expiry_utc(r0.expiry)])[0]
        df = np.exp(-cg.RATE * T)
        imp = float(np.median([k + (c - p) / df for k, c, p in pairs[:5]]))
        parity[f"{key[0]} {cg.MONTH_ABBR[key[1][1]-1]}{key[1][0] % 100:02d}"] = (imp, cg.gc_ticker(*u), F_u)
        if abs(imp / F_u - 1) > PARITY_TOL:
            near = min(futs, key=lambda k: abs(futs[k]["settle"] - imp))
            raise BulletinError(f"{key[0]} {key[1]}: put-call parity forward {imp:.1f} does not match "
                                f"its mapped future {cg.gc_ticker(*u)} {F_u:.1f} "
                                f"(nearest: {cg.gc_ticker(*near)} {futs[near]['settle']:.1f})")

    # ---------------- implied vols for labelled rows; variance surface
    def iv_of(rs, Fs, Ts):
        price = np.array([r.settle for r in rs])
        K = np.array([r.strike for r in rs])
        call = np.array([r.cp == "C" for r in rs])
        ok = np.isfinite(price) & (price > 0)
        iv = np.zeros(len(rs))
        if ok.any():
            iv[ok] = cg.b76_iv(price[ok], Fs[ok], K[ok], Ts[ok], cg.RATE, call[ok])
        return iv

    live_lab = [r for r in labelled if r.expiry > trade_date]
    F_lab = np.array([futs[fwd(r)]["settle"] for r in live_lab])
    T_lab = _years(t_settle, [expiry_utc(r.expiry) for r in live_lab])
    iv_lab = iv_of(live_lab, F_lab, T_lab)
    surf = VarSurface()
    d_lab = np.array([r.delta for r in live_lab])
    for e in sorted({r.expiry for r in live_lab}):
        m = np.array([r.expiry == e for r in live_lab]) & (iv_lab > 0) & (d_lab > 0.03) & (d_lab < 0.97)
        if m.any():
            x = np.log(np.array([r.strike for r in live_lab])[m] / F_lab[m])
            surf.add(T_lab[m][0], x, iv_lab[m] ** 2 * T_lab[m][0])

    # ---------------- mixed weekly rows: classify call/put and week
    mixed = [r for r in rows if r.kind == "mixed" and np.nan_to_num(r.oi) > 0]
    unassigned = []
    for r in mixed:
        tab = _table_dates(exp_table.get((r.family, list(_WEEKDAYS)[r.weekday]), []), trade_date)
        tmax = max((d for d in tab if (d.year, d.month) == r.ym), default=None)
        cands = []
        for e in mixed_candidates(r.weekday, r.ym, trade_date, tmax):
            u = cg.weekly_underlying(e)
            if u not in futs:
                continue
            F = futs[u]["settle"]
            T = _years(t_settle, [expiry_utc(e)])[0]
            wref = surf(np.log(r.strike / F), T)
            if not (wref and wref > 0):
                continue
            # price and delta this row would have, as a call or a put at this
            # expiry, on the labelled-series vol surface; closest wins.
            sig = np.sqrt(wref / T)
            for cp in ("C", "P"):
                if not np.isfinite(r.delta) and (cp == "P") != (r.strike < F):
                    continue           # no delta printed: minimum tick, out of the money
                pm = float(cg.b76_price(F, r.strike, T, sig, cg.RATE, cp == "C"))
                score = abs(np.log((pm + 0.1) / (r.settle + 0.1))) / 0.05
                if np.isfinite(r.delta):
                    dm = abs(float(cg.b76_delta(F, r.strike, T, sig, cg.RATE, cp == "C")))
                    score += abs(dm - r.delta) / 0.01
                cands.append((score, e, cp, series_code(r.weekday, e)))
        if not cands:
            unassigned.append(r)
            continue
        r.cands = cands
        _, r.expiry, r.cp, r.code = min(cands, key=lambda c: c[0])
    mixed = [r for r in mixed if r.expiry]

    # With Section 02B, its per-series (week, C/P) open interest is used as a
    # constraint: a penalty per series is adjusted until the assigned OI
    # matches, so every row still goes to its best-fitting week given them.
    s02b_oi = parse_02b(s02b) if s02b is not None else None
    weekly_miss_model = weekly_miss = None
    if s02b_oi and mixed:
        codes = {c[3] for r in mixed for c in r.cands}
        # OI 02B attributes to these codes, minus the labelled series sharing them
        target = {(c, cp): s02b_oi.get((c, cp), 0.0) for c in codes for cp in "CP"}

        def assign(lam):
            got = dict.fromkeys(target, 0.0)
            pick = []
            for r in mixed:
                c = min(r.cands, key=lambda c: c[0] + lam.get((c[3], c[2]), 0.0))
                pick.append(c)
                got[(c[3], c[2])] = got.get((c[3], c[2]), 0.0) + r.oi
            miss = sum(abs(got[k] - target.get(k, 0.0)) for k in got) / 2
            return pick, miss

        lam: dict = {}
        pick, miss = assign(lam)
        weekly_miss_model = miss
        best = (miss, pick)
        for it in range(400):
            got: dict = {}
            for r, c in zip(mixed, pick):
                got[(c[3], c[2])] = got.get((c[3], c[2]), 0.0) + r.oi
            for k in set(target) | set(got):
                err = got.get(k, 0.0) - target.get(k, 0.0)
                lam[k] = lam.get(k, 0.0) + 2.0 * err / max(target.get(k, 0.0), 100.0)
            pick, miss = assign(lam)
            if miss < best[0]:
                best = (miss, pick)
            if miss == 0:
                break
        weekly_miss = best[0]
        for r, c in zip(mixed, best[1]):
            _, r.expiry, r.cp, r.code = c
    if unassigned:
        log(f"  note (gold): {len(unassigned)} weekly rows (OI {sum(r.oi for r in unassigned):,.0f}) "
            f"could not be matched to an expiry and were left out")

    used = [r for r in live_lab if np.nan_to_num(r.oi) > 0] + [r for r in mixed if r.expiry]
    if not used:
        raise BulletinError("no live gold option rows with open interest")
    exp = np.array([r.expiry for r in used])
    if any(e < trade_date for e in exp):
        raise BulletinError("rows with open interest have an expiry before the trade date")
    uym = [fwd(r) if r.kind != "mixed" else cg.weekly_underlying(r.expiry) for r in used]
    K = np.array([r.strike for r in used])
    settle = np.array([r.settle for r in used])
    oi = np.array([r.oi for r in used], dtype=float)
    bdelta = np.array([r.delta for r in used])
    call = np.array([r.cp == "C" for r in used])
    Fu = np.array([futs[u]["settle"] for u in uym])
    kref = K * F_ref / Fu
    exp_dt = [expiry_utc(e) for e in exp]
    T_set = _years(t_settle, exp_dt)
    iv = iv_of(used, Fu, T_set)

    # ---------------- strikes must cluster around the futures price.
    # Gold carries a long far-OTM call tail (e.g. Dec 15000/20000 calls at
    # the 0.10 minimum), so the test uses the near-the-money rows by the
    # bulletin's own delta: a strike/price column or scaling error moves those
    # far outside the band.
    rel = kref / F_ref - 1.0
    days = np.array([(e - trade_date).days for e in exp])
    ntm = (bdelta >= 0.2) & (bdelta <= 0.8) & (days <= 60)
    if ntm.sum() < 10:
        raise BulletinError(f"only {int(ntm.sum())} near-the-money rows (delta 0.2-0.8, <= 60 days) "
                            f"-- delta/settle columns not found?")
    w = oi[ntm]
    o = np.argsort(rel[ntm])
    med = rel[ntm][o][np.searchsorted(np.cumsum(w[o]), 0.5 * w.sum())]
    in_band = w[np.abs(rel[ntm]) <= STRIKE_BAND].sum() / w.sum()
    if abs(med) > 0.05 or in_band < 0.95:
        raise BulletinError(f"strikes are not clustered near the futures price {F_ref:g}: "
                            f"near-the-money OI-weighted median strike {100*med:+.1f}% away, "
                            f"{100*in_band:.0f}% within +-15%")

    # ---------------- Black-76 delta vs the bulletin's (unsigned) delta
    chk = (iv > 0) & np.isfinite(bdelta)
    if chk.sum() < 20:
        raise BulletinError(f"only {int(chk.sum())} rows have both an IV and a bulletin delta")
    md = np.abs(cg.b76_delta(Fu[chk], K[chk], T_set[chk], iv[chk], cg.RATE, call[chk]))
    mu = np.abs(cg.b76_delta(Fu[chk], K[chk], T_set[chk], iv[chk], cg.RATE, call[chk], discounted=False))
    delta_mad = float(min(np.median(np.abs(md - bdelta[chk])), np.median(np.abs(mu - bdelta[chk]))))
    if delta_mad > DELTA_MAD_MAX:
        raise BulletinError(f"Black-76 delta vs bulletin delta: median |diff| {delta_mad:.3f} > "
                            f"{DELTA_MAD_MAX} -- settle, delta or expiry mapping is off")

    # ---------------- Section 02B cross-checks
    warnings = []
    oi_all = float(np.nansum([r.oi for r in rows]))
    oi_02b = None
    weekly_check = {}
    if s02b_oi is not None:
        s = s02b_oi
        oi_02b = float(sum(s.values()))
        if oi_02b > 0 and abs(oi_all - oi_02b) / oi_02b > OI_TOL:
            raise BulletinError(f"gold options OI: Section 64 rows {oi_all:,.0f} vs Section 02B "
                                f"{oi_02b:,.0f} (> {100*OI_TOL:.0f}% apart)")
        mine: dict = {}
        for r in used:
            mine[(r.code, r.cp)] = mine.get((r.code, r.cp), 0.0) + r.oi
        codes = {c for c, _ in s} | {c for c, _ in mine}
        for c in sorted(codes):
            for cp in ("C", "P"):
                a, b = mine.get((c, cp), 0.0), s.get((c, cp), 0.0)
                if a or b:
                    weekly_check[f"{c} {cp}"] = (a, b)
        miss = sum(abs(a - b) for a, b in weekly_check.values()) / 2
        if miss > 0.05 * oi_02b:
            warnings.append(f"{miss:,.0f} contracts sit in a different week/CP bucket than Section 02B")
    for wmsg in warnings:
        log(f"  WARNING (gold): {wmsg}")

    # ---------------- the book: live rows within the strike band
    sel = np.abs(kref / F_ref - 1.0) <= STRIKE_BAND
    sess = next_session(trade_date)
    if isinstance(sess, dt.date) and not isinstance(sess, dt.datetime):
        sess = session_utc(sess)
    T = _years(sess, [d for d, s_ in zip(exp_dt, sel) if s_])

    atm = sel & (iv > 0) & (np.abs(kref / F_ref - 1.0) < 0.02)
    pool = iv[atm] if atm.any() else iv[sel & (iv > 0)]
    if pool.size == 0:
        raise BulletinError("no implied vols could be backed out of the settlements")
    ref = futs[ref_ym]
    series = {}
    for r in used:
        k = f"{r.code} {r.expiry.isoformat()}"
        if k not in series:
            series[k] = (r.family, cg.gc_ticker(*(cg.weekly_underlying(r.expiry) if r.kind != "monthly"
                                                  else cg.gc_underlying_month(*r.ym))))
    book = {
        "kref": kref[sel], "k": K[sel], "S": Fu[sel], "T": T, "iv": iv[sel],
        "oi": oi[sel], "call": call[sel], "q": np.full(int(sel.sum()), cg.RATE),
        "exp": np.array([e.toordinal() for e in exp[sel]], dtype=int),
        "S_ref": float(F_ref), "asof": t_settle, "session": sess,
    }
    meta = {
        "ref_ticker": ref_ticker,
        "tv_ref": cg.tv_symbol(ref_ticker, trade_date.year),
        "hi": float(ref["high"]) if np.isfinite(ref["high"]) else float(F_ref),
        "lo": float(ref["low"]) if np.isfinite(ref["low"]) else float(F_ref),
        "iv_fallback": float(np.median(pool)),
        "stamps": {
            "source": "cme-bulletin", "trade_date": trade_date.isoformat(),
            "s64": str(s64), "s62": str(s62), "s02b": str(s02b) if s02b else None,
            "rows_parsed": len(rows), "rows_used": len(used), "rows_book": int(sel.sum()),
            "oi_total": oi_all, "oi_02b": oi_02b, "delta_mad": delta_mad,
            "front_expiry": min(exp).isoformat(), "series": series, "parity": parity,
            "weekly_check": weekly_check, "unassigned": len(unassigned),
            "weekly_miss_model": weekly_miss_model, "weekly_miss": weekly_miss,
            "futures": {cg.gc_ticker(*k): v["settle"] for k, v in sorted(futs.items())},
            "warnings": warnings,
            "synthetic": "SYNTHETIC" in first_page_text(s64, 15).upper(),
        },
    }
    return book, meta


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
    t = first_page_text(path, 15).upper()
    if "PG64" in t or ("METAL" in t and "OPTION PRODUCTS" in t):
        return "s64"
    if "PG62" in t or "METAL FUTURES PRODUCTS" in t:
        return "s62"
    if "PG02B" in t:
        return "s02b"
    return None


def find_inputs(inbox) -> dict:
    """{'s64': (path, trade_date), 's62': ..., 's02b': ...}, newest by trade date."""
    best: dict = {}
    for p in sorted(Path(inbox).glob("*.pdf")) + sorted(Path(inbox).glob("*.PDF")):
        try:
            sec = section_of(p)
        except Exception as e:
            raise BulletinError(f"{p.name}: cannot read PDF ({e})") from e
        if not sec:
            continue
        td = trade_date_of(p)
        if sec not in best or td > best[sec][1]:
            best[sec] = (p, td)
    return best


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
def inspect(paths_in, n_rows=4):
    paths = []
    for p in paths_in:
        p = Path(p)
        paths += sorted(p.glob("*.pdf")) if p.is_dir() else [p]
    found = {section_of(p): p for p in paths}
    for p in paths:
        sec = section_of(p)
        print("=" * 100)
        print(f"{p.name}: section={sec}  trade date={trade_date_of(p)}")
        if sec == "s64":
            rows, d = parse_options(p)
            print(f"--- gold option block titles ({len(set(d['titles']))} distinct) ---")
            for t in dict.fromkeys(d["titles"]):
                print("  " + t)
            print("--- gold-named blocks skipped:", sorted(set(d["skipped_titles"])))
            if d["unknown_gold_titles"]:
                print("--- UNRECOGNISED gold titles:", sorted(set(d["unknown_gold_titles"])))
            print("--- last-trade-date table (gold) ---")
            for (c, k), v in d["exp_table"].items():
                if c.startswith(("OG", "G")):
                    print(f"  {c} {k}: " + " ".join(f"{a:02d}/{b:02d}" for a, b in v))
            print("--- blocks: family / C-P-or-weekday / month -> rows, OI (TOTAL line) ---")
            agg: dict = {}
            for r in rows:
                k = (r.family, r.cp or list(_WEEKDAYS)[r.weekday], r.ym)
                a = agg.setdefault(k, [0, 0.0])
                a[0] += 1
                a[1] += np.nan_to_num(r.oi)
            for k, (n, o) in agg.items():
                tk = (k[0], k[1] if k[1] in ("C", "P") else _WEEKDAYS[k[1]], k[2])
                print(f"  {k[0]:4s} {k[1]:4s} {cg.MONTH_ABBR[k[2][1]-1]}{k[2][0] % 100:02d} "
                      f"rows={n:5d} OI={o:9,.0f}  TOTAL={d['totals'].get(tk, float('nan')):9,.0f}")
            print(f"--- raw rows (first {n_rows} per family) ---")
            seen: dict = {}
            for r in rows:
                if seen.get(r.family, 0) < n_rows and np.nan_to_num(r.oi) > 0:
                    seen[r.family] = seen.get(r.family, 0) + 1
                    print(f"  RAW: {r.raw}")
                    print(f"       -> {r.family} {r.cp or '?'} {r.ym} K={r.strike:g} settle={r.settle} "
                          f"delta={r.delta} vol={r.volume:g} oi={r.oi:g}")
            print(f"gold option rows parsed: {len(rows)}   unparsed strike rows: {len(d['unparsed'])}")
            for u in d["unparsed"][:5]:
                print("  UNPARSED: " + u)
        elif sec == "s62":
            futs, d = parse_futures(p)
            print("--- GC titles:", d["titles"], " last trade dates:", d["exp_table"])
            for k, v in sorted(futs.items()):
                print(f"  {cg.gc_ticker(*k)}  settle={v['settle']} hi={v['high']} lo={v['low']} "
                      f"oi={v['oi']:,.0f}   RAW: {v['raw']}")
        elif sec == "s02b":
            try:
                s = parse_02b(p)
                for (c, cp), v in sorted(s.items()):
                    print(f"  {c:4s} {cp}  OI {v:10,.0f}")
                print(f"  total 100oz gold options OI (02B): {sum(s.values()):,.0f}")
            except BulletinError as e:
                print(f"ERROR {e}")
    if "s64" in found and "s62" in found:
        import gexmap
        print("=" * 100)
        try:
            book, meta = build_book(found["s64"], found["s62"], gexmap.next_session, found.get("s02b"))
        except BulletinError as e:
            print(f"BUILD ERROR: {e}")
            return
        st = meta["stamps"]
        print(f"reference future {meta['ref_ticker']} ({meta['tv_ref']}) settle {book['S_ref']}  "
              f"hi {meta['hi']} lo {meta['lo']}")
        print("--- series -> expiry -> GC future ---")
        for k, (fam, u) in sorted(st["series"].items(), key=lambda kv: kv[0].split()[1]):
            print(f"  {k:18s} (block {fam:4s}) -> {u}")
        print("--- put-call parity forward vs mapped future ---")
        for k, (imp, u, F) in st["parity"].items():
            print(f"  {k:10s} parity F {imp:8.1f}  {u} {F:8.1f}  diff {imp-F:+6.1f}")
        if st["weekly_check"]:
            print("--- OI by series code: Section 64 (classified) vs Section 02B ---")
            for k, (a, b) in st["weekly_check"].items():
                print(f"  {k:6s} {a:9,.0f} {b:9,.0f}  {'' if a == b else 'diff %+.0f' % (a - b)}")
        if st["weekly_miss_model"] is not None:
            print(f"weekly rows: {st['weekly_miss_model']:,.0f} contracts off 02B by model fit alone, "
                  f"{st['weekly_miss']:,.0f} after balancing to 02B")
        print(f"rows parsed {st['rows_parsed']}, with OI used {st['rows_used']}, in book {st['rows_book']}, "
              f"unassigned weekly rows {st['unassigned']}")
        print(f"gold options OI {st['oi_total']:,.0f}  (02B {st['oi_02b'] or float('nan'):,.0f})")
        print(f"Black-76 vs bulletin delta: median |diff| = {st['delta_mad']:.4f}")
        print(f"front expiry {st['front_expiry']}, session {book['session']}")


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--inspect", nargs="+", metavar="PATH", help="PDFs or folders to dump")
    a = ap.parse_args()
    if a.inspect:
        inspect(a.inspect)
    else:
        ap.print_help()
        sys.exit(1)
