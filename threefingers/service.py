"""启动/等待后台守护进程的辅助函数。"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import time
from pathlib import Path

from . import control

log = logging.getLogger(__name__)


def project_root() -> Path:
    return Path(__file__).resolve().parent.parent


def daemon_entry() -> Path:
    return project_root() / "bin" / "threefingers"


def spawn_daemon() -> subprocess.Popen | None:
    entry = daemon_entry()
    if not entry.exists():
        log.error("找不到启动脚本: %s", entry)
        return None
    try:
        return subprocess.Popen(
            [sys.executable, str(entry), "daemon"],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            close_fds=True,
            cwd=str(project_root()),
            env=os.environ.copy(),
        )
    except OSError as exc:
        log.error("启动守护进程失败: %s", exc)
        return None


def ensure_daemon(timeout: float = 3.0) -> bool:
    """确保守护进程在运行；必要时启动它。"""
    if control.is_running(timeout=0.4):
        return True
    if spawn_daemon() is None:
        return False
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if control.is_running(timeout=0.3):
            return True
        time.sleep(0.1)
    return control.is_running(timeout=0.3)
