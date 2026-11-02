#!/usr/bin/env bash
# 三指拖移卸载脚本
# 用法： sudo ./uninstall.sh [--purge]
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "请使用 sudo 运行： sudo $0" >&2
    exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
TARGET_USER="${SUDO_USER:-}"
if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
    echo "无法确定目标用户。请以普通用户身份执行： sudo ./uninstall.sh" >&2
    exit 1
fi
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
TARGET_UID="$(id -u "$TARGET_USER")"
LAUNCHER="$TARGET_HOME/.local/bin/threefingers"
RUNTIME_DIR="/run/user/$TARGET_UID"
PURGE=0
[[ "${1:-}" == "--purge" ]] && PURGE=1

run_as_user() {
    if command -v runuser >/dev/null 2>&1; then
        runuser -u "$TARGET_USER" -- "$@"
    else
        local quoted
        quoted="$(printf '%q ' "$@")"
        su -s /bin/sh "$TARGET_USER" -c "$quoted"
    fi
}

echo "==> 卸载三指拖移"

# 1. 停止守护进程
if [[ -x "$LAUNCHER" ]]; then
    run_as_user env XDG_RUNTIME_DIR="$RUNTIME_DIR" "$LAUNCHER" quit \
        >/dev/null 2>&1 || true
    echo "==> 已请求停止守护进程"
fi

# 2. 移除 udev 规则
rm -f /etc/udev/rules.d/70-threefingers-uaccess.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=input --action=change
echo "==> 已移除 udev 规则"

# 3. 移除桌面项、自动启动项、图标和启动器
rm -f "$TARGET_HOME/.config/autostart/threefingers-daemon.desktop" \
      "$TARGET_HOME/.config/autostart/threefingers-tray.desktop" \
      "$TARGET_HOME/.local/share/applications/threefingers-settings.desktop" \
      "$TARGET_HOME/.local/share/icons/hicolor/scalable/apps/threefingers.svg"

if [[ -L "$LAUNCHER" ]]; then
    TARGET="$(readlink -f "$LAUNCHER" || true)"
    if [[ "$TARGET" == "$PROJECT_DIR/bin/threefingers" ]]; then
        rm -f "$LAUNCHER"
    else
        echo "!! 保留 $LAUNCHER（指向 $TARGET）" >&2
    fi
fi
run_as_user gtk-update-icon-cache -f -t \
    "$TARGET_HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true

if [[ $PURGE -eq 1 ]]; then
    rm -rf "$TARGET_HOME/.config/threefingers" \
           "$TARGET_HOME/.local/state/threefingers"
    echo "==> 已删除配置和日志"
else
    echo "==> 配置和日志保留在："
    echo "    $TARGET_HOME/.config/threefingers"
    echo "    $TARGET_HOME/.local/state/threefingers"
    echo "    如需一并删除，请使用： sudo $0 --purge"
fi

echo
echo "=== 卸载完成 ==="
echo "当前会话中触控板的临时 ACL 可能要等到注销后才失效。"
