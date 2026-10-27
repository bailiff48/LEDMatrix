"""Fantasy tab API: screens/presets, leagues, ESPN sign-in. Run from the repo
root: python -m pytest tests/test_fantasy_config_api.py"""

import json
import os
import sys
from datetime import date

import pytest
from flask import Flask

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from src import fantasy_config_api as api  # noqa: E402

S2 = "A" * 180 + "%2B" + "b" * 40
SWID = "{1A2B3C4D-1A2B-1A2B-1A2B-1A2B3C4D5E6F}"


# ---------------------------------------------------------------- pure

def test_presets_nest_and_hide_the_sleeper_screen():
    cfg = {}
    ess, std, every = (set(api.preset_ids(t, cfg)) for t in api.TIERS)
    assert ess == {"my_summary", "my_matchup", "my_roster", "my_alerts",
                   "nfl_leaderboard", "injury_report"}
    assert ess < std and std == every  # no blitz league: Everything == Standard
    assert "blitz_league" not in every and "season_race" in std
    cfg = {"fantasy-blitz": {"league": {"provider": "sleeper", "league_id": "123"}}}
    assert "blitz_league" in api.preset_ids("everything", cfg)
    assert "blitz_league" not in api.preset_ids("standard", cfg)


def test_apply_screens_writes_both_plugin_shapes():
    cfg = {"fantasy-blitz": {"enabled": True, "screens": {"leaderboard": {"duration": 12}}}}
    on = set(api.preset_ids("essentials", cfg))
    api.apply_screens(cfg, {sid: sid in on for sid in api.SCREEN_IDS})
    fl, fb = cfg["fantasy-leagues"], cfg["fantasy-blitz"]
    assert fl["enabled"] is True and fl["screens"] == {
        "summary": True, "matchup": True, "roster": True, "alerts": True}
    assert fb["enabled"] is True
    assert fb["screens"]["leaderboard"] == {"duration": 12, "enabled": True}  # duration kept
    assert fb["screens"]["player_card"] == {"enabled": False}
    assert api.detect_preset(api.screen_states(cfg), cfg) == "essentials"


def test_turning_everything_off_disables_both_plugins():
    cfg = {}
    api.apply_screens(cfg, {sid: False for sid in api.SCREEN_IDS})
    assert cfg["fantasy-leagues"]["enabled"] is False
    assert cfg["fantasy-blitz"]["enabled"] is False
    assert api.detect_preset(api.screen_states(cfg), cfg) == "off"


def test_partial_update_keeps_other_screens_and_custom_is_detected():
    cfg = {}
    api.apply_screens(cfg, {sid: True for sid in api.preset_ids("standard", cfg)})
    api.apply_screens(cfg, {"hot_pickups": False})
    states = api.screen_states(cfg)
    assert states["position_kings"] and not states["hot_pickups"]
    assert api.detect_preset(states, cfg) == "custom"


def test_screen_states_of_a_hand_written_config():
    cfg = {"fantasy-leagues": {"enabled": True, "leagues": []},           # no screens: defaults on
           "fantasy-blitz": {"enabled": False, "screens": {}}}             # plugin off: all off
    s = api.screen_states(cfg)
    assert s["my_summary"] and s["my_alerts"] and not s["nfl_leaderboard"]


@pytest.mark.parametrize("text,expected", [
    ("https://fantasy.espn.com/football/team?leagueId=265444&teamId=14&seasonId=2026",
     {"league_id": "265444", "team_id": 14, "season": 2026}),
    ("fantasy.espn.com/football/league?leagueId=265444",
     {"league_id": "265444", "team_id": None, "season": None}),
    ("https://fantasy.espn.com/football/league/standings?LEAGUEID=99&seasonId=2025",
     {"league_id": "99", "team_id": None, "season": 2025}),
    ("  265444 ", {"league_id": "265444", "team_id": None, "season": None}),
])
def test_parse_espn_url(text, expected):
    assert api.parse_espn_url(text) == expected


@pytest.mark.parametrize("text", ["https://sleeper.com/leagues/123", "https://fantasy.espn.com/football/",
                                  "hello"])
def test_parse_espn_url_rejects(text):
    with pytest.raises(ValueError):
        api.parse_espn_url(text)


def test_cookie_cleanup():
    assert api.normalize_swid("1a2b3c4d-1a2b-1a2b-1a2b-1a2b3c4d5e6f") == SWID
    assert api.normalize_swid(f"SWID={SWID};") == SWID
    assert api.normalize_s2(f' espn_s2="{S2}" ') == S2
    with pytest.raises(ValueError):
        api.normalize_swid("{not-a-swid}")
    with pytest.raises(ValueError):
        api.normalize_s2("short")
    with pytest.raises(ValueError):
        api.normalize_s2("A" * 120 + " " + "B" * 20)


def test_espn_status_warns_a_month_before_a_year():
    sec = {"fantasy-leagues": {"accounts": {"espn": {"espn_s2": S2, "swid": SWID,
                                                     "saved_at": "2025-11-20"}}}}
    st = api.espn_status(sec, date(2026, 10, 27))
    assert st["connected"] and st["expires_soon"] and st["age_days"] == 341
    assert S2 not in json.dumps(st) and st["swid"].startswith("{1A2B")
    assert not api.espn_status(sec, date(2026, 6, 1))["expires_soon"]
    assert api.espn_status({}, date(2026, 6, 1))["connected"] is False


def test_add_league_updates_same_league_instead_of_duplicating():
    cfg = {}
    api.add_league(cfg, {"provider": "espn", "league_id": "265444", "team_id": 14, "name": "UJ"})
    api.add_league(cfg, {"provider": "espn", "league_id": "265444", "team_id": 3})
    rows = api.league_rows(cfg)
    assert len(rows) == 1 and rows[0]["team_id"] == 3 and rows[0]["name"] == "UJ"
    assert api.remove_league(cfg, rows[0]["key"]) and api.league_rows(cfg) == []
    assert not api.remove_league(cfg, "espn:1:1")


# ---------------------------------------------------------------- routes

class FakeProvider:
    behaviour = "ok"

    def __init__(self, provider, league_id, season, team_id, creds):
        self.args = (provider, league_id, season, team_id, creds)
        FakeProvider.last = self

    def fetch_league_info(self):
        mod = api.load_providers()
        if FakeProvider.behaviour == "auth":
            raise mod.AuthError("401")
        if FakeProvider.behaviour == "missing":
            raise mod.ProviderError("404")
        if FakeProvider.behaviour == "down":
            raise ConnectionError("timed out")
        return {"name": "Urbandale Jaycees", "season": 2026, "week": 8,
                "teams": [{"id": 3, "name": "Other", "abbrev": "OTH", "record": "4-3", "mine": False},
                          {"id": 14, "name": "Big Money", "abbrev": "BMF", "record": "5-2",
                           "mine": FakeProvider.behaviour == "ok"}]}


@pytest.fixture
def env(tmp_path):
    cfg_dir = tmp_path / "config"
    cfg_dir.mkdir()
    (cfg_dir / "config.json").write_text(json.dumps({
        "timezone": "America/Chicago",
        "fantasy-blitz": {"enabled": True, "scoring_format": "ppr"},
    }))
    (cfg_dir / "config_secrets.json").write_text(json.dumps({"weather": {"api_key": "k"}}))
    os.chmod(cfg_dir / "config_secrets.json", 0o640)
    restarts = []
    FakeProvider.behaviour = "ok"
    app = Flask(__name__)
    api.register_fantasy_config_routes(
        app, config_path=str(cfg_dir / "config.json"),
        secrets_path=str(cfg_dir / "config_secrets.json"),
        restart=lambda: (restarts.append(1) or (True, "restarting")),
        provider_factory=lambda *a: FakeProvider(*a),
        today=lambda: date(2026, 10, 27))
    client = app.test_client()
    client.restarts = restarts
    client.cfg = lambda: json.loads((cfg_dir / "config.json").read_text())
    client.secrets = lambda: json.loads((cfg_dir / "config_secrets.json").read_text())
    client.secrets_mode = lambda: os.stat(cfg_dir / "config_secrets.json").st_mode & 0o777
    return client


def test_get_config_starts_from_blitz_defaults(env):
    r = env.get("/api/fantasy/config").get_json()
    assert r["ok"] and r["season"] == 2026 and r["leagues"] == []
    states = {s["id"]: s["on"] for s in r["screens"]}
    assert not states["my_summary"] and states["nfl_leaderboard"]
    assert r["preset"] == "custom" and r["espn"]["connected"] is False
    assert next(s for s in r["screens"] if s["id"] == "blitz_league")["available"] is False


def test_save_screens_restarts_and_round_trips(env):
    on = env.get("/api/fantasy/config").get_json()["presets"]["essentials"]
    r = env.post("/api/fantasy/screens", json={"screens": {s: s in on for s in api.SCREEN_IDS}})
    assert r.get_json()["restarted"] and env.restarts == [1]
    assert env.get("/api/fantasy/config").get_json()["preset"] == "essentials"
    cfg = env.cfg()
    assert cfg["fantasy-blitz"]["scoring_format"] == "ppr"  # untouched settings survive
    assert cfg["fantasy-leagues"]["screens"]["alerts"] is True
    assert env.post("/api/fantasy/screens", json={"screens": {"nope": True}}).status_code == 400


def test_save_without_apply_does_not_restart(env):
    env.post("/api/fantasy/screens", json={"screens": {"my_roster": True}, "apply": False})
    assert env.restarts == []


def test_add_flow_test_then_add(env):
    r = env.post("/api/fantasy/leagues/parse-url", json={
        "url": "https://fantasy.espn.com/football/team?leagueId=265444&teamId=14"}).get_json()
    assert r["league_id"] == "265444" and r["team_id"] == 14
    t = env.post("/api/fantasy/leagues/test", json={"provider": "espn", "league_id": "265444"}).get_json()
    assert t["ok"] and t["team"]["id"] == 14 and not t["needs_team"]   # found by SWID ownership
    assert FakeProvider.last.args[2] == 2026
    a = env.post("/api/fantasy/leagues/add", json={
        "provider": "espn", "league_id": "265444", "team_id": 14,
        "name": t["league_name"], "team_name": t["team"]["name"]}).get_json()
    assert a["ok"] and a["league"]["key"] == "espn:265444:14"
    cfg = env.cfg()
    assert cfg["fantasy-leagues"]["leagues"][0]["name"] == "Urbandale Jaycees"
    # the first league turns the league screens on so it shows up
    assert cfg["fantasy-leagues"]["enabled"] and cfg["fantasy-leagues"]["screens"]["summary"]
    t2 = env.post("/api/fantasy/leagues/test", json={"key": "espn:265444:14"}).get_json()
    assert t2["ok"] and t2["team"]["name"] == "Big Money"
    assert env.post("/api/fantasy/leagues/remove", json={"key": "espn:265444:14"}).get_json()["ok"]
    assert env.cfg()["fantasy-leagues"]["leagues"] == []


def test_test_errors_are_plain_words(env):
    FakeProvider.behaviour = "auth"
    r = env.post("/api/fantasy/leagues/test", json={"league_id": "265444", "team_id": 14}).get_json()
    assert not r["ok"] and r["needs_login"] and "private" in r["error"]
    FakeProvider.behaviour = "missing"
    assert "no league" in env.post("/api/fantasy/leagues/test", json={"league_id": "1"}).get_json()["error"]
    FakeProvider.behaviour = "down"
    assert "Couldn't reach" in env.post("/api/fantasy/leagues/test", json={"league_id": "1"}).get_json()["error"]
    FakeProvider.behaviour = "ok"
    r = env.post("/api/fantasy/leagues/test", json={"league_id": "265444", "team_id": 99}).get_json()
    assert not r["ok"] and len(r["teams"]) == 2
    assert env.post("/api/fantasy/leagues/add", json={"league_id": "265444"}).status_code == 400
    assert env.post("/api/fantasy/leagues/add", json={"provider": "yahoo", "league_id": "1",
                                                      "team_id": 1}).status_code == 400


def test_connect_espn_saves_secrets_only(env):
    r = env.post("/api/fantasy/espn/connect", json={"espn_s2": S2, "swid": SWID.lower()}).get_json()
    assert r["ok"] and r["espn"]["connected"] and r["espn"]["saved_at"] == "2026-10-27"
    assert S2 not in json.dumps(r)
    sec = env.secrets()
    assert sec["weather"] == {"api_key": "k"}  # other secrets kept
    assert sec["fantasy-leagues"]["accounts"]["espn"]["swid"] == SWID
    assert env.secrets_mode() == 0o640
    # a later config save must not copy the cookies into config.json
    env.post("/api/fantasy/screens", json={"screens": {"my_roster": True}})
    assert "accounts" not in env.cfg().get("fantasy-leagues", {})
    # and the cookies are used for tests
    env.post("/api/fantasy/leagues/test", json={"league_id": "265444"})
    assert FakeProvider.last.args[4] == {"espn_s2": S2, "swid": SWID}
    assert env.post("/api/fantasy/espn/disconnect", json={}).get_json()["espn"]["connected"] is False
    assert "espn" not in env.secrets()["fantasy-leagues"]["accounts"]


def test_connect_rejects_bad_or_refused_cookies(env):
    assert env.post("/api/fantasy/espn/connect", json={"espn_s2": "x", "swid": SWID}).status_code == 400
    env.post("/api/fantasy/leagues/add", json={"league_id": "265444", "team_id": 14, "apply": False})
    FakeProvider.behaviour = "auth"
    r = env.post("/api/fantasy/espn/connect", json={"espn_s2": S2, "swid": SWID})
    assert r.status_code == 400 and "didn't accept" in r.get_json()["error"]
    assert "fantasy-leagues" not in env.secrets()
