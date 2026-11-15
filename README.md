# Magic Video · 本地播放历史与断点续播工具

> 一个只跑在你自己电脑上的追番/看剧记录工具。**外观是独立软件窗口，数据全部留在本机。**
> 当前版本：**v0.1.3**（语义化版本，唯一真源为 `app/__init__.py` 的 `__version__`）

[![Python](https://img.shields.io/badge/Python-3.13-blue)]() [![Platform](https://img.shields.io/badge/Windows-10%2F11-blue)]() [![DB](https://img.shields.io/badge/SQLite-WAL-lightgrey)]()

---

## 1. 它解决什么问题

| 你的原始需求 | 实现状态 | 在哪里用 |
|---|---|---|
| 输入一份「看过的动漫列表」并记入数据库 | ✅ 已完成 | 「导入列表」页，支持粘贴文本 / CSV / TSV，即时去重入库 |
| 用 PotPlayer 看过的剧集都记进历史，可随时查看并继续观看 | ✅ 已完成 | 托盘守护进程 3 秒轮询，「继续观看」一键从断点启动 |
| 添加目录批量扫描已下载视频，外文名自动检索成中文 | ✅ 已完成 | 「扫描目录」页，导入/扫描时同步匹配中文名（**无定时任务**） |
| 输出方案、语言、Skill 清单并确认后开发 | ✅ 已交付 | 方案评审通过后完成 P0，详见 `docs/FEATURES.md` |
| 中文名补全不要每天定时，只在导入时自动完成 | ✅ 已按此重构 | v0.1.1 删除定时任务与待补全队列，改为动作内同步匹配 |
| 结合本地 Obsidian，自动记录开发流程与结果，方便后续优化补齐 | ✅ 已完成 | v0.1.3 同步 6 类笔记 + 按天日志到 vault 独立目录，保留手写批注 |

界面形态按你的选择定为 **C：原生窗口 + 网页界面**（pywebview + 系统自带 Edge WebView2），看起来是独立软件，无浏览器地址栏；后台记录按你的选择做成**托盘常驻 + 可选开机自启**。

---

## 2. 快速开始

### 安装（一次性）

```bat
cd /d "D:\project\magic video"
python -m pip install -r requirements.txt
```

依赖仅 6 个：`pywebview`、`pythonnet`、`pystray`、`Pillow`、`requests`、`psutil`。
本机已实测安装完成（Python 3.13 / Node 22 / SQLite 3.53 / Edge WebView2 157）。

### 启动

| 操作 | 效果 |
|---|---|
| 双击 `start.bat` | 打开主界面（8 个功能页） |
| 双击 `start_daemon.bat` | 启动托盘常驻记录（无控制台窗口） |
| 「设置」页打开开机自启 | 写 `HKCU\...\CurrentVersion\Run`，无需管理员权限 |

### 三步用起来

1. **导入列表** → 粘贴作品名，点「解析预览并匹配中文名」→ 中文名当场出现且可手改 → 点「确认导入」。
2. **添加视频目录** → 填入已下载视频所在文件夹 → 自动递归扫描，外文文件名当场转成中文名。
3. **继续观看** → 从列表点开任意一集，PotPlayer 以 `/seek=hh:mm:ss` 从上次位置启动；播放过程自动写入进度与会话。

> 即使不通过本工具、直接双击用 PotPlayer 打开视频，托盘守护也会记录（会用窗口标题建一条 `external` 占位记录）。
> 把该视频所在目录加进「扫描目录」后，续播即可正常使用。

---

## 3. 功能一览

| 页面 | 功能 |
|---|---|
| 总览 | 媒体/已看/会话统计、PotPlayer 实时进度条、继续观看置顶 |
| 继续观看 | 断点续播、从头播放、标记已看；下方「已看完」可标记未看 / 重置进度 |
| 历史记录 | 每次观看会话的起止时间、起止位置、最远进度 |
| 媒体库 | 按文件名 / 中文名 / 原名搜索，显示集数与观看百分比 |
| 看过的列表 | 导入结果，含中文名、匹配方式、季与集数范围 |
| 导入列表 | 解析预览、中文名即时匹配、中文名可直接编辑并永久记住 |
| 扫描目录 | 添加/停用/移除目录、立即扫描、词典状态、重新匹配 |
| 设置 | PotPlayer 路径、轮询间隔、已看完阈值、代理、数据源策略、开机自启 |

### 中文名匹配机制（重要）

**只在三个动作里同步执行，没有任何定时任务、后台回填队列或待办文件：**

```
导入列表预览 / 扫描目录完成 / 用户点「重新匹配」
        ↓
内置离线词典（98 部作品的真实 Bangumi 对照，随代码自带）
        ↓ 未命中
本地已学词典（用户手动改过的中文名，优先级最高）
        ↓ 未命中且为外文名
在线 Bangumi 搜索（可配代理，1.5s/3s 短超时，失败整批快速降级）
        ↓
结果当场写入 name_alias / subjects / watched_list / media_files 并立即显示
```

- 数据来源 `app/data/aliases_builtin.json`，随版本库提交，**完全离线也能命中** `Attack on Titan → 进击的巨人`、`Frieren → 葬送的芙莉莲` 等。
- 本机直连 `api.bgm.tv` 实测超时，因此离线词典是主力；在「设置」里填代理后在线源自动生效。
- 带季数的标题优先显示该季中文名（如「更衣人偶坠入爱河 第二季」）；词典未收录该季时只给主标题中文名并标记「主标题匹配」，**不编造季名**，由你在预览里改一次即可永久记住。
- 用户手工修正永远优先于内置词典和在线结果。

### 播放记录与断点续播

- `potplayer.py` 用 Win32 `WM_USER` 消息回读总时长 / 当前位置 / 播放状态（实测有效），无需改 PotPlayer 设置、无需插件。
- `watcher.py` 每 3 秒轮询，按窗口标题切换自动结算会话；进度 ≥90%（可配）自动标记「已看完」。
- 续播用官方 `/seek=hh:mm:ss /current /autoplay` 精确跳转。
- 纠错操作「标记已看 / 标记未看 / 重置进度」只改进度表，**绝不删除观看会话历史**。

---

## 4. 数据存储

单文件 SQLite（`data/library.db`，WAL 模式），8 张表：

| 表 | 用途 |
|---|---|
| `directories` | 已添加的扫描目录与上次扫描时间 |
| `media_files` | 扫描到的文件、文件名解析结果、中文名、季/集 |
| `subjects` | 作品级元数据（Bangumi ID、中文名、原名、季） |
| `watched_list` | 你导入的「看过的列表」 |
| `watch_progress` | 断点位置、百分比、完成标记、播放次数 |
| `watch_sessions` | 每次观看的起止时间与最远进度 |
| `name_alias` | 中英/中日对照词典（内置 + 用户修正） |
| `settings` | 播放器路径、轮询间隔、阈值、代理等 |

`data/` 已被 `.gitignore` 排除，**不会进版本库、不会上传**。旧版本数据库升级只做 `ALTER TABLE ADD COLUMN`，不重建表、不丢已有播放记录。

---

## 5. 命令行

```bat
python cli.py init                      :: 初始化数据库并显示词典统计
python cli.py import seen.txt           :: 导入清单（即时匹配中文名）
python cli.py scan                      :: 扫描全部已启用目录
python cli.py status                    :: 库统计 + 播放器实时状态 + 词典统计
python cli.py player                    :: 读取 PotPlayer 当前播放状态
python cli.py progress                  :: 列出所有断点进度
python cli.py mark 12 watched           :: 标记已看
python cli.py mark 12 unwatched         :: 标记未看（清续播点，保留历史）
python cli.py mark 12 reset             :: 重置进度
python cli.py match                     :: 重新匹配所有未识别标题
python cli.py dict                      :: 词典统计
python cli.py import-alias-file         :: 手动导入 data/alias_import.json
python cli.py autostart on|off          :: 开机自启开关
python cli.py daemon                    :: 前台启动托盘守护
python cli.py test                      :: 运行全部单元测试（结果写入 data/test_last.json）
python cli.py obsidian status           :: 查看 vault、笔记与最近测试状态
python cli.py obsidian sync             :: 同步流程与结果到 Obsidian
python cli.py obsidian path "D:\My Vault" :: 指定要写入的 vault
python cli.py obsidian open             :: 打开笔记目录
```

### Obsidian：自动记录流程与结果

v0.1.3 起，软件可以把**开发流程、自测结果、优化路线图、功能缺口**自动写进你本机 Obsidian 的一个独立文件夹
（默认 `Magic Video/`）：项目总览、版本记录、P0–P3 路线图、自测报告、缺口与决策、使用速查，外加按天追加的开发日志。

- **只读你已有的笔记、只写这个独立文件夹**；检测到多个 vault 时不会擅自猜，需要在「设置 → Obsidian 知识沉淀」里明确指定。
- 每篇笔记的「我的批注」区在同步时**原样保留**，可以直接在 Obsidian 里勾路线图、写想法、加标签。
- `run_tests.py` 会写 `data/test_last.json`，自测报告与结果时间线从中生成；开启「测试后自动同步」后 `cli.py test` 会自动留痕（默认关闭）。
- 笔记只有聚合统计与项目文档，**不含视频路径、文件名、观看明细**，也不依赖任何 Obsidian 插件或联网服务。

完整契约见 [`docs/OBSIDIAN.md`](docs/OBSIDIAN.md)；本机已接入 `C:\Users\Administrator\Documents\Obsidian Vault`。

---

## 6. 测试与稳定性

```bat
python run_tests.py
```

- **144 个单元测试**，覆盖数据层与迁移、文件名解析、清单导入、中文匹配、PotPlayer 桥接、守护会话、js_api、Obsidian 同步安全边界、端到端串联、文档版本一致性。
- 全部用例在**临时目录的独立 SQLite** 中运行，并屏蔽真实网络请求，**绝不读写你的真实播放记录**。
- 版本一致性由 `tests/test_docs.py` 守护：代码版本、README、AGENTS、CHANGELOG 任一漂移，测试立即失败。
- 详细自测结果见 `docs/TESTING.md`。

---

## 7. 版本管理与输出目录

| 输出类型 | 存放位置 | 是否入库 |
|---|---|---|
| 功能总览 / 使用说明 | `README.md` | ✅ |
| AI Agent 协作约定 | `AGENTS.md` | ✅ |
| 版本变更记录 | `CHANGELOG.md` | ✅ |
| 功能规格、优化建议、自测报告 | `docs/*.md` | ✅ |
| 应用代码 / 前端 / 单元测试 | `app/`、`web/`、`tests/`、`*.py` | ✅ |
| 离线词典（随程序自带） | `app/data/aliases_builtin.json` | ✅ |
| 运行数据库 / 封面 / 日志 / 测试结果快照 / 同步历史 | `data/` | ❌ 已忽略 |
| 项目工作日志与长期笔记 | `.workbuddy/memory/` | ❌ 已忽略 |
| Obsidian 同步笔记 | vault 内 `Magic Video/`（仓库之外） | ❌ 不入库、不受 git 管理 |

发版规则、提交规范与「每次交付必须同步更新哪些文件」写在 `AGENTS.md`；变更历史见 `CHANGELOG.md`。

### 版本输出一览

| 版本 | 主要输出 |
|---|---|
| v0.1.0 | 项目骨架、SQLite 8 表、原生窗口 8 页、PotPlayer 桥接、托盘守护、开机自启、列表导入与目录扫描 |
| v0.1.1 | 中文名改为**导入时同步匹配**，删除每日定时任务与待补全队列；新增匹配引擎 + 98 部离线词典；数据库平滑迁移 |
| v0.1.2 | 116 个单元测试与端到端自测、25 项缺陷修复、标记已看/未看/重置进度、CLI 纠错命令、完整文档与版本管理体系 |
| v0.1.3 | Obsidian 知识沉淀：6 类笔记 + 按天开发日志、测试结果自动留痕、路线图/缺口/ADR 联动、设置页与 CLI 同步入口、新增 26 个单测（合计 144） |

> 版本阶段：目前统一停留在 `0.1.x`，功能体系完善后再开 `0.2.0+`（2026-11-15 用户确认；原 0.2.0 / 0.3.0 / 0.4.0 已回溯重编号）。

---

## 8. 已知限制与后续建议

- 外部打开但尚未加入扫描目录的文件，只有窗口标题作为路径，需先「添加目录」才能续播。
- 在线刮削依赖网络可达性；不通时自动降级到离线词典，未命中的外文名可在导入预览里手动填一次。
- 进度表目前以文件路径为主键，移动硬盘换盘后需要重新扫描（重定位向导已列入优化建议）。
- 同类软件调研与 14 条分优先级优化建议见 **`docs/OPTIMIZATION.md`**。

---

## 9. 文档索引

| 文件 | 内容 |
|---|---|
| `README.md` | 本文件：功能总览、快速开始、版本与输出管理 |
| `AGENTS.md` | 给 AI Agent 的项目约定、不可动摇的约束、发版流程 |
| `CHANGELOG.md` | 语义化版本变更记录 |
| `docs/FEATURES.md` | 功能规格、验收标准、数据流、状态机、API 与表结构 |
| `docs/OPTIMIZATION.md` | 竞品调研、对比表、优化建议（P0–P3）、避坑清单 |
| `docs/TESTING.md` | 自测环境、命令、结果、单测覆盖矩阵 |
| `docs/OBSIDIAN.md` | Obsidian 集成契约、笔记结构、安全边界、同步入口与故障排查 |

> 隐私承诺：所有数据存于本机 `data/library.db`；除你主动配置的在线元数据检索外，程序不向任何服务上传文件名或观看记录。
