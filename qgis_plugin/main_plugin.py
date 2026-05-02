from __future__ import annotations

from pathlib import Path


class GISPulsePlugin:
    def __init__(self, iface) -> None:
        self.iface = iface
        self.plugin_dir = Path(__file__).resolve().parent
        self.actions: list = []
        self.menu = "&GISPulse"
        self._dock = None

    def initGui(self) -> None:
        from qgis.PyQt.QtGui import QIcon
        from qgis.PyQt.QtWidgets import QAction

        icon = QIcon(str(self.plugin_dir / "icon.png"))
        about_action = QAction(icon, "About GISPulse", self.iface.mainWindow())
        about_action.triggered.connect(self._show_about)
        self.iface.addPluginToMenu(self.menu, about_action)
        self.actions.append(about_action)

        check_action = QAction(icon, "Check gispulse install…", self.iface.mainWindow())
        check_action.triggered.connect(self._check_install)
        self.iface.addPluginToMenu(self.menu, check_action)
        self.actions.append(check_action)

        panel_action = QAction(icon, "Show panel", self.iface.mainWindow())
        panel_action.triggered.connect(self._show_panel)
        self.iface.addPluginToMenu(self.menu, panel_action)
        self.actions.append(panel_action)

    def unload(self) -> None:
        if self._dock is not None:
            self.iface.removeDockWidget(self._dock)
            self._dock.deleteLater()
            self._dock = None
        for action in self.actions:
            self.iface.removePluginMenu(self.menu, action)
        self.actions.clear()

    def _show_about(self) -> None:
        from qgis.PyQt.QtWidgets import QMessageBox

        QMessageBox.information(
            self.iface.mainWindow(),
            "GISPulse",
            "GISPulse plugin scaffold (v1.4-1).\n\n"
            "Full features land in v1.4-2..v1.4-8: gispulse executable detection, "
            "Attach trigger dock, sub-process runner, layer refresh.\n\n"
            "https://gispulse.dev",
        )

    def _check_install(self) -> None:
        from qgis.PyQt.QtWidgets import QMessageBox

        from .runtime import detect_gispulse
        from .runtime.error_dialog import show_install_dialog

        result = detect_gispulse(use_cache=False)
        if result.found:
            QMessageBox.information(
                self.iface.mainWindow(),
                "GISPulse",
                f"GISPulse {result.version_str} found at:\n{result.path}",
            )
            return
        show_install_dialog(self.iface.mainWindow(), result)

    def _show_panel(self) -> None:
        from qgis.PyQt.QtCore import Qt

        from .ui.dock_widget import build_dock_widget

        if self._dock is None:
            self._dock = build_dock_widget(self.iface.mainWindow())
            self.iface.addDockWidget(Qt.RightDockWidgetArea, self._dock)
        self._dock.show()
        self._dock.raise_()
