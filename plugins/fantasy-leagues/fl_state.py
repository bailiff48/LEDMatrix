"""League bookkeeping for the fantasy-leagues plugin (no drawing, no network).

* ``normalize_league`` turns a config entry into a complete league dict.
* ``LeagueSlot`` holds one league's last good settings, matchup and recap,
  and decides when each needs fetching.
* ``detect_alerts`` compares two matchup snapshots and returns big plays.
* ``AlertQueue`` merges the same play seen in several leagues into one alert.
"""

import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Sequence, Tuple

RGB = Tuple[int, int, int]

HOSTS: Dict[str, Dict[str, Any]] = {
    "espn": {"label": "ESPN", "color": (200, 16, 46)},
    "yahoo": {"label": "YHOO", "color": (96, 1, 210)},
    "sleeper": {"label": "SLPR", "color": (0, 150, 170)},
}
DEFAULT_TEAM_COLOR: RGB = (40, 200, 80)
WEEKDAYS = ("mon", "tue", "wed", "thu", "fri", "sat", "sun")

SETTINGS_MAX_AGE = 12 * 3600
RECAP_MAX_AGE = 6 * 3600
#: Points changing this recently keeps the fast poll on outside game windows.
RECENT_CHANGE = 20 * 60
#: The same player's play from another league within this window merges.
MERGE_WINDOW = 180
#: An alert nobody got to see within this long is dropped.
ALERT_MAX_AGE = 600
MAX_QUEUE = 8


# ----------------------------------------------------------------------
# config
# ----------------------------------------------------------------------

def parse_color(value: Any, default: RGB) -> RGB:
    """'#22c55e', '22c55e', [34, 197, 94] or None -> an RGB tuple."""
    if isinstance(value, (list, tuple)) and len(value) == 3:
        try:
            return tuple(max(0, min(255, int(c))) for c in value)  # type: ignore[return-value]
        except (TypeError, ValueError):
            return default
    if isinstance(value, str):
        h = value.strip().lstrip("#")
        if len(h) == 6:
            try:
                return tuple(int(h[i:i + 2], 16) for i in (0, 2, 4))  # type: ignore[return-value]
            except ValueError:
                return default
    return default


def default_season(now: Optional[datetime] = None) -> int:
    """NFL season year: Jan-Feb playoffs still belong to last year's season."""
    now = now or datetime.now()
    return now.year if now.month >= 3 else now.year - 1


def normalize_league(raw: Dict[str, Any], now: Optional[datetime] = None) -> Dict[str, Any]:
    provider = str(raw.get("provider") or "espn").lower()
    host = HOSTS.get(provider, {"label": provider[:4].upper(), "color": (120, 120, 120)})
    screens = raw.get("screens") or {}
    alerts = raw.get("alerts") or {}
    return {
        "provider": provider,
        "league_id": str(raw.get("league_id") or "").strip(),
        "team_id": raw.get("team_id"),
        "season": int(raw.get("season") or default_season(now)),
        "enabled": bool(raw.get("enabled", True)),
        "label": str(raw.get("label") or host["label"]).upper()[:6],
        "host_color": parse_color(raw.get("host_color"), host["color"]),
        "team_color": parse_color(raw.get("team_color"), DEFAULT_TEAM_COLOR),
        "screens": {
            "summary": bool(screens.get("summary", True)),
            "matchup": bool(screens.get("matchup", True)),
            "roster": bool(screens.get("roster", True)),
            "roster_opponent": bool(screens.get("roster_opponent", False)),
        },
        "alerts": {
            "mine": bool(alerts.get("mine", True)),
            "opponent": bool(alerts.get("opponent", True)),
        },
    }


def in_game_window(local: datetime) -> bool:
    """True during the usual NFL kickoff-to-final windows (US Central)."""
    wd, minutes = local.weekday(), local.hour * 60 + local.minute
    if wd == 6:                                   # Sunday, London games from 8:30
        return minutes >= 8 * 60
    if wd in (0, 3):                              # Monday / Thursday night
        return minutes >= 18 * 60 + 45
    if wd in (0, 1, 4) and minutes <= 30:         # games running past midnight
        return True
    if wd == 5 and local.month in (12, 1):        # late-season Saturdays
        return minutes >= 11 * 60
    if wd == 4 and local.month == 11 and local.day >= 22:  # Black Friday game
        return minutes >= 13 * 60
    return False


# ----------------------------------------------------------------------
# one league
# ----------------------------------------------------------------------

class LeagueSlot:
    def __init__(self, cfg: Dict[str, Any], provider: Any) -> None:
        self.cfg = cfg
        self.provider = provider
        self.key = f"{cfg['provider']}:{cfg['league_id']}:{cfg['team_id']}"
        self.settings: Optional[Dict[str, Any]] = None
        self.settings_at = 0.0
        self.matchup: Optional[Dict[str, Any]] = None
        self.matchup_at = 0.0
        self.recap: Optional[Dict[str, Any]] = None
        self.recap_at = 0.0
        self.error: Optional[str] = None
        self.auth_error = False
        self.last_change_at = 0.0
        self.next_fetch = 0.0

    @property
    def week(self) -> Optional[int]:
        return (self.settings or {}).get("week")

    def needs_settings(self, now: float) -> bool:
        return self.settings is None or now - self.settings_at > SETTINGS_MAX_AGE

    def needs_recap(self, now: float, weekday: str, recap_days: Sequence[str]) -> bool:
        week = self.week
        if not week or week <= 1 or weekday not in recap_days:
            return False
        if self.recap is None or self.recap.get("week") != week - 1:
            return True
        return now - self.recap_at > RECAP_MAX_AGE

    def is_live(self, now: float, local: datetime) -> bool:
        return in_game_window(local) or (now - self.last_change_at) < RECENT_CHANGE

    def view(self, weekday: str, recap_days: Sequence[str]) -> Optional[Dict[str, Any]]:
        """The matchup to show: last week's final early in the week."""
        if weekday in recap_days and self.recap is not None:
            return self.recap
        return self.matchup

    def scoring_period(self) -> Optional[int]:
        """NFL week whose points to show (differs only in two-week playoff rounds)."""
        s = self.settings or {}
        week = s.get("week")
        periods = (s.get("matchup_periods") or {}).get(week) or []
        latest = s.get("latest_scoring_period")
        if periods and latest in periods:
            return latest
        return periods[0] if periods else week


# ----------------------------------------------------------------------
# big plays
# ----------------------------------------------------------------------

def player_key(p: Dict[str, Any]) -> str:
    """Same NFL player across sites: name + NFL team (ids differ by site)."""
    name = "".join(ch for ch in str(p.get("name") or "").upper() if ch.isalnum())
    return f"{name}:{p.get('team') or ''}"


def points_changed(old: Optional[Dict[str, Any]], new: Dict[str, Any]) -> bool:
    if not old or old.get("week") != new.get("week"):
        return False
    return any(abs(float(new[s]["points"]) - float(old[s]["points"])) > 0.01 for s in ("me", "opp"))


def detect_alerts(cfg: Dict[str, Any], old: Optional[Dict[str, Any]], new: Dict[str, Any],
                  min_points: float, now: Optional[float] = None) -> List[Dict[str, Any]]:
    """Starters whose league points rose by ``min_points`` or more."""
    if not old or old.get("week") != new.get("week") or new.get("final"):
        return []
    now = time.time() if now is None else now
    out = []
    for side in ("me", "opp"):
        if not cfg["alerts"]["mine" if side == "me" else "opponent"]:
            continue
        before = {p["id"]: p for p in old[side]["starters"] + old[side]["bench"]}
        for p in new[side]["starters"]:
            prev = before.get(p["id"])
            if prev is None:
                continue
            delta = round(float(p["points"]) - float(prev["points"]), 2)
            if delta < min_points:
                continue
            out.append({
                "key": player_key(p), "at": now,
                "short": p["short"], "name": p["name"], "pos": p["pos"], "team": p["team"],
                "leagues": [{
                    "league": cfg["label"], "host_color": cfg["host_color"],
                    "team_color": cfg["team_color"], "side": side,
                    "delta": delta, "points": p["points"],
                    "me": (new["me"]["abbrev"], new["me"]["points"]),
                    "opp": (new["opp"]["abbrev"], new["opp"]["points"]),
                }],
            })
    return out


class AlertQueue:
    def __init__(self) -> None:
        self.pending: List[Dict[str, Any]] = []
        self.current: Optional[Dict[str, Any]] = None
        self.current_until = 0.0
        self.recent: List[Dict[str, Any]] = []  # shown or queued, for merging

    def add(self, alert: Dict[str, Any]) -> None:
        for seen in self.recent:
            if seen["key"] == alert["key"] and alert["at"] - seen["at"] <= MERGE_WINDOW:
                have = {(lg["league"], lg["side"]) for lg in seen["leagues"]}
                for lg in alert["leagues"]:
                    if (lg["league"], lg["side"]) not in have:
                        seen["leagues"].append(lg)
                    else:  # same league again (two quick scores): keep the newest
                        for i, old in enumerate(seen["leagues"]):
                            if (old["league"], old["side"]) == (lg["league"], lg["side"]):
                                lg = dict(lg, delta=round(old["delta"] + lg["delta"], 2))
                                seen["leagues"][i] = lg
                return
        self.pending.append(alert)
        self.recent.append(alert)
        del self.pending[:-MAX_QUEUE]
        self.recent = [a for a in self.recent if alert["at"] - a["at"] <= MERGE_WINDOW]

    def active(self, now: float) -> Optional[Dict[str, Any]]:
        """The alert to draw now (advancing the queue), or None."""
        if self.current is not None and now < self.current_until:
            return self.current
        self.current = None
        self.pending = [a for a in self.pending if now - a["at"] <= ALERT_MAX_AGE]
        return None

    def start_next(self, now: float, seconds: float) -> Optional[Dict[str, Any]]:
        if self.active(now) is not None:
            return self.current
        if not self.pending:
            return None
        self.current = self.pending.pop(0)
        self.current_until = now + seconds
        return self.current

    def has_content(self, now: float) -> bool:
        if self.current is not None and now < self.current_until:
            return True
        return any(now - a["at"] <= ALERT_MAX_AGE for a in self.pending)
