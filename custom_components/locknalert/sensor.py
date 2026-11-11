"""Sensor: how many alerts are open in the household."""

from __future__ import annotations

from homeassistant.components.sensor import (
    SensorEntity,
    SensorEntityDescription,
    SensorStateClass,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .coordinator import LocknAlertConfigEntry
from .entity import LocknAlertEntity

# Read-only polling through one coordinator: no parallel update limit needed
PARALLEL_UPDATES = 0

OPEN_ALERTS = SensorEntityDescription(
    key="open_alert_count",
    translation_key="open_alert_count",
    state_class=SensorStateClass.MEASUREMENT,
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LocknAlertConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the open-alerts sensor for a household.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry.
        async_add_entities: Adds the entities.
    """
    del hass
    async_add_entities([LocknAlertOpenAlertsSensor(entry.runtime_data, OPEN_ALERTS)])


class LocknAlertOpenAlertsSensor(LocknAlertEntity, SensorEntity):
    """The number of open (or escalated and still active) check-ins."""

    @property
    def native_value(self) -> int:
        """Return the number of open alerts."""
        return self.coordinator.data.open_alert_count
