"""守护进程与命令行/托盘/GUI 之间的本地控制协议。

使用 Unix domain socket，一行一个 JSON 请求/响应。套接字位于
``$XDG_RUNTIME_DIR/threefingers/control.sock``，仅当前用户可访问。
"""

from __future__ import annotations

import json
import logging
import os
import socket
from typing import Callable

from . import config

log = logging.getLogger(__name__)

_MAX_MESSAGE = 65536


class AlreadyRunningError(RuntimeError):
    pass


class ControlServer:
    def __init__(self, handler: Callable[[dict], dict]) -> None:
        self._handler = handler
        config.ensure_runtime_dir()
        self.path = config.socket_path()
        self._sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            self._sock.bind(str(self.path))
        except OSError:
            if _probe(self.path):
                self._sock.close()
                raise AlreadyRunningError("服务已在运行")
            try:
                self.path.unlink()
            except FileNotFoundError:
                pass
            self._sock.bind(str(self.path))
        os.chmod(self.path, 0o600)
        self._sock.listen(8)
        self._sock.setblocking(False)

    def fileno(self) -> int:
        return self._sock.fileno()

    def handle_ready(self) -> None:
        while True:
            try:
                conn, _addr = self._sock.accept()
            except BlockingIOError:
                return
            except OSError:
                log.exception("接受控制连接失败")
                return
            try:
                conn.settimeout(0.5)
                payload = _recv_line(conn)
                if payload is None:
                    continue
                try:
                    request = json.loads(payload.decode("utf-8"))
                    if not isinstance(request, dict):
                        raise ValueError("请求必须是 JSON 对象")
                    response = self._handler(request)
                except Exception as exc:
                    log.exception("处理控制请求失败")
                    response = {"ok": False, "error": str(exc)}
                conn.sendall(
                    json.dumps(response, ensure_ascii=False).encode("utf-8")
                    + b"\n"
                )
            except (OSError, ValueError):
                log.debug("控制连接异常", exc_info=True)
            finally:
                try:
                    conn.close()
                except OSError:
                    pass

    def close(self) -> None:
        try:
            self._sock.close()
        except OSError:
            pass
        try:
            self.path.unlink()
        except OSError:
            pass


def _recv_line(conn: socket.socket) -> bytes | None:
    chunks = bytearray()
    while len(chunks) <= _MAX_MESSAGE:
        try:
            data = conn.recv(4096)
        except socket.timeout:
            return None
        if not data:
            break
        chunks.extend(data)
        if b"\n" in data:
            break
    if not chunks:
        return None
    return bytes(chunks).split(b"\n", 1)[0]


def _probe(path) -> bool:
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(0.3)
    try:
        sock.connect(str(path))
        return True
    except OSError:
        return False
    finally:
        sock.close()


def request(payload: dict, timeout: float = 1.5) -> dict | None:
    """发送一条请求；服务未运行时返回 None。"""
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.settimeout(timeout)
    try:
        sock.connect(str(config.socket_path()))
    except OSError:
        sock.close()
        return None
    try:
        sock.sendall(
            json.dumps(payload, ensure_ascii=False).encode("utf-8") + b"\n"
        )
        chunks = bytearray()
        while len(chunks) <= _MAX_MESSAGE:
            data = sock.recv(4096)
            if not data:
                break
            chunks.extend(data)
            if b"\n" in data:
                break
        if not chunks:
            return None
        return json.loads(bytes(chunks).split(b"\n", 1)[0].decode("utf-8"))
    except (OSError, ValueError):
        log.debug("控制请求失败", exc_info=True)
        return None
    finally:
        sock.close()


def is_running(timeout: float = 0.4) -> bool:
    response = request({"cmd": "status"}, timeout=timeout)
    return bool(response and response.get("ok"))
