from datetime import datetime

from ..engine.seeder import ReseedStats
from .site_table import build_site_rows, failed_sites_display


def _display_width(s: str) -> int:
    width = 0
    for ch in s:
        if ord(ch) < 128:
            width += 1
        else:
            width += 2
    return width


def _pad_str(s: str, width: int, align: str = '<') -> str:
    dw = _display_width(s)
    if dw >= width:
        return s
    padding = width - dw
    if align == '>':
        return ' ' * padding + s
    elif align == '^':
        left = padding // 2
        right = padding - left
        return ' ' * left + s + ' ' * right
    else:
        return s + ' ' * padding


class ReportGenerator:

    def __init__(self):
        self._report_time = datetime.now()

    def generate(self, stats: ReseedStats) -> str:
        lines = []
        lines.append("=" * 50)
        lines.append("  SeedHound 辅种报告")
        lines.append(f"  {self._report_time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"  运行耗时: {stats.duration_str}")
        lines.append("=" * 50)
        lines.append("")

        unique = stats.total_torrents - stats.duplicate_count

        left_pairs = [
            ('🟢', '辅种成功', stats.succeeded_count),
            ('🎯', '全站匹配', stats.matched_count),
            ('📦', '扫描种子', stats.total_torrents),
            ('🆕', '新增种子', stats.new_torrents),
        ]
        right_pairs = [
            ('🔴', '下载失败', stats.failed_count),
            ('⏭️', '追踪器跳过', stats.tracker_skip_count),
            ('🔄', '去重', stats.duplicate_count),
            ('📌', '唯一种子', unique),
        ]

        label_width = 10
        value_width = 6

        for (l_icon, l_label, l_val), (r_icon, r_label, r_val) in zip(left_pairs, right_pairs):
            left_part = (f'{l_icon} '
                         f'{_pad_str(l_label, label_width, "<")}'
                         f'{_pad_str(str(l_val), value_width, ">")}')
            right_part = (f'{r_icon} '
                          f'{_pad_str(r_label, label_width, "<")}'
                          f'{_pad_str(str(r_val), value_width, ">")}')
            lines.append(f'  {left_part}    {right_part}')
        lines.append("")

        lines.append(f"[ 站点匹配 ]  共匹配 {stats.matched_count} 个种子可跨站辅种")

        rows = build_site_rows(stats)
        if rows:
            name_width = max((_display_width(r.name) for r in rows), default=14)
            name_width = max(name_width, 14)

            status_col = '状态'
            site_col = _pad_str('站点', name_width + 2, '^')
            header = (f'  | {_pad_str(status_col, 6, "^")}'
                      f' | {site_col}'
                      f' | {_pad_str("匹配", 8, ">")}'
                      f' | {_pad_str("成功", 8, ">")}'
                      f' | {_pad_str("失败", 8, ">")} |')
            lines.append(header)

            sep_cols = (
                f'  | {_pad_str(":--:", 6, "^")}'
                f' | {_pad_str(":" + "-" * name_width, name_width + 2, "^")}'
                f' | {_pad_str("-" * 7 + ":", 8, ">")}'
                f' | {_pad_str("-" * 7 + ":", 8, ">")}'
                f' | {_pad_str("-" * 7 + ":", 8, ">")} |'
            )
            lines.append(sep_cols)

            for row in rows:
                status_col_content = _pad_str(row.status_icon, 6, '^')
                site_col_content = _pad_str(row.name, name_width + 2, '^')
                row_line = (f'  | {status_col_content}'
                            f' | {site_col_content}'
                            f' | {_pad_str(str(row.matched), 8, ">")}'
                            f' | {_pad_str(str(row.succeeded), 8, ">")}'
                            f' | {_pad_str(str(row.failed), 8, ">")} |')
                lines.append(row_line)
        else:
            lines.append("  (无站点返回匹配数据)")
        lines.append("")

        dead_count, dead_text = failed_sites_display(stats)
        if dead_count:
            lines.append(f"  ⚠️ 无匹配站点 ({dead_count} 个): {dead_text}")
            lines.append("")

        total_sites = len(stats.site_details)
        active_sites = sum(1 for c in stats.site_details.values() if c > 0)
        zero_sites = total_sites - active_sites
        lines.append(f"[ 站点汇总 ]  启用 {total_sites} 个  |  有匹配 {active_sites} 个  |  无匹配 {zero_sites} 个")
        lines.append("")

        lines.append("=" * 50)
        return "\n".join(lines)

    def generate_markdown(self, stats: ReseedStats) -> str:
        lines = []
        lines.append(
            f"**SeedHound 辅种报告**  "
            f"\n{self._report_time.strftime('%Y-%m-%d %H:%M:%S')}  "
            f"\n耗时: {stats.duration_str}\n"
        )

        lines.append("**扫描统计**")
        lines.append(f"本地种子: {stats.total_torrents}  ")
        lines.append(f"新增种子: {stats.new_torrents}  ")
        cached = stats.total_torrents - stats.new_torrents
        lines.append(f"已缓存: {cached}\n")

        lines.append(f"**站点匹配** （共 {stats.matched_count} 个种子可辅种）")

        rows = build_site_rows(stats)
        if rows:
            lines.append("| 站点 | 匹配 | 成功 | 失败 |")
            lines.append("|---|---:|---:|---:|")
            for row in rows:
                lines.append(
                    f"| {row.name} | {row.matched} | {row.succeeded} | {row.failed} |"
                )
        lines.append("")

        lines.append("**辅种结果**")
        lines.append(f"成功: {stats.succeeded_count}  ")
        lines.append(f"失败: {stats.failed_count}  ")
        lines.append(f"追踪器跳过: {stats.tracker_skip_count}")

        return "\n".join(lines)
