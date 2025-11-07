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
        self._attr_name = f"{self.coordinator.connector.route_name} line to {self.coordinator.connector.direction_name} from {self.coordinator.connector.stop_name} disruptions active"
        self._attr_unique_id = f"{self.coordinator.connector.route}-{self.coordinator.connector.direction}-{self.coordinator.connector.stop}-disruptions"
        self._attr_icon = "mdi:alert"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(self.coordinator.connector.route))},
            "name": f"{self.coordinator.connector.route_name} line",
            "manufacturer": "Public Transport Victoria",
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
