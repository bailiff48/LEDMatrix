"""
API routes for the Fantasy tab (fantasy-leagues + fantasy-blitz plugins).

One Pi-wide screen selection covers both plugins. Leagues and the ESPN
sign-in belong to fantasy-leagues; the ESPN cookies live in
config/config_secrets.json under fantasy-leagues.accounts.espn and never
leave the Pi (the page only ever sees a masked SWID).

Endpoints:
    GET  /api/fantasy/config              screens, preset, leagues, ESPN sign-in status
    POST /api/fantasy/screens             {"screens": {id: bool}} save the screen selection
    POST /api/fantasy/leagues/parse-url   {"url"} -> league_id, team_id, season from an ESPN link
    POST /api/fantasy/leagues/test        {"provider","league_id","team_id"?} or {"key"}: ask the site
    POST /api/fantasy/leagues/add         {"provider","league_id","team_id","name"?,"team_name"?}
    POST /api/fantasy/leagues/remove      {"key"}
    POST /api/fantasy/espn/connect        {"espn_s2","swid"} check and save the ESPN sign-in
    POST /api/fantasy/espn/disconnect     forget the ESPN sign-in

Every save restarts the ledmatrix service (like the Restart Service button)
unless the request says "apply": false.
"""

import importlib.util
import json
import logging
import os
import re
import subprocess
import sys
import threading
from datetime import date, datetime
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import parse_qs, urlparse

from flask import jsonify, request

logger = logging.getLogger(__name__)

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LEAGUES = "fantasy-leagues"
BLITZ = "fantasy-blitz"

ESSENTIALS, STANDARD, EVERYTHING = "essentials", "standard", "everything"
TIERS = [ESSENTIALS, STANDARD, EVERYTHING]

#: Every fantasy screen on the panel, in the order the page lists them.
#: (id, plugin, key in that plugin's "screens", label, help, tier)
SCREENS: List[Tuple[str, str, str, str, str, str]] = [
    ("my_summary", LEAGUES, "summary", "My leagues at a glance",
     "Every league on one screen: you against this week's opponent.", ESSENTIALS),
    ("my_matchup", LEAGUES, "matchup", "My matchup",
     "Big scores, records and projections, one league per page.", ESSENTIALS),
    ("my_roster", LEAGUES, "roster", "My starters",
     "Your lineup with live points, a few players per page.", ESSENTIALS),
    ("my_alerts", LEAGUES, "alerts", "Big plays by my players",
     "Pops up when one of your starters (or your opponent's) scores big. "
     "It interrupts the rotation, so it takes no rotation time.", ESSENTIALS),
    ("nfl_leaderboard", BLITZ, "leaderboard", "NFL top scorers",
     "This week's top fantasy scorers across the NFL.", ESSENTIALS),
    ("injury_report", BLITZ, "injury_report", "Injury report",
     "Injury tags on the players who matter, before kickoff.", ESSENTIALS),
    ("player_cards", BLITZ, "player_card", "Top scorer cards",
     "This week's best players as collectible cards.", STANDARD),
    ("hot_pickups", BLITZ, "hot_pickups", "Hot waiver pickups",
     "The free agents everyone is adding (hidden while games are on).", STANDARD),
    ("position_kings", BLITZ, "position_kings", "Best at each position",
     "The top scorer at every position on one screen.", STANDARD),
    ("weekly_awards", BLITZ, "weekly_awards", "Weekly awards",
     "Tuesday and Wednesday trophies: MVP, bust, biggest boom, waiver hero.", STANDARD),
    ("dud_alert", BLITZ, "dud_alert", "Biggest busts",
     "Players who fell furthest short of their projection.", STANDARD),
    ("nfl_big_plays", BLITZ, "big_play", "Big plays across the NFL",
     "Pops up for any big fantasy play in the league while games are on.", STANDARD),
    ("season_race", BLITZ, "season_race", "Season scoring race",
     "The season's top fantasy scorers so far.", STANDARD),
    ("watchlist", BLITZ, "watchlist", "Watchlist players",
     "Live points for players on the Fantasy Blitz watchlist. Shows nothing "
     "until players are added to it.", STANDARD),
    ("blitz_league", BLITZ, "league_matchup", "Sleeper league matchups",
     "Head-to-head scores from a Sleeper league set up in Fantasy Blitz.", EVERYTHING),
]
SCREEN_IDS = [s[0] for s in SCREENS]
LIVE_ONLY = {"my_alerts", "nfl_big_plays"}  # take over the panel; no rotation time

ESPN_COOKIE_DAYS = 365   # espn_s2 lasts about a year from sign-in
ESPN_WARN_DAYS = 330     # start warning about a month before
SWID_RE = re.compile(r"^\{[0-9A-F]{8}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{4}-[0-9A-F]{12}\}$")

_lock = threading.Lock()


# ----------------------------------------------------------------------
# pure helpers (unit-tested)
# ----------------------------------------------------------------------

def default_season(today: Optional[date] = None) -> int:
    """NFL season year: Jan-Feb playoffs still belong to last year's season."""
    today = today or date.today()
    return today.year if today.month >= 3 else today.year - 1


def screen_available(screen_id: str, config: Dict[str, Any]) -> bool:
    if screen_id == "blitz_league":
        lg = (config.get(BLITZ) or {}).get("league") or {}
        return str(lg.get("provider") or "none") not in ("", "none") and bool(lg.get("league_id"))
    return True


def preset_ids(tier: str, config: Dict[str, Any]) -> List[str]:
    """Screen ids a preset turns on (only those available on this Pi)."""
    if tier == "off":
        return []
    upto = TIERS[: TIERS.index(tier) + 1]
    return [s[0] for s in SCREENS if s[5] in upto and screen_available(s[0], config)]


def screen_states(config: Dict[str, Any]) -> Dict[str, bool]:
    """What each screen is set to now (a plugin that is off shows all off)."""
    out = {}
    for sid, plugin, key, *_ in SCREENS:
        section = config.get(plugin) or {}
        if not section.get("enabled", False):
            out[sid] = False
            continue
        value = (section.get("screens") or {}).get(key)
        if isinstance(value, dict):  # fantasy-blitz: {"enabled": bool, "duration": int}
            value = value.get("enabled", True)
        out[sid] = True if value is None else bool(value)  # schema default is on
    return out


def detect_preset(states: Dict[str, bool], config: Dict[str, Any]) -> str:
    on = {sid for sid, v in states.items() if v and screen_available(sid, config)}
    for tier in ["off"] + TIERS:
        if on == set(preset_ids(tier, config)):
            return tier
    return "custom"


def apply_screens(config: Dict[str, Any], wanted: Dict[str, Any]) -> Dict[str, Any]:
    """Write a screen selection into both plugins' sections. Screens not in
    ``wanted`` keep their current value. A plugin is enabled when any of its
    screens is on."""
    current = screen_states(config)
    final = {sid: bool(wanted[sid]) if sid in wanted else current[sid] for sid in SCREEN_IDS}
    for plugin in (LEAGUES, BLITZ):
        section = config.setdefault(plugin, {})
        screens = section.setdefault("screens", {})
        for sid, p, key, *_ in SCREENS:
            if p != plugin:
                continue
            if plugin == BLITZ:
                entry = screens.get(key)
                if not isinstance(entry, dict):
                    entry = screens[key] = {}
                entry["enabled"] = final[sid]
            else:
                screens[key] = final[sid]
        section["enabled"] = any(final[s[0]] for s in SCREENS if s[1] == plugin)
    return final


def parse_espn_url(text: str) -> Dict[str, Any]:
    """league_id / team_id / season from an ESPN fantasy link (or a bare id)."""
    text = (text or "").strip()
    if re.fullmatch(r"\d{3,12}", text):
        return {"league_id": text, "team_id": None, "season": None}
    if not re.match(r"^[a-z]+://", text, re.I):
        text = "https://" + text
    try:
        parsed = urlparse(text)
    except ValueError:
        raise ValueError("That doesn't look like a link.")
    query = {k.lower(): v[0] for k, v in parse_qs(parsed.query).items() if v}
    host = (parsed.hostname or "").lower()
    league = query.get("leagueid", "")
    if not league or not league.isdigit():
        if "espn" not in host:
            raise ValueError("That isn't an ESPN fantasy link.")
        raise ValueError("No league id in that link. Open your league on ESPN and copy the "
                         "address from your team page.")
    team = query.get("teamid")
    season = query.get("seasonid")
    return {"league_id": league,
            "team_id": int(team) if team and team.isdigit() else None,
            "season": int(season) if season and season.isdigit() else None}


def clean_cookie(value: str, name: str) -> str:
    value = (value or "").strip().strip(";").strip()
    if value.lower().startswith(name.lower() + "="):
        value = value[len(name) + 1:]
    return value.strip().strip('"').strip("'").strip()


def normalize_swid(value: str) -> str:
    v = clean_cookie(value, "SWID").upper()
    if v and not v.startswith("{"):
        v = "{" + v
    if v and not v.endswith("}"):
        v = v + "}"
    if not SWID_RE.match(v):
        raise ValueError("SWID should look like {1A2B3C4D-1A2B-1A2B-1A2B-1A2B3C4D5E6F}.")
    return v


def normalize_s2(value: str) -> str:
    v = clean_cookie(value, "espn_s2")
    if len(v) < 100 or re.search(r"\s", v):
        raise ValueError("espn_s2 should be one long string (usually 200-400 characters) "
                         "with no spaces. Copy the whole Value cell.")
    return v


def mask_swid(swid: str) -> str:
    swid = swid or ""
    return swid[:5] + "…" + swid[-2:] if len(swid) > 8 else ("set" if swid else "")


def espn_status(secrets: Dict[str, Any], today: Optional[date] = None) -> Dict[str, Any]:
    acct = (((secrets.get(LEAGUES) or {}).get("accounts") or {}).get("espn") or {})
    connected = bool(acct.get("espn_s2") and acct.get("swid"))
    out: Dict[str, Any] = {"connected": connected, "swid": mask_swid(acct.get("swid", "")),
                           "saved_at": acct.get("saved_at"), "age_days": None,
                           "expires_soon": False}
    if connected and acct.get("saved_at"):
        try:
            saved = datetime.strptime(str(acct["saved_at"])[:10], "%Y-%m-%d").date()
            age = ((today or date.today()) - saved).days
            out["age_days"] = age
            out["expires_soon"] = age >= ESPN_WARN_DAYS
        except ValueError:
            pass
    return out


def league_key(raw: Dict[str, Any]) -> str:
    return f"{str(raw.get('provider') or 'espn').lower()}:{raw.get('league_id')}:{raw.get('team_id')}"


def league_rows(config: Dict[str, Any]) -> List[Dict[str, Any]]:
    rows = []
    for raw in (config.get(LEAGUES) or {}).get("leagues") or []:
        if not isinstance(raw, dict):
            continue
        rows.append({"key": league_key(raw),
                     "provider": str(raw.get("provider") or "espn").lower(),
                     "league_id": str(raw.get("league_id") or ""),
                     "team_id": raw.get("team_id"),
                     "season": raw.get("season"),
                     "enabled": bool(raw.get("enabled", True)),
                     "name": raw.get("name") or "",
                     "team_name": raw.get("team_name") or ""})
    return rows


def add_league(config: Dict[str, Any], entry: Dict[str, Any]) -> Dict[str, Any]:
    """Add a league, or update the one already there for the same site and
    league id (you only have one team per league)."""
    leagues = config.setdefault(LEAGUES, {}).setdefault("leagues", [])
    new = {"provider": entry["provider"], "league_id": str(entry["league_id"]),
           "team_id": int(entry["team_id"]), "enabled": True}
    for k in ("name", "team_name"):
        if entry.get(k):
            new[k] = str(entry[k])[:80]
    if entry.get("season"):
        new["season"] = int(entry["season"])
    for i, raw in enumerate(leagues):
        if (isinstance(raw, dict) and str(raw.get("provider") or "espn").lower() == new["provider"]
                and str(raw.get("league_id")) == new["league_id"]):
            merged = dict(raw)
            merged.update(new)
            leagues[i] = merged
            return merged
    leagues.append(new)
    return new


def remove_league(config: Dict[str, Any], key: str) -> bool:
    leagues = (config.get(LEAGUES) or {}).get("leagues") or []
    keep = [r for r in leagues if not (isinstance(r, dict) and league_key(r) == key)]
    if len(keep) == len(leagues):
        return False
    config[LEAGUES]["leagues"] = keep
    return True


# ----------------------------------------------------------------------
# files, providers, restart
# ----------------------------------------------------------------------

class Store:
    """config.json through ConfigManager (which strips secrets on save) and
    config_secrets.json written directly, atomically, keeping its mode."""

    def __init__(self, config_path: str, secrets_path: str) -> None:
        self.config_path = config_path
        self.secrets_path = secrets_path

    def _manager(self):
        from src.config_manager import ConfigManager
        return ConfigManager(config_path=self.config_path, secrets_path=self.secrets_path)

    def load(self) -> Dict[str, Any]:
        """config.json with secrets merged in (what the display sees)."""
        return self._manager().load_config()

    def save(self, config: Dict[str, Any]) -> None:
        self._manager().save_config(config)

    def load_secrets(self) -> Dict[str, Any]:
        if not os.path.exists(self.secrets_path):
            return {}
        with open(self.secrets_path) as f:
            return json.load(f)

    def save_secrets(self, secrets: Dict[str, Any]) -> None:
        mode = 0o600
        if os.path.exists(self.secrets_path):
            mode = os.stat(self.secrets_path).st_mode & 0o777
        tmp = self.secrets_path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(secrets, f, indent=4)
        os.chmod(tmp, mode)
        os.replace(tmp, self.secrets_path)


_providers_module = None


def load_providers():
    """fantasy-leagues/providers as a package, without putting the plugin
    folder on sys.path (its module names are generic)."""
    global _providers_module
    if _providers_module is None:
        pkg_dir = os.path.join(ROOT, "plugins", LEAGUES, "providers")
        name = "fantasy_leagues_providers"
        spec = importlib.util.spec_from_file_location(
            name, os.path.join(pkg_dir, "__init__.py"), submodule_search_locations=[pkg_dir])
        module = importlib.util.module_from_spec(spec)
        sys.modules[name] = module
        spec.loader.exec_module(module)
        _providers_module = module
    return _providers_module


def default_provider_factory(provider: str, league_id: str, season: int, team_id: Any,
                             credentials: Dict[str, str]):
    import requests
    mod = load_providers()
    cls = mod.PROVIDERS.get(provider)
    if cls is None:
        return None
    return cls(league_id, season, team_id if team_id is not None else 0, credentials,
               session=requests.Session(), timeout=15)


def default_restart() -> Tuple[bool, str]:
    try:
        result = subprocess.run(["sudo", "-n", "systemctl", "restart", "ledmatrix"],
                                capture_output=True, text=True, timeout=30)
    except Exception as exc:  # noqa: BLE001
        return False, f"Saved, but the display could not be restarted: {exc}"
    if result.returncode != 0:
        msg = (result.stderr or result.stdout or "").strip()[:200]
        return False, f"Saved, but the display could not be restarted ({msg or result.returncode})."
    return True, "Saved. The display is restarting with the new settings."


def test_league(factory: Callable, provider: str, league_id: str, team_id: Any,
                season: Optional[int], credentials: Dict[str, str]) -> Dict[str, Any]:
    """Ask the site about a league. Never raises."""
    season = int(season or default_season())
    try:
        mod = load_providers()
        p = factory(provider, league_id, season, team_id, credentials)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "error": f"Could not load the {provider} reader: {exc}"}
    if p is None:
        return {"ok": False, "error": f"{provider.title()} leagues aren't supported yet."}
    try:
        info = p.fetch_league_info()
    except mod.AuthError:
        if credentials.get("espn_s2"):
            return {"ok": False, "needs_login": True,
                    "error": "ESPN refused your sign-in. It has probably expired: "
                             "connect ESPN again with fresh cookies."}
        return {"ok": False, "needs_login": True,
                "error": "This league is private. Connect your ESPN account below, then test again."}
    except mod.ProviderError as exc:
        return {"ok": False, "error": f"ESPN has no league {league_id} for the {season} season. "
                                      f"Check the link. ({exc})"}
    except Exception as exc:  # noqa: BLE001 - network, bad JSON, site down
        return {"ok": False, "error": f"Couldn't reach {provider.upper()}: {exc}"}

    teams = info.get("teams") or []
    team = None
    if team_id not in (None, ""):
        team = next((t for t in teams if t["id"] == int(team_id)), None)
        if team is None:
            return {"ok": False, "teams": teams, "league_name": info.get("name"),
                    "error": f"Team {team_id} isn't in this league. Pick your team from the list."}
    else:
        mine = [t for t in teams if t.get("mine")]
        if len(mine) == 1:
            team = mine[0]
    return {"ok": True, "league_name": info.get("name") or f"League {league_id}",
            "season": info.get("season") or season, "week": info.get("week"),
            "teams": teams, "team": team, "needs_team": team is None}


# ----------------------------------------------------------------------
# routes
# ----------------------------------------------------------------------

def register_fantasy_config_routes(app, config_path: Optional[str] = None,
                                   secrets_path: Optional[str] = None,
                                   restart: Optional[Callable[[], Tuple[bool, str]]] = None,
                                   provider_factory: Optional[Callable] = None,
                                   today: Optional[Callable[[], date]] = None):
    """Register the Fantasy tab's routes. The keyword arguments are for tests."""
    store = Store(config_path or os.path.join(ROOT, "config", "config.json"),
                  secrets_path or os.path.join(ROOT, "config", "config_secrets.json"))
    restart = restart or default_restart
    factory = provider_factory or default_provider_factory
    today = today or date.today

    def body() -> Dict[str, Any]:
        return request.get_json(silent=True) or {}

    def finish(payload: Dict[str, Any], data: Dict[str, Any]):
        if data.get("apply", True):
            ok, msg = restart()
            payload["restarted"], payload["message"] = ok, msg
        else:
            payload["restarted"], payload["message"] = False, "Saved."
        return jsonify(payload)

    def credentials(provider: str) -> Dict[str, str]:
        acct = ((store.load_secrets().get(LEAGUES) or {}).get("accounts") or {}).get(provider) or {}
        return {k: v for k, v in acct.items() if k in ("espn_s2", "swid") and v}

    def fail(message: str, code: int = 400, **extra):
        out = {"ok": False, "error": message}
        out.update(extra)
        return jsonify(out), code

    @app.route("/api/fantasy/config", methods=["GET"])
    def fantasy_get_config():
        try:
            config = store.load()
            states = screen_states(config)
            screens = [{"id": sid, "label": label, "help": help_, "tier": tier,
                        "live_only": sid in LIVE_ONLY, "on": states[sid],
                        "available": screen_available(sid, config)}
                       for sid, _p, _k, label, help_, tier in SCREENS]
            presets = {t: preset_ids(t, config) for t in ["off"] + TIERS}
            return jsonify({"ok": True, "screens": screens, "presets": presets,
                            "preset": detect_preset(states, config),
                            "leagues": league_rows(config),
                            "espn": espn_status(store.load_secrets(), today()),
                            "season": default_season(today())})
        except Exception as exc:  # noqa: BLE001
            logger.error("Fantasy config load failed: %s", exc, exc_info=True)
            return fail(str(exc), 500)

    @app.route("/api/fantasy/screens", methods=["POST"])
    def fantasy_save_screens():
        data = body()
        wanted = data.get("screens")
        if not isinstance(wanted, dict):
            return fail("Send {\"screens\": {screen_id: true/false}}.")
        unknown = [k for k in wanted if k not in SCREEN_IDS]
        if unknown:
            return fail(f"Unknown screens: {', '.join(unknown)}")
        try:
            with _lock:
                config = store.load()
                final = apply_screens(config, wanted)
                store.save(config)
            logger.info("Fantasy screens saved: %s", sorted(k for k, v in final.items() if v))
            return finish({"ok": True, "preset": detect_preset(final, config)}, data)
        except Exception as exc:  # noqa: BLE001
            logger.error("Fantasy screens save failed: %s", exc, exc_info=True)
            return fail(str(exc), 500)

    @app.route("/api/fantasy/leagues/parse-url", methods=["POST"])
    def fantasy_parse_url():
        try:
            return jsonify({"ok": True, **parse_espn_url(str(body().get("url") or ""))})
        except ValueError as exc:
            return fail(str(exc))

    @app.route("/api/fantasy/leagues/test", methods=["POST"])
    def fantasy_test_league():
        data = body()
        if data.get("key"):
            row = next((r for r in league_rows(store.load()) if r["key"] == data["key"]), None)
            if row is None:
                return fail("That league isn't in the list any more.", 404)
            provider, league_id, team_id, season = (row["provider"], row["league_id"],
                                                    row["team_id"], row["season"])
        else:
            provider = str(data.get("provider") or "espn").lower()
            league_id = str(data.get("league_id") or "").strip()
            team_id, season = data.get("team_id"), data.get("season")
            if not league_id.isdigit():
                return fail("Enter the league id (a number) or paste the league's link.")
        if team_id not in (None, "") and not str(team_id).isdigit():
            return fail("Team id should be a number.")
        result = test_league(factory, provider, league_id, team_id, season, credentials(provider))
        return jsonify(result)

    @app.route("/api/fantasy/leagues/add", methods=["POST"])
    def fantasy_add_league():
        data = body()
        provider = str(data.get("provider") or "espn").lower()
        league_id = str(data.get("league_id") or "").strip()
        team_id = data.get("team_id")
        if provider != "espn":
            return fail("Only ESPN leagues can be added for now. Yahoo is next.")
        if not league_id.isdigit():
            return fail("League id should be a number.")
        if team_id in (None, "") or not str(team_id).isdigit():
            return fail("Pick your team first.")
        try:
            with _lock:
                config = store.load()
                entry = add_league(config, {"provider": provider, "league_id": league_id,
                                            "team_id": int(team_id), "name": data.get("name"),
                                            "team_name": data.get("team_name"),
                                            "season": data.get("season")})
                section = config[LEAGUES]
                states = screen_states(config)
                if not any(states[s[0]] for s in SCREENS if s[1] == LEAGUES):
                    # First league on a Pi where the league screens are off:
                    # turn the Essentials league screens on so it shows up.
                    apply_screens(config, {s[0]: True for s in SCREENS
                                           if s[1] == LEAGUES and s[5] == ESSENTIALS})
                section.setdefault("enabled", True)
                store.save(config)
            return finish({"ok": True, "league": {**entry, "key": league_key(entry)}}, data)
        except Exception as exc:  # noqa: BLE001
            logger.error("Fantasy add league failed: %s", exc, exc_info=True)
            return fail(str(exc), 500)

    @app.route("/api/fantasy/leagues/remove", methods=["POST"])
    def fantasy_remove_league():
        data = body()
        try:
            with _lock:
                config = store.load()
                if not remove_league(config, str(data.get("key") or "")):
                    return fail("That league isn't in the list.", 404)
                store.save(config)
            return finish({"ok": True}, data)
        except Exception as exc:  # noqa: BLE001
            logger.error("Fantasy remove league failed: %s", exc, exc_info=True)
            return fail(str(exc), 500)

    @app.route("/api/fantasy/espn/connect", methods=["POST"])
    def fantasy_espn_connect():
        data = body()
        try:
            s2 = normalize_s2(str(data.get("espn_s2") or ""))
            swid = normalize_swid(str(data.get("swid") or ""))
        except ValueError as exc:
            return fail(str(exc))
        creds = {"espn_s2": s2, "swid": swid}

        # Check them against a league if there is one to check against.
        check = None
        rows = [r for r in league_rows(store.load()) if r["provider"] == "espn" and r["enabled"]]
        if rows:
            r = rows[0]
            check = test_league(factory, "espn", r["league_id"], r["team_id"], r["season"], creds)
            if not check["ok"] and check.get("needs_login"):
                return fail("ESPN didn't accept these cookies. Make sure you copied them from "
                            "a browser that is signed in to ESPN Fantasy.", check=check)
        try:
            with _lock:
                secrets = store.load_secrets()
                acct = secrets.setdefault(LEAGUES, {}).setdefault("accounts", {})
                acct["espn"] = {**creds, "saved_at": today().isoformat()}
                store.save_secrets(secrets)
        except Exception as exc:  # noqa: BLE001
            logger.error("Saving ESPN sign-in failed: %s", exc, exc_info=True)
            return fail(f"Could not save the sign-in: {exc}", 500)
        payload = {"ok": True, "espn": espn_status(store.load_secrets(), today()),
                   "checked": check}
        return finish(payload, data)

    @app.route("/api/fantasy/espn/disconnect", methods=["POST"])
    def fantasy_espn_disconnect():
        data = body()
        try:
            with _lock:
                secrets = store.load_secrets()
                accts = (secrets.get(LEAGUES) or {}).get("accounts") or {}
                accts.pop("espn", None)
                store.save_secrets(secrets)
        except Exception as exc:  # noqa: BLE001
            return fail(f"Could not update the secrets file: {exc}", 500)
        return finish({"ok": True, "espn": espn_status(store.load_secrets(), today())}, data)
