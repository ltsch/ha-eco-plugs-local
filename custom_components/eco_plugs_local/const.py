"""Constants for the Eco Plugs (Local) integration."""

DOMAIN = "eco_plugs_local"
MANUFACTURER = "KAB / Eco Plugs"

CONF_MAC = "mac"
CONF_DEVICE_ID = "device_id"
CONF_DEBOUNCE = "debounce"
CONF_STALE_TIMEOUT = "stale_timeout"
CONF_FAMILY = "family"

FAMILY_KAB = "kab"  # beacon protocol, current firmware (protocol.py)
FAMILY_FT = "ft"  # search/poll protocol, v1.x firmware (protocol_ft.py)

# Consecutive agreeing beacons needed to accept an unsolicited state change.
DEFAULT_DEBOUNCE = 2
# Seconds without a beacon before the entity goes unavailable.
DEFAULT_STALE_TIMEOUT = 30

DISCOVERY_SECONDS = 6.0  # beacons arrive about every 2 s
REPLY_TIMEOUT = 1.5  # per attempt
COMMAND_ATTEMPTS = 3
# Ignore contradicting beacons this long after a confirmed command.
ACK_GRACE_SECONDS = 6.0

# v1.x plugs send nothing unsolicited, so state is polled. The plug lets one client
# write at a time and holds that for a few seconds after the client's last packet, so
# every poll of a plug goes out in one burst per cycle and the plug is quiet in between.
CONF_POLL_INTERVAL = "poll_interval"
DEFAULT_POLL_INTERVAL = 10
FT_SLOW_POLL_SECONDS = 60  # timers and the clock record change rarely
FT_DISCOVERY_SECONDS = 3.0
FT_CONFIRM_DELAY = 0.3  # between status queries while waiting for a set to show
FT_POLL_ATTEMPTS = 2
