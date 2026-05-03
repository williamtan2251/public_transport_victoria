"""DataUpdateCoordinator for Public Transport Victoria."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.core import HomeAssistant
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator, UpdateFailed

from .api import Connector
from .const import SCAN_INTERVAL

_LOGGER = logging.getLogger(__name__)


class PublicTransportVictoriaCoordinator(DataUpdateCoordinator[dict[str, Any]]):
    """Coordinator to manage fetching Public Transport Victoria data."""

    def __init__(self, hass: HomeAssistant, connector: Connector) -> None:
        """Initialize the coordinator."""
        self.connector = connector
        super().__init__(
            hass,
            _LOGGER,
            name="Public Transport Victoria",
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self) -> dict[str, Any]:
        """Fetch all data from Public Transport Victoria."""
        _LOGGER.debug("Fetching all data from Public Transport Victoria API.")
        try:
            await self.connector.async_update_all()
        except Exception as err:
            raise UpdateFailed(f"Error communicating with PTV API: {err}") from err
        return {
            "departures": self.connector.departures,
            "disruptions_current": self.connector.disruptions_current,
        }
