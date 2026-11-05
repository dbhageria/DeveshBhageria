"""Stage 1: run every family x parameter grid and report DEVELOPMENT-period (2010-2016) stats only.

Usage: python3 -I search.py /path/to/m15.pkl out.csv
"""
import sys
import multiprocessing as mp
import pandas as pd
import engine as E
import strategies as S

DEV = ("2010-01-01", "2017-01-01")
_D = None


def _init(path):
    global _D
    _D = E.load_segment(path, DEV[0])


def run_family(family):
    fn, sp, ep = S.FAMILIES[family]
    rows = []
    sig_cache = {}
    for params, combo in S.grid(family):
        skey = tuple(params[k] for k in sp)
        if skey not in sig_cache:
            sig_cache.clear()
            sig_cache[skey] = fn(_D, **{k: params[k] for k in sp})
        sig = sig_cache[skey]
        trades = E.run(_D, sig, **{k: params[k] for k in ep})
        m = E.metrics(_D, trades, *DEV)
        rows.append(dict(family=family, combo="-".join(map(str, combo)), **params, **m))
    return rows


if __name__ == "__main__":
    path, out = sys.argv[1], sys.argv[2]
    with mp.Pool(4, initializer=_init, initargs=(path,)) as pool:
        results = pool.map(run_family, list(S.FAMILIES))
    df = pd.DataFrame([r for rows in results for r in rows])
    df.to_csv(out, index=False)
    print(len(df), "configs written to", out)
