"""Constants for the V-Guard Smart integration."""

DOMAIN = "vguard"

# Idle poll default for HA (app uses 6s only while its screen is open).
DEFAULT_SCAN_INTERVAL = 60
MIN_STABLE_SCAN_INTERVAL = 30
MIN_SCAN_INTERVAL = 6
MAX_SCAN_INTERVAL = 300
ACTIVE_POLL_INTERVAL = 6
ACTIVE_POLL_HOLD_S = 60

CONF_EMAIL = "email"
CONF_PASSWORD = "password"
CONF_SERIAL = "serial"
CONF_ACCESS_TOKEN = "access_token"
CONF_REFRESH_TOKEN = "refresh_token"
CONF_FCM_TOKEN = "fcm_token"
CONF_SCAN_INTERVAL = "scan_interval"
CONF_BATTERY_CAPACITY_AH = "battery_capacity_ah"

DEFAULT_BATTERY_CAPACITY_AH = 200
MIN_BATTERY_CAPACITY_AH = 100
MAX_BATTERY_CAPACITY_AH = 300
