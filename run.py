import argparse
import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))

from src.engine.seeder import ReseedEngine  # noqa: E402
from src.notify.feishu import FeishuNotifier  # noqa: E402
from src.report.generator import ReportGenerator  # noqa: E402
from src.storage.config import Config  # noqa: E402
from src.utils.logger import setup_logger  # noqa: E402


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
    args = parser.parse_args()

    config = Config(
        config_path="config.yaml",
        sites_path="sites.yaml",
    )

    log_cfg = config.log_config
    logger = setup_logger(
        level=log_cfg.get("level", "INFO"),
        log_file=log_cfg.get("file", "logs/reseed.log"),
    )

    engine = ReseedEngine(config)

    try:
        await engine.start()
        stats = await engine.run(dry_run=args.dry_run)

        generator = ReportGenerator()
        report = generator.generate(stats)

        print()
        print(report)

        if not args.no_feishu:
            feishu = FeishuNotifier.from_config(config.feishu_config)
            if feishu:
                logger.info("推送报告到飞书...")
                await feishu.send_report_card(stats, report)

    except Exception as e:
        logger.error("辅种引擎运行异常: %s", e, exc_info=True)
        return 1
    finally:
        await engine.close()

    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
