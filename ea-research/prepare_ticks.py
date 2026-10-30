"""
Convert one year of Dukascopy XAUUSD ticks (FX-Data/FX-Data-XAUUSD-DS, branch XAUUSD-<year>)
into compact numpy files:

  <out>/<year>_m1.npz     1-minute BID bars for the whole day (London measurement)
  <out>/<year>_ticks.npz  every bid/ask tick between 12:00 and 22:00 UTC (covers NY 08:00-17:00
                          in both EST and EDT), used for tick-exact trade simulation

Raw tick prices are stored /100 (13.48641 = $1348.641). Timestamps are UTC.

Usage: python3 prepare_ticks.py YEAR WORKDIR OUTDIR
"""
import glob
import os
import shutil
import subprocess
import sys
import numpy as np
import pandas as pd

REPO = "https://github.com/FX-Data/FX-Data-XAUUSD-DS"


AWK = (r'{d=$1; gsub(/\./,"",d); '
       r'printf "%s,%d,%s,%s\n", d, ($2*3600+$3*60)*1000 + $4*1000 + 0.5, $5, $6}')


def read_month(files):
    """Ticks of one month as (t_ns, bid, ask); awk turns the timestamps into numbers so pandas
    only parses numeric columns (much faster and lighter than datetime strings)."""
    cat = subprocess.Popen(["cat", *files], stdout=subprocess.PIPE)
    awk = subprocess.Popen(["awk", "-F", "[ ,:]", AWK], stdin=cat.stdout, stdout=subprocess.PIPE)
    cat.stdout.close()
    df = pd.read_csv(awk.stdout, header=None, names=["d", "ms", "a", "b"],
                     dtype={"d": np.int64, "ms": np.int64, "a": np.float64, "b": np.float64})
    awk.wait()
    day_ns = {d: pd.Timestamp(str(d)).value for d in df["d"].unique()}
    t = df["d"].map(day_ns).to_numpy(np.int64) + df["ms"].to_numpy(np.int64) * 1_000_000
    a, b = df["a"].to_numpy(), df["b"].to_numpy()
    order = np.argsort(t, kind="stable")
    return t[order], (np.minimum(a, b) * 100)[order], (np.maximum(a, b) * 100)[order]


def main(year, work, out):
    clone = os.path.join(work, f"x{year}")
    if os.path.isdir(clone):
        shutil.rmtree(clone)  # may be a partial clone from an interrupted run
    subprocess.run(["git", "clone", "-q", "--depth", "1", "--single-branch",
                    "-b", f"XAUUSD-{year}", REPO, clone], check=True)
    base = os.path.join(clone, "XAUUSD", str(year))
    m1_parts, tk_parts, n_ticks, n_files, spreads = [], [], 0, 0, []
    for month in sorted(os.listdir(base)):
        files = sorted(glob.glob(os.path.join(base, month, "*_ticks.csv")))
        if not files:
            continue
        t, bid, ask = read_month(files)
        n_ticks += len(t)
        n_files += len(files)
        spreads.append(np.median(ask - bid))
        s = pd.Series(bid, index=pd.to_datetime(t, utc=True))
        m1_parts.append(s.resample("1min").ohlc().dropna())
        hour = (t // 3_600_000_000_000) % 24
        keep = (hour >= 12) & (hour < 22)
        tk_parts.append((t[keep], bid[keep].astype(np.float32), ask[keep].astype(np.float32)))

    m1 = pd.concat(m1_parts).sort_index()
    m1 = m1[~m1.index.duplicated()]
    np.savez(os.path.join(out, f"{year}_m1.npz"),
             t=m1.index.asi8, o=m1["open"].to_numpy(), h=m1["high"].to_numpy(),
             l=m1["low"].to_numpy(), c=m1["close"].to_numpy())
    t = np.concatenate([p[0] for p in tk_parts])
    order = np.argsort(t, kind="stable")
    np.savez(os.path.join(out, f"{year}_ticks.npz"), t=t[order],
             bid=np.concatenate([p[1] for p in tk_parts])[order],
             ask=np.concatenate([p[2] for p in tk_parts])[order])

    print(f"{year}: {n_files} hour files, {n_ticks:,} ticks, {len(t):,} NY-hours ticks, "
          f"{len(m1):,} M1 bars, {m1.index[0]} -> {m1.index[-1]}, "
          f"median spread ${np.median(spreads):.2f}", flush=True)
    shutil.rmtree(clone)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], sys.argv[3])
