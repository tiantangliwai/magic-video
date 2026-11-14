# 自测与单元测试报告 · v0.3.0

> 执行时间：2026-11-15；环境：Windows 10 专业版 / Python 3.13 / Node 22 / SQLite 3.53 / Edge WebView2 157。
> 本报告只记录**实际跑过的命令与真实输出**，未实测的能力不会写成「通过」。

---

## 1. 结论

| 项目 | 结果 |
|---|---|
| 单元测试 | **116 个用例全部通过**（0 失败 / 0 错误 / 0 跳过） |
| Python 语法 | `compileall` 全通过 |
| JavaScript 语法 | `node --check web/app.js` 通过 |
| 原生窗口 | 8 个页面全部渲染成功，`js_api` 暴露 31 个方法 |
| CLI | `init / status / dict / progress / player / mark / test` 全部可用 |
| 真实数据 | 旧播放记录、会话、断点进度、词典完整保留，未被本轮改动破坏 |
| 远程推送 | 未执行（无 GitHub 凭证，等待用户授权） |

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
| `tests/test_docs.py` | 8 | 版本语义化、交付文档齐备、README/AGENTS/CHANGELOG 版本一致、gitignore 不误伤内置词典、依赖格式、前端、无定时任务残留 |
| **合计** | **116** | |

### 隔离机制

`tests/base.py::IsolatedDbTestCase` 在每个用例：

- 把 `config.DATA_DIR / DB_PATH / COVER_DIR / LOG_DIR / ALIAS_IMPORT_FILE` 指向临时目录；
- 重置 `db._INITIALIZED`、`metadata._BUILTIN_READY`、`_INDEX`、`_ONLINE_BLOCKED` 等进程内缓存；
- 用 `mock.patch` 屏蔽 `metadata.search_online`，**测试永不发真实网络请求**；
- 结束后恢复所有配置并清理临时目录。

因此测试**不会读写** `data/library.db`，也不会启动真实 PotPlayer。

---

## 3. TDD 过程与发现的缺陷

按「先写测试看红 → 修实现到绿」执行，共三轮：

| 轮次 | 结果 |
|---|---|
| 第 1 轮 | 116 用例，15 失败 + 10 错误（25 项红） |
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
version: 0.3.0
media:   1 条，status=external，title_cn=更衣人偶坠入爱河，match_by=native
sessions: 1 条    progress: 1 条（761929ms / 53.65% / completed=0）
watched: 0 条     aliases: 267 条
```

v0.1.0 的真实观看会话与断点进度在 v0.2.0 迁移和 v0.3.0 重构后**完整保留**。

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

## 6. 发版门禁（每次提交前）

- [ ] `compileall` 通过
- [ ] `node --check web/app.js` 通过
- [ ] `python run_tests.py` 116 个用例全绿
- [ ] 旧数据保留已确认
- [ ] README / AGENTS / CHANGELOG / docs/TESTING.md 版本一致（`tests/test_docs.py` 守护）
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
