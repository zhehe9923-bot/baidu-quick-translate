# 利用 GitHub 的更新方案

本文说明如何用 **GitHub Releases** 给「划词翻译」提供自动更新：
发布侧用 GitHub Actions 构建并发布，客户端用自带更新器检查、下载、校验并替换重启。

> 代码位置：`updater.py`（更新器核心）、`gui.py` 里的「检查更新」窗口、
> `.github/workflows/release.yml`（自动发布）、`main.py --check-update`（命令行检查）。

---

## 1. 总体流程

```
你：改代码 → 改 version.py → git tag v1.3.0 → git push --tags
        │
        ▼
GitHub Actions（Windows）：
  按 tag 写入版本号 → PyInstaller 打包 onedir → 压 zip → 算 SHA-256
  → 生成 version.json → 创建 Release（上传 zip + version.json）
        │
        ▼
用户电脑上的「划词翻译」：
  读取 https://github.com/zhehe9923-bot/baidu-quick-translate/releases/latest/download/version.json
  → 版本比较（有新版本才提示）
  → 下载 zip → 校验 SHA-256
  → 解压到配置目录（防 zip 路径穿越）
  → 生成 apply_update.ps1：等程序退出 → 旧目录改名 .old → 覆盖 → 启动新 exe
  → 新程序启动时清理 .old，更新完成
```

关键点：**`releases/latest/download/version.json` 这类地址永远指向最新 Release 的资产**，
客户端不需要调 GitHub API（不受未登录 60 次/小时限流影响）。

---

## 2. 仓库准备（一次性）

> 目标仓库：**`https://github.com/zhehe9923-bot/baidu-quick-translate`**（public）。
> 程序里的默认更新源已经指向它，仓库建好、发布一次后即可直接用。

1. 在 GitHub 新建一个 **public** 仓库：名字 `baidu-quick-translate`（**不要**勾选 Add README / .gitignore / license，保持空仓库）。
2. 把本目录（`翻译/`）下的**文件**放到仓库根目录，保持结构：
   ```
   .github/workflows/release.yml
   version.py
   main.py  gui.py  updater.py  ...
   assets/  docs/  build_exe.bat  run.bat  README.md
   ```
   > workflow 默认假设程序源码在仓库根目录；如果你的布局不同，改一下 `run:` 里的路径即可。
3. 仓库设置里无需额外配置；workflow 使用自带的 `GITHUB_TOKEN`（已声明 `permissions: contents: write`）。
4. 确认 `version.py` 里的版本号与将要发布的版本一致（workflow 会按 tag/输入的版本覆盖，但本地也要改，方便 `--check-update` 自测）。

### 2.1 只用浏览器发布（不需要命令行，本机网络被限制时用这个）

1. 建好仓库后，打开仓库首页 → **Add file → Upload files**；
2. 把 `翻译_可上传\` 文件夹里的**所有内容**（含 `.github`、`assets`、`docs`）拖进网页，Commit；
3. 打开仓库 **Actions** 标签页 → 左侧选 `release` → 右侧 **Run workflow**：
   - 版本号填 `1.2.0`（不带 v），点绿色 Run workflow；
4. 等 3~5 分钟：Actions 里出现绿色对勾后，仓库 **Releases** 页面会出现 `v1.2.0`，
   资产包含 `BaiduQuickTranslate-win64.zip` 和 `version.json`；
5. 之后客户端「检查更新」就能读到它了。

> 之后每次发新版：改代码 → 网页上传改动文件（或命令行 push）→ Actions 里 Run workflow 填入新版本号。
> 命令行方式见下一节。

---

## 3. 发布新版本（每次更新）

```bash
# 1) 改代码、更新说明
# 2) 升版本号（app 自检 / 命令行也会显示这个版本）
#    version.py:  APP_VERSION = "1.3.0"

git add .
git commit -m "feat: 新增 xxx"
git tag v1.3.0
git push origin main
git push origin v1.3.0        # ← 这一步触发 Actions 发布
```

Actions 完成后，在仓库 **Releases** 页面会出现：

| 资产 | 用途 |
| --- | --- |
| `BaiduQuickTranslate-win64.zip` | 免安装版（程序目录整体，含 exe + `_internal`） |
| `version.json` | 客户端更新源（版本号 / 下载地址 / SHA-256 / 更新说明） |

`version.json` 示例：

```json
{
  "version": "1.3.0",
  "url": "https://github.com/zhehe9923-bot/baidu-quick-translate/releases/latest/download/BaiduQuickTranslate-win64.zip",
  "sha256": "e3b0c44298fc1c149afbf4c8996fb924...",
  "notes": "划词翻译 v1.3.0\n\nfeat: 新增 xxx",
  "page": "https://github.com/zhehe9923-bot/baidu-quick-translate/releases/latest"
}
```

> 手动发布也可以：本地 `build_exe.bat` 打包 → 压 zip → 算 SHA-256 →
> 在 Releases 里手动创建 tag 并上传 zip 和手写的 `version.json`（字段如上）。

---

## 4. 客户端更新流程

1. **检查更新**：主窗口「检查更新」按钮；或启动后自动检查（默认开，每天最多一次）。
2. **版本比较**：按 `v1.2.3` 数字段比较，只有更高版本才提示；显示更新说明。
3. **下载**：流式下载到 `%APPDATA%\BaiduQuickTranslate\updates\<version>\update.zip`。
   - 有 300 MB 下载上限；
   - 必须带 `sha256`，下载完成后逐块校验，不一致直接丢弃。
4. **解压**：限制解压后总大小（600 MB），拒绝 `../`、绝对路径、盘符路径（防 zip 路径穿越）。
5. **替换重启**（Windows 上程序运行中不能覆盖自身文件）：
   - 生成 `apply_update.ps1`（PowerShell，天然支持中文路径；不用 .cmd 是为了避开批处理代码页坑）；
   - 先等当前进程 PID 退出（`Get-Process` 轮询，最多 120 秒）；
   - 把旧程序目录改名为 `<目录>.old`（文件被占用时重试，最多 60 秒）；
   - 用 `Copy-Item` 把新文件复制到原目录，`Start-Process` 启动新 `划词翻译.exe`；
   - 删除 `.old`。
   - 新程序启动时也会尝试清理上次遗留的 `.old` 和更新缓存。
   - 覆盖失败会自动回滚到旧版本并重新启动，不会让用户「没有程序可用」。
6. **失败处理**：下载/校验/解压失败不会改动现有程序；替换失败时脚本会提示手动解压位置并保留 `.old` 备份。

更新器只替换**程序目录**，配置文件 `config.json`、用量 `usage.json` 都在 `%APPDATA%\BaiduQuickTranslate`，
升级不会丢设置和密钥。

源码运行（`python main.py`）时**不支持自动替换**：更新窗口会提示用 `git pull` 或重新下载发布包；
自动检查更新也只在打包版（frozen）里启用。

---

## 5. 配置更新源

`config.json`（或程序内「检查更新」窗口）里的字段：

| 字段 | 说明 |
| --- | --- |
| `update_url` | version.json 地址，默认已指向本仓库：`https://github.com/zhehe9923-bot/baidu-quick-translate/releases/latest/download/version.json` |
| `update_auto_check` | 启动时自动检查，默认 `true` |
| `update_last_check` | 上次检查时间戳（自动维护，24 小时内不重复检查） |
| `update_mirror` | 可选下载加速前缀（见下文），默认空 |

命令行检查（不需要打开界面）：

```bat
python main.py --check-update
```

发布后先用它验证一次：能读到 `version.json`、显示版本/更新说明/下载地址即为配置成功。

---

## 6. 国内下载加速 / 镜像

GitHub Release 下载在国内可能较慢，可任选一种方式：

1. **代理前缀**：把 `update_mirror` 设为例如 `https://ghproxy.net/`，客户端会自动拼成
   `https://ghproxy.net/https://github.com/.../xxx.zip` 下载；`sha256` 校验不受影响。
2. **自建镜像**：把 zip 和 version.json 放到自己的服务器/OSS/CDN，改 `update_url` 指向镜像的 version.json、
   并把其 `url` 写成镜像地址即可。
3. **Gitee 同步**：在 Gitee 建同名仓库并手动上传 Release 资产，`update_url` 指向 Gitee 的
   `releases/latest/download/version.json`（Gitee 路径格式以其文档为准）。

> 无论用哪种镜像，都要保证 `version.json` 里的 `sha256` 与实际 zip 一致；客户端只信任这个校验值。

---

## 7. 安全设计

- 只接受 `http(s)` 更新源；默认使用 GitHub HTTPS。
- **必须提供 SHA-256**，缺失或格式不正确直接拒绝更新。
- 下载限速/限大小；解压前校验条目路径与总大小，防路径穿越和 zip 炸弹。
- 更新器不执行发布包里的任何脚本，只做「替换程序目录 + 启动同名 exe」。
- Release 只由仓库自己的 Actions 发布（token 权限最小化）；建议不要随意把仓库交给他人。
- 可选的进一步增强：
  - 给 zip 做代码签名 / 用 `minisign` 校验发布者签名；
  - 在 `version.json` 里加 `min_version`，对跨大版本强制手动更新；
  - stable / beta 两个更新源（两套 `version.json`）。

---

## 8. 回滚与故障处理

| 现象 | 处理 |
| --- | --- |
| 更新后程序打不开 | 程序目录旁边会有 `<目录>.old` 备份：删掉新目录，把 `.old` 改回原名即可 |
| 替换时报「无法替换程序目录」 | 程序所在目录没有写权限（例如装在 `C:\Program Files`）。把整个文件夹复制到用户目录，或手动解压 `updates\<版本>\stage` 覆盖 |
| 下载一直失败 | 换网络/配置 `update_mirror`，或直接去 Release 页面手动下载 zip 覆盖 |
| 自动检查没反应 | 打包版才自动检查；检查 `update_url` 是否还是占位符；看 `%APPDATA%\BaiduQuickTranslate\app.log` |

> 建议安装位置：`D:\Apps\划词翻译\` 这类用户可写目录（不要放 Program Files）。

---

## 9. 本地测试方案（不依赖真实仓库）

1. 起一个本地 HTTP 服务，准备 `version.json` + zip（zip 里包含 `划词翻译/划词翻译.exe`）。
2. 把 `update_url` 指到 `http://127.0.0.1:8765/version.json`。
3. 用 `python main.py --check-update` 验证读取与版本比较。
4. 打包版里点「检查更新 → 下载并安装」，确认：SHA-256 校验、解压、重启替换全部成功。
5. `python main.py --selftest` 里有「GitHub 更新：版本比较 / 校验 / 解压安全」一组用例，
   覆盖版本解析、version.json 校验、SHA-256、zip 路径穿越、更新脚本生成。

---

## 10. 版本策略

- 采用 `v主版本.次版本.修订号`（SemVer 风格），tag 必须 `v` 开头；
- workflow 会把 tag 写进 `version.py` 再打包，保证「发布包版本 = tag」；
- 重大不兼容（例如配置结构大改）建议升主版本，并在更新说明里提醒备份配置；
- 预发布版可以用单独的 prerelease 更新源（另一份 `version.json` 地址）来灰度。
