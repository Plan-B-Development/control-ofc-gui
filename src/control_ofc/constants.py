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
