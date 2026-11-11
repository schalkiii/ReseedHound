import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.cookie_sync import sync_cookies  # noqa: E402
from src.network.savept import (  # noqa: E402
    disable_sites_in_yaml,
    fetch_dead_sites,
    format_report,
    load_local_sites,
    match_local,
)
from src.runner import run_once  # noqa: E402
from src.scheduler import ReseedScheduler  # noqa: E402
from src.storage.config import Config  # noqa: E402
from src.utils.logger import setup_logger, trim_log_file  # noqa: E402

# CLI 缺省值的单一来源。公共开关的 default 统一用 argparse.SUPPRESS，
# 解析完成后再由这里补齐，避免子解析器用默认值覆盖顶层已解析的开关。
# 业务侧的缺省值统一在 src/storage/config.py 的 DEFAULTS 中定义。
CLI_DEFAULTS = {
    "dry_run": False,
    "no_feishu": False,
    "site": None,
    "mode": None,
    "config": "config.yaml",
    "sites_path": "sites.yaml",
    "cookiecloud_url": None,
    "cookiecloud_uuid": None,
    "cookiecloud_password": None,
    "savept_url": None,
}

COMMANDS = ("reseed", "schedule", "sync-cookies", "check-dead")


def build_parser() -> argparse.ArgumentParser:
    """构造 CLI 解析器。独立成函数以便测试直接对参数组合做断言。"""
    parser = argparse.ArgumentParser(description="SeedHound 辅种引擎")

    def add_common_args(p):
        p.add_argument(
            "--dry-run",
            action="store_true",
            default=argparse.SUPPRESS,
            help="演练模式：查询站点但不添加种子到下载器",
        )
        p.add_argument(
            "--no-feishu",
            action="store_true",
            default=argparse.SUPPRESS,
            help="跳过飞书通知推送",
        )
        p.add_argument(
            "--site",
            type=str,
            default=argparse.SUPPRESS,
            help="指定站点名称进行辅种，多个用逗号分隔（不指定则处理全部站点）",
        )
        p.add_argument(
            "--mode",
            type=str,
            default=argparse.SUPPRESS,
            choices=["pieces_hash", "jackett", "both"],
            help="辅种模式: pieces_hash(默认), jackett, both",
        )
        p.add_argument(
            "--config",
            type=str,
            default=argparse.SUPPRESS,
            help="主配置文件路径",
        )
        p.add_argument(
            "--sites-path",
            type=str,
            default=argparse.SUPPRESS,
            help="站点配置文件路径",
        )

    # 顶层注册一份，兼容 `python run.py --dry-run` 的无子命令写法
    add_common_args(parser)

    common = argparse.ArgumentParser(add_help=False)
    add_common_args(common)

    paths_only = argparse.ArgumentParser(add_help=False)
    paths_only.add_argument(
        "--config", type=str, default=argparse.SUPPRESS, help="主配置文件路径"
    )
    paths_only.add_argument(
        "--sites-path", type=str, default=argparse.SUPPRESS, help="站点配置文件路径"
    )

    subparsers = parser.add_subparsers(dest="command", help="子命令")
    subparsers.add_parser("reseed", parents=[common], help="运行辅种引擎（默认）")
    subparsers.add_parser(
        "schedule",
        parents=[common],
        help="按 config 中 scheduler 配置循环定时运行辅种引擎",
    )

    cookie_parser = subparsers.add_parser(
        "sync-cookies", parents=[paths_only], help="从 CookieCloud 同步站点 Cookie"
    )
    cookie_parser.add_argument(
        "--cookiecloud-url",
        type=str,
        default=argparse.SUPPRESS,
        help="CookieCloud 服务地址",
    )
    cookie_parser.add_argument(
        "--cookiecloud-uuid",
        type=str,
        default=argparse.SUPPRESS,
        help="CookieCloud UUID（缺省读取 config.yaml 的 cookiecloud.uuid）",
    )
    cookie_parser.add_argument(
        "--cookiecloud-password",
        type=str,
        default=argparse.SUPPRESS,
        help="CookieCloud 解密密码（缺省读取 config.yaml 的 cookiecloud.password）",
    )

    dead_parser = subparsers.add_parser(
        "check-dead",
        parents=[paths_only],
        help="对照 savept.icu 死亡名单，推荐禁用已死亡站点",
    )
    dead_parser.add_argument(
        "--apply",
        action="store_true",
        help="把命中的站点写入 sites.yaml（enabled: false）；缺省只打印建议不改配置",
    )
    dead_parser.add_argument(
        "--savept-url",
        type=str,
        default=argparse.SUPPRESS,
        help="savept 数据源地址（缺省读取 config.yaml 的 savept.url）",
    )

    return parser


def parse_args(argv=None) -> argparse.Namespace:
    """解析参数并补齐缺省值。公共开关在顶层与子命令后给出时都能生效。"""
    args = build_parser().parse_args(argv)
    for key, value in CLI_DEFAULTS.items():
        if not hasattr(args, key):
            setattr(args, key, value)
    return args


async def cmd_sync_cookies(args):
    config = Config(args.config, args.sites_path or "sites.yaml")
    setup_logging(config, keep_runs=3)
    logger = logging.getLogger("seedhound")
    logger.info("从 CookieCloud 同步站点 Cookie...")

    # 凭据只从本地 config.yaml 的 cookiecloud 块读取，代码里不保留任何默认值
    cookiecloud = config.cookiecloud_config
    url = args.cookiecloud_url or config.value("cookiecloud", "url")
    uuid = args.cookiecloud_uuid or cookiecloud.get("uuid")
    password = args.cookiecloud_password or cookiecloud.get("password")
    if not (uuid and password):
        logger.error(
            "CookieCloud 未配置：请在 %s 的 cookiecloud 块填写 uuid 与 password",
            args.config,
        )
        return 1
    return await sync_cookies(
        url=url,
        uuid=uuid,
        password=password,
        sites_path=args.sites_path or "sites.yaml",
    )


async def cmd_check_dead(args) -> int:
    """对照 savept.icu 死亡名单，推荐禁用本地已死亡站点（默认只建议不改配置）。"""
    config = Config(args.config, args.sites_path or "sites.yaml")
    setup_logging(config, keep_runs=3)
    logger = logging.getLogger("seedhound")
    sites_path = args.sites_path or "sites.yaml"
    base_url = args.savept_url or config.value("savept", "url")
    timeout = config.value("savept", "timeout")

    try:
        local_sites = load_local_sites(sites_path, only_enabled=True)
    except FileNotFoundError as exc:
        logger.error("%s", exc)
        return 1

    logger.info(
        "拉取 savept.icu 死亡名单并对照 %d 个启用中的本地站点...", len(local_sites)
    )
    dead_sites = await fetch_dead_sites(base_url=base_url, timeout=timeout)
    matches = match_local(dead_sites, local_sites)

    report = format_report(matches, len(local_sites))
    print()
    print(report)

    if getattr(args, "apply", False) and matches:
        changed = disable_sites_in_yaml(
            sites_path, [m.local_name for m in matches]
        )
        if changed:
            logger.info("已禁用 %d 个站点: %s", len(changed), "、".join(changed))
        else:
            logger.warning("没有站点被写入 enabled: false")
    elif matches:
        logger.info("以上仅为建议；确认后加 --apply 写入 sites.yaml")
    return 0


async def cmd_reseed(args, config: Config):
    # 与定时调度器共用 src/runner.py 的单轮实现，保持两条路径严格等价
    return await run_once(config, args)


async def cmd_schedule(args, config: Config):
    scheduler = ReseedScheduler(config, args)
    return await scheduler.run()


def build_config(args) -> Config:
    config = Config(
        config_path=args.config,
        sites_path=args.sites_path,
    )
    if args.mode:
        config._config.setdefault("global", {})["mode"] = args.mode
    return config


def setup_logging(config: Config, keep_runs: int = 3) -> None:
    """初始化日志（控制台 + 文件）。keep_runs<=0 时不裁剪日志（定时模式长驻）。"""
    log_file = config.value("log", "file")
    if keep_runs > 0:
        trim_log_file(log_file, keep_runs=keep_runs)
    setup_logger(
        level=config.value("log", "level"),
        log_file=log_file,
    )


async def main():
    args = parse_args()
    logger = logging.getLogger("seedhound")

    # 容器场景：未显式指定子命令时，按 SEEDHOUND_MODE 环境变量选择运行模式
    # （Dockerfile 默认 reseed；可用 -e SEEDHOUND_MODE=schedule 切换为定时运行）
    if args.command is None:
        env_mode = os.environ.get("SEEDHOUND_MODE", "reseed")
        if env_mode in COMMANDS:
            args.command = env_mode
        else:
            logger.warning("未知 SEEDHOUND_MODE=%r，回退为 reseed", env_mode)
            args.command = "reseed"

    if args.command == "sync-cookies":
        return await cmd_sync_cookies(args)

    if args.command == "check-dead":
        return await cmd_check_dead(args)

    config = build_config(args)
    # scheduler.enabled=true 时，默认/显式 reseed 自动切换为定时运行（sync-cookies 不受影响）
    run_scheduled = args.command == "schedule" or (
        config.scheduler_enabled and args.command == "reseed"
    )
    # 定时模式长驻，不裁剪历史日志；一次性运行仅保留最近 3 次
    setup_logging(config, keep_runs=0 if run_scheduled else 3)
    logger = logging.getLogger("seedhound")

    if config.scheduler_enabled and args.command == "reseed":
        logger.info("scheduler.enabled=true，自动切换为定时运行模式")

    if run_scheduled:
        return await cmd_schedule(args, config)

    return await cmd_reseed(args, config)


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
