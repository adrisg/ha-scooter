"""Config flow: discover the scooter, get its login key (Mi Cloud QR + PIN, or manual)."""
from __future__ import annotations

import asyncio
import logging
import re
from typing import Any

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection
import voluptuous as vol

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry, ConfigFlow, ConfigFlowResult, OptionsFlow
from homeassistant.const import CONF_ADDRESS
from homeassistant.core import callback
from homeassistant.helpers.aiohttp_client import async_create_clientsession
from homeassistant.helpers import selector

from .client import LoginRejected, ScooterError, ScooterSession
from .cloud import CloudError, MiCloud, PinRequired
from .const import (
    CONF_IDLE_INTERVAL, CONF_KEEP_WHEN_ON, CONF_LTMK, CONF_MODEL, CONF_PLUG_IDLE_W,
    CONF_PLUG_POWER, CONF_PLUG_SWITCH, DEFAULT_IDLE_INTERVAL, DEFAULT_PLUG_IDLE_W, DOMAIN,
)

_LOGGER = logging.getLogger(__name__)
MAC_RE = re.compile(r"^([0-9A-F]{2}:){5}[0-9A-F]{2}$")


def _is_scooter(info: bluetooth.BluetoothServiceInfoBleak) -> bool:
    return (info.name or "").startswith("xiaomi.scooter")


class ScooterConfigFlow(ConfigFlow, domain=DOMAIN):
    VERSION = 1

    def __init__(self) -> None:
        self._address: str | None = None
        self._name: str | None = None
        self._model: str | None = None
        self._cloud: MiCloud | None = None
        self._login_task: asyncio.Task | None = None
        self._device: dict | None = None

    # ---------------------------------------------------------- discovery
    async def async_step_bluetooth(self, info: bluetooth.BluetoothServiceInfoBleak) -> ConfigFlowResult:
        await self.async_set_unique_id(info.address.upper())
        self._abort_if_unique_id_configured()
        self._address, self._model = info.address.upper(), info.name
        self._name = info.name
        self.context["title_placeholders"] = {"name": info.name}
        return await self.async_step_method()

    async def async_step_user(self, user_input: dict[str, Any] | None = None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            address = user_input[CONF_ADDRESS].strip().upper()
            if not MAC_RE.match(address):
                errors[CONF_ADDRESS] = "invalid_mac"
            else:
                await self.async_set_unique_id(address)
                self._abort_if_unique_id_configured()
                self._address = address
                info = bluetooth.async_last_service_info(self.hass, address, connectable=False)
                self._model = self._name = info.name if info else None
                return await self.async_step_method()
        found = [
            selector.SelectOptionDict(value=i.address.upper(), label=f"{i.name} ({i.address})")
            for i in bluetooth.async_discovered_service_info(self.hass, connectable=False)
            if _is_scooter(i) and i.address.upper() not in self._async_current_ids()
        ]
        schema = vol.Schema({vol.Required(CONF_ADDRESS): selector.SelectSelector(
            selector.SelectSelectorConfig(options=found, custom_value=True))})
        return self.async_show_form(step_id="user", data_schema=schema, errors=errors)

    async def async_step_method(self, user_input=None) -> ConfigFlowResult:
        return self.async_show_menu(step_id="method", menu_options=["cloud", "manual"],
                                    description_placeholders={"address": self._address})

    # -------------------------------------------------------- Mi Cloud QR
    async def async_step_cloud(self, user_input=None) -> ConfigFlowResult:
        if self._cloud is None:
            self._cloud = MiCloud(async_create_clientsession(self.hass))
            try:
                await self._cloud.start_qr_login()
            except (CloudError, Exception) as err:  # noqa: BLE001
                _LOGGER.debug("QR start failed: %s", err)
                self._cloud = None
                return self.async_abort(reason="cloud_unreachable")
            self._login_task = self.hass.async_create_task(self._cloud.wait_qr_login())
        if not self._login_task.done():
            return self.async_show_progress(
                step_id="cloud", progress_action="qr_scan", progress_task=self._login_task,
                description_placeholders={"qr": self._cloud.qr_image_url, "url": self._cloud.login_url},
            )
        try:
            self._login_task.result()
        except Exception as err:  # noqa: BLE001
            _LOGGER.debug("QR login failed: %s", err)
            self._cloud = self._login_task = None
            return self.async_show_progress_done(next_step_id="cloud_failed")
        return self.async_show_progress_done(next_step_id="cloud_device")

    async def async_step_cloud_failed(self, user_input=None) -> ConfigFlowResult:
        return self.async_abort(reason="cloud_login_failed")

    async def async_step_cloud_device(self, user_input=None) -> ConfigFlowResult:
        try:
            scooters = await self._cloud.list_scooters()
        except (CloudError, Exception) as err:  # noqa: BLE001
            _LOGGER.debug("device list failed: %s", err)
            return self.async_abort(reason="cloud_unreachable")
        match = [s for s in scooters if s["mac"] == self._address]
        if not match:
            return self.async_abort(reason="not_in_account",
                                    description_placeholders={"address": self._address})
        self._device = match[0]
        self._name, self._model = self._device["name"], self._device["model"]
        try:
            ltmk = await self._cloud.fetch_ltmk(self._device["did"], self._device["country"])
        except PinRequired:
            return await self.async_step_pin()
        except CloudError:
            return self.async_abort(reason="cloud_unreachable")
        return await self._finish(ltmk, step_id="cloud") or self.async_abort(reason="key_rejected")

    async def async_step_pin(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            try:
                ltmk = await self._cloud.fetch_ltmk(
                    self._device["did"], self._device["country"], user_input["pin"])
            except CloudError:
                return self.async_abort(reason="cloud_unreachable")
            result = await self._finish(ltmk, step_id="pin")
            if result is not None:
                return result
            errors["base"] = "invalid_pin"
        schema = vol.Schema({vol.Required("pin"): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))})
        return self.async_show_form(step_id="pin", data_schema=schema, errors=errors)

    # ------------------------------------------------------------- manual
    async def async_step_manual(self, user_input=None) -> ConfigFlowResult:
        errors = {}
        if user_input is not None:
            key = user_input[CONF_LTMK].strip().lower()
            if not re.fullmatch(r"[0-9a-f]{64}", key):
                errors[CONF_LTMK] = "invalid_key_format"
            else:
                result = await self._finish(bytes.fromhex(key), step_id="manual")
                if result is not None:
                    return result
                errors["base"] = "invalid_key"
        schema = vol.Schema({vol.Required(CONF_LTMK): selector.TextSelector(
            selector.TextSelectorConfig(type=selector.TextSelectorType.PASSWORD))})
        return self.async_show_form(step_id="manual", data_schema=schema, errors=errors)

    # ------------------------------------------------------------- finish
    async def _try_login(self, ltmk: bytes) -> bool | None:
        """True ok, False rejected, None scooter not reachable (cannot verify)."""
        device = bluetooth.async_ble_device_from_address(self.hass, self._address, connectable=True)
        if device is None:
            return None
        try:
            client = await establish_connection(BleakClientWithServiceCache, device, self._address, max_attempts=2)
        except (BleakError, asyncio.TimeoutError, Exception):  # noqa: BLE001
            return None
        try:
            await ScooterSession(client, ltmk).start()
            return True
        except LoginRejected:
            return False
        except (ScooterError, BleakError, asyncio.TimeoutError):
            return None
        finally:
            await client.disconnect()

    async def _finish(self, ltmk: bytes, step_id: str) -> ConfigFlowResult | None:
        """Create the entry, or None when the scooter rejected the key."""
        if await self._try_login(ltmk) is False:
            return None
        return self.async_create_entry(
            title=self._name or f"Scooter {self._address[-5:]}",
            data={CONF_ADDRESS: self._address, CONF_LTMK: ltmk.hex(), CONF_MODEL: self._model},
        )

    @staticmethod
    @callback
    def async_get_options_flow(entry: ConfigEntry) -> OptionsFlow:
        return ScooterOptionsFlow()


class ScooterOptionsFlow(OptionsFlow):
    async def async_step_init(self, user_input=None) -> ConfigFlowResult:
        if user_input is not None:
            return self.async_create_entry(data=user_input)
        o = self.config_entry.options
        schema = vol.Schema({
            vol.Optional(CONF_PLUG_SWITCH, description={"suggested_value": o.get(CONF_PLUG_SWITCH)}):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="switch")),
            vol.Optional(CONF_PLUG_POWER, description={"suggested_value": o.get(CONF_PLUG_POWER)}):
                selector.EntitySelector(selector.EntitySelectorConfig(domain="sensor", device_class="power")),
            vol.Required(CONF_PLUG_IDLE_W, default=o.get(CONF_PLUG_IDLE_W, DEFAULT_PLUG_IDLE_W)):
                selector.NumberSelector(selector.NumberSelectorConfig(min=0, max=100, step=0.5, unit_of_measurement="W")),
            vol.Required(CONF_IDLE_INTERVAL, default=o.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL)):
                selector.NumberSelector(selector.NumberSelectorConfig(min=60, max=3600, step=30, unit_of_measurement="s")),
            vol.Required(CONF_KEEP_WHEN_ON, default=o.get(CONF_KEEP_WHEN_ON, False)):
                selector.BooleanSelector(),
        })
        return self.async_show_form(step_id="init", data_schema=schema)
