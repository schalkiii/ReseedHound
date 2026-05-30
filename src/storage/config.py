from pathlib import Path
from typing import Any

import yaml


class Config:
    def __init__(
        self, config_path: str = "config.yaml", sites_path: str = "sites.yaml"
    ):
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
    def source_downloader_config(self) -> dict:
        dl = self.downloader_config
        if "source" in dl:
            return dl["source"]
        return dl

    @property
    def destination_downloader_config(self) -> dict:
        dl = self.downloader_config
        if "destination" in dl:
            return dl["destination"]
        return dl

    @property
    def is_dual_downloader(self) -> bool:
        dl = self.downloader_config
        return "source" in dl and "destination" in dl

    @property
    def feishu_config(self) -> dict:
        return self._config.get("notify", {}).get("feishu", {})

    @property
    def jackett_config(self) -> dict:
        return self._config.get("jackett", {})

    @property
    def jackett_enabled(self) -> bool:
        jc = self.jackett_config
        return jc.get("enabled", False) and bool(jc.get("api_key"))

    @property
    def jackett_url(self) -> str:
        return self.jackett_config.get("url", "http://localhost:9117")

    @property
    def jackett_api_key(self) -> str:
        return self.jackett_config.get("api_key", "")

    @property
    def operation_mode(self) -> str:
        return self._config.get("global", {}).get("mode", "pieces_hash")

    @property
    def sites(self) -> list[dict]:
        return [
            s
            for s in self._sites.get("sites", [])
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
