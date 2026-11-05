import json, sys, pandas as pd
r=[json.loads(x) for x in open(sys.argv[1])]
d=pd.DataFrame(r)
pd.set_option('display.width',250)
print(d[['profile','mode','accounts_used','pct_alive_1d','pct_alive_7d','pct_alive_30d','median_life_days','pct_reach_10k','pct_reach_100k','best_peak','basket_win_rate','deposited','ending_money','net_result']].to_string())
