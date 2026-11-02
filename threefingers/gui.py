"""图形设置窗口：一个真正的开关，以及跟手速度调节。"""

from __future__ import annotations

import subprocess
import sys
import time
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk  # noqa: E402

from . import config, control, service  # noqa: E402

_ICON_PATH = Path(__file__).resolve().parent.parent / "share" / "threefingers.svg"


class SettingsWindow(Gtk.Window):
    def __init__(self) -> None:
        super().__init__(title="三指拖移设置")
        self.set_default_size(480, 380)
        self.set_border_width(18)
        self.set_icon_from_file(str(_ICON_PATH))
        self.connect("destroy", self._on_destroy)

        self._updating = False
        self._scale_changed_at = 0.0
        self._scale_timer: int | None = None
        self._refresh_timer = GLib.timeout_add(1500, self._refresh)
        self._cfg = config.load_config()

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=14)
        self.add(outer)

        outer.pack_start(self._build_header(), False, False, 0)
        outer.pack_start(self._build_switch_card(), False, False, 0)
        outer.pack_start(self._build_speed_card(), False, False, 0)
        outer.pack_start(self._build_status_card(), True, True, 0)
        outer.pack_start(self._build_buttons(), False, False, 0)

        self.show_all()
        self._start_button.hide()
        GLib.idle_add(self._auto_start)

    # ------------------------------------------------------------------
    def _build_header(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        try:
            image = Gtk.Image.new_from_file(str(_ICON_PATH))
            image.set_pixel_size(48)
            box.pack_start(image, False, False, 0)
        except Exception:
            pass
        text = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        title = Gtk.Label(xalign=0)
        title.set_markup("<b><span size='large'>三指拖移</span></b>")
        subtitle = Gtk.Label(
            label="像 macOS 一样，用三根手指拖动窗口、选择文字、拖动文件",
            xalign=0,
        )
        subtitle.set_line_wrap(True)
        subtitle.get_style_context().add_class("dim-label")
        text.pack_start(title, False, False, 0)
        text.pack_start(subtitle, False, False, 0)
        box.pack_start(text, True, True, 0)
        return box

    def _build_switch_card(self) -> Gtk.Widget:
        frame = Gtk.Frame()
        inner = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=12)
        inner.set_border_width(14)
        labels = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=3)
        title = Gtk.Label(label="启用三指拖移", xalign=0)
        title.set_markup("<b>启用三指拖移</b>")
        desc = Gtk.Label(
            label="三指按住并移动即按住左键拖动；抬起任意一指结束。"
            "三指轻点不会产生点击。",
            xalign=0,
        )
        desc.set_line_wrap(True)
        desc.get_style_context().add_class("dim-label")
        labels.pack_start(title, False, False, 0)
        labels.pack_start(desc, False, False, 0)
        inner.pack_start(labels, True, True, 0)

        self.switch = Gtk.Switch()
        self.switch.set_valign(Gtk.Align.CENTER)
        self.switch.set_tooltip_text("打开或关闭三指拖移")
        self.switch.connect("notify::active", self._on_switch)
        inner.pack_start(self.switch, False, False, 0)
        frame.add(inner)
        return frame

    def _build_speed_card(self) -> Gtk.Widget:
        frame = Gtk.Frame()
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        inner.set_border_width(14)
        top = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        label = Gtk.Label(label="跟手速度", xalign=0)
        self.speed_value = Gtk.Label(label="1.0×", xalign=1)
        top.pack_start(label, True, True, 0)
        top.pack_start(self.speed_value, False, False, 0)
        inner.pack_start(top, False, False, 0)

        self.scale = Gtk.Scale.new_with_range(
            Gtk.Orientation.HORIZONTAL, 0.5, 3.0, 0.1
        )
        self.scale.set_digits(1)
        self.scale.set_draw_value(False)
        self.scale.set_tooltip_text(
            "1.0 表示手指在触控板上移动的距离与屏幕上的物理距离大致相同"
        )
        self.scale.connect("value-changed", self._on_scale_changed)
        inner.pack_start(self.scale, False, False, 0)
        frame.add(inner)
        return frame

    def _build_status_card(self) -> Gtk.Widget:
        frame = Gtk.Frame()
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=5)
        inner.set_border_width(14)
        self.service_label = Gtk.Label(xalign=0)
        self.service_label.set_markup(
            "<span foreground='#8a8a8a'>● 正在检查服务…</span>"
        )
        self.device_label = Gtk.Label(xalign=0)
        self.device_label.set_line_wrap(True)
        self.device_label.set_selectable(True)
        self.hint_label = Gtk.Label(xalign=0)
        self.hint_label.set_line_wrap(True)
        self.hint_label.set_selectable(True)
        self.hint_label.get_style_context().add_class("dim-label")
        inner.pack_start(self.service_label, False, False, 0)
        inner.pack_start(self.device_label, False, False, 0)
        inner.pack_start(self.hint_label, False, False, 0)
        frame.add(inner)
        return frame

    def _build_buttons(self) -> Gtk.Widget:
        box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
        self._start_button = Gtk.Button(label="启动服务")
        self._start_button.connect("clicked", self._on_start_service)
        log_button = Gtk.Button(label="查看日志")
        log_button.connect("clicked", self._on_open_log)
        close_button = Gtk.Button(label="关闭")
        close_button.connect("clicked", lambda _b: self.destroy())
        box.pack_start(self._start_button, False, False, 0)
        box.pack_start(log_button, False, False, 0)
        box.pack_end(close_button, False, False, 0)
        return box

    # ------------------------------------------------------------------
    def _auto_start(self) -> bool:
        if not control.is_running(timeout=0.2):
            service.spawn_daemon()
        self._refresh()
        return False

    def _on_switch(self, switch: Gtk.Switch, _param) -> None:
        if self._updating:
            return
        enabled = bool(switch.get_active())
        response = control.request(
            {"cmd": "set_enabled", "value": enabled}, timeout=0.8
        )
        if response is None:
            cfg = config.load_config()
            cfg["enabled"] = enabled
            config.save_config(cfg)
            if enabled:
                service.spawn_daemon()
        GLib.timeout_add(350, self._refresh_now)

    def _on_scale_changed(self, scale: Gtk.Scale) -> None:
        self._scale_changed_at = time.monotonic()
        self.speed_value.set_text(f"{scale.get_value():.1f}×")
        if self._updating:
            return
        if self._scale_timer is not None:
            GLib.source_remove(self._scale_timer)
        self._scale_timer = GLib.timeout_add(350, self._apply_scale)

    def _apply_scale(self) -> bool:
        self._scale_timer = None
        value = round(self.scale.get_value(), 1)
        response = control.request(
            {"cmd": "set_config", "sensitivity": value}, timeout=0.8
        )
        if response is None:
            cfg = config.load_config()
            cfg["sensitivity"] = value
            config.save_config(cfg)
        return False

    def _on_start_service(self, _button) -> None:
        service.spawn_daemon()
        GLib.timeout_add(700, self._refresh_now)

    def _on_open_log(self, _button) -> None:
        path = config.log_file()
        try:
            subprocess.Popen(
                ["xdg-open", str(path)],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
            )
        except OSError:
            self.hint_label.set_text(f"日志文件：{path}")

    # ------------------------------------------------------------------
    def _refresh_now(self) -> bool:
        self._refresh()
        return False

    def _refresh(self) -> bool:
        data = control.request({"cmd": "status"}, timeout=0.5)
        self._updating = True
        try:
            if data is None:
                cfg = config.load_config()
                self.switch.set_active(bool(cfg["enabled"]))
                self.service_label.set_markup(
                    "<span foreground='#8a8a8a'>● 服务未运行</span>"
                )
                self.device_label.set_text("触控板：—")
                self.hint_label.set_text(
                    "点击“启动服务”后即可使用。登录时会自动启动。"
                )
                self._start_button.show()
                self.scale.set_value(cfg["sensitivity"])
                self.speed_value.set_text(f"{cfg['sensitivity']:.1f}×")
                self.scale.set_sensitive(True)
                return True

            self._start_button.hide()
            self.switch.set_active(bool(data.get("enabled")))
            if data.get("ready"):
                self.service_label.set_markup(
                    "<span foreground='#2e7d32'>● 服务运行中</span>"
                )
            else:
                self.service_label.set_markup(
                    "<span foreground='#c77700'>● 服务运行中（未就绪）</span>"
                )
            device = data.get("device") or {}
            if device.get("ok"):
                self.device_label.set_text(
                    f"触控板：{device.get('name')} ({device.get('path')})"
                )
            else:
                self.device_label.set_text(
                    "触控板：不可用\n" + (device.get("error") or "未知错误")
                )
            x11 = data.get("x11") or {}
            if not x11.get("ok"):
                self.device_label.set_text(
                    self.device_label.get_text()
                    + "\nX11 注入："
                    + (x11.get("error") or "未知错误")
                )
            hint = ""
            error = device.get("error") or ""
            if error and "权限" in error:
                hint = (
                    "安装脚本会添加只针对触控板的 udev 权限规则：\n"
                    f"sudo {service.project_root() / 'install.sh'}"
                )
            self.hint_label.set_text(hint)

            if time.monotonic() - self._scale_changed_at > 1.0:
                sensitivity = float(data.get("sensitivity", 1.0))
                self.scale.set_value(sensitivity)
                self.speed_value.set_text(f"{sensitivity:.1f}×")
        finally:
            self._updating = False
        return True

    def _on_destroy(self, _window) -> None:
        if self._refresh_timer is not None:
            GLib.source_remove(self._refresh_timer)
            self._refresh_timer = None
        Gtk.main_quit()


def main() -> int:
    SettingsWindow()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
