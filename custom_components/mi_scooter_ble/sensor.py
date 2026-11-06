"""Sensors."""
from __future__ import annotations

from dataclasses import dataclass

from homeassistant.components.sensor import (
    SensorDeviceClass, SensorEntity, SensorEntityDescription, SensorStateClass,
)
from homeassistant.const import (
    PERCENTAGE, EntityCategory, UnitOfElectricCurrent, UnitOfElectricPotential, UnitOfEnergy,
    UnitOfLength, UnitOfPower, UnitOfSpeed, UnitOfTemperature, UnitOfTime,
)
from homeassistant.core import HomeAssistant
from homeassistant.helpers.entity_platform import AddEntitiesCallback

from .entity import ScooterEntity

M = SensorStateClass.MEASUREMENT


@dataclass(frozen=True, kw_only=True)
class Desc(SensorEntityDescription):
    pass


SENSORS = (
    Desc(key="battery_level", device_class=SensorDeviceClass.BATTERY, native_unit_of_measurement=PERCENTAGE, state_class=M),
    Desc(key="voltage", device_class=SensorDeviceClass.VOLTAGE, native_unit_of_measurement=UnitOfElectricPotential.VOLT, state_class=M),
    Desc(key="current", device_class=SensorDeviceClass.CURRENT, native_unit_of_measurement=UnitOfElectricCurrent.AMPERE, state_class=M),
    Desc(key="power", device_class=SensorDeviceClass.POWER, native_unit_of_measurement=UnitOfPower.WATT, state_class=M),
    Desc(key="remaining_capacity", native_unit_of_measurement="mAh", state_class=M, icon="mdi:battery-charging-medium"),
    Desc(key="remaining_range", device_class=SensorDeviceClass.DISTANCE, native_unit_of_measurement=UnitOfLength.KILOMETERS, state_class=M),
    Desc(key="total_distance", device_class=SensorDeviceClass.DISTANCE, native_unit_of_measurement=UnitOfLength.KILOMETERS, state_class=SensorStateClass.TOTAL_INCREASING),
    Desc(key="trip_distance", device_class=SensorDeviceClass.DISTANCE, native_unit_of_measurement=UnitOfLength.KILOMETERS, state_class=M),
    Desc(key="average_speed", device_class=SensorDeviceClass.SPEED, native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR, state_class=M),
    Desc(key="highest_speed", device_class=SensorDeviceClass.SPEED, native_unit_of_measurement=UnitOfSpeed.KILOMETERS_PER_HOUR, state_class=M),
    Desc(key="riding_time", device_class=SensorDeviceClass.DURATION, native_unit_of_measurement=UnitOfTime.SECONDS, state_class=M),
    Desc(key="battery_temperature", device_class=SensorDeviceClass.TEMPERATURE, native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=M),
    Desc(key="scooter_temperature", device_class=SensorDeviceClass.TEMPERATURE, native_unit_of_measurement=UnitOfTemperature.CELSIUS, state_class=M),
    Desc(key="charge_cycles", state_class=SensorStateClass.TOTAL_INCREASING, icon="mdi:battery-sync"),
    Desc(key="battery_health", native_unit_of_measurement=PERCENTAGE, state_class=M, icon="mdi:battery-heart-variant"),
    Desc(key="lifetime_energy", device_class=SensorDeviceClass.ENERGY, native_unit_of_measurement=UnitOfEnergy.KILO_WATT_HOUR, state_class=SensorStateClass.TOTAL_INCREASING),
    Desc(key="riding_mode", icon="mdi:speedometer", entity_category=EntityCategory.DIAGNOSTIC),
    Desc(key="fault", icon="mdi:alert-circle-outline", entity_category=EntityCategory.DIAGNOSTIC),
    Desc(key="energy_recovery", icon="mdi:battery-arrow-up", entity_category=EntityCategory.DIAGNOSTIC),
    Desc(key="firmware", icon="mdi:chip", entity_category=EntityCategory.DIAGNOSTIC),
    Desc(key="bms_firmware", icon="mdi:chip", entity_category=EntityCategory.DIAGNOSTIC),
    Desc(key="last_read", device_class=SensorDeviceClass.TIMESTAMP, entity_category=EntityCategory.DIAGNOSTIC),
    Desc(key="last_seen", device_class=SensorDeviceClass.TIMESTAMP, entity_category=EntityCategory.DIAGNOSTIC),
)


async def async_setup_entry(hass: HomeAssistant, entry, async_add_entities: AddEntitiesCallback) -> None:
    coordinator = entry.runtime_data
    async_add_entities(ScooterSensor(coordinator, d) for d in SENSORS)


class ScooterSensor(ScooterEntity, SensorEntity):
    def __init__(self, coordinator, description: Desc) -> None:
        super().__init__(coordinator, description.key)
        self.entity_description = description

    @property
    def native_value(self):
        return self.scooter_value
