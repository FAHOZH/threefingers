"""三指拖移守护进程。"""

from __future__ import annotations

import argparse
import logging
import os
import selectors
import signal
import sys
import time
from logging.handlers import RotatingFileHandler

from . import __version__, config
from .control import AlreadyRunningError, ControlServer
from .evdev_input import DeviceError, TouchpadDevice, find_touchpad
from .gesture import GestureEngine, MotionSettings
from .screen import detect_screen_scale
from .x11 import X11DragBackend

log = logging.getLogger(__name__)

_RETRY_INTERVAL = 2.0
_SELECT_TIMEOUT = 0.5


def setup_logging(foreground: bool, verbose: bool) -> None:
    config.state_dir().mkdir(parents=True, exist_ok=True)
    formatter = logging.Formatter(
        "%(asctime)s %(levelname)s %(name)s: %(message)s"
    )
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if verbose else logging.INFO)
    for handler in list(root.handlers):
        root.removeHandler(handler)

    file_handler = RotatingFileHandler(
        config.log_file(),
        maxBytes=512 * 1024,
        backupCount=2,
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    root.addHandler(file_handler)

    if foreground:
        stream = logging.StreamHandler()
        stream.setFormatter(formatter)
        root.addHandler(stream)


class Daemon:
    def __init__(self) -> None:
        self.cfg = config.load_config()
        self.running = True
        self.sel = selectors.DefaultSelector()
        self.control: ControlServer | None = None
        self.backend: X11DragBackend | None = None
        self.device: TouchpadDevice | None = None
        self.engine: GestureEngine | None = None
        self.screen_scale = None
        self.errors: dict[str, str] = {}
        self._device_registered = False
        self.next_retry = 0.0
        self._reload_requested = False

    # ------------------------------------------------------------------
    def run(self) -> int:
        signal.signal(signal.SIGTERM, self._on_stop_signal)
        signal.signal(signal.SIGINT, self._on_stop_signal)
        signal.signal(signal.SIGHUP, self._on_reload_signal)

        try:
            self.control = ControlServer(self._handle_command)
        except AlreadyRunningError:
            log.info("三指拖移服务已经在运行，退出")
            return 0
        except Exception:
            log.exception("无法创建控制套接字")
            return 1

        self.sel.register(
            self.control.fileno(), selectors.EVENT_READ, "control"
        )
        log.info("三指拖移服务启动 (v%s)", __version__)

        try:
            while self.running:
                try:
                    events = self.sel.select(_SELECT_TIMEOUT)
                except InterruptedError:
                    events = []
                except OSError:
                    log.exception("等待输入事件失败")
                    events = []

                for key, _mask in events:
                    if not self.running:
                        break
                    if key.data == "control":
                        self.control.handle_ready()
                    elif key.data == "device":
                        self._read_device()

                if self._reload_requested:
                    self._reload_requested = False
                    self._reload_from_disk()

                if self.engine is not None and self.engine.fatal_error:
                    self._handle_engine_failure()

                if self.engine is None and time.monotonic() >= self.next_retry:
                    self._try_init()
        finally:
            self._shutdown()
        return 0

    # ------------------------------------------------------------------
    def _on_stop_signal(self, _signum, _frame) -> None:
        self.running = False

    def _on_reload_signal(self, _signum, _frame) -> None:
        self._reload_requested = True

    def _on_gesture_state(self, state: str) -> None:
        log.debug("手势状态: %s", state)

    # ------------------------------------------------------------------
    def _try_init(self) -> bool:
        self.next_retry = time.monotonic() + _RETRY_INTERVAL

        if self.backend is None:
            try:
                self.backend = X11DragBackend()
                self._clear_error("x11")
                log.info(
                    "已连接 X11（DISPLAY=%s）",
                    os.environ.get("DISPLAY", "?"),
                )
            except Exception as exc:
                self._set_error("x11", str(exc))
                return False

        if self.device is None:
            try:
                path = find_touchpad(self.cfg["device"])
                if not path:
                    raise DeviceError(
                        "未找到触控板；可在配置文件中用 device 指定 "
                        "/dev/input/eventX"
                    )
                device = TouchpadDevice(path)
                device.open()
                self.device = device
                self._clear_error("device")
                assert device.geometry is not None
                log.info(
                    "触控板: %s (%s) %s",
                    device.name,
                    device.path,
                    device.geometry.describe(),
                )
            except Exception as exc:
                self._set_error("device", str(exc))
                return False

        try:
            if self.screen_scale is None:
                self.screen_scale = detect_screen_scale(
                    self.cfg["pixels_per_mm"]
                )
                log.info(
                    "屏幕缩放: %.2f x %.2f px/mm（来源: %s）",
                    self.screen_scale.px_per_mm_x,
                    self.screen_scale.px_per_mm_y,
                    self.screen_scale.source,
                )
            assert self.device is not None
            assert self.device.geometry is not None
            settings = MotionSettings.from_geometry(
                self.device.geometry, self.screen_scale, self.cfg
            )
            engine = GestureEngine(
                self.backend,
                settings,
                on_state_change=self._on_gesture_state,
            )
            engine.set_enabled(bool(self.cfg["enabled"]))
            self.engine = engine
            self.sel.register(
                self.device.fileno, selectors.EVENT_READ, "device"
            )
            self._device_registered = True
            log.info(
                "三指拖移已就绪（%s）",
                "已开启" if self.cfg["enabled"] else "已关闭",
            )
            return True
        except Exception as exc:
            self._set_error("device", f"初始化失败: {exc}")
            self._teardown_runtime(close_device=True, close_backend=False)
            return False

    def _read_device(self) -> None:
        if self.device is None or self.engine is None:
            return
        try:
            reports = self.device.read_reports()
        except DeviceError as exc:
            log.error("读取触控板失败: %s", exc)
            self._set_error("device", str(exc))
            self._teardown_runtime(close_device=True, close_backend=False)
            return
        for report in reports:
            if report is None:
                self.engine.cancel()
            else:
                self.engine.handle_report(report, time.monotonic())

    def _handle_engine_failure(self) -> None:
        assert self.engine is not None
        message = self.engine.fatal_error or "输入注入失败"
        log.error("输入注入失败，尝试重新连接: %s", message)
        self._set_error("x11", message)
        self._teardown_runtime(close_device=True, close_backend=True)

    # ------------------------------------------------------------------
    def _teardown_runtime(
        self, close_device: bool, close_backend: bool
    ) -> None:
        if self._device_registered and self.device is not None:
            try:
                self.sel.unregister(self.device.fileno)
            except (KeyError, ValueError, OSError):
                pass
            self._device_registered = False
        if self.engine is not None:
            try:
                self.engine.shutdown()
            except Exception:
                log.exception("停止手势引擎失败")
            self.engine = None
        if close_device and self.device is not None:
            try:
                self.device.close()
            except OSError:
                pass
            self.device = None
        if close_backend and self.backend is not None:
            try:
                self.backend.close()
            except Exception:
                log.exception("关闭 X11 连接失败")
            self.backend = None
            self.screen_scale = None
        self.next_retry = time.monotonic() + _RETRY_INTERVAL

    def _shutdown(self) -> None:
        log.info("三指拖移服务退出")
        self._teardown_runtime(close_device=True, close_backend=True)
        try:
            self.sel.close()
        except OSError:
            pass
        if self.control is not None:
            self.control.close()
            self.control = None

    # ------------------------------------------------------------------
    def _handle_command(self, request: dict) -> dict:
        cmd = request.get("cmd")
        if cmd in ("status", "ping"):
            return self._status()
        if cmd == "set_enabled":
            value = request.get("value", request.get("enabled"))
            if not isinstance(value, bool):
                return {"ok": False, "error": "value 必须是布尔值"}
            self._update_config({"enabled": value})
            return self._status()
        if cmd == "toggle":
            self._update_config({"enabled": not self.cfg["enabled"]})
            return self._status()
        if cmd == "set_config":
            allowed = (
                "sensitivity",
                "drag_threshold_mm",
                "invert_x",
                "invert_y",
                "pixels_per_mm",
            )
            updates = {key: request[key] for key in allowed if key in request}
            if not updates:
                return {"ok": False, "error": "没有可更新的配置项"}
            self._update_config(updates)
            return self._status()
        if cmd == "reload":
            self._reload_from_disk()
            return self._status()
        if cmd == "quit":
            self.running = False
            return {"ok": True, "message": "服务正在退出"}
        return {"ok": False, "error": f"未知命令: {cmd}"}

    def _update_config(self, updates: dict) -> None:
        new_cfg = config.validate_config({**self.cfg, **updates})
        try:
            config.save_config(new_cfg)
        except OSError as exc:
            log.error("保存配置失败: %s", exc)
        self._apply_config(new_cfg)

    def _apply_config(self, new_cfg: dict) -> None:
        old_cfg = self.cfg
        self.cfg = new_cfg
        if old_cfg["pixels_per_mm"] != new_cfg["pixels_per_mm"]:
            self.screen_scale = None
        if old_cfg["device"] != new_cfg["device"]:
            log.info("设备配置变化，重新连接触控板")
            self._teardown_runtime(close_device=True, close_backend=False)
            self.next_retry = 0.0
            return
        if self.engine is None:
            self.next_retry = 0.0
            return
        if self.screen_scale is None:
            self.screen_scale = detect_screen_scale(
                self.cfg["pixels_per_mm"]
            )
        assert self.device is not None
        assert self.device.geometry is not None
        settings = MotionSettings.from_geometry(
            self.device.geometry, self.screen_scale, self.cfg
        )
        self.engine.update_settings(settings)
        # 只有开关状态变化时才调用 set_enabled()，避免调节速度时
        # 中断正在进行的三指拖拽。
        if self.engine.enabled != bool(self.cfg["enabled"]):
            self.engine.set_enabled(bool(self.cfg["enabled"]))
        log.info(
            "配置已更新：%s，速度 %.2fx，阈值 %.1fmm",
            "已开启" if self.cfg["enabled"] else "已关闭",
            self.cfg["sensitivity"],
            self.cfg["drag_threshold_mm"],
        )

    def _reload_from_disk(self) -> None:
        new_cfg = config.load_config()
        log.info("重新加载配置")
        self._apply_config(new_cfg)

    # ------------------------------------------------------------------
    def _set_error(self, component: str, message: str) -> None:
        if self.errors.get(component) == message:
            return
        self.errors[component] = message
        if component == "x11":
            log.error("X11 不可用: %s", message)
        else:
            log.error("触控板不可用: %s", message)

    def _clear_error(self, component: str) -> None:
        if self.errors.pop(component, None) is not None:
            log.info("%s 已恢复", "X11" if component == "x11" else "触控板")

    def _status(self) -> dict:
        device = self.device
        return {
            "ok": True,
            "service": "running",
            "pid": os.getpid(),
            "version": __version__,
            "enabled": bool(self.cfg["enabled"]),
            "ready": self.engine is not None,
            "dragging": bool(self.engine and self.engine.dragging),
            "device": {
                "ok": device is not None,
                "path": device.path if device else None,
                "name": device.name if device else None,
                "error": self.errors.get("device"),
            },
            "x11": {
                "ok": self.backend is not None,
                "error": self.errors.get("x11"),
            },
            "sensitivity": self.cfg["sensitivity"],
            "drag_threshold_mm": self.cfg["drag_threshold_mm"],
            "pixels_per_mm": (
                self.screen_scale.px_per_mm_x if self.screen_scale else None
            ),
            "log_file": str(config.log_file()),
        }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="三指拖移守护进程")
    parser.add_argument(
        "--foreground",
        action="store_true",
        help="把日志输出到终端（默认写入日志文件）",
    )
    parser.add_argument(
        "--verbose", "-v", action="store_true", help="输出调试日志"
    )
    args = parser.parse_args(argv)
    setup_logging(args.foreground, args.verbose)
    return Daemon().run()


if __name__ == "__main__":
    sys.exit(main())
