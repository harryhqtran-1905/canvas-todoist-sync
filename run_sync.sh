#!/bin/zsh
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Activate conda if available (override with CONDA_SH / CONDA_ENV env vars).
CONDA_SH="${CONDA_SH:-/opt/homebrew/Caskroom/miniconda/base/etc/profile.d/conda.sh}"
CONDA_ENV="${CONDA_ENV:-dsci}"
if [[ -f "$CONDA_SH" ]]; then
  source "$CONDA_SH"
  conda activate "$CONDA_ENV"
fi

cd "$SCRIPT_DIR"
python sync.py >> sync_log.txt 2>&1
