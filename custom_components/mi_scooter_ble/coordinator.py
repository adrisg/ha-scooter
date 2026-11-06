"""Coordinator: presence, connection management, on/off detection and charge control.

What we know about the scooter (docs/PROTOCOL.md):
  * It keeps advertising and answering over BLE even when powered off, but then it
    returns a frozen snapshot taken at power-off. Live values only while powered on.
  * While the charger is plugged in and the scooter is on, it never powers off.
  * Powering it on drops any open BLE connection.
"""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
import logging
import time

from bleak.exc import BleakError
from bleak_retry_connector import BleakClientWithServiceCache, establish_connection

from homeassistant.components import bluetooth
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import CONF_ADDRESS, STATE_ON
from homeassistant.core import CALLBACK_TYPE, HomeAssistant, callback
from homeassistant.helpers.update_coordinator import DataUpdateCoordinator
from homeassistant.util import dt as dt_util

from . import protocol as p
from .client import LoginRejected, ScooterError, ScooterSession
from .const import (
    CONF_IDLE_INTERVAL, CONF_KEEP_WHEN_ON, CONF_LTMK, CONF_PLUG_IDLE_W, CONF_PLUG_POWER,
    CONF_PLUG_SWITCH, DEFAULT_CHARGE_LIMIT, DEFAULT_IDLE_INTERVAL, DEFAULT_PLUG_IDLE_W, DOMAIN,
    EVENT_CHARGE_STOPPED, FAST_INTERVAL, LIVENESS_PROBE, LIVENESS_WINDOW, PLUG_FINISH_DELAY,
    SLOW_REFRESH,
)

_LOGGER = logging.getLogger(__name__)


class ScooterCoordinator(DataUpdateCoordinator[dict]):
    def __init__(self, hass: HomeAssistant, entry: ConfigEntry) -> None:
        self.entry = entry
        self.address: str = entry.data[CONF_ADDRESS].upper()
        self._ltmk = bytes.fromhex(entry.data[CONF_LTMK])
        opts = entry.options
        self.plug_switch: str | None = opts.get(CONF_PLUG_SWITCH)
        self.plug_power: str | None = opts.get(CONF_PLUG_POWER)
        self.plug_idle_w: float = opts.get(CONF_PLUG_IDLE_W, DEFAULT_PLUG_IDLE_W)
        self.idle_interval: int = opts.get(CONF_IDLE_INTERVAL, DEFAULT_IDLE_INTERVAL)
        self.keep_when_on: bool = opts.get(CONF_KEEP_WHEN_ON, False)
        super().__init__(hass, _LOGGER, name=f"{DOMAIN} {self.address}",
                         update_interval=timedelta(seconds=self.idle_interval))

        # Restored by the number / switch entities.
        self.charge_limit: float = DEFAULT_CHARGE_LIMIT
        self.charge_control: bool = False

        self.in_range = False
        self.last_seen: datetime | None = None
        self._client = None
        self._session: ScooterSession | None = None
        self._ble_lock = asyncio.Lock()
        self._values: dict = {}
        self._live_sample: tuple | None = None
        self._last_change = 0.0
        self._power_on: bool | None = None
        self._last_slow = 0.0
        self._plug_low_since: float | None = None
        self._cut_sent = False
        self._unsubs: list[CALLBACK_TYPE] = []
        self.auth_failed = False

    # ------------------------------------------------------------ lifecycle
    @callback
    def async_start(self) -> None:
        self._unsubs.append(bluetooth.async_register_callback(
            self.hass, self._on_advertisement,
            bluetooth.BluetoothCallbackMatcher(address=self.address, connectable=False),
            bluetooth.BluetoothScanningMode.PASSIVE,
        ))
        self._unsubs.append(bluetooth.async_track_unavailable(
            self.hass, self._on_unavailable, self.address, connectable=False))
        if bluetooth.async_address_present(self.hass, self.address, connectable=False):
            self.in_range = True
            self.last_seen = dt_util.utcnow()

    async def async_shutdown(self) -> None:
        for unsub in self._unsubs:
            unsub()
        self._unsubs.clear()
        await self._disconnect()
        await super().async_shutdown()

    @callback
    def _on_advertisement(self, _info, _change) -> None:
        self.last_seen = dt_util.utcnow()
        if not self.in_range:
            self.in_range = True
            self.hass.async_create_task(self.async_request_refresh())
        else:
            self.async_update_listeners()

    @callback
    def _on_unavailable(self, _info) -> None:
        self.in_range = False
        self.async_set_updated_data(self._snapshot())

    @callback
    def _on_disconnect(self, _client) -> None:
        if self._session is None:
            return  # we closed it ourselves
        _LOGGER.debug("scooter dropped the connection (power on/off?)")
        self._client = self._session = None
        self._live_sample = None
        self._power_on = None
        self.async_set_updated_data(self._snapshot())
        # Powering the scooter on drops the link: look again soon.
        self.hass.loop.call_later(3, lambda: self.hass.async_create_task(self.async_request_refresh()))

    # ----------------------------------------------------------- connection
    @property
    def connected(self) -> bool:
        return self._session is not None

    async def _connect(self) -> ScooterSession:
        if self._session is not None:
            return self._session
        device = bluetooth.async_ble_device_from_address(self.hass, self.address, connectable=True)
        if device is None:
            raise ScooterError("no connectable Bluetooth adapter/proxy can reach the scooter")
        client = await establish_connection(
            BleakClientWithServiceCache, device, self.address,
            disconnected_callback=self._on_disconnect, max_attempts=3)
        session = ScooterSession(client, self._ltmk)
        try:
            await session.start()
        except Exception:
            await client.disconnect()
            raise
        self._client, self._session = client, session
        self._live_sample = None
        return session

    async def _disconnect(self) -> None:
        client, self._client, self._session = self._client, None, None
        if client is not None:
            try:
                await client.disconnect()
            except BleakError:
                pass

    # ---------------------------------------------------------------- reads
    async def _read(self, session: ScooterSession, keys) -> None:
        for key in keys:
            value = await session.get(p.PROPS[key])
            if value is not None:
                self._values[key] = p.scaled(p.PROPS[key], value)

    def _sample_changed(self) -> bool:
        sample = tuple(self._values.get(k) for k in p.LIVENESS_KEYS)
        changed = self._live_sample is not None and sample != self._live_sample
        self._live_sample = sample
        if changed:
            self._last_change = time.monotonic()
        return changed

    @property
    def powered_on(self) -> bool:
        """Last decision; kept after we disconnect on purpose."""
        return bool(self._power_on)

    async def _async_update_data(self) -> dict:
        if not self.in_range:
            await self._disconnect()
            self._set_interval(self.idle_interval)
            return self._snapshot()
        async with self._ble_lock:
            fresh = self._session is None
            try:
                session = await self._connect()
                if fresh:
                    self._last_change = 0.0
                await self._read(session, p.LIVE_KEYS)
                self._sample_changed()
                if fresh:
                    # A single read cannot tell live data from the frozen snapshot:
                    # sample the fast-changing values for a while.
                    end = time.monotonic() + LIVENESS_PROBE
                    while time.monotonic() < end:
                        await asyncio.sleep(2)
                        await self._read(session, p.LIVENESS_KEYS)
                        if self._sample_changed():
                            break
                if time.monotonic() - self._last_slow > SLOW_REFRESH:
                    await self._read(session, p.SLOW_KEYS)
                    self._last_slow = time.monotonic()
                self.auth_failed = False
                self._power_on = time.monotonic() - self._last_change < LIVENESS_WINDOW
                self._values["last_read"] = dt_util.utcnow()
            except LoginRejected:
                self.auth_failed = True
                await self._disconnect()
                _LOGGER.error("The scooter rejected the login key; reconfigure the integration")
                self._set_interval(self.idle_interval)
                return self._snapshot()
            except (ScooterError, BleakError, asyncio.TimeoutError) as err:
                _LOGGER.debug("read failed: %s", err)
                await self._disconnect()
                self._set_interval(self.idle_interval)
                return self._snapshot()

            keep = self.powered_on and (self.charging_live or self.keep_when_on)
            if keep:
                self._set_interval(FAST_INTERVAL)
            else:
                await self._disconnect()
                self._set_interval(self.idle_interval)

        await self._charge_control()
        return self._snapshot()

    def _set_interval(self, seconds: int) -> None:
        self.update_interval = timedelta(seconds=seconds)

    # -------------------------------------------------------------- charging
    @property
    def charging_live(self) -> bool:
        return bool(self._values.get("charging"))

    def _plug_watts(self) -> float | None:
        if not self.plug_power:
            return None
        state = self.hass.states.get(self.plug_power)
        try:
            return float(state.state) if state else None
        except ValueError:
            return None

    @property
    def charging(self) -> bool | None:
        """Best guess: live flag while on, otherwise the plug's power draw."""
        if self.powered_on:
            return self.charging_live
        watts = self._plug_watts()
        if watts is not None:
            return watts > self.plug_idle_w
        return None

    def _plug_is_on(self) -> bool:
        state = self.hass.states.get(self.plug_switch) if self.plug_switch else None
        return state is not None and state.state == STATE_ON

    async def _charge_control(self) -> None:
        if not (self.charge_control and self.plug_switch):
            return
        if not self._plug_is_on():
            self._cut_sent = False
            self._plug_low_since = None
            return
        reason = None
        level = self._values.get("battery_level")
        if self.powered_on and self.charging_live and level is not None and level >= self.charge_limit:
            reason = f"battery {level}% >= limit {self.charge_limit:.0f}%"
        elif not self.powered_on:
            watts = self._plug_watts()
            if watts is not None and watts < self.plug_idle_w:
                self._plug_low_since = self._plug_low_since or time.monotonic()
                if time.monotonic() - self._plug_low_since > PLUG_FINISH_DELAY:
                    reason = f"charger idle ({watts:.1f} W)"
            else:
                self._plug_low_since = None
        if reason and not self._cut_sent:
            self._cut_sent = True
            _LOGGER.info("Stopping charge: %s", reason)
            await self.hass.services.async_call(
                "switch", "turn_off", {"entity_id": self.plug_switch}, blocking=True)
            self.hass.bus.async_fire(EVENT_CHARGE_STOPPED, {
                "address": self.address, "reason": reason, "battery_level": level})

    # ---------------------------------------------------------------- writes
    async def async_set_property(self, key: str, value) -> None:
        prop = p.PROPS[key]
        async with self._ble_lock:
            session = await self._connect()
            if not await session.set(prop, value):
                raise ScooterError(f"scooter refused {key}={value}")
            self._values[key] = p.scaled(prop, await session.get(prop))
            if not (self.powered_on and (self.charging_live or self.keep_when_on)):
                await self._disconnect()
        self.async_set_updated_data(self._snapshot())

    # -------------------------------------------------------------- snapshot
    def _snapshot(self) -> dict:
        data = dict(self._values)
        data.update(
            in_range=self.in_range, connected=self.connected, powered_on=self.powered_on,
            charging_any=self.charging, last_seen=self.last_seen,
        )
        data.update(p.decode_battery_extra(data.get("battery_extra")))
        return data
