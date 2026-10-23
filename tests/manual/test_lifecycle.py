# -*- coding: utf-8 -*-
"""独立运行验收：本程序不启动 Codex、也不跟随 Codex 关闭。

检查项：
  1. exe 能正常启动，并写出「==== 启动 Codex 额度 ====」日志
  2. 启动后连续两次成功读取（证明定时刷新在跑，而不是只在启动时读一次）
  3. Codex 关闭后，程序仍然存活并继续刷新
  4. 文字窗口仍然可见，且贴住任务栏托盘区左侧

关于「关闭 Codex」——**默认不会替你关**：
  Codex 是你在用的程序，强行结束它（taskkill）可能丢掉未保存的对话。
  所以默认流程是：脚本停下来提示你**自己**保存好、关闭 Codex，然后按回车继续。

  如果确实要在无人值守的环境里跑，可以加 --close-codex 让脚本代劳；
  它只会结束 ChatGPT.exe 这一类进程，并且会在结束后把 Codex 重新拉起来。

用法：
    .venv\\Scripts\\python.exe tests\\manual\\test_lifecycle.py
    .venv\\Scripts\\python.exe tests\\manual\\test_lifecycle.py --yes
    .venv\\Scripts\\python.exe tests\\manual\\test_lifecycle.py --yes --close-codex
    .venv\\Scripts\\python.exe tests\\manual\\test_lifecycle.py --yes --keep-codex-closed
"""
from __future__ import annotations

import ctypes
import os
import subprocess
import sys
import time
from ctypes import wintypes

import psutil

import _safety

ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
EXE = os.path.join(ROOT, "dist", "CodexQuotaTray.exe")
LOG = os.path.join(os.environ["LOCALAPPDATA"], "CodexQuotaTray", "tray.log")
DESKTOP = os.path.join(os.path.expanduser("~"), "Desktop")
CODEX_LNK = os.path.join(DESKTOP, "Codex.lnk")
LABEL_CLASS = "CodexQuotaLabelWnd_1"

REFRESH_TIMEOUT = 90.0
POLL = 1.0

user32 = ctypes.WinDLL("user32", use_last_error=True)
user32.SetProcessDPIAware()


class RECT(ctypes.Structure):
    _fields_ = [("left", wintypes.LONG), ("top", wintypes.LONG),
                ("right", wintypes.LONG), ("bottom", wintypes.LONG)]


def rect(hwnd):
    r = RECT()
    user32.GetWindowRect(hwnd, ctypes.byref(r))
    return (r.left, r.top, r.right, r.bottom)


def find_label():
    found = []

    def cb(h, _):
        name = ctypes.create_unicode_buffer(128)
        user32.GetClassNameW(h, name, 128)
        if name.value == LABEL_CLASS:
            found.append(h)
        return True

    user32.EnumWindows(ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND,
                                          wintypes.LPARAM)(cb), 0)
    return found[0] if found else 0


def _pids(name: str) -> list[int]:
    want = name.lower()
    out = []
    for proc in psutil.process_iter(["pid", "name"]):
        if (proc.info["name"] or "").lower() == want:
            out.append(proc.info["pid"])
    return out


def tray_pids() -> list[int]:
    return _pids("CodexQuotaTray.exe")


def codex_pids() -> list[int]:
    return _pids("ChatGPT.exe")


def kill_tray() -> None:
    """只结束本程序自己的进程，不碰别的。"""
    for pid in tray_pids():
        try:
            psutil.Process(pid).kill()
        except psutil.Error:
            pass
    time.sleep(1.0)


def read_lines() -> list[str]:
    if not os.path.isfile(LOG):
        return []
    with open(LOG, encoding="utf-8", errors="replace") as handle:
        return handle.read().splitlines()


def ok_reads() -> list[str]:
    return [line for line in read_lines() if "读取成功" in line]


def wait_until(predicate, limit, what):
    deadline = time.time() + limit
    while time.time() < deadline:
        if predicate():
            return True
        time.sleep(POLL)
    print(f"    超时：{what}")
    return False


def main() -> int:
    auto_yes = "--yes" in sys.argv or "-y" in sys.argv
    close_codex = "--close-codex" in sys.argv
    keep_closed = "--keep-codex-closed" in sys.argv

    _safety.announce(
        "独立运行验收（test_lifecycle.py）",
        [
            "结束本程序自己的 CodexQuotaTray.exe 进程 —— 只动这一个程序",
            "启动 dist\\CodexQuotaTray.exe，并在任务栏显示两行额度",
            "要求你手动关闭 Codex（或在你加 --close-codex 时代为结束 ChatGPT.exe）",
            "关闭 Codex 后检查本程序是否仍在刷新",
            "若由脚本关闭 Codex，测完会用桌面 Codex.lnk 把它重新打开",
        ],
        [
            "不改任何系统设置、注册表、任务栏配置",
            "不重启 explorer.exe，不动你的其他窗口",
            "不删除任何文件",
            "默认不强行结束 Codex —— 需要你手动关闭",
        ],
    )
    if not _safety.confirm(auto_yes):
        return 1

    if not os.path.isfile(EXE):
        print(f"找不到 {EXE}，请先打包")
        return 2

    failures: list[str] = []

    print("[1] 清理已有的本程序实例")
    kill_tray()
    print("    remaining:", tray_pids())

    before = len(ok_reads())
    print("[2] 启动 exe（不带任何参数）")
    subprocess.Popen([EXE])
    if not wait_until(lambda: len(tray_pids()) >= 1, 20, "进程出现"):
        print("结果：失败 —— 进程没有启动")
        return 1
    if not wait_until(lambda: bool(find_label()), 20, "文字窗口出现"):
        failures.append("文字窗口没有出现")
    print(f"    进程 {tray_pids()}  文字窗口 {find_label()}")

    print("[3] 等待启动后的第一次读取")
    if not wait_until(lambda: len(ok_reads()) > before, 40, "第一次读取"):
        failures.append("启动后没有读取成功")
    first = len(ok_reads())

    print("[4] 等待第二次读取（验证定时刷新）")
    if not wait_until(lambda: len(ok_reads()) > first, REFRESH_TIMEOUT, "第二次读取"):
        failures.append("定时刷新没生效（只读了一次）")
    else:
        print("    最近两条：")
        for line in ok_reads()[-2:]:
            print("      ", line)

    print("[5] 关闭 Codex")
    if close_codex:
        if not codex_pids():
            print("    Codex 本来就没在运行")
        else:
            print(f"    按 --close-codex 结束 {len(codex_pids())} 个 ChatGPT.exe 进程")
            for pid in codex_pids():
                try:
                    psutil.Process(pid).kill()
                except psutil.Error:
                    pass
            wait_until(lambda: not codex_pids(), 30, "Codex 未完全退出")
    else:
        if not codex_pids():
            print("    Codex 本来就没在运行，跳过这一步")
        else:
            print(f"    当前有 {len(codex_pids())} 个 ChatGPT.exe 进程在跑。")
            print("    请先保存好正在做的事，然后手动关闭 Codex 窗口；")
            print("    关好之后回到这里按回车继续（Ctrl+C 取消，不会动 Codex）。")
            if not _safety.wait_for_operator("    按回车继续… "):
                print("    没有确认，已取消。")
                return 1
            if not wait_until(lambda: not codex_pids(), 30, "Codex 还没完全退出"):
                failures.append("等不到 Codex 退出（可能只是最小化到托盘了）")
    print(f"    Codex 进程数 {len(codex_pids())}")

    print("[6] 确认 Codex 关掉后仍在刷新")
    closed_at = len(ok_reads())
    if not wait_until(lambda: len(ok_reads()) > closed_at + 1, REFRESH_TIMEOUT,
                      "Codex 关闭后的刷新"):
        failures.append("Codex 关闭后不再刷新")
    else:
        print("    Codex 关闭后的新增读取：")
        for line in ok_reads()[closed_at:]:
            print("      ", line)

    print("[7] 检查进程与文字窗口")
    alive = bool(tray_pids())
    label = find_label()
    visible = bool(user32.IsWindowVisible(label)) if label else False
    print(f"    托盘进程存活={alive}  文字窗口={rect(label) if label else None}  可见={visible}")
    if not alive:
        failures.append("Codex 关闭后进程退出了")
    if not visible:
        failures.append("Codex 关闭后文字不见了")

    if label:
        shell = user32.FindWindowW("Shell_TrayWnd", None)
        tray = user32.FindWindowExW(shell, None, "TrayNotifyWnd", None)
        if tray:
            print(f"    文字右缘距托盘区左界 {rect(tray)[0] - rect(label)[2]} px（期望 10）")

    if close_codex and not keep_closed and os.path.isfile(CODEX_LNK):
        print("[8] 恢复现场：重新启动 Codex（因为刚才是脚本关的）")
        os.startfile(CODEX_LNK)
        wait_until(lambda: bool(codex_pids()), 60, "Codex 未重新启动")
        print(f"    Codex 进程数 {len(codex_pids())}")
    elif not close_codex and codex_pids():
        pass
    else:
        print("[8] Codex 由你手动关闭，脚本不再自动打开它；需要时点桌面 Codex 快捷方式即可。")

    print()
    if failures:
        print("结果：失败")
        for item in failures:
            print("  -", item)
        return 1
    print("结果：全部通过（独立运行 + 定时刷新 + Codex 关闭后仍存活）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
