import base64
import hashlib
import hmac
import json
import logging
import time
from typing import Optional

import aiohttp

from ..report.site_table import build_site_rows, failed_sites_display

logger = logging.getLogger("seedhound")


class FeishuNotifier:

    def __init__(self, webhook_url: str, secret: Optional[str] = None):
        self._webhook_url = webhook_url
        self._secret = secret

    async def send_report_card(self, stats) -> bool:
        elements = self._build_card_elements(stats)
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": "SeedHound 辅种报告",
                    },
                    "template": "blue",
                },
                "elements": elements,
            },
        }
        return await self._send(payload)

    def _build_card_elements(self, stats) -> list[dict]:
        elements = []

        unique_count = stats.total_torrents - stats.duplicate_count

        if stats.succeeded_count > 0:
            status_icon = "✅"
        elif stats.matched_count > 0:
            status_icon = "⚠️"
        else:
            status_icon = "❌"

        elements.append({
            "tag": "markdown",
            "content": (
                f"**{{0}} 总览**\n\n"
                f"🟢 辅种成功　**{stats.succeeded_count}**　　　　"
                f"🔴 下载失败　**{stats.failed_count}**\n"
                f"🎯 全站匹配　**{stats.matched_count}**　　　　"
                f"⏭️ 追踪器跳过 **{stats.tracker_skip_count}**\n"
                f"📦 扫描种子　**{stats.total_torrents}**　　　　"
                f"🔄 去重　　　**{stats.duplicate_count}**\n"
                f"🆕 新增种子　**{stats.new_torrents}**　　　　　"
                f"📌 唯一种子　**{unique_count}**"
            ).format(status_icon),
        })

        rows = build_site_rows(stats)
        if rows:
            elements.append({"tag": "hr"})

            table_lines = [
                "**📈 站点详情**\n",
                "| 状态 | 站点 | 匹配 | 成功 | 失败 |",
                "| :---: | :--- | ---: | ---: | ---: |",
            ]
            for row in rows:
                table_lines.append(
                    f"| {row.status_icon} | {row.name} | {row.matched} | "
                    f"{row.succeeded} | {row.failed} |"
                )
            elements.append({
                "tag": "markdown",
                "content": "\n".join(table_lines),
            })

        elements.append({"tag": "hr"})

        site_total = len(stats.site_details)
        active = sum(1 for c in stats.site_details.values() if c > 0)
        dead_count, dead_text = failed_sites_display(stats)

        footer_parts = [f"⏱ 耗时 **{stats.duration_str}**"]
        footer_parts.append(f"📡 {site_total} 站点")
        footer_parts.append(f"🎯 {active} 命中")
        footer_parts.append(f"❌ {dead_count} 无匹配")
        if stats.dead_count:
            footer_parts.append(f"💀 {stats.dead_count} 死种")

        elements.append({
            "tag": "note",
            "elements": [{"tag": "plain_text", "content": " · ".join(footer_parts)}],
        })

        if dead_count:
            elements.append({
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": f"无匹配 ({dead_count} 个): {dead_text}"}
                ],
            })

        return elements

    @staticmethod
    def _gen_sign(timestamp: str, secret: str) -> str:
        """生成飞书自定义机器人签名。

        签名字符串由 timestamp、换行符与 secret 依次拼接，以该字符串作为
        HMAC-SHA256 的密钥对空消息体做摘要，再 Base64 编码。
        timestamp 单位为秒，服务端只接受 1 小时内的值。
        """
        string_to_sign = f"{timestamp}\n{secret}"
        digest = hmac.new(
            string_to_sign.encode("utf-8"), digestmod=hashlib.sha256
        ).digest()
        return base64.b64encode(digest).decode("utf-8")

    async def _send(self, payload: dict) -> bool:
        if self._secret:
            timestamp = str(int(time.time()))
            payload = {
                "timestamp": timestamp,
                "sign": self._gen_sign(timestamp, self._secret),
                **payload,
            }
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(
                    self._webhook_url,
                    json=payload,
                    headers={"Content-Type": "application/json"},
                    timeout=aiohttp.ClientTimeout(total=10),
                ) as resp:
                    body = await resp.json()
                    if body.get("code") == 0 or body.get("StatusCode") == 0:
                        logger.info("飞书消息发送成功")
                        return True
                    logger.warning("飞书消息发送失败: %s", json.dumps(body, ensure_ascii=False))
                    return False
        except Exception as e:
            logger.error("飞书消息发送异常: %s", e)
            return False

    @staticmethod
    def from_config(config: dict) -> Optional["FeishuNotifier"]:
        webhook_url = config.get("webhook_url", "")
        if not webhook_url:
            logger.warning("飞书 webhook_url 未配置，跳过通知")
            return None
        return FeishuNotifier(
            webhook_url=webhook_url,
            secret=config.get("secret"),
        )
