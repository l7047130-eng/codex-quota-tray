# -*- coding: utf-8 -*-
"""额度读取：通过 Codex 自带的只读 app-server 接口 account/rateLimits/read。

设计要点
  * 只读：仅调用 account/rateLimits/read，不发起任何推理请求；
  * 不碰凭据：不读取、不解析、不落盘 auth.json 或任何令牌；
  * 子进程：codex.exe app-server 常驻一个子进程，退出时连同子进程树一起回收；
  * 失败即失效：任何异常都向上抛，由界面层显示「不可用」，绝不保留旧值。
"""
from __future__ import annotations

import json
import os
import queue
import shutil
import subprocess
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Optional

CREATE_NO_WINDOW = 0x08000000

FIVE_HOUR_MINUTES = 300
WEEKLY_MINUTES = 10080

# 只接受**能确认**是 300 分钟 / 10080 分钟的窗口。
#
#   * 接口直接给出 windowDurationMins 时，必须**精确等于** 300 / 10080；
#   * 只有当分钟数是由 limit_window_seconds 换算出来时，才允许 ±1 分钟误差，
#     因为秒→分钟的四舍五入可能差 1 分钟。
#
# 不再按「<=24 小时算 5 小时、>24 小时算每周」二分，也不用 ±5% 这种宽口径：
# 1440 / 43200 / 315 / 9600 这类时长一律判为未知，界面显示「—」，绝不拿别的窗口顶替。
WINDOW_TOLERANCE_MINUTES = 1


def window_duration(raw: dict) -> tuple[Optional[int], bool]:
    """取窗口时长，返回 (分钟, 是否由秒字段换算而来)。取不到返回 (None, False)。"""
    value = raw.get("windowDurationMins")
    if value is not None:
        try:
            return int(value), False
        except (TypeError, ValueError):
            return None, False
    seconds = raw.get("limit_window_seconds")
    if seconds is not None:
        try:
            return int(round(float(seconds) / 60.0)), True
        except (TypeError, ValueError):
            return None, False
    return None, False


def window_minutes_of(raw: dict) -> Optional[int]:
    """取一个额度窗口的时长（分钟）。取不到返回 None。"""
    return window_duration(raw)[0]


def _matches(minutes: int, target: int, converted: bool) -> bool:
    """精确匹配；只有秒→分钟换算出来的值才给 ±1 分钟余量。"""
    if converted:
        return abs(minutes - target) <= WINDOW_TOLERANCE_MINUTES
    return minutes == target


def split_windows(snapshot: dict) -> tuple[Optional[dict], Optional[dict]]:
    """按窗口时长识别 5 小时与每周额度。

    只认**能确认是 300 分钟 / 10080 分钟**的窗口（windowDurationMins 精确相等；
    由秒换算来的分钟允许 ±1）。时长缺失、或时长对不上（1440、43200、315、9600 …）
    一律忽略，那一侧返回 None，界面显示「—」。

    特别注意：不按 primary / secondary 的位置兜底。位置只是顺序，
    不代表语义，猜错就会把别的额度当作 5 小时或每周显示。

    返回 (五小时原始窗口, 每周原始窗口)。
    """
    five: Optional[dict] = None
    weekly: Optional[dict] = None
    five_gap: Optional[int] = None
    weekly_gap: Optional[int] = None

    for key in ("primary", "secondary"):
        raw = snapshot.get(key)
        if not isinstance(raw, dict) or raw.get("usedPercent") is None:
            continue
        minutes, converted = window_duration(raw)
        if minutes is None:
            continue
        if _matches(minutes, FIVE_HOUR_MINUTES, converted):
            gap = abs(minutes - FIVE_HOUR_MINUTES)
            if five_gap is None or gap < five_gap:
                five, five_gap = raw, gap
        elif _matches(minutes, WEEKLY_MINUTES, converted):
            gap = abs(minutes - WEEKLY_MINUTES)
            if weekly_gap is None or gap < weekly_gap:
                weekly, weekly_gap = raw, gap
    return five, weekly


class RateLimitError(Exception):
    """额度读取失败。"""


@dataclass(frozen=True)
class QuotaWindow:
    used_percent: float
    window_minutes: Optional[int]
    resets_at: Optional[int]  # epoch 秒

    @property
    def remaining_percent(self) -> int:
        value = 100.0 - float(self.used_percent)
        return int(max(0, min(100, round(value))))


@dataclass(frozen=True)
class QuotaSnapshot:
    five_hour: Optional[QuotaWindow]
    weekly: Optional[QuotaWindow]
    fetched_at: datetime
    source: str
    plan_type: Optional[str] = None

    def window(self, bucket: str) -> Optional[QuotaWindow]:
        return self.weekly if bucket == "weekly" else self.five_hour


# ------------------------------------------------------------------ 可执行文件定位
def _codex_home_candidates() -> list[str]:
    local = os.environ.get("LOCALAPPDATA") or os.path.expanduser("~\\AppData\\Local")
    roaming = os.environ.get("APPDATA") or os.path.expanduser("~\\AppData\\Roaming")
    out = []

    bin_root = os.path.join(local, "OpenAI", "Codex", "bin")
    if os.path.isdir(bin_root):
        found = []
        for name in os.listdir(bin_root):
            exe = os.path.join(bin_root, name, "codex.exe")
            if os.path.isfile(exe):
                found.append(exe)
        found.sort(key=lambda p: os.path.getmtime(p), reverse=True)
        out.extend(found)

    npm_exe = os.path.join(
        roaming, "npm", "node_modules", "@openai", "codex", "node_modules",
        "@openai", "codex-win32-x64", "vendor", "x86_64-pc-windows-msvc", "bin", "codex.exe",
    )
    if os.path.isfile(npm_exe):
        out.append(npm_exe)

    npm_exe_arm = npm_exe.replace("codex-win32-x64", "codex-win32-arm64").replace(
        "x86_64-pc-windows-msvc", "aarch64-pc-windows-msvc"
    )
    if os.path.isfile(npm_exe_arm):
        out.append(npm_exe_arm)

    which = shutil.which("codex")
    if which:
        base = os.path.dirname(which)
        for sub in ("node_modules/@openai/codex/node_modules/@openai/codex-win32-x64/"
                    "vendor/x86_64-pc-windows-msvc/bin/codex.exe",):
            cand = os.path.join(base, *sub.split("/"))
            if os.path.isfile(cand):
                out.append(cand)
    return out


def find_codex_exe() -> Optional[str]:
    for cand in _codex_home_candidates():
        if os.path.isfile(cand):
            return cand
    return None


# ------------------------------------------------------------------ 解析
def _to_epoch_seconds(value: Any) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool):
        return None
    if isinstance(value, (int, float)):
        number = float(value)
        if number <= 0:
            return None
        if number > 1e11:  # 毫秒
            number /= 1000.0
        return int(number)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        if text.isdigit():
            return _to_epoch_seconds(int(text))
        try:
            parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return int(parsed.timestamp())
    return None


def _window_from_raw(raw: Any) -> Optional[QuotaWindow]:
    if not isinstance(raw, dict):
        return None
    used = raw.get("usedPercent")
    if used is None:
        used = raw.get("used_percent")
    if used is None:
        return None
    try:
        used_value = float(used)
    except (TypeError, ValueError):
        return None
    minutes = raw.get("windowDurationMins")
    if minutes is None:
        seconds = raw.get("limit_window_seconds")
        if seconds is not None:
            try:
                minutes = int(round(float(seconds) / 60.0))
            except (TypeError, ValueError):
                minutes = None
    try:
        minutes_value = int(minutes) if minutes is not None else None
    except (TypeError, ValueError):
        minutes_value = None
    resets = raw.get("resetsAt")
    if resets is None:
        resets = raw.get("reset_at")
    return QuotaWindow(
        used_percent=used_value,
        window_minutes=minutes_value,
        resets_at=_to_epoch_seconds(resets),
    )


def parse_rate_limits_response(result: Any) -> QuotaSnapshot:
    """解析 account/rateLimits/read 的返回。

    只认能确认属于 Codex 的数据来源：

      1. ``rateLimitsByLimitId.codex`` —— 明确标了 codex 的那一桶；
      2. 旧版 ``rateLimits`` —— 同一接口里 Codex 自己的配额字段。

    **rateLimitsByLimitId 里没有 codex 桶时，绝不拿别的桶顶替。**
    那些桶属于别的额度体系（别的模型/别的套餐），拿来当 Codex 额度会显示错误数字。
    这种情况下直接判为不可用，并把实际有哪些桶写进错误信息，方便排查。
    """
    if not isinstance(result, dict):
        raise RateLimitError("接口返回格式无法识别")

    source = ""
    snapshot: Optional[dict] = None

    by_id = result.get("rateLimitsByLimitId")
    if isinstance(by_id, dict):
        candidate = by_id.get("codex")
        if isinstance(candidate, dict):
            snapshot = candidate
            source = "rateLimitsByLimitId.codex"

    if snapshot is None:
        legacy = result.get("rateLimits")
        if isinstance(legacy, dict):
            snapshot = legacy
            source = "rateLimits"

    if snapshot is None:
        if isinstance(by_id, dict) and by_id:
            buckets = "、".join(sorted(str(key) for key in by_id))
            raise RateLimitError(
                f"返回里没有 codex 额度桶（只有 {buckets}），不拿别的桶当 Codex 额度")
        raise RateLimitError("返回中没有额度字段")

    raw_five, raw_weekly = split_windows(snapshot)
    five = _window_from_raw(raw_five) if raw_five else None
    weekly = _window_from_raw(raw_weekly) if raw_weekly else None
    if five is None and weekly is None:
        found = []
        for key in ("primary", "secondary"):
            raw = snapshot.get(key)
            if not isinstance(raw, dict) or raw.get("usedPercent") is None:
                continue
            minutes = window_minutes_of(raw)
            found.append(f"{key}={minutes}min" if minutes is not None else f"{key}=时长未知")
        detail = "、".join(found) if found else "没有可用窗口"
        raise RateLimitError(
            f"没有能确认的 5 小时(300min) / 每周(10080min) 窗口（{detail}）")

    plan = snapshot.get("planType")
    return QuotaSnapshot(
        five_hour=five,
        weekly=weekly,
        fetched_at=datetime.now(),
        source=source,
        plan_type=plan if isinstance(plan, str) else None,
    )


# ------------------------------------------------------------------ RPC 客户端
class _AppServerProcess:
    """一个 codex app-server 子进程 + 逐行 JSON 读取线程。"""

    def __init__(self, exe: str, log, job=None) -> None:
        self.exe = exe
        self.log = log
        self._job = job
        self._queue: "queue.Queue[Any]" = queue.Queue()
        self._ids = 0
        self._closed = False
        self.proc = subprocess.Popen(
            [exe, "app-server", "--stdio"],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            bufsize=0,
            creationflags=CREATE_NO_WINDOW,
        )
        if job is not None and getattr(job, "valid", False):
            job.assign(self.proc.pid)
        self._reader = threading.Thread(
            target=self._read_loop, name="app-server-reader", daemon=True
        )
        self._reader.start()

    # -- 读取
    def _read_loop(self) -> None:
        stream = self.proc.stdout
        if stream is None:
            self._queue.put(None)
            return
        while True:
            try:
                line = stream.readline()
            except (ValueError, OSError):
                break
            if not line:
                break
            text = line.decode("utf-8", "replace").strip()
            if not text:
                continue
            try:
                message = json.loads(text)
            except json.JSONDecodeError:
                continue
            self._queue.put(message)
        self._queue.put(None)

    # -- 发送
    def send(self, payload: dict) -> None:
        if self._closed or self.proc.stdin is None:
            raise RateLimitError("子进程已关闭")
        data = (json.dumps(payload, ensure_ascii=False) + "\n").encode("utf-8")
        try:
            self.proc.stdin.write(data)
            self.proc.stdin.flush()
        except (BrokenPipeError, OSError) as exc:
            raise RateLimitError(f"写入子进程失败: {exc}") from exc

    def request(self, method: str, params: Any, timeout: float) -> dict:
        self._ids += 1
        request_id = self._ids
        self.send({"jsonrpc": "2.0", "id": request_id, "method": method, "params": params})
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise RateLimitError(f"{method} 超时")
            try:
                message = self._queue.get(timeout=remaining)
            except queue.Empty:
                raise RateLimitError(f"{method} 超时") from None
            if message is None:
                code = self.proc.poll()
                raise RateLimitError(f"子进程提前退出（code={code}）")
            if not isinstance(message, dict):
                continue
            if message.get("id") != request_id:
                continue  # 服务端通知，忽略
            if "error" in message:
                err = message.get("error") or {}
                text = err.get("message") if isinstance(err, dict) else str(err)
                raise RateLimitError(text or "接口返回错误")
            result = message.get("result")
            if not isinstance(result, dict):
                raise RateLimitError("接口返回为空")
            return result

    def alive(self) -> bool:
        return (not self._closed) and self.proc.poll() is None

    def close(self) -> None:
        if self._closed:
            return
        self._closed = True
        proc = self.proc
        try:
            if proc.stdin is not None:
                proc.stdin.close()
        except OSError:
            pass
        _kill_process_tree(proc)


def _kill_process_tree(proc: subprocess.Popen) -> None:
    """先杀子进程树（app-server 可能带出 git 等孙进程），再兜底杀本体。"""
    try:
        import psutil

        try:
            parent = psutil.Process(proc.pid)
            for child in parent.children(recursive=True):
                try:
                    child.kill()
                except Exception:
                    pass
        except Exception:
            pass
    except ImportError:
        pass
    try:
        proc.kill()
    except Exception:
        pass
    try:
        proc.wait(timeout=5)
    except Exception:
        pass


class RateLimitReader:
    """对外入口：读一次额度。内部维护常驻子进程，失败自动重建。"""

    def __init__(self, exe: Optional[str] = None, log=None, job=None,
                 startup_timeout: float = 25.0, query_timeout: float = 20.0) -> None:
        self.exe = exe or find_codex_exe()
        self.log = log
        self.job = job
        self.startup_timeout = startup_timeout
        self.query_timeout = query_timeout
        self._client: Optional[_AppServerProcess] = None
        self._lock = threading.Lock()

    def _ensure_client(self) -> _AppServerProcess:
        if self._client is not None and self._client.alive():
            return self._client
        if self._client is not None:
            self._client.close()
            self._client = None
        if not self.exe:
            raise RateLimitError("未找到 codex.exe")
        if self.log:
            self.log.info("启动 app-server 子进程: %s", self.exe)
        client = _AppServerProcess(self.exe, self.log, self.job)
        try:
            client.request(
                "initialize",
                {"clientInfo": {"name": "codex-quota-tray", "version": "1.0.0"}},
                timeout=self.startup_timeout,
            )
        except Exception:
            client.close()
            raise
        self._client = client
        return client

    def read(self) -> QuotaSnapshot:
        with self._lock:
            try:
                client = self._ensure_client()
                result = client.request("account/rateLimits/read", None,
                                        timeout=self.query_timeout)
            except RateLimitError:
                self._reset()
                raise
            except Exception as exc:
                self._reset()
                raise RateLimitError(str(exc)) from exc
            return parse_rate_limits_response(result)

    def _reset(self) -> None:
        if self._client is not None:
            self._client.close()
            self._client = None

    def close(self) -> None:
        with self._lock:
            self._reset()
