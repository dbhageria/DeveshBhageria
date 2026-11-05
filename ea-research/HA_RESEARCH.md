# Heiken Ashi Conviction EA: backtest and settings search (XAUUSD)

## Verdict

**The strategy as specified loses money in every year from 2011 to 2026.** It made 19,487 trades
and averaged −0.32R per trade, which wipes the account out. One modified setting held up in every
check run here. It is a candidate for a demo forward test, not a proven edge:

> **Candidate A:** all **8/8** frames agree, initial stop beyond the last **24** M5 candles
> (2 hours) instead of 5, a fixed **1.5R** target instead of the trailing stop, and new baskets
> only between **07:00 and 20:00 UTC**.
> Over 2011 – Oct 2026 it made 219 trades (about 14 a year), 47% wins, PF 1.30 and +0.16R per trade.
> With the 0.5/1/2% risk tiers (always 2% here, since every trade has 8 votes), equity grew ×1.91,
> with an 18.6% maximum drawdown. It is now the EA's default.

Why the specification fails: the signal has no edge even before costs. With zero spread and zero
commission it makes −0.06R per trade (trailing) or −0.015R (2R target). The stop beyond five M5
candles is usually only $1–3 away ($9 in 2026), so the spread and commission cost 15–30% of R on
every trade.

## Data and engine

- **Data:** Dukascopy XAUUSD 1-minute bars with separate bid and ask, Jan 2010 – 16 Oct 2026, from
  the `kevingtlin/dukascopy_XAUUSD_1m_Data` mirror. That's 5.97M bars. On 2011–2018 the same data
  and engine matched a tick-by-tick replay (see README.md), with the same trades and exits.
- **Frames:** M1, M5, M15, M30, M45, H1, H4 and D1, built on broker server time (New York + 7 h).
  M45 is anchored to server midnight. Each frame uses only its last candle that had closed at the
  decision time, and a frame whose latest expected candle is missing blocks entries.
- **Heikin Ashi** is built from bid prices. A no-wick candle needs a body of at least 2 ticks and a
  wick of at most 1 tick. The **strict** vote counts only no-wick candles in the signal direction;
  the **colour** vote counts any HA candle of that colour.
- **Entry:** a fresh M1 no-wick signal with at least *min* agreeing frames (M1 included). It fills
  at the next minute's open: buys at the ask, sells at the bid. Only one basket is open at a time,
  and a new fresh signal is needed after each exit. The five equal legs share one stop, so they're
  simulated as one position.
- **Stop and exits:** the initial stop sits beyond the extreme of the last N completed M5 bid
  candles, plus $0.10. Longs exit on the bid and shorts on the ask. When the stop and the target or
  trail fall inside the same minute, the stop is assumed to hit first. Gaps fill at the open.
- **Costs:** the real Dukascopy spread on every fill, plus $0.07/oz round-trip commission (about
  $7 per lot).

## Settings search (`ha_backtest.py`)

864 settings: vote rule (strict / colour) × minimum votes (5–8) × side (both / buys / sells) ×
exit (trail 1/1, 1/0.5, 2/1; TP 1.5R, 2R, 3R) × hours (all / 07–20 UTC) × stop (5, 12 or 24 M5
candles). Full table: `results/ha_grid_all_settings.csv`.

| | Result |
|---|---|
| Positive average R over 2011–2026 | 109 / 864 |
| Positive in **each** of 2011–18, 2019–22 and 2023–26 | 13 / 864 |
| Settings with positive average R, by stop width | 5 M5: 3% · 12 M5: 8% · 24 M5: 27% |
| Settings with positive average R, by minimum votes | 5: 3% · 6: 5% · 7: 6% · 8: 37% |
| Settings with positive average R, by hours | all: 9% · 07–20 UTC: 16% |

What helps is a wider stop (costs become a smaller share of R), demanding more agreement, and
avoiding the Asian and rollover hours, when spreads are wide.

## Robustness checks (`ha_validate.py`)

**Walk-forward:** I ranked all settings on 2011–2018 only and took the top 10. Only 5 of the 10 were
positive on 2019–2026, with a median of −0.003R. So picking the best past setting usually does
**not** carry forward. Candidate A was the exception: +0.15R in sample, **+0.18R out of sample**
over 103 trades, PF 1.35.

**The best settings, with confidence intervals and cost sensitivity:**

| Setting | Trades | avg R | 95% CI | Commission $0 | $0.14/oz |
|---|---|---|---|---|---|
| **A: strict 8/8, both sides, TP 1.5R, 07–20 UTC, stop 24 M5** | 219 | **+0.164** | +0.000 .. +0.327 | +0.173 | +0.154 |
| B: strict 6/8, both sides, TP 3R, 07–20 UTC, stop 24 M5 | 1,437 | +0.098 | +0.005 .. +0.191 | +0.108 | +0.088 |
| colour 7/8, both sides, TP 3R, all hours, stop 24 M5 | 2,115 | +0.082 | +0.008 .. +0.160 | +0.094 | +0.070 |

These were chosen from 864 settings, so their averages are biased upward, and none clears zero by a
comfortable margin.

## Year by year (`ha_report.py`)

| Year | Spec default: trades / avg R | A: trades / avg R | B: trades / avg R |
|---|---|---|---|
| 2011 | 1,357 / −0.31 | 21 / +0.06 | 106 / +0.16 |
| 2012 | 1,075 / −0.40 | 8 / −1.01 | 88 / −0.20 |
| 2013 | 1,248 / −0.37 | 10 / +0.75 | 104 / −0.01 |
| 2014 | 1,145 / −0.39 | 11 / −0.10 | 72 / +0.10 |
| 2015 | 1,098 / −0.45 | 15 / +0.15 | 59 / +0.07 |
| 2016 | 1,318 / −0.38 | 18 / +0.38 | 94 / +0.18 |
| 2017 | 1,192 / −0.35 | 23 / +0.26 | 119 / +0.16 |
| 2018 | 1,308 / −0.35 | 10 / +0.23 | 97 / +0.10 |
| 2019 | 1,332 / −0.35 | 12 / +0.65 | 77 / +0.03 |
| 2020 | 1,147 / −0.25 | 12 / +0.66 | 84 / +0.33 |
| 2021 | 1,186 / −0.34 | 10 / −0.51 | 31 / +0.41 |
| 2022 | 1,193 / −0.31 | 7 / +0.07 | 81 / +0.28 |
| 2023 | 1,189 / −0.29 | 17 / +0.17 | 91 / −0.09 |
| 2024 | 1,281 / −0.22 | 11 / −0.10 | 99 / +0.13 |
| 2025 | 1,398 / −0.22 | 22 / +0.25 | 130 / +0.18 |
| 2026 (to 16 Oct) | 1,020 / −0.16 | 12 / +0.04 | 105 / −0.06 |
| **2011–2026** | **19,487 / −0.32** | **219 / +0.16** | **1,437 / +0.10** |
| Equity with risk tiers | **×0.00 (wiped out)** | ×1.91, max DD 18.6% | ×2.79, max DD 21.3% |

The 2026 trade lists for A and B are in `results/ha_trades_2026_*.csv`. They have server times,
entry, stop, target and result, for comparison with the MT5 Strategy Tester. Note: 2026 stops for A
are $28–49 wide, because gold's 2-hour range is large this year.

## The EA (`HeikenAshiConvictionEA.mq5`)

The EA follows the specification, and every tested option is an input. **Its defaults are candidate
A.** To get the original specification back, set InpMinVotes = 5, InpStopM5Bars = 5,
InpUseTrailing = true and InpUseSession = false.

Implemented:
- the eight-frame HA votes, with M45 built from M1 (complete buckets only, and the latest expected
  bucket must be present)
- fresh-signal entry, with no action on old signals at attach or restart
- 0.5/1/2% risk tiers with a hard cap, and legs rounded down
- a cost and slippage reserve and a margin cap
- a broker-side SL on every leg
- trailing or fixed-R exits
- the flat-account gate
- demo / tester only, hedging accounts only
- Auto OFF by default, with Auto and CLOSE HA BASKET buttons and a status panel

Simplified compared with your description:
- **Order results:** a failed or uncertain result pauses Auto and closes the basket. There is no
  broker-history reconciliation of delayed results.
- **Duplicate check:** there is no check for a second instance of the EA.
- **Panel:** it is a plain text panel, with no font or scale inputs.

`HeikenAshiConvictionEA-Backtest.set` is the tester preset: the same values as the source defaults,
except that entries are enabled.

**Not compiled here** (no MetaEditor in this environment), so compile it in MetaEditor (F7) and
fix anything it reports before testing.

### Testing in MT5

1. Copy the `.mq5` file to `MQL5/Experts/`, compile it, then open the Strategy Tester.
2. Choose XAUUSD on **M1** with **Every tick based on real ticks**, on a hedging account. Load
   `HeikenAshiConvictionEA-Backtest.set`.
3. The session inputs (10–23 server time) assume a GMT+2/+3 broker. That equals 07–20 UTC in summer
   and 08–21 UTC in winter, about one hour off the research in winter.
4. Compare the 2026 trades against `results/ha_trades_2026_A_8of8_tp1.5_ldnny_stop24.csv`. Dates and
   directions should broadly match; prices differ by your broker's feed and spread.

*Research only, not financial advice. Past results do not guarantee future results.*
