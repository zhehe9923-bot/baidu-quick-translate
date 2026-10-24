"""配置管理。

配置文件默认保存在： %APPDATA%\\BaiduQuickTranslate\\config.json
也可以设置环境变量 BAIDU_QT_HOME 指定目录（便于做成便携版）。
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import baidu_api

APP_NAME = "BaiduQuickTranslate"
APP_ID = "BaiduQuickTranslate.Desktop.1"
APP_TITLE = "划词翻译 · 百度翻译"

# 各服务默认的每月免费字符额度（只用于本机估算，实际以百度控制台为准）
DEFAULT_QUOTAS: dict[str, int] = {
    "llm": 1_000_000,
    "general": 1_000_000,
    "domain": 500_000,
}

DEFAULT_CONFIG: dict = {
    # ---- 百度翻译开放平台 ----
    "appid": "",
    "secret": "",
    # ---- 使用方式 ----
    "hotkey": "ctrl+alt+t",
    "target_mode": "auto",          # auto=中英互译；其它值为百度语种代码
    "service": "general",           # llm=大模型文本翻译；general=通用；domain=领域
    "domain": "academic",           # 领域文本翻译的领域代码（见 baidu_api.DOMAINS）
    "quota_llm": DEFAULT_QUOTAS["llm"],          # 每月免费额度（字符），0 = 只显示用量不估算
    "quota_general": DEFAULT_QUOTAS["general"],
    "quota_domain": DEFAULT_QUOTAS["domain"],
    "fallback_clipboard": True,     # 没抓到选中文本时，翻译剪贴板内容
    "auto_copy": True,              # 译文显示后自动写入剪贴板
    "request_timeout": 10,          # 单次请求超时（秒）
    # ---- 弹窗 ----
    "popup_seconds": 0,             # 0 = 不自动关闭
    "opacity": 0.97,
    "show_source": True,
    # ---- 托盘常驻 ----
    "minimize_to_tray": True,       # 关闭主窗口时收进托盘，而不是退出程序
    "start_minimized": False,       # 启动时只显示托盘图标（不弹主窗口）
    # ---- 自动更新（GitHub Releases）----
    "update_url": "https://github.com/zhehe9923-bot/baidu-quick-translate/"
                  "releases/latest/download/version.json",
    "update_auto_check": True,      # 启动时自动检查更新（每 24 小时最多一次）
    "update_last_check": 0.0,       # 上次检查时间戳（time.time()）
    "update_mirror": "",            # 可选下载加速前缀，例如 https://ghproxy.net/
    # ---- 状态 ----
    "first_run_done": False,
}

# 各字段的合法类型，用于读取时做一次简单校验
_TYPES = {
    "appid": str,
    "secret": str,
    "hotkey": str,
    "target_mode": str,
    "service": str,
    "domain": str,
    "quota_llm": int,
    "quota_general": int,
    "quota_domain": int,
    "fallback_clipboard": bool,
    "auto_copy": bool,
    "request_timeout": int,
    "popup_seconds": int,
    "opacity": float,
    "show_source": bool,
    "minimize_to_tray": bool,
    "start_minimized": bool,
    "update_url": str,
    "update_auto_check": bool,
    "update_last_check": float,
    "update_mirror": str,
    "first_run_done": bool,
}


def config_dir() -> Path:
    """返回配置目录（尽量保证目录存在；无权限时也不让程序崩溃）。"""
    override = os.environ.get("BAIDU_QT_HOME", "").strip()
    if override:
        base = Path(override).expanduser()
    else:
        appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
        base = Path(appdata) / APP_NAME
    try:
        base.mkdir(parents=True, exist_ok=True)
    except OSError:
        # 目录不可写时：读取会退回默认配置，保存时再给出友好提示
        pass
    return base


def config_path() -> Path:
    return config_dir() / "config.json"


def _coerce(value, kind):
    """尽量把读到的值转换成期望类型，失败则返回 (False, None)。"""
    try:
        if kind is bool:
            if isinstance(value, bool):
                return True, value
            if isinstance(value, str):
                return True, value.strip().lower() in ("1", "true", "yes", "on")
            return True, bool(value)
        if kind is int:
            if isinstance(value, bool):
                return False, None
            return True, int(value)
        if kind is float:
            if isinstance(value, bool):
                return False, None
            return True, float(value)
        if kind is str:
            return True, str(value)
    except (TypeError, ValueError):
        return False, None
    return False, None


def normalize(raw: dict | None) -> dict:
    """合并默认值，并丢弃类型不合法的字段。"""
    cfg = dict(DEFAULT_CONFIG)
    if isinstance(raw, dict):
        for key, kind in _TYPES.items():
            if key in raw:
                ok, value = _coerce(raw[key], kind)
                if ok:
                    cfg[key] = value
    # 额外的边界修正
    cfg["request_timeout"] = max(3, min(60, cfg["request_timeout"]))
    cfg["popup_seconds"] = max(0, min(600, cfg["popup_seconds"]))
    cfg["opacity"] = max(0.4, min(1.0, cfg["opacity"]))
    if cfg["service"] not in baidu_api.SERVICE_NAME:
        cfg["service"] = baidu_api.DEFAULT_SERVICE
    if cfg["domain"] not in baidu_api.DOMAIN_NAME:
        cfg["domain"] = baidu_api.DEFAULT_DOMAIN
    if cfg["target_mode"] not in ("auto", "zh", "cht", "en", "jp", "kor", "fra", "de",
                                  "spa", "ru", "pt", "it", "ara", "th", "vie"):
        cfg["target_mode"] = "auto"
    for service in DEFAULT_QUOTAS:
        key = f"quota_{service}"
        cfg[key] = max(0, min(1_000_000_000, cfg[key]))
    cfg["update_url"] = cfg["update_url"].strip()
    cfg["update_mirror"] = cfg["update_mirror"].strip()
    cfg["update_last_check"] = max(0.0, float(cfg["update_last_check"]))
    return cfg


def load() -> dict:
    path = config_path()
    raw: dict = {}
    if path.is_file():
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            raw = {}
    return normalize(raw)


def save(cfg: dict) -> None:
    """原子写入配置文件。"""
    path = config_path()
    cfg = normalize(cfg)
    text = json.dumps(cfg, ensure_ascii=False, indent=2) + "\n"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)
