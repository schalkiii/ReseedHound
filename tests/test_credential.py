"""凭据失效提醒：错误集合语义与飞书卡片推送。"""
import pytest

from src.engine.seeder import CREDENTIAL_ERRORS
from src.notify.feishu import FeishuNotifier


def test_credential_errors_covers_auth_and_permission():
    assert CREDENTIAL_ERRORS == {
        "http_401",
        "http_403",
        "html_permission_denied",
        "html_forbidden",
        "html_auth_required",
        "auth_redirect",
    }
    # 凭据类错误必须都是「严重错误」，否则不会进跳过统计
    assert "http_404" not in CREDENTIAL_ERRORS  # 404 是种子/接口问题，不是凭据
    assert "timeout" not in CREDENTIAL_ERRORS


@pytest.mark.asyncio
async def test_send_credential_alert_lists_sites_with_reason_and_hint():
    notifier = FeishuNotifier("https://hook.example/xxx")
    captured = {}

    async def fake_send(payload):
        captured["payload"] = payload
        return True

    notifier._send = fake_send
    ok = await notifier.send_credential_alert(
        {"1ptba": "http_403", "猪猪": "auth_redirect"}
    )
    assert ok is True

    payload = captured["payload"]
    assert payload["msg_type"] == "interactive"
    card = payload["card"]
    assert card["header"]["template"] == "red"
    content = card["elements"][0]["content"]
    assert "1ptba" in content and "http_403" in content
    assert "猪猪" in content and "auth_redirect" in content
    assert "sync-cookies" in content  # 给出可操作的更新指引
    assert "**2** 个站点" in content


@pytest.mark.asyncio
async def test_send_credential_alert_empty_is_noop():
    notifier = FeishuNotifier("https://hook.example/xxx")

    async def fake_send(payload):
        raise AssertionError("无失效站点时不应推送")

    notifier._send = fake_send
    assert await notifier.send_credential_alert({}) is False
