"""单轮辅种的唯一实现。

一次性 `reseed` 与定时调度器的每一轮都调用 run_once()，
保证引擎生命周期、报告生成、飞书推送与异常口径完全一致，
避免新增行为时只改一条路径。
"""
import logging

from .engine.seeder import ReseedEngine
from .notify.feishu import FeishuNotifier
from .report.generator import ReportGenerator
from .storage.config import Config

logger = logging.getLogger("seedhound")


async def run_once(config: Config, args) -> int:
    """执行一轮辅种，返回进程语义的退出码：0 成功，1 失败。"""
    engine = ReseedEngine(config)
    try:
        await engine.start()
        stats = await engine.run(dry_run=args.dry_run, site_name=args.site)

        report = ReportGenerator().generate(stats)
        print()
        print(report)

        if not args.no_feishu:
            feishu = FeishuNotifier.from_config(config.feishu_config)
            if feishu:
                logger.info("推送报告到飞书...")
                await feishu.send_report_card(stats)
    except Exception as exc:
        logger.error("辅种引擎运行异常: %s", exc, exc_info=True)
        return 1
    finally:
        await engine.close()

    return 0
