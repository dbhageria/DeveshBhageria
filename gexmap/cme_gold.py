"""Shared COMEX gold helpers.

Black-76 pricing, implied vol and greeks; CME contract symbols; the CME
business-day calendar and the expiry / underlying rules for gold options on
GC futures.  Nothing here touches the network.
"""
from __future__ import annotations

import datetime as dt
import math
from functools import lru_cache

import numpy as np

# Discount rate used for Black-76 (annualised, continuous).  Gold options on
# futures are priced with the futures price as the forward, so this only sets
# the e^{-rT} discount factor.  Passed as `q` in the book (BSM with q = r is
# Black-76).
RATE = 0.04

CONTRACT_SIZE_OZ = 100.0  # GC / OG: 100 troy ounces

MONTH_ABBR = ("JAN", "FEB", "MAR", "APR", "MAY", "JUN",
              "JUL", "AUG", "SEP", "OCT", "NOV", "DEC")
MONTH_CODES = "FGHJKMNQUVXZ"
# GC futures active (listed-every-cycle) months: Feb, Apr, Jun, Aug, Oct, Dec.
GC_ACTIVE_MONTHS = (2, 4, 6, 8, 10, 12)

YEAR_MIN = 1.0 / (365.0 * 24.0 * 2.0)  # 30 minutes in years


# --------------------------------------------------------------------------
# Black-76
# --------------------------------------------------------------------------
_erf = np.frompyfunc(math.erf, 1, 1)


def _ncdf(x):
    x = np.asarray(x, dtype=float)
    return 0.5 * (1.0 + np.asarray(_erf(x / math.sqrt(2.0)), dtype=float))


def _npdf(x):
    x = np.asarray(x, dtype=float)
    return np.exp(-0.5 * x * x) / math.sqrt(2.0 * math.pi)


def _d1(F, K, T, sigma):
    sd = sigma * np.sqrt(T)
    with np.errstate(divide="ignore", invalid="ignore"):
        return (np.log(F / K) + 0.5 * sd * sd) / sd, sd


def b76_price(F, K, T, sigma, r=RATE, call=True):
    F, K, T, sigma = (np.asarray(a, dtype=float) for a in (F, K, T, sigma))
    call = np.asarray(call, dtype=bool)
    df = np.exp(-r * T)
    d1, sd = _d1(F, K, T, sigma)
    d2 = d1 - sd
    c = df * (F * _ncdf(d1) - K * _ncdf(d2))
    p = df * (K * _ncdf(-d2) - F * _ncdf(-d1))
    return np.where(call, c, p)


def b76_iv(price, F, K, T, r=RATE, call=True, lo=1e-4, hi=5.0, iters=80):
    """Vectorised Black-76 implied vol by bisection.

    Returns 0.0 where no vol in [lo, hi] reproduces the price (price at or
    below discounted intrinsic, or above the hi-vol price).  Callers fill
    those with fill_iv().
    """
    price, F, K, T = np.broadcast_arrays(*(np.asarray(a, dtype=float)
                                           for a in (price, F, K, T)))
    call = np.broadcast_to(np.asarray(call, dtype=bool), price.shape)
    a = np.full(price.shape, lo)
    b = np.full(price.shape, hi)
    p_lo = b76_price(F, K, T, a, r, call)
    p_hi = b76_price(F, K, T, b, r, call)
    ok = np.isfinite(price) & (price > p_lo) & (price < p_hi) & (T > 0)
    for _ in range(iters):
        m = 0.5 * (a + b)
        pm = b76_price(F, K, T, m, r, call)
        up = pm < price
        a = np.where(up, m, a)
        b = np.where(up, b, m)
    iv = 0.5 * (a + b)
    return np.where(ok, iv, 0.0)


def b76_delta(F, K, T, sigma, r=RATE, call=True, discounted=True):
    F, K, T, sigma = (np.asarray(a, dtype=float) for a in (F, K, T, sigma))
    d1, _ = _d1(F, K, T, sigma)
    df = np.exp(-r * T) if discounted else 1.0
    return np.where(np.asarray(call, dtype=bool), df * _ncdf(d1), -df * _ncdf(-d1))


def b76_gamma(F, K, T, sigma, r=RATE):
    """dDelta/dF (per 1.0 move in the futures price)."""
    F, K, T, sigma = (np.asarray(a, dtype=float) for a in (F, K, T, sigma))
    d1, sd = _d1(F, K, T, sigma)
    with np.errstate(divide="ignore", invalid="ignore"):
        g = np.exp(-r * T) * _npdf(d1) / (F * sd)
    return np.where(np.isfinite(g), g, 0.0)


# --------------------------------------------------------------------------
# Symbols
# --------------------------------------------------------------------------
def gc_ticker(year: int, month: int) -> str:
    """(2026, 12) -> 'GCZ6'."""
    return f"GC{MONTH_CODES[month - 1]}{year % 10}"


def tv_symbol(ticker: str, ref_year: int | None = None) -> str:
    """'GCZ6' -> 'COMEX:GCZ2026'.

    A one-digit year is resolved to the decade nearest ref_year (default:
    this year), never more than a year in the past.
    """
    root, code, ydigits = ticker[:-2], ticker[-2], ticker[-1:]
    if ticker[-2:].isdigit():  # 'GCZ26'
        root, code, ydigits = ticker[:-3], ticker[-3], ticker[-2:]
    ref_year = ref_year or dt.date.today().year
    if len(ydigits) == 1:
        year = (ref_year // 10) * 10 + int(ydigits)
        if year < ref_year - 1:
            year += 10
    else:
        year = (ref_year // 100) * 100 + int(ydigits)
    return f"COMEX:{root}{code}{year}"


# --------------------------------------------------------------------------
# CME calendar
# --------------------------------------------------------------------------
def _easter(y: int) -> dt.date:
    a = y % 19
    b, c = divmod(y, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    month, day = divmod(h + l - 7 * m + 114, 31)
    return dt.date(y, month, day + 1)


def _nth_weekday(y, m, weekday, n):
    """n-th (1-based) weekday (Mon=0) of month, or None.  n=-1 for last."""
    if n == -1:
        d = dt.date(y + (m == 12), m % 12 + 1, 1) - dt.timedelta(days=1)
        return d - dt.timedelta(days=(d.weekday() - weekday) % 7)
    d = dt.date(y, m, 1)
    d += dt.timedelta(days=(weekday - d.weekday()) % 7)
    d += dt.timedelta(weeks=n - 1)
    return d if d.month == m else None


def _observed(d: dt.date) -> dt.date:
    if d.weekday() == 5:
        return d - dt.timedelta(days=1)
    if d.weekday() == 6:
        return d + dt.timedelta(days=1)
    return d


@lru_cache(maxsize=None)
def cme_holidays(y: int) -> frozenset:
    """Full-day CME metals closures (approximation of the CME holiday
    calendar: early closes are treated as business days)."""
    h = {
        _observed(dt.date(y, 1, 1)),
        _nth_weekday(y, 1, 0, 3),            # MLK
        _nth_weekday(y, 2, 0, 3),            # Presidents
        _easter(y) - dt.timedelta(days=2),   # Good Friday
        _nth_weekday(y, 5, 0, -1),           # Memorial
        _observed(dt.date(y, 7, 4)),
        _nth_weekday(y, 9, 0, 1),            # Labor
        _nth_weekday(y, 11, 3, 4),           # Thanksgiving
        _observed(dt.date(y, 12, 25)),
    }
    if y >= 2022:
        h.add(_observed(dt.date(y, 6, 19)))  # Juneteenth
    # New Year of next year observed on Fri Dec 31
    nxt = dt.date(y + 1, 1, 1)
    if nxt.weekday() == 5:
        h.add(dt.date(y, 12, 31))
    return frozenset(h)


def is_business_day(d: dt.date) -> bool:
    return d.weekday() < 5 and d not in cme_holidays(d.year)


def next_business_day(d: dt.date) -> dt.date:
    d += dt.timedelta(days=1)
    while not is_business_day(d):
        d += dt.timedelta(days=1)
    return d


def prev_business_day(d: dt.date) -> dt.date:
    d -= dt.timedelta(days=1)
    while not is_business_day(d):
        d -= dt.timedelta(days=1)
    return d


def og_monthly_expiry(year: int, month: int) -> dt.date:
    """Last trading day of the OG option for contract month (year, month):
    4th last business day of the prior month; if that is a Friday or the day
    before a holiday, the business day before."""
    py, pm = (year, month - 1) if month > 1 else (year - 1, 12)
    d = dt.date(py + (pm == 12), pm % 12 + 1, 1) - dt.timedelta(days=1)
    bdays = []
    while len(bdays) < 4:
        if is_business_day(d):
            bdays.append(d)
        d -= dt.timedelta(days=1)
    e = bdays[-1]
    nxt = e + dt.timedelta(days=1)
    if e.weekday() == 4 or (nxt.weekday() < 5 and not is_business_day(nxt)):
        e = prev_business_day(e)
    return e


def weekly_expiry(year: int, month: int, weekday: int, week: int) -> dt.date | None:
    """n-th <weekday> of the month; rolled back to the prior business day if
    that is a holiday.  None if the month has no such week."""
    d = _nth_weekday(year, month, weekday, week)
    if d is None:
        return None
    return d if is_business_day(d) else prev_business_day(d)


def gc_underlying_month(year: int, month: int) -> tuple[int, int]:
    """Nearest active GC month at or after (year, month)."""
    for m in GC_ACTIVE_MONTHS:
        if m >= month:
            return year, m
    return year + 1, GC_ACTIVE_MONTHS[0]


def weekly_underlying(expiry: dt.date) -> tuple[int, int]:
    """Weekly/daily gold options exercise into the GC month of the nearest
    monthly OG expiring on or after the weekly's expiry."""
    y, m = expiry.year, expiry.month
    for _ in range(4):
        # OG contract month whose expiry is in month (y, m) is (y, m+1)
        cy, cm = (y, m + 1) if m < 12 else (y + 1, 1)
        if og_monthly_expiry(cy, cm) >= expiry:
            return gc_underlying_month(cy, cm)
        y, m = cy, cm
    raise ValueError(f"no monthly OG after {expiry}")
