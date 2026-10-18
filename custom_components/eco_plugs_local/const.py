"""Constants for the Eco Plugs (Local) integration."""

DOMAIN = "eco_plugs_local"
MANUFACTURER = "KAB / Eco Plugs"

CONF_MAC = "mac"
CONF_DEVICE_ID = "device_id"
CONF_DEBOUNCE = "debounce"
CONF_STALE_TIMEOUT = "stale_timeout"

# Consecutive agreeing beacons needed to accept an unsolicited state change.
DEFAULT_DEBOUNCE = 2
# Seconds without a beacon before the entity goes unavailable.
DEFAULT_STALE_TIMEOUT = 30

DISCOVERY_SECONDS = 6.0  # beacons arrive about every 2 s
REPLY_TIMEOUT = 1.5  # per attempt
COMMAND_ATTEMPTS = 3
# Ignore contradicting beacons this long after a confirmed command.
ACK_GRACE_SECONDS = 6.0
