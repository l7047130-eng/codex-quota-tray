# -*- coding: utf-8 -*-
"""极简日志：写本地文件，超过上限自动轮转，不写任何凭据信息。"""
from __future__ import annotations

import logging
import os
import sys
from logging.handlers import RotatingFileHandler

APP_DIR_NAME = "CodexQuotaTray"
MAX_BYTES = 256 * 1024
BACKUP_COUNT = 2


def app_data_dir() -> str:
    base = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    path = os.path.join(base, APP_DIR_NAME)
    os.makedirs(path, exist_ok=True)
    return path


def log_path() -> str:
    return os.path.join(app_data_dir(), "tray.log")


def setup(level: int = logging.INFO) -> logging.Logger:
    logger = logging.getLogger("codex-quota-tray")
    logger.setLevel(level)
    logger.propagate = False
    if logger.handlers:
        return logger

    formatter = logging.Formatter(
        "%(asctime)s %(levelname)-7s %(threadName)-16s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )
    try:
        handler = RotatingFileHandler(
            log_path(), maxBytes=MAX_BYTES, backupCount=BACKUP_COUNT, encoding="utf-8"
        )
        handler.setFormatter(formatter)
        logger.addHandler(handler)
    except OSError:
        pass

    if not getattr(sys, "frozen", False) and sys.stderr is not None:
        stream = logging.StreamHandler(sys.stderr)
        stream.setFormatter(formatter)
        logger.addHandler(stream)
    return logger
