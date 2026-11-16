# 实现细节与踩坑档案（L0 冷存储）

> 本文件是 **L0 冷存储**：存放具体实现细节、命令码、性能数字、踩坑结论。
> **不随会话注入上下文**，只在需要改某块代码时按需检索（用关键词搜索即可）。
> 铁律 / 不变量 / 指针放在 `AGENTS.md`（约定真源）与 `.workbuddy/memory/MEMORY.md`（≤3000 字）。
> 当天做了什么放在 `.workbuddy/memory/YYYY-MM-DD.md`（追加式短日志）。
> 从 MEMORY.md 外移而来（2026-11-16），内容保持原样，仅按主题重新归类。

## 1. 环境事实（实测）

- 必须用 `.venv/Scripts/python.exe`；Python 3.13.14 / Node 22 / WebView2 157。无 gh CLI；GitHub 私有 `git@github.com:tiantangliwai/magic-video.git`（SSH 免 PAT），本地占位身份。
- PotPlayer `D:\tool\DAUM\PotPlayer\PotPlayerMini64.exe`（类 `PotPlayer64`），**闭源无源码可集成**。`SendMessageW(hwnd,0x0400,码)`：0x5000 音量/0x5002 时长/0x5004 位置/0x5006 状态(-1停/1暂停/2播放)；参数 /seek /current /autoplay；运行控制靠 PostMessage 快捷键。开源内核调研见 `docs/OPTIMIZATION.md` §8：暂缓替换，首选 libmpv。
- 网络：curl 直连 bgm.tv/TMDB/Jikan 超时；GitHub/pypi 可达；HTTPS 代理随机 ProxyError → 会话复用+重试+verify=False。在线刮削可降级，离线词典为主。
- 打包 `build_dist.py` 出 ZIP+MSI：PyInstaller 6.22.3 / pywebview 6.2.1 / pythonnet 3.2.1；WiX 3.14.1，MSI 字符串须 ASCII。两坑：COLLECT 清旧 pyi-dist 被 safe-delete 拦→先 mv 成 `Magic Video.prev`；main.py 必须 `private_mode=True`。
- 本机 Everything 以 `-svc` 跑，es.exe 连不上 IPC（Error 8），`-ipc*`/`-instance` 无效，http_server_enabled=0 → 真全盘需用户启用 HTTP；不改 ini、不重启服务。核心引擎闭源，ES 只是 MIT CLI，Lite 不支持。

## 2. 架构与数据细节

- **数据目录 `D:\magicvideo`**；`MAGIC_VIDEO_DATA_DIR` > `portable.txt` > 默认。旧 `%APPDATA%\Magic Video` 已 backup 迁移。
- SQLite(WAL) **9 表**；主窗口与守护独立进程只通过库通信。`init_db()` 幂等、`_migrate()` 只 ALTER ADD COLUMN；新默认键 INSERT OR IGNORE。`get_setting(key)` **不传空 default**（否则挡掉 DEFAULTS 回退）。`get_conn()` 的 WAL 设置按库路径只做一次。
- 入口 run.py/daemon.py/cli.py/start.bat；调试 dev.bat、`./start --local`。前端 Ctrl+R、Python 关窗重跑，别拿已装 exe 验证源码。
- 中文匹配 `metadata.py`+`aliases_builtin.json`(98部)：词典→name_alias→Bangumi 在线（短超时降级），当场写 title_cn；手改 source=user 优先。
- `Api.get_media_files_page()` 是扫描/媒体库唯一源（page_size<=0 全量、`_MEDIA_SORTS` 白名单、LIKE ESCAPE）；library_tree/get_media_tree/get_poster_wall 共用 `_media_filters()`。
- 单实例 `single_instance.py`；`Api._window` 私有；`webview.start(gui="edgechromium", private_mode=True)` 不传 background_color；`potplayer.send()` 用 `SendMessageTimeoutW(SMTO_ABORTIFHUNG,300ms)`。
- 主题 settings.theme + `html[data-theme=dark]`；原生标题栏 `theme_native.py` DWM(20/19)。内嵌 `embedded_player.py` 纯 ctypes SetParent（不碰 window.native），失败降级外部；HWND 写 `settings.embed_hwnd`；退出 finally detach()。库外文件 status=external。
- 守护：`daemon_auto_start` 默认1，create_window()+init() 双幂等，**Api.__init__ 绝不拉起进程**。关开关会真杀守护→用例必须 mock 进程。
- 三表无外键：watched_list 是导入快照（importer.commit 唯一 INSERT），播放只写 watch_progress/watch_sessions。**同步必须按 (title,季) 聚合成一条区间行**（12集→1行），先合并旧 `source='playback'` 每集一条（match_by=user 优先）；禁止每集 commit（判重键含集号）。清洗只改 title/title_cn/match_by、保 raw_text/季集/note。
- completed 只增不减；`dismissed_at` 是继续观看软删。`get_history()` 按作品标题分组只留最新会话，无标题按 file_id/-id；只收拢展示、旧会话零删；禁用按 file_id 分组。
- 人工清理 `file_ops.py`：`SHFileOperationW(FO_DELETE|FOF_ALLOWUNDO)` 进回收站，拒相对路径/非视频/符号链接/系统目录，失败不降级永久删，单次上限300；`delete_history_session` 是唯一 `DELETE FROM watch_sessions`；DB 零 DELETE 只 UPDATE deleted_at。
- 关键词 `textmatch.py`：`normalize()` 先小写再去标点；行首编号必须跟空格。
- 追剧 `watchlist.py`：763 条、`sort_order=1..763`，原文 `D:\magicvideo\watchlist_source.txt`。两栏互转、↑↓、⇈ `move_top()` 设为同栏 min-1（不重排不跨栏）、行内改名；匹配首个命中即停，短英文词独立单词。`match_files()`+`media_index()` 共用；未明确不得重灌（会抹掉调序）。

## 3. 视频黑名单（blacklist.py，细节）

- `video_blacklist`(38词) + `blacklist_enabled`(默认1)。切分 `[,，、;；|\r\n]`、小写去重；名称包含、只看 `filename/title_guess/title_cn`（不看 path）。
- **两段式匹配**：SQL LIKE 粗筛 + Python 归一化精筛（`normalize_name` = NFKC + 去 `- _ . · 空格・` + 异体表 `_VARIANTS`：日军 `亜`/繁 `亞` → `亚`，否则「黑名单有三上悠亚、剧集墙仍有三上悠亜」永远不命中；只收字形一一对应的，不做乾/干多义合并）。
- **短英文词（≤4 ASCII）必须带词边界**：左右原始字符不能是英文字母（数字/符号放行），否则 `BDrip X264`→`bdripx264` 撞 `ipx`、`future` 撞 `ure`；边界必须同时下推到 SQL（`GLOB '*ipx*' AND NOT GLOB '*[a-z]ipx*' AND NOT GLOB '*ipx[a-z]*'`），SQL 删了行 Python 就放不回来。
- 禁止把多层 replace 塞进 WHERE（2100 行多 110ms）。目录名派生的分组标题 + 季子节点标题都要 `_prune_blacklisted_groups()` 二次过滤。进程快照 `_STATE` 键 =（库路径, `db.write_count()`），禁止每次 hit() 都 get_setting()（190 分组耗 2 秒）。只隐藏不删行，关闭全恢复。

## 4. 封面与搜索（细节）

- 封面存储 `<数据目录>/covers/` 标题哈希；`cover_data_uris()` 转 data URI。两层缓存：后端 `_COVER_URI_CACHE` 按 `mtime_ns+size` 失效，前端 `coverUriCache`+`coverUris()` 只请求缺失；禁止各视图各写 `call('cover_data_uris')`。封面墙两段式渲染，`renderLibrary` 先骨架 + 目录 8s 缓存。
- 抓取 `covers.py`：AGE `agedm.io/search?query=` / Seeduck `seeduck.cc/s/<kw>/` 实测 200。三条硬约束：① 只取 `data-original`（百度代理 `src=` 解 `cdn.aqdstatic.com:966`），`src` 是共用占位图；② `_blocks()` 用 finditer+末尾兜底不丢最后块；③ `exact_pick()`→`strict_pick()`(0.90+季/续作后缀)，禁止 0.62 模糊凑数。Seeduck 用 `<h2>` 短标题。真实网络 5/5 命中。
- 人工封面：`coverDialog()` 支持 Ctrl+V/本地文件/网址；`validate_image_bytes()` 魔数+512B~3MB；`source='manual'` 优先，`is_manual()` 按缓存文件名哈希；`force=True` 也不覆盖；`clear_cover()` 唯一撤销。
- 网页搜索 `websearch.py`：`search()` 并行抓 AGE + Seeduck，解析复用封面那套硬约束（只认 `data-original`、Seeduck `<h2>`），合并后带 `source` 标记，单站失败降级不影响另一站，45s 缓存 + 8s 预算。不做内嵌浏览器，点开走 `Api.open_url()`（只放行 http/https，`webbrowser.open` 不拼 shell）。前端 nav/VIEW_META/RENDERERS 加 `websearch`（SVG 13 个）。实测「葬送的芙莉莲」4 条 /「进击的巨人」19 条。
- 全盘快搜 `everything.py`：es.exe→HTTP→SQL 三级降级，library 降级是正常设计。`es_exe()` 空串回退 `DEFAULT_ES_PATH`；官方 es.exe 在 `D:\tool\Everything\`；支持 Basic Auth；Error 8 → `-svc` 会话隔离提示走 HTTP；`open_app()` 后端固定路径 os.startfile。

## 5. UI 与交互（细节）

- 看过的列表季数双击：`set_watched_season()` 只 UPDATE season_no，支持 `2`/`S3`/`第4季`，留空清，负数/超 999 拒绝；前端 `watchedEditField` 分支；不得复用 `update_watched`。
- 分组/视图：`parse_filename` 认 E01；`_folder_label` 逐段去噪（识别 `1.36G`）；无季文件直挂 series.files，禁止「未标注季」；`build_tree(labels=)` 用 `settings.work_labels_json`。四视图 cards/series/folder/flat（媒体库默认 cards、扫描默认 folder）；看过的列表默认平铺表格。设置页 `.settings-actions` sticky；nav/VIEW_META/RENDERERS 键集合一致。
- 看过的列表→打开文件夹：`watched_folders()` 复用 media_index()+match_files()；`open_watched_folder(id)` 校验 `is_safe_directory()`，os.startfile。前端只传 id，禁拼 explorer；`_existing_folder()` 上退≤2层、不跨扫描根；批量同步只建一次索引。
- 播放控制条：顶栏 `#playerBar` 常驻（上下集/播放暂停/停止/倍速/全屏），`syncPlayerBar()` 按 PotPlayer 运行或 currentPlaying 显隐；`get_player_status().file_id` 回填 currentPlaying；独立窗口全屏投递按键。内嵌舞台另有底部控制条（HTML 层，不被子窗口盖住）。
- **播放指令通道 `potplayer.control()`**：播放类动作优先 `WM_COMMAND(0x111)`（`play_pause=10014`/`play=20001`/`pause=20000`/`stop=20002`/`prev=10123`/`next=10124`）→ `POT_SET_PLAY_STATUS(0x5007)` → 投按键，逐条发 + 每次 `POT_GET_PLAY_STATUS(0x5006)` 复查；禁止同时发（一次点击连切三次，画面看着没变）。倍速/全屏走 `POT_SEND_VIRTUAL_KEY(0x5010)`（免焦点）。返回带 `via`。实测 `via=command:10014` 生效。

## 6. 性能（实测数字）

- 媒体库/剧集墙：`api._MEDIA_CACHE` 20s，键 = `_db_signature()`（库路径 + `db.write_count()` + 行数/MAX(id)/SUM(LENGTH)/软删数），禁止用文件 mtime（守护每 3 秒写进度，mtime 永远变）。三处固定热点：`library_tree._is_tech_token` 预编译正则（原来每次 `re.escape()` 冲掉 re 缓存）、`covers.resolve_cover_many()` 批量（原来逐张重建索引 + stat）、`db.get_conn()` 的 WAL 按库路径只设一次。实测剧集墙 1105→257ms（缓存 7ms）、媒体库树 511→177ms（6ms）。
- 匹配索引缓存键不含 `db.write_count()`（守护每3秒写进度，带上等于每3秒重算200ms）；`watched_list` 指纹进 `_data_fingerprint()`。

## 7. 测试与版本（细节）

- `run_tests.py` + tests/ 808 个 unittest（test_websearch 20 / test_poster_watched 14 / test_blacklist +17 / test_player_controls +7 / test_concurrency 10 / test_listimport 21）。IsolatedDbTestCase 临时 SQLite + mock search_online，不碰真实库/网络，`reset_module_state()` 清 `api._MEDIA_CACHE` / `websearch.clear_cache()` / `blacklist.invalidate()` / `metadata._LAST_CALL` / `metadata._PAUSED_UNTIL`（限速状态跨用例泄漏会让"该发包却没发包"）。只统计总数用 `TestLoader().discover()`，不算跑全量。快照 `D:\magicvideo\test_last.json`。
- 版本真源 `app/__init__.py::__version__`；发版同步 README/AGENTS/CHANGELOG/FEATURES/TESTING/OBSIDIAN/index.html 并打 tag，`test_docs.py` 校验。

## 8. 提交与阶段历史（按需查，非当前状态）

- 已发版 v0.1.5（2026-11-15，提交 `3d6f980`，tag 已推送）：一天内 27 个本地提交集中发布，`master` + `v0.1.5` 已推到 `origin`（`b38601a..3d6f980`），用例 173 → 709。产物 `dist/MagicVideo-0.1.5-win64-portable.zip` 22.1 MB / `.msi` 22.2 MB（`exe --selftest` ok=True version=0.1.5）。P0 `v0.1.0`~`v0.1.5`（ZIP+MSI）。
- 最新本地提交 `6546c1e`「安装包按用户选的路径安装，并在升级时继承上次路径」：8 文件，未推送。根因是 `installer/product.wxs` 的安装目录 Id 写成 `AppInstallFolder`（含小写 → MSI 私有属性，UI 序列选的路径传不到执行序列，永远装默认目录）；改为 `APPFOLDER` 并标 `Secure="yes"`（本机安装提权，`Privileged=1`，只有 SecureCustomProperties 才跨序列）；`HKCU\Software\Magic Video\InstallPath` 记路径、`RegistrySearch` 回读；`ARPINSTALLLOCATION` 用 Type 51 动作。验证脚本 `installer/verify_install_flow.py`、查表工具 `installer/inspect_msi.py`。打包测试 19 → 26，增量 69/69。
- v0.1.5 发版提交 `3d6f980`「chore(release)」：7 文件，版本号/缓存串/CHANGELOG/README/TESTING/OBSIDIAN/AGENTS 全同步。
- 近期各轮增量均全绿：`789ee03` 剧集墙看过标记（459/459）、`804988c` 返回入口（429/429）、`3f64db0` 黑名单归一化（379/379）、`56c6b2e` 播放指令通道（686/686，改 tests/base.py 命中全量白名单③）。
- v0.1.6 P0 阶段 6 个本地提交（三阈值可配置 / JSON 备份导出 / 等），P1 阶段 6 个本地提交：`71cd5b8` 刮削限速、`6714054` 文件名作品 ID 直查、`9a19bb8` 导入待确认区、`810f384` 路径失效重定位、`fb37d21` 清单三段式对齐（另有 P1-3 并发提交在 P0 尾部）。版本号仍 0.1.5，未发版、未推送。
- 下一版优先（P2）：时长统计页、按季海报墙 + 继续观看置顶、多版本合并、观看日历/标签、倍速步进 + 快搜提示、安装器真机验证。

## 9. v0.1.6 P1 阶段实现细节（并发 / 刮削 / 导入对齐）

### 9.1 SQLite 并发加固（P1-3）

- **只对锁竞争重试，不引入写队列**。`db._BUSY_HINTS = ("locked", "busy", "readonly")`，`_RETRY_DELAYS = (0.05, 0.12, 0.30, 0.60)`；`db.is_busy(exc)` 公开给上层判断。`rows/row/execute/executemany/scalar/init_db` 全部包进 `_retry`，公共签名零改动。
- `executemany` 必须先 `payload = list(seq)` 物化再重试 —— 生成器第一次迭代后就空了，重试会插 0 行（告警式静默错误）。
- `session(immediate=False)` 新增 `immediate=True`：此时 `conn.isolation_level = None` + 显式 `BEGIN IMMEDIATE`。**原因**：默认 DEFERRED 事务在"先读后写"时会 `SQLITE_BUSY_SNAPSHOT`，这个错误**不吃 `busy_timeout`**，重试也没用，只能让事务一开始就拿写锁串行化。守护的三条写路径（`_open_session` / `_close_session` / `_tick`）各合成单个 `immediate` 事务；`_close_session` 的"先读 completed 再写续播点"因此变成原子。
- `get_conn` 顺序修正：`busy_timeout` **要提前到 `journal_mode` 之前**；并先 `PRAGMA journal_mode` 读一次，已经是 wal 就不再设。多进程首次同时打开时，双方都执行 `PRAGMA journal_mode=WAL` 会抢 `-shm` 映射，输的一方拿到 `attempt to write a readonly database` —— 这个 readonly 是**暂时**状态，所以 `_BUSY_HINTS` 里带上 `"readonly"` 让它进重试。
- 加 `PRAGMA synchronous=NORMAL`（WAL 下安全且快），新增 `db.checkpoint(mode="TRUNCATE")`。
- `_create_schema()` 拆出：建表放在显式事务外（DDL 自带隐式提交，塞进 `BEGIN IMMEDIATE` 反而会报"cannot start a transaction within a transaction"），迁移 + 默认设置放在 `BEGIN IMMEDIATE` 内。
- `_register_external` 改 `INSERT OR IGNORE` 再查回 id —— 原来撞 `UNIQUE(path)` 直接吞掉，等于静默丢进度。
- 守护 `_loop` 连续 3 轮 `db.is_busy` 就 `_emit("db_busy")` 上报前端，不做自动重启。
- `backup.export_to_json` 用 `conn.isolation_level = None` + 显式 `BEGIN`/`COMMIT` 读**单一 WAL 快照**，导出完 `db.checkpoint()`；`restore_from_json` 用 `session(immediate=True)` + `PRAGMA busy_timeout=30000`，恢复期停写用**直接 `UPDATE settings SET value=?`**，不能用 `set_setting`（后者刷新 `updated_at`，roundtrip 用例会比对时间戳而变红）。
- 实测：4 进程 × 40 次自增 = 160 无丢失；`integrity_check` ok。`tests/test_concurrency.py` 用子进程脚本 + `MAGIC_VIDEO_DATA_DIR` 指向临时库。

### 9.2 刮削限速与三态负缓存（P1-5）

- 新增设置：`bangumi_rate_ms=250`（请求间隔）、`bangumi_backoff_sec=120`（429/503 退避窗口）、`online_negative_ttl_sec=21600`（未命中负缓存 6h）、`online_cache_ttl_days=0`（0 = 命中缓存永久复用）、`title_lang_pref="cn"`。
- **429/503 只暂停退避窗口，不做永久熔断**。旧版一次限流就 `_online_disabled = True` 停到用户改设置，体验很差；现在 `_PAUSED_UNTIL` 到点自动恢复。**断网/超时仍走整批快速降级**（`reset_online()` 的 `force=False` 语义），不逐个等超时。
- 三态负缓存 `_cache_lookup(title) -> (seen, payload)`：`seen=False` 从没查过 → 联网；`seen=True, payload=None` → 明确查过没命中，**别再发包**；`seen=True, payload=dict` → 直接复用。**不能**用"返回 None 表示 miss"的两态设计 —— 那等于 miss 永远不缓存。
- 表 `scrape_cache(raw_title PK, status, bgm_id, title_cn, title_orig, season_no, error, hits, created_at, expires_at)` + 索引 `ix_scrape_expires`。**可重建，明确不进 `backup.TABLES`**。
- `api.save_settings` 白名单加上述 5 键；限速类键变更时也调 `metadata.reset_online()`，否则要继续吃旧退避。
- `rematch_all(force=True)` 的强制重刮必须带 `COALESCE(match_by,'') <> 'user'` 条件，人工修正不被覆盖。

### 9.3 文件名作品 ID 直查 + 稳定 ID 层（P1-4）

- `ID_TAG_RE = re.compile(r"[\[{(]\s*(tmdb|tvdb)(?:id)?\s*[-_:= ]?\s*(\d{0,9})\s*[\]})]", re.I)`，认 `{tmdb-123}` / `[tvdbid-456]` / `{tvdb=789}` 三种写法。
- **独立正则，不塞进 `BRACKET_RE`**：`{}` 里有大量标题装饰（`{简繁}`、`{招募}`），并进去会误吞标题。清洗前先 `id_clean = ID_TAG_RE.sub(" ", stem)` 再走原有清洗链。
- `media_files` 加 `tmdb_id` / `tvdb_id`，`subjects` 加 `tvdb_id`；`_migrate()` 的 `additions` 同步扩（含 `partial_hash`、`missing_since`）。
- 老库惰性补 ID：`scan` 时只填空、不覆盖已有。匹配优先级 `by="id"` > 标题；`apply_id_groups()` 让同 ID 的组共享标题，**UPDATE 必须带 `AND EXISTS (SELECT 1 FROM media_files src WHERE ... title_cn 非空)`**，否则"全组都没匹配"也会白写一遍，rowcount 虚高让用例断言失败。
- ID 只做辅助匹配，**路径仍是主键**。

### 9.4 清单三段式对齐（P1-2，`app/listimport.py`）

- 格式：MAL XML（`<anime><entry>`）、Bangumi Takeout JSON、以及通用 JSON 列表。`MAX_FILE_BYTES = 20MB`，`detect_format()` 先嗅探再解析。
- 三段式：**解析 → diff（只读差异报告）→ commit（带 skip 选项）**。中间一定有一份可看、可跳过的报告，不做"导入即写库"。
- 统一 record 结构 + `max` 语义合并（本地 12 集 / 导入 14 集 → 14，不回退）；`completed` 只增不减。
- 成功判定沿用双条件：**文件命中 + 标题校验**（`_title_verifies`）；`_align_files` 对齐时不许直接用 `watchlist.match_files()` 的结论。
- 没匹配上的行写 `status='pending_review'` 进「待确认区」，`skip_unmatched=True` 时计入 `skipped` 跳过。
- **不新建 `app/import_sources/` 多文件包**（过度设计），单文件 `listimport.py` 约 500 行。
- **不改 `importer.parse_text`**（`test_importer_realworld` 严格计数），只把它的内部逻辑抽成 `preview_records(records)` 供复用。
- 前端：导入页新增清单文件区块（`choose-import-file` / `preview-list` / `commit-list`），`#pendingBlock` / `#diffBlock` 两个渲染块。

### 9.5 导入待确认区（P1-6）与路径失效重定位（P1-1）

- `importer.commit(records, source, skip_unmatched)` 返回加 `pending`；看过的列表给 `pending_review` 行加徽章，前端支持批量标记 + `change` 事件委托。
- 路径重定位：`scanner.diagnose_library()` / `preview_relocate()` / `apply_relocate()` / `dismiss_missing()`；`media_files.missing_since` 记首次发现缺失的时间。
- 目录不可达时 `_mark_missing(directory_id)` 并返回 `missing_dirs` 计数，**不删行**；`_under_prefix` 只允许同前缀下平移，禁止跨根乱认。
- **严格遵守 `tests/test_manual_cleanup.py` 的守护：`api.py` 内不得出现 `DELETE FROM media_files`**。清理永远只能是"标缺失 + 用户确认后软删（`deleted_at`）"。
- 播放一个 `missing_since` 非空的条目时，`_play_target` 给出指向"路径重定位"的针对性报错，而不是笼统的"文件不存在"。

### 9.6 列表展示标题（P1 附带）

- `api._display_title_expr()` 生成一段 SQL 表达式，按 `title_lang_pref`（`cn` / `orig`）在 `title_cn` 与 `title_orig` 间选择并回退，**10 处列表 SQL 统一改用它**（注意有两种前缀形态：`SELECT f.*,` 与 `f.status, f.deleted_at,`，改的时候要一一核对）。
- 前端设置页新增"列表展示标题"下拉。
