"""Attach-trigger dock widget (issues v1.4-3 + v1.4-4).

UI surface and live runner integration:

* layer + rules picker, vector-only gate, persisted custom properties
  (v1.4-3)
* QProcess-backed gispulse runner with streamed coloured logs, Cancel
  with SIGTERM→SIGKILL escalation, success/error status banner, and a
  Pause-autoscroll toggle (v1.4-4)

Pure validation/state logic lives in `state.py`; subprocess wrapping in
`runtime/runner.py`. This module is the Qt glue between them.
"""

from __future__ import annotations

import tempfile
from pathlib import Path

from ..runtime import LogLevel, detect_gispulse, format_log_html
from ..runtime.error_dialog import show_install_dialog
from ..runtime.runner import make_runner_class
from .state import CUSTOM_PROPERTY_KEY, AttachState


def _qgis_imports():
    """Lazy import so unit tests on the Qt-free `state` / `log_format`
    modules don't pull in QGIS bindings."""
    from qgis.core import QgsMapLayer, QgsProject, QgsVectorFileWriter
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import (
        QCheckBox,
        QComboBox,
        QDockWidget,
        QFileDialog,
        QHBoxLayout,
        QLabel,
        QPushButton,
        QTextEdit,
        QVBoxLayout,
        QWidget,
    )

    return {
        "QgsMapLayer": QgsMapLayer,
        "QgsProject": QgsProject,
        "QgsVectorFileWriter": QgsVectorFileWriter,
        "Qt": Qt,
        "QCheckBox": QCheckBox,
        "QComboBox": QComboBox,
        "QDockWidget": QDockWidget,
        "QFileDialog": QFileDialog,
        "QHBoxLayout": QHBoxLayout,
        "QLabel": QLabel,
        "QPushButton": QPushButton,
        "QTextEdit": QTextEdit,
        "QVBoxLayout": QVBoxLayout,
        "QWidget": QWidget,
    }


def build_dock_widget(parent):
    q = _qgis_imports()
    QgsMapLayer = q["QgsMapLayer"]
    QgsProject = q["QgsProject"]
    QgsVectorFileWriter = q["QgsVectorFileWriter"]
    Qt = q["Qt"]
    GispulseRunner = make_runner_class()

    dock = q["QDockWidget"](_tr("GISPulse"), parent)
    dock.setObjectName("GISPulseDock")
    dock.setAllowedAreas(Qt.LeftDockWidgetArea | Qt.RightDockWidgetArea)

    container = q["QWidget"](dock)
    layout = q["QVBoxLayout"](container)
    state = AttachState()

    # Layer combo
    layout.addWidget(q["QLabel"](_tr("Layer:")))
    layer_combo = q["QComboBox"](container)
    layout.addWidget(layer_combo)
    layer_msg = q["QLabel"]("", container)
    layer_msg.setStyleSheet("color: #b00;")
    layout.addWidget(layer_msg)

    # Rules YAML picker
    layout.addWidget(q["QLabel"](_tr("Rules YAML:")))
    rules_row = q["QHBoxLayout"]()
    rules_label = q["QLabel"]("(none)", container)
    rules_btn = q["QPushButton"](_tr("Browse…"), container)
    rules_row.addWidget(rules_label, stretch=1)
    rules_row.addWidget(rules_btn)
    layout.addLayout(rules_row)
    rules_msg = q["QLabel"]("", container)
    rules_msg.setStyleSheet("color: #b00;")
    layout.addWidget(rules_msg)

    # Preview (rules YAML)
    layout.addWidget(q["QLabel"](_tr("Preview:")))
    preview = q["QTextEdit"](container)
    preview.setReadOnly(True)
    preview.setLineWrapMode(q["QTextEdit"].NoWrap)
    layout.addWidget(preview, stretch=1)

    # Run / Cancel + autoscroll
    button_row = q["QHBoxLayout"]()
    run_btn = q["QPushButton"](_tr("Run trigger"), container)
    run_btn.setEnabled(False)
    cancel_btn = q["QPushButton"](_tr("Cancel"), container)
    cancel_btn.setEnabled(False)
    autoscroll = q["QCheckBox"](_tr("Autoscroll"), container)
    autoscroll.setChecked(True)
    button_row.addWidget(run_btn, stretch=1)
    button_row.addWidget(cancel_btn)
    button_row.addWidget(autoscroll)
    layout.addLayout(button_row)

    # Status banner
    status = q["QLabel"]("", container)
    status.setVisible(False)
    layout.addWidget(status)

    # Live logs area
    layout.addWidget(q["QLabel"](_tr("Logs:")))
    logs = q["QTextEdit"](container)
    logs.setReadOnly(True)
    logs.setLineWrapMode(q["QTextEdit"].NoWrap)
    logs.setStyleSheet("font-family: monospace;")
    layout.addWidget(logs, stretch=2)

    container.setLayout(layout)
    dock.setWidget(container)

    runner = GispulseRunner(container)

    # ─── helpers ─────────────────────────────────────────────────────

    def _refresh_run_state() -> None:
        layer_msg.setText(state.layer_message())
        v = state.rules_validation()
        rules_msg.setText(v.message)
        run_btn.setEnabled(state.can_run() and not runner.is_running())

    def _populate_layers() -> None:
        layer_combo.blockSignals(True)
        try:
            current_id = state.layer_id
            layer_combo.clear()
            layer_combo.addItem(_tr("(select a layer)"), None)
            for layer in QgsProject.instance().mapLayers().values():
                layer_combo.addItem(layer.name(), layer.id())
            if current_id:
                idx = layer_combo.findData(current_id)
                if idx >= 0:
                    layer_combo.setCurrentIndex(idx)
        finally:
            layer_combo.blockSignals(False)

    def _on_layer_changed(idx: int) -> None:
        layer_id = layer_combo.itemData(idx)
        if not layer_id:
            state.set_layer(None, is_vector=False)
            preview.clear()
            _refresh_run_state()
            return
        layer = QgsProject.instance().mapLayer(layer_id)
        is_vector = bool(layer) and layer.type() == QgsMapLayer.VectorLayer
        state.set_layer(layer_id, is_vector=is_vector)
        if layer is not None:
            stored = layer.customProperty(CUSTOM_PROPERTY_KEY, "")
            if stored:
                _set_rules_path(stored)
        _refresh_run_state()

    def _set_rules_path(path: str) -> None:
        state.set_rules_path(path)
        rules_label.setText(path or "(none)")
        v = state.rules_validation()
        if v.valid:
            try:
                preview.setPlainText(open(path, encoding="utf-8").read())
            except OSError as exc:  # pragma: no cover - defensive
                preview.setPlainText(f"# could not read {path}: {exc}")
            if state.layer_id:
                layer = QgsProject.instance().mapLayer(state.layer_id)
                if layer is not None:
                    layer.setCustomProperty(CUSTOM_PROPERTY_KEY, path)
        else:
            preview.clear()
        _refresh_run_state()

    def _on_browse_clicked() -> None:
        path, _filter = q["QFileDialog"].getOpenFileName(
            container, _tr("Pick a rules YAML"), "", "YAML (*.yml *.yaml)"
        )
        if path:
            _set_rules_path(path)

    def _show_status(text: str, color: str) -> None:
        status.setText(text)
        status.setStyleSheet(f"padding: 4px; color: white; background-color: {color};")
        status.setVisible(True)

    def _project_dir() -> str:
        path = QgsProject.instance().homePath()
        return path or tempfile.gettempdir()

    def _export_layer_to_gpkg(layer_id: str) -> str | None:
        layer = QgsProject.instance().mapLayer(layer_id)
        if layer is None:
            return None
        out = Path(tempfile.gettempdir()) / f"gispulse_{layer_id}.gpkg"
        opts = QgsVectorFileWriter.SaveVectorOptions()
        opts.driverName = "GPKG"
        opts.layerName = layer.name()
        QgsVectorFileWriter.writeAsVectorFormatV3(
            layer, str(out), QgsProject.instance().transformContext(), opts
        )
        return str(out)

    def _on_run_clicked() -> None:
        det = detect_gispulse(use_cache=False)
        if not det.found:
            show_install_dialog(parent, det)
            return
        if not state.layer_id or not state.rules_path:
            return
        dataset_path = _export_layer_to_gpkg(state.layer_id)
        if not dataset_path:
            _show_status(_tr("Failed to export layer to GeoPackage."), "#b00020")
            return
        logs.clear()
        status.setVisible(False)
        run_btn.setEnabled(False)
        cancel_btn.setEnabled(True)
        runner.start(
            exe=det.path or "gispulse",
            rules_path=state.rules_path,
            dataset_path=dataset_path,
            project_dir=_project_dir(),
        )

    def _on_cancel_clicked() -> None:
        runner.cancel()
        cancel_btn.setEnabled(False)

    def _on_log_line(line: str, level_name: str) -> None:
        try:
            level = LogLevel[level_name]
        except KeyError:
            level = LogLevel.INFO
        logs.append(format_log_html(line, level))
        if autoscroll.isChecked():
            scroll = logs.verticalScrollBar()
            scroll.setValue(scroll.maximum())

    def _on_finished(exit_code: int) -> None:
        cancel_btn.setEnabled(False)
        if exit_code == 0:
            _show_status(_tr("Trigger completed successfully."), "#1f8a3a")
        elif exit_code == 130:
            _show_status(_tr("Trigger cancelled."), "#c97a00")
        else:
            _show_status(
                _tr("Trigger failed (exit {code}). See logs above.").format(code=exit_code),
                "#b00020",
            )
        _refresh_run_state()

    # ─── signals ─────────────────────────────────────────────────────

    layer_combo.currentIndexChanged.connect(_on_layer_changed)
    rules_btn.clicked.connect(_on_browse_clicked)
    run_btn.clicked.connect(_on_run_clicked)
    cancel_btn.clicked.connect(_on_cancel_clicked)
    runner.log_line.connect(_on_log_line)
    runner.finished.connect(_on_finished)

    project = QgsProject.instance()
    project.layersAdded.connect(lambda *_: _populate_layers())
    project.layersRemoved.connect(lambda *_: _populate_layers())

    _populate_layers()
    _refresh_run_state()
    return dock


def _tr(text: str) -> str:
    """Translation hook (Qt linguist).

    QGIS plugins use `QCoreApplication.translate(context, text)` which
    requires `pylupdate5` over the source. For v1.4 the strings live
    here verbatim; the .ts/.qm files are added in v1.4-7 with the
    install tutorial work.
    """
    try:
        from qgis.PyQt.QtCore import QCoreApplication

        return QCoreApplication.translate("GISPulseDock", text)
    except ImportError:
        return text
