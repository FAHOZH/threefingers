"""直接从 evdev 读取触控板的多点触控（MT-B）事件。

X 服务器/窗口管理器不会把三指位置暴露给普通客户端，所以这里绕过
libinput，以只读方式打开触控板的事件节点。设备同时可以被 X 服务器
和本进程读取，不会抢占输入。

需要读取 /dev/input/event* 的权限。安装脚本会添加一条只针对触控板
的 udev uaccess 规则，把读权限授予当前登录用户。
"""

from __future__ import annotations

import ctypes
import fcntl
import glob
import logging
import os
import struct
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .models import Contact, MT_TOOL_FINGER

log = logging.getLogger(__name__)

EV_SYN = 0x00
EV_ABS = 0x03
SYN_REPORT = 0x00
SYN_DROPPED = 0x03

ABS_MT_SLOT = 0x2F
ABS_MT_POSITION_X = 0x35
ABS_MT_POSITION_Y = 0x36
ABS_MT_TOOL_TYPE = 0x37
ABS_MT_TRACKING_ID = 0x39

_EVENT = struct.Struct("llHHi")
_ABSINFO = struct.Struct("6i")
_EVIOCGABS = 0x80184540
_EVIOCGNAME = 0x81004506


class DeviceError(RuntimeError):
    """触控板不可用。"""


class DevicePermissionError(DeviceError):
    """没有读取触控板事件节点的权限。"""


@dataclass(frozen=True)
class AbsInfo:
    value: int
    minimum: int
    maximum: int
    fuzz: int
    flat: int
    resolution: int


@dataclass(frozen=True)
class Geometry:
    x_min: int
    x_max: int
    y_min: int
    y_max: int
    x_resolution: int
    y_resolution: int
    width_mm: float | None
    height_mm: float | None

    @property
    def units_per_mm_x(self) -> float:
        if self.x_resolution > 0:
            return float(self.x_resolution)
        if self.width_mm and self.width_mm > 0:
            return (self.x_max - self.x_min) / self.width_mm
        return max(1.0, (self.x_max - self.x_min) / 100.0)

    @property
    def units_per_mm_y(self) -> float:
        if self.y_resolution > 0:
            return float(self.y_resolution)
        if self.height_mm and self.height_mm > 0:
            return (self.y_max - self.y_min) / self.height_mm
        return max(1.0, (self.y_max - self.y_min) / 60.0)

    def describe(self) -> str:
        return (
            f"x={self.x_min}..{self.x_max} y={self.y_min}..{self.y_max} "
            f"res={self.x_resolution}/{self.y_resolution} "
            f"size={self.width_mm or '?'}x{self.height_mm or '?'}mm "
            f"({self.units_per_mm_x:.2f}/{self.units_per_mm_y:.2f} units/mm)"
        )


@dataclass
class _Slot:
    tracking_id: int | None = None
    x: int = 0
    y: int = 0
    has_x: bool = False
    has_y: bool = False
    tool_type: int = MT_TOOL_FINGER


def _ioctl_name(fd: int) -> str:
    buf = ctypes.create_string_buffer(256)
    fcntl.ioctl(fd, _EVIOCGNAME, buf)
    return buf.value.decode("utf-8", "replace")


def _ioctl_abs(fd: int, code: int) -> AbsInfo:
    buf = ctypes.create_string_buffer(_ABSINFO.size)
    fcntl.ioctl(fd, _EVIOCGABS + code, buf)
    return AbsInfo(*_ABSINFO.unpack(buf.raw))


def iter_event_devices():
    """遍历 /sys/class/input/event*/device/name，不需要读权限。"""
    for name_path in sorted(glob.glob("/sys/class/input/event*/device/name")):
        path = Path(name_path)
        event_dir = path.parent.parent
        try:
            name = path.read_text(encoding="utf-8", errors="replace").strip()
        except OSError:
            continue
        yield str(Path("/dev/input") / event_dir.name), name


def find_touchpad(preference: str = "auto") -> str | None:
    """根据配置查找触控板事件节点。"""
    devices = list(iter_event_devices())
    preference = (preference or "auto").strip()

    if preference and preference != "auto":
        if "/" in preference:
            return preference if os.path.exists(preference) else None
        # 先精确匹配，再按子串匹配
        for path, name in devices:
            if name == preference:
                return path
        lowered = preference.lower()
        for path, name in devices:
            if lowered in name.lower():
                return path
        return None

    touchpads = [
        (path, name)
        for path, name in devices
        if "touchpad" in name.lower() or "touch pad" in name.lower()
    ]
    if not touchpads:
        return None
    touchpads.sort(key=lambda item: item[0])
    return touchpads[0][0]


def _udev_size_mm(path: str) -> tuple[float | None, float | None]:
    try:
        result = subprocess.run(
            ["udevadm", "info", "--query=property", "--name", path],
            capture_output=True,
            text=True,
            timeout=3,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None, None
    if result.returncode != 0:
        return None, None
    props: dict[str, str] = {}
    for line in result.stdout.splitlines():
        if "=" in line:
            key, value = line.split("=", 1)
            props[key] = value
    try:
        width = float(props.get("ID_INPUT_WIDTH_MM") or 0) or None
    except ValueError:
        width = None
    try:
        height = float(props.get("ID_INPUT_HEIGHT_MM") or 0) or None
    except ValueError:
        height = None
    return width, height


class TouchpadDevice:
    """一个只读打开的多点触控设备。"""

    def __init__(self, path: str) -> None:
        self.path = path
        self.name = ""
        self.geometry: Geometry | None = None
        self._fd: int = -1
        self._buffer = bytearray()
        self._current_slot = 0
        self._slots: dict[int, _Slot] = {}
        self._reset_slots()

    # ------------------------------------------------------------------
    def open(self) -> None:
        try:
            fd = os.open(
                self.path, os.O_RDONLY | os.O_NONBLOCK | os.O_CLOEXEC
            )
        except PermissionError as exc:
            raise DevicePermissionError(
                f"没有读取 {self.path} 的权限。请运行 "
                "sudo ./install.sh 安装 udev 规则（或重新登录）。"
            ) from exc
        except OSError as exc:
            raise DeviceError(f"无法打开 {self.path}: {exc}") from exc

        try:
            name = _ioctl_name(fd)
            x_info = self._read_axis(fd, ABS_MT_POSITION_X)
            y_info = self._read_axis(fd, ABS_MT_POSITION_Y)
            if x_info is None or y_info is None:
                raise DeviceError(
                    f"{self.path} 不是多点触控设备（缺少 MT 坐标轴）"
                )
            if x_info.maximum <= x_info.minimum or y_info.maximum <= y_info.minimum:
                raise DeviceError(f"{self.path} 的坐标范围无效")
            width_mm, height_mm = _udev_size_mm(self.path)
            geometry = Geometry(
                x_min=x_info.minimum,
                x_max=x_info.maximum,
                y_min=y_info.minimum,
                y_max=y_info.maximum,
                x_resolution=x_info.resolution,
                y_resolution=y_info.resolution,
                width_mm=width_mm,
                height_mm=height_mm,
            )
        except Exception:
            os.close(fd)
            raise

        self._fd = fd
        self.name = name
        self.geometry = geometry
        self._buffer.clear()
        self._reset_slots()

    def _read_axis(self, fd: int, code: int) -> AbsInfo | None:
        try:
            info = _ioctl_abs(fd, code)
        except OSError:
            return None
        if info.maximum <= info.minimum:
            return None
        return info

    # ------------------------------------------------------------------
    @property
    def fileno(self) -> int:
        return self._fd

    def read_reports(self) -> list[dict[int, Contact] | None]:
        """读取当前可用的数据。

        每个 SYN_REPORT 返回一份接触点快照（tracking_id -> Contact）；
        SYN_DROPPED 返回 None，表示事件流出现缺口，需要取消当前拖拽。
        """
        if self._fd < 0:
            raise DeviceError("设备未打开")
        reports: list[dict[int, Contact] | None] = []
        while True:
            try:
                data = os.read(self._fd, 8192)
            except BlockingIOError:
                break
            except OSError as exc:
                raise DeviceError(f"读取触控板失败: {exc}") from exc
            if not data:
                raise DeviceError("触控板事件节点已关闭")
            reports.extend(self.feed(data))
        return reports

    def feed(self, data: bytes) -> list[dict[int, Contact] | None]:
        reports: list[dict[int, Contact] | None] = []
        if not data:
            return reports
        self._buffer.extend(data)
        offset = 0
        total = len(self._buffer)
        while total - offset >= _EVENT.size:
            _sec, _usec, etype, code, value = _EVENT.unpack_from(
                self._buffer, offset
            )
            offset += _EVENT.size
            if etype == EV_ABS:
                self._handle_abs(code, value)
            elif etype == EV_SYN:
                if code == SYN_REPORT:
                    reports.append(self._snapshot())
                elif code == SYN_DROPPED:
                    log.warning("触控板事件丢失（SYN_DROPPED），重置手势状态")
                    self._reset_slots()
                    reports.append(None)
                # SYN_MT_REPORT 等事件忽略
        if offset:
            del self._buffer[:offset]
        return reports

    def _handle_abs(self, code: int, value: int) -> None:
        if code == ABS_MT_SLOT:
            self._current_slot = max(0, value)
            self._slots.setdefault(self._current_slot, _Slot())
            return
        slot = self._slots.setdefault(self._current_slot, _Slot())
        if code == ABS_MT_TRACKING_ID:
            if value == -1:
                slot.tracking_id = None
                slot.has_x = False
                slot.has_y = False
            else:
                if slot.tracking_id != value:
                    slot.has_x = False
                    slot.has_y = False
                slot.tracking_id = value
        elif code == ABS_MT_POSITION_X:
            slot.x = value
            slot.has_x = True
        elif code == ABS_MT_POSITION_Y:
            slot.y = value
            slot.has_y = True
        elif code == ABS_MT_TOOL_TYPE:
            slot.tool_type = value

    def _snapshot(self) -> dict[int, Contact]:
        contacts: dict[int, Contact] = {}
        for slot in self._slots.values():
            if slot.tracking_id is None or not (slot.has_x and slot.has_y):
                continue
            contacts[slot.tracking_id] = Contact(
                tracking_id=slot.tracking_id,
                x=float(slot.x),
                y=float(slot.y),
                tool_type=slot.tool_type,
            )
        return contacts

    def _reset_slots(self) -> None:
        self._slots.clear()
        self._current_slot = 0
        self._slots[0] = _Slot()

    def close(self) -> None:
        if self._fd >= 0:
            try:
                os.close(self._fd)
            finally:
                self._fd = -1
        self._buffer.clear()
        self._reset_slots()
