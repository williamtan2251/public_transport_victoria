"""Platform for sensor integration."""
from __future__ import annotations

import logging
from typing import Any

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import ATTR_ATTRIBUTION
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import (
    ATTRIBUTION,
    DEFAULT_DETAILS_LIMIT,
    DEFAULT_ICON,
    DOMAIN,
    ROUTE_TYPE_ICONS,
    get_device_info,
)
from .coordinator import PublicTransportVictoriaCoordinator

_LOGGER = logging.getLogger(__name__)


async def async_setup_entry(
    hass: HomeAssistant,
    config_entry: ConfigEntry,
    async_add_entities: AddEntitiesCallback,
) -> None:
    """Add sensors for passed config_entry in HA."""
    entry_data = hass.data[DOMAIN][config_entry.entry_id]
    coordinator: PublicTransportVictoriaCoordinator = entry_data["coordinator"]

    entities: list[CoordinatorEntity] = [
        PublicTransportVictoriaSensor(coordinator, i) for i in range(5)
    ]
    entities.append(
        PublicTransportVictoriaDisruptionsDetailSensor(
            coordinator, details_limit=DEFAULT_DETAILS_LIMIT
        )
    )
    async_add_entities(entities)


class PublicTransportVictoriaSensor(CoordinatorEntity[PublicTransportVictoriaCoordinator]):
    """Sensor for a single departure."""

    def __init__(
        self,
        coordinator: PublicTransportVictoriaCoordinator,
        number: int,
    ) -> None:
        """Initialize the departure sensor."""
        super().__init__(coordinator)
        self._number = number
        connector = coordinator.connector
        self._attr_name = (
            f"{connector.route_name} line {connector.stop_name} "
            f"to {connector.direction_name} {number}"
        )
        self._attr_unique_id = (
            f"{connector.route}-{connector.direction}-{connector.stop}-dep-{number}"
        )
        self._attr_device_info = get_device_info(connector)
        self._attr_icon = ROUTE_TYPE_ICONS.get(connector.route_type, DEFAULT_ICON)

    @property
    def state(self) -> str:
        """Return the state of the sensor."""
        deps = (self.coordinator.data or {}).get("departures", [])
        if len(deps) > self._number:
            return deps[self._number].get("departure", "No data")
        return "No data"

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return the state attributes of the sensor."""
        deps = (self.coordinator.data or {}).get("departures", [])
        if len(deps) > self._number:
            attr = dict(deps[self._number])
            attr[ATTR_ATTRIBUTION] = ATTRIBUTION
            return attr
        return {ATTR_ATTRIBUTION: ATTRIBUTION}


class PublicTransportVictoriaDisruptionsDetailSensor(
    CoordinatorEntity[PublicTransportVictoriaCoordinator],
):
    """Sensor for details of current disruptions."""

    def __init__(
        self,
        coordinator: PublicTransportVictoriaCoordinator,
        details_limit: int,
    ) -> None:
        """Initialize the disruptions detail sensor."""
        super().__init__(coordinator)
        connector = coordinator.connector
        self._details_limit = details_limit
        self._attr_name = f"Disruption {connector.stop_name} to {connector.direction_name}"
        self._attr_unique_id = (
            f"{connector.route}-{connector.direction}-{connector.stop}-disruptions-detail"
        )
        self._attr_device_info = get_device_info(connector)
        self._attr_icon = "mdi:note-text"

    @property
    def state(self) -> str:
        """Return a brief state: first disruption summary, else 'No disruptions'."""
        dis = (self.coordinator.data or {}).get("disruptions_current") or []
        if not dis:
            return "No disruptions"
        result = dis[0].get("state_text") or dis[0].get("title_clean") \
            or dis[0].get("title") or "Disruption"
        if len(result) > 255:
            result = result[:252] + "..."
        return result

    @property
    def extra_state_attributes(self) -> dict[str, Any]:
        """Return detailed disruption attributes."""
        data = self.coordinator.data or {}
        dis = data.get("disruptions_current") or []
        disruptions = dis[: self._details_limit]
        return {
            ATTR_ATTRIBUTION: ATTRIBUTION,
            "disruptions": disruptions,
            "total_disruptions": len(dis),
            "period_relative": disruptions[0].get("period_relative") if disruptions else None,
        }
