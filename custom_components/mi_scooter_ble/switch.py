"""Switches: automatic charge cut-off and the scooter's writable settings."""
from __future__ import annotations

from typing import Any

from homeassistant.components.switch import SwitchEntity
from homeassistant.const import EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import AddEntitiesCallback
from homeassistant.helpers.restore_state import RestoreEntity

from .client import ScooterError
from .entity import ScooterEntity

# Settings written to the scooter. Lock and "Bluetooth search" are left out on purpose.
SETTINGS = {
    "tail_light": "mdi:car-light-dimmed",
    "auto_light": "mdi:car-light-high",
    "ambient_light": "mdi:led-strip-variant",
    "cruise_control": "mdi:speedometer-medium",
    "tcs": "mdi:car-traction-control",
    "intelligent_downhill": "mdi:slope-downhill",
    "hill_parking": "mdi:car-brake-hold",
}


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    entities: list[SwitchEntity] = [ChargeControl(coordinator)]
    entities += [SettingSwitch(coordinator, key, icon) for key, icon in SETTINGS.items()]
    async_add_entities(entities)


class ChargeControl(ScooterEntity, SwitchEntity, RestoreEntity):
    """When on, the configured plug is switched off at the charge limit."""

    _attr_icon = "mdi:power-plug-battery"
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "charge_control")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_state()
        self.coordinator.charge_control = bool(last and last.state == "on")

    @property
    def is_on(self) -> bool:
        return self.coordinator.charge_control

    async def async_turn_on(self, **kwargs: Any) -> None:
        if not self.coordinator.plug_switch:
            raise HomeAssistantError("Configure the charger plug in the integration options first")
        self.coordinator.charge_control = True
        self.async_write_ha_state()

    async def async_turn_off(self, **kwargs: Any) -> None:
        self.coordinator.charge_control = False
        self.async_write_ha_state()


class SettingSwitch(ScooterEntity, SwitchEntity):
    _attr_entity_category = EntityCategory.CONFIG

    def __init__(self, coordinator, key: str, icon: str) -> None:
        super().__init__(coordinator, key)
        self._attr_icon = icon

    @property
    def is_on(self) -> bool | None:
        value = self.value
        return None if value is None else bool(value)

    async def _write(self, on: bool) -> None:
        if not self.coordinator.in_range:
            raise HomeAssistantError("The scooter is not in range")
        try:
            await self.coordinator.async_set_property(self._key, 1 if on else 0)
        except (ScooterError, Exception) as err:
            raise HomeAssistantError(f"Could not change {self._key}: {err}") from err

    async def async_turn_on(self, **kwargs: Any) -> None:
        await self._write(True)

    async def async_turn_off(self, **kwargs: Any) -> None:
        await self._write(False)
