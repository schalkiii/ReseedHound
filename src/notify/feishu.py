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
            f"本地种子: {stats.total_torrents} | "
            f"去重: {stats.duplicate_count} | "
            f"唯一: {unique_count}\n"
            f"新增: {stats.new_torrents} | "
            f"全站匹配: {stats.matched_count}\n"
            f"追踪器跳过: {stats.tracker_skip_count} | "
            f"成功辅种: {stats.succeeded_count} | "
            f"失败: {stats.failed_count} | "
            f"耗时: {stats.duration_str}"
        )

        site_matches = stats.site_match_counts
        if site_matches:
            sorted_m = sorted(site_matches.items(), key=lambda x: x[1], reverse=True)
            lines = "\n".join(
                f"{name}: 匹配 {count} 个" for name, count in sorted_m[:20]
            )
            if len(sorted_m) > 20:
                lines += f"\n（仅展示前20，共 {len(sorted_m)} 个站点有匹配）"
            parts.append(f"**各站点匹配数（去重后）**\n{lines}")

        succeeded = stats.site_succeeded
        if succeeded:
            sorted_s = sorted(succeeded.items(), key=lambda x: x[1], reverse=True)
            lines = "\n".join(
                f"{name}: {count} 个" for name, count in sorted_s[:15]
            )
            if len(sorted_s) > 15:
                lines += f"\n（仅展示前15，共 {len(sorted_s)} 个成功站点）"
            parts.append(f"**辅种成功站点**\n{lines}")
        else:
            parts.append("**辅种成功站点**\n无")

        failed = stats.site_failed
        if failed:
            sorted_f = sorted(failed.items(), key=lambda x: x[1], reverse=True)
            lines = "\n".join(
                f"{name}: {count} 次失败" for name, count in sorted_f[:10]
            )
            if len(sorted_f) > 10:
                lines += f"\n（仅展示前10，共 {len(sorted_f)} 个有失败站点）"
            parts.append(f"**辅种失败站点**\n{lines}")

        dead = stats.failed_sites
        if dead:
            dead_str = "、".join(dead[:15])
            if len(dead) > 15:
                dead_str += f" 等共{len(dead)}个"
            parts.append(f"**无匹配站点**: {dead_str}")

        site_total = len(stats.site_details)
        active = sum(1 for c in stats.site_details.values() if c > 0)
        parts.append(
            f"启用站点: {site_total} | 命中: {active} | 未命中: {site_total - active}"
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
