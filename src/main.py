# -*- coding: utf-8 -*-
"""Codex 额度：在任务栏折叠箭头左侧显示 5 小时与每周剩余额度。

完全独立运行：由用户自己启动、自己退出，不打开 Codex，也不随 Codex 关闭而退出。

用法：
    CodexQuotaTray.exe                 启动额度显示（任务栏两行文字）
    CodexQuotaTray.exe --tray-icons    改用通知区域图标显示（备用）
    CodexQuotaTray.exe --once          读一次额度并打印 JSON（排查用）
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import threading
import time
import traceback
from datetime import datetime
from typing import Optional

# PyInstaller 单文件模式下把 src 作为根，普通运行时手动把 src 加进路径
_HERE = os.path.dirname(os.path.abspath(__file__))
if _HERE not in sys.path:
    sys.path.insert(0, _HERE)

import applog  # noqa: E402
import iconart  # noqa: E402
import quota  # noqa: E402
import taskbar_label as label_mod  # noqa: E402
import tray as tray_mod  # noqa: E402
import w32  # noqa: E402

APP_NAME = "Codex 额度"
VERSION = "1.2.0"
MUTEX_NAME = "Local\\CodexQuotaTray_SingleInstance_v1"

DEFAULT_INTERVAL = 15.0
BUCKET_LABEL = {"five_hour": "5 小时", "weekly": "每周"}
BUCKET_ORDER = ("five_hour", "weekly")


# ------------------------------------------------------------------ 文本
def human_left(seconds: float) -> str:
    seconds = max(0, int(seconds))
    days, rest = divmod(seconds, 86400)
    hours, rest = divmod(rest, 3600)
    minutes, secs = divmod(rest, 60)
    if days:
        return f"{days}天{hours}小时后"
    if hours:
        return f"{hours}小时{minutes}分后"
    if minutes:
        return f"{minutes}分{secs}秒后"
    return f"{secs}秒后"


def fmt_reset(epoch: Optional[int]) -> str:
    if not epoch:
        return "重置时间未知"
    try:
        when = datetime.fromtimestamp(epoch)
    except (OverflowError, OSError, ValueError):
        return "重置时间未知"
    today = datetime.now().date()
    if when.date() == today:
        return when.strftime("%H:%M")
    return when.strftime("%m-%d %H:%M")


def build_reset_line(window: Optional[quota.QuotaWindow]) -> str:
    """悬停卡片里的次要信息行：重置时刻 + 距离重置还有多久。"""
    if window is None:
        return "接口未返回该额度窗口"
    if not window.resets_at:
        return "重置时间未知"
    left = window.resets_at - time.time()
    if left > 0:
        return f"重置 {fmt_reset(window.resets_at)} · {human_left(left)}"
    return f"重置 {fmt_reset(window.resets_at)} · 已到重置点，等待刷新"


# ------------------------------------------------------------------ 可见区提升
PIN_TOOLTIP_PREFIXES = ("5 小时额度", "每周额度")


def _record_is_ours(path: str, tip: str, exe: str) -> bool:
    """判断一条 NotifyIconSettings 记录是否确实属于本程序。

    两个条件必须同时满足：
      * 可执行路径就是自己；
      * 提示文字以本程序的特征文案开头。

    只看路径会在「源码运行」（sys.executable 是 python.exe）时误伤别的 Python 程序；
    只看提示文字又可能误伤别的工具。确认不了就不动它。
    """
    return os.path.normcase(path) == exe and tip.startswith(PIN_TOOLTIP_PREFIXES)


def pin_tray_icons(log) -> int:
    """Windows 11 默认把新出现的托盘图标收进隐藏区。

    这里只修改 HKEY_CURRENT_USER 下本工具自己的两条记录，把 IsPromoted 设为 1，
    让图标直接显示在任务栏通知区域。不动其他程序的设置。
    返回实际修改的条数。
    """
    try:
        import winreg
    except ImportError:  # 非 Windows
        return 0
    exe = os.path.normcase(os.path.abspath(sys.executable))
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                              r"Control Panel\NotifyIconSettings", 0,
                              winreg.KEY_READ | winreg.KEY_WRITE)
    except OSError:
        return 0
    changed = 0
    try:
        count = winreg.QueryInfoKey(root)[0]
        for index in range(count):
            try:
                name = winreg.EnumKey(root, index)
                sub = winreg.OpenKey(root, name, 0, winreg.KEY_READ | winreg.KEY_WRITE)
            except OSError:
                continue
            try:
                try:
                    path = str(winreg.QueryValueEx(sub, "ExecutablePath")[0])
                except FileNotFoundError:
                    path = ""
                try:
                    tip = str(winreg.QueryValueEx(sub, "InitialTooltip")[0])
                except FileNotFoundError:
                    tip = ""
                if not _record_is_ours(path, tip, exe):
                    continue
                try:
                    promoted = int(winreg.QueryValueEx(sub, "IsPromoted")[0])
                except (FileNotFoundError, ValueError):
                    promoted = 0
                if promoted != 1:
                    winreg.SetValueEx(sub, "IsPromoted", 0, winreg.REG_DWORD, 1)
                    changed += 1
            except OSError as exc:
                if log:
                    log.warning("写入托盘可见性设置失败: %s", exc)
            finally:
                try:
                    winreg.CloseKey(sub)
                except OSError:
                    pass
    finally:
        winreg.CloseKey(root)
    return changed


def ensure_forced_exit(log, timeout: float = 6.0) -> None:
    """兜底：若消息循环没能按时退出，直接结束进程，避免留下无响应的托盘僵尸。
    额度查询子进程被放进 Job 对象，进程结束时系统会一并回收。"""
    def _watch() -> None:
        time.sleep(timeout)
        log.warning("界面未在 %.0f 秒内退出，强制结束进程", timeout)
        try:
            import psutil  # noqa: F401
        except ImportError:
            pass
        os._exit(0)

    threading.Thread(target=_watch, name="force-exit", daemon=True).start()


def cleanup_marker() -> str:
    """一次性清理的标记文件；存在就不再扫注册表。"""
    return os.path.join(applog.app_data_dir(), ".tray_cleanup_v1")


def remove_tray_settings(log) -> int:
    """清掉早期版本在 HKCU 下留下的托盘图标记录（含 IsPromoted 常显设置）。

    两点约束：
      * **只在第一次启动时扫一次**，扫完写 `.tray_cleanup_v1` 标记，
        之后正常启动完全不碰注册表；
      * 只删「可执行路径 + 提示文字」都能确认归属本程序的条目，确认不了就不删。

    万一有记录没删掉，删掉标记文件即可让下次启动重试。
    """
    marker = cleanup_marker()
    if os.path.isfile(marker):
        return 0
    try:
        import winreg
    except ImportError:
        return 0
    exe = os.path.normcase(os.path.abspath(sys.executable))
    try:
        root = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                              r"Control Panel\NotifyIconSettings", 0,
                              winreg.KEY_READ | winreg.KEY_WRITE)
    except OSError:
        return 0
    targets: list[str] = []
    try:
        count = winreg.QueryInfoKey(root)[0]
        for index in range(count):
            try:
                name = winreg.EnumKey(root, index)
                sub = winreg.OpenKey(root, name, 0, winreg.KEY_READ)
            except OSError:
                continue
            try:
                try:
                    path = str(winreg.QueryValueEx(sub, "ExecutablePath")[0])
                except FileNotFoundError:
                    path = ""
                try:
                    tip = str(winreg.QueryValueEx(sub, "InitialTooltip")[0])
                except FileNotFoundError:
                    tip = ""
                if _record_is_ours(path, tip, exe):
                    targets.append(name)
            finally:
                try:
                    winreg.CloseKey(sub)
                except OSError:
                    pass
    finally:
        winreg.CloseKey(root)

    removed = 0
    failed = 0
    for name in targets:
        try:
            root = winreg.OpenKey(winreg.HKEY_CURRENT_USER,
                                  r"Control Panel\NotifyIconSettings", 0,
                                  winreg.KEY_ALL_ACCESS)
            try:
                winreg.DeleteKey(root, name)
                removed += 1
            finally:
                winreg.CloseKey(root)
        except OSError as exc:
            failed += 1
            if log:
                log.warning("删除托盘图标记录 %s 失败: %s", name, exc)

    # 不管成功与否都记下已完成，避免正常启动每次重复扫注册表。
    # 有失败时提示一下，删掉标记文件即可让下次启动重试。
    try:
        with open(marker, "w", encoding="utf-8") as handle:
            handle.write("tray cleanup done\n")
    except OSError:
        pass
    if failed and log:
        log.warning("有 %d 条托盘记录没删掉；如需重试请删除 %s", failed, marker)
    return removed


class TrayAdapter:
    """把统一视图转成托盘图标版的两条 update（保留 -–tray-icons 后备方案）。"""

    def __init__(self, tray: "tray_mod.TrayApp") -> None:
        self.tray = tray

    def update(self, view) -> None:
        for bucket, pct, reset in (("five_hour", view.five, view.five_reset),
                                   ("weekly", view.week, view.week_reset)):
            name = BUCKET_LABEL[bucket]
            if view.loading:
                self.tray.update(bucket, None, f"{name}额度 正在读取…")
                continue
            if view.failed:
                self.tray.update(bucket, None,
                                 f"{name}额度 不可用（{view.message or '读取失败'}）"[:118])
                continue
            text = f"{name}额度 剩余 " + ("—" if pct is None else f"{pct}%")
            if reset:
                text += "｜" + reset
            self.tray.update(bucket, pct, text[:118])

    def stop(self) -> None:
        self.tray.stop()


# ------------------------------------------------------------------ 控制器
class Controller:
    def __init__(self, args, log) -> None:
        self.args = args
        self.log = log
        exe = args.codex_exe or quota.find_codex_exe()
        if args.codex_exe and not os.path.isfile(args.codex_exe):
            log.warning("指定的 codex.exe 不存在：%s", args.codex_exe)
        self.reader = quota.RateLimitReader(exe=exe, log=log, job=args.job)
        self.ui = None
        self._stop = threading.Event()
        self._wake = threading.Event()
        self._state_lock = threading.Lock()
        self._snapshot: Optional[quota.QuotaSnapshot] = None
        self._error: Optional[str] = None
        self._loading = True
        self._refreshing = False
        self._thread: Optional[threading.Thread] = None
        self._exit_reason = ""

    # -- 组装
    def attach_ui(self, ui) -> None:
        self.ui = ui

    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, name="refresh-loop", daemon=True)
        self._thread.start()

    # -- 刷新循环
    def _loop(self) -> None:
        """启动立即读一次，之后每 interval 秒重读；右键「立即刷新」可提前唤醒。

        注意：_wake 只有在启动和手动刷新时才会被 set，所以定时刷新必须自己
        按时间判断，不能只在 _wake 被唤醒时才读——否则第一次读完就再也不读了。
        """
        self._wake.set()  # 首次立刻刷新
        last = 0.0
        while not self._stop.is_set():
            signalled = self._wake.wait(0.2)
            if self._stop.is_set():
                return
            if signalled:
                self._wake.clear()
                self._refresh(manual=False)
                last = time.monotonic()
            elif time.monotonic() - last >= self.args.interval:
                self._refresh(manual=False)
                last = time.monotonic()

    def refresh_now(self) -> None:
        """右键「立即刷新」。"""
        if self._stop.is_set():
            return
        self.log.info("手动刷新")
        self._wake.set()

    def _refresh(self, manual: bool) -> None:
        if self._refreshing:
            return
        self._refreshing = True
        try:
            snapshot = self.reader.read()
            with self._state_lock:
                self._snapshot = snapshot
                self._error = None
                self._loading = False
            self.log.info(
                "读取成功 来源=%s 5h=%s%%(剩%d) 周=%s%%(剩%d)",
                snapshot.source,
                f"{snapshot.five_hour.used_percent:.0f}" if snapshot.five_hour else "无",
                snapshot.five_hour.remaining_percent if snapshot.five_hour else -1,
                f"{snapshot.weekly.used_percent:.0f}" if snapshot.weekly else "无",
                snapshot.weekly.remaining_percent if snapshot.weekly else -1,
            )
        except Exception as exc:
            detail = str(exc) or exc.__class__.__name__
            with self._state_lock:
                self._snapshot = None  # 关键：失败即丢弃旧值，绝不把旧数字当实时值
                self._error = detail
                self._loading = False
            self.log.warning("读取失败：%s", detail)
        finally:
            self._refreshing = False
            self._paint()

    def _paint(self) -> None:
        if self.ui is None:
            return
        with self._state_lock:
            snapshot = self._snapshot
            error = self._error
            loading = self._loading
        if loading:
            state = "loading"
        elif error:
            state = "error"
        else:
            state = "ok"
        five = week = None
        five_reset = week_reset = ""
        if snapshot is not None:
            five_window = snapshot.five_hour
            week_window = snapshot.weekly
            five = five_window.remaining_percent if five_window else None
            week = week_window.remaining_percent if week_window else None
            five_reset = build_reset_line(five_window)
            week_reset = build_reset_line(week_window)
        detail = ""
        if error:
            detail = (error.strip().splitlines() or ["读取失败"])[0]
            if len(detail) > 42:
                detail = detail[:41] + "…"
        self.ui.update(label_mod.QuotaView(
            state=state, five=five, week=week,
            five_reset=five_reset, week_reset=week_reset, message=detail,
        ))

    # -- 退出
    def request_exit(self, reason: str) -> None:
        if self._stop.is_set():
            return
        self._exit_reason = reason
        self.log.info("请求退出：%s", reason)
        self._stop.set()
        self._wake.set()
        try:
            if self.ui is not None:
                self.ui.stop()
        except Exception:
            self.log.exception("通知界面退出失败")
        ensure_forced_exit(self.log)

    def shutdown(self) -> None:
        self._stop.set()
        self._wake.set()
        try:
            self.reader.close()
            self.log.info("已清理额度查询子进程")
        except Exception:
            self.log.exception("清理子进程时出错")
        if self._thread is not None:
            self._thread.join(timeout=3)


# ------------------------------------------------------------------ 入口
def parse_args(argv: Optional[list[str]] = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        prog="CodexQuotaTray",
        description="在通知区域显示 Codex 的 5 小时与每周剩余额度",
    )
    parser.add_argument("--interval", type=float, default=DEFAULT_INTERVAL,
                        help=f"刷新间隔秒数，默认 {DEFAULT_INTERVAL:.0f}")
    parser.add_argument("--once", action="store_true",
                        help="只读一次额度并输出 JSON，然后退出")
    parser.add_argument("--icon-preview", metavar="PNG",
                        help="渲染图标预览图到指定文件后退出")
    parser.add_argument("--codex-exe", metavar="PATH",
                        help="指定 codex.exe 路径（默认自动查找）")
    parser.add_argument("--tray-icons", action="store_true",
                        help="用通知区域图标显示（旧的显示方式，备用）")
    parser.add_argument("--no-pin", action="store_true",
                        help="不自动把托盘图标提升到任务栏可见区（默认会提升）")
    parser.add_argument("--version", action="version", version=f"%(prog)s {VERSION}")
    args = parser.parse_args(argv)
    if args.interval < 5:
        args.interval = 5.0
    return args


def _emit_json(text: str, log) -> None:
    """把 --once 的结果送出去。

    打包版是 --windowed（无控制台）：在命令行里带重定向执行时 stdout 是一个管道，
    print 正常；但直接双击、或父进程没给标准输出句柄时，sys.stdout 是 None，
    print 会静默什么都不做。那种情况下改成写文件，保证一定拿得到结果。
    """
    if sys.stdout is not None:
        print(text)
        return
    path = os.path.join(applog.app_data_dir(), "once.json")
    try:
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(text + "\n")
        log.info("没有可用的标准输出，结果已写入 %s", path)
    except OSError as exc:
        log.warning("写入 %s 失败：%s", path, exc)


def dump_once(log) -> int:
    reader = quota.RateLimitReader(log=log)
    try:
        snapshot = reader.read()
    except Exception as exc:
        _emit_json(json.dumps({"ok": False, "error": str(exc)}, ensure_ascii=False), log)
        return 1
    finally:
        reader.close()
    payload = {
        "ok": True,
        "source": snapshot.source,
        "plan_type": snapshot.plan_type,
        "fetched_at": snapshot.fetched_at.strftime("%Y-%m-%d %H:%M:%S"),
        "windows": {},
    }
    for bucket in BUCKET_ORDER:
        window = snapshot.window(bucket)
        if window is None:
            payload["windows"][bucket] = None
            continue
        payload["windows"][bucket] = {
            "remaining_percent": window.remaining_percent,
            "used_percent": window.used_percent,
            "window_minutes": window.window_minutes,
            "resets_at": window.resets_at,
            "resets_at_text": datetime.fromtimestamp(window.resets_at).strftime(
                "%Y-%m-%d %H:%M:%S") if window.resets_at else None,
        }
    _emit_json(json.dumps(payload, ensure_ascii=False, indent=2), log)
    return 0


def run(argv: Optional[list[str]] = None) -> int:
    args = parse_args(argv)
    log = applog.setup()

    if args.icon_preview:
        build_preview_sheet(args.icon_preview)
        print(f"已生成预览图：{args.icon_preview}")
        return 0

    if args.once:
        return dump_once(log)

    w32.set_dpi_aware()

    mutex, already = w32.create_mutex(MUTEX_NAME)
    if already:
        log.info("已存在实例，本次启动不再创建")
        return 0

    log.info("==== 启动 %s v%s ====", APP_NAME, VERSION)
    log.info("codex.exe: %s", args.codex_exe or quota.find_codex_exe() or "未找到")

    job = w32.KillOnCloseJob()
    args.job = job if job.valid else None

    archive = Controller(args, log)

    if args.tray_icons:
        tray_app = tray_mod.TrayApp(
            on_refresh=archive.refresh_now,
            on_exit=archive.request_exit,
            log=log,
        )
        archive.attach_ui(TrayAdapter(tray_app))
        loop_owner = tray_app
        log.info("显示方式：通知区域图标（--tray-icons）")
    else:
        label = label_mod.TaskbarLabel(
            on_refresh=archive.refresh_now,
            on_exit=archive.request_exit,
            log=log,
        )
        archive.attach_ui(label)
        loop_owner = label
        log.info("显示方式：任务栏文字")

    def _bootstrap() -> None:
        if not loop_owner.wait_ready(5.0):
            log.error("界面消息循环未就绪")
            archive.request_exit("界面初始化失败")
            return
        archive.start()
        if args.tray_icons:
            if args.no_pin:
                return
            deadline = time.monotonic() + 20
            while time.monotonic() < deadline and not tray_app.icons_ready:
                time.sleep(0.2)
            changed = pin_tray_icons(log)
            if changed:
                log.info("已将 %d 个托盘图标设为常显（隐藏区 → 通知区域）", changed)
                tray_app.readd()
        else:
            removed = remove_tray_settings(log)
            if removed:
                log.info("已清理旧的托盘图标记录 %d 条", removed)

    threading.Thread(target=_bootstrap, name="bootstrap", daemon=True).start()

    exit_code = 0
    try:
        loop_owner.run()
    except Exception:
        log.exception("消息循环异常")
        exit_code = 1
    finally:
        archive.shutdown()
        job.close()
        try:
            mutex and w32.kernel32.CloseHandle(mutex)
        except Exception:
            pass
        log.info("==== 退出：%s ====", archive._exit_reason or "正常结束")
    return exit_code


def build_preview_sheet(path: str) -> None:
    """给文档用的图标预览：4 种数值状态 × 2 种额度 × 3 种尺寸。"""
    from PIL import Image, ImageDraw, ImageFont

    states = [(92, "充足"), (35, "偏少"), (8, "紧张"), (None, "不可用")]
    sizes = [16, 20, 32]
    zoom = 7
    gap = 14
    pad = 18
    icon_span = sum(s * zoom + gap for s in sizes) + 18
    cell_w = icon_span
    header_h = 26
    row_label_h = 26
    card_h = 32 * zoom + 58
    width = pad * 2 + cell_w * len(states)
    height = pad * 2 + header_h + (row_label_h + card_h) * len(BUCKET_ORDER) + 10

    sheet = Image.new("RGB", (width, height), (245, 246, 249))
    draw = ImageDraw.Draw(sheet)
    try:
        title_font = ImageFont.truetype(iconart.FONT_CJK_BOLD, 16)
        font = ImageFont.truetype(iconart.FONT_CJK_BOLD, 13)
        small = ImageFont.truetype(iconart.FONT_CJK, 11)
    except OSError:
        title_font = font = small = ImageFont.load_default()

    draw.text((pad, pad - 4), "Codex 额度托盘图标（深色任务栏实际观感，放大 7 倍显示）",
              font=title_font, fill=(28, 32, 38))
    y = pad + header_h
    for bucket in BUCKET_ORDER:
        name = "5 小时额度 · 圆角方形" if bucket == "five_hour" else "每周额度 · 圆形"
        draw.text((pad, y), name, font=font, fill=(60, 66, 76))
        y += row_label_h
        for index, (remaining, caption) in enumerate(states):
            x = pad + index * cell_w
            draw.rounded_rectangle([x, y, x + cell_w - 12, y + card_h], radius=7,
                                   fill=(32, 32, 34))
            cursor = x + 12
            for size in sizes:
                image = iconart.render(bucket, remaining, size)
                big = image.resize((size * zoom, size * zoom), Image.NEAREST)
                draw.rectangle([cursor, y + 16, cursor + size * zoom,
                                y + 16 + size * zoom], fill=(32, 32, 34))
                sheet.paste(big, (cursor, y + 16), big)
                draw.text((cursor, y + 16 + size * zoom + 4), f"{size}px",
                          font=small, fill=(150, 156, 166))
                cursor += size * zoom + gap
            draw.text((x + 12, y + card_h - 22), caption, font=small, fill=(198, 204, 214))
        y += card_h + 8
    sheet.save(path)


if __name__ == "__main__":
    try:
        sys.exit(run())
    except SystemExit:
        raise
    except Exception:
        applog.setup().exception("未捕获异常")
        traceback.print_exc()
        sys.exit(1)
