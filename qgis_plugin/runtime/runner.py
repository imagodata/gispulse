"""Non-blocking gispulse trigger runner backed by `QProcess`.

`subprocess.Popen` blocks the QGIS event loop while polling stdout, so
we use `QProcess` and connect to `readyReadStandardOutput` /
`readyReadStandardError` to push lines into the dock widget as they
arrive. Streaming latency is the MVP's load-bearing UX promise:
"`[Running…]` frozen for 30 seconds" is the failure mode we're avoiding.
"""

from __future__ import annotations

import shlex
from datetime import datetime
from pathlib import Path

from .log_format import LogLevel, log_file_path, parse_log_level

# Time we give the child to react to SIGTERM before escalating to SIGKILL.
# Longer than gispulse's own shutdown grace so well-behaved runs flush
# their state, short enough that hitting Cancel still feels responsive.
KILL_GRACE_MS = 5_000


def build_command(*, exe: str, rules_path: str, dataset_path: str) -> list[str]:
    """Compose the argv for `gispulse triggers run`. Kept Qt-free so the
    test suite can assert the contract without spinning up QProcess.
    """
    return [exe, "triggers", "run", "--rules", rules_path, "--dataset", dataset_path]


def shell_repr(argv: list[str]) -> str:
    """Render argv as a single, copy-pasteable shell line for the log
    file header. Uses `shlex.join` so paths with spaces stay quoted.
    """
    return shlex.join(argv)


def _qprocess_imports():
    from qgis.PyQt.QtCore import QObject, QProcess, QTimer, pyqtSignal

    return QObject, QProcess, QTimer, pyqtSignal


def make_runner_class():
    """Build `GispulseRunner` lazily so importing this module doesn't
    require Qt — keeps the rest of `runtime/` unit-testable in CI.
    """
    QObject, QProcess, QTimer, pyqtSignal = _qprocess_imports()

    class GispulseRunner(QObject):
        log_line = pyqtSignal(str, str)  # (line, level_name)
        finished = pyqtSignal(int)  # exit code
        started = pyqtSignal()

        def __init__(self, parent=None) -> None:
            super().__init__(parent)
            self._proc: QProcess | None = None
            self._kill_timer: QTimer | None = None
            self._log_handle = None
            self._cancelled = False

        def is_running(self) -> bool:
            return self._proc is not None and self._proc.state() != QProcess.NotRunning

        def start(
            self,
            *,
            exe: str,
            rules_path: str,
            dataset_path: str,
            project_dir: str | Path,
        ) -> None:
            if self.is_running():
                raise RuntimeError("a runner is already in progress")
            self._cancelled = False
            argv = build_command(exe=exe, rules_path=rules_path, dataset_path=dataset_path)
            log_path = log_file_path(project_dir, now=datetime.utcnow())
            log_path.parent.mkdir(parents=True, exist_ok=True)
            self._log_handle = log_path.open("w", encoding="utf-8")
            self._log_handle.write(f"# {shell_repr(argv)}\n")
            self._log_handle.flush()

            self._proc = QProcess(self)
            self._proc.setProcessChannelMode(QProcess.SeparateChannels)
            self._proc.readyReadStandardOutput.connect(self._on_stdout)
            self._proc.readyReadStandardError.connect(self._on_stderr)
            self._proc.finished.connect(self._on_finished)
            self._proc.errorOccurred.connect(self._on_error)
            self._proc.start(argv[0], argv[1:])
            self.started.emit()

        def cancel(self) -> None:
            if not self.is_running():
                return
            self._cancelled = True
            assert self._proc is not None
            self._proc.terminate()
            timer = QTimer(self)
            timer.setSingleShot(True)
            timer.timeout.connect(self._kill_if_running)
            timer.start(KILL_GRACE_MS)
            self._kill_timer = timer

        # ─── slots ────────────────────────────────────────────────

        def _on_stdout(self) -> None:
            assert self._proc is not None
            data = bytes(self._proc.readAllStandardOutput()).decode("utf-8", "replace")
            self._emit_lines(data, is_stderr=False)

        def _on_stderr(self) -> None:
            assert self._proc is not None
            data = bytes(self._proc.readAllStandardError()).decode("utf-8", "replace")
            self._emit_lines(data, is_stderr=True)

        def _emit_lines(self, data: str, *, is_stderr: bool) -> None:
            for raw in data.splitlines():
                if not raw:
                    continue
                level = parse_log_level(raw)
                # gispulse uses stderr for structured progress AND errors;
                # only escalate when the line itself doesn't already
                # carry an explicit level marker.
                if is_stderr and level is LogLevel.INFO:
                    level = LogLevel.WARN
                if self._log_handle is not None:
                    self._log_handle.write(raw + "\n")
                    self._log_handle.flush()
                self.log_line.emit(raw, level.value)

        def _on_finished(self, exit_code: int, _exit_status: int) -> None:
            self._teardown()
            code = exit_code if not self._cancelled else 130  # convention: "user cancelled"
            self.finished.emit(code)

        def _on_error(self, _err: int) -> None:
            # `errorOccurred` fires for FailedToStart, Crashed, etc. We
            # let `finished` handle the cleanup; this slot just records
            # a diagnostic line.
            assert self._proc is not None
            msg = self._proc.errorString()
            self.log_line.emit(f"[error] {msg}", LogLevel.ERROR.value)

        def _kill_if_running(self) -> None:
            if self.is_running():
                assert self._proc is not None
                self._proc.kill()

        def _teardown(self) -> None:
            if self._log_handle is not None:
                try:
                    self._log_handle.close()
                except OSError:
                    pass
                self._log_handle = None
            if self._kill_timer is not None:
                self._kill_timer.stop()
                self._kill_timer = None

    return GispulseRunner
