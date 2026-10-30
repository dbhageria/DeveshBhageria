# EA research: "London > $30 → NY continues > $60" on XAUUSD

**Claim tested** (Forex Factory, post #97,762):
> Anytime London moves more than $30 in a direction, NY has 87–98% chance of continuing over $60 move in that same direction.

## Verdict

**The claim is false, and the strategy built from it has no edge that survives testing. Do not trade it live.**

| Test | Data | Result |
|---|---|---|
| First look | 6 months of 2026, 5-minute bars, 20 trades | Looked good: PF 2.75 with SL $40 / TP $60 |
| Tick-exact, levels scaled by price | 2011–2018, 216M real ticks, 141 trades | PF 1.12, +0.04R per trade: breakeven |
| Tick-exact, levels scaled by volatility | 2011–2018, 216M real ticks, 386 trades | PF 0.72, −0.14R per trade: **losing** (−25% at 0.5% risk) |
| Walk-forward (best of 2011–14 → tested on 2015–18) | ticks | Best settings lost money out of sample under both scalings |

The good 2026 result is 19–20 trades. It falls within the range of luck, and 7.5 years of tick data
do not reproduce it.

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

## 2. First look: 6 months of 2026 (5-minute bars)

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

## How to get even more accuracy

The one thing missing is 2019–2025 tick data. To close that gap, either:
1. Run `LondonNYContinuation.mq5` in the MT5 Strategy Tester with **"Every tick based on real ticks"**
   on your broker's 2019–2026 data, or
2. Allow `datafeed.dukascopy.com` in this cloud environment's network settings, and the same
   tick backtest can be extended to 2026.

Expect the same answer. Seven-plus years of ticks, two scaling methods and a walk-forward all
point the same way.

## Files

- `tick_backtest.py`: tick-exact backtester (`python3 tick_backtest.py TICKS_DIR [XAUUSD_1m.csv]`)
- `prepare_ticks.py`: converts one year of FX-Data Dukascopy ticks (`python3 prepare_ticks.py 2015 WORK OUT`)
- `results/tick_backtest_output.txt`: full output of the tick backtest
- `london_ny_study.py`: the first-look 2026 study (`python3 london_ny_study.py XAUUSD_5m.csv`)
- `LondonNYContinuation.mq5`: MT5 Expert Advisor (London > $30, SL $40, TP $60, 0.5% risk, one trade per
  day; session times in server time, defaults for GMT+2/+3 brokers). Kept for testing in MT5;
  the evidence above says it should not be run live. Not compiled here.

*Research only, not financial advice. Past results do not guarantee future results.*
