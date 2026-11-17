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

- `run_tests.py` + tests/ 863 个 unittest（test_websearch 20 / test_poster_watched 14 / test_blacklist +17 / test_player_controls +7 / test_concurrency 10 / test_listimport 21 / test_cache_keys 7 / test_stats 25）。IsolatedDbTestCase 临时 SQLite + mock search_online，不碰真实库/网络，`reset_module_state()` 清 `api._MEDIA_CACHE` / `websearch.clear_cache()` / `blacklist.invalidate()` / `metadata._LAST_CALL` / `metadata._PAUSED_UNTIL`（限速状态跨用例泄漏会让"该发包却没发包"）。只统计总数用 `TestLoader().discover()`，不算跑全量。快照 `D:\magicvideo\test_last.json`。
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

## 10. v0.1.6 P2 阶段实现细节（统计 / 海报墙 / 多版本 / 标注）

### 10.1 看片统计（P2-1）

- `Api.get_watch_stats(days)` 全部在 Python 里聚合，不写复杂 SQL：先按 `started_at >= since` 取会话，
  再用 `blacklist.filter_rows` 过一遍（黑名单判定是归一化的，放 SQL 里写不进去），最后归日 / 归作品。
- **为什么不用 `GROUP BY substr(started_at,1,10)`**：会话量本来就小（个人使用，一年也就几千条），
  而黑名单过滤必须在 Python 侧做；先过滤再聚合，口径只有一处，不会出现「页面总数与明细对不上」。
- `daily` 一定是**连续 N 天**（含 0 值的那天），前端柱状图不用自己补空洞。
- 统计口径随返回值下发（`note`），页面直接展示。口径改了就改那一句，别在文档和 UI 各写一版。
- 完成率取 `watch_progress` 的 `completed/total`，与「有效时长」是两套数据，不要混算。

### 10.2 按季海报墙 + 继续观看行（P2-2）

- `get_poster_wall` 现在同时返回 `cards`（按作品）与 `season_cards`（按季）。**cards 的语义一点没动**，
  既有调用方与测试不受影响；前端只做「哪个数组」的切换。
- `season_cards` 复用了作品层的 `cover_titles` 与 `watched_listed` 判定，所以同一部作品的各季
  角标一致、封面一致（不会一季有图一季空白）。
- 季节点需要季号：`library_tree._finalize` 里给 `kind == "season"` 的节点补 `season_no`，
  因为内部字段 `_season` 会在 finalize 末尾被清掉。
- `get_continue_row` 在 Python 里按 `display_title` 去重（同一作品多集只留 last_played_at 最新的那条），
  不做 SQL 窗口函数——SQLite 版本参差，而且黑名单过滤同样在 Python 侧。
- 继续观看行只取 `completed = 0 AND position_ms > 0 AND dismissed_at IS NULL AND deleted_at 为空`。

### 10.3 多版本合并（P2-3）

- **只在展示层合并**。`library_tree._collapse_versions()` 按 `(tree_season, tree_episode)` 分桶，
  主条目用 `rank()` 挑：已看完 > 有进度 > 体积大 > id 小。其余版本进 `versions` 数组。
- `_apply_version_merge(group)` 必须**在作品层做一次**，再把结果按季号铺回各季节点：
  如果对作品和季各做一次，同一个 dict 会被处理两遍，`versions` 会被追加两次（这个坑踩过）。
- 副作用（有意为之）：`file_count` 从「文件数」变成「集数」。`all_done` / `removable_count` 都跟着变，
  语义更正确——「12 集全看完」不该因为多存了一份 BD 就变成「13 个文件里看完 12 个」。
- 平铺列表（`get_media_files_page`）另外挂 `versions`：`_version_index()` 一次分组查询 + 按
  `_db_signature()` 缓存，翻页不重扫。前端 `<select class="version-select">` 的 change 走
  `playSmart('id', id, false)`，与点「播放」完全同一条路径。

### 10.4 标签 / 评分 / 日历（P2-4）

- `work_meta(work_key PK, rating, tags, updated_at)`：`work_key` 就是海报卡的 `group_id`
  （`series::<key>`），与「改名覆盖」用的是同一套键，所以改完名标注不会丢。
- **与 `media_files` 无外键**：删本地文件、换盘、清缓存都不该带走用户手写的评分。
  因此它必须进 `backup.TABLES`（现为 10 张），`tests/test_backup.py` 的数量断言同步改了。
- `tags` 存成逗号分隔字符串而不是子表：个人工具，标签量级是「每部几个」，
  子表要 JOIN、要额外迁移，收益不抵成本。保存时中文逗号统一归一化。
- 日历不落表：`get_watch_calendar` 按月 `LIKE 'YYYY-MM-%'` 取会话后聚合，删会话日历立刻变。

### 10.5 倍速与快搜提示（P2-5）

- `potplayer.DEFAULT_SPEED_STEP` 0.1 → 0.25。`SPEED_STEPS` 仍保留 (0.1, 0.25, 0.5)，
  老用户存的 0.1 继续尊重——**这是配置项而不是常量，不能强行覆盖**。
- `test_player_controls` 里原来靠默认 0.1 验证「1.25 到不了」的用例，改成显式 `set_setting(0.1)`；
  另加一条「默认步进能精确命中全部预设」，两边都锁住。
- `everything.capabilities()` 增加 `degraded / degraded_reason / http_enable_steps`。
  **只读探测**：不读改 Everything 的 ini、不重启服务（项目铁律），提示里只给操作路径。

### 10.6 集数置信度（OPTIMIZATION #6 尾巴）

- `SEASON_RE` 的中文分支**必须要求「季/期」后缀**。旧写法 `(?:Season|S|第)\s*(\d{1,2})\s*(?:季|期)?`
  后缀可选，导致 `第03话` 被同时读成 season=3、episode=3，一部 12 集的番会拆成 12 个季。
- `EPISODE_PATTERNS[0]` 同理：`第` 分支也要求 `话/集/話` 后缀，否则 `第2季` 会被当成第 2 集。
- 新增 `XFORMAT_RE`（`1x01`）优先级只在 `SxxEyy` 之后、其它规则之前。
- `RANGE_EP_RE` 两个数字之间**不允许空格**：`Season 2 - 05` 里的「2 - 05」是季 + 集不是区间
  （第一版允许空格，把这类文件全打成了 low，实测才发现）。
- 置信度分三档而不是两档：`1x01` / `第01话` 是**规范写法**，没有歧义，定 medium 即可；
  真正进待确认的只有「同一串数字两种解释都成立」的 `[01]` 与 `01-02`，定 low。
- 落库两列 `episode_source` / `episode_confidence`；老库靠 `_migrate` 补列，
  首次扫描时**只填空**（`episode_source` 为空才写），用户确认过的 `user` 永不被覆盖。
- `Api.set_episode()` 校验 0~99 / 0~9999，留空表示「不区季」；写入即标 `user`+`high`。

### 10.7 站点解析单一层（补强 C-1）

- 新建 `app/sites.py`：正则、`blocks()`、`clean_title()`、`image_urls()`、`age_candidates()`、
  `age_entries()`、`seeduck_entries()`、`absolute_url()`、`search_url()`、`health()`。
- `covers.py` 用**别名转出**保留 `_blocks` / `_TITLE_RE` / `SEEDUCK_PAGES` 等旧名字：
  这些名字被多个测试直接引用，为了「重构好看」去改一堆测试是负收益。
- `covers.parse_agedm` 需要「含占位 src 的全部候选」，`websearch.parse_agedm` 只需要真实封面。
  所以 `age_entries()` 同时返回 `images`（含 `src`，历史契约）与 `covers`（只 `data-original`），
  由调用方各取所需——这是两边唯一真正的差异，其余全部共用。
- `seeduck_entries(require_image=, legacy=)`：封面抓取要求必须有图（`require_image=True`）
  且需要旧结构兜底；网页搜索允许无图（`require_image=False`）但要复用同一套正则。
- 解析空结果会 `_bump(source, 0)` 计数，`sites.health()` 可查；`websearch` 的返回里带
  `parse_health`，界面上「解析不到结果（站点可能改版）」有据可依。
- `tests/base.py::reset_module_state()` 增加 `sites.reset_health()`，否则解析计数跨用例累加。

### 10.8 缓存键守护（补强 C-2）

- `tests/test_cache_keys.py` 两路守：
  - **源码**：`_data_fingerprint` / `_db_signature` / `_cache_get` / `_cache_put` 的函数体里
    不许出现 `mtime|st_ctime|getmtime`（docstring 里解释「为什么不用」是允许的，所以要先剥 docstring）。
  - **行为**：写一条 `watch_progress` 不改变 `_data_fingerprint()`，但改变 `_db_signature()`；
    增删 `media_files` / `watched_list` 必须改变指纹。
- 这条以前只写在文档里靠人看。守护进程每 3 秒写一次进度，一旦把 mtime 放进键，缓存永远命中不了。

### 10.9 已知限制

- 多版本合并按 (季, 集) 判定，**同一集但季号缺失**（一个标 S1E01、一个完全没标季）不会被合并。
  这是有意的：缺季号时无法确认它们真是同一集，宁可不合并也别合错。
- 统计「按开始日期归日」：跨零点连看会把整段算在开始那天，不做时间切分。个人使用量级下没必要。
- 日历只按月查，跨月对比要自己翻；不做年度热力图（数据量撑不起，且收益低于复杂度）。
- 安装器真机 7 步仍未自动化：需要干净机器 / 提权 / 注册表，无法在单测里安全模拟。

---

## 11. 追剧列表的词序容错（2026-11-16，v0.1.6 后日常迭代）

### 11.1 触发场景

用户清单第一条写「会长大人是女仆」，磁盘目录是
`D:\magic\[SAIO-Raws] 会长是女仆大人Kaichou wa Maid-sama! [BD 1920x1080 HEVC-10bit OPUS]`。
追剧列表显示「未匹配到文件」，点不到任何本地剧集。

### 11.2 为什么原来的匹配必然漏

- `query_variants("会长大人是女仆")` 只产出 **1 个**变体（整串没有书名号、冒号、括号可拆，中文 run 就是它自己）。
- 那 26 个文件的 `filename` / `title_guess` 全是 `[SAIO-Raws] Kaichou wa Maid-sama! 01`，
  `title_cn` 是 **NULL** —— 中文名**只存在于 path 的目录名里**。
- `match_files` 走 `key in row["_full_hay"]`，`"会长大人是女仆"` 不是
  `"...会长是女仆大人..."` 的子串（「大人」位置不同）→ 0 命中。

### 11.3 判定口径（宁漏不错）

`textmatch.loose_title_hit(variant, haystack)` 四条**同时**成立才算同一部剧：

1. 变体**纯中文**（归一化后不含 `a-z0-9`）且至少 **4 个汉字**；
2. 变体的汉字**全部**出现在 haystack 里（不是 90%）；
3. 二字组覆盖率 ≥ **0.6**；
4. 变体的**首二字组与末二字组都必须在命中集合里**。

第 4 条是区分度的关键：词序错位通常发生在中间
（`会长`**`大人是`**`女仆` → `会长`**`是`**`女仆`**`大人`**），首尾仍保留；
「女朋友的朋友」撞上「超超超超超喜欢你的100个女朋友」时首尾二字组对不上，直接否。

### 11.4 先放宽过一次，误配 36 条

第一版只要求「字符覆盖 ≥90% + 二字组 ≥40%」。实测 762 条清单：

| 口径 | matched | 仅靠兜底命中 | 其中正确的 |
|---|---:|---:|---:|
| 改动前 | 105 | — | — |
| 字符集版（已废弃） | 142 | 37 | **1** |
| 最终版 | 106 | 1 | 1 |

字符集合对短词完全失效：`nana` 的字符集是 `{n,a}`、二字组是 `{na,an}`，
几乎任何英文目录都能满足 → 267 个文件被判成「已下载」；`overlord` / `mygo` / `rewrite`
同理。所以最终版把「纯中文 + ≥4 汉字」写成硬门槛。

### 11.5 性能：懒加载倒排 + 整批复用

657 条未匹配条目 × 2032 行全表扫 = 兜底一开就多 500+ ms。做法：

- `MediaIndex(list)` 子类带 **懒加载**的中文二字组倒排 `_anchor_map`（`gram -> [row]`，
  只索引两个字符都是汉字的组合），只有真的走到兜底才建表；
- `media_index()` 统一返回 `MediaIndex`，于是 `items()` / `_watched_list_matches()` /
  `_watched_folder_for()` 全部整批复用同一份索引，**不需要改任何调用点**；
- 锚点取 `key[:2]` 与 `key[-2:]` 两个池，**小池先试**。

实测 `watchlist.items()`：不含兜底 388 ms → 含兜底 494 ms（+106 ms，其中约 80 ms 是一次性建表）。

### 11.6 搜索词也要换

`search_keyword` 原来是命中的变体本身，兜底命中时仍是「会长大人是女仆」——
点「媒体库搜索」是 **0 条**（`get_media_files_page` 的 keyword 搜的是 filename / title_* / path，
不含这种词序）。改为 `_loose_keyword()`：取 path 里与变体用字重合最多的**连续中文 run**
（本例 `会长是女仆大人`），媒体库搜索同样一条能搜到 **26 条**。

### 11.7 测试

`tests/test_watchlist.py` 新增 10 例：

- `LooseTitleHitTests`（5）：正例命中；`nana` 不命中；2 字中文拒绝；首二字组缺失拒绝；中英混排拒绝。
- `LooseTitleMatchTests`（5）：真命中 3 集且 path 落在该目录；严格匹配仍优先（不换 keyword）；
  2 字标题不兜底；与「100个女朋友」部分重合不误配；`items()` 端到端带出 `match_count` 与搜索词。

---

## 12. 从播放记录同步：季号折算（2026-11-16 追加）

### 12.1 现象

同一部《更衣人偶坠入爱河》在看过的列表里出现三条：

| id | season_no | 区间 | 写入时间 | 来源 |
|---|---|---|---|---|
| 151 | 1 | 3-12 | 11-15 15:41 | playback |
| 157 | 2 | 1-12 | 11-15 19:59 | playback（手改过中文名） |
| 166 | **NULL** | 3-12 | 11-16 22:51 | playback ← 本次同步新插的重复行 |

`151` 与 `166` 是同一批文件的同一个区间，纯重复。

### 12.2 根因：幂等键里的季号会漂移

第 1 季那批文件的目录名是
`[Nekomoe kissaten&LoliHouse] Sono Bisque Doll wa Koi wo Suru [WebRip 1080p HEVC-10bit AAC]`，
**不带季标记**，扫描出的 `season_no` 是 NULL。而老同步留下的行写的是 1。

原实现两处都拿 `int(season_no or 0)` 当键：

- 聚合：`key = (title.lower(), season)` → NULL 变成 `0`；
- 查重：`WHERE ... AND COALESCE(season_no, 0) = ?` → 拿 `0` 去比，`151` 的 `1` 对不上。

于是判重落空 → `INSERT` 一条 `season_no=NULL` 的新行。**只要目录名没有季标记，
每点一次同步就会多一行**（旧行是 1，新行是 NULL，永远不相等）。

`3-12` 本身不是 bug：第 1 季 12 个文件里 `completed=1` 的是 **03、08、09、10、11、12**
（04~07 没有任何播放记录），区间按并集取 `min..max` 就是 3-12。

### 12.3 修法：`_season_key()` 把「无季标记」折算成第 1 季

```python
def _season_key(value) -> int:
    season = int(value or 0)   # 容错字符串
    return season or 1         # NULL/0 -> 1
```

- 聚合键与查重条件都改用折算后的值；查重 SQL 写成
  `COALESCE(NULLIF(season_no, 0), 1) = ?`（顺带兼容库里可能的 0）。
- **入库仍写原值**（`season or None`），所以界面上该行还是「不显示季」，不篡改数据。
- 真正的第 2 季 `season_no=2` 不参与折算，`157` 不会被并进第 1 季。
- `_merge_playback_duplicates()` 的分组键同步改成 `_season_key`，于是**已存在的
  重复行在下次同步时自动合并**——实测真实库 `merged=1`，`166` 被删、`151` 保留，
  再跑一次 `added=0 / merged=0`。

### 12.4 已知取舍

折算的前提是「目录名没季标记 = 第 1 季」。若某部剧只有第 2 季的文件、且目录名恰好
也不带季标记，它会被当成第 1 季与既有第 1 季行合并。因为标题必须相同才走到这一步，
影响面限于「同一部作品的季归属」，且现有数据里没有这种样本，暂不加额外判据。

### 12.5 另一个未处理的数据现象

同作品的第 2 季存在两套文件：`[CheeseAni] ... Season 2 [13]..[24]`（`episode_no` 仍是
NULL，属老扫描结果，`[13]` 这种方括号集数在新口径里是 low 置信度）与
`My.DressUp.Darling.S02E01..E12`（已正常解析）。前者一旦有播放记录，会以「无集号」
进入同步、生成区间为 NULL 的整部行。本次未改，重扫后可落进「待确认集数」。

---

## 13. 改名传播与发布组脏名防护（2026-11-17 追加）

### 13.1 现象

「看过的列表」里把 `My DressUp Darling KKTV AAC2 0 -BlackTV` 改成
「更衣人偶坠入爱河 第二季」后，历史记录仍显示原名。

### 13.2 三处数据源，只改了一处

| 位置 | 标题来源 | 改名是否生效 |
|---|---|---|
| 看过的列表 | `watched_list.title` / `title_cn` | 是（`UPDATE watched_list`） |
| 历史记录 | `media_files.title_cn` > `title_guess` | **否** |
| 剧集墙 / 媒体库 | 同上（树分组 label） | **否** |

两张表无外键，`update_watched` 从来只写 `watched_list`，所以历史记录停在旧名字上。

### 13.3 传播实现 `Api._sync_media_titles(row, new_name)`

- 定位键用**改名前的** `title_cn` / `title`，不是新名字：同步生成的 watched 行标题
  取自 `COALESCE(title_cn, title_guess)`，旧值必然与媒体行一致；新名字（「第二季」
  这类）在媒体库里不存在，用它去找是 0 条。
- 季号用与 `importer._season_key` 相同的折算（NULL/0 → 1），**必须相等才改**。
  第 1 季与第 2 季常共用同一中文名，不按季过滤会把另一季的文件名一起改掉。
- 命中数 > 300 直接放弃：宁可不同步，也不能一次改掉半个库。
- 写回 `match_by='user'`：`metadata.apply_to_media(force=True)` 的 where 里带
  `COALESCE(match_by,'') <> 'user'`，这样强制重刮也不会覆盖用户改名。
- 整个调用包在 `try/except` 里，同步失败不能连累改名本身。

### 13.4 `match_by` 语义修正

原来 `title_cn == title` 时落 `native`。但 `apply_to_watched` 的自动补名条件是
`title_cn IS NULL OR title_cn = '' OR title_cn = title`——正好命中这种行，
用户改的名字活不过一次「外文补中文」。现改为：**只要用户主动改名且新中文名非空，
一律标 `user`**（`chat` 结论：宁可多标人工结果，也不能让改名被静默回滚）。

### 13.5 发布组脏名防护 `textmatch.looks_like_release()`

命中任一项即判为「文件名残片，不是作品名」：分辨率（`1080p`）、来源
（`WEB-DL` / `BluRay` / `BDRip`）、编码（`HEVC` / `x264` / `10bit`）、音轨
（`AAC2.0` / `FLAC` / `OPUS`）、方括号组名（`[Nekomoe kissaten&LoliHouse]`）、
`KKTV` / `BlackTV` / `CR-WebRip`。
兜底：≥5 个空格分隔的词、含纯数字、且全无汉字 → 也判脏。

作用点只有一个：`update_watched` 在写库与记词典之前过一遍。脏值按空处理，
于是既不写 `title_cn`、也不进 `name_alias`。

### 13.6 真实库修复记录（2026-11-16 夜）

备份：`D:/magicvideo/library.db.backup-20261116-fix`（1 929 216 字节）。

| 动作 | 结果 |
|---|---|
| 删 `name_alias` id=2（`更衣人偶坠入爱河 → My DressUp Darling KKTV AAC2 0 -BlackTV`，`source='user'`） | 反向映射清除 |
| `update_watched(157, '更衣人偶坠入爱河 第二季', '更衣人偶坠入爱河 第二季')` | `media_updated=12`（149–160） |
| 校验第 1 季 137–148 | 仍为「更衣人偶坠入爱河」，未被误改 |
| 历史记录 id=26 | 由发布组串变为「更衣人偶坠入爱河 第二季」 |

### 13.7 未处理的同类脏数据（等用户决定）

同一 bug 还留下了 5 条「半截中文名」，共 3 处：

- `name_alias` id 272–276：`魔王2099 → 魔王2`、`香格里拉边境 S1-2 → 香格里拉边境 S`、
  `关于我转生变成史莱姆这档事 S1- S3 → ...S S3`、`K  s1\s2 → K`、`k - 剧场版 → 剧场版`
- `watched_list` 对应 5 行的 `title_cn` 是同一批截断值
- `subjects` id 38 / 63：`... S S3`、`... S`

这几条会让人误以为作品真叫「魔王2」。清理需要先确认用户想要的正确名字，
`lookup_local('魔王2099')` 目前返回「魔王2」，是词典优先级导致的。
