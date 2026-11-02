"""配置、运行时目录与日志路径。

配置保存在 ``~/.config/threefingers/config.json``。守护进程和命令行工具
都会读写同一个文件；守护进程运行时，开关状态以守护进程为准。
"""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any

DEFAULT_CONFIG: dict[str, Any] = {
    # 总开关
    "enabled": True,
    # 指针跟手速度倍率：1.0 表示按屏幕物理尺寸 1:1 映射手指移动
    "sensitivity": 1.0,
    # 三指移动超过该距离（毫米）才开始拖拽，避免三指轻点误触发
    "drag_threshold_mm": 1.0,
    # 方向修正（个别触控板坐标方向相反）
    "invert_x": False,
    "invert_y": False,
    # "auto"、/dev/input/eventX 路径，或设备名的一部分
    "device": "auto",
    # 屏幕每毫米像素数；0 表示自动检测
    "pixels_per_mm": 0.0,
}

_APP_DIR_NAME = "threefingers"


def _xdg_dir(env_var: str, fallback: str) -> Path:
    value = os.environ.get(env_var)
    if value:
        return Path(value).expanduser()
    return Path.home() / fallback


def config_dir() -> Path:
    return _xdg_dir("XDG_CONFIG_HOME", ".config") / _APP_DIR_NAME


def config_file() -> Path:
    return config_dir() / "config.json"


def state_dir() -> Path:
    return _xdg_dir("XDG_STATE_HOME", ".local/state") / _APP_DIR_NAME


def log_file() -> Path:
    return state_dir() / "daemon.log"


def runtime_dir() -> Path:
    base = os.environ.get("XDG_RUNTIME_DIR")
    if base:
        return Path(base) / _APP_DIR_NAME
    return Path(f"/tmp/threefingers-{os.getuid()}")


def socket_path() -> Path:
    return runtime_dir() / "control.sock"


def ensure_runtime_dir() -> Path:
    path = runtime_dir()
    path.mkdir(parents=True, exist_ok=True, mode=0o700)
    try:
        path.chmod(0o700)
    except OSError:
        pass
    return path


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def validate_config(raw: dict[str, Any] | None) -> dict[str, Any]:
    """把任意输入整理成完整、合法的配置。"""
    raw = raw if isinstance(raw, dict) else {}
    cfg: dict[str, Any] = dict(DEFAULT_CONFIG)
    for key in DEFAULT_CONFIG:
        if key in raw:
            cfg[key] = raw[key]

    cfg["enabled"] = bool(cfg["enabled"])
    cfg["invert_x"] = bool(cfg["invert_x"])
    cfg["invert_y"] = bool(cfg["invert_y"])
    try:
        cfg["sensitivity"] = _clamp(float(cfg["sensitivity"]), 0.2, 4.0)
    except (TypeError, ValueError):
        cfg["sensitivity"] = DEFAULT_CONFIG["sensitivity"]
    try:
        cfg["drag_threshold_mm"] = _clamp(
            float(cfg["drag_threshold_mm"]), 0.2, 10.0
        )
    except (TypeError, ValueError):
        cfg["drag_threshold_mm"] = DEFAULT_CONFIG["drag_threshold_mm"]
    try:
        cfg["pixels_per_mm"] = _clamp(float(cfg["pixels_per_mm"]), 0.0, 50.0)
    except (TypeError, ValueError):
        cfg["pixels_per_mm"] = DEFAULT_CONFIG["pixels_per_mm"]
    device = str(cfg.get("device") or "auto").strip()
    cfg["device"] = device or "auto"
    return cfg


def load_config(path: Path | None = None) -> dict[str, Any]:
    target = path or config_file()
    try:
        with open(target, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError):
        return dict(DEFAULT_CONFIG)
    return validate_config(raw)


def save_config(cfg: dict[str, Any], path: Path | None = None) -> None:
    target = path or config_file()
    target.parent.mkdir(parents=True, exist_ok=True)
    clean = validate_config(cfg)
    tmp_name = None
    try:
        with tempfile.NamedTemporaryFile(
            "w", encoding="utf-8", dir=target.parent, delete=False
        ) as fh:
            tmp_name = fh.name
            json.dump(clean, fh, ensure_ascii=False, indent=2)
            fh.write("\n")
        os.replace(tmp_name, target)
        os.chmod(target, 0o600)
    finally:
        if tmp_name and os.path.exists(tmp_name):
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
