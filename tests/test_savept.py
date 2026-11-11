"""savept.icu 死亡站点对照：域名/名称归一化、解析、匹配与配置写入。"""
from pathlib import Path

import pytest

from src.network.savept import (
    DeadSite,
    disable_sites_in_yaml,
    format_report,
    load_local_sites,
    match_local,
    norm_domain,
    norm_name,
    parse_dead_items,
    registrable_domain,
)


def test_norm_domain_strips_scheme_www_port_path():
    assert norm_domain("https://www.pt.cdfile.org/path?x=1") == "pt.cdfile.org"
    assert norm_domain("http://pt.cdfile.org:8080/") == "pt.cdfile.org"
    assert norm_domain("www.52movie.top") == "52movie.top"
    assert norm_domain("") == ""


def test_registrable_domain_takes_last_two_labels():
    assert registrable_domain("pt.cdfile.org") == "cdfile.org"
    assert registrable_domain("yhpp.cc") == "yhpp.cc"
    assert registrable_domain("a.b.c.d.com") == "d.com"


def test_norm_name_folds_pt_suffix_and_whitespace():
    assert norm_name("MomentPT") == "moment"
    assert norm_name("Moment") == "moment"
    assert norm_name(" 传道院 ") == "传道院"
    assert norm_name("SBTracker") == "sb"


def test_parse_dead_items_reads_fields_and_skips_malformed():
    payload = {
        "has_more": False,
        "items": [
            {
                "date": "2026-10-20",
                "description": "忘年桥",
                "event_label": "宣告死亡",
                "name": "PTLAO",
                "url": "https://ptlao.top/",
            },
            {"name": "", "url": ""},  # 空项应跳过
            "not-a-dict",
        ],
    }
    sites = parse_dead_items(payload)
    assert len(sites) == 1
    s = sites[0]
    assert isinstance(s, DeadSite)
    assert s.name == "PTLAO" and s.alias == "忘年桥"
    assert s.dead_date == "2026-10-20" and s.event_label == "宣告死亡"


@pytest.mark.parametrize("payload", [None, [], {"items": "x"}, {"no_items": 1}])
def test_parse_dead_items_handles_bad_shapes(payload):
    assert parse_dead_items(payload) == []


def test_match_local_hits_domain_and_registrable():
    dead = [
        DeadSite(name="CDFile", url="https://pt.cdfile.org/", dead_date="2026-10-20",
                 event_label="宣告死亡"),
        DeadSite(name="PTChina", url="https://ptchina.org/", dead_date="2025-11-10",
                 event_label="意外死亡"),
    ]
    local = [
        {"name": "cdfile", "url": "https://pt.cdfile.org"},   # 域名一致 -> high
        {"name": "铂金学院", "url": "https://www.ptchina.org"},  # 注册域一致 -> high
        {"name": "活站", "url": "https://alive.example"},     # 不匹配
    ]
    matches = match_local(dead, local)
    assert {m.local_name for m in matches} == {"cdfile", "铂金学院"}
    assert all(m.confidence == "high" for m in matches)


def test_match_local_name_alias_match_is_medium_confidence():
    dead = [
        DeadSite(name="CDY", url="https://pt.cdy.pics", dead_date="2026-08-17",
                 event_label="意外死亡", alias="传道院"),
    ]
    local = [{"name": "传道院", "url": "https://pt.cdy.skin"}]  # 域名不同，靠别名
    matches = match_local(dead, local)
    assert len(matches) == 1
    m = matches[0]
    assert m.matched_by == "name" and m.confidence == "medium"


def test_match_local_ignores_unmatched_sites():
    assert match_local([], [{"name": "a", "url": "https://a.example"}]) == []


def test_format_report_states_actions_and_counts():
    empty = format_report([], total_local=5)
    assert "未发现" in empty and "5" in empty

    dead = [DeadSite(name="X", url="https://x.example", dead_date="2026-01-01",
                     event_label="宣告死亡")]
    matches = match_local(dead, [{"name": "x", "url": "https://x.example"}])
    report = format_report(matches, total_local=5)
    assert "1 个命中" in report
    assert "confidence=high" in report and "--apply" in report


def test_load_local_sites_only_returns_name_url_and_skips_disabled(tmp_path):
    p = tmp_path / "sites.yaml"
    p.write_text(
        "sites:\n"
        "  - name: site_alpha\n"
        "    url: https://on.example\n"
        "    passkey: SECRET\n"
        "  - name: site_beta\n"
        "    url: https://off.example\n"
        "    enabled: false\n"
        "    api_key: SECRET2\n",
        encoding="utf-8",
    )
    sites = load_local_sites(str(p), only_enabled=True)
    assert sites == [{"name": "site_alpha", "url": "https://on.example"}]
    # 不泄漏任何凭据字段
    assert all(set(s.keys()) == {"name", "url"} for s in sites)
    # only_enabled=False 时包含已禁用站点
    assert len(load_local_sites(str(p), only_enabled=False)) == 2


def test_load_local_sites_missing_file_raises(tmp_path):
    with pytest.raises(FileNotFoundError):
        load_local_sites(str(tmp_path / "nope.yaml"))


def test_disable_sites_in_yaml_inserts_enabled_false(tmp_path):
    p = tmp_path / "sites.yaml"
    p.write_text(
        "sites:\n"
        "  - name: cdfile\n"
        "    url: https://pt.cdfile.org\n"
        "    passkey: SECRET\n"
        "  - name: alive\n"
        "    url: https://alive.example\n",
        encoding="utf-8",
    )
    changed = disable_sites_in_yaml(str(p), ["cdfile", "alive", "ghost"])
    assert changed == ["alive", "cdfile"]
    text = Path(p).read_text(encoding="utf-8")
    assert "  enabled: false\n" in text
    assert text.count("enabled: false") == 2
    # 原有字段仍在，凭据未被改写
    assert "passkey: SECRET" in text
