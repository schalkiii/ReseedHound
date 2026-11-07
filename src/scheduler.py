import asyncio
import logging
import signal

from .cookie_sync import sync_cookies
from .runner import run_once
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
        def handler(*_):
            self._request_stop()

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

    async def _sync_cookies(self):
        """运行前从 CookieCloud 同步站点 Cookie（受 config 的 cookiecloud.enabled 控制）。

        同步失败时仅告警并继续本轮，避免 CookieCloud 不可达导致整轮辅种失败。
        """
        try:
            cc = self._config.cookiecloud_config
            uuid = cc.get("uuid")
            password = cc.get("password")
            if not (uuid and password):
                logger.warning("CookieCloud 未配置 uuid/password，跳过本轮 Cookie 同步")
                return
            result = await sync_cookies(
                url=self._config.value("cookiecloud", "url"),
                uuid=uuid,
                password=password,
                sites_path=self._config.sites_path,
            )
            if result == 0:
                # 同步写回 sites.yaml 后重新加载内存配置，使本轮引擎使用最新 Cookie
                self._config.reload_sites()
                logger.info("CookieCloud 同步完成，已重新加载 sites 配置")
            else:
                logger.warning("CookieCloud 同步未获取到 Cookie，本轮使用已有配置")
        except Exception as exc:
            logger.warning("CookieCloud 同步异常(本轮使用已有 Cookie): %s", exc)

    async def _run_once(self) -> int:
        """执行单轮辅种（与一次性运行共用 run_once 实现）。

        CookieCloud 同步是定时模式特有的前置步骤：写回 sites.yaml 后
        reload_sites()，随后的 run_once 自然使用最新 Cookie。
        """
        if self._config.cookiecloud_enabled:
            await self._sync_cookies()
        return await run_once(self._config, self._args)

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

        loop = asyncio.get_running_loop()
        self._install_signal_handlers(loop)

        if startup_delay > 0:
            logger.info("等待启动延迟 %.0f 秒...", startup_delay)
            # _wait_with_stop 返回 True 表示等待期间收到了退出信号
            if await self._wait_with_stop(startup_delay):
                logger.info("定时调度器在启动延迟期间收到退出信号（共执行 0 轮）")
                return 0

        if not run_on_start:
            logger.info(
                "run_on_start=false，先等待 %.0f 分钟后执行首轮...", interval / 60
            )
            if await self._wait_with_stop(interval):
                logger.info("定时调度器在首轮等待期间收到退出信号（共执行 0 轮）")
                return 0

        while not self._stop_event.is_set():
            self._cycle += 1
            logger.info("===== 第 %d 轮定时辅种开始 =====", self._cycle)
            try:
                result = await self._run_once()
                if result != 0:
                    logger.warning("第 %d 轮辅种返回非零状态，继续下一轮", self._cycle)
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
