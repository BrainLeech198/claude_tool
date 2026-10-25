"""窗口矩形的纯计算：把"上次存下来的位置"夹回当前显示器。

单独拎出来是为了能直接断言——拔掉副屏、分辨率变小、最小化时关窗存了
`-32000` 这类坐标，都是输入是数字、输出是数字的事，不用真开窗就能验。
"""
from collections import namedtuple

Rect = namedtuple("Rect", "x y w h")


def fit_rect(x, y, w, h, area, min_w=1, min_h=1):
    """把一个窗口矩形收进 area（那一块屏幕的工作区，已扣掉任务栏）。

    规则：整扇窗口都要落在同一块屏幕里。窗口比屏幕还大时，缩到屏幕那么大
    （但不会小于 min_w/min_h——那是界面自己的下限，宁可超出屏幕也不能把控件
    挤成一条缝）。位置越界时贴着边放，不搞"只留 80 像素"的半截窗口。

    area 是 (left, top, right, bottom)；多屏时 left/top 可能是负的。
    """
    left, top, right, bottom = area
    room_w = max(right - left, min_w)
    room_h = max(bottom - top, min_h)
    w = min(max(w, min_w), room_w)
    h = min(max(h, min_h), room_h)
    x = min(max(x, left), max(left, right - w))
    y = min(max(y, top), max(top, bottom - h))
    return Rect(x, y, w, h)
