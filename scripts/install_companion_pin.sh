#!/usr/bin/env bash
set -euo pipefail

# PyPI's virtuoso-bridge-lite is not the real companion package; install this Git pin.
python -m pip install "git+https://github.com/Arcadia-1/virtuoso-bridge-lite@fb5af05fe206794baa7afb90a1db70c684a9e24f"
