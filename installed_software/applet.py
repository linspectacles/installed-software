#!/usr/bin/env python3
# -*- coding: utf-8 -*-
# LinSpectacles - Linux Inspection Applets
# Copyright (C) 2026 brunonlinespace
# GPL-3.0-or-later

APPLET_ID = 'linspectacles-installed-software'
APPLET_NAME = 'Installed Software'
APPLET_AUTHOR = "brunonlinespace"
APPLET_EDITOR = "brunonlinespace"
APPLET_VERSION = '0.0.9'
APPLET_ORGANIZATION_ID = 'linspectacles'
APPLET_ORGANIZATION_URL = 'https://github.com/linspectacles/'
APPLET_REPOSITORY = 'https://github.com/linspectacles/installed-software'

import json
import os
import re
import shutil
import subprocess
import time
from pathlib import Path
from PyQt6.QtCore import QObject, QRunnable, QSettings, QThreadPool, QTimer, Qt, QUrl, pyqtSignal
from PyQt6.QtGui import QColor, QKeySequence, QPixmap, QShortcut, QTextCharFormat, QTextCursor
from PyQt6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QLabel, QListWidget, QMessageBox, QPushButton, QTableWidgetItem,
    QTabWidget, QTextEdit, QVBoxLayout, QWidget,
)
from ._support import BaseInspectorTab, export_records_dialog, metadata_font_qss
from ._package_metadata import lookup_package_metadata

class InstalledSoftwareConfig:
    DEFAULT_APPIMAGE_FOLDERS = ["~/Applications", "~/.local/bin", "~/Downloads", "/opt"]
    def __init__(self):
        self.path = Path(__file__).resolve().parent.parent / "applet-config.json"
        self.data = {"appimage_search_folders": list(self.DEFAULT_APPIMAGE_FOLDERS)}
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
            folders = raw.get("appimage_search_folders") if isinstance(raw, dict) else None
            if isinstance(folders, list) and all(isinstance(item, str) for item in folders):
                self.data["appimage_search_folders"] = folders
        except (FileNotFoundError, json.JSONDecodeError, OSError):
            pass
    @property
    def appimage_search_folders(self):
        return list(self.data.get("appimage_search_folders", self.DEFAULT_APPIMAGE_FOLDERS))
    @appimage_search_folders.setter
    def appimage_search_folders(self, folders):
        self.data["appimage_search_folders"] = list(folders)
        try:
            tmp = self.path.with_suffix(".tmp")
            tmp.write_text(json.dumps(self.data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
            os.replace(tmp, self.path)
        except OSError:
            pass

class AppImageFoldersDialog(QDialog):
    """Edit the folders used for read-only AppImage discovery."""

    def __init__(self, folders, parent=None):
        super().__init__(parent)
        self.setWindowTitle("AppImage Search Folders — Installed Software")
        self.resize(620, 360)

        root = QVBoxLayout(self)
        root.setContentsMargins(14, 14, 14, 14)
        root.setSpacing(10)

        note = QLabel(
            "LinSpectacles discovers AppImage files recursively in these folders. "
            "Missing or unreadable folders are skipped."
        )
        note.setWordWrap(True)
        root.addWidget(note)

        self.list_widget = QListWidget()
        root.addWidget(self.list_widget, 1)
        self.set_folders(folders)

        controls = QHBoxLayout()
        add_btn = QPushButton("Add Folder")
        remove_btn = QPushButton("Remove")
        defaults_btn = QPushButton("Restore Defaults")
        add_btn.clicked.connect(self.add_folder)
        remove_btn.clicked.connect(self.remove_selected)
        defaults_btn.clicked.connect(
            lambda: self.set_folders(InstalledSoftwareConfig.DEFAULT_APPIMAGE_FOLDERS)
        )
        controls.addWidget(add_btn)
        controls.addWidget(remove_btn)
        controls.addWidget(defaults_btn)
        controls.addStretch(1)
        root.addLayout(controls)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel
        )
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    @staticmethod
    def _compact_home(path):
        path = os.path.abspath(os.path.expanduser(path))
        home = os.path.abspath(str(Path.home()))
        if path == home:
            return "~"
        if path.startswith(home + os.sep):
            return "~" + path[len(home):]
        return path

    @staticmethod
    def _canonical(path):
        return os.path.normcase(os.path.abspath(os.path.expanduser(path)))

    def set_folders(self, folders):
        self.list_widget.clear()
        seen = set()
        for folder in folders:
            canonical = self._canonical(folder)
            if canonical in seen:
                continue
            seen.add(canonical)
            self.list_widget.addItem(self._compact_home(folder))

    def add_folder(self):
        directory = QFileDialog.getExistingDirectory(self, "Add AppImage Search Folder")
        if not directory:
            return
        candidate = self._compact_home(directory)
        existing = {
            self._canonical(self.list_widget.item(i).text())
            for i in range(self.list_widget.count())
        }
        if self._canonical(candidate) not in existing:
            self.list_widget.addItem(candidate)

    def remove_selected(self):
        for item in self.list_widget.selectedItems():
            self.list_widget.takeItem(self.list_widget.row(item))

    def folders(self):
        return [self.list_widget.item(i).text() for i in range(self.list_widget.count())]

def _populate_sources_canvas(widget, text):
    """Display the existing source notes with the family Sources presentation."""
    section_headings = {
        "Distribution packages",
        "Flatpak applications",
        "Snap packages",
        "AppImages",
        "System package backend selection",
        "Selected-record metadata",
        "Important distinctions",
    }
    heading_format = QTextCharFormat()
    heading_format.setForeground(QColor("#9cdcfe"))
    heading_format.setFontWeight(600)
    body_format = QTextCharFormat()
    body_format.setForeground(QColor("#dcdcdc"))

    widget.clear()
    cursor = widget.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    for line in text.splitlines(keepends=True):
        cursor.insertText(
            line,
            heading_format if line.rstrip("\r\n") in section_headings else body_format,
        )
    cursor.movePosition(QTextCursor.MoveOperation.Start)
    widget.setTextCursor(cursor)
    widget.verticalScrollBar().setValue(0)


class InstalledSoftwareOptionsDialog(QDialog):
    """Established LinSpectacles Options treatment for Installed Software."""

    def __init__(self, owner):
        super().__init__(owner)
        self.owner = owner
        self.setWindowTitle("Options — Installed Software")
        self.resize(980, 720)

        root = QVBoxLayout(self)
        tabs = QTabWidget()
        root.addWidget(tabs, 1)

        # AppImage Folders — deliberately first for this applet.
        appimage_page = QWidget()
        appimage_layout = QVBoxLayout(appimage_page)
        appimage_layout.setContentsMargins(12, 12, 12, 12)
        appimage_layout.setSpacing(10)

        appimage_heading = QLabel("AppImage Search Folders")
        appimage_heading_font = appimage_heading.font()
        appimage_heading_font.setBold(True)
        appimage_heading.setFont(appimage_heading_font)
        appimage_layout.addWidget(appimage_heading)

        appimage_note = QLabel(
            "LinSpectacles discovers AppImage files recursively in these folders. "
            "Missing or unreadable folders are skipped. Changes are used by the next scan."
        )
        appimage_note.setWordWrap(True)
        appimage_layout.addWidget(appimage_note)

        self.appimage_list = QListWidget()
        appimage_layout.addWidget(self.appimage_list, 1)
        self._set_appimage_folders(owner.config.appimage_search_folders, persist=False)

        appimage_controls = QHBoxLayout()
        add_folder_btn = QPushButton("Add Folder")
        remove_folder_btn = QPushButton("Remove")
        defaults_btn = QPushButton("Restore Defaults")
        add_folder_btn.clicked.connect(self._add_appimage_folder)
        remove_folder_btn.clicked.connect(self._remove_appimage_folders)
        defaults_btn.clicked.connect(
            lambda: self._set_appimage_folders(
                InstalledSoftwareConfig.DEFAULT_APPIMAGE_FOLDERS, persist=True
            )
        )
        appimage_controls.addWidget(add_folder_btn)
        appimage_controls.addWidget(remove_folder_btn)
        appimage_controls.addWidget(defaults_btn)
        appimage_controls.addStretch(1)
        appimage_layout.addLayout(appimage_controls)
        tabs.addTab(appimage_page, "AppImage Folders")

        # Appearance
        appearance_page = QWidget()
        appearance_layout = QVBoxLayout(appearance_page)
        appearance_layout.setContentsMargins(12, 12, 12, 12)
        appearance_layout.setSpacing(10)

        appearance_heading = QLabel("Metadata display")
        heading_font = appearance_heading.font()
        heading_font.setBold(True)
        appearance_heading.setFont(heading_font)
        appearance_layout.addWidget(appearance_heading)

        appearance_note = QLabel(
            "Choose how the inventory and Metadata Details panes are arranged. "
            "This Installed Software setting is remembered."
        )
        appearance_note.setWordWrap(True)
        appearance_layout.addWidget(appearance_note)

        layout_row = QHBoxLayout()
        layout_row.addWidget(QLabel("Record details layout:"))
        self.layout_combo = QComboBox()
        self.layout_combo.addItems(["Horizontal", "Vertical"])
        self.layout_combo.setCurrentText(
            "Vertical" if owner.details_orientation == "vertical" else "Horizontal"
        )
        self.layout_combo.currentTextChanged.connect(self._layout_changed)
        layout_row.addWidget(self.layout_combo)
        layout_row.addStretch(1)
        appearance_layout.addLayout(layout_row)

        self.show_metadata_panel = QCheckBox("Show Metadata Details panel (F3)")
        self.show_metadata_panel.setChecked(owner.metadata_panel_visible)
        self.show_metadata_panel.toggled.connect(self._metadata_panel_changed)
        appearance_layout.addWidget(self.show_metadata_panel)

        shortcut_note = QLabel(
            "F3 hides/restores Metadata Details. Ctrl+L toggles Horizontal/Vertical layout. "
            "Shift+F5 scans installed software or scans again. Ctrl+/ opens Options. "
            "Ctrl+F focuses Search and selects its contents."
        )
        shortcut_note.setWordWrap(True)
        appearance_layout.addWidget(shortcut_note)
        appearance_layout.addStretch(1)
        tabs.addTab(appearance_page, "Appearance")

        # Sources
        sources_page = QWidget()
        sources_layout = QVBoxLayout(sources_page)
        sources_layout.setContentsMargins(12, 12, 12, 12)
        sources_layout.setSpacing(10)

        sources_heading = QLabel("Data Sources")
        sources_heading_font = sources_heading.font()
        sources_heading_font.setBold(True)
        sources_heading_font.setPointSize(max(sources_heading_font.pointSize(), 11))
        sources_heading.setFont(sources_heading_font)
        sources_layout.addWidget(sources_heading)

        sources_note = QLabel(
            "Linux interfaces and package-manager commands used by this read-only software inventory."
        )
        sources_note.setWordWrap(True)
        sources_layout.addWidget(sources_note)

        sources_text = QTextEdit()
        sources_text.setReadOnly(True)
        sources_text.setLineWrapMode(QTextEdit.LineWrapMode.WidgetWidth)
        chosen_font = getattr(owner, "_metadata_font", None)
        if chosen_font is not None:
            sources_text.setFont(chosen_font)
            sources_text.document().setDefaultFont(chosen_font)
            sources_text.setCurrentFont(chosen_font)
            font_rule = metadata_font_qss(chosen_font)
        else:
            font_rule = ""
        sources_text.setStyleSheet(
            "QTextEdit { background-color: #151719; color: #dcdcdc; border: 1px solid #333333; "
            f"{font_rule} "
            "}"
        )
        _populate_sources_canvas(sources_text, owner.sources_text())
        sources_layout.addWidget(sources_text, 1)
        tabs.addTab(sources_page, "Sources")

        # Export
        export_page = QWidget()
        export_layout = QVBoxLayout(export_page)
        export_layout.setContentsMargins(12, 12, 12, 12)
        export_layout.setSpacing(10)

        export_heading = QLabel("Export details")
        export_heading_font = export_heading.font()
        export_heading_font.setBold(True)
        export_heading.setFont(export_heading_font)
        export_layout.addWidget(export_heading)

        export_note = QLabel("Select the Installed Software details to include in the export.")
        export_note.setWordWrap(True)
        export_layout.addWidget(export_note)

        self.export_name = QCheckBox("Name")
        self.export_type = QCheckBox("Type")
        self.export_version = QCheckBox("Version")
        self.export_architecture = QCheckBox("Architecture")
        self.export_source = QCheckBox("Source")
        self.export_metadata = QCheckBox("Metadata Details")
        for checkbox in (
            self.export_name,
            self.export_type,
            self.export_version,
            self.export_architecture,
            self.export_source,
        ):
            checkbox.setChecked(True)
        self.export_metadata.setChecked(False)
        for checkbox in (
            self.export_name,
            self.export_type,
            self.export_version,
            self.export_architecture,
            self.export_source,
            self.export_metadata,
        ):
            export_layout.addWidget(checkbox)

        export_layout.addStretch(1)
        export_row = QHBoxLayout()
        export_row.addStretch(1)
        export_button = QPushButton("Export...")
        export_button.clicked.connect(self._export_selected)
        export_row.addWidget(export_button)
        export_layout.addLayout(export_row)
        tabs.addTab(export_page, "Export")

        # About
        about_page = QWidget()
        about_layout = QVBoxLayout(about_page)
        about_layout.setContentsMargins(24, 20, 24, 18)
        about_layout.setSpacing(8)

        icon_label = QLabel()
        icon_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        icon_label.setFixedSize(112, 112)
        icon_path = Path(__file__).resolve().parent / "icon.png"
        if icon_path.is_file():
            pixmap = QPixmap(str(icon_path))
            if not pixmap.isNull():
                icon_label.setPixmap(
                    pixmap.scaled(
                        112,
                        112,
                        Qt.AspectRatioMode.KeepAspectRatio,
                        Qt.TransformationMode.SmoothTransformation,
                    )
                )
        about_layout.addWidget(icon_label, alignment=Qt.AlignmentFlag.AlignHCenter)

        name = QLabel("<h2 style='margin:0'>Installed Software</h2>")
        name.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(name)
        version = QLabel(f"Version {APPLET_VERSION}")
        version.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(version)
        slogan = QLabel("<b>Expose. Explore. Explain.</b>")
        slogan.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(slogan)
        description = QLabel(
            "A read-only LinSpectacles applet for explicitly scanning distro packages, "
            "Flatpak applications, Snaps and discovered AppImages."
        )
        description.setWordWrap(True)
        description.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(description)
        details = QLabel(
            "<b>Build:</b> Standalone LinSpectacles applet<br>"
            "<b>Organisation ID:</b> "
            f"<a href='{APPLET_ORGANIZATION_URL}'>{APPLET_ORGANIZATION_ID}</a><br>"
            f"<b>Applet ID:</b> {APPLET_ID}"
        )
        details.setWordWrap(True)
        details.setTextInteractionFlags(
            Qt.TextInteractionFlag.TextBrowserInteraction
            | Qt.TextInteractionFlag.TextSelectableByMouse
        )
        details.setOpenExternalLinks(True)
        details.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(details)
        copyright_label = QLabel(
            "Copyright © 2026 "
            "<a href='https://github.com/brunonlinespace'>brunonlinespace</a>"
        )
        copyright_label.setOpenExternalLinks(True)
        copyright_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(copyright_label)
        license_label = QLabel("Licensed under GNU GPLv3 or later.")
        license_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(license_label)
        readme_url = QUrl.fromLocalFile(str(Path(__file__).resolve().parent.parent / "README.md")).toString()
        license_url = QUrl.fromLocalFile(str(Path(__file__).resolve().parent.parent / "LICENSE")).toString()
        links = QLabel(
            f"<a href='{readme_url}'>README</a> "
            "&nbsp;·&nbsp; "
            f"<a href='{license_url}'>License</a> "
            "&nbsp;·&nbsp; "
            f"<a href='{APPLET_REPOSITORY}'>Repository</a>"
        )
        links.setOpenExternalLinks(True)
        links.setAlignment(Qt.AlignmentFlag.AlignCenter)
        about_layout.addWidget(links)
        about_layout.addStretch(1)
        tabs.addTab(about_page, "About")

        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        buttons.rejected.connect(self.reject)
        root.addWidget(buttons)

    def _canonical(self, path):
        return AppImageFoldersDialog._canonical(path)

    def _set_appimage_folders(self, folders, *, persist):
        self.appimage_list.clear()
        seen = set()
        for folder in folders:
            canonical = self._canonical(folder)
            if canonical in seen:
                continue
            seen.add(canonical)
            self.appimage_list.addItem(AppImageFoldersDialog._compact_home(folder))
        if persist:
            self._persist_appimage_folders()

    def _add_appimage_folder(self):
        directory = QFileDialog.getExistingDirectory(self, "Add AppImage Search Folder")
        if not directory:
            return
        candidate = AppImageFoldersDialog._compact_home(directory)
        existing = {
            self._canonical(self.appimage_list.item(i).text())
            for i in range(self.appimage_list.count())
        }
        if self._canonical(candidate) not in existing:
            self.appimage_list.addItem(candidate)
            self._persist_appimage_folders()

    def _remove_appimage_folders(self):
        changed = False
        for item in self.appimage_list.selectedItems():
            self.appimage_list.takeItem(self.appimage_list.row(item))
            changed = True
        if changed:
            self._persist_appimage_folders()

    def _persist_appimage_folders(self):
        folders = [
            self.appimage_list.item(i).text()
            for i in range(self.appimage_list.count())
        ]
        self.owner.config.appimage_search_folders = folders
        action = "Scan Again" if self.owner.has_scanned else "Scan Installed Software"
        self.owner.set_status(
            f"AppImage search folders updated ({len(folders)} configured). "
            f"Run {action} to refresh discoveries."
        )

    def _layout_changed(self, text):
        self.owner.set_details_orientation(str(text).strip().lower(), persist=True)

    def _metadata_panel_changed(self, visible):
        self.owner.set_metadata_panel_visible(bool(visible), persist=True)

    def _export_selected(self):
        selected = []
        if self.export_name.isChecked():
            selected.append(("name", "Name", 190, "left"))
        if self.export_type.isChecked():
            selected.append(("category", "Type", 125, "left"))
        if self.export_version.isChecked():
            selected.append(("version", "Version", 150, "left"))
        if self.export_architecture.isChecked():
            selected.append(("architecture", "Architecture", 105, "left"))
        if self.export_source.isChecked():
            selected.append(("source", "Source", 170, "left"))
        if self.export_metadata.isChecked():
            selected.append(("metadata_details", "Metadata Details", 520, "left"))
        if not selected:
            QMessageBox.information(self, "Export", "Select at least one detail to export.")
            return

        records = []
        include_metadata = self.export_metadata.isChecked()
        for record in self.owner.get_export_records():
            exported = dict(record)
            if include_metadata:
                exported["metadata_details"] = self.owner.metadata_details_for_export(record)
            records.append(exported)
        if not records:
            QMessageBox.information(self, "Export", "There is no collected data to export yet.")
            return
        path = export_records_dialog(self, APPLET_NAME, records, columns=selected)
        if path is not None:
            self.owner.set_status(f"Exported data to {path}")


class _MetadataLookupSignals(QObject):
    finished = pyqtSignal(int, object, object)


class _MetadataLookup(QRunnable):
    """Query selected-item metadata away from the Qt GUI thread."""

    def __init__(self, generation, record_key, record):
        super().__init__()
        self.generation = generation
        self.record_key = record_key
        self.record = dict(record)
        self.signals = _MetadataLookupSignals()

    def run(self):
        # No QWidget is accessed from this worker thread.
        try:
            result = lookup_package_metadata(self.record)
        except Exception as exc:
            result = ("Package", [("Detail lookup", f"Unavailable: {exc}")])
        self.signals.finished.emit(self.generation, self.record_key, result)


class InstalledSoftwareTab(BaseInspectorTab):
    """Unified, explicit-scan inventory for distro packages, Flatpak, Snap and AppImage."""

    def __init__(self, config=None, parent=None):
        self.config = config or InstalledSoftwareConfig()
        columns = [
            ("name", "Name", 190, "left"),
            ("category", "Type", 125, "left"),
            ("version", "Version", 150, "left"),
            ("architecture", "Architecture", 105, "left"),
            ("source", "Source", 170, "left"),
        ]
        categories = [
            "All Categories",
            "System Package",
            "Flatpak",
            "Snap",
            "AppImage",
        ]
        super().__init__("Installed Software", columns, categories, parent)
        self.export_title = APPLET_NAME
        self.has_scanned = False
        self.category_label.setText("Type:")
        self.category_dropdown.setItemText(0, "All Types")
        # Base filtering treats its first category as the all-items sentinel.
        self.categories[0] = "All Types"

        self.refresh_btn.setText("Scan Installed Software")
        self.stats_label.setText("Installed Software: Not scanned")
        self.set_status("Not scanned - select Scan Installed Software to inspect this system.")

        self.details_orientation = "horizontal"
        self._load_details_orientation()
        self.metadata_panel_visible = True
        self._load_metadata_panel_visibility()

        # Additional package metadata is requested only after selection; the
        # inventory scan remains byte-for-byte unchanged in its collectors.
        self._metadata_generation = 0
        self._detail_cache = {}
        self._detail_tasks = {}
        self._selected_detail_key = None
        self._detail_timer = QTimer(self)
        self._detail_timer.setSingleShot(True)
        self._detail_timer.timeout.connect(self._fetch_selected_metadata)

        self._shortcuts = []
        self._install_shortcuts()

        self.export_btn.setText("Options...")
        try:
            self.export_btn.clicked.disconnect()
        except TypeError:
            pass
        self.export_btn.clicked.connect(self.show_options)
        self.export_btn.setEnabled(True)

    def filter_modules(self, *_):
        query = self.search_entry.text().strip().lower()
        selected_type = self.category_dropdown.currentText()
        matches = []
        for record in self.modules_data:
            matches_type = selected_type == "All Types" or record.get("category") == selected_type
            matches_query = not query or any(query in str(value).lower() for value in record.values())
            if matches_type and matches_query:
                matches.append(record)

        self.table.setSortingEnabled(False)
        self.table.setRowCount(len(matches))
        for row, record in enumerate(matches):
            for column_index, (column_id, _, _, alignment) in enumerate(self.columns):
                value = record.get(column_id, "")
                item = QTableWidgetItem(str(value))
                if column_index == 0:
                    item.setData(Qt.ItemDataRole.UserRole, record)
                if alignment == "right":
                    item.setTextAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
                else:
                    item.setTextAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignVCenter)
                self.table.setItem(row, column_index, item)
        self.table.setSortingEnabled(False)

        if query or selected_type != "All Types":
            self.filter_status_lbl.setText(
                f"(Showing {len(matches)} of {len(self.modules_data)} items)"
            )
        else:
            self.filter_status_lbl.clear()

    @staticmethod
    def _run(command, timeout=30):
        return subprocess.run(
            command,
            capture_output=True,
            text=True,
            check=False,
            timeout=timeout,
        )

    @staticmethod
    def _read_os_release():
        values = {}
        try:
            with open("/etc/os-release", "r", encoding="utf-8", errors="replace") as handle:
                for line in handle:
                    if "=" not in line:
                        continue
                    key, value = line.rstrip().split("=", 1)
                    values[key] = value.strip().strip('"')
        except OSError:
            pass
        return values

    def _detect_system_backend(self):
        os_release = self._read_os_release()
        identity = " ".join(
            [os_release.get("ID", ""), os_release.get("ID_LIKE", "")]
        ).lower()

        if shutil.which("pacman") and "arch" in identity:
            return "pacman"
        if shutil.which("dpkg-query") and any(token in identity for token in ("debian", "ubuntu")):
            return "dpkg"
        if shutil.which("rpm") and any(
            token in identity for token in ("fedora", "rhel", "centos", "suse", "opensuse", "mageia")
        ):
            return "rpm"

        if shutil.which("rpm") and (Path("/var/lib/rpm").exists() or Path("/usr/lib/sysimage/rpm").exists()):
            return "rpm"
        if shutil.which("dpkg-query") and Path("/var/lib/dpkg/status").exists():
            return "dpkg"
        if shutil.which("pacman") and Path("/var/lib/pacman/local").exists():
            return "pacman"
        return None

    def _collect_system_packages(self):
        backend = self._detect_system_backend()
        records = []
        if backend == "rpm":
            fmt = "%{NAME}\\t%{VERSION}-%{RELEASE}\\t%{ARCH}\\t%{SUMMARY}\\t%{LICENSE}\\t%{SIZE}\\t%{INSTALLTIME}\\n"
            result = self._run(["rpm", "-qa", "--qf", fmt], timeout=45)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split("\t", 6)
                    if len(parts) < 3:
                        continue
                    while len(parts) < 7:
                        parts.append("")
                    name, version, arch, summary, license_text, size_text, install_time = parts
                    record = {
                        "name": name,
                        "category": "System Package",
                        "version": version or "-",
                        "architecture": arch or "-",
                        "source": "RPM",
                        "summary": summary or "-",
                        "license": license_text or "-",
                    }
                    try:
                        record["installed_size"] = int(size_text)
                    except (TypeError, ValueError):
                        pass
                    try:
                        stamp = int(install_time)
                        record["install_time"] = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(stamp))
                    except (TypeError, ValueError, OSError):
                        pass
                    records.append(record)
            return records, "RPM"

        if backend == "dpkg":
            fmt = "${Package}\\t${Version}\\t${Architecture}\\t${binary:Summary}\\n"
            result = self._run(["dpkg-query", "-W", f"-f={fmt}"], timeout=45)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split("\t", 3)
                    if len(parts) < 3:
                        continue
                    while len(parts) < 4:
                        parts.append("")
                    name, version, arch, summary = parts
                    records.append({
                        "name": name,
                        "category": "System Package",
                        "version": version or "-",
                        "architecture": arch or "-",
                        "source": "dpkg",
                        "summary": summary or "-",
                    })
            return records, "dpkg"

        if backend == "pacman":
            result = self._run(["pacman", "-Q"], timeout=45)
            if result.returncode == 0:
                for line in result.stdout.splitlines():
                    parts = line.split(None, 1)
                    if len(parts) != 2:
                        continue
                    records.append({
                        "name": parts[0],
                        "category": "System Package",
                        "version": parts[1],
                        "architecture": "-",
                        "source": "pacman",
                    })
            return records, "pacman"

        return records, None

    def _collect_flatpaks(self):
        if not shutil.which("flatpak"):
            return [], False

        columns = "application,name,version,branch,arch,origin,installation"
        result = self._run(["flatpak", "list", "--app", f"--columns={columns}"], timeout=35)
        has_installation = result.returncode == 0
        if result.returncode != 0:
            columns = "application,name,version,branch,arch,origin"
            result = self._run(["flatpak", "list", "--app", f"--columns={columns}"], timeout=35)
        if result.returncode != 0:
            return [], True

        records = []
        for line in result.stdout.splitlines():
            parts = line.split("\t")
            if len(parts) < 2:
                continue
            while len(parts) < (7 if has_installation else 6):
                parts.append("")
            app_id, name, version, branch, arch, origin = parts[:6]
            installation = parts[6] if has_installation and len(parts) > 6 else ""
            records.append({
                "name": name or app_id,
                "category": "Flatpak",
                "version": version or branch or "-",
                "architecture": arch or "-",
                "source": origin or "Flatpak",
                "application_id": app_id,
                "branch": branch or "-",
                "origin": origin or "-",
                "installation": installation or "-",
            })
        return records, True

    def _collect_snaps(self):
        if not shutil.which("snap"):
            return [], False
        result = self._run(["snap", "list"], timeout=35)
        if result.returncode != 0:
            return [], True

        records = []
        lines = result.stdout.splitlines()
        for line in lines[1:]:
            parts = line.split(None, 5)
            if len(parts) < 2:
                continue
            while len(parts) < 6:
                parts.append("")
            name, version, revision, tracking, publisher, notes = parts
            records.append({
                "name": name,
                "category": "Snap",
                "version": version or "-",
                "architecture": "-",
                "source": publisher or "Snap",
                "revision": revision or "-",
                "tracking": tracking or "-",
                "publisher": publisher or "-",
                "notes": notes or "-",
            })
        return records, True

    @staticmethod
    def _appimage_architecture(path):
        machine_map = {
            3: "x86",
            40: "ARM",
            62: "x86_64",
            183: "aarch64",
            243: "riscv64",
        }
        try:
            with open(path, "rb") as handle:
                header = handle.read(20)
            if len(header) < 20 or header[:4] != b"\x7fELF":
                return "-"
            data_encoding = header[5]
            if data_encoding not in (1, 2):
                return "-"
            byte_order = "little" if data_encoding == 1 else "big"
            machine = int.from_bytes(header[18:20], byte_order)
            return machine_map.get(machine, str(machine))
        except OSError:
            return "-"

    @staticmethod
    def _appimage_name_version(filename):
        """Infer human name/version from common AppImage filename conventions.

        AppImage has no authoritative installed-package database, so filename
        parsing is intentionally conservative.  Architecture/packaging suffixes
        are removed before looking for a semantic version token.
        """
        stem = re.sub(r"(?i)\.appimage$", "", filename).strip()
        if not stem:
            return filename, "-"

        # Common build/architecture suffixes are metadata, not part of a version.
        suffix_re = re.compile(
            r"(?i)(?:[-_.](?:x86_64|amd64|aarch64|arm64|i[3-6]86|x86|armv7l|riscv64|linux|portable))+$"
        )
        candidate = suffix_re.sub("", stem).rstrip("-_. ")

        # Accept versions such as 0.7.2, 0.1.1-exp1, 0.0.1-exp3-r1, etc.
        match = re.match(
            r"^(.*?)[-_ ]v?(\d+(?:\.\d+)+(?:[-_.][A-Za-z0-9]+)*)$",
            candidate,
        )
        if match:
            name = match.group(1).strip("-_ ")
            version = match.group(2).replace("_", "-")
            if name:
                return name, version

        return candidate or stem, "-"

    def _collect_appimages(self):
        records = []
        seen_files = set()
        configured_folders = self.config.appimage_search_folders
        scanned_folders = 0

        for configured in configured_folders:
            root = os.path.abspath(os.path.expanduser(configured))
            if not os.path.isdir(root) or not os.access(root, os.R_OK):
                continue
            scanned_folders += 1
            try:
                walker = os.walk(root, topdown=True, followlinks=False)
                for current_root, dirs, files in walker:
                    # Avoid descending through unreadable child directories.
                    dirs[:] = [
                        name for name in dirs
                        if os.access(os.path.join(current_root, name), os.R_OK)
                    ]
                    for filename in files:
                        if not filename.lower().endswith(".appimage"):
                            continue
                        full_path = os.path.join(current_root, filename)
                        canonical = os.path.realpath(full_path)
                        if canonical in seen_files:
                            continue
                        seen_files.add(canonical)
                        name, version = self._appimage_name_version(filename)
                        record = {
                            "name": name or filename,
                            "category": "AppImage",
                            "version": version,
                            "architecture": self._appimage_architecture(full_path),
                            "source": AppImageFoldersDialog._compact_home(configured),
                            "path": AppImageFoldersDialog._compact_home(full_path),
                            "search_folder": AppImageFoldersDialog._compact_home(configured),
                        }
                        try:
                            stat_info = os.stat(full_path)
                            record["size"] = stat_info.st_size
                            record["modified"] = time.strftime(
                                "%Y-%m-%d %H:%M:%S", time.localtime(stat_info.st_mtime)
                            )
                        except OSError:
                            pass
                        records.append(record)
            except OSError:
                continue
        return records, scanned_folders

    def configure_appimage_folders(self):
        dialog = AppImageFoldersDialog(self.config.appimage_search_folders, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.config.appimage_search_folders = dialog.folders()
            count = len(self.config.appimage_search_folders)
            self.set_status(
                f"AppImage search folders updated ({count} configured). Scan Again to refresh discoveries."
            )

    @staticmethod
    def _format_bytes(value):
        try:
            number = float(value)
        except (TypeError, ValueError):
            return str(value)
        units = ("B", "KB", "MB", "GB", "TB")
        for unit in units:
            if number < 1024 or unit == units[-1]:
                return f"{number:.0f} {unit}" if unit == "B" else f"{number:.1f} {unit}"
            number /= 1024.0
        return str(value)

    def load_modules(self):
        # Results belonging to a previous inventory must not populate details
        # for this scan, even if a worker completes after Scan Again.
        self._metadata_generation += 1
        self._detail_timer.stop()
        self._selected_detail_key = None
        self._detail_cache.clear()
        self.modules_data.clear()
        self.table.setRowCount(0)
        self.clear_info()
        self.refresh_btn.setEnabled(False)
        self.set_status("Scanning installed software...")
        QApplication.processEvents()

        counts = {"System Package": 0, "Flatpak": 0, "Snap": 0, "AppImage": 0}
        source_notes = []
        try:
            system_records, system_backend = self._collect_system_packages()
            self.modules_data.extend(system_records)
            counts["System Package"] = len(system_records)
            source_notes.append(system_backend or "no distro package backend")
            QApplication.processEvents()

            flatpak_records, flatpak_available = self._collect_flatpaks()
            self.modules_data.extend(flatpak_records)
            counts["Flatpak"] = len(flatpak_records)
            if flatpak_available:
                source_notes.append("Flatpak")
            QApplication.processEvents()

            snap_records, snap_available = self._collect_snaps()
            self.modules_data.extend(snap_records)
            counts["Snap"] = len(snap_records)
            if snap_available:
                source_notes.append("Snap")
            QApplication.processEvents()

            appimage_records, scanned_folders = self._collect_appimages()
            self.modules_data.extend(appimage_records)
            counts["AppImage"] = len(appimage_records)

            self.modules_data.sort(key=lambda record: (record.get("name", "").lower(), record.get("category", "")))
            self.has_scanned = True
            self.stats_label.setText(f"Installed Software: {len(self.modules_data)}")
            self.filter_modules()
            self.refresh_btn.setText("Scan Again")
            self.set_status(
                "Scan complete - "
                f"{counts['System Package']} system packages, "
                f"{counts['Flatpak']} Flatpaks, {counts['Snap']} Snaps, "
                f"{counts['AppImage']} AppImages discovered in {scanned_folders} readable configured folders."
            )
        except subprocess.TimeoutExpired:
            self.stats_label.setText("Installed Software: Scan incomplete")
            self.set_status("Installed software scan timed out while querying a software source.")
        except Exception as exc:
            self.stats_label.setText("Installed Software: Scan failed")
            self.set_status(f"Installed software scan failed: {exc}")
            self.show_error("Installed Software", f"Could not complete the software scan:\n{exc}")
        finally:
            self.refresh_btn.setEnabled(True)

    def _render_record_details(self, record):
        self.clear_info()
        preferred_order = [
            ("name", "Name"),
            ("category", "Type"),
            ("version", "Version"),
            ("architecture", "Architecture"),
            ("source", "Source"),
            ("application_id", "Application ID"),
            ("branch", "Branch"),
            ("origin", "Origin"),
            ("installation", "Installation"),
            ("revision", "Revision"),
            ("tracking", "Tracking"),
            ("publisher", "Publisher"),
            ("summary", "Summary"),
            ("license", "License"),
            ("installed_size", "Installed Size"),
            ("install_time", "Install Time"),
            ("path", "Path"),
            ("search_folder", "Search Folder"),
            ("size", "File Size"),
            ("modified", "Modified"),
            ("notes", "Notes"),
        ]
        for key, label in preferred_order:
            if key not in record or record[key] in (None, ""):
                continue
            value = record[key]
            if key in ("installed_size", "size"):
                value = self._format_bytes(value)
            self.append_key_value(label, value)

        if record.get("category") != "AppImage":
            extra = self._detail_cache.get(self._detail_key(record))
            # Exporting an unvisited row must not claim an asynchronous lookup
            # is running or silently fetch every package's metadata.
            if extra is not None or self._detail_key(record) == self._selected_detail_key:
                self.append_info("\n--- Additional Package Metadata ---\n")
                if extra is None:
                    self.append_key_value("Status", "Loading local package metadata on selection...")
                else:
                    backend, fields = extra
                    self.append_key_value("Backend", backend)
                    if not fields:
                        self.append_key_value("Status", "No additional local metadata reported")
                    for label, value in fields:
                        self.append_key_value(label, value)

    def _scroll_details_to_top(self):
        cursor = self.info_text.textCursor()
        cursor.movePosition(QTextCursor.MoveOperation.Start)
        self.info_text.setTextCursor(cursor)
        self.info_text.verticalScrollBar().setValue(0)

    def on_module_select(self):
        self._detail_timer.stop()
        record = self.current_record()
        self._selected_detail_key = self._detail_key(record) if record else None
        if not record:
            return
        self._render_record_details(record)
        self._scroll_details_to_top()
        self.set_status(f"Inspecting installed software entry: [{record.get('name', 'Unknown')}]")
        if record.get("category") != "AppImage" and self._detail_key(record) not in self._detail_cache:
            # Debounce rapid keyboard/navigation changes; only the selected
            # item triggers a command, never all packages in a scan.
            if (self._metadata_generation, self._detail_key(record)) not in self._detail_tasks:
                self._detail_timer.start(150)

    @staticmethod
    def _detail_key(record):
        """Stable across PyQt QVariant conversions of QTableWidgetItem data."""
        return tuple(str(record.get(name, "") or "") for name in (
            "category", "source", "name", "version", "architecture",
            "application_id", "branch", "installation", "revision", "path",
        ))

    def _fetch_selected_metadata(self):
        record = self.current_record()
        if record is None or self._detail_key(record) != self._selected_detail_key:
            return
        if record.get("category") == "AppImage" or self._detail_key(record) in self._detail_cache:
            return
        key = (self._metadata_generation, self._detail_key(record))
        if key in self._detail_tasks:
            return
        task = _MetadataLookup(self._metadata_generation, self._detail_key(record), record)
        task.signals.finished.connect(self._metadata_lookup_finished)
        # Hold the worker/signals until the queued completion is delivered.
        self._detail_tasks[key] = task
        QThreadPool.globalInstance().start(task)

    def _metadata_lookup_finished(self, generation, record_key, result):
        self._detail_tasks.pop((generation, record_key), None)
        if generation != self._metadata_generation:
            return
        self._detail_cache[record_key] = result
        current = self.current_record()
        if current is None or self._detail_key(current) != self._selected_detail_key:
            return
        if self._detail_key(current) != record_key:
            return
        self._render_record_details(current)
        self._scroll_details_to_top()

    @staticmethod
    def sources_text():
        return (
            "Installed Software is read-only and only scans when requested. Its inventory can use these sources.\n\n"
            "Distribution packages\n"
            "  RPM systems: rpm -qa --qf ...\n"
            "  Debian-family systems: dpkg-query -W ...\n"
            "  Arch-family systems: pacman -Q\n\n"
            "Flatpak applications\n"
            "  flatpak list --app --columns=...\n\n"
            "Snap packages\n"
            "  snap list\n\n"
            "AppImages\n"
            "  Recursive read-only discovery of files ending in .AppImage inside the configured AppImage Search Folders.\n"
            "  Architecture is inferred from the AppImage ELF header where available; name/version are conservatively inferred from the filename.\n\n"
            "System package backend selection\n"
            "  /etc/os-release plus the available package-manager database/command.\n\n"
            "Selected-record metadata\n"
            "  Retrieved on selection only, using local installed-software information; cached until Scan Again.\n"
            "  RPM: rpm -qi, --requires and --provides for the selected installed package.\n"
            "  dpkg: dpkg-query -s for the selected installed package.\n"
            "  pacman: pacman -Qi for the selected installed package.\n"
            "  Flatpak: flatpak info for the selected installed application.\n"
            "  Snap: installed /snap/<name>/current/meta/snap.yaml, when readable.\n"
            "  AppImages have no authoritative package metadata; their existing file information remains unchanged.\n\n"
            "Important distinctions\n"
            "  AppImage discovery is folder-based because AppImage has no authoritative system-wide installed-package database.\n"
            "  Missing or unreadable AppImage search folders are skipped.\n"
            "  Installed Software never installs, removes, updates or modifies software."
        )

    def show_options(self):
        dialog = InstalledSoftwareOptionsDialog(self)
        dialog.exec()

    def _add_shortcut(self, sequence, callback):
        shortcut = QShortcut(QKeySequence(sequence), self)
        shortcut.setContext(Qt.ShortcutContext.WidgetWithChildrenShortcut)
        shortcut.activated.connect(callback)
        self._shortcuts.append(shortcut)

    def _install_shortcuts(self):
        self._add_shortcut("F3", self.toggle_metadata_panel)
        self._add_shortcut("Ctrl+L", self.toggle_details_orientation)
        self._add_shortcut("Shift+F5", self.refresh_btn.click)
        self._add_shortcut("Ctrl+/", self.show_options)
        self._add_shortcut("Ctrl+F", self.focus_search)

    def toggle_details_orientation(self):
        target = "vertical" if self.details_orientation == "horizontal" else "horizontal"
        self.set_details_orientation(target, persist=True)

    def focus_search(self):
        self.search_entry.setFocus()
        self.search_entry.selectAll()

    def _load_metadata_panel_visibility(self):
        settings = QSettings("brunonlinespace", "Installed Software")
        value = settings.value("metadata_panel_visible", True)
        if isinstance(value, str):
            visible = value.strip().lower() not in {"0", "false", "no", "off"}
        else:
            visible = bool(value)
        self.set_metadata_panel_visible(visible, persist=False)

    def set_metadata_panel_visible(self, visible, *, persist=True):
        self.metadata_panel_visible = bool(visible)
        details_panel = self.splitter.widget(1)
        if details_panel is not None:
            details_panel.setVisible(self.metadata_panel_visible)
        if self.metadata_panel_visible:
            self.set_details_orientation(self.details_orientation, persist=False)
        if persist:
            settings = QSettings("brunonlinespace", "Installed Software")
            settings.setValue("metadata_panel_visible", self.metadata_panel_visible)
            settings.sync()

    def toggle_metadata_panel(self):
        self.set_metadata_panel_visible(not self.metadata_panel_visible, persist=True)

    def _load_details_orientation(self):
        settings = QSettings("brunonlinespace", "Installed Software")
        orientation = str(settings.value("details_orientation", "horizontal") or "horizontal").lower()
        if orientation not in {"horizontal", "vertical"}:
            orientation = "horizontal"
        self.set_details_orientation(orientation, persist=False)

    def set_details_orientation(self, orientation, *, persist=True):
        orientation = str(orientation or "horizontal").lower()
        if orientation not in {"horizontal", "vertical"}:
            orientation = "horizontal"
        self.details_orientation = orientation
        details_panel = self.splitter.widget(1)
        details_layout = details_panel.layout() if details_panel is not None else None
        if orientation == "vertical":
            self.splitter.setOrientation(Qt.Orientation.Vertical)
            if details_layout is not None:
                details_layout.setContentsMargins(0, 8, 0, 0)
            self.splitter.setSizes([430, 330])
        else:
            self.splitter.setOrientation(Qt.Orientation.Horizontal)
            if details_layout is not None:
                details_layout.setContentsMargins(8, 0, 0, 0)
            self.splitter.setSizes([480, 700])
        if persist:
            settings = QSettings("brunonlinespace", "Installed Software")
            settings.setValue("details_orientation", orientation)
            settings.sync()

    def metadata_details_for_export(self, record):
        original_info = self.info_text
        temp = QTextEdit()
        self.info_text = temp
        try:
            self._render_record_details(record)
            return temp.toPlainText()
        finally:
            self.info_text = original_info


def create_applet(parent=None):
    return InstalledSoftwareTab(parent=parent)

def run_standalone():
    import sys
    from pathlib import Path
    from PyQt6.QtGui import QIcon
    from PyQt6.QtWidgets import QApplication, QMainWindow
    from ._support import STANDALONE_STYLESHEET
    app = QApplication(sys.argv)
    app.setApplicationName(APPLET_NAME)
    app.setOrganizationName(APPLET_ORGANIZATION_ID)
    icon_path = Path(__file__).resolve().parent / "icon.png"
    if icon_path.is_file():
        app.setWindowIcon(QIcon(str(icon_path)))
    app.setStyleSheet(STANDALONE_STYLESHEET)
    window = QMainWindow()
    if icon_path.is_file():
        window.setWindowIcon(QIcon(str(icon_path)))
    window.setWindowTitle(f"{APPLET_NAME} {APPLET_VERSION} — LinSpectacles Applet")
    window.resize(1200, 780)
    window.setCentralWidget(create_applet())
    window.show()
    return app.exec()
