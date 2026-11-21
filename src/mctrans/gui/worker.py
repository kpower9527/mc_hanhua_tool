"""在后台线程中运行服务层函数，通过 Qt 信号把日志、进度和结果送回界面线程。"""
from __future__ import annotations

import inspect
import threading
import traceback

from PySide6.QtCore import QObject, QThread, Signal, Slot

from ..service import Reporter, ServiceError


class _SignalReporter(Reporter):
    def __init__(self, job: "_Job", cancel: threading.Event) -> None:
        self._job = job
        self._cancel = cancel

    def log(self, text: str, level: str = "info") -> None:
        self._job.log.emit(text, level)

    def progress(self, done: int, total: int) -> None:
        self._job.progress.emit(done, total)

    def cancelled(self) -> bool:
        return self._cancel.is_set()


class _Job(QObject):
    log = Signal(str, str)
    progress = Signal(int, int)
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, args, kwargs, cancel: threading.Event) -> None:
        super().__init__()
        self.fn, self.args, self.kwargs, self.cancel = fn, args, kwargs, cancel

    @Slot()
    def run(self) -> None:
        kwargs = dict(self.kwargs)
        try:
            if "reporter" in inspect.signature(self.fn).parameters:
                kwargs["reporter"] = _SignalReporter(self, self.cancel)
        except (TypeError, ValueError):
            pass
        try:
            result = self.fn(*self.args, **kwargs)
        except ServiceError as exc:
            self.failed.emit(str(exc))
        except Exception as exc:  # 未预料的错误：带上堆栈写进日志，方便排查
            self.log.emit(traceback.format_exc(), "error")
            self.failed.emit(f"发生意外错误：{type(exc).__name__}: {exc}")
        else:
            self.finished.emit(result)


class TaskRunner(QObject):
    """同一时间只运行一个后台任务。"""

    log = Signal(str, str)
    progress = Signal(int, int)
    finished = Signal(object)
    failed = Signal(str)
    busy_changed = Signal(bool)

    def __init__(self, parent: QObject | None = None) -> None:
        super().__init__(parent)
        self._thread: QThread | None = None
        self._job: _Job | None = None
        self._cancel = threading.Event()
        self.task_name = ""

    @property
    def busy(self) -> bool:
        return self._thread is not None

    def start(self, name: str, fn, *args, **kwargs) -> bool:
        if self.busy:
            return False
        self.task_name = name
        self._cancel.clear()
        thread = QThread()
        job = _Job(fn, args, kwargs, self._cancel)
        job.moveToThread(thread)
        thread.started.connect(job.run)
        job.log.connect(self.log)
        job.progress.connect(self.progress)
        job.finished.connect(self._on_finished)
        job.failed.connect(self._on_failed)
        self._thread, self._job = thread, job
        self.busy_changed.emit(True)
        thread.start()
        return True

    def cancel(self) -> None:
        self._cancel.set()

    def wait(self, ms: int = 30000) -> None:
        if self._thread is not None:
            self._thread.wait(ms)

    def _cleanup(self) -> None:
        thread = self._thread
        self._thread, self._job = None, None
        if thread is not None:
            thread.quit()
            thread.wait()
            thread.deleteLater()
        self.busy_changed.emit(False)

    @Slot(object)
    def _on_finished(self, result) -> None:
        self._cleanup()
        self.finished.emit(result)

    @Slot(str)
    def _on_failed(self, message: str) -> None:
        self._cleanup()
        self.failed.emit(message)
