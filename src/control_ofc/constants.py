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
LEASE_RENEW_INTERVAL_S = 30
CAPABILITIES_REFRESH_INTERVAL_S = 300

# Per-call timeout for lease HTTP calls (take/renew/release). Bounded well
# below LEASE_RENEW_INTERVAL_S so the recurring renew timer cannot pile up
# concurrent in-flight requests, and below the daemon's 60s lease TTL so we
# always get an authoritative answer before the lease can expire. Lease ops
# in production run on a worker thread (see LeaseService); this is a defense
# against a hung daemon making any individual HTTP call drag on.
LEASE_API_TIMEOUT_S = 1.5

# Control loop
CONTROL_LOOP_INTERVAL_MS = 1000
HYSTERESIS_DEADBAND_C = 2.0
PWM_WRITE_THRESHOLD_PCT = 1

# History
HISTORY_DURATION_S = 7200  # 2 hours

# Curves
DEFAULT_CURVE_POINTS = 5

# Calibration / fan wizard
THERMAL_ABORT_C = 85.0

# Pages
PAGE_DASHBOARD = 0
PAGE_CONTROLS = 1
PAGE_SETTINGS = 2
PAGE_DIAGNOSTICS = 3
