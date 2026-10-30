# EA research: "London > $30 → NY continues > $60" on XAUUSD

**Claim tested** (Forex Factory, post #97,762):
> Anytime London moves more than $30 in a direction, NY has 87–98% chance of continuing over $60 move in that same direction.

## Data and definitions

- XAUUSD 5-minute bars, **26 Mar – 25 Sep 2026**, 129 complete trading days (free sample from
  `getdata-finance/xauusd-5m-ohlcv-metals-historical-data`). Gold traded around $4,280–4,620.
- **London** = 08:00–13:00 London time. **NY** = 08:00–17:00 New York time. DST is handled.
- **London move** = London close − London open (net, not range).
- **Signal day** = |London move| > $30 → **20 of 129 days (15%)**.

## Results

| Reading of the claim | Signal days | All days (baseline) |
|---|---|---|
| A. NY touches +$60 in London's direction (from NY open) | **20%** (4/20) | 16% |
| B. NY touches −$60 *against* London | 0% (0/20) | 16% |
| C. NY *closes* ≥ $60 in London's direction | 5% (1/20) | – |
| D. NY closes in London's direction at all | 60% (12/20) | 50% |
| E. Price gets $60 past the **London open** during NY | **85%** (17/20) | – |
| F. +$60 reached before −$30 (from NY open) | 15% (3/20) | – |

95% confidence intervals are wide at n = 20 (e.g. A: 8–42%, D: 39–78%, E: 64–95%).

**What this means**

1. **The literal claim is false.** NY reaches a fresh $60 move in London's direction only about 20% of
   the time, roughly the same as the 16% baseline.
2. **The 87–98% figure only appears under reading E,** where the $60 is counted from the London open.
   London has already covered $30–66 of it, so NY only has to add the rest. That figure measures
   a move that has mostly already happened, so it isn't a forecast.
3. **There is a weak edge in a different direction:** after a big London move, NY rarely reversed by
   $60 (0/20, against 16% on ordinary days). It closed in London's direction 60% of the time
   (50% on ordinary days). The London-to-NY correlation is +0.30 on signal days and +0.07 on all days.
   So it is a weak trend-persistence effect, not a reliable $60 extension.

## Tradeable version (what the EA does)

Enter at the NY open in London's direction. Use fixed $ SL/TP, close at the NY close, and assume
$0.35 cost per trade. When SL and TP both fall inside one 5-minute bar, the SL is counted first.

| SL | TP | Trades | Win % | Avg $/oz | Total $/oz | PF | Max DD $/oz |
|---|---|---|---|---|---|---|---|
| 20 | 60 | 20 | 40% | +3.9 | +77 | 1.36 | 145 |
| 30 | 60 | 20 | 45% | +2.8 | +55 | 1.22 | 163 |
| **40** | **60** | 20 | 60% | +12.9 | +257 | 2.75 | 112 |
| 60 | 60 | 20 | 60% | +13.0 | +261 | 2.82 | 109 |
| 40 | 80 | 20 | 60% | +15.7 | +315 | 3.15 | 112 |
| none | none (exit at NY close) | 20 | 60% | +10.2 | +203 | 2.42 | 109 |

Wider stops worked better because the edge is "London's direction doesn't fully reverse",
not "NY runs $60". Tight stops get shaken out.

**Do not trust these numbers yet.** 20 trades over 6 months is far too few, and picking the best
SL/TP from this table overfits. Before risking money:

1. Backtest `LondonNYContinuation.mq5` in the MT5 Strategy Tester with **"Every tick based on real
   ticks"** over 2020–2026, ideally 2015–2026. Scale the thresholds for older years, because $30 at
   $1,800 gold is a different move than at $4,400. A version based on % or ATR is the obvious next step.
2. Look for at least ~150 trades with PF > 1.3 holding up in both halves of the data
   (walk-forward), not just the full period.
3. Demo-trade it for 1–2 months and compare fills and spreads around the NY open.

## Files

- `london_ny_study.py`: the analysis (`python3 london_ny_study.py XAUUSD_5m.csv`)
- `LondonNYContinuation.mq5`: MT5 Expert Advisor. Session times are **server time**; the defaults suit
  GMT+2/+3 brokers. Defaults: London > $30, SL $40, TP $60, 0.5% risk, one trade per day.
  Not compiled here; compile in MetaEditor before use.

*Research only, not financial advice. Past results do not guarantee future results.*
