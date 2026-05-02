"""Attach-trigger dock widget (issue v1.4-3).

UI surface only — running the trigger is done in v1.4-4 (#470). The
"Run trigger" button is wired to a no-op stub that the runner story
will replace.
"""

from __future__ import annotations

from .state import CUSTOM_PROPERTY_KEY, AttachState


def _qgis_imports():
    """Lazy import so unit tests on the Qt-free `state` module don't pull
    in QGIS bindings."""
    from qgis.core import QgsMapLayer, QgsProject
    from qgis.PyQt.QtCore import Qt
    from qgis.PyQt.QtWidgets import (
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
        "Qt": Qt,
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
    Qt = q["Qt"]

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

    # Preview
    layout.addWidget(q["QLabel"](_tr("Preview:")))
    preview = q["QTextEdit"](container)
    preview.setReadOnly(True)
    preview.setLineWrapMode(q["QTextEdit"].NoWrap)
    layout.addWidget(preview, stretch=1)

    # Run button
    run_btn = q["QPushButton"](_tr("Run trigger"), container)
    run_btn.setEnabled(False)
    layout.addWidget(run_btn)

    container.setLayout(layout)
    dock.setWidget(container)

    # ─── helpers ─────────────────────────────────────────────────────

    def _refresh_run_state() -> None:
        layer_msg.setText(state.layer_message())
        v = state.rules_validation()
        rules_msg.setText(v.message)
        run_btn.setEnabled(state.can_run())

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

    def _on_run_clicked() -> None:
        # Sub-process runner lands in v1.4-4 (#470). For now, emit a
        # human-readable line into the preview so the wiring is visible.
        preview.append(f"\n# [v1.4-3 stub] would run gispulse with {state.rules_path}\n")

    # ─── signals ─────────────────────────────────────────────────────

    layer_combo.currentIndexChanged.connect(_on_layer_changed)
    rules_btn.clicked.connect(_on_browse_clicked)
    run_btn.clicked.connect(_on_run_clicked)

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
