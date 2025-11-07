"""Binary sensors for Public Transport Victoria disruptions."""
import datetime
import logging

from homeassistant.components.binary_sensor import BinarySensorEntity
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import ATTR_ATTRIBUTION

from .const import ATTRIBUTION, DOMAIN

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up binary sensors for Public Transport Victoria from a config entry."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    connector = entry_data["connector"]

    # Reuse the current disruptions coordinator from sensors if available
    # If not available, we simply won't create the binary sensor here.
    # For simplicity, rely on the count/detail sensors' coordinator to exist.
    # Users who disable planned sensors still have current coordinator.
    from .sensor import PublicTransportVictoriaGlobalCoordinator

    if "coordinator" not in entry_data:
        entry_data["coordinator"] = PublicTransportVictoriaGlobalCoordinator(hass, connector)
    coordinator = entry_data["coordinator"]

    await coordinator.async_config_entry_first_refresh()
    async_add_entities([PTVCurrentDisruptionsBinarySensor(coordinator)])


class PTVCurrentDisruptionsBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor that is on when there are current disruptions."""

    def __init__(self, coordinator):
        super().__init__(coordinator)

    @property
    def is_on(self):
        dis = (self.coordinator.data or {}).get("disruptions_current") or []
        return bool(dis)

    @property
    def name(self):
        return "{} line current disruption active".format(self.coordinator.connector.route_name)

    @property
    def unique_id(self):
        return "{}-current-disruptions-binary".format(self.coordinator.connector.route)

    @property
    def extra_state_attributes(self):
        return {ATTR_ATTRIBUTION: ATTRIBUTION}

    @property
    def device_info(self):
        return {
            "identifiers": {(DOMAIN, str(self.coordinator.connector.route))},
            "name": "{} line".format(self.coordinator.connector.route_name),
            "manufacturer": "Public Transport Victoria",
        }

    @property
    def icon(self):
        return "mdi:alert"
