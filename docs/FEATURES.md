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
| 信息 | `get_app_info` `get_stats` `get_player_status` `get_settings` `save_settings` |
| 列表 | `get_continue_watching` `get_completed` `get_history` `get_watched_list` `update_watched` `delete_watched` `get_media_files` `get_media_files_page` |
| 导入 | `preview_import` `commit_import` |
| 播放 | `play_file` `open_data_folder` |
| 目录 | `get_directories` `add_directory` `remove_directory` `set_directory_enabled` `choose_directory` `scan_now` `get_last_scan` |
| 词典 | `dictionary_status` `rematch_all` `import_alias_file` |
| 纠错 | `mark_watched` `mark_unwatched` `reset_progress` |
| 守护 | `daemon_running` `start_daemon` `attach_window` |

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
| `autostart` | 0 | 开机自启 |
| `http_proxy` | 空 | 在线元数据代理 |
| `metadata_source` | `cache_first` | `cache_first` / `online_first` / `local_only` |
| `bangumi_base` | `https://api.bgm.tv` | 在线源地址 |
| `window_width` / `window_height` | 1180 / 780 | 主窗口尺寸 |
| `minimize_to_tray` | 1 | 关闭时最小化到托盘 |
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

## 10. 尚未实现（明确边界，避免误判为缺陷）

- 内容哈希重定位、跨设备同步、NFO/JSON 备份导出、按季海报墙、看片时长统计、外部追番站点同步。
- 这些已按优先级列入 `docs/OPTIMIZATION.md`，不属于当前版本验收范围。
