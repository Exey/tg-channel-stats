#!/bin/bash
# Build a standalone binary for the current OS (macOS or Linux) via PyInstaller.
# PyInstaller does not cross-compile — run this on each target OS separately.
set -e
cd "$(dirname "$0")"
if [ ! -d ".venv" ]; then
    python3 -m venv .venv
fi
source .venv/bin/activate
pip install -q -r requirements.txt -r requirements-build.txt
python3 build.py
