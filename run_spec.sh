#!/bin/bash
# BVEX2 spectrometer test kit: program the RFSoC and open the live spectrum and power meter.
#
# Usage: ./run_spec.sh [mode] [options]
#   mode     960khz (alias coarse), 120khz (alias fine), 60khz or 7p5khz.
#            Leave it out to choose in a window.
#   options  -a <0-3>          ADC input
#            -l <n>            acc_len register value
#            --no-save         start without saving
#            --file-minutes N  minutes per data file (default 10)

# Override either one from the shell, e.g. RFSOC_IP=10.0.0.5 ./run_spec.sh
RFSOC_IP="${RFSOC_IP:-172.20.4.172}"
CFPGA_VENV="${CFPGA_VENV:-$HOME/sandbox/casper/cfpga_venv}"

MODE_ARGS=()
case "$1" in
    ""|-*) ;;
    *) MODE_ARGS=(--mode "$1"); shift ;;
esac

cd "$(dirname "$(readlink -f "$0")")" || exit 1
if [ -f "$CFPGA_VENV/bin/activate" ]; then
    source "$CFPGA_VENV/bin/activate"
else
    echo "No casperfpga venv at $CFPGA_VENV; using the python on PATH."
fi

if [ -z "$DISPLAY" ]; then
    echo "No display found, so the plots cannot open."
    echo "Reconnect with 'ssh -X', or run this from the machine's own desktop."
    exit 1
fi

python bvex2_spec.py "$RFSOC_IP" "${MODE_ARGS[@]}" "$@"
