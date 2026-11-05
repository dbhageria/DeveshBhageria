"""gexmap: options gamma levels -> TradingView Pine script.

Gold (XAUUSD) levels come from COMEX gold options on GC futures (OG monthlies
plus every weekly/daily series), read from the CME Daily Bulletin PDFs that
you download by hand into ./inbox (see README.md).

    python gexmap.py                       # reads ./inbox, writes output/gamma_map.pine
    python gexmap.py --inbox ~/Downloads --no-move

Levels (all computed in the reference GC future's price space; the Pine
script converts them to the chart symbol with the live XAUUSD / GC ratio):

  CW / PW   call wall / put wall: strike with the largest call / put gamma $
  walls     the five strikes with the largest |net gamma $|
  HVL       gamma flip: price where total net dealer gamma crosses zero
  GW        gamma wall: strike with the largest total (call + put) gamma $
  MP        max pain of the front expiry
  0DTE      front-expiry-only call wall (C), put wall (P) and flip (F)
  VU / VD   +-1 sigma one-day move from the front-expiry ATM implied vol
  regime    +GEX above HVL (dealers long gamma: mean reversion), -GEX below
  bias      bull above HVL and max pain, bear below both, else neutral
"""
from __future__ import annotations

import argparse
import datetime as dt
import shutil
import sys
from pathlib import Path

import numpy as np

import cme_gold as cg

HERE = Path(__file__).resolve().parent
UTC = dt.timezone.utc
STRIKE_BIN = 5.0          # GC strikes are on a $5 grid; kref is binned to it
N_WALLS = 5
GRID = np.linspace(0.90, 1.10, 801)   # HVL search grid (fraction of S_ref)


# --------------------------------------------------------------------------
# Sessions and IV
# --------------------------------------------------------------------------
def next_session(trade_date: dt.date) -> dt.datetime:
    """Next CME business day after trade_date, 13:30 UTC."""
    return dt.datetime.combine(cg.next_business_day(trade_date), dt.time(13, 30), UTC)


def fill_iv(book: dict, meta: dict) -> dict:
    """Replace zero/NaN IVs: interpolate in log-moneyness within the same
    expiry; fall back to meta['iv_fallback']."""
    iv = np.asarray(book["iv"], dtype=float).copy()
    bad = ~np.isfinite(iv) | (iv <= 0)
    if bad.any():
        x = np.log(book["kref"] / book["S_ref"])
        for e in np.unique(book["exp"][bad]):
            m = book["exp"] == e
            good = m & ~bad
            tgt = m & bad
            if good.sum() >= 2:
                o = np.argsort(x[good])
                iv[tgt] = np.interp(x[tgt], x[good][o], iv[good][o])
            else:
                iv[tgt] = meta["iv_fallback"]
        book = dict(book, iv=iv)
    return book


# --------------------------------------------------------------------------
# Gamma
# --------------------------------------------------------------------------
def row_gex(book, P):
    """Dealer gamma $ per 1% move for every row at reference price P
    (calls +, puts -; 100 oz per contract).  P may be an array -> (len(P), n)."""
    P = np.atleast_1d(np.asarray(P, dtype=float))[:, None]
    F = book["S"][None, :] * P / book["S_ref"]
    g = cg.b76_gamma(F, book["k"][None, :], book["T"][None, :], book["iv"][None, :], cg.RATE)
    sign = np.where(book["call"], 1.0, -1.0)[None, :]
    return sign * g * F * F * 0.01 * cg.CONTRACT_SIZE_OZ * book["oi"][None, :]


def _bins(kref, step=STRIKE_BIN):
    return np.round(kref / step) * step


def _flip(book, mask, S_ref):
    if not mask.any():
        return None
    sub = {k: (v[mask] if isinstance(v, np.ndarray) and v.shape == mask.shape else v)
           for k, v in book.items()}
    P = GRID * S_ref
    tot = row_gex(sub, P).sum(axis=1)
    s = np.sign(tot)
    idx = np.where(s[:-1] * s[1:] < 0)[0]
    if idx.size == 0:
        return None
    i = idx[np.argmin(np.abs(P[idx] - S_ref))]
    p0, p1, t0, t1 = P[i], P[i + 1], tot[i], tot[i + 1]
    return float(p0 - t0 * (p1 - p0) / (t1 - t0))


def _walls(book, mask):
    if not mask.any():
        return None, None
    g = row_gex(book, book["S_ref"])[0]
    b = _bins(book["kref"])
    cm, pm = mask & book["call"], mask & ~book["call"]
    cw = pw = None
    if cm.any():
        u, inv = np.unique(b[cm], return_inverse=True)
        cw = float(u[np.argmax(np.bincount(inv, weights=g[cm]))])
    if pm.any():
        u, inv = np.unique(b[pm], return_inverse=True)
        pw = float(u[np.argmax(np.bincount(inv, weights=-g[pm]))])
    return cw, pw


def max_pain(book, mask):
    if not mask.any():
        return None
    K, oi, c = book["kref"][mask], book["oi"][mask], book["call"][mask]
    cand = np.unique(_bins(K))
    pain = [(oi[c] * np.maximum(P - K[c], 0)).sum() + (oi[~c] * np.maximum(K[~c] - P, 0)).sum()
            for P in cand]
    return float(cand[int(np.argmin(pain))])


def atm_iv(book, mask):
    x = np.abs(book["kref"][mask] / book["S_ref"] - 1.0)
    iv = book["iv"][mask]
    if iv.size == 0:
        return None
    near = np.argsort(x)[:4]
    return float(np.mean(iv[near]))


def compute_levels(book: dict, meta: dict) -> dict:
    S = book["S_ref"]
    n = len(book["k"])
    if n == 0:
        raise ValueError("empty book")
    allm = np.ones(n, dtype=bool)
    g = row_gex(book, S)[0]
    b = _bins(book["kref"])
    u, inv = np.unique(b, return_inverse=True)
    net = np.bincount(inv, weights=g)
    gross = np.bincount(inv, weights=np.abs(g))

    cw, pw = _walls(book, allm)
    order = np.argsort(-np.abs(net))[:N_WALLS]
    walls = sorted(float(x) for x in u[order])
    hvl = _flip(book, allm, S)
    gw = float(u[np.argmax(gross)])

    front = int(book["exp"].min())
    fm = book["exp"] == front
    mp = max_pain(book, fm)
    c0, p0 = _walls(book, fm)
    f0 = _flip(book, fm, S)

    iv0 = atm_iv(book, fm) or meta["iv_fallback"]
    sig = iv0 * np.sqrt(1.0 / 252.0)
    vu, vd = S * np.exp(sig), S * np.exp(-sig)

    total = float(g.sum())
    pos = (S >= hvl) if hvl is not None else (total >= 0)
    regime = "+GEX" if pos else "-GEX"
    above_mp = mp is None or S >= mp
    bias = "bull" if (pos and above_mp) else ("bear" if (not pos and not above_mp) else "neutral")

    return {
        "S_ref": S, "ref_ticker": meta["ref_ticker"], "tv_ref": meta["tv_ref"],
        "hi": meta["hi"], "lo": meta["lo"],
        "CW": cw, "PW": pw, "walls": walls, "HVL": hvl, "GW": gw, "MP": mp,
        "C0": c0, "P0": p0, "F0": f0, "VU": float(vu), "VD": float(vd),
        "iv_atm": iv0, "net_gex": total, "regime": regime, "bias": bias,
        "front": dt.date.fromordinal(front).isoformat(),
        "trade_date": meta["stamps"].get("trade_date"),
        "session": book["session"].isoformat() if hasattr(book["session"], "isoformat") else str(book["session"]),
    }


# --------------------------------------------------------------------------
# Output
# --------------------------------------------------------------------------
def _p(x, nd=0):
    return "n/a" if x is None else f"{x:.{nd}f}"


def fmt_line(asset: str, lv: dict) -> str:
    walls = ",".join(_p(w) for w in lv["walls"])
    return (f"{asset:7s} [{lv['ref_ticker']} {lv['S_ref']:.1f} | TD {lv['trade_date']} | "
            f"front {lv['front']} | ATM iv {100*lv['iv_atm']:.1f}%]  "
            f"CW {_p(lv['CW'])}  PW {_p(lv['PW'])}  walls {walls}  HVL {_p(lv['HVL'], 1)}  "
            f"GW {_p(lv['GW'])}  MP {_p(lv['MP'])}  "
            f"0DTE C {_p(lv['C0'])} P {_p(lv['P0'])} F {_p(lv['F0'], 1)}  "
            f"VU {_p(lv['VU'], 1)} VD {_p(lv['VD'], 1)}  "
            f"regime {lv['regime']} bias {lv['bias']}")


def _levels_for_pine(lv):
    out = []
    add = lambda name, v, col, style, w: v is not None and out.append((name, v, col, style, w))  # noqa: E731
    add("CW", lv["CW"], "color.new(color.red, 0)", "line.style_solid", 2)
    add("PW", lv["PW"], "color.new(color.lime, 0)", "line.style_solid", 2)
    add("HVL", lv["HVL"], "color.new(color.orange, 0)", "line.style_solid", 2)
    add("GW", lv["GW"], "color.new(color.fuchsia, 0)", "line.style_dashed", 2)
    add("MP", lv["MP"], "color.new(color.gray, 0)", "line.style_dashed", 1)
    add("0DTE C", lv["C0"], "color.new(color.red, 40)", "line.style_dotted", 1)
    add("0DTE P", lv["P0"], "color.new(color.lime, 40)", "line.style_dotted", 1)
    add("0DTE F", lv["F0"], "color.new(color.orange, 40)", "line.style_dotted", 1)
    add("VU +1σ", lv["VU"], "color.new(color.aqua, 0)", "line.style_dashed", 1)
    add("VD -1σ", lv["VD"], "color.new(color.aqua, 0)", "line.style_dashed", 1)
    for w in lv["walls"]:
        if w not in (lv["CW"], lv["PW"], lv["GW"]):
            add("wall", w, "color.new(color.silver, 30)", "line.style_solid", 1)
    # one line per price: levels on the same strike share it ("CW/GW 4100")
    merged: dict = {}
    for name, v, col, style, w in out:
        key = round(v, 2)
        if key in merged:
            merged[key][0] += "/" + name
        else:
            merged[key] = [name, v, col, style, w]
    return [tuple(m) for m in merged.values()]


def pine_script(asset: str, lv: dict, generated: str) -> str:
    lvls = _levels_for_pine(lv)
    prices = ", ".join(f"{v:.2f}" for _, v, _, _, _ in lvls)
    names = ", ".join(f'"{n}"' for n, _, _, _, _ in lvls)
    cols = ", ".join(c for _, _, c, _, _ in lvls)
    styles = ", ".join(s for _, _, _, s, _ in lvls)
    widths = ", ".join(str(w) for _, _, _, _, w in lvls)
    info = (f"{asset} gamma | TD {lv['trade_date']} | {lv['ref_ticker']} {lv['S_ref']:.1f} | "
            f"{lv['regime']} {lv['bias']}")
    return f"""//@version=6
// Generated by gexmap.py on {generated}
// Source: CME Daily Bulletin (Sections 62/64), trade date {lv['trade_date']}
// Levels are in {lv['ref_ticker']} price space and are converted to the chart
// symbol with the live chart/{lv['ref_ticker']} price ratio.
indicator("Gamma Map - Gold", shorttitle="GammaMap", overlay=true, max_lines_count=100, max_labels_count=100)

refSym      = input.symbol("{lv['tv_ref']}", "Reference GC future")
convert     = input.bool(true, "Convert GC levels to chart symbol (live ratio)")
manualRatio = input.float(0.0, "Manual chart/GC ratio (0 = auto)", step=0.0001)
onlyGold    = input.bool(true, "Only draw on gold charts (XAU / GC)")
showLabels  = input.bool(true, "Show labels")
showTable   = input.bool(true, "Show info table")

refClose = request.security(refSym, timeframe.period, close, ignore_invalid_symbol=true)
isGC     = syminfo.root == "GC" or syminfo.root == "MGC"
isGold   = isGC or str.contains(syminfo.ticker, "XAU") or str.contains(syminfo.ticker, "GOLD")
ratio    = not convert or isGC ? 1.0 : manualRatio > 0 ? manualRatio : na(refClose) ? 1.0 : close / refClose

lvPrice = array.from({prices})
lvName  = array.from({names})
lvColor = array.from({cols})
lvStyle = array.from({styles})
lvWidth = array.from({widths})

var array<line>  gLines  = array.new<line>()
var array<label> gLabels = array.new<label>()
var table info = table.new(position.top_right, 1, 2, border_width=1)

if barstate.islast and (isGold or not onlyGold)
    for l in gLines
        line.delete(l)
    array.clear(gLines)
    for lb in gLabels
        label.delete(lb)
    array.clear(gLabels)
    for i = 0 to array.size(lvPrice) - 1
        p = array.get(lvPrice, i) * ratio
        array.push(gLines, line.new(bar_index - 1, p, bar_index, p, extend=extend.both, color=array.get(lvColor, i), style=array.get(lvStyle, i), width=array.get(lvWidth, i)))
        if showLabels
            array.push(gLabels, label.new(bar_index + 4, p, array.get(lvName, i) + " " + str.tostring(p, "#.0"), style=label.style_label_lower_left, color=color.new(color.black, 100), textcolor=array.get(lvColor, i), size=size.small))
    if showTable
        table.cell(info, 0, 0, "{info}", text_color=color.white, bgcolor=color.new(color.black, 30), text_size=size.small)
        table.cell(info, 0, 1, na(refClose) and convert and not isGC ? "ref " + refSym + " unavailable: set a manual ratio" : "ratio " + str.tostring(ratio, "#.#####"), text_color=color.silver, bgcolor=color.new(color.black, 30), text_size=size.small)
"""


# --------------------------------------------------------------------------
# Sources
# --------------------------------------------------------------------------
def gold_book(source: str, inbox: Path, log=print):
    if source == "bulletin":
        import bulletin_gold
        book, meta = bulletin_gold.load_gold(next_session, inbox, log=log)
    else:  # pragma: no cover - argparse restricts choices
        raise ValueError(f"unknown gold source {source}")
    return fill_iv(book, meta), meta


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--gold-source", choices=["bulletin"], default="bulletin",
                    help="where XAUUSD levels come from (default: CME bulletin PDFs)")
    ap.add_argument("--inbox", default=str(HERE / "inbox"), help="folder holding the bulletin PDFs")
    ap.add_argument("--out", default=str(HERE / "output" / "gamma_map.pine"))
    ap.add_argument("--no-move", action="store_true", help="leave processed PDFs in the inbox")
    a = ap.parse_args(argv)

    import bulletin_gold
    inbox = Path(a.inbox)
    results = {}
    try:
        book, meta = gold_book(a.gold_source, inbox)
        lv = compute_levels(book, meta)
        results["XAUUSD"] = (lv, meta)
        st = meta["stamps"]
        if st.get("synthetic"):
            print("  WARNING: these PDFs are SYNTHETIC test fixtures, not CME data -- levels are not real")
        print(f"  gold: {st['rows_parsed']} option rows parsed, {st['rows_book']} in book, "
              f"OI {st['oi_total']:,.0f}"
              + (f" (02B {st['oi_02b']:,.0f})" if st.get("oi_02b") is not None else "")
              + f", Black-76 vs bulletin delta median |diff| {st['delta_mad']:.4f}")
        if st.get("weekly_miss") is not None:
            print(f"  gold: Mon-Thu weekly rows placed by vol-surface fit; {st['weekly_miss_model']:,.0f} "
                  f"contracts differed from 02B per-series OI, {st['weekly_miss']:,.0f} after balancing")
    except bulletin_gold.NoBulletinData as e:
        print(f"XAUUSD  skipped: {e}. Download Sections 62 and 64 from the CME Daily Bulletin "
              f"into {inbox} (see README.md).")
    except bulletin_gold.BulletinError as e:
        print(f"XAUUSD  ERROR: {e}", file=sys.stderr)
        return 2

    if not results:
        return 1
    out = Path(a.out)
    out.parent.mkdir(parents=True, exist_ok=True)
    gen = dt.datetime.now(UTC).strftime("%Y-%m-%d %H:%M UTC")
    lv, meta = results["XAUUSD"]
    if meta["stamps"].get("synthetic"):
        gen += " FROM SYNTHETIC TEST DATA - NOT REAL LEVELS"
    out.write_text(pine_script("XAUUSD", lv, gen))
    for asset, (lv, _) in results.items():
        print(fmt_line(asset, lv))
    print(f"wrote {out}")

    if not a.no_move:
        done = inbox / "done"
        done.mkdir(parents=True, exist_ok=True)
        for f in meta["stamps"].get("files", []):
            shutil.move(f, done / Path(f).name)
        print(f"moved {len(meta['stamps'].get('files', []))} PDF(s) to {done}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
