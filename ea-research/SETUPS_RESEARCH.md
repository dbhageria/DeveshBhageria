# High-R:R gold setups: MultiHighRR_Setups.mq5 and the four target setups

Data: Dukascopy XAUUSD 1-minute bid/ask, Jan 2010 – 16 Oct 2026, results from 2011 on. Longs fill
at the ask and exit on the bid, shorts the reverse. Every trade pays the real spread plus $0.07/oz
commission. When the stop and target fall inside the same minute, the stop is counted first.

## Verdict

**None of them makes money.** The posted EA loses on every chart timeframe. The four setups,
turned into exact rules, lose in 69 of 72 variants. The 3 positive ones are noise (5 trades, 20
trades, and +0.002R), and none is positive in all three periods (2011–18, 2019–22, 2023–26).

The reason is the same in every case: **a bigger reward:risk comes with a proportionally lower win
rate.** The win rate lands at, or below, the breakeven rate for that R:R:

| Setup (all variants with 100+ trades) | Typical R:R | Win rate | Breakeven win rate |
|---|---|---|---|
| 1 Trend pullback into value | 3.5 | 18.0% | 22.1% |
| 2 BOS + retest | 4.9 | 16.2% | 16.9% |
| 3 Liquidity sweep + FVG | 3.8 | 20.9% | 22.2% |
| 4 Session breakout | 1.5 | 39.9% | 39.8% |

That's what random entries with costs would look like. "1:5 setups" are easy to find. The hard
part is a win rate above 1 / (1 + R:R), and none of these delivers it on gold, mechanically.

## 1. MultiHighRR_Setups.mq5 as posted (`multi_rr_backtest.py`)

Bugs found:
1. **It won't compile:** `IndicatorRelease(emaemaSlowHTF)` should be `emaSlowHTF`.
2. **Sell targets are on the wrong side:** `tp = entry - (entry - sl) * DefaultRR` is above the entry
   for a sell. MT5 rejects these orders as invalid stops, so almost no sells open; only some
   session-breakout sells get through. The same formula is used when MinRR forces a 5R target.
3. **Trailing never runs:** `if(PositionsByMagic() > 0) return;` exits OnTick before `ManageTrailing()`.
4. **BOS_Retest can never fire:** the swing range includes bar 2, so `close2 > swingHigh` is impossible.
5. **Smaller issues:**
   - `CalculateLots` rounds up to the minimum lot, which can over-risk.
   - Session 7–17 server time is 04–14 UTC on a GMT+2/+3 broker, not "London open".
   - `MaxSpreadPoints = 50` ($0.50) blocks many 2025–26 entries.

| Chart TF | As written: trades / win / avg R / PF | Bugs fixed: trades / avg R / PF |
|---|---|---|
| M5 | 27,812 / 9% / −0.55R / 0.46 | 43,601 / −0.53R / 0.44 |
| M15 | 9,993 / 11% / −0.34R / 0.65 | 16,096 / −0.30R / 0.64 |
| M30 | 5,419 / 12% / −0.23R / 0.75 | 8,839 / −0.23R / 0.72 |
| H1 | 3,258 / 13% / −0.14R / 0.84 | 5,257 / −0.14R / 0.82 |

All with the EA's default filters (7–17 server time, spread ≤ $0.50). Per-setup results are in
`results/multi_rr_by_setup.csv`. The only positive cell is H1 LiqSweep as written: 213 trades,
+0.14R, with a CI of −0.17 to +0.45, so it's noise.

## 2. The four setups as described (`setups_backtest.py`)

The exact rules are in the script's docstring. In short:

1. **Pullback:**
   - **Trend:** an H1 or H4 close above a rising EMA50, with EMA50 above EMA200.
   - **Impulse:** swing low → swing high of at least 2 ATR.
   - **Entry:** a pullback into the 50–61.8% Fibonacci zone, then a bullish engulfing or pin bar.
   - **Stop and target:** stop beyond the pullback extreme by 0.1 or 1 ATR. Target the swing high
     or a measured move, with a minimum R:R of 0, 3 or 4.
2. **BOS + retest:**
   - **Break:** a close beyond the last confirmed swing, 07–20 UTC.
   - **Retest:** within 24 bars, a bar touches the level, closes back on the break side, and is
     bullish (or bearish for shorts).
   - **Stop and target:** stop beyond the retest wick. Target the next liquidity (the prior 200-bar
     extreme) or 5R.
3. **Sweep + FVG:**
   - **Sweep:** of the Asian high/low or the previous day's high/low, 07–20 UTC.
   - **Displacement:** within 3 bars, a body of at least 1 ATR and 60% of the bar's range, closing
     back through the level and leaving an FVG.
   - **Entry:** a limit at the FVG edge, valid for 12 bars.
   - **Stop and target:** stop beyond the sweep extreme. Target the opposite liquidity or 5R.
4. **Breakout:**
   - **Range:** the Asian range (00–07 UTC) or London's first hour.
   - **Entry:** the first close outside the range before 12:00 UTC.
   - **Stop and target:** stop at the range midpoint or the opposite side. Target 1× or 2× the
     range height.

Best variant of each, with at least 100 trades:

| Setup | Best variant | Trades | Win % | R:R | PF | avg R | 2011–18 | 2019–22 | 2023–26 |
|---|---|---|---|---|---|---|---|---|---|
| 4 Breakout | M15, Asian range, stop opposite side, target 1× | 2,655 | 58% | 0.8 | 1.00 | +0.00 | +0.02 | −0.04 | +0.01 |
| 1 Pullback | M15, H1 trend, stop +1 ATR, measured move, min R:R 3 | 146 | 21% | 3.5 | 0.91 | −0.08 | −0.08 | −0.15 | −0.01 |
| 3 Sweep + FVG | M15, target opposite liquidity | 599 | 40% | 1.4 | 0.70 | −0.18 | −0.19 | −0.11 | −0.25 |
| 2 BOS + retest | M15, 5R target | 5,393 | 14% | 5.0 | 0.74 | −0.24 | −0.28 | −0.22 | −0.16 |

M5 is worse than M15 for every setup, because the stops are smaller, so costs take a bigger
share. The full 72-row table is in `results/setups_summary.csv` and `results/setups_output.txt`.

## Limits of this test

These are one reasonable set of mechanical rules for setups that traders apply with discretion
(which zone, which swing, which rejection). Different definitions will give different numbers. But
the pattern held across every variant: a target twice as far away was hit about half as often.
That points to the entries having no directional edge, not to a badly chosen target.

*Research only, not financial advice.*
