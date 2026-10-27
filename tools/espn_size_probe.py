#!/usr/bin/env python3
"""Measure ESPN fantasy response sizes for each view/filter variant.

Run on a Pi from the repo root:
    python3 tools/espn_size_probe.py            # uses the first ESPN league in config
    python3 tools/espn_size_probe.py 265444 14  # league id, team id

Prints KB per variant and whether my matchup's rosters came back, so we can
pick the smallest request that still has everything the screens need.
"""

import json
import os
import sys
import time

import requests

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
URL = ("https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/"
       "{season}/segments/0/leagues/{league}")


def load(path):
    try:
        with open(os.path.join(ROOT, path)) as f:
            return json.load(f)
    except (OSError, ValueError):
        return {}


def main():
    cfg = load("config/config.json").get("fantasy-leagues", {})
    secrets = load("config/config_secrets.json").get("fantasy-leagues", {})
    acct = secrets.get("accounts", {}).get("espn") or cfg.get("accounts", {}).get("espn") or {}
    league = next((l for l in cfg.get("leagues", []) if l.get("provider", "espn") == "espn"), {})
    league_id = sys.argv[1] if len(sys.argv) > 1 else str(league.get("league_id", "265444"))
    team_id = int(sys.argv[2]) if len(sys.argv) > 2 else int(league.get("team_id", 14))
    season = int(league.get("season") or time.strftime("%Y"))
    cookies = {"espn_s2": acct.get("espn_s2", ""), "SWID": acct.get("swid", "")}
    url = URL.format(season=season, league=league_id)

    st = requests.get(url, params=[("view", "mStatus")], cookies=cookies, timeout=20).json()
    week = int(st["status"]["currentMatchupPeriod"])
    print(f"league {league_id} team {team_id} season {season}: current matchup period {week}\n")

    def flt(**extra):
        f = {"schedule": {"filterMatchupPeriodIds": {"value": [week]}}}
        f["schedule"].update(extra)
        return {"X-Fantasy-Filter": json.dumps(f)}

    variants = [
        ("settings: mSettings+mStatus+mTeam", ["mSettings", "mStatus", "mTeam"], None, False),
        ("OLD full 4 views + filter", ["mMatchup", "mMatchupScore", "mBoxscore", "mScoreboard"], flt(), True),
        ("LEAN mMatchupScore+mScoreboard + filter", ["mMatchupScore", "mScoreboard"], flt(), True),
        ("LEAN, no filter", ["mMatchupScore", "mScoreboard"], None, True),
        ("mMatchupScore+mBoxscore + filter", ["mMatchupScore", "mBoxscore"], flt(), True),
        ("mBoxscore only + filter", ["mBoxscore"], flt(), True),
        ("LEAN + filterTeamIds", ["mMatchupScore", "mScoreboard"],
         flt(filterTeamIds={"value": [team_id]}), True),
    ]
    print(f"{'variant':44}{'HTTP':>5}{'KB':>8}{'games':>7}  rosters  starters(me/opp)")
    for name, views, headers, roster_check in variants:
        params = [("view", v) for v in views] + [("scoringPeriodId", week)]
        t0 = time.time()
        try:
            r = requests.get(url, params=params, headers=headers or {}, cookies=cookies, timeout=60)
        except requests.RequestException as exc:
            print(f"{name:44} error {exc}")
            continue
        kb = len(r.content) // 1024
        games, rosters, counts = "-", "-", ""
        if r.ok and roster_check:
            data = r.json()
            sched = data.get("schedule") or []
            games = str(len(sched))
            mine = [g for g in sched if g.get("matchupPeriodId") == week and team_id in (
                (g.get("home") or {}).get("teamId"), (g.get("away") or {}).get("teamId"))]
            if mine:
                g = mine[0]
                n = [len(((g.get(s) or {}).get("rosterForCurrentScoringPeriod") or {}).get("entries") or [])
                     for s in ("home", "away")]
                rosters = "yes" if all(n) else "NO"
                counts = f"{n[0]}/{n[1]}"
            else:
                rosters = "no game"
        print(f"{name:44}{r.status_code:>5}{kb:>8}{games:>7}  {rosters:8} {counts}  ({time.time() - t0:.1f}s)")


if __name__ == "__main__":
    main()
