"""Fantasy Leagues: your own fantasy football leagues on the panel.

Screens (display modes):
  fantasy_leagues_summary  every league at a glance: host chip, me vs opp, bar
  fantasy_leagues_matchup  one league per page: big scores, records, projections
  fantasy_leagues_roster   my starters, four per page: slot, name, points, proj
  fantasy_leagues_live     big-play alert when one of my starters (or my
                           opponent's) jumps by ``alert_min_points``; takes over
                           the panel when ``live_priority`` is on

Points are each league's own, as the site reports them. Providers live in
``providers/`` (ESPN now; Yahoo next). Runs next to ChuckBuilds' fantasy-blitz
(the NFL-wide show) without touching it.
"""

import logging
import threading
import time
from datetime import datetime
from typing import Any, Dict, List, Optional, Tuple

import requests

try:
    from src.plugin_system.base_plugin import BasePlugin
except ImportError:  # running tests from the plugin folder
    import os
    import sys
    sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..")))
    from src.plugin_system.base_plugin import BasePlugin

import fl_render as render
from fl_state import (WEEKDAYS, AlertQueue, LeagueSlot, detect_alerts, normalize_league,
                      points_changed)
from providers import PROVIDERS, AuthError

try:
    from zoneinfo import ZoneInfo
except ImportError:  # pragma: no cover - Python < 3.9
    ZoneInfo = None  # type: ignore[assignment]

MODE_SUMMARY = "fantasy_leagues_summary"
MODE_MATCHUP = "fantasy_leagues_matchup"
MODE_ROSTER = "fantasy_leagues_roster"
MODE_LIVE = "fantasy_leagues_live"
MODES = [MODE_SUMMARY, MODE_MATCHUP, MODE_ROSTER, MODE_LIVE]
SCREEN_OF = {MODE_SUMMARY: "summary", MODE_MATCHUP: "matchup", MODE_ROSTER: "roster",
             MODE_LIVE: "alerts"}
CACHE_MAX_AGE = 7 * 86400


class FantasyLeaguesPlugin(BasePlugin):

    def __init__(self, plugin_id: str, config: Dict[str, Any], display_manager: Any,
                 cache_manager: Any, plugin_manager: Any) -> None:
        super().__init__(plugin_id, config, display_manager, cache_manager, plugin_manager)
        self.modes = list(MODES)
        self.session = requests.Session()
        self.queue = AlertQueue()
        self._queue_lock = threading.Lock()
        self._mode: Optional[str] = None
        self._mode_started = 0.0
        self._last_frame_key: Any = None
        self.slots: List[LeagueSlot] = []
        self._load_config(config)
        self._restore()

    # ------------------------------------------------------------------
    # config
    # ------------------------------------------------------------------

    def _load_config(self, config: Dict[str, Any]) -> None:
        self.config = config or {}
        c = self.config
        self.enabled = bool(c.get("enabled", True))
        self.display_duration = _num(c.get("display_duration"), 10, 3, 120)
        self.roster_page_seconds = _num(c.get("roster_page_seconds"), 5, 2, 60)
        self.live_interval = _num(c.get("live_update_interval"), 60, 30, 600)
        self.idle_interval = _num(c.get("update_interval"), 900, 120, 3600)
        self.alert_seconds = _num(c.get("alert_seconds"), 8, 3, 60)
        self.alert_min_points = _num(c.get("alert_min_points"), 6, 1, 50)
        self.recap_days = [str(d)[:3].lower() for d in (c.get("recap_days") or ["tue", "wed"])]
        screens = c.get("screens") or {}
        self.screens = {s: bool(screens.get(s, True)) for s in ("summary", "matchup", "roster", "alerts")}
        tz = None
        try:
            tz = self.plugin_manager.config_manager.get_timezone()
        except Exception:  # noqa: BLE001
            pass
        self.tz = None
        if ZoneInfo is not None:
            try:
                self.tz = ZoneInfo(c.get("timezone") or tz or "America/Chicago")
            except Exception:  # noqa: BLE001
                self.tz = None

        accounts = c.get("accounts") or {}
        old = {s.key: s for s in self.slots}
        self.slots = []
        for raw in c.get("leagues") or []:
            if not isinstance(raw, dict):
                continue
            cfg = normalize_league(raw)
            if not cfg["enabled"] or not cfg["league_id"] or cfg["team_id"] in (None, ""):
                continue
            cls = PROVIDERS.get(cfg["provider"])
            provider = None
            if cls is not None:
                provider = cls(cfg["league_id"], cfg["season"], cfg["team_id"],
                               accounts.get(cfg["provider"]) or {}, session=self.session)
            slot = LeagueSlot(cfg, provider)
            prev = old.get(slot.key)
            if prev is not None:  # keep data across a config change
                for attr in ("settings", "settings_at", "matchup", "matchup_at", "recap",
                             "recap_at", "last_change_at"):
                    setattr(slot, attr, getattr(prev, attr))
            if provider is None:
                slot.error = f"{cfg['provider'].upper()} NOT SUPPORTED YET"
            self.slots.append(slot)
        if not self.slots:
            self.logger.warning("No leagues configured: add fantasy-leagues.leagues in config.json")

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        self._load_config(new_config)

    def validate_config(self) -> bool:
        ok = super().validate_config()
        for raw in self.config.get("leagues") or []:
            if not isinstance(raw, dict) or not raw.get("league_id") or raw.get("team_id") in (None, ""):
                self.logger.error("Each league needs league_id and team_id: %s", raw)
                ok = False
        return ok

    # ------------------------------------------------------------------
    # cache (so a restart shows the last scores straight away)
    # ------------------------------------------------------------------

    def _cache_key(self, slot: LeagueSlot) -> str:
        return f"fantasy-leagues:{slot.key}"

    def _restore(self) -> None:
        for slot in self.slots:
            try:
                saved = self.cache_manager.get(self._cache_key(slot), max_age=CACHE_MAX_AGE)
            except Exception:  # noqa: BLE001
                saved = None
            if not isinstance(saved, dict):
                continue
            settings = saved.get("settings")
            if isinstance(settings, dict):
                settings["teams"] = {int(k): v for k, v in (settings.get("teams") or {}).items()}
                settings["matchup_periods"] = {int(k): v for k, v in
                                               (settings.get("matchup_periods") or {}).items()}
                slot.settings = settings  # settings_at stays 0: refreshed on first update
            slot.matchup = saved.get("matchup")
            slot.recap = saved.get("recap")
            slot.matchup_at = float(saved.get("matchup_at") or 0)
            slot.recap_at = float(saved.get("recap_at") or 0)

    def _save(self, slot: LeagueSlot) -> None:
        try:
            self.cache_manager.set(self._cache_key(slot), {
                "settings": slot.settings, "matchup": slot.matchup, "recap": slot.recap,
                "matchup_at": slot.matchup_at, "recap_at": slot.recap_at})
        except Exception as exc:  # noqa: BLE001
            self.logger.debug("cache save failed: %s", exc)

    # ------------------------------------------------------------------
    # data
    # ------------------------------------------------------------------

    def _local(self, now: float) -> datetime:
        return datetime.fromtimestamp(now, self.tz) if self.tz else datetime.fromtimestamp(now)

    def get_update_interval(self) -> Optional[float]:
        now = time.time()
        local = self._local(now)
        if any(s.is_live(now, local) for s in self.slots if s.provider):
            return self.live_interval
        return self.idle_interval

    def update(self) -> None:
        now = time.time()
        local = self._local(now)
        weekday = WEEKDAYS[local.weekday()]
        for slot in self.slots:
            if slot.provider is None:
                continue
            try:
                self._update_slot(slot, now, local, weekday)
                slot.error, slot.auth_error = None, False
            except AuthError as exc:
                slot.error, slot.auth_error = f"{slot.cfg['label']} LOGIN EXPIRED", True
                slot.next_fetch = now + self.idle_interval
                self.logger.error("%s", exc)
            except Exception as exc:  # noqa: BLE001 - keep the last good data
                slot.error = "NO CONNECTION" if isinstance(exc, requests.RequestException) else "ERROR"
                slot.next_fetch = now + min(self.idle_interval, 120)
                self.logger.warning("League %s update failed: %s", slot.key, exc)

    def _update_slot(self, slot: LeagueSlot, now: float, local: datetime, weekday: str) -> None:
        p = slot.provider
        if slot.needs_settings(now):
            slot.settings = p.fetch_settings()
            slot.settings_at = now
            self.logger.info("League %s: %s, week %s", slot.key, slot.settings.get("name"), slot.week)
        name = slot.settings.get("name") or ""
        teams = slot.settings.get("teams")
        week = slot.week

        if now >= slot.next_fetch:
            fresh = slot.matchup if now - slot.matchup_at <= max(180.0, 3 * self.live_interval) else None
            new = p.fetch_matchup(week, name, teams=teams, scoring_period=slot.scoring_period())
            if points_changed(slot.matchup, new):
                slot.last_change_at = now
            if self.screens["alerts"]:
                found = detect_alerts(slot.cfg, fresh, new, self.alert_min_points, now)
                with self._queue_lock:
                    for a in found:
                        self.queue.add(a)
                for a in found:
                    self.logger.info("Big play %s: %s +%s", slot.cfg["label"], a["short"],
                                     a["leagues"][0]["delta"])
            slot.matchup, slot.matchup_at = new, now
            slot.next_fetch = now + (self.live_interval if slot.is_live(now, local) else self.idle_interval)
            self._save(slot)

        if slot.needs_recap(now, weekday, self.recap_days):
            slot.recap = p.fetch_matchup(week - 1, name, teams=teams, scoring_period=week - 1)
            slot.recap_at = now
            self._save(slot)

    # ------------------------------------------------------------------
    # pages
    # ------------------------------------------------------------------

    def _views(self, now: float) -> List[Tuple[LeagueSlot, Optional[Dict[str, Any]]]]:
        weekday = WEEKDAYS[self._local(now).weekday()]
        return [(s, s.view(weekday, self.recap_days)) for s in self.slots]

    def _pages(self, mode: str, now: float) -> List[Any]:
        views = self._views(now)
        if mode == MODE_SUMMARY:
            rows = [{"label": s.cfg["label"], "host_color": s.cfg["host_color"],
                     "team_color": s.cfg["team_color"], "matchup": m,
                     "error": s.error or ("LOADING" if m is None else None)}
                    for s, m in views if s.cfg["screens"]["summary"]]
            if not any(r["matchup"] for r in rows):
                return []
            return [rows[i:i + 2] for i in range(0, len(rows), 2)]
        if mode == MODE_MATCHUP:
            return [(s, m) for s, m in views if m and s.cfg["screens"]["matchup"]]
        if mode == MODE_ROSTER:
            pages = []
            for s, m in views:
                if not m or not s.cfg["screens"]["roster"]:
                    continue
                sides = [("me", s.cfg["team_color"])]
                if s.cfg["screens"]["roster_opponent"]:
                    sides.append(("opp", render.OPP))
                for side, color in sides:
                    chunks = _balanced(m[side]["starters"], 4)
                    for i, chunk in enumerate(chunks):
                        pages.append((s, m, side, color, chunk, f"{i + 1}/{len(chunks)}"))
            return pages
        return []

    # ------------------------------------------------------------------
    # display
    # ------------------------------------------------------------------

    def display(self, force_clear: bool = False, display_mode: Optional[str] = None) -> bool:
        mode = display_mode or MODE_SUMMARY
        now = time.time()
        if force_clear or mode != self._mode:
            self._mode, self._mode_started, self._last_frame_key = mode, now, None
        if not self.enabled or not self.screens.get(SCREEN_OF.get(mode, ""), False):
            return False
        img = self.display_manager.image
        w, h = img.size

        if mode == MODE_LIVE:
            with self._queue_lock:
                a = self.queue.start_next(now, self.alert_seconds)
                started = self.queue.current_until - self.alert_seconds
            if a is None:
                return False
            frame = render.alert(w, h, a, now - started)
            return self._show(frame, None)

        pages = self._pages(mode, now)
        if not pages:
            return False
        per = self.roster_page_seconds if mode == MODE_ROSTER else self.display_duration
        index = int((now - self._mode_started) // per) % len(pages)
        page = pages[index]
        tag = f"{index + 1}/{len(pages)}" if len(pages) > 1 else ""
        if mode == MODE_SUMMARY:
            frame = render.summary(w, h, page, "MY LEAGUES", tag)
        elif mode == MODE_MATCHUP:
            s, m = page
            frame = render.matchup(w, h, m, s.cfg["label"], s.cfg["host_color"], s.cfg["team_color"])
        else:
            s, m, side, color, chunk, sub = page
            frame = render.roster(w, h, m[side], chunk, s.cfg["label"], s.cfg["host_color"],
                                  color, sub, show_proj=not m.get("final"))
        return self._show(frame, (mode, index, frame.tobytes()))

    def _show(self, frame: Any, key: Any) -> bool:
        if key is not None and key == self._last_frame_key:
            return True  # unchanged: no redraw, no flicker
        self._last_frame_key = key
        self.display_manager.image.paste(frame, (0, 0))
        self.display_manager.update_display()
        return True

    def get_display_duration(self) -> float:
        now = time.time()
        if self._mode == MODE_LIVE:
            return self.alert_seconds
        if self._mode == MODE_ROSTER:
            return max(self.roster_page_seconds, self.roster_page_seconds * len(self._pages(MODE_ROSTER, now)))
        if self._mode in (MODE_SUMMARY, MODE_MATCHUP):
            return max(self.display_duration, self.display_duration * len(self._pages(self._mode, now)))
        return self.display_duration

    # ------------------------------------------------------------------
    # live priority
    # ------------------------------------------------------------------

    def has_live_priority(self) -> bool:
        return bool(self.config.get("live_priority", True)) and self.screens["alerts"]

    def has_live_content(self) -> bool:
        with self._queue_lock:
            return self.queue.has_content(time.time())

    def get_live_modes(self) -> List[str]:
        return [MODE_LIVE]

    def get_info(self) -> Dict[str, Any]:
        info = super().get_info()
        info["config"] = {k: v for k, v in self.config.items() if k != "accounts"}
        info["leagues"] = [{"league": s.key, "week": s.week, "error": s.error,
                            "updated": s.matchup_at} for s in self.slots]
        return info

    def cleanup(self) -> None:
        try:
            self.session.close()
        except Exception:  # noqa: BLE001
            pass
        super().cleanup()


def _balanced(items: List[Any], per_page: int) -> List[List[Any]]:
    """9 starters at 4 a page -> 3, 3, 3 rather than 4, 4, 1."""
    if not items:
        return []
    n = -(-len(items) // per_page)
    size = -(-len(items) // n)
    return [items[i:i + size] for i in range(0, len(items), size)]


def _num(value: Any, default: float, lo: float, hi: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return float(default)
    return max(lo, min(hi, v))
