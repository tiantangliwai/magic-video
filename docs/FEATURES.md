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
| R2-8 | 历史页按时间倒序列出每次会话的起止时间与最远进度 | `test_smoke.py::test_full_user_journey` |
| R2-9 | 「标记已看 / 标记未看 / 重置进度」可纠错，且**不删除** `watch_sessions` | `test_api.py::test_mark_watched_and_unwatched` 等 |

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
| `recording_enabled` | 1 | 托盘可暂停/恢复记录 |
| `autostart` | 0 | 开机自启（注册表 Run 项） |
| `daemon_auto_start` | 1 | 打开主窗口时自动拉起托盘守护；设置页可关。关时立即停守护，开时立即补拉起。旧库由 `init_db()` 的 `INSERT OR IGNORE` 自动补默认值 |
| `http_proxy` | 空 | 在线元数据代理 |
| `metadata_source` | `cache_first` | `cache_first` / `online_first` / `local_only` |
| `bangumi_base` | `https://api.bgm.tv` | 在线源地址 |
| `window_width` / `window_height` | 1180 / 780 | 主窗口尺寸 |
| `minimize_to_tray` | 1 | 关闭时最小化到托盘 |
| `theme` | `light` | 展示风格 `light` / `dark`，只走 `set_theme()`，不在 `save_settings` 白名单里 |
| `embed_player` | 1 | 是否在软件内嵌播放 PotPlayer；关闭或嵌入失败一律用独立窗口 |
| `embed_hwnd` | 0 | 运行时写入：内嵌后的 PotPlayer 窗口句柄，供守护进程跨进程回查 |
| `everything_es_path` / `everything_http_url` | 空 | 全盘快搜的两个可选通道，都不可用时降级为媒体库检索 |
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
| 抓到就能显示 | 封面按标题哈希落盘，所以 `cover_titles()` **第一优先级就是剧集墙卡片标题**（`poster_labels()` 复用 `library_tree.build_tree(rows,"series")`）；`cover_key()` 去季标记 + 去尾集号，逐集分组共用一张；卡片标题与抓取标题仍不一致时 `resolve_cover()` 按 `subjects` 精确/去季/包含回退，`get_poster_wall()` 再用分组内文件 `title_cn/title_guess` 兜底 |
| 优先抓能抓到的 | 卡片标题之后依次是导入清单中文名、媒体库中文名、其余英文标题；技术标签 / 番号编号 /「（完）」残行 / 零宽字符先过滤 |
| Everything 快搜 | `app/everything.py`：`es.exe` → Everything HTTP 服务 → **自动降级为媒体库检索**；能力探测与提示写进「设置」页 |

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

对应测试：`tests/test_covers.py`、`tests/test_everything.py`。本机 `D:\tool\Everything` 只有 `Everything.exe`，要启用全盘需用户补官方 `es.exe` 或在 Everything 里开 HTTP 服务。

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
| 15-9 | 播放记录与看过的列表是两条独立数据流；「从播放记录同步」按钮幂等回补 `completed=1` 的作品，区间覆盖判重，不改手动导入行 |
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

## 16. 尚未实现（明确边界，避免误判为缺陷）

- 内容哈希重定位、跨设备同步、NFO/JSON 备份导出、看片时长统计、外部追番站点同步、封面人工确认与多站点兜底。
- 这些已按优先级列入 `docs/OPTIMIZATION.md`，不属于当前版本验收范围。
