"""savept.icu（PT监护室）死亡站点数据源与本地站点对照。

数据源（公开、无鉴权）：

    GET {base}/api/history/dead?page={n}
    -> {"has_more": bool, "next_page": int, "items": [
          {"date": "2026-10-29", "description": "瞬间", "event_label": "意外死亡",
           "name": "MomentPT", "url": "https://www.momentpt.top/"}, ...]}

用途：把本地 `sites.yaml` 里的站点与「已死亡」名单对照，**推荐**禁用。
只出建议、默认不改配置；写入需 CLI `--apply` 显式确认（见 run.py 的 check-dead）。

匹配优先级（置信度由高到低）：
  1. 域名归一化后完全一致      -> high（url 直接对得上）
  2. 注册域一致（末两段）      -> high（如 pt.cdfile.org ↔ cdfile.org）
  3. 站点名 / 别名归一化一致    -> medium（域名不同，多为站点换域名，建议人工核对）
"""
import asyncio
import logging
import re
from dataclasses import dataclass
from pathlib import Path

import aiohttp
import yaml

logger = logging.getLogger("seedhound")

DEFAULT_SAVEPT_URL = "https://savept.icu"
DEAD_API_PATH = "/api/history/dead"


@dataclass(frozen=True)
class DeadSite:
    """savept 记录的一条死亡事件。"""

    name: str
    url: str
    dead_date: str
    event_label: str
    alias: str = ""


@dataclass(frozen=True)
class DeadMatch:
    """本地站点与死亡名单的一条匹配结果。"""

    local_name: str
    local_url: str
    dead_name: str
    dead_url: str
    dead_date: str
    event_label: str
    matched_by: str
    confidence: str  # high / medium


def norm_domain(url: str) -> str:
    """把 URL 归一化成裸域名（去协议/www./端口/路径，小写）。"""
    d = str(url or "").strip()
    d = re.sub(r"^[a-zA-Z][a-zA-Z0-9+.-]*://", "", d)
    d = d.split("/")[0].split("?")[0]
    d = d.split("@")[-1]
    d = d.split(":")[0]
    d = d.lower()
    return d[4:] if d.startswith("www.") else d


def registrable_domain(domain: str) -> str:
    """粗略取注册域（末两段），不引入 publicsuffix 依赖。"""
    parts = [p for p in str(domain or "").split(".") if p]
    return ".".join(parts[-2:]) if len(parts) >= 2 else str(domain or "")


def norm_name(name: str) -> str:
    """站点名归一化：去空白、小写、去掉尾部 pt/tracker 以便 MomentPT ↔ Moment 匹配。"""
    s = re.sub(r"\s+", "", str(name or "")).lower()
    s = re.sub(r"(pt|tracker)$", "", s)
    return s


def parse_dead_items(payload: object) -> list[DeadSite]:
    """把 /api/history/dead 的一页响应解析成 DeadSite 列表，忽略畸形项。"""
    if not isinstance(payload, dict):
        return []
    items = payload.get("items")
    if not isinstance(items, list):
        return []
    out: list[DeadSite] = []
    for it in items:
        if not isinstance(it, dict):
            continue
        url = str(it.get("url") or "").strip()
        name = str(it.get("name") or "").strip()
        if not url and not name:
            continue
        out.append(
            DeadSite(
                name=name,
                url=url,
                dead_date=str(it.get("date") or "").strip(),
                event_label=str(it.get("event_label") or "").strip(),
                alias=str(it.get("description") or "").strip(),
            )
        )
    return out


async def fetch_dead_sites(
    base_url: str = DEFAULT_SAVEPT_URL,
    timeout: float = 20.0,
    max_pages: int = 50,
) -> list[DeadSite]:
    """分页拉取 savept 的死亡事件列表。"""
    base = str(base_url or DEFAULT_SAVEPT_URL).rstrip("/")
    collected: list[DeadSite] = []
    client_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=client_timeout) as session:
        for page in range(1, max_pages + 1):
            url = f"{base}{DEAD_API_PATH}?page={page}"
            try:
                async with session.get(url) as resp:
                    if resp.status != 200:
                        logger.warning(
                            "savept 死亡名单拉取失败: HTTP %d (%s)", resp.status, url
                        )
                        break
                    try:
                        payload = await resp.json()
                    except aiohttp.ContentTypeError:
                        logger.warning("savept 返回非 JSON，停止拉取 (%s)", url)
                        break
            except asyncio.TimeoutError:
                logger.warning("savept 死亡名单拉取超时 (%s)", url)
                break
            except aiohttp.ClientError as exc:
                logger.warning("savept 死亡名单拉取错误 (%s): %s", url, exc)
                break

            batch = parse_dead_items(payload)
            collected.extend(batch)
            if not batch or not payload.get("has_more"):
                break
    return collected


def match_local(dead_sites: list[DeadSite], local_sites: list[dict]) -> list[DeadMatch]:
    """把死亡名单与本地站点对照，返回匹配结果（含置信度）。"""
    by_domain: dict[str, DeadSite] = {}
    by_reg: dict[str, DeadSite] = {}
    by_name: dict[str, DeadSite] = {}
    for d in dead_sites:
        dom = norm_domain(d.url)
        if dom:
            by_domain.setdefault(dom, d)
            by_reg.setdefault(registrable_domain(dom), d)
        for raw in (d.name, d.alias):
            key = norm_name(raw)
            if key:
                by_name.setdefault(key, d)

    matches: list[DeadMatch] = []
    for s in local_sites:
        if not isinstance(s, dict):
            continue
        local_name = str(s.get("name") or "").strip()
        local_url = str(s.get("url") or "").strip()
        dom = norm_domain(local_url)

        dead = by_domain.get(dom) if dom else None
        matched_by = "domain"
        if dead is None and dom:
            dead = by_reg.get(registrable_domain(dom))
            matched_by = "registrable"
        if dead is None:
            key = norm_name(local_name)
            dead = by_name.get(key) if key else None
            matched_by = "name"
        if dead is None:
            continue

        confidence = "high" if matched_by in ("domain", "registrable") else "medium"
        matches.append(
            DeadMatch(
                local_name=local_name,
                local_url=local_url,
                dead_name=dead.name,
                dead_url=dead.url,
                dead_date=dead.dead_date,
                event_label=dead.event_label,
                matched_by=matched_by,
                confidence=confidence,
            )
        )
    return matches


def format_report(matches: list[DeadMatch], total_local: int) -> str:
    """把匹配结果排版成可直接看的建议清单。"""
    if not matches:
        return (
            f"未发现本地 {total_local} 个站点出现在 savept.icu 死亡名单中，"
            "无需禁用。"
        )
    lines = [
        f"对照 savept.icu 死亡名单，本地 {total_local} 个站点中有 "
        f"{len(matches)} 个命中：",
        "",
        f"{'本地站点':<12}{'置信':<7}{'死亡日期':<12}{'死因':<10}{'savept 名称':<14}匹配",
        "-" * 78,
    ]
    for m in sorted(matches, key=lambda x: (x.confidence != "high", x.local_name)):
        lines.append(
            f"{m.local_name:<12}{m.confidence:<7}{m.dead_date:<12}"
            f"{m.event_label:<10}{m.dead_name:<14}{m.matched_by}"
        )
    lines.append("")
    lines.append("建议动作：")
    lines.append("  - confidence=high   → 可直接禁用（站点确已死亡）")
    lines.append("  - confidence=medium → 域名不同，先人工核对是否站点换域名再决定")
    lines.append("  - 写入配置请加 --apply（默认只打印建议，不改 sites.yaml）")
    return "\n".join(lines)


def disable_sites_in_yaml(sites_path: str, names: list[str]) -> list[str]:
    """把指定站点写成 enabled: false（保留条目便于恢复）。返回实际改动的站点名。

    用文本行处理而非 yaml 转储，避免丢注释与顺序；缩进按顶级列表条目（2 空格）。
    若条目已有 enabled 键则就地改值，避免插入重复键（YAML 重复键取后者会导致禁用失效）。
    """
    if not names:
        return []
    path = Path(sites_path)
    if not path.exists():
        raise FileNotFoundError(f"站点配置不存在: {path}")
    targets = {str(n).strip() for n in names if str(n).strip()}
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    out: list[str] = []
    done: set[str] = set()
    current: str | None = None
    current_has_enabled = False

    def close_current() -> None:
        # 条目结束：目标站点若还没有 enabled 行，补一条 false
        if current in targets and current not in done and not current_has_enabled:
            out.append("  enabled: false\n")
            done.add(current)

    for line in lines:
        m = re.search(r"^\s*- name:\s*(.+?)\s*$", line)
        if m:
            close_current()
            current = m.group(1).strip().strip('"').strip("'")
            current_has_enabled = False
            out.append(line)
            continue

        em = re.match(r"^(\s*)enabled\s*:\s*(.+?)\s*$", line)
        if em and current in targets:
            # 就地改值，不新增行，杜绝重复键
            out.append(f"{em.group(1)}enabled: false\n")
            current_has_enabled = True
            done.add(current)
            continue
        if re.match(r"^\s*enabled\s*:", line):
            current_has_enabled = True
        out.append(line)

    close_current()
    path.write_text("".join(out), encoding="utf-8")
    return sorted(done)


def load_local_sites(sites_path: str, only_enabled: bool = True) -> list[dict]:
    """读取 sites.yaml 的站点清单，只取 name/url，绝不触碰 passkey/api_key/cookie。

    only_enabled=True 时跳过 enabled: false 的站点——已禁用的无需再推荐禁用。
    """
    path = Path(sites_path)
    if not path.exists():
        raise FileNotFoundError(f"站点配置不存在: {path}")
    raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    out: list[dict] = []
    for s in raw.get("sites") or []:
        if not isinstance(s, dict):
            continue
        name = str(s.get("name") or "").strip()
        url = str(s.get("url") or "").strip()
        if not name or not url:
            continue
        if only_enabled and s.get("enabled") is False:
            continue
        out.append({"name": name, "url": url})
    return out


__all__ = [
    "DEFAULT_SAVEPT_URL",
    "DEAD_API_PATH",
    "DeadSite",
    "DeadMatch",
    "norm_domain",
    "registrable_domain",
    "norm_name",
    "parse_dead_items",
    "fetch_dead_sites",
    "match_local",
    "format_report",
    "disable_sites_in_yaml",
    "load_local_sites",
]
