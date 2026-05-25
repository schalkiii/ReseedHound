import logging
import sys
from pathlib import Path

RUN_MARKER = "SeedHound 辅种引擎启动"


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
        logger.addHandler(file_handler)

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)

    return logger
