"""Xiaomi Scooter BLE integration."""
from __future__ import annotations

from homeassistant.config_entries import ConfigEntry
from homeassistant.const import Platform
from homeassistant.core import HomeAssistant

from .coordinator import ScooterCoordinator

PLATFORMS = [Platform.BINARY_SENSOR, Platform.BUTTON, Platform.NUMBER, Platform.SENSOR, Platform.SWITCH]

type ScooterConfigEntry = ConfigEntry[ScooterCoordinator]


async def async_setup_entry(hass: HomeAssistant, entry: ScooterConfigEntry) -> bool:
    coordinator = ScooterCoordinator(hass, entry)
    coordinator.async_start()
    entry.runtime_data = coordinator
    await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    # First read in the background: the scooter may be out of range at startup.
    entry.async_create_background_task(hass, coordinator.async_refresh(), "mi_scooter_ble first read")
    entry.async_on_unload(entry.add_update_listener(_reload_on_options))
    return True


async def _reload_on_options(hass: HomeAssistant, entry: ScooterConfigEntry) -> None:
    await hass.config_entries.async_reload(entry.entry_id)


async def async_unload_entry(hass: HomeAssistant, entry: ScooterConfigEntry) -> bool:
    ok = await hass.config_entries.async_unload_platforms(entry, PLATFORMS)
    if ok:
        await entry.runtime_data.async_shutdown()
    return ok
