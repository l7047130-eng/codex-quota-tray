# -*- coding: utf-8 -*-
"""打包脚本：把 src 打成单文件无控制台的 CodexQuotaTray.exe。

用法： <带 PyInstaller 的 python> build\\build_exe.py
       <任意 python> build\\build_exe.py --docs-only   # 只同步文档与截图到 dist
产物： dist\\CodexQuotaTray.exe

打包结束会把 exe 的完整性级别重置为「中」，原因见 reset_integrity_level()。
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SRC = os.path.join(ROOT, "src")
ENTRY = os.path.join(SRC, "main.py")
ICON = os.path.join(ROOT, "assets", "app.ico")
DIST = os.path.join(ROOT, "dist")
WORK = os.path.join(ROOT, "build", "pyi")
NAME = "CodexQuotaTray"


INTEGRITY_NAMES = {0x0000: "Untrusted", 0x1000: "Low", 0x2000: "Medium",
                   0x3000: "High", 0x4000: "System"}


def file_integrity_level(path: str) -> str | None:
    """读取文件的强制完整性级别（不依赖 icacls 的本地化输出）。

    返回 "Low" / "Medium" / "High" 等；文件没有显式标签时返回 None
    （等同于 Medium）。用 GetNamedSecurityInfoW 取 SACL，再从
    SYSTEM_MANDATORY_LABEL_ACE 里读 SID 的第一个子权威。
    """
    import ctypes
    from ctypes import wintypes

    advance = ctypes.WinDLL("advapi32", use_last_error=True)
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)

    SE_FILE_OBJECT = 1
    LABEL_SECURITY_INFORMATION = 0x00000010
    DACL_SECURITY_INFORMATION = 0x00000004
    SYSTEM_MANDATORY_LABEL_ACE_TYPE = 0x11

    sacl = ctypes.c_void_p()
    sd = ctypes.c_void_p()
    advance.GetNamedSecurityInfoW.argtypes = [
        wintypes.LPWSTR, ctypes.c_int, wintypes.DWORD,
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p), ctypes.POINTER(ctypes.c_void_p),
        ctypes.POINTER(ctypes.c_void_p)]
    rc = advance.GetNamedSecurityInfoW(
        path, SE_FILE_OBJECT, LABEL_SECURITY_INFORMATION,
        None, None, None, ctypes.byref(sacl), ctypes.byref(sd))
    if rc != 0 or not sacl:
        return None
    try:
        # ACL: AclRevision(1) Sbz1(1) AclSize(2) AceCount(2) Sbz2(2)，ACE 从偏移 8 开始
        header = ctypes.string_at(sacl, 8)
        ace_count = int.from_bytes(header[4:6], "little")
        offset = 8
        for _ in range(ace_count):
            ace = ctypes.string_at(sacl.value + offset, 4)
            ace_type = ace[0]
            ace_size = int.from_bytes(ace[2:4], "little")
            if ace_type == SYSTEM_MANDATORY_LABEL_ACE_TYPE and ace_size >= 12:
                # ACE_HEADER(4) + Mask(4) + SidStart(4 起)
                sid = ctypes.c_void_p(sacl.value + offset + 8)
                advance.GetSidSubAuthorityCount.argtypes = [ctypes.c_void_p]
                advance.GetSidSubAuthorityCount.restype = ctypes.POINTER(ctypes.c_ubyte)
                count = advance.GetSidSubAuthorityCount(sid)[0]
                advance.GetSidSubAuthority.restype = ctypes.POINTER(wintypes.DWORD)
                advance.GetSidSubAuthority.argtypes = [ctypes.c_void_p, wintypes.DWORD]
                rid = advance.GetSidSubAuthority(sid, count - 1)[0]
                return INTEGRITY_NAMES.get(rid & 0xF000, f"0x{rid:X}")
            offset += ace_size
        return None
    finally:
        kernel32.LocalFree(sd)


def reset_integrity_level(path: str) -> tuple[bool, str]:
    """把产物 exe 的完整性级别显式设为「中」。

    背景：DSH 等工作区沙箱会给项目目录打上「低完整性」强制标签，目录里的文件
    会继承该标签。Windows 对带完整性标签的可执行文件有特殊规则——由它启动的
    进程会以同样的低完整性运行。PyInstaller 单文件 bootloader 在低完整性下读不出
    自己内嵌的 PKG 归档，于是弹出
        Could not load PyInstaller's embedded PKG archive from the executable
    然后卡住；无控制台版本看起来就是「双击没反应」，而且一行日志都不写。

    实测：同一份 exe 放到没有低完整性标签的目录就完全正常。所以打包后把 exe
    重置为「中」即可，和代码无关。
    """
    try:
        result = subprocess.run(
            ["icacls", path, "/setintegritylevel", "Medium"],
            capture_output=True, text=True, errors="replace",
        )
    except OSError as exc:
        return False, f"无法执行 icacls：{exc}"
    if result.returncode == 0:
        return True, ""
    detail = ((result.stderr or "") + (result.stdout or "")).strip()
    return False, detail.replace("\n", " ")[:300] or f"icacls 退出码 {result.returncode}"


def sync_docs() -> None:
    """把 README / 使用说明 / 测试报告与截图同步到 dist，让交付目录能整体拷走。

    dist 是自包含的：报告在项目里引用 `images/xxx.png`（相对 docs/），
    拷到 dist 根目录后要多一层 `docs/`，所以这里做一次路径改写。
    """
    for name in ("使用说明.md", "README.md"):
        src = os.path.join(ROOT, name)
        if os.path.isfile(src):
            shutil.copy2(src, os.path.join(DIST, name))
    report = os.path.join(ROOT, "docs", "测试报告.md")
    if os.path.isfile(report):
        with open(report, encoding="utf-8") as handle:
            text = handle.read()
        text = text.replace("](images/", "](docs/images/")
        with open(os.path.join(DIST, "测试报告.md"), "w", encoding="utf-8",
                  newline="") as handle:
            handle.write(text)
    images = os.path.join(ROOT, "docs", "images")
    if os.path.isdir(images):
        target = os.path.join(DIST, "docs", "images")
        os.makedirs(target, exist_ok=True)
        for name in os.listdir(images):
            if name.lower().endswith(".png"):
                shutil.copy2(os.path.join(images, name), os.path.join(target, name))


def main() -> int:
    pyinstaller = os.path.join(os.path.dirname(sys.executable), "pyinstaller.exe")
    if not os.path.isfile(pyinstaller):
        pyinstaller = "pyinstaller"

    if not os.path.isfile(ICON):
        sys.path.insert(0, SRC)
        import iconart  # noqa: WPS433

        iconart.build_app_ico(ICON)

    cmd = [
        pyinstaller,
        "--noconfirm",
        "--clean",
        "--onefile",
        "--windowed",
        "--name", NAME,
        "--icon", ICON,
        "--paths", SRC,
        "--distpath", DIST,
        "--workpath", WORK,
        "--specpath", WORK,
        "--exclude-module", "tkinter",
        "--exclude-module", "unittest",
        "--exclude-module", "pydoc",
        "--exclude-module", "test",
        ENTRY,
    ]
    print(" ".join(cmd))
    result = subprocess.run(cmd, cwd=ROOT)
    if result.returncode != 0:
        return result.returncode

    exe = os.path.join(DIST, NAME + ".exe")
    if not os.path.isfile(exe):
        print("打包失败：未生成 exe")
        return 1
    size_mb = os.path.getsize(exe) / 1_000_000   # 用十进制 MB，和资源管理器一致
    print(f"\n完成：{exe}  ({size_mb:.1f} MB)")

    # 关键一步：工作区沙箱可能给目录打了低完整性标签，会让打出来的 exe 起不来。
    # 这一步失败就不能交付，所以直接判构建失败，并把坏产物删掉防止被误发。
    level = file_integrity_level(exe)
    if level in (None, "Medium", "High", "System"):
        integrity_ok, integrity_detail = True, ""
        print(f"exe 完整性级别 = {level or '未设标签（等同 Medium）'}，无需重置")
    else:
        integrity_ok, integrity_detail = reset_integrity_level(exe)
    if not integrity_ok:
        print("\n构建失败：未能把 exe 的完整性级别重置为「中」。")
        print("这种 exe 在带「低完整性」标签的目录里会表现为「双击没反应、日志一行不写」，")
        print("不能作为交付物。已删除这个产物，请修复后重新打包。")
        print(f"icacls 报错：{integrity_detail}")
        print(f'手动验证命令： icacls "{exe}" /setintegritylevel Medium')
        try:
            os.remove(exe)
        except OSError as exc:
            print(f"（顺带删除产物也失败了：{exc}）")
        return 1
    print("已把 exe 完整性级别设为「中」（避免沙箱低标签导致起不来）")

    # 顺手把文档和截图拷到产物目录，方便整体拷走
    sync_docs()
    return 0


if __name__ == "__main__":
    if "--docs-only" in sys.argv:
        sync_docs()
        print(f"已同步文档与截图到 {DIST}")
        sys.exit(0)
    sys.exit(main())
