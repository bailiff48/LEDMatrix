"""ESPN fantasy football (v3 API) league provider.

Points come straight from ESPN: each player's ``appliedStatTotal`` is his
score under this league's settings, and the starters add up to the team
total ESPN shows (verified on league 265444, week 7: 109.0 = 109.0).

Private leagues need the ``espn_s2`` and ``SWID`` cookies from a signed-in
browser. One request per refresh: the matchup views, filtered to this week's
matchup period so the response is small (the unfiltered views are ~12 MB).
"""

import json
import logging
from typing import Any, Dict, List, Optional

from .base import AuthError, LeagueProvider, ProviderError, short_name

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


class EspnProvider(LeagueProvider):
    name = "espn"

    def _get(self, views: List[str], week: Optional[int] = None,
             matchup_period: Optional[int] = None) -> Dict[str, Any]:
        params = [("view", v) for v in views]
        if week:
            params.append(("scoringPeriodId", int(week)))
        headers = {"User-Agent": "ledmatrix-fantasy-leagues"}
        if matchup_period:
            headers["X-Fantasy-Filter"] = json.dumps(
                {"schedule": {"filterMatchupPeriodIds": {"value": [int(matchup_period)]}}})
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
        return resp.json()

    # ------------------------------------------------------------------

    def fetch_settings(self) -> Dict[str, Any]:
        """League name, current week and lineup. Changes rarely; cache daily."""
        data = self._get(["mSettings", "mStatus"])
        return parse_settings(data)

    def current_week(self) -> int:
        return self.fetch_settings()["week"]

    def fetch_matchup(self, week: int, league_name: str = "") -> Dict[str, Any]:
        views = ["mMatchup", "mMatchupScore", "mBoxscore", "mScoreboard"]
        data = self._get(views, week=week, matchup_period=week)
        if not any(g.get("matchupPeriodId") == week for g in data.get("schedule") or []):
            # Filter ignored or a playoff period numbered differently: retry unfiltered.
            data = self._get(views, week=week)
        return parse_matchup(data, self.team_id, week, league_id=self.league_id,
                             season=self.season, league_name=league_name)


# ----------------------------------------------------------------------
# parsing (pure functions, tested against a recorded response)
# ----------------------------------------------------------------------

def parse_settings(data: Dict[str, Any]) -> Dict[str, Any]:
    settings = data.get("settings") or {}
    status = data.get("status") or {}
    week = int(status.get("currentMatchupPeriod") or data.get("scoringPeriodId") or 1)
    slots = (settings.get("rosterSettings") or {}).get("lineupSlotCounts") or {}
    return {
        "name": str(settings.get("name") or ""),
        "week": week,
        "latest_scoring_period": int(status.get("latestScoringPeriod") or week),
        "final_week": int(status.get("finalScoringPeriod") or 17),
        "lineup": {SLOTS.get(int(k), k): int(v) for k, v in slots.items()
                   if int(v) and int(k) not in BENCH_SLOTS},
    }


def parse_matchup(data: Dict[str, Any], team_id: Any, week: int, league_id: str = "",
                  season: int = 0, league_name: str = "") -> Dict[str, Any]:
    team_id = int(team_id)
    teams = {t.get("id"): t for t in data.get("teams") or []}
    game = None
    for g in data.get("schedule") or []:
        if g.get("matchupPeriodId") != week:
            continue
        ids = ((g.get("home") or {}).get("teamId"), (g.get("away") or {}).get("teamId"))
        if team_id in ids:
            game = g
            break
    if game is None:
        raise ProviderError(f"No week {week} matchup for team {team_id} (bye or wrong team id?)")
    home, away = game.get("home") or {}, game.get("away") or {}
    mine, theirs = (home, away) if home.get("teamId") == team_id else (away, home)
    return {
        "provider": "espn",
        "league_id": str(league_id),
        "league_name": league_name,
        "season": season,
        "week": week,
        "final": str(game.get("winner") or "UNDECIDED") != "UNDECIDED",
        "me": _side(mine, teams, week),
        "opp": _side(theirs, teams, week),
    }


def _side(side: Dict[str, Any], teams: Dict[Any, Dict[str, Any]], week: int) -> Dict[str, Any]:
    tid = side.get("teamId")
    team = teams.get(tid) or {}
    overall = ((team.get("record") or {}).get("overall") or {})
    record = f"{int(overall.get('wins') or 0)}-{int(overall.get('losses') or 0)}"
    if overall.get("ties"):
        record += f"-{int(overall['ties'])}"
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
        "record": record,
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
