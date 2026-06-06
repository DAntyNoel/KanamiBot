from __future__ import annotations

import atexit
import json
import logging
import os
import shutil
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

from .paths import DATA_DIR

logger = logging.getLogger(__name__)

DATA = DATA_DIR / "plugin_configs"
AutoClearStrategy = Literal["daily", "weekly", "monthly"] | None


class ConfigUnit:
    """一个插件配置单元，负责 JSON 读写、备份和周期性重置。"""

    def __init__(
        self,
        name: str,
        file_path: Path,
        backup_dir: Path,
        default_data: dict[str, Any],
        auto_clear_strategy: AutoClearStrategy,
        max_backups: int,
    ) -> None:
        self.name = name
        self.file_path = file_path
        self.backup_dir = backup_dir
        self.default_data = default_data
        self.auto_clear_strategy = auto_clear_strategy
        self.max_backups = max_backups
        self.meta_path = self.file_path.with_suffix(".meta.json")

        self._lock = threading.RLock()
        self._data: dict[str, Any] = {}

        self._init_storage()
        self.reload()
        self._last_reset_key = self._load_meta_reset_key()

    def _init_storage(self) -> None:
        self.file_path.parent.mkdir(parents=True, exist_ok=True)
        self.backup_dir.mkdir(parents=True, exist_ok=True)

        if not self.file_path.exists():
            with self._lock:
                self._atomic_write(self.default_data.copy())

    def _load_meta_reset_key(self) -> str:
        try:
            if self.meta_path.exists():
                with self.meta_path.open("r", encoding="utf-8") as file:
                    return json.load(file).get("last_reset_key", "")
        except (OSError, json.JSONDecodeError):
            logger.warning("[%s] Failed to load reset metadata.", self.name)
        return ""

    def _save_meta_reset_key(self, key: str) -> None:
        try:
            self._atomic_write_to_path(self.meta_path, {"last_reset_key": key})
        except OSError as exc:
            logger.error("[%s] Failed to save reset metadata: %s", self.name, exc)

    def _atomic_write(self, data: dict[str, Any]) -> None:
        self._atomic_write_to_path(self.file_path, data)

    @staticmethod
    def _atomic_write_to_path(path: Path, data: dict[str, Any]) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        temp_file = path.with_suffix(f".tmp.{uuid.uuid4()}")
        try:
            with temp_file.open("w", encoding="utf-8") as file:
                json.dump(data, file, indent=4, ensure_ascii=False)
            os.replace(temp_file, path)
        except Exception:
            temp_file.unlink(missing_ok=True)
            raise

    def get(
        self,
        key: str | None = None,
        default: Any = None,
        _require_reload: bool = False,
    ) -> Any:
        with self._lock:
            if _require_reload:
                self.reload()

            if key is None:
                return self._data.copy()
            return self._data.get(key, default)

    def set(self, key: str, value: Any) -> None:
        with self._lock:
            self._data[key] = value
            self._atomic_write(self._data)

    def set_self(self, data: dict[str, Any]) -> None:
        with self._lock:
            self._data = data
            self._atomic_write(self._data)

    def update(self, new_data: dict[str, Any]) -> None:
        with self._lock:
            self._data.update(new_data)
            self._atomic_write(self._data)

    def reload(self) -> None:
        with self._lock:
            try:
                with self.file_path.open("r", encoding="utf-8") as file:
                    loaded = json.load(file)
            except (json.JSONDecodeError, FileNotFoundError):
                self._data = {}
                return

            self._data = loaded if isinstance(loaded, dict) else {}

    def maintenance(self) -> None:
        if self.auto_clear_strategy:
            self._check_and_perform_auto_clear()

    def _check_and_perform_auto_clear(self) -> None:
        now = datetime.now()
        current_key = ""

        if self.auto_clear_strategy == "daily":
            current_key = now.strftime("%Y-%m-%d")
        elif self.auto_clear_strategy == "weekly":
            current_key = now.strftime("%Y-W%U")
        elif self.auto_clear_strategy == "monthly":
            current_key = now.strftime("%Y-%m")

        if not current_key or current_key == self._last_reset_key:
            return

        with self._lock:
            if current_key == self._last_reset_key:
                return

            logger.warning(
                "[%s] Triggering auto clear (%s): %s",
                self.name,
                self.auto_clear_strategy,
                current_key,
            )
            self._perform_backup(reason="pre_clear_snapshot")
            self._data = self.default_data.copy()
            self._atomic_write(self._data)
            self._last_reset_key = current_key
            self._save_meta_reset_key(current_key)
            self._perform_cleanup_backups()

    def _perform_backup(self, reason: str = "scheduled") -> None:
        if not self.file_path.exists():
            logger.warning("[%s] Backup skipped because config file does not exist.", self.name)
            return

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        backup_name = f"{self.name}_{reason}_{timestamp}.json"
        dest_path = self.backup_dir / backup_name

        try:
            self.backup_dir.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.file_path, dest_path)
            logger.info("[%s] Backup created: %s", self.name, backup_name)
        except OSError as exc:
            logger.error("[%s] Backup failed: %s", self.name, exc)

    def _perform_cleanup_backups(self) -> None:
        try:
            backups = sorted(self.backup_dir.glob(f"{self.name}_*.json"), key=os.path.getmtime)
            if len(backups) > self.max_backups:
                for file in backups[: -self.max_backups]:
                    file.unlink(missing_ok=True)
        except OSError as exc:
            logger.error("[%s] Cleanup backups error: %s", self.name, exc)


class CentralConfigManager:
    """中央配置管理器，负责分发 ConfigUnit 并维护定时备份线程。"""

    _instance: CentralConfigManager | None = None
    _lock = threading.Lock()

    def __new__(cls, *args: Any, **kwargs: Any) -> CentralConfigManager:
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = super().__new__(cls)
                    cls._instance._initialized = False
        return cls._instance

    def __init__(self, root_dir: Path = DATA) -> None:
        if self._initialized:
            return

        self.root_dir = Path(root_dir)
        self.modules: dict[str, ConfigUnit] = {}
        self.global_lock = threading.RLock()
        self._stop_event = threading.Event()
        self._worker_thread = threading.Thread(
            target=self._background_loop,
            daemon=True,
            name="ConfigManagerDaemon",
        )
        self._worker_thread.start()
        atexit.register(self.stop)

        self._initialized = True
        logger.info("CentralConfigManager initialized at %s", self.root_dir)

    @classmethod
    def get_instance(cls) -> CentralConfigManager:
        return cls()

    def get_config(
        self,
        module_name: str,
        default_config: dict[str, Any] | None = None,
        filename: str | None = None,
        auto_clear_strategy: AutoClearStrategy = None,
        max_backups: int = 24,
    ) -> ConfigUnit:
        default_data = default_config.copy() if default_config is not None else {}
        config_filename = filename or f"{module_name}.json"

        with self.global_lock:
            if module_name in self.modules:
                return self.modules[module_name]

            unit = ConfigUnit(
                name=module_name,
                file_path=self.root_dir / config_filename,
                backup_dir=self.root_dir / "backups" / module_name,
                default_data=default_data,
                auto_clear_strategy=auto_clear_strategy,
                max_backups=max_backups,
            )
            self.modules[module_name] = unit
            logger.info("Module '%s' registered.", module_name)
            return unit

    def _background_loop(self) -> None:
        while not self._stop_event.is_set():
            now = datetime.now()
            current_hour = now.hour
            current_time_key = now.strftime("%Y-%m-%d-%H")
            last_backup_path = self.root_dir / "backups" / "LAST_BACKUP_TIME"

            try:
                last_backup_time_key = last_backup_path.read_text(encoding="utf-8").strip()
            except FileNotFoundError:
                last_backup_time_key = ""

            with self.global_lock:
                units = list(self.modules.values())

            should_backup = (
                current_hour in {0, 6, 12, 16, 18}
                and current_time_key != last_backup_time_key
                and bool(units)
            )

            for unit in units:
                try:
                    unit.maintenance()
                    if should_backup:
                        unit._perform_backup()
                except Exception as exc:
                    logger.error("Maintenance/backup error in %s: %s", unit.name, exc)

            if should_backup:
                last_backup_path.parent.mkdir(parents=True, exist_ok=True)
                last_backup_path.write_text(current_time_key, encoding="utf-8")
                logger.info(
                    "[CentralConfigManager] Scheduled backup completed for %s:00:00",
                    current_hour,
                )

            self._stop_event.wait(1)

    def stop(self) -> None:
        if self._stop_event.is_set():
            return

        self._stop_event.set()
        if self._worker_thread.is_alive():
            self._worker_thread.join(timeout=2)
        logger.info("CentralConfigManager stopped.")


ConfigManager = CentralConfigManager()
