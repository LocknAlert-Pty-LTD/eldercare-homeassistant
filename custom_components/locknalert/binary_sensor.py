"""Binary sensors: one per kind of LocknAlert alert, plus 'any alert active'."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from homeassistant.components.binary_sensor import (
    BinarySensorEntity,
    BinarySensorEntityDescription,
)
from homeassistant.components.binary_sensor.const import BinarySensorDeviceClass
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddConfigEntryEntitiesCallback

from .api import HouseholdStatus
from .const import ALERT_KEYS
from .coordinator import LocknAlertConfigEntry, LocknAlertCoordinator
from .entity import LocknAlertEntity

# Read-only polling through one coordinator: no parallel update limit needed
PARALLEL_UPDATES = 0


@dataclass(frozen=True, kw_only=True)
class LocknAlertBinarySensorDescription(BinarySensorEntityDescription):
    """Describes a LocknAlert alert binary sensor."""

    is_on_fn: Callable[[HouseholdStatus], bool]


def _alert_flag(key: str) -> Callable[[HouseholdStatus], bool]:
    """Return a reader for one alert flag.

    Args:
        key: The alert flag's name.

    Returns:
        A function reading that flag from a status.
    """

    def read(status: HouseholdStatus) -> bool:
        """Return whether the flag is set.

        Args:
            status: The household status.

        Returns:
            The flag's value.
        """
        return status.alerts[key]

    return read


# "Watch off" is a problem to fix, not a safety alarm; the rest are unsafe states
_PROBLEM_KEYS = frozenset({"watch_off"})

BINARY_SENSORS: tuple[LocknAlertBinarySensorDescription, ...] = (
    LocknAlertBinarySensorDescription(
        key="active_alert",
        translation_key="active_alert",
        device_class=BinarySensorDeviceClass.SAFETY,
        is_on_fn=lambda status: status.active_alert,
    ),
    *(
        LocknAlertBinarySensorDescription(
            key=key,
            translation_key=key,
            device_class=(
                BinarySensorDeviceClass.PROBLEM
                if key in _PROBLEM_KEYS
                else BinarySensorDeviceClass.SAFETY
            ),
            is_on_fn=_alert_flag(key),
        )
        for key in ALERT_KEYS
    ),
)


async def async_setup_entry(
    hass: HomeAssistant,
    entry: LocknAlertConfigEntry,
    async_add_entities: AddConfigEntryEntitiesCallback,
) -> None:
    """Set up the alert binary sensors for a household.

    Args:
        hass: The Home Assistant instance.
        entry: The config entry.
        async_add_entities: Adds the entities.
    """
    del hass
    coordinator = entry.runtime_data
    async_add_entities(
        LocknAlertBinarySensor(coordinator, description)
        for description in BINARY_SENSORS
    )


class LocknAlertBinarySensor(LocknAlertEntity, BinarySensorEntity):
    """Whether one kind of alert is active in the household."""

    entity_description: LocknAlertBinarySensorDescription

    def __init__(
        self,
        coordinator: LocknAlertCoordinator,
        description: LocknAlertBinarySensorDescription,
    ) -> None:
        """Initialise the sensor.

        Args:
            coordinator: The household's coordinator.
            description: Which alert it reports.
        """
        super().__init__(coordinator, description)

    @property
    def is_on(self) -> bool:
        """Return True while this kind of alert is active."""
        return self.entity_description.is_on_fn(self.coordinator.data)
