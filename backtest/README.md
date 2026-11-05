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
- Every version lost money, including with zero spread: the strategy has no edge.
- The HTF filter (H1/H4 EMA50, D1 EMA20) changed basket win rates by under 1%.
- Starting from $10 like the video: about 61,000 accounts blown in 16 months; the best reached
  $18.5k and none reached $100k.

## Limits
M1 bars rather than ticks; the bot rules are reconstructed from the video; no swaps,
commissions or slippage.
