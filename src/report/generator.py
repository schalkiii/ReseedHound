from datetime import datetime

from ..engine.seeder import ReseedStats


class ReportGenerator:

    def __init__(self):
        self._report_time = datetime.now()

    def generate(self, stats: ReseedStats) -> str:
        lines = []
        lines.append("=" * 40)
        lines.append("  SeedHound 辅种报告")
        lines.append(f"  {self._report_time.strftime('%Y-%m-%d %H:%M:%S')}")
        lines.append(f"  运行耗时: {stats.duration_str}")
        lines.append("=" * 40)
        lines.append("")

        lines.append("[ 扫描统计 ]")
        lines.append(f"  本地种子:  {stats.total_torrents:>6}")
        lines.append(f"  新增种子:  {stats.new_torrents:>6}")
        cached = stats.total_torrents - stats.new_torrents
        lines.append(f"  已缓存:    {cached:>6}")
        lines.append("")

        lines.append(f"[ 站点匹配 ]  共匹配 {stats.matched_count} 个种子可跨站辅种")
        if stats.site_details:
            sorted_sites = sorted(
                stats.site_details.items(),
                key=lambda x: x[1],
                reverse=True,
            )
            for site_name, count in sorted_sites:
                if count > 0:
                    lines.append(f"  {site_name:<14} {count:>6} 个")
        else:
            lines.append("  (无站点返回匹配数据)")
        lines.append("")

        lines.append("[ 辅种结果 ]")
        lines.append(f"  成功辅种:  {stats.succeeded_count:>6}")
        lines.append(f"  失败辅种:  {stats.failed_count:>6}")

        succeeded = stats.site_succeeded
        if succeeded:
            lines.append("")
            lines.append("  各站点成功辅种数:")
            for name, count in sorted(succeeded.items(), key=lambda x: x[1], reverse=True):
                lines.append(f"    {name:<14} {count:>6} 个")

        dead = stats.failed_sites
        if dead:
            lines.append("")
            lines.append(f"  无匹配站点 ({len(dead)} 个): {', '.join(dead)}")

        lines.append("")

        total_sites = len(stats.site_details)
        active_sites = sum(1 for c in stats.site_details.values() if c > 0)
        zero_sites = total_sites - active_sites
        lines.append("[ 站点汇总 ]")
        lines.append(f"  启用站点:  {total_sites:>6}")
        lines.append(f"  有匹配站点:{active_sites:>6}")
        lines.append(f"  无匹配站点:{zero_sites:>6}")
        lines.append("")

        lines.append("=" * 40)
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
        if stats.site_details:
            sorted_sites = sorted(
                stats.site_details.items(),
                key=lambda x: x[1],
                reverse=True,
            )
            active = [(n, c) for n, c in sorted_sites if c > 0]
            if active:
                lines.append("| 站点 | 匹配数 |")
                lines.append("|------|--------|")
                for site_name, count in active:
                    lines.append(f"| {site_name} | {count} |")
        lines.append("")

        lines.append("**辅种结果**")
        lines.append(f"成功: {stats.succeeded_count}  ")
        lines.append(f"失败: {stats.failed_count}")

        return "\n".join(lines)
