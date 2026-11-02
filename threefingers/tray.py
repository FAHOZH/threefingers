"""XFCE/桌面托盘图标：一键开关三指拖移。"""

from __future__ import annotations

import logging
import subprocess
import sys
from pathlib import Path

import gi

gi.require_version("Gtk", "3.0")
gi.require_version("GdkPixbuf", "2.0")
from gi.repository import GdkPixbuf, GLib, Gtk  # noqa: E402

from . import config, control, service  # noqa: E402

log = logging.getLogger(__name__)

_ICON_PATH = Path(__file__).resolve().parent.parent / "share" / "threefingers.svg"


class Tray:
    def __init__(self) -> None:
        self._updating = False
        self._data: dict | None = None

        self.icon = Gtk.StatusIcon()
        self.icon.set_title("三指拖移")
        self.icon.set_tooltip_text("三指拖移")
        self._set_icon()
        self.icon.connect("activate", self._on_activate)
        self.icon.connect("popup-menu", self._on_popup_menu)

        self.menu = Gtk.Menu()
        self.check_item = Gtk.CheckMenuItem(label="启用三指拖移")
        self.check_item.connect("toggled", self._on_toggle)
        self.menu.append(self.check_item)

        self.status_item = Gtk.MenuItem(label="服务：检查中…")
        self.status_item.set_sensitive(False)
        self.menu.append(self.status_item)

        self.start_item = Gtk.MenuItem(label="启动服务")
        self.start_item.connect("activate", self._on_start)
        self.menu.append(self.start_item)

        settings_item = Gtk.MenuItem(label="设置…")
        settings_item.connect("activate", self._on_settings)
        self.menu.append(settings_item)

        self.menu.append(Gtk.SeparatorMenuItem())
        quit_item = Gtk.MenuItem(label="退出托盘")
        quit_item.connect("activate", self._on_quit)
        self.menu.append(quit_item)
        self.menu.show_all()
        self.start_item.hide()

        self.icon.set_visible(True)
        GLib.idle_add(self._initial_check)
        self._timer = GLib.timeout_add(2000, self._refresh)

    # ------------------------------------------------------------------
    def _set_icon(self) -> None:
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file_at_scale(
                str(_ICON_PATH), 24, 24, True
            )
            self.icon.set_from_pixbuf(pixbuf)
        except Exception:
            self.icon.set_from_icon_name("input-touchpad")

    def _initial_check(self) -> bool:
        if not control.is_running(timeout=0.2):
            service.spawn_daemon()
        self._refresh()
        return False

    def _on_activate(self, _icon) -> None:
        self._on_settings(None)

    def _on_popup_menu(self, icon, button, activate_time) -> None:
        self.menu.popup(
            None,
            None,
            Gtk.StatusIcon.position_menu,
            icon,
            button,
            activate_time,
        )

    def _on_toggle(self, item: Gtk.CheckMenuItem) -> None:
        if self._updating:
            return
        enabled = bool(item.get_active())
        response = control.request(
            {"cmd": "set_enabled", "value": enabled}, timeout=0.8
        )
        if response is None:
            cfg = config.load_config()
            cfg["enabled"] = enabled
            config.save_config(cfg)
            if enabled:
                service.spawn_daemon()
                GLib.timeout_add(800, self._refresh_now)
        else:
            GLib.timeout_add(250, self._refresh_now)

    def _on_start(self, _item) -> None:
        service.spawn_daemon()
        GLib.timeout_add(700, self._refresh_now)

    def _on_settings(self, _item) -> None:
        try:
            subprocess.Popen(
                [sys.executable, str(service.daemon_entry()), "settings"],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                start_new_session=True,
                cwd=str(service.project_root()),
            )
        except OSError:
            log.exception("打开设置窗口失败")

    def _on_quit(self, _item) -> None:
        if self._timer is not None:
            GLib.source_remove(self._timer)
            self._timer = None
        Gtk.main_quit()

    def _refresh_now(self) -> bool:
        self._refresh()
        return False

    def _refresh(self) -> bool:
        data = control.request({"cmd": "status"}, timeout=0.5)
        self._data = data
        self._updating = True
        try:
            if data is None:
                cfg = config.load_config()
                self.check_item.set_active(bool(cfg["enabled"]))
                self.status_item.set_label("服务：未运行")
                self.start_item.show()
                self.icon.set_tooltip_text("三指拖移：服务未运行")
                return True

            enabled = bool(data.get("enabled"))
            ready = bool(data.get("ready"))
            self.check_item.set_active(enabled)
            self.start_item.hide()
            if ready:
                self.status_item.set_label("服务：运行中")
            else:
                self.status_item.set_label("服务：运行中（未就绪）")
            if not enabled:
                tip = "三指拖移：已关闭"
            elif ready:
                tip = "三指拖移：已开启\n三指拖动窗口 / 选择文字"
            else:
                tip = "三指拖移：已开启，但触控板不可用"
            self.icon.set_tooltip_text(tip)
        finally:
            self._updating = False
        return True


def main() -> int:
    Tray()
    Gtk.main()
    return 0


if __name__ == "__main__":
    sys.exit(main())
