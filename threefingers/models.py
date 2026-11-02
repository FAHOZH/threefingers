"""共享的数据类型与常量。"""

from __future__ import annotations

from dataclasses import dataclass

MT_TOOL_FINGER = 0x01
MT_TOOL_PALM = 0x02


@dataclass(frozen=True)
class Contact:
    """触控板上的一个接触点（单位：设备原始坐标）。"""

    tracking_id: int
    x: float
    y: float
    tool_type: int = MT_TOOL_FINGER
