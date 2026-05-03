from __future__ import annotations

from pathlib import Path


class GISPulsePlugin:
    def __init__(self, iface) -> None:
        self.iface = iface
        self.plugin_dir = Path(__file__).resolve().parent
        self.actions: list = []
        self.menu = "&GISPulse"

    def initGui(self) -> None:
        from qgis.PyQt.QtGui import QIcon
        from qgis.PyQt.QtWidgets import QAction

        icon = QIcon(str(self.plugin_dir / "icon.png"))
        action = QAction(icon, "About GISPulse", self.iface.mainWindow())
        action.triggered.connect(self._show_about)
        self.iface.addPluginToMenu(self.menu, action)
        self.actions.append(action)

    def unload(self) -> None:
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
