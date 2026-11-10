#!/usr/bin/env python3
"""BVEX2 spectrometer test kit: live spectrum and power meter, with continuous saving.

Modes (one bitstream each, in bitstreams/<mode>/):
  960khz  full band, 2048 channels
  120khz  full band, 16384 channels (the design flown in 2025)
  60khz   983 MHz around the 22.235 GHz water maser, 16384 channels
  7p5khz  245.76 MHz zoom around the maser, 32768 channels

Programs the RFSoC, sets up the clocks and RFDC, then opens the plots. Without
--mode, a small window asks which mode to run. Normally started via run_spec.sh.
"""
import argparse
import atexit
import os
import sys
import time
from datetime import datetime, timezone

import casperfpga
import matplotlib.pyplot as plt
import numpy as np
from matplotlib.animation import FuncAnimation
from matplotlib.widgets import Button, RadioButtons, Slider

HERE = os.path.dirname(os.path.abspath(__file__))
SAVE_DIR = os.path.join(HERE, "bvex2_test_data")
BITSTREAMS = os.path.join(HERE, "bitstreams")

# f_low_ghz is the sky frequency of the first channel after fftshift(flip(raw)),
# the order used for display and saved files (low to high frequency).
MODES = {
    "960khz": {
        "label": "960kHz", "title": "960 kHz, full band",
        "fpg": "960khz/rfsoc4x2_tut_spec_cx_2023-04-03_1141.fpg",
        "nfft": 2048, "brams": 8, "acc_len": 2 ** 18,
        "f_low_ghz": 20.96608, "bw_mhz": 1966.08,
    },
    "120khz": {
        "label": "120kHz", "title": "120 kHz, full band (flown 2025)",
        "fpg": "120khz/rfsoc4x2_tut_spec_cx_14pt_fft_2025-05-19_1245.fpg",
        "nfft": 16384, "brams": 8, "acc_len": 2 ** 18,
        "f_low_ghz": 20.96608, "bw_mhz": 1966.08,
    },
    "60khz": {
        "label": "60kHz", "title": "60 kHz, 983 MHz around the maser",
        "fpg": "60khz/rfsoc4x2_tut_spec_cx_60khz_maser_centered_2026-03-02_1837.fpg",
        "nfft": 16384, "brams": 8, "acc_len": 2 ** 18,
        "f_low_ghz": 22.235 - 983.04 / 2000, "bw_mhz": 983.04,
    },
    "7p5khz": {
        "label": "7p5kHz", "title": "7.5 kHz zoom, 245.76 MHz around the maser",
        "fpg": "7p5khz/rfsoc4x2_tut_spec_cx_7p5khz_zoom_2026-07-03_2131.fpg",
        # The register counts 256-clock units; 128 units = one 32768-point spectrum.
        "nfft": 32768, "brams": 2, "acc_len": 4096 * 128,
        "f_low_ghz": 22.235 - 245.76 / 2000, "bw_mhz": 245.76,
        "note": "The first dump arrives about 8 s after start (sync period).",
    },
}
ALIASES = {"coarse": "960khz", "fine": "120khz", "7.5khz": "7p5khz"}

LMK_FILE = "rfsoc4x2_PL_122M88_REF_245M76.txt"
LMX_FILE = "rfsoc4x2_LMX_REF_245M76_OUT_491M52.txt"
POLL_MS = 100
POWER_WINDOW_S = 300
FILE_MINUTES = 10
SKIP_DUMPS = 2


def frequencies(cfg):
    n = cfg["nfft"]
    return cfg["f_low_ghz"] + np.arange(n) * cfg["bw_mhz"] / n / 1000


def to_sky_order(raw):
    """FPGA channel order to low-to-high frequency, as in the flight readout scripts."""
    return np.fft.fftshift(np.flip(raw))


def to_db(values):
    return 10 * np.log10(np.maximum(values, 1.0))


def choose_mode():
    """Small start-up window to pick a mode. Returns the mode key, or None if closed."""
    keys = list(MODES)
    labels = []
    for k in keys:
        f = frequencies(MODES[k])
        labels.append(f"{MODES[k]['title']}   ({f[0]:.3f}-{f[-1]:.3f} GHz)")
    fig = plt.figure(figsize=(7.5, 3.6))
    fig.canvas.manager.set_window_title("BVEX2 spectrometer: choose a mode")
    fig.text(0.05, 0.9, "Choose the spectrometer mode", fontsize=13, fontweight="bold")
    radio = RadioButtons(fig.add_axes([0.05, 0.3, 0.9, 0.55], frameon=False), labels, active=0)
    start = Button(fig.add_axes([0.05, 0.08, 0.25, 0.14]), "Start")
    start.color = start.hovercolor = "#bfe3bf"
    chosen = {}

    def on_start(_event):
        chosen["key"] = keys[labels.index(radio.value_selected)]
        plt.close(fig)

    start.on_clicked(on_start)
    fig._widgets = (radio, start)
    plt.show()
    return chosen.get("key")


def program_board(host, cfg, acc_len, adc):
    """Load the bitstream, set up clocks and RFDC, and configure the accumulator."""
    fpg = os.path.join(BITSTREAMS, cfg["fpg"])
    fpga = casperfpga.CasperFpga(host)
    time.sleep(0.2)
    print(f"Programming {fpg}")
    fpga.upload_to_ram_and_program(fpg)
    rfdc = fpga.adcs.rfdc
    rfdc.init()
    rfdc.progpll("lmk", LMK_FILE)
    rfdc.progpll("lmk", LMK_FILE)
    rfdc.progpll("lmx", LMX_FILE)
    time.sleep(3)
    rfdc.init()
    time.sleep(2)
    rfdc.status()
    fpga.write_int("acc_len", acc_len)
    fpga.write_int("adc_chan_sel", adc)
    fpga.write_int("cnt_rst", 1)
    fpga.write_int("cnt_rst", 0)
    time.sleep(1)
    return fpga


def read_raw(fpga, cfg):
    """One accumulated spectrum in FPGA channel order, linear power in arbitrary units."""
    brams, chunk = cfg["brams"], cfg["nfft"] // cfg["brams"]
    lanes = [np.frombuffer(fpga.read(f"q{i + 1}", chunk * 8, 0), dtype=">u8") for i in range(brams)]
    return np.stack(lanes, axis=1).ravel().astype(np.float64)


class Recorder:
    """Writes every spectrum and every power point, starting new files every few minutes.

    Spectrum files: one line per spectrum, the unix timestamp then every channel in dB,
    low to high frequency, the format of rfsoc4x2_tut_spec_17.py.
    """

    def __init__(self, base_dir, mode, cfg, adc, acc_len, file_minutes):
        self.base_dir = base_dir
        self.mode = mode
        self.cfg = cfg
        self.adc = adc
        self.acc_len = acc_len
        self.file_seconds = file_minutes * 60
        self.active = False
        self.run_dir = None
        self.spectrum_file = None
        self.power_file = None
        self.file_start = 0.0
        self.started_at = 0.0
        self.spectra_in_file = 0
        self.spectra_total = 0
        self.fmt = ["%.6f"] + ["%.4f"] * cfg["nfft"]

    def start(self):
        cfg = self.cfg
        stamp = datetime.now(timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
        self.run_dir = os.path.join(self.base_dir, f"{stamp}_UTC_{cfg['label']}")
        os.makedirs(self.run_dir, exist_ok=True)
        freq = frequencies(cfg)
        np.savetxt(os.path.join(self.run_dir, "frequencies_GHz.txt"), freq, fmt="%.7f")
        with open(os.path.join(self.run_dir, "run_info.txt"), "w") as f:
            f.write(
                f"Mode: {self.mode} ({cfg['title']})\n"
                f"Bitstream: {os.path.basename(cfg['fpg'])}\n"
                f"Channels: {cfg['nfft']}, {freq[0]:.6f} to {freq[-1]:.6f} GHz, "
                f"{cfg['bw_mhz'] / cfg['nfft'] * 1000:.3f} kHz per channel\n"
                f"ADC input: {self.adc}, acc_len register: {self.acc_len}\n"
                f"Started: {stamp} UTC. A new pair of files starts every "
                f"{self.file_seconds / 60:g} minutes.\n"
                f"*_spectrum_data.txt: one line per spectrum: unix_time, then {cfg['nfft']} "
                "values of 10*log10(power) in arbitrary units, in fftshift(flip(raw)) order, "
                "low to high frequency. frequencies_GHz.txt gives each column's frequency. "
                "A channel with zero power is written as -inf.\n"
                "*_integrated_power_data.txt: one line per power-meter point: "
                "unix_time integration_s dumps power_linear_arb power_dB_arb\n"
            )
        self.spectra_total = 0
        self.started_at = time.time()
        self.active = True
        self._open_files(self.started_at)

    def _open_files(self, now):
        self._close_files()
        stamp = datetime.fromtimestamp(now, timezone.utc).strftime("%Y-%m-%d_%H-%M-%S")
        base = os.path.join(self.run_dir, f"{stamp}_{self.cfg['label']}")
        self.spectrum_file = open(f"{base}_spectrum_data.txt", "w")
        self.power_file = open(f"{base}_integrated_power_data.txt", "w")
        self.power_file.write("# unix_time integration_s dumps power_linear_arb power_dB_arb\n")
        self.file_start = now
        self.spectra_in_file = 0

    def _close_files(self):
        for f in (self.spectrum_file, self.power_file):
            if f is not None and not f.closed:
                f.close()
        self.spectrum_file = self.power_file = None

    def _rotate_if_due(self, now):
        if now - self.file_start >= self.file_seconds:
            self._open_files(now)

    def write_spectrum(self, timestamp, sky):
        if not self.active:
            return
        self._rotate_if_due(timestamp)
        with np.errstate(divide="ignore"):
            values = 10 * np.log10(sky)
        np.savetxt(self.spectrum_file, np.concatenate(([timestamp], values))[None, :], fmt=self.fmt)
        self.spectrum_file.flush()
        self.spectra_in_file += 1
        self.spectra_total += 1

    def write_power(self, row):
        if not self.active:
            return
        self._rotate_if_due(row[0])
        unix_time, integration, dumps, power = row
        self.power_file.write(
            f"{unix_time:.3f} {integration} {dumps} {power:.3f} {10 * np.log10(max(power, 1.0)):.4f}\n")
        self.power_file.flush()

    def spectrum_bytes(self):
        return self.spectrum_file.tell() if self.spectrum_file else 0

    def stop(self):
        self._close_files()
        self.active = False


class SpectrometerApp:
    def __init__(self, fpga, mode, cfg, recorder, save_at_start=True):
        self.fpga = fpga
        self.mode = mode
        self.cfg = cfg
        self.recorder = recorder
        self.nfft = cfg["nfft"]
        self.freq_ghz = frequencies(cfg)

        self.integration_s = 1
        self.start_time = time.time()
        self.last_acc = None
        self.dumps_seen = 0
        self.window_start = self.start_time
        self.window_sum = np.zeros(self.nfft)
        self.window_dumps = 0
        self.power_rows = []
        self.blink = False

        self.fig = plt.figure(figsize=(14, 6.8))
        self.fig.canvas.manager.set_window_title(f"BVEX2 spectrometer: {cfg['title']}")
        self.fig.suptitle(f"Mode: {cfg['title']}", fontsize=12, fontweight="bold")
        grid = self.fig.add_gridspec(1, 2, left=0.06, right=0.98, top=0.88, bottom=0.27, wspace=0.2)
        self.ax_spec = self.fig.add_subplot(grid[0, 0])
        self.ax_power = self.fig.add_subplot(grid[0, 1])

        self.spec_line, = self.ax_spec.plot(self.freq_ghz, np.zeros(self.nfft), lw=0.8)
        self.ax_spec.set_xlabel("Frequency (GHz)")
        self.ax_spec.set_ylabel("Power (dB arb.)")
        self.ax_spec.set_xlim(self.freq_ghz[0], self.freq_ghz[-1])
        self.ax_spec.set_title("Waiting for the first spectrum..." + (
            f"\n{cfg['note']}" if cfg.get("note") else ""), fontsize=10)

        self.power_line, = self.ax_power.plot([], [], "-o", ms=3, lw=1)
        self.ax_power.set_xlabel("Time since start (s)")
        self.ax_power.set_ylabel("Integrated power (dB arb.)")
        self.ax_power.ticklabel_format(axis="y", useOffset=False)
        self.ax_power.set_title("Power meter, 1 s integration", fontsize=10)

        spec_pos = self.ax_spec.get_position()
        power_pos = self.ax_power.get_position()
        self.slider = Slider(self.fig.add_axes([power_pos.x0 + 0.07, 0.1, power_pos.width - 0.12, 0.04]),
                             "Integration (s)", 1, 5, valinit=1, valstep=1, valfmt="%d s")
        self.slider.on_changed(self.on_integration_changed)

        self.save_button = Button(self.fig.add_axes([spec_pos.x0, 0.09, 0.16, 0.07]), "")
        self.save_button.label.set_fontsize(11)
        self.save_button.on_clicked(self.on_save_clicked)
        self.rec_light = self.fig.text(spec_pos.x0 + 0.175, 0.125, "", fontsize=13,
                                       fontweight="bold", va="center")
        self.status = self.fig.text(spec_pos.x0, 0.035, "", fontsize=9, va="center", family="monospace")
        self.fig.canvas.mpl_connect("close_event", lambda _event: self.recorder.stop())
        atexit.register(self.recorder.stop)
        if save_at_start:
            self.recorder.start()
        self.refresh_save_controls()
        self.animation = FuncAnimation(self.fig, self.update, interval=POLL_MS,
                                       blit=False, cache_frame_data=False)

    def read_new_dump(self):
        """Return (acc, raw) for a new dump, or None if there is no clean new one."""
        acc = self.fpga.read_uint("acc_cnt")
        if acc == self.last_acc:
            return None
        raw = read_raw(self.fpga, self.cfg)
        if self.fpga.read_uint("acc_cnt") != acc:
            return None  # the accumulator dumped during the read
        self.last_acc = acc
        return acc, raw

    def update(self, _frame):
        dump = self.read_new_dump()
        if dump is None:
            return
        acc, raw = dump
        self.dumps_seen += 1
        if self.dumps_seen <= SKIP_DUMPS:
            return  # the first dumps after a counter reset can hold partial or stale data
        now = time.time()
        sky = to_sky_order(raw)
        self.recorder.write_spectrum(now, sky)
        self.refresh_save_controls()

        spectrum_db = to_db(sky)
        self.spec_line.set_ydata(spectrum_db)
        self.ax_spec.set_ylim(spectrum_db.min() - 5, spectrum_db.max() + 5)
        self.ax_spec.set_title(f"{self.cfg['nfft']} channels, accumulation {acc}", fontsize=10)

        self.window_sum += sky
        self.window_dumps += 1
        if now - self.window_start >= self.integration_s:
            self.finish_power_point(now)

    def finish_power_point(self, now):
        mean_spectrum = self.window_sum / self.window_dumps
        row = (now, self.integration_s, self.window_dumps, float(mean_spectrum.sum()))
        self.power_rows.append(row)
        self.recorder.write_power(row)
        self.reset_window(now)
        rows = np.array(self.power_rows)
        keep = rows[:, 0] >= now - POWER_WINDOW_S
        self.power_line.set_data(rows[keep, 0] - self.start_time, to_db(rows[keep, 3]))
        self.ax_power.relim()
        self.ax_power.autoscale_view()

    def reset_window(self, now):
        self.window_start = now
        self.window_sum = np.zeros(self.nfft)
        self.window_dumps = 0

    def on_integration_changed(self, value):
        self.integration_s = int(value)
        self.ax_power.set_title(f"Power meter, {self.integration_s} s integration", fontsize=10)
        self.reset_window(time.time())

    def on_save_clicked(self, _event):
        if self.recorder.active:
            self.recorder.stop()
        else:
            self.recorder.start()
        self.refresh_save_controls()

    def refresh_save_controls(self):
        rec = self.recorder
        if rec.active:
            self.blink = not self.blink
            elapsed = int(time.time() - rec.started_at)
            self.save_button.label.set_text("Stop saving")
            self.save_button.color = self.save_button.hovercolor = "#f2b8b5"
            self.rec_light.set_text(
                f"● REC  {elapsed // 3600:02d}:{elapsed // 60 % 60:02d}:{elapsed % 60:02d}")
            self.rec_light.set_color("#d62728" if self.blink else "#8c1d1d")
            self.status.set_text(
                f"Saving to {short_path(rec.run_dir)}/\n"
                f"Current file: {os.path.basename(rec.spectrum_file.name)}  "
                f"{rec.spectrum_bytes() / 1e6:.1f} MB, {rec.spectra_in_file} spectra  |  "
                f"{rec.spectra_total} spectra this run  |  new file every {rec.file_seconds / 60:g} min")
        else:
            self.save_button.label.set_text("Start saving")
            self.save_button.color = self.save_button.hovercolor = "#bfe3bf"
            self.rec_light.set_text("○ NOT SAVING")
            self.rec_light.set_color("0.45")
            self.status.set_text("Data is not being saved." if rec.run_dir is None
                                 else f"Stopped. Last run saved in {short_path(rec.run_dir)}/")
        self.save_button.ax.set_facecolor(self.save_button.color)
        self.fig.canvas.draw_idle()


def short_path(path):
    rel = os.path.relpath(path, HERE)
    return path if rel.startswith("..") else rel


def main():
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("host", help="RFSoC hostname or IP address")
    parser.add_argument("--mode", help="960khz, 120khz, 60khz or 7p5khz (asks if left out)")
    parser.add_argument("-a", "--adc", type=int, choices=range(4), default=0, help="ADC input")
    parser.add_argument("-l", "--acc-len", type=int, help="acc_len register value (default per mode)")
    parser.add_argument("--no-save", action="store_true", help="start without saving data")
    parser.add_argument("--file-minutes", type=float, default=FILE_MINUTES,
                        help="minutes per data file before a new one starts (default 10)")
    parser.add_argument("--data-dir", default=SAVE_DIR, help="folder for saved runs")
    args = parser.parse_args()

    mode = args.mode.lower() if args.mode else choose_mode()
    if mode is None:
        sys.exit("No mode chosen.")
    mode = ALIASES.get(mode, mode)
    if mode not in MODES:
        sys.exit(f"Unknown mode '{args.mode}'. Use one of: {', '.join(MODES)}")
    cfg = MODES[mode]
    acc_len = args.acc_len or cfg["acc_len"]
    print(f"Mode: {cfg['title']}")
    fpga = program_board(args.host, cfg, acc_len, args.adc)
    recorder = Recorder(args.data_dir, mode, cfg, args.adc, acc_len, args.file_minutes)
    app = SpectrometerApp(fpga, mode, cfg, recorder, save_at_start=not args.no_save)
    plt.show()
    return app


if __name__ == "__main__":
    main()
