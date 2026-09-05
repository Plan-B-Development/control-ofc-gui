"""Settings page — app preferences, themes, and import/export."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt, Signal

if TYPE_CHECKING:
    from control_ofc.api.client import DaemonClient
    from control_ofc.services.series_selection import SeriesSelectionModel
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QScrollArea,
    QSpinBox,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

from control_ofc.api.errors import DaemonError
from control_ofc.constants import PAGE_CONTROLS, PAGE_DASHBOARD, PAGE_SETTINGS
from control_ofc.paths import (
    app_settings_path,
    atomic_write,
    config_dir,
    export_default_dir,
    load_json_capped,
    profiles_dir,
    set_path_overrides,
    themes_dir,
)
from control_ofc.services.app_settings_service import AppSettingsService
from control_ofc.services.app_state import AppState
from control_ofc.services.daemon_features import (
    requires_daemon,
    unsupported_feature_message,
)
from control_ofc.services.orphan_prune import OrphanReport, find_orphans, live_series_keys
from control_ofc.services.profile_import_service import import_profiles
from control_ofc.services.profile_service import ImportCollection, collect_local_profiles_for_import
from control_ofc.ui.components.a11y import name_value_control
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import Card, SectionHeader
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.components.toggle_switch import ToggleSwitch
from control_ofc.ui.qt_util import block_signals, set_chip_class
from control_ofc.ui.theme import ThemeTokens

log = logging.getLogger(__name__)


# DEC-237: every ``AppSettings`` field this page exposes, mapped to the
# objectName of the widget that edits or resets it.
#
# ``tests/test_settings_coverage_dec237.py`` asserts two things against this map:
# that it, plus the Theme page's fields and an explicitly-justified implicit
# list, accounts for *every* field on ``AppSettings``; and that each objectName
# below resolves to a real widget on a constructed page. Together those make an
# orphaned setting a test failure rather than a silent gap — 14 of 29 fields had
# drifted out of reach before this map existed, because nothing checked.
#
# Add a field to ``AppSettings`` → the suite fails until it is classified here,
# on the Theme page, or in the implicit list with a reason.
SETTINGS_FIELD_WIDGETS: dict[str, str] = {
    # General & Startup
    "default_startup_page": "Settings_Combo_startupPage",
    "restore_last_page": "Settings_Check_restorePage",
    "demo_on_disconnect": "Settings_Check_demoDisconnect",
    "show_gpu_zero_rpm_warning": "Settings_Check_gpuZeroRpmWarn",
    "chart_default_range_index": "Settings_Combo_chartRange",
    # Operational Behavior
    "wizard_spindown_seconds": "Settings_Spin_wizardSpindown",
    "hide_igpu_sensors": "Settings_Check_hideIgpu",
    "hide_unused_fan_headers": "Settings_Check_hideUnusedFans",
    # Path Management
    "profiles_dir_override": "Settings_Label_profilesDir",
    "themes_dir_override": "Settings_Label_themesDir",
    "export_default_dir": "Settings_Label_exportDir",
    # Fan Names & Aliases (mirrors the DEC-227 Dashboard rename)
    "fan_aliases": "Settings_Table_fanAliases",
    # Sensors & Chart Series (mirrors the Overview context menu + Sensors rail)
    "diagnostics_hidden_sensor_ids": "Settings_Btn_unhideSensors",
    "sensor_class_overrides": "Settings_Btn_resetSensorClasses",
    "series_colors": "Settings_Btn_resetSeriesColors",
    "hidden_chart_series": "Settings_Btn_showAllSeries",
    # Prompts & Dismissals
    "show_aio_pump_info": "Settings_Check_aioPumpInfo",
    "acknowledged_kernel_warnings": "Settings_Btn_clearKernelWarnings",
    "daemon_import_prompted": "Settings_Btn_reofferImport",
    "fan_aliases_seeded": "Settings_Btn_reseedAliases",
    "chart_series_seeded": "Settings_Btn_reseedSeries",
    # Card Layout
    "controls_card_sizes": "Settings_Btn_resetCardSizes",
}


# DEC-285: the daemon-owned half of the same guarantee.
#
# ``GET /config`` reports a ``mutable`` flag per key. Every mutable key must have
# a control on this page — otherwise the daemon exposes a setting the GUI can
# neither show nor change. That is not hypothetical: ``profiles.search_dirs`` was
# mutable from the day the endpoint shipped, the GUI fetched it and discarded it,
# and meanwhile ``services/polling.py`` added an entry to it on every connect and
# the directory picker added another on every change. The list only ever grew,
# was displayed nowhere, and could be pruned only by hand-editing a root-owned
# ``runtime.toml``.
#
# ``tests/test_daemon_config_coverage.py`` asserts this map against
# ``tests/fixtures/daemon_config_keys.json`` — the declared ``GET /config``
# surface, pinned on the daemon side by
# ``get_config_key_set_and_mutability_are_pinned`` — and resolves every
# objectName on a constructed page.
#: Sanity band for the Fan Wizard spin-down timer. The *upper* bound is a
#: fallback: where the daemon advertises `limits.openfan_stop_timeout_s` and an
#: OpenFan controller is present, that value replaces it, because the daemon
#: rejects a 0% OpenFan command held longer than its own `STOP_TIMEOUT` — so a
#: longer timer here would promise a stop that does not happen (`WIRE-d`).
WIZARD_SPINDOWN_MIN_S = 5
WIZARD_SPINDOWN_MAX_S = 12

DAEMON_CONFIG_WIDGETS: dict[str, str] = {
    "profiles.search_dirs": "Settings_List_profileSearchDirs",
    "startup.delay_secs": "Settings_Spin_startupDelay",
    "polling.poll_interval_ms": "Settings_Spin_pollInterval",
    "serial.port": "Settings_Edit_serialPort",
    "serial.timeout_ms": "Settings_Spin_serialTimeout",
    "detection.allow_port_probe": "Settings_Check_allowPortProbe",
    "detection.enable_nvidia_telemetry": "Settings_Check_nvidiaTelemetry",
}

# Keys the daemon reports as ``mutable: false`` **by design** (DEC-243): a bad
# socket path locks every client out permanently, and moving the state dir
# orphans ``runtime.toml`` and the profile store. Still shown — read-only, in one
# shared label — because they are diagnostic.
DAEMON_CONFIG_READONLY_WIDGETS: dict[str, str] = {
    "ipc.socket_path": "Settings_Label_daemonPaths",
    "state.state_dir": "Settings_Label_daemonPaths",
}

#: The admin-owned system profile directory. The daemon always keeps it in the
#: search path and refuses to prune it (``config::SYSTEM_PROFILE_DIR``), so the
#: Remove button is disabled for it rather than offering an action that can only
#: come back as a 400.
_SYSTEM_PROFILE_DIR = "/etc/control-ofc/profiles"


def _same_dir(a: str, b: str) -> bool:
    """Whether two directory strings name the same directory.

    Resolved, not compared literally. The daemon persists whatever raw spelling
    the caller sent, so a trailing slash, a `.` segment or a symlinked spelling
    all describe the same directory under different strings — and a literal
    comparison of two of them silently answers "different". That matters where
    this is used: the Settings page blocks removing *this* application's own
    profiles directory, because `services/polling.py` re-registers it on the next
    connect and the removal would undo itself. A guard that misses on a spelling
    difference is a guard that lets exactly that happen.

    `Path.resolve()` is non-strict, so a directory that no longer exists still
    normalises; a symlink loop or an unreadable parent raises `OSError`, and
    falling back to string equality there is the conservative answer (it can only
    fail to block, never block wrongly).
    """
    try:
        return Path(a).resolve() == Path(b).resolve()
    except OSError:
        return a == b


def _safe_import_name(name: str, dest_dir: Path) -> str | None:
    """Validate an untrusted import key used as a ``<name>.json`` filename.

    Imported profile/theme dict keys are attacker-controlled: a key like
    ``"../../evil"`` would escape *dest_dir* (audit P1-B). Accept only a bare
    filename, and require the resolved destination to stay inside *dest_dir*
    (defense in depth). Returns *name* when safe, else ``None`` so the caller
    skips it (counted in the existing "invalid item(s) skipped" total). POSIX
    GUI only — backslash handling is out of scope.
    """
    if not name or name != Path(name).name:
        return None
    try:
        dest = (dest_dir / f"{name}.json").resolve()
        if not dest.is_relative_to(dest_dir.resolve()):
            return None
    except (OSError, ValueError):
        return None
    return name


_PAGE_NAMES = {
    PAGE_DASHBOARD: "Dashboard",
    PAGE_CONTROLS: "Controls",
    PAGE_SETTINGS: "Settings",
}


class SettingsPage(QWidget):
    """Application settings, path management, preferred sensors, and backup/restore.

    DEC-215: restyled from a QTabWidget into a single 2-column card surface; the
    former Themes tab moved to its own ``ThemePage`` and Import/Export folded into
    the "Sync & Backup" card.
    """

    # DEC-245: MainWindow owns the live splitters (it adopts them all in one pass),
    # so the page asks for a reset rather than reaching across for them.
    layout_reset_requested = Signal()

    def __init__(
        self,
        state: AppState | None = None,
        settings_service: AppSettingsService | None = None,
        client: DaemonClient | None = None,
        series_selection: SeriesSelectionModel | None = None,
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self._state = state
        self._settings_svc = settings_service or AppSettingsService()
        self._client = client
        # DEC-237: the live chart-series model. "Show all series" has to go
        # through it rather than writing hidden_chart_series directly — the model
        # is what the dashboard renders from, and its selection_changed signal is
        # what MainWindow persists on.
        self._series_selection = series_selection
        # Guards the alias table's itemChanged handler while it is repopulated.
        self._populating_aliases = False
        # DEC-243 daemon-config state. `_daemon_config_unsupported` latches on a
        # 404 so a pre-2.16.0 daemon is asked once, mirroring the DEC-200
        # preferred-sensor precedent; `_populating_daemon_cfg` stops the render
        # from firing the change handlers back at the daemon.
        self._daemon_config = None
        self._daemon_config_unsupported = False
        self._populating_daemon_cfg = False
        self._daemon_cfg_loaded = False
        # Last values the daemon reported, keyed by config key. The write
        # guard compares against these so a focus-out cannot re-POST an
        # untouched field (see _write_daemon_key).
        self._daemon_cfg_rendered: dict[str, object] = {}
        self._port_probe_reason = ""
        # Phase 4 (DEC-200): preferred-sensor selector state. ``_populating_prefs``
        # suppresses the change→POST while the combos are filled programmatically;
        # ``_prefs_loaded`` gates the one-time lazy fetch on first show.
        self._populating_prefs = False
        self._prefs_loaded = False
        # Set by `_sync_profile_search_dir` when the new profiles directory was
        # registered but the old one could not be retired, so the status line can
        # say so instead of reporting an unqualified success.
        self._stale_search_dir = ""

        self.setObjectName("Settings_Root")

        layout = QVBoxLayout(self)
        layout.setContentsMargins(24, 16, 24, 16)
        layout.setSpacing(10)

        # ─── Header: title + subtitle + Save Changes (batched save) ───
        header = QHBoxLayout()
        title = QLabel("Application Settings")
        title.setProperty("class", "PageTitle")
        header.addWidget(title)
        subtitle = QLabel("Application preferences, themes, and backup/restore")
        subtitle.setProperty("class", "PageSubtitle")
        header.addWidget(subtitle)
        header.addStretch()
        save_btn = make_button("Save Changes", "primary", object_name="Settings_Btn_saveApp")
        save_btn.setToolTip("Save application settings")
        save_btn.clicked.connect(self._save_app_settings)
        header.addWidget(save_btn)
        layout.addLayout(header)

        # ─── 2-column card grid (scrollable — DEC-206 deep-link scrolls here) ───
        self._app_scroll = QScrollArea()
        self._app_scroll.setWidgetResizable(True)
        self._app_scroll.setFrameShape(QFrame.Shape.NoFrame)
        body = QWidget()
        cols = QHBoxLayout(body)
        cols.setContentsMargins(0, 0, 0, 0)
        cols.setSpacing(16)
        col1 = QVBoxLayout()
        col1.setSpacing(16)
        # Column split is GUI-owned preferences (left) vs paths, daemon-facing
        # and backup (right). That also keeps the two balanced: putting three of
        # the five new cards on the right left it roughly twice the height of the
        # left, i.e. a screen of dead space under col1's stretch.
        col1.addWidget(self._build_general_startup_card())
        col1.addWidget(self._build_operational_card())
        col1.addWidget(self._build_fan_aliases_card())
        col1.addWidget(self._build_sensors_series_card())
        col1.addWidget(self._build_prompts_card())
        col1.addWidget(self._build_card_layout_card())
        col1.addStretch()
        col2 = QVBoxLayout()
        col2.setSpacing(16)
        col2.addWidget(self._build_path_management_card())
        col2.addWidget(self._build_preferred_sensors_group())
        col2.addWidget(self._build_daemon_config_card())
        col2.addWidget(self._build_sync_backup_card())
        col2.addStretch()
        cols.addLayout(col1, 1)
        cols.addLayout(col2, 1)
        self._app_scroll.setWidget(body)
        layout.addWidget(self._app_scroll, 1)

        # Status line
        self._status_label = QLabel("")
        self._status_label.setProperty("class", "PageSubtitle")
        layout.addWidget(self._status_label)

        # Load current values
        self._load_current_settings()

    # ─── Tab builders ────────────────────────────────────────────────

    def _setting_row(self, title: str, subtitle: str, control: QWidget) -> QHBoxLayout:
        """A mockup settings row: a title + sublabel on the left, a control right.

        DEC-268: the row also *names* the control for assistive tech. A
        ``ToggleSwitch`` carries no text of its own — the label beside it is a
        separate ``QLabel``, so the switch announces as an anonymous checkbox,
        once per boolean down the page. `set_accessible_label` existed for this
        since DEC-255 and no caller ever used it.

        Doing it here rather than at each of the construction sites is the
        point: this row is the only place that holds both the control and the
        words describing it, so a future control is named by construction
        instead of relying on whoever adds it to remember.
        """
        row = QHBoxLayout()
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        title_label = QLabel(title)
        text.addWidget(title_label)
        sub = QLabel(subtitle)
        sub.setProperty("class", "CardMeta")
        text.addWidget(sub)
        row.addLayout(text, 1)
        row.addWidget(control)
        # Every control on this page that carries no text of its own gets the row
        # title as its accessible name.
        #
        # History, because the reasoning has been wrong twice. DEC-268 named the
        # eight ToggleSwitches and scoped itself there, on the claim that naming
        # a QComboBox or QSpinBox would "replace useful information with a
        # restatement of the label". DEC-269 refuted that — QAccessible::Text
        # ::Name and ::Value are separate queries, so a name is ADDED to the
        # announcement ("Poll interval, spin button, 1000 ms"), not substituted
        # for the value — but corrected only the comment and deferred the code.
        # DEC-271 is the code (register OPEN-01 item 01-e).
        #
        # TWO mechanisms, because one is not enough on the platform we ship to.
        # DEC-269's refutation holds for QSpinBox and QLineEdit — measured, the
        # announced Name really is the title and the Value really is separate.
        # It does NOT hold for a non-editable QComboBox: Qt's Unix
        # QAccessibleComboBox::text(Name) falls through to the current item, so
        # `setAccessibleName` alone is silently discarded and the combo goes on
        # announcing "Dashboard" with no clue what it sets. `setBuddy` is what
        # survives — it publishes a RelationFlag.Label that AT-SPI exposes as
        # labelled-by, which is what Orca actually reads. Set both: the name for
        # platforms that honour it, the buddy for the one this app runs on.
        #
        # Buttons are deliberately excluded: several rows here place a
        # `make_button` on the right, and a QPushButton's visible text already
        # *is* its accessible name. Overwriting "Clear overrides" with "Sensor
        # classification overrides" would make the spoken name disagree with the
        # printed one. Where two such buttons share text ("Run again"), the
        # call site passes `accessible_name=` to `make_button` instead (DEC-269).
        #
        # 273-g moved the rule itself into `components.a11y.name_value_control`
        # so the rest of the app could use it. It lived here, private to one
        # page, which is exactly why every other surface stayed unnamed for
        # three ADRs: a dialog cannot call a private method on SettingsPage.
        name_value_control(control, title_label)
        return row

    def _build_general_startup_card(self) -> QWidget:
        card = Card()
        v = QVBoxLayout(card)
        v.setSpacing(10)
        v.addWidget(SectionHeader("General & Startup"))

        self._startup_page_combo = QComboBox()
        self._startup_page_combo.setObjectName("Settings_Combo_startupPage")
        for page_id, name in sorted(_PAGE_NAMES.items()):
            self._startup_page_combo.addItem(name, page_id)
        v.addLayout(
            self._setting_row(
                "Default startup page",
                "Page to load when the application launches",
                self._startup_page_combo,
            )
        )

        self._restore_page_cb = ToggleSwitch()
        self._restore_page_cb.setObjectName("Settings_Check_restorePage")
        v.addLayout(
            self._setting_row(
                "Restore last selected page",
                "Overrides the default startup page",
                self._restore_page_cb,
            )
        )

        self._demo_disconnect_cb = ToggleSwitch()
        self._demo_disconnect_cb.setObjectName("Settings_Check_demoDisconnect")
        v.addLayout(
            self._setting_row(
                "Start in demo mode",
                "When the daemon is unavailable",
                self._demo_disconnect_cb,
            )
        )

        self._gpu_zero_rpm_warn_cb = ToggleSwitch()
        self._gpu_zero_rpm_warn_cb.setObjectName("Settings_Check_gpuZeroRpmWarn")
        self._gpu_zero_rpm_warn_cb.setToolTip(
            "Display an informational popup explaining that zero-RPM idle mode "
            "is temporarily disabled while a curve controls the GPU fan"
        )
        v.addLayout(
            self._setting_row(
                "Show GPU zero-RPM warning",
                "Warn when adding a GPU fan to a role",
                self._gpu_zero_rpm_warn_cb,
            )
        )

        # Chart default range — retained (part of the batched save; DEC-215 D2).
        self._chart_range_combo = QComboBox()
        self._chart_range_combo.setObjectName("Settings_Combo_chartRange")
        from control_ofc.ui.widgets.timeline_chart import TIME_RANGES

        for label, _seconds in TIME_RANGES:
            self._chart_range_combo.addItem(label)
        v.addLayout(
            self._setting_row(
                "Chart time range",
                "Window shown on the dashboard graph; follows the chart's own selector",
                self._chart_range_combo,
            )
        )
        return card

    def _build_operational_card(self) -> QWidget:
        card = Card()
        v = QVBoxLayout(card)
        v.setSpacing(10)
        v.addWidget(SectionHeader("Operational Behavior"))

        self._wizard_spindown_spin = QSpinBox()
        self._wizard_spindown_spin.setObjectName("Settings_Spin_wizardSpindown")
        self._wizard_spindown_spin.setRange(WIZARD_SPINDOWN_MIN_S, WIZARD_SPINDOWN_MAX_S)
        self._wizard_spindown_spin.setSuffix(" seconds")
        self._wizard_spindown_spin.setToolTip(
            "How long each fan is stopped during the wizard identification test"
        )
        v.addLayout(
            self._setting_row(
                "Fan Wizard spin-down timer",
                "How long each fan is stopped during identification",
                self._wizard_spindown_spin,
            )
        )

        # NOTE: the daemon startup delay used to sit here, driven by an
        # AppSettings mirror. It is a daemon-owned key and now lives on the
        # Daemon Configuration card behind `_write_daemon_key` (DEC-285) — see
        # `_DAEMON_ROWS`. Do not move it back: on this card it bypassed the
        # no-op-write guard and Save POSTed it unconditionally.

        self._hide_igpu_cb = ToggleSwitch()
        self._hide_igpu_cb.setObjectName("Settings_Check_hideIgpu")
        self._hide_igpu_cb.setToolTip(
            "Hide iGPU temperature sensors when a discrete GPU is present"
        )
        v.addLayout(
            self._setting_row(
                "Auto-hide integrated GPU sensors",
                "Clean up the sensor list",
                self._hide_igpu_cb,
            )
        )

        self._hide_unused_fans_cb = ToggleSwitch()
        self._hide_unused_fans_cb.setObjectName("Settings_Check_hideUnusedFans")
        self._hide_unused_fans_cb.setToolTip("Hide motherboard fan headers that report zero RPM")
        v.addLayout(
            self._setting_row(
                "Auto-hide unused fan headers",
                "Hide headers reporting 0 RPM",
                self._hide_unused_fans_cb,
            )
        )
        return card

    def _build_path_management_card(self) -> QWidget:
        card = Card()
        v = QVBoxLayout(card)
        v.setSpacing(8)
        v.addWidget(SectionHeader("Path Management"))
        note = QLabel(
            "Override where profiles, themes, and exports are stored. "
            "Leave blank to use the default XDG location."
        )
        note.setWordWrap(True)
        note.setProperty("class", "CardMeta")
        v.addWidget(note)

        self._profiles_dir_label = QLabel()
        self._profiles_dir_label.setObjectName("Settings_Label_profilesDir")
        v.addLayout(
            self._dir_picker_row(
                "Profiles:",
                self._profiles_dir_label,
                self._browse_profiles_dir,
                key="profilesDir",
                what="profiles directory",
            )
        )
        self._themes_dir_label = QLabel()
        self._themes_dir_label.setObjectName("Settings_Label_themesDir")
        v.addLayout(
            self._dir_picker_row(
                "Themes:",
                self._themes_dir_label,
                self._browse_themes_dir,
                key="themesDir",
                what="themes directory",
            )
        )
        self._export_dir_label = QLabel()
        self._export_dir_label.setObjectName("Settings_Label_exportDir")
        v.addLayout(
            self._dir_picker_row(
                "Default export:",
                self._export_dir_label,
                self._browse_export_dir,
                key="exportDir",
                what="default export directory",
            )
        )

        # Disclosure: ``services/polling.py`` registers the profiles directory
        # with the daemon on first poll and again on every reconnect, so the
        # daemon can discover GUI-authored profiles. That is a write to daemon
        # config the operator would otherwise never see. The *daemon's* actual
        # list now has a real editor on the Daemon Configuration card (DEC-285);
        # this sentence discloses the automatic write and points at it.
        self._search_dir_note = QLabel()
        self._search_dir_note.setObjectName("Settings_Label_searchDirNote")
        self._search_dir_note.setWordWrap(True)
        self._search_dir_note.setProperty("class", "CardMeta")
        v.addWidget(self._search_dir_note)
        return card

    def _refresh_search_dir_note(self) -> None:
        """Update the profile-search-dir disclosure to match the current path.

        Deliberately says only what this side knows. It used to read as though
        it were reporting the daemon's search path while printing the GUI's own
        directory, which is a different thing and was wrong whenever the two had
        diverged — and they always had, because every change added an entry and
        nothing ever removed one. The daemon's real list is on the Daemon
        Configuration card, read from ``GET /config``.
        """
        path = self._profiles_dir_label.text() or str(profiles_dir())
        self._search_dir_note.setText(
            f"This directory is registered with the daemon as a profile search "
            f"directory ({path}), on connect and on every reconnect. The daemon's "
            f"full search path is under Daemon Configuration."
        )

    # ─── DEC-237: mirrored + reset surfaces ──────────────────────────
    # These cards make Settings a complete map of what can be configured. The
    # in-context affordances they mirror (Dashboard rename, Overview context
    # menu, Sensors-rail colour picker) all stay exactly where they were — this
    # is an additional surface, not a relocation.

    def _in_demo_mode(self) -> bool:
        """Demo replaces the per-hardware maps with synthetic ones (DEC-227).

        Demo fan ids collide exactly with real hardware ids, so MainWindow
        refuses to persist alias edits made during a demo session. Editing here
        would therefore look like it worked and silently not stick — so the
        editor is disabled instead of lying.
        """
        from control_ofc.api.models import OperationMode

        return self._state is not None and self._state.mode == OperationMode.DEMO

    def _build_fan_aliases_card(self) -> QWidget:
        card = Card()
        v = QVBoxLayout(card)
        v.setSpacing(8)
        header = SectionHeader("Fan Names", object_name="Settings_SectionHeader_fanAliases")
        v.addWidget(header)

        note = QLabel(
            "Custom names for your fans. Fans can also be renamed in place from "
            "the Dashboard and the Overview fan table — this is the same list."
        )
        note.setWordWrap(True)
        note.setProperty("class", "CardMeta")
        v.addWidget(note)

        self._fan_alias_table = QTableWidget(0, 2)
        self._fan_alias_table.setObjectName("Settings_Table_fanAliases")
        self._fan_alias_table.setHorizontalHeaderLabels(["Fan", "Name"])
        apply_dense_table(self._fan_alias_table)
        self._fan_alias_table.horizontalHeader().setSectionResizeMode(
            0, QHeaderView.ResizeMode.ResizeToContents
        )
        self._fan_alias_table.horizontalHeader().setStretchLastSection(True)
        self._fan_alias_table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self._fan_alias_table.setMinimumHeight(120)
        self._fan_alias_table.itemChanged.connect(self._on_fan_alias_edited)
        v.addWidget(self._fan_alias_table)

        self._fan_alias_note = QLabel("")
        self._fan_alias_note.setObjectName("Settings_Label_fanAliasNote")
        self._fan_alias_note.setWordWrap(True)
        self._fan_alias_note.setProperty("class", "CardMeta")
        v.addWidget(self._fan_alias_note)

        row = QHBoxLayout()
        row.addStretch()
        self._reset_aliases_btn = make_button(
            "Clear all names", "ghost", object_name="Settings_Btn_resetAliases"
        )
        self._reset_aliases_btn.setToolTip(
            "Remove every custom fan name and fall back to detection"
        )
        self._reset_aliases_btn.clicked.connect(self._reset_fan_aliases)
        row.addWidget(self._reset_aliases_btn)
        v.addLayout(row)
        return card

    def _refresh_fan_aliases(self) -> None:
        """Repopulate the alias table from live fans plus any orphaned aliases."""
        table = self._fan_alias_table
        aliases = dict(self._state.fan_aliases) if self._state else {}
        fan_ids = [f.id for f in (self._state.fans if self._state else [])]
        # Aliases whose fan is not currently present still deserve a row —
        # otherwise a stale name for unplugged hardware can never be cleared.
        for fan_id in aliases:
            if fan_id not in fan_ids:
                fan_ids.append(fan_id)

        demo = self._in_demo_mode()
        self._populating_aliases = True
        try:
            table.setRowCount(len(fan_ids))
            for row, fan_id in enumerate(fan_ids):
                id_item = QTableWidgetItem(fan_id)
                id_item.setFlags(id_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                table.setItem(row, 0, id_item)

                name_item = QTableWidgetItem(aliases.get(fan_id, ""))
                name_item.setData(Qt.ItemDataRole.UserRole, fan_id)
                if demo:
                    name_item.setFlags(name_item.flags() & ~Qt.ItemFlag.ItemIsEditable)
                fallback = self._state.fan_fallback_name(fan_id) if self._state else fan_id
                name_item.setToolTip(f"Leave blank to use the detected name: {fallback}")
                table.setItem(row, 1, name_item)
        finally:
            self._populating_aliases = False

        self._reset_aliases_btn.setEnabled(bool(aliases) and not demo)
        if demo:
            self._fan_alias_note.setText(
                "Demo mode — names shown are synthetic and edits are not saved."
            )
        elif not fan_ids:
            self._fan_alias_note.setText("No fans detected yet.")
        else:
            self._fan_alias_note.setText(
                f"{len(aliases)} of {len(fan_ids)} fans have custom names."
            )

    def _on_fan_alias_edited(self, item: QTableWidgetItem) -> None:
        if self._populating_aliases or item.column() != 1 or self._state is None:
            return
        fan_id = item.data(Qt.ItemDataRole.UserRole)
        if not fan_id:
            return
        # AppState owns the rule (empty/fallback clears) and emits the signal
        # MainWindow persists on — including the demo-mode refusal. Never write
        # settings.fan_aliases from here.
        self._state.apply_fan_rename(fan_id, item.text())
        self._refresh_fan_aliases()

    def _reset_fan_aliases(self) -> None:
        if self._state is None or self._in_demo_mode():
            return
        for fan_id in list(self._state.fan_aliases):
            self._state.set_fan_alias(fan_id, "")
        self._refresh_fan_aliases()
        self._set_status("Cleared all custom fan names")

    def _build_sensors_series_card(self) -> QWidget:
        card = Card()
        v = QVBoxLayout(card)
        v.setSpacing(8)
        v.addWidget(
            SectionHeader("Sensors & Chart Series", object_name="Settings_SectionHeader_sensors")
        )
        note = QLabel(
            "Hidden sensors, coolant overrides and chart colours are set where "
            "you see them — on the Overview sensor table and the Dashboard "
            "sensor list. Reset them from here."
        )
        note.setWordWrap(True)
        note.setProperty("class", "CardMeta")
        v.addWidget(note)

        self._unhide_sensors_btn = make_button(
            "Unhide all sensors", "ghost", object_name="Settings_Btn_unhideSensors"
        )
        self._unhide_sensors_btn.clicked.connect(self._unhide_all_sensors)
        v.addLayout(
            self._setting_row(
                "Hidden sensors",
                "Sensors hidden from the Overview table",
                self._unhide_sensors_btn,
            )
        )

        self._reset_classes_btn = make_button(
            "Clear overrides", "ghost", object_name="Settings_Btn_resetSensorClasses"
        )
        self._reset_classes_btn.clicked.connect(self._reset_sensor_classes)
        v.addLayout(
            self._setting_row(
                "Sensor classification overrides",
                "Sensors manually marked as coolant",
                self._reset_classes_btn,
            )
        )

        self._reset_colors_btn = make_button(
            "Reset colours", "ghost", object_name="Settings_Btn_resetSeriesColors"
        )
        self._reset_colors_btn.clicked.connect(self._reset_series_colors)
        v.addLayout(
            self._setting_row(
                "Custom chart colours",
                "Series colours picked from the sensor list",
                self._reset_colors_btn,
            )
        )

        self._show_series_btn = make_button(
            "Show all series", "ghost", object_name="Settings_Btn_showAllSeries"
        )
        self._show_series_btn.clicked.connect(self._show_all_series)
        v.addLayout(
            self._setting_row(
                "Hidden chart series",
                "Series hidden from the dashboard graph",
                self._show_series_btn,
            )
        )

        self._prune_orphans_btn = make_button(
            "Remove", "ghost", object_name="Settings_Btn_pruneChartOrphans"
        )
        self._prune_orphans_btn.clicked.connect(self._prune_chart_orphans)
        v.addLayout(
            self._setting_row(
                "Settings for missing hardware",
                "Colours and hidden-series entries for fans and sensors the daemon "
                "no longer reports",
                self._prune_orphans_btn,
            )
        )
        return card

    def _unhide_all_sensors(self) -> None:
        self._settings_svc.update(diagnostics_hidden_sensor_ids=[])
        self._refresh_reset_buttons()
        self._set_status("All sensors unhidden")

    def _reset_sensor_classes(self) -> None:
        if self._state is not None:
            # Route through AppState so the Overview table re-renders via
            # sensor_class_override_changed and MainWindow persists the result.
            for sensor_id in list(self._state.sensor_class_overrides):
                self._state.set_sensor_class_override(sensor_id, "")
        else:
            self._settings_svc.update(sensor_class_overrides={})
        self._refresh_reset_buttons()
        self._set_status("Sensor classification overrides cleared")

    def _reset_series_colors(self) -> None:
        self._settings_svc.update(series_colors={})
        self._refresh_reset_buttons()
        self._set_status("Chart series colours reset to defaults")

    def _chart_orphans(self) -> OrphanReport:
        """Chart settings referring to hardware the daemon no longer reports.

        Returns an empty report whenever the live key set is empty — disconnected,
        or before the first poll. Without that, "prune what the daemon did not
        mention" would mean "delete everything" (see ``find_orphans``).
        """
        if self._state is None:
            return OrphanReport()
        status = self._state.daemon_status
        known = live_series_keys(
            (f.id for f in self._state.fans),
            (s.id for s in self._state.sensors),
            # DEC-193: quarantined sensors are evicted from `sensors`, so without
            # this a WiFi temp with the radio off reads as gone and loses its colour.
            (u.id for u in (status.unavailable_sensors if status else [])),
        )
        s = self._settings_svc.settings
        return find_orphans(s.hidden_chart_series, s.series_colors, known)

    def _prune_chart_orphans(self) -> None:
        report = self._chart_orphans()
        if not report:
            return
        s = self._settings_svc.settings
        dropped = set(report.hidden_series)
        self._settings_svc.update(
            hidden_chart_series=[k for k in s.hidden_chart_series if k not in dropped],
            series_colors={
                k: v for k, v in s.series_colors.items() if k not in set(report.series_colors)
            },
        )
        # Keep the live model in step, or the chart goes on hiding a series whose
        # saved entry has just been removed and the next emit writes it back.
        if self._series_selection is not None:
            self._series_selection.load_hidden(self._settings_svc.settings.hidden_chart_series)
        self._refresh_reset_buttons()
        # Report what actually happened. Both keys are demo-sealed (DEC-244), so in
        # a demo session update() drops the call entirely — announcing a removal
        # that did not occur is the same fabricated-success bug v2.39.0 fixed
        # elsewhere, just smaller.
        if self._chart_orphans().total == report.total:
            self._set_status("Not saved — demo mode uses session-only settings")
        else:
            self._set_status(f"Removed {report.total} setting(s) for missing hardware")

    def _show_all_series(self) -> None:
        if self._series_selection is not None:
            # select_all() emits selection_changed, which is what persists the
            # (now empty) hidden set — writing the setting directly would leave
            # the live model still hiding them until restart.
            self._series_selection.select_all()
        else:
            self._settings_svc.update(hidden_chart_series=[])
        self._refresh_reset_buttons()
        self._set_status("All chart series shown")

    # ─── DEC-243: daemon configuration ───────────────────────────────
    # Read side of a surface that used to be write-only. The GUI previously kept
    # a local mirror of the startup delay and pushed it on save, so a fresh GUI
    # against a daemon set to 10 s displayed 0 s — the field was a guess. These
    # values now come from the daemon, and `restart_pending` is the daemon's own
    # verdict (on-disk vs running), never something inferred here.

    #: Single-control rows rendered by the card: (config key, label, sublabel).
    #: Ordered to match the daemon's own ``keys[]`` emission order, minus
    #: ``profiles.search_dirs`` — that key is a list, so it gets its own block
    #: below rather than a one-control row.
    _DAEMON_ROWS = (
        (
            "startup.delay_secs",
            "Startup delay",
            "Wait before detecting devices after boot — for slow USB/hwmon enumeration",
        ),
        ("polling.poll_interval_ms", "Poll interval", "How often the daemon reads hardware"),
        ("serial.port", "Serial port", "OpenFan device path — blank to auto-detect"),
        ("serial.timeout_ms", "Serial timeout", "Read timeout for the OpenFan device"),
        ("detection.allow_port_probe", "Super-I/O port probe", "Opt-in active chip detection"),
        ("detection.enable_nvidia_telemetry", "NVIDIA telemetry", "Opt-in read-only NVML"),
    )

    def _build_daemon_config_card(self) -> QWidget:
        card = Card()
        v = QVBoxLayout(card)
        v.setSpacing(8)
        v.addWidget(
            SectionHeader("Daemon Configuration", object_name="Settings_SectionHeader_daemonConfig")
        )

        self._daemon_cfg_note = QLabel(
            "Settings owned by the daemon. Changes are written to its runtime "
            "configuration and take effect when the daemon restarts."
        )
        self._daemon_cfg_note.setWordWrap(True)
        self._daemon_cfg_note.setProperty("class", "CardMeta")
        v.addWidget(self._daemon_cfg_note)

        self._daemon_restart_banner = QLabel("")
        self._daemon_restart_banner.setObjectName("Settings_Label_daemonRestartBanner")
        self._daemon_restart_banner.setTextFormat(Qt.TextFormat.PlainText)  # DEC-231
        self._daemon_restart_banner.setWordWrap(True)
        self._daemon_restart_banner.setVisible(False)
        self._daemon_restart_banner.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        v.addWidget(self._daemon_restart_banner)

        self._startup_delay_spin = QSpinBox()
        self._startup_delay_spin.setObjectName("Settings_Spin_startupDelay")
        self._startup_delay_spin.setRange(0, 30)
        self._startup_delay_spin.setSuffix(" seconds")
        self._startup_delay_spin.setToolTip(
            "Delay before daemon begins device detection after boot (takes effect on restart)"
        )
        self._startup_delay_spin.editingFinished.connect(
            lambda: self._write_daemon_key(
                "startup.delay_secs",
                self._startup_delay_spin.value(),
                lambda c: c.set_startup_delay(self._startup_delay_spin.value()),
            )
        )

        self._poll_interval_spin = QSpinBox()
        self._poll_interval_spin.setObjectName("Settings_Spin_pollInterval")
        self._poll_interval_spin.setRange(250, 2000)
        self._poll_interval_spin.setSingleStep(250)
        self._poll_interval_spin.setSuffix(" ms")
        self._poll_interval_spin.editingFinished.connect(
            lambda: self._write_daemon_key(
                "polling.poll_interval_ms",
                self._poll_interval_spin.value(),
                lambda c: c.set_poll_interval(self._poll_interval_spin.value()),
            )
        )

        self._serial_port_edit = QLineEdit()
        self._serial_port_edit.setObjectName("Settings_Edit_serialPort")
        self._serial_port_edit.setPlaceholderText("auto-detect")
        self._serial_port_edit.setMaximumWidth(220)
        self._serial_port_edit.editingFinished.connect(self._write_serial_port)

        self._serial_timeout_spin = QSpinBox()
        self._serial_timeout_spin.setObjectName("Settings_Spin_serialTimeout")
        self._serial_timeout_spin.setRange(50, 1000)
        self._serial_timeout_spin.setSingleStep(50)
        self._serial_timeout_spin.setSuffix(" ms")
        self._serial_timeout_spin.editingFinished.connect(
            lambda: self._write_daemon_key(
                "serial.timeout_ms",
                self._serial_timeout_spin.value(),
                lambda c: c.set_serial_timeout(self._serial_timeout_spin.value()),
            )
        )

        self._port_probe_toggle = ToggleSwitch()
        self._port_probe_toggle.setObjectName("Settings_Check_allowPortProbe")
        self._port_probe_toggle.toggled.connect(
            lambda on: self._write_daemon_key(
                "detection.allow_port_probe", on, lambda c: c.set_allow_port_probe(on)
            )
        )

        self._nvidia_toggle = ToggleSwitch()
        self._nvidia_toggle.setObjectName("Settings_Check_nvidiaTelemetry")
        self._nvidia_toggle.toggled.connect(
            lambda on: self._write_daemon_key(
                "detection.enable_nvidia_telemetry", on, lambda c: c.set_nvidia_telemetry(on)
            )
        )

        # Keyed by config key, and kept: `_apply_daemon_key_mutability` drives
        # each editor's enabled state off the daemon's own `keys[].mutable`
        # rather than off this hardcoded membership (`WIRE-g`).
        controls = {
            "startup.delay_secs": self._startup_delay_spin,
            "polling.poll_interval_ms": self._poll_interval_spin,
            "serial.port": self._serial_port_edit,
            "serial.timeout_ms": self._serial_timeout_spin,
            "detection.allow_port_probe": self._port_probe_toggle,
            "detection.enable_nvidia_telemetry": self._nvidia_toggle,
        }
        self._daemon_key_widgets: dict[str, QWidget] = dict(controls)
        # Per-row source/restart annotation, keyed by config key.
        self._daemon_row_notes: dict[str, QLabel] = {}
        for key, title, subtitle in self._DAEMON_ROWS:
            v.addLayout(self._setting_row(title, subtitle, controls[key]))
            note = QLabel("")
            note.setObjectName(f"Settings_Label_daemonNote_{key.replace('.', '_')}")
            # DEC-231: interpolates daemon-supplied strings, one of which
            # (running_value) another local user can set. AutoText would let
            # Qt.mightBeRichText() render it as HTML.
            note.setTextFormat(Qt.TextFormat.PlainText)
            note.setWordWrap(True)
            note.setProperty("class", "CardMeta")
            note.setVisible(False)
            v.addWidget(note)
            self._daemon_row_notes[key] = note

        # ── Profile search directories (DEC-285) ──────────────────────
        # The one daemon key that applies *live*, and the one this card used to
        # fetch and throw away. Its only previous surface was a sentence on the
        # Path Management card printing the GUI's own directory — never the
        # daemon's list, which is what actually decides whether a profile
        # resolves.
        dirs_title = QLabel("Profile search directories")
        v.addWidget(dirs_title)
        dirs_sub = QLabel(
            "Where the daemon looks for profile files. Applies immediately — no restart."
        )
        dirs_sub.setWordWrap(True)
        dirs_sub.setProperty("class", "CardMeta")
        v.addWidget(dirs_sub)

        self._search_dirs_list = QListWidget()
        self._search_dirs_list.setObjectName("Settings_List_profileSearchDirs")
        self._search_dirs_list.setMaximumHeight(110)
        # `name_value_control` deliberately covers value controls only — an item
        # view announces its current item, not a value, so it is not in
        # VALUE_CONTROLS. The two calls are the same pair that helper makes, for
        # the same reason (DEC-269): the buddy is the half AT-SPI actually reads
        # on Linux, and the name is for platforms that honour the property.
        self._search_dirs_list.setAccessibleName("Profile search directories")
        dirs_title.setBuddy(self._search_dirs_list)
        self._search_dirs_list.currentRowChanged.connect(
            lambda _row: self._refresh_search_dir_buttons()
        )
        v.addWidget(self._search_dirs_list)

        dir_btns = QHBoxLayout()
        self._add_search_dir_btn = make_button(
            "Add...",
            "secondary",
            object_name="Settings_Btn_addSearchDir",
            accessible_name="Add a profile search directory",
        )
        self._add_search_dir_btn.clicked.connect(self._add_search_dir)
        dir_btns.addWidget(self._add_search_dir_btn)
        self._remove_search_dir_btn = make_button(
            "Remove",
            "secondary",
            object_name="Settings_Btn_removeSearchDir",
            accessible_name="Remove the selected profile search directory",
        )
        self._remove_search_dir_btn.clicked.connect(self._remove_search_dir)
        dir_btns.addWidget(self._remove_search_dir_btn)
        dir_btns.addStretch()
        v.addLayout(dir_btns)

        dirs_note = QLabel("")
        dirs_note.setObjectName("Settings_Label_daemonNote_profiles_search_dirs")
        dirs_note.setTextFormat(Qt.TextFormat.PlainText)  # DEC-231
        dirs_note.setWordWrap(True)
        dirs_note.setProperty("class", "CardMeta")
        dirs_note.setVisible(False)
        v.addWidget(dirs_note)
        self._daemon_row_notes["profiles.search_dirs"] = dirs_note

        # Read-only by design (DEC-243): a bad socket path locks the GUI out of
        # the daemon permanently, and moving the state dir orphans runtime.toml
        # and the daemon-owned profile store. Shown so they stay diagnosable.
        self._daemon_paths_label = QLabel("")
        self._daemon_paths_label.setObjectName("Settings_Label_daemonPaths")
        self._daemon_paths_label.setTextFormat(Qt.TextFormat.PlainText)  # DEC-231
        self._daemon_paths_label.setWordWrap(True)
        self._daemon_paths_label.setProperty("class", "CardMeta")
        self._daemon_paths_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        v.addWidget(self._daemon_paths_label)

        self._daemon_cfg_result = QLabel("")
        self._daemon_cfg_result.setObjectName("Settings_Label_daemonCfgResult")
        self._daemon_cfg_result.setTextFormat(Qt.TextFormat.PlainText)  # DEC-231
        self._daemon_cfg_result.setWordWrap(True)
        self._daemon_cfg_result.setVisible(False)
        v.addWidget(self._daemon_cfg_result)
        # Nothing has been read from the daemon yet, so the search-dir buttons
        # must start in their unavailable state rather than at Qt's default
        # (enabled) — an Add that can only open a dialog and then do nothing is
        # worse than a disabled one.
        self._refresh_search_dir_buttons()
        return card

    def _apply_daemon_key_mutability(self, cfg) -> None:
        """Let the daemon decide which config keys are editable (`WIRE-g`).

        `GET /config` reports `mutable` per key — whether a `POST /config/*`
        route exists for it — and the GUI hardcoded that judgement instead, in
        the membership of `DAEMON_CONFIG_WIDGETS`. A daemon that makes a key
        newly immutable would therefore keep an editor that can only come back
        as a 400, and one that makes a key newly mutable would keep it greyed
        out. Same drift class as DEC-257.

        Only ever *disables*: a key the daemon does not report at all leaves its
        widget as `_set_daemon_config_available` left it, because absence means
        "this daemon predates the key", not "this key is locked" — and greying
        a control the user could previously edit on the strength of a truncated
        response would be the same over-reach in the other direction.
        """
        for key, widget in self._daemon_key_widgets.items():
            entry = cfg.get(key)
            if entry is not None and not entry.mutable:
                widget.setEnabled(False)
                widget.setToolTip(
                    f"{key} is read-only on this daemon — it reports no write route "
                    "for it. Edit the daemon's config file and restart."
                )
        dirs = cfg.get("profiles.search_dirs")
        if dirs is not None and not dirs.mutable:
            for widget in (self._add_search_dir_btn, self._remove_search_dir_btn):
                widget.setEnabled(False)

    def _daemon_config_controls(self) -> list[QWidget]:
        return [
            self._startup_delay_spin,
            self._poll_interval_spin,
            self._serial_port_edit,
            self._serial_timeout_spin,
            self._port_probe_toggle,
            self._nvidia_toggle,
            self._search_dirs_list,
            self._add_search_dir_btn,
            self._remove_search_dir_btn,
        ]

    def _refresh_daemon_config(self) -> None:
        """Fetch GET /config and render it. 404-tolerant (pre-2.16.0 daemon)."""
        if self._client is None or self._daemon_config_unsupported:
            self._set_daemon_config_available(False)
            return
        try:
            cfg = self._client.get_daemon_config()
        except DaemonError as e:
            if getattr(e, "status", None) == 404:
                # Older daemon: the card cannot be truthful, so it stands down
                # rather than showing values it would have to invent.
                self._daemon_config_unsupported = True
                self._daemon_cfg_note.setText(
                    "This daemon is too old to report its configuration "
                    f"{requires_daemon('daemon_config_report')}."
                )
            else:
                self._set_daemon_cfg_result(
                    f"Could not read daemon configuration: {e.message}", "CriticalChip"
                )
            self._set_daemon_config_available(False)
            return
        except (ConnectionError, OSError):
            self._daemon_cfg_note.setText("Daemon unavailable — configuration not loaded.")
            self._set_daemon_config_available(False)
            return

        self._daemon_config = cfg
        # Latch only on a real answer. Setting this before the call (as it was)
        # meant a single timeout or a daemon restart during the user's first
        # visit disabled the card for the whole session, with no way back: the
        # controls are disabled, so no signal can trigger another fetch. The
        # 404 branch above latches separately and permanently, which is correct
        # — a daemon does not gain the endpoint without restarting.
        self._daemon_cfg_loaded = True
        self._set_daemon_config_available(True)
        self._render_daemon_config(cfg)

    def _set_daemon_config_available(self, available: bool) -> None:
        for widget in self._daemon_config_controls():
            widget.setEnabled(available)

    def _render_daemon_config(self, cfg) -> None:
        self._populating_daemon_cfg = True
        try:
            delay = cfg.get("startup.delay_secs")
            if delay is not None and isinstance(delay.value, int):
                self._show_spin_value(self._startup_delay_spin, delay.value)
            poll = cfg.get("polling.poll_interval_ms")
            if poll is not None and isinstance(poll.value, int):
                self._show_spin_value(self._poll_interval_spin, poll.value)
            port = cfg.get("serial.port")
            if port is not None:
                self._serial_port_edit.setText("" if port.value is None else str(port.value))
            timeout = cfg.get("serial.timeout_ms")
            if timeout is not None and isinstance(timeout.value, int):
                self._show_spin_value(self._serial_timeout_spin, timeout.value)
            probe = cfg.get("detection.allow_port_probe")
            if probe is not None:
                self._port_probe_toggle.setChecked(bool(probe.value))
            nvidia = cfg.get("detection.enable_nvidia_telemetry")
            if nvidia is not None:
                self._nvidia_toggle.setChecked(bool(nvidia.value))

            self._render_search_dirs(cfg.get("profiles.search_dirs"))

            # Snapshot what the daemon reported for the editable keys, so the
            # write guard can tell a real edit from a focus-out.
            self._daemon_cfg_rendered = {
                key: entry.value
                for key, _title, _sub in self._DAEMON_ROWS
                if (entry := cfg.get(key)) is not None
            }
        finally:
            self._populating_daemon_cfg = False

        self._apply_daemon_key_mutability(cfg)

        for key, label in self._daemon_row_notes.items():
            self._render_daemon_row_note(
                cfg.get(key),
                label,
                extra=self._search_dir_divergence(cfg) if key == "profiles.search_dirs" else "",
            )

        if cfg.restart_pending:
            # Name the keys rather than showing a bare count: a count alone reads
            # as "1 change pending" with nothing on screen to explain which, and
            # a future daemon key may well have no row here at all.
            pending = [k.key for k in cfg.keys if k.restart_pending]
            self._daemon_restart_banner.setText(
                f"Saved but not yet in effect: {', '.join(pending)}. "
                "Restart the daemon to apply:  sudo systemctl restart control-ofc-daemon"
            )
            set_chip_class(self._daemon_restart_banner, "CautionChip", skip_if_unchanged=True)
        self._daemon_restart_banner.setVisible(cfg.restart_pending)

        self._daemon_paths_label.setText(
            f"Admin config (hand-edit only): {cfg.admin_config_path}\n"
            f"Daemon runtime config: {cfg.runtime_config_path}\n"
            f"Socket: {self._daemon_value(cfg, 'ipc.socket_path')} · "
            f"State directory: {self._daemon_value(cfg, 'state.state_dir')}"
        )

    @staticmethod
    def _show_spin_value(spin: QSpinBox, value: int) -> None:
        """Display the daemon's value even when it sits outside the API's range.

        `daemon.toml` has no upper bound on the interval keys, so an operator may
        legitimately be running a value this control refuses to *accept*. Letting
        Qt clamp it would make the card display a number the daemon is not
        running — exactly the dishonesty this card exists to remove — and would
        then make a focus-out write the clamp back, silently shadowing the
        operator's file with a value nobody chose.
        """
        if value > spin.maximum():
            spin.setMaximum(value)
        if value < spin.minimum():
            spin.setMinimum(value)
        spin.setValue(value)

    @staticmethod
    def _daemon_value(cfg, key: str) -> str:
        entry = cfg.get(key)
        return "—" if entry is None else str(entry.value)

    def _render_daemon_row_note(self, entry, label: QLabel, extra: str = "") -> None:
        """Annotate a row with where its value came from and what it still needs.

        ``extra`` is an already-composed sentence the caller wants appended —
        used for the one key whose divergence the daemon does not flag (see
        ``_search_dir_divergence``).
        """
        if entry is None:
            label.setVisible(False)
            return
        parts: list[str] = []
        if entry.source == "runtime":
            parts.append("set here (overrides daemon.toml)")
        elif entry.source == "admin":
            parts.append("set in daemon.toml")
        if entry.restart_pending:
            parts.append(f"restart required — daemon is running {entry.running_display}")
        if entry.requires_privilege:
            # Never let the toggle alone read as "the feature is on".
            parts.append(entry.requires_privilege)
        if entry.key == "detection.allow_port_probe" and self._port_probe_reason:
            parts.append(self._port_probe_reason)
        if extra:
            parts.append(extra)
        label.setText(" · ".join(parts))
        label.setVisible(bool(parts))

    # ── Profile search directories (DEC-285) ─────────────────────────

    def _render_search_dirs(self, entry) -> None:
        """Fill the list from the daemon's **running** search path.

        ``running_value``, not ``value``. For every other key those are "what a
        restart would give" vs "what is live", and ``value`` is the right thing
        to edit against. This key applies immediately, so the live list *is* the
        answer to "where does the daemon look?" — and the daemon reports it from
        its in-process lock precisely so a client can show that.
        """
        self._search_dirs_list.clear()
        if entry is None:
            self._refresh_search_dir_buttons()
            return
        running = entry.running_value
        dirs = running if isinstance(running, list) else entry.value
        if isinstance(dirs, list):
            self._search_dirs_list.addItems([str(d) for d in dirs])
        self._refresh_search_dir_buttons()

    @staticmethod
    def _search_dir_divergence(cfg) -> str:
        """Report a config-file list the running daemon has not picked up.

        The daemon reports ``requires_restart: false`` for this key — correct,
        because an API write applies live — so it never raises
        ``restart_pending`` for it. But a hand-edited ``daemon.toml`` with no
        runtime override *does* leave the files and the process disagreeing, with
        nothing anywhere to say so. This is the only place that can notice.
        """
        entry = cfg.get("profiles.search_dirs")
        if entry is None or not isinstance(entry.value, list):
            return ""
        if not isinstance(entry.running_value, list):
            return ""
        # Compare in ONE direction only: entries the config files list that the
        # running daemon is not using. The reverse is normal and permanent — the
        # daemon injects its own profile store into the running list at boot
        # (`main.rs::with_store_dir`) and never writes it into the files unless a
        # runtime override happens to capture it, so an equality test reports a
        # divergence on every fresh daemon and advises a restart that cannot
        # resolve it. Extra running entries are never something a restart fixes;
        # missing ones are exactly what a restart would apply.
        missing = [d for d in entry.value if d not in entry.running_value]
        if not missing:
            return ""
        return (
            f"the configuration files also list {', '.join(str(d) for d in missing)} — "
            f"restart the daemon to apply that"
        )

    def _daemon_supports_dir_removal(self) -> bool:
        """Whether this daemon accepts a ``remove`` on the search-dir endpoint.

        Must be checked, not probed. A pre-2.23.0 daemon does not 404 a
        ``remove`` — it parses only ``add`` and silently ignores the rest — so an
        ungated call would report success having pruned nothing.
        """
        caps = self._state.capabilities if self._state else None
        control = getattr(caps, "control", None)
        return bool(control and getattr(control, "profile_search_dir_remove", False))

    def _search_dir_removal_block(self) -> str:
        """Why the selected search dir cannot be removed, or ``""`` if it can.

        Four entries are un-removable, and every one of them would otherwise
        offer an action that does not do what the button says:

        * **the system directory** — the daemon refuses it (it holds the
          admin-installed profiles);
        * **the last remaining entry** — the daemon refuses it; profile
          activation resolves against this list, so emptying it is a soft-lock;
        * **the first entry** — that is *by definition* the daemon's profile
          store of record (`profile.rs::store_dir()` is
          `profile_search_dirs.first()`, and it is the write target for profile
          create and delete). The daemon refuses it too;
        * **this GUI's own profiles directory** — the daemon would accept it,
          and ``services/polling.py`` would silently register it again on the
          next connect. The removal would appear to work and then undo itself,
          which is exactly the silent-partial-success failure this whole change
          exists to remove. Change it under Path Management instead — that path
          retires the old registration in the same request.

        The first three are the honest local form of a rule the daemon enforces;
        the fourth is a rule only this side can know.
        """
        item = self._search_dirs_list.currentItem()
        if item is None:
            return "no directory selected"
        if item.text() == _SYSTEM_PROFILE_DIR:
            return "the system profile directory cannot be removed"
        if self._search_dirs_list.count() <= 1:
            return "at least one profile search directory must remain"
        if self._search_dirs_list.currentRow() == 0:
            return "the daemon's own profile store cannot be removed"
        if _same_dir(item.text(), self._profiles_dir_label.text() or str(profiles_dir())):
            return (
                "this application registers its own profiles directory on every "
                "connect, so removing it here would not stick — change it under "
                "Path Management instead"
            )
        return ""

    def _refresh_search_dir_buttons(self) -> None:
        """Enable Remove only when removing the selection is actually possible."""
        connected = (
            self._client is not None
            and not self._daemon_config_unsupported
            and self._daemon_cfg_loaded
        )
        self._add_search_dir_btn.setEnabled(connected)
        supported = self._daemon_supports_dir_removal()
        block = self._search_dir_removal_block()
        self._remove_search_dir_btn.setEnabled(connected and supported and not block)
        if connected and not supported:
            self._remove_search_dir_btn.setToolTip(
                "This daemon is too old to remove a profile search directory "
                f"{requires_daemon('profile_search_dir_removal')}."
            )
        elif block:
            self._remove_search_dir_btn.setToolTip(f"Cannot remove: {block}.")
        else:
            self._remove_search_dir_btn.setToolTip(
                "Stop the daemon looking for profiles in the selected directory"
            )

    def _add_search_dir(self) -> None:
        start = self._profiles_dir_label.text() or str(profiles_dir())
        path = QFileDialog.getExistingDirectory(self, "Add Profile Search Directory", start)
        if path:
            self._edit_search_dirs(add=[path], done=f"Added {path}")

    def _remove_search_dir(self) -> None:
        # Re-checked here, not merely reflected in the button's enabled state:
        # the enabled state is a rendering of this rule, and a rule that lives
        # only in a rendering is one a future caller can walk around.
        block = self._search_dir_removal_block()
        if block:
            self._set_daemon_cfg_result(f"Cannot remove: {block}.", "CautionChip")
            return
        path = self._search_dirs_list.currentItem().text()
        self._edit_search_dirs(remove=[path], done=f"Removed {path}")

    def _edit_search_dirs(
        self,
        *,
        add: list[str] | None = None,
        remove: list[str] | None = None,
        done: str,
    ) -> None:
        """POST one search-dir edit, then re-read so the list shows daemon truth.

        Never predicts the result: an edit can be legitimately idempotent
        (removing an entry that was never registered, re-adding one already
        present), so the daemon's returned list is the only honest thing to
        render.
        """
        if self._client is None or self._daemon_config_unsupported:
            return
        if remove and not self._daemon_supports_dir_removal():
            self._set_daemon_cfg_result(
                "This daemon is too old to remove a profile search directory "
                f"{requires_daemon('profile_search_dir_removal')}.",
                "CautionChip",
            )
            return
        try:
            self._client.update_profile_search_dirs(add=add, remove=remove)
        except DaemonError as e:
            self._set_daemon_cfg_result(
                f"Could not update profile search directories: {e.message}", "CriticalChip"
            )
            self._refresh_daemon_config()
            return
        except (ConnectionError, OSError):
            self._set_daemon_cfg_result("Daemon unavailable — not saved.", "CautionChip")
            self._refresh_daemon_config()
            return
        self._set_daemon_cfg_result(f"{done}.", "SuccessChip")
        self._refresh_daemon_config()

    def _sync_profile_search_dir(self, old_path: str, new_path: str) -> str | None:
        """Register *new_path* with the daemon and retire *old_path* in one call.

        Returns a user-facing error message, or ``None`` on success (and when
        there is no client to talk to).

        The retire half is what stops the daemon's list growing without bound.
        ``services/polling.py`` re-registers the GUI's profiles directory on
        every connect, so before this each directory change left the previous
        one behind **permanently** — the endpoint was add-only, so three changes
        meant three live entries, visible in no UI and removable only by hand-
        editing a root-owned ``runtime.toml``. Against a pre-2.23.0 daemon the
        add still happens and the old entry still leaks; that is the honest
        degradation, and the capability flag is why we know which one we got.
        """
        self._stale_search_dir = ""
        if self._client is None:
            return None
        retire = (
            [old_path]
            if old_path and old_path != new_path and self._daemon_supports_dir_removal()
            else None
        )
        error = self._post_search_dir_edit(add=[new_path], remove=retire)
        if error is None or retire is None:
            return error
        # The retire half can be refused on its own terms — the old directory may
        # be outside this user's home (a `profiles_dir_override` the daemon never
        # accepted), or one the daemon protects — and the daemon rejects the
        # WHOLE request when it is. By this point `_handle_dir_change` has already
        # moved the profile files into the new directory, so losing the *add* is
        # the one outcome that must not happen: the daemon would be left
        # searching only the old, now-empty location and GUI-authored profiles
        # would stop resolving. Fall back to the pre-DEC-285 behaviour — register
        # the new directory alone and leave the stale entry behind, which is also
        # exactly what happens against a daemon too old to remove anything.
        add_only = self._post_search_dir_edit(add=[new_path])
        if add_only is not None:
            return add_only
        log.warning(
            "Registered profile search dir %s but could not retire %s: %s",
            new_path,
            old_path,
            error,
        )
        self._stale_search_dir = old_path
        return None

    def _post_search_dir_edit(
        self, *, add: list[str] | None = None, remove: list[str] | None = None
    ) -> str | None:
        """One `POST /config/profile-search-dirs`. Returns a message, or None."""
        try:
            self._client.update_profile_search_dirs(add=add, remove=remove)
        except DaemonError as exc:
            return exc.message
        except (ConnectionError, OSError) as exc:
            return str(exc)
        return None

    def _refresh_port_probe_availability(self) -> None:
        """Ask the daemon whether the probe can actually run right now.

        The config flag is only half the requirement — the other half is a root
        systemd drop-in this GUI cannot install. The daemon already computes the
        real answer (`port_probe_available` + reason), so report that rather than
        implying the toggle is sufficient.
        """
        if self._client is None:
            return
        try:
            readiness = self._client.hardware_readiness()
        except (DaemonError, ConnectionError, OSError):
            return
        superio = getattr(readiness, "superio", None)
        if superio is None or superio.port_probe_available:
            self._port_probe_reason = ""
        else:
            self._port_probe_reason = superio.port_probe_reason or ""

    def _write_serial_port(self) -> None:
        text = self._serial_port_edit.text().strip()
        self._write_daemon_key(
            "serial.port", text or None, lambda c: c.set_serial_port(text or None)
        )

    def _write_daemon_key(self, key: str, value: object, call) -> None:
        """POST one daemon config key, then re-read so the card shows daemon truth.

        Skips the write when *value* already matches what the daemon reported.
        `QSpinBox.editingFinished` and `QLineEdit.editingFinished` fire on
        focus-out whether or not anything was edited, so without this guard
        merely tabbing through the card would POST every field — writing them
        into runtime.toml, flipping their `source` to "runtime", and thereby
        permanently shadowing the operator's daemon.toml with values they never
        chose. Silent config mutation as a side effect of looking at a page.
        """
        if self._populating_daemon_cfg or self._client is None:
            return
        if self._daemon_config_unsupported:
            return
        if key in self._daemon_cfg_rendered and self._daemon_cfg_rendered[key] == value:
            return
        try:
            result = call(self._client)
        except DaemonError as e:
            self._set_daemon_cfg_result(f"Could not save {key}: {e.message}", "CriticalChip")
            self._refresh_daemon_config()  # revert the control to daemon truth
            return
        except (ConnectionError, OSError):
            self._set_daemon_cfg_result("Daemon unavailable — not saved.", "CautionChip")
            self._refresh_daemon_config()
            return

        msg = f"Saved {key}."
        if result.note:
            msg = f"{msg} {result.note}."
        if result.requires_privilege:
            msg = f"{msg} Note: {result.requires_privilege}."
        self._set_daemon_cfg_result(msg, "SuccessChip")
        # Re-read rather than trusting the local control: this is what makes the
        # restart-pending annotation the daemon's verdict instead of our memory.
        self._refresh_daemon_config()

    def _set_daemon_cfg_result(self, text: str, css: str) -> None:
        self._daemon_cfg_result.setText(text)
        self._daemon_cfg_result.setVisible(bool(text))
        if css:
            set_chip_class(self._daemon_cfg_result, css, skip_if_unchanged=True)

    def _build_prompts_card(self) -> QWidget:
        card = Card()
        v = QVBoxLayout(card)
        v.setSpacing(8)
        v.addWidget(
            SectionHeader("Prompts & Dismissals", object_name="Settings_SectionHeader_prompts")
        )
        note = QLabel("One-time prompts and advisories you have dismissed. Re-arm them here.")
        note.setWordWrap(True)
        note.setProperty("class", "CardMeta")
        v.addWidget(note)

        # The sibling of show_gpu_zero_rpm_warning on the General card. This one
        # was dismiss-only with no way back until DEC-237.
        self._aio_pump_info_cb = ToggleSwitch()
        self._aio_pump_info_cb.setObjectName("Settings_Check_aioPumpInfo")
        self._aio_pump_info_cb.setToolTip(
            "Show the informational popup when an AIO pump header is added to a role"
        )
        v.addLayout(
            self._setting_row(
                "Show AIO pump info",
                "Explains pump headers when one is added to a role",
                self._aio_pump_info_cb,
            )
        )

        self._clear_kernel_warnings_btn = make_button(
            "Clear dismissed", "ghost", object_name="Settings_Btn_clearKernelWarnings"
        )
        self._clear_kernel_warnings_btn.clicked.connect(self._clear_kernel_warnings)
        v.addLayout(
            self._setting_row(
                "Dismissed driver advisories",
                "Kernel/driver warnings you have acknowledged",
                self._clear_kernel_warnings_btn,
            )
        )

        self._reoffer_import_btn = make_button(
            "Offer again", "ghost", object_name="Settings_Btn_reofferImport"
        )
        self._reoffer_import_btn.setToolTip(
            "Offer to migrate local profiles into the daemon store at next startup"
        )
        self._reoffer_import_btn.clicked.connect(self._reoffer_profile_import)
        v.addLayout(
            self._setting_row(
                "Daemon profile import",
                "The once-per-install migration offer",
                self._reoffer_import_btn,
            )
        )

        self._reseed_aliases_btn = make_button(
            "Run again",
            "ghost",
            object_name="Settings_Btn_reseedAliases",
            # DEC-269: two buttons on this page both read "Run again", so Qt's
            # text fallback announces them identically — the same defect as the
            # eight "Toggle" switches, at lower volume. Sighted users
            # disambiguate from the row title; a screen-reader user tabbing the
            # button column cannot.
            accessible_name="Run fan name seeding again",
        )
        self._reseed_aliases_btn.setToolTip(
            "Re-adopt fan names from your profiles on the next connection"
        )
        self._reseed_aliases_btn.clicked.connect(self._reseed_fan_aliases)
        v.addLayout(
            self._setting_row(
                "Fan name seeding",
                "One-time adoption of names from your profiles",
                self._reseed_aliases_btn,
            )
        )

        self._reseed_series_btn = make_button(
            "Run again",
            "ghost",
            object_name="Settings_Btn_reseedSeries",
            accessible_name="Re-pick default chart series",
        )
        self._reseed_series_btn.setToolTip(
            "Re-pick the default chart series on the next connection"
        )
        self._reseed_series_btn.clicked.connect(self._reseed_chart_series)
        v.addLayout(
            self._setting_row(
                "Chart series defaults",
                "One-time pick of which series start visible",
                self._reseed_series_btn,
            )
        )
        return card

    def _clear_kernel_warnings(self) -> None:
        self._settings_svc.update(acknowledged_kernel_warnings=[])
        self._refresh_reset_buttons()
        self._set_status("Dismissed driver advisories cleared")

    def _reoffer_profile_import(self) -> None:
        self._settings_svc.update(daemon_import_prompted=False)
        self._refresh_reset_buttons()
        self._set_status("The profile import will be offered again at next startup")

    def _reseed_fan_aliases(self) -> None:
        self._settings_svc.update(fan_aliases_seeded=False)
        self._refresh_reset_buttons()
        self._set_status("Fan name seeding will run again at next startup")

    def _reseed_chart_series(self) -> None:
        self._settings_svc.update(chart_series_seeded=False)
        self._refresh_reset_buttons()
        self._set_status("Chart series defaults will be re-picked at next startup")

    def _build_card_layout_card(self) -> QWidget:
        card = Card()
        v = QVBoxLayout(card)
        v.setSpacing(8)
        v.addWidget(SectionHeader("Card Layout", object_name="Settings_SectionHeader_cardLayout"))
        note = QLabel(
            "Controls-page cards are resized with their corner grips; "
            "double-click a grip to reset one card."
        )
        note.setWordWrap(True)
        note.setProperty("class", "CardMeta")
        v.addWidget(note)

        self._reset_card_sizes_btn = make_button(
            "Reset all sizes", "ghost", object_name="Settings_Btn_resetCardSizes"
        )
        self._reset_card_sizes_btn.clicked.connect(self._reset_card_sizes)
        v.addLayout(
            self._setting_row(
                "Custom card sizes",
                "Per-card size overrides on the Controls page",
                self._reset_card_sizes_btn,
            )
        )

        self._reset_layout_btn = make_button(
            "Reset layout", "ghost", object_name="Settings_Btn_resetLayout"
        )
        self._reset_layout_btn.clicked.connect(self._reset_layout)
        v.addLayout(
            self._setting_row(
                "Section sizes",
                "Panel sizes you have set by dragging the dividers between sections",
                self._reset_layout_btn,
            )
        )
        return card

    def _reset_layout(self) -> None:
        """DEC-245: the escape hatch that made persisting splitters acceptable.

        The restore clamp stops a pane coming back unusably small; this is the way
        out for a saved layout the user simply dislikes. MainWindow owns the live
        splitters, so the page asks rather than reaching for them.
        """
        self.layout_reset_requested.emit()
        self._refresh_reset_buttons()
        self._set_status("Section sizes reset")

    def _reset_card_sizes(self) -> None:
        self._settings_svc.update(controls_card_sizes={})
        self._refresh_reset_buttons()
        self._set_status("Controls card sizes reset")

    def _refresh_reset_buttons(self) -> None:
        """Label each reset control with its count and disable it when empty.

        A reset button that is always enabled cannot tell you whether there is
        anything to reset — the count is the state readout for these settings.
        """
        s = self._settings_svc.settings
        hidden_series = (
            len(self._series_selection.to_dict()["hidden_keys"])
            if self._series_selection is not None
            else len(s.hidden_chart_series)
        )
        for btn, count, label in (
            (self._unhide_sensors_btn, len(s.diagnostics_hidden_sensor_ids), "Unhide all sensors"),
            (self._reset_classes_btn, len(s.sensor_class_overrides), "Clear overrides"),
            (self._reset_colors_btn, len(s.series_colors), "Reset colours"),
            (self._show_series_btn, hidden_series, "Show all series"),
            (
                self._clear_kernel_warnings_btn,
                len(s.acknowledged_kernel_warnings),
                "Clear dismissed",
            ),
            (self._reset_card_sizes_btn, len(s.controls_card_sizes), "Reset all sizes"),
            (self._prune_orphans_btn, self._chart_orphans().total, "Remove"),
        ):
            btn.setEnabled(count > 0)
            btn.setText(f"{label} ({count})" if count else label)

        # The one-time latches are booleans, not counts: enabled only while the
        # latch is set, because re-arming an unfired prompt is a no-op.
        self._reoffer_import_btn.setEnabled(s.daemon_import_prompted)
        self._reseed_aliases_btn.setEnabled(s.fan_aliases_seeded)
        self._reseed_series_btn.setEnabled(s.chart_series_seeded)

    def _dir_picker_row(
        self,
        label_text: str,
        path_label: QLabel,
        browse_callback,
        *,
        key: str,
        what: str,
    ) -> QHBoxLayout:
        """One path-override row: a caption, the current path, Browse, Reset.

        ``key`` is the camelCase objectName fragment (``"profilesDir"``); ``what``
        is the spoken noun phrase for the directory ("profiles directory").

        Both buttons go through ``make_button`` rather than a hand-rolled
        ``QPushButton`` (`CLAUDE.md § GUI component standard`), and both take a
        unique objectName and an explicit accessible name. This row is built
        three times, so the hand-rolled form left six buttons with **no**
        objectName — unfindable by `findChild`, hence untestable — and with only
        two distinct visible labels between them: three announcing "Browse..."
        and three announcing "Reset", indistinguishable to a screen-reader user
        tabbing the column. Same defect as the two "Run again" buttons DEC-269
        fixed on this page, at three times the volume.
        """
        row = QHBoxLayout()
        row.addWidget(QLabel(label_text))
        path_label.setMinimumWidth(250)
        # Scoped QSS class, not an inline token f-string: the f-string variant
        # froze the colour at construction time, and SettingsPage is not in the
        # MainWindow set_theme fan-out — so a live dark→light switch left the
        # path labels at the previous theme's muted tint. The .MutedLabel rule
        # re-resolves from the freshly applied stylesheet on every theme change.
        path_label.setProperty("class", "MutedLabel")
        row.addWidget(path_label, 1)
        slug = key[:1].upper() + key[1:]
        browse_btn = make_button(
            "Browse...",
            "secondary",
            object_name=f"Settings_Btn_browse{slug}",
            accessible_name=f"Browse for the {what}",
        )
        browse_btn.clicked.connect(browse_callback)
        row.addWidget(browse_btn)
        reset_btn = make_button(
            "Reset",
            # `secondary`, matching Browse, not the `ghost` the page's other
            # reset/clear buttons use. Those are each the only control in their
            # row, where ghost reads as a quiet affordance; this one sits
            # immediately beside a bordered sibling, where it would read as a
            # weakened twin of Browse rather than a peer control. The two were
            # visually identical before this change and stay that way.
            "secondary",
            object_name=f"Settings_Btn_reset{slug}",
            accessible_name=f"Reset the {what} to its default location",
        )
        reset_btn.setToolTip("Reset to default XDG location")
        reset_btn.clicked.connect(lambda: self._reset_dir(path_label))
        row.addWidget(reset_btn)
        return row

    def _build_sync_backup_card(self) -> QWidget:
        """Sync & Backup card (DEC-215) — folds the former Import/Export tab."""
        card = Card()
        v = QVBoxLayout(card)
        v.setSpacing(8)
        v.addWidget(SectionHeader("Sync & Backup"))
        note = QLabel(
            "Export or import all application settings — preferences, theme, fan "
            "aliases, chart configuration, and profile bindings."
        )
        note.setWordWrap(True)
        note.setProperty("class", "CardMeta")
        v.addWidget(note)

        # Sync local profiles into the daemon's own store (DEC-161).
        sync_btn = make_button(
            "Sync Local Profiles to Daemon",
            "primary",
            object_name="Settings_Btn_importProfilesToDaemon",
        )
        sync_btn.setToolTip(
            "Import your local fan profiles into the daemon's own store so it can "
            "manage them directly. Profiles already in the daemon are skipped; "
            "your local copies are left untouched. Requires daemon v1.19 or newer."
        )
        sync_btn.clicked.connect(lambda: self.run_profile_import(auto=False))
        v.addWidget(sync_btn)

        btn_row = QHBoxLayout()
        export_btn = make_button(
            "Export Config", "secondary", object_name="Settings_Btn_exportConfig"
        )
        export_btn.clicked.connect(self._export_settings)
        btn_row.addWidget(export_btn, 1)
        import_btn = make_button(
            "Import Config", "secondary", object_name="Settings_Btn_importConfig"
        )
        import_btn.clicked.connect(self._import_settings)
        btn_row.addWidget(import_btn, 1)
        v.addLayout(btn_row)

        backup_note = QLabel("A backup is created automatically before import.")
        backup_note.setWordWrap(True)
        backup_note.setAlignment(Qt.AlignmentFlag.AlignCenter)
        backup_note.setProperty("class", "CardMeta")
        v.addWidget(backup_note)

        self._export_result_label = QLabel("")
        v.addWidget(self._export_result_label)
        return card

    # ─── Logic ───────────────────────────────────────────────────────

    def _apply_wizard_spindown_limit(self) -> None:
        """Bound the wizard spin-down timer by what the daemon will actually honour.

        `GET /capabilities` publishes `limits.openfan_stop_timeout_s` so that
        "clients size their identify/stop UI timeouts from this advertised
        value" — the daemon rejects a 0% OpenFan command held longer than its own
        `STOP_TIMEOUT`, so a longer timer here offers a spin-down that silently
        ends early. The GUI previously hardcoded 8 s, which matched the daemon
        constant **by coincidence** and would have desynced the moment it moved
        (register row `WIRE-d`); the spinner's maximum of 12 already exceeded it.

        Scoped to daemons that report an OpenFan controller: the limit is an
        OpenFan serial-protocol bound, and an hwmon-only machine is governed by
        the identify deadman instead. With no capability, no OpenFan, or a `0`
        (older daemon / malformed response) the static band stands — the
        pre-existing behaviour, and the safe direction, since narrowing on an
        unknown limit would silently shorten every user's timer.
        """
        caps = getattr(self._state, "capabilities", None) if self._state else None
        if caps is None or not getattr(getattr(caps, "openfan", None), "present", False):
            ceiling = WIZARD_SPINDOWN_MAX_S
        else:
            advertised = getattr(getattr(caps, "limits", None), "openfan_stop_timeout_s", 0) or 0
            ceiling = advertised if advertised > 0 else WIZARD_SPINDOWN_MAX_S
        # Never below the floor, and never above the static band: an implausible
        # advertised value must not widen the control past what the wizard's own
        # copy and layout assume.
        ceiling = max(WIZARD_SPINDOWN_MIN_S, min(ceiling, WIZARD_SPINDOWN_MAX_S))
        self._wizard_spindown_spin.setMaximum(ceiling)
        if ceiling < WIZARD_SPINDOWN_MAX_S:
            self._wizard_spindown_spin.setToolTip(
                "How long each fan is stopped during the wizard identification test. "
                f"Capped at {ceiling} s because this daemon restarts an OpenFan fan "
                "after that, whatever the timer says."
            )

    def _load_current_settings(self) -> None:
        s = self._settings_svc.settings
        idx = self._startup_page_combo.findData(s.default_startup_page)
        if idx >= 0:
            self._startup_page_combo.setCurrentIndex(idx)
        self._restore_page_cb.setChecked(s.restore_last_page)
        self._demo_disconnect_cb.setChecked(s.demo_on_disconnect)
        self._chart_range_combo.setCurrentIndex(
            max(0, min(s.chart_default_range_index, self._chart_range_combo.count() - 1))
        )
        self._gpu_zero_rpm_warn_cb.setChecked(s.show_gpu_zero_rpm_warning)
        self._aio_pump_info_cb.setChecked(s.show_aio_pump_info)
        # Apply the daemon's advertised ceiling BEFORE seeding, so a stored
        # value the daemon will not honour is clamped rather than displayed.
        self._apply_wizard_spindown_limit()
        self._wizard_spindown_spin.setValue(s.wizard_spindown_seconds)
        # No startup-delay seed here: the daemon owns that key and
        # `_refresh_daemon_config` is the only thing allowed to fill the spinner
        # (DEC-285). Seeding it locally is what let the card display a guess.
        self._hide_igpu_cb.setChecked(s.hide_igpu_sensors)
        self._hide_unused_fans_cb.setChecked(s.hide_unused_fan_headers)

        # DEC-215: card_size moved to the Theme page (it owns the combo + its
        # single writer); this page no longer loads or persists it.

        # Directory overrides (show override or default as placeholder)
        self._profiles_dir_label.setText(s.profiles_dir_override or str(profiles_dir()))
        self._themes_dir_label.setText(s.themes_dir_override or str(themes_dir()))
        self._export_dir_label.setText(s.export_default_dir or str(export_default_dir()))
        self._refresh_search_dir_note()
        self._refresh_fan_aliases()
        self._refresh_reset_buttons()

    def _save_app_settings(self) -> None:
        # Determine directory overrides: empty label text means "use default"
        profiles_override = self._profiles_dir_label.text()
        themes_override = self._themes_dir_label.text()
        export_override = self._export_dir_label.text()

        # Clear override if it matches the XDG default
        from control_ofc.paths import config_dir as _config_dir

        xdg_profiles = str(_config_dir() / "profiles")
        xdg_themes = str(_config_dir() / "themes")
        if profiles_override == xdg_profiles:
            profiles_override = ""
        if themes_override == xdg_themes:
            themes_override = ""
        if export_override == str(Path.home()):
            export_override = ""

        self._settings_svc.update(
            default_startup_page=self._startup_page_combo.currentData(),
            restore_last_page=self._restore_page_cb.isChecked(),
            demo_on_disconnect=self._demo_disconnect_cb.isChecked(),
            chart_default_range_index=self._chart_range_combo.currentIndex(),
            show_gpu_zero_rpm_warning=self._gpu_zero_rpm_warn_cb.isChecked(),
            show_aio_pump_info=self._aio_pump_info_cb.isChecked(),
            wizard_spindown_seconds=self._wizard_spindown_spin.value(),
            hide_igpu_sensors=self._hide_igpu_cb.isChecked(),
            hide_unused_fan_headers=self._hide_unused_fans_cb.isChecked(),
            profiles_dir_override=profiles_override,
            themes_dir_override=themes_override,
            export_default_dir=export_override,
        )

        # Apply path overrides immediately
        set_path_overrides(
            profiles_dir=profiles_override,
            themes_dir=themes_override,
            export_dir=export_override,
        )

        # No daemon write here (DEC-285). Save used to POST `startup.delay_secs`
        # unconditionally, bypassing `_write_daemon_key`'s no-op guard — so
        # pressing Save once wrote the key into runtime.toml, flipped its source
        # to "runtime", and permanently shadowed the operator's daemon.toml with
        # a value nobody chose. Every daemon key is now written only by the
        # control that edits it, and only when it actually changed.
        self._set_status("Application settings saved")

    # ─── Preferred sensors (daemon, DEC-200) ───────────────────────

    def _build_preferred_sensors_group(self) -> QWidget:
        group = Card()
        group.setObjectName("Settings_Group_preferredSensors")
        v = QVBoxLayout(group)
        v.setSpacing(8)

        header = SectionHeader("Preferred Sensors")
        refresh_btn = make_button(
            "Refresh from Daemon",
            "secondary",
            object_name="Settings_Btn_refreshPreferredSensors",
        )
        refresh_btn.clicked.connect(self._refresh_preferred_sensors)
        header.add_trailing(refresh_btn)
        v.addWidget(header)

        note = QLabel(
            "Pin which temperature sensor the daemon treats as your CPU and "
            "motherboard reference. Persisted by the daemon and shared across "
            "clients. Advisory only — thermal safety always uses the hottest CPU "
            "sensor. Leave on Automatic to use the daemon's recommendation."
        )
        note.setWordWrap(True)
        note.setProperty("class", "CardMeta")
        v.addWidget(note)

        # These two combos are stacked under their labels rather than placed by
        # `_setting_row`, so they miss that helper's naming pass. They are the
        # last two of the nine nameless Settings controls DEC-269 counted
        # (register OPEN-01 item 01-e) — named here at the only site that holds
        # both the combo and its words.
        #
        # `setBuddy` is the load-bearing call, not `setAccessibleName`: Qt's Unix
        # QAccessibleComboBox reports the *current item* as its Name, discarding
        # the property, so without the buddy relation these announce "Automatic"
        # and nothing else. See `_setting_row` for the full reasoning.
        cpu_label = QLabel("Preferred CPU sensor")
        cpu_label.setProperty("class", "CardMeta")
        v.addWidget(cpu_label)
        self._pref_cpu_combo = QComboBox()
        self._pref_cpu_combo.setObjectName("Settings_Combo_preferredCpu")
        name_value_control(self._pref_cpu_combo, cpu_label)
        self._pref_cpu_combo.currentIndexChanged.connect(self._on_preferred_cpu_changed)
        v.addWidget(self._pref_cpu_combo)

        mb_label = QLabel("Preferred motherboard sensor")
        mb_label.setProperty("class", "CardMeta")
        v.addWidget(mb_label)
        self._pref_mb_combo = QComboBox()
        self._pref_mb_combo.setObjectName("Settings_Combo_preferredMb")
        name_value_control(self._pref_mb_combo, mb_label)
        self._pref_mb_combo.currentIndexChanged.connect(self._on_preferred_mb_changed)
        v.addWidget(self._pref_mb_combo)

        # WIRE-x: the daemon sends a plain-English reason for its recommendation
        # plus a confidence and a `user`/`auto` provenance. Only `sensor_id` was
        # read, so the starred preselection arrived with nothing to justify it
        # and the user had to take it on trust.
        self._pref_cpu_rationale = QLabel("")
        self._pref_cpu_rationale.setObjectName("Settings_Label_preferredCpuRationale")
        # The rationale is a daemon-supplied string; PlainText for the same
        # reason as the daemon-config row notes (DEC-231).
        self._pref_cpu_rationale.setTextFormat(Qt.TextFormat.PlainText)
        self._pref_cpu_rationale.setWordWrap(True)
        self._pref_cpu_rationale.setProperty("class", "CardMeta")
        self._pref_cpu_rationale.setVisible(False)
        v.addWidget(self._pref_cpu_rationale)

        self._pref_result_label = QLabel("")
        self._pref_result_label.setObjectName("Settings_Label_preferredResult")
        self._pref_result_label.setWordWrap(True)
        v.addWidget(self._pref_result_label)

        self._pref_group = group  # for focus_preferred_sensors (DEC-206 deep-link)
        return group

    def focus_preferred_sensors(self, role: str = "cpu") -> None:
        """Reveal the preferred-sensors picker and focus the CPU or motherboard
        combo — the target of the merged readiness view's "Pick a sensor"
        deep-link (DEC-206). Refreshes the combos from the daemon so the picker is
        populated when arrived at directly, then scrolls the group into view and
        focuses the requested combo. (DEC-215: no tab to select — single surface.)
        """
        if self._client is not None:
            self._refresh_preferred_sensors()
        combo = self._pref_mb_combo if role == "mb" else self._pref_cpu_combo
        self._app_scroll.ensureWidgetVisible(self._pref_group)
        combo.setFocus(Qt.FocusReason.OtherFocusReason)

    def showEvent(self, event) -> None:
        super().showEvent(event)
        # Capabilities arrive after construction, so the wizard spin-down
        # ceiling has to be re-derived on arrival — `_load_current_settings`
        # runs before the handshake and would leave the static fallback in
        # place forever (`WIRE-d`).
        self._apply_wizard_spindown_limit()
        # Load preferred-sensor options from the daemon the first time Settings
        # is shown — a light, cache-backed GET kept off the startup path.
        if not self._prefs_loaded and self._client is not None:
            self._refresh_preferred_sensors()
        # Daemon config is likewise fetched on first arrival, not at startup.
        # `_refresh_daemon_config` owns the latch and sets it only on success,
        # so a transient failure retries on the next visit.
        if not self._daemon_cfg_loaded and self._client is not None:
            self._refresh_port_probe_availability()
            self._refresh_daemon_config()
        # The mirrored surfaces reflect state owned elsewhere (fans arrive by
        # poll; sensors are hidden from Overview; colours are picked on the
        # Dashboard), so re-read on arrival rather than trusting construction.
        self._refresh_fan_aliases()
        self._refresh_reset_buttons()
        # DEC-245 made the Dashboard's Range combo a second writer of
        # chart_default_range_index, so this mirror has to re-read on arrival too.
        # Without it the combo holds its construction-time value and Save Changes
        # writes that back, silently reverting a range picked on the Dashboard.
        with block_signals(self._chart_range_combo):
            self._chart_range_combo.setCurrentIndex(
                max(
                    0,
                    min(
                        self._settings_svc.settings.chart_default_range_index,
                        self._chart_range_combo.count() - 1,
                    ),
                )
            )

    def _refresh_preferred_sensors(self) -> None:
        """Fetch the classified sensor inventory and (re)populate the combos."""
        if self._client is None:
            self._set_pref_result(
                "Daemon not connected — preferred sensors unavailable.", "CautionChip"
            )
            return
        from control_ofc.api.errors import DaemonError

        try:
            inv = self._client.inventory_hwmon()
        except DaemonError as e:
            if getattr(e, "status", None) == 404:
                self._set_pref_result(
                    unsupported_feature_message("preferred_sensors"), "CautionChip"
                )
            else:
                self._set_pref_result(f"Could not load sensors: {e.message}", "CriticalChip")
            return
        except (ConnectionError, OSError):
            self._set_pref_result("Daemon unavailable — could not load sensors.", "CautionChip")
            return

        self._prefs_loaded = True
        recommended = inv.default_cpu.sensor_id if inv.default_cpu else None
        cpu_pref = inv.preferences.cpu_sensor_id if inv.preferences else None
        mb_pref = inv.preferences.mb_sensor_id if inv.preferences else None
        sensors = [s for s in inv.temp_sensors if s.control_eligible]

        self._populating_prefs = True
        try:
            self._fill_pref_combo(self._pref_cpu_combo, sensors, recommended, cpu_pref)
            self._fill_pref_combo(self._pref_mb_combo, sensors, None, mb_pref)
        finally:
            self._populating_prefs = False
        self._render_default_cpu_rationale(inv.default_cpu)
        self._set_pref_result("", "")

    def _render_default_cpu_rationale(self, default_cpu) -> None:
        """Explain the starred CPU-sensor recommendation in the daemon's words.

        `default_cpu` carries `rationale`, `confidence` and `source` alongside
        the id (`WIRE-x`). `source` is the load-bearing one: `"user"` means the
        star is merely echoing a choice already persisted, and calling that a
        recommendation would be circular.
        """
        text = ""
        if default_cpu is not None and default_cpu.sensor_id:
            reason = (default_cpu.rationale or "").strip()
            if default_cpu.source == "user":
                lead = "★ Your pinned CPU sensor"
            else:
                lead = "★ Recommended by the daemon"
            confidence = (default_cpu.confidence or "").strip()
            # "unknown" is a real token and says nothing; suppress it rather
            # than printing "confidence: unknown" beside a recommendation.
            if confidence and confidence != "unknown":
                lead = f"{lead} ({confidence} confidence)"
            text = f"{lead}: {reason}" if reason else lead
        self._pref_cpu_rationale.setText(text)
        self._pref_cpu_rationale.setVisible(bool(text))

    def _fill_pref_combo(self, combo, sensors, recommended, current) -> None:
        combo.clear()
        combo.addItem("Automatic (recommended)", None)
        for s in sensors:
            star = "★ " if recommended and s.id == recommended else ""
            cls = f" — {s.classification}" if s.classification else ""
            combo.addItem(f"{star}{s.label or s.id}{cls}", s.id)
        idx = combo.findData(current) if current else 0
        combo.setCurrentIndex(idx if idx >= 0 else 0)

    def _on_preferred_cpu_changed(self, _index: int) -> None:
        if self._populating_prefs:
            return
        self._post_preferred("cpu", self._pref_cpu_combo.currentData())

    def _on_preferred_mb_changed(self, _index: int) -> None:
        if self._populating_prefs:
            return
        self._post_preferred("mb", self._pref_mb_combo.currentData())

    def _post_preferred(self, role: str, sensor_id: str | None) -> None:
        if self._client is None:
            return
        from control_ofc.api.errors import DaemonError

        try:
            if role == "cpu":
                self._client.set_preferred_cpu_sensor(sensor_id)
            else:
                self._client.set_preferred_mb_sensor(sensor_id)
        except DaemonError as e:
            self._set_pref_result(
                f"Could not save preferred {role} sensor: {e.message}", "CriticalChip"
            )
            return
        except (ConnectionError, OSError):
            self._set_pref_result("Daemon unavailable — preferred sensor not saved.", "CautionChip")
            return
        where = "cleared" if sensor_id is None else "saved"
        label = "CPU" if role == "cpu" else "motherboard"
        self._set_pref_result(f"Preferred {label} sensor {where}.", "SuccessChip")

    def _set_pref_result(self, text: str, css: str) -> None:
        from control_ofc.ui.qt_util import set_chip_class

        self._pref_result_label.setText(text)
        if css:
            set_chip_class(self._pref_result_label, css, skip_if_unchanged=True)

    # ─── Directory picker handlers ─────────────────────────────────

    def _browse_profiles_dir(self) -> None:
        current = self._profiles_dir_label.text() or str(profiles_dir())
        path = QFileDialog.getExistingDirectory(self, "Select Profiles Directory", current)
        if path:
            self._handle_dir_change("profiles", self._profiles_dir_label, path, profiles_dir())

    def _browse_themes_dir(self) -> None:
        current = self._themes_dir_label.text() or str(themes_dir())
        path = QFileDialog.getExistingDirectory(self, "Select Themes Directory", current)
        if path:
            self._handle_dir_change("themes", self._themes_dir_label, path, themes_dir())

    def _browse_export_dir(self) -> None:
        current = self._export_dir_label.text() or str(export_default_dir())
        path = QFileDialog.getExistingDirectory(self, "Select Default Export Directory", current)
        if path:
            self._export_dir_label.setText(path)

    def _reset_dir(self, label: QLabel) -> None:
        label.setText("")
        label.setToolTip("Using default XDG location")

    def _handle_dir_change(self, kind: str, label: QLabel, new_path: str, old_dir: Path) -> None:
        """Handle profile/theme directory change: offer to move existing files."""
        new_dir = Path(new_path)
        if new_dir == old_dir:
            label.setText(new_path)
            return

        # Check for existing files to migrate
        existing_files = list(old_dir.glob("*.json")) if old_dir.exists() else []
        if existing_files:
            reply = QMessageBox.question(
                self,
                f"Move existing {kind}?",
                f"Move {len(existing_files)} file(s) from:\n{old_dir}\n\nto:\n{new_dir}?",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if reply == QMessageBox.StandardButton.Yes:
                new_dir.mkdir(parents=True, exist_ok=True)
                moved = 0
                for f in existing_files:
                    try:
                        dest = new_dir / f.name
                        shutil.move(str(f), str(dest))
                        moved += 1
                    except OSError as e:
                        log.warning("Failed to move %s: %s", f, e)
                self._set_status(f"Moved {moved}/{len(existing_files)} files to {new_dir}")

        label.setText(new_path)

        # If profiles dir changed, update daemon via API. Register the new
        # directory AND retire the old one in the same call — otherwise the
        # daemon's search path gains an entry on every change and never loses
        # one (DEC-285).
        if kind == "profiles" and self._client:
            error = self._sync_profile_search_dir(str(old_dir), new_path)
            if error is not None:
                QMessageBox.warning(self, "Daemon Config", f"Failed to update daemon: {error}")
            elif self._stale_search_dir:
                # Registered, but the old entry is still there. Say so — a silent
                # leak is the thing this whole change exists to stop, and the
                # user can prune it from Daemon Configuration.
                self._set_status(
                    f"Profile search dirs updated on daemon — the previous directory "
                    f"({self._stale_search_dir}) is still registered and can be removed "
                    f"under Daemon Configuration"
                )
            else:
                self._set_status("Profile search dirs updated on daemon")
        elif kind == "profiles":
            self._set_status("Daemon not connected — update profile search dirs manually")

        if kind == "profiles":
            self._refresh_search_dir_note()
            # The daemon card's list is now stale — re-read it if it is loaded.
            if self._daemon_cfg_loaded:
                self._refresh_daemon_config()

    # ── Daemon profile import (DEC-161) ──────────────────────────────────

    def run_profile_import(self, *, auto: bool) -> None:
        """Migrate the user's local profiles into the daemon's profile store.

        ``auto=True`` is the once-per-install startup offer (gated by
        ``should_offer_import`` in the main window); ``auto=False`` is the
        always-available manual button. Idempotent: a re-run 409-skips ids
        already in the store. Originals on disk are never modified.
        """
        caps = self._state.capabilities if self._state else None
        control = getattr(caps, "control", None)
        storage_ok = bool(control and getattr(control, "profile_storage", False))
        if self._client is None or not storage_ok:
            if not auto:
                QMessageBox.information(
                    self,
                    "Import profiles",
                    "Connect to a daemon that supports profile storage (v1.19 "
                    "or newer) before importing your local profiles.",
                )
            return

        collection = collect_local_profiles_for_import()
        if collection.is_empty:
            if auto:
                self._mark_import_prompted()
            else:
                QMessageBox.information(
                    self, "Import profiles", "No local profiles were found to import."
                )
            return

        if auto:
            # Offered now — never auto-ask again on this install, even on "No".
            self._mark_import_prompted()
            proceed = QMessageBox.question(
                self,
                "Import your profiles?",
                f"Found {len(collection.ready)} local profile(s). Import them "
                "into the daemon so it can manage them?\n\nProfiles already in "
                "the daemon are skipped, and your local copies are left "
                "untouched.",
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            )
            if proceed != QMessageBox.StandardButton.Yes:
                return

        try:
            report = import_profiles(self._client, collection, on_conflict="skip")
        except DaemonError as e:
            QMessageBox.warning(
                self,
                "Import failed",
                f"The daemon became unavailable during import:\n{e}",
            )
            return

        if not auto:
            self._mark_import_prompted()
        self._present_import_report(report, collection)

    def _mark_import_prompted(self) -> None:
        if not self._settings_svc.settings.daemon_import_prompted:
            self._settings_svc.update(daemon_import_prompted=True)

    def _present_import_report(self, report, collection: ImportCollection) -> None:
        """Show the import result and, when ids collided, offer to re-import the
        skipped profiles as renamed copies (DEC-161)."""
        allow_rename = True
        while True:
            box = QMessageBox(self)
            box.setObjectName("Settings_Dialog_importReport")
            box.setWindowTitle("Profile import")
            box.setIcon(
                QMessageBox.Icon.Warning if report.quarantined else QMessageBox.Icon.Information
            )
            box.setText(self._format_import_report(report))
            box.addButton(QMessageBox.StandardButton.Ok)
            rename_btn = None
            if allow_rename and report.skipped:
                rename_btn = box.addButton(
                    f"Import {len(report.skipped)} skipped as copies",
                    QMessageBox.ButtonRole.ActionRole,
                )
            box.exec()

            if rename_btn is None or box.clickedButton() is not rename_btn:
                break

            skipped_ids = {o.profile_id for o in report.skipped}
            sub = ImportCollection(
                ready=[c for c in collection.ready if c.profile_id in skipped_ids]
            )
            try:
                renamed = import_profiles(self._client, sub, on_conflict="rename")
            except DaemonError as e:
                QMessageBox.warning(self, "Import failed", f"The daemon became unavailable:\n{e}")
                break
            report.imported.extend(renamed.imported)
            report.quarantined.extend(renamed.quarantined)
            report.skipped = []  # every skipped candidate was just reprocessed
            allow_rename = False

        self._export_result_label.setText(
            f"Imported {len(report.imported)}, skipped {len(report.skipped)}, "
            f"quarantined {len(report.quarantined)}."
        )

    @staticmethod
    def _format_import_report(report) -> str:
        lines = [
            f"Imported: {len(report.imported)}",
            f"Skipped (already in daemon): {len(report.skipped)}",
            f"Quarantined (not imported): {len(report.quarantined)}",
        ]
        if report.quarantined:
            lines.append("")
            lines.append("Quarantined:")
            for o in report.quarantined[:10]:
                label = o.name or o.source_path
                lines.append(f"  • {label}: {o.reason}")
            if len(report.quarantined) > 10:
                lines.append(f"  • … and {len(report.quarantined) - 10} more")
        return "\n".join(lines)

    def _export_settings(self) -> None:
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Settings", "control_ofc_settings.json", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            # Build comprehensive export including profiles
            export_data = self._build_full_export()
            # Atomic write (2026-07-21 sweep): the export previously went
            # through AppSettingsService.export_settings, whose atomicity the
            # audit-v3 regression tests pinned; that dead method is gone, so
            # the crash-safety property moves to the live path.
            atomic_write(Path(path), json.dumps(export_data, indent=2) + "\n")
            self._set_export_result("Settings exported successfully", "SuccessChip")
        except (OSError, ValueError, TypeError) as e:
            self._set_export_result(f"Export failed: {e}", "CriticalChip")

    def _import_settings(self) -> None:
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Settings", "", "JSON files (*.json);;All files (*)"
        )
        if not path:
            return
        try:
            raw = load_json_capped(Path(path))
            if not isinstance(raw, dict):
                self._set_export_result("Import failed: invalid file format", "CriticalChip")
                return

            export_ver = raw.get("export_version")
            if export_ver is not None:
                if isinstance(export_ver, bool) or not isinstance(export_ver, (int, float)):
                    self._set_export_result(
                        "Import failed: unrecognized export version", "CriticalChip"
                    )
                    return
                if export_ver > 1:
                    self._set_export_result(
                        f"Import failed: unsupported export version {export_ver} "
                        "(max supported: 1)",
                        "CriticalChip",
                    )
                    return

            # Auto-backup current settings before applying anything.
            backup_path = self._create_backup()

            # Merge imported settings onto the current ones so machine-specific
            # state (window geometry, data-dir overrides, …) is preserved and
            # only portable preferences are overwritten (DEC-140). Stripping the
            # machine keys from the *incoming* data means even a legacy full
            # export can never move the window or wipe local overrides.
            incoming = raw.get("settings")
            if isinstance(incoming, dict):
                from control_ofc.services.app_settings_service import MACHINE_SPECIFIC_KEYS

                merged = self._settings_svc.settings.to_dict()
                merged.update({k: v for k, v in incoming.items() if k not in MACHINE_SPECIFIC_KEYS})
                imported = self._settings_svc.import_settings_from_dict(merged)
                self._settings_svc.apply_imported(imported)
                self._load_current_settings()
                # Apply live side effects, mirroring a manual Save (F11): the
                # data-dir overrides. An import no longer pushes anything to the
                # daemon (DEC-285) — `daemon_startup_delay_secs` is gone from
                # AppSettings, so a shared config can no longer carry one
                # machine's daemon setting onto another's, which was the DEC-140
                # concern reaching a daemon-owned key.
                #
                # `profiles_dir_override` is a MACHINE_SPECIFIC_KEY and is
                # stripped from the incoming data above, so the profiles
                # directory cannot move on import and no search-dir sync is owed
                # here.
                set_path_overrides(
                    profiles_dir=imported.profiles_dir_override,
                    themes_dir=imported.themes_dir_override,
                    export_dir=imported.export_default_dir,
                )

            # Apply profiles if present.
            skipped = 0
            profiles = raw.get("profiles")
            if isinstance(profiles, dict):
                skipped += self._import_profiles(profiles)

            # Apply custom themes if present.
            themes = raw.get("themes")
            if isinstance(themes, dict):
                skipped += self._import_themes(themes)

            backup_msg = f" (backup: {backup_path.name})" if backup_path else ""
            skip_msg = f" ({skipped} invalid item(s) skipped)" if skipped else ""
            css = "WarningChip" if skipped else "SuccessChip"
            self._set_export_result(
                f"Settings imported{backup_msg}{skip_msg} — "
                "some changes take effect on next launch",
                css,
            )
        except (
            json.JSONDecodeError,
            OSError,
            KeyError,
            ValueError,
            TypeError,
            AttributeError,
        ) as e:
            self._set_export_result(f"Import failed: {e}", "CriticalChip")

    def _build_full_export(self) -> dict:
        """Build a comprehensive export covering all configurable state."""
        export: dict = {
            "export_version": 1,
            "exported_at": datetime.now().isoformat(),
            # Portable subset only — machine/session state stays local (DEC-140).
            "settings": self._settings_svc.settings.portable_dict(),
        }
        # Include profiles
        from control_ofc.paths import profiles_dir

        pdir = profiles_dir()
        if pdir.exists():
            profiles = {}
            for p in pdir.glob("*.json"):
                try:
                    profiles[p.stem] = load_json_capped(p)
                except (OSError, ValueError) as e:
                    log.warning("Skipping unreadable profile %s: %s", p, e)
            if profiles:
                export["profiles"] = profiles

        # Include all custom themes (not just active)
        td = themes_dir()
        if td.exists():
            themes = {}
            for tf in td.glob("*.json"):
                try:
                    themes[tf.stem] = load_json_capped(tf)
                except (OSError, ValueError) as e:
                    log.warning("Skipping unreadable theme %s: %s", tf, e)
            if themes:
                export["themes"] = themes

        return export

    def _create_backup(self) -> Path | None:
        """Create a timestamped backup of current settings before import."""
        src = app_settings_path()
        if not src.exists():
            return None
        backup_dir = config_dir() / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup = backup_dir / f"settings_backup_{stamp}.json"
        shutil.copy2(src, backup)
        log.info("Settings backup created: %s", backup)
        return backup

    def _import_profiles(self, profiles: dict) -> int:
        """Import profile JSON files from export data.

        Returns the number of profiles that failed validation.
        """
        from control_ofc.paths import profiles_dir
        from control_ofc.services.profile_service import Profile

        pdir = profiles_dir()
        pdir.mkdir(parents=True, exist_ok=True)

        # Validate all profiles before writing any to disk.
        valid: dict[str, dict] = {}
        skipped: list[str] = []
        for name, data in profiles.items():
            if not isinstance(data, dict):
                log.warning("Skipping profile '%s': not a JSON object", name)
                skipped.append(name)
                continue
            if _safe_import_name(name, pdir) is None:
                log.warning("Skipping profile '%s': unsafe name (path traversal)", name)
                skipped.append(name)
                continue
            try:
                Profile.from_dict(data)
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Skipping profile '%s': validation failed: %s", name, exc)
                skipped.append(name)
                continue
            valid[name] = data

        if not valid:
            return len(skipped)

        existing = [name for name in valid if (pdir / f"{name}.json").exists()]
        if existing:
            reply = QMessageBox.question(
                self,
                "Overwrite profiles?",
                f"{len(existing)} profile(s) already exist and will be overwritten:\n"
                + ", ".join(existing[:5])
                + ("\n..." if len(existing) > 5 else ""),
                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.Cancel,
            )
            if reply != QMessageBox.StandardButton.Yes:
                return len(skipped)

        for name, data in valid.items():
            dest = pdir / f"{name}.json"
            atomic_write(dest, json.dumps(data, indent=2) + "\n")
        log.info("Imported %d profile(s)", len(valid))
        return len(skipped)

    def _import_themes(self, themes_data: dict) -> int:
        """Import custom theme JSON files from export data.

        Returns the number of themes that failed validation.
        """
        from control_ofc.ui.theme import _apply_token_dict, _migrate_tokens

        td = themes_dir()
        td.mkdir(parents=True, exist_ok=True)

        valid: dict[str, dict] = {}
        skipped: list[str] = []
        for name, data in themes_data.items():
            if not isinstance(data, dict):
                log.warning("Skipping theme '%s': not a JSON object", name)
                skipped.append(name)
                continue
            if _safe_import_name(name, td) is None:
                log.warning("Skipping theme '%s': unsafe name (path traversal)", name)
                skipped.append(name)
                continue
            try:
                migrated = _migrate_tokens(data)
                # Strict validation: any invalid colour/value raises and the
                # whole theme is skipped (DEC-142).
                _apply_token_dict(ThemeTokens(), migrated, strict=True)
            except (KeyError, TypeError, ValueError) as exc:
                log.warning("Skipping theme '%s': validation failed: %s", name, exc)
                skipped.append(name)
                continue
            valid[name] = data

        for name, data in valid.items():
            dest = td / f"{name}.json"
            atomic_write(dest, json.dumps(data, indent=2) + "\n")
        log.info("Imported %d theme(s)", len(valid))
        return len(skipped)

    def _set_export_result(self, text: str, css_class: str) -> None:
        self._export_result_label.setText(text)
        set_chip_class(self._export_result_label, css_class, skip_if_unchanged=True)

    def _set_status(self, msg: str) -> None:
        self._status_label.setText(msg)
