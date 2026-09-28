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

The GUI is a daemon API client only -- all hardware access is performed by
the daemon.

# OPTIONS

*--socket* <path>
	Path to the daemon Unix socket.
	Default: /run/control-ofc/control-ofc.sock

*--demo*
	Run in demo mode with simulated hardware data. No daemon connection
	required.

# FILES

_~/.config/control-ofc/app_settings.json_
	GUI application preferences.

_~/.config/control-ofc/profiles/_
	User-created fan control profiles (JSON).

_~/.config/control-ofc/themes/_
	Custom theme files (JSON).

_~/.config/control-ofc/backups/_
	Automatic backups of app_settings.json, one taken before every
	settings import.

_~/.local/share/control-ofc/reports/_
	Saved PWM Test Reports (JSON).

_~/.cache/control-ofc/_
	Generated theme images. Safe to delete; it is recreated.

_/run/control-ofc/control-ofc.sock_
	Default daemon Unix socket path.

# ENVIRONMENT

*XDG_CONFIG_HOME*
	Base directory for configuration. The GUI stores its preferences,
	profiles, themes and settings backups under $XDG_CONFIG_HOME/control-ofc
	(default: ~/.config/control-ofc). The profiles and themes folders can be
	moved in Settings.

*XDG_DATA_HOME*
	Base directory for data the GUI keeps: saved PWM Test Reports go in
	$XDG_DATA_HOME/control-ofc/reports (default: ~/.local/share/control-ofc).

*XDG_CACHE_HOME*
	Base directory for the cache, $XDG_CACHE_HOME/control-ofc (default:
	~/.cache/control-ofc).

# SEE ALSO

*control-ofc-daemon*(1)

Project: https://github.com/Plan-B-Development/control-ofc-gui

# AUTHORS

Plan B Development
