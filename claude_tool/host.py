"""系统层的门面：按平台转发给 winhost 或 nixhost。

上层只 import 这里，底下两个模块按同一组名字导出，所以界面代码不用长平台分支
——三个平台长得一样这件事，靠的是共用同一份界面代码，不是靠自觉。

Windows 那边是 winhost（真去操作 HWND）；Linux / macOS 那边是 nixhost（没有
HWND 这套东西，改按进程管）。

EMBED_SUPPORTED 是这条分界线上唯一的例外：内嵌要往自己的窗口里塞一个别人的
窗口，Windows 上 SetParent 一行就完事，Linux 上得跟窗口管理器打交道、macOS 上
得碰 Cocoa，都不值当。非 Windows 上整块界面直接不出现。

WINDOW_CONTROL 管的是另一件事：按句柄去指挥别人的窗口（挪到旁边、压顶层）。
"关掉"不算在里面——那是按进程树发信号，nixhost 做得到。
"""
import sys

EMBED_SUPPORTED = sys.platform == "win32"
WINDOW_CONTROL = sys.platform == "win32"

if EMBED_SUPPORTED:
    from claude_tool import winhost as _host
else:
    from claude_tool import nixhost as _host

# 两个模块必须导出同样这些名字，缺一个这里就报 AttributeError——要的正是这种
# 当面炸掉，而不是运行到一半才发现某个平台上少了个函数。
EmbeddedConsole = _host.EmbeddedConsole
bring_next_to = _host.bring_next_to
close_window = _host.close_window
fresh_console = _host.fresh_console
fresh_terminal = _host.fresh_terminal
open_path = _host.open_path
open_url = _host.open_url
place_window = _host.place_window
screen_bounds = _host.screen_bounds
spawn_console = _host.spawn_console
spawn_terminal = _host.spawn_terminal
trash_path = _host.trash_path
terminal_windows = _host.terminal_windows
toggle_topmost = _host.toggle_topmost
window_alive = _host.window_alive
window_position = _host.window_position
