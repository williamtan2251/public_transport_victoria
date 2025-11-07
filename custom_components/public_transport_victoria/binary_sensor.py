"""Binary sensors for Public Transport Victoria disruptions."""
import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity, DataUpdateCoordinator
from homeassistant.const import ATTR_ATTRIBUTION

from .const import ATTRIBUTION, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up binary sensors for Public Transport Victoria from a config entry."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    connector = entry_data["connector"]

    from .sensor import PublicTransportVictoriaGlobalCoordinator

    if "coordinator" not in entry_data:
        entry_data["coordinator"] = PublicTransportVictoriaGlobalCoordinator(hass, connector)
    coordinator = entry_data["coordinator"]

    await coordinator.async_config_entry_first_refresh()
    async_add_entities([PTVCurrentDisruptionsBinarySensor(coordinator)])


class PTVCurrentDisruptionsBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor that is on when there are current disruptions."""

    def __init__(self, coordinator: DataUpdateCoordinator):
        """Initialize the binary sensor."""
        super().__init__(coordinator)
        connector = self.coordinator.connector
        self._attr_name = f"Active Disruption {connector.stop_name} to {connector.direction_name}"
        self._attr_unique_id = f"{connector.route}-{connector.direction}-{connector.stop}-disruptions"
        self._attr_icon = "mdi:alert"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(connector.route))},
            "name": f"{connector.route_name} line",
            "manufacturer": "Public Transport Victoria",
            "model": f"{connector.stop_name} to {connector.direction_name}",
        }

    @property
    def is_on(self) -> bool:
        """Return True if there are current disruptions."""
        dis = (self.coordinator.data or {}).get("disruptions_current") or []
        return bool(dis)

    @property
    def extra_state_attributes(self) -> dict:
        """Return the state attributes."""
        return {ATTR_ATTRIBUTION: ATTRIBUTION}
