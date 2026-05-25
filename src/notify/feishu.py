import json
import logging
from typing import Optional

import aiohttp

logger = logging.getLogger("seedhound")


class FeishuNotifier:

    def __init__(self, webhook_url: str, secret: Optional[str] = None):
        self._webhook_url = webhook_url
        self._secret = secret

    async def send_text(self, content: str) -> bool:
        payload = {
            "msg_type": "text",
            "content": {
                "text": content,
            },
        }
        return await self._send(payload)

    async def send_card(self, title: str, content: str) -> bool:
        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {
                        "tag": "plain_text",
                        "content": title,
                    },
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": content,
                    },
                ],
            },
        }
        return await self._send(payload)

    async def send_report_card(self, stats) -> bool:
        return await self.send_card(
            title="SeedHound 辅种报告",
            content=self._build_report_markdown(stats),
        )

    def _build_report_markdown(self, stats) -> str:
        parts = []

        unique_count = stats.total_torrents - stats.duplicate_count
        parts.append(
            f"**扫描统计**\n"
            f"本地种子 {stats.total_torrents} | "
            f"去重 {stats.duplicate_count} | "
            f"唯一 {unique_count} | "
            f"新增 {stats.new_torrents}\n"
            f"全站匹配 {stats.matched_count} | "
            f"追踪器跳过 {stats.tracker_skip_count} | "
            f"成功 {stats.succeeded_count} | "
            f"失败 {stats.failed_count} | "
            f"耗时 {stats.duration_str}"
        )

        site_match = stats.site_match_counts or {}
        site_ok = stats.site_succeeded or {}
        site_fail = stats.site_failed or {}

        all_sites = set(site_match.keys()) | set(site_ok.keys()) | set(site_fail.keys())
        if all_sites:
            rows = []
            for name in all_sites:
                m = site_match.get(name, 0)
                ok = site_ok.get(name, 0)
                fail = site_fail.get(name, 0)
                if m or ok or fail:
                    rows.append((name, m, ok, fail))
            rows.sort(key=lambda x: x[1], reverse=True)

            table_lines = ["| 站点 | 匹配 | 成功 | 失败 |", "|---|---:|---:|---:|"]
            for name, m, ok, fail in rows:
                table_lines.append(f"| {name} | {m} | {ok} | {fail} |")
            parts.append("**站点详情**\n" + "\n".join(table_lines))

        dead = stats.failed_sites
        if dead:
            dead_str = "、".join(dead)
            parts.append(f"**无匹配站点 ({len(dead)} 个)**\n" + dead_str)

        site_total = len(stats.site_details)
        active = sum(1 for c in stats.site_details.values() if c > 0)
        parts.append(
            f"启用 {site_total} | 命中 {active} | 未命中 {site_total - active}"
        )

        return "\n\n".join(parts)

    async def _send(self, payload: dict) -> bool:
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
