#!/usr/bin/env python3
"""Quick check that a saved spectrometer run was stored properly.

Plots one random spectrum and the power-meter time series from one saved file
pair, and prints basic checks (line count, columns, timing gaps, bad values).

  python check_data.py                    newest run in this folder
  python check_data.py RUN_FOLDER         a run folder (its first file pair)
  python check_data.py SPECTRUM_FILE      one *_spectrum_data.txt file
  python check_data.py --pick             choose a file in a window
  add --png out.png to save the plot instead of opening a window
"""
import argparse
import glob
import os
import random
import sys

import numpy as np

HERE = os.path.dirname(os.path.abspath(__file__))


def newest_run():
    runs = [d for d in glob.glob(os.path.join(HERE, "*")) if os.path.isdir(d)
            and glob.glob(os.path.join(d, "*_spectrum_data.txt"))]
    if not runs:
        sys.exit(f"No run folders with *_spectrum_data.txt files in {HERE}")
    return max(runs, key=os.path.getmtime)


def pick_file():
    import tkinter
    from tkinter import filedialog
    root = tkinter.Tk()
    root.withdraw()
    path = filedialog.askopenfilename(
        title="Choose a spectrum data file", initialdir=HERE,
        filetypes=[("Spectrum data", "*_spectrum_data.txt"), ("All files", "*")])
    root.destroy()
    if not path:
        sys.exit("Nothing chosen")
    return path


def resolve(target):
    """Return (spectrum file, power file or None, frequency file or None)."""
    if os.path.isdir(target):
        files = sorted(glob.glob(os.path.join(target, "*_spectrum_data.txt")))
        if not files:
            sys.exit(f"No *_spectrum_data.txt files in {target}")
        if len(files) > 1:
            print(f"{len(files)} spectrum files in this folder, checking the first:")
            for f in files:
                print("   ", os.path.basename(f))
        target = files[0]
    folder = os.path.dirname(target)
    power = target.replace("_spectrum_data.txt", "_integrated_power_data.txt")
    freq = os.path.join(folder, "frequencies_GHz.txt")
    return target, (power if os.path.exists(power) else None), (freq if os.path.exists(freq) else None)


def check_spectra(path):
    with open(path) as f:
        lines = [l for l in f if l.strip() and not l.startswith("#")]
    if not lines:
        sys.exit(f"{path} has no spectra")
    field_counts = {}
    times = np.empty(len(lines))
    for i, line in enumerate(lines):
        head, _, rest = line.partition(" ")
        times[i] = float(head)
        n = rest.count(" ") + 1
        field_counts[n] = field_counts.get(n, 0) + 1
    pick = random.randrange(len(lines))
    values = np.array(lines[pick].split()[1:], dtype=float)

    print(f"\nSpectrum file: {path}")
    print(f"  size {os.path.getsize(path) / 1e6:.1f} MB, {len(lines)} spectra")
    print(f"  channels per spectrum: " + ", ".join(f"{n} ({c} lines)" for n, c in field_counts.items()))
    report_times(times)
    finite = values[np.isfinite(values)]
    bad = len(values) - len(finite)
    median = f"{np.median(finite):.1f}" if len(finite) else "n/a"
    print(f"  plotted spectrum: line {pick + 1}, {bad} non-finite values (zero power), median {median}")
    problems = len(field_counts) > 1 or bad > len(values) // 2
    if bad > len(values) // 2:
        print("  most values are -inf: the spectrometer was reading zeros when this was saved")
    return times[pick], values, problems


def report_times(times):
    if len(times) < 2:
        print("  only one timestamp")
        return
    dt = np.diff(times)
    span = times[-1] - times[0]
    print(f"  span {span / 60:.2f} min, median spacing {np.median(dt):.3f} s, "
          f"max gap {dt.max():.2f} s, out-of-order steps {int(np.sum(dt <= 0))}")


def load_power(path):
    rows = np.loadtxt(path, comments="#", ndmin=2)
    t = rows[:, 0]
    # New files: unix_time integration_s dumps linear dB. Older files: unix_time linear.
    power_db = rows[:, 4] if rows.shape[1] >= 5 else 10 * np.log10(np.maximum(rows[:, -1], 1.0))
    print(f"\nPower file: {path}")
    print(f"  {len(t)} points")
    report_times(t)
    return t, power_db


def main():
    p = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("target", nargs="?", help="run folder or *_spectrum_data.txt file")
    p.add_argument("--pick", action="store_true", help="choose the file in a window")
    p.add_argument("--png", help="save the plot to this file instead of showing it")
    a = p.parse_args()

    target = pick_file() if a.pick else (a.target or newest_run())
    spec_path, power_path, freq_path = resolve(os.path.abspath(target))
    t_spec, values, problems = check_spectra(spec_path)
    freq = np.loadtxt(freq_path) if freq_path else None
    if freq is not None and len(freq) != len(values):
        print(f"  frequencies_GHz.txt has {len(freq)} values, spectrum has {len(values)}")
        freq = None

    import matplotlib
    if a.png:
        matplotlib.use("Agg")
    import matplotlib.pyplot as plt
    from datetime import datetime, timezone

    fig, (ax1, ax2) = plt.subplots(1, 2, figsize=(14, 5.5))
    x = freq if freq is not None else np.arange(len(values))
    ax1.plot(x, values, lw=0.8)
    ax1.set_xlabel("Frequency (GHz)" if freq is not None else "Channel")
    ax1.set_ylabel("Power (dB arb.)")
    stamp = datetime.fromtimestamp(t_spec, timezone.utc).strftime("%Y-%m-%d %H:%M:%S UTC")
    ax1.set_title(f"Random spectrum, {stamp}")

    if power_path and os.path.getsize(power_path) > 0 and len(np.loadtxt(power_path, comments="#", ndmin=2)):
        t, power_db = load_power(power_path)
        ax2.plot((t - t[0]) / 60, power_db, "-o", ms=2, lw=0.8)
        ax2.set_xlabel("Minutes from start of file")
        ax2.set_ylabel("Integrated power (dB arb.)")
        ax2.ticklabel_format(axis="y", useOffset=False)
        ax2.set_title(f"Power meter, {len(t)} points")
    else:
        ax2.text(0.5, 0.5, "No matching power file", ha="center", va="center")
        ax2.set_axis_off()
    fig.suptitle(os.path.relpath(spec_path, HERE) if spec_path.startswith(HERE) else spec_path,
                 fontsize=10)
    fig.tight_layout()
    print("\nResult:", "PROBLEMS FOUND, see above" if problems else "looks OK")
    if a.png:
        fig.savefig(a.png, dpi=90)
        print(f"Plot saved to {a.png}")
    else:
        plt.show()


if __name__ == "__main__":
    main()
