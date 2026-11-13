#!/bin/sh
# Installs the systemd timers and removes the old cron lines.   Usage: sudo ./deploy/install.sh
# (Set DESTDIR=/some/dir to only render the unit files there, for inspection.)
set -e
DIR="$(cd "$(dirname "$0")/.." && pwd)"
RUN_USER="${SUDO_USER:-$(id -un)}"
DEST="${DESTDIR:-/etc/systemd/system}"
mkdir -p "$DEST"
for f in f1-digest.service f1-digest.timer f1-fetch.service f1-fetch.timer; do
  sed -e "s|@USER@|$RUN_USER|g" -e "s|@DIR@|$DIR|g" "$DIR/deploy/$f" > "$DEST/$f"
done
chmod +x "$DIR/run_daily.sh" "$DIR/fetch_only.sh"
if [ -n "$DESTDIR" ]; then echo "rendered units into $DEST (nothing enabled)"; exit 0; fi
# remove old cron lines for this project (if any)
if crontab -u "$RUN_USER" -l >/dev/null 2>&1; then
  crontab -u "$RUN_USER" -l | grep -v -e "f1-digest/run_daily.sh" -e "f1-digest/fetch_only.sh" | crontab -u "$RUN_USER" - || true
fi
systemctl daemon-reload
systemctl enable --now f1-digest.timer f1-fetch.timer
echo; systemctl list-timers 'f1-*' --no-pager
echo; echo "Done. Test now with:  sudo systemctl start f1-digest.service   then   journalctl -u f1-digest -n 30"
