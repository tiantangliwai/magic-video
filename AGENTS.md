# AGENTS.md · 给 AI Agent 的项目协作约定

> 本文件是 **AI Agent 在本仓库工作时的第一优先级约定**。与直觉冲突时，以本文件为准。
> 当前版本：**v0.1.3**（唯一真源 `app/__init__.py` → `__version__`，本文件与 README / CHANGELOG 必须同步）
> 适用环境：Windows 10/11、Python 3.13、项目根目录 `D:\project\magic video`

---

## 0. 一句话身份

Magic Video 是**纯本地**的播放历史与断点续播工具：pywebview 原生窗口（Edge WebView2）做界面，SQLite 存数据，`ctypes` 直读 PotPlayer 播放状态，托盘守护进程常驻记录。

不是网站、不是服务器、不需要账号、不上传观看数据。

---

## 1. 需求基线（用户明确确认过，不可擅自推翻）

| # | 约定 | 来源 |
|---|---|---|
| 1 | 输入一份「看过的动漫列表」→ 记入数据库 | 初始需求 1 |
| 2 | 用 PotPlayer 看过的剧集自动进历史，可随时查看并继续观看 | 初始需求 2 |
| 3 | 添加目录批量扫描本地视频；外文名自动检索成中文 | 初始需求 3 |
| 4 | 方案先行、经用户同意后才开发 | 初始需求 4 |
| 5 | **界面 = 原生独立窗口 + 网页界面**（C 方案），不要浏览器标签页、不要纯 PyQt | 用户选择 |
| 6 | **中文名只在导入时同步匹配完成，不要每日定时任务** | 用户明确要求（v0.1.1 已重构） |
| 7 | 后台记录 = 托盘常驻 + 可选开机自启 | 用户选择 |
| 8 | 优先离线可用；在线检索作为补充，失败必须快速降级 | 本机直连 Bangumi 超时 |
| 9 | 版本进度用 git 跟踪，交付文档与单元测试齐备 | 用户要求 |
| 10 | 结合本机 Obsidian 自动记录**流程与结果**，供后续优化和补齐功能；只写 vault 内独立命名空间，绝不覆盖用户已有笔记，保留手写批注；不做定时同步 | 用户要求（v0.1.3） |

**任何"要不要加个定时任务 / 要不要改成 Web 服务 / 要不要上传到云"的改动，都必须先问用户。**
**Obsidian 同步是纯本地文件写入，不是定时任务、不是云同步、不引入账号或插件。**

---

## 2. 架构约束

```
run.py ──> app/main.py ──> webview 窗口 ──> web/index.html + app.js
                              │ js_api 进程内桥接
                              v
                          app/api.py (Api)
                              │
   daemon.py ──> Watcher ──> app/db.py (SQLite WAL)  <── Api / cli.py
                   │                    ▲
                   v                    │
            app/potplayer.py     app/metadata.py / scanner.py / importer.py
            (WM_USER 消息)       app/data/aliases_builtin.json
```

- **单写者原则**：SQLite 用 WAL + `busy_timeout`；写操作走 `db.session()` 短事务，禁止长事务包裹网络请求。`init_db()` 必须保持进程内幂等。
- **双进程模型**：主窗口只读为主，托盘守护负责轮询写入；两者不共享内存状态，只通过数据库通信。
- **不修改用户文件**：扫描、解析、刮削一律只读视频文件，绝不重命名、移动、删除或写入同目录 `.nfo`。
- **数据本地优先**：`data/` 不入库；元数据结果落库，不写回视频目录。
- **前端无框架**：原生 HTML/CSS/JS，通过 `window.pywebview.api.*` 调后端；新增后端方法需同步加前端 `call(...)`。
- **可测试性**：模块导入不得因平台差异崩溃（`winreg`、`ctypes.windll` 已做安全降级）；纯逻辑与副作用分离，便于 mock。

---

## 3. 版本管理

### 3.1 规则

- 语义化版本 `MAJOR.MINOR.PATCH`；**唯一真源**是 `app/__init__.py` 的 `__version__`，禁止在其他代码里硬编码版本号（User-Agent 等一律引用该常量）。
- **版本阶段（用户 2026-11-15 确认）**：项目在完善前统一停留在 `0.1.0+`（0.1.x）阶段，**后续功能体系完善了再开 `0.2.0+`**；在此之前即使新增功能也只递增 PATCH。原 `0.2.0` / `0.3.0` / `0.4.0` 已回溯重编号为 `0.1.1` / `0.1.2` / `0.1.3`，git tag 同步重建，提交历史不变。
- `PATCH`：0.1.x 阶段的日常迭代（bug / 测试 / 文档 / 小功能补齐）。
- `MINOR`：**暂不启用**，待核心功能完善后整体跃迁到 `0.2.0`。
- `MAJOR`：数据库结构不兼容、需求基线变更、界面形态更换。
- git tag 格式 `vX.Y.Z`，打在对应的提交上。

### 3.2 每次发版必须同步的文件（缺一不可）

| 文件 | 要改什么 |
|---|---|
| `app/__init__.py` | 提升 `__version__` |
| `CHANGELOG.md` | 新增 `## [X.Y.Z] - YYYY-MM-DD` 段落，列 Added / Changed / Fixed / Tests / Docs |
| `README.md` | 顶部当前版本 + 「版本输出一览」新增一行 |
| `AGENTS.md` | 顶部当前版本声明 |
| `docs/TESTING.md` | 追加该版本的自测结论与测试数量 |
| `docs/OBSIDIAN.md` | Obsidian 契约有变化时同步更新；`docs/OPTIMIZATION.md` 是路线图笔记的单一真源 |
| git tag | `git tag vX.Y.Z` |

`tests/test_docs.py::VersionConsistencyTests` 会校验 README / AGENTS / CHANGELOG 是否都声明了当前版本，**任一漂移会导致测试失败**。

### 3.3 提交规范

```
<type>: <一句话中文简述>

- 变更点 1
- 变更点 2
```

`type` 取 `feat` / `fix` / `refactor` / `test` / `docs` / `chore`。一次提交一个逻辑单元；提交前必须跑 `python run_tests.py` 且全绿。提交身份当前为本地占位 `Magic Video <magic-video@local>`，拿到用户 GitHub 邮箱后再统一改写。

---

## 4. 输出管理（产物放哪、什么不能写）

| 产物类别 | 目标位置 | 命名/格式 | 入库 |
|---|---|---|---|
| 功能总览与使用说明 | `README.md` | 中文 Markdown | ✅ |
| Agent 协作约定 | `AGENTS.md` | 中文 Markdown | ✅ |
| 版本变更记录 | `CHANGELOG.md` | Keep a Changelog 风格 | ✅ |
| 功能规格 | `docs/FEATURES.md` | 需求 → 验收标准 → 数据流 | ✅ |
| 调研与优化建议 | `docs/OPTIMIZATION.md` | 对比表 + P0–P3 建议 + 来源 | ✅ |
| 自测与测试报告 | `docs/TESTING.md` | 命令 + 实测结果 + 覆盖矩阵 | ✅ |
| Obsidian 集成契约 | `docs/OBSIDIAN.md` | 笔记结构 + 安全边界 + 同步入口 | ✅ |
| 单元测试 | `tests/test_*.py` | `unittest`，继承 `tests.base.IsolatedDbTestCase` | ✅ |
| 测试入口 | `run_tests.py` | 零第三方依赖；写 `data/test_last.json` | ✅ |
| 内置离线词典 | `app/data/aliases_builtin.json` | `{"items":[{"cn","orig","aliases","bgm","season"}]}` | ✅ |
| 运行数据库 / 封面 / 日志 | `data/` | `library.db`（WAL） | ❌ `.gitignore: /data/` |
| 测试结果快照 / 同步历史 | `data/` | `test_last.json` / `obsidian_history.json` | ❌ |
| Obsidian 同步笔记 | vault 内 `Magic Video/` | `00 项目总览.md` 等 6 篇 + `06 开发日志/YYYY-MM-DD.md` | ❌ 仓库外 |
| 手动补充词典 | `data/alias_import.json` | 可选，用户自行放置 | ❌ |
| 项目工作日志 | `.workbuddy/memory/YYYY-MM-DD.md` | **追加式**，不覆写 | ❌ |
| 项目长期笔记 | `.workbuddy/memory/MEMORY.md` | 原地更新，≤3000 字 | ❌ |

**禁止**：在仓库根目录散落临时 `.txt` / `.json` 报告；把 `data/` 整目录加入版本库；把 `.gitignore` 写成裸 `data/`（会连带忽略 `app/data/aliases_builtin.json`）；删除 `.workbuddy/` 目录；在文档里编造未实测的数据或功能；
在 Obsidian 命名空间之外写任何文件；覆盖没有 `managed-by` 标记的用户笔记；把观看明细 / 视频路径写进笔记；为 Obsidian 引入定时任务或云同步。

---

## 5. 测试门禁

- 新增/修改任何 `app/` 逻辑 → 必须在 `tests/` 里补对应用例。
- 涉及数据库 → 继承 `IsolatedDbTestCase`；**严禁**在测试中连接真实 `data/library.db`。
- 涉及网络 → 基类的 `search_online` mock 已禁用真实请求；要测在线分支就本地 patch 假数据。
- 涉及 PotPlayer → mock `read_status` / `resolve_media` / `subprocess.Popen`，不真的启动播放器。
- 发版前：`python -m compileall -q app tests *.py` + `node --check web/app.js` + `python run_tests.py` 三项全过。
- 窗口冒烟：`webview.create_window(..., hidden=True)` + 线程内 `evaluate_js` 检查 8 页渲染，完成后 `destroy()`。

---

## 6. 标准改动流程

1. 读 `AGENTS.md` 与 `docs/FEATURES.md`，确认不违反第 1 节需求基线。
2. 先写/补测试（允许先红），再改实现，跑到全绿。
3. 同步更新受影响的文档（README / FEATURES / TESTING / CHANGELOG）。
4. 语法检查 + 完整测试 + 窗口冒烟。
5. 按 3.3 规范提交；若是发版，走 3.2 清单并打 tag。
6. 把有长期价值的结论追加到 `.workbuddy/memory/YYYY-MM-DD.md`。

---

## 7. 明确禁止

- ❌ 恢复「每日定时中文名补全」或任何待补全队列 / `pending_titles.json`。
- ❌ 把元数据写回视频目录、修改用户文件名。
- ❌ 引入账号体系、公网服务、数据库上云（除非用户明确要求）。
- ❌ 用文件路径之外的东西当主键却不迁移旧数据；删表重建导致历史丢失。
- ❌ 在文件不可达时自动删除 `media_files` / 进度 / 会话记录。
- ❌ 让在线刮削阻塞导入、扫描或播放记录；必须短超时 + 整批降级。
- ❌ 用一个长事务同时包住网络请求和数据库写。
- ❌ 提交 `data/*.db`、日志、临时测试产物。

---

## 8. 本机环境事实（实测，勿凭猜测）

| 项 | 值 |
|---|---|
| Python / Node / SQLite | 3.13 / 22 / 3.53（WAL 可用） |
| PotPlayer | `D:\tool\DAUM\PotPlayer\PotPlayerMini64.exe`，窗口类 `PotPlayer64` |
| 进度回读 | `WM_USER(0x0400)` + `0x5002` 总时长 / `0x5004` 当前位置 / `0x5006` 状态（实测成功） |
| 续播参数 | `/seek=hh:mm:ss` `/current` `/autoplay`（官方 `CmdLine64.txt`） |
| WebView2 | Edge WebView2 157 已安装，pywebview `gui="edgechromium"` |
| 网络 | 本机直连 `api.bgm.tv` / TMDB / Jikan **超时**；GitHub / pypi 可达 |
| git | 本地仓库已初始化，无 gh CLI、无 GitHub 凭证；远程仓库待用户授权 |
| Obsidian | `%APPDATA%\obsidian\obsidian.json` 注册 2 个 vault；主库 `C:\Users\Administrator\Documents\Obsidian Vault`，另一个是装修项目专用库（不写） |
| 同步产物 | `data/test_last.json`（测试结果）、`data/obsidian_history.json`（结果时间线）；均在 `.gitignore` 内 |

---

## 9. 常见任务速查

| 想做什么 | 从哪下手 |
|---|---|
| 加一个界面按钮 | `web/app.js` 的 `data-action` + `handleAction`，后端在 `app/api.py` 加同名方法 |
| 改文件名解析规则 | `app/scanner.py::parse_filename` + `tests/test_scanner.py::FilenameTests` |
| 扩充中文词典 | `app/data/aliases_builtin.json`（真实条目，禁止编造）+ `tests/test_metadata.py` |
| 改观看完成判定 | `app/watcher.py::_tick` + `tests/test_watcher.py` |
| 加数据库字段 | `app/db.py::SCHEMA` + `_migrate()` 补列，并写迁移不丢数据的用例 |
| 加 CLI 命令 | `cli.py` 的 `choices` 与分支 + README 第 5 节 |
| 改 Obsidian 笔记内容 | `app/obsidian.py` 的 `render_*` + `tests/test_obsidian.py`；改路线图请改 `docs/OPTIMIZATION.md` |
| 发新版 | 走第 3.2 节清单，跑 `tests/test_docs.py` 验证一致性 |
