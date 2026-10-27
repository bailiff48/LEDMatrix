"""Plugin tests: polling, alerts, pages and pixel goldens, from the ESPN fixture.

Regenerate goldens after an intended visual change:
    FL_WRITE_GOLDENS=1 python3 -m pytest -q test_fantasy_leagues.py
"""

import copy
import json
import os
import sys
from datetime import datetime

import pytest
from PIL import Image, ImageChops

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)

import fl_render as R  # noqa: E402
import manager as M  # noqa: E402
from fl_state import AlertQueue, detect_alerts, in_game_window, normalize_league  # noqa: E402
from providers.espn import parse_matchup  # noqa: E402

FIX = json.load(open(os.path.join(HERE, "test/fixtures/espn_265444.json")))
GOLDEN_DIR = os.path.join(HERE, "test/goldens")
ESPN, YH, G = (200, 16, 46), (96, 1, 210), (40, 200, 80)
TEAMS = {14: {"name": "Bailiff's Arsekickers", "abbrev": "BMF", "record": "5-2"}}


# ----------------------------------------------------------------------
# fakes
# ----------------------------------------------------------------------

class Resp:
    def __init__(self, body, code=200):
        self.status_code, self._body = code, body
        self.content = json.dumps(body).encode()

    def json(self):
        return self._body

    def raise_for_status(self):
        if self.status_code >= 400:
            raise RuntimeError(self.status_code)


class FakeEspn:
    """Answers settings and matchup calls; ``bump`` adds points to a starter."""

    def __init__(self, week=8):
        self.week = week
        self.data = {7: copy.deepcopy(FIX["weeks"]["7"]), 8: copy.deepcopy(FIX["weeks"]["8"])}
        self.code = 200
        self.calls = []

    def bump(self, week, side_team, index, points):
        g = self.data[week]["schedule"][0]
        side = g["home"] if g["home"]["teamId"] == side_team else g["away"]
        e = side["rosterForCurrentScoringPeriod"]["entries"]
        starters = [x for x in e if x["lineupSlotId"] not in (20, 21)]
        starters[index]["playerPoolEntry"]["appliedStatTotal"] += points
        side["totalPoints"] = side.get("totalPoints", 0) + points

    def get(self, url, params=None, **kw):
        views = [v for k, v in params if k == "view"]
        self.calls.append(views)
        if self.code != 200:
            return Resp({}, self.code)
        if "mSettings" in views:
            return Resp(FIX["settings"])
        period = dict(params).get("scoringPeriodId")
        return Resp(self.data[int(period)])

    def close(self):
        pass


class FakeCache:
    def __init__(self):
        self.store = {}

    def get(self, key, max_age=300):
        v = self.store.get(key)
        return json.loads(v) if v else None

    def set(self, key, data):
        self.store[key] = json.dumps(data)


class FakeDM:
    def __init__(self, w=128, h=32):
        self.image = Image.new("RGB", (w, h))
        self.frames = 0

    def update_display(self):
        self.frames += 1


class FakePM:
    class config_manager:  # noqa: N801
        @staticmethod
        def get_timezone():
            return "America/Chicago"


LEAGUE = {"provider": "espn", "league_id": "265444", "team_id": 14, "season": 2026}


def make(fake=None, cache=None, now=None, **extra):
    cfg = {"enabled": True, "leagues": [dict(LEAGUE)],
           "accounts": {"espn": {"espn_s2": "s2", "swid": "{X}"}}, "recap_days": []}
    cfg.update(extra)
    p = M.FantasyLeaguesPlugin("fantasy-leagues", cfg, FakeDM(), cache or FakeCache(), FakePM())
    p.session = fake or FakeEspn()
    for s in p.slots:
        s.provider.session = p.session
    return p


@pytest.fixture
def clock(monkeypatch):
    """A controllable time.time() for the plugin (Sunday 1pm Central)."""
    t = {"now": datetime(2026, 11, 1, 13, 0).timestamp()}
    monkeypatch.setattr(M.time, "time", lambda: t["now"])
    return t


# ----------------------------------------------------------------------
# polling
# ----------------------------------------------------------------------

def test_first_update_reads_settings_then_lean_matchup(clock):
    p = make()
    p.update()
    s = p.slots[0]
    assert s.settings["name"] == "Urbandale Jaycees" and s.week == 8
    assert p.session.calls[0] == ["mSettings", "mStatus", "mTeam"]
    assert p.session.calls[1] == ["mMatchupScore", "mScoreboard"]
    assert s.matchup["me"]["abbrev"] == "BMF" and s.error is None


def test_poll_is_fast_during_games_and_slow_otherwise(clock):
    p = make()
    p.update()
    assert p.get_update_interval() == 60  # Sunday 1pm
    clock["now"] = datetime(2026, 11, 3, 10, 0).timestamp()  # Tuesday morning
    p.slots[0].last_change_at = 0
    assert p.get_update_interval() == 900


def test_game_windows():
    assert in_game_window(datetime(2026, 11, 1, 12, 0))       # Sunday noon
    assert in_game_window(datetime(2026, 11, 2, 20, 15))      # Monday night
    assert in_game_window(datetime(2026, 10, 30, 0, 20))      # Thursday game past midnight
    assert not in_game_window(datetime(2026, 11, 3, 20, 0))   # Tuesday night
    assert not in_game_window(datetime(2026, 11, 1, 7, 0))    # Sunday early


def test_login_failure_keeps_last_scores_and_says_why(clock):
    p = make()
    p.update()
    p.session.code = 401
    clock["now"] += 3600 * 13  # settings due again
    p.update()
    s = p.slots[0]
    assert s.auth_error and s.error == "ESPN LOGIN EXPIRED"
    assert s.matchup is not None
    rows = p._pages(M.MODE_SUMMARY, clock["now"])[0]
    assert rows[0]["error"] == "ESPN LOGIN EXPIRED" and rows[0]["matchup"] is not None


# ----------------------------------------------------------------------
# alerts
# ----------------------------------------------------------------------

def test_starter_scoring_six_raises_one_alert_and_takes_over(clock):
    p = make()
    p.update()
    assert not p.has_live_content()
    p.session.bump(8, 14, 0, 6.0)  # a TD for the first starter ESPN lists
    clock["now"] += 61
    p.update()
    assert p.has_live_content()
    assert p.display(force_clear=True, display_mode=M.MODE_LIVE) is True
    a = p.queue.current
    assert a["short"] == "HARRISON" and a["leagues"][0]["side"] == "me"
    assert a["leagues"][0]["delta"] == 6.0
    clock["now"] += 9
    assert p.display(display_mode=M.MODE_LIVE) is False
    assert not p.has_live_content()


def test_small_gains_and_opponent_toggle(clock):
    p = make(leagues=[dict(LEAGUE, alerts={"opponent": False})])
    p.update()
    opp_team = p.slots[0].matchup["opp"]["team_id"]
    p.session.bump(8, 14, 1, 3.0)          # under threshold
    p.session.bump(8, opp_team, 0, 7.0)    # opponent, alerts off
    clock["now"] += 61
    p.update()
    assert not p.has_live_content()


def test_restart_from_old_cache_does_not_alert_on_hours_of_points(clock):
    cache = FakeCache()
    p = make(cache=cache)
    p.update()
    fake = p.session
    fake.bump(8, 14, 0, 30.0)
    clock["now"] += 3 * 3600
    p2 = make(fake=fake, cache=cache)
    assert p2.slots[0].matchup is not None  # shows cached scores at once
    p2.update()
    assert not p2.has_live_content()


def test_same_player_in_two_leagues_is_one_alert():
    cfg_a = normalize_league({"provider": "espn", "league_id": "1", "team_id": 14})
    cfg_b = normalize_league({"provider": "yahoo", "league_id": "2", "team_id": 3})
    old = parse_matchup(FIX["weeks"]["7"], 14, 7, teams=TEAMS)
    new = copy.deepcopy(old)
    new["final"] = False
    old["final"] = False
    new["me"]["starters"][0]["points"] += 6
    q = AlertQueue()
    for a in detect_alerts(cfg_a, old, new, 6, now=100) + detect_alerts(cfg_b, old, new, 6, now=130):
        q.add(a)
    assert len(q.pending) == 1
    assert [lg["league"] for lg in q.pending[0]["leagues"]] == ["ESPN", "YHOO"]


# ----------------------------------------------------------------------
# pages and durations
# ----------------------------------------------------------------------

def test_roster_pages_are_balanced_and_rotate(clock):
    p = make()
    p.update()
    pages = p._pages(M.MODE_ROSTER, clock["now"])
    assert [len(pg[4]) for pg in pages] == [3, 3, 3]
    assert p.display(force_clear=True, display_mode=M.MODE_ROSTER)
    assert p.get_display_duration() == 15
    first = p.display_manager.image.tobytes()
    clock["now"] += 5
    p.display(display_mode=M.MODE_ROSTER)
    assert p.display_manager.image.tobytes() != first


def test_recap_day_shows_last_weeks_final(clock):
    clock["now"] = datetime(2026, 10, 27, 12, 0).timestamp()  # Tuesday
    p = make(recap_days=["tue", "wed"])
    p.update()
    s = p.slots[0]
    assert s.recap["week"] == 7 and s.recap["final"]
    _, m = p._pages(M.MODE_MATCHUP, clock["now"])[0]
    assert m["week"] == 7 and m["me"]["points"] == 109.0


def test_nothing_to_show_skips_every_screen(clock):
    p = make()
    for mode in M.MODES:
        assert p.display(force_clear=True, display_mode=mode) is False


def test_unchanged_frame_is_not_redrawn(clock):
    p = make()
    p.update()
    p.display(force_clear=True, display_mode=M.MODE_MATCHUP)
    p.display(display_mode=M.MODE_MATCHUP)
    assert p.display_manager.frames == 1


def test_secrets_are_not_in_get_info():
    p = make()
    assert "accounts" not in json.dumps(p.get_info()["config"])


# ----------------------------------------------------------------------
# goldens
# ----------------------------------------------------------------------

def _screens():
    wk7 = parse_matchup(FIX["weeks"]["7"], 14, 7, league_name="Urbandale Jaycees", teams=TEAMS)
    wk8 = parse_matchup(FIX["weeks"]["8"], 14, 8, league_name="Urbandale Jaycees", teams=TEAMS)
    live = copy.deepcopy(wk8)
    for i, pl in enumerate(live["me"]["starters"]):
        pl["points"] = [18, 7, 12, 0, 9, 4, 0, 3, 5][i]
    live["me"].update(points=58.0, projected=104.3)
    live["opp"].update(points=61.0, projected=97.1)
    live["me"]["starters"][1]["injury"] = "QUESTIONABLE"
    lg = {"league": "ESPN", "host_color": ESPN, "team_color": G, "side": "me", "delta": 7.0,
          "points": 19.0, "me": ("BMF", 65.0), "opp": ("RT3", 61.0)}
    alert = {"key": "x", "at": 0, "short": "SMITH-NJIGBA", "name": "Jaxon Smith-Njigba",
             "pos": "WR", "team": "SEA", "leagues": [lg, dict(lg, league="YHOO", host_color=YH)]}
    return {
        "summary_live": R.summary(128, 32, [
            {"label": "ESPN", "host_color": ESPN, "team_color": G, "matchup": live},
            {"label": "YHOO", "host_color": YH, "team_color": G, "matchup": None,
             "error": "YHOO LOGIN EXPIRED"}], "MY LEAGUES"),
        "matchup_live": R.matchup(128, 32, live, "ESPN", ESPN, G),
        "matchup_final": R.matchup(128, 32, wk7, "ESPN", ESPN, G),
        "roster_live": R.roster(128, 32, live["me"], live["me"]["starters"][:3], "ESPN", ESPN, G, "1/3"),
        "alert": R.alert(128, 32, alert, 2.0),
    }


@pytest.mark.parametrize("name", list(_screens()))
def test_golden(name):
    img = _screens()[name]
    path = os.path.join(GOLDEN_DIR, f"{name}.png")
    if os.environ.get("FL_WRITE_GOLDENS"):
        os.makedirs(GOLDEN_DIR, exist_ok=True)
        img.save(path)
    golden = Image.open(path).convert("RGB")
    assert ImageChops.difference(img, golden).getbbox() is None, f"{name} differs from golden"
