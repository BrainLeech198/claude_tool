"""小说工作台 —— 选中一本小说工程，右栏直接摊开它。

## 它做什么

宿主把"当前选中的工作区"交给它（`register_view("workspace_detail", …)`），它按
作品集 `作品集结构规范.md` §七 的读取契约，把那本书的几样东西画出来：

    书卡        书籍信息.md            书名 / 类别 / 内容简介
    章节        story/*.txt           按章号排序，已发布的标「已发布·冻结」
    人物        人物设定.md           有谁（缺文件就不画这一块）
    本轮观察词   doc/审查清单.md §三  这一轮在盯哪些词

加两颗按钮：「按规范审查」和「打开书目录」。

## 边界：只读，不跑脚本

这个插件**不写任何文件、不跑任何脚本**。审查报告由那个 claude 会话自己落盘——
插件跑在启动器进程里，为了审一次稿去给它开"任意命令执行"的口子，不划算（见
《小说工作台-插件对接函》N2）。

## 为什么按钮画在面板里，不用 register_action

`register_action("workspace_detail", …)` 挂出来的按钮是**全局**的：当前选中的不是
小说工程时，它照样排在右栏那一行。而这里的动作只对"一本书"成立，所以跟面板一起画。
详见 `novel_panel` 开头。
"""
import tkinter as tk                                    # noqa: F401  （面板要用）

from novel_book import detect
from novel_panel import build_book_panel, build_collection_panel


def register(host):
    """插件入口。**必须幂等**——宿主每次重建 host 都会再调一次（见 host.register_view）。

    这里只做一件事：注册一块面板。面板自己按当前工作区决定画什么、或者不画。
    """

    def build(parent, entry):
        path = (entry or {}).get("path")
        kind, data = detect(path)
        if kind == "book":
            build_book_panel(parent, data, host)
        elif kind == "collection":
            build_collection_panel(parent, data, path, host)
        # 都不是：什么都不画。宿主会把空面板连「插件」那个标题一起收掉。

    host.register_view("workspace_detail", build)
