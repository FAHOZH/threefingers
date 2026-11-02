"""命令行入口：threefingers ..."""

from __future__ import annotations

import argparse
import json
import selectors
import sys
import time

from . import __version__, config, control, service
from .evdev_input import TouchpadDevice, find_touchpad
from .gesture import GestureEngine, MotionSettings
from .screen import detect_screen_scale


def _load_cfg() -> dict:
    return config.load_config()


def _save_enabled(enabled: bool) -> dict:
    cfg = _load_cfg()
    cfg["enabled"] = enabled
    config.save_config(cfg)
    return cfg


def _print_status(data: dict | None) -> int:
    if data is None:
        cfg = _load_cfg()
        print(f"三指拖移：{'已开启' if cfg['enabled'] else '已关闭'}（仅配置）")
        print("服务：未运行")
        print("提示：运行 `threefingers on` 启动服务，或重新登录。")
        return 1

    cfg = _load_cfg()
    enabled = bool(data.get("enabled", cfg["enabled"]))
    device = data.get("device") or {}
    x11 = data.get("x11") or {}

    print(f"三指拖移：{'已开启' if enabled else '已关闭'}")
    print(f"服务：运行中（PID {data.get('pid', '?')}）")
    if device.get("ok"):
        print(f"触控板：{device.get('name')} ({device.get('path')})")
    else:
        print(f"触控板：不可用 — {device.get('error') or '未连接'}")
    if x11.get("ok"):
        print("X11 注入：正常")
    else:
        print(f"X11 注入：不可用 — {x11.get('error') or '未知错误'}")
    sensitivity = data.get("sensitivity", cfg["sensitivity"])
    ppm = data.get("pixels_per_mm")
    if ppm:
        print(f"跟手速度：{sensitivity:.2f}×（{ppm:.2f} px/mm）")
    else:
        print(f"跟手速度：{sensitivity:.2f}×")
    if data.get("dragging"):
        print("当前状态：正在拖移")
    print(f"日志：{data.get('log_file', config.log_file())}")

    error = device.get("error") or ""
    if not device.get("ok") and "权限" in error:
        print()
        print("提示：安装脚本会添加只针对触控板的 udev 权限规则：")
        print(f"  sudo {service.project_root() / 'install.sh'}")
    ready = bool(data.get("ready")) and bool(device.get("ok")) and bool(
        x11.get("ok")
    )
    return 0 if ready else 1


def _maybe_warn(resp: dict) -> None:
    device = resp.get("device") or {}
    x11 = resp.get("x11") or {}
    if not device.get("ok"):
        print(f"警告：触控板不可用 — {device.get('error') or '未知错误'}")
    if not x11.get("ok"):
        print(f"警告：X11 注入不可用 — {x11.get('error') or '未知错误'}")


def cmd_on(_args) -> int:
    resp = control.request({"cmd": "set_enabled", "value": True})
    if resp is None:
        _save_enabled(True)
        if not service.ensure_daemon():
            print("无法启动三指拖移服务。")
            print(f"请查看日志：{config.log_file()}")
            return 1
        resp = control.request({"cmd": "set_enabled", "value": True})
    print("三指拖移已开启。")
    if resp:
        _maybe_warn(resp)
    return 0


def cmd_off(_args) -> int:
    resp = control.request({"cmd": "set_enabled", "value": False})
    if resp is None:
        _save_enabled(False)
    print("三指拖移已关闭。")
    return 0


def cmd_toggle(_args) -> int:
    status = control.request({"cmd": "status"})
    if status is not None:
        resp = control.request({"cmd": "toggle"})
        if resp is not None:
            enabled = bool(resp.get("enabled"))
            print(f"三指拖移已{'开启' if enabled else '关闭'}。")
            _maybe_warn(resp)
            return 0
        # 服务刚退出，退回本地配置
    cfg = _load_cfg()
    enabled = not bool(cfg["enabled"])
    _save_enabled(enabled)
    if enabled:
        if not service.ensure_daemon():
            print("三指拖移已写入配置，但服务启动失败。")
            print(f"请查看日志：{config.log_file()}")
            return 1
        resp = control.request({"cmd": "set_enabled", "value": True})
        if resp:
            _maybe_warn(resp)
    print(f"三指拖移已{'开启' if enabled else '关闭'}。")
    return 0


def cmd_status(args) -> int:
    data = control.request({"cmd": "status"})
    if args.json:
        if data is None:
            cfg = _load_cfg()
            print(
                json.dumps(
                    {"ok": False, "running": False, "enabled": cfg["enabled"]},
                    ensure_ascii=False,
                    indent=2,
                )
            )
            return 1
        print(json.dumps(data, ensure_ascii=False, indent=2))
        device = data.get("device") or {}
        x11 = data.get("x11") or {}
        return 0 if data.get("ready") and device.get("ok") and x11.get("ok") else 1
    return _print_status(data)


def cmd_quit(_args) -> int:
    resp = control.request({"cmd": "quit"})
    if resp is None:
        print("三指拖移服务未运行。")
        return 0
    print("三指拖移服务已停止（配置中的开关状态保持不变）。")
    return 0


def cmd_reload(_args) -> int:
    resp = control.request({"cmd": "reload"})
    if resp is None:
        print("三指拖移服务未运行。")
        return 1
    print("配置已重新加载。")
    _maybe_warn(resp)
    return 0


def cmd_daemon(args) -> int:
    from .daemon import main as daemon_main

    daemon_args = []
    if args.foreground:
        daemon_args.append("--foreground")
    if args.verbose:
        daemon_args.append("--verbose")
    return daemon_main(daemon_args)


def cmd_settings(_args) -> int:
    try:
        from . import gui
    except ImportError:
        print(
            "设置窗口需要 PyGObject（python3-gi）。"
            "请安装后重试：sudo apt install python3-gi",
            file=sys.stderr,
        )
        return 1
    return gui.main()


def cmd_tray(_args) -> int:
    try:
        from . import tray
    except ImportError:
        print(
            "托盘图标需要 PyGObject（python3-gi）。"
            "请安装后重试：sudo apt install python3-gi",
            file=sys.stderr,
        )
        return 1
    return tray.main()


class _PrintBackend:
    """调试用的假后端：只打印将要执行的操作，不真正注入。"""

    def button_down(self) -> None:
        print("  [模拟] 左键按下")

    def button_up(self) -> None:
        print("  [模拟] 左键释放")

    def move(self, dx: int, dy: int) -> None:
        print(f"  [模拟] 移动指针 ({dx:+d}, {dy:+d})")


def cmd_debug(args) -> int:
    cfg = _load_cfg()
    path = find_touchpad(cfg["device"])
    if not path:
        print("未找到触控板。", file=sys.stderr)
        return 1
    device = TouchpadDevice(path)
    try:
        device.open()
    except Exception as exc:
        print(f"无法读取触控板：{exc}", file=sys.stderr)
        return 1
    assert device.geometry is not None
    print(f"触控板：{device.name} ({device.path})")
    print(f"几何信息：{device.geometry.describe()}")
    scale = detect_screen_scale(cfg["pixels_per_mm"])
    print(
        f"屏幕缩放：{scale.px_per_mm_x:.2f} x {scale.px_per_mm_y:.2f} px/mm"
        f"（来源: {scale.source}）"
    )
    print("三根手指放在触控板上并移动即可看到输出；Ctrl+C 退出。")
    if args.simulate:
        print("模拟模式：只打印将执行的动作，不会真正点击或移动。")
        backend = _PrintBackend()
        engine = GestureEngine(
            backend,
            MotionSettings.from_geometry(
                device.geometry, scale, cfg
            ),
            on_state_change=lambda state: print(f"  [状态] {state}"),
        )
        engine.set_enabled(True)
    else:
        engine = None

    selector = selectors.DefaultSelector()
    selector.register(device.fileno, selectors.EVENT_READ)
    last_print = 0.0
    try:
        while True:
            selector.select(0.5)
            try:
                reports = device.read_reports()
            except Exception as exc:
                print(f"\n读取触控板失败：{exc}", file=sys.stderr)
                if engine:
                    engine.cancel()
                return 1
            for contacts in reports:
                if contacts is None:
                    print("事件流失步（SYN_DROPPED）")
                    if engine:
                        engine.cancel()
                    continue
                if contacts:
                    now_monotonic = time.monotonic()
                    if now_monotonic - last_print >= 0.1:
                        last_print = now_monotonic
                        now = time.strftime("%H:%M:%S")
                        parts = []
                        for contact in sorted(
                            contacts.values(), key=lambda c: c.tracking_id
                        ):
                            kind = "手掌" if contact.tool_type == 2 else "手指"
                            parts.append(
                                f"id={contact.tracking_id} "
                                f"({contact.x:.0f},{contact.y:.0f}) {kind}"
                            )
                        print(
                            f"[{now}] {len(contacts)} 个接触点: "
                            + " | ".join(parts)
                        )
                if engine:
                    engine.handle_report(contacts)
    except KeyboardInterrupt:
        print()
        if engine:
            engine.cancel()
        return 0
    finally:
        selector.close()
        device.close()


def cmd_version(_args) -> int:
    print(f"threefingers {__version__}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="threefingers",
        description="macOS 风格的三指拖移（拖动窗口、选择文字）",
    )
    parser.add_argument(
        "--version", action="version", version=f"threefingers {__version__}"
    )
    sub = parser.add_subparsers(dest="command")

    p = sub.add_parser("on", help="打开三指拖移")
    p.set_defaults(func=cmd_on)
    p = sub.add_parser("off", help="关闭三指拖移")
    p.set_defaults(func=cmd_off)
    p = sub.add_parser("toggle", help="切换三指拖移")
    p.set_defaults(func=cmd_toggle)
    p = sub.add_parser("status", help="显示运行状态")
    p.add_argument("--json", action="store_true", help="以 JSON 输出")
    p.set_defaults(func=cmd_status)
    p = sub.add_parser("quit", help="停止后台服务")
    p.set_defaults(func=cmd_quit)
    p = sub.add_parser("reload", help="重新加载配置文件")
    p.set_defaults(func=cmd_reload)
    p = sub.add_parser("settings", aliases=["gui"], help="打开图形设置窗口")
    p.set_defaults(func=cmd_settings)
    p = sub.add_parser("tray", help="运行托盘图标")
    p.set_defaults(func=cmd_tray)
    p = sub.add_parser("debug", help="查看实时触点（用于校准/排障）")
    p.add_argument(
        "--simulate",
        action="store_true",
        help="同时打印手势将执行的动作（不真正注入）",
    )
    p.set_defaults(func=cmd_debug)
    p = sub.add_parser("daemon", help="在前台运行守护进程")
    p.add_argument(
        "--foreground", action="store_true", help="日志输出到终端"
    )
    p.add_argument("--verbose", "-v", action="store_true", help="调试日志")
    p.set_defaults(func=cmd_daemon)
    p = sub.add_parser("version", help="显示版本")
    p.set_defaults(func=cmd_version)
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "func", None):
        parser.print_help()
        return 0
    return int(args.func(args) or 0)


if __name__ == "__main__":
    sys.exit(main())
