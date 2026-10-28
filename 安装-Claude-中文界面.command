#!/bin/zsh
set -e
project_root="$(cd "$(dirname "$0")" && pwd)"
python3 "$project_root/scripts/repair_macos_429.py" --apply
