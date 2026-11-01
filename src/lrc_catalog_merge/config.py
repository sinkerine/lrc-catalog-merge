"""YAML configuration.

```yaml
sources:
  - /nas/backups/Lightroom
  - /nas/backups/Lightroom-asako
outputs:
  - path: /nas/backups/Lightroom
    exclude_folders:
      - C:/Users/asako/Pictures/
  - path: /nas/backups/Lightroom-asako
```

``sources`` are merged in order (the first is the base). Each output gets
the merged catalog without the photos under its ``exclude_folders``
(Lightroom folder paths, matched case-insensitively with subfolders).
Without ``outputs``, every source is an output.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml


@dataclass(frozen=True)
class Output:
    path: Path
    exclude_folders: tuple[str, ...] = ()


@dataclass
class Config:
    sources: list[Path] = field(default_factory=list)
    outputs: list[Output] = field(default_factory=list)


class ConfigError(ValueError):
    pass


def default_path() -> Path:
    base = os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config"
    return Path(base) / "lrc-catalog-merge" / "config.yaml"


def _path(value, where: str) -> Path:
    if not isinstance(value, str) or not value:
        raise ConfigError(f"{where}: expected a path string")
    return Path(value).expanduser()


def load(path: Path) -> Config:
    with open(path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    if not isinstance(data, dict):
        raise ConfigError(f"{path}: expected a mapping at the top level")
    unknown = set(data) - {"sources", "outputs"}
    if unknown:
        raise ConfigError(f"{path}: unknown keys: {', '.join(sorted(unknown))}")

    sources = data.get("sources") or []
    if not isinstance(sources, list):
        raise ConfigError(f"{path}: sources must be a list")
    config = Config(sources=[_path(s, f"{path}: sources[{i}]") for i, s in enumerate(sources)])

    outputs = data.get("outputs") or []
    if not isinstance(outputs, list):
        raise ConfigError(f"{path}: outputs must be a list")
    for i, entry in enumerate(outputs):
        where = f"{path}: outputs[{i}]"
        if isinstance(entry, str):
            entry = {"path": entry}
        if not isinstance(entry, dict):
            raise ConfigError(f"{where}: expected a path or a mapping")
        unknown = set(entry) - {"path", "exclude_folders"}
        if unknown:
            raise ConfigError(f"{where}: unknown keys: {', '.join(sorted(unknown))}")
        excludes = entry.get("exclude_folders") or []
        if not isinstance(excludes, list) or not all(isinstance(e, str) and e for e in excludes):
            raise ConfigError(f"{where}: exclude_folders must be a list of paths")
        config.outputs.append(Output(_path(entry.get("path"), f"{where}.path"), tuple(excludes)))
    return config
