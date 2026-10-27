"""ESPN fantasy football (v3 API) league provider.

Points come straight from ESPN: each player's ``appliedStatTotal`` is his
score under this league's settings, and the starters add up to the team
total ESPN shows (verified on league 265444, week 7: 109.0 = 109.0).

Private leagues need the ``espn_s2`` and ``SWID`` cookies from a signed-in
browser.

Request sizes (league 265444, Oct 27 2026, KB per live poll): the old
``mMatchup`` + ``mMatchupScore`` + ``mBoxscore`` + ``mScoreboard`` set was
5045; the lean pair ``mMatchupScore`` + ``mScoreboard`` (what the python
espn-api box scores use) 397; the lean pair with an X-Fantasy-Filter on
``filterMatchupPeriodIds`` + ``filterTeamIds`` 67, with both rosters of my one
game. So the live poll asks for that, and team names/records come from the
daily settings call (``mTeam``). If the filtered reply is missing my game the
provider asks again unfiltered; if the lean views ever come back without
rosters it falls back to the full set once and remembers it.
``tools/espn_size_probe.py`` measures every variant on a Pi.
"""

import json
import logging
from typing import Any, Dict, List, Optional

from .base import AuthError, LeagueProvider, ProviderError, disambiguate, short_name

logger = logging.getLogger(__name__)

URL = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
       "{season}/segments/0/leagues/{league_id}")

#: ESPN lineup slot id -> label on the panel.
SLOTS = {0: "QB", 1: "TQB", 2: "RB", 3: "R/W", 4: "WR", 5: "W/T", 6: "TE", 7: "OP",
         16: "DST", 17: "K", 18: "P", 19: "HC", 20: "BE", 21: "IR", 23: "FLX", 24: "ER"}
BENCH_SLOTS = (20, 21)
#: Order starters are listed in, top to bottom.
SLOT_ORDER = [0, 1, 2, 3, 23, 4, 5, 6, 7, 16, 17, 18, 19, 24]
POSITIONS = {1: "QB", 2: "RB", 3: "WR", 4: "TE", 5: "K", 7: "P", 16: "DST"}
#: ESPN proTeamId -> NFL abbreviation.
NFL_TEAMS = {0: "FA", 1: "ATL", 2: "BUF", 3: "CHI", 4: "CIN", 5: "CLE", 6: "DAL", 7: "DEN",
             8: "DET", 9: "GB", 10: "TEN", 11: "IND", 12: "KC", 13: "LV", 14: "LAR",
             15: "MIA", 16: "MIN", 17: "NE", 18: "NO", 19: "NYG", 20: "NYJ", 21: "PHI",
             22: "ARI", 23: "PIT", 24: "LAC", 25: "SF", 26: "SEA", 27: "TB", 28: "WSH",
             29: "CAR", 30: "JAX", 33: "BAL", 34: "HOU"}
ACTUAL, PROJECTED = 0, 1  # statSourceId

LEAN_VIEWS = ["mMatchupScore", "mScoreboard"]
FULL_VIEWS = ["mMatchup", "mMatchupScore", "mBoxscore", "mScoreboard"]
SETTINGS_VIEWS = ["mSettings", "mStatus", "mTeam"]


class EspnProvider(LeagueProvider):
    name = "espn"

    def __init__(self, *args, **kwargs) -> None:
        super().__init__(*args, **kwargs)
        self.views = list(LEAN_VIEWS)
        self.last_response_bytes = 0

    def _get(self, views: List[str], week: Optional[int] = None,
             matchup_period: Optional[int] = None,
             team_id: Optional[int] = None) -> Dict[str, Any]:
        params = [("view", v) for v in views]
        if week:
            params.append(("scoringPeriodId", int(week)))
        headers = {"User-Agent": "ledmatrix-fantasy-leagues"}
        if matchup_period:
            schedule: Dict[str, Any] = {"filterMatchupPeriodIds": {"value": [int(matchup_period)]}}
            if team_id is not None:
                schedule["filterTeamIds"] = {"value": [int(team_id)]}
            headers["X-Fantasy-Filter"] = json.dumps({"schedule": schedule})
        cookies = None
        s2, swid = self.credentials.get("espn_s2"), self.credentials.get("swid")
        if s2 and swid:
            cookies = {"espn_s2": s2, "SWID": swid}
        resp = self.session.get(URL.format(season=self.season, league_id=self.league_id),
                                params=params, headers=headers, cookies=cookies,
                                timeout=self.timeout)
        if resp.status_code in (401, 403):
            raise AuthError(f"ESPN refused league {self.league_id} (HTTP {resp.status_code}): "
                            "check espn_s2 / SWID")
        if resp.status_code == 404:
            raise ProviderError(f"ESPN league {self.league_id} not found for {self.season}")
        resp.raise_for_status()
        self.last_response_bytes = len(getattr(resp, "content", b"") or b"")
        return resp.json()

    # ------------------------------------------------------------------

    def fetch_settings(self) -> Dict[str, Any]:
        """League name, current week, lineup and team names. Cache daily."""
        data = self._get(SETTINGS_VIEWS)
        return parse_settings(data)

    def fetch_league_info(self) -> Dict[str, Any]:
        """For the web page's Test button: league name, week and every team,
        with the signed-in user's own team marked when the SWID owns one."""
        return league_info(self._get(SETTINGS_VIEWS), self.credentials.get("swid", ""))

    def current_week(self) -> int:
        return self.fetch_settings()["week"]

    def fetch_matchup(self, week: int, league_name: str = "",
                      teams: Optional[Dict[Any, Dict[str, Any]]] = None,
                      scoring_period: Optional[int] = None) -> Dict[str, Any]:
        """My matchup for matchup period ``week``.

        ``teams`` (from fetch_settings) fills in names and records the lean
        views leave out. ``scoring_period`` is the NFL week whose player
        points to show; it differs from ``week`` only in two-week playoff
        rounds.
        """
        period = int(scoring_period or week)
        team = int(self.team_id)
        data = self._get(self.views, week=period, matchup_period=week, team_id=team)
        if _my_game(data, team, week) is None:
            # A playoff period numbered differently, or a filter ESPN did not
            # honour: retry without the filter.
            data = self._get(self.views, week=period)
        if self.views != FULL_VIEWS and not _has_rosters(data, self.team_id, week):
            logger.warning("ESPN league %s: lean views had no rosters; using full views",
                           self.league_id)
            self.views = list(FULL_VIEWS)
            data = self._get(self.views, week=period, matchup_period=week, team_id=team)
        return parse_matchup(data, self.team_id, week, league_id=self.league_id,
                             season=self.season, league_name=league_name, teams=teams,
                             scoring_period=period)


# ----------------------------------------------------------------------
# parsing (pure functions, tested against a recorded response)
# ----------------------------------------------------------------------

def _my_game(data: Dict[str, Any], team_id: int, week: int) -> Optional[Dict[str, Any]]:
    for g in data.get("schedule") or []:
        if g.get("matchupPeriodId") != week:
            continue
        ids = ((g.get("home") or {}).get("teamId"), (g.get("away") or {}).get("teamId"))
        if team_id in ids:
            return g
    return None


def _has_rosters(data: Dict[str, Any], team_id: Any, week: int) -> bool:
    game = _my_game(data, int(team_id), week)
    if game is None:
        return True  # nothing to compare; let parse_matchup report it
    side = game.get("home") or {}
    return bool((side.get("rosterForCurrentScoringPeriod") or {}).get("entries"))


def parse_teams(data: Dict[str, Any]) -> Dict[int, Dict[str, Any]]:
    out = {}
    for t in data.get("teams") or []:
        if t.get("id") is None:
            continue
        overall = ((t.get("record") or {}).get("overall") or {})
        rec = f"{int(overall.get('wins') or 0)}-{int(overall.get('losses') or 0)}"
        if overall.get("ties"):
            rec += f"-{int(overall['ties'])}"
        name = t.get("name") or " ".join(x for x in (t.get("location"), t.get("nickname")) if x)
        out[int(t["id"])] = {"name": str(name or ""), "abbrev": str(t.get("abbrev") or ""),
                             "record": rec if overall else ""}
    return out

def _swid_key(value: Any) -> str:
    return str(value or "").strip().strip("{}").upper()


def league_info(data: Dict[str, Any], swid: str = "") -> Dict[str, Any]:
    """League name, season, week and teams (id, name, abbrev, mine) from a
    settings reply. ``mine`` is set when ``swid`` is one of the team's owners."""
    settings = parse_settings(data)
    me = _swid_key(swid)
    teams = []
    for t in data.get("teams") or []:
        if t.get("id") is None:
            continue
        owners = [_swid_key(o) for o in (t.get("owners") or [])]
        if t.get("primaryOwner"):
            owners.append(_swid_key(t["primaryOwner"]))
        info = settings["teams"].get(int(t["id"]), {})
        teams.append({"id": int(t["id"]), "name": info.get("name", ""),
                      "abbrev": info.get("abbrev", ""), "record": info.get("record", ""),
                      "mine": bool(me) and me in owners})
    teams.sort(key=lambda x: x["id"])
    return {"name": settings["name"], "season": int(data.get("seasonId") or 0) or None,
            "week": settings["week"], "teams": teams}


def parse_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    settings = data.get("settings") or {}
    status = data.get("status") or {}
    week = int(status.get("currentMatchupPeriod") or data.get("scoringPeriodId") or 1)
    slots = (settings.get("rosterSettings") or {}).get("lineupSlotCounts") or {}
    periods = ((settings.get("scheduleSettings") or {}).get("matchupPeriods") or {})
    return {
        "name": str(settings.get("name") or ""),
        "week": week,
        "teams": parse_teams(data),
        "matchup_periods": {int(k): [int(x) for x in v] for k, v in periods.items()},
        "latest_scoring_period": int(status.get("latestScoringPeriod") or week),
        "final_week": int(status.get("finalScoringPeriod") or 17),
        "lineup": {SLOTS.get(int(k), k): int(v) for k, v in slots.items()
                   if int(v) and int(k) not in BENCH_SLOTS},
    }


def parse_matchup(data: Dict[str, Any], team_id: Any, week: int, league_id: str = "",
                  season: int = 0, league_name: str = "",
                  teams: Optional[Dict[Any, Dict[str, Any]]] = None,
                  scoring_period: Optional[int] = None) -> Dict[str, Any]:
    team_id = int(team_id)
    known = {int(k): v for k, v in (teams or {}).items()}
    for tid, fresh in parse_teams(data).items():  # this response's own values win
        known[tid] = {**known.get(tid, {}), **{k: v for k, v in fresh.items() if v}}
    game = _my_game(data, team_id, week)
    if game is None:
        raise ProviderError(f"No week {week} matchup for team {team_id} (bye or wrong team id?)")
    home, away = game.get("home") or {}, game.get("away") or {}
    mine, theirs = (home, away) if home.get("teamId") == team_id else (away, home)
    period = int(scoring_period or week)
    me, opp = _side(mine, known, period), _side(theirs, known, period)
    disambiguate(me["starters"] + me["bench"] + opp["starters"] + opp["bench"])
    return {
        "provider": "espn",
        "league_id": str(league_id),
        "league_name": league_name,
        "season": season,
        "week": week,
        "final": str(game.get("winner") or "UNDECIDED") != "UNDECIDED",
        "me": me,
        "opp": opp,
    }


def _side(side: Dict[str, Any], teams: Dict[Any, Dict[str, Any]], week: int) -> Dict[str, Any]:
    tid = side.get("teamId")
    team = teams.get(tid) or {}
    starters, bench = [], []
    entries = (side.get("rosterForCurrentScoringPeriod") or {}).get("entries") or []
    for e in entries:
        player = _player(e, week)
        (bench if e.get("lineupSlotId") in BENCH_SLOTS else starters).append(player)
    order = {SLOTS.get(s): i for i, s in enumerate(SLOT_ORDER)}
    starters.sort(key=lambda p: order.get(p["slot"], 99))
    live = side.get("totalPointsLive")
    points = live if live is not None else side.get("totalPoints")
    if (points is None or points == 0) and starters:
        points = sum(p["points"] for p in starters)
    projected = side.get("totalProjectedPointsLive")
    if projected is None:
        projected = side.get("totalProjectedPoints")
    return {
        "team_id": tid,
        "name": str(team.get("name") or f"TEAM {tid}"),
        "abbrev": str(team.get("abbrev") or ""),
        "record": team.get("record") or "",
        "points": round(float(points or 0.0), 2),
        "projected": round(float(projected), 2) if projected is not None else None,
        "starters": starters,
        "bench": bench,
    }


def _player(entry: Dict[str, Any], week: int) -> Dict[str, Any]:
    ppe = entry.get("playerPoolEntry") or {}
    p = ppe.get("player") or {}
    projected = None
    actual = None
    for s in p.get("stats") or []:
        if s.get("scoringPeriodId") != week or s.get("statSplitTypeId") not in (1, None):
            continue
        if s.get("statSourceId") == PROJECTED:
            projected = s.get("appliedTotal")
        elif s.get("statSourceId") == ACTUAL:
            actual = s.get("appliedTotal")
    points = ppe.get("appliedStatTotal")
    if points is None:
        points = actual
    name = str(p.get("fullName") or "")
    return {
        "id": p.get("id") or entry.get("playerId"),
        "name": name,
        "short": short_name(name),
        "pos": POSITIONS.get(p.get("defaultPositionId"), "?"),
        "slot": SLOTS.get(entry.get("lineupSlotId"), str(entry.get("lineupSlotId"))),
        "team": NFL_TEAMS.get(p.get("proTeamId"), "FA"),
        "points": round(float(points or 0.0), 2),
        "projected": round(float(projected), 2) if projected is not None else None,
        "injury": str(p.get("injuryStatus") or entry.get("injuryStatus") or "ACTIVE"),
        "locked": bool(ppe.get("lineupLocked")),
    }
