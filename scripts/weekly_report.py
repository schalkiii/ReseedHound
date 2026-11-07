#!/usr/bin/env python3
"""SeedHound 日志周报生成 + 远期日志清理工具。

解析 logs/reseed.log，按站点聚合近一段时间（默认近 7 天）的辅种指标，
输出 Markdown 周报；可选地清理远期日志（默认 dry-run 预览，--apply 才真正截断）。

用法:
    python scripts/weekly_report.py                 # 近 7 天周报（控制台）
    python scripts/weekly_report.py --days 30       # 近 30 天
    python scripts/weekly_report.py --markdown out.md
    python scripts/weekly_report.py --clean --keep-days 30     # 预览清理
    python scripts/weekly_report.py --clean --keep-days 30 --apply
"""
from __future__ import annotations

import argparse
import re
import sys
from collections import defaultdict, deque
from datetime import datetime, timedelta
from pathlib import Path
from urllib.parse import urlparse

try:
    import yaml
except ImportError:
    yaml = None

# 轮次标志只维护一份：与日志裁剪（src/utils/logger.py）共用 RUN_MARKER，
# 日志启动文案变更时周报清理不会静默失真。
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from src.utils.logger import RUN_MARKER

TS_RE = re.compile(r"^(\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2})")
URL_HOST_RE = re.compile(r"https?://([^/]+)/")
RUN_SUMMARY_RE = re.compile(
    r"辅种完成: 扫描=(\d+), 去重=(\d+), 新=(\d+), 匹配=(\d+), "
    r"追踪器跳过=(\d+), 成功=(\d+), 失败=(\d+), 死种=(\d+)"
)
MATCH_RE = re.compile(r"站点 (.+?) 匹配到 (\d+) 个种子")
SKIP_RE = re.compile(r"站点 (.+?) \((\d+)次严重错误: (.+?)\)，跳过后续下载")
SUCCESS_RE = re.compile(r"辅种成功: .*?-> (.+?) \(id=(\d+)\)")
HTML_FAIL_RE = re.compile(r"下载返回HTML而非torrent: .*?原因: (\S+)")
TIMEOUT_RE = re.compile(r"下载超时:")
CONN_ERR_RE = re.compile(r"下载错误 ")


def load_site_host_map(sites_path: str) -> dict[str, str]:
    """构建 host -> 站点名 映射，用于把下载失败 URL 归属到站点。"""
    if not yaml or not Path(sites_path).exists():
        return {}
    with open(sites_path, encoding="utf-8") as f:
        data = yaml.safe_load(f) or {}
    host_map: dict[str, str] = {}
    for s in data.get("sites", []):
        url = s.get("url", "")
        name = s.get("name", "")
        if not url or not name:
            continue
        host = urlparse(url).netloc.lower()
        if host:
            host_map[host] = name
    return host_map


def parse_dt(line: str):
    m = TS_RE.match(line)
    if not m:
        return None
    try:
        return datetime.strptime(m.group(1), "%Y-%m-%d %H:%M:%S")
    except ValueError:
        return None


def parse_log(log_path: str, since: datetime, host_map: dict[str, str]):
    """逐行解析日志，返回 (per_site 聚合, run_summaries, cutoff 信息)。"""
    per_site: dict[str, dict] = defaultdict(lambda: {
        "matched": 0, "success": 0, "failed": 0,
        "severe_skips": 0, "skip_reasons": defaultdict(int),
        "reason_counts": defaultdict(int), "last_seen": None,
    })
    runs: list[dict] = []
    in_window = False

    with open(log_path, encoding="utf-8", errors="replace") as f:
        for line in f:
            dt = parse_dt(line)
            if dt is None:
                continue
            if dt >= since:
                in_window = True
            elif not in_window:
                continue  # 早于窗口且尚未进入窗口，跳过

            _classify(line, dt, per_site, runs, host_map)

    return per_site, runs


def _classify(line: str, dt: datetime, per_site, runs, host_map):
    m = RUN_SUMMARY_RE.search(line)
    if m:
        runs.append({
            "time": dt,
            "scan": int(m.group(1)), "dedup": int(m.group(2)),
            "new": int(m.group(3)), "matched": int(m.group(4)),
            "tracker_skip": int(m.group(5)), "success": int(m.group(6)),
            "failed": int(m.group(7)), "dead": int(m.group(8)),
        })
        return

    m = MATCH_RE.search(line)
    if m:
        name = m.group(1).strip()
        per_site[name]["matched"] += int(m.group(2))
        per_site[name]["last_seen"] = dt
        return

    m = SKIP_RE.search(line)
    if m:
        name = m.group(1).strip()
        reason = m.group(3).strip()
        st = per_site[name]
        st["severe_skips"] += 1
        st["skip_reasons"][reason] += 1
        st["last_seen"] = dt
        return

    m = SUCCESS_RE.search(line)
    if m:
        name = m.group(1).strip()
        per_site[name]["success"] += 1
        per_site[name]["last_seen"] = dt
        return

    # 下载失败归因到站点
    site_name = None
    reason = None
    if HTML_FAIL_RE.search(line):
        r = HTML_FAIL_RE.search(line)
        reason = r.group(1).strip(")")
        um = URL_HOST_RE.search(line)
        if um:
            site_name = host_map.get(um.group(1).lower())
    elif TIMEOUT_RE.search(line):
        reason = "timeout"
        um = URL_HOST_RE.search(line)
        if um:
            site_name = host_map.get(um.group(1).lower())
    elif CONN_ERR_RE.search(line):
        reason = "conn_error"
        um = URL_HOST_RE.search(line)
        if um:
            site_name = host_map.get(um.group(1).lower())

    if site_name and reason:
        st = per_site[site_name]
        st["failed"] += 1
        st["reason_counts"][reason] += 1
        st["last_seen"] = dt


def generate_report(per_site: dict, runs: list, since: datetime) -> str:
    lines = []
    lines.append(f"# SeedHound 周报（{since.date()} 起）")
    lines.append("")
    if runs:
        total_match = sum(r["matched"] for r in runs)
        total_ok = sum(r["success"] for r in runs)
        total_fail = sum(r["failed"] for r in runs)
        total_dead = sum(r["dead"] for r in runs)
        lines.append(f"- 运行轮次: **{len(runs)}**")
        lines.append(f"- 累计匹配: {total_match} | 成功: {total_ok} | 失败: {total_fail} | 死种: {total_dead}")
        lines.append("")
        lines.append("## 各轮汇总")
        lines.append("")
        lines.append("| 时间 | 匹配 | 成功 | 失败 | 死种 |")
        lines.append("| --- | --- | --- | --- | --- |")
        for r in runs:
            lines.append(
                f"| {r['time'].strftime('%m-%d %H:%M')} | {r['matched']} | "
                f"{r['success']} | {r['failed']} | {r['dead']} |"
            )
    else:
        lines.append("_窗口内无运行记录_")

    lines.append("")
    lines.append("## 站点明细（按失败数降序）")
    lines.append("")
    lines.append("| 站点 | 匹配 | 成功 | 失败 | 严重跳过 | 主要失败原因 |")
    lines.append("| --- | --- | --- | --- | --- | --- |")
    for name, st in sorted(per_site.items(), key=lambda kv: kv[1]["failed"], reverse=True):
        reasons = ", ".join(
            f"{k}:{v}" for k, v in sorted(st["reason_counts"].items(), key=lambda x: -x[1])[:3]
        )
        lines.append(
            f"| {name} | {st['matched']} | {st['success']} | {st['failed']} | "
            f"{st['severe_skips']} | {reasons} |"
        )
    return "\n".join(lines)


def cleanup_log(log_path: str, keep_days: int, keep_runs: int, apply: bool) -> str:
    """清理远期日志：保留最近 keep_days 天，且至少保留 keep_runs 轮。"""
    p = Path(log_path)
    lines = p.read_text(encoding="utf-8", errors="replace").splitlines(keepends=True)
    run_starts = [i for i, ln in enumerate(lines) if RUN_MARKER in ln]

    # 按天数截止
    cutoff_dt = None
    for ln in reversed(lines):
        dt = parse_dt(ln)
        if dt:
            cutoff_dt = dt - timedelta(days=keep_days)
            break
    keep_from_idx = 0
    if cutoff_dt:
        for i, ln in enumerate(lines):
            dt = parse_dt(ln)
            if dt and dt >= cutoff_dt:
                keep_from_idx = i
                break

    # 按轮次下限（至少保留 keep_runs 轮，避免日期窗口过窄时误删过多）
    if len(run_starts) > keep_runs:
        keep_from_idx = min(keep_from_idx, run_starts[-keep_runs])

    deleted_lines = keep_from_idx
    kept_lines = len(lines) - keep_from_idx
    removed_bytes = sum(len(ln.encode("utf-8")) for ln in lines[:keep_from_idx])
    kept_bytes = sum(len(ln.encode("utf-8")) for ln in lines[keep_from_idx:])
    msg = (
        f"[清理预览] 将删除 {deleted_lines} 行（约 {removed_bytes/1024/1024:.2f} MB），"
        f"保留 {kept_lines} 行（约 {kept_bytes/1024/1024:.2f} MB）。"
    )
    if apply:
        p.write_text("".join(lines[keep_from_idx:]), encoding="utf-8")
        msg = f"[已清理] 删除 {deleted_lines} 行，保留 {kept_lines} 行。"
    return msg


def main():
    ap = argparse.ArgumentParser(description="SeedHound 日志周报 + 清理")
    ap.add_argument("--log", default="logs/reseed.log")
    ap.add_argument("--sites", default="sites.yaml")
    ap.add_argument("--days", type=int, default=7, help="统计窗口（天），默认 7")
    ap.add_argument("--markdown", help="将周报写入该 Markdown 文件")
    ap.add_argument("--clean", action="store_true", help="执行日志清理")
    ap.add_argument("--keep-days", type=int, default=30, help="清理时保留最近天数")
    ap.add_argument("--keep-runs", type=int, default=3, help="清理时至少保留的轮次（安全下限）")
    ap.add_argument("--apply", action="store_true", help="真正截断日志（默认仅预览）")
    args = ap.parse_args()

    log_path = args.log
    if not Path(log_path).exists():
        print(f"日志文件不存在: {log_path}", file=sys.stderr)
        return 1

    host_map = load_site_host_map(args.sites)
    # 以日志中最新时间戳为参考终点。只保留文件尾部有限行数，
    # 避免把动辄数百 MB 的历史日志一次性载入内存。
    latest = None
    with open(log_path, encoding="utf-8", errors="replace") as f:
        tail = deque(f, maxlen=2000)
    for ln in reversed(tail):
        latest = parse_dt(ln)
        if latest:
            break
    since = (latest or datetime.now()) - timedelta(days=args.days)

    per_site, runs = parse_log(log_path, since, host_map)
    report = generate_report(per_site, runs, since)
    if args.markdown:
        Path(args.markdown).write_text(report, encoding="utf-8")
        print(f"周报已写入 {args.markdown}")
    else:
        print(report)

    if args.clean:
        print(cleanup_log(log_path, args.keep_days, args.keep_runs, args.apply))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
