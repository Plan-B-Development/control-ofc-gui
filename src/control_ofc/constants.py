"""Application-wide constants."""

from importlib.metadata import PackageNotFoundError, version

APP_NAME = "Control-OFC"
try:
    APP_VERSION = version("control-ofc-gui")
except PackageNotFoundError:
    APP_VERSION = "dev"

# Daemon IPC
DEFAULT_SOCKET_PATH = "/run/control-ofc/control-ofc.sock"
API_TIMEOUT_S = 5.0

# Contract version this GUI is built against. Compared on the first
# /capabilities response against the daemon's reported ``api_version``; a
# mismatch surfaces a non-fatal warning banner (the AUR ``depends>=`` floor only
# guards the *minimum* daemon version, not a future-incompatible one, and either
# package can be upgraded out of lockstep). MUST equal the daemon's
# ``responses.rs::API_VERSION`` — bump in lockstep on any contract version change.
EXPECTED_API_VERSION = 1

# Polling
POLL_INTERVAL_MS = 1000
CAPABILITIES_REFRESH_INTERVAL_S = 300

# History
HISTORY_DURATION_S = 7200  # 2 hours

# Synthetic "average fan RPM" chart series (DEC-181). A derived history key, NOT a
# real fan — the chart's curated default shows this single line instead of every
# fan. Ends ``:rpm`` so the chart routes it to the RPM axis and the selection model
# never filters it (only ``:pwm`` is filtered); the ``__aggregate__`` id can't
# collide with a real fan id. PWM is deliberately not aggregated — the chart has no
# PWM axis (B1 fork, DEC-181).
AGGREGATE_FAN_RPM_KEY = "fan:__aggregate__:rpm"

# Curves
DEFAULT_CURVE_POINTS = 5

# Calibration / fan wizard
THERMAL_ABORT_C = 85.0

# Pages
PAGE_DASHBOARD = 0
PAGE_CONTROLS = 1
PAGE_SETTINGS = 2
PAGE_DIAGNOSTICS = 3
