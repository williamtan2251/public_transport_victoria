"""Binary sensors for Public Transport Victoria disruptions."""
from __future__ import annotations

import logging
from typing import Any, TYPE_CHECKING

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass,
    BinarySensorEntity,
)
from homeassistant.const import ATTR_ATTRIBUTION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import ATTRIBUTION, get_device_info
from .coordinator import PublicTransportVictoriaCoordinator

if TYPE_CHECKING:
    from . import PTVConfigEntry

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: PTVConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Set up binary sensors for Public Transport Victoria from a config entry."""
    async_add_entities([PTVCurrentDisruptionsBinarySensor(config_entry.runtime_data)])


class PTVCurrentDisruptionsBinarySensor(
    CoordinatorEntity[PublicTransportVictoriaCoordinator],
    BinarySensorEntity,
):
    """Binary sensor that is on when there are current disruptions."""

    _attr_has_entity_name = True
    _attr_name = "Active disruption"
    _attr_device_class = BinarySensorDeviceClass.PROBLEM
    _attr_icon = "mdi:alert"

    def __init__(self, coordinator: PublicTransportVictoriaCoordinator) -> None:
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        connector = coordinator.connector
        self._attr_unique_id = (
            f"{connector.route}-{connector.direction}-{connector.stop}-disruptions"
        )
        self._attr_device_info = get_device_info(connector)

    @property
    def is_on(self) -> bool:
        """Return True if there are current disruptions."""
        dis = (self.coordinator.data or {}).get("disruptions_current") or []
        return bool(dis)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        return {ATTR_ATTRIBUTION: ATTRIBUTION}
