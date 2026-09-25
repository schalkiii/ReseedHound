import argparse
import asyncio
import logging
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.cookie_sync import sync_cookies  # noqa: E402
from src.engine.seeder import ReseedEngine  # noqa: E402
from src.notify.feishu import FeishuNotifier  # noqa: E402
from src.report.generator import ReportGenerator  # noqa: E402
from src.scheduler import ReseedScheduler  # noqa: E402
from src.storage.config import Config  # noqa: E402
from src.utils.logger import setup_logger, trim_log_file  # noqa: E402


async def cmd_sync_cookies(args):
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    return await sync_cookies(
        url=args.cookiecloud_url,
        uuid=args.cookiecloud_uuid,
        password=args.cookiecloud_password,
        sites_path=args.sites_path or "sites.yaml",
    )


async def cmd_reseed(args, config: Config):
    logger = logging.getLogger("seedhound")

    engine = ReseedEngine(config)

    try:
        await engine.start()
        stats = await engine.run(dry_run=args.dry_run, site_name=args.site)

        generator = ReportGenerator()
        report = generator.generate(stats)

        print()
        print(report)

        if not args.no_feishu:
            feishu = FeishuNotifier.from_config(config.feishu_config)
            if feishu:
                logger.info("推送报告到飞书...")
                await feishu.send_report_card(stats)

    except Exception as e:
        logger.error("辅种引擎运行异常: %s", e, exc_info=True)
        return 1
    finally:
        await engine.close()

    return 0


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
    log_cfg = config.log_config
    log_file = log_cfg.get("file", "logs/seedhound.log")
    if keep_runs > 0:
        trim_log_file(log_file, keep_runs=keep_runs)
    setup_logger(
        level=log_cfg.get("level", "INFO"),
        log_file=log_file,
    )


async def main():
    parser = argparse.ArgumentParser(description="SeedHound 辅种引擎")
    subparsers = parser.add_subparsers(dest="command", help="子命令")

    def add_common_args(p):
        p.add_argument(
            "--dry-run",
            action="store_true",
            help="演练模式：查询站点但不添加种子到下载器",
        )
        p.add_argument(
            "--no-feishu",
            action="store_true",
            help="跳过飞书通知推送",
        )
        p.add_argument(
            "--site",
            type=str,
            default=None,
            help="指定站点名称进行辅种，多个用逗号分隔（不指定则处理全部站点）",
        )
        p.add_argument(
            "--mode",
            type=str,
            default=None,
            choices=["pieces_hash", "jackett", "both"],
            help="辅种模式: pieces_hash(默认), jackett, both",
        )
        p.add_argument(
            "--config",
            type=str,
            default="config.yaml",
            help="主配置文件路径",
        )
        p.add_argument(
            "--sites-path",
            type=str,
            default="sites.yaml",
            help="站点配置文件路径",
        )

    reseed_parser = subparsers.add_parser("reseed", help="运行辅种引擎（默认）")
    add_common_args(reseed_parser)

    schedule_parser = subparsers.add_parser(
        "schedule", help="按 config 中 scheduler 配置循环定时运行辅种引擎"
    )
    add_common_args(schedule_parser)

    cookie_parser = subparsers.add_parser(
        "sync-cookies", help="从 CookieCloud 同步站点 Cookie"
    )
    cookie_parser.add_argument(
        "--cookiecloud-url",
        type=str,
        default="http://127.0.0.1:8082/cookie",
        help="CookieCloud 服务地址",
    )
    cookie_parser.add_argument(
        "--cookiecloud-uuid",
        type=str,
        default="3bXA8nVbF8XnfBmXk5aA1y",
        help="CookieCloud UUID",
    )
    cookie_parser.add_argument(
        "--cookiecloud-password",
        type=str,
        default="pNCWDegC882djqqALYtxjs",
        help="CookieCloud 密码",
    )
    cookie_parser.add_argument(
        "--config",
        type=str,
        default="config.yaml",
        help="主配置文件路径",
    )
    cookie_parser.add_argument(
        "--sites-path",
        type=str,
        default="sites.yaml",
        help="站点配置文件路径",
    )

    # 顶层参数：兼容 `python run.py --dry-run` 的无子命令写法（向后兼容）
    parser.add_argument("--dry-run", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--no-feishu", action="store_true", help=argparse.SUPPRESS)
    parser.add_argument("--site", type=str, default=None, help=argparse.SUPPRESS)
    parser.add_argument(
        "--mode", type=str, default=None,
        choices=["pieces_hash", "jackett", "both"], help=argparse.SUPPRESS,
    )
    parser.add_argument("--config", type=str, default="config.yaml", help=argparse.SUPPRESS)
    parser.add_argument("--sites-path", type=str, default="sites.yaml", help=argparse.SUPPRESS)

    args = parser.parse_args()
    logger = logging.getLogger("seedhound")

    # 容器场景：未显式指定子命令时，按 SEEDHOUND_MODE 环境变量选择运行模式
    # （Dockerfile 默认 reseed；可用 -e SEEDHOUND_MODE=schedule 切换为定时运行）
    if args.command is None:
        env_mode = os.environ.get("SEEDHOUND_MODE", "reseed")
        if env_mode in ("reseed", "schedule", "sync-cookies"):
            args.command = env_mode
        else:
            logger.warning("未知 SEEDHOUND_MODE=%r，回退为 reseed", env_mode)
            args.command = "reseed"

    if args.command == "sync-cookies":
        return await cmd_sync_cookies(args)

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
