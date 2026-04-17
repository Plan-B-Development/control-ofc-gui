# Contributing to Control-OFC GUI

## Setup

```bash
git clone https://github.com/Plan-B-Development/control-ofc-gui.git
cd control-ofc-gui
python3 -m venv .venv
source .venv/bin/activate
pip install -e ".[dev]"
```

## Quality Gates

All three must pass before merging:

```bash
ruff format --check src/ tests/
ruff check src/ tests/
pytest
```

Run all at once:

```bash
ruff format --check src/ tests/ && ruff check src/ tests/ && pytest
```

## Running

```bash
# Demo mode (no daemon needed)
control-ofc-gui --demo

# Connected to daemon
control-ofc-gui

# Custom socket path
control-ofc-gui --socket /tmp/control-ofc.sock
```

## Testing

```bash
# Full suite (815+ tests)
pytest

# Specific file
pytest tests/test_control_loop.py -v

# With coverage
pytest --cov --cov-branch --cov-report=term-missing
```

Tests use `pytest-qt` for widget testing. All tests must be deterministic — no real hardware, no real network calls, no flaky timing.

## Code Style

- **Formatter:** ruff (auto-format with `ruff format src/ tests/`)
- **Linter:** ruff (auto-fix with `ruff check --fix src/ tests/`)
- **Python:** >= 3.12, develop on 3.14
- **Type hints:** required for public APIs; use `TYPE_CHECKING` imports to avoid circular dependencies
- **Comments:** only when the *why* is non-obvious

## Architecture Rules

1. **No direct hardware access.** The GUI communicates only with the daemon API.
2. **Truthful UI.** Never hide errors, never display stale data as fresh.
3. **Daemon owns safety.** Thermal rules, fan floors, and emergency overrides belong to the daemon.
4. **All widgets must have unique `objectName`s** for test targeting.

## Project Structure

```
src/control_ofc/
├── api/            # Daemon HTTP client and typed models
├── services/       # AppState, PollingService, ControlLoop, ProfileService
├── ui/
│   ├── pages/      # Dashboard, Controls, Settings, Diagnostics
│   └── widgets/    # Reusable components (cards, charts, editors)
└── main.py         # Entry point and arg parsing
```

## Pull Requests

- Keep PRs focused — one feature or fix per PR
- Include tests for new features and regression tests for bug fixes
- Update `CHANGELOG.md` under `[Unreleased]`
- Update relevant `docs/` if architecture changes materially

## Reporting Issues

File issues at https://github.com/Plan-B-Development/control-ofc-gui/issues
