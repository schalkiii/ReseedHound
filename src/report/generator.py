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

            header = f"  {'站点':<14} {'匹配':>6}  {'成功':>6}  {'失败':>6}"
            lines.append(header)
            lines.append(f"  {'-' * 14:<14} {'-' * 6:>6}  {'-' * 6:>6}  {'-' * 6:>6}")
            for name, m, ok, fail in rows:
                lines.append(
                    f"  {name:<14} {m:>6}  {ok:>6}  {fail:>6}"
                )
        else:
            lines.append("  (无站点返回匹配数据)")
        lines.append("")

        lines.append("[ 辅种结果 ]")
        lines.append(f"  成功辅种:  {stats.succeeded_count:>6}")
        lines.append(f"  失败辅种:  {stats.failed_count:>6}")
        lines.append(f"  追踪器跳过:{stats.tracker_skip_count:>6}")

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
            lines.append("| 站点 | 匹配 | 成功 | 失败 |")
            lines.append("|---|---:|---:|---:|")
            for name, m, ok, fail in rows:
                lines.append(f"| {name} | {m} | {ok} | {fail} |")
        lines.append("")

        lines.append("**辅种结果**")
        lines.append(f"成功: {stats.succeeded_count}  ")
        lines.append(f"失败: {stats.failed_count}  ")
        lines.append(f"追踪器跳过: {stats.tracker_skip_count}")

        return "\n".join(lines)
