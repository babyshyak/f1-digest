"""Dead-man's switch (healthchecks.io or any compatible service). Silent no-op if no URL is configured.

A run pings /start, then success or /fail. If a ping never arrives (Pi off, cron/timer broken, crash before
Python starts) the service itself notices the silence and alerts you; that is the whole point.
"""
import os

import requests


def ping(env_var: str, kind: str = "", message: str = "") -> None:
    url = os.environ.get(env_var, "").strip().rstrip("/")
    if not url:
        return
    suffix = {"": "", "start": "/start", "fail": "/fail"}[kind]
    try:
        requests.post(url + suffix, data=message[:5000].encode("utf-8"), timeout=10)
    except Exception:  # noqa: BLE001  (the watchdog must never break the digest)
        pass
