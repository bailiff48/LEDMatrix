"""
ESPN scoreboard date-range workaround  (added Oct 2026)

Since mid-September 2026, ESPN's site API answers any scoreboard request
using a date range (dates=YYYYMMDD-YYYYMMDD) with HTTP 400
"Failed to get events endpoint."  Single days (YYYYMMDD), whole months
(YYYYMM) and whole years (YYYY) still return 200.

Importing this module patches requests.Session.request (which also covers
plain requests.get) so that any GET to site.api.espn.com/.../scoreboard
with a date range is transparently rewritten as:

  1. one request per calendar month the range touches (dates=YYYYMM);
  2. if ESPN refuses a month, or the month hits the result limit, that
     month's slice of the range is fetched one day at a time instead;
  3. results are merged, de-duplicated by event id, trimmed back to the
     original range (ESPN assigns games to US Eastern days), and sorted.

Callers get back a normal 200 Response with the usual JSON shape, so no
sports manager code needs to change. Requests without a range are sent
untouched. If everything fails, the caller sees ESPN's real error.

Install: import once, before any managers are created, e.g. at the top of
src/display_controller.py:

    import src.espn_date_range_fix  # ESPN date-range workaround
"""
import json
import logging
import re
import threading
import time
from datetime import date, datetime, timedelta
from urllib.parse import parse_qsl, urlsplit, urlunsplit

import requests

try:
    from zoneinfo import ZoneInfo
    _ESPN_TZ = ZoneInfo("America/New_York")
except Exception:  # pragma: no cover
    _ESPN_TZ = None

logger = logging.getLogger(__name__)

_RANGE_RE = re.compile(r"^(\d{8})-(\d{8})$")
_MONTH_LIMIT = 1000      # per-month request size; hitting it triggers per-day fallback
_DAY_LIMIT = 500
_CACHE_TTL = 30          # seconds; dedupes near-simultaneous recent/upcoming fetches
_MAX_CACHE = 300

_cache = {}
_cache_lock = threading.Lock()
_orig_request = requests.Session.request


def _event_day(event):
    """ESPN's local (US Eastern) calendar day for an event, or None."""
    raw = event.get("date")
    if not raw:
        return None
    try:
        dt = datetime.fromisoformat(raw.replace("Z", "+00:00"))
    except ValueError:
        return None
    if _ESPN_TZ is not None and dt.tzinfo is not None:
        dt = dt.astimezone(_ESPN_TZ)
    return dt.date()


def _months_between(start, end):
    y, m = start.year, start.month
    while (y, m) <= (end.year, end.month):
        yield y, m
        m += 1
        if m == 13:
            y, m = y + 1, 1


def _month_last_day(y, m):
    return date(y + m // 12, m % 12 + 1, 1) - timedelta(days=1)


def _fetch(session, base_url, params, kwargs):
    """GET via the original (unpatched) request, with a short TTL cache."""
    key = (base_url, tuple(sorted(params.items())))
    now = time.time()
    with _cache_lock:
        hit = _cache.get(key)
        if hit and now - hit[0] < _CACHE_TTL:
            return hit[1]
    resp = _orig_request(session, "GET", base_url, params=params, **kwargs)
    if resp.status_code == 200:
        with _cache_lock:
            _cache[key] = (now, resp)
            if len(_cache) > _MAX_CACHE:
                for k in [k for k, v in _cache.items() if now - v[0] >= _CACHE_TTL]:
                    _cache.pop(k, None)
    return resp


def _ranged_fetch(session, original_url, base_url, query, start_s, end_s, kwargs):
    start = datetime.strptime(start_s, "%Y%m%d").date()
    end = datetime.strptime(end_s, "%Y%m%d").date()
    if end < start:
        start, end = end, start

    base_params = {k: v for k, v in query.items() if k not in ("dates", "limit")}
    template = None
    last_resp = None
    events = {}
    requests_made = 0

    def add(resp):
        nonlocal template
        data = resp.json()
        if template is None:
            template = data
        for ev in data.get("events") or []:
            day = _event_day(ev)
            if day is not None and not (start <= day <= end):
                continue
            eid = ev.get("id") or json.dumps(ev, sort_keys=True)[:200]
            events.setdefault(eid, ev)

    for y, mo in _months_between(start, end):
        params = dict(base_params, dates=f"{y:04d}{mo:02d}", limit=str(_MONTH_LIMIT))
        resp = _fetch(session, base_url, params, kwargs)
        requests_made += 1
        last_resp = resp
        if resp.status_code == 200:
            if len(resp.json().get("events") or []) < _MONTH_LIMIT:
                add(resp)
                continue
        # Month refused or truncated: fetch this month's slice of the range by day
        d = max(start, date(y, mo, 1))
        last = min(end, _month_last_day(y, mo))
        while d <= last:
            params = dict(base_params, dates=d.strftime("%Y%m%d"), limit=str(_DAY_LIMIT))
            r = _fetch(session, base_url, params, kwargs)
            requests_made += 1
            last_resp = r
            if r.status_code == 200:
                add(r)
            d += timedelta(days=1)

    if template is None:
        # Nothing succeeded; hand back ESPN's real error so callers log it as before
        return last_resp

    merged = dict(template)
    merged["events"] = sorted(events.values(), key=lambda ev: ev.get("date") or "")

    out = requests.models.Response()
    out.status_code = 200
    out.reason = "OK"
    out._content = json.dumps(merged).encode("utf-8")
    out.encoding = "utf-8"
    out.headers["Content-Type"] = "application/json; charset=utf-8"
    out.url = original_url
    out.elapsed = timedelta(0)
    logger.debug("ESPN range %s-%s on %s: %d request(s), %d events",
                 start_s, end_s, base_url, requests_made, len(merged["events"]))
    return out


def _patched_request(self, method, url, params=None, **kwargs):
    if (str(method).upper() == "GET"
            and "site.api.espn.com" in str(url)
            and "/scoreboard" in str(url)):
        try:
            prepared = requests.Request("GET", url, params=params).prepare()
            parts = urlsplit(prepared.url)
            query = dict(parse_qsl(parts.query, keep_blank_values=True))
            match = _RANGE_RE.match(query.get("dates", ""))
            if match:
                base_url = urlunsplit((parts.scheme, parts.netloc, parts.path, "", ""))
                return _ranged_fetch(self, prepared.url, base_url, query,
                                     match.group(1), match.group(2), kwargs)
        except Exception as e:
            logger.warning("ESPN range workaround failed, sending original request: %s", e)
    return _orig_request(self, method, url, params=params, **kwargs)


if getattr(requests.Session.request, "__name__", "") != "_patched_request":
    requests.Session.request = _patched_request
    logger.info("ESPN date-range workaround active (ranges -> month/day requests)")
