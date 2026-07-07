import asyncio
import logging
import signal
from typing import Optional

from .engine.seeder import ReseedEngine
from .notify.feishu import FeishuNotifier
from .report.generator import ReportGenerator
from .storage.config import Config

logger = logging.getLogger("seedhound.scheduler")


class ReseedScheduler:
    """原生定时辅种调度器。

    基于 config 中的 scheduler 配置块，循环运行辅种引擎，
    每轮重新创建引擎以保证状态干净，支持 SIGINT/SIGTERM 优雅退出。
    """

    def __init__(self, config: Config, args):
        self._config = config
        self._args = args
        self._stop_event = asyncio.Event()
        self._cycle = 0

    def _install_signal_handlers(self, loop: asyncio.AbstractEventLoop):
        """注册退出信号。Windows 不支持 loop.add_signal_handler，回退到 signal 模块。"""
        handler = lambda *_: self._request_stop()
        try:
            for sig in (signal.SIGINT, signal.SIGTERM):
                loop.add_signal_handler(sig, self._request_stop)
        except NotImplementedError:
            for sig in (signal.SIGINT, signal.SIGTERM):
                signal.signal(sig, handler)

    def _request_stop(self):
        if not self._stop_event.is_set():
            logger.info("收到退出信号，调度器将在本轮结束后停止...")
            self._stop_event.set()

    async def _run_once(self):
        """执行单轮辅种（与一次性运行完全一致的代码路径）。"""
        engine = ReseedEngine(self._config)
        try:
            await engine.start()
            stats = await engine.run(
                dry_run=self._args.dry_run, site_name=self._args.site
            )

            generator = ReportGenerator()
            report = generator.generate(stats)
            print()
            print(report)

            if not self._args.no_feishu:
                feishu = FeishuNotifier.from_config(self._config.feishu_config)
                if feishu:
                    logger.info("推送报告到飞书...")
                    await feishu.send_report_card(stats)
        finally:
            await engine.close()

    async def run(self) -> int:
        interval = self._config.scheduler_interval_seconds
        startup_delay = self._config.scheduler_startup_delay
        run_on_start = self._config.scheduler_run_on_start

        logger.info(
            "定时调度器启动: 间隔=%.0f 分钟, 启动延迟=%.0f 秒, 首轮立即运行=%s",
            interval / 60,
            startup_delay,
            run_on_start,
        )

        loop = asyncio.get_event_loop()
        self._install_signal_handlers(loop)

        if startup_delay > 0:
            logger.info("等待启动延迟 %.0f 秒...", startup_delay)
            if not await self._wait_with_stop(startup_delay):
                return 0

        while not self._stop_event.is_set():
            self._cycle += 1
            logger.info("===== 第 %d 轮定时辅种开始 =====", self._cycle)
            try:
                await self._run_once()
            except Exception as exc:
                logger.error("本轮辅种异常: %s", exc, exc_info=True)

            if self._stop_event.is_set():
                break

            logger.info("等待 %.0f 分钟后进行下一轮...", interval / 60)
            if await self._wait_with_stop(interval):
                break

        logger.info("定时调度器已停止（共执行 %d 轮）", self._cycle)
        return 0

    async def _wait_with_stop(self, seconds: float) -> bool:
        """等待指定秒数，期间若收到退出信号立即返回 True（表示应停止）。"""
        try:
            await asyncio.wait_for(self._stop_event.wait(), timeout=seconds)
        except asyncio.TimeoutError:
            return False
        return True
