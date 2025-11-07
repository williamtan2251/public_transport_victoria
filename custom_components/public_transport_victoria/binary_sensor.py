"""Binary sensors for Public Transport Victoria disruptions."""

from __future__ import annotations

import datetime
import logging
from typing import Any

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.helpers.entity import DeviceInfo, EntityCategory
from homeassistant.helpers.update_coordinator import CoordinatorEntity
from homeassistant.const import ATTR_ATTRIBUTION

from .const import ATTRIBUTION, DOMAIN

_LOGGER = logging.getLogger(__name__)

# Entity descriptions for better organization
CURRENT_DISRUPTION_BINARY_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key="current_disruption_active",
    name="Current Disruption Active",
    icon="mdi:alert",
    device_class="problem",
    entity_category=EntityCategory.DIAGNOSTIC,
)

PLANNED_DISRUPTION_BINARY_SENSOR_DESCRIPTION = BinarySensorEntityDescription(
    key="planned_disruption_active",
    name="Planned Disruption Active",
    icon="mdi:calendar-alert",
    device_class="problem",
    entity_category=EntityCategory.DIAGNOSTIC,
)


async def async_setup_entry(hass, config_entry, async_add_entities):
    """Set up binary sensors for Public Transport Victoria from a config entry."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    connector = entry_data["connector"]

    # Read configuration options
    planned_enabled = config_entry.options.get("planned_disruptions", True)

    # Use existing coordinator from sensor setup
    if "coordinator" not in entry_data:
        # Import here to avoid circular imports
        from .sensor import PublicTransportVictoriaGlobalCoordinator
        entry_data["coordinator"] = PublicTransportVictoriaGlobalCoordinator(hass, connector)
    
    coordinator = entry_data["coordinator"]

    # Ensure we have initial data
    await coordinator.async_config_entry_first_refresh()

    entities = [
        PTVDisruptionBinarySensor(coordinator, connector, current=True),
    ]

    # Add planned disruption sensor if enabled
    if planned_enabled:
        entities.append(PTVDisruptionBinarySensor(coordinator, connector, current=False))

    async_add_entities(entities)


class PTVDisruptionBinarySensor(CoordinatorEntity, BinarySensorEntity):
    """Binary sensor that is on when there are disruptions."""

    def __init__(self, coordinator, connector, current: bool):
        """Initialize the disruption binary sensor."""
        super().__init__(coordinator)
        self._connector = connector
        self._current = current

        # Set entity description based on type
        if current:
            self.entity_description = CURRENT_DISRUPTION_BINARY_SENSOR_DESCRIPTION
        else:
            self.entity_description = PLANNED_DISRUPTION_BINARY_SENSOR_DESCRIPTION

        # Set unique ID and name
        disruption_type = "current" if current else "planned"
        self._attr_unique_id = f"{connector.route}-{disruption_type}-disruptions-binary"
        self._attr_name = f"{connector.route_name} {disruption_type.title()} Disruption Active"

    @property
    def is_on(self) -> bool:
        """Return True if there are disruptions."""
        disruptions = self._get_disruptions()
        return bool(disruptions)

    @property
    def available(self) -> bool:
        """Return if entity is available."""
        return (
            self.coordinator.last_update_success
            and self.coordinator.data is not None
            and super().available
        )

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes."""
        disruptions = self._get_disruptions()
        disruption_count = len(disruptions)
        
        attr = {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "disruption_type": "current" if self._current else "planned",
            "disruption_count": disruption_count,
        }

        # Add first disruption details if available
        if disruptions:
            first_disruption = disruptions[0]
            attr.update({
                "latest_disruption_title": first_disruption.get("title"),
                "latest_disruption_url": first_disruption.get("url"),
                "latest_update": first_disruption.get("last_updated"),
            })

        return attr

    def _get_disruptions(self) -> list[dict]:
        """Safe method to get disruptions data."""
        if not self.coordinator.data:
            return []
        
        key = "disruptions_current" if self._current else "disruptions_planned"
        return self.coordinator.data.get(key, [])

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
    def icon(self) -> str:
        """Return dynamic icon based on state and type."""
        if self.is_on:
            return "mdi:alert-octagon" if self._current else "mdi:calendar-alert"
        else:
            return "mdi:check-circle" if self._current else "mdi:calendar-check"
