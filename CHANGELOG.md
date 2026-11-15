# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) `MAJOR.MINOR.PATCH`。
版本号的唯一真源是 `app/__init__.py`；发版清单与同步规则见 `AGENTS.md` 第 3 节。

---

## [0.1.4] - 2026-11-15

**主题：扫描出的文件全部可见、可搜、可直接点击播放。**

### Added

- 「扫描目录」页新增「扫描结果 · 全部文件」区块，「媒体库」页同步换成同一个全量列表组件：默认每页 200 条，可翻页 / 跳页，或选「一次显示全部」把库内所有文件一次列出（真实库副本实测 2104 条）。
- 搜索覆盖文件名、中文名、解析原名、原始标题与完整路径；`%` / `_` / `\` 按字面量处理，不会被 LIKE 通配符放大结果。
- 支持按扫描目录、匹配状态（已匹配中文 / 未匹配 / 有播放进度）筛选，按最近扫描 / 文件名 / 中文名 / 文件体积 / 观看进度排序。
- 新增 `Api.get_media_files_page(options)`：返回 `rows` 及 `total` / `total_all` / `matched` / `pages` / `directories`；排序取值走白名单，非法值安全降级。
- 点击文件名、缩略图或「播放」按钮直接调用 `play_file` 用 PotPlayer 打开（有断点自动续播），右侧另有「从头播放」。

### Changed

- 移除媒体库旧的 400 条硬上限；搜索与筛选改为只局部刷新列表容器，输入框不再丢焦点。
- 扫描完成后的自动刷新延迟由 1.5 / 1.8 秒调整为 3 秒，并新增「刷新列表」按钮。
- 升级防缓存：`app/main.py` 改用 `private_mode=True` 临时 WebView2 profile；`web/index.html` 的 `app.js?v=<版本>` 由 `tests/test_docs.py` 守护，避免装了新版仍显示旧界面。

### Fixed

- 分页页码越界时不再显示 `1001–450 / 450` 这类错误区间，改为 `(from, to) = (0, 0)`。

### Tests

- 新增 `tests/test_media_browser.py` 共 11 个用例，`tests/test_docs.py` 新增 1 个前端缓存守护用例；全量 **173 / 173 全绿**。

### Docs

- README / AGENTS / CHANGELOG / docs/FEATURES / docs/TESTING / docs/OBSIDIAN 同步 0.1.4；ZIP 与 MSI 随版本重新构建。

---

## [0.1.3] - 2026-11-15

**主题：接入本机 Obsidian，自动沉淀开发流程、自测结果与后续优化依据。**

> 版本编号回溯（2026-11-15，用户要求）：项目在完善前统一停留在 `0.1.0+` 阶段，后续完善了再开 `0.2.0+`。
> 原 `0.2.0` / `0.3.0` / `0.4.0` 依次重编号为 `0.1.1` / `0.1.2` / `0.1.3`，git tag 同步重建（提交历史不变）。

### Added

- 新增 `app/obsidian.py` 同步引擎：只读探测 `%APPDATA%\obsidian\obsidian.json` 中的 vault，
  在 vault 内独立命名空间（默认 `Magic Video/`）生成 6 篇结构化笔记 + 按天开发日志：
  `00 项目总览` / `01 版本记录` / `02 优化路线图` / `03 自测报告` / `04 功能缺口与决策` / `05 使用速查`。
  - 笔记带 YAML frontmatter、`managed-by` 标记、`[[wikilink]]` 互链，可直接用 Obsidian 图谱与标签。
  - `02 优化路线图` 直接解析 `docs/OPTIMIZATION.md` 的 P0–P3 表格，文档为单一真源，改建议不用两边维护。
  - `03 自测报告` 读取 `data/test_last.json` 与 `data/obsidian_history.json`，含最近结果、模块分布与结果时间线。
  - `04 功能缺口与决策` 记录真实待补齐项与 ADR 轻量决策，供后续版本直接取用。
- `run_tests.py` 每次运行写出 `data/test_last.json`（用例数、通过/失败/错误/跳过、耗时、各模块用例数）。
- 新增 CLI `python cli.py obsidian status|sync|vaults|path|folder|auto|open`；
  `cli.py test` 在 `obsidian_auto_sync=1` 且测试全绿时自动追加一次结果留痕（默认关闭）。
- 「设置」页新增 **Obsidian 知识沉淀** 卡片：vault 列表与路径、笔记文件夹、自动同步开关、
  「同步到 Obsidian」「打开笔记目录」按钮；`Api` 新增 4 个桥接方法。
- 新增 `docs/OBSIDIAN.md`（集成契约、笔记结构、安全边界、故障排查）与 `tests/test_obsidian.py`（26 个用例）。
- 新增 `build_dist.py` 与 `installer/product.wxs`：一键生成 PyInstaller onedir 绿色版 ZIP 与 WiX v3 MSI 安装包（perUser、开始菜单快捷方式、卸载入口）。
- 新增 `app/runtime.py` / `app/daemon.py`：打包版与源码版共用启动命令，托盘守护通过 `--daemon` 复用同一个 exe；`run.py` 另加 `--selftest` 自检入口。
- 新增 `tests/test_packaging.py`：覆盖四种路径布局、两种形态启动命令、开机自启、笔记降级、守护/窗口启动分发与自检分发（共 17 个用例）。

### Changed

- 版本号由 0.1.2 提升至 **0.1.3**；`config.DEFAULTS` 新增 `obsidian_vault` / `obsidian_folder` / `obsidian_auto_sync`。
- 多个 vault 时**不自动猜**：仅当候选恰好 1 个时自动采用，否则要求显式指定，避免误写错库。
- `app/config.py` 拆分**资源目录与数据目录**：打包版资源取 `sys._MEIPASS`，数据默认 `%APPDATA%\Magic Video`；绿色版由 `portable.txt` 改为 exe 同级 `data/`；可用 `MAGIC_VIDEO_DATA_DIR` 覆盖。
- `app/autostart.py` / `app/tray.py` / `app/api.py` 改用 `runtime.*_command()`；`app/obsidian.py` 在打包版用版本号替代 git 信息；`daemon.py` 改为调用共享入口。

### Safety

- 只写 vault 内独立文件夹；`safe_folder()` 拒绝 `..`、绝对路径、盘符，`resolve_vault()` 二次校验解析后仍在 vault 内。
- 遇到同名但没有 `managed-by` 标记的用户笔记一律跳过；自动内容只替换 `BEGIN AUTO / END AUTO` 区块，
  区块外「我的批注」与用户自定义的 frontmatter 字段永久保留；原子写；模块内不删除任何用户文件。
- 笔记只含聚合统计与项目文档，不含视频路径、文件名或观看明细；不安装插件、不启动 Obsidian、不做定时同步。

### Docs

- README / AGENTS / FEATURES / TESTING / OPTIMIZATION 同步 0.1.3；AGENTS 需求基线新增第 10 条与输出/禁止规则。
- README 新增「打包产物」章节；AGENTS 输出管理补充构建脚本、MSI 定义与 `dist/` / `build/` 约定；`docs/TESTING.md` 追加打包产物验收记录。

---

## [0.1.2] - 2026-11-15

**主题：完整自测、单元测试体系、稳定性修复与交付文档/版本管理。**

### Added

- 新增 `tests/` 单元测试套件与 `run_tests.py` 入口，共 **116 个用例**，零新增依赖（标准库 `unittest`）。
  - 覆盖数据层与旧库迁移、文件名解析、清单导入、中文匹配引擎、PotPlayer 桥接、守护会话、js_api、端到端串联、文档版本一致性。
  - `tests/base.py` 提供隔离基类：临时 SQLite + 屏蔽真实网络，保证测试绝不读写用户真实播放记录。
- 新增「标记已看 / 标记未看 / 重置进度」纠错能力（`Api.mark_watched` / `mark_unwatched` / `reset_progress` / `get_completed`），UI 在「继续观看」页增加「已看完（可纠错）」区块；纠错只改进度表，**不删除观看会话历史**。
- 新增 CLI 命令 `progress`、`mark <file_id> watched|unwatched|reset`、`test`。
- 新增交付文档：`README.md`、`AGENTS.md`、`CHANGELOG.md`、`docs/FEATURES.md`、`docs/OPTIMIZATION.md`、`docs/TESTING.md`。
- 建立版本管理闭环：单一真源版本号 + 发版清单 + `tests/test_docs.py` 一致性守护 + git tag。

### Changed

- 版本号由 0.1.1 提升至 **0.1.2**；在线请求 User-Agent 改为引用 `__version__`，不再硬编码。
- `scanner.parse_filename` 重写解析流水线：优先 `SxxEyy`，兼容中文「第 N 话/期」、以集数结尾的文件名；从标题中清除季标记、集标记、年份与画质标签。
- `scanner.should_ignore` 的 `SP` 改为独立词匹配；`scan()` 把 `is_file()` 纳入容错，单文件不可读不再中断整次扫描。
- `metadata.rebuild_index` 调整索引顺序，**用户手工修正优先级高于内置词典与在线结果**；`lookup_local` 模糊匹配增加季数冲突保护；未收录的季只返回主标题中文名并标记 `base`，不编造季名。
- `metadata.match_titles` 按归一化标题去重，同一作品的不同大小写/空格写法只查一次，并为每种写法保留结果映射；`local_only` 模式不再虚报「在线可用」。
- `app/potplayer.py` 与 `app/autostart.py` 的平台相关导入做安全降级，非 Windows 环境导入不崩溃。
- `Api.save_settings` 白名单补充 `minimize_to_tray`。

### Fixed

- 修复守护进程**首次轮询不写进度**的问题：新会话的完成判定、时长回填此前要等位置变化才生效。
- 修复 `Spy x Family`、`Special Edition` 等含 `sp` 的正片被当成特典跳过。
- 修复 `Show.S01E02.mkv` 标题残留 `S01E02`、`Movie.2019.mkv` 标题残留年份、`Title - 12.mkv` 解析不出集数。
- 修复 `第2期` 无法识别为第 2 季（清单导入时季数丢失）。
- 修复 `apply_to_media` / `apply_to_watched` 返回「匹配条数」而非真实更新行数，导致扫描结果虚高。
- 修复 `watch_progress.completed` 在重看开头后被回退为 0（已看完是历史事实，需显式「标记未看」才能改）；保留 `MAX(completed, ?)` 语义并新增显式纠错入口。
- 修复 `data/` 忽略规则曾连带忽略 `app/data/aliases_builtin.json`（已改为 `/data/`）。
- 本轮共修复 **22 项**测试暴露的实现缺陷与 3 项测试用例自身问题。

### Tests

- `python run_tests.py` → 116 个用例全绿（详见 `docs/TESTING.md`）。
- Python / JavaScript 语法检查、CLI 全命令冒烟、原生窗口 8 页渲染与 js_api 桥接冒烟均通过。

### Docs

- README 补齐需求对照、快速开始、中文匹配机制、数据模型、CLI、版本输出一览。
- AGENTS 明确需求基线、架构约束、发版流程、输出目录与禁止事项。
- `docs/OPTIMIZATION.md` 收录同类软件调研与 14 条分优先级优化建议。

---

## [0.1.1] - 2026-11-15

**主题：中文名改为导入时同步匹配，移除每日定时补全。**

### Added

- 新增 `app/metadata.py` 即时匹配引擎：内置离线词典 → 本地已学词典 → 在线 Bangumi 三级链路，只在导入列表、扫描目录、手动「重新匹配」时执行。
- 新增 `app/data/aliases_builtin.json`：98 部作品的真实 Bangumi 中英/中日对照，随程序自带、离线可用。
- 导入预览的中文名可直接编辑，改动永久记住、下次自动命中。
- 新增 `cli.py` 的 `match` / `dict` / `import-alias-file` 命令与界面「重新匹配」入口。

### Changed

- **删除** `app/metadata_queue.py`、`data/pending_titles.json` 与 WorkBuddy 每日 09:00 自动化任务；界面 8 页去掉全部定时队列文案。
- 数据库补 `title_cn` / `match_by` 列（`ALTER TABLE ADD COLUMN`），旧播放会话与断点进度完整保留。
- `init_db()` 加进程级幂等，修复界面刷新反复写库导致的 `readonly database`。
- 词典首次加载由 4.69s 优化到 0.021s（单连接批量写）。
- `.gitignore` 的 `data/` 改为 `/data/`，保证内置词典进版本库。

### Fixed

- `S2 01-12` 不再显示成第一季中文名；年份 `2099` 不再被误解析成第 99 集。
- 在线源不可达时整批快速降级（实测约 1.85s），不阻塞导入与扫描。

---

## [0.1.0] - 2026-11-14

**主题：P0 可运行骨架。**

### Added

- pywebview（Edge WebView2）原生窗口 + `js_api` 进程内桥接，8 个功能页（总览 / 继续观看 / 历史 / 媒体库 / 看过列表 / 导入 / 扫描 / 设置）。
- SQLite（WAL）8 张核心表、配置读写与旧库迁移能力。
- PotPlayer 桥接：`WM_USER` 消息回读总时长 / 当前位置 / 播放状态，`/seek` 精确断点续播；实测读到正在播放的番剧进度。
- 后台守护进程、系统托盘、`winreg` 开机自启（HKCU，无需管理员）。
- 「看过的动漫列表」导入（文本 / CSV / TSV）、目录增量扫描、文件名清洗与集数提取。
- `run.py` / `daemon.py` / `cli.py` / `start.bat` / `start_daemon.bat` 一键启动与命令行工具。
- 本地 git 仓库初始化与首次提交（`75222b1`）。

### Notes

- 该版本中文名补全依赖「导出待补全清单 → 每日定时任务联网回填 → 软件启动时导入」，已在 0.1.1 按用户要求移除。
- 远程仓库后续已建好并完整推送：`https://github.com/tiantangliwai/magic-video`（SSH 通道，2026-11-15）。
