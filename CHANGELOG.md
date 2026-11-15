# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) `MAJOR.MINOR.PATCH`。
版本号的唯一真源是 `app/__init__.py`；发版清单与同步规则见 `AGENTS.md` 第 3 节。

---

## [0.1.5] - 2026-11-15

**主题：一天内 27 次本地迭代的集中发布——从「能看历史」变成「能追番」。**

这一批全部按「日常迭代」节奏开发（不单独打包、不打 tag），累计的改动统一在 0.1.5 发布。
用例数从 173 涨到 **709**。

### Added

- **追剧列表**（`app/watchlist.py`）：导入 763 条原文清单，分「已下载 / 未下载」两栏互转，支持行内改名、↑↓ 单步调序与 ⇈ 一键置顶（同栏内最小 `sort_order`，不重排不跨栏），按作品名匹配本地文件后可直接打开所在文件夹。
- **后台进程页**：显示守护进程状态、最近日志与启停按钮；`daemon_auto_start` 默认开启，主窗口与守护只通过 SQLite 通信。
- **媒体库树状视图**：作品 → 季 → 集三级展开，点击节点才渲染文件行；同时提供 cards / series / folder / flat 四种展示视图（媒体库默认卡片、扫描页默认目录）。
- **内嵌播放与播放控制条**：`app/embedded_player.py` 用 ctypes `SetParent` 把 PotPlayer 嵌进主窗口（失败自动降级外部播放），顶栏 `#playerBar` 常驻「上一集 / 播放暂停 / 停止 / 下一集 / 倍速 / 全屏」，内嵌舞台另有底部控制条。
- **剧集墙**：按作品聚合的封面卡片墙，封面从 AGE动漫（`agedm.io`）与 Seeduck（`seeduck.cc`）抓取；支持改名、人工设定封面（Ctrl+V 粘贴 / 选本地文件 / 填网址）、「查看分集」带搜索词跳转媒体库、整部看完后一键清理本地文件（进回收站，观看记录保留）。
- **看过的列表增强**：快速搜索、行内改名、双击季数编辑（接受 `2` / `S3` / `第4季`）、默认平铺表格 + 可切卡片墙、一键打开本地所在文件夹。
- **从播放记录同步**：按 `(作品名, 季)` 聚合成一条区间行（12 集压成 1 行），幂等且能清理旧的「每集一条」记录。
- **人工清理**：删除单条历史会话、删除整部作品的本地文件；`SHFileOperationW` 进回收站，拒相对路径 / 非视频 / 符号链接 / 系统目录，失败不降级永久删除，单次上限 300。
- **全盘快搜**：独立左侧导航项，`es.exe` → Everything HTTP → SQL 三级降级（本机 Everything 以 `-svc` 运行、IPC 不可用时自动走 HTTP，两者都不可用时降级为库内搜索，属正常设计）。
- **网页搜索**：左侧新增入口，并行抓取 AGE动漫 与 Seeduck 的搜索结果，合并后渲染为本地卡片并**标明来源**，单站失败不影响另一站；不做内嵌浏览器，点开走系统默认浏览器。
- **视频黑名单**：新增设置项（`video_blacklist` 词表 + `blacklist_enabled` 开关，默认开启），名称包含任一关键词的视频在媒体库四视图、剧集墙、看过的列表、历史、继续观看、快搜、统计、追剧匹配中全部默认不展示。
- **剧集墙「是否看过」标记**：封面右上角角标，五态 `已看完 / 已看 N/M / 在看 / 看过 / 未看`，优先级由后端 `_watch_state()` 统一判定。
- **跨视图返回入口**：从剧集墙（以及追剧 / 看过的列表）带搜索词跳进媒体库后，媒体库顶部出现「← 返回X」，不用重新跳一遍。
- **开发辅助**：`dev.bat` / `./start --local` 本地调试入口，源码改动不必依赖已安装的 exe 验证。

### Changed

- **数据目录迁到 `D:\magicvideo`**：优先级 `MAGIC_VIDEO_DATA_DIR` > 解压目录 `portable.txt` > 默认；旧 `%APPDATA%\Magic Video` 自动备份迁移。
- **性能**：媒体库与剧集墙三处热点全部修掉，并加 20 秒结果缓存（键为数据指纹，不是文件 mtime）：

  | 场景 | 改前 | 首次 | 命中缓存 |
  |---|---|---|---|
  | 剧集墙 | 1105 ms | 257 ms | 7 ms |
  | 媒体库树 | 511 ms | 177 ms | 6 ms |
  | 平铺全量 | 235 ms | 146 ms | — |

- **播放控制改走 PotPlayer 官方指令通道**：`WM_COMMAND(0x111)`（`10014` 播放暂停 / `20001` 播放 / `20000` 暂停 / `20002` 停止 / `10123` 上一集 / `10124` 下一集）→ `POT_SET_PLAY_STATUS(0x5007)` → 投按键，三段**逐条退让**并在每段后复查 `POT_GET_PLAY_STATUS(0x5006)`；倍速与全屏改走 `POT_SEND_VIRTUAL_KEY(0x5010)`（不要求窗口焦点）。
- **同步去重**：`watched_list` 是导入快照，播放只写 `watch_progress` / `watch_sessions`；同步必须按 `(title, 季)` 聚合，禁止每集 commit。
- **历史按作品去重展示**：`get_history()` 按作品标题分组只留最新一次会话，只收拢展示、旧会话零删除。
- **继续观看软删除**：`dismissed_at` 标记，不再物理删行。
- **深色主题**：`html[data-theme=dark]` + 原生标题栏 DWM 属性同步，消除顶部白条。
- **封面两层缓存**：后端按 `mtime_ns + size` 失效的 data URI 缓存 + 前端 `coverUriCache` 只请求缺失项，禁止各视图各写一次 `cover_data_uris`。
- **卡片墙结构固定为「外层普通块 + 内层 `.poster-grid`」**，卡片模式不再挂 `content-fill`，标题恢复正常显示。
- **单实例与启动**：命名互斥锁保证只有一个窗口，`_window` 改为私有属性避免反射 WebView2 刷 `E_NOINTERFACE`，播放器状态轮询改用 `SendMessageTimeoutW`（300 ms 超时）。

### Fixed

- **黑名单按字形归一化**：卡片标题是日文新字体「三上悠**亜**」（U+4E9C），词表是简体「三上悠**亚**」（U+4E9A），繁体「亞」又是一位——三个码位不等，包含判断永远不命中。改为 NFKC → 小写 → 一次性 `translate`（去连接符 + 异体归并），只改比较不改展示。
- **短英文词误伤**：去连接符后 `BDrip X264` 变成 `bdripx264` 撞上 `ipx`、`future` / `return` 撞上 `ure`，导致《GATE 奇幻自卫队》《The Return Of The Native》等正常番被隐藏。≤4 字母的纯 ASCII 词改为要求左右原始字符非英文字母（数字与符号放行，`PRED733` 仍能命中），并**同步下推 SQL**（`GLOB '*ipx*' AND NOT GLOB '*[a-z]ipx*' ...`），否则 SQL 删掉的行 Python 没机会放回。真实库隐藏清单 41 → 39 张，剩下的全是真番号。
- **目录名派生的分组标题**（剧集墙卡片标题、媒体库树分组名）文件名里可能根本没有关键词，补 `_prune_blacklisted_groups()` 二次过滤，并扩展到季子节点标题。
- **剧集墙仍能看到黑名单剧集**：`FC2PPV` 挡不住 `FC2-PPV-1288` 的连字符差异，改为两侧归一化后再比。
- **播放按钮点了没反应**：旧实现只投 `WM_KEYDOWN/WM_KEYUP`，遇自定义键位 / 内嵌子窗口 / 播放器不在前台就静默失败。
- **看过的列表卡片墙挤成一堆且没有标题**：`flex` 铺满规则把卡片冻成弹性子项、`overflow:hidden` 裁掉标题。
- **剧集墙「看过」误标**：直接复用追剧的宽松匹配会把《斯巴达克斯》标成《斯巴达300勇士》、《内情》标成《After Story》，加标题校验——归一化后完全相等，或一方包含另一方且短侧 ≥ 4 字符。
- **封面抓错图**：AGE 页面只取 `data-original`（`src` 是共用占位图，百度代理要解出原始 CDN），Seeduck 用 `<h2>` 短标题；模糊兜底阈值从 0.62 收紧到 `strict_pick()` 的 0.90 + 季/续作后缀校验。
- **后台记录状态误报**、**深色模式顶部白条**、**封面抓取未对齐剧集墙标题**、**启动多窗口与 WebView2 COM 报错**一并修掉。
- **缓存永不命中**：缓存键最初用 `os.stat()` 的 mtime，而守护每 3 秒写播放进度，mtime 一直在变；改为数据指纹（库路径 + `db.write_count()` + 行数 / `MAX(id)` / 字段长度和 / 软删行数，播放进度故意不纳入）。

### Tests

- 用例数 **173 → 709**，新增模块：`test_watchlist`、`test_watchlist_top`、`test_sync_dedupe`、`test_blacklist`、`test_websearch`、`test_poster_watched`、`test_player_controls`、`test_embedded_player`、`test_history_dedupe`、`test_media_browser`、`test_theme`、`test_file_ops`、`test_everything` 等。
- 新增 `run_changed_tests.py`：按 `git diff` 自动映射受影响用例（`IMPACT` / `FRONTEND_MODULES` 是单一真源），日常只跑增量；`tests/test_changed_tests.py` 守护映射表本身。
- `tests/test_docs.py` 新增「日常只跑增量」的制度契约，防止规则被悄悄改回去。
- 全量 `python run_tests.py` 在发版前跑通，结果快照写入 `D:\magicvideo\test_last.json`。

### Docs

- README 功能表与「版本输出一览」、AGENTS 需求基线（累计 50 条）与测试门禁、`docs/FEATURES.md`（新增 §11~§24 十四节）、`docs/TESTING.md`、`docs/OBSIDIAN.md` 同步到 0.1.5。
- ZIP 与 MSI 随版本重新构建。

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
