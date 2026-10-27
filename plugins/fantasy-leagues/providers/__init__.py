"""League providers: one per fantasy site, all returning the same Matchup shape."""

from .base import AuthError, LeagueProvider, ProviderError  # noqa: F401
from .espn import EspnProvider

PROVIDERS = {"espn": EspnProvider}
