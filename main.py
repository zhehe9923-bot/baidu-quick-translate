"""划词翻译助手（百度翻译）—— 程序入口。

直接双击运行（或 pythonw main.py）会打开界面；
也可以用命令行方式使用：

    python main.py --translate "hello world"
    python main.py --translate "hello world" --to zh --from en
    python main.py --selftest
"""

from __future__ import annotations

import argparse
import ctypes
import logging
import os
import sys

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
if BASE_DIR not in sys.path:
    sys.path.insert(0, BASE_DIR)

import baidu_api  # noqa: E402
import config as config_mod  # noqa: E402
import updater  # noqa: E402
import usage  # noqa: E402
import winutil  # noqa: E402
from version import APP_VERSION  # noqa: E402

LOG_FORMAT = "%(asctime)s [%(levelname)s] %(name)s: %(message)s"


# --------------------------------------------------------------------------- 日志
def _ensure_std_streams() -> None:
    """pythonw / PyInstaller --windowed 下 sys.stdout、sys.stderr 为 None。

    这里兜底到日志文件，避免命令行模式下 print() 直接抛异常。
    """
    if sys.stdout is not None and sys.stderr is not None:
        return
    try:
        stream = open(config_mod.config_dir() / "cli.log", "a", encoding="utf-8", buffering=1)
    except OSError:
        stream = open(os.devnull, "w", encoding="utf-8")
    if sys.stdout is None:
        sys.stdout = stream
    if sys.stderr is None:
        sys.stderr = stream


def setup_logging(debug: bool) -> None:
    handlers: list[logging.Handler] = []
    if sys.stderr is not None:
        handlers.append(logging.StreamHandler())
    try:
        handlers.append(logging.FileHandler(config_mod.config_dir() / "app.log", encoding="utf-8"))
    except OSError:
        pass
    if not handlers:
        handlers = [logging.NullHandler()]
    logging.basicConfig(
        level=logging.DEBUG if debug else logging.INFO,
        format=LOG_FORMAT,
        handlers=handlers,
    )


# --------------------------------------------------------------------------- 命令行翻译
def run_cli_translate(
    text: str, from_lang: str, to_lang: str, service: str | None = None, domain: str | None = None
) -> int:
    cfg = config_mod.load()
    if not (cfg.get("appid") and cfg.get("secret")):
        print("错误：还没有配置 APPID / 密钥。")
        print(f"请先运行图形界面（python {os.path.basename(__file__)}）并在「设置」里填写。")
        print(f"配置文件：{config_mod.config_path()}")
        return 2
    service = baidu_api.normalize_service(service or cfg.get("service", baidu_api.DEFAULT_SERVICE))
    domain = baidu_api.normalize_domain(domain or cfg.get("domain", baidu_api.DEFAULT_DOMAIN))
    try:
        translator = baidu_api.BaiduTranslator(
            cfg["appid"], cfg["secret"], cfg.get("request_timeout", 10),
            service=service, domain=domain,
        )
        if from_lang == "auto" and to_lang == "auto":
            from_lang, to_lang = baidu_api.detect_direction(text, cfg.get("target_mode", "auto"))
        result = translator.translate(text, from_lang, to_lang)
    except baidu_api.BaiduTranslateError as exc:
        print(f"翻译失败：{exc}")
        return 1
    label = f"[{result.service_name}] {result.lang_label}"
    if result.domain:
        label += f" · {result.domain_name}"
    print(label)
    print(result.source)
    print("-" * 40)
    print(result.target)
    # 记一笔本机用量（和图形界面共用同一份统计）
    try:
        used, _count = usage.record(result.service, len(result.source))
        quota = int(cfg.get(f"quota_{result.service}", config_mod.DEFAULT_QUOTAS.get(result.service, 0)))
        if quota > 0:
            print(f"\n本月已用：{usage.format_number(used)} / {usage.format_number(quota)} 字符"
                  f"（本机统计，约剩 {usage.format_number(max(0, quota - used))}）")
        else:
            print(f"\n本月已用：{usage.format_number(used)} 字符（本机统计）")
    except Exception:  # 记账失败不影响翻译结果
        pass
    return 0


# --------------------------------------------------------------------------- 自检
class _Check:
    def __init__(self) -> None:
        self.passed = 0
        self.failed = 0

    def __call__(self, name: str, func) -> None:
        try:
            func()
        except AssertionError as exc:
            self.failed += 1
            print(f"[失败] {name}：{exc}")
        except Exception as exc:  # noqa: BLE001 - 自检要显示所有问题
            self.failed += 1
            print(f"[错误] {name}：{type(exc).__name__}: {exc}")
        else:
            self.passed += 1
            print(f"[通过] {name}")


def run_selftest() -> int:
    """不联网、不弹窗的本地自检。"""
    check = _Check()

    def test_sign():
        # 百度翻译开放平台文档中的示例
        sign = baidu_api.make_sign("2015063000000001", "apple", "1435660288", "12345678")
        assert sign == "f89f9594663708c1605f3d736d01d2d4", f"签名不一致：{sign}"

    def test_hotkey():
        import hotkey as hotkey_mod

        mods, vk, display = hotkey_mod.parse_hotkey("ctrl+alt+t")
        assert mods == (hotkey_mod.MOD_CONTROL | hotkey_mod.MOD_ALT), mods
        assert vk == ord("T"), vk
        assert display == "Ctrl + Alt + T", display
        mods, vk, _ = hotkey_mod.parse_hotkey("Ctrl + Shift + F2")
        assert mods == (hotkey_mod.MOD_CONTROL | hotkey_mod.MOD_SHIFT)
        assert vk == 0x71, vk
        for bad in ("", "t", "ctrl", "ctrl+alt", "ctrl+alt+unknownkey"):
            try:
                hotkey_mod.parse_hotkey(bad)
            except ValueError:
                pass
            else:
                raise AssertionError(f"{bad!r} 本应报错")

    def test_chunk():
        short = "hello 世界"
        assert baidu_api._split_chunks(short) == [short]
        long_text = ("这是一个很长的句子，用来测试分段逻辑。" * 400) + "\n\n" + ("English line. " * 300)
        chunks = baidu_api._split_chunks(long_text)
        assert len(chunks) > 1, "长文本应该被分成多段"
        assert "".join(chunks) == long_text, "分段后拼接必须与原文一致"
        assert all(len(c.encode("utf-8")) <= baidu_api.MAX_CHUNK_BYTES for c in chunks), "存在超长分段"

    def test_direction():
        assert baidu_api.detect_direction("你好，世界") == ("zh", "en")
        assert baidu_api.detect_direction("hello world") == ("auto", "zh")
        assert baidu_api.detect_direction("hello", "jp") == ("auto", "jp")
        assert baidu_api.detect_direction("你好", "zh") == ("zh", "en")

    def test_services():
        assert [code for code, _ in baidu_api.SERVICES] == ["llm", "general", "domain"]
        assert baidu_api.SERVICE_NAME["llm"] == "大模型文本翻译"
        assert baidu_api.DOMAIN_NAME["senimed"] == "生物医药"
        assert baidu_api.DOMAIN_NAME["wiki"] == "人文社科（仅中→英）"
        assert len(baidu_api.DOMAINS) == 11, len(baidu_api.DOMAINS)
        assert baidu_api.normalize_service("unknown") == baidu_api.DEFAULT_SERVICE
        assert baidu_api.normalize_domain("unknown") == baidu_api.DEFAULT_DOMAIN

    def test_request_builders():
        import json
        import urllib.parse

        # 大模型文本翻译：JSON + Bearer 密钥
        llm = baidu_api.BaiduTranslator("appid123", "secret-abc", service="llm")
        url, data, headers = llm._build_request("hello", "en", "zh")
        assert url == baidu_api.LLM_URL, url
        assert headers["Authorization"] == "Bearer secret-abc", headers
        assert "application/json" in headers["Content-Type"]
        body = json.loads(data.decode("utf-8"))
        assert body == {"appid": "appid123", "q": "hello", "from": "en", "to": "zh"}, body

        # 通用文本翻译：md5(appid+q+salt+密钥)
        general = baidu_api.BaiduTranslator("appid123", "secret-abc", service="general")
        url, data, headers = general._build_request("hello", "en", "zh")
        assert url == baidu_api.GENERAL_URL, url
        form = urllib.parse.parse_qs(data.decode("utf-8"))
        salt = form["salt"][0]
        assert form["sign"][0] == baidu_api.make_sign("appid123", "hello", salt, "secret-abc")
        assert "domain" not in form

        # 领域文本翻译：签名包含 domain
        domain_translator = baidu_api.BaiduTranslator(
            "appid123", "secret-abc", service="domain", domain="senimed"
        )
        url, data, headers = domain_translator._build_request("hello", "en", "zh")
        assert url == baidu_api.DOMAIN_URL, url
        form = urllib.parse.parse_qs(data.decode("utf-8"))
        salt = form["salt"][0]
        assert form["domain"][0] == "senimed"
        assert form["sign"][0] == baidu_api.make_domain_sign(
            "appid123", "hello", salt, "senimed", "secret-abc"
        )
        # 领域翻译只支持中英互译，auto 会被解析成具体语种
        assert domain_translator._prepare_languages("auto", "zh") == ("en", "zh")
        assert domain_translator._prepare_languages("auto", "en") == ("zh", "en")
        try:
            domain_translator._prepare_languages("auto", "jp")
        except baidu_api.BaiduTranslateError:
            pass
        else:
            raise AssertionError("领域翻译不支持日语方向，本应报错")

    def test_usage():
        import json as json_mod
        import shutil
        from pathlib import Path

        original = config_mod.config_dir
        base = Path(BASE_DIR) / "_selftest_tmp_usage"
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        config_mod.config_dir = lambda: base  # type: ignore[assignment]
        try:
            usage.reset()
            assert usage.month_usage("general") == (0, 0)
            assert usage.record("general", 120) == (120, 1)
            assert usage.record("general", 30) == (150, 2)
            assert usage.record("llm", 6) == (6, 1)
            assert usage.month_usage("general") == (150, 2)
            assert usage.all_usage() == {"general": 150, "llm": 6}
            assert usage.estimate_remaining("general", 1000) == 850
            assert usage.estimate_remaining("general", 0) is None
            assert usage.format_number(1234567) == "1,234,567"
            # 跨月自动清零
            usage.usage_path().write_text(
                json_mod.dumps({"month": "2000-01", "services": {"general": 99}}),
                encoding="utf-8",
            )
            assert usage.month_usage("general") == (0, 0)
            usage.reset()
            assert usage.all_usage() == {}
            assert usage.month_usage("domain") == (0, 0)
        finally:
            config_mod.config_dir = original  # type: ignore[assignment]
            shutil.rmtree(base, ignore_errors=True)

    def test_config():
        import shutil
        from pathlib import Path

        original = config_mod.config_dir
        # 放在程序目录下，避免系统临时目录在某些受限环境下不可写
        base = Path(BASE_DIR) / "_selftest_tmp"
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        config_mod.config_dir = lambda: base  # type: ignore[assignment]
        try:
            cfg = config_mod.load()
            assert cfg["hotkey"] == "ctrl+alt+t"
            assert cfg["minimize_to_tray"] is True
            assert cfg["start_minimized"] is False
            assert cfg["service"] == baidu_api.DEFAULT_SERVICE
            assert cfg["domain"] == baidu_api.DEFAULT_DOMAIN
            assert cfg["quota_llm"] == config_mod.DEFAULT_QUOTAS["llm"]
            assert cfg["quota_general"] == config_mod.DEFAULT_QUOTAS["general"]
            assert cfg["quota_domain"] == config_mod.DEFAULT_QUOTAS["domain"]
            cfg["appid"] = "123"
            cfg["opacity"] = 3
            cfg["service"] = "llm"
            cfg["domain"] = "finance"
            cfg["quota_general"] = -5
            config_mod.save(cfg)
            loaded = config_mod.load()
            assert loaded["appid"] == "123"
            assert 0.4 <= loaded["opacity"] <= 1.0, loaded["opacity"]
            assert loaded["target_mode"] == "auto"
            assert loaded["service"] == "llm"
            assert loaded["domain"] == "finance"
            assert loaded["quota_general"] == 0, loaded["quota_general"]
            # 非法服务/领域应退回默认值
            loaded["service"] = "no-such"
            loaded["domain"] = "no-such"
            fixed = config_mod.normalize(loaded)
            assert fixed["service"] == baidu_api.DEFAULT_SERVICE
            assert fixed["domain"] == baidu_api.DEFAULT_DOMAIN
        finally:
            config_mod.config_dir = original  # type: ignore[assignment]
            shutil.rmtree(base, ignore_errors=True)

    def test_updater():
        import json as json_mod
        import shutil
        import zipfile
        from pathlib import Path

        # 版本号比较
        assert updater.parse_version("v1.2.3") == (1, 2, 3)
        assert updater.parse_version("1.2.3-beta") == (1, 2, 3)
        assert updater.parse_version("1.2") == (1, 2)
        assert updater.is_newer("1.2.0", "1.1.9")
        assert updater.is_newer("v2.0", "1.9.9")
        assert not updater.is_newer("1.2.0", "1.2.0")
        assert not updater.is_newer("1.1.0", "1.2.0")
        for bad in ("", "abc", "v"):
            try:
                updater.parse_version(bad)
            except updater.UpdateError:
                pass
            else:
                raise AssertionError(f"{bad!r} 应被视为非法版本号")

        # version.json 校验
        ok_payload = json_mod.dumps({
            "version": "v9.9.9",
            "url": "https://example.com/app.zip",
            "sha256": "a" * 64,
            "notes": "测试",
            "page": "https://example.com",
        }).encode()
        info = updater.parse_feed(ok_payload)
        assert info.version == "v9.9.9" and info.sha256 == "a" * 64
        bad_cases = [
            b"not json",
            json_mod.dumps({"version": "1.0", "url": "ftp://x", "sha256": "a" * 64}).encode(),
            json_mod.dumps({"version": "1.0", "url": "https://x"}).encode(),
            json_mod.dumps({"version": "1.0", "url": "https://x", "sha256": "xyz"}).encode(),
        ]
        for bad in bad_cases:
            try:
                updater.parse_feed(bad)
            except updater.UpdateError:
                pass
            else:
                raise AssertionError("非法 version.json 应被拒绝")

        # zip 解压：路径穿越必须被拒绝；正常包能解出 exe
        base = Path(BASE_DIR) / "_selftest_tmp_updater"
        shutil.rmtree(base, ignore_errors=True)
        base.mkdir(parents=True, exist_ok=True)
        try:
            evil_zip = base / "evil.zip"
            with zipfile.ZipFile(evil_zip, "w") as zf:
                zf.writestr("../evil.txt", "boom")
            try:
                updater.extract_update(evil_zip, base / "x")
            except updater.UpdateError:
                pass
            else:
                raise AssertionError("zip 路径穿越应被拒绝")

            good_zip = base / "good.zip"
            with zipfile.ZipFile(good_zip, "w") as zf:
                zf.writestr("划词翻译/划词翻译.exe", "dummy exe")
                zf.writestr("划词翻译/_internal/data.txt", "hello")
            extracted = updater.extract_update(good_zip, base / "stage")
            payload = updater.find_payload_dir(extracted, "划词翻译.exe")
            assert (payload / "划词翻译.exe").is_file()
            assert (payload / "_internal" / "data.txt").read_text(encoding="utf-8") == "hello"

            # 更新脚本内容：包含等待 PID、备份、启动新版本等关键步骤
            script = updater.write_update_script(
                base / "payload", base / "app", "划词翻译.exe", 12345, base
            )
            text = script.read_text(encoding="utf-8")
            for keyword in ("Rename-Item", "Copy-Item", "Start-Process", "$App", "$Payload"):
                assert keyword in text, f"更新脚本缺少 {keyword}"
        finally:
            shutil.rmtree(base, ignore_errors=True)

        # 配置里新增的更新字段
        cfg = config_mod.normalize({})
        assert cfg["update_url"].startswith("https://github.com/")
        assert cfg["update_auto_check"] is True
        assert cfg["update_last_check"] == 0.0
        assert cfg["update_mirror"] == ""

    def test_tray():
        import tray as tray_mod

        # NOTIFYICONDATAW 的结构大小必须和 Windows 一致，否则托盘图标会创建失败
        size = ctypes.sizeof(tray_mod.NOTIFYICONDATAW)
        assert size == 976, f"NOTIFYICONDATAW 大小异常：{size}（应为 976）"
        icon = os.path.join(BASE_DIR, "assets", "icon.ico")
        assert os.path.isfile(icon), f"缺少托盘图标文件：{icon}"
        assert hasattr(tray_mod.TrayIcon, "start")
        assert hasattr(tray_mod.TrayIcon, "stop")
        assert hasattr(tray_mod.TrayIcon, "set_tooltip")
        assert hasattr(tray_mod.TrayIcon, "notify")

    def test_clipboard():
        import selection

        marker = "剪贴板自检 ✅ 123"
        original = selection.get_clipboard_text()
        try:
            assert selection.set_clipboard_text(marker), "写入剪贴板失败"
            assert selection.get_clipboard_text() == marker, "读取内容不一致"
        finally:
            if original:
                selection.set_clipboard_text(original)

    check("百度翻译签名算法（官方示例）", test_sign)
    check("三种翻译服务 / 领域代码", test_services)
    check("请求构造（大模型 Bearer / 通用签名 / 领域签名）", test_request_builders)
    check("全局快捷键解析", test_hotkey)
    check("长文本分段（不丢字、不超长）", test_chunk)
    check("翻译方向自动判断", test_direction)
    check("配置读写与校验", test_config)
    check("本机用量统计 / 额度估算", test_usage)
    check("GitHub 更新：版本比较 / 校验 / 解压安全", test_updater)
    check("托盘图标结构 / 资源", test_tray)
    check("剪贴板读写", test_clipboard)

    print("-" * 46)
    print(f"通过 {check.passed} 项，失败 {check.failed} 项")
    return 0 if check.failed == 0 else 1


# --------------------------------------------------------------------------- 图形界面
def run_gui() -> int:
    if not winutil.single_instance("BaiduQuickTranslate.SingleInstance"):
        try:
            import tkinter as tk
            from tkinter import messagebox

            root = tk.Tk()
            root.withdraw()
            messagebox.showinfo(
                config_mod.APP_TITLE,
                "划词翻译已经在运行了。\n\n如果看不到窗口，请查看任务栏或按快捷键使用。",
            )
            root.destroy()
        except Exception:  # noqa: BLE001 - 无界面环境下静默退出
            pass
        return 0

    winutil.enable_dpi_awareness()
    winutil.set_app_user_model_id(config_mod.APP_ID)

    from gui import App

    App().run()
    return 0


# --------------------------------------------------------------------------- 检查更新（命令行）
def run_cli_check_update() -> int:
    cfg = config_mod.load()
    url = str(cfg.get("update_url", "")).strip()
    print(f"当前版本：v{APP_VERSION}")
    print(f"更新源：{url or '(未配置)'}")
    try:
        info = updater.check_for_update(APP_VERSION, url)
    except updater.UpdateError as exc:
        print(f"检查失败：{exc}")
        return 1
    if info is None:
        print("已是最新版本。")
        return 0
    print(f"发现新版本：v{info.version}")
    if info.notes:
        print("-" * 40)
        print(info.notes)
    print("-" * 40)
    print(f"下载地址：{info.url}")
    if info.page:
        print(f"发布页面：{info.page}")
    return 0


# --------------------------------------------------------------------------- 入口
def main(argv: list[str] | None = None) -> int:
    _ensure_std_streams()
    parser = argparse.ArgumentParser(
        prog="划词翻译",
        description="选中文字后按快捷键，用百度翻译翻译（Windows 小工具）。",
    )
    parser.add_argument("--translate", "-t", metavar="TEXT", help="命令行翻译一段文本后退出")
    parser.add_argument("--from", dest="from_lang", default="auto", help="源语言，默认 auto")
    parser.add_argument("--to", dest="to_lang", default="auto", help="目标语言，默认 auto（中↔英）")
    parser.add_argument(
        "--service", choices=["llm", "general", "domain"],
        help="翻译服务：llm=大模型文本翻译，general=通用文本翻译，domain=领域文本翻译（默认读配置）",
    )
    parser.add_argument(
        "--domain", help=f"领域代码（仅 domain 服务）：{'、'.join(code for code, _ in baidu_api.DOMAINS)}",
    )
    parser.add_argument("--check-update", action="store_true", help="检查 GitHub 上是否有新版本后退出")
    parser.add_argument("--selftest", action="store_true", help="运行本地自检后退出")
    parser.add_argument("--debug", action="store_true", help="输出调试日志")
    parser.add_argument("--version", action="version", version=f"%(prog)s {APP_VERSION}")
    args = parser.parse_args(argv)

    setup_logging(args.debug)

    if args.selftest:
        return run_selftest()
    if args.check_update:
        return run_cli_check_update()
    if args.translate:
        return run_cli_translate(
            args.translate, args.from_lang, args.to_lang, args.service, args.domain
        )
    return run_gui()


if __name__ == "__main__":
    raise SystemExit(main())
