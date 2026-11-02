# 三指拖移（threefingers）

> 安装后的完整使用手册（含本机实测参数、校准与排障）见
> [使用说明.md](使用说明.md)。

让 Linux 触控板拥有和 macOS「三指拖移」一样的手势：

- 三指按住触控板并移动 → 相当于按住鼠标左键拖动；
- 可以拖动窗口、选择文字、拖动文件/图标；
- 抬起任意一根发起拖拽的手指 → 松开左键，完成拖放；
- 三指轻点不会产生点击（需要移动超过约 1mm 才触发）；
- 第四根手指不参与、不打断正在进行的拖拽。

项目自带系统托盘开关、图形设置窗口和命令行开关，可随时打开/关闭，
开关状态会保存，登录时自动恢复。

当前实现面向 **X11**（已在 Debian 13 + XFCE 4.20 + Synaptics
`SYNA8004:00 06CB:CD8B Touchpad` 上验证）。Wayland 会话暂不支持。

---

## 安装

在项目目录中执行：

```bash
sudo ./install.sh
```

安装脚本会：

1. 添加一条只针对触控板的 udev `uaccess` 规则
   （`/etc/udev/rules.d/70-threefingers-uaccess.rules`），把触控板的
   **只读**权限授予当前登录用户。不会授予键盘或其他设备权限；
2. 把命令安装为 `~/.local/bin/threefingers`；
3. 安装设置窗口的桌面项、托盘和服务的自动启动项；
4. 首次安装时创建默认配置。

安装后立即启动：

```bash
threefingers on
threefingers status
```

如果 `status` 显示触控板权限错误，请注销并重新登录（或重启）后再试。
正常情况下不需要重启。

---

## 使用

### 三指拖移

1. 把三根手指放在触控板上；
2. 移动手指超过约 1mm —— 指针当前位置会按住左键并跟随手指移动；
3. 继续移动即可拖动窗口、选择文字；
4. 抬起任意一根手指结束拖拽。

注意：在三指按住时**不要**再把触控板当作滚动区域，移动三指就是拖拽。

### 开关

有三种方式，效果相同：

| 方式 | 操作 |
| --- | --- |
| 系统托盘 | 点击面板中的触控板图标，勾选/取消「启用三指拖移」 |
| 图形设置 | 运行 `threefingers settings`，或应用菜单 →「三指拖移设置」 |
| 命令行 | `threefingers on` / `threefingers off` / `threefingers toggle` |

托盘图标在登录后自动出现；如果不想使用托盘，也可以只用命令行或设置窗口。
开关状态保存在 `~/.config/threefingers/config.json`，重启后保持。

设置窗口里还可以调整**跟手速度**（0.5×～3.0×）。

---

## 校准与排障

### 查看实时触点

```bash
threefingers debug
```

三指在触控板上移动时会打印每个接触点的 ID、坐标和类型。
如果坐标方向相反，可以修改配置中的 `invert_x` / `invert_y`。

带模拟的手势调试（不会真正点击或移动指针）：

```bash
threefingers debug --simulate
```

它会打印达到阈值后“将要”执行的按下、移动、释放动作，方便确认逻辑。

### 常见问题

**1. `status` 显示「没有读取 /dev/input/eventX 的权限」**

重新运行安装脚本：

```bash
sudo ./install.sh
```

检查 ACL：

```bash
getfacl /dev/input/event6
```

应能看到 `user:fhz:rw-`。如果仍然没有：

```bash
sudo usermod -aG input fhz   # 把 fhz 换成你的用户名
```

然后**注销并重新登录**。注意：加入 `input` 组会获得读取所有输入设备
（包括键盘）的权限，安全性不如默认的 udev 规则，因此只作为兜底方案。

**2. 服务未运行**

```bash
threefingers on
threefingers status
```

查看日志：

```bash
tail -n 50 ~/.local/state/threefingers/daemon.log
```

**3. 方向相反**

编辑 `~/.config/threefingers/config.json`：

```json
{ "invert_x": true }
```

或 `"invert_y": true`，然后：

```bash
threefingers reload
```

**4. 太慢或太快**

在设置窗口拖动「跟手速度」，或直接修改 `sensitivity`（0.2～4.0）：

```json
{ "sensitivity": 1.5 }
```

然后 `threefingers reload`。

**5. 三指轻点会触发中键**

这是 libinput 原有的三指轻点映射（中键），与本功能无关；三指拖移
本身需要移动超过阈值才会按下左键。如果不需要，可关闭触控板的「点按
以单击」或改用其他点击方式。

**6. 打字时手掌误触**

守护进程直接读取触控板的原始触点，因此不经过 libinput 的
「打字时禁用触控板」和压力/面积过滤。当前设备没有报告压力或接触面积，
只能依赖 `MT_TOOL_PALM` 标记（如果固件提供）以及严格的“恰好三指 +
移动阈值”判断。如果经常出现误触，可以降低 `drag_threshold_mm` 或
用托盘开关临时关闭。

**7. 用快捷键关闭了触控板，但三指拖移仍然生效**

X 层面禁用触控板不会停止内核向事件节点上报触点。需要时请用本项目的
开关关闭功能。

**8. Wayland**

当前版本只支持 X11（通过 XTest 注入）。Wayland 下需要改用 uinput
等其他注入方式，尚未实现。

---

## 配置

`~/.config/threefingers/config.json`：

| 键 | 默认值 | 说明 |
| --- | --- | --- |
| `enabled` | `true` | 总开关 |
| `sensitivity` | `1.0` | 跟手速度倍率；1.0 ≈ 屏幕物理 1:1 |
| `drag_threshold_mm` | `1.0` | 三指移动多少毫米后开始拖拽 |
| `invert_x` | `false` | 左右方向反转 |
| `invert_y` | `false` | 上下方向反转 |
| `device` | `"auto"` | 自动查找触控板；也可填 `/dev/input/eventX` 或设备名片段 |
| `pixels_per_mm` | `0.0` | 屏幕每毫米像素数；0 = 自动检测 |

修改配置文件后运行 `threefingers reload` 使其生效（`enabled` 也可以
直接用开关修改）。

---

## 工作原理

1. **读取触点**：以只读方式打开触控板的 evdev 事件节点，解析
   Linux 多点触控协议 B（`ABS_MT_SLOT` / `ABS_MT_TRACKING_ID` /
   `ABS_MT_POSITION_X/Y`）。X 服务器可以同时读取同一设备，不会冲突。
2. **手势识别**：恰好三根手指（排除 `MT_TOOL_PALM`）按下后，计算三指
   质心；质心移动超过阈值时进入拖拽。拖拽期间只使用最初的三根手指，
   第四根手指被忽略；任一发起手指抬起即结束。
3. **注入鼠标**：通过 X11 XTest 扩展发送左键按下、相对移动、释放事件。
   XTest 事件走服务器正常输入路径，窗口管理器（xfwm4）和应用都会当作
   真实鼠标事件处理，所以拖动窗口、文本选择、文件拖放都可用。
4. **物理映射**：用屏幕的物理尺寸（优先 `xrandr` 报告值）计算
   px/mm，再乘以跟手速度。移动距离先按触控板的 units/mm 归一化为毫米。
5. **权限**：udev 的 `uaccess` 标签让 systemd-logind 只把触控板节点的
   读写 ACL 授予当前 seat 的活跃用户；不使用 root 守护进程。

守护进程与 UI 通过 `$XDG_RUNTIME_DIR/threefingers/control.sock`
通信（权限 0600，一行一个 JSON 请求）。

---

## 项目结构

```
bin/threefingers                 命令行入口（可被符号链接）
threefingers/
  cli.py                         子命令
  daemon.py                      守护进程（事件循环）
  evdev_input.py                 触控板 MT-B 解析
  gesture.py                     三指拖移状态机
  x11.py                         XTest 注入
  screen.py                      屏幕 px/mm 检测
  control.py                     Unix socket 控制协议
  gui.py                         设置窗口（GTK3）
  tray.py                        托盘图标（GTK3 StatusIcon）
  config.py                      配置/路径
system/70-threefingers-uaccess.rules
share/                           图标与桌面项模板
tests/                           单元测试
install.sh / uninstall.sh
```

运行测试：

```bash
python3 -m unittest discover -s tests -v
```

---

## 卸载

```bash
sudo ./uninstall.sh
```

保留配置和日志；如需一并删除：

```bash
sudo ./uninstall.sh --purge
```

当前登录会话中已授予的 ACL 可能要等到注销后才会完全失效。

---

## 安全说明

- udev 规则只匹配 `ID_INPUT_TOUCHPAD=1` 的设备，只授予读权限，
  不涉及键盘、鼠标或 `/dev/uinput`。
- 守护进程以当前用户身份运行，不监听网络，只提供本地 Unix socket。
- 注入的只有鼠标左键和相对移动，不发送键盘事件。

---

## 已知限制

- 仅支持 X11；Wayland 不支持。
- 单屏映射；多屏/扩展坞改变分辨率后，建议 `threefingers quit &&
  threefingers on` 重新计算屏幕比例。
- 屏幕物理尺寸依赖 `xrandr`/EDID；如果报告值缺失，跟手速度会退化为
  基于 Xlib 的估计值，可用 `sensitivity` 或 `pixels_per_mm` 手动校正。
- 不经过 libinput 的掌压/面积/打字检测（设备未提供压力数据时尤其明显）。
- 如果同时使用其他三指手势工具（如 `libinput-gestures`、`fusuma`），
  可能会与本功能冲突，请只保留一种。

---

## License

[MIT](LICENSE) © 2026 FAHOZH

