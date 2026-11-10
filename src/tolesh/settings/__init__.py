"""User settings: validated, exported and deleted with everything else."""

from .store import DESCRIPTIONS, DEFAULTS, SettingsError, SettingsStore

__all__ = ["DEFAULTS", "DESCRIPTIONS", "SettingsError", "SettingsStore"]
