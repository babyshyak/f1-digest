#!/bin/sh
# Daily digest (cron). Lock prevents overlapping with fetch_only.sh. Log: data/run.log
cd "$(dirname "$0")" || exit 1
mkdir -p data
exec flock -w 1800 data/.lock sh -c '. .venv/bin/activate && python -m f1digest run >> data/run.log 2>&1'
