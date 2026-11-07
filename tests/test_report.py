"""报告：文本/Markdown/飞书卡片共用站点表口径、飞书签名、结构化断言。"""
import pytest

import src.notify.feishu as feishu_module
from src.engine.seeder import ReseedStats
from src.notify.feishu import FeishuNotifier
from src.report.generator import ReportGenerator
from src.report.site_table import (
    MAX_FAILED_SITES_DISPLAY,
    build_site_rows,
    failed_sites_display,
)

FEISHU_GOLDEN_SIGN = "l1N0gAcBjdwBvGm1xMjOF0XSyaLRpR7tuO5dHfhAYc8="


def _sample_stats() -> ReseedStats:
    return ReseedStats(
        total_torrents=100,
        new_torrents=40,
        duplicate_count=10,
        matched_count=25,
        succeeded_count=8,
        failed_count=3,
        tracker_skip_count=5,
        dead_count=2,
        site_details={"alpha": 50, "beta": 30, "gamma": 0, "delta": 10},
        site_match_counts={"alpha": 50, "beta": 30, "gamma": 20, "delta": 10},
        site_succeeded={"alpha": 8},
        site_failed={"beta": 3, "epsilon": 1},
        failed_sites=["gamma", "delta"],
        start_time=0.0,
        end_time=65.0,
    )


def test_report_numbers_are_structurally_checked():
    report = ReportGenerator().generate(_sample_stats())
    assert "运行耗时: 1分5秒" in report
    assert "共匹配 25 个种子可跨站辅种" in report
    summary = [line for line in report.splitlines() if "站点汇总" in line][0]
    assert "启用 4 个" in summary
    assert "有匹配 3 个" in summary
    assert "无匹配 1 个" in summary
    assert "无匹配站点 (2 个): gamma, delta" in report


def test_markdown_table_matches_shared_rows():
    stats = _sample_stats()
    rows = build_site_rows(stats)
    assert [r.name for r in rows] == ["alpha", "beta", "gamma", "delta", "epsilon"]
    lines = [
        line for line in ReportGenerator().generate_markdown(stats).splitlines()
        if line.startswith("| ") and "---" not in line
    ]
    assert lines[0] == "| 站点 | 匹配 | 成功 | 失败 |"
    assert len(lines) - 1 == len(rows)
    for line, row in zip(lines[1:], rows):
        assert line == (
            f"| {row.name} | {row.matched} | {row.succeeded} | {row.failed} |"
        )


def test_feishu_card_matches_shared_rows_and_icons():
    stats = _sample_stats()
    rows = build_site_rows(stats)
    assert [r.status_icon for r in rows] == ["🟢", "🔵", "🔵", "🔵", "⚪"]
    elements = FeishuNotifier("https://x")._build_card_elements(stats)
    table = [
        e["content"] for e in elements
        if e["tag"] == "markdown" and "站点详情" in e["content"]
    ][0]
    lines = [
        line for line in table.splitlines()
        if line.startswith("| ")
        and ":---:" not in line
        and not line.startswith("| 状态")
    ]
    assert len(lines) == len(rows)
    for line, row in zip(lines, rows):
        assert line == (
            f"| {row.status_icon} | {row.name} | {row.matched} | "
            f"{row.succeeded} | {row.failed} |"
        )


def test_failed_sites_display_truncation():
    stats = _sample_stats()
    stats.failed_sites = [f"s{i}" for i in range(14)]
    count, text = failed_sites_display(stats)
    assert count == 14
    assert text.startswith("s0, s1,")
    assert text.endswith("等 14 个")
    assert len(text.split(",")) == MAX_FAILED_SITES_DISPLAY

    report = ReportGenerator().generate(stats)
    assert "无匹配站点 (14 个)" in report
    assert "等 14 个" in report
    elements = FeishuNotifier("https://x")._build_card_elements(stats)
    notes = " ".join(
        e["elements"][0]["content"] for e in elements if e["tag"] == "note"
    )
    assert "等 14 个" in notes


def test_feishu_sign_matches_official_golden():
    assert FeishuNotifier._gen_sign("1599360473", "demo") == FEISHU_GOLDEN_SIGN


@pytest.mark.asyncio
async def test_feishu_payload_signature_and_no_secret_path(monkeypatch):
    captured = {}

    class Resp:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        async def json(self):
            return {"code": 0}

    class Session:
        async def __aenter__(self):
            return self

        async def __aexit__(self, *_):
            return False

        def post(self, url, **kwargs):
            captured.update(kwargs)
            return Resp()

    monkeypatch.setattr(feishu_module.aiohttp, "ClientSession", lambda: Session())
    monkeypatch.setattr(feishu_module.time, "time", lambda: 1599360473)

    signed = FeishuNotifier("https://x", secret="demo")
    assert await signed._send({"msg_type": "interactive"}) is True
    payload = captured["json"]
    assert payload["timestamp"] == "1599360473"
    assert payload["sign"] == FEISHU_GOLDEN_SIGN

    unsigned = FeishuNotifier("https://x")
    assert await unsigned._send({"msg_type": "interactive"}) is True
    assert "sign" not in captured["json"]
    assert "timestamp" not in captured["json"]


def test_feishu_from_config():
    assert FeishuNotifier.from_config({}) is None
    assert FeishuNotifier.from_config({"webhook_url": ""}) is None
    notifier = FeishuNotifier.from_config({"webhook_url": "https://open.feishu.cn/test"})
    assert notifier is not None
    assert notifier._webhook_url == "https://open.feishu.cn/test"
