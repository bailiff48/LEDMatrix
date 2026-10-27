"""Slim BasePlugin, API-compatible with ChuckBuilds' LEDMatrix plugin core.

Only the surface that plugins actually call is here: identity, config,
logger, the update/display contract, duration, update interval, live
priority, lifecycle hooks and get_info(). Vegas-ticker, layout and style
helpers are deliberately left out; plugins import those inside try/except
and fall back when they are missing.

Reference: ChuckBuilds/LEDMatrix src/plugin_system/base_plugin.py (v3.8.7).
"""

import logging
from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class BasePlugin(ABC):
    """Base class every plugin inherits from."""

    API_VERSION = "1.0.0"

    def __init__(
        self,
        plugin_id: str,
        config: Dict[str, Any],
        display_manager: Any,
        cache_manager: Any,
        plugin_manager: Any,
    ) -> None:
        self.plugin_id: str = plugin_id
        self.config: Dict[str, Any] = config or {}
        self.display_manager: Any = display_manager
        self.cache_manager: Any = cache_manager
        self.plugin_manager: Any = plugin_manager
        self.logger = logging.getLogger(f"plugin.{plugin_id}")
        self.enabled: bool = self.config.get("enabled", True)
        self.logger.info("Initialized plugin: %s", plugin_id)

    # ------------------------------------------------------------------
    # the contract
    # ------------------------------------------------------------------

    @abstractmethod
    def update(self) -> None:
        """Fetch data. All network calls belong here, never in display()."""

    @abstractmethod
    def display(self, force_clear: bool = False) -> Any:
        """Draw the current frame from memory.

        Plugins with several display modes accept ``display_mode`` too.
        Returning False means "nothing to show"; the host rotates past it.
        """

    # ------------------------------------------------------------------
    # timing
    # ------------------------------------------------------------------

    def get_display_duration(self) -> float:
        """Seconds to stay on this plugin: display_duration attr, config, 15."""
        for value in (getattr(self, "display_duration", None),
                      self.config.get("display_duration")):
            seconds = _positive(value)
            if seconds is not None:
                return seconds
        return 15.0

    def get_update_interval(self) -> Optional[float]:
        """Seconds until the next update() wanted; None uses config/manifest.

        Called every loop tick: attribute reads only, must not raise.
        """
        return None

    # ------------------------------------------------------------------
    # live priority
    # ------------------------------------------------------------------

    def has_live_priority(self) -> bool:
        return bool(self.config.get("live_priority", False))

    def has_live_content(self) -> bool:
        return False

    def get_live_modes(self) -> List[str]:
        """Modes to show when taking over for live content (default: all)."""
        modes = getattr(self, "modes", None)
        return list(modes) if modes else []

    # ------------------------------------------------------------------
    # lifecycle
    # ------------------------------------------------------------------

    def validate_config(self) -> bool:
        if "enabled" in self.config and not isinstance(self.config["enabled"], bool):
            self.logger.error("'enabled' must be a boolean")
            return False
        if "display_duration" in self.config and _positive(self.config["display_duration"]) is None:
            self.logger.error("'display_duration' must be a positive number")
            return False
        return True

    def on_config_change(self, new_config: Dict[str, Any]) -> None:
        self.config = new_config or {}
        self.enabled = self.config.get("enabled", self.enabled)

    def on_enable(self) -> None:
        self.enabled = True
        self.logger.info("Plugin enabled: %s", self.plugin_id)

    def on_disable(self) -> None:
        self.enabled = False
        self.logger.info("Plugin disabled: %s", self.plugin_id)

    def cleanup(self) -> None:
        self.logger.info("Cleaning up plugin: %s", self.plugin_id)

    def get_info(self) -> Dict[str, Any]:
        return {
            "id": self.plugin_id,
            "enabled": self.enabled,
            "config": self.config,
            "api_version": self.API_VERSION,
        }


def _positive(value: Any) -> Optional[float]:
    if isinstance(value, bool):
        return None
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return None
    return seconds if seconds > 0 else None
