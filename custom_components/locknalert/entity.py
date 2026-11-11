"""Base entity for the LocknAlert integration."""

from __future__ import annotations

from homeassistant.helpers.device_registry import DeviceEntryType, DeviceInfo
from homeassistant.helpers.entity import EntityDescription
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import DOMAIN, entry_unique_id
from .coordinator import LocknAlertCoordinator


class LocknAlertEntity(CoordinatorEntity[LocknAlertCoordinator]):
    """An entity belonging to one person in a LocknAlert household.

    Each person who links Home Assistant gets their own device, so their
    alerts are never mixed with another member's.
    """

    _attr_has_entity_name = True

    def __init__(
        self, coordinator: LocknAlertCoordinator, description: EntityDescription
    ) -> None:
        """Initialise the entity.

        Args:
            coordinator: The person's coordinator.
            description: What the entity reports.
        """
        super().__init__(coordinator)
        self.entity_description = description
        device_id = entry_unique_id(coordinator.serial, coordinator.person_ref)
        self._attr_unique_id = f"{device_id}_{description.key}"
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, device_id)},
            # The entry title names the person, e.g. "Mary (LNA-00042)"
            name=coordinator.config_entry.title,
            manufacturer="LocknAlert",
            model="Eldercare household",
            serial_number=coordinator.serial,
            entry_type=DeviceEntryType.SERVICE,
            configuration_url="https://eldercare.locknalert.co.za/eldercare/dashboard",
        )
