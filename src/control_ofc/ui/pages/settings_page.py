"""Settings page — app preferences, themes, and import/export."""

from __future__ import annotations

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING

from PySide6.QtCore import Qt

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
    QMessageBox,
    QPushButton,
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
from control_ofc.services.profile_import_service import import_profiles
from control_ofc.services.profile_service import ImportCollection, collect_local_profiles_for_import
from control_ofc.ui.components.buttons import make_button
from control_ofc.ui.components.cards import Card, SectionHeader
from control_ofc.ui.components.tables import apply_dense_table
from control_ofc.ui.components.toggle_switch import ToggleSwitch
from control_ofc.ui.qt_util import set_chip_class
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
    "daemon_startup_delay_secs": "Settings_Spin_startupDelay",
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
        self._port_probe_reason = ""
        # Phase 4 (DEC-200): preferred-sensor selector state. ``_populating_prefs``
        # suppresses the change→POST while the combos are filled programmatically;
        # ``_prefs_loaded`` gates the one-time lazy fetch on first show.
        self._populating_prefs = False
        self._prefs_loaded = False

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
        col1.addWidget(self._build_general_startup_card())
        col1.addWidget(self._build_operational_card())
        col1.addWidget(self._build_fan_aliases_card())
        col1.addWidget(self._build_card_layout_card())
        col1.addStretch()
        col2 = QVBoxLayout()
        col2.setSpacing(16)
        col2.addWidget(self._build_path_management_card())
        col2.addWidget(self._build_preferred_sensors_group())
        col2.addWidget(self._build_sensors_series_card())
        col2.addWidget(self._build_daemon_config_card())
        col2.addWidget(self._build_prompts_card())
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
        """A mockup settings row: a title + sublabel on the left, a control right."""
        row = QHBoxLayout()
        text = QVBoxLayout()
        text.setContentsMargins(0, 0, 0, 0)
        text.setSpacing(0)
        text.addWidget(QLabel(title))
        sub = QLabel(subtitle)
        sub.setProperty("class", "CardMeta")
        text.addWidget(sub)
        row.addLayout(text, 1)
        row.addWidget(control)
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
                "Chart default time range",
                "Initial window on the dashboard graph",
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
        self._wizard_spindown_spin.setRange(5, 12)
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

        self._startup_delay_spin = QSpinBox()
        self._startup_delay_spin.setObjectName("Settings_Spin_startupDelay")
        self._startup_delay_spin.setRange(0, 30)
        self._startup_delay_spin.setSuffix(" seconds")
        self._startup_delay_spin.setToolTip(
            "Delay before daemon begins device detection after boot (takes effect on restart)"
        )
        v.addLayout(
            self._setting_row(
                "Daemon startup delay",
                "Delay before device detection after boot (restart to apply)",
                self._startup_delay_spin,
            )
        )

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
            self._dir_picker_row("Profiles:", self._profiles_dir_label, self._browse_profiles_dir)
        )
        self._themes_dir_label = QLabel()
        self._themes_dir_label.setObjectName("Settings_Label_themesDir")
        v.addLayout(
            self._dir_picker_row("Themes:", self._themes_dir_label, self._browse_themes_dir)
        )
        self._export_dir_label = QLabel()
        self._export_dir_label.setObjectName("Settings_Label_exportDir")
        v.addLayout(
            self._dir_picker_row("Default export:", self._export_dir_label, self._browse_export_dir)
        )

        # Disclosure: ``services/polling.py`` registers the profiles directory
        # with the daemon on first poll and again on every reconnect, so the
        # daemon can discover GUI-authored profiles. The call is additive and
        # deduplicated daemon-side, but it is a write to daemon config that the
        # operator otherwise never sees anywhere in the UI.
        self._search_dir_note = QLabel()
        self._search_dir_note.setObjectName("Settings_Label_searchDirNote")
        self._search_dir_note.setWordWrap(True)
        self._search_dir_note.setProperty("class", "CardMeta")
        v.addWidget(self._search_dir_note)
        return card

    def _refresh_search_dir_note(self) -> None:
        """Update the profile-search-dir disclosure to match the current path."""
        path = self._profiles_dir_label.text() or str(profiles_dir())
        self._search_dir_note.setText(
            f"Automatically registered with the daemon as a profile search "
            f"directory: {path}. The GUI re-registers this on connect and on "
            f"every reconnect."
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

    #: Rows rendered by the card: (config key, label, sublabel).
    _DAEMON_ROWS = (
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
        self._daemon_restart_banner.setWordWrap(True)
        self._daemon_restart_banner.setVisible(False)
        self._daemon_restart_banner.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        v.addWidget(self._daemon_restart_banner)

        self._poll_interval_spin = QSpinBox()
        self._poll_interval_spin.setObjectName("Settings_Spin_pollInterval")
        self._poll_interval_spin.setRange(250, 10000)
        self._poll_interval_spin.setSingleStep(250)
        self._poll_interval_spin.setSuffix(" ms")
        self._poll_interval_spin.editingFinished.connect(
            lambda: self._write_daemon_key(
                "polling.poll_interval_ms",
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
        self._serial_timeout_spin.setRange(50, 5000)
        self._serial_timeout_spin.setSingleStep(50)
        self._serial_timeout_spin.setSuffix(" ms")
        self._serial_timeout_spin.editingFinished.connect(
            lambda: self._write_daemon_key(
                "serial.timeout_ms",
                lambda c: c.set_serial_timeout(self._serial_timeout_spin.value()),
            )
        )

        self._port_probe_toggle = ToggleSwitch()
        self._port_probe_toggle.setObjectName("Settings_Check_allowPortProbe")
        self._port_probe_toggle.toggled.connect(
            lambda on: self._write_daemon_key(
                "detection.allow_port_probe", lambda c: c.set_allow_port_probe(on)
            )
        )

        self._nvidia_toggle = ToggleSwitch()
        self._nvidia_toggle.setObjectName("Settings_Check_nvidiaTelemetry")
        self._nvidia_toggle.toggled.connect(
            lambda on: self._write_daemon_key(
                "detection.enable_nvidia_telemetry", lambda c: c.set_nvidia_telemetry(on)
            )
        )

        controls = {
            "polling.poll_interval_ms": self._poll_interval_spin,
            "serial.port": self._serial_port_edit,
            "serial.timeout_ms": self._serial_timeout_spin,
            "detection.allow_port_probe": self._port_probe_toggle,
            "detection.enable_nvidia_telemetry": self._nvidia_toggle,
        }
        # Per-row source/restart annotation, keyed by config key.
        self._daemon_row_notes: dict[str, QLabel] = {}
        for key, title, subtitle in self._DAEMON_ROWS:
            v.addLayout(self._setting_row(title, subtitle, controls[key]))
            note = QLabel("")
            note.setObjectName(f"Settings_Label_daemonNote_{key.replace('.', '_')}")
            note.setWordWrap(True)
            note.setProperty("class", "CardMeta")
            note.setVisible(False)
            v.addWidget(note)
            self._daemon_row_notes[key] = note

        # Read-only by design (DEC-243): a bad socket path locks the GUI out of
        # the daemon permanently, and moving the state dir orphans runtime.toml
        # and the daemon-owned profile store. Shown so they stay diagnosable.
        self._daemon_paths_label = QLabel("")
        self._daemon_paths_label.setObjectName("Settings_Label_daemonPaths")
        self._daemon_paths_label.setWordWrap(True)
        self._daemon_paths_label.setProperty("class", "CardMeta")
        self._daemon_paths_label.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextSelectableByMouse
        )
        v.addWidget(self._daemon_paths_label)

        self._daemon_cfg_result = QLabel("")
        self._daemon_cfg_result.setObjectName("Settings_Label_daemonCfgResult")
        self._daemon_cfg_result.setWordWrap(True)
        self._daemon_cfg_result.setVisible(False)
        v.addWidget(self._daemon_cfg_result)
        return card

    def _daemon_config_controls(self) -> list[QWidget]:
        return [
            self._poll_interval_spin,
            self._serial_port_edit,
            self._serial_timeout_spin,
            self._port_probe_toggle,
            self._nvidia_toggle,
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
                    "(requires control-ofc-daemon 2.16.0 or newer)."
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
        self._set_daemon_config_available(True)
        self._render_daemon_config(cfg)

    def _set_daemon_config_available(self, available: bool) -> None:
        for widget in self._daemon_config_controls():
            widget.setEnabled(available)

    def _render_daemon_config(self, cfg) -> None:
        self._populating_daemon_cfg = True
        try:
            poll = cfg.get("polling.poll_interval_ms")
            if poll is not None and isinstance(poll.value, int):
                self._poll_interval_spin.setValue(poll.value)
            port = cfg.get("serial.port")
            if port is not None:
                self._serial_port_edit.setText("" if port.value is None else str(port.value))
            timeout = cfg.get("serial.timeout_ms")
            if timeout is not None and isinstance(timeout.value, int):
                self._serial_timeout_spin.setValue(timeout.value)
            probe = cfg.get("detection.allow_port_probe")
            if probe is not None:
                self._port_probe_toggle.setChecked(bool(probe.value))
            nvidia = cfg.get("detection.enable_nvidia_telemetry")
            if nvidia is not None:
                self._nvidia_toggle.setChecked(bool(nvidia.value))

            # The startup-delay spinner on the Operational card was a local
            # guess; seed it from the daemon so the two cannot disagree.
            delay = cfg.get("startup.delay_secs")
            if delay is not None and isinstance(delay.value, int):
                self._startup_delay_spin.setValue(delay.value)
        finally:
            self._populating_daemon_cfg = False

        for key, _title, _sub in self._DAEMON_ROWS:
            self._render_daemon_row_note(cfg.get(key), self._daemon_row_notes[key])

        if cfg.restart_pending:
            pending = [k.key for k in cfg.keys if k.restart_pending]
            self._daemon_restart_banner.setText(
                f"{len(pending)} change(s) saved but not yet in effect. "
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
    def _daemon_value(cfg, key: str) -> str:
        entry = cfg.get(key)
        return "—" if entry is None else str(entry.value)

    def _render_daemon_row_note(self, entry, label: QLabel) -> None:
        """Annotate a row with where its value came from and what it still needs."""
        if entry is None:
            label.setVisible(False)
            return
        parts: list[str] = []
        if entry.source == "runtime":
            parts.append("set here (overrides daemon.toml)")
        elif entry.source == "admin":
            parts.append("set in daemon.toml")
        if entry.restart_pending:
            parts.append(f"restart required — daemon is running {entry.effective_running_value}")
        if entry.requires_privilege:
            # Never let the toggle alone read as "the feature is on".
            parts.append(entry.requires_privilege)
        if entry.key == "detection.allow_port_probe" and self._port_probe_reason:
            parts.append(self._port_probe_reason)
        label.setText(" · ".join(parts))
        label.setVisible(bool(parts))

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
        self._write_daemon_key("serial.port", lambda c: c.set_serial_port(text or None))

    def _write_daemon_key(self, key: str, call) -> None:
        """POST one daemon config key, then re-read so the card shows daemon truth."""
        if self._populating_daemon_cfg or self._client is None:
            return
        if self._daemon_config_unsupported:
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
            "Run again", "ghost", object_name="Settings_Btn_reseedAliases"
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
            "Run again", "ghost", object_name="Settings_Btn_reseedSeries"
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
        return card

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
        ):
            btn.setEnabled(count > 0)
            btn.setText(f"{label} ({count})" if count else label)

        # The one-time latches are booleans, not counts: enabled only while the
        # latch is set, because re-arming an unfired prompt is a no-op.
        self._reoffer_import_btn.setEnabled(s.daemon_import_prompted)
        self._reseed_aliases_btn.setEnabled(s.fan_aliases_seeded)
        self._reseed_series_btn.setEnabled(s.chart_series_seeded)

    def _dir_picker_row(self, label_text: str, path_label: QLabel, browse_callback) -> QHBoxLayout:
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
        browse_btn = QPushButton("Browse...")
        browse_btn.clicked.connect(browse_callback)
        row.addWidget(browse_btn)
        reset_btn = QPushButton("Reset")
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
        self._wizard_spindown_spin.setValue(s.wizard_spindown_seconds)
        self._startup_delay_spin.setValue(s.daemon_startup_delay_secs)
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
            daemon_startup_delay_secs=self._startup_delay_spin.value(),
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

        # Push startup delay to daemon if connected
        if self._client:
            from control_ofc.api.errors import DaemonError

            try:
                self._client.set_startup_delay(self._startup_delay_spin.value())
            except DaemonError as e:
                log.warning("Failed to sync startup delay to daemon: %s", e.message)
                self._set_status("Application settings saved (startup delay not synced to daemon)")
                return

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

        cpu_label = QLabel("Preferred CPU sensor")
        cpu_label.setProperty("class", "CardMeta")
        v.addWidget(cpu_label)
        self._pref_cpu_combo = QComboBox()
        self._pref_cpu_combo.setObjectName("Settings_Combo_preferredCpu")
        self._pref_cpu_combo.currentIndexChanged.connect(self._on_preferred_cpu_changed)
        v.addWidget(self._pref_cpu_combo)

        mb_label = QLabel("Preferred motherboard sensor")
        mb_label.setProperty("class", "CardMeta")
        v.addWidget(mb_label)
        self._pref_mb_combo = QComboBox()
        self._pref_mb_combo.setObjectName("Settings_Combo_preferredMb")
        self._pref_mb_combo.currentIndexChanged.connect(self._on_preferred_mb_changed)
        v.addWidget(self._pref_mb_combo)

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
        # Load preferred-sensor options from the daemon the first time Settings
        # is shown — a light, cache-backed GET kept off the startup path.
        if not self._prefs_loaded and self._client is not None:
            self._refresh_preferred_sensors()
        # Daemon config is likewise fetched on first arrival, not at startup.
        if not self._daemon_cfg_loaded and self._client is not None:
            self._daemon_cfg_loaded = True
            self._refresh_port_probe_availability()
            self._refresh_daemon_config()
        # The mirrored surfaces reflect state owned elsewhere (fans arrive by
        # poll; sensors are hidden from Overview; colours are picked on the
        # Dashboard), so re-read on arrival rather than trusting construction.
        self._refresh_fan_aliases()
        self._refresh_reset_buttons()

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
                    "This daemon version does not support preferred sensors.", "CautionChip"
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
        self._set_pref_result("", "")

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

        # If profiles dir changed, update daemon via API
        if kind == "profiles" and self._client:
            from control_ofc.api.errors import DaemonError

            try:
                self._client.update_profile_search_dirs(add=[new_path])
                self._set_status("Profile search dirs updated on daemon")
            except DaemonError as exc:
                QMessageBox.warning(
                    self, "Daemon Config", f"Failed to update daemon: {exc.message}"
                )
        elif kind == "profiles":
            self._set_status("Daemon not connected — update profile search dirs manually")

        if kind == "profiles":
            self._refresh_search_dir_note()

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
                # Apply live side effects, mirroring a manual Save (F11):
                # data-dir overrides and the daemon startup delay.
                set_path_overrides(
                    profiles_dir=imported.profiles_dir_override,
                    themes_dir=imported.themes_dir_override,
                    export_dir=imported.export_default_dir,
                )
                if self._client:
                    from control_ofc.api.errors import DaemonError

                    try:
                        self._client.set_startup_delay(imported.daemon_startup_delay_secs)
                    except DaemonError as e:
                        log.warning("Failed to sync startup delay on import: %s", e.message)

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
