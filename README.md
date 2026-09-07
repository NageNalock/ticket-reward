# Bing Rewards

一个驻留在 macOS 菜单栏的本地 Microsoft Rewards 运行工具。支持手动运行、定时运行、失败重试、历史记录，以及从 GitHub Releases 检查和下载更新。

## 界面与 Logo

使用原生 AppKit，浅色薄荷绿界面搭配原创二次元美少女形象。运行概览提供累计积分、下次运行时间、实时进度与历史筛选。关闭主窗口会隐藏到菜单栏，菜单中的「显式退出」才会结束应用。

- `assets/mascot.png`：透明背景人物 Logo。
- `assets/AppIcon.icns`：包含 16–1024 px 分辨率的 macOS 应用图标。
- `scripts/build_icon.sh`：从人物源图重新生成应用图标。
- [设计说明与生成提示词](docs/brand.md)。

## 本地运行

需要 macOS、Python 3.10 或更高版本。当前发布的安装包面向 Apple Silicon。

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m playwright install chromium
.venv/bin/python scripts/menu_bar_app.py
```

首次运行，点击「登录账号」，完成 Microsoft 登录后再点击「立即运行」。定时运行时间及重试间隔可在「打开配置」中修改。修改定时配置后重新启动应用生效。

概览窗口会独立显示登录状态，启动、重新打开窗口、刷新记录时自动检测，并在后台定期刷新。已登录时隐藏登录按钮；会话过期时显示「重新登录」。检测只读本机保存的 Bing 会话有效期和失效标记，不打开浏览器、不读取账号名称或输出 Cookie 内容；网络端是否仍接受该会话，由实际运行时校验。运行进度和记录刷新不会覆盖登录状态。

登录成功后，Rewards 面板和积分余额仍可能需要时间加载。任务开始前、结束后都会等待余额出现；每次等待最多 15 秒，读取失败后重新打开页面，最多尝试 3 次。重试用尽时保留「积分未核实」提示和未知增量，日志会标明读取阶段及失败原因。

按 `daily_activities.skip_types` 配置跳过的活动会记录在「跳过任务」中，不计作完成或失败，也不会因此触发失败重试。旧运行记录仍保留当时的结果。

活动扫描覆盖 Rewards 面板中的「每日设置」和额外活动。同名卡片按各自的活动标识或链接匹配；无法唯一识别的卡片会提示失败，不会借用另一张同名卡片的完成状态。

带固定 `+N` 积分的推荐卡会尝试打开活动页，再刷新 Rewards 校验完成状态；没有固定积分、只宣传邀请奖励的卡片仍按 `referral` 跳过。访问过程不会发送邀请或提交表单。若需要跳过所有这类访问，可将 `visit` 加入 `daily_activities.skip_types`。只有 Rewards 明确标记完成，或该卡属于已全部完成的每日设置，才会计作完成；打开页面、卡片消失本身都不代表积分到账。

## 检查更新

点击主窗口或菜单栏菜单中的「检查更新」。应用会在后台读取：

```text
https://api.github.com/repos/NageNalock/ticket-reward/releases/latest
```

窗口显示最新正式版本、更新说明和安装包大小。点击「下载更新」后可查看进度或取消下载。应用优先使用 GitHub 提供的 `sha256` digest；没有 digest 时读取同一 Release 的 `SHA256SUMS.txt`。只有大小和 SHA-256 都匹配的完整安装包才会出现在最终路径中。

下载完成后点击「在 Finder 中显示」，双击打开 DMG，退出应用，再把左侧的 `Bing Rewards.app` 拖到右侧的 `Applications` 文件夹替换。安装后推出磁盘映像。应用不会直接覆盖正在运行的程序。

打包版的登录状态、配置和记录位于 `~/Library/Application Support/Bing Rewards/`，替换 `.app` 不会删除这些数据。下载包保存在其 `data/updates/` 子目录。源码运行时使用仓库内的 `config/` 和 `data/`。

更新规则：

- 读取最新正式 Release，忽略草稿和预发布。
- 兼容现有 `build-序号-提交号` 标签；同一构建或更旧构建不会提示更新。
- 同时支持 `v主版本.次版本.修订版本` 格式。
- 源码版没有可比较的 CI 构建序号时，明确提示无法自动比较，可手动下载发布包。
- 优先选择适配 `arm64`、`x86_64` 或 `universal2` / `universal` 的 DMG；旧 Release 只有 ZIP 时仍支持下载，解压后安装。缺少匹配架构的包时显示提示。
- 无正式版本、网络超时、限流、校验失败和取消下载均有独立处理，可重试。

接口与格式依据：[GitHub Releases API](https://docs.github.com/en/rest/releases/releases#get-the-latest-release)。

## 构建与发布

```bash
.venv/bin/python -m pip install -r requirements-build.txt
bash scripts/build_app.sh
```

生成：

```text
dist/Bing Rewards.app
dist/Bing-Rewards-macOS-arm64.dmg
dist/SHA256SUMS.txt
```

构建脚本自动生成图标和 `build/build-info.json`，并把版本元数据一起放入 `.app`。`src/version.py` 与 `pyproject.toml` 中的应用版本应同步修改。

已有 `.app` 时，可用 `bash scripts/build_dmg.sh` 重新生成 DMG。DMG 为只读压缩映像，包含应用和指向 `/Applications` 的快捷方式，Finder 布局由固定参数生成。打包时不复制本机扩展属性，并排除浏览器安装缓存中的本机路径记录。

GitHub Actions 在各分支运行检查与构建，只有 `main` 发布正式 Release。发布标签与应用内嵌标签来自同一份构建元数据。每个 Release 包含：

```text
Bing-Rewards-macOS-arm64.dmg
SHA256SUMS.txt
```

SHA256SUMS 使用 `64位十六进制摘要  文件名` 格式；更新器也兼容旧版的 `dist/文件名` 格式。当前构建保留仓库原有的 ad-hoc 签名方式，未配置 Developer ID 签名及 Apple notarization。

## 验证

```bash
.venv/bin/python -m unittest discover -s tests -v
.venv/bin/ruff check src tests scripts
REWARDS_BROWSER_TESTS=1 PLAYWRIGHT_BROWSERS_PATH="$PWD/build/playwright-browsers" \
  .venv/bin/python -m unittest tests.test_activity_browser -v
.venv/bin/python scripts/menu_bar_app.py --ui-smoke-test
.venv/bin/python scripts/menu_bar_app.py --check-update
```

UI 冒烟测试检查关闭窗口后菜单栏仍然存在，且不会启动定时任务。更新单元测试使用模拟网络响应，不会安装或替换应用。`--check-update` 会真实访问 GitHub 并打印版本和匹配安装包，不执行下载。

活动浏览器回归使用构建脚本下载的 Chromium，以无界面模式验证卡片扫描、同名匹配、访问和完成校验。所有网页请求由本地测试页面响应，不使用账号登录状态，也不会发送邀请。
