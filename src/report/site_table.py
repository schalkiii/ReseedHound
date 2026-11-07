"""站点明细表的统一口径。

文本报告与飞书卡片共用同一份行构造、状态灯、排序规则与无匹配站点展示，
避免同一轮辅种在两处给出不同结论或不同顺序。
"""
from dataclasses import dataclass

# 四档状态灯：全部成功 / 有成功也有失败 / 仅匹配未添加 / 无有效数据
STATUS_ALL_OK = "🟢"
STATUS_PARTIAL = "🟡"
STATUS_MATCH_ONLY = "🔵"
STATUS_NONE = "⚪"

# 报告里最多列出的无匹配站点数，超出部分以「等 N 个」收束
MAX_FAILED_SITES_DISPLAY = 10


@dataclass(frozen=True)
class SiteRow:
    name: str
    matched: int
    succeeded: int
    failed: int

    @property
    def success_rate(self) -> float:
        total = self.succeeded + self.failed
        return self.succeeded / total if total > 0 else 0.0

    @property
    def status_icon(self) -> str:
        if self.succeeded > 0 and self.failed == 0:
            return STATUS_ALL_OK
        if self.succeeded > 0:
            return STATUS_PARTIAL
        if self.matched > 0:
            return STATUS_MATCH_ONLY
        return STATUS_NONE


def build_site_rows(stats) -> list[SiteRow]:
    """从 ReseedStats 构造站点行，按成功率降序稳定排序。

    排序键：成功率 → 成功数 → 匹配数 → 站点名。前三项降序，
    名称升序保证结果稳定可复现，不受字典迭代顺序影响。
    """
    match = stats.site_match_counts or {}
    ok = stats.site_succeeded or {}
    fail = stats.site_failed or {}

    rows = []
    for name in set(match) | set(ok) | set(fail):
        row = SiteRow(
            name=name,
            matched=match.get(name, 0),
            succeeded=ok.get(name, 0),
            failed=fail.get(name, 0),
        )
        if row.matched or row.succeeded or row.failed:
            rows.append(row)

    rows.sort(key=lambda r: (-r.success_rate, -r.succeeded, -r.matched, r.name))
    return rows


def failed_sites_display(stats) -> tuple[int, str]:
    """返回 (无匹配站点总数, 展示文案)，两处报告共用同一截断规则。"""
    sites = list(stats.failed_sites or [])
    shown = sites[:MAX_FAILED_SITES_DISPLAY]
    text = ", ".join(shown)
    if len(sites) > len(shown):
        text += f" 等 {len(sites)} 个"
    return len(sites), text
