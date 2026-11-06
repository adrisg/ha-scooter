"""Binary sensors: in range, powered on, charging, connected, riding."""
from __future__ import annotations

from homeassistant.components.binary_sensor import (
    BinarySensorDeviceClass, BinarySensorEntity, BinarySensorEntityDescription,
)
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ScooterEntity

BINARY = (
    BinarySensorEntityDescription(key="in_range", device_class=BinarySensorDeviceClass.PRESENCE),
    BinarySensorEntityDescription(key="powered_on", device_class=BinarySensorDeviceClass.POWER),
    BinarySensorEntityDescription(key="charging_any", device_class=BinarySensorDeviceClass.BATTERY_CHARGING),
    # On when the battery is at or above the charge limit (for your own automations).
    BinarySensorEntityDescription(key="charged", icon="mdi:battery-check"),
    BinarySensorEntityDescription(key="riding", device_class=BinarySensorDeviceClass.MOVING),
    BinarySensorEntityDescription(key="locked", device_class=BinarySensorDeviceClass.LOCK, icon="mdi:lock"),
    BinarySensorEntityDescription(key="connected", device_class=BinarySensorDeviceClass.CONNECTIVITY,
                                  entity_category=EntityCategory.DIAGNOSTIC),
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(ScooterBinary(coordinator, d) for d in BINARY)


class ScooterBinary(ScooterEntity, BinarySensorEntity):
    def __init__(self, coordinator, description) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def is_on(self) -> bool | None:
        value = self.scooter_value
        if self._key == "locked" and value is not None:
            return not value  # HA lock class: on = unlocked
        return None if value is None else bool(value)
