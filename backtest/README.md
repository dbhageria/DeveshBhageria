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
`swing_check.py` runs a slower wide-grid variant. Change the settings in `PROFILES` inside
`grid_basket_backtest.py`.

## Result files
| File | Spread | Start balance |
|---|---|---|
| `r_s030_10.jsonl` | $0.30 | $10 |
| `r_s030_1000.jsonl` | $0.30 | $1,000 |
| `r_s030_10000.jsonl` | $0.30 | $10,000 |
| `r_s0_1000.jsonl` | $0 | $1,000 |
| `r_swing.jsonl` | $0.30 | $10,000 (wide-grid variant) |

## Findings
- All main-grid runs (`video_like` / `moderate` / `conservative`) lost money overall, including at
  zero spread: the strategy has no edge.
- The wide-grid `swing` variant finished net-positive only in `htf_H4`, and only because one of its
  19 accounts reached the $1M cap (lottery variance, not edge). The other 4 swing modes lost money.
- The HTF filter (H1/H4 EMA50, D1 EMA20) changed basket win rates by under 1%.
- Starting from $10 with `video_like` settings: about 61,000 accounts blown in 16 months; the best
  reached $18.5k and none reached $100k. With `moderate` settings, one $10 account reached $793k
  before blowing up.
- Accounts that reach $1M are stopped there and counted as cashed out (the cap). Each result's
  `top_accounts` field lists its 3 best accounts with start, peak and end times.

## Tests
```bash
python3 -m unittest -v test_backtest
```

## Limits
M1 bars rather than ticks; the bot rules are reconstructed from the video; no swaps,
commissions or slippage.
