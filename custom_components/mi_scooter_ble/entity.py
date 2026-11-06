"""Base entity."""
from __future__ import annotations

from homeassistant.helpers.device_registry import CONNECTION_BLUETOOTH, DeviceInfo
from homeassistant.helpers.update_coordinator import CoordinatorEntity

from .const import CONF_MODEL, DOMAIN
from .coordinator import ScooterCoordinator


class ScooterEntity(CoordinatorEntity[ScooterCoordinator]):
    _attr_has_entity_name = True

    def __init__(self, coordinator: ScooterCoordinator, key: str) -> None:
        super().__init__(coordinator)
        self._key = key
        self._attr_translation_key = key
        self._attr_unique_id = f"{coordinator.address}_{key}"
        data = coordinator.data or {}
        self._attr_device_info = DeviceInfo(
            identifiers={(DOMAIN, coordinator.address)},
            connections={(CONNECTION_BLUETOOTH, coordinator.address)},
            name=coordinator.entry.title,
            manufacturer="Xiaomi",
            model=coordinator.entry.data.get(CONF_MODEL),
            sw_version=data.get("firmware"),
        )

    @property
    def available(self) -> bool:
        # Keep showing the last known values even when the scooter is away.
        return True

    @property
    def value(self):
        return (self.coordinator.data or {}).get(self._key)
