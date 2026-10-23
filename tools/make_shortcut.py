# -*- coding: utf-8 -*-
"""生成桌面快捷方式「Codex 额度」——直接启动额度程序，不带任何联动参数。

不依赖 COM（部分环境里 WScript.Shell 不可用），直接按 MS-SHLLINK 规范写 .lnk：
Header + LinkTargetIDList + LinkInfo(Unicode) + StringData + ExtraData。
IDList 必须存在，否则 ShellExecute 会返回 SE_ERR_NOASSOC。

会顺手清理旧版本留下的「Codex + 额度」快捷方式。

用法：
    python tools\\make_shortcut.py                     # 在桌面创建 / 覆盖
    python tools\\make_shortcut.py --target <exe路径>
    python tools\\make_shortcut.py --remove            # 删除（含旧版快捷方式）
"""
from __future__ import annotations

import argparse
import ctypes
import os
import struct
import sys
import uuid
from ctypes import wintypes

LINK_CLSID = uuid.UUID("00021401-0000-0000-C000-000000000046")

shell32 = ctypes.WinDLL("shell32", use_last_error=True)
ole32 = ctypes.WinDLL("ole32", use_last_error=True)
shell32.SHParseDisplayName.argtypes = [
    wintypes.LPCWSTR, ctypes.c_void_p, ctypes.POINTER(ctypes.c_void_p),
    wintypes.DWORD, ctypes.POINTER(wintypes.ULONG),
]
shell32.SHParseDisplayName.restype = ctypes.c_long
shell32.ILGetSize.argtypes = [ctypes.c_void_p]
shell32.ILGetSize.restype = wintypes.UINT
ole32.CoTaskMemFree.argtypes = [ctypes.c_void_p]

SHORTCUT_NAME = "Codex 额度.lnk"
LEGACY_NAMES = ("Codex + 额度.lnk",)

HAS_LINK_TARGET_IDLIST = 0x00000001
HAS_LINK_INFO = 0x00000002
HAS_NAME = 0x00000004
HAS_WORKING_DIR = 0x00000010
HAS_ARGUMENTS = 0x00000020
HAS_ICON_LOCATION = 0x00000040
IS_UNICODE = 0x00000080

FILE_ATTRIBUTE_ARCHIVE = 0x00000020
SW_SHOWNORMAL = 1
DRIVE_FIXED = 3


def _ansi(text: str) -> bytes:
    try:
        return text.encode("mbcs", errors="replace") + b"\x00"
    except LookupError:
        return text.encode("utf-8") + b"\x00"


def build_idlist(path: str) -> bytes:
    """用 shell 解析目标路径，拿到真正的 ITEMIDLIST，Windows 才能可靠解析快捷方式。"""
    pidl = ctypes.c_void_p()
    attrs = wintypes.ULONG()
    if shell32.SHParseDisplayName(os.path.abspath(path), None,
                                  ctypes.byref(pidl), 0, ctypes.byref(attrs)) != 0 or not pidl:
        raise OSError(f"无法解析路径为 shell 项目：{path}")
    try:
        size = int(shell32.ILGetSize(pidl))
        raw = ctypes.string_at(pidl, size)
    finally:
        ole32.CoTaskMemFree(pidl)
    return struct.pack("<H", len(raw)) + raw


def _link_info(target: str) -> bytes:
    local = _ansi(target)
    suffix = _ansi("")
    local_u = target.encode("utf-16-le") + b"\x00\x00"
    suffix_u = "\x00".encode("utf-16-le")

    header_size = 0x24
    volume = struct.pack("<IIII", 4 + 4 + 4 + 4 + 1, DRIVE_FIXED, 0, 0x10) + b"\x00"
    volume_offset = header_size
    local_offset = volume_offset + len(volume)
    suffix_offset = local_offset + len(local)
    local_u_offset = suffix_offset + len(suffix)
    suffix_u_offset = local_u_offset + len(local_u)
    total = suffix_u_offset + len(suffix_u)

    head = struct.pack(
        "<IIIIIIIII",
        total, header_size, 1,
        volume_offset, local_offset, 0, suffix_offset,
        local_u_offset, suffix_u_offset,
    )
    return head + volume + local + suffix + local_u + suffix_u


def _string_data(text: str) -> bytes:
    encoded = text.encode("utf-16-le")
    return struct.pack("<H", len(text)) + encoded


def build_lnk(target: str, arguments: str = "", working_dir: str = "",
              icon: str = "", description: str = "") -> bytes:
    flags = HAS_LINK_TARGET_IDLIST | HAS_LINK_INFO | IS_UNICODE
    if description:
        flags |= HAS_NAME
    if working_dir:
        flags |= HAS_WORKING_DIR
    if arguments:
        flags |= HAS_ARGUMENTS
    if icon:
        flags |= HAS_ICON_LOCATION

    header = struct.pack("<I", 0x4C) + LINK_CLSID.bytes_le
    header += struct.pack("<I", flags)
    header += struct.pack("<I", FILE_ATTRIBUTE_ARCHIVE)
    header += struct.pack("<QQQ", 0, 0, 0)          # 创建/访问/修改时间：留空
    header += struct.pack("<I", 0)                  # 目标文件大小
    header += struct.pack("<I", 0)                  # 图标索引
    header += struct.pack("<I", SW_SHOWNORMAL)
    header += struct.pack("<H", 0)                  # 热键
    header += struct.pack("<H", 0)                  # 保留
    header += struct.pack("<I", 0)                  # 保留
    header += struct.pack("<I", 0)                  # 保留

    body = build_idlist(target) + _link_info(target)
    if description:
        body += _string_data(description)
    if working_dir:
        body += _string_data(working_dir)
    if arguments:
        body += _string_data(arguments)
    if icon:
        body += _string_data(icon)
    body += struct.pack("<I", 0)  # ExtraData 终止块
    return header + body


def desktop_dir() -> str:
    """优先用户桌面（和 Codex.lnk 放在一起），退而用公共桌面。"""
    user = os.path.join(os.path.expanduser("~"), "Desktop")
    if os.path.isdir(user):
        return user
    public = os.environ.get("PUBLIC")
    if public:
        candidate = os.path.join(public, "Desktop")
        if os.path.isdir(candidate):
            return candidate
    return user


def current_target() -> str:
    root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(root, "dist", "CodexQuotaTray.exe")


def remove_legacy() -> list[str]:
    """删掉旧版本生成的「Codex + 额度」快捷方式。"""
    removed = []
    dirs = [desktop_dir()]
    public = os.environ.get("PUBLIC")
    if public:
        dirs.append(os.path.join(public, "Desktop"))
    for base in dirs:
        for name in LEGACY_NAMES:
            path = os.path.join(base, name)
            if os.path.isfile(path):
                try:
                    os.remove(path)
                    removed.append(path)
                except OSError:
                    pass
    return removed


def create(target: str, name: str = SHORTCUT_NAME,
           description: str = "显示 Codex 额度") -> str:
    """创建快捷方式。不带任何参数启动额度程序本身。"""
    target = os.path.abspath(target)
    if not os.path.isfile(target):
        raise FileNotFoundError(f"目标不存在：{target}")
    path = os.path.join(desktop_dir(), name)
    data = build_lnk(
        target=target,
        arguments="",
        working_dir=os.path.dirname(target),
        icon=target,
        description=description,
    )
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def main() -> int:
    parser = argparse.ArgumentParser(description="创建「Codex 额度」桌面快捷方式")
    parser.add_argument("--target", default=current_target(), help="CodexQuotaTray.exe 路径")
    parser.add_argument("--name", default=SHORTCUT_NAME, help="快捷方式文件名")
    parser.add_argument("--remove", action="store_true", help="删除桌面快捷方式")
    args = parser.parse_args()

    if args.remove:
        for path in [os.path.join(desktop_dir(), args.name)] + [
                os.path.join(desktop_dir(), n) for n in LEGACY_NAMES]:
            if os.path.isfile(path):
                os.remove(path)
                print(f"已删除：{path}")
        return 0

    try:
        created = create(args.target, args.name)
    except FileNotFoundError as exc:
        print(exc)
        return 1
    print(f"已创建：{created}")
    for path in remove_legacy():
        print(f"已删除旧快捷方式：{path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
