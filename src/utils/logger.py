import logging
import re
import sys
from pathlib import Path
from typing import Optional

# 一轮辅种在日志中的起始标志。日志裁剪与周报清理共用此常量，
# 修改日志文案时只需改这一处。
RUN_MARKER = "SeedHound 辅种引擎启动"

# 下载链接形如 .../download.php?id=1&passkey=xxxx，passkey 等同于账号权限。
# HDArea 通道另外引入 apikey 查询参数、dl_token 短效凭证与 X-API-Key 请求头，
# 一并纳入统一脱敏，避免新增日志点各自处理。
_SECRET_PATTERNS = (
    re.compile(r"(passkey=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"(api[_-]?key=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"(dl_token=)[^&\s\"']+", re.IGNORECASE),
    re.compile(r"(X-API-Key:\s*)\S+", re.IGNORECASE),
)


def redact_secrets(text: str) -> str:
    """把日志文本里的 passkey / API Key / 短效下载凭证打码。"""
    redacted = str(text)
    for pattern in _SECRET_PATTERNS:
        redacted = pattern.sub(r"\1***", redacted)
    return redacted


class _RedactSecretsFilter(logging.Filter):
    """README 承诺「passkey 不出现在日志中」，用过滤器把承诺变成不变量。

    挂在 handler 上而不是某个 logger 上，子 logger 冒泡上来的记录同样会被脱敏，
    也就不再依赖每个调用点自觉。
    """

    def filter(self, record: logging.LogRecord) -> bool:
        message = record.getMessage()
        redacted = redact_secrets(message)
        if redacted != message:
            record.msg = redacted
            record.args = ()
        return True


def trim_log_file(log_path: str, keep_runs: int = 3):
    log_file = Path(log_path)
    if not log_file.exists():
        return
    try:
        text = log_file.read_text(encoding="utf-8")
        lines = text.splitlines(keepends=True)
        run_starts = [
            i for i, line in enumerate(lines)
            if RUN_MARKER in line
        ]
        if len(run_starts) <= keep_runs:
            return
        keep_from = run_starts[-keep_runs]
        trimmed = "".join(lines[keep_from:])
        log_file.write_text(trimmed, encoding="utf-8")
    except Exception as exc:
        logging.getLogger("seedhound").debug("日志裁剪失败: %s", exc)


def setup_logger(
    level: Optional[str] = None, log_file: Optional[str] = None
) -> logging.Logger:
    # logger 是最底层模块，不在顶层导入 storage.config 以免形成跨层依赖；
    # 缺省值仍从 DEFAULTS 单一来源取得。
    from ..storage.config import DEFAULTS

    level = level or DEFAULTS["log"]["level"]
    log_file = DEFAULTS["log"]["file"] if log_file is None else log_file

    # Windows 控制台默认 GBK，报告里的 emoji（🟢🔴）会让 print 抛 UnicodeEncodeError，
    # 连带跳过飞书推送；stdout 重定向到文件时同样如此。统一按 UTF-8 输出，不可编码字符降级。
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")

    logger = logging.getLogger("seedhound")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

    # 同步 Cookie 与辅种共用同一 logger；重复初始化时先卸掉旧 handler，避免日志重复输出
    for existing in list(logger.handlers):
        logger.removeHandler(existing)
        existing.close()

    formatter = logging.Formatter(
        fmt="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if log_file:
        log_path = Path(log_file)
        log_path.parent.mkdir(parents=True, exist_ok=True)
        file_handler = logging.FileHandler(log_file, encoding="utf-8")
        file_handler.setFormatter(formatter)
        file_handler.addFilter(_RedactSecretsFilter())
        logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    console_handler.addFilter(_RedactSecretsFilter())
    logger.addHandler(console_handler)

    return logger
