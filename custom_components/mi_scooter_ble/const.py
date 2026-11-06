"""Constants for the Xiaomi Scooter BLE integration."""
DOMAIN = "mi_scooter_ble"

CONF_LTMK = "ltmk"
CONF_MODEL = "model"

# Options
CONF_PLUG_SWITCH = "plug_switch"
CONF_PLUG_POWER = "plug_power_sensor"
CONF_PLUG_IDLE_W = "plug_idle_watts"
CONF_IDLE_INTERVAL = "idle_interval"
CONF_KEEP_WHEN_ON = "keep_connected_when_on"

DEFAULT_PLUG_IDLE_W = 8.0  # charger draw below this (W) = not charging / finished
DEFAULT_IDLE_INTERVAL = 300  # s between checks while the scooter is off
FAST_INTERVAL = 10  # s between reads while connected (powered on)
SLOW_REFRESH = 600  # s between reads of settings / counters
LIVENESS_WINDOW = 60  # s without any change in live values => powered off (frozen cache)
LIVENESS_PROBE = 45  # s spent sampling after a fresh connection to decide on/off
PLUG_FINISH_DELAY = 300  # s below idle watts before cutting the plug (scooter off)

DEFAULT_CHARGE_LIMIT = 100
EVENT_CHARGE_STOPPED = f"{DOMAIN}_charge_stopped"
