"""Charge limit."""
from __future__ import annotations

from homeassistant.components.number import NumberMode, RestoreNumber
from homeassistant.const import PERCENTAGE, EntityCategory
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .const import DEFAULT_CHARGE_LIMIT
from .entity import ScooterEntity


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    async_add_entities([ChargeLimit(entry.runtime_data)])


class ChargeLimit(ScooterEntity, RestoreNumber):
    _attr_native_min_value = 50
    _attr_native_max_value = 100
    _attr_native_step = 5
    _attr_native_unit_of_measurement = PERCENTAGE
    _attr_mode = NumberMode.SLIDER
    _attr_entity_category = EntityCategory.CONFIG
    _attr_icon = "mdi:battery-charging-80"

    def __init__(self, coordinator) -> None:
        super().__init__(coordinator, "charge_limit")

    async def async_added_to_hass(self) -> None:
        await super().async_added_to_hass()
        last = await self.async_get_last_number_data()
        if last and last.native_value is not None:
            self.coordinator.charge_limit = last.native_value
        else:
            self.coordinator.charge_limit = DEFAULT_CHARGE_LIMIT

    @property
    def native_value(self) -> float:
        return self.coordinator.charge_limit

    async def async_set_native_value(self, value: float) -> None:
        self.coordinator.charge_limit = value
        self.async_write_ha_state()
        self.coordinator.async_set_updated_data(self.coordinator._snapshot())  # update "Charged"
        # Raising the limit while connected-idle should resume fast reads right away.
        await self.coordinator.async_request_refresh()
