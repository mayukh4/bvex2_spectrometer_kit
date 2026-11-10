# BVEX2 spectrometer test kit

This is the bench kit we use to run the BVEX2 RFSoC 4x2 spectrometers outside the flight software. One script programs the board, shows a live spectrum and power meter, and saves every spectrum to disk. All four spectrometer designs and their bitstreams are in here, so nothing else needs to be found.

## Modes

| Mode | Channels | Channel width | Sky band (GHz) | Notes |
|---|---|---|---|---|
| `960khz` (`coarse`) | 2048 | 960 kHz | 20.966 to 22.931 | full band, quick look |
| `120khz` (`fine`) | 16384 | 120 kHz | 20.966 to 22.932 | full band, the design flown in 2025 |
| `60khz` | 16384 | 60 kHz | 21.743 to 22.727 | centred on the 22.235 GHz water maser |
| `7p5khz` | 32768 | 7.5 kHz | 22.112 to 22.358 | 245.76 MHz zoom around the maser |

The frequency axis is sky frequency through the BVEX2 receiver. Each mode has its `.fpg` and `.dtbo` in `bitstreams/<mode>/`. The board needs both. `bitstreams/MD5SUMS` lets you check them.

## Setup

You need a Linux machine on the same network as the RFSoC, with a display, and a Python 3.8 venv with [casperfpga](https://github.com/casper-astro/casperfpga), numpy, matplotlib and tkinter. On taurus that venv already exists at `~/sandbox/casper/cfpga_venv`.

The board must have the LMK and LMX clock files `rfsoc4x2_PL_122M88_REF_245M76.txt` and `rfsoc4x2_LMX_REF_245M76_OUT_491M52.txt` (245.76 MHz reference, 491.52 MHz out). The script programs both clocks each time it starts.

## Running

```bash
./run_spec.sh            # a small window asks for the mode
./run_spec.sh 7p5khz     # or give the mode directly
```

It talks to the board at `172.20.4.172` and uses the taurus venv. Change either one from the shell:

```bash
RFSOC_IP=10.0.0.5 CFPGA_VENV=~/my_venv ./run_spec.sh 960khz
```

Other options: `-a 0-3` picks the ADC input, `-l N` sets the `acc_len` register, `--no-save` starts without saving, `--file-minutes N` changes the file length (default 10), and `--data-dir` saves somewhere else.

The window has the spectrum on top and the power meter below it, with a slider for the power meter integration time (1 to 5 s).

## Saving

Saving starts as soon as the plots open. While it is on, a red `REC` light blinks with the elapsed time, and the status line shows the folder, the current file, its size, and how many spectra have been written. **Stop saving** stops it. **Start saving** begins a new run in a new folder.

Each run goes in `bvex2_test_data/<UTC start>_UTC_<mode>/`, for example `2026-11-10_21-39-20_UTC_960kHz/`. Every 10 minutes it starts a new pair of files:

- `<time>_<mode>_spectrum_data.txt`: one line per spectrum. The unix timestamp, then every channel in dB, low to high frequency. This is the same format as the old `rfsoc4x2_tut_spec_17.py`.
- `<time>_<mode>_integrated_power_data.txt`: one line per power meter point. Unix time, integration time in s, number of spectra, linear power, dB.

Each run folder also has `frequencies_GHz.txt` (the frequency of every column) and `run_info.txt` (mode, bitstream, band, format).

A 10 minute spectrum file is about 40 MB at 960 kHz and a few hundred MB in the other modes, so keep an eye on disk space for long runs. Saved runs are never committed.

## Checking the data

```bash
cd bvex2_test_data
python check_data.py              # newest run
python check_data.py RUN_FOLDER   # a run folder or one *_spectrum_data.txt file
python check_data.py --pick       # pick the file in a window
python check_data.py --png out.png
```

It plots one random spectrum and the full power time series, checks that every line has the right number of channels and that there are no gaps in the timestamps, and prints `looks OK` or `PROBLEMS FOUND`.

## Things to know

- With a weak input, like the bare bench input with no LNAs, most channels in the 120, 60 and 7.5 kHz modes read zero. That is the noise floor sitting below one count, not a broken design. A test tone shows up fine, and the receiver noise fills the floor in. A -30 dBm CW tone at 3.235 GHz lands at 22.23496 GHz in the 7.5 kHz mode.
- The first two spectra after a counter reset are junk and are thrown away.
- In the 7.5 kHz mode the first spectrum takes about 8 s to show up, then one comes every 0.55 s.
- Still to check: the 120 and 60 kHz modes with a tone, and the direction of the full band frequency axis with a known tone.

## Files

```
run_spec.sh                    launcher
bvex2_spec.py                  programming, plots and saving
bitstreams/<mode>/             .fpg and .dtbo for each mode, plus MD5SUMS
bvex2_test_data/check_data.py  quick look at saved runs
requirements.txt               python packages (casperfpga comes from CASPER)
```
