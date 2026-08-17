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
# Per-call timeout for the hwmon/GPU fan-verify endpoints: the daemon sleeps
# VERIFY_WAIT_SECONDS = 6 s between the test write and readback (DEC-101), and
# the worst-case round-trip under load is ~7.5 s, so these calls need a longer
# budget than API_TIMEOUT_S (DEC-231: was a bare 12.0 literal at two call sites).
VERIFY_TIMEOUT_S = 12.0
# Per-call timeout for POST /fans/openfan/rescan (DEC-266). The daemon probes
# every ttyACM*/ttyUSB* candidate at its serial timeout (500 ms default, up to
# 1000 ms) and re-verifies the winner, so a host with several unresponsive
# USB-serial gadgets can exceed API_TIMEOUT_S. Aborting at 5 s used to be
# actively harmful rather than merely slow: the probe is a blocking task the
# daemon cannot cancel, so the adoption completed and was then discarded with
# the handler's future — losing the thermal emergency's OpenFan leg from a
# request that merely looked like a timeout. The daemon side no longer drops the
# adoption either way (the install moved off the handler's future), but the
# client should not be provoking that path once per rescan.
OPENFAN_RESCAN_TIMEOUT_S = 25.0

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

# Curves
DEFAULT_CURVE_POINTS = 5

# Calibration / fan wizard
THERMAL_ABORT_C = 85.0

# Pages
PAGE_DASHBOARD = 0
PAGE_CONTROLS = 1
PAGE_SETTINGS = 2
# DEC-216 retired the legacy Diagnostics page (was index 3); the pages below
# renumbered down one so page_stack index still equals the PAGE_* value.
PAGE_OVERVIEW = 3  # DEC-209: Overview split to its own page (4th stack page)
PAGE_LOGS = 4  # DEC-210: Logs split to its own page (5th stack page)
PAGE_SYSTEM_STATE = 5  # DEC-211: System State split to its own page (6th stack page)
PAGE_HARDWARE = 6  # DEC-212: Hardware split to its own page (7th stack page)
PAGE_THEME = 7  # DEC-215: Theme split from the Settings tabs to its own page (8th stack page)

# Sidebar navigation ids (DEC-208). Each is a unique QButtonGroup id; the page
# switch is driven by the NavItem's page_id (``sidebar.select_page`` matches by
# page_id), so nav_id need not equal page_id. The primary entries keep nav_id ==
# page_id so the QButtonGroup's checkedId equals the page index for them
# (startup restore-page sync). DEC-216: the staged redesign is complete — every
# entry now routes straight to its own standalone page (no sub-tabs).
NAV_DASHBOARD = PAGE_DASHBOARD  # 0
NAV_CONTROLS = PAGE_CONTROLS  # 1
NAV_SETTINGS = PAGE_SETTINGS  # 2
NAV_OVERVIEW = 3  # == PAGE_OVERVIEW again after the DEC-216 renumber
NAV_SYSTEM_STATE = 4
NAV_HARDWARE = 5
NAV_THEME = 6
NAV_LOGS = 7
