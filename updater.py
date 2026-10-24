"""基于 GitHub Releases 的自动更新。

整体流程：
1. 客户端读取「更新源」里的 version.json（发布时作为 Release 资产上传）：
       {"version": "1.3.0",
        "url": "https://github.com/zhehe9923-bot/baidu-quick-translate/releases/latest/download/BaiduQuickTranslate-win64.zip",
        "sha256": "<zip 的 SHA-256>",
        "notes": "更新内容……",
        "page": "https://github.com/zhehe9923-bot/baidu-quick-translate/releases/latest"}
2. 版本比较：新版本才提示。
3. 下载 zip（限制大小）→ 校验 SHA-256 → 解压到配置目录（防 zip 路径穿越）。
4. 生成一个 apply_update.ps1（PowerShell，天然支持中文路径）：等当前进程退出 →
   把旧程序目录改名为 .old → 用新文件覆盖 → 启动新 exe → 删除 .old。
   当前程序随即退出。用 PowerShell 而不是 .cmd，是为了避开批处理代码页/中文路径的坑。

安全约定：只接受 http(s) 地址、必须带 sha256、严格校验解压路径与大小。
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
import time
import urllib.error
import urllib.request
import zipfile
from dataclasses import dataclass
from pathlib import Path

logger = logging.getLogger(__name__)

USER_AGENT = "BaiduQuickTranslate-Updater/1.0"
MAX_DOWNLOAD_BYTES = 300 * 1024 * 1024        # 下载包最大 300 MB
MAX_UNCOMPRESSED_BYTES = 600 * 1024 * 1024   # 解压后最大 600 MB


class UpdateError(Exception):
    """检查/下载/应用更新失败。"""


@dataclass
class UpdateInfo:
    version: str
    url: str
    sha256: str = ""
    notes: str = ""
    page: str = ""


# --------------------------------------------------------------------------- 版本
def parse_version(text: str) -> tuple[int, ...]:
    """"v1.2.3" / "1.2.3-beta" / "1.2" → (1,2,3) 这样的数字元组。"""
    raw = str(text or "").strip().lstrip("vV")
    core = re.split(r"[-+]", raw, maxsplit=1)[0]
    parts: list[int] = []
    for piece in core.split("."):
        match = re.match(r"\d+", piece.strip())
        if not match:
            break
        parts.append(int(match.group(0)))
    if not parts:
        raise UpdateError(f"无法解析版本号：{text!r}")
    return tuple(parts)


def is_newer(latest: str, current: str) -> bool:
    """latest 是否比 current 新（逐段比较，缺的段补 0）。"""
    a = parse_version(latest)
    b = parse_version(current)
    length = max(len(a), len(b))
    a = a + (0,) * (length - len(a))
    b = b + (0,) * (length - len(b))
    return a > b


# --------------------------------------------------------------------------- 更新源
def parse_feed(payload: bytes | str) -> UpdateInfo:
    """解析 version.json，做基本合法性校验。"""
    try:
        if isinstance(payload, bytes):
            payload = payload.decode("utf-8-sig", "replace")
        data = json.loads(payload)
    except (TypeError, ValueError) as exc:
        raise UpdateError("更新源返回的内容不是合法 JSON") from exc
    if not isinstance(data, dict):
        raise UpdateError("更新源格式不正确（应为 JSON 对象）")

    version = str(data.get("version") or "").strip()
    url = str(data.get("url") or "").strip()
    if not version:
        raise UpdateError("更新源缺少 version 字段")
    if not url:
        raise UpdateError("更新源缺少 url 字段（安装包下载地址）")
    if not url.lower().startswith(("https://", "http://")):
        raise UpdateError("更新包的下载地址必须是 http(s) 链接")

    sha256 = str(data.get("sha256") or "").strip().lower()
    if not sha256:
        raise UpdateError("更新源没有提供 sha256，为安全起见已取消更新")
    if not re.fullmatch(r"[0-9a-f]{64}", sha256):
        raise UpdateError("更新源里的 sha256 格式不正确")

    parse_version(version)  # 让版本号格式错误尽早暴露
    return UpdateInfo(
        version=version,
        url=url,
        sha256=sha256,
        notes=str(data.get("notes") or "").strip(),
        page=str(data.get("page") or "").strip(),
    )


def fetch_update_info(feed_url: str, timeout: float = 10.0) -> UpdateInfo:
    feed_url = (feed_url or "").strip()
    if not feed_url or "YOUR_GITHUB" in feed_url:
        raise UpdateError(
            "还没有配置更新源。请先在 GitHub 建仓并发布（见 docs/UPDATE_PLAN.md），"
            "然后把更新源地址填到「检查更新」窗口里。"
        )
    request = urllib.request.Request(
        feed_url,
        headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read(1024 * 1024)
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"更新源返回 HTTP {exc.code}（仓库/发布是否存在？）") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(f"连接更新源失败：{exc}") from exc
    return parse_feed(body)


def check_for_update(current_version: str, feed_url: str, timeout: float = 10.0) -> UpdateInfo | None:
    """有可用新版本则返回 UpdateInfo，否则返回 None。"""
    info = fetch_update_info(feed_url, timeout)
    return info if is_newer(info.version, current_version) else None


# --------------------------------------------------------------------------- 下载/解压
def _sha256_of(path: Path) -> str:
    hasher = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1024 * 1024), b""):
            hasher.update(chunk)
    return hasher.hexdigest()


def download_update(
    info: UpdateInfo,
    target_dir: Path,
    progress=None,
    timeout: float = 60.0,
) -> Path:
    """下载安装包到 target_dir/update.zip，校验 SHA-256 后返回路径。"""
    target_dir = Path(target_dir)
    target_dir.mkdir(parents=True, exist_ok=True)
    zip_path = target_dir / "update.zip"
    request = urllib.request.Request(info.url, headers={"User-Agent": USER_AGENT})
    hasher = hashlib.sha256()
    downloaded = 0
    started = time.time()
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response, open(zip_path, "wb") as fh:
            total = int(response.headers.get("Content-Length") or 0)
            while True:
                chunk = response.read(64 * 1024)
                if not chunk:
                    break
                downloaded += len(chunk)
                if downloaded > MAX_DOWNLOAD_BYTES:
                    raise UpdateError("安装包超过大小限制，已取消下载")
                fh.write(chunk)
                hasher.update(chunk)
                if progress:
                    try:
                        progress(downloaded, total, time.time() - started)
                    except Exception:
                        pass
    except urllib.error.HTTPError as exc:
        raise UpdateError(f"下载安装包失败：HTTP {exc.code}") from exc
    except (urllib.error.URLError, OSError) as exc:
        raise UpdateError(f"下载安装包失败：{exc}") from exc

    digest = hasher.hexdigest()
    if digest != info.sha256:
        try:
            zip_path.unlink()
        except OSError:
            pass
        raise UpdateError(
            "安装包校验失败（SHA-256 不一致），可能下载不完整或被篡改，已丢弃"
        )
    logger.info("更新包下载完成：%s（%s 字节）", zip_path, downloaded)
    return zip_path


def _validate_zip_entries(zf: zipfile.ZipFile) -> None:
    total = 0
    for item in zf.infolist():
        name = item.filename
        normalized = name.replace("\\", "/")
        if (
            normalized.startswith("/")
            or re.match(r"^[A-Za-z]:", normalized)
            or ".." in normalized.split("/")
        ):
            raise UpdateError(f"更新包包含非法路径：{name}")
        total += int(item.file_size or 0)
        if total > MAX_UNCOMPRESSED_BYTES:
            raise UpdateError("更新包解压后超过大小限制，已取消")


def extract_update(zip_path: Path, dest_dir: Path) -> Path:
    """解压更新包到 dest_dir（会先清空），返回解压目录。"""
    dest_dir = Path(dest_dir)
    if dest_dir.exists():
        for child in dest_dir.iterdir():
            if child.is_dir():
                import shutil

                shutil.rmtree(child, ignore_errors=True)
            else:
                try:
                    child.unlink()
                except OSError:
                    pass
    dest_dir.mkdir(parents=True, exist_ok=True)
    try:
        with zipfile.ZipFile(zip_path) as zf:
            _validate_zip_entries(zf)
            zf.extractall(dest_dir)
    except zipfile.BadZipFile as exc:
        raise UpdateError("安装包不是有效的 zip 文件") from exc
    logger.info("更新包已解压到 %s", dest_dir)
    return dest_dir


def find_payload_dir(extracted_dir: Path, exe_name: str) -> Path:
    """在解压目录里找到包含目标 exe 的那一层（兼容 zip 里多套一层文件夹）。"""
    extracted_dir = Path(extracted_dir)
    if (extracted_dir / exe_name).is_file():
        return extracted_dir
    for child in sorted(extracted_dir.iterdir()):
        if child.is_dir() and (child / exe_name).is_file():
            return child
    for child in sorted(extracted_dir.iterdir()):
        if child.is_dir():
            for grandchild in sorted(child.iterdir()):
                if grandchild.is_dir() and (grandchild / exe_name).is_file():
                    return grandchild
    raise UpdateError(f"更新包里没有找到 {exe_name}，可能下载错了文件")


# --------------------------------------------------------------------------- 替换/重启
def app_dir_of(executable: str | None = None) -> Path:
    """当前程序所在目录（frozen 时为 exe 所在目录）。"""
    exe = executable or sys.executable
    return Path(exe).resolve().parent


def exe_name_of(executable: str | None = None) -> str:
    return Path(executable or sys.executable).name


def is_frozen() -> bool:
    return bool(getattr(sys, "frozen", False))


SCRIPT_TEMPLATE = """param([int]$OldPid = 0)

$ErrorActionPreference = 'Stop'

# 由「划词翻译」自动生成：等待旧进程退出 → 备份旧目录 → 覆盖 → 启动新版本
$App = {app}
$Payload = {payload}
$Exe = {exe}
$Old = $App + '.old'
if ($OldPid -eq 0) {{ $OldPid = {pid} }}

function Start-App {{
    param([string]$path)
    try {{
        if (Test-Path -LiteralPath $path) {{ Start-Process -FilePath $path }}
    }} catch {{ }}
}}

# 1) 等旧程序完全退出（最多 120 秒）
$deadline = (Get-Date).AddSeconds(120)
while ((Get-Process -Id $OldPid -ErrorAction SilentlyContinue) -and ((Get-Date) -lt $deadline)) {{
    Start-Sleep -Milliseconds 500
}}

# 2) 把旧目录改名成 .old（文件仍被占用时重试，最多 60 秒）
if (Test-Path -LiteralPath $Old) {{
    Remove-Item -LiteralPath $Old -Recurse -Force -ErrorAction SilentlyContinue
}}
$renamed = $false
$deadline = (Get-Date).AddSeconds(60)
while (-not $renamed) {{
    try {{
        Rename-Item -LiteralPath $App -NewName (Split-Path -Leaf $Old) -ErrorAction Stop
        $renamed = $true
    }} catch {{
        if ((Get-Date) -gt $deadline) {{ break }}
        Start-Sleep -Milliseconds 500
    }}
}}

if (-not $renamed) {{
    # 替换不了（比如没有写权限）：把旧版本重新启动，避免用户没程序可用
    Start-App (Join-Path $App $Exe)
    exit 1
}}

# 3) 覆盖 + 启动新版本；失败则回滚到旧版本
try {{
    Copy-Item -LiteralPath $Payload -Destination $App -Recurse -Force -ErrorAction Stop
    Start-App (Join-Path $App $Exe)
    Remove-Item -LiteralPath $Old -Recurse -Force -ErrorAction SilentlyContinue
}} catch {{
    Remove-Item -LiteralPath $App -Recurse -Force -ErrorAction SilentlyContinue
    try {{
        Rename-Item -LiteralPath $Old -NewName (Split-Path -Leaf $App) -ErrorAction Stop
    }} catch {{ }}
    Start-App (Join-Path $App $Exe)
    exit 1
}}

exit 0
"""


def _ps_quote(value) -> str:
    """PowerShell 单引号字符串转义。"""
    return "'" + str(value).replace("'", "''") + "'"


def write_update_script(payload_dir, app_dir, exe_name, pid, script_dir):
    """生成替换脚本（apply_update.ps1）。"""
    app = Path(app_dir)
    script_path = Path(script_dir) / "apply_update.ps1"
    script_path.parent.mkdir(parents=True, exist_ok=True)
    text = SCRIPT_TEMPLATE.format(
        app=_ps_quote(app),
        payload=_ps_quote(payload_dir),
        exe=_ps_quote(exe_name),
        pid=int(pid),
    )
    # PowerShell 5.1 读取 UTF-8 脚本需要 BOM
    script_path.write_bytes(b"\xef\xbb\xbf" + text.replace("\n", "\r\n").encode("utf-8"))
    return script_path


def powershell_path() -> str:
    """Windows 自带的 Windows PowerShell（5.1）。"""
    import os

    system_root = os.environ.get("SystemRoot", r"C:\Windows")
    candidate = Path(system_root) / "System32" / "WindowsPowerShell" / "v1.0" / "powershell.exe"
    return str(candidate) if candidate.is_file() else "powershell.exe"


def launch_update_script(script_path, pid=None) -> None:
    """以独立进程启动 PowerShell 更新脚本（当前程序随后应退出）。"""
    import os
    import subprocess

    if not is_frozen():
        raise UpdateError("源码运行模式不支持自动替换，请用 git pull 或重新下载新版")
    pid = pid if pid is not None else os.getpid()
    creationflags = 0
    if hasattr(subprocess, "DETACHED_PROCESS"):
        creationflags |= subprocess.DETACHED_PROCESS
    if hasattr(subprocess, "CREATE_NEW_PROCESS_GROUP"):
        creationflags |= subprocess.CREATE_NEW_PROCESS_GROUP
    subprocess.Popen(
        [
            powershell_path(),
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-WindowStyle",
            "Hidden",
            "-File",
            str(script_path),
            "-OldPid",
            str(pid),
        ],
        creationflags=creationflags,
        close_fds=True,
    )


def cleanup_leftovers(app_dir: Path) -> None:
    """清理上次更新遗留的 .old 备份目录和更新缓存（启动时调用，失败忽略）。"""
    import shutil

    old_dir = Path(str(app_dir) + ".old")
    if old_dir.exists():
        try:
            shutil.rmtree(old_dir, ignore_errors=True)
            logger.info("已清理旧版本备份目录：%s", old_dir)
        except OSError:
            pass
    try:
        root = updates_root()
        if root.exists():
            shutil.rmtree(root, ignore_errors=True)
            logger.info("已清理更新缓存目录：%s", root)
    except OSError:
        pass


def updates_root() -> Path:
    """更新缓存目录（和配置文件放在一起）。"""
    import config as config_mod

    return config_mod.config_dir() / "updates"
