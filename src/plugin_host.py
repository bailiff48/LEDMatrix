"""Runs ChuckBuilds-style plugins inside the fork's display controller.

A plugin is a folder ``plugins/<id>/`` with a ``manifest.json``
(entry_point, class_name, display_modes, update_interval) and usually a
``config_schema.json``. It is loaded when ``config.json`` has a section keyed
by the plugin id with ``"enabled": true``; that section (with
``config_secrets.json`` already merged in by ConfigManager) is laid over the
schema's defaults and handed to the plugin, as Chuck's core does.

What the host does:
  * imports the plugin class and builds it with the fork's display manager,
    a cache adapter and a small plugin-manager stand-in;
  * runs update() on a background thread on the plugin's own schedule, so a
    slow API call never freezes the panel;
  * wraps every display mode in a ``PluginModeAdapter`` that looks like a
    fork manager (``display()``, ``live_games``), so the controller's
    existing rotation and live-priority code drive plugins too.

The controller hook is small; see the ``plugin_host`` lines in
``display_controller.py``.
"""

import copy
import importlib.util
import inspect
import json
import logging
import os
import sys
import threading
import time
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
PLUGINS_DIR = os.path.join(REPO_ROOT, "plugins")

MIN_UPDATE_INTERVAL = 15.0
DEFAULT_UPDATE_INTERVAL = 300.0
#: max_age the fork's cache treats as "never expires" (Chuck's core takes None).
NEVER_EXPIRES = 10 ** 9


# ----------------------------------------------------------------------
# helpers handed to plugins
# ----------------------------------------------------------------------

class PluginCacheAdapter:
    """Chuck's cache API over the fork's CacheManager.

    Same wrap/unwrap semantics; adds ``max_age=None`` (never expires) and
    the ``memory_ttl`` / ``ttl`` keywords, which the fork's cache lacks.
    """

    def __init__(self, cache_manager: Any) -> None:
        self._cache = cache_manager

    def get(self, key: str, max_age: Optional[float] = 300,
            memory_ttl: Optional[float] = None) -> Any:
        age = NEVER_EXPIRES if max_age is None else max_age
        return self._cache.get(key, max_age=age)

    def set(self, key: str, data: Any, ttl: Optional[float] = None) -> None:
        self._cache.set(key, data)

    def delete(self, key: str) -> None:
        clear = getattr(self._cache, "clear_cache", None)
        if clear is not None:
            clear(key)

    def __getattr__(self, name: str) -> Any:  # anything else: the real cache
        return getattr(self._cache, name)


class _ConfigManagerShim:
    def __init__(self, config: Dict[str, Any]) -> None:
        self._config = config

    def get_timezone(self) -> str:
        return self._config.get("timezone") or "America/Chicago"

    def load_config(self) -> Dict[str, Any]:
        return self._config


class HostPluginManager:
    """The bits of Chuck's PluginManager that plugins reach for."""

    def __init__(self, host: "PluginHost", config: Dict[str, Any]) -> None:
        self._host = host
        self.config_manager = _ConfigManagerShim(config)

    def get_plugin(self, plugin_id: str) -> Any:
        rec = self._host.plugins.get(plugin_id)
        return rec.instance if rec else None


# ----------------------------------------------------------------------
# schema defaults
# ----------------------------------------------------------------------

def schema_defaults(schema: Dict[str, Any]) -> Any:
    """Default values from a JSON-Schema (draft-07) object, recursively."""
    if not isinstance(schema, dict):
        return None
    if "default" in schema:
        return copy.deepcopy(schema["default"])
    if schema.get("type") == "object" or "properties" in schema:
        out = {}
        for name, sub in (schema.get("properties") or {}).items():
            value = schema_defaults(sub)
            if value is not None:
                out[name] = value
        return out
    return None


def deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for key, value in (override or {}).items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = deep_merge(out[key], value)
        else:
            out[key] = copy.deepcopy(value)
    return out


# ----------------------------------------------------------------------
# per-plugin record and mode adapter
# ----------------------------------------------------------------------

class PluginRecord:
    def __init__(self, plugin_id: str, manifest: Dict[str, Any], instance: Any,
                 config: Dict[str, Any]) -> None:
        self.plugin_id = plugin_id
        self.manifest = manifest
        self.instance = instance
        self.config = config
        self.modes: List[str] = [str(m) for m in (getattr(instance, "modes", None)
                                                  or manifest.get("display_modes")
                                                  or [plugin_id])]
        self.next_update = 0.0
        self.thread: Optional[threading.Thread] = None
        self.last_error_log = 0.0

    def static_interval(self) -> float:
        for value in (self.config.get("update_interval"), self.manifest.get("update_interval")):
            try:
                if value is not None and float(value) > 0:
                    return float(value)
            except (TypeError, ValueError):
                pass
        return DEFAULT_UPDATE_INTERVAL

    def interval(self) -> float:
        try:
            dynamic = self.instance.get_update_interval()
        except Exception:  # noqa: BLE001 - a bad hook falls back to static
            dynamic = None
        seconds = dynamic if dynamic else self.static_interval()
        return max(MIN_UPDATE_INTERVAL, float(seconds))

    def live_modes(self) -> List[str]:
        try:
            modes = list(self.instance.get_live_modes() or [])
        except Exception:  # noqa: BLE001
            modes = []
        # The fork's live machinery keys on "<sport>_live" mode names.
        return [m for m in modes if m in self.modes and m.endswith("_live")]

    def has_live_priority(self) -> bool:
        try:
            return bool(self.instance.enabled and self.instance.has_live_priority())
        except Exception:  # noqa: BLE001
            return False


class PluginModeAdapter:
    """Makes one plugin display mode look like a fork manager."""

    def __init__(self, host: "PluginHost", record: PluginRecord, mode: str,
                 is_live: bool) -> None:
        self.host = host
        self.record = record
        self.mode = mode
        self.is_live = is_live
        self.plugin_id = record.plugin_id
        self._last_error_log = 0.0
        try:
            params = inspect.signature(record.instance.display).parameters
            self._takes_mode = "display_mode" in params
        except (TypeError, ValueError):
            self._takes_mode = True

    def update(self) -> None:
        """The controller calls this before drawing a *_live mode. Updates
        already run in the background, so there is nothing to do here."""

    def get_display_duration(self) -> float:
        try:
            return float(self.record.instance.get_display_duration())
        except Exception:  # noqa: BLE001
            return 15.0

    def display(self, force_clear: bool = False) -> bool:
        plugin = self.record.instance
        try:
            if self._takes_mode:
                shown = plugin.display(force_clear=force_clear, display_mode=self.mode)
            else:  # a single-mode plugin
                shown = plugin.display(force_clear=force_clear)
        except Exception as exc:  # noqa: BLE001 - never lose the loop to a plugin
            now = time.time()
            if now - self._last_error_log > 60:
                self._last_error_log = now
                logger.error("Plugin %s display(%s) failed: %s",
                             self.plugin_id, self.mode, exc, exc_info=True)
            shown = False
        self.host._after_display(self, shown is not False, force_clear)
        return shown is not False


class LivePluginModeAdapter(PluginModeAdapter):
    """A plugin's *_live mode. Only this kind carries ``live_games``: the
    controller reads that attribute both for live takeover and, in
    ``_mode_has_content``, as "has nothing to show" when it is empty, so a
    regular mode must not have it at all."""

    @property
    def live_games(self) -> List[Any]:
        try:
            return [self.mode] if self.record.instance.has_live_content() else []
        except Exception:  # noqa: BLE001
            return []


# ----------------------------------------------------------------------
# the host
# ----------------------------------------------------------------------

class PluginHost:
    def __init__(self, config: Dict[str, Any], display_manager: Any, cache_manager: Any,
                 controller: Any = None, plugins_dir: str = PLUGINS_DIR) -> None:
        self.config = config
        self.display_manager = display_manager
        self.cache = PluginCacheAdapter(cache_manager)
        self.controller = controller
        self.plugins_dir = plugins_dir
        self.plugin_manager = HostPluginManager(self, config)
        self.plugins: Dict[str, PluginRecord] = {}
        self.adapters: Dict[str, PluginModeAdapter] = {}
        self.load_all()

    # loading ---------------------------------------------------------

    def load_all(self) -> None:
        if not os.path.isdir(self.plugins_dir):
            return
        for plugin_id in sorted(os.listdir(self.plugins_dir)):
            section = self.config.get(plugin_id)
            if not isinstance(section, dict) or not section.get("enabled", False):
                continue
            try:
                self._load(plugin_id, section)
            except Exception as exc:  # noqa: BLE001 - one bad plugin must not stop boot
                logger.error("Could not load plugin %s: %s", plugin_id, exc, exc_info=True)

    def _load(self, plugin_id: str, section: Dict[str, Any]) -> None:
        plugin_dir = os.path.join(self.plugins_dir, plugin_id)
        with open(os.path.join(plugin_dir, "manifest.json")) as f:
            manifest = json.load(f)

        schema_file = os.path.join(plugin_dir, manifest.get("config_schema", "config_schema.json"))
        defaults: Dict[str, Any] = {}
        if os.path.exists(schema_file):
            with open(schema_file) as f:
                defaults = schema_defaults(json.load(f)) or {}
        config = deep_merge(defaults, section)

        # Plugins import their sibling modules by bare name.
        if plugin_dir not in sys.path:
            sys.path.insert(0, plugin_dir)
        entry = os.path.join(plugin_dir, manifest.get("entry_point", "manager.py"))
        module_name = f"ledmatrix_plugin_{plugin_id.replace('-', '_')}"
        spec = importlib.util.spec_from_file_location(module_name, entry)
        module = importlib.util.module_from_spec(spec)
        sys.modules[module_name] = module
        spec.loader.exec_module(module)
        cls = getattr(module, manifest["class_name"])

        instance = cls(plugin_id, config, self.display_manager, self.cache, self.plugin_manager)
        if hasattr(instance, "validate_config") and not instance.validate_config():
            logger.warning("Plugin %s: config did not validate; loading anyway", plugin_id)

        record = PluginRecord(plugin_id, manifest, instance, config)
        self.plugins[plugin_id] = record
        live = set(record.live_modes())
        for mode in record.modes:
            if mode in self.adapters:
                logger.warning("Plugin %s: mode %s already taken; skipped", plugin_id, mode)
                continue
            cls = LivePluginModeAdapter if mode in live else PluginModeAdapter
            self.adapters[mode] = cls(self, record, mode, mode in live)
        logger.info("Loaded plugin %s v%s: %d modes (live: %s)", plugin_id,
                    manifest.get("version", "?"), len(record.modes), sorted(live) or "none")

    # what the controller asks ----------------------------------------

    def adapter(self, mode: str) -> Optional[PluginModeAdapter]:
        return self.adapters.get(mode)

    def owns(self, mode: str) -> bool:
        return mode in self.adapters

    def rotation_modes(self) -> List[str]:
        """Modes for regular rotation. A live mode of a plugin with live
        priority on is left out: it only appears by taking over, like the
        fork's own *_live modes with live_priority true."""
        out = []
        for mode, a in self.adapters.items():
            if a.is_live and a.record.has_live_priority():
                continue
            out.append(mode)
        return out

    def live_attributes(self) -> Dict[str, PluginModeAdapter]:
        """{"<sport>_live": adapter} for setattr on the controller, which
        looks live managers up by that attribute name."""
        return {mode: a for mode, a in self.adapters.items() if a.is_live}

    def live_takeover_sports(self) -> List[str]:
        """Sport keys ("fantasy" for fantasy_live) wanting to take over now."""
        out = []
        for mode, a in self.adapters.items():
            if a.is_live and a.record.has_live_priority() and a.live_games:
                out.append(mode[: -len("_live")])
        return out

    def duration(self, mode: str) -> int:
        a = self.adapters.get(mode)
        return max(1, int(round(a.get_display_duration()))) if a else 15

    # scheduling ------------------------------------------------------

    def tick(self, now: Optional[float] = None) -> None:
        """Start any update that is due. Cheap; call every loop."""
        now = time.time() if now is None else now
        for rec in self.plugins.values():
            if not getattr(rec.instance, "enabled", True):
                continue
            if rec.thread is not None and rec.thread.is_alive():
                continue
            if now < rec.next_update:
                continue
            rec.thread = threading.Thread(target=self._run_update, args=(rec,),
                                          name=f"plugin-update-{rec.plugin_id}", daemon=True)
            rec.thread.start()

    def _run_update(self, rec: PluginRecord) -> None:
        started = time.time()
        try:
            rec.instance.update()
        except Exception as exc:  # noqa: BLE001
            if started - rec.last_error_log > 300:
                rec.last_error_log = started
                logger.error("Plugin %s update() failed: %s", rec.plugin_id, exc, exc_info=True)
        took = time.time() - started
        if took > 20:
            logger.warning("Plugin %s update() took %.1fs", rec.plugin_id, took)
        rec.next_update = time.time() + rec.interval()

    # controller feedback ---------------------------------------------

    def _after_display(self, adapter: PluginModeAdapter, shown: bool, force_clear: bool) -> None:
        c = self.controller
        if c is None:
            return
        if not shown:
            # Nothing to show: rotate past it, as Chuck's core does.
            if not adapter.is_live:
                c.force_change = True
            return
        if force_clear and not adapter.is_live:
            # The plugin's duration is only right once the mode has drawn its
            # first frame, so refresh the controller's cached value now.
            c._cached_mode_duration = self.duration(adapter.mode)

    def cleanup(self) -> None:
        for rec in self.plugins.values():
            try:
                rec.instance.cleanup()
            except Exception as exc:  # noqa: BLE001
                logger.debug("Plugin %s cleanup failed: %s", rec.plugin_id, exc)
