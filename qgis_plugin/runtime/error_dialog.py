"""Qt-side companion to `detector.py`.

Kept in a separate module so unit tests can import `detector` without
pulling in `qgis.PyQt`. The "Test again" button calls `clear_cache()` and
re-runs detection without restarting QGIS.
"""

from __future__ import annotations

from .detector import DetectorResult, clear_cache, detect_gispulse, install_hint

DOC_URL = "https://gispulse.dev/plugins/qgis-troubleshooting"


def show_install_dialog(parent, result: DetectorResult) -> DetectorResult:
    """Show a modal explaining how to install / upgrade gispulse, with
    a 'Test again' button. Returns the (possibly fresh) DetectorResult."""
    from qgis.PyQt.QtCore import QUrl
    from qgis.PyQt.QtGui import QDesktopServices
    from qgis.PyQt.QtWidgets import QDialog, QDialogButtonBox, QLabel, QVBoxLayout

    dialog = QDialog(parent)
    dialog.setWindowTitle("GISPulse — install required")
    layout = QVBoxLayout(dialog)

    layout.addWidget(QLabel(_format_message(result)))
    hint = QLabel(install_hint())
    hint.setTextInteractionFlags(hint.textInteractionFlags() | 0x4)
    layout.addWidget(hint)

    buttons = QDialogButtonBox()
    test_btn = buttons.addButton("Test again", QDialogButtonBox.ActionRole)
    docs_btn = buttons.addButton("Open docs", QDialogButtonBox.ActionRole)
    close_btn = buttons.addButton(QDialogButtonBox.Close)
    layout.addWidget(buttons)

    state: dict[str, DetectorResult] = {"result": result}

    def _retest() -> None:
        clear_cache()
        fresh = detect_gispulse(use_cache=False)
        state["result"] = fresh
        if fresh.found:
            dialog.accept()
        else:
            layout.itemAt(0).widget().setText(_format_message(fresh))

    test_btn.clicked.connect(_retest)
    docs_btn.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(DOC_URL)))
    close_btn.clicked.connect(dialog.reject)

    dialog.exec_()
    return state["result"]


def _format_message(result: DetectorResult) -> str:
    if result.found:
        return f"GISPulse {result.version_str} found at {result.path}."
    if result.path and result.version is not None:
        return (
            f"GISPulse {result.version_str} found at {result.path}, but the plugin "
            f"needs a newer version. Please upgrade and click 'Test again'."
        )
    return (
        "GISPulse CLI was not found on this system.\n"
        "Install it with the command below, then click 'Test again'."
    )
