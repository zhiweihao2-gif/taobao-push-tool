"""配置加载：config/settings.yaml 优先，缺失时回退到 settings.example.yaml。"""
from __future__ import annotations

import os
from functools import lru_cache
from pathlib import Path
from typing import Any

import yaml

ROOT = Path(__file__).resolve().parents[2]
CONFIG_DIR = ROOT / "config"


def _deep_merge(base: dict, override: dict) -> dict:
    out = dict(base)
    for k, v in override.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _deep_merge(out[k], v)
        else:
            out[k] = v
    return out


class Settings:
    def __init__(self, raw: dict[str, Any]):
        self.raw = raw

    def __getitem__(self, key: str) -> Any:
        return self.raw[key]

    def get(self, path: str, default: Any = None) -> Any:
        node: Any = self.raw
        for part in path.split("."):
            if not isinstance(node, dict) or part not in node:
                return default
            node = node[part]
        return node

    # 常用路径
    @property
    def data_dir(self) -> Path:
        return self._p("paths.data_dir", "data")

    @property
    def raw_dir(self) -> Path:
        return self._p("paths.raw_dir", "data/raw")

    @property
    def items_dir(self) -> Path:
        return self._p("paths.items_dir", "data/items")

    @property
    def media_dir(self) -> Path:
        return self._p("paths.media_dir", "data/media")

    @property
    def mapping_dir(self) -> Path:
        return self._p("paths.mapping_dir", "data/mapping")

    @property
    def cookie_dir(self) -> Path:
        return self._p("paths.cookie_dir", "config/cookies")

    @property
    def db_path(self) -> Path:
        return self._p("paths.db_path", "data/app.db")

    def _p(self, key: str, default: str) -> Path:
        val = self.get(key, default)
        p = Path(val)
        if not p.is_absolute():
            p = ROOT / p
        return p

    def ensure_dirs(self) -> None:
        for p in (self.data_dir, self.raw_dir, self.items_dir, self.media_dir,
                  self.mapping_dir, self.cookie_dir):
            p.mkdir(parents=True, exist_ok=True)


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    example = CONFIG_DIR / "settings.example.yaml"
    user = CONFIG_DIR / "settings.yaml"
    raw: dict[str, Any] = {}
    if example.exists():
        raw = yaml.safe_load(example.read_text(encoding="utf-8")) or {}
    if user.exists():
        raw = _deep_merge(raw, yaml.safe_load(user.read_text(encoding="utf-8")) or {})
    # 环境变量覆盖数据目录，便于容器化
    if os.getenv("TAOBAO_DATA_DIR"):
        raw.setdefault("paths", {})["data_dir"] = os.environ["TAOBAO_DATA_DIR"]
    s = Settings(raw)
    s.ensure_dirs()
    return s
