# Strategy search: gold (XAUUSD) M15, 2010-2019 + 2025-2026

A structured search for a profitable, tradeable gold setup, using rules fixed **before** any
results were seen, so that a result found by luck is unlikely to pass.

## Data
- 2010-2019: 1-minute XAUUSD from
  [parshant65-lang/Gold-XAUUSD-1m-2010-2014-Historical-Data](https://github.com/parshant65-lang/Gold-XAUUSD-1m-2010-2014-Historical-Data)
  and [parshant65-lang/Gold-XAUUSD-1m-2015-2019-Historical-Data](https://github.com/parshant65-lang/Gold-XAUUSD-1m-2015-2019-Historical-Data).
- 2025-2026: 1-minute XAUUSD from Gianniskas/mt5-m1-bars-us500-eurusd-xauusd.
- Resampled to 15-minute bars by `prepare_data.py`. There is no 2020-2024 data; each continuous
  block runs on its own so no indicator spans the gap (`engine.load_segment`).

## Protocol (fixed in advance)
| Stage | Period | Use |
|---|---|---|
| Search | 2010-2016 (bull, 2013 crash, bear, sideways) | all tuning |
| Validation | 2017-2019 | candidates checked once |
| Hold-out | Mar 2025 - May 2026 (Jan-Feb 2025 = indicator warm-up) | one setup checked once |

A setup is **tradeable** only if all of these hold:
1. Profitable in all three periods after spread and swaps.
2. Profit factor >= 1.15 in search, >= 1.05 in validation and hold-out.
3. Max drawdown <= 30% at 1% risk per trade.
4. >= 200 trades in search, >= 50 in validation and in hold-out.
5. >= 4 of 7 search years profitable.
6. Neighbouring settings (one step in one parameter) also work: median profit factor >= 1.05
   and >= 60% profitable.

Selection rule: of the candidates passing validation, the one with the best search-period
profit factor goes to the hold-out.

Costs: spread max($0.30, 1.1 bp of price) per round trip; swaps 2.4 bp of price per night for
longs and 0.15 bp for shorts (triple on Wednesday). Stress test = 1.5x costs.

## What was tested (`strategies.py`): 1,344 settings in 5 families
| Family | Settings | Profitable in 2010-2016 |
|---|---|---|
| Trend pullback (RSI dip in H1/H4/D1 trend) | 432 | 0.5% |
| Donchian channel breakout | 432 | 46% |
| Asian-range breakout | 192 | 6% |
| Bollinger mean reversion | 144 | 4% |
| Higher-timeframe MA crossover | 144 | 41% |

22 settings passed the search rules and the robustness check; all were Donchian breakouts.
15 of 22 passed validation; all 5 without the D1 trend filter failed.

## Selected setup
**Donchian 192 + D1 trend filter, M15 chart**
- **Long:** M15 bar closes above the highest high of the previous 192 M15 bars (2 days) and the
  last completed daily close is above its 50-day EMA. **Short:** mirror image.
- Enter at the next bar's open. One position at a time.
- Initial stop 3 x ATR(14, M15) from entry. Trailing stop 5 x that ATR behind the best price
  since entry. No fixed take-profit.
- Risk 1% of balance per trade.

| Period | Trades | Profit factor | Avg/trade | Return | Max DD |
|---|---|---|---|---|---|
| 2010-2016 (search) | 532 | 1.36 | +0.19R | +159% | 23.0% |
| 2017-2019 (validation) | 247 | 1.20 | +0.11R | +26% | 19.5% |
| Mar 2025 - May 2026 (hold-out) | 117 | 1.97 | +0.50R | +76% | 10.1% |
| Hold-out, 1.5x costs | 117 | 1.90 | +0.47R | +70% | 10.8% |
| 2010-2019, 1.5x costs | 779 | 1.21 | +0.12R | +126% | 27.4% |

- Profitable in 9 of 12 years; losing years: 2014 (-5.3R), 2018 (-5.9R); 2012 flat.
- Both directions work: shorts made +29.5R in the 2013 crash, longs made +32R in 2016.
- Win rate 37%, average win +2.0R, average loss -0.86R, longest losing streak 13 trades,
  median hold about 8 hours.
- Luck test: random entries in the D1-trend direction with the same stops averaged -0.03R
  (best of 50 seeds +0.02R); the breakout entry averaged +0.17R, beating all 50.

## Caveats
- 1,344 settings were tried; the untouched validation and hold-out periods and the luck test
  are what make the result credible, but the hold-out has only 117 trades.
- Backtested on 15-minute bars; a stop and a profit level inside one bar count as the stop.
  No slippage beyond the spread.
- Long losing streaks (13 in a row, 27% drawdown at 1.5x costs) are normal for this style.
- Past results do not guarantee future results; forward-test on a demo account first.

## Run
```bash
python3 prepare_data.py <2010-2014 repo> <2015-2019 repo> <XAUUSD_m1.csv> m15.pkl
python3 search.py m15.pkl dev_results.csv
python3 select_dev.py dev_results.csv candidates_round1.csv
python3 evaluate.py m15.pkl candidates_round1.csv 2017-01-01 2020-01-01 validation_round1.csv
python3 evaluate.py m15.pkl selected_round1.csv 2025-03-01 2026-06-01 holdout_round1_normal.csv
python3 check_selected.py m15.pkl
python3 -m unittest -v test_engine
```
