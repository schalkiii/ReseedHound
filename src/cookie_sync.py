import logging
import re
from pathlib import Path

import yaml

from .network.cookiecloud import CookieCloudClient

logger = logging.getLogger("seedhound")


class _QuotedStr(str):
    pass


class _WideDumper(yaml.Dumper):
    pass


_WideDumper.best_width = 999999


def _quoted_str_representer(dumper, data):
    return dumper.represent_scalar("tag:yaml.org,2002:str", str(data), style='"')


yaml.add_representer(_QuotedStr, _quoted_str_representer)


async def sync_cookies(url: str, uuid: str, password: str, sites_path: str) -> int:
    """从 CookieCloud 同步站点 Cookie 并写回 sites_path。

    返回 0 表示成功获取并写入 Cookie，1 表示未获取到任何 Cookie。
    """
    logger.info("从 CookieCloud 同步站点 Cookie...")
    client = CookieCloudClient(base_url=url, uuid=uuid, password=password)
    await client.start()
    try:
        cookies_by_domain = await client.fetch_cookies()
        if not cookies_by_domain:
            logger.error("未获取到任何 Cookie 数据")
            return 1

        cookie_domains = set(cookies_by_domain.keys())
        logger.info("从 CookieCloud 获取到 %d 个域名的 Cookie", len(cookie_domains))

        path = Path(sites_path)
        with open(path, encoding="utf-8") as f:
            sites_data = yaml.safe_load(f) or {}

        sites = sites_data.get("sites", [])
        matched_count = 0
        for site in sites:
            site_url = site.get("url", "")
            matched_domain = CookieCloudClient.match_domain(site_url, cookie_domains)
            if matched_domain:
                cookie_str = CookieCloudClient.cookies_to_string(
                    cookies_by_domain[matched_domain]
                )
                site["cookie"] = _QuotedStr(cookie_str)
                matched_count += 1
                logger.info(
                    "  [OK] %s <- %s (%d 条 Cookie)",
                    site.get("name", "?"),
                    matched_domain,
                    len(cookies_by_domain[matched_domain]),
                )
            else:
                logger.debug("  [--] %s 未匹配到 Cookie", site.get("name", "?"))

        with open(path, "w", encoding="utf-8") as f:
            yaml.dump(
                sites_data,
                f,
                Dumper=_WideDumper,
                allow_unicode=True,
                default_flow_style=False,
                sort_keys=False,
            )

        raw = path.read_text(encoding="utf-8")
        raw = re.sub(r"\\\n    \\ ", " ", raw)
        path.write_text(raw, encoding="utf-8")

        logger.info(
            "同步完成: %d/%d 个站点已更新 Cookie → %s",
            matched_count,
            len(sites),
            path,
        )
        return 0
    finally:
        await client.close()
