# -*- coding: utf-8 -*-
"""手工验收脚本共用的前置说明与确认。

这些脚本都是「真机验收」，会动到桌面上的东西（鼠标、截图、本程序进程，
个别脚本还会临时改任务栏设置）。所以统一要求：**先把会做什么说清楚，再让人确认**。

非交互环境下（没有控制台 / 管道输入）默认中止，除非显式加 --yes。
"""
from __future__ import annotations

import sys

BAR = "=" * 70


def announce(title: str, will: list[str], wont: list[str]) -> None:
    print(BAR)
    print(title)
    print("-" * 70)
    print("会做：")
    for item in will:
        print("  ·", item)
    print("不会做：")
    for item in wont:
        print("  ·", item)
    print(BAR)


def confirm(auto_yes: bool = False, prompt: str = "确认现在执行？[y/N] ") -> bool:
    if auto_yes:
        print("（已指定 --yes，跳过确认）\n")
        return True
    try:
        interactive = sys.stdin is not None and sys.stdin.isatty()
    except (ValueError, OSError):
        interactive = False
    if not interactive:
        print("当前不是可交互终端，且没有加 --yes —— 已中止，未做任何改动。\n")
        return False
    try:
        answer = input(prompt)
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。\n")
        return False
    return answer.strip().lower() in {"y", "yes", "是"}


def wait_for_operator(message: str) -> bool:
    """等操作员处理完手上的事再继续（例如手动关闭 Codex）。"""
    if sys.stdin is None:
        return False
    try:
        if not sys.stdin.isatty():
            return False
    except (ValueError, OSError):
        return False
    try:
        input(message)
    except (EOFError, KeyboardInterrupt):
        print("\n已取消。\n")
        return False
    return True
