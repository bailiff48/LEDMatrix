"""ESPN provider tests against a trimmed real capture (league 265444, 2026)."""

import json
import os
import sys

import pytest

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

from providers import AuthError, ProviderError  # noqa: E402
from providers.base import short_name  # noqa: E402
from providers.espn import EspnProvider, parse_matchup, parse_settings  # noqa: E402

FIX = json.load(open(os.path.join(HERE, "test/fixtures/espn_265444.json")))


def test_settings():
    s = parse_settings(FIX["settings"])
    assert s["name"] == "Urbandale Jaycees"
    assert s["week"] == 8
    assert s["lineup"] == {"QB": 1, "RB": 1, "R/W": 2, "WR": 1, "W/T": 1, "TE": 1, "DST": 1, "K": 1}


def test_finished_week_points_are_espns_and_add_up():
    m = parse_matchup(FIX["weeks"]["7"], 14, 7, league_id="265444", season=2026)
    me, opp = m["me"], m["opp"]
    assert m["final"] is True
    assert me["name"] == "Bailiff's Arsekickers" and me["abbrev"] == "BMF"
    assert (me["points"], opp["points"]) == (109.0, 92.0)
    assert len(me["starters"]) == 9 and len(opp["starters"]) == 9
    assert sum(p["points"] for p in me["starters"]) == me["points"]
    assert sum(p["points"] for p in opp["starters"]) == opp["points"]
    burrow = me["starters"][0]
    assert (burrow["slot"], burrow["short"], burrow["team"], burrow["points"]) == ("QB", "BURROW", "CIN", 36.0)
    assert burrow["projected"] == pytest.approx(24.2, abs=0.1)
    assert [p["slot"] for p in me["starters"]] == ["QB", "RB", "R/W", "R/W", "WR", "W/T", "TE", "DST", "K"]


def test_upcoming_week_has_zero_points_and_projections():
    m = parse_matchup(FIX["weeks"]["8"], 14, 8)
    assert m["final"] is False
    assert m["me"]["points"] == 0.0
    assert m["me"]["projected"] and m["me"]["projected"] > 50
    assert all(p["projected"] is not None for p in m["me"]["starters"])


def test_wrong_team_is_a_clear_error():
    with pytest.raises(ProviderError):
        parse_matchup(FIX["weeks"]["7"], 99, 7)


def test_short_names():
    assert short_name("Jaxon Smith-Njigba") == "SMITH-NJIGBA"
    assert short_name("Marvin Harrison Jr.") == "HARRISON"
    assert short_name("Patriots D/ST") == "PATRIOTS"
    assert short_name("James Cook III") == "COOK"


class _Resp:
    def __init__(self, code, body):
        self.status_code, self._body = code, body

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class _Session:
    def __init__(self, *responses):
        self.responses, self.calls = list(responses), []

    def get(self, url, **kw):
        self.calls.append((url, kw))
        return self.responses.pop(0)


def _provider(session):
    return EspnProvider("265444", 2026, 14, {"espn_s2": "s2", "swid": "{X}"}, session=session)


def test_request_is_filtered_to_my_game_with_cookies():
    s = _Session(_Resp(200, FIX["weeks"]["7"]))
    m = _provider(s).fetch_matchup(7, league_name="Urbandale Jaycees")
    url, kw = s.calls[0]
    assert url.endswith("/seasons/2026/segments/0/leagues/265444")
    assert ("scoringPeriodId", 7) in kw["params"]
    assert json.loads(kw["headers"]["X-Fantasy-Filter"]) == {
        "schedule": {"filterMatchupPeriodIds": {"value": [7]}, "filterTeamIds": {"value": [14]}}}
    assert kw["cookies"] == {"espn_s2": "s2", "SWID": "{X}"}
    assert m["league_name"] == "Urbandale Jaycees" and len(s.calls) == 1


def test_bad_cookies_raise_auth_error():
    with pytest.raises(AuthError):
        _provider(_Session(_Resp(401, {}))).fetch_matchup(7)


def test_filter_ignored_falls_back_to_unfiltered():
    empty = {"schedule": [], "teams": []}
    s = _Session(_Resp(200, empty), _Resp(200, FIX["weeks"]["7"]))
    assert _provider(s).fetch_matchup(7)["me"]["points"] == 109.0
    assert "X-Fantasy-Filter" not in s.calls[1][1]["headers"]


def test_settings_carry_team_names_for_lean_matchups():
    s = parse_settings(FIX["settings"])
    lean = json.loads(json.dumps(FIX["weeks"]["7"]))
    names = {t["id"]: t for t in lean.pop("teams")}
    teams = {tid: {"name": t["name"], "abbrev": t["abbrev"], "record": "5-2"} for tid, t in names.items()}
    m = parse_matchup(lean, 14, 7, teams=teams)
    assert m["me"]["abbrev"] == "BMF" and m["me"]["record"] == "5-2"
    assert isinstance(s["teams"], dict)


def test_lean_views_without_rosters_fall_back_to_full_once():
    no_rosters = json.loads(json.dumps(FIX["weeks"]["7"]))
    for side in ("home", "away"):
        no_rosters["schedule"][0][side].pop("rosterForCurrentScoringPeriod")
    s = _Session(_Resp(200, no_rosters), _Resp(200, FIX["weeks"]["7"]), _Resp(200, FIX["weeks"]["7"]))
    p = _provider(s)
    assert p.fetch_matchup(7)["me"]["points"] == 109.0
    views = [[v for k, v in c[1]["params"] if k == "view"] for c in s.calls]
    assert views[0] == ["mMatchupScore", "mScoreboard"]
    assert "mBoxscore" in views[1]
    p.fetch_matchup(7)
    assert "mBoxscore" in [v for k, v in s.calls[2][1]["params"] if k == "view"]


def test_same_last_name_gets_first_name_prefix():
    from providers.base import disambiguate
    ps = [{"id": 1, "name": "Mike Washington Jr.", "short": "WASHINGTON"},
          {"id": 2, "name": "Malik Washington", "short": "WASHINGTON"},
          {"id": 3, "name": "Joe Burrow", "short": "BURROW"},
          {"id": 4, "name": "Jaylen Smith", "short": "SMITH"},
          {"id": 5, "name": "Tyler Smith", "short": "SMITH"}]
    disambiguate(ps)
    assert [p["short"] for p in ps] == ["MI.WASHINGTON", "MA.WASHINGTON", "BURROW", "J.SMITH", "T.SMITH"]


def test_filtered_reply_without_my_game_retries_unfiltered():
    other = json.loads(json.dumps(FIX["weeks"]["7"]))
    for g in other["schedule"]:
        for side in ("home", "away"):
            if (g.get(side) or {}).get("teamId") == 14:
                g[side]["teamId"] = 99
    s = _Session(_Resp(200, other), _Resp(200, FIX["weeks"]["7"]))
    assert _provider(s).fetch_matchup(7)["me"]["points"] == 109.0
    assert "X-Fantasy-Filter" not in s.calls[1][1]["headers"]


def test_string_team_id_is_sent_as_a_number():
    s = _Session(_Resp(200, FIX["weeks"]["7"]))
    EspnProvider("265444", 2026, "14", {}, session=s).fetch_matchup(7)
    flt = json.loads(s.calls[0][1]["headers"]["X-Fantasy-Filter"])
    assert flt["schedule"]["filterTeamIds"] == {"value": [14]}
    assert s.calls[0][1]["cookies"] is None


def test_league_info_marks_the_signed_in_users_team():
    from providers.espn import league_info
    data = json.loads(json.dumps(FIX["settings"]))
    data["seasonId"] = 2026
    data["teams"] = [
        {"id": 3, "name": "Other Guys", "abbrev": "OG", "owners": ["{AAAA-1}"]},
        {"id": 14, "location": "Big", "nickname": "Money", "abbrev": "BMF",
         "owners": ["{bbbb-2}"], "record": {"overall": {"wins": 5, "losses": 2}}},
    ]
    info = league_info(data, "{BBBB-2}")
    assert info["season"] == 2026 and info["name"] == parse_settings(FIX["settings"])["name"]
    assert [t["id"] for t in info["teams"] if t["mine"]] == [14]
    assert info["teams"][1]["name"] == "Big Money" and info["teams"][1]["record"] == "5-2"
    assert not any(t["mine"] for t in league_info(data, "")["teams"])
