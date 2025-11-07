"""Platform for sensor integration."""

from __future__ import annotations

import datetime
import logging
import re
from typing import Any, cast

from homeassistant.components.sensor import SensorEntity, SensorEntityDescription
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import (
    CoordinatorEntity,
    DataUpdateCoordinator,
)
from homeassistant.const import ATTR_ATTRIBUTION
from .const import ATTRIBUTION, DOMAIN, DEFAULT_DETAILS_LIMIT, CONF_ROUTE, CONF_DIRECTION, CONF_STOP

_LOGGER = logging.getLogger(__name__)
SCAN_INTERVAL = datetime.timedelta(minutes=1)

# Entity descriptions for better organization
DEPARTURE_SENSOR_DESCRIPTION = SensorEntityDescription(
    key="departure",
    icon="mdi:clock-outline",
    device_class=None,  # Not a standard device class
)

DISRUPTION_COUNT_SENSOR_DESCRIPTION = SensorEntityDescription(
    key="disruption_count",
    icon="mdi:alert",
    entity_category=EntityCategory.DIAGNOSTIC,
)

DISRUPTION_DETAIL_SENSOR_DESCRIPTION = SensorEntityDescription(
    key="disruption_detail",
    icon="mdi:note-text",
    entity_category=EntityCategory.DIAGNOSTIC,
)

async def async_setup_entry(hass, config_entry, async_add_entities):
    """Add sensors for passed config_entry in HA."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    connector = entry_data["connector"]
    config_data = config_entry.data

    # Read options with fallbacks
    details_limit = config_entry.options.get("details_limit", DEFAULT_DETAILS_LIMIT)
    departures_limit = config_entry.options.get("departures_limit", 5)
    planned_enabled = config_entry.options.get("planned_disruptions", True)
    simplified_disruptions = config_entry.options.get("simplified_disruptions", True)

    # Create or get global coordinator
    if "coordinator" not in entry_data:
        entry_data["coordinator"] = PublicTransportVictoriaGlobalCoordinator(hass, connector)
    coordinator = entry_data["coordinator"]

    # Fetch initial data
    await coordinator.async_config_entry_first_refresh()

    entities: list[BasePTVSensor] = []

    # Create departure sensors
    entities.extend([
        DepartureSensor(coordinator, i, connector) 
        for i in range(departures_limit)
    ])

    # Create disruption sensors
    entities.append(DisruptionCountSensor(coordinator, connector, current=True))
    
    if simplified_disruptions:
        entities.append(DisruptionDetailSensor(
            coordinator, connector, current=True, 
            details_limit=details_limit, simplified=True
        ))
    
    entities.append(DisruptionDetailSensor(
        coordinator, connector, current=True, 
        details_limit=details_limit, simplified=False
    ))

    # Planned disruptions (optional)
    if planned_enabled:
        entities.append(DisruptionCountSensor(coordinator, connector, current=False))
        
        if simplified_disruptions:
            entities.append(DisruptionDetailSensor(
                coordinator, connector, current=False, 
                details_limit=details_limit, simplified=True
            ))
        
        entities.append(DisruptionDetailSensor(
            coordinator, connector, current=False, 
            details_limit=details_limit, simplified=False
        ))

    async_add_entities(entities)


class PublicTransportVictoriaGlobalCoordinator(DataUpdateCoordinator):
    """Class to manage fetching Public Transport Victoria data."""

    def __init__(self, hass, connector):
        """Initialize the coordinator."""
        self.connector = connector
        self._last_update_success = True
        
        super().__init__(
            hass,
            _LOGGER,
            name=f"PTV {connector.route_name}",
            update_interval=SCAN_INTERVAL,
        )

    async def _async_update_data(self):
        """Fetch all data from Public Transport Victoria."""
        try:
            _LOGGER.debug("Fetching all data from Public Transport Victoria API for %s", self.connector.route_name)
            await self.connector.async_update_all()
            self._last_update_success = True
            
            return {
                "departures": self.connector.departures or [],
                "disruptions_current": self.connector.disruptions_current or [],
                "disruptions_planned": self.connector.disruptions_planned or [],
                "last_update": datetime.datetime.now().isoformat(),
            }
        except Exception as err:
            self._last_update_success = False
            _LOGGER.error("Error fetching PTV data: %s", err)
            raise


class BasePTVSensor(CoordinatorEntity, SensorEntity):
    """Base class for PTV sensors with common functionality."""

    def __init__(self, coordinator, connector):
        """Initialize the base sensor."""
        super().__init__(coordinator)
        self._connector = connector
        self._attr_attribution = ATTRIBUTION

    @property
    def device_info(self) -> DeviceInfo:
        """Return device information."""
        return DeviceInfo(
            identifiers={(DOMAIN, str(self._connector.route))},
            name=f"{self._connector.route_name} Line",
            manufacturer="Public Transport Victoria",
            model=f"{self._connector.route_name} to {self._connector.direction_name}",
            via_device=(DOMAIN, "ptv_public_transport_victoria"),
        )

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and super().available
        )


class DepartureSensor(BasePTVSensor):
    """Representation of a departure sensor."""

    def __init__(self, coordinator, number, connector):
        """Initialize the departure sensor."""
        super().__init__(coordinator, connector)
        self.entity_description = DEPARTURE_SENSOR_DESCRIPTION
        self._number = number
        self._attr_unique_id = f"{connector.route}-{connector.direction}-{connector.stop}-dep-{number}"
        self._attr_name = f"{connector.route_name} to {connector.direction_name} #{number + 1}"

    @property
    def state(self) -> str:
        """Return the state of the sensor."""
        deps = self._get_departures()
        if len(deps) > self._number:
            return deps[self._number].get("departure", "No data")
        return "No data"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        deps = self._get_departures()
        if len(deps) > self._number:
            attr = dict(deps[self._number])
            attr[ATTR_ATTRIBUTION] = ATTRIBUTION
            attr["departure_number"] = self._number + 1
            return attr
        return {}

    def _get_departures(self) -> list[dict]:
        """Safe method to get departures data."""
        if not self.coordinator.data:
            return []
        return self.coordinator.data.get("departures", [])

    @property
    def icon(self) -> str:
        """Return dynamic icon based on route type."""
        route_type = self._connector.route_type
        icon_map = {
            0: "mdi:train",      # Train
            1: "mdi:tram",       # Tram
            2: "mdi:bus",        # Bus
            3: "mdi:ferry",      # V/Line (using ferry as proxy)
            4: "mdi:bus",        # Night Bus
        }
        return icon_map.get(route_type, "mdi:transit-connection")


class DisruptionCountSensor(BasePTVSensor):
    """Representation of a disruptions count sensor."""

    def __init__(self, coordinator, connector, current: bool):
        """Initialize the disruption count sensor."""
        super().__init__(coordinator, connector)
        self.entity_description = DISRUPTION_COUNT_SENSOR_DESCRIPTION
        self._current = current
        disruption_type = "current" if current else "planned"
        self._attr_unique_id = f"{connector.route}-{disruption_type}-disruptions-count"
        self._attr_name = f"{connector.route_name} {disruption_type.title()} Disruption Count"

    @property
    def state(self) -> int:
        """Return the number of disruptions."""
        disruptions = self._get_disruptions()
        return len(disruptions)

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        disruptions = self._get_disruptions()
        return {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "disruption_type": "current" if self._current else "planned",
            "disruption_titles": [d.get("title", "Unknown") for d in disruptions[:5]],  # First 5 titles
        }

    def _get_disruptions(self) -> list[dict]:
        """Safe method to get disruptions data."""
        if not self.coordinator.data:
            return []
        key = "disruptions_current" if self._current else "disruptions_planned"
        return self.coordinator.data.get(key, [])

    @property
    def icon(self) -> str:
        """Return dynamic icon."""
        return "mdi:alert-octagon" if self._current else "mdi:calendar-alert"


class DisruptionDetailSensor(BasePTVSensor):
    """Representation of a disruptions detail sensor."""

    def __init__(self, coordinator, connector, current: bool, details_limit: int, simplified: bool = False):
        """Initialize the disruption detail sensor."""
        super().__init__(coordinator, connector)
        self.entity_description = DISRUPTION_DETAIL_SENSOR_DESCRIPTION
        self._current = current
        self._details_limit = details_limit
        self._simplified = simplified
        
        disruption_type = "current" if current else "planned"
        suffix = "simplified" if simplified else "detailed"
        self._attr_unique_id = f"{connector.route}-{disruption_type}-disruptions-{suffix}"
        
        name_suffix = "Simplified" if simplified else "Detailed"
        self._attr_name = f"{connector.route_name} {disruption_type.title()} Disruptions {name_suffix}"

    @property
    def state(self) -> str:
        """Return the state with first disruption details."""
        try:
            disruptions = self._get_disruptions()
            if not disruptions:
                return "No disruptions"

            if self._simplified:
                return self._get_simplified_state(disruptions[0])
            else:
                title = disruptions[0].get("title", "Disruption")
                return self._truncate_state(title)
                
        except Exception as err:
            _LOGGER.error("Error in disruption sensor state: %s", err)
            return "Error"

    def _get_simplified_state(self, disruption: dict) -> str:
        """Get simplified state with cleaned up title and date."""
        raw_title = disruption.get("title", "Disruption")
        title = disruption.get("title_clean", raw_title)
        rel = disruption.get("period_relative", "")
        
        if not rel:
            return self._truncate_state(title)
        
        # Handle date range formatting
        date_range_pattern = r'from\s+\w+.*?\s+(?:to|until)\s+\w+.*?(?:\s|$)'
        if (rel.startswith("from ") and " until " in rel and
                re.search(date_range_pattern, raw_title, re.IGNORECASE)):
            
            # Clean up the title
            if " until " in title:
                title = title.split(" until ", 1)[0]
            elif " to " in title:
                title = title.split(" to ", 1)[0]
                if " from " in title:
                    title = title.replace(" from ", " ", 1)
            
            # Extract the until part
            until_part = rel.split(" until ", 1)[1]
            result = f"{title} until {until_part}"
        else:
            result = f"{title} — {rel}"
        
        return self._truncate_state(result)

    def _truncate_state(self, text: str) -> str:
        """Truncate state text to 255 characters."""
        if len(text) > 255:
            return text[:252] + "..."
        return text

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return comprehensive disruption attributes."""
        disruptions = self._get_disruptions()
        limited_disruptions = disruptions[:self._details_limit]
        
        attr = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "disruption_type": "current" if self._current else "planned",
            "total_disruptions": len(disruptions),
            "displayed_disruptions": len(limited_disruptions),
            "simplified_format": self._simplified,
        }
        
        # Add detailed disruption info
        if limited_disruptions:
            attr["disruptions"] = limited_disruptions
            attr["latest_update"] = limited_disruptions[0].get("last_updated")
            
        return attr

    def _get_disruptions(self) -> list[dict]:
        """Safe method to get disruptions data."""
        if not self.coordinator.data:
            return []
        key = "disruptions_current" if self._current else "disruptions_planned"
        return self.coordinator.data.get(key, [])

    @property
    def icon(self) -> str:
        """Return dynamic icon."""
        return "mdi:text-box-multiple" if self._simplified else "mdi:text-box"
