"""
API credit balance for the HUD, in dollars.

Anthropic exposes no endpoint for the prepaid balance itself, only the Cost API
(spend per day, in cents). So: the user enters the balance shown on
platform.claude.com/settings/billing once, and this module subtracts every
dollar spent since that moment.

The Cost API only has daily buckets. To avoid counting money spent earlier on
the day the balance was entered, the spend of that day *at entry time* is
remembered and subtracted out again.

Key: JARVIS_COST_KEY in .env — deliberately NOT ANTHROPIC_API_KEY, which would
switch Claude Code away from the subscription. Works with an Admin key
(sk-ant-admin...) or a personal key that is not scoped to a workspace.
"""
import datetime as dt
import json
import os
import threading
import time
import urllib.error
import urllib.request

import commands

URL = "https://api.anthropic.com/v1/organizations/cost_report"
_CACHE = {"at": 0.0, "data": None, "busy": False}
_LOCK = threading.Lock()
TTL = 300   # Cost API data lags ~5 min anyway; polling more often is pointless


def configured():
    return bool(os.environ.get("JARVIS_COST_KEY", "").strip())


def _day(ts):
    return dt.datetime.fromtimestamp(ts, dt.timezone.utc).strftime("%Y-%m-%d")


def _spend_by_day(start_day):
    """{'YYYY-MM-DD': dollars} from start_day (UTC) until now."""
    key = os.environ["JARVIS_COST_KEY"].strip()
    out, page = {}, None
    for _ in range(10):
        q = f"?starting_at={start_day}T00:00:00Z&limit=31" + (f"&page={page}" if page else "")
        req = urllib.request.Request(URL + q, headers={
            "x-api-key": key, "anthropic-version": "2023-06-01", "User-Agent": "JARVIS-HUD/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=30) as r:
                d = json.loads(r.read())
        except urllib.error.HTTPError as e:
            body = e.read(300).decode("utf-8", "replace")
            if e.code in (401, 403):
                raise RuntimeError("Key hat keinen Zugriff auf die Cost API (Admin-Key oder persönlicher Key nötig).")
            raise RuntimeError(f"Cost API HTTP {e.code}: {body}")
        for b in d.get("data", []):
            day = b["starting_at"][:10]
            out[day] = out.get(day, 0.0) + sum(float(x.get("amount", 0)) for x in b.get("results", [])) / 100
        if not d.get("has_more"):
            break
        page = d.get("next_page")
    return out


def set_balance(usd):
    """The user read `usd` off the billing page just now."""
    usd = round(float(usd), 2)
    now = time.time()
    today_spend = 0.0
    if configured():
        try:
            today_spend = _spend_by_day(_day(now)).get(_day(now), 0.0)
        except Exception:  # noqa: BLE001
            pass
    commands.update_state(lambda d: d.__setitem__("balance", {
        "usd": usd, "at": now, "day_spend_at_set": today_spend}))
    _CACHE.update(at=0.0, data=None)
    return summary(force=True)


def _compute():
    bal = commands.load().get("balance")
    now = time.time()
    info = {"configured": configured(), "balance_set": bool(bal)}
    if bal:
        info.update(entered=bal["usd"], entered_at=bal["at"])
    if not configured():
        if bal:
            info["remaining"] = bal["usd"]
            info["note"] = "ohne Cost-Key: Stand vom Eintragen, ohne Abzüge"
        return info
    month_start = dt.datetime.now(dt.timezone.utc).replace(day=1).strftime("%Y-%m-%d")
    first = min(month_start, _day(bal["at"])) if bal else month_start
    days = _spend_by_day(first)
    today = _day(now)
    info["today"] = round(days.get(today, 0.0), 2)
    info["month"] = round(sum(v for k, v in days.items() if k >= month_start), 2)
    if bal:
        since = sum(v for k, v in days.items() if k >= _day(bal["at"])) - bal.get("day_spend_at_set", 0.0)
        info["spent_since"] = round(max(0.0, since), 2)
        info["remaining"] = round(bal["usd"] - max(0.0, since), 2)
    info["at"] = now
    return info


def summary(force=False):
    """Cached; refreshes in the background so the HUD never waits on the API."""
    fresh = time.time() - _CACHE["at"] < TTL
    if _CACHE["data"] is not None and (fresh or not force):
        if not fresh and not _CACHE["busy"]:
            threading.Thread(target=_refresh, daemon=True).start()
        return _CACHE["data"]
    return _refresh()


def _refresh():
    with _LOCK:
        _CACHE["busy"] = True
        try:
            data = _compute()
        except Exception as e:  # noqa: BLE001
            data = {"configured": configured(), "error": str(e)[:200]}
            bal = commands.load().get("balance")
            if bal:
                data.update(balance_set=True, entered=bal["usd"], entered_at=bal["at"])
        _CACHE.update(at=time.time(), data=data, busy=False)
        return data
