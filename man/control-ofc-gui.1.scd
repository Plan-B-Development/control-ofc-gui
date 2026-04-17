control-ofc-gui(1)

# NAME

control-ofc-gui - desktop fan control interface for control-ofc-daemon

# SYNOPSIS

*control-ofc-gui* [OPTIONS]

# DESCRIPTION

Control-OFC GUI is a PySide6 (Qt6) desktop application for Linux that
communicates with the control-ofc-daemon service over its Unix socket HTTP
API. It provides real-time monitoring of temperatures and fan speeds, fan
curve editing, profile management, and manual fan override.

The GUI is a daemon API client only — all hardware access is performed by
the daemon.

# OPTIONS

*--socket* <path>
	Path to the daemon Unix socket.
	Default: /run/control-ofc/control-ofc.sock

*--demo*
	Run in demo mode with simulated hardware data. No daemon connection
	required.

# FILES

_~/.config/control-ofc/settings.json_
	GUI application preferences.

_~/.config/control-ofc/profiles/_
	User-created fan control profiles (JSON).

_~/.config/control-ofc/themes/_
	Custom theme files (JSON).

_/run/control-ofc/control-ofc.sock_
	Default daemon Unix socket path.

# ENVIRONMENT

*OPENFAN_PROFILE*
	Profile name to activate at startup (fallback if no --profile passed
	to the daemon).

# SEE ALSO

*control-ofc-daemon*(1)

Project: https://github.com/Plan-B-Development/control-ofc-gui

# AUTHORS

Plan B Development
