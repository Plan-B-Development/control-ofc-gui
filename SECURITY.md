# Security Policy

## Reporting a Vulnerability

If you discover a security vulnerability in Control-OFC GUI, please report it privately:

1. **Email:** chomeop@gmail.com
2. **Subject:** `[SECURITY] Control-OFC GUI — <brief description>`

Please include:
- Description of the vulnerability
- Steps to reproduce
- Affected version(s)
- Potential impact

We will acknowledge receipt within 48 hours and aim to provide a fix within 7 days for critical issues.

## Scope

The GUI communicates with a local daemon over a Unix domain socket. The primary security boundaries are:

| Boundary | Concern |
|----------|---------|
| Profile/theme import | Malformed JSON, path traversal, oversized files |
| Settings persistence | File permissions, XDG path handling |
| Daemon API communication | Input validation, error handling |
| Support bundle export | Accidental inclusion of sensitive system information |

The daemon is a separate project with its own security considerations (sysfs access, serial I/O, socket permissions). Report daemon vulnerabilities to the [daemon repository](https://github.com/Plan-B-Development/control-ofc-daemon).

## Supported Versions

| Version | Supported |
|---------|-----------|
| 1.0.x   | Yes       |
| < 1.0   | No        |
