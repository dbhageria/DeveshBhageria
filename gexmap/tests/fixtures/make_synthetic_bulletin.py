"""Generate SYNTHETIC CME-Daily-Bulletin-style PDFs for tests.

These are NOT CME data.  Prices come from a Black-76 model with a smile; the
pages copy the layout of the real 2026 Sections 62, 64 and 02B (text rows,
the page-1 last-trade-date table, unsigned deltas, TOTAL lines, mixed
call/put/week blocks for the Mon-Thu gold weeklies, the EOO/blocks tail, and
the column-positioned 02B summary).

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
from reportlab.lib.pagesizes import letter  # noqa: E402
from reportlab.pdfgen import canvas  # noqa: E402

import bulletin_gold as bg  # noqa: E402
import cme_gold as cg  # noqa: E402

FONT, SIZE, LEAD = "Courier", 6.0, 7.2
MARK = "SYNTHETIC TEST DATA - NOT CME DATA"


class Doc:
    def __init__(self, path, banner):
        self.c = canvas.Canvas(str(path), pagesize=letter)
        self.W, self.H = letter
        self.banner = banner
        self.page = 0
        self._new_page()

    def _new_page(self):
        if self.page:
            self.c.showPage()
        self.c.setFont(FONT, SIZE)
        self.page += 1
        self.y = self.H - 24
        for t in self.banner:
            self.line(t, check=False)

    def line(self, s, check=True):
        if check and self.y < 24:
            self._new_page()
        self.c.drawString(18, self.y, s)
        self.y -= LEAD

    def at(self, items):
        """[(x, text, 'l'|'r')] on one line."""
        if self.y < 24:
            self._new_page()
        for x, t, al in items:
            (self.c.drawRightString if al == "r" else self.c.drawString)(x, self.y, t)
        self.y -= LEAD

    def save(self):
        self.c.save()


def vol(x, T):
    return 0.21 - 0.10 * x + 1.2 * x * x + (0.03 if T < 0.01 else 0.0)


def _delta_txt(d):
    d = abs(d)
    return "1.0000" if d >= 0.99995 else f"{d:.4f}".lstrip("0")


def _row(K, px, dl, oi, vol_tr):
    return (f"{K:.0f} ---- ---- ---- ---- ---- {px:.2f} - 1.20 {_delta_txt(dl)} ---- ---- "
            f"{vol_tr if vol_tr else '----'} ---- {oi} UNCH")


def _total(vol_tr, oi):
    return f"TOTAL {vol_tr} {oi} + 0" if vol_tr else f"TOTAL {oi} 0"


def market():
    futs = {}
    for y, m in [(2026, 11), (2026, 12), (2027, 1), (2027, 2), (2027, 4), (2027, 6)]:
        months = (y - 2026) * 12 + m - 12
        F = round(4000.0 * math.exp(0.10 * months / 12.0), 1)
        oi = {(2026, 12): 300_000, (2027, 2): 90_000, (2027, 4): 20_000}.get((y, m), 400)
        futs[(y, m)] = (F, oi)
    return futs


def build(out_dir, trade_date=dt.date(2026, 11, 4), seed=11):
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    rng = np.random.default_rng(seed)
    futs = market()
    day = trade_date.strftime("%a, %b %d, %Y")
    stamp = trade_date.strftime("%Y%m%d")
    t_set = bg.expiry_utc(trade_date)
    truth = {}            # (code, cp) -> OI, for the 02B summary and tests

    def T_of(e):
        return max((bg.expiry_utc(e) - t_set).total_seconds() / (365 * 86400), cg.YEAR_MIN)

    def price_rows(F, T, strikes, cp, peak, skew):
        rows = []
        for K in strikes:
            x = math.log(K / F)
            sig = vol(x, T)
            px = round(float(cg.b76_price(F, K, T, sig, cg.RATE, cp == "C")), 1)
            if px < 0.1:
                continue
            dl = float(cg.b76_delta(F, K, T, sig, cg.RATE, cp == "C"))
            oi = int(peak * math.exp(-((K - F - skew) / (F * 0.04)) ** 2)
                     * (1.8 if K % 100 == 0 else 1.0) * rng.uniform(0.6, 1.4))
            vt = int(oi * rng.uniform(0, 0.4))
            rows.append((K, px, dl, oi, vt))
        return rows

    # ---------------- Section 64
    d = Doc(out / f"Section64_Metals_Option_Products_{stamp}.pdf",
            ["64 METALS OPTIONS PRODUCTS 64", MARK,
             f"PG64 BULLETIN # 214@ METALS OPTION PRODUCTS {day} PG64"])
    monthly = [(2026, 12), (2027, 1), (2027, 2)]
    fridays = [1, 2, 3, 4]
    d.line("METAL FUTURES CONTRACTS LAST TRADE DATES")
    og_dates = " ".join(cg.og_monthly_expiry(*ym).strftime("%m/%d") for ym in monthly)
    d.line(f"OG CALL {og_dates}")
    d.line(f"OG PUT {og_dates}")
    for n in fridays:
        e = cg.weekly_expiry(2026, 11, 4, n).strftime("%m/%d")
        d.line(f"OG{n} CALL {e}")
        d.line(f"OG{n} PUT {e}")
    gwr = [e for e in (cg.weekly_expiry(2026, 11, 3, n) for n in range(1, 6)) if e and e > trade_date][:3]
    d.line(f"GWR THU {gwr[-1].strftime('%m/%d')}")
    d.line("OMG OPT 11/24")
    d.line("GOLD OPTIONS ON FUTURES")
    d.line("OMG OPT MICRO GOLD OPTIONS")
    d.line("DEC26")
    d.line("4000 ---- ---- ---- ---- ---- 48.70 - 69.40 .3483 ---- ---- ---- ---- 3 UNCH")
    d.line(_total(0, 3))

    # Friday weeklies (labelled C/P blocks)
    for n in fridays:
        e = cg.weekly_expiry(2026, 11, 4, n)
        F = futs[cg.weekly_underlying(e)][0]
        for cp, name in (("P", "PUT"), ("C", "CALL")):
            rows = price_rows(F, T_of(e), np.arange(3700, 4301, 25), cp, 900, -50 if cp == "P" else 50)
            d.line(f"OG{n} {name} GOLD OPTIONS")
            d.line("NOV26")
            for r in rows:
                d.line(_row(*r))
            d.line(_total(sum(r[4] for r in rows), sum(r[3] for r in rows)))
            truth[(f"OG{n}", cp)] = sum(r[3] for r in rows)

    # monthly OG (labelled)
    for cp, name in (("P", "PUT"), ("C", "CALL")):
        tot = 0
        for ym in monthly:
            e = cg.og_monthly_expiry(*ym)
            F = futs[cg.gc_underlying_month(*ym)][0]
            rows = price_rows(F, T_of(e), np.arange(3400, 4601, 25), cp, 4000, -80 if cp == "P" else 80)
            if cp == "C":      # far-OTM lottery-ticket calls at the minimum tick (as in real gold)
                rows.append((15000, 0.1, float("nan"), 3000, 0))
            d.line(f"OG {name} COMEX GOLD OPTIONS")
            d.line(f"{cg.MONTH_ABBR[ym[1]-1]}{ym[0] % 100:02d}")
            for K, px, dl, oi, vt in rows:
                if np.isnan(dl):
                    d.line(f"{K:.0f} ---- ---- ---- ----/0.10A ---- 0.10 UNCH ---- ---- ---- ---- ---- {oi} UNCH")
                else:
                    d.line(_row(K, px, dl, oi, vt))
            d.line(_total(sum(r[4] for r in rows), sum(r[3] for r in rows)))
            tot += sum(r[3] for r in rows)
        truth[("OG", cp)] = tot

    # Thursday weeklies: weeks and calls/puts mixed in one unlabelled block
    mixed = []
    for e in gwr:
        F = futs[cg.weekly_underlying(e)][0]
        code = bg.series_code(3, e)
        for cp in ("C", "P"):
            rows = price_rows(F, T_of(e), np.arange(3850, 4151, 10), cp, 600, 0)
            truth[(code, cp)] = sum(r[3] for r in rows)
            mixed += [(r, e, cp) for r in rows]
    order = rng.permutation(len(mixed))
    mixed = sorted((mixed[i] for i in order), key=lambda m: m[0][0])
    d.line("GWR THU GOLD WEEKLY THURSDAY OPTION Week1")
    d.line("NOV26")
    for r, _, _ in mixed:
        d.line(_row(*r))
    d.line(_total(sum(m[0][4] for m in mixed), sum(m[0][3] for m in mixed)))

    d.line("SO CALL COMEX SILVER OPTIONS")
    d.line("DEC26")
    d.line("47 ---- ---- ---- ---- ---- 1.23 - 0.10 .5100 ---- ---- 100 ---- 5000 UNCH")
    d.line(_total(100, 5000))
    d.line("OPTIONS EOO'S AND BLOCKS")
    d.line("PRODUCT STRIKE PRICE EOO VOLUME BLOCK VOLUME")
    d.line("OG CALL COMEX GOLD OPTIONS")
    d.line("DEC26 CALL")
    d.line("OG 4050 0 100")
    d.save()

    # ---------------- Section 62
    d = Doc(out / f"Section62_Metals_Futures_Products_{stamp}.pdf",
            ["62 METAL FUTURES PRODUCTS 62", MARK,
             f"PG62 BULLETIN # 214@ METAL FUTURES PRODUCTS {day} PG62"])
    d.line("1OZ FUT 1 OUNCE GOLD FUTURES")
    d.line("DEC26 3939.00 4081.00 /3928.00 9999.00 + 132.50 93949 ---- 81120 - 30")
    d.line("TOTAL 1OZ FUT 96059 86237 + 5")
    d.line("GC FUT COMEX GOLD FUTURES")
    for (y, m), (F, oi) in sorted(futs.items()):
        d.line(f"{cg.MONTH_ABBR[m-1]}{y % 100:02d} {F-60:.2f} {F*1.01:.2f} /{F*0.975:.2f} {F:.2f} "
               f"+ 132.60 {oi // 2} ---- {oi} - 1715")
    d.line("MGC FUT MICRO GOLD FUTURES")
    d.line("DEC26 3939.00 4081.00 /3928.00 8888.00 + 132.50 93949 ---- 999999 - 30")
    d.line("GC FUT 11/25 12/29 01/27 02/24")
    d.save()

    # ---------------- Section 02B (column positions as in the real page)
    d = Doc(out / f"Section02B_Summary_Volume_And_Open_Interest_Metals_Futures_And_Options_{stamp}.pdf",
            ["02B 02B", MARK,
             f"PG02BBULLETIN # 214@ EXCHANGE SUMMARY VOLUME AND OPEN INTEREST {day}PG02B"])
    d.at([(186, "GLOBEX", "l"), (228, "OPEN", "l"), (246, "OUTCRY", "l"), (281, "PNT", "l")])
    d.at([(324, "VOLUME", "l"), (386, "OPEN", "l"), (404, "INTEREST", "l"), (436, "&", "l"),
          (443, "CHGE", "l"), (498, "VOLUME", "l"), (541, "OPEN", "l"), (560, "INTEREST", "l")])
    names = {"OG": "COMEX GOLD OPTIONS", "G1R": "GOLD WEEKLY THURSDAY OPTION Week1",
             "G2R": "GOLD WEEKLY THURSDAY OPTION Week2", "G3R": "GOLD WEEKLY THURSDAY OPTION Week3"}
    for (code, cp), oi in sorted(truth.items()):
        v = str(int(oi * 0.1))
        d.at([(19, code, "l"), (33, names.get(code, "GOLD OPTIONS"), "l"), (168, cp, "l"),
              (210, v, "r"), (351, v, "r"), (404, f"{oi}", "r"), (414, "+", "l"), (450, "12", "r"),
              (527, "100", "r"), (587, "2000", "r")])
    d.at([(19, "G4T", "l"), (33, "GOLD WEEKLY TUESDAY OPTION Week4", "l"), (168, "C", "l"),
          (527, "41", "r"), (587, "183", "r")])           # prior-year columns only
    d.at([(19, "SO", "l"), (33, "COMEX SILVER OPTIONS", "l"), (168, "C", "l"),
          (210, "9000", "r"), (351, "9000", "r"), (404, "80000", "r")])
    d.save()
    return truth


if __name__ == "__main__":
    td = dt.date.fromisoformat(sys.argv[2]) if len(sys.argv) > 2 else dt.date(2026, 11, 4)
    t = build(sys.argv[1], td)
    print("synthetic gold options OI:", sum(t.values()))
