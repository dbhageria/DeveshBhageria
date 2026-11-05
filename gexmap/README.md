# gexmap — gold gamma levels for TradingView

`gexmap.py` turns COMEX gold options open interest into gamma levels and writes a
TradingView Pine script (`output/gamma_map.pine`) that draws them on an XAUUSD or GC
chart.

| Level | Meaning |
|---|---|
| **CW / PW** | Call wall / put wall: the strike with the most call / put gamma $ |
| **walls** | The five strikes with the largest \|net gamma $\| |
| **HVL** | Gamma flip: the price where total net dealer gamma crosses zero |
| **GW** | Gamma wall: the strike with the most total (call + put) gamma $ |
| **MP** | Max pain of the front expiry |
| **0DTE C / P / F** | Call wall, put wall and flip of the front expiry alone |
| **VU / VD** | ±1σ one-day move from the front-expiry ATM implied vol |
| **regime / bias** | `+GEX` above HVL (dealers long gamma), `-GEX` below; `bull` above HVL and MP, `bear` below both, else `neutral` |

## Gold data: CME Daily Bulletin PDFs (free, manual)

Gold levels come from **COMEX gold options on GC futures**: the OG monthlies plus
every weekly and daily series (Friday `OG1`–`OG5`, Monday `G1M`–`G5M`, Tuesday
`G1T`–`G5T`, Wednesday `G1W`–`G5W`, Thursday `G1R`–`G5R`). GLD is not used.

**The code never downloads anything from cmegroup.com.** CME's terms forbid
automated access, so you download the PDFs yourself.

### Where to download

CME Daily Bulletin page: <https://www.cmegroup.com/market-data/daily-bulletin.html>

| Section | File | Needed |
|---|---|---|
| **64 – Metal Options** | `daily_bulletin/current/Section64_Metals_Option_Products.pdf` | yes |
| **62 – Metal Futures** | `daily_bulletin/current/Section62_Metals_Futures_Products.pdf` | yes |
| 02B – Metals volume / OI summary | `daily_bulletin/current/Section02B_Summary_Volume_And_Open_Interest_Metals_Futures_And_Options.pdf` | optional cross-check |

All three live under `https://www.cmegroup.com/daily_bulletin/current/`. That folder is
overwritten every trading day, so save each day's files (the parser reads the trade date
from inside the PDF, so you don't need to rename them).

### When they're available

The preliminary bulletin is posted at about **midnight Chicago time** after each trading
day. That's about **17:00 Melbourne** in AEDT (Nov–Mar). In the northern summer it's
about 15:00 AEST, and about 16:00 for the weeks in March/April and October when only one
side is on daylight time. The final bulletin follows at about 10:00 Chicago time.

### Daily routine

1. Download Section 64 and Section 62 (optionally 02B) from the bulletin page.
2. Drop them into `gexmap/inbox/`.
3. Run:
   ```bash
   cd gexmap
   pip install -r requirements.txt     # first time only
   python gexmap.py
   ```
   It prints the gold line, writes `output/gamma_map.pine` and moves the processed
   PDFs to `inbox/done/` (`--no-move` keeps them; `--inbox PATH` reads another folder).
4. In TradingView, open the Pine Editor, paste `output/gamma_map.pine`, click
   **Add to chart**, and open an XAUUSD (or GC) chart.

The script converts the GC-space levels to the chart symbol with the live
chart / reference-future price ratio (`COMEX:GCZ2026`, for example). If your TradingView
plan has no COMEX data, set **Manual chart/GC ratio** in the indicator settings
(XAUUSD ÷ GC price).

If no bulletin PDFs are in the inbox, XAUUSD is skipped with a message. A bad parse
**is an error, never a silent success**: zero rows, strikes not clustered near the
futures price, a series without a GC future, an expiry before the trade date, or a
Black-76 delta that doesn't match the bulletin's delta column all stop the run.

### How the bulletin is turned into a book

* **Trade date**: read from the page header; the newest PDF of each section wins.
  Sections 62 and 64 must carry the same date.
* **Reference future**: the GC contract with the highest open interest. Every strike
  is converted into its price space: `kref = K * F_ref / F_underlying`.
* **Expiries**: the bulletin gives contract months, not expiry dates, so they're
  derived from CME rules. OG expires on the 4th-last business day of the month before
  the contract month (one day earlier if that's a Friday or the day before a holiday)
  and exercises into the nearest active GC month (Feb, Apr, Jun, Aug, Oct, Dec).
  Weeklies expire on the n-th weekday of the month in the block's label, and exercise
  into the GC month of the next monthly OG.
* **Implied vol**: backed out of each settlement price with Black-76 (`cme_gold.b76_iv`,
  r = `cme_gold.RATE`). The Black-76 delta is checked against the bulletin's delta
  column, and the run prints the median absolute difference.
* **T**: from the next session (13:30 UTC on `next_session(trade_date)`) to each
  option's 13:30 ET expiry, with a 30-minute floor.
* **hi / lo**: the reference future's session high/low from Section 62, else its settle.

### Checking a new PDF layout

```bash
python bulletin_gold.py --inspect inbox/
```

This prints block titles, every gold series with its derived expiry and GC future, the
detected column layout, the trade date, and raw rows next to the parsed fields. If CME
changes the layout, run this first.

## Automatic alternative: Databento

The fully automatic route is Databento's licensed CME feed (`GLBX.MDP3`: GC futures and
all OG / weekly gold options, with definitions, settlements and open interest). It's paid
(new accounts get free credit) and needs a `DATABENTO_API_KEY`. That path isn't included
in this folder yet; `--gold-source` currently accepts `bulletin` only.

## Tests

```bash
python -m pytest -q tests
```

* **Synthetic tests** run on `tests/fixtures/synthetic/*.pdf`, made by
  `tests/fixtures/make_synthetic_bulletin.py`. These are **not CME data**: they're
  model prices laid out like a bulletin, used to exercise the code end to end.
* **Real tests** run on your PDFs in `inbox/` or `inbox/done/` (or `$GEXMAP_INBOX`)
  and are skipped when none are present. They check for more than 500 gold option rows,
  strikes within ±15% of the futures price, a GC future for every series, a sensible
  delta match, and total OI within 2% of Section 02B when that PDF is present.

`inbox/` and `output/` are git-ignored. Don't commit CME PDFs to a public repo.
