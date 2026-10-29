#!/bin/zsh
set -e
project_root="$(cd "$(dirname "$0")" && pwd)"
python3 "$project_root/scripts/update_guard.py"
