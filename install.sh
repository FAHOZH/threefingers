#!/usr/bin/env bash
# 三指拖移安装脚本
# 用法： sudo ./install.sh
set -euo pipefail

if [[ ${EUID} -ne 0 ]]; then
    echo "请使用 sudo 运行： sudo $0" >&2
    exit 1
fi

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd -P)"
TARGET_USER="${SUDO_USER:-}"
if [[ -z "$TARGET_USER" || "$TARGET_USER" == "root" ]]; then
    echo "无法确定要安装到的普通用户。请以普通用户身份执行： sudo ./install.sh" >&2
    exit 1
fi
TARGET_HOME="$(getent passwd "$TARGET_USER" | cut -d: -f6)"
TARGET_GROUP="$(id -gn "$TARGET_USER")"
TARGET_UID="$(id -u "$TARGET_USER")"
LAUNCHER="$TARGET_HOME/.local/bin/threefingers"

run_as_user() {
    if command -v runuser >/dev/null 2>&1; then
        runuser -u "$TARGET_USER" -- "$@"
    else
        local quoted
        quoted="$(printf '%q ' "$@")"
        su -s /bin/sh "$TARGET_USER" -c "$quoted"
    fi
}

install_user_file() {
    local src="$1" dest="$2" mode="${3:-0644}"
    install -o "$TARGET_USER" -g "$TARGET_GROUP" -m "$mode" "$src" "$dest"
}

echo "==> 安装三指拖移"
echo "    用户: $TARGET_USER ($TARGET_UID)"
echo "    目录: $PROJECT_DIR"

# ---------------------------------------------------------------------------
# 1. 只针对触控板的 udev uaccess 规则
# ---------------------------------------------------------------------------
install -d -m 0755 /etc/udev/rules.d
install -m 0644 \
    "$PROJECT_DIR/system/70-threefingers-uaccess.rules" \
    /etc/udev/rules.d/70-threefingers-uaccess.rules
udevadm control --reload-rules
udevadm trigger --subsystem-match=input --action=change
echo "==> 已安装 udev 规则（只授予触控板读权限，不影响键盘）"

find_touchpad_event() {
    local name_file name event_dir
    for name_file in /sys/class/input/event*/device/name; do
        [[ -r "$name_file" ]] || continue
        name="$(cat "$name_file" 2>/dev/null || true)"
        case "${name,,}" in
            *touchpad*|*"touch pad"*)
                event_dir="$(dirname "$(dirname "$name_file")")"
                echo "/dev/input/$(basename "$event_dir")"
                return 0
                ;;
        esac
    done
    return 1
}

TOUCHPAD_EVENT="$(find_touchpad_event || true)"
ACL_OK=0
if [[ -n "$TOUCHPAD_EVENT" ]]; then
    for _ in $(seq 1 30); do
        if run_as_user test -r "$TOUCHPAD_EVENT" 2>/dev/null; then
            ACL_OK=1
            break
        fi
        sleep 0.2
    done
fi

if [[ $ACL_OK -eq 0 && -n "$TOUCHPAD_EVENT" && -x "$(command -v setfacl || true)" ]]; then
    # uaccess 没有及时生效时的兜底：给当前会话临时加 ACL，
    # 重启或重新登录后仍由 udev 规则接管。
    if setfacl -m "u:${TARGET_USER}:rw" "$TOUCHPAD_EVENT" 2>/dev/null; then
        if run_as_user test -r "$TOUCHPAD_EVENT" 2>/dev/null; then
            ACL_OK=1
            echo "==> 已临时为 $TOUCHPAD_EVENT 授予读权限"
        fi
    fi
fi

if [[ $ACL_OK -eq 1 ]]; then
    echo "==> 触控板权限检查通过：$TOUCHPAD_EVENT"
elif [[ -n "$TOUCHPAD_EVENT" ]]; then
    echo "!! 警告：$TARGET_USER 仍无法读取 $TOUCHPAD_EVENT" >&2
    {
        echo "   可尝试以下任一方式："
        echo "   1. 重新登录或重启系统后再次检查；"
        echo "   2. sudo usermod -aG input $TARGET_USER，然后注销并重新登录；"
        echo "   3. 检查 udev 规则：cat /etc/udev/rules.d/70-threefingers-uaccess.rules"
    } >&2
else
    echo "!! 警告：系统中没有找到触控板事件节点。" >&2
fi

# ---------------------------------------------------------------------------
# 2. 启动器（~/.local/bin，已在普通用户 PATH 中）
# ---------------------------------------------------------------------------
install -d -o "$TARGET_USER" -g "$TARGET_GROUP" -m 0755 \
    "$TARGET_HOME/.local/bin"
if [[ -L "$LAUNCHER" ]]; then
    CURRENT_TARGET="$(readlink -f "$LAUNCHER" || true)"
    if [[ "$CURRENT_TARGET" != "$PROJECT_DIR/bin/threefingers" ]]; then
        echo "错误：$LAUNCHER 已指向别处（$CURRENT_TARGET），拒绝覆盖。" >&2
        exit 1
    fi
elif [[ -e "$LAUNCHER" ]]; then
    echo "错误：$LAUNCHER 已存在且不是符号链接，拒绝覆盖。" >&2
    exit 1
fi
chmod 0755 "$PROJECT_DIR/bin/threefingers"
ln -sfn "$PROJECT_DIR/bin/threefingers" "$LAUNCHER"
chown -h "$TARGET_USER:$TARGET_GROUP" "$LAUNCHER"
echo "==> 已安装命令：$LAUNCHER"

# ---------------------------------------------------------------------------
# 3. 桌面项、自动启动项、图标
# ---------------------------------------------------------------------------
install -d -o "$TARGET_USER" -g "$TARGET_GROUP" -m 0755 \
    "$TARGET_HOME/.config/autostart" \
    "$TARGET_HOME/.local/share/applications" \
    "$TARGET_HOME/.local/share/icons/hicolor/scalable/apps"

render_desktop() {
    local src="$1" dest="$2"
    sed "s|@LAUNCHER@|$LAUNCHER|g" "$src" > "$dest"
    chown "$TARGET_USER:$TARGET_GROUP" "$dest"
    chmod 0644 "$dest"
}
render_desktop "$PROJECT_DIR/share/threefingers-daemon.desktop.in" \
    "$TARGET_HOME/.config/autostart/threefingers-daemon.desktop"
render_desktop "$PROJECT_DIR/share/threefingers-tray.desktop.in" \
    "$TARGET_HOME/.config/autostart/threefingers-tray.desktop"
render_desktop "$PROJECT_DIR/share/threefingers-settings.desktop.in" \
    "$TARGET_HOME/.local/share/applications/threefingers-settings.desktop"

install_user_file "$PROJECT_DIR/share/threefingers.svg" \
    "$TARGET_HOME/.local/share/icons/hicolor/scalable/apps/threefingers.svg"
if [[ ! -f "$TARGET_HOME/.local/share/icons/hicolor/index.theme" &&
      -f /usr/share/icons/hicolor/index.theme ]]; then
    install_user_file /usr/share/icons/hicolor/index.theme \
        "$TARGET_HOME/.local/share/icons/hicolor/index.theme"
fi
run_as_user gtk-update-icon-cache -f -t \
    "$TARGET_HOME/.local/share/icons/hicolor" >/dev/null 2>&1 || true
echo "==> 已安装桌面项、自动启动项和图标"

# ---------------------------------------------------------------------------
# 4. 配置与日志目录
# ---------------------------------------------------------------------------
install -d -o "$TARGET_USER" -g "$TARGET_GROUP" -m 0700 \
    "$TARGET_HOME/.config/threefingers" \
    "$TARGET_HOME/.local/state/threefingers"
CONFIG_PATH="$TARGET_HOME/.config/threefingers/config.json"
if [[ ! -f "$CONFIG_PATH" ]]; then
    cat > "$CONFIG_PATH" <<'EOF'
{
  "enabled": true,
  "sensitivity": 1.0,
  "drag_threshold_mm": 1.0,
  "invert_x": false,
  "invert_y": false,
  "device": "auto",
  "pixels_per_mm": 0.0
}
EOF
    chown "$TARGET_USER:$TARGET_GROUP" "$CONFIG_PATH"
    chmod 0600 "$CONFIG_PATH"
fi
echo "==> 配置：$CONFIG_PATH"

# ---------------------------------------------------------------------------
# 5. 依赖检查
# ---------------------------------------------------------------------------
if ! run_as_user python3 -c "import gi" >/dev/null 2>&1; then
    echo "!! 提示：未安装 python3-gi，设置窗口和托盘图标不可用。" >&2
    echo "   守护进程与命令行仍可工作；可运行：sudo apt install python3-gi" >&2
fi

# ---------------------------------------------------------------------------
echo
echo "=== 安装完成 ==="
echo "现在启动：       threefingers on"
echo "查看状态：       threefingers status"
echo "图形开关：       threefingers settings（或应用菜单“三指拖移设置”）"
echo "托盘开关：       登录后系统托盘中的触控板图标"
echo "重新登录后，服务与托盘会自动启动。"
echo
echo "注意：本实现面向 X11（当前 XFCE 会话）。"
if [[ $ACL_OK -ne 1 ]]; then
    echo "注意：权限检查未通过，请按上面的提示处理后运行 threefingers status 复查。" >&2
fi
