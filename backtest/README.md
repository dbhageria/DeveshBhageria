# Gold grid-basket bot backtest

Backtest of the XAUUSD "grid basket" bot strategy from a social-media video, with an
optional higher-timeframe (HTF) trend filter for choosing buy or sell baskets.

## Data
1-minute XAUUSD bars, 2025-01-02 → 2026-05-15, from the public repo
[Gianniskas/mt5-m1-bars-us500-eurusd-xauusd](https://github.com/Gianniskas/mt5-m1-bars-us500-eurusd-xauusd)
(not included here). Clone it, then point the script at `XAUUSD_m1.csv`.

## Run
```bash
python3 -I grid_basket_backtest.py /path/to/XAUUSD_m1.csv 0.30 1000   # spread, start balance
python3 -I summarize.py r_s030_1000.jsonl
```
`swing_check.py` runs a slower wide-grid variant. `flip_check.py` compares the HTF modes with and
without `flip_close` (close the basket as soon as the HTF trend turns against it). Change the settings in `PROFILES` inside
`grid_basket_backtest.py`.

## Result files
| File | Spread | Start balance |
|---|---|---|
| `r_s030_10.jsonl` | $0.30 | $10 |
| `r_s030_1000.jsonl` | $0.30 | $1,000 |
| `r_s030_10000.jsonl` | $0.30 | $10,000 |
| `r_s0_1000.jsonl` | $0 | $1,000 |
| `r_swing.jsonl` | $0.30 | $10,000 (wide-grid variant) |
| `r_flip.jsonl` | $0.30 | $1,000 ($10,000 for swing); HTF modes, flip-close off vs on |
| `r_trend.jsonl` | $0.30 | $10,000; no-averaging trend strategy, 1% risk, all modes |
| `r_trend_swaps.jsonl` | $0.30 | $10,000; same, $20 stop, with overnight swaps |
| `r_trend_longonly.jsonl` | $0.30 | $10,000; buy-only-in-uptrend, full period and each half |

## Findings
- All main-grid runs (`video_like` / `moderate` / `conservative`) lost money overall, including at
  zero spread: the strategy has no edge.
- The wide-grid `swing` variant finished net-positive only in `htf_H4`, and only because one of its
  19 accounts reached the $1M cap (lottery variance, not edge). The other 4 swing modes lost money.
- The HTF filter (H1/H4 EMA50, D1 EMA20) changed basket win rates by under 1%.
- Closing the basket when the HTF trend flips changed almost nothing: 84-100% of deposits were
  still lost in every non-swing run, and 12-66% of flip-closes were in profit depending on the run.
- Starting from $10 with `video_like` settings: about 61,000 accounts blown in 16 months; the best
  reached $18.5k and none reached $100k. With `moderate` settings, one $10 account reached $793k
  before blowing up.
- Accounts that reach $1M are stopped there and counted as cashed out (the cap). Each result's
  `top_accounts` field lists its 3 best accounts with start, peak and end times.

## No-averaging trend strategy (`trend_backtest.py`, `trend_longonly_check.py`)
One trade at a time in the HTF direction, fixed $5/$10/$20 stop, take-profit at 1-3x the stop,
lot sized to lose 1% of balance at the stop. Compared with buy-only, sell-only and random
direction (5 seeds).
- $5 stops lost in every mode: the spread is too large a share of the stop.
- With $20 stops, H1/H4 trend trades earned +0.05 to +0.09R per trade (+97% to +127% over
  16 months) with 36-52% max drawdown. Buy-only earned about the same. Random direction averaged
  -44% to +20% depending on settings, but single seeds ranged from -72% to +188%: luck alone
  produces results as large as the best trend results.
- Split by side: buys in an H1/H4 uptrend earned +0.11 to +0.23R; sells in a downtrend earned
  about 0R. The filter helps by skipping buys in downtrends, not by selling.
- Buy only when the H1/H4 trend is up (otherwise stay out), $20 stop: +118% to +153% with
  26-30% max drawdown (+89% to +116% with swaps). It beat plain buying in Sep 2025-May 2026
  but not clearly in Jan-Aug 2025.
- Gold rose 31% in each half of the test, so none of this shows how it does in a falling or
  sideways market.

## Tests
```bash
python3 -m unittest -v test_backtest
```

## Limits
M1 bars rather than ticks; the bot rules are reconstructed from the video; no commissions or
slippage; swaps only where stated (the trend `swaps` runs).
