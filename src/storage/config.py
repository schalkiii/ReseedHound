from pathlib import Path
from typing import Any

import yaml


class Config:
    def __init__(self, config_path: str = "config.yaml", sites_path: str = "sites.yaml"):
        self._config = self._load_yaml(config_path)
        self._sites = self._load_yaml(sites_path)

    @staticmethod
    def _load_yaml(path: str) -> dict:
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    @property
    def global_config(self) -> dict:
        return self._config.get("global", {})

    @property
    def db_path(self) -> str:
        return self._config.get("db", {}).get("path", "data/cache.db")

    @property
    def log_config(self) -> dict:
        return self._config.get("log", {})

    @property
    def downloader_config(self) -> dict:
        return self._config.get("downloader", {})

    @property
    def feishu_config(self) -> dict:
        return self._config.get("notify", {}).get("feishu", {})

    @property
    def sites(self) -> list[dict]:
        return [
            s for s in self._sites.get("sites", [])
            if s.get("enabled", True) and s.get("passkey")
        ]

    def get(self, key: str, default: Any = None) -> Any:
        keys = key.split(".")
        value = self._config
        for k in keys:
            if isinstance(value, dict):
                value = value.get(k)
                if value is None:
                    return default
            else:
                return default
        return value
