"""What every league provider (ESPN, Yahoo, Sleeper) returns.

A provider turns one site's API into a plain ``Matchup`` dict so screens never
care where the numbers came from. Points are always the league's own points
as the site reports them (each league's scoring), never a generic format.

Matchup = {
  "provider": "espn", "league_id": "265444", "league_name": "...",
  "season": 2026, "week": 7, "final": bool,
  "me":  Side, "opp": Side,
}
Side = {
  "team_id": 14, "name": "...", "abbrev": "BMF", "record": "5-2",
  "points": 109.0, "projected": 80.2,
  "starters": [Player], "bench": [Player],
}
Player = {
  "id": 4361370, "name": "Joe Burrow", "short": "BURROW", "pos": "QB",
  "slot": "QB", "team": "CIN", "points": 36.0, "projected": 24.2,
  "injury": "ACTIVE" | "QUESTIONABLE" | ..., "locked": bool,
}
"""

from typing import Any, Dict, Optional


class ProviderError(Exception):
    """A league could not be read (bad id, expired login, site down)."""


class AuthError(ProviderError):
    """The site refused our credentials (private league, stale cookies/token)."""


class LeagueProvider:
    name = "base"

    def __init__(self, league_id: str, season: int, team_id: Any,
                 credentials: Optional[Dict[str, str]] = None, session=None,
                 timeout: float = 15.0) -> None:
        self.league_id = str(league_id)
        self.season = int(season)
        self.team_id = team_id
        self.credentials = credentials or {}
        self.session = session
        self.timeout = timeout

    def current_week(self) -> int:
        raise NotImplementedError

    def fetch_matchup(self, week: int) -> Dict[str, Any]:
        """My matchup for ``week`` with both rosters and league points."""
        raise NotImplementedError


def short_name(full: str) -> str:
    """'Jaxon Smith-Njigba' -> 'SMITH-NJIGBA'; 'Patriots D/ST' -> 'PATRIOTS'."""
    full = (full or "").strip()
    if full.endswith("D/ST"):
        return full[:-4].strip().upper()
    parts = [p for p in full.split() if p.rstrip(".").upper() not in ("JR", "SR", "II", "III", "IV", "V")]
    return (parts[-1] if parts else full).upper()


_SUFFIXES = ("JR", "SR", "II", "III", "IV", "V")


def _given(full: str) -> str:
    parts = [p for p in (full or "").split() if p.rstrip(".").upper() not in _SUFFIXES]
    return "".join(ch for ch in (parts[0] if len(parts) > 1 else "") if ch.isalpha()).upper()


def disambiguate(players) -> None:
    """Give players who share a short name a first-name prefix, in place.

    'Mike Washington Jr.' + 'Malik Washington' -> 'MI.WASHINGTON' /
    'MA.WASHINGTON'; one letter is used when it is enough ('J.SMITH').
    D/ST and anyone without a first name are left alone.
    """
    groups = {}
    for p in players:
        groups.setdefault(p.get("short"), []).append(p)
    for short, same in groups.items():
        if len(same) < 2 or len({p.get("id") for p in same}) < 2:
            continue
        givens = [_given(p.get("name", "")) for p in same]
        for n in range(1, 4):
            prefixes = [g[:n] for g in givens]
            if all(prefixes) and len(set(prefixes)) == len(prefixes):
                break
        for p, pre in zip(same, prefixes):
            if pre:
                p["short"] = f"{pre}.{short}"
