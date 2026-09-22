"""The PWM Test Report (DEC-404): a GUI-run assessment over the daemon's own
diagnostics.

Everything in this package is Qt-free, so the rules that decide what a report
may claim are testable without a widget. The window
(``ui/widgets/pwm_report_window.py``) and the worker
(``ui/pages/diagnostics_workers.py``) are thin shells over these modules.

Kept deliberately import-free: ``app_settings_service`` imports
:mod:`.setup_facts`, and a package ``__init__`` that pulled in the runner or the
models would drag them into the settings layer.
"""
