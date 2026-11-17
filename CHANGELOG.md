# Changelog

本项目遵循 [语义化版本](https://semver.org/lang/zh-CN/) `MAJOR.MINOR.PATCH`。
版本号的唯一真源是 `app/__init__.py`；发版清单与同步规则见 `AGENTS.md` 第 3 节。

---

## [0.1.7] - 2026-11-17

**主题：把 0.1.6 发版后暴露的 4 个「明明改了但用不上」的问题一次性固化进安装包。**

本版无新功能面，只收 4 个修复（含 1 个新能力：封面随包分发）。用例数 **863 → 900**。

### Added

- **剧集墙封面随包分发**：新增 `assets/covers/`（80 张封面 + `index.json`，含 `file/title/aliases/source/cover_url/sha256/bytes/updated_at`）并打入 ZIP / MSI，新装、离线即有封面。解析顺序为**本机缓存优先 → 随包资产补齐**，资产到 `COVER_DIR` 只做单向原子复制，绝不反向写安装目录。配套 `export_covers.py` 导出资产（跳过 `source='manual'`、不删已有资产、`--refresh-index` 重算哈希）与 `assets/covers/README.md` 贡献指南，允许他人提 PR 换图、本地也可用「设为封面 / 强制重抓」换成新图。
- **清除封面记墓碑**：新增 `settings.cover_cleared`（按作品键），解决「手动清除后又被随包资产复活」；同名本机图也一并剔除。

### Fixed

- **追剧列表词序不同的同一译名匹配不上**：`textmatch.loose_title_hit()` + `watchlist._loose_match()`——常规匹配全落空后走词序容错（纯中文、≥4 汉字、首末二字组命中、字符覆盖 ≥0.9、二字组覆盖 ≥0.6），命中时 `search_keyword` 换成目录里的中文片段。清单「会长大人是女仆」现在能关联磁盘目录「会长是女仆大人」并直接点播。第一版仅比字符集合导致 762 条清单误配 36 条（`nana` 撞 267 个文件），已用硬门槛挡住：**宁漏不错**。
- **从播放记录同步多插一行「无季标记 + 第 1 季」重复行**：`importer._season_key()` 把 `NULL/0` 折算为 `1`，聚合键与查重 SQL 改为 `COALESCE(NULLIF(season_no,0),1)`（入库仍写原值），`_merge_playback_duplicates()` 同用折算键。真实库清理掉 1 条重复行。
- **看过的列表改名后历史记录 / 剧集墙仍是旧名**：`Api._sync_media_titles()` 用**改名前的** `title_cn/title` 与媒体行 `display_title` 精确相等定位、季号折算后必须相等、命中 >300 条放弃，写回 `match_by='user'` 防重刮覆盖。`match_by` 语义同时修正为「用户主动改名且新中文名非空 → 一律 `user`」。
- **发布组串被当成中文名写库**：`textmatch.looks_like_release()` 前置拦截 `KKTV AAC2 0 -BlackTV`、`1080p HEVC`、`[组名]` 这类串，不再污染词典与 `title_cn`。

### Tests

- 用例数 **863 → 900**（全量 900/900，118.7 秒）。新增 `tests/test_covers_bundle.py`（15 例：资产解析、单向复制、墓碑、索引哈希校验）、`tests/test_rename_sync.py`（9 例：同季同步、跨季隔离、发布组串拦截、`match_by` 语义）。
- `tests/base.py` 的 `_PATCHED_KEYS` 补 `BUNDLED_COVER_DIR`，缓存重置同步加 `files` 键。

### Docs

- `docs/FEATURES.md` 新增 §29（封面随包分发）与 §30（改名传播，30-A1~A7）；`docs/IMPLEMENTATION_NOTES.md` 新增 §12（同步季号折算）与 §13（改名传播与脏名防护，含 13.7 未处理脏数据清单）；`AGENTS.md` 需求基线扩到 **56 条**。
- ZIP 与 MSI 随版本重新构建。

---

## [0.1.6] - 2026-11-16

**主题：补数据地基与体验层——从「能追番」变成「记录不丢、可备份、可迁移」。**

本版不铺新功能面，集中补 v0.1.5 之后识别出的三类薄弱处（数据地基 / 边界完整性 / 外部依赖脆弱），
并顺手补掉路线图尾巴。用例数从 **709** 涨到 **863**。

### Added

- **JSON 完整备份与恢复**（`app/backup.py`）：一键导出全部数据表为单个 JSON（含 `format_version`、导出时间、行数统计），恢复支持 `merge`（仅新增）与 `overwrite`（整库覆盖）两种模式，保留原始 id 以维持 `file_id` 关联；恢复前自动备份当前库。
- **批量标记已看 / 未看 / 重置进度**：`Api.batch_mark(ids, action)`，历史页全选 + 三个批量操作；只改进度表、不删 `watch_sessions`，`completed` 保持 MAX 语义。
- **路径失效重定位向导**：目录不可达时把其下文件标 `missing_since`（只标记、不删行、不动进度）；`diagnose_library()` 只读体检、`preview_relocate()` 纯计算影响面、`apply_relocate()` 按前缀整体替换（整盘换符场景比逐文件指纹可靠），冲突交给指纹重关联，新目录自动注册。
- **清单导入三段式对齐**（`app/listimport.py`）：纯文本 / MAL `animelist.xml` / Bangumi Takeout JSON 三条入口统一成 importer record，共用预览与提交；差异报告给「只本地有 / 只清单有 / 进度不同 / 中文名冲突 / 本地有文件但没记录」；合并按 max 只增不减、中文名只补空、`match_by='user'` 永不覆盖、整体幂等。
- **导入未匹配「待确认区」**：预览返回 `pending` 行，提交默认保留但标 `status='pending_review'`（不再让外文原名冒充正常记录），看过的列表显示「待确认」徽章。
- **文件名 ID 直查 + 稳定作品 ID 层**：解析 `{tmdb-123}` / `[tvdbid-456]` / `{tvdb=789}`，`media_files.tmdb_id/tvdb_id`、`subjects.tvdb_id` 落库；`lookup_by_ids` 先查 subjects 再查媒体库，`match_one(ids=)` 以 `by='id'` 优先于标题链；展示标题按 `title_lang_pref` 切换。
- **刮削限速 + TTL 负缓存 + 强制重刮**：`bangumi_rate_ms=250` 串行限速（多线程也保证间隔）、`bangumi_backoff_sec=120` 退避、`online_negative_ttl_sec=21600` 负缓存；新表 `scrape_cache`（可重建、不进备份）；429/503 只暂停退避窗口不再永久熔断；界面新增「强制重新刮削」。
- **看片统计页**（新视图「看片统计」）：只用 `watch_sessions.watched_ms`（快进 / 拖动不计），按会话开始日期归日、同一集重看累计、黑名单剔除；给每日柱状图、看最多的作品 Top20、完成率与观看日历热力图，统计口径随返回下发并直接展示。
- **按季海报墙 + 继续观看置顶行**：`get_poster_wall` 增加 `season_cards`（每季一张），前端「按作品 / 按季」切换；`get_continue_row()` 按作品去重只留最新一条，顶部横向滚动行点一下直接续播。
- **观看日历 + 评分标签**：新表 `work_meta(work_key PK, rating, tags, updated_at)`（进备份、删媒体文件不连带删标注）；封面卡显示 ★ 评分与标签，`rate-work` 内联录入；纯本地不同步。
- **CSV / NFO 导出**：`export_csv(kind)` 导出 history / watched / media 三张表（UTF-8-BOM），`export_nfo()` 按作品写 `<数据目录>/exports/nfo/<作品>/tvshow.nfo`——**只写数据目录，绝不写视频所在目录**。
- **集数低置信度标记**：`media_files` 新增 `episode_source` / `episode_confidence`，扫描页新增「待确认集数」卡片，可手工改正。
- **缓存键 mtime 守护测试**（`tests/test_cache_keys.py`）：静态扫描 `_data_fingerprint` / `_db_signature` / `_cache_get` / `_cache_put` 源码，出现 `mtime` / `st_ctime` / `getmtime` 直接失败。

### Changed

- **完成判定三阈值可配置**：`min_resume_percent=5` / `ignore_seconds_at_start=30` / `ignore_percent_at_end=8` + 既有 `complete_percent=90`；低于下限不建续播记录、超上限标已看并清续播点。
- **真实观看时长累计**：`watch_sessions.watched_ms` + `watch_progress.total_watched_ms`，只累加「连续播放」的位置增量，快进 / 拖进度条 / 回拖都不计入；同一集连续播放仍是一个活跃 session。
- **SQLite 并发写加固**：`db._retry` 只对 locked/busy/readonly 短退避重试；`session(immediate=True)` 用 `BEGIN IMMEDIATE` 避开 `SQLITE_BUSY_SNAPSHOT`；`get_conn` 先读 `journal_mode` 再决定是否设 WAL（压掉多进程首开的 `-shm` 争抢）+ `synchronous=NORMAL`；watcher 三条写路径合成单事务并原子化「读 completed 再写续播点」。
- **多版本文件合并**：`library_tree._collapse_versions` 在**展示层**把同一 `(季, 集)` 的多文件并成一条（主条目按「已看完 > 有进度 > 体积大」挑），`file_count` 因此等于真实集数不再虚高；平铺列表给版本下拉，数据库零改动、各版本进度独立。
- **外部解析收敛到唯一适配层**：新建 `app/sites.py`，AGE动漫 / Seeduck 的全部正则、URL 模板、HTML 清洗与健康计数集中一处；`covers.py`、`websearch.py` 改为消费 `age_entries()` / `seeduck_entries()`，两边不再各写一套选优逻辑。
- **倍速步进精确化**：`DEFAULT_SPEED_STEP` 由 0.1 改为 0.25，四个预设（1.25 / 1.5 / 2 等）全部精确命中，仍允许用户显式选 0.1 / 0.5。
- **全盘快搜可达性提示**：`everything.capabilities()` 返回 `degraded` / `degraded_reason` / `http_enable_steps`，快搜页降级时用琥珀色提示块写清「为什么降级 + 两条补法」，**不改 ini、不重启服务**。
- **安装器静态核对**：`installer/verify_install_flow.py --static` 不需要 WiX 或打包产物即可核对「目录 Id 全大写 + `Secure` + `InstallPath` 写入回读 + `MajorUpgrade` + `ARPINSTALLLOCATION`」七项，并纳入单测；另附真机 7 步清单。

### Fixed

- **`第03话` 被读成「第 3 季」**：`SEASON_RE` 的中文季分支改为必须带「季 / 期」（`第 X 话 / 集 / 話` 归集数），`EPISODE_PATTERNS` 首条同时接受 `第 X 话 / 集 / 話`。
- **`Season 2 - 05` 被误判为区间集数**：`RANGE_EP_RE` 要求两个数字之间**不允许空格**，`Show.01-02` 仍按低置信度区间处理。
- **缓存键回退风险**：把「缓存键不得使用文件 mtime」从文档约定变成可执行断言（守护测试），防止再次被改回去。
- **NFO 导出误写视频目录**：显式限定只写 `<数据目录>/exports/nfo/`，并有用例断言源目录内文件数不变。
- **`work_meta` 漏出备份范围**：`backup.TABLES` 由 9 张扩到 10 张，用例同步守护。

### Tests

- 用例数 **709 → 863**，新增模块：`test_stats`（25 例，统计 / 继续观看 / 按季 / 多版本 / 评分标签 / 日历）、`test_cache_keys`（7 例）、`test_concurrency`（10 例），并在 `test_scanner` 增 `EpisodeConfidenceTests`（10 例）、`test_backup` 增 `TableExportTest`（6 例）、`test_api` / `test_everything_open` / `test_packaging` 各补新用例。
- `run_changed_tests.py` 的 `IMPACT` / `FRONTEND_MODULES` 同步补入 `sites`、`backup`、`library_tree` 等新模块映射。
- 全量 `python run_tests.py` 在发版前跑通（863 / 863），结果快照写入 `D:\magicvideo\test_last.json`。

### Docs

- 新增 `docs/ROADMAP_0.1.6.md`（需求清单与逐项落地结论）、`docs/IMPLEMENTATION_NOTES.md` §10（P2 阶段实现细节与已知限制）。
- README 功能表与「版本输出一览」、AGENTS 需求基线（累计 53 条）、`docs/FEATURES.md` §28、`docs/TESTING.md` §12 同步到 0.1.6。
- ZIP 与 MSI 随版本重新构建。

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
