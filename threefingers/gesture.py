"""三指拖移的手势状态机。

逻辑与 macOS 的“三指拖移”一致：

* 三指按下后，手指移动超过一个小阈值即按住鼠标左键（在当前位置开始拖拽）；
* 拖拽过程中指针跟随三指中心移动；
* 任意一根发起拖拽的手指抬起时立即释放左键，完成拖放；
* 三指轻点（移动不超过阈值）不会产生点击；
* 第四根手指加入不影响正在进行的拖拽（其位置不计入移动）；
* 额外的手指/手掌接触（MT_TOOL_PALM）会被忽略。
"""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from typing import Callable, Protocol

from .models import Contact, MT_TOOL_PALM

log = logging.getLogger(__name__)

IDLE = "idle"
ARMED = "armed"
DRAGGING = "dragging"

_MAX_JUMP_MM = 40.0
_FALLBACK_PX_PER_MM = 8.0


class DragBackend(Protocol):
    def button_down(self) -> None: ...

    def button_up(self) -> None: ...

    def move(self, dx: int, dy: int) -> None: ...


@dataclass(frozen=True)
class MotionSettings:
    units_per_mm_x: float
    units_per_mm_y: float
    px_per_mm_x: float
    px_per_mm_y: float
    sensitivity: float
    threshold_mm: float
    invert_x: bool
    invert_y: bool

    @classmethod
    def from_geometry(cls, geometry, screen_scale, config) -> "MotionSettings":
        return cls(
            units_per_mm_x=max(1.0, geometry.units_per_mm_x),
            units_per_mm_y=max(1.0, geometry.units_per_mm_y),
            px_per_mm_x=max(0.1, screen_scale.px_per_mm_x),
            px_per_mm_y=max(0.1, screen_scale.px_per_mm_y),
            sensitivity=float(config["sensitivity"]),
            threshold_mm=float(config["drag_threshold_mm"]),
            invert_x=bool(config["invert_x"]),
            invert_y=bool(config["invert_y"]),
        )


def _centroid(contacts: list[Contact]) -> tuple[float, float]:
    if not contacts:
        return 0.0, 0.0
    return (
        sum(c.x for c in contacts) / len(contacts),
        sum(c.y for c in contacts) / len(contacts),
    )


class GestureEngine:
    def __init__(
        self,
        backend: DragBackend,
        settings: MotionSettings,
        on_state_change: Callable[[str], None] | None = None,
    ) -> None:
        self.backend = backend
        self.settings = settings
        self.enabled = True
        self._on_state_change = on_state_change
        self._state = IDLE
        self._origin_tids: tuple[int, ...] = ()
        self._armed_x = 0.0
        self._armed_y = 0.0
        self._last_x = 0.0
        self._last_y = 0.0
        self._acc_x = 0.0
        self._acc_y = 0.0
        self._pressed = False
        self.fatal_error: str | None = None

    # ------------------------------------------------------------------
    @property
    def state(self) -> str:
        return self._state

    @property
    def dragging(self) -> bool:
        return self._state == DRAGGING

    @property
    def pressed(self) -> bool:
        return self._pressed

    def update_settings(self, settings: MotionSettings) -> None:
        self.settings = settings

    def set_enabled(self, enabled: bool) -> None:
        self.enabled = bool(enabled)
        # 无论打开还是关闭，都先结束进行中的手势；
        # cancel() 会在按住左键时释放按键，避免“卡住左键”。
        self.cancel()

    # ------------------------------------------------------------------
    def handle_report(
        self, contacts: dict[int, Contact], now: float | None = None
    ) -> None:
        if self.fatal_error is not None:
            return
        if not self.enabled:
            if self._state != IDLE:
                self.cancel()
            return

        current = {
            tid: c for tid, c in contacts.items() if c.tool_type != MT_TOOL_PALM
        }

        if self._state == IDLE:
            if len(current) == 3:
                self._origin_tids = tuple(current.keys())
                self._armed_x, self._armed_y = _centroid(list(current.values()))
                self._set_state(ARMED)
            return

        if self._state == ARMED:
            if not all(tid in current for tid in self._origin_tids):
                self._reset()
                return
            origin = [current[tid] for tid in self._origin_tids]
            cx, cy = _centroid(origin)
            dx_mm = (cx - self._armed_x) / self.settings.units_per_mm_x
            dy_mm = (cy - self._armed_y) / self.settings.units_per_mm_y
            distance_mm = math.hypot(dx_mm, dy_mm)
            if distance_mm > _MAX_JUMP_MM:
                log.warning("三指位置跳变过大（%.1fmm），忽略", distance_mm)
                self._reset()
                return
            if distance_mm >= self.settings.threshold_mm:
                # 从越过阈值的这一刻开始计算位移，避免拖动开始时指针跳跃
                self._start_drag(cx, cy)
            return

        if self._state == DRAGGING:
            if not all(tid in current for tid in self._origin_tids):
                # 发起拖拽的三指中有手指抬起：结束拖拽
                self.cancel()
                return
            origin = [current[tid] for tid in self._origin_tids]
            cx, cy = _centroid(origin)
            dx_mm = (cx - self._last_x) / self.settings.units_per_mm_x
            dy_mm = (cy - self._last_y) / self.settings.units_per_mm_y
            if math.hypot(dx_mm, dy_mm) > _MAX_JUMP_MM:
                log.warning("拖动过程中位置跳变过大，结束拖拽")
                self.cancel()
                return
            self._last_x, self._last_y = cx, cy
            self._move_pointer(dx_mm, dy_mm)

    def _start_drag(self, cx: float, cy: float) -> None:
        try:
            self.backend.button_down()
        except Exception as exc:
            log.exception("按下鼠标左键失败")
            self.fatal_error = str(exc)
            try:
                self.backend.button_up()
            except Exception:
                pass
            self._state = IDLE
            self._reset_values()
            self._notify_state()
            return
        self._pressed = True
        self._last_x, self._last_y = cx, cy
        self._acc_x = self._acc_y = 0.0
        self._set_state(DRAGGING)
        log.debug("开始三指拖移")

    def _move_pointer(self, dx_mm: float, dy_mm: float) -> None:
        sx = self.settings.sensitivity * self.settings.px_per_mm_x
        sy = self.settings.sensitivity * self.settings.px_per_mm_y
        if not math.isfinite(sx) or sx <= 0:
            sx = _FALLBACK_PX_PER_MM
        if not math.isfinite(sy) or sy <= 0:
            sy = _FALLBACK_PX_PER_MM
        if self.settings.invert_x:
            sx = -sx
        if self.settings.invert_y:
            sy = -sy

        self._acc_x += dx_mm * sx
        self._acc_y += dy_mm * sy
        ix = int(self._acc_x)
        iy = int(self._acc_y)
        if ix == 0 and iy == 0:
            return
        self._acc_x -= ix
        self._acc_y -= iy
        try:
            self.backend.move(ix, iy)
        except Exception as exc:
            log.exception("移动指针失败")
            self.fatal_error = str(exc)
            self.cancel()

    # ------------------------------------------------------------------
    def cancel(self) -> None:
        """结束拖拽并释放按键（幂等）。"""
        if self._pressed:
            self._pressed = False
            try:
                self.backend.button_up()
            except Exception:
                log.exception("释放鼠标左键失败")
        if self._state != IDLE:
            log.debug("结束三指拖移")
        self._state = IDLE
        self._reset_values()
        self._notify_state()

    def shutdown(self) -> None:
        self.cancel()

    def _reset(self) -> None:
        if self._state != IDLE:
            log.debug("重置手势状态")
        self._state = IDLE
        self._reset_values()
        self._notify_state()

    def _reset_values(self) -> None:
        self._origin_tids = ()
        self._armed_x = self._armed_y = 0.0
        self._last_x = self._last_y = 0.0
        self._acc_x = self._acc_y = 0.0

    def _set_state(self, state: str) -> None:
        if self._state != state:
            self._state = state
            self._notify_state()

    def _notify_state(self) -> None:
        if self._on_state_change:
            try:
                self._on_state_change(self._state)
            except Exception:
                log.exception("状态回调失败")
