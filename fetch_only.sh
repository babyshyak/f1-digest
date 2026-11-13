#!/bin/sh
# Collects new articles only (no summaries, no email). Run every 3 hours so nothing rolls off the feeds.
cd "$(dirname "$0")" || exit 1
mkdir -p data
exec flock -n data/.lock sh -c '. .venv/bin/activate && python -m f1digest fetch >> data/fetch.log 2>&1'
