"""通过 X11 XTest 扩展注入鼠标事件。

只依赖系统自带的 libX11 / libXtst，不需要 xdotool。
XTest 生成的是服务器端的真实输入事件，窗口管理器（xfwm4）和应用
都会像处理真实鼠标一样处理它们，因此可以拖动窗口、选择文字。
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging

log = logging.getLogger(__name__)

_BUTTON_LEFT = 1


class X11Error(RuntimeError):
    """无法连接 X 服务器或注入输入事件。"""


def _load_library(name: str, fallback: str) -> ctypes.CDLL:
    path = ctypes.util.find_library(name) or fallback
    try:
        return ctypes.CDLL(path)
    except OSError as exc:  # pragma: no cover - 取决于系统
        raise X11Error(f"无法加载 {path}: {exc}") from exc


class X11DragBackend:
    """维护一个 X 连接，并提供按下/移动/释放左键的原子操作。"""

    def __init__(self) -> None:
        self._x11 = _load_library("X11", "libX11.so.6")
        self._xtst = _load_library("Xtst", "libXtst.so.6")
        self._setup_prototypes()

        self._display = self._x11.XOpenDisplay(None)
        if not self._display:
            raise X11Error(
                "无法连接 X 服务器（DISPLAY 未设置或没有访问权限）"
            )
        self._pressed = False

    def _setup_prototypes(self) -> None:
        x11 = self._x11
        xtst = self._xtst

        x11.XOpenDisplay.restype = ctypes.c_void_p
        x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
        x11.XCloseDisplay.restype = ctypes.c_int
        x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
        x11.XFlush.restype = ctypes.c_int
        x11.XFlush.argtypes = [ctypes.c_void_p]
        x11.XSync.restype = ctypes.c_int
        x11.XSync.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XDisplayWidth.restype = ctypes.c_int
        x11.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
        x11.XDisplayHeight.restype = ctypes.c_int
        x11.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]

        xtst.XTestFakeButtonEvent.restype = ctypes.c_int
        xtst.XTestFakeButtonEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_uint,
            ctypes.c_int,
            ctypes.c_ulong,
        ]
        xtst.XTestFakeRelativeMotionEvent.restype = ctypes.c_int
        xtst.XTestFakeRelativeMotionEvent.argtypes = [
            ctypes.c_void_p,
            ctypes.c_int,
            ctypes.c_int,
            ctypes.c_ulong,
        ]

    # ------------------------------------------------------------------
    @property
    def pressed(self) -> bool:
        return self._pressed

    def button_down(self) -> None:
        if self._pressed:
            return
        ok = self._xtst.XTestFakeButtonEvent(
            self._display, _BUTTON_LEFT, 1, 0
        )
        if not ok:
            raise X11Error("XTestFakeButtonEvent(按下) 失败")
        self._x11.XFlush(self._display)
        self._pressed = True

    def button_up(self) -> None:
        if not self._pressed:
            return
        ok = self._xtst.XTestFakeButtonEvent(
            self._display, _BUTTON_LEFT, 0, 0
        )
        if not ok:
            raise X11Error("XTestFakeButtonEvent(释放) 失败")
        self._x11.XFlush(self._display)
        self._pressed = False

    def move(self, dx: int, dy: int) -> None:
        if dx == 0 and dy == 0:
            return
        ok = self._xtst.XTestFakeRelativeMotionEvent(
            self._display, int(dx), int(dy), 0
        )
        if not ok:
            raise X11Error("XTestFakeRelativeMotionEvent 失败")
        self._x11.XFlush(self._display)

    def screen_size(self) -> tuple[int, int]:
        return (
            int(self._x11.XDisplayWidth(self._display, 0)),
            int(self._x11.XDisplayHeight(self._display, 0)),
        )

    def close(self) -> None:
        if self._display:
            try:
                self.button_up()
            except Exception:  # pragma: no cover - 关闭时的兜底
                log.exception("关闭 X11 连接前释放鼠标按键失败")
            self._x11.XCloseDisplay(self._display)
            self._display = None
