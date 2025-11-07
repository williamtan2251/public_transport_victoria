"""Platform for sensor integration."""

import datetime
import logging

from homeassistant.helpers.entity import Entity
from homeassistant.helpers.update_coordinator import (
    DataUpdateCoordinator,
    CoordinatorEntity,
)
from homeassistant.const import ATTR_ATTRIBUTION
from .const import ATTRIBUTION, DOMAIN, DEFAULT_DETAILS_LIMIT

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = datetime.timedelta(minutes=1)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Add sensors for passed config_entry in HA."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    connector = entry_data["connector"]

    details_limit = DEFAULT_DETAILS_LIMIT

    # Create the coordinator to manage polling
    if "coordinator" not in entry_data:
        entry_data["coordinator"] = PublicTransportVictoriaGlobalCoordinator(hass, connector)
    coordinator = entry_data["coordinator"]

    await coordinator.async_config_entry_first_refresh()

    # Create sensors for the first 5 departures
    new_devices = [PublicTransportVictoriaSensor(coordinator, i) for i in range(5)]

    # Create current disruptions sensors only (no planned disruptions)
    new_devices.append(PublicTransportVictoriaDisruptionsCountSensor(coordinator))
    new_devices.append(PublicTransportVictoriaDisruptionsDetailSensor(coordinator, details_limit=details_limit))

    async_add_entities(new_devices)

class PublicTransportVictoriaGlobalCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Public Transport Victoria data."""

    def __init__(self, hass, connector):
        """Initialize the coordinator."""
        self.connector = connector
        super().__init__(
            hass,
            _LOGGER,
            name="Public Transport Victoria (Global)",
            update_interval=datetime.timedelta(minutes=1),
        )

    async def _async_update_data(self):
        """Fetch all data from Public Transport Victoria."""
        _LOGGER.debug("Fetching all data from Public Transport Victoria API.")
        await self.connector.async_update_all()
        return {
            "departures": self.connector.departures,
            "disruptions_current": self.connector.disruptions_current,
        }

class PublicTransportVictoriaSensor(CoordinatorEntity, Entity):
    """Sensor for a single departure."""

    def __init__(self, coordinator: DataUpdateCoordinator, number: int):
        super().__init__(coordinator)
        self._number = number
        self._connector = coordinator.connector
        self._attr_name = f"{self._connector.route_name} line to {self._connector.direction_name} from {self._connector.stop_name} {self._number}"
        self._attr_unique_id = f"{self._connector.route}-{self._connector.direction}-{self._connector.stop}-dep-{self._number}"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(self._connector.route))},
            "name": f"{self._connector.route_name} line",
            "manufacturer": "Public Transport Victoria",
            "model": f"{self._connector.stop_name} to {self._connector.direction_name}",
        }

    @property
    def state(self):
        """Return the state of the sensor."""
        deps = (self.coordinator.data or {}).get("departures", [])
        if len(deps) > self._number:
            return deps[self._number].get("departure", "No data")
        return "No data"

    @property
    def extra_state_attributes(self):
        """Return the state attributes of the sensor."""
        deps = (self.coordinator.data or {}).get("departures", [])
        if len(deps) > self._number:
            attr = dict(deps[self._number])  # Copy to avoid mutating coordinator data
            attr[ATTR_ATTRIBUTION] = ATTRIBUTION
            return attr
        return {ATTR_ATTRIBUTION: ATTRIBUTION}

    @property
    def icon(self):
        """Return the icon based on route type."""
        rt = self._connector.route_type
        if rt == 0:
            return "mdi:train"
        if rt == 1:
            return "mdi:tram"
        if rt == 2:
            return "mdi:bus"
        return "mdi:transit-connection"

class PublicTransportVictoriaDisruptionsCountSensor(CoordinatorEntity, Entity):
    """Sensor for the count of current disruptions."""

    def __init__(self, coordinator: DataUpdateCoordinator):
        super().__init__(coordinator)
        self._attr_name = f"{self.coordinator.connector.stop_name} to {self.coordinator.connector.direction_name} disruptions"
        self._attr_unique_id = f"{self.coordinator.connector.route}-{self.coordinator.connector.direction}-{self.coordinator.connector.stop}-disruptions-count"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(self.coordinator.connector.route))},
            "name": f"{self.coordinator.connector.route_name} line",
            "manufacturer": "Public Transport Victoria",
            "model": f"{self.coordinator.connector.stop_name} to {self.coordinator.connector.direction_name}",
        }
        self._attr_icon = "mdi:alert"

    @property
    def state(self):
        """Return the number of current disruptions."""
        data = self.coordinator.data or {}
        dis = data.get("disruptions_current") or []
        return len(dis)

    @property
    def extra_state_attributes(self):
        """Return the state attributes."""
        return {ATTR_ATTRIBUTION: ATTRIBUTION}

class PublicTransportVictoriaDisruptionsDetailSensor(CoordinatorEntity, Entity):
    """Sensor for details of current disruptions."""

    def __init__(self, coordinator: DataUpdateCoordinator, details_limit: int):
        super().__init__(coordinator)
        self._details_limit = details_limit
        self._attr_name = f"{self.coordinator.connector.stop_name} to {self.coordinator.connector.direction_name} current disruption"
        self._attr_unique_id = f"{self.coordinator.connector.route}-{self.coordinator.connector.direction}-{self.coordinator.connector.stop}-disruptions-detail"
        self._attr_device_info = {
            "identifiers": {(DOMAIN, str(self.coordinator.connector.route))},
            "name": f"{self.coordinator.connector.route_name} line",
            "manufacturer": "Public Transport Victoria",
            "model": f"{self.coordinator.connector.stop_name} to {self.coordinator.connector.direction_name}",
        }
        self._attr_icon = "mdi:note-text"

    @property
    def state(self):
        """Return a brief state: first disruption title, else 'No disruptions'."""
        try:
            data = self.coordinator.data or {}
            dis = data.get("disruptions_current") or []
            if dis:
                raw_title = dis[0].get("title") or "Disruption"
                title = dis[0].get("title_clean") or raw_title
                rel = dis[0].get("period_relative")
                result = title
                if rel:
                    import re
                    date_range_pattern = r'from\s+\w+.*?\s+(?:to|until)\s+\w+.*?(?:\s|$)'
                    if (rel.startswith("from ") and " until " in rel and
                            re.search(date_range_pattern, raw_title, re.IGNORECASE)):
                        if " until " in title:
                            title = title.split(" until ", 1)[0]
                        elif " to " in title:
                            title = title.split(" to ", 1)[0]
                            if " from " in title:
                                title = title.replace(" from ", " ", 1)
                        until_part = rel.split(" until ", 1)[1]
                        result = f"{title} until {until_part}"
                    else:
                        result = f"{title} — {rel}"

                if len(result) > 255:
                    result = result[:252] + "..."

                return result
            return "No disruptions"
        except Exception as e:
            _LOGGER.error("Error in disruption sensor state: %s", e)
            return "Error"

    @property
    def extra_state_attributes(self):
        """Return detailed disruption attributes."""
        data = self.coordinator.data or {}
        dis = data.get("disruptions_current") or []
        disruptions = dis[: self._details_limit]
        attr = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "disruptions": disruptions,
            "total_disruptions": len(dis),
            "period_relative": disruptions[0].get("period_relative") if disruptions else None,
        }
        return attr
