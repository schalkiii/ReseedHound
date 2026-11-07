"""pytest 共享 fixture：所有配置均由 tmp_path 现场生成。"""
import sys
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).parent.parent))

from tests.helpers import minimal_config_dicts, write_yaml


@pytest.fixture
def config_files(tmp_path):
    """返回 (config_path, sites_path, config_dict, sites_dict)。

    测试可就地修改 dict 后调用 write_yaml 写回，再重建 Config 验证行为。
    """
    cfg_path = tmp_path / "config.yaml"
    sites_path = tmp_path / "sites.yaml"
    cfg, sites = minimal_config_dicts(tmp_path)
    write_yaml(cfg_path, cfg)
    write_yaml(sites_path, sites)
    return cfg_path, sites_path, cfg, sites


@pytest.fixture
def fresh_config(config_files):
    from src.storage.config import Config

    cfg_path, sites_path, _, _ = config_files
    return Config(str(cfg_path), str(sites_path))
