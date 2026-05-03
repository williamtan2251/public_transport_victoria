"""Diagnostics support for Public Transport Victoria."""
from __future__ import annotations

from typing import Any, TYPE_CHECKING

from homeassistant.components.diagnostics import async_redact_data
from homeassistant.const import CONF_API_KEY, CONF_ID
from homeassistant.core import HomeAssistant

if TYPE_CHECKING:
    from . import PTVConfigEntry

TO_REDACT = {CONF_API_KEY, CONF_ID}


async def async_get_config_entry_diagnostics(
    hass: HomeAssistant, entry: PTVConfigEntry
) -> dict[str, Any]:
    """Return diagnostics for a config entry."""
    return {
        "config_entry": async_redact_data(entry.as_dict(), TO_REDACT),
        "coordinator_data": entry.runtime_data.data,
    }
