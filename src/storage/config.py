"""配置加载与默认值单一来源。

DEFAULTS 是 config.yaml / sites.yaml 中所有可缺省字段的唯一权威定义。
业务模块不得再自行硬编码缺省数值，一律通过 Config.value() /
Config.downloader_value() 或本模块的 DEFAULTS 取值。
"""
import logging
from pathlib import Path
from typing import Any

import yaml

logger = logging.getLogger("seedhound")

DEFAULTS: dict[str, dict[str, Any]] = {
    "global": {
        "concurrency": 20,
        "batch_size": 100,
        "api_timeout": 60,
        "download_timeout": 15,
        "download_interval": 5.0,
        "retry_count": 2,
        "retry_delay": 2.0,
        "mode": "pieces_hash",
    },
    "db": {"path": "data/cache.db"},
    "log": {"level": "INFO", "file": "logs/reseed.log"},
    "scheduler": {
        "enabled": False,
        "interval_minutes": 360,
        "startup_delay": 0,
        "run_on_start": True,
    },
    # 只放行为型缺省值；host/port/username/password 属于必填连接信息，
    # 不给默认值，缺失时由 create_downloader 抛出可读错误。
    "downloader": {
        "torrent_dir": "",
        "skip_hash_check": True,
        "auto_start": True,
        "tag": "SeedHound",
    },
    "jackett": {
        "enabled": False,
        "url": "http://localhost:9117",
        "api_key": "",
        "concurrency": 5,
        "search_interval": 1.0,
        "timeout": 30,
    },
    "cookiecloud": {
        "enabled": False,
        "url": "http://127.0.0.1:8082/cookie",
    },
    # HDArea REST API 读取限流 60 次/分。每批 200 个 info_hash 是接口硬约束，
    # 不暴露为可配置项，见 src/network/hdarea.py 的 MAX_HASHES_PER_REQUEST。
    "hdarea": {
        "request_interval": 1.1,
        "concurrency": 2,
    },
    # savept.icu（PT监护室）死亡站点对照：仅 check-dead 命令使用
    "savept": {
        "url": "https://savept.icu",
        "timeout": 20.0,
    },
    "sites": {"enabled": True},
}

# 各配置块允许出现的字段（含必填项）。未知字段只告警、不阻断，用于暴露拼写错误。
KNOWN_KEYS: dict[str, frozenset[str]] = {
    "global": frozenset(DEFAULTS["global"]),
    "db": frozenset(DEFAULTS["db"]),
    "log": frozenset(DEFAULTS["log"]),
    "scheduler": frozenset(DEFAULTS["scheduler"]),
    "downloader": frozenset({
        *DEFAULTS["downloader"],
        "type", "host", "port", "username", "password",
        "source", "destination",
    }),
    "notify": frozenset({"feishu"}),
    "feishu": frozenset({"webhook_url", "secret"}),
    "jackett": frozenset({
        *DEFAULTS["jackett"], "retry_count", "retry_delay",
    }),
    "cookiecloud": frozenset({*DEFAULTS["cookiecloud"], "enable", "uuid", "password"}),
    "hdarea": frozenset(DEFAULTS["hdarea"]),
    "savept": frozenset(DEFAULTS["savept"]),
}

KNOWN_SITE_KEYS = frozenset({
    "name", "url", "api_url", "passkey",
    "api_type", "api_key",
    "enabled", "enable",
    "cookie", "cookies", "download_interval",
})


class Config:
    def __init__(
        self, config_path: str = "config.yaml", sites_path: str = "sites.yaml"
    ):
        self._config = self._load_yaml(config_path)
        self._sites_path = sites_path
        self._sites = self._load_yaml(sites_path)
        self._validate()

    @staticmethod
    def _load_yaml(path: str) -> dict:
        file_path = Path(path)
        if not file_path.exists():
            raise FileNotFoundError(f"配置文件不存在: {file_path}")
        with open(file_path, "r", encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def _validate(self) -> None:
        """汇总并告警未识别字段，避免拼错的开关被静默回退到默认值。"""
        unknown: set[str] = set()
        for section, body in self._config.items():
            if not isinstance(body, dict):
                continue
            allowed = KNOWN_KEYS.get(section)
            if allowed is None:
                unknown.add(f"配置块 {section}")
                continue
            for key in body:
                if key not in allowed:
                    unknown.add(f"{section}.{key}")
            if section == "downloader":
                for role in ("source", "destination"):
                    sub = body.get(role)
                    if isinstance(sub, dict):
                        for key in sub:
                            if key not in allowed:
                                unknown.add(f"downloader.{role}.{key}")
            if section == "notify":
                feishu = body.get("feishu")
                if isinstance(feishu, dict):
                    for key in feishu:
                        if key not in KNOWN_KEYS["feishu"]:
                            unknown.add(f"notify.feishu.{key}")

        for site in self._sites.get("sites", []):
            if isinstance(site, dict):
                for key in site:
                    if key not in KNOWN_SITE_KEYS:
                        unknown.add(f"sites[].{key}")

        if unknown:
            logger.warning(
                "配置中发现未识别字段（将被忽略，请检查拼写）: %s",
                ", ".join(sorted(unknown)),
            )

    def value(self, section: str, key: str, fallback: Any = None) -> Any:
        """读取 section.key；未配置或显式为 null 时回退 DEFAULTS，再回退 fallback。"""
        body = self._config.get(section)
        if isinstance(body, dict):
            configured = body.get(key)
            if configured is not None:
                return configured
        return DEFAULTS.get(section, {}).get(key, fallback)

    def downloader_value(self, key: str, role: str = "destination") -> Any:
        """读取下载器子块字段；单/双下载器统一入口，缺省回退 DEFAULTS。"""
        cfg = (
            self.source_downloader_config
            if role == "source"
            else self.destination_downloader_config
        )
        configured = cfg.get(key)
        return DEFAULTS["downloader"][key] if configured is None else configured

    @property
    def global_config(self) -> dict:
        return self._config.get("global", {})

    @property
    def db_path(self) -> str:
        return self.value("db", "path")

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
        return bool(self.value("jackett", "enabled")) and bool(
            self.value("jackett", "api_key")
        )

    @property
    def jackett_url(self) -> str:
        return self.value("jackett", "url")

    @property
    def jackett_api_key(self) -> str:
        return self.value("jackett", "api_key")

    @property
    def operation_mode(self) -> str:
        return self.value("global", "mode")

    @property
    def scheduler_config(self) -> dict:
        return self._config.get("scheduler", {}) or {}

    @property
    def scheduler_enabled(self) -> bool:
        return bool(self.value("scheduler", "enabled"))

    @property
    def scheduler_interval_seconds(self) -> float:
        minutes = float(self.value("scheduler", "interval_minutes"))
        return max(1.0, minutes * 60)

    @property
    def scheduler_startup_delay(self) -> float:
        return max(0.0, float(self.value("scheduler", "startup_delay")))

    @property
    def scheduler_run_on_start(self) -> bool:
        return bool(self.value("scheduler", "run_on_start"))

    @property
    def sites(self) -> list[dict]:
        result = []
        for s in self._sites.get("sites", []):
            # pieces_hash 通道用 passkey，HDArea 的 info_hash 通道用 api_key；
            # 两者皆无的站点没有可用凭据，静默跳过。
            if not isinstance(s, dict) or not (
                s.get("passkey") or s.get("api_key")
            ):
                continue
            # enable 优先（CHANGELOG 约定的历史写法），否则读 enabled；
            # 两者都缺省时默认启用。两种拼写都有效，避免静默参与辅种。
            if "enable" in s:
                is_enabled = bool(s["enable"])
            else:
                is_enabled = bool(s.get("enabled", DEFAULTS["sites"]["enabled"]))
            if is_enabled:
                result.append(s)
        return result

    @property
    def sites_path(self) -> str:
        return self._sites_path

    def reload_sites(self) -> None:
        """重新从磁盘加载 sites 配置（CookieCloud 同步写回后调用，使后续引擎使用新 Cookie）。"""
        self._sites = self._load_yaml(self._sites_path)

    @property
    def cookiecloud_config(self) -> dict:
        return self._config.get("cookiecloud", {}) or {}

    @property
    def cookiecloud_enabled(self) -> bool:
        # 新配置统一用 enabled；兼容历史 enable 拼写且保持其优先级
        cc = self.cookiecloud_config
        if "enable" in cc:
            return bool(cc["enable"])
        return bool(self.value("cookiecloud", "enabled"))

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
