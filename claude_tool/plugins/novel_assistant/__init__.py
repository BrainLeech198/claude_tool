"""小说工作台 —— 选中一本小说工程，右栏直接摊开它；也能从零建一本。

## 它做什么

宿主把"当前选中的工作区"交给它（`register_view("workspace_detail", …)`），它按
作品集 `作品集结构规范.md` §七 的读取契约，把那本书的几样东西画出来：

    书卡        书籍信息.md            书名 / 类别 / 内容简介
    章节        story/*.txt           按章号排序，已发布的标「已发布·冻结」
    人物        人物设定.md           有谁（缺文件就不画这一块）
    本轮观察词   doc/审查清单.md §三  这一轮在盯哪些词

加两颗按钮：「按规范审查」和「打开书目录」。

另外两个入口解决"还没有书"的情况：

    顶栏「✍ 新建小说」      选父目录 + 文件夹名 + 书名，建好、加进左栏并选中
    非小说文件夹的「初始化」  把当前选中的文件夹变成小说工程（不覆盖已有文件）

两者都只铺最低契约（`书籍信息.md` + `story/`），见 `novel_new`。

## 边界：只铺骨架，不跑脚本

插件会写文件，但只在用户点「新建/初始化」时写那两样，**不覆盖任何已存在的文件**，
也不跑任何脚本。审查报告仍然由那个 claude 会话自己落盘——为了审一次稿给插件开
"任意命令执行"的口子不划算（见《小说工作台-插件对接函》N2）。

## 为什么面板按钮不挂 register_action

`register_action("workspace_detail", …)` 挂出来的按钮是**全局**的：当前选中的不是
小说工程时，它照样排在右栏那一行。而审查/打开目录只对"一本书"成立，所以跟面板
一起画。顶栏那颗「新建小说」是全局动作，才用 register_action。
"""
import os
import tkinter as tk                                    # noqa: F401  （面板要用）

from novel_book import detect, is_empty_dir
from novel_new import init_current, new_book
from novel_panel import (build_book_panel, build_collection_panel,
                         build_init_panel, build_start_panel)


def register(host):
    """插件入口。**必须幂等**——宿主每次重建 host 都会再调一次（见 host.register_view）。

    一个全局动作 + 一块面板。面板按当前工作区画：小说工程看书卡，别的文件夹给
    「初始化」入口，没选中时给「新建小说」。**不再留空白**——装了插件就该有东西可点。
    """
    host.register_action("toolbar", "✍ 新建小说", lambda: new_book(host))

    def build(parent, entry):
        path = (entry or {}).get("path")
        kind, data = detect(path)
        if kind == "book":
            build_book_panel(parent, data, host)
        elif kind == "collection":
            build_collection_panel(parent, data, path, host)
        elif path and os.path.isdir(path):
            build_init_panel(parent, path, host,
                             on_init=lambda p=path: init_current(host, p),
                             empty=is_empty_dir(path))
        else:
            build_start_panel(parent, host, on_new=lambda: new_book(host))

    host.register_view("workspace_detail", build)
