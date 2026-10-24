"""本机用量统计（按自然月、按翻译服务）。

说明：百度翻译开放平台**没有公开的「剩余额度查询」接口**，实际用量只能登录
控制台查看。所以这里统计的是「本程序在本机的调用量」，用来估算还剩多少免费
字符；额度数值可以在设置里按服务配置。

数据保存在配置文件同目录的 usage.json，格式：
    {"month": "2026-10", "services": {"general": 1234}, "requests": {"general": 3}}
跨月会自动清零。
"""

from __future__ import annotations

import json
import threading
import time

import config as config_mod

_lock = threading.Lock()


def usage_path():
    return config_mod.config_dir() / "usage.json"


def current_month() -> str:
    return time.strftime("%Y-%m")


def _clean_services(raw) -> dict[str, int]:
    result: dict[str, int] = {}
    if isinstance(raw, dict):
        for key, value in raw.items():
            if not isinstance(key, str):
                continue
            try:
                number = int(value)
            except (TypeError, ValueError):
                continue
            if number > 0:
                result[key] = number
    return result


def _load_unlocked() -> dict:
    path = usage_path()
    raw = {}
    try:
        if path.is_file():
            raw = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        raw = {}
    month = raw.get("month") if isinstance(raw, dict) else None
    if not isinstance(month, str) or month != current_month():
        # 跨月/文件损坏：从零开始
        return {"month": current_month(), "services": {}, "requests": {}}
    return {
        "month": month,
        "services": _clean_services(raw.get("services")),
        "requests": _clean_services(raw.get("requests")),
    }


def _save_unlocked(data: dict) -> None:
    path = usage_path()
    try:
        text = json.dumps(data, ensure_ascii=False, indent=2) + "\n"
        tmp = path.with_suffix(".json.tmp")
        tmp.write_text(text, encoding="utf-8")
        tmp.replace(path)
    except OSError:
        pass


def load() -> dict:
    """读取本月用量（线程安全）。"""
    with _lock:
        return _load_unlocked()


def record(service: str, chars: int) -> tuple[int, int]:
    """记录一次成功翻译：字符数与请求次数。返回该服务本月累计 (字符, 次数)。"""
    try:
        chars = int(chars)
    except (TypeError, ValueError):
        chars = 0
    if chars < 0:
        chars = 0
    with _lock:
        data = _load_unlocked()
        services = data["services"]
        requests = data["requests"]
        services[service] = services.get(service, 0) + chars
        requests[service] = requests.get(service, 0) + 1
        _save_unlocked(data)
        return services[service], requests[service]


def month_usage(service: str) -> tuple[int, int]:
    """返回本月该服务 (已用字符, 请求次数)。"""
    data = load()
    return data["services"].get(service, 0), data["requests"].get(service, 0)


def all_usage() -> dict[str, int]:
    """返回本月各服务已用字符。"""
    return dict(load()["services"])


def reset() -> None:
    """清零本月统计。"""
    with _lock:
        _save_unlocked({"month": current_month(), "services": {}, "requests": {}})


def estimate_remaining(service: str, quota: int) -> int | None:
    """按额度估算剩余字符；quota <= 0 时返回 None（表示不估算）。"""
    try:
        quota = int(quota)
    except (TypeError, ValueError):
        return None
    if quota <= 0:
        return None
    used, _ = month_usage(service)
    return max(0, quota - used)


def format_number(value: int) -> str:
    """1234567 -> '1,234,567'。"""
    try:
        return f"{int(value):,}"
    except (TypeError, ValueError):
        return str(value)
