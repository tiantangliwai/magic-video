# 功能规格说明书 · Magic Video v0.1.4

> 本文件定义「做完了」的判定标准。每条需求都给出可验证的验收条件，供开发、测试与后续 Agent 对照。
> 需求基线见 `AGENTS.md` 第 1 节；变更历史见 `CHANGELOG.md`。

---

## 1. 产品边界

| 维度 | 约定 |
|---|---|
| 形态 | Windows 原生独立窗口（pywebview + Edge WebView2），无浏览器地址栏 |
| 运行方式 | 主窗口按需打开；托盘守护进程常驻（可开机自启） |
| 数据 | 全部存本机 `data/library.db`，不上传、不需要账号 |
| 播放器 | 外部 PotPlayer（`D:\tool\DAUM\PotPlayer\PotPlayerMini64.exe`），本工具不内置解码 |
| 不做什么 | 不重命名/移动用户文件、不写 `.nfo` 到视频目录、不提供公网服务、不做多用户 |

---

## 2. 需求与验收标准

### R1 · 导入「看过的动漫列表」

**输入格式**

```
进击的巨人
更衣人偶坠入爱河 S2 01-12
Attack on Titan 第1季 1-25集
葬送的芙莉莲,1,1,28
Bocchi the Rock\t1\t1\t12
# 注释行
```

| 编号 | 验收条件 | 对应测试 |
|---|---|---|
| R1-1 | 每行解析出 `title / season_no / episode_from / episode_to`，兼容 `S2`、`第1季`、`第2期`、CSV/TSV、范围 `1-25`、单集 `第08话` | `test_importer.py::ParseLineTests` |
| R1-2 | `#` / `//` 注释与空行跳过；文本内重复行去重 | 同上 |
| R1-3 | 年份 `(2013)` / `2099` 不被误解析为集数 | `test_year_is_not_an_episode` |
| R1-4 | 预览时**当场**显示中文名，并标注匹配方式（离线词典 / 本地词典 / 在线 / 中文原名 / 手动指定 / 主标题匹配 / 未匹配） | `test_preview_matches_chinese_immediately` |
| R1-5 | 重复导入不产生重复记录，返回 `inserted / skipped` | `test_commit_deduplicates_and_counts` |
| R1-6 | 预览里手改中文名后，该对照永久记住，下次导入自动命中 | `test_user_edited_chinese_name_is_remembered` |
| R1-7 | 保留原始行文本与来源（manual / cli / test） | `test_commit_keeps_raw_text_and_source` |

### R2 · 播放历史与断点续播

| 编号 | 验收条件 | 对应测试 |
|---|---|---|
| R2-1 | 每 3 秒（可配）读取 PotPlayer 总时长 / 当前位置 / 播放状态，算出百分比 | `test_potplayer.py::ReadStatusTests` |
| R2-2 | 播放器未运行、无窗口标题、位置超过总时长时安全降级，不崩溃 | 同上 |
| R2-3 | 首次轮询即写进度、时长与完成判定（不能因为「位置未变」跳过） | `test_watcher.py::test_first_tick_opens_session_and_progress` |
| R2-4 | 切换剧集自动结算上一段会话，开启新会话 | `test_switching_episode_closes_previous_session` |
| R2-5 | 进度 ≥90%（可配）自动标记 `completed=1`；重看开头不回退该标记 | `test_completed_threshold_marks_watched` 等 |
| R2-6 | 直接双击 PotPlayer 打开库外文件也留历史，并即时查离线词典 | `test_external_file_is_registered_and_matched_offline` |
| R2-7 | 续播用 `/seek=hh:mm:ss /current /autoplay` 从上次位置启动；「从头播放」不传 seek | `test_potplayer.py::PlayTests`、`test_api.py::test_play_file_uses_saved_position` |
| R2-8 | 历史页按时间倒序列出作品的起止时间与最远进度；**同一作品的不同集/多次会话只显示最新一条** | `test_continue_dismiss.py::HistoryDedupTests`、`test_smoke.py::test_full_user_journey` |
| R2-9 | 「标记已看 / 标记未看 / 重置进度」可纠错，且**不删除** `watch_sessions` | `test_api.py::test_mark_watched_and_unwatched` 等 |
| R2-10 | 历史去重按作品标题（中文名 > 推断名）分组，无标题会话按 `file_id` / `-id` 独立保留；只是展示层收拢，旧会话一条不删，人工删最新一条后自动露出该作品次新一条 | `test_continue_dismiss.py::test_same_work_different_episodes_keep_only_latest` 等 |

### R3 · 目录扫描与中文名识别

| 编号 | 验收条件 | 对应测试 |
|---|---|---|
| R3-1 | 递归扫描常见视频扩展名，跳过字幕、样片、特典与系统目录 | `test_scanner.py::IgnoreRuleTests` |
| R3-2 | `SP` 短标记按词匹配，不误杀 `Spy x Family` / `Special Edition` | `test_sp_token_must_not_blacklist_regular_titles` |
| R3-3 | 从文件名提取作品名 / 季 / 集 / 年份，兼容字幕组方括号、`SxxEyy`、`第03话`、以集数结尾 | `test_scanner.py::FilenameTests` |
| R3-4 | 增量扫描：路径已存在只更新 size/mtime，不重复插入 | `test_scan_is_incremental_and_matches_chinese` |
| R3-5 | 目录不可达时跳过而非删除已有记录；单文件不可读不中断整次扫描 | `test_scan_survives_unreadable_file` |
| R3-6 | 扫描结束当场把外文名匹配成中文名并入库，**无待办队列、无定时任务** | `test_scan_is_incremental_and_matches_chinese` |
| R3-7 | 绝不修改磁盘上的视频文件名或内容 | 设计约束，代码只读 `stat` / `rglob` |

### R4 · 原生窗口与常驻

| 编号 | 验收条件 |
|---|---|
| R4-1 | 8 个页面均可渲染，无「页面加载失败」 |
| R4-2 | `window.pywebview.api` 暴露全部后端方法，前端调用无缺失 |
| R4-3 | 托盘菜单：打开主界面 / 立即扫描 / 暂停或恢复记录 / 退出 |
| R4-4 | 开机自启写 `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`，设置页可开关 |
| R4-5 | 守护进程与主窗口并发读写同一 SQLite 不报 `database is locked` |
| R4-6 | **后台记录默认自动启动**：打开主窗口即自动拉起托盘守护，不需要在左下角或总览手动点「启动」；左下角只显示状态圆点/文字 |
| R4-7 | 「设置」页可关掉 `daemon_auto_start`：关闭时立即 `stop_daemon()` 停掉当前守护，重新打开时立即补拉起或下次启动生效 |
| R4-8 | 自动启动幂等：已在运行不重复拉起；窗口侧与前端 `init()` 双调用也只拉起一个托盘图标 |
| R4-9 | `Api.__init__` 不启动任何进程；进程拉起只发生在 `create_window()` / 设置变更时的显式调用，单测可安全实例化 `Api()` |
| R4-10 | 源码版（`pythonw.exe daemon.py`）与打包版（`exe --daemon`）都能被识别、检测与停止 |
| R4-11 | 左下角状态准确：守护刚被拉起时显示「启动中…」，就绪后 1 秒内转「正在运行」；切回窗口立即校正；`refresh()` 抛错也不能阻断状态轮询注册 |
| R4-12 | 守护进程自身抢命名互斥体 `MagicVideo_Daemon_v1`，两个窗口同时 Popen 也只留一份托盘；进程识别精确到 `python(w).exe` + `daemon.py` 参数，逐进程容错，不把诊断脚本误判为守护 |
| R4-13 | 单测绝不启停本机真实守护：`save_settings()` 会立刻走 `_apply_daemon_auto_start()`，相关用例必须 mock；`DaemonAutoStartApiTests.setUp` 默认把 `psutil.process_iter` 钉成空列表、把 `Popen` 钉成报错做双保险 |

> 三个开关语义独立：`daemon_auto_start`（本次启动拉起守护，默认 1）、`autostart`（开机登录自启，默认 0）、`recording_enabled`（守护是否写历史，默认 1）。

### R5 · 版本与输出管理

| 编号 | 验收条件 | 对应测试 |
|---|---|---|
| R5-1 | `app/__init__.py` 是版本号唯一真源，格式为语义化版本 | `test_docs.py::test_version_is_semver` |
| R5-2 | README / AGENTS / CHANGELOG 都声明当前版本，CHANGELOG 有对应段落 | `test_docs.py` |
| R5-3 | 交付文档与测试入口齐备 | `test_required_documents_exist` |
| R5-4 | `.gitignore` 用 `/data/`，内置词典不被忽略 | `test_gitignore_does_not_ignore_builtin_dictionary` |
| R5-5 | 代码中无定时任务 / 待补全队列残留 | `test_no_legacy_scheduled_queue_references` |

---

## 3. 核心数据流

### 3.1 导入流

```
用户输入文本
  → importer.parse_text()          解析 + 去重（纯函数）
  → metadata.match_titles()        内置词典 → 本地已学 → 在线（短超时/整批降级）
  → importer.preview()             标注 duplicate / match_by / 可编辑中文名
  → 用户确认 / 手改
  → importer.commit()              去重入库 + remember() 记住手改对照
  → watched_list
```

### 3.2 扫描流

```
添加目录 → directories
  → scanner.scan()                 rglob + should_ignore + stat（容错）
  → parse_filename()               标题 / 季 / 集 / 年份（只读，不改名）
  → media_files                    增量插入或更新 size/mtime
  → metadata.auto_match_media()    即时中文匹配 → title_cn / match_by
```

### 3.3 播放流

```
PotPlayer 窗口 ──WM_USER──> read_status()
                              │
                    Watcher._tick()（3s）
                              ├─ resolve_media() / _register_external()
                              ├─ _open_session() → watch_sessions + watch_progress
                              ├─ 进度变化/状态变化 → UPDATE 进度 + 会话
                              └─ 切换文件/停止 → _close_session()
```

### 3.4 续播流

```
「继续观看」→ Api.play_file(file_id)
  → 读 watch_progress.position_ms
  → potplayer.play(path, position)  →  /seek=hh:mm:ss /current /autoplay
  → PotPlayer 从断点启动 → 守护继续记录
```

---

## 4. 观看状态机

```
                 mark_watched()
   [未看] ──────────────────────────────> [已看完]
     │  首次播放                              │
     ├──> [在看] ──进度≥complete_percent──> [已看完]
     │      │                                 │
     │      └── 从头播放 / 重置进度 ──────────┘
     └────────── mark_unwatched() / reset_progress() ──> [未看]（清续播点）
```

- `completed` 只增不减（守护用 `MAX(completed, ?)`），避免重看开头抹掉已看完事实。
- 显式「标记未看 / 重置进度」才把 `completed` 置 0 并清空 `position_ms` / `percent`。
- 任何纠错操作都**保留** `watch_sessions` 历史。

---

## 5. 后端 API 清单（`app/api.py`）

| 分类 | 方法 |
|---|---|
| 信息 | `get_app_info` `get_stats` `get_player_status` `get_settings` `save_settings` `set_theme` |
| 列表 | `get_continue_watching` `get_completed` `get_history` `get_watched_list` `update_watched` `delete_watched` `get_media_files` `get_media_files_page` `get_media_tree` |
| 导入 | `preview_import` `commit_import` |
| 播放 | `play_file` `play_external` `play_embedded` `play_embedded_path` `embed_status` `set_embed_player` `embed_sync` `embed_stop` `open_data_folder` |
| 封面 | `get_poster_wall` `cover_status` `start_cover_fetch` `cancel_cover_fetch` `cover_data_uris` |
| 快搜 | `everything_capabilities` `everything_search` |
| 目录 | `get_directories` `add_directory` `remove_directory` `set_directory_enabled` `choose_directory` `scan_now` `get_last_scan` |
| 词典 | `dictionary_status` `rematch_all` `import_alias_file` |
| 纠错 | `mark_watched` `mark_unwatched` `reset_progress` |
| 守护 | `daemon_running` `start_daemon` `stop_daemon` `ensure_daemon_auto_start` `attach_window` |

> 前端 `web/app.js` 通过 `call('<方法名>', ...)` 调用；新增方法必须前后端同步，并补 `tests/test_api.py`。

---

## 6. 数据库表与关键字段

| 表 | 关键字段 | 约束 |
|---|---|---|
| `settings` | key, value | 主键 key |
| `directories` | path, enabled, last_scan_at, file_count | path UNIQUE |
| `subjects` | bgm_id, title_cn, title_orig, season_no, verified | 按 bgm_id 去重 |
| `media_files` | path, filename, size, mtime, title_raw, title_guess, **title_cn**, **match_by**, season_no, episode_no, duration_ms, status | path UNIQUE；`external` 表示外部打开的占位记录 |
| `watched_list` | raw_text, title, title_cn, match_by, season_no, episode_from/to, source | 业务去重键见 `importer._key` |
| `watch_progress` | file_id, position_ms, duration_ms, percent, completed, play_count, first/last_played_at | file_id 主键，级联删除 |
| `watch_sessions` | file_id, started_at, ended_at, from_ms, to_ms, max_ms | 每次观看一行 |
| `name_alias` | raw_title, title_cn, source, verified | raw_title UNIQUE；`user`/`manual` 优先级最高 |

迁移策略：只 `ALTER TABLE ADD COLUMN`，不重建表；`_migrate()` 有旧库不丢数据的单元测试守护。

---

## 7. 配置项（`app/config.py::DEFAULTS`）

| 键 | 默认 | 说明 |
|---|---|---|
| `potplayer_path` | `D:\tool\DAUM\PotPlayer\PotPlayerMini64.exe` | 播放器可执行文件 |
| `poll_interval_sec` | 3 | 守护轮询间隔（秒） |
| `complete_percent` | 90 | 自动标记已看完的进度阈值 |
| `min_resume_percent` | 5 | 续播点下限：进度低于此百分比不生成续播（v0.1.6） |
| `ignore_seconds_at_start` | 30 | 片头忽略秒数：播放不足这么多秒不生成续播（v0.1.6） |
| `ignore_percent_at_end` | 8 | 片尾忽略百分比：剩这么少即判已看完并清续播点（v0.1.6） |
| `recording_enabled` | 1 | 托盘可暂停/恢复记录 |
| `autostart` | 0 | 开机自启（注册表 Run 项） |
| `daemon_auto_start` | 1 | 打开主窗口时自动拉起托盘守护；设置页可关。关时立即停守护，开时立即补拉起。旧库由 `init_db()` 的 `INSERT OR IGNORE` 自动补默认值 |
| `http_proxy` | 空 | 在线元数据代理 |
| `metadata_source` | `cache_first` | `cache_first` / `online_first` / `local_only` |
| `bangumi_base` | `https://api.bgm.tv` | 在线源地址 |
| `bangumi_rate_ms` | 250 | 在线刮削的最小请求间隔（毫秒），4 线程并发时同样生效（v0.1.6） |
| `bangumi_backoff_sec` | 120 | 429 / 503 后退避秒数，到期自动恢复而不是永久熔断（v0.1.6） |
| `online_negative_ttl_sec` | 21600 | 「未命中 / 请求失败」结果缓存多久再重试（秒）（v0.1.6） |
| `online_cache_ttl_days` | 0 | 已命中结果缓存天数，0 = 长期有效（v0.1.6） |
| `title_lang_pref` | `cn` | 列表展示标题偏好：`cn` 中文名优先 / `orig` 原名优先；只影响显示（v0.1.6） |
| `window_width` / `window_height` | 1180 / 780 | 主窗口尺寸 |
| `minimize_to_tray` | 1 | 关闭时最小化到托盘 |
| `theme` | `light` | 展示风格 `light` / `dark`，只走 `set_theme()`，不在 `save_settings` 白名单里 |
| `embed_player` | 1 | 是否在软件内嵌播放 PotPlayer；关闭或嵌入失败一律用独立窗口 |
| `embed_hwnd` | 0 | 运行时写入：内嵌后的 PotPlayer 窗口句柄，供守护进程跨进程回查 |
| `everything_es_path` | 空 | es.exe 路径；**留空即回退** `D:\tool\Everything\es.exe`（空串不再变成 `Path('.')`） |
| `everything_http_url` | 空 | Everything HTTP 服务地址，如 `http://127.0.0.1:8080` |
| `everything_http_user` / `everything_http_pass` | 空 | HTTP 服务的 Basic Auth；用户名为空则不发送认证 |
| `everything_app_path` | 空 | 「打开 Everything」快捷按钮用的主程序路径，留空即 `D:\tool\Everything\Everything.exe`（前端不传路径） |
| `obsidian_vault` | 空 | Obsidian vault 绝对路径；空且仅检测到 1 个 vault 时自动采用 |
| `obsidian_folder` | `Magic Video` | vault 内笔记文件夹（相对路径，禁止 `..` / 绝对路径） |
| `obsidian_auto_sync` | 0 | `cli.py test` 全绿后是否自动同步一次（默认关闭） |

---

## 8. Obsidian 知识沉淀（v0.1.3）

### 需求与验收标准

| 需求 | 验收标准 |
|---|---|
| 自动记录流程与结果 | 一次同步生成 6 篇笔记 + 当天日志；总览含版本/提交/媒体数/会话数/词典量/测试结论 |
| 结果可追溯 | `run_tests.py` 写 `data/test_last.json`；`03 自测报告` 显示最近结果与最近 15 次时间线 |
| 优化与补齐有依据 | `02 优化路线图` 从 `docs/OPTIMIZATION.md` 解析 P0–P3 全部建议；`04` 列出真实缺口与 ADR |
| 不破坏用户笔记 | 命名空间外的文件零改动；无 `managed-by` 标记的同名文件跳过；AUTO 区块外批注保留 |
| 可控 | 默认不自动同步；界面按钮 / CLI 命令显式触发；多 vault 时必须用户指定 |

### 数据流

```
run_tests.py ──> data/test_last.json ──┐
git / CHANGELOG / OPTIMIZATION / db ──┼──> app/obsidian.py::collect_snapshot()
                                      │            │
                          render_* / append_log     │
                                      v            v
   Obsidian vault/Magic Video/  <── 原子写 ──  data/obsidian_history.json
   （6 篇笔记 + 06 开发日志/）
```

### 关键接口

| 接口 | 说明 |
|---|---|
| `obsidian.detect_vaults()` | 只读解析 Obsidian 主配置，返回所有 vault 及存在/打开状态 |
| `obsidian.resolve_vault()` | 校验路径与目录边界，返回安全的 `notes_root` |
| `obsidian.sync()` | 生成/更新全部笔记，返回 `created/updated/skipped/log/roadmap_items` 报告 |
| `obsidian.status()` / `set_options()` / `open_notes_folder()` | 状态查询、配置写入、打开目录 |
| `Api.obsidian_*` | 界面桥接：`obsidian_status` / `obsidian_sync` / `obsidian_set_vault` / `obsidian_open_folder` |

> 详细契约、frontmatter 字段、事件指纹与故障排查见 `docs/OBSIDIAN.md`。

---

## 9. 全量文件列表与点击播放（v0.1.4）

### 需求与验收标准

| 需求 | 验收标准 |
|---|---|
| 扫描结果完整可见 | 「扫描目录」页列出全部扫描文件；`get_media_files_page({})` 的 `total` 等于 `media_files` 总数；界面无 400 条上限 |
| 一次显示全部 | `page_size=0` 一次返回全部行（真实库副本实测 2104 条 / `pages=1`）；界面可翻页、跳页、回到分页 |
| 搜索 | 命中文件名、中文名、解析原名、原始标题、完整路径；大小写不敏感；`%` / `_` / `\` 按字面量 |
| 筛选与排序 | 目录、匹配状态（已匹配中文 / 未匹配 / 有播放进度）可组合；排序走白名单，非法值降级为最近扫描且不执行注入 |
| 点击播放 | 文件名链接 / 缩略图 /「播放」按钮调用 `play_file`，有断点自动续播；「从头播放」忽略断点 |
| 交互稳定 | 搜索与筛选只局部刷新列表容器，输入框不丢焦点；扫描完成后可手动刷新 |

### 数据流

```
web/app.js::loadFileList()
  -> Api.get_media_files_page({keyword, page, page_size, directory_id, match_state, sort})
  -> db.rows(带 WHERE / 白名单 ORDER BY / LIMIT OFFSET) + COUNT(*)
  -> {rows, total, total_all, matched, pages, directories}
  -> fileListRows() / fileListPagerHtml()
  -> 点击 data-action="play" -> Api.play_file(id) -> potplayer.play(path, position)
```

---

## 10. 媒体库树状视图（0.1.4 日常迭代）

### 需求与验收标准

| 需求 | 验收标准 |
|---|---|
| 不再只平铺全部视频 | 媒体库默认「作品 → 季 → 集」；第一季/第二季/第三季各自成组，一部番十几集展开季后可逐集看到 |
| 视图可切换 | 同一组件提供「作品树 / 目录树 / 平铺列表」，切换后搜索、目录筛选、匹配状态筛选保持一致；平铺列表行为与 §9 完全一致 |
| 作品归并抗脏文件名 | 字幕组命名（`[组][片名][01][1080P]…`）按发布目录归并，去掉字幕组/分辨率/集数范围/季标记；≥2 个文件的目录才作为分组依据，单文件电影不误并；已匹配的干净中文名优先级最高 |
| 季 / 集可推断 | 解析字段优先，缺省时从 `Season 2`、`S2`、`第三季`、`[01]` 等补齐；无季无集的文件进「未标注季」，总数不丢 |
| 完整可见 + 不截断 | 树对当前筛选结果的**全部**文件分组（实测 2104 条），与 `page_size` 无关；节点按需展开，折叠时只渲染分组头 |
| 可直接看可播 | 节点显示集数 / 已看 / 在看与「播放下一集」（优先回到看了一半的一集）；叶子文件行复用 `fileListRows()`，点击即调 `play_file` |
| 交互稳定 | 展开/折叠只重绘列表容器且不重新查库；搜索时自动展开命中的分组，结果过多时只展开第一层 |

### 数据流

```
web/app.js::loadFileList()  (view_mode != 'flat')
  -> Api.get_media_tree({keyword, directory_id, match_state, mode})
  -> Api._media_filters()（与平铺列表共用，参数绑定 + 排序白名单）
  -> app/library_tree.py::build_tree(rows, mode, directories)
        series_tree: clean_cn_key → folder_key_of（去噪音 / 跳过通用目录）→ title_guess
                     季数 season_of / 集数 episode_of → 作品 → 季 → 文件
        directory_tree: 扫描根目录 → 子目录 → 文件（库外文件按盘符兜底）
  -> {mode, groups[节点: id/kind/label/sub/计数/next_file_id/children/files], total, group_count}
  -> treeNodeHtml() 递归渲染；tree-toggle / tree-*-all 只改 treeState.open
  -> 叶子文件行 fileListRows() -> data-action="play" -> Api.play_file(id)
```

对应测试：`tests/test_media_tree.py`（分组、季数/集数推断、脏文件名归并、筛选与无上限、目录树、已看/在看计数与续播集、播放链路、前端契约）。

---

## 11. 启动体验与单实例（0.1.4 日常迭代）

| 需求 | 验收标准 |
|---|---|
| 不卡启动 | `Api()` 只做一次 `init_db()` + 内置词典装载；`js_api` 不公开持有 pywebview Window；`create_window` 不传 `background_color`；`webview.start(..., private_mode=True)` |
| 不弹 COM 报错 | 不在非 UI 线程访问 `window.native` 的 WebView2 控件树；播放器消息用 `SendMessageTimeoutW(SMTO_ABORTIFHUNG, 300ms)` |
| 只有一个窗口 | `app/single_instance.py::guard()` 用命名互斥锁；第二实例只 `ShowWindow(SW_RESTORE)` + `SetForegroundWindow` 后退出 |
| 一键启动 | `./start` 从源码启动并连 `D:\magicvideo\library.db`（真实库）；`--local` 用调试库；打印数据目录与 `.db` 路径 |

数据流：`./start` → `run.py` → `main.create_window()` → `single_instance.guard()` → `Api()` → `webview.create_window()` → `webview.start()` →（窗口关闭）`embedded_player.cleanup()`。

测试：`tests/test_startup.py`。

---

## 12. 黑白主题（0.1.4 日常迭代）

| 需求 | 验收标准 |
|---|---|
| 只要黑 / 白两套 | `set_theme()` 只接受 `light` / `dark`，非法值直接拒绝；`style.css` 用 `html[data-theme=dark]` 覆盖 CSS 变量，组件样式不复制 |
| 设置要持久 | 存在 SQLite `settings.theme`，不是 `localStorage`（WebView2 用 `private_mode`，重启会清空浏览器存储） |
| 切换即时生效 | 前端先本地 `applyTheme()` 再异步落库，不等后端往返；右上按钮与标签同步 |
| 原生标题栏不返白 | 网页顶栏由 CSS 变量控制，但 pywebview 的 WinForms 标题栏 / 边框由 DWM 绘制，CSS 管不到。`app/theme_native.py` 用 `DwmSetWindowAttribute(DWMWA_USE_IMMERSIVE_DARK_MODE=20，旧系统回退 19)` 同步；Win11 22H2+ 额外设标题栏 / 边框 / 文字颜色；`color-scheme: dark` 让滚动条与 `<select>` 等原生控件跟随；`html { background: var(--bg) }` 铺满 WebView2 画布防露白 |
| 启动与切换都同步 | `init()` 调 `Api.sync_native_theme()`（窗口刚出现时短重试找 HWND），`set_theme()` 内直接调 `theme_native.apply()`；找不到窗口 / 非 Windows / DWM 失败一律静默降级，不影响主题切换与播放记录 |

数据流：

```
启动 -> pywebviewready -> init() -> Api.sync_native_theme() -> theme_native.apply(theme)
切换 -> set_theme() 落 SQLite -> theme_native.apply(wanted)
                              -> embedded_player.find_main_window() (PID + 标题枚举)
                              -> DwmSetWindowAttribute(hwnd, 20, dark?1:0)
```

测试：`tests/test_theme.py`（设置持久化 + 前端契约）、`tests/test_theme_native.py`（DWM 属性、旧系统回退、非 Windows/无窗口/异常降级、启动同步、CSS 契约）。本机实测：`DwmGetWindowAttribute` 读回 `dark=1`；`DWMWA_CAPTION_COLOR` 在本机返回失败，已按设计静默忽略。

---

## 13. 剧集墙、封面与全盘快搜（0.1.4 日常迭代）

### 需求与验收标准

| 需求 | 验收标准 |
|---|---|
| 参考 SeedHub / AGE动漫 的作品墙 | 「剧集墙」按作品出卡片：封面、季数、集数、已看/在看、播放下一集；无封面显示占位图，不影响播放 |
| 有对应动漫展示图 | `app/covers.py` 主源 AGE动漫搜索页（精确标题 → `data-original` 高清图）、备源 SeedHub 首页/分类页（title 属性配对）；下载到自己的 `<数据目录>/covers/`，`subjects.cover_local` 落库 |
| 后台起一次性任务 | `start_cover_fetch()` 只在用户点击时起 daemon 线程，可 `cancel_cover_fetch()`；**没有 `Timer` / 定时任务 / 开机自跑**；重复点击不会起第二个线程 |
| 不阻塞界面、不炸磁盘 | 短超时 + 单张 3 MB 上限 + Content-Type 校验 + 单部作品异常只记失败；`cover_data_uris()` 转 data URI 给 WebView2（不依赖 `file://`） |
| 抓到就能显示 | 封面按标题哈希落盘，所以 `cover_titles()` **第一优先级就是剧集墙卡片标题**（`poster_labels()` 复用 `library_tree.build_tree(rows,"series")`）；`season_key()` **保留季标记**（`… S2` / `… 第二季` / `… · 第 2 季` 统一归一为 `#s2`）+ 去尾集号，逐集分组共用一张、**不同季各存一张**；卡片标题与抓取标题仍不一致时 `resolve_cover()` 按「带季键 → 作品级键 → subjects 精确/去季/包含」回退，`get_poster_wall()` 再用分组内文件 `title_cn/title_guess` 兜底 |
| 优先抓能抓到的 | 卡片标题之后依次是导入清单中文名、媒体库中文名、其余英文标题；技术标签 / 番号编号 /「（完）」残行 / 零宽字符先过滤 |
| Everything 快搜 | `app/everything.py`：`es.exe` → Everything HTTP 服务 → **自动降级为媒体库检索**；能力探测与提示写进「设置」页 |
| 全盘快搜独立入口 | 左侧导航「全盘快搜」→ 独立视图 `quicksearch`（搜索框 / 结果 / 当前通道徽标 / 设置跳转）；剧集墙只保留封面卡片，不再内嵌搜索框；`VIEW_META` / `RENDERERS` 与导航项一一对应，由 `tests/test_quicksearch.py` 守护 |

### 数据流

```
点击「后台抓取封面」 -> Api.start_cover_fetch(limit, force)
  -> covers.start_job()  (daemon 线程, 一次性)
  -> covers.cover_titles() -> clean_candidate() -> fetch_one()
       -> 缓存命中? -> search_cover() -> download_cover() -> save_cover_record()
  -> covers.status() 供前端轮询，结束即停轮询

剧集墙渲染 -> Api.get_poster_wall() -> {cards, covers: cover_map()}
           -> Api.cover_data_uris(titles) -> data:image/...;base64
全盘快搜   -> Api.everything_search(kw) -> es.exe / HTTP / 媒体库 SQL
           -> 播放按钮 -> Api.play_embedded_path() 或 Api.play_external()
```

对应测试：`tests/test_covers.py`、`tests/test_everything.py`、`tests/test_quicksearch.py`（独立入口与导航 / 视图一致性）。本机 `D:\tool\Everything` 只有 `Everything.exe`，要启用全盘需用户补官方 `es.exe` 或在 Everything 里开 HTTP 服务。

---

## 14. PotPlayer 软件内播放（0.1.4 日常迭代）

### 需求与验收标准

| 需求 | 验收标准 |
|---|---|
| 在当前软件窗口里播放 | `app/embedded_player.py::play()` 按 PID + 标题找主窗口 HWND，用 Win32 `SetParent` 把 PotPlayer 变成子窗口，`SetWindowPos` 摆到前端 `#playerHost` 的矩形（CSS 像素 × `devicePixelRatio`） |
| 不碰 .NET 控件树 | 不访问 `window.native`；`Api._window` 保持私有；纯 `ctypes` 调 `user32`，避开非 UI 线程 `E_NOINTERFACE` |
| 失败必须降级 | 找不到主窗口 / 等不到 PotPlayer 窗口 / `SetParent` 或 `SetWindowPos` 失败 / 非 Windows，一律让 `potplayer.play()` 的结果照常在独立窗口播放，并返回 `fallback=True` + 人话原因 |
| 记录不能断 | 子窗口不再是顶层窗口，HWND 写进 `settings.embed_hwnd`；`potplayer.find_window()` 从 settings 回查并校验 `IsWindow` + 窗口类，**另一个进程**的守护照常读进度写会话 |
| 不留孤儿窗口 | 「退出内嵌播放」、切走导航页、主窗口关闭（`main.create_window` 的 `finally`）都调用 `detach()` 还原原样式 / 原位置 / 顶层父窗口，并清掉 `embed_hwnd` |
| 可随时关掉 | 右上角开关与设置页 `embed_player`（默认 1）；关闭后所有播放走原 `play_file` / `play_external` |

### 数据流

```
点击播放 -> web/app.js::playSmart() -> openPlayerStage()（先显示舞台，界面不阻塞）
        -> Api.play_embedded(id / path, rect)
        -> embedded_player.play()
             -> potplayer.play()（先保证播放开始）
             -> find_main_window() -> _wait_pot_window() -> attach()
             -> 成功: potplayer.set_embedded_hwnd() + settings.embed_hwnd
             -> 失败: detach() + 返回 fallback（PotPlayer 仍在独立窗口播放）
        -> startEmbedSync()（仅内嵌期间 700ms 重排，resize 也触发）
        -> 退出/关窗 -> Api.embed_stop() / main.py finally -> detach()
```

对应测试：`tests/test_embedded_player.py`（DPR 换算、附着/还原、失败路径、降级、状态清理）、`tests/test_potplayer.py::EmbeddedWindowLookupTests`（跨进程 HWND 回查与陈旧句柄拒绝）。

---

## 15. 界面与数据体验（0.1.4 日常迭代，11 项）

### 需求与验收标准

| # | 验收标准 |
|---|---|
| 15-1 | 默认数据目录 `D:\magicvideo`（`config.DEFAULT_DATA_DIR`）；源码版、打包版、守护、开机自启一致；`MAGIC_VIDEO_DATA_DIR` / `portable.txt` 优先级更高 |
| 15-2 | 旧 `%APPDATA%\Magic Video` 数据用 SQLite `backup()` 一致性快照迁移，旧目录保留可回滚，7 张表行数逐一对齐 |
| 15-3 | 窗口标题为软件名 `Magic Video`，网页 `document.title` 跟随当前视图；`single_instance.WINDOW_TITLE` 与 `main.create_window` 完全一致 |
| 15-4 | 媒体库「展示方式」只有「剧集展示（作品 → 季 → 集）」与「平铺列表」；旧的 `folder` 状态归一到 `series`，后端 `library_tree` 的 folder 分支保留 |
| 15-5 | 看过的列表卡片随窗口高度撑满、表格内部滚动、表头 `position: sticky`；计数移到卡片右上角 badge |
| 15-6 | 双击作品名/中文名单元格直接编辑，焦点离开整行或 Enter 自动保存；切到另一输入框/点取消不误触发 |
| 15-7 | 顶栏背景用互斥的 `html[data-theme=light/dark] .topbar` 显式规则，切主题当场重绘，无需点别处 |
| 15-8 | 关闭 PotPlayer 且窗口句柄消失时，`Watcher._close_session()` 用本次 `max_ms` 兜底写 `watch_progress`，不让断点停在 0；切回软件窗口立即刷新首页/继续观看 |
| 15-9 | 播放记录与看过的列表是两条独立数据流；「从播放记录同步」按钮幂等回补 `completed=1` 的作品，区间覆盖判重（无季标记的目录按第 1 季折算），不改手动导入行 |
| 15-10 | 「清洗脏数据」按钮幂等修正旧版解析器残留标题、删纯 `- 完` 垃圾行，保留 `raw_text`/季/集/备注与用户手改中文名 |
| 15-11 | 封面任务状态含 `current`，剧集墙工具栏显示进度条与当前作品名；轮询仍只在任务运行期存在，非常驻定时任务 |

### 数据流

```
播放 -> watcher -> watch_progress / watch_sessions
                     └─(点「从播放记录同步」)→ importer.sync_watched_from_progress()
                                              └→ watched_list(source='playback')
导入 -> importer.commit(source='manual') ─────→ watched_list(source='manual')
                     └─(点「清洗脏数据」)→ importer.clean_watched_rows()
```

对应测试：`tests/test_watched_cleanup.py`（32 个用例：解析防再犯、清洗幂等与用户改名保护、同步回补与区间判重、守护断点兜底、前端静态契约）、`tests/test_packaging.py`、`tests/test_startup.py`。隐藏窗口冒烟实测顶栏浅色 `rgb(255,255,255)` → 深色 `rgb(30,34,43)` 同步切换、表头 sticky、卡片撑满。

---

## 16. 人工清理：删记录 / 删本地文件，观看记录不自动删除（0.1.4 日常迭代）

对应需求：历史记录支持人工删除；剧集全部看完后支持人工删除本地文件；**但观看记录不要自动删除**。
实现文件：`app/file_ops.py`（回收站安全）、`app/api.py`（`delete_history_session` /
`get_delete_candidates` / `delete_local_files`）、`app/library_tree.py`（`all_done` /
`removable_count` / `file_ids`）、`app/db.py`（`media_files.deleted_at`）、
`app/scanner.py`（文件恢复后清标记）、`web/app.js`（确认框与入口）、`web/style.css`（模态框样式）。

### 需求与验收标准

| # | 验收标准 |
|---|---|
| 16-1 | 「历史记录」每行有「删除」按钮，确认后只删 `watch_sessions` 一行；`watch_progress` / `media_files` / `watched_list` 行数不变 |
| 16-2 | 后端全项目只有 `Api.delete_history_session` 一处 `DELETE FROM watch_sessions`；守护、扫描、导入、会话关闭时都没有自动清理 |
| 16-3 | 单集清理入口只在 `completed=1` 的文件上出现；整部清理入口只在 `all_done`（分组内每一集都看完）且 `removable_count>0` 时出现 |
| 16-4 | 删除前 `get_delete_candidates()` 返回权威清单（路径、大小、可否删除与原因）；整部删除时后端重新构建分组校验 id 全部属于该作品，伪造 id 整体拒绝 |
| 16-5 | 二次确认框列出全部路径、合计大小与明确危险警告，默认焦点在「取消」，Esc / 点遮罩都取消；没有任何自动或定时触发点 |
| 16-6 | 文件用 `SHFileOperationW(FO_DELETE + FOF_ALLOWUNDO)` 移入回收站；拒绝相对路径、非视频、符号链接、系统目录；接口失败只报错，绝不降级为永久删除；单次上限 300 |
| 16-7 | **数据库零 DELETE**：成功只 `UPDATE media_files.deleted_at`；因 `watch_progress` / `watch_sessions` 是 `ON DELETE CASCADE`，删行会连带清掉记录，所以绝不删行。进度、断点、会话、看过的列表全部保留 |
| 16-8 | 已移除的文件显示「本地文件已移除」badge 并隐藏清理按钮；从回收站恢复后扫描一次自动清 `deleted_at`；文件缺失时扫描只跳过、不删记录 |
| 16-9 | `play_file()` 遇到 `deleted_at` 且文件不存在时给出「本地文件已移入回收站；观看记录已保留」的明确提示 |

### 数据流

```
历史页点「删除」 ──确认──> DELETE FROM watch_sessions WHERE id=?   （仅此一行）
                          └─ watch_progress / media_files / watched_list 不动

已看完单集 / 整部点「删除本地文件」 ──确认──> get_delete_candidates()
   └─> delete_local_files() 二次校验 ──> file_ops.trash_file() → Windows 回收站
       └─> UPDATE media_files SET deleted_at=now()   （零 DELETE，观看记录全部保留）
文件从回收站恢复 + 扫描 ──> UPDATE media_files SET deleted_at=NULL
```

对应测试：`tests/test_manual_cleanup.py`（36 个用例：路径安全与永久删除防线、
历史单条删除、整剧看完判定、清单预览、伪造 id 拒绝、失败不落标记、
重复删除跳过、记录零删除、扫描恢复与保留、前端与静态契约）。
隐藏窗口真机冒烟：历史页 11 个删除按钮、继续观看 4 个单集清理按钮、
确认框默认焦点为取消且列出完整路径、卡片与树节点在 `all_done` 时渲染清理按钮。
测试中所有回收站调用均 mock，`file_ops._shell_trash` 在 `setUp` 全局钉死，绝不真删文件。

---

## 17. 追剧列表（0.1.4 日常迭代）

对应需求：增加追剧列表导入，分已下载 / 未下载两栏；顺序默认按写入顺序但支持后续调整；未下载 ↔ 已下载可互转；扫描目录中存在已下载剧集时要能匹配并跳转媒体库；剧集墙不要「播放下一集」，「查看分集」跳转要带搜索。
实现文件：`app/watchlist.py`、`app/textmatch.py`、`app/db.py`（`watchlist` 表）、`app/api.py`、`web/app.js`、`web/index.html`、`web/style.css`。

### 需求与验收标准

| # | 验收标准 |
|---|---|
| 17-1 | `已下载：` / `未下载：` 分段解析；按书写顺序写 `sort_order`，重复标题幂等去重；行首编号、书名号、剧场版、`2.5次元的诱惑` 这类数字开头标题都不被误削 |
| 17-2 | ↑↓ 调整顺序、两栏互转、行内改名、增删条目；排序连续且不产生并列 |
| 17-3 | 本地匹配用短关键词逐个尝试、首个命中即停；短英文词按独立单词匹配，防止 `Zero` 之类泛词误命中上百文件；已 `deleted_at` 的文件不算命中 |
| 17-4 | 命中的条目可就地展开列出文件并点播，或带 `search_keyword` 跳到媒体库并自动按该词筛选 |
| 17-5 | `watchlist` 与 `media_files` / `watch_progress` 无外键，删除追剧条目不影响任何播放记录 |
| 17-6 | **词序不同的同一译名也要能关联本地文件**（v0.1.6 补）：清单写「会长大人是女仆」、目录名写「会长是女仆大人」，两者子串互不包含，这类文件的 `title_cn` 又常常为空（中文名只存在于目录名里），纯包含判断会整条漏掉。兜底判定要求**纯中文 + 汉字全部出现 + 二字组覆盖 ≥60% + 首末二字组都在**，且只在常规关键词全部落空后才启用——放宽成「字符集合覆盖」会一次误配 36/37 条（`nana`、`overlord` 这类短英文词几乎能撞上任何目录） |
| 17-7 | 兜底命中时 `search_keyword` 换成**目录名里与变体用字重合最多的中文片段**：清单写法去搜媒体库是 0 条，目录写法才有全部剧集（「会长大人是女仆」0 条 / 「会长是女仆大人」26 条） |

### 数据流

```
清单原文 -> watchlist.parse_text() -> 幂等入库(sort_order=1..N)
                                   └─> textmatch.query_variants() -> media_files 逐词匹配
                                          └─ 全部落空 -> 中文二字组倒排锚点 -> loose_title_hit 严格兜底
剧集墙「查看分集」-> Api._card_search_keyword(group) -> 媒体库 keyword + 展开命中分组
   └─ 脏分组名(1.36G) -> 组内 title_cn -> 文件名括号中文片名(爱的躯壳) -> 原分组名
```

---

## 18. 后台进程管理与封面关键词优化（0.1.4 日常迭代）

对应需求：新增「后台进程管理」标签，用来启停和查看后台进程与剧集图片缓存进度；后台抓取背景图整合进去，不再在剧集墙展示；抓取失败率高，改用文件名关键字多试几次，从两个网址获取对应背景图。
实现文件：`app/covers.py`、`app/textmatch.py`、`app/api.py::process_status()`、`web/app.js`（`processes` 视图）。

### 需求与验收标准

| # | 验收标准 |
|---|---|
| 18-1 | 左侧独立「后台进程」页；剧集墙不再出现任何封面抓取控件，也不再有「播放下一集」按钮 |
| 18-2 | `process_status()` 只读、绝不启动进程；返回守护状态/PID、封面目录、任务 running/total/done/ok/failed/current、各源命中与最近失败原因 |
| 18-3 | 可开始 / 强制重抓 / 停止一次性封面任务；进度轮询只在停留于该页时存在，切走即停；无定时任务、无开机自跑 |
| 18-4 | 站点搜索不用完整长标题：`query_variants()` 依次给《书名号》内容、去季/集标记主标题、最长中文片段、英文关键词；`match_score()` 宽容排序 |
| 18-5 | AGE动漫 + SeedHub 多分类页整批共享一次抓取索引；HTTPS 代理断连重试、非 200 快速换源、单次任务总预算防卡死 |

### 数据流

```
点「开始抓取」-> covers.start_fetch() 一次性线程
   -> cover_titles()（剧集墙卡片标题优先）-> cover_key() 去季/集
   -> query_variants(title) 逐词尝试
        ├─ AGE动漫 search?query=<短词> -> data-original 高清图
        └─ SeedHub 分类页共享索引 -> 标题配对
   -> <数据目录>/covers/<标题哈希> + subjects.cover_url/cover_local
前端轮询 process_status()（仅页面停留期）-> 进度 / 当前关键词 / 失败原因
```

对应测试：`tests/test_watchlist.py`（49 个用例：解析与编号、清单顺序与状态、本地匹配与泛词防线、关键词变体、封面多轮搜索与代理重试、进程状态只读、导航/视图/按钮静态契约、脏分组搜索词回退）、`tests/test_changed_tests.py`（新增模块影响面映射）。

---

## 19. 播放控制条（0.1.4 日常迭代）

对应需求：播放/停止键居中，支持上一集和下一集按钮，调整倍速的按钮与下拉框 1.25 / 1.5 / 2，全屏按钮调整到右下角。用户同时提出「从 GitHub 下载 PotPlayer 源码彻底集成」——**经核实 PotPlayer 是 Kakao/Daum 闭源专有软件，官方从未公开源码，GitHub 上只有皮肤/插件类第三方仓库，不具备集成条件**；用户确认改为「保留 PotPlayer + 软件内控制层」，另并行调研开源内核。
实现文件：`app/potplayer.py`（按键通道与倍速计划）、`app/api.py`（`player_control` / `player_speed` / `player_bindings` / `player_adjacent`）、`web/index.html` / `web/app.js` / `web/style.css`（控制条）。

### 需求与验收标准

| # | 验收标准 |
|---|---|
| 19-1 | 控制条在 `#playerHost` **之外**的 HTML 层，播放/暂停与停止按钮居中，上一集/下一集与倍速下拉同组，全屏按钮在右下角；不会被 `SetParent` 进来的原生窗口遮挡 |
| 19-2 | 运行时控制只走动作白名单（play_pause / stop / fullscreen），用 `PostMessage(WM_KEYDOWN/WM_KEYUP)` 投到播放器 HWND；找不到窗口、非 Windows、投递失败都返回明确错误，不抛异常 |
| 19-3 | 键位与倍速步进可在设置里改；非法键名回退默认值；步进只认 0.1 / 0.25 / 0.5，脏配置不会导致连按几十次 |
| 19-4 | 倍速下拉只有 1.0 / 1.25 / 1.5 / 2.0；每次先复位到 1.0 再按 N 次加速，与当前倍速无关；步进到不了时**如实返回实际倍速**，不假装精确 |
| 19-5 | 上一集/下一集复用 `get_media_tree` 的同一套「作品 → 季 → 集」分组与 `_file_order` 排序，跨季连续，首集/末集给明确提示；播放仍由前端 `playSmart` 统一发起，断点与历史照常记录 |
| 19-6 | 内嵌模式的「全屏」只放大软件内播放区（隐藏标题条、重排画面），**不触发 PotPlayer 自身全屏**（子窗口全屏会脱离父窗口）；仅独立窗口模式才把全屏键发给播放器 |

### 数据流

```
控制条按钮 -> Api.player_control(action) -> potplayer.send_key(action)
   -> binding() 读设置(白名单 VK_CODES) -> PostMessageW(hwnd, WM_KEYDOWN/WM_KEYUP)
倍速下拉 -> Api.player_speed(1.25) -> speed_plan() 算次数 -> apply_speed()
   -> send_key(reset_speed) + N × send_key(faster) -> 返回 wanted/actual/exact
上一集/下一集 -> Api.player_adjacent(file_id, direction)
   -> get_media_tree(mode=series) 分组 -> _file_order 排序 -> 取相邻 id
   -> 前端 playSmart('id', id, true) -> play_embedded / play_file
```

对应测试：`tests/test_player_controls.py`（27 个用例：键位与设置覆盖、Win32 消息码与失败分支、倍速步进精确/不精确、调用序列、API 白名单、相邻集排序与边界、前端三文件静态契约）。所有 `PostMessageW` / `MapVirtualKeyW` 均 mock，绝不启动真实播放器。

---

## 20. 展示视图、分组修正与作品改名（0.1.4 日常迭代）

### 验收标准

| 需求 | 验收 |
|---|---|
| 看过的列表默认平铺、也展示剧集列表与图片、只显示一个作品名 | 默认**平铺表格**：一行一条、不分组、无「中文名」列；可切到卡片墙（每部作品一张封面卡片、只显示 `r.title`）；双击作品名仍可改名 |
| 媒体库也展示剧集列表 | 媒体库默认封面墙，另有剧集展示 / 目录结构 / 平铺列表，四种视图共用 `_media_filters()` |
| 扫描结果按目录结构展示 | 扫描页默认 `folder` 树（扫描根 → 子目录 → 文件），后端 `directory_tree` 已实现并有单测 |
| 设置页保存按钮固化在顶部 | `.settings-actions` `position: sticky; top: 0`，`case 'save-settings'` 与事件委托不变 |
| Anna E01–E08 是一部剧 | `parse_filename` 认 `E01`；发布目录归并后 8 集同组、集号 1–8 有序 |
| `1.36G` 应显示「爱的躯壳」 | `_folder_label` 逐段去噪 + 体积 token 识别，树 label 与卡片标题均为「爱的躯壳」 |
| 不要「未标注季」这一层 | 无季标记的文件直接挂到 series 节点的 `files`，`children` 里不再有「未标注季」 |
| 提供编辑修改名称的能力 | `Api.rename_work()` 写 `settings.work_labels_json`，树 / 剧集墙 / 整剧树三处同源；留空恢复；只改展示标签 |

### 数据流与边界

- 封面：`get_poster_wall` → `covers` 映射（标题 → 文件名）→ `cover_data_uris(titles)` → data URI。看过的列表同时用 `title` 与 `title_cn` 查缓存。**只读缓存，不启动 `covers.start_job`**。
- 改名：`rename_work(group_id, label)` 校验 `group_id` 前缀 `series::` → 读写一个 JSON 设置键 → `build_tree(..., labels=...)` 在 `series_tree` 里覆盖 `group["label"]`。分组 id 不变，因此 `_find_series_group()` 的整剧清理校验不受影响。
- 目录归并：`folder_key_of()` 从最内层非通用目录开始，逐段切分（方括号 / 常见分隔符），技术词、站点名、体积 token、字幕噪音逐段剔除，中文片名优先；一层全是噪音就继续往外层找。
- **铁律不变**：分组只用于展示，不改数据库行、不改磁盘文件名；观看记录零自动删除；用户 `source='user'` 的手改中文名不被自动匹配覆盖。

对应测试：`tests/test_media_tree.py`（14 个用例，含 Anna / 1.36G / 无季层 / 改名持久化）、`tests/test_watched_cleanup.py`（前端契约 37 个用例，含四视图、卡片墙 / 表格、sticky、改名两端接线）、`tests/test_scanner.py`（`E01` 解析由媒体库用例连带验证）。

---

## 21. 看过的列表 → 资源管理器跳转对应文件夹（0.1.4 日常迭代）

### 验收标准

| 需求 | 验收 |
|---|---|
| 能匹配到扫描目录里对应作品时，看过的列表条目可跳到该文件夹 | 卡片墙与表格两种视图共用「打开文件夹」按钮；只有 `watched_folders()` 返回了目录的行才显示 |
| 匹配不到就不要出现入口 | 未匹配行不进 `folders` 字典；`open_watched_folder` 返回「没有匹配到扫描目录里的对应作品」 |
| 跳转的是作品所在目录，不是扫描根目录概览 | 取命中文件最多的**发布目录**；文件直接放在扫描根里时才原样打开该根 |
| 目录被移走/改名也不误开整个盘 | `_existing_folder()` 最多向上退 2 层到仍存在、仍在扫描根内的一级作品目录；**回退时不会退到扫描根本身**，也不跨出扫描根 |
| 不能借这个入口打开任意路径 | 前端只传 `watched_list.id`；后端按 id 重新算目录并二次校验（绝对路径 / 真实存在 / 非系统目录黑名单 / 在 `directories` 登记的扫描根内），再用 `os.startfile` 打开 |
| 「从播放记录同步」会一次写很多条 | 同步按集写入且 `title_cn` 回退成 `title`：`watched_folders()` 整批只建一次媒体索引、`title == title_cn` 去重、一次调用内按 `(title, title_cn)` 复用结果；同步后前端重新取映射，新条目按钮当场出现 |

### 数据流与边界

- `renderWatched()` 并行取 `get_watched_list(0)` 与 `watched_folders()`；目录接口异常时 `.catch()` 降级成空字典，列表本身照常渲染。
- `Api.watched_folders()` 复用 `watchlist.media_index()` + `match_files()`（同一套 `textmatch.query_variants` 关键词优先级、首个命中即停），按 `title` → `title_cn` 依次尝试；不塞进 `get_watched_list` 的返回体，旧契约不变。真实库只读实测：134 条看过的列表里有 **8 条**能匹配到目录，其余作品本地确实没有文件（媒体库里 `进击的巨人 / 鬼灭之刃 / 刀剑神域` 等关键词命中数为 0），属于预期而非漏配。全量匹配一次约 0.16s。
- 打开动作**只读**：`os.startfile(folder)`，不拼 `explorer` 命令、不写库、不改磁盘；`startfile` 抛 `OSError` 时返回错误文案而不冒泡。
- 与「人工删除」共用 `app/file_ops.py` 的系统目录黑名单，但 `is_safe_directory()` 是**目录**校验（不检查视频后缀、要求真实存在、要求在扫描根内），和 `is_safe_video_path()` 互不复用。

- 大量同步条目的性能：`media_index()` 整批只建一次（不是每行一次）；`_watched_folder_for()` 用 `dict.fromkeys` 保序去重标题；`watched_folders()` 的 `by_title` 缓存**只在本次调用内有效**，因此改名 / 删除 / 重新扫描后下次调用自然重算，不会出现指向旧目录的陈旧按钮。同步链路：`sync_watched_from_progress()` 写完 → 前端 `case 'sync-watched-history'` 调 `renderWatched()` → 重新取 `watched_folders()`。隔离库模拟 3000 条同步记录 / 100 部作品 / 600 个媒体文件，匹配耗时 **0.09s**。

对应测试：`tests/test_watched_folders.py`（22 个用例，含目录白名单、只传 id、失效目录回退与边界、同步后整条链路、同名条目只匹配一次、不同作品不串目录、`startfile` 全程 mock、前端双视图与失败降级）；`run_changed_tests.py` 的 `web/app.js` / `app/api.py` / `app/file_ops.py` / `app/watchlist.py` 映射已连带加入 `tests.test_watched_list` 与 `tests.test_watched_folders`。

---

## 22. 封面精确抓取、人工设定封面、Everything 快捷方式、季数编辑（0.1.4 日常迭代）

对应需求（2026-11-15）：用户给出两个确切搜索 URL
`agedm.io/search?query=零之使魔` 与 `seeduck.cc/s/零之使魔/`，要求「仔细考虑能否拿到对应图片，实在不行就加一个地方让我人工把背景图粘贴进去，目前很多图片都是错的」；
另要求「看过的列表第几季像改名一样双击可输入」；以及上一轮未收口的「全盘快搜彻底集成 / 加快捷方式」。

实现文件：`app/covers.py`、`app/everything.py`、`app/api.py`、`app/config.py`、`web/app.js`、`web/style.css`。

### 22-A 官方搜索 URL 实测与错图根因

| 站点 | 实测 | 关键结构 |
|---|---|---|
| AGE动漫 | HTTP 200、无需 JS/CF | 4 个 `cata_video_item` 块；`<a title>` 是标题；`data-original` 是百度图片代理（`/gimg/app=2001&...&src=<urlencoded CDN>`）；**所有块 `src` 是同一个占位图** |
| Seeduck | HTTP 200、无需 JS/CF | `<a title=长标题 class="image"><img src=真实封面>` + 同条目 `<h2>` 干净短标题；图片是绝对 URL、无占位 |

精准命中常在靠后位置（「葬送的芙莉莲」第 2 块、「进击的巨人」第 3 块、「零之使魔」第 4 块），
旧 `_BLOCK_RE` 用 `(?=...|</main>)` 切块会丢掉最后一条，而最后一条往往正是精确匹配。

修复：
- `_BLOCK_RE` 改用 `finditer` + `\Z` 兜底，`_blocks()` 保证最后一块不丢；
- `_age_candidates()` **只取 `data-original`**，从百度代理 `src=` 参数解出原始 CDN 直连并排首位，
  彻底排除共用占位 `src`；`_GENERIC_IMG_RE` 也不再含裸 `src`；
- `_seed_pairs()` 按 `<a class="image">` 切条目，优先用 `<h2>` 短标题，旧页回退 `<a title>`；
- `exact_pick()` 精确 → 去季精确；`strict_pick()` 兜底要求 0.90 相似度且差值只能是季/续作后缀，
  **无关标题一律不凑数**；
- `search_cover()` 逐关键词请求 AGE 搜索页 → Seeduck `/s/<kw>/` 搜索页（带短 TTL 缓存）
  → 旧首页池严格兜底。`SEEDUCK_PAGES` / `seed_pairs()` 无参行为保持不变。

隔离库真实网络实测：`零之使魔 / 葬送的芙莉莲 / 进击的巨人 / 更衣人偶坠入爱河 / 间谍过家家`
**5/5 精确命中**（`hit` 与查询完全一致），全部落到 `cdn.aqdstatic.com` 直连，
5 张均为真实 JPEG、24~58 KB、体积各不相同（不再有共用占位图）。

### 22-B 人工设定封面

| # | 验收标准 |
|---|---|
| 22-B1 | 剧集墙卡片与看过的列表卡片墙都有「设定封面」；弹窗支持 **Ctrl+V 粘贴剪贴板图片**（FileReader → base64）、系统文件对话框选本地图片、或粘贴 http(s) 图片网址 |
| 22-B2 | 三种来源统一由后端 `Api.set_cover()` 读取/下载，`covers.validate_image_bytes()` 校验魔数（JPEG/PNG/WebP/GIF/BMP）与 `512B ≤ size ≤ 3MB`；拒绝相对路径、非图片后缀、非 http(s)、非法 base64 |
| 22-B3 | 落盘到 `cache_path(season_key(title))`（**键带季**：`… S2` → `… #s2`，无季标题与原键一致，存量不用迁移），写 `subjects(source='manual')`；`subject_covers()` 用 `ORDER BY (source='manual') DESC` 保证人工图优先 |
| 22-B4 | `is_manual()` 按**缓存文件名**（哈希）判定，不受标题空格/大小写影响；`fetch_one()` 连 `force=True` 也不覆盖人工图，`cover_titles()` 把人工作品排除出抓取队列 |
| 22-B5 | 弹窗「恢复自动」调 `clear_cover()` 删 manual 记录并删掉这一张缓存文件；不碰用户视频/图片文件 |
| 22-B6 | **两季可以各设一张**（2026-11-18 用户反馈「设完两季强制一致」）：键保留季，第二季设图不会覆盖第一季；季卡片解析时**自身标题排在作品名之前**（`get_poster_wall`），否则永远被作品级封面抢先 |
| 22-B7 | 没单独设过的季仍显示该作品的图（`local_cover()` 带季键未命中 → 回落作品级键），只是显示回落、不写库；「清除某一季」只删这一季的键，另一季与本季作品级图不受影响 |
| 22-B8 | 不是定时任务；`app/covers.py` 源码不含 `Timer(` / `schedule` |

### 22-C Everything A+B（彻底集成评估结论）

| 问题 | 结论 |
|---|---|
| GitHub 有源码吗 | **核心搜索引擎闭源**，无官方源码仓库；`voidtools/ES` 是 MIT 的 es.exe 命令行客户端，`everything_sdk3` 是 IPC 客户端，都只是连到运行中的 Everything 实例 |
| 能否编译进本软件 | **不能**；Lite 版还会禁用 IPC/ES/HTTP，必须完整版。真正开源可嵌入的替代（FastFind.NET 等）需自研内核 + 提权，本轮不做 |
| 随包捆绑合法吗 | es.exe / Everything.exe 为 MIT 风格许可，可随第三方分发并保留版权声明；属打包/授权层改动，需单独确认 |
| 当前降级提示 | §22 时认为是正常设计；2026-11-17 实测发现两处实现 bug（假降级 + GBK 乱码），已在 §34 修复 |

落地（用户选 A+B 一次到位）：
- 修 `es_exe()`：设置项空串回退 `DEFAULT_ES_PATH`，不再变成 `Path('.')`；
- 下载官方 `ES-1.1.0.38.x64.zip` 并把 `es.exe` 放到 `D:\tool\Everything\es.exe`；
- `http_auth()` 支持 Basic Auth，`search_http()` 传 `auth=`；新增 3 个设置键；
- `search_es()` 识别退出码 8 / `IPC not found`，给出「Everything 以 -svc 服务模式运行，请启用 HTTP」的可操作提示，降级文案区分「没装 es.exe」与「装了但连不上」；
- `Api.everything_open()` + `everything.open_app()`：路径由后端固定配置决定（前端不传参），校验绝对/`.exe`/真实存在后 `os.startfile`，不拼 shell 命令；
- 快搜页与设置页都有「打开 Everything」按钮，主程序不存在时置灰；`capabilities()` 新增 `app_path` / `installed`。

**本机环境事实**：Everything 1.4.1.1032 完整版以 `-svc`（会话 0 服务）运行，用户会话的 es.exe 连不上 IPC（Error 8，`-ipc1/2/3` 与 `-instance` 均无效），`http_server_enabled=0`。这是会话隔离，改系统服务配置/重启需用户决定，本轮不动。要真正全盘检索，请在 Everything 选项启用 HTTP 服务并填地址。

### 22-D 看过的列表「第几季」双击编辑

| # | 验收标准 |
|---|---|
| 22-D1 | 表格视图「季」单元格带 `data-edit-watched` + `data-field="season_no"`，双击变数字输入框，失焦 / 回车 / 保存按钮三条路径共用 `saveWatchedEdit()` |
| 22-D2 | `watchedEditField` 区分 `'title'` 与 `'season_no'`，改名链路（含隐藏中文名域 `watchedEditCn`）原样保留 |
| 22-D3 | `Api.set_watched_season()` 是独立入口，**只 `UPDATE season_no`**，不动 title/title_cn/raw_text/集数/match_by；支持 `2` / `S3` / `第4季`，留空、`0`、`—` 清空，负数/带符号/超 1–999 拒绝 |
| 22-D4 | 卡片墙视图不变（按作品聚合的「N 季」标签是统计值，不是单条记录） |

对应测试：`tests/test_manual_cover.py`（真实站点结构、占位图排除、CDN 解析、搜索 URL、人工三种来源、force 不覆盖、含空格标题保护、前端契约）、
`tests/test_everything_open.py`（空串回退、Error 8 提示、HTTP Auth、打开主程序白名单、前端按钮）、
`tests/test_watched_season.py`（后端 API 与前端字段分支）。
`run_changed_tests.py` 的 `app/api.py` / `app/config.py` / `app/covers.py` / `app/everything.py` 与 `FRONTEND_MODULES` 已连带加入三个新模块。

---

## 23. 同步按作品合并、视频黑名单、播放条常驻、追剧置顶、媒体库切换优化（0.1.4 日常迭代）

### 23-A 从播放记录同步：按作品合并成一条区间行

| # | 验收标准 |
|---|---|
| 23-A1 | 一部 12 集番同步后在看过的列表里**只有 1 行**：`episode_from=1`、`episode_to=12`，`source='playback'` |
| 23-A2 | 幂等：连点三次同步，第二次起 `added=0`、行数不变 |
| 23-A3 | 已存在的同作品同季行（手动导入的 `1-3`）被**扩展区间**（→ `1-4`），不新增行；整部行（区间 NULL）覆盖一切，不再插入 |
| 23-A4 | 历史遗留的 `source='playback'` 每集一条记录，点一次同步即合并成一行，返回 `merged` 计数；`match_by='user'` 的手改行优先保留 |
| 23-A5 | 不同季各自一行；手动导入行与其它来源的行一律不动；无集号文件生成区间为 NULL 的单行且只生成一次 |
| 23-A6 | 只改 `watched_list`，不动 `watch_progress` / `watch_sessions` / `media_files` |
| 23-A7 | **季号折算**：目录名没有季标记时扫描出的 `season_no` 是 NULL，与老数据里的 `1` 视为同一季（`_season_key`）。同一部剧不会因为「目录名有没有季标记」而在看过的列表里留下两行「3-12」 |
| 23-A8 | 真正的第 2 季（`season_no=2`）不参与折算，绝不与第 1 季合并；入库仍按原值存（NULL 就是不显示季） |

对应测试：`tests/test_sync_dedupe.py`（10 个用例）、`tests/test_watched_cleanup.py::SyncFromProgressTests`（含季号折算三条）；`tests/test_watched_folders.py` 的同步规模用例已按新语义更新（3 集→1 行，匹配仍只扫一次索引）。

### 23-B 视频黑名单（设置页，默认开启）

| # | 验收标准 |
|---|---|
| 23-B1 | 设置页有「启用黑名单过滤」开关（默认开启）+ 多行关键词输入框，保存走 `save_settings` 白名单的 `blacklist_enabled` / `video_blacklist` |
| 23-B2 | 关键词按逗号 / 中文逗号 / 顿号 / 分号 / 竖线 / 换行切分，trim + 小写去重；空项丢弃；LIKE 通配符 `%` `_` 被转义 |
| 23-B3 | 判定是「名称包含」且不区分大小写；只看 `filename` / `title_guess` / `title_cn`，**不看 `path`**（避免目录名连带隐藏整棵子树） |
| 23-B4 | 覆盖位置：媒体库四种视图（共用 `_media_filters`）、剧集墙、扫描结果、看过的列表、播放历史、继续观看、总览统计、追剧列表的本地匹配、全盘快搜结果 |
| 23-B5 | **只隐藏不删**：`watched_list` / `media_files` 行数不变；关闭开关后所有位置立刻恢复全部内容 |
| 23-B6 | 追剧清单条目本身保留（那是用户手写的待办），只是「本地 N 个文件」不再统计黑名单视频 |

对应测试：`tests/test_blacklist.py`（19 个用例：解析 / 命中 / SQL 片段 / 各 API 过滤 / 开关恢复 / 设置白名单 / 前端契约）。

### 23-C 播放控制条常驻顶栏

| # | 验收标准 |
|---|---|
| 23-C1 | 顶栏右侧 `#playerBar` 常驻：上一集 / 播放暂停 / 停止 / 下一集 / 倍速下拉（1.0·1.25·1.5·2.0×）/ 全屏 |
| 23-C2 | 显示条件：PotPlayer 在运行**或**已有当前播放条目；两者都没有时整条隐藏 |
| 23-C3 | 软件重启后播放器仍在播时，用 `get_player_status().file_id` 回填 `currentPlaying`，上下集按钮直接可用 |
| 23-C4 | 独立窗口模式下「全屏」把全屏键投给 PotPlayer；内嵌模式仍只放大播放区（不触发播放器自身全屏） |
| 23-C5 | 倍速下拉的 `change` 事件与内嵌控制条共用 `setPlayerSpeed()` |

### 23-D 追剧列表按钮对齐 + 一键置顶

| # | 验收标准 |
|---|---|
| 23-D1 | `watchlist.move_top(id)`：把该条 `sort_order` 设为**同栏最小值 - 1**，不重排其它行、不跨栏、不改 `downloaded` |
| 23-D2 | 已在最上面时返回 `moved=False, already_top=True`，前端提示「已经在最上面了」 |
| 23-D3 | ↑↓⇈ 使用统一的 `.btn-icon` 等宽位，所有按钮 24px 等高、整组右对齐、窄窗口自动换行 |
| 23-D4 | 未下载栏的顺序不受已下载栏置顶影响 |

对应测试：`tests/test_watchlist_top.py`（9 个用例：置顶 / 已置顶 / 跨栏隔离 / 连续置顶 / 未知 id / 状态不变 / 前端与 CSS 契约）。

### 23-E 卡片墙滚动与媒体库切换性能

| # | 验收标准 |
|---|---|
| 23-E1 | 看过的列表切到卡片墙后可正常滚动：`.watched-cards` 有独立的 `flex: 1 1 auto; min-height: 0; overflow: auto`，不被 `content-fill` 的 `flex: none` 规则冻结 |
| 23-E2 | 表格视图内部滚动 + 表头 sticky、设置页 sticky 工具条均不受影响 |
| 23-E3 | 切到媒体库先出骨架，再查目录 / 分组 / 封面；目录列表 8 秒内复用 |
| 23-E4 | 封面 data URI 双层缓存：后端按 `mtime_ns + size` 失效，前端 `coverUriCache` 只请求缺失标题；人工设定 / 恢复自动封面后清空前端缓存 |
| 23-E5 | 封面墙两段式渲染：先占位卡片、再补封面，不再等全部 base64 转换完才首帧 |

---

## 24. 播放指令通道、媒体库提速、卡片墙结构、黑名单归一化、网页搜索（0.1.4 日常迭代）

用户反馈原话（2026-11-15，五项）：「快进展厅倍速之类的按钮都没有用」「媒体库和剧集墙打开还是很慢」「看过的列表中卡片墙状态下，所有的卡片都挤成一堆，而且没有名字了」「剧集墙里还是能看到黑名单里限制名称的剧集」「增加网页搜索选项，集成 seeduck.cc 和 agedm.io 的搜索能力……如果不行就在网页搜索中的卡片中当做浏览器直接访问相应的网址」。

### 24-A 播放控制改走 PotPlayer 菜单指令

| # | 验收标准 |
|---|---|
| 24-A1 | 播放/暂停、停止使用 `WM_COMMAND(0x0111)` + 命令号（`10014` 播放暂停 / `20001` 播放 / `20000` 暂停 / `20002` 停止），**不依赖窗口焦点、不受用户自定义键位影响** |
| 24-A2 | 每次动作后读 `WM_USER 0x5006` 复查状态是否真的变化；没变化才退到 `POT_SET_PLAY_STATUS(0x5007)`，再不行退到快捷键 |
| 24-A3 | 三条通道**按需逐条发出**（不得一次全发：暂停+切换+空格会连切三次，画面看起来毫无反应） |
| 24-A4 | 已经在目标状态（例如重复点停止）直接返回 `via="already"`，不重复下发 |
| 24-A5 | 三条都没生效时返回 `ok=False` 并说明「指令已送出但状态没变」，绝不假装成功 |
| 24-A6 | 倍速与全屏走 `POT_SEND_VIRTUAL_KEY(0x5010)`（wParam=命令码、lParam=虚拟键），老版本不认时退回 `WM_KEYDOWN`/`WM_KEYUP` |
| 24-A7 | `Api.player_control` 白名单仍是 `play_pause/play/pause/stop/fullscreen`；上下集由前端换片，不走此接口 |

实测：本机 PotPlayer 正在播放时 `control("play_pause")` 返回 `{'ok': True, 'state': 1, 'via': 'command:10014'}`，旧按键注入在同一环境下静默无效。

对应测试：`tests/test_player_controls.py`（35 个用例：键位与设置覆盖、虚拟键通道优先、虚拟键被拒时退回 WM_KEYDOWN、状态复查与三条通道退让、已在目标状态、无播放器、未知动作不发消息、API 白名单、相邻集排序、前端静态契约）。

### 24-B 媒体库 / 剧集墙性能

| 场景（2100 文件 / 195 作品） | 改前 | 首次 | 缓存 |
|---|---|---|---|
| 剧集墙 `get_poster_wall` | 1105 ms | 257 ms | 7 ms |
| 媒体库剧集展示 `get_media_tree` | 511 ms | 177 ms | 6 ms |
| 平铺全量 `get_media_files_page` | 235 ms | 146 ms | 命中同键 |

根因与改法：

| # | 根因 | 改法 |
|---|---|---|
| 24-B1 | `library_tree._is_tech_token` 每次调用都 `re.escape` 拼新正则，re 的 512 条缓存被冲掉后反复 `_compile`（2020 文件 × 40 词 ≈ 8 万次，占 `build_tree` 一半以上） | 模块级预编译 `_SHORT_TECH_RES` / `_NON_WORD_RE` / `_RANGE_RE` |
| 24-B2 | `_folder_label` / `_strip_release_noise` / `norm_title` 按文件重复计算（同一目录名算上千次） | `functools.lru_cache` |
| 24-B3 | 剧集墙逐张调 `resolve_cover`：每次重建 subjects 归一化索引并逐个 `stat()` 校验文件（195 张 ≈ 220 ms） | 新增 `cover_index()` + `resolve_cover_many()`：索引与文件存在性检查各做一次 |
| 24-B4 | 批量解析的模糊兜底对每张卡重跑 80 条记录的归一化（150 张对不上的卡 ≈ 3 s） | 预计算归一化键，纯字符串比较 |
| 24-B5 | `blacklist.hit()` 每次调用都读一次 settings，每次读都新开 SQLite 连接（190 个分组 ≈ 2 s） | 词表按「库路径 + `db.write_count()`」缓存 |
| 24-B6 | 每次连接都执行 `PRAGMA journal_mode=WAL`（写库头 + 抢锁，3~5 ms/次） | WAL 按库路径只设一次 |
| 24-B7 | 切视图重复全量重算 | `get_poster_wall` / `get_media_tree` / `get_media_files_page` 结果缓存，键 = 选项 + 数据指纹（行数 / 最大 id / 文件名与中文名总长度 / 软删数 / 写库次数），写库自动失效 |

显式约束：缓存键**不得**使用数据库文件 mtime —— 守护进程每 3 秒写播放进度，mtime 一直在变，缓存会永远失效；播放进度变化不影响分组与封面，故意不纳入指纹。

对应测试：`tests/test_blacklist.py::MediaCacheTests`（命中不重算 / 写库失效 / 选项隔离 / 关黑名单失效）+ `tests/test_media_tree.py` / `tests/test_covers.py`（分组与封面语义不变）。

### 24-C 看过的列表卡片墙结构

| # | 验收标准 |
|---|---|
| 24-C1 | 卡片模式不再套 `content-fill`（改整页滚动）；表格模式仍吃满高度 + 表头 sticky |
| 24-C2 | 结构改为「外层 `.watched-cards` 普通块 + 内层 `.poster-grid`」，与剧集墙一致；卡片不得被压成一条、标题必须可见 |
| 24-C3 | 卡片上仍显示作品名、条数、季数标签、打开文件夹 / 设定封面 / 查看条目 |

对应测试：`tests/test_watched_cleanup.py`（CSS 与结构契约已按新结构更新）。

### 24-D 黑名单归一化匹配

| # | 验收标准 |
|---|---|
| 24-D1 | 两侧都去掉 `- _ . · 空格（含全角）` 再比较：词表 `FC2PPV` 能命中 `FC2-PPV-1288053`，`MIDA` 能命中 `MIDA_077` |
| 24-D2 | 归一化不影响正常片名（葬送的芙莉莲 / 更衣人偶坠入爱河 / Spy x Family 均不命中） |
| 24-D3 | **目录名派生的作品名**（文件名里没有关键词）在剧集墙卡片与媒体库分组这一层再筛一遍，这是「剧集墙还能看到黑名单」的真因之一 |
| 24-D4 | SQL 侧仍是便宜的 `LIKE` 粗筛（38 词 × 3 字段），归一化精筛放在 Python 侧；**不得**把 5 层 `replace()` 塞进 `WHERE`（实测 2100 行多 110 ms，每次搜索都卡） |
| 24-D5 | 条目总数随过滤同步扣减，避免「本页只剩 197 条」这类错位 |
| 24-D6 | **字形归一化**：词表写简体 `三上悠亚`，也能挡住日语新字体 `三上悠亜`（U+4E9C）与繁体 `三上悠亞`（U+4E9E）——三者是不同码位。先 NFKC（全角字母数字折半角）再查异体表归到规范简体，**只改比较、不改展示**。异体表只收字形一一对应的字，不做多义合并（乾/干、著/着不进来），避免误伤 |
| 24-D7 | **短英文词带词边界**：3~4 字母纯 ASCII 词要求命中位置左右原始字符都不是英文字母，数字与符号放行。`BDrip X264`（去空格后含 `ipx`）、`future`（含 `ure`）、`Return`、`Tsurezure` 不再被误隐藏；`IPX-123`、`SSIS-001`、`MIDA_077`、`@PRED733` 仍正常挡下 |
| 24-D8 | 词边界**必须同时下推到 SQL**（`lower(x) GLOB '*ipx*' AND NOT GLOB '*[a-z]ipx*' AND NOT GLOB '*ipx[a-z]*'`）：SQL 一旦删行，Python 侧复核就没机会把行放回来 |
| 24-D9 | 季子节点标题也参与分组过滤：作品目录叫「合集」、季目录才带关键词时也要隐藏 |

对应测试：`tests/test_blacklist.py`（38 个用例，含 `BlacklistNormalizationTests` 与 `BlacklistVariantTests`）。

### 24-E 网页搜索（AGE动漫 + Seeduck）

| # | 验收标准 |
|---|---|
| 24-E1 | 左侧独立入口「网页搜索」，`nav/meta/RENDERERS` 三处注册一致 |
| 24-E2 | 一个关键词并行抓两站：AGE `https://www.agedm.io/search?query=`、Seeduck `https://seeduck.cc/s/<关键词>/` |
| 24-E3 | 每张卡片**标明来源**（AGE动漫 / Seeduck），展示封面、标题、年份类型等元信息 |
| 24-E4 | AGE 只取 `data-original` 解出的原始 CDN（`src` 是所有结果共用的占位图，不得当封面）；Seeduck 用同条目 `<h2>` 短标题 |
| 24-E5 | 详情页链接一律转绝对地址并把 `http://` 升级为 `https://` |
| 24-E6 | 单站失败 / 改版只影响自己：`errors[source]` 记录原因，另一站结果照常展示，两站搜索页链接始终返回 |
| 24-E7 | 点「打开网页」走 `Api.open_url`：只放行 `http/https`，`file:` / `javascript:` / `ms-*` / 相对路径全部拒绝，不拼 shell 命令 |
| 24-E8 | 搜索结果同样过黑名单；结果缓存 45 秒，可 `use_cache=False` 绕过 |
| 24-E9 | 不支持内嵌浏览器：第三方页面不嵌入 WebView2（登录/广告/脚本冲突不可控），只在卡片上「打开网页」 |

实测（2026-11-15）：关键词「葬送的芙莉莲」1.4 s 返回 4 条（AGE 2 条 + Seeduck 2 条），封面均为真实 CDN；「进击的巨人」返回 19 条。

对应测试：`tests/test_websearch.py`（20 个用例：两站真实结构解析、占位图排除、h2 短标题、绝对 URL、两站合并与来源标记、单站失败降级、HTTP 错误上报、黑名单过滤、空关键词、缓存与绕过、`open_url` 白名单、前端四处契约）。

### 24-F 跨视图跳转的返回入口

对应需求（用户 2026-11-15）：「从剧集墙跳转到媒体库里后需要一个返回按钮，不然又要重新跳转了」。

| # | 验收标准 |
|---|---|
| 24-F1 | 带搜索词跳进媒体库（剧集墙「查看分集」、追剧 / 看过的列表「媒体库搜索」）时，媒体库顶部出现「← 返回<来源页名>」 |
| 24-F2 | 点返回直接切回来源视图，**来源页原有的筛选 / 搜索 / 展开状态不变**（只切视图，不重置 `fileListState` 之外的状态） |
| 24-F3 | **直接点左侧导航进媒体库不显示返回**：那不是「跳转」，没有上一页可回 |
| 24-F4 | 返回按钮旁边写明当前带的搜索词，用户知道自己在看哪部作品对应的结果 |
| 24-F5 | 来源视图取跳转前的 `currentView` 的 `VIEW_META` 显示名，不写死「剧集墙」（从看过的列表 / 追剧列表跳也一样能返回） |

对应测试：`tests/test_media_browser.py::FrontendFileListTests`（`viewReturn` 只在带关键词跳转时记录、`backRow()` 插入列表卡片、`goto-back` 动作先清来源再切视图、导航直进不显示）。

### 24-G 剧集墙「是否看过」标记

对应需求（用户 2026-11-15）：「剧集墙中增加是否已经看过的标记」。

两路数据合并判定，缺一路都不完整：

* **`watch_progress`**（本软件里真正播过的集）：精确到集，能算出 `已看 3/12`；
* **`watched_list`**（导入 / 同步来的「看过的列表」）：作品级。只按进度标记的话，用户导了几百部之后剧集墙几乎全是「未看」，标记就没意义了。

| # | 验收标准 |
|---|---|
| 24-G1 | 封面右上角有角标，五种状态：`✓ 已看完`（全绿）、`已看 3/12`（浅绿）、`在看`（琥珀）、`看过`（蓝）、`未看`（半透明灰） |
| 24-G2 | 优先级：整部看完 > 看过一部分 > 看了一半 > 只在看过的列表里 > 未看。**有真实进度时以进度为准**，不让列表覆盖 |
| 24-G3 | 状态由后端 `_watch_state()` 算好、以 `watch_state` 字段下发；前端 `posterStateTag()` 只挑文案与配色，**不在 JS 里重算优先级** |
| 24-G4 | 「看过的列表」判定必须同时满足：① 列表条目命中该卡片的文件；② 标题校验通过（归一化后相等，或一方包含另一方且**短侧 ≥ 4 字符**）。只满足①会误标：《斯巴达克斯》← 《斯巴达300勇士》、《内情 The inside story》← 《After Story》、英文片 ← `real真实` |
| 24-G5 | 导入 / 同步 / 清空看过的列表后标记立刻更新（指纹含 `watched_list` 行数与标题长度）；而播放进度写入**不会**让匹配索引反复重建（缓存键不含写库次数） |
| 24-G6 | 剧集墙汇总行显示「N 部作品 · 未看 M 部 · 已有封面 K 张」，一百多张卡片光看角标数不过来 |
| 24-G7 | 卡片 meta 只显示集数，不再重复「已看 N」（角标已经表达），避免同一卡片两次说同一件事 |

对应测试：`tests/test_poster_watched.py`（14 个用例：五态判定、进度优先于看过的列表、宽松匹配不误标（含前提校验）、导入后缓存失效、`_title_contains` 规则、前端五态与定位契约）。

---

## 25. 安装包：路径选择、记忆与升级（2026-11-15 修复）

用户反馈两件事：一是「已经装过了，升级时应该自动读回上次的安装路径」，二是「重新选了路径为什么还是装到 C 盘」。两者都出在 `installer/product.wxs`。

| # | 验收标准 |
|---|---|
| 25-1 | **安装目录属性必须全大写**。MSI 规定只有全大写属性才是 public property，值才能在 UI 序列 → 执行序列之间传递。原来写成 `AppInstallFolder`（含小写）会退化成私有属性，向导里改的路径在切到执行序列时被丢弃，最终仍按 `Directory` 表的默认值安装 —— 这就是「选了 D 盘还是装 C 盘」的根因。现统一为 `APPFOLDER` |
| 25-2 | 目录属性必须标 `Secure="yes"`。本机实测安装以提权方式运行（`Privileged=1`），此时只有 `SecureCustomProperties` 里的公共属性会传给执行序列；实测构建产物里 `SecureCustomProperties = APPFOLDER;WIX_DOWNGRADE_DETECTED;WIX_UPGRADE_DETECTED` |
| 25-3 | 三处引用必须指向同一个 Id：`WIXUI_INSTALLDIR`（向导绑定）、`build_dist.py` 的 `heat -dr`（组件归属）、快捷方式 `Target="[APPFOLDER]Magic Video.exe"` 与 `WorkingDirectory` |
| 25-4 | **升级继承上次路径**：组件安装时把实际路径写进 `HKCU\Software\Magic Video\InstallPath`，下一版由 `AppSearch` 的 `RegistrySearch` 在 `CostFinalize` 之前填回 `APPFOLDER`；用户在向导里重新选则覆盖它 |
| 25-5 | 「应用和功能」的安装位置不再空白：`ARPINSTALLLOCATION` 用 Type 51 自定义动作（`After="CostFinalize"`）赋值 —— 属性表本身不做格式化，直接写 `[APPFOLDER]` 会存成字面量（WiX 也会报 CNDL1077） |
| 25-6 | 0.1.4 及更早版本没写过 `InstallPath`，所以**从旧版升级的第一次**仍会落回默认目录，从这一版起才开始记住；默认目录仍是 `%LOCALAPPDATA%\Programs\Magic Video`，因此旧版升级不会意外换位置 |
| 25-7 | 升级语义不变：`MajorUpgrade` 先卸载旧版再装新版，数据库在 `D://magicvideo`、不在安装目录，播放记录与封面不受影响 |

**验证方式（真装真卸，不靠推断）**：`installer/verify_install_flow.py` 把 `product.wxs` 复制一份、替换成测试专用 `UpgradeCode` / 组件 GUID / 注册表键，构建 0.1.5 与 0.1.6 两个测试包：先指定 `APPFOLDER=<临时目录>\CustomPath` 静默安装并确认文件真的落在那里，再确认 `HKCU\...\InstallPath` 记住了它，然后**不传路径**升级到 0.1.6 并确认仍装在原位置，最后卸载并断言注册表值与快捷方式已清理。全程不碰真实安装。

对应测试：`tests/test_packaging.py::InstallerDefinitionTests`（7 个用例：目录 Id 全大写、向导绑定一致、`heat -dr` 一致、快捷方式指向一致、`Secure="yes"`、`InstallPath` 写入与回读、`ARPINSTALLLOCATION` 走自定义动作）。

---

## 26. 尚未实现（明确边界，避免误判为缺陷）

- 内容哈希重定位（指纹只用头尾各 1MB 的 `size:sha1`，避免大库首次扫描 I/O 过重）、跨设备同步、外部追番站点双向同步、多站点封面兜底自动投票（人工确认已实现，见 §22-B）。
- 随包捆绑完整版 `Everything.exe` + `es.exe`（合法但属打包/授权层改动，需单独确认）；把开源全盘索引内核编译进本软件。
- 换用 mpv / libVLC 等开源内核（正在调研，未决定是否迁移；当前仍是 PotPlayer 控制层方案）。
- **换 mpv / libVLC 内核**（暂缓：当前唯一痛点是倍速精度，已由倍速步进 0.25 精确命中 1.25/1.5/2.0 折中解决）、**内容哈希识别**（暂缓：大库首次扫描 I/O 太重，现用头尾各 1MB 指纹）。
  跨设备同步、外部追番站点双向同步仍明确**不做**（与「纯本地、无账号无云」定位冲突）。
- 安装器的**真机**安装/升级/卸载验证需人工在干净机器执行一次（静态层已自动化），清单见 `installer/verify_install_flow.py` 的 `MANUAL_CHECKLIST`。
- 这些已按优先级列入 `docs/OPTIMIZATION.md` / `docs/ROADMAP_0.1.6.md`，不属于当前版本验收范围。

---

## 27. v0.1.6 数据地基与导入对齐（P0 + P1 全部落地）

> 详细验收口径见 `docs/ROADMAP_0.1.6.md`；命令码、性能数字与踩坑结论见 `docs/IMPLEMENTATION_NOTES.md`。
> 本版**不升版本号、未打包**，功能已可用。

**A. 续播与完成判定（P0-1）**
- 四阈值可配置：`complete_percent=90` / `min_resume_percent=5` / `ignore_seconds_at_start=30` / `ignore_percent_at_end=8`。
- 续播点**只在前进时更新，已看完才清零**——重看片头不会抹掉上一次看到的位置。

**B. 数据可备份可迁移（P0-2 / P0-4 / P1-3）**
- 设置页「数据备份与恢复」：9 张表导出为单个 JSON，恢复支持「仅新增 / 覆盖」，恢复前自动把当前库再备份一份。
- 文件指纹：`size:sha1`（头尾各 1MB）；文件被移动/改名时，仅在「唯一同 size/mtime 候选 + 旧路径已不存在 + 指纹一致」时重关联，**副本不合并、歧义不动**。
- 目录不可达只标 `missing_since`（缺失），不删行、不动进度；恢复后扫描自动清标记。
- 路径重定位向导：按前缀整体替换（整盘换符）、冲突跳过交给指纹、新目录自动注册；用户确认后才软删长期找不回的文件。
- 并发写加固：只对锁竞争重试；守护的读-判断-写路径用 `BEGIN IMMEDIATE`；连续争锁会上报 `db_busy` 事件，不再静默丢进度。

**C. 纠错与时长口径（P0-3 / P0-5）**
- 历史页支持全选 + 批量「已看 / 未看 / 重置进度」，只改进度表不删会话。
- `watch_sessions.watched_ms` + `watch_progress.total_watched_ms`：只累加连续播放增量（≤3 个轮询间隔），快进/拖进度条/回拖都不计入。统计页 UI 已随 P2-1 落地，见 §28-A。

**D. 中文名匹配更可靠（P1-4 / P1-5）**
- 文件名里的 `{tmdb-123}` / `[tvdbid-456]` / `{tvdb=789}` 标签会被解析并剥离（旧版会把花括号留在标题里导致匹配失败）；同一 ID 组内共享中文名（`match_by='id'`），只填空不覆盖。
- 展示标题语言偏好：`cn` 中文名优先 / `orig` 原名优先，只改显示。
- 刮削限速 250ms + 负缓存 6 小时 + 429 退避 120 秒；「强制重新刮削」清缓存重查，手改名字永不被覆盖。

**E. 清单导入（P1-2 / P1-6）**
- 导入页支持三方输入：粘贴纯文本、MyAnimeList `animelist.xml`、Bangumi Takeout `.json`（数组或 JSONL）。
- 解析后先给**差异报告**：只本地有 / 只清单有 / 进度不同（双向）/ 中文名冲突 / 本地有文件但没记录（文件命中 + 标题校验双判定）。
- 合并规则：进度**只增不减**、中文名只补空、`match_by='user'` 永不覆盖、**不删除任何已有记录**、整体幂等。
- 未匹配中文名的行进入「待确认区」：可逐行改名或勾选「暂不导入」；默认入库但标 `status='pending_review'`，看过的列表显示「待确认」徽章——不再静默混入。

---

## 28. v0.1.6 P2 体验层（P2-1 ~ P2-6）+ 两项工程加固

### A. 观影报告（P2-1，左侧「观影报告」）

- **统计口径（改之前先想清楚，代码与页面都写着同一句）**：
  - 优先累加 `watch_sessions.watched_ms`，即「连续播放的位置增量」；**快进、拖进度条、回拖都不计入**。
  - **旧会话兼容**：`watched_ms` 是 v0.1.6 才加的列，迁移时只 `ADD COLUMN DEFAULT 0`、不回填；
    对 `watched_ms=0` 且确有播放区间的会话，**读取时**按 `max_ms-from_ms` 做保守估算
    （拖进度条把 max 抬高、最后停在靠前位置时只算到 `to_ms`，单条上限 12 小时），
    并在 `daily` / `by_work` / 日历的每一天标 `estimated` 次数，界面显「估」角标。
    **估算不写库、不改 schema**，绝不冒充 `watched_ms` 的精确口径。
  - 按会话的**开始日期**归日；跨零点的长会话整段算在开始那天。
  - 同一集看两遍算两次（真实发生过的观看）；连播按集切会话。
  - 黑名单里的作品不计入。
- 页面：近 7 / 30 / 90 天切换；有效时长（分钟 + 小时）、有看片的天数、日均、完成率四个指标；每日柱状图（纯 CSS，不引图表库）；「看得最多的作品」Top 20；底部直接展示口径说明。
- 接口：`get_watch_stats(days=30)`，返回 `daily / by_work / totals / completion / note`，`totals.estimated_sessions` 给出估算会话数；`get_watch_calendar(month)` 同口径并带 `estimated_sessions`。

### B. 观看日历（P2-4）

- `get_watch_calendar(month="YYYY-MM")`：按月算出每天看了多少分钟、当天主看的作品；月份不合法时退回当月。
- 前端 7 列网格（周一开头），颜色深浅分 4 档；上一月 / 下一月切换；悬停显示具体分钟与作品名。
- 不落表，全部按 `watch_sessions` 实时算，删会话即随之变化。

### C. 按季海报墙 + 继续观看置顶（P2-2）

- 剧集墙工具条新增「按作品 / 按季」切换：按季模式下每一季单独一张卡（`season_cards`），`work_key` 与作品一致，评分/标签共用。
- 顶部常驻「继续观看」横向滚动行：`get_continue_row(limit=12)` 按作品去重只留最新一条；已看完但仍有下一集的作品，以**下一集**卡片补位（标「接第 N 集 → 第 M 集」，点击从头播）；人工移除、本地文件已删、全剧看完的不出现。

### D. 多版本文件合并（P2-3）

- 同一 (作品, 季, 集) 存在多个文件（BD / WEB / 不同压制组）时，**展示层**并成一条：
  - 主条目按「已看完 > 有进度 > 体积大 > id 小」挑，点播默认落到它。
  - 因此卡片上的「N 集」是真实集数，不再因多版本虚高。
  - 其余版本挂在 `versions` 里；平铺列表显示「N 个版本」徽章 + 下拉，切换即播对应文件。
- **数据库零改动**：`media_files` 仍是每文件一行，进度各自独立，删掉一个版本不影响另一个。
- 接口：`get_versions(file_id)`。

### E. 标签与评分（P2-4）

- 新表 `work_meta(work_key PRIMARY KEY, rating, tags, updated_at)`，按作品分组 id（`series::xxx`）挂靠，与媒体文件**无外键**——删本地文件不会连带删评分。
- `save_work_meta(work_key, rating, tags)`：评分 0~10（留空即清除），标签逗号分隔（中文逗号也归一化）；`get_work_meta(keys)` 批量取，海报卡片显示 ★ 评分与最多 3 个标签。
- 已进 JSON 备份表清单（`backup.TABLES` 现为 10 张）。纯本地，不同步任何外部站点。

### F. 倍速精确化（P2-5）

- 倍速步进默认 0.1 → **0.25**，使 1.25 / 1.5 / 2.0 三个预设全部精确命中。
- 仍允许在设置里显式选 0.1 / 0.5；选 0.1 时界面如实提示「1.25× 只能到 1.2 或 1.3」。

### G. 全盘快搜降级提示（P2-5）

- `everything.capabilities()` 新增 `degraded / degraded_reason / http_enable_steps`。
- 降级（es.exe 与 HTTP 都不可用）时，快搜页显示琥珀色提示块，直接写清：**当前只搜本软件媒体库、不是整台电脑** + 原因 + 两条补法（启用 Everything HTTP 服务 / 下载 es.exe）+「去设置里启用」按钮。
- **不改 Everything 的 ini、不重启它的服务**（项目铁律），只做只读探测与提示。

### H. 集数解析置信度（OPTIMIZATION #6 尾巴）

- `parse_filename` 增加 `episode_source` / `episode_confidence` 两列（`media_files` 同名落库）：
  - `high`：`S01E02` / `EP12` / `第03话` / `E01`
  - `medium`：`1x01`（新增支持）、纯数字文件名、结尾数字、`Season 2 - 05`
  - `low`：`[01]` 方括号集数、`01-02` 区间——**语义本身有歧义**
- 只有 `low` 进「扫描目录 → 集数待确认」卡片，可逐条填季/集确认；确认后记 `episode_source='user'`，重新扫描不再覆盖。
- 顺带修掉旧 bug：`第03话` 曾被季正则读成「第 3 季」，一部 12 集的番会变成 12 个季。
- **口径说明**：`1x01` 与 `第01话` 定为 medium 而不是 low——格式规范、无歧义，不该打扰用户；low 只留给真正两种解释都成立的写法。

### I. CSV / NFO 导出（OPTIMIZATION #5 尾巴）

- 设置页新增「导出表格 / NFO」卡片：
  - **CSV**：历史记录 / 看过的列表 / 媒体库三张表，UTF-8 BOM，Excel 双击不乱码；
  - **NFO**：按作品导出 Kodi 风格 `tvshow.nfo`，结构 `<数据目录>/exports/nfo/<作品名>/tvshow.nfo`。
- **只写 `<数据目录>/exports/`，绝不写进视频所在目录**（项目铁律：不写同目录 `.nfo`）。
- 接口：`export_table(kind)` / `export_nfo()` / `open_export_folder()`。

### J. 两项工程加固（补强方案 C）

- **C-1 外部解析单一层**：新建 `app/sites.py`，AGE / Seeduck 的分块、标题、封面候选、URL 补全、meta 提取只有一份实现；`covers.py` 与 `websearch.py` 都从它取（旧私有名保留别名，测试无需大改）。解析空结果会计数，`sites.health()` 可查——站点改版不再静默返回空。
- **C-2 缓存键守护**：`tests/test_cache_keys.py` 从源码（键构造函数里不许出现 `mtime`）和行为（写播放进度不改变分组指纹、写库改变签名）两路锁住「缓存键必须按数据指纹」这条铁律。

### K. 安装器验证静态层（P2-6）

- `installer/verify_install_flow.py --static`：不需要 WiX 与 PyInstaller 产物，核对「目录 Id 全大写 / WIXUI_INSTALLDIR 一致 / Secure=yes / MajorUpgrade / InstallPath 写入 + RegistrySearch 回读 / ARPINSTALLLOCATION 走 Type 51」；已纳入单测，防止「又装回 C 盘」这类回归拖到发版后才暴露。
- 真机 7 步清单（干净环境安装 → 带路径升级 → 卸载 → 重装）由脚本 `MANUAL_CHECKLIST` 打印，**必须人工执行一次**，自动化覆盖不到。

---

## 29. 公共封面资产随包分发 + PR 更新（2026-11-16）

### 29-A 为什么要有这一层

封面原来只存在 `<数据目录>/covers/`，属于本机缓存：换机器、新装、离线时全都没有，
别人也没法贡献。现在把已抓到的封面**导进仓库**，随安装包一起分发。

| # | 验收标准 |
|---|---|
| 29-A1 | `assets/covers/` 含图片与 `index.json`（`{file,title,aliases,source,cover_url,sha256,bytes,updated_at}`），`count` 与条目数一致 |
| 29-A2 | 打包时 `--add-data assets:assets`，安装包内 `_internal/assets/covers/` 可见；新装即有封面，**不需要联网抓一次** |
| 29-A3 | 运行期只做**单向**复制：本机 `COVER_DIR` 缺这张图时才从资产补一份；本机已有（含手工封面）一律优先，绝不反向写安装目录 |
| 29-A4 | 资产缺失 / `index.json` 损坏 / 清单里有但图片被删：一律静默降级为「没有封面」，不影响抓取与本地缓存 |
| 29-A5 | 手工封面（`source='manual'`）**不进公共资产**，否则「清除封面」会被资产复活 |
| 29-A6 | 导出脚本 `export_covers.py`：默认只导出本机已落库的非手工封面、**不删除**资产里已有文件（别人 PR 进来的不会被本地导出冲掉）；`--refresh-index` 只重算 `sha256`/`bytes` |
| 29-A7 | **清除封面后不会被资产复活**：清除时按作品键记一条墓碑（`settings.cover_cleared`），解析层直接剔除该作品的资产条目，本机已补过的同名图也不再用；重新设定/重新抓取封面即自动解墓碑 |

### 29-B 协作流程

- **换某部剧的封面**：在 `index.json` 里按作品名找到 `file` → 用新图覆盖同名 jpg →
  `python export_covers.py --refresh-index` → 提 PR。改动只有「一张图 + 一条哈希」。
- **新增作品**：图片放 `assets/covers/`，`index.json` 加一条，跑 `--refresh-index` 补齐哈希。
- 细则写在 `assets/covers/README.md`（尺寸、命名、不要放什么、版权提示）。

### 29-C 实现要点

- `app/config.py::BUNDLED_COVER_DIR = RESOURCE_DIR / "assets" / "covers"`（源码版是项目根，打包版是 `_internal/`）。
- `app/covers.py` 新增 `bundled_covers()`（按 `index.json` 的 mtime+size 缓存，同时建
  「文件名 → 作品键」反查表）与 `all_cover_entries()`（本机已落库在前、资产在后，
  `cover_index` 用 `setdefault` 保证前者优先；已清除的作品在这里就被剔除）。
- `_cover_file_ok()` 在本机缺图时调 `_materialize_cover()`：读资产 → 写 `.part` → `replace` 原子落盘，
  `OSError` 一律吞掉（只读安装目录也不能让封面功能崩）。
- `Api.cover_data_uris()` 改用 `all_cover_entries()`，剧集墙/媒体库/看过的列表共用同一套解析。
- 单测隔离：`tests/base.py` 把 `BUNDLED_COVER_DIR` 一并纳入临时目录替换，避免仓库里的真实图片
  影响既有用例；`reset_module_state()` 清 `_BUNDLED_CACHE`。

---

## 30. 看过的列表改名 → 历史记录 / 剧集墙跟随（2026-11-17）

### 30-A 问题

历史记录的标题读 `media_files`（`title_cn` > `title_guess`），剧集墙分组名也来自
`media_files`；而「看过的列表」的行内改名只写 `watched_list`。两张表没有外键，
改名不传播时就会出现「看过的列表已经是新名字、历史记录还是老名字」。

真实样本：第 2 季文件名是 `My.DressUp.Darling.S02E12...-BlackTV.mkv`，扫描出的
`title_guess` 是 `My DressUp Darling KKTV AAC2 0 -BlackTV`，`title_cn` 为空。
在「看过的列表」里改成「更衣人偶坠入爱河 第二季」后，历史记录仍显示那串发布组名。

### 30-B 验收标准

| # | 验收标准 |
|---|---|
| 30-A1 | 在看过的列表改名后，**同一季**的媒体文件标题同步更新；历史记录、剧集墙、媒体库立即显示新名字，无需重扫 |
| 30-A2 | **只改同一季**：第 1 季与第 2 季常常共用同一个中文名，改第 2 季不能把第 1 季一起改掉 |
| 30-A3 | 无季标记（NULL）与「第 1 季」视为同一季（沿用同步侧的 `_season_key` 折算口径） |
| 30-A4 | 写回的行带 `match_by='user'`：后续刮削（含「强制重刮」）与「外文补中文」都不得覆盖 |
| 30-A5 | 发布组 / 压制参数串（`... KKTV AAC2 0 -BlackTV`、`... 1080p HEVC`、`[组名] ...`）**不当中文名写库、不进学习词典**，否则会顺着词典反向污染其它文件 |
| 30-A6 | 媒体库里没有对应文件时只改 `watched_list` 并正常返回 `media_updated=0`，不得报错 |
| 30-A7 | 命中的文件行数超过 300 时放弃同步（宁可不同步，也不能一次改掉半个库） |

### 30-C 改名时的定位顺序

1. 用**改名前的** `title_cn` / `title` 与媒体行的 `display_title` 精确相等；
2. 季号必须折算后相等才改；
3. 第一条命中即停，不做多路并集——多路并集会把别的作品一起卷进来。

为什么用「改名前的名字」定位：同步生成的 watched 行，其标题本来就取自
`COALESCE(title_cn, title_guess)`，所以旧值必然与媒体行一致；而用户起的新名字
（「第二季」这类）在媒体库里根本不存在，用新名去找一定 0 条。

### 30-D 顺带修掉的历史脏数据

`update_watched` 原先把「改名时回传的旧中文名」当作要记住的中文名写进
`name_alias`。若旧值本身是发布组串，就会记成
`更衣人偶坠入爱河 → My DressUp Darling KKTV AAC2 0 -BlackTV` 这种**反向映射**，
之后每部作品补中文名都可能被它污染。现在这类值一律按空处理。

---

## 31. 看完一集后「继续观看」直接接下一集

### 31-A 场景

历史记录最新一条显示「某作品第 1 集已看完」，但继续观看列表空了——因为守护看完时会把
`watch_progress.completed=1`、`position_ms=0`（清续播点），而继续观看的两个接口都硬过滤
`completed=0 AND position_ms>0`。历史读的是 `watch_sessions`，不关心完成状态，所以仍在。

### 31-B 行为

| # | 验收标准 |
|---|---|
| 31-B1 | 看完第 1 集且存在第 2 集时，首页「继续观看」、导航「继续观看」页、剧集墙顶部继续观看行**三处**都显示这张作品卡，指向第 2 集 |
| 31-B2 | 卡片标 `接第 N 集 → 第 M 集` + 「下一集」角标，主按钮文案「播放下一集」，点击**从头**播放第 2 集 |
| 31-B3 | 全剧看完（没有下一集）时自然从继续观看消失；不靠放宽 `completed` 判定来实现 |
| 31-B4 | 跨季沿用 `player_adjacent` 的整部作品排序：第 1 季末集的下一集是第 2 季第 1 集 |
| 31-B5 | 下一集已移入回收站、完成项被「移除」、命中黑名单 → 不显示；同一部剧已有在看条目时优先在看条目，不重复出卡 |
| 31-B6 | 第 1、2 集都看完时只按**最新**完成记录求下一集，不回退到更早的集 |
| 31-B7 | `watch_progress.completed` / `position_ms` / 完成率 / 历史记录 / 四阈值**一律不动**；「已看完」清单照旧保留该集 |

### 31-C 实现位置

- `app/api.py::_completed_followups()`：取数侧补集，复用 `player_adjacent` 的排序口径；
- `app/api.py::_adjacent_in_tree()`：从 `player_adjacent` 抽出的辅助函数，补集一次构建剧集树、多部作品共用；
- `web/app.js::progressItem()` / `renderContinueRow()`：角标与「播放下一集」文案，点击仍走既有 `data-action="play"` → `playSmart('id', id, false)`。

---

## 32. 启动首屏主题：不再「先白后黑」

### 32-A 现象与原因

暗色已正确持久化在 SQLite，表头的 `--surface-2` 在暗色下也有 `#272c37`，但重新打开软件时
表头先是白的，手动切一下主题才变黑。原因是 `<html data-theme="light">` 首屏写死，
`applyTheme(info.theme)` 要等 `await call('get_app_info')` 这一趟异步 IPC 才执行；
`private_mode=True` 下 localStorage 又不能用来同步预设。

### 32-B 验收标准

| # | 验收标准 |
|---|---|
| 32-B1 | 上次选的是暗色时，**从第一帧起**就是暗色：表头、侧栏、标题栏一致，不需要手动切换 |
| 32-B2 | 主题参数由 Python 侧从 settings 同步注入 URL，非法值 / 缺值回退 light，与 `Api._theme()` 口径一致 |
| 32-B3 | `index.html` 的同步预设脚本必须位于 `style.css` 之前，CSS 首次解析时 `data-theme` 已就位 |
| 32-B4 | JS 侧 `currentTheme` 初值跟随 DOM 的 `data-theme`，主题按钮图标 / 文字首屏就正确，不出现「CSS 暗、按钮显示浅色」 |
| 32-B5 | 用户切换主题照旧即时生效并写回 SQLite，行为不变 |

### 32-C 实现位置

- `app/main.py::startup_url()`：把 settings 里的 theme 拼进 `file://.../index.html?theme=dark`；
- `web/index.html` head 内同步脚本：读 `URLSearchParams(location.search).get('theme')`，在样式表前设 `data-theme`；
- `web/app.js`：`currentTheme` 初值从 `document.documentElement` 读取。

---

## 33. 剧集墙工具栏修正：按季恢复、冗余按钮移除（2026-11-17）

### 33-A 现象与根因

用户反馈「剧集墙点『按季』没反应」。诊断发现不是绑定缺失，而是 `handleAction` 分支里
把形参 `btn` 误写成未声明的 `el`；严格模式下抛 `ReferenceError` 被全局 click 的 try/catch
吞掉，只闪一下 toast，`posterState.mode` 从未改变。同类笔误共 6 处（另 5 处：
`save-episode` / `export-csv` / `stats-range` / `calendar-shift` / `rate-work`），都会让对应按钮假死。

### 33-B 验收标准

| # | 验收标准 |
|---|---|
| 33-B1 | 「按季」按钮保留且点击生效：在「按作品 / 按季」间切换、按钮高亮跟随、卡片按对应口径重绘 |
| 33-B2 | 上述 6 个 case 一律从 `btn.dataset` 取值，`handleAction` 作用域内不得再出现未声明的 `el.dataset` |
| 33-B3 | 剧集墙移除「刷新」按钮及其 `poster-refresh` 分支：后端按数据指纹 + 20s TTL 自动缓存，该按钮 20s 内命中缓存等于无效，且不刷封面、不写库、不扫盘 |
| 33-B4 | 剧集墙移除「去后台进程管理封面」按钮：它只是跳到左侧已有的「后台进程」页，无封面动作；通用 `goto` case 保留（媒体库「查看分集」等仍用） |
| 33-B5 | 移除按钮不 losses 数据更新能力：扫描目录、切视图 / 翻页、任何写库操作、重启软件仍会刷新数据；封面更新仍走后台进程页抓取 |

### 33-C 实现位置

- `web/app.js`：6 处 `el.dataset` → `btn.dataset`；`renderPosters()` 删两个按钮；删 `case 'poster-refresh'`；
- `tests/test_watchlist.py`：`test_cover_controls_moved_out_of_poster_wall` 改断言两个按钮不存在，
  新增 `test_poster_mode_button_exists_and_uses_btn` 与 `test_handle_action_cases_use_btn_not_undefined_el`。

---

## 34. 全盘快搜：修「假降级」与中文路径 GBK 乱码（2026-11-17）

> 注：§22 记录的「Everything 核心引擎闭源、无法编译进本软件」结论仍然成立；本节只修
> 现有 es.exe / HTTP 通道的两个实现 bug，不改变"依赖外部 Everything 实例"的架构。

### 34-A 现象与根因

用户看到「来源：library · 全盘检索通道暂时不可用，已自动降级」。实测（只读）本机
es.exe 存在、Everything 主程序在跑、es 通道**搜 `mp4` 能返回真实文件**，通道根本没挂。

- **假降级**：`search()` 用「通道有没有返回行」判断可用性——关键词在**物理文件名**
  里没命中（es 只搜文件名，不搜数据库中文译名）就被误判成「通道不可用」。
- **中文乱码**：`search_es()` 用 `encoding="utf-8", errors="replace"` 解码 es.exe 输出；
  但中文 Windows 的 es.exe 按 **GBK** 输出，坏字节被替换成 `?`，全盘搜到的中文动画路径全坏、点开失败。

### 34-B 验收标准

| # | 验收标准 |
|---|---|
| 34-B1 | es.exe 按 GBK 输出中文路径时正确解码（`_decode_output`：utf-8 strict 优先、GBK 回退），纯 ASCII / UTF-8 输出也不受影响 |
| 34-B2 | 全盘通道**执行成功但 0 命中**时：`channel_ok=True`、`degraded=False`，提示「全盘检索未找到匹配的视频文件（Everything 通道正常），以下是本程序媒体库的结果」，不得再说「通道暂时不可用」 |
| 34-B3 | 全盘通道**真失败**（es 超时 / Error 8 / HTTP 错误、且无备用通道）时：仍 `degraded=True` 并给出失败原因，行为不变 |
| 34-B4 | 命中时 `source=everything-es` / `everything-http`，0 命中落到 library 时结果仍来自媒体库，仅提示文案区分原因 |
| 34-B5 | 不改变"依赖外部 Everything 实例"的架构，不改系统服务配置、不随包捆绑（沿用 §22 结论） |

### 34-C 实现位置

- `app/everything.py::_decode_output()`：统一解码，修 GBK 乱码；
- `app/everything.py::search_es()`：不再指定 `text/encoding`，拿原始 bytes 走 `_decode_output`；
- `app/everything.py::search()`：以 `es_error`/`http_error` 是否为空判断通道执行是否成功，新增 `channel_ok` 字段，区分「没命中」与「通道挂了」；
- `tests/test_everything.py`：新增 GBK bytes 解码、UTF-8 兼容、通道正常 0 命中不报降级、通道真失败仍降级 4 例。

**实测**：`search('从零')` → `source=everything-es`、中文路径完整；`search('不存在的词')` → `channel_ok=True`、`degraded=False`、提示「通道正常但没找到匹配」。

---

## 35. 播放前蓝牙音箱保障（2026-11-17）

### 35-A 场景与能力边界

用户用蓝牙音箱看视频，音箱常断联。先厘清一个 Windows 的硬约束（本机只读实测确认）：

| 断联本质 | 系统信号 | 软件能否自动救 |
|---|---|---|
| 默认输出被切走（链路还在） | 音箱端点 `Active`，但默认指向别处 | ✅ 能，切默认设备约 5ms |
| 蓝牙链路真断（休眠/超距/关机） | 端点 `Unplugged` / `NotPresent` | ❌ 不能。Windows **无公开稳定的 A2DP 重连 API**，只能提示用户手动连 |

因此本功能做「自适应」：**能切则切，断链则提示**，绝不假装已连上。核心引擎闭源无法编译进软件的结论不变（同 §22 全盘快搜）。

### 35-B 验收标准

| # | 验收标准 |
|---|---|
| 35-B1 | 设置页「播放器与记录」新增「蓝牙音箱名称」输入（子串匹配、不区分大小写、留空=禁用） |
| 35-B2 | 点播放起播前探测：目标音箱 `Active` 但非默认 → 自动切为默认；已经是默认 → 无操作；切换成功 toast「已切回…」 |
| 35-B3 | 目标音箱 `Unplugged/NotPresent` → 弹提示条「当前未连接，请在系统蓝牙设置里连接」+「打开蓝牙设置」按钮（一键 `ms-settings:bluetooth`）+「知道了」 |
| 35-B4 | 找不到同名设备 → 提示检查设置里的设备名；未配置 / COM 不可用 / 任何异常 → 静默，**绝不影响 PotPlayer 起播** |
| 35-B5 | 四个起播入口（媒体库、全盘快搜、软件内嵌入×2）统一挂接；播放失败时不弹蓝牙提示 |
| 35-B6 | 依赖 comtypes + pycaw（纯 Python，随包分发）；测试全程 mock CoreAudio，不碰真实音频设备 |

### 35-C 实现位置

- `app/bluetooth.py`（新）：`probe()` 状态机（disabled/switched/active/link_down/not_found/error）+ `open_bluetooth_settings()`；pycaw 懒加载、COM 调用集中、异常全吞；
- `app/api.py`：`_with_bluetooth()` 挂接四个播放方法 + `open_bluetooth_settings()` 接口；`bluetooth_speaker_name` 入设置白名单；
- `web/app.js` / `web/index.html`：设置输入 + `handleBluetoothTip()` 提示条 + 两个 action 分支；
- `requirements.txt` + `build_dist.py`：新增依赖与 `--collect-all pycaw/comtypes`（否则打包版探测降级为 error）。

---

## 36. 设置页改成 IDEA 式弹窗（2026-11-18）

### 36-A 场景

设置原来作为主内容区的一页，9 组字段一屏纵向铺开，要滚很久才能找到某一项。用户反馈「数据有些多有些乱」，
希望像 IDEA 那样「左上角列分类，右边显示对应内容，整合成一个弹窗」。

### 36-B 验收标准

| # | 验收标准 |
|---|---|
| 36-B1 | 从标题旁下拉菜单点「设置」打开浮层弹窗（遮罩 + 对话框），不再切主视图；关闭后仍停在原来那页 |
| 36-B2 | 弹窗左侧列 9 个分类（播放与记录 / 播放控制键位 / 中文名与刮削 / 全盘快搜 / 视频黑名单 / Obsidian 笔记 / 媒体库体检 / 备份与导出 / 数据与外观），右侧只显示当前分类的内容 |
| 36-B3 | 切分类只切显隐，不重新取数；打开时一次性读全量设置，字段契约 `#set-xxx` + `[data-setting]` 保持不变 |
| 36-B4 | Esc / 右上角 ✕ / 「取消」/ 点遮罩都能关闭并放弃未保存改动；「保存设置」照旧整批写库并刷新 |
| 36-B5 | 所有 `goto settings` 的旧入口（剧集墙、媒体库等处的按钮）改成直接开弹窗，不再出现空白设置页 |

### 36-C 实现位置

- `web/index.html`：`.modal-root#settingsModal`（遮罩 + `.modal-dialog` + 左 `#settingsNav` + 右 `#settingsPane` + 底栏取消/保存）；
- `web/app.js`：`openSettingsDialog()` / `closeSettingsDialog()` / `renderSettingsPanes()` / `switchSettingsTab()`；`switchView('settings')` 直接转开弹窗；
- `web/style.css`：弹窗与左栏样式，删掉原来的 `.settings-actions` sticky 横条。

---

## 37. 标题旁下拉菜单收纳工具入口（2026-11-18）

### 37-A 场景

侧栏原来 14 个入口平铺：先按用户提议把「导入列表 / 扫描目录」提成左上角快捷卡片、「设置」下沉底栏；
随后用户反馈「这三个这样不好看，参考 PotPlayer 在表头名称处加个下拉按钮」。最终形态是**顶栏标题旁的小箭头下拉**，
侧栏只留浏览页面，工具性入口按需展开。

### 37-B 验收标准

| # | 验收标准 |
|---|---|
| 37-B1 | 顶栏「总览」等标题右侧有小箭头按钮（`data-action="toggle-title-menu"`），点击展开下拉；再点箭头、点菜单外、按 Esc、点任一菜单项都收起，`aria-expanded` 同步 |
| 37-B2 | 下拉含「导入列表 / 扫描目录 / 设置」三项（设置前有分隔线），复用 `.nav-item`，点击行为与普通导航完全一致（切视图、离开内嵌播放时还原播放器、设置仍开弹窗） |
| 37-B3 | 侧栏不再出现这三个入口：`#mainNav` 只留 11 个浏览页，底栏只留守护状态与版本号 |
| 37-B4 | `nav == VIEW_META == RENDERERS` 仍是 14 项（菜单项也计入），不出现同一视图两份实现 |
| 37-B5 | 深浅两套主题下菜单有独立卡片底、描边与阴影，浮在内容之上不被遮挡；780 高窗口下侧栏 11 项与底栏全部可见 |

### 37-C 实现位置

- `web/index.html`：`.topbar-title .title-row` 内加 `.title-menu-btn` + `.title-menu`（三个 `.nav-item.menu-item`）；删掉 `.quick-block` 与 `.sidebar-foot` 的设置按钮；
- `web/style.css`：`.title-menu { position:absolute; z-index:60 }` + `[hidden]` 规则；删掉 `.quick-*` 卡片样式；
- `web/app.js`：`closeTitleMenu()` + 点击委托里的开关/外部关闭分支 + Esc 分支；
- `tests/test_watchlist.py::test_title_menu_holds_utility_entries`：菜单三项、侧栏不含这三项、样式与交互契约。

---

## 38. 看完自动同步进「看过的列表」（2026-11-18）

### 38-A 现象与根因

用户看完 `Kaichou wa Maid-sama!` 第 1、2 集，点「从播放记录同步」却看不到这部作品。
只读排查真实库，得到**两个独立原因**：

| # | 现象（库内实据） | 根因 |
|---|---|---|
| 1 | 第 1 集 `watch_sessions.max_ms = 1379837`（占时长 94.96%），但 `watch_progress.completed = 0`、`percent = 0` | PotPlayer 的 `POT_GET_TOTAL_TIME` 读回 0 时 `duration_ms = 0` → `percent` 恒为 0 → 永远不满足 `complete_percent=90`，这一集**从来不算看完** |
| 2 | 第 2 集 `completed = 1`，但同步结果按集拆行 | `title_guess` 是 `Kaichou wa Maid-sama! 02`（集号没剥掉），同一部番每集标题都不同，按 (标题, 季) 聚合就成了每集一条 |

另外用户明确要求：**不能总靠手动点按钮**。

### 38-B 验收标准

| # | 验收标准 |
|---|---|
| 38-B1 | 播放器读不到总时长（`duration_ms=0`）时，用 `media_files.duration_ms`（扫描或上次播放写入的）兜底算 `percent`，该集到阈值即判完成；兜底时长不得被 0 覆盖回库 |
| 38-B2 | 判定完成的**那一次轮询**就把作品补进看过的列表（`completed` 由 0→1 时触发），无需用户点按钮 |
| 38-B3 | 实现只处理刚看完的那一部（`only_titles`），不整表重扫；任何异常静默，绝不影响进度记录主链路 |
| 38-B4 | `title_guess` 末尾并进去的集号按 `episode_no` 精确剥离（`Kaichou wa Maid-sama! 02` → `Kaichou wa Maid-sama!`）；标题自带数字（`魔王2099`）不受影响 |
| 38-B5 | 打开「看过的列表」先静默补同步一次（8 秒节流），覆盖守护没运行 / 那次写库失败 / 旧记录从未同步；有新增才提示，失败不挡住列表渲染 |
| 38-B6 | 手动点「标记已看」同样进看过的列表；批量标记只在循环结束后同步一次（不逐条重扫） |
| 38-B7 | **删除墓碑**：在看过的列表里删掉 `source='playback'` 的行后，自动同步不再把它加回来；用户重新手动导入同名作品则解除标记。删手动导入的行不留墓碑 |
| 38-B8 | 设置里可关（`auto_sync_watched` 默认开）：关掉后判定完成照常写进度，但不再动看过的列表，手动按钮仍在 |

### 38-C 实现位置

- `app/watcher.py::_tick()`：`duration` 三层兜底（播放器 → `media_files` → 本次会话），`percent` 用兜底时长重算；写库改用兜底值；事务内读 `completed` 旧值，`0→1` 时调 `_auto_sync_watched()`；
- `app/watcher.py::_auto_sync_watched()`：延迟导入 importer、只同步该作品、异常写 `last_error` 不抛；
- `app/importer.py::_strip_episode_suffix()`：按 `episode_no` 精确剥尾部集号；`_sync_key` / `mark_sync_ignored` / `clear_sync_ignored` 用 settings `watched_sync_ignored` 存墓碑；`sync_watched_from_progress(only_titles=None, merge_duplicates=True)` 支持限定范围与跳过墓碑，返回 `ignored`；
- `app/api.py`：`_set_watch_state(..., sync_watched=True)` + `_sync_watched_for_file()`、`batch_mark` 循环后统一同步、`delete_watched` 记墓碑、新增 `sync_watched_auto()`（8 秒节流）、`save_settings` 白名单加 `auto_sync_watched`；
- `app/config.py`：`auto_sync_watched: 1`；
- `web/app.js::renderWatched()`：渲染前静默调用 `sync_watched_auto`，有新增才 toast；设置「播放与记录」加开关。
- `tests/test_auto_sync.py`（13 例）：聚合不拆行、标题自带数字不误伤、`only_titles` 范围、墓碑跳过 / 手动导入解除 / 删除记墓碑、时长兜底判完成、完成即同步、开关关闭、Api 节流。
