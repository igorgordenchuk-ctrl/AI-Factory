"""
FileWatcher — polling-based folder monitoring.
More reliable than watchdog on Windows.
"""

import logging
import time
import threading
from pathlib import Path
from typing import Callable

logger = logging.getLogger(__name__)


class PollingWatcher:
    """
    Polls a folder for new .json task card files.
    Calls callback(file_path) for each new file detected.
    Runs in a background thread.
    """

    def __init__(
        self,
        folder_path: str | Path,
        callback: Callable[[Path], None],
        interval: float = 2.0,
        name: str = "Watcher",
    ):
        self.folder_path = Path(folder_path)
        self.callback = callback
        self.interval = interval
        self.name = name
        self._known_files: set[str] = set()
        self._running = False
        self._thread: threading.Thread | None = None

    def _scan(self):
        """Scan folder for new task card files."""
        if not self.folder_path.exists():
            return

        current = set()
        for f in self.folder_path.glob("task-*.json"):
            if not f.name.endswith(".lock"):
                current.add(f.name)

        new_files = current - self._known_files
        for fname in sorted(new_files):
            file_path = self.folder_path / fname
            logger.debug(f"[{self.name}] Новый файл: {fname}")
            try:
                self.callback(file_path)
            except Exception as e:
                logger.error(f"[{self.name}] Ошибка обработки {fname}: {e}")

        self._known_files = current

    def _loop(self):
        """Main polling loop."""
        # Инициализация: запоминаем существующие файлы
        if self.folder_path.exists():
            for f in self.folder_path.glob("task-*.json"):
                if not f.name.endswith(".lock"):
                    self._known_files.add(f.name)

        logger.info(f"[{self.name}] Мониторинг запущен: {self.folder_path}")

        while self._running:
            try:
                self._scan()
            except Exception as e:
                logger.error(f"[{self.name}] Ошибка сканирования: {e}")
            time.sleep(self.interval)

        logger.info(f"[{self.name}] Мониторинг остановлен")

    def start(self):
        """Start watching in a background thread."""
        if self._running:
            return
        self._running = True
        self._thread = threading.Thread(
            target=self._loop,
            name=f"watcher-{self.name}",
            daemon=True,
        )
        self._thread.start()

    def stop(self):
        """Stop the watcher."""
        self._running = False
        if self._thread:
            self._thread.join(timeout=5)
            self._thread = None

    @property
    def is_running(self) -> bool:
        return self._running


class MultiWatcher:
    """Convenience class to manage multiple folder watchers."""

    def __init__(self):
        self._watchers: list[PollingWatcher] = []

    def add(
        self,
        folder: str | Path,
        callback: Callable[[Path], None],
        interval: float = 2.0,
        name: str = "Watcher",
    ) -> PollingWatcher:
        watcher = PollingWatcher(folder, callback, interval, name)
        self._watchers.append(watcher)
        return watcher

    def start_all(self):
        """Start all watchers."""
        for w in self._watchers:
            w.start()

    def stop_all(self):
        """Stop all watchers."""
        for w in self._watchers:
            w.stop()
