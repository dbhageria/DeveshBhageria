"""Generate SYNTHETIC CME-Daily-Bulletin-style PDFs for tests.

These are not CME data.  Prices come from a Black-76 model with a smile, laid
out the way the parser expects the real Section 62 / 64 / 02B pages to look
(fixed-width columns under stacked headings).  They exercise the code path end
to end; the tests marked `real` run against your actual PDFs in ./inbox.

    python tests/fixtures/make_synthetic_bulletin.py OUT_DIR [YYYY-MM-DD]
"""
from __future__ import annotations

import datetime as dt
import math
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parents[1]))

import numpy as np  # noqa: E402
from reportlab.lib.pagesizes import landscape, letter  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

import bulletin_gold as bg  # noqa: E402
import cme_gold as cg  # noqa: E402

FONT, SIZE, LEAD = "Courier", 6.5, 8.0
CW = 0.6 * SIZE  # Courier char width

# (label lines, value width in chars); values right-aligned in the column
OPT_COLS = [
    (("", "STRIKE"), 7), (("OPEN", "RANGE"), 9), (("", "HIGH"), 9), (("", "LOW"), 9),
    (("CLOSING", "RANGE"), 9), (("SETT.", "PRICE"), 9), (("PT.", "CHGE."), 8),
    (("", "DELTA"), 6), (("", "EXERCISES"), 9), (("VOLUME", "TRADES CLEARED"), 14),
    (("OPEN", "INTEREST"), 9), (("OPEN INTEREST", "CHANGE"), 13),
    (("CONTRACT", "HIGH"), 9), (("", "LOW"), 9),
]
FUT_COLS = [
    (("", "MONTH"), 7), (("OPEN", "RANGE"), 9), (("", "HIGH"), 9), (("", "LOW"), 9),
    (("CLOSING", "RANGE"), 9), (("SETT.", "PRICE"), 9), (("PT.", "CHGE."), 8),
    (("RTH", "VOLUME"), 8), (("GLOBEX", "VOLUME"), 8), (("OPEN", "INTEREST"), 9),
    (("OPEN INTEREST", "CHANGE"), 13), (("CONTRACT", "HIGH"), 9), (("", "LOW"), 9),
]
GAP = 2  # chars between columns


class Doc:
    def __init__(self, path, banner, cols):
        self.c = canvas.Canvas(str(path), pagesize=landscape(letter))
        self.c.setFont(FONT, SIZE)
        self.W, self.H = landscape(letter)
        self.banner, self.cols = banner, cols
        self.x = []
        x = 30.0
        for _, w in cols:
            self.x.append((x, x + w * CW))
            x += (w + GAP) * CW
        self.page = 0
        self._new_page()

    def _new_page(self):
        if self.page:
            self.c.showPage()
            self.c.setFont(FONT, SIZE)
        self.page += 1
        self.y = self.H - 30
        self.text(self.banner.replace("{p}", str(self.page)))
        self.y -= LEAD
        for i in (0, 1):
            for (lab, _), (x0, _x1) in zip(self.cols, self.x):
                if lab[i]:
                    self.c.drawString(x0, self.y, lab[i])
            self.y -= LEAD
        self.y -= 2

    def text(self, s):
        if self.y < 30:
            self._new_page()
        self.c.drawString(30, self.y, s)
        self.y -= LEAD

    def row(self, vals):
        if self.y < 30:
            self._new_page()
        for v, (x0, x1) in zip(vals, self.x):
            if v != "":
                self.c.drawRightString(x1, self.y, str(v))
        self.y -= LEAD

    def save(self):
        self.c.save()


def _f(v, nd=1):
    return f"{v:.{nd}f}"


def _oi(v):
    return f"{int(v):,}" if v else ""


def market(trade_date: dt.date):
    """Synthetic GC curve and option series alive on trade_date."""
    F0 = 4000.0
    futs = {}
    for y, m in [(2026, 11), (2026, 12), (2027, 1), (2027, 2), (2027, 4), (2027, 6),
                 (2027, 8), (2027, 12)]:
        months = (y - 2026) * 12 + m - 12
        F = F0 * math.exp(0.035 * months / 12.0)
        oi = {(2026, 12): 310_000, (2027, 2): 140_000, (2027, 4): 40_000}.get((y, m), 3_000)
        futs[(y, m)] = (round(F, 1), oi)
    series = [("OG", (2026, 12)), ("OG", (2027, 1)), ("OG", (2027, 2)), ("OG", (2027, 4)),
              ("G1R", (2026, 11)), ("OG1", (2026, 11)), ("G2M", (2026, 11)),
              ("G2T", (2026, 11)), ("G2W", (2026, 11)), ("G2R", (2026, 11)),
              ("OG2", (2026, 11)), ("OG3", (2026, 11))]
    return futs, series


def build(out_dir, trade_date=dt.date(2026, 11, 4), seed=7):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    futs, series = market(trade_date)
    day = trade_date.strftime("%a, %b %d, %Y")
    stamp = trade_date.strftime("%Y%m%d")

    # ---------------- Section 62 ----------------
    d = Doc(out / f"Section62_Metals_Futures_Products_{stamp}.pdf",
            f"PG62 BULLETIN # 214@  SYNTHETIC TEST DATA - NOT CME DATA   METAL FUTURES PRODUCTS"
            f"   PAGE {{p}}   {day}", FUT_COLS)
    d.text("SI COMEX SILVER FUTURES")
    d.row(["DEC26", "", "48.10", "47.20", "", "47.655", "+.120", "50000", "60000", "150,000", "", "", ""])
    d.text("GC COMEX GOLD FUTURES")
    for (y, m), (F, oi) in sorted(futs.items()):
        hi, lo = F * 1.012, F * 0.991
        d.row([f"{cg.MONTH_ABBR[m-1]}{y % 100:02d}", f"{F-8:.1f}", _f(hi), _f(lo), "",
               _f(F), "+12.4", str(oi // 50), str(oi // 3), _oi(oi), "+1,204", _f(F * 1.2), _f(F * .7)])
    d.text(f"TOTAL GC FUT {sum(v[1] for v in futs.values()):,}")
    d.text("MGC MICRO GOLD FUTURES")
    d.row(["DEC26", "", "4048.0", "3964.0", "", "4004.6", "+12.4", "9000", "90000", "50,000", "", "", ""])
    d.save()

    # ---------------- Section 64 ----------------
    d = Doc(out / f"Section64_Metals_Option_Products_{stamp}.pdf",
            f"PG64 BULLETIN # 214@  SYNTHETIC TEST DATA - NOT CME DATA   METAL OPTIONS PRODUCTS"
            f"   PAGE {{p}}   {day}", OPT_COLS)
    t_set = bg.expiry_utc(trade_date)
    total_oi = 0
    for code, ym in series:
        exp, uym = bg.series_expiry_and_underlying(code, ym)
        if exp <= trade_date:
            continue
        F = futs[uym][0]
        T = max((bg.expiry_utc(exp) - t_set).total_seconds() / (365 * 86400), cg.YEAR_MIN)
        kind = bg.series_info(code)[0]
        step = 25 if kind == "monthly" else 10
        width = 0.16 if kind == "monthly" else 0.06
        strikes = np.arange(round(F * (1 - width) / step) * step, F * (1 + width) + 1, step)
        label = f"{cg.MONTH_ABBR[ym[1]-1]}{ym[0] % 100:02d}"
        desc = "COMEX GOLD OPTIONS" if kind == "monthly" else "GOLD WEEKLY OPTIONS"
        for cp, name in (("C", "CALL"), ("P", "PUT")):
            d.text(f"{code} {name} {desc}")
            d.text(label)
            blk = 0
            for K in strikes:
                x = math.log(K / F)
                vol = 0.20 - 0.08 * x + 0.9 * x * x
                px = float(cg.b76_price(F, K, T, vol, cg.RATE, cp == "C"))
                px = round(px, 1)
                if px < 0.1:
                    continue
                dl = float(cg.b76_delta(F, K, T, vol, cg.RATE, cp == "C"))
                peak = 4000 if kind == "monthly" else 900
                oi = int(peak * math.exp(-((K - F + (60 if cp == "P" else -60)) / (F * 0.05)) ** 2)
                         * (1.6 if K % 100 == 0 else 1.0) * rng.uniform(0.7, 1.3))
                vol_tr = int(oi * rng.uniform(0.0, 0.3))
                blk += oi
                d.row([f"{K:.0f}", "", "", "", "", _f(px), "-1.2", f"{dl:.2f}".replace("0.", "."),
                       "", str(vol_tr) if vol_tr else "", _oi(oi), "", "", ""])
            d.text(f"TOTAL {code} {name} {blk:,}")
            total_oi += blk
    d.text("SO CALL COMEX SILVER OPTIONS")
    d.text("DEC26")
    d.row(["4700", "", "", "", "", "1.234", "", ".51", "", "100", "5,000", "", "", ""])
    d.text("OMG CALL MICRO GOLD OPTIONS")
    d.text("DEC26")
    d.row(["4000", "", "", "", "", "95.0", "", ".50", "", "100", "7,777", "", "", ""])
    d.save()

    # ---------------- Section 02B ----------------
    cols = [(("PRODUCT", "CODE"), 8), (("", "PRODUCT NAME"), 34), (("TOTAL", "VOLUME"), 10),
            (("OPEN", "INTEREST"), 12), (("OI", "CHANGE"), 9)]
    d = Doc(out / f"Section02B_Summary_Volume_And_Open_Interest_Metals_Futures_And_Options_{stamp}.pdf",
            f"PG02B DAILY INFORMATION BULLETIN SYNTHETIC TEST DATA - NOT CME DATA  SUMMARY VOLUME AND"
            f" OPEN INTEREST METALS   {day}", cols)
    d.row(["GC", "COMEX GOLD FUTURES", "120000", f"{sum(v[1] for v in futs.values()):,}", "+1,000"])
    d.row(["OG", "COMEX GOLD OPTIONS (ALL SERIES)", "45000", f"{total_oi:,}", "+500"])
    d.row(["SO", "COMEX SILVER OPTIONS", "9000", "80,000", "-20"])
    d.save()
    return total_oi


if __name__ == "__main__":
    td = dt.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else dt.date(2026, 11, 4)
    print("synthetic gold options OI:", build(sys.argv[1], td))
