"""Build one M15 gold dataset from the 2010-2019 daily CSV archives and the 2025-2026 M1 file.

Usage: python3 -I prepare_data.py <2010-2014 repo> <2015-2019 repo> <XAUUSD_m1.csv> <out.pkl>
"""
import sys, glob
import pandas as pd

def load_archive(root):
    files = sorted(glob.glob(f"{root}/XAUUSD/1m/*/*/*.csv"))
    parts = [pd.read_csv(f, usecols=["datetime_utc", "open", "high", "low", "close"]) for f in files]
    df = pd.concat(parts, ignore_index=True)
    df["datetime"] = pd.to_datetime(df.pop("datetime_utc"))
    return df.set_index("datetime")

def to_m15(m1):
    m1 = m1[~m1.index.duplicated()].sort_index()
    return m1.resample("15min", label="left", closed="left").agg(
        {"open": "first", "high": "max", "low": "min", "close": "last"}).dropna()

if __name__ == "__main__":
    a, b, recent, out = sys.argv[1:5]
    old = pd.concat([load_archive(a), load_archive(b)])
    new = pd.read_csv(recent, parse_dates=["datetime"]).set_index("datetime")[["open", "high", "low", "close"]]
    m15 = pd.concat([to_m15(old), to_m15(new)])
    # drop bad prints: bars whose range is >5% of price
    bad = (m15["high"] - m15["low"]) > 0.05 * m15["close"]
    print("bad bars dropped:", int(bad.sum()))
    m15 = m15[~bad]
    m15.to_pickle(out)
    print(len(m15), "M15 bars;", m15.index[0], "->", m15.index[-1])
    print(m15["close"].resample("YE").last().round(0).to_string())
