# -*- coding: utf-8 -*-
"""托盘界面：两个通知区域图标 + 右键菜单。

只做机械工作：注册窗口、装/换图标、更新提示文本、弹菜单、跑消息循环。
数据与文案由上层传入。
"""
from __future__ import annotations

import ctypes
import threading
from typing import Callable, Optional

import iconart
import w32

WM_TASKBARCREATED = None  # 运行时注册

CLASS_NAME = "CodexQuotaTrayMsgWnd_1"

CMD_REFRESH = 1001
CMD_EXIT = 1002

UID_FIVE = 1
UID_WEEK = 2


class TrayApp:
    def __init__(self, on_refresh: Callable[[], None], on_exit: Callable[[str], None],
                 log=None) -> None:
        self._on_refresh = on_refresh
        self._on_exit = on_exit
        self._log = log
        self._hwnd = 0
        self._wndproc_ref = None
        self._hicons: dict[int, int] = {}
        self._tips: dict[int, str] = {}
        self._added: set[int] = set()
        self._icon_size = w32.small_icon_size()
        self._lock = threading.RLock()
        self._loop_ready = threading.Event()
        self._closed = False
        self._taskbar_created_msg = 0

    # -------------------------------------------------------------- 生命周期
    def run(self) -> None:
        """在当前线程建立消息循环（阻塞直到 quit）。"""
        self._taskbar_created_msg = w32.user32.RegisterWindowMessageW("TaskbarCreated")
        # WNDPROC 必须是模块级存活对象，否则回调地址会被回收
        self._wndproc_ref = w32.WNDPROC(self._dispatch)
        if not w32.register_window_class(CLASS_NAME, self._wndproc_ref):
            err = ctypes.get_last_error()
            if err not in (0, 1410):  # 1410 = 类已注册
                raise OSError(f"RegisterClass 失败: {err}")
        self._hwnd = w32.create_message_window(CLASS_NAME)
        if not self._hwnd:
            raise OSError(f"CreateWindow 失败: {ctypes.get_last_error()}")
        self._loop_ready.set()
        w32.message_loop({})
        self._closed = True
        self._cleanup_icons()

    def wait_ready(self, timeout: float = 5.0) -> bool:
        return self._loop_ready.wait(timeout)

    @property
    def hwnd(self) -> int:
        return self._hwnd

    @property
    def icon_size(self) -> int:
        return self._icon_size

    @property
    def icons_ready(self) -> bool:
        return len(self._added) >= 2

    def stop(self) -> None:
        """从任意线程请求退出。"""
        if self._hwnd:
            w32.PostMessageW(self._hwnd, w32.WM_CLOSE, 0, 0)

    # -------------------------------------------------------------- 图标更新
    def update(self, bucket: str, remaining: Optional[int], tooltip: str) -> None:
        uid = UID_WEEK if bucket == "weekly" else UID_FIVE
        if self._closed or not self._hwnd:
            return
        with self._lock:
            try:
                data = iconart.tray_icon_bytes(bucket, remaining, self._icon_size)
                hicon = w32.icon_from_ico_bytes(data, self._icon_size)
            except Exception as exc:
                if self._log:
                    self._log.warning("生成图标失败 %s: %s", bucket, exc)
                return
            try:
                if uid in self._added:
                    w32.notify_modify(self._hwnd, uid, hicon, tooltip,
                                      w32.WM_TRAYICON)
                else:
                    w32.notify_add(self._hwnd, uid, hicon, tooltip,
                                   w32.WM_TRAYICON)
                    self._added.add(uid)
            except Exception as exc:
                if self._log:
                    self._log.warning("更新托盘图标失败 %s: %s", bucket, exc)
                w32.user32.DestroyIcon(hicon)
                return
            self._tips[uid] = tooltip
            old = self._hicons.get(uid)
            self._hicons[uid] = hicon
            if old:
                w32.user32.DestroyIcon(old)

    def readd(self) -> None:
        """删掉再装一遍，让资源管理器重新读取图标设置（用于提升到可见区）。"""
        if self._closed or not self._hwnd:
            return
        with self._lock:
            for uid in list(self._added):
                try:
                    w32.notify_delete(self._hwnd, uid)
                except Exception:
                    pass
            self._added.clear()
            for uid, hicon in list(self._hicons.items()):
                try:
                    w32.notify_add(self._hwnd, uid, hicon, self._tips.get(uid, ""),
                                   w32.WM_TRAYICON)
                    self._added.add(uid)
                except Exception as exc:
                    if self._log:
                        self._log.warning("重新注册图标失败 %s: %s", uid, exc)

    def _cleanup_icons(self) -> None:
        with self._lock:
            for uid in list(self._added):
                try:
                    w32.notify_delete(self._hwnd, uid)
                except Exception:
                    pass
            self._added.clear()
            self._tips.clear()
            for hicon in self._hicons.values():
                if hicon:
                    w32.user32.DestroyIcon(hicon)
            self._hicons.clear()

    # -------------------------------------------------------------- 消息处理
    def _dispatch(self, hwnd, msg, wparam, lparam):
        return self._wndproc(hwnd, msg, wparam, lparam)

    def _wndproc(self, hwnd, msg, wparam, lparam):
        try:
            if msg == w32.WM_TRAYICON:
                if int(lparam) == w32.WM_RBUTTONUP:
                    self._popup_menu(hwnd)
                    return 0
                return 0
            if msg == self._taskbar_created_msg and self._taskbar_created_msg:
                if self._log:
                    self._log.info("任务栏重建，重新注册图标")
                self._readd_all()
                return 0
            if msg == w32.WM_COMMAND:
                command = int(wparam) & 0xFFFF
                if command == CMD_REFRESH:
                    self._on_refresh()
                    return 0
                if command == CMD_EXIT:
                    self._on_exit("用户选择退出")
                    return 0
                return 0
            if msg == w32.WM_CLOSE:
                w32.user32.DestroyWindow(hwnd)
                return 0
            if msg == w32.WM_DESTROY:
                self._cleanup_icons()
                w32.user32.PostQuitMessage(0)
                return 0
        except Exception as exc:  # 回调里绝不能抛出去
            if self._log:
                self._log.exception("wndproc 异常: %s", exc)
            return 0
        return w32.user32.DefWindowProcW(hwnd, msg, wparam, lparam)

    def _popup_menu(self, hwnd) -> None:
        command = w32.show_menu(hwnd, [
            (CMD_REFRESH, "立即刷新"),
            (0, "-"),
            (CMD_EXIT, "退出"),
        ])
        if command == CMD_REFRESH:
            self._on_refresh()
        elif command == CMD_EXIT:
            self._on_exit("用户选择退出")

    def _readd_all(self) -> None:
        """任务栏重启后重新装图标（保留当前 Tip 与图标）。"""
        with self._lock:
            for uid in list(self._added):
                hicon = self._hicons.get(uid)
                if hicon:
                    try:
                        w32.notify_add(self._hwnd, uid, hicon,
                                       self._tips.get(uid, ""), w32.WM_TRAYICON)
                    except Exception:
                        pass
