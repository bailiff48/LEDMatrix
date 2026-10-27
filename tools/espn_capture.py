#!/usr/bin/env python3
"""Save ESPN fantasy responses for one league, to build and test against.

Reads espn_s2 / SWID from config/config_secrets.json at
fantasy-leagues.accounts.espn. Writes espn_capture_<league>.json with the
current and previous scoring week. Members' real names are removed; team
names and display names stay.

Usage (from ~/LEDMatrix):  python3 tools/espn_capture.py 265444 2026
"""
import json
import sys
import requests

URL = "https://lm-api-reads.fantasy.espn.com/apis/v3/games/ffl/seasons/{season}/segments/0/leagues/{league}"
STRIP = {"firstName", "lastName", "notificationSettings", "email"}


def scrub(obj):
    if isinstance(obj, dict):
        return {k: scrub(v) for k, v in obj.items() if k not in STRIP}
    if isinstance(obj, list):
        return [scrub(v) for v in obj]
    return obj


def get(league, season, cookies, views, week=None):
    params = [("view", v) for v in views]
    if week:
        params.append(("scoringPeriodId", week))
    r = requests.get(URL.format(season=season, league=league), params=params,
                     cookies=cookies, timeout=20, headers={"User-Agent": "ledmatrix-capture"})
    print(f"  {', '.join(views)} week={week}: HTTP {r.status_code}, {len(r.content)} bytes")
    r.raise_for_status()
    return r.json()


def main():
    league = sys.argv[1] if len(sys.argv) > 1 else "265444"
    season = int(sys.argv[2]) if len(sys.argv) > 2 else 2026
    secrets = json.load(open("config/config_secrets.json"))
    acct = (((secrets.get("fantasy-leagues") or {}).get("accounts") or {}).get("espn") or {})
    if not acct.get("espn_s2") or not acct.get("swid"):
        sys.exit("No espn_s2/swid at fantasy-leagues.accounts.espn in config/config_secrets.json")
    cookies = {"espn_s2": acct["espn_s2"], "SWID": acct["swid"]}

    status = get(league, season, cookies, ["mStatus", "mSettings"])
    st = status.get("status") or {}
    latest = int(st.get("latestScoringPeriod") or status.get("scoringPeriodId") or 1)
    print(f"  league '{(status.get('settings') or {}).get('name')}', latest scoring period {latest}")

    out = {"league_id": league, "season": season, "status": scrub(status), "weeks": {}}
    for week in sorted({max(1, latest - 1), latest}):
        out["weeks"][str(week)] = {
            "rosters": scrub(get(league, season, cookies, ["mTeam", "mRoster"], week)),
            "matchups": scrub(get(league, season, cookies,
                                  ["mMatchup", "mMatchupScore", "mBoxscore", "mScoreboard"], week)),
        }
    path = f"espn_capture_{league}.json"
    json.dump(out, open(path, "w"))
    print(f"Saved {path}")


if __name__ == "__main__":
    main()
