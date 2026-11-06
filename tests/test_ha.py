"""Home Assistant level tests (needs pytest-homeassistant-custom-component)."""
from unittest.mock import patch

import pytest

pytest.importorskip("pytest_homeassistant_custom_component")

from homeassistant import config_entries  # noqa: E402
from homeassistant.const import CONF_ADDRESS  # noqa: E402
from homeassistant.data_entry_flow import FlowResultType  # noqa: E402
from pytest_homeassistant_custom_component.common import MockConfigEntry  # noqa: E402

DOMAIN = "mi_scooter_ble"
MAC = "AA:BB:CC:DD:EE:FF"
KEY = "11" * 32


@pytest.fixture(autouse=True)
def auto_enable(enable_custom_integrations):
    yield


@pytest.fixture(autouse=True)
def no_bluetooth_stack():
    """Skip the real Bluetooth stack: patch the helpers the integration uses."""
    with patch("homeassistant.components.bluetooth.async_setup", return_value=True), \
         patch("homeassistant.components.bluetooth_adapters.async_setup", return_value=True, create=True), \
         patch("homeassistant.components.bluetooth.async_discovered_service_info", return_value=[]), \
         patch("homeassistant.components.bluetooth.async_last_service_info", return_value=None), \
         patch("homeassistant.components.bluetooth.async_register_callback", return_value=lambda: None), \
         patch("homeassistant.components.bluetooth.async_track_unavailable", return_value=lambda: None), \
         patch("homeassistant.components.bluetooth.async_address_present", return_value=False):
        yield


async def test_manual_flow_creates_entry(hass):
    result = await hass.config_entries.flow.async_init(DOMAIN, context={"source": config_entries.SOURCE_USER})
    assert result["type"] is FlowResultType.FORM

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: "nope"})
    assert result["errors"] == {CONF_ADDRESS: "invalid_mac"}

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {CONF_ADDRESS: MAC.lower()})
    assert result["type"] is FlowResultType.MENU

    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"next_step_id": "manual"})
    result = await hass.config_entries.flow.async_configure(result["flow_id"], {"ltmk": "abc"})
    assert result["errors"] == {"ltmk": "invalid_key_format"}

    with patch("custom_components.mi_scooter_ble.config_flow.ScooterConfigFlow._try_login", return_value=False):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"ltmk": KEY})
    assert result["errors"] == {"base": "invalid_key"}

    with patch("custom_components.mi_scooter_ble.config_flow.ScooterConfigFlow._try_login", return_value=None), \
         patch("custom_components.mi_scooter_ble.async_setup_entry", return_value=True):
        result = await hass.config_entries.flow.async_configure(result["flow_id"], {"ltmk": KEY})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    assert result["data"] == {CONF_ADDRESS: MAC, "ltmk": KEY, "model": None}


async def test_setup_creates_entities_and_options(hass):
    entry = MockConfigEntry(domain=DOMAIN, unique_id=MAC, title="Scooter",
                            data={CONF_ADDRESS: MAC, "ltmk": KEY, "model": "xiaomi.scooter.5max"})
    entry.add_to_hass(hass)
    assert await hass.config_entries.async_setup(entry.entry_id)
    await hass.async_block_till_done()

    states = {s.entity_id for s in hass.states.async_all() if s.entity_id.split(".")[1].startswith("scooter")}
    for expected in ("sensor.scooter_battery", "binary_sensor.scooter_in_range",
                     "binary_sensor.scooter_charging", "number.scooter_charge_limit",
                     "switch.scooter_charge_control", "switch.scooter_tail_light_always_on"):
        assert expected in states, sorted(states)
    assert hass.states.get("binary_sensor.scooter_in_range").state == "off"
    assert hass.states.get("number.scooter_charge_limit").state == "100"

    result = await hass.config_entries.options.async_init(entry.entry_id)
    assert result["type"] is FlowResultType.FORM
    result = await hass.config_entries.options.async_configure(result["flow_id"], {
        "plug_switch": "switch.shelly_patinete", "plug_idle_watts": 8, "idle_interval": 300,
        "keep_connected_when_on": False})
    assert result["type"] is FlowResultType.CREATE_ENTRY
    await hass.async_block_till_done()
    assert entry.runtime_data.plug_switch == "switch.shelly_patinete"

    assert await hass.config_entries.async_unload(entry.entry_id)


async def test_debug_loader(hass):
    import sys
    from homeassistant import loader
    cc = sys.modules.get("custom_components")
    print("DEBUG custom_components:", getattr(cc, "__path__", None), getattr(cc, "__file__", None))
    comps = await loader.async_get_custom_components(hass)
    assert False, ("DEBUGX", getattr(cc, "__path__", None), sorted(comps))
