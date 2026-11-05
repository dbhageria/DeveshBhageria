# EA research: "London > $30 → NY continues > $60" on XAUUSD

**Claim tested** (Forex Factory, post #97,762):
> Anytime London moves more than $30 in a direction, NY has 87–98% chance of continuing over $60 move in that same direction.

## Verdict

**The claim is false. Over 16 years the strategy built from it is breakeven to losing; it only works
in some years, 2026 among them. Do not trade it live on this evidence.**

| Test | Data | Result |
|---|---|---|
| First look | 6 months of 2026, 5-minute bars, 20 trades | Looked good: PF 2.75 with SL $40 / TP $60 |
| Tick-exact, levels scaled by price | 2011–2018, 216M real ticks, 141 trades | PF 1.12, +0.04R per trade: breakeven |
| Tick-exact, levels scaled by volatility | 2011–2018, 216M real ticks, 386 trades | PF 0.72, −0.14R per trade: **losing** (−25% at 0.5% risk) |
| Latest data, scaled by price | 2019 – Oct 2026, Dukascopy 1m bid/ask, 168 trades | PF 1.07, +0.03R: breakeven (+2% at 0.5% risk over 8 years) |
| Latest data, scaled by volatility | 2019 – Oct 2026, Dukascopy 1m bid/ask, 392 trades | PF 1.01, +0.005R: breakeven |
| Walk-forward (several splits) | ticks and 1m | No setting is reliably positive out of sample; every CI includes zero or lies below it |

The good 2026 result is real in Dukascopy's data too: PF 1.8–3.3 over 26–33 trades in 2026.
But 2020, 2022 and 2025 lost money with the same rules, so it is a good year, not a lasting edge.

## 1. Tick-level backtest (most accurate test available here)

**Data:** Dukascopy XAUUSD bid/ask ticks from `FX-Data/FX-Data-XAUUSD-DS`, 10 May 2011 – 19 Jun 2018:
216 million ticks with median spreads of $0.24–0.40 and 1,814 usable NY sessions. Dukascopy's own
feed, which would cover 2019–2025, is blocked in this environment.

**Execution model:**
- **Entry:** the first tick at NY 08:00 (New York time), buying at the ask or selling at the bid.
- **Exits:** checked tick by tick (long on the bid, short on the ask). There's no guessing which of
  SL or TP came first.
- **Fills:** stops fill at the tick that crossed them, including any gap slippage. Targets fill at
  the limit price.
- **Session end:** anything still open is closed at the last tick before 17:00 NY.
- **London move:** the price at NY open minus the London 08:00 open.

**Scaling:** $30 meant something very different when gold was $1,300 and quiet. Every $ level is
therefore quoted in 2026 terms and scaled to each day in two ways:
- **By price:** $30 / $40 / $60 at $4,450 = 0.67% / 0.90% / 1.35% of price.
- **By volatility:** relative to the prior 20-day average daily range, against 2026's $96.8. So $30 ≈ 0.31 × that
  day's average range, with no look-ahead. This is the fairer comparison across market regimes.

### The claim on 7.5 years of ticks

| | Scaled by price: signal / all days | Scaled by volatility: signal / all days |
|---|---|---|
| A. NY makes +$60 in London's direction | 20.6% / 8.6% | 21.8% / 21.5% |
| B. NY makes −$60 against London | 15.6% / 6.0% | 22.8% / 19.0% |
| D. NY closes in London's direction | 51.8% / 47.3% | 44.3% / 47.3% |
| F. +$60 before −$30 | 17.7% | 17.4% |

- **Not 87–98%:** reading A is about 21% either way.
- **Big London days are volatile days, not directional days.** Under price scaling, B (−$60
  against London) rises as much as A does.
- **Once volatility is accounted for,** a big London move tells you nothing about NY's direction:
  21.8% after a signal against 21.5% on any day.

### Strategy with the default EA settings (London > $30, SL $40, TP $60)

| Year | Price-scaled trades | PF | avg R | Vol-scaled trades | PF | avg R |
|---|---|---|---|---|---|---|
| 2011 | 33 | 1.38 | +0.16 | 42 | 1.13 | +0.07 |
| 2012 | 15 | 0.86 | −0.05 | 48 | 0.44 | −0.34 |
| 2013 | 38 | 1.17 | +0.07 | 61 | 1.09 | +0.04 |
| 2014 | 21 | 0.75 | −0.08 | 55 | 0.54 | −0.25 |
| 2015 | 15 | 1.41 | +0.07 | 54 | 0.52 | −0.27 |
| 2016 | 13 | 1.37 | +0.10 | 44 | 0.83 | −0.08 |
| 2017 | 2 | 0.00 | −0.28 | 51 | 0.63 | −0.18 |
| 2018 (H1) | 4 | 0.29 | −0.28 | 31 | 0.77 | −0.13 |
| **2011–2018** | **141** | **1.12** | **+0.04** | **386** | **0.72** | **−0.14** |
| 2026 (1m bars) | 19 | 1.84 | +0.21 | 19 | 2.63 | +0.33 |

- **Equity at 0.5% risk per trade:** +2.9% over 7.5 years (price-scaled) or **−24.7%, max drawdown 27%**
  (vol-scaled).
- **Out of sample 2015–2018 (vol-scaled):** −0.17R per trade. The 95% bootstrap CI of −0.31 to
  −0.03 lies entirely below zero.
- **Walk-forward:** the best SL/TP chosen on 2011–2014 was PF 1.36–1.56 in sample and **PF 0.18–0.72
  out of sample.** Optimising the settings only fits noise.
- **The London filter adds nothing:** trading every day in London's direction, or raising the
  threshold, gives the same flat-to-negative expectancy (see `results/tick_backtest_output.txt`,
  section 3).

## 2. Latest data: 2019 – Oct 2026 (Dukascopy 1-minute bid/ask)

**Data:** Dukascopy XAUUSD 1-minute bars with separate bid and ask, Jan 2010 – 16 Oct 2026. They come
from the mirror `kevingtlin/dukascopy_XAUUSD_1m_Data`, downloaded with `dukascopy-node`. Dukascopy's
own server drops connections from this cloud environment, so the mirror was used. That gives 4,199
usable NY sessions, 1,951 of them in 2019–2026. The median spread at the NY open was $0.28–0.40
until 2024, then $0.56–0.59 in 2025–2026.

**Engine:** the same rules as the tick test. Longs enter at the ask and exit on the bid, shorts the
reverse, so the real spread is paid. Inside each minute, the extreme that hurts the trade is taken first.

**Check against ticks:** on the 2011–2018 overlap, the 1-minute engine takes the **same 141 / 384
trades**, gets the **same exit on every trade**, and its R correlates 0.999 with the tick engine.
It is slightly more pessimistic (−0.02R per trade), so the numbers below are, if anything, a little
low.

### The claim, 2019–2026

| | Price-scaled: signal / all days | Vol-scaled: signal / all days |
|---|---|---|
| A. NY makes +$60 in London's direction | 21.4% / 8.6% | 24.5% / 19.2% |
| B. NY makes −$60 against London | 14.3% / 7.3% | 23.2% / 21.1% |
| D. NY closes in London's direction | 50.0% / 48.7% | 50.3% / 48.7% |

That is the same picture as 2011–2018: about 1 in 4, not 87–98%.

### Default EA settings by year (London > $30, SL $40, TP $60)

| Year | Price-scaled trades | PF | avg R | Vol-scaled trades | PF | avg R |
|---|---|---|---|---|---|---|
| 2019 | 8 | 0.50 | −0.12 | 54 | 1.31 | +0.12 |
| 2020 | 36 | 1.01 | +0.01 | 62 | 0.76 | −0.12 |
| 2021 | 16 | 1.61 | +0.19 | 47 | 0.90 | −0.05 |
| 2022 | 20 | 0.48 | −0.24 | 63 | 0.55 | −0.26 |
| 2023 | 11 | 1.08 | +0.02 | 38 | 0.99 | −0.01 |
| 2024 | 14 | 1.46 | +0.14 | 48 | 1.56 | +0.20 |
| 2025 | 30 | 0.75 | −0.13 | 54 | 0.99 | −0.00 |
| 2026 (to 16 Oct) | 33 | 1.80 | +0.26 | 26 | 3.30 | +0.49 |
| **2019–2026** | **168** | **1.07** | **+0.03** | **392** | **1.01** | **+0.01** |

The literal, unscaled $30 / $40 / $60 settings only trigger often from 2025 onward. In 2026 they
gave 33 trades, PF 2.09 and +0.32R, which matches the first-look result. In 2025 they gave 17 trades
and PF 1.06.

### Walk-forward on the latest data

| Chosen on → tested on | Best in-sample settings, out of sample | Default 30/40/60, out of sample |
|---|---|---|
| 2011–2018 → 2019–2026 (price) | +0.13 to +0.18R, 92 trades, CI −0.16..+0.49 | +0.03R, CI −0.12..+0.17 |
| 2011–2018 → 2019–2026 (vol) | +0.12 to +0.18R, 128 trades, CI −0.13..+0.46 | +0.01R, CI −0.10..+0.11 |
| 2019–2022 → 2023–2026 (price) | +0.04 to +0.10R, 185 trades, CI includes 0 | +0.08R, CI −0.13..+0.28 |
| 2019–2022 → 2023–2026 (vol) | +0.24 to +0.27R, 58 trades, CI −0.36..+0.98 | +0.13R, 166 trades, CI −0.03..+0.29 |

None of these is statistically distinguishable from zero.

### The only leads worth watching (not proven)

These patterns showed up in more than one period. They were found by slicing the results after the
fact, though, so they need fresh data before anyone relies on them:

1. **Bigger London moves, held to the NY close.** In-sample selection kept picking a $40–50 London
   threshold with no SL/TP. That was positive out of sample (+0.12 to +0.18R), but on only 90–130
   trades with CIs that include zero.
2. **Short trades after London sell-offs** did better than longs. With price scaling they made
   +0.06R (2011–18), +0.15R (2019–22) and +0.15R (2023–26). With vol scaling they made +0.27R over
   70 trades in 2023–26. So the recent profits are not just gold's bull run: longs were flat. See
   `results/m1_direction_split.txt`.

The honest way to test either one is a forward test on a demo account from today, with the rule
fixed in advance. Optimising them further on this same history would just fit noise.

## 3. First look: 6 months of 2026 (5-minute bars)

XAUUSD 5-minute bars, 26 Mar – 25 Sep 2026, 129 days (`getdata-finance` free sample). London =
08:00–13:00 London time, NY = 08:00–17:00 New York time. Signal day = |London move| > $30
(20 days).

| Reading of the claim | Signal days | All days |
|---|---|---|
| A. NY touches +$60 in London's direction (from NY open) | 20% (4/20) | 16% |
| B. NY touches −$60 against London | 0% (0/20) | 16% |
| C. NY closes ≥ $60 in London's direction | 5% (1/20) | – |
| D. NY closes in London's direction at all | 60% (12/20) | 50% |
| E. Price gets $60 past the **London open** during NY | **85%** (17/20) | – |
| F. +$60 reached before −$30 (from NY open) | 15% (3/20) | – |

**Where the 87–98% comes from:** reading E counts the $60 from the London open, so London has
already done $30–66 of it. That describes the past, not a forecast.

The 2026 trade test with a $0.35 cost (SL counted first inside a 5m bar) gave SL 40 / TP 60:
60% wins, PF 2.75 over 20 trades. The tick test above shows this does not hold over a longer history.

## Remaining gaps

- 2019–2026 uses 1-minute bid/ask rather than raw ticks. The tick comparison above shows this
  changes results by only about 0.02R per trade, in the conservative direction.
- Your broker's spreads, commission and fills differ from Dukascopy's. A run of
  `LondonNYContinuation.mq5` in the MT5 Strategy Tester ("Every tick based on real ticks") on your
  broker's data is the final check for your own conditions.

## Files

- `m1_backtest.py`: 2010–2026 backtest on Dukascopy 1m bid/ask, including the tick cross-check
  (`python3 m1_backtest.py <repo>/xauusd [TICKS_DIR]`)
- `results/m1_backtest_output.txt`, `results/m1_direction_split.txt`: its output
- `tick_backtest.py`: tick-exact backtester (`python3 tick_backtest.py TICKS_DIR [XAUUSD_1m.csv]`)
- `prepare_ticks.py`: converts one year of FX-Data Dukascopy ticks (`python3 prepare_ticks.py 2015 WORK OUT`)
- `results/tick_backtest_output.txt`: full output of the tick backtest
- `london_ny_study.py`: the first-look 2026 study (`python3 london_ny_study.py XAUUSD_5m.csv`)
- `LondonNYContinuation.mq5`: MT5 Expert Advisor (London > $30, SL $40, TP $60, 0.5% risk, one trade per
  day; session times in server time, defaults for GMT+2/+3 brokers). Kept for testing in MT5;
  the evidence above says it should not be run live. Not compiled here.

*Research only, not financial advice. Past results do not guarantee future results.*
