# 自测与单元测试报告 · v0.1.4

> 执行时间：2026-11-15；环境：Windows 10 专业版 / Python 3.13 / Node 22 / SQLite 3.53 / Edge WebView2 157。
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
| 单元测试 | **173 个用例全部通过**（0 失败 / 0 错误 / 0 跳过，耗时 25.44 秒） |
| Python 语法 | `compileall` 全通过 |
| JavaScript 语法 | `node --check web/app.js` 通过 |
| 原生窗口 | 8 个页面全部渲染成功，`js_api` 暴露 36 个方法；扫描结果 / 媒体库共用全量列表组件 |
| 打包产物 | 0.1.3：ZIP 21.8 MB / MSI 21.9 MB，`exe --selftest` 全通过；0.1.4 重新构建的产物验收见第 10 节 |
| CLI | `init / status / dict / progress / player / mark / test / obsidian` 全部可用 |
| 真实数据 | 旧播放记录、会话、断点进度、词典完整保留，未被本轮改动破坏 |
| Obsidian 真实 vault | 首次同步生成 6 篇笔记 + 当天日志；用户原有 2 篇笔记 md5 前后完全一致；二次同步批注保留、日志去重通过 |
| 远程推送 | 已完成：`master` + `v0.1.0`~`v0.1.4` 推送到私有仓库 `tiantangliwai/magic-video`（SSH 通道） |

---

## 2. 测试分层与覆盖矩阵

| 测试文件 | 用例数 | 覆盖内容 |
|---|---:|---|
| `tests/test_db.py` | 10 | 建表 8 张、WAL、设置读写与容错、旧库迁移不丢数据、外键级联、幂等初始化 |
| `tests/test_importer.py` | 19 | 行解析（季/集/范围/CSV/TSV/年份/注释）、文本去重、预览即时匹配、季名映射、提交去重、用户修正记忆 |
| `tests/test_scanner.py` | 17 | 字幕组命名、`SxxEyy`、中文话数、结尾集数、年份与标签清洗、忽略规则、增量扫描、目录停用、文件不可读容错 |
| `tests/test_metadata.py` | 20 | 归一化/季数解析、内置词典完整性、离线加载幂等、中英日命中、季名映射、用户修正优先级、`local_only` 状态、在线结果即时学习、写回行数、重匹配、词典统计、外部词典导入 |
| `tests/test_potplayer.py` | 15 | 时间格式化、窗口标题解析、文件反查、进度百分比与钳制、无窗口降级、非 Windows 安全导入、`/seek` 参数组装、缺文件报错 |
| `tests/test_watcher.py` | 10 | 首次轮询即写进度、进度推进、完成阈值、已看完不回退、切集结算、播放器关闭、外部文件登记、时长回填、回调异常隔离 |
| `tests/test_api.py` | 15 | 版本信息、统计、导入往返、设置白名单、代理重置、续播参数、已看完列表、标记已看/未看/重置进度、目录扫描桥接、守护探测 |
| `tests/test_smoke.py` | 2 | 端到端串联（导入 → 扫描 → 播放记录 → 续播 → 纠错 → 历史）、测试库隔离验证 |
| `tests/test_docs.py` | 11 | 版本语义化、交付文档齐备、版本一致、gitignore、依赖格式、前端、前端缓存串、无定时任务残留、Obsidian 接线与文档版本 |
| `tests/test_obsidian.py` | 26 | vault 探测与多选不猜、路径穿越防护、首次生成 6 篇笔记、同名用户笔记不覆盖、AUTO 区块外批注保留、frontmatter 用户字段保留、日志事件去重、历史快照去重、命名空间外零改动、路线图解析、结果渲染、状态查询、API 桥接与设置白名单 |
| `tests/test_packaging.py` | 17 | 四种路径布局、两种形态启动命令、开机自启、笔记降级、守护/窗口/自检分发 |
| `tests/test_media_browser.py` | 11 | 分页总数与旧上限、搜索字段、组合筛选、全量模式与排序、注入/越界安全、点击播放、前端契约 |
| **合计** | **173** | v0.1.4 相对 161 新增 12 项（11 项全量文件浏览 + 1 项前端缓存守护） |

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
