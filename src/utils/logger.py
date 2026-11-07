import logging
import re
import sys
from pathlib import Path

RUN_MARKER = "SeedHound 辅种引擎启动"

# 下载链接形如 .../download.php?id=1&passkey=xxxx，passkey 等同于账号权限
_PASSKEY_PATTERN = re.compile(r"(passkey=)[^&\s\"']+", re.IGNORECASE)


def redact_secrets(text: str) -> str:
    """把日志文本里的 passkey 打码。"""
    return _PASSKEY_PATTERN.sub(r"\1***", str(text))


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
    except Exception:
        pass


def setup_logger(level: str = "INFO", log_file: str = "logs/seedhound.log") -> logging.Logger:
    logger = logging.getLogger("seedhound")
    logger.setLevel(getattr(logging, level.upper(), logging.INFO))

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
