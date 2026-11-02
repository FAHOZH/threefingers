"""估算屏幕每毫米像素数，用来把手指的物理移动映射为指针移动。

macOS 的三指拖移大致是 1:1 的物理位移（手指在触控板上移动 1mm，
光标在屏幕上移动约 1mm）。Xlib 的 XDisplayWidthMM() 在很多驱动上
只返回 96 DPI 的假设值，所以优先使用 xrandr 报告的 EDID 尺寸。
"""

from __future__ import annotations

import logging
import re
import shutil
import subprocess
from dataclasses import dataclass

log = logging.getLogger(__name__)

_CONNECTED_RE = re.compile(
    r"^(\S+)\s+connected(?:\s+primary)?\s+"
    r"(\d+)x(\d+)\+\d+\+\d+.*?"
    r"(\d+)mm\s+x\s+(\d+)mm"
)

_DEFAULT_PX_PER_MM = 8.0


@dataclass(frozen=True)
class ScreenScale:
    px_per_mm_x: float
    px_per_mm_y: float
    width_px: int
    height_px: int
    source: str


def _from_xrandr() -> ScreenScale | None:
    if not shutil.which("xrandr"):
        return None
    try:
        result = subprocess.run(
            ["xrandr", "--query"],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if result.returncode != 0:
        return None
    for line in result.stdout.splitlines():
        match = _CONNECTED_RE.match(line)
        if not match:
            continue
        _name, width, height, width_mm, height_mm = match.groups()
        width_i, height_i = int(width), int(height)
        width_mm_f, height_mm_f = float(width_mm), float(height_mm)
        if width_mm_f > 0 and height_mm_f > 0:
            return ScreenScale(
                px_per_mm_x=width_i / width_mm_f,
                px_per_mm_y=height_i / height_mm_f,
                width_px=width_i,
                height_px=height_i,
                source="xrandr",
            )
    return None


def _from_xlib() -> ScreenScale | None:
    import ctypes
    import ctypes.util

    path = ctypes.util.find_library("X11") or "libX11.so.6"
    try:
        x11 = ctypes.CDLL(path)
    except OSError:
        return None
    x11.XOpenDisplay.restype = ctypes.c_void_p
    x11.XOpenDisplay.argtypes = [ctypes.c_char_p]
    x11.XCloseDisplay.argtypes = [ctypes.c_void_p]
    x11.XDisplayWidth.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XDisplayWidth.restype = ctypes.c_int
    x11.XDisplayHeight.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XDisplayHeight.restype = ctypes.c_int
    x11.XDisplayWidthMM.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XDisplayWidthMM.restype = ctypes.c_int
    x11.XDisplayHeightMM.argtypes = [ctypes.c_void_p, ctypes.c_int]
    x11.XDisplayHeightMM.restype = ctypes.c_int

    display = x11.XOpenDisplay(None)
    if not display:
        return None
    try:
        width = int(x11.XDisplayWidth(display, 0))
        height = int(x11.XDisplayHeight(display, 0))
        width_mm = int(x11.XDisplayWidthMM(display, 0))
        height_mm = int(x11.XDisplayHeightMM(display, 0))
    finally:
        x11.XCloseDisplay(display)
    if width_mm > 0 and height_mm > 0:
        return ScreenScale(
            px_per_mm_x=width / width_mm,
            px_per_mm_y=height / height_mm,
            width_px=width,
            height_px=height,
            source="xlib",
        )
    return None


def detect_screen_scale(override: float = 0.0) -> ScreenScale:
    """返回屏幕缩放信息；override > 0 时直接使用指定值。"""
    if override > 0:
        return ScreenScale(
            px_per_mm_x=override,
            px_per_mm_y=override,
            width_px=0,
            height_px=0,
            source="config",
        )
    scale = _from_xrandr()
    if scale:
        return scale
    scale = _from_xlib()
    if scale:
        log.warning(
            "无法从 xrandr 获取物理尺寸，退回 Xlib 的数值（可能不准确）"
        )
        return scale
    log.warning(
        "无法检测屏幕尺寸，使用默认值 %.1f px/mm", _DEFAULT_PX_PER_MM
    )
    return ScreenScale(
        px_per_mm_x=_DEFAULT_PX_PER_MM,
        px_per_mm_y=_DEFAULT_PX_PER_MM,
        width_px=0,
        height_px=0,
        source="default",
    )
