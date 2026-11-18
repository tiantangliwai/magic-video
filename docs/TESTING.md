# 自测与单元测试报告 · v0.1.8

> 执行时间：2026-11-18（v0.1.8 打包门禁）；环境：Windows 10 专业版 / Python 3.13 / Node 22 / SQLite 3.53 / Edge WebView2 157。
> 本报告只记录**实际跑过的命令与真实输出**，未实测的能力不会写成「通过」。

---

## 0. 测试节奏（2026-11-15 用户约定）

| 时机 | 命令 | 说明 |
|---|---|---|
| 日常改代码 / 补用例 / 改文档 | `python run_changed_tests.py` | 按 `git diff` 变更映射受影响模块，`--list` 只预览、`--module` 追加、`--only` 只跑显式模块、`--base HEAD~1` 回看已提交改动 |
| 只改前端语法 / 文案 | `node --check web/app.js` | 不必跑全量 Python 用例 |
| **`git push` 前** | `python run_tests.py` | 全量必须全绿 |
| **打 tag / 发版前** | `compileall` + `node --check` + `run_tests.py` | 另加打包链验收 |
| `tests/base.py` / schema 改动 | `python run_changed_tests.py --all` | 基类影响全部用例，脚本会拒绝只跑增量 |

- 影响面表 = `run_changed_tests.py` 的 `IMPACT`（跨模块依赖）与 `FRONTEND_MODULES`（前端静态契约），契约由 `tests/test_changed_tests.py` 守护。
- 增量运行不写 `data/test_last.json`；该文件只由全量 `run_tests.py` 更新，供 Obsidian 自测报告笔记读取。日常迭代不刷新该快照是预期行为，**不是补跑全量的理由**。
- **全量 `python run_tests.py` 的封闭白名单（用户 2026-11-15 二次纠正，仅此三条）**：① 用户本轮明确要求推送 / `git push`；② 用户本轮明确要求打 tag / 发版 / 打包；③ 本轮改了 `tests/base.py` 或数据库 schema。**其余情况一律不得跑全量**。
- **禁止借口**：「改动面大 / 文件多」「想兜底一下」「收尾再验一遍」「确认总用例数」「增量全绿后顺手再跑一次」全部无效。**增量全绿 = 验收通过**，直接同步文档、记日志、本地提交；日常回复里不报全量数字。完整口径见 `AGENTS.md` 文件顶部测试铁律与 3.0.1，契约由 `tests/test_docs.py::test_incremental_test_policy_is_documented_in_agents` 守护。

---

## 1. 结论

| 项目 | 结果 |
|---|---|
| 单元测试 | **951 个用例全部通过**（0 失败 / 0 错误 / 0 跳过，116.68 秒，详见第 14 节；0.1.7 那轮的 900 见第 13 节） |
| Python 语法 | `compileall` 全通过 |
| JavaScript 语法 | `node --check web/app.js` 通过 |
| 原生窗口 | 14 个页面（含新增「看片统计」）全部渲染成功，`js_api` 暴露 50 余个方法；扫描结果 / 媒体库共用全量列表组件 |
| 打包产物 | 0.1.4：ZIP 21.8 MB / MSI 22.0 MB（见第 10 节）；0.1.5 见第 11.3 节；0.1.6 见第 12.3 节；0.1.7 见第 13.3 节；**0.1.8 重新构建的产物验收见第 14.3 节** |
| CLI | `init / status / dict / progress / player / mark / test / obsidian` 全部可用 |
| 真实数据 | 旧播放记录、会话、断点进度、词典完整保留，未被本轮改动破坏 |
| Obsidian 真实 vault | 首次同步生成 6 篇笔记 + 当天日志；用户原有 2 篇笔记 md5 前后完全一致；二次同步批注保留、日志去重通过 |
| 远程推送 | 0.1.6 已推送：`master` + `v0.1.0`~`v0.1.6` 到私有仓库 `tiantangliwai/magic-video`（SSH 通道），GitHub Release 附 ZIP + MSI；**v0.1.7 / v0.1.8 仅本地**（只打包，commit 与 tag 均未推送，未建 Release） |

---

## 2. 测试分层与覆盖矩阵

| 测试文件 | 用例数 | 覆盖内容 |
|---|---:|---|
| `tests/test_api.py` | 23 | 版本信息、统计、导入往返、设置白名单、代理重置、续播参数、已看完列表、标记已看/未看/重置进度、批量标记、待确认集数、CSV/NFO 导出、目录扫描桥接、守护探测 |
| `tests/test_backup.py` | 20 | JSON 三模式导出与 `merge`/`overwrite` 恢复、恢复前自动备份、保留原始 id、10 张表完整性、CSV 三表 BOM、NFO 按作品写且不碰视频目录 |
| `tests/test_blacklist.py` | 38 | 默认开启、分隔符解析与去重、包含匹配、字形归一化（`亜`/`亞`/`亚`、NFKC 全角）、短英文词词边界与 GLOB 下推、目录派生的分组标题与季子节点过滤、各视图/历史/统计全被过滤、只隐藏不删行、关闭后全恢复 |
| `tests/test_cache_keys.py` | 7 | 缓存键不得使用文件 mtime 的源码级守护、写进度不改指纹但改签名、增删媒体/看过的列表改指纹、TTL 过期 |
| `tests/test_changed_tests.py` | 16 | 增量测试映射契约：`IMPACT` / `FRONTEND_MODULES` 不漏项、拒绝无授权全量 |
| `tests/test_concurrency.py` | 10 | 4 进程真并发不丢更新、短锁等待与重试策略、`BEGIN IMMEDIATE` 避开快照冲突、`integrity_check`、WAL 首开 `-shm` 争抢 |
| `tests/test_continue_dismiss.py` | 15 | 继续观看软删除、历史按作品只留最新、旧会话零删除 |
| `tests/test_covers.py` | 23 | AGE / Seeduck 页面解析（统一走 `app/sites.py`）、只取 `data-original`、占位图排除、精确与严格选取阈值、封面文件名哈希 |
| `tests/test_daemon_autostart.py` | 38 | 守护自动启动幂等、开关关闭立即停止、`Api.__init__` 绝不拉起进程 |
| `tests/test_db.py` | 10 | 建表、WAL、设置读写与容错、旧库迁移不丢数据（含 `work_meta` / `scrape_cache` / 集数两列）、外键级联、幂等初始化 |
| `tests/test_docs.py` | 16 | 版本语义化与一致性、交付文档齐备、前端缓存串、增量测试制度契约、v0.1.6 路线图要素、Obsidian 接线 |
| `tests/test_embedded_player.py` | 23 | `SetParent` 嵌入、HWND 记录、失败降级外部播放、退出 `detach`、库外文件 `external` |
| `tests/test_everything.py` | 9 | `es.exe` → HTTP → SQL 三级降级、Error 8 会话隔离提示 |
| `tests/test_everything_open.py` | 16 | `es.exe` 空串回退、HTTP 认证、打开主程序与文件、降级时给出可操作补法 |
| `tests/test_importer.py` | 24 | 行解析（季/集/范围/CSV/TSV/年份/注释）、去重、预览即时匹配、提交去重、用户修正记忆、未匹配进待确认区 |
| `tests/test_importer_realworld.py` | 17 | 真实清单脏数据、季集范围、异常行容错 |
| `tests/test_listimport.py` | 21 | 三段式入口（纯文本 / MAL XML / Bangumi Takeout JSON）、差异报告五类、只增不减合并、中文名只补空、`match_by='user'` 不覆盖、整体幂等 |
| `tests/test_manual_cleanup.py` | 36 | 删单条历史会话、整剧删本地文件进回收站、拒危险路径、单次上限 300 |
| `tests/test_manual_cover.py` | 23 | 人工封面魔数与体积校验、`source=manual` 优先、`force` 也不覆盖、撤销 |
| `tests/test_media_browser.py` | 12 | 分页总数与旧上限、搜索字段、组合筛选、全量模式与排序、注入安全、点击播放、跳转返回入口契约、版本下拉 |
| `tests/test_media_tree.py` | 14 | 作品 → 季 → 集分组、无季文件直挂 `series.files`、工作标签重命名、`season_no` 下发、`_collapse_versions` 展示层折叠 |
| `tests/test_metadata.py` | 32 | 归一化/季数解析、内置词典完整性、离线命中、用户修正优先、在线结果即时学习、ID 直查优先级、限速与负缓存、`title_lang_pref` |
| `tests/test_obsidian.py` | 26 | vault 探测、路径穿越防护、笔记生成、AUTO 区块外批注保留、日志去重 |
| `tests/test_packaging.py` | 27 | 四种路径布局、两种形态启动命令、开机自启、守护/窗口/自检分发、安装器 `--static` 七项核对 |
| `tests/test_player_controls.py` | 36 | `WM_COMMAND` 命令号、三通道逐条退让、`POT_GET_PLAY_STATUS` 复查、倍速与全屏虚拟键、动作白名单、默认步进 0.25 踩满全部预设 |
| `tests/test_poster_watched.py` | 14 | 剧集墙五态判定、进度优先于看过的列表、宽松匹配不误标、导入后缓存失效 |
| `tests/test_potplayer.py` | 20 | 时间格式化、窗口标题反查、进度百分比与钳制、`/seek` 参数组装 |
| `tests/test_quicksearch.py` | 7 | 全盘快搜独立入口与导航视图一致性契约 |
| `tests/test_scanner.py` | 41 | 字幕组命名、`SxxEyy`、中文话数、忽略规则、增量扫描、目录停用、集数置信度三档、`第03话` 不误判为季、`Season 2 - 05` 不误判区间、指纹重关联、路径重定位 |
| `tests/test_smoke.py` | 2 | 端到端串联（导入 → 扫描 → 播放记录 → 续播 → 纠错 → 历史） |
| `tests/test_startup.py` | 15 | 单实例、`js_api` 反射安全、播放器消息超时、一键启动脚本 |
| `tests/test_stats.py` | 25 | 观看时长统计口径（快进不计）、继续观看按作品去重置顶、按季海报墙、多版本合并挑主条目、评分与标签持久化、观看日历 |
| `tests/test_sync_dedupe.py` | 9 | 按 `(title, 季)` 合并成区间行、幂等、清理旧的「每集一条」 |
| `tests/test_theme.py` | 9 | 主题切换持久化、SVG 图标数量（14 个视图）、导航与视图元信息一致 |
| `tests/test_theme_native.py` | 13 | DWM 属性设置与失败降级 |
| `tests/test_watched_cleanup.py` | 37 | 清洗只改 `title`/`title_cn`/`match_by`、卡片墙结构、四种视图与默认平铺表格 |
| `tests/test_watched_folders.py` | 22 | 看过的列表 → 资源管理器、`is_safe_directory`、上退层数不跨扫描根 |
| `tests/test_watched_list.py` | 12 | 看过的列表快速搜索与行内改名 |
| `tests/test_watched_season.py` | 10 | 季数双击编辑、`2` / `S3` / `第4季`、非法值拒绝 |
| `tests/test_watcher.py` | 17 | 轮询写进度、完成三阈值、切集结算、外部文件登记、真实观看时长只累加连续播放 |
| `tests/test_watchlist.py` | 49 | 导入、两栏互转、↑↓ 调序、匹配、未明确不得重灌 |
| `tests/test_watchlist_top.py` | 9 | 同栏置顶、已置顶不移动、跨栏隔离、按钮等宽右对齐 CSS |
| `tests/test_websearch.py` | 20 | 两站真实结构解析（统一走 `app/sites.py`）、占位图排除、绝对 URL、来源标记、单站失败降级、`parse_health` 下发、`open_url` 白名单 |
| **合计** | **863** | v0.1.6 相对 0.1.5 的 709 新增 **154** 项，测试模块 38 → 43 个 |

### 隔离机制

`tests/base.py::IsolatedDbTestCase` 在每个用例：

- 把 `config.DATA_DIR / DB_PATH / COVER_DIR / LOG_DIR / ALIAS_IMPORT_FILE` 指向临时目录；
- 重置 `db._INITIALIZED`、`metadata._BUILTIN_READY`、`_INDEX`、`_ONLINE_BLOCKED` 等进程内缓存；
- 用 `mock.patch` 屏蔽 `metadata.search_online`，**测试永不发真实网络请求**；
- 结束后恢复所有配置并清理临时目录。

因此测试**不会读写** `data/library.db`，也不会启动真实 PotPlayer。

---

## 3. TDD 过程与发现的缺陷（v0.1.2 历史记录）

按「先写测试看红 → 修实现到绿」执行，共三轮：

| 轮次 | 结果 |
|---|---|
| 第 1 轮（v0.1.2） | 116 用例，15 失败 + 10 错误（25 项红） |
| 第 2 轮 | 1 失败 + 3 错误（其中 3 项为文档尚未产出） |
| 第 3 轮 | 1 失败（仅缺 `docs/TESTING.md`） |
| 最终 | **全绿** |

### 修复的实现缺陷（22 项）

**扫描与文件名**
1. `SP` 短标记用子串匹配，`Spy x Family` / `Special Edition` 被误判为特典跳过。
2. `Show.S01E02.mkv` 的作品名残留 `S01E02`。
3. `Movie.2019.mkv` 的作品名残留年份。
4. `Title - 12.mkv`（集数在结尾）解析不出集数。
5. `Season 2 - 05` 结尾集数解析不出。
6. `第2期` 在清单导入中无法识别为第 2 季。
7. `is_file()` 抛 `OSError` 会中断整次扫描（文件被占用/权限不足）。

**中文匹配**
8. 用户手工修正被内置词典覆盖（索引 `setdefault` 顺序错误）。
9. 模糊包含匹配绕过季数保护，把带 `S2` 的标题套成第一季。
10. 词典未收录该季时无兜底，只能显示「未匹配」。
11. 大小写/空格不同的同一标题被重复查询。
12. `local_only` 模式虚报 `online_available = true`。
13. `apply_to_media` / `apply_to_watched` 返回匹配条数而非真实更新行数。
14. 在线 User-Agent 硬编码旧版本号。

**播放记录与纠错**
15. 守护**首次轮询不写进度**：完成判定与时长回填要等位置变化才生效。
16. 缺少「标记已看 / 标记未看 / 重置进度」纠错入口。
17. `completed` 在重看开头后被回退（本轮明确保留 `MAX(completed, ?)` 语义，并新增显式纠错方法）。
18. 设置白名单漏了 `minimize_to_tray`。

**健壮性与输出**
19. `potplayer.py` 模块级 `ctypes.windll` 在非 Windows 导入即崩。
20. `autostart.py` 模块级 `import winreg` 在非 Windows 导入即崩。
21. 前端把「在线通道已启用」显示为「在线检索可用」，与未实测的网络可达性混淆。
22. `scanner.py` / `importer.py` 中过时的「P0 基础版 / P1 再做」注释与实际能力不符。

另有 3 项测试用例自身问题（`config_path` 未定义、`Path.stat` 全局 patch 过宽、季数断言选了词典已收录的别名），已同步修正。

---

## 4. 端到端自测记录

### 4.1 CLI 冒烟（真实数据库，只读为主）

```
python cli.py init      → 数据库已就绪；表数量 9（业务表 8 + SQLite 自动 sqlite_sequence）
python cli.py status    → {"files":1,"watched":0,"sessions":1}
python cli.py dict      → builtin_terms 98 / alias_total 267 / builtin 265 / learned 2 / unmatched 0
python cli.py progress  → [{file_id:1, title:"更衣人偶坠入爱河", position_ms:761929,
                            duration_ms:1420155, percent:53.65, completed:0}]
python cli.py player    → {"available": false, "reason": "PotPlayer 未运行"}
python cli.py mark 999999 watched → {"ok": false, "error": "找不到该文件记录"}
python cli.py mark 999999 reset   → {"ok": false, "error": "找不到该文件记录"}
python cli.py test      → 调用 run_tests.py，结果与直接运行一致
```

> 说明：用户休息后 PotPlayer 已关闭，本轮 `player` 如实返回「未运行」。
> 进度回读（总时长 23:40 / 当前 17:16 / 状态暂停）是 2026-11-14 在播放器运行时实测通过的能力，
> 本轮由 `tests/test_potplayer.py` 用模拟窗口消息覆盖了换算、钳制与降级逻辑。

### 4.2 旧数据保留

```
version: 0.1.2
media:   1 条，status=external，title_cn=更衣人偶坠入爱河，match_by=native
sessions: 1 条    progress: 1 条（761929ms / 53.65% / completed=0）
watched: 0 条     aliases: 267 条
```

v0.1.0 的真实观看会话与断点进度在 v0.1.1 迁移和 v0.1.2 重构后**完整保留**。

### 4.3 原生窗口与前后端桥接

```
pywebview (edgechromium) hidden 窗口启动 → GUI_START_OK
页面标题：总览
js_api 方法数：31，mark_watched 类型 function
8 页渲染：
  home len=1807  continue len=1130  history len=385  library len=922
  watched len=389  import len=898  scan len=1298  settings len=2691
  全部 failed=False，全部无「WorkBuddy 定时自动化 / 导出待补全清单」旧文案
继续观看页新按钮：mark-watched 已渲染（存在进行中进度）
```

> `mark-unwatched` 按钮未出现是正常的：当前真实库没有 `completed=1` 的条目，
> 「已看完（可纠错）」区块显示为空态；该分支已由 `tests/test_api.py` 与端到端用例覆盖。

### 4.4 中文名即时匹配实测

```
Frieren          → 葬送的芙莉莲        [builtin]
Attack on Titan  → 进击的巨人          [builtin]
Attack on Titan S2 → 进击的巨人 第二季  [季名映射]
Sono Bisque Doll wa Koi wo Suru S2 → 更衣人偶坠入爱河 第二季
Totally Unknown Show XYZ → 未匹配（可手改，改后永久记住）
```

---

## 5. 如何复现

```bat
cd /d "D:\project\magic video"

python -m compileall -q app tests cli.py run.py daemon.py run_tests.py
node --check web/app.js
python run_tests.py            :: 或 python cli.py test
```

窗口冒烟可用 `webview.create_window(..., hidden=True)` + 线程内 `evaluate_js` 逐页检查，模板见 `AGENTS.md` 第 5 节。

---

## 6. 发版门禁（仅用户明确要求发版时执行）

> 日常需求提交**只需 `python run_changed_tests.py` 增量全绿**（改了 Python / JS 再补 `compileall` / `node --check`），**不要跑全量 `python run_tests.py`**；全量只在本报告第 0 节的封闭白名单三种情形下执行。以下清单是发版专属，见 `AGENTS.md` 3.0 与 3.0.1 —— 不要每个需求都打包、打 tag，也不要每个需求都跑全量。

- [ ] `compileall` 通过
- [ ] `node --check web/app.js` 通过
- [x] `python run_tests.py` 173 个用例全绿
- [ ] 旧数据保留已确认
- [ ] README / AGENTS / CHANGELOG / docs/TESTING.md / docs/OBSIDIAN.md 版本一致（`tests/test_docs.py` 守护）
- [ ] 真实 vault 同步通过，且用户原有笔记未被改动
- [ ] `git status` 干净，无 `data/` 产物被暂存

---

## 7. 未覆盖 / 待人工验证

| 项 | 原因 | 建议 |
|---|---|---|
| 真实 PotPlayer 进度回读 | 本轮执行时播放器未运行 | 打开任意视频后跑 `python cli.py player` |
| 真实 `/seek` 续播效果 | 需人眼确认跳转位置 | 在「继续观看」点一集，观察是否从断点开始 |
| 在线 Bangumi 实时刮削 | 本机直连超时（已知环境限制） | 在设置里填可用代理后点「重新匹配」 |
| 托盘常驻与开机自启 | 需真实桌面会话与重启验证 | 双击 `start_daemon.bat`，检查托盘菜单 |
| GitHub 远程推送 | 无 gh CLI / 无凭证 | 用户提供 PAT 或启用连接器后执行 |
| 多进程并发写压力 | 单元测试只验证 WAL 与短事务 | 守护 + 主窗口同时开着扫描一次大目录 |

---

## 8. v0.1.3 增量自测（Obsidian 集成）

### 8.1 TDD 增量

| 轮次 | 结果 |
|---|---|
| 第 1 轮 | 新增 26 个用例，1 项红：`open_notes_folder` 不支持自定义文件夹参数，导致打开默认目录 |
| 修复 | 给 `open_notes_folder(vault, folder)` 增加 `folder` 参数并透传 `resolve_vault()` |
| 最终 | 全量 **144 / 144 全绿**，0 失败 / 0 错误 / 0 跳过，耗时 14.94 秒 |

### 8.2 真实命令与输出

```bat
python -m compileall -q app tests cli.py run.py daemon.py run_tests.py   :: PY_OK
node --check web/app.js                                                 :: JS_OK
python run_tests.py                                                     :: 144 个用例全绿，写 data/test_last.json
python cli.py obsidian vaults                                           :: 探测到 2 个 vault
python cli.py obsidian path "C:\Users\Administrator\Documents\Obsidian Vault"
python cli.py obsidian status                                           :: resolvable=true, folder=Magic Video
python cli.py obsidian sync                                             :: 首次同步
```

首次同步真实返回（摘要）：

```json
{"ok": true, "created": 6, "updated": 0, "skipped": {}, "log": "created",
 "roadmap_items": 17, "test": {"total": 144, "passed": 144, "failures": 0, "errors": 0},
 "commit": "2d87892", "version": "0.1.3"}
```

生成文件 7 个（约 40 KB）：6 篇笔记 + `06 开发日志/2026-11-15.md`，含 frontmatter、`managed-by` 标记、
AUTO 区块、批注区与 `[[wikilink]]` 互链。路线图解析出 17 条 P0–P3 建议，与 `docs/OPTIMIZATION.md` 一致。

### 8.3 安全边界实测

| 验证项 | 方法 | 结果 |
|---|---|---|
| 原有笔记零改动 | 同步前后 `md5sum` 对比 `设计方案模板.md` / `详细设计方案.md` | 两个 md5 完全一致（`e25a15a…` / `40c0d8f…`） |
| 命名空间外零写入 | 检查 vault 顶层是否出现新笔记 | 仅 `Magic Video/` 文件夹，顶层无新文件 |
| 手写批注保留 | 在 `00 项目总览.md` 批注区写一行，再同步 | 二次同步 `updated: 6`，批注行仍在；验证后已清理该自测行 |
| frontmatter 用户字段保留 | 单测插入 `aliases` / `cssclasses` 后再同步 | 两个字段均保留 |
| 日志去重 | 同状态连续同步两次 | 第二次 `log: duplicate`，`<!-- event:` 仍为 1 条 |
| 结果历史去重 | 检查 `data/obsidian_history.json` | 同状态只留 1 条事件 |
| 路径穿越 | 单测传入 `../outside`、`C:\Windows`、`/etc` | 全部 `ValueError`，`sync` 返回 `ok=false` 不抛异常 |
| 同名用户笔记 | 单测预置无标记的同名文件 | `skipped_unmanaged`，内容不变 |

### 8.4 窗口冒烟

`webview.create_window(..., hidden=True)` + 线程内 `evaluate_js`：

- 8 个页面全部渲染成功，`len(innerHTML)` 385–4104，无「页面加载失败」；
- `js_api` 暴露 **35 个方法**（v0.1.2 为 31），新增 4 个 Obsidian 桥接方法就绪；
- 设置页：`Obsidian 知识沉淀` 卡片 ✅、`obsidian-sync` 按钮 ✅、`obsidian_auto_sync` 开关 ✅；
- `typeof window.pywebview.api.obsidian_sync` → `function`。

### 8.5 本版未覆盖 / 待人工验证

| 项 | 原因 | 建议 |
|---|---|---|
| Obsidian GUI 里实际打开与图谱效果 | 本机 Obsidian 进程未运行，笔记是标准 Markdown 无插件依赖 | 启动 Obsidian 选主库，打开 `Magic Video/00 项目总览` |
| `cli.py test` 自动同步触发 | `obsidian_auto_sync` 默认关闭（设计如此） | 设置页打开开关后跑一次 `python cli.py test` |
| 手写批注长期保留 | 单测 + 一次真实二次同步已验证 | 日常使用中随时可再验 |

---

## 9. 打包产物与版本回溯增量自测（0.1.3）

> 本节是第 8 节之后的增量记录，记录 0.1.3 当时的实测值；**当前全量为 173**，见第 10 节。

### 9.1 构建链实测

| 步骤 | 命令 / 工具 | 结果 |
|---|---|---|
| 依赖环境 | `.venv` + `requirements.txt` + `pyinstaller` | PyInstaller 6.22.3 / pywebview 6.2.1 / pythonnet 3.2.1 / clr_loader 0.3.1 安装成功 |
| exe | `PyInstaller --onedir --windowed --collect-all webview/clr_loader/pystray` | `Magic Video.exe` 6,656,482 B；onedir 共 217 个文件 |
| 绿色版 | `build_dist.py` → `zipfile` | `MagicVideo-0.1.3-win64-portable.zip`，21.8 MB / 218 条目 |
| MSI | WiX 3.14.1 `heat` → `candle` → `light` | `MagicVideo-0.1.3-win64.msi`，21.9 MB / 217 个程序文件 |
| 工具下载 | GitHub Release 直连 | ❌ `curl(35) schannel` 握手失败；改用 NuGet 镜像 `wix.3.14.1.nupkg` ✅ |
| MSI 首次编译 | `light.exe` | ❌ `LGHT0311`：中文描述串超出 1252 代码页 → 改为英文描述后重跑 ✅ |

### 9.2 产物验收（真实执行，非推断）

| 检查 | 方法 | 结果 |
|---|---|---|
| exe 自检 | `MAGIC_VIDEO_DATA_DIR=<临时目录> "Magic Video.exe" --selftest` | `ok=True` / `version=0.1.3` / `frozen=True`；`webview`、`clr`、`clr_load`、`pystray`、`PIL`、`psutil`、`requests`、网页、词典、数据库 **10 项全通过** |
| 路径分离 | 自检报告的 `resource_dir` / `data_dir` | 资源 = `...\Magic Video\_internal`，数据 = 临时目录；**未写进解压目录** |
| 主窗口 | 启动 exe 后 8 秒检查进程 | 进程存活，临时数据目录生成 `library.db`；随后终止，未污染真实库 |
| ZIP 内容 | `zipfile` 逐项核对 | exe / `portable.txt` / `web/index.html` / `aliases_builtin.json` / `docs/OPTIMIZATION.md` / `CHANGELOG.md` / `_internal/webview/lib/Microsoft.Web.WebView2.*.dll` 齐全 |
| MSI 内容 | `dark.exe -x` 解包 + 反编译 wxs | `ProductVersion=0.1.3`、固定 `UpgradeCode`、开始菜单 `Shortcut`；主 exe / WebView2 DLL / 词典 / 前端按大小比对均在包内；`portable.txt` 不在 MSI 内（安装版走 `%APPDATA%`） |
| 单测 | `python run_tests.py` | **161 / 161 全绿**（0 失败 / 0 错误 / 0 跳过，20.69 秒） |
| 语法 | `compileall` + `node --check` | 全通过 |

### 9.3 未覆盖 / 待人工验证

| 项 | 原因 | 建议 |
|---|---|---|
| MSI 实际安装与卸载 | 未在用户机器上执行安装（避免改动系统状态） | 双击 `.msi` 安装 → 开始菜单启动 → 控制面板卸载 |
| 绿色版跨版本升级不丢数据 | 已在代码层与自检中验证路径分离 | 解压两个版本，把旧 `data/` 放进新目录后确认记录仍在 |
| 打包版托盘守护长时间记录 | 冒烟只验证主窗口 8 秒 | 运行 `Magic Video.exe --daemon` 后用 PotPlayer 播放一集，检查历史写入 |

---

## 10. v0.1.4 增量自测（全量文件列表与点击播放）

### 10.1 代码与真实数据（已实测）

| 检查 | 命令 / 方法 | 结果 |
|---|---|---|
| 全量单元测试 | `.venv\Scripts\python.exe run_tests.py` | **173 / 173 全绿**（0 失败 / 0 错误 / 0 跳过，25.44 秒），新增 12 个用例（11 个文件浏览 + 1 个前端缓存守护） |
| Python 语法 | `compileall -q app tests *.py` | 通过 |
| JavaScript 语法 | `node --check web/app.js` | 通过 |
| 真实库副本（不碰原库） | `sqlite3.Connection.backup()` 到临时目录 → `Api.get_media_files_page` | 默认第 1 页 200 条；`total=total_all=2104`、`pages=11`、`matched=324`；4 个目录选项 |
| 一次显示全部 | `{"page_size": 0}` | 返回 2104 行，`pages=1`，区间 1–2104 |
| 搜索 | 关键词 `家里蹲` / `吸血姬` / `DBD-Raws` / `100%` | 分别 17 / 17 / 296 / 0 条；`%` 按字面量，不会放大结果 |
| 筛选 | `directory_id=1`、`match_state=matched` | 207 条 / 324 条 |
| 排序 | `sort=name` | 按文件名升序返回；非法排序串安全降级且表未被注入 |
| 旧 400 条上限移除 | 造 450 条数据验证 `total=450` / 9 页 | 通过（`tests/test_media_browser.py`） |

### 10.2 产物验收（0.1.4，真实执行）

| 检查 | 方法 | 结果 |
|---|---|---|
| 构建链 | `.venv\Scripts\python.exe build_dist.py` | ZIP 21.8 MB / MSI 22.0 MB；WiX `candle -dProductVersion=0.1.4` |
| exe 自检 | 构建脚本内 `Magic Video.exe --selftest` | `ok=True` / `version=0.1.4` / `frozen=True`；资源在 `_internal`、数据在临时目录 |
| ZIP 内容 | `zipfile` 逐项核对 | 218 条目；exe / `portable.txt` / 网页 / 内置词典 / docs 齐全；`webview/lib/Microsoft.Web.WebView2.*.dll` 在包内 |
| 打包版前端 | 读 ZIP 内 `web/index.html` 与 `web/app.js` | 缓存串 `app.js?v=0.1.4` 存在；含 `get_media_files_page` / `fileListBox` / `file-all`，旧 400 条上限已移除 |
| MSI 属性 | `dark.exe` 反编译 wxs | `Version="0.1.4"`、固定 `UpgradeCode`、开始菜单 `Shortcut` |
| 主窗口 | 启动 `Magic Video.exe` 8 秒 + 临时 `MAGIC_VIDEO_DATA_DIR` | 进程存活，临时目录生成 `library.db`；随后终止，未污染真实库 |

### 10.3 升级防缓存（本轮实测发现）

- 首次隐藏窗口冒烟读到的仍是**旧界面**：`app/main.py` 的 `private_mode=False` 让 WebView2 复用了持久 profile 里缓存的旧 `app.js`。
- 修复：`private_mode=True`（临时 profile）+ `web/index.html` 的 `app.js?v=<版本>` 双保险，并新增 `tests/test_docs.py::test_frontend_is_not_served_from_stale_cache` 守护，防止以后发版漏改缓存串。
- 纯 JS 层用 Node `vm` 模拟 DOM 验证：`renderScan` / `renderLibrary` 均渲染出 `fileListBox` 与搜索框，`fileListRows` 生成可点击播放链接，`loadFileList` 正确传入 6 个查询参数并只局部刷新列表容器。

---

## 11. v0.1.5 发版门禁（一天 27 次迭代的集中验收）

本版把 0.1.4 之后累计的 27 个本地提交一次性发布，因此门禁按「发版」标准执行：
`compileall` + `node --check` + **全量 `run_tests.py`** + 打包链 + 产物验收。

### 11.1 代码与用例（真实输出）

| 检查 | 命令 | 结果 |
|---|---|---|
| Python 语法 | `.venv\Scripts\python.exe -m compileall -q app tests *.py` | `PY_OK` |
| JavaScript 语法 | `node --check web/app.js` | `JS_OK` |
| 全量单元测试 | `.venv\Scripts\python.exe run_tests.py` | **709 / 709 全绿**（0 失败 / 0 错误 / 0 跳过，83.69 秒），快照写入 `D:/magicvideo/test_last.json` |
| 用例规模 | `TestLoader().discover('tests')` | 709，测试模块 38 个（0.1.4 为 173 / 12 个） |

### 11.2 真实库接口耗时（`MAGIC_VIDEO_DATA_DIR=D:/magicvideo`，2105 个文件）

| 接口 | 首次 | 命中缓存 |
|---|---:|---:|
| `get_poster_wall`（193 张卡片） | 467 ms | 13 ms |
| `get_media_tree`（1954 个节点） | 177 ms | 11 ms |
| `get_media_files_page`（每页 50） | 151 ms | — |
| `get_stats` | 60 ms | — |
| `get_watched_list` | 17 ms | — |
| `get_watchlist`（762 条） | 331 ms | — |
| `get_history` / `get_continue_watching` | 5 ms / 5 ms | — |

剧集墙首次比 0.1.4 的 1105 ms 降了约 58%，其中约 200 ms 是首次构建「看过的列表」匹配索引（之后按数据指纹缓存，守护写播放进度不会让它失效）。
真实库 193 张卡片的观看状态分布：`none` 188 / `listed` 2 / `partial` 1 / `done` 1 / `watching` 1——`listed` 的 3 张逐条人工核对过，全部为真匹配。

### 11.3 产物验收（0.1.5，真实执行）

| 检查 | 方法 | 结果 |
|---|---|---|
| 构建链 | `.venv\Scripts\python.exe build_dist.py` | ZIP 22.1 MB / MSI 22.2 MB；WiX `candle -dProductVersion=0.1.5` |
| exe 自检 | 构建脚本内 `Magic Video.exe --selftest` | `ok=True` / `version=0.1.5` / `frozen=True`；资源在 `_internal`、数据在临时目录 |
| ZIP 内容 | `zipfile` 逐项核对 | 218 条目；`Magic Video/Magic Video.exe`、`portable.txt`、`_internal/web/`、`_internal/app/data/aliases_builtin.json`、`_internal/webview/lib/Microsoft.Web.WebView2.*.dll` 齐全 |
| 打包版前端 | 读 ZIP 内 `_internal/web/index.html` 与 `app.js` | 缓存串 `app.js?v=0.1.5` 存在；含 `web_search` / `renderWebSearch` / `posterStateTag` / `watchedCardsHtml` / `goto-back` |
| MSI 属性 | `dark.exe` 反编译成 `product.wxs` | `Version="0.1.5"`、`Name="Magic Video"`、`UpgradeCode` 与历史版本一致 |

> 打包踩坑（已固化到 AGENTS §8）：PyInstaller 的 `COLLECT` 会先删旧 `build/pyi-dist/Magic Video`，该操作被安全删除策略拦下导致构建失败。正确做法是**先把旧目录 `mv` 成 `Magic Video.prev`**，再跑 `build_dist.py`。

### 11.4 本版未覆盖 / 待人工验证

- **MSI 在干净机器上的安装 / 升级 / 卸载**：只验证了版本属性与文件清单，未在无 Python 环境的机器上跑安装向导。
- **内嵌播放的真实窗口行为**：`SetParent` 单元测试全部 mock Win32 调用，真实嵌入效果需在有 PotPlayer 的桌面上手动确认。
- **播放控制指令的跨版本兼容**：`via=command:10014` 只在本机 PotPlayer 上实测过；不同版本对 `WM_COMMAND` 命令号的响应需实机确认。
- **网页搜索的两站可用性**：依赖外网，站点改版会让解析规则失效（已做单站失败降级，但两条都挂时搜索页为空）。
- **黑名单字形表**：只收了字形一一对应的异体（日文新字体 / 繁体 / 旧字形），未做乾/干、著/着这类多义合并；遇到新字型仍需补表。

---

## 12. v0.1.6 发版门禁（数据地基 + 体验层）

> 本版把 2026-11-16 识别的三类薄弱处（数据地基 / 边界完整性 / 外部依赖脆弱）一次性补齐，
> 共 17 项 P0/P1/P2 需求 + 补强 C-1/C-2 + OPTIMIZATION #5/#6 尾巴。需求与逐项结论见 `docs/ROADMAP_0.1.6.md`。

### 12.1 门禁结果

| 检查 | 命令 | 结果 |
|---|---|---|
| Python 语法 | `.venv\Scripts\python.exe -m compileall -q app tests *.py` | `PY_OK` |
| JavaScript 语法 | `node --check web/app.js` | `JS_OK` |
| 全量单元测试 | `.venv\Scripts\python.exe run_tests.py` | **863 / 863 全绿**（0 失败 / 0 错误 / 0 跳过，123.04 秒），快照写入 `D:/magicvideo/test_last.json` |
| 用例规模 | `TestLoader().discover('tests')` | 863，测试模块 43 个（0.1.5 为 709 / 38 个） |
| 安装器静态核对 | `.venv\Scripts\python.exe installer\verify_install_flow.py --static` | `[PASS] 安装器静态核对通过`（七项 WiX 属性） |

### 12.2 本版新增能力的验证方式

| 能力 | 验证手段 | 覆盖用例 |
|---|---|---|
| JSON 备份 / 恢复 | 三模式往返、恢复后原 id 可继续查进度、恢复前备份文件存在 | `tests/test_backup.py`（20） |
| 批量标记 | 只改 `watch_progress`、`watch_sessions` 行数不变、`completed` 取 MAX | `tests/test_api.py` |
| 文件指纹重关联 | 唯一候选 + 旧路径不存在 + 指纹一致才迁移；副本与多候选不合并 | `tests/test_scanner.py` |
| 并发写加固 | 4 进程真并发累加同一行不丢更新、`integrity_check` 通过 | `tests/test_concurrency.py`（10） |
| 清单三段式对齐 | MAL XML / Bangumi Takeout / 纯文本三入口产出同一 record，二次导入幂等 | `tests/test_listimport.py`（21） |
| 路径重定位 | 不可达只标 `missing_since`，`apply_relocate` 后行数不变、进度不变 | `tests/test_scanner.py` |
| 看片统计 | 快进增量不计入、重看累加、黑名单剔除、口径字段随返回下发 | `tests/test_stats.py`（25） |
| 按季海报墙 / 继续观看 | `season_cards` 季数与作品一致、继续观看按作品去重只留最新 | `tests/test_stats.py` |
| 多版本合并 | 同一 `(季, 集)` 折叠为一条、`file_count` 不虚高、各版本进度独立 | `tests/test_stats.py` / `tests/test_media_tree.py` |
| 缓存键守护 | 源码出现 `mtime` / `st_ctime` / `getmtime` 直接失败 | `tests/test_cache_keys.py`（7） |
| 集数置信度 | `SxxEyy`/`EP12`/`第03话`=high，`1x01`/结尾数字=medium，`[01]`/`01-02`=low | `tests/test_scanner.py` |
| CSV / NFO 导出 | 三张表列头固定、UTF-8 BOM、源视频目录文件数不变 | `tests/test_backup.py` |
| 安装器静态层 | 目录 Id 全大写、`Secure`、`InstallPath` 回读、`MajorUpgrade`、ARP 走 Type 51 | `tests/test_packaging.py` |

### 12.3 产物验收（0.1.6，真实执行）

| 检查 | 方法 | 结果 |
|---|---|---|
| 构建链 | `.venv\Scripts\python.exe build_dist.py` | ZIP **22.2 MB** / MSI **22.4 MB**；WiX `candle -dProductVersion=0.1.6` |
| exe 自检 | 构建脚本内 `Magic Video.exe --selftest` | `ok=True` / `version=0.1.6` / `frozen=True`；资源在 `_internal`、数据在临时目录 |
| ZIP 内容 | `zipfile` 逐项核对 | **220 条目**；`Magic Video/Magic Video.exe`、`portable.txt`、`_internal/web/index.html`、`_internal/web/app.js`、`_internal/app/data/aliases_builtin.json`、`_internal/webview/lib/Microsoft.Web.WebView2.*.dll`（2 个）齐全 |
| 打包版前端 | 读 ZIP 内 `_internal/web/index.html` 与 `app.js` | 缓存串 `app.js?v=0.1.6` 存在；本版新增的 `renderStats` / `rate-work` / `season_cards` / `export-csv` / `version-select` / `poster-mode` 六处标记全部命中 |
| MSI 属性 | `installer/inspect_msi.py` 直读 Property 表 | `ProductVersion='0.1.6'`、`ProductName='Magic Video'`、`UpgradeCode='{8F3A1C2E-...}'` 与历史版本一致、`WIXUI_INSTALLDIR='APPFOLDER'` |
| 安装器静态层 | `installer/verify_install_flow.py --static` | `[PASS]`：目录 Id 全大写 + `Secure` + `InstallPath` 写入/回读 + `MajorUpgrade` + ARP 走 Type 51 |

> 打包踩坑（已固化到 AGENTS §8）：PyInstaller 的 `COLLECT` 会先删旧 `build/pyi-dist/Magic Video`，该操作被安全删除策略拦下导致构建失败。正确做法是**先把旧目录 `mv` 成 `Magic Video.prev`**（已有的 `.prev` 顺延成 `.prev-0.1.5`），再跑 `build_dist.py`。

### 12.4 本版未覆盖 / 待人工验证

- **MSI 在干净机器上的安装 / 带路径升级 / 卸载 / 重装**：`--static` 只能核对 WiX 属性，真机 7 步需按 `installer/verify_install_flow.py` 打印的 `MANUAL_CHECKLIST` 人工跑一次。
- **内嵌播放与播放控制的真实窗口行为**：单测全部 mock Win32 调用，真实嵌入效果与 PotPlayer 版本兼容性需在有播放器的桌面上手动确认。
- **AGE动漫 / Seeduck 站点结构变更**：解析已收敛到 `app/sites.py` 单一适配层，但站点改版会让规则失效；`parse_health()` 会计数并在网页搜索页暴露，两条源都挂时搜索页为空。
- **libmpv / libVLC 内核替换**：调研结论维持「暂缓」，见 `docs/OPTIMIZATION.md` §8。

---

## 13. v0.1.7 打包门禁（4 个修复固化进安装包）

> 本版无新功能面，只把 0.1.6 发版后暴露的 4 个问题收进安装包：追剧词序容错、同步季号折算、封面随包分发、改名传播。逐项细节见 `docs/IMPLEMENTATION_NOTES.md` §12 / §13 与 `docs/FEATURES.md` §29 / §30。

### 13.1 触发原因

0.1.6 的 ZIP / MSI 是在 4 个修复提交之前打的，**已安装用户拿不到这些修复**。本版唯一目的就是让安装包与源码一致。

### 13.2 门禁结果（真实执行）

| 检查项 | 说明 |
|---|---|
| 增量用例 | 4 个修复各自落地时均跑 `run_changed_tests.py`（末轮 399/399） |
| 全量用例 | `python run_tests.py` → **900 / 900 通过**，0 失败 / 0 错误 / 0 跳过，**118.74 秒**；快照写入 `D:\magicvideo\test_last.json` |
| Python 语法 | `compileall` 全通过 |
| JavaScript 语法 | `node --check web/app.js` 通过 |
| 版本一致性 | `tests/test_docs.py` 16 例通过：`__version__` / README / AGENTS / CHANGELOG / OBSIDIAN / `app.js?v=` 全部为 `0.1.7` |

> 本次全量属白名单第 ② 条（用户明确要求打包出安装包），非「顺手兜底」。

### 13.3 产物验收（0.1.7，真实执行）

| 检查 | 方法 | 结果 |
|---|---|---|
| 构建链 | `.venv\Scripts\python.exe build_dist.py` | ZIP **25.9 MB**（27,198,677 字节）/ MSI **25.3 MB**（26,546,212 字节）；WiX `candle -dProductVersion=0.1.7` |
| exe 自检 | 构建脚本内 `Magic Video.exe --selftest` | `ok=True` / `version=0.1.7` / `frozen=True`；资源在 `_internal`、数据在临时目录 |
| ZIP 内容 | `zipfile` 逐项核对 | **302 条目**（0.1.6 为 220，多出 82 条封面资产）；`Magic Video/Magic Video.exe`、`portable.txt`、`_internal/web/index.html` 齐全 |
| 打包版前端 | 读 ZIP 内 `_internal/web/index.html` | 缓存串 `app.js?v=0.1.7` 存在 |
| 封面资产 | 读 ZIP 内 `_internal/assets/covers/` | 80 张封面 + `index.json`（`schema` / `count` / `generated_at` / `covers` 四段）+ `README.md`，共 82 条 |
| MSI 属性 | `installer/inspect_msi.py dist/MagicVideo-0.1.7-win64.msi` | `ProductVersion='0.1.7'`、`ProductName='Magic Video'`、`UpgradeCode='{8F3A1C2E-...}'` 与历史版本一致、`WIXUI_INSTALLDIR='APPFOLDER'`、`APPFOLDER <- PreviousInstallFolder`（升级读回旧路径） |
| 安装器静态层 | `installer/verify_install_flow.py --static` | `[PASS]`：目录 Id 全大写 + `Secure` + `InstallPath` 写入/回读 + `MajorUpgrade` |

### 13.4 本版未覆盖

- **版本传播**：commit 与 tag `v0.1.7` 仅本地，未 `git push`、未建 GitHub Release（用户 2026-11-17 明确选择「只打包，先不推送」）。
- 安装器真机 7 步、内嵌播放真实窗口行为、外部站点改版风险 —— 同 12.4，仍需人工。

---

## 14. v0.1.8 打包门禁（0.1.7 之后 7 项反馈固化进安装包）

> 0.1.7 打包后，源码又积累了 7 个提交（继续观看接下一集 / 首屏主题 / 观影报告 / DB 隔离 / 剧集墙按钮 / 全盘快搜 / 蓝牙保障），但安装版用户拿不到。本版唯一目的是让安装包与源码一致。逐项细节见 `docs/FEATURES.md` §29–§35 与 `docs/IMPLEMENTATION_NOTES.md` §12–§17。

### 14.1 触发原因

用户 2026-11-18 明确要求「运行打包程序」，命中全量白名单第 ② 条（打 tag / 发版 / 打包），按发版清单升版本 → 补 CHANGELOG → 全量门禁 → 重出 ZIP + MSI → 本地 tag。

### 14.2 门禁结果（真实执行）

| 检查项 | 说明 |
|---|---|
| 全量用例 | `python run_tests.py` → **951 / 951 通过**，0 失败 / 0 错误 / 0 跳过，**116.68 秒**；快照写入 `D:\magicvideo\test_last.json` |
| Python 语法 | `compileall -q app tests *.py` 全通过 |
| JavaScript 语法 | `node --check web/app.js` 通过 |
| 版本一致性 | `tests/test_docs.py` 16 例通过：`__version__` / README / AGENTS / CHANGELOG / OBSIDIAN / `app.js?v=` 全部为 `0.1.8` |

### 14.3 产物验收（0.1.8，真实执行）

| 检查 | 方法 | 结果 |
|---|---|---|
| 构建链 | `.venv\Scripts\python.exe build_dist.py` | ZIP **27.1 MB**（28,464,500 字节）/ MSI **26.5 MB**（27,825,848 字节）；WiX `candle -dProductVersion=0.1.8` |
| exe 自检 | 构建脚本内 `Magic Video.exe --selftest` | `ok=True` / `version=0.1.8` / `frozen=True`；资源在 `_internal`、数据在临时目录 |
| ZIP 内容 | `zipfile` 逐项核对 | **492 条目**；`Magic Video/Magic Video.exe`、`portable.txt`、`_internal/web/index.html` / `app.js` 齐全；README / CHANGELOG 在包内 |
| 打包版前端 | 读 ZIP 内 `_internal/web/index.html` | 缓存串 `app.js?v=0.1.8` 存在 |
| 封面资产 | 读 ZIP 内 `_internal/assets/covers/` | 82 条（80 张封面 + `index.json` + `README.md`），与 0.1.7 一致 |
| MSI 属性 | `installer/inspect_msi.py dist/MagicVideo-0.1.8-win64.msi` | `ProductVersion='0.1.8'`、`ProductName='Magic Video'`、`UpgradeCode='{8F3A1C2E-...}'` 与历史版本一致、`WIXUI_INSTALLDIR='APPFOLDER'`、`APPFOLDER <- PreviousInstallFolder`（升级读回旧路径） |
| 安装器静态层 | `installer/verify_install_flow.py --static` | `[PASS]`：目录 Id 全大写 + `Secure` + `InstallPath` 写入/回读 + `MajorUpgrade` |

### 14.4 本版未覆盖

- **版本传播**：commit 与 tag `v0.1.8` 仅本地，未 `git push`、未建 GitHub Release（用户本轮只要求打包，未要求推送）。
- 安装器真机 7 步、内嵌播放真实窗口行为、蓝牙音箱真实切换、外部站点改版风险 —— 同 12.4，仍需人工。
