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

        site_match = stats.site_match_counts or {}
        site_ok = stats.site_succeeded or {}
        site_fail = stats.site_failed or {}
        all_sites = set(site_match.keys()) | set(site_ok.keys()) | set(site_fail.keys())

        if all_sites:
            elements.append({"tag": "hr"})

            rows = []
            for name in all_sites:
                m = site_match.get(name, 0)
                ok = site_ok.get(name, 0)
                fail = site_fail.get(name, 0)
                if m or ok or fail:
                    if ok > 0 and fail == 0:
                        icon = "🟢"
                    elif ok > 0:
                        icon = "🟡"
                    elif m > 0:
                        icon = "🔵"
                    else:
                        icon = "⚪"
                    rows.append((icon, name, m, ok, fail))
            rows.sort(key=lambda x: (x[4] == 0, -x[3], -x[2]))

            table_lines = [
                "**📈 站点详情**\n",
                "| 状态 | 站点 | 匹配 | 成功 | 失败 |",
                "| :---: | :--- | ---: | ---: | ---: |",
            ]
            for icon, name, m, ok, fail in rows:
                table_lines.append(
                    f"| {icon} | {name} | {m} | {ok} | {fail} |"
                )
            elements.append({
                "tag": "markdown",
                "content": "\n".join(table_lines),
            })

        elements.append({"tag": "hr"})

        site_total = len(stats.site_details)
        active = sum(1 for c in stats.site_details.values() if c > 0)
        dead_sites = stats.failed_sites
        dead_count = len(dead_sites) if dead_sites else 0

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

        if dead_sites and len(dead_sites) <= 10:
            elements.append({
                "tag": "note",
                "elements": [
                    {"tag": "plain_text", "content": f"无匹配: {' '.join(dead_sites)}"}
                ],
            })

        return elements

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
