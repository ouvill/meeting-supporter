import os
import tomllib
from collections.abc import Generator
from contextlib import contextmanager
from dataclasses import dataclass, field
from pathlib import Path
from threading import RLock
from typing import TypeVar, cast

from app.core.types import TomlTable
from app.services._file_utils import atomic_write_text

_T = TypeVar("_T")


@dataclass
class SettingsStore:
    config_path: Path
    default_config_path: Path
    _write_lock: RLock = field(default_factory=RLock, init=False, repr=False)

    @contextmanager
    def locked(self) -> Generator[None, None, None]:
        """Serialize a load/merge/write transaction across settings writers."""
        with self._write_lock:
            yield

    @staticmethod
    def _read_toml(path: Path) -> TomlTable:
        if not path.exists():
            return {}
        with open(path, "rb") as f:
            data = tomllib.load(f)
        return cast(TomlTable, data)

    def load_config(self) -> TomlTable:
        path = self.config_path if self.config_path.exists() else self.default_config_path
        return self._read_toml(path)

    @staticmethod
    def cfg_get(cfg: TomlTable, section: str, key: str, default: _T) -> _T:
        env_key = f"{section.upper()}_{key.upper()}"
        env_val = os.getenv(env_key)
        if env_val is not None:
            if isinstance(default, bool):
                return cast(_T, env_val.lower() in ("true", "1", "yes"))
            if isinstance(default, float):
                return cast(_T, float(env_val))
            if isinstance(default, int):
                return cast(_T, int(env_val))
            return cast(_T, env_val)
        section_data = cfg.get(section)
        if isinstance(section_data, dict):
            return cast(_T, section_data.get(key, default))
        return default

    @staticmethod
    def write_sectioned_toml(path: Path, data: TomlTable) -> None:
        lines = ["# 会議支援AI 設定ファイル"]
        for section, values in data.items():
            if isinstance(values, list):
                for item in values:
                    if not isinstance(item, dict):
                        continue
                    lines.append(f"\n[[{section}]]")
                    for key, value in item.items():
                        formatted = SettingsStore._format_toml_value(value)
                        if formatted is not None:
                            lines.append(f"{key} = {formatted}")
                continue
            if not isinstance(values, dict):
                continue
            lines.append(f"\n[{section}]")
            scalar_items: list[tuple[str, object]] = []
            table_arrays: list[tuple[str, list[object]]] = []
            for key, value in values.items():
                if isinstance(value, list):
                    formatted = SettingsStore._format_toml_value(value)
                    if formatted is not None:
                        scalar_items.append((key, value))
                    else:
                        table_arrays.append((key, cast(list[object], value)))
                    continue
                scalar_items.append((key, value))
            for key, value in scalar_items:
                formatted = SettingsStore._format_toml_value(value)
                if formatted is not None:
                    lines.append(f"{key} = {formatted}")
            for key, items in table_arrays:
                for item in items:
                    if not isinstance(item, dict):
                        continue
                    lines.append(f"\n[[{section}.{key}]]")
                    for item_key, item_value in cast(dict[str, object], item).items():
                        formatted = SettingsStore._format_toml_value(item_value)
                        if formatted is not None:
                            lines.append(f"{item_key} = {formatted}")
        content = "\n".join(lines) + "\n"
        atomic_write_text(path, content)

    @staticmethod
    def _format_toml_value(value: object) -> str | None:
        if isinstance(value, str):
            escaped = (
                value.replace("\\", "\\\\")
                .replace('"', '\\"')
                .replace("\n", "\\n")
                .replace("\t", "\\t")
                .replace("\r", "\\r")
            )
            return f'"{escaped}"'
        if isinstance(value, bool):
            return "true" if value else "false"
        if isinstance(value, int) and not isinstance(value, bool):
            return str(value)
        if isinstance(value, float):
            return str(value)
        if isinstance(value, list):
            items = cast(list[object], value)
            if all(
                isinstance(item, str)
                or isinstance(item, bool)
                or (isinstance(item, int) and not isinstance(item, bool))
                or isinstance(item, float)
                for item in items
            ):
                formatted_items = [SettingsStore._format_toml_value(item) for item in items]
                if all(item is not None for item in formatted_items):
                    return "[" + ", ".join(cast(list[str], formatted_items)) + "]"
        return None
