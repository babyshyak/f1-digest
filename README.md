# F1 Digest

Daily F1 news: emailed, plus a mobile-friendly page with the last 30 days.
Runs on a Raspberry Pi with a small local model (Ollama). The page is published free on GitHub Pages.

## How it makes sure you miss nothing, and see nothing twice
1. Articles are grouped into **stories** (embedding similarity: 5 outlets on one event = 1 story).
2. **New story** -> shown once, with a full-article summary. The top `max_items` (12) get the full treatment;
   any extra stories go in a compact **"Also in the news"** list. Nothing is dropped.
3. **Same story, near-identical copy** from another outlet -> counted as confirmation ("Reported by 4 outlets"), never re-shown.
4. **Same story, genuinely new coverage within 7 days** -> a one-line **follow-up** in "Also in the news" (not a repeat of the story).
5. **Story you saw more than 7 days ago with new coverage** -> returns as a full **Update**.
6. **Pi off, or a feed down for days?** "New" means *arrived since the last digest*, not *published recently*, so late arrivals still get delivered.
7. **Feeds only hold the latest ~10-30 items**, so a 3-hourly `fetch` job collects articles before they roll off.
8. **Unsent email?** A failed send is retried on the next run (last 3 days).
9. **Summaries are checked**: any name or number the model adds that isn't in the sources makes it fall back to plain extracted text.
10. **Different stories aren't merged on embeddings alone** (they must share 2 words), so one story can't swallow another.
11. **Feeds failing?** The email carries a warning when fewer than half respond.
Everything older than 30 days is deleted. Re-running a day never duplicates anything.

## Setup (Raspberry Pi, ~10 min)
```bash
sudo apt install -y git python3-venv
curl -fsSL https://ollama.com/install.sh | sh
ollama pull all-minilm          # embeddings (46 MB)
ollama pull qwen2.5:3b          # summaries (Pi 5). Pi 4/4GB: qwen2.5:1.5b, then edit config.toml

# 1. Create an EMPTY public GitHub repo (e.g. f1-digest), clone this project into it, push.
#    In repo Settings > Pages: deploy from branch main, folder /docs.
#    Give the Pi push access (SSH key or a fine-grained token).
cd f1-digest
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env && nano .env   # email + Resend (or Brevo) key
nano config.toml                    # set [site] url to your Pages URL

.venv/bin/python -m f1digest check-feeds   # which feeds work from your network
.venv/bin/python -m f1digest test-email    # confirms email works
.venv/bin/python -m f1digest backfill --no-email   # fills the archive with whatever feeds still hold
sudo ./deploy/install.sh        # systemd timers (replaces cron; see 'Scheduling and watchdog' below)
```
Resend note: without a verified domain you can only send to the email you signed up with, from onboarding@resend.dev. That is fine for personal use.

## Commands
`run` (default, daily) · `fetch` (collect only) · `status` · `serve` · `backfill` · `check-feeds` · `rebuild-site` · `test-email` · `run --force` (rebuild today)

## Tuning
- Too many near-duplicates getting through: lower `similarity` in config.toml (0.72 -> 0.68). Different stories merged: raise it.
- `resurface_days`, `max_items`, `fresh_days`, `duplicate_similarity` (lower = fewer follow-ups, higher = more), feeds: all in config.toml.
- Want a wider net? Uncomment the Google News feed at the bottom of config.toml (run `check-feeds` after).
- If Ollama is off, it falls back to word-overlap matching and extractive summaries, so the run never fails.

## Scheduling and watchdog (do both)
**Timers, not cron.** `sudo ./deploy/install.sh` installs two systemd timers: the digest at 07:30 and a collection run every 3 hours.
`Persistent=true` means that if the Pi was off at the scheduled time, the job runs as soon as it boots. It also removes any old cron lines.
```bash
systemctl list-timers 'f1-*'                 # next run times
sudo systemctl start f1-digest.service       # run it now
journalctl -u f1-digest -n 40                # what happened
```

**Watchdog (healthchecks.io, free).** It tells you when the digest *stops* arriving, which is exactly when you can't tell.
1. Sign up at healthchecks.io. Create two checks:
   - **F1 digest**: period 1 day, grace time 4 hours
   - **F1 fetch**: period 3 hours, grace time 1 hour
2. Under Integrations, add your email (and the phone app if you like).
3. Paste each check's ping URL into `.env` as `HEALTHCHECK_URL` and `HEALTHCHECK_FETCH_URL`.
Each run pings when it starts and when it finishes. It sends a **failure** ping if all feeds are down, the email or page publish fails, or the script crashes.
A missing ping (Pi off, power cut, timer broken) makes healthchecks.io alert you after the grace time.

## Extras
- **Backups:** every run snapshots the database to `data/backups/` (keeps 7). Set `[backup] dir` in config.toml to a USB stick for safety.
- **Ranking:** big-news words (penalty, disqualified, signs...) and trusted outlets raise a story's rank. Edit `[ranking]`.
- **Private page instead of GitHub:** set `publish = false`, then `python -m f1digest serve` and open `http://<pi-ip>:8080` on your home network (use Tailscale to reach it away from home). Run it as a service or in `tmux`.
- **Status check:** `python -m f1digest status` shows last digest, pending emails, backups, and whether the model is online.
- **Backfill is fast** (headlines + snippets only). Add `--fulltext` to read full articles for old days too (slow).
- **Broad feeds** (e.g. Google News, marked `broad = true`) are filtered to F1 topics via `require_any`.

## Check after the first week
`grep borderline data/run.log` shows merges that were close calls. If they're wrong, raise `similarity`; if duplicates leak through, lower it.
Edit `exclude` in config.toml to cut noise (TV times, betting, etc.).

## Notes
- The 30-day archive starts from what RSS feeds still hold (usually a few days) and fills up daily after that.
- The repo is public for free Pages. Only `docs/` is published; `.env` and `data/` stay on the Pi. **Back up `data/f1.db`**: if it's lost, history is lost and old stories can reappear as new. A weekly `cp` to a USB stick or a private repo is enough.
- Tests: `python -I tests/test_pipeline.py` and `python -I tests/test_watchdog.py`
