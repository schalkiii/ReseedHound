"""Config：默认值单一来源、字段命名兼容、schema 告警、双下载器解析。"""
import inspect
import logging

import pytest

from src.engine.jackett import JackettClient
from src.network.client import SiteClient
from src.storage.config import DEFAULTS, Config
from src.utils.logger import setup_logger
from tests.helpers import write_yaml


@pytest.fixture
def bare_config(config_files):
    """只写必填下载器信息，其余字段全部走 DEFAULTS。"""
    cfg_path, sites_path, _, _ = config_files
    write_yaml(cfg_path, {"downloader": {"host": "h"}})
    write_yaml(sites_path, {"sites": []})
    return Config(str(cfg_path), str(sites_path))


def test_defaults_single_source(bare_config):
    c = bare_config
    d = DEFAULTS
    assert c.value("global", "api_timeout") == d["global"]["api_timeout"] == 60
    assert c.value("global", "download_timeout") == d["global"]["download_timeout"] == 15
    assert c.value("global", "concurrency") == d["global"]["concurrency"]
    assert c.value("global", "batch_size") == d["global"]["batch_size"]
    assert c.value("global", "download_interval") == d["global"]["download_interval"]
    assert c.value("global", "retry_count") == d["global"]["retry_count"]
    assert c.value("global", "retry_delay") == d["global"]["retry_delay"]
    assert c.db_path == d["db"]["path"]
    assert c.value("log", "file") == d["log"]["file"] == "logs/reseed.log"
    assert c.value("log", "level") == d["log"]["level"]
    assert c.operation_mode == d["global"]["mode"]
    assert c.scheduler_enabled is d["scheduler"]["enabled"]
    assert c.scheduler_run_on_start is d["scheduler"]["run_on_start"]
    assert c.jackett_url == d["jackett"]["url"]
    assert c.jackett_enabled is False
    assert c.cookiecloud_enabled is d["cookiecloud"]["enabled"]
    assert c.downloader_value("tag") == d["downloader"]["tag"]
    assert c.downloader_value("skip_hash_check") is d["downloader"]["skip_hash_check"]
    assert c.downloader_value("auto_start") is d["downloader"]["auto_start"]
    assert c.downloader_value("torrent_dir", role="source") == ""


def test_client_and_logger_defaults_come_from_defaults():
    site = inspect.signature(SiteClient.__init__).parameters
    jackett = inspect.signature(JackettClient.__init__).parameters
    assert site["timeout"].default == DEFAULTS["global"]["api_timeout"]
    assert site["download_timeout"].default == DEFAULTS["global"]["download_timeout"]
    assert site["concurrency"].default == DEFAULTS["global"]["concurrency"]
    assert site["download_interval"].default == DEFAULTS["global"]["download_interval"]
    assert jackett["timeout"].default == DEFAULTS["jackett"]["timeout"]
    assert jackett["concurrency"].default == DEFAULTS["jackett"]["concurrency"]
    assert jackett["search_interval"].default == DEFAULTS["jackett"]["search_interval"]
    assert jackett["retry_count"].default == DEFAULTS["global"]["retry_count"]
    logger_params = inspect.signature(setup_logger).parameters
    assert logger_params["level"].default is None
    assert logger_params["log_file"].default is None


def test_explicit_values_override_defaults(fresh_config):
    assert fresh_config.value("global", "concurrency") == 7
    assert fresh_config.value("global", "batch_size") == 13
    assert fresh_config.value("global", "api_timeout") == 21
    assert fresh_config.value("log", "level") == "DEBUG"
    assert fresh_config.downloader_value("tag") == "UnitTest"
    assert fresh_config.get("global.concurrency") == 7
    assert fresh_config.get("db.path").endswith("cache.db")
    assert fresh_config.get("missing.key", "fallback") == "fallback"


@pytest.mark.parametrize("flags,expected_enabled", [
    ({"enabled": False}, False),
    ({"enable": False}, False),
    ({"enable": False, "enabled": True}, False),  # enable 优先
    ({"enable": True, "enabled": False}, True),
    ({}, True),  # 两者都缺省时默认启用
])
def test_sites_enable_enabled_compat(config_files, flags, expected_enabled):
    cfg_path, sites_path, _, sites = config_files
    sites["sites"] = [{
        "name": "only",
        "url": "https://only.example.com",
        "api_url": "https://only.example.com/api",
        "passkey": "p",
        **flags,
    }]
    write_yaml(sites_path, sites)
    c = Config(str(cfg_path), str(sites_path))
    assert bool(c.sites) is expected_enabled


def test_sites_without_passkey_are_skipped(config_files):
    cfg_path, sites_path, _, sites = config_files
    sites["sites"][0].pop("passkey")
    write_yaml(sites_path, sites)
    assert [s["name"] for s in Config(str(cfg_path), str(sites_path)).sites] == []


@pytest.mark.parametrize("key", ["enable", "enabled"])
def test_cookiecloud_enable_enabled_compat(config_files, key):
    cfg_path, sites_path, cfg, _ = config_files
    cfg["cookiecloud"] = {key: True}
    write_yaml(cfg_path, cfg)
    assert Config(str(cfg_path), str(sites_path)).cookiecloud_enabled is True


def test_unknown_fields_are_warned(config_files, caplog):
    cfg_path, sites_path, cfg, sites = config_files
    cfg["global"]["api_tiemout"] = 60
    cfg["global"]["typo_block"] = {"x": 1}
    sites["sites"][0]["enabeld"] = True
    write_yaml(cfg_path, cfg)
    write_yaml(sites_path, sites)
    with caplog.at_level(logging.WARNING, logger="seedhound"):
        Config(str(cfg_path), str(sites_path))
    assert "global.api_tiemout" in caplog.text
    assert "global.typo_block" in caplog.text
    assert "sites[].enabeld" in caplog.text


def test_dual_downloader_parsing(config_files):
    cfg_path, sites_path, cfg, _ = config_files
    cfg["downloader"] = {
        "source": {
            "type": "qbittorrent", "host": "src", "port": 8080,
            "torrent_dir": "/src/BT_backup",
        },
        "destination": {
            "type": "transmission", "host": "dst", "port": 9091, "tag": "Dst",
        },
    }
    write_yaml(cfg_path, cfg)
    c = Config(str(cfg_path), str(sites_path))
    assert c.is_dual_downloader is True
    assert c.source_downloader_config["host"] == "src"
    assert c.destination_downloader_config["host"] == "dst"
    assert c.downloader_value("torrent_dir", role="source") == "/src/BT_backup"
    assert c.downloader_value("tag") == "Dst"
    assert c.downloader_value("skip_hash_check") is DEFAULTS["downloader"]["skip_hash_check"]


def test_missing_config_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError, match="配置文件不存在"):
        Config(str(tmp_path / "missing.yaml"), str(tmp_path / "sites.yaml"))
