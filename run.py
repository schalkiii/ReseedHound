import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.engine.seeder import ReseedEngine  # noqa: E402
from src.notify.feishu import FeishuNotifier  # noqa: E402
from src.report.generator import ReportGenerator  # noqa: E402
from src.storage.config import Config  # noqa: E402
from src.utils.logger import setup_logger, trim_log_file  # noqa: E402


async def main():
    parser = argparse.ArgumentParser(description="SeedHound 辅种引擎")
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="演练模式：查询站点但不添加种子到下载器",
    )
    parser.add_argument(
        "--no-feishu",
        action="store_true",
        help="跳过飞书通知推送",
    )
    parser.add_argument(
        "--site",
        type=str,
        default=None,
        help="指定站点名称进行辅种，多个用逗号分隔（不指定则处理全部站点）",
    )
    parser.add_argument(
        "--mode",
        type=str,
        default=None,
        choices=["pieces_hash", "jackett", "both"],
        help="辅种模式: pieces_hash(默认), jackett, both",
    )
    args = parser.parse_args()

    config = Config(
        config_path="config.yaml",
        sites_path="sites.yaml",
    )

    if args.mode:
        config._config.setdefault("global", {})["mode"] = args.mode

    log_cfg = config.log_config
    log_file = log_cfg.get("file", "logs/seedhound.log")
    trim_log_file(log_file, keep_runs=3)
    logger = setup_logger(
        level=log_cfg.get("level", "INFO"),
        log_file=log_file,
    )

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


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
