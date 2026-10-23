# -*- coding: utf-8 -*-
"""额度解析单元测试（纯标准库，不依赖 Pillow / psutil）。

重点覆盖：`rateLimitsByLimitId` 里没有 `codex` 桶时，不能把别的桶当成 Codex 额度。

跑法：
    .venv\\Scripts\\python.exe tests\\test_quota_parse.py
"""
from __future__ import annotations

import os
import sys

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, "src"))

import quota  # noqa: E402

NOW = 1_760_000_000


def window(used: float, minutes: int, reset_in: int = 3600) -> dict:
    return {"usedPercent": used, "windowDurationMins": minutes, "resetsAt": NOW + reset_in}


def codex_ok() -> dict:
    return {"primary": window(2, 300), "secondary": window(9, 10080)}


CASES = []


def case(func):
    CASES.append(func)
    return func


@case
def only_other_buckets_must_fail():
    """只有其他桶 —— 既不能显示数字，也不能悄悄回退。"""
    payload = {
        "rateLimitsByLimitId": {
            "gpt-5": {"primary": window(12, 300)},
            "o3-pro": {"primary": window(40, 10080)},
        }
    }
    try:
        quota.parse_rate_limits_response(payload)
    except quota.RateLimitError as exc:
        text = str(exc)
        assert "codex" in text.lower(), f"错误信息应点明缺少 codex 桶，实际：{text}"
        assert "gpt-5" in text or "o3-pro" in text, f"错误信息应列出实际桶名，实际：{text}"
        return f"判为不可用：{text}"
    raise AssertionError("只有其他桶时竟然解析成功了 —— 会把别人的额度当成 Codex 的")


@case
def single_other_bucket_must_fail():
    """只有一个其他桶也不行（旧实现会直接用它）。"""
    payload = {"rateLimitsByLimitId": {"gpt-5": {"primary": window(12, 300)}}}
    try:
        quota.parse_rate_limits_response(payload)
    except quota.RateLimitError as exc:
        return f"判为不可用：{exc}"
    raise AssertionError("单个其他桶被当成 Codex 额度了")


@case
def codex_bucket_is_used():
    payload = {
        "rateLimitsByLimitId": {
            "codex": codex_ok(),
            "gpt-5": {"primary": window(99, 300)},
        }
    }
    snap = quota.parse_rate_limits_response(payload)
    assert snap.source == "rateLimitsByLimitId.codex", snap.source
    assert snap.five_hour is not None and snap.five_hour.remaining_percent == 98
    assert snap.weekly is not None and snap.weekly.remaining_percent == 91
    return f"source={snap.source} 5h=剩{snap.five_hour.remaining_percent}% 周=剩{snap.weekly.remaining_percent}%"


@case
def codex_bucket_without_weekly_does_not_borrow():
    """codex 桶只有 5 小时窗口时，每周必须留空，不能从别的桶借。"""
    payload = {
        "rateLimitsByLimitId": {
            "codex": {"primary": window(2, 300)},
            "gpt-5": {"primary": window(40, 10080)},
        }
    }
    snap = quota.parse_rate_limits_response(payload)
    assert snap.five_hour is not None and snap.five_hour.remaining_percent == 98
    assert snap.weekly is None, "每周额度被别的桶顶替了"
    return "每周留空（界面显示「周 —」）"


@case
def nonstandard_durations_are_not_guessed():
    """1440 分钟（24h）和 43200 分钟（30 天）都不是 300 / 10080，不能当成 5h 和周。"""
    payload = {
        "rateLimitsByLimitId": {
            "codex": {"primary": window(20, 1440), "secondary": window(30, 43200)},
        }
    }
    try:
        quota.parse_rate_limits_response(payload)
    except quota.RateLimitError as exc:
        text = str(exc)
        assert "300" in text and "10080" in text, f"错误信息应点明期望的时长，实际：{text}"
        assert "1440" in text and "43200" in text, f"错误信息应列出实际时长，实际：{text}"
        return f"判为不可用：{text}"
    raise AssertionError("1440/43200 被当成了 5 小时/每周额度")


@case
def only_five_hour_matches_when_other_is_nonstandard():
    """300 + 1440：只认 5 小时，每周留空（不能把 1440 当每周）。"""
    payload = {
        "rateLimitsByLimitId": {
            "codex": {"primary": window(2, 300), "secondary": window(20, 1440)},
        }
    }
    snap = quota.parse_rate_limits_response(payload)
    assert snap.five_hour is not None and snap.five_hour.remaining_percent == 98
    assert snap.weekly is None, "1440 分钟被当成了每周额度"
    return "5h 正常、每周留空"


@case
def missing_duration_is_not_guessed_by_position():
    """时长字段缺失时不能按 primary/secondary 的位置猜。"""
    payload = {
        "rateLimitsByLimitId": {
            "codex": {
                "primary": {"usedPercent": 5},
                "secondary": {"usedPercent": 9},
            },
        }
    }
    try:
        quota.parse_rate_limits_response(payload)
    except quota.RateLimitError as exc:
        return f"判为不可用：{exc}"
    raise AssertionError("时长缺失时按位置猜出了额度")


@case
def tolerance_accepts_near_miss_rounding():
    """±5% 内的换算误差仍然认（避免秒→分钟取整把正常窗口丢掉）。"""
    payload = {
        "rateLimitsByLimitId": {
            "codex": {
                "primary": {"usedPercent": 2, "limit_window_seconds": 300 * 60},
                "secondary": {"usedPercent": 9, "limit_window_seconds": 10080 * 60},
            },
        }
    }
    snap = quota.parse_rate_limits_response(payload)
    assert snap.five_hour is not None and snap.five_hour.window_minutes == 300
    assert snap.weekly is not None and snap.weekly.window_minutes == 10080
    return "秒字段换算后仍正确识别"


@case
def five_minute_over_is_not_five_hour():
    """315 分钟（5 小时 + 15）不得识别为 5 小时。"""
    payload = {"rateLimitsByLimitId": {"codex": {"primary": window(20, 315)}}}
    try:
        quota.parse_rate_limits_response(payload)
    except quota.RateLimitError as exc:
        return f"判为不可用：{exc}"
    raise AssertionError("315 分钟被当成了 5 小时额度")


@case
def wide_weekly_is_not_weekly():
    """9600 分钟（每周 - 480）不得识别为每周。"""
    payload = {"rateLimitsByLimitId": {"codex": {"primary": window(30, 9600)}}}
    try:
        quota.parse_rate_limits_response(payload)
    except quota.RateLimitError as exc:
        return f"判为不可用：{exc}"
    raise AssertionError("9600 分钟被当成了每周额度")


@case
def two_minutes_off_from_seconds_is_rejected():
    """秒→分钟换算的余量只有 ±1 分钟：差 2 分钟就不认。"""
    for seconds in (298 * 60, 10082 * 60):
        payload = {"rateLimitsByLimitId": {"codex": {"primary": {
            "usedPercent": 10, "limit_window_seconds": seconds}}}}
        try:
            quota.parse_rate_limits_response(payload)
        except quota.RateLimitError:
            continue
        raise AssertionError(f"{seconds} 秒（差 2 分钟）被认成了额度窗口")
    return "差 2 分钟判为不可用"


@case
def one_minute_off_from_seconds_is_accepted():
    """秒→分钟换算留了 ±1 分钟：299 / 10081 分钟仍然认（source 仍是 codex）。"""
    payload = {"rateLimitsByLimitId": {"codex": {
        "primary": {"usedPercent": 2, "limit_window_seconds": 299 * 60},
        "secondary": {"usedPercent": 9, "limit_window_seconds": 10081 * 60},
    }}}
    snap = quota.parse_rate_limits_response(payload)
    assert snap.five_hour is not None and snap.five_hour.window_minutes == 299
    assert snap.weekly is not None and snap.weekly.window_minutes == 10081
    return "±1 分钟内仍然识别"


@case
def other_buckets_fall_back_to_legacy_codex_field():
    """没有 codex 桶但存在旧版 rateLimits（同接口里 Codex 自己的字段）→ 用旧版。"""
    payload = {
        "rateLimitsByLimitId": {"gpt-5": {"primary": window(12, 300)}},
        "rateLimits": codex_ok(),
    }
    snap = quota.parse_rate_limits_response(payload)
    assert snap.source == "rateLimits", snap.source
    assert snap.five_hour.remaining_percent == 98
    assert snap.weekly.remaining_percent == 91
    return f"source={snap.source}（没有拿 gpt-5 的数字）"


@case
def legacy_only_still_works():
    snap = quota.parse_rate_limits_response({"rateLimits": codex_ok()})
    assert snap.source == "rateLimits"
    return "旧版字段可用"


@case
def empty_payload_fails():
    for payload in ({}, {"rateLimitsByLimitId": {}}):
        try:
            quota.parse_rate_limits_response(payload)
        except quota.RateLimitError:
            continue
        raise AssertionError(f"空返回竟然解析成功：{payload}")
    return "空返回判为不可用"


@case
def non_dict_fails():
    for payload in (None, [], "oops", 42):
        try:
            quota.parse_rate_limits_response(payload)
        except quota.RateLimitError:
            continue
        raise AssertionError(f"非 dict 返回竟然解析成功：{payload!r}")
    return "非法返回判为不可用"


def main() -> int:
    failed = 0
    print(f"共 {len(CASES)} 个用例\n")
    for func in CASES:
        try:
            detail = func()
        except AssertionError as exc:
            failed += 1
            print(f"FAIL  {func.__name__}\n      {exc}")
        except Exception as exc:  # noqa: BLE001
            failed += 1
            print(f"ERROR {func.__name__}\n      {exc.__class__.__name__}: {exc}")
        else:
            print(f"ok    {func.__name__}  ->  {detail}")
    print()
    if failed:
        print(f"结果：{failed} 个用例失败")
        return 1
    print("结果：全部通过")
    return 0


if __name__ == "__main__":
    sys.exit(main())
