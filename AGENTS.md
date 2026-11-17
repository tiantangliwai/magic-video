# AGENTS.md · 给 AI Agent 的项目协作约定

> 本文件是 **AI Agent 在本仓库工作时的第一优先级约定**。与直觉冲突时，以本文件为准。
> 当前版本：**v0.1.7**（唯一真源 `app/__init__.py` → `__version__`，本文件与 README / CHANGELOG 必须同步）
> 适用环境：Windows 10/11、Python 3.13、项目根目录 `D:\project\magic video`

> 🚨 **测试铁律（用户 2026-11-15 二次纠正「怎么又开始全量跑单元测试了」，违反即算做错）**
> **日常迭代只跑增量单元测试 `python run_changed_tests.py`，禁止跑全量 `python run_tests.py`。**
> 全量 **仅** 在下列三种情形允许（封闭白名单，不得自行扩充）：
> ① 用户**本轮**明确要求推送 / `git push` / 推到远端；
> ② 用户**本轮**明确要求打 tag / 发版 / 打包 / 出安装包；
> ③ 本轮改动了 `tests/base.py` 或数据库 schema（增量工具会自动要求 `--all`）。
> **以下理由一律不成立，禁止拿来当加跑全量的借口**：「这次改动面大 / 文件多 / 新增模块多」、「想更稳妥、顺便兜底一下」、「收尾时再验一遍」、「确认总用例数没变 / 刷一下 `test_last.json`」、「增量跑完了顺手也跑个全量」。
> **增量全绿 = 本次门禁通过**，随后直接同步受影响文档、记日志、本地提交；**不得在增量全绿之后再追加一次全量**。
> 违规实例（不得重演）：曾以「本批改动面大」为由，在增量 **422/422** 全绿后额外跑全量 **609/609**。当时用户未要求推送、未要求发版、未改基类/schema，属无授权加跑。
> 详见 3.0.1；提交前门禁见 3.3，禁止项见 §7。

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
| 11 | 扫描出的文件必须**完整可见**（不得截断）、可搜索、可在软件里点击直接打开播放器 | 用户要求（v0.1.4） |
| 12 | 「看过的列表」要能**改动名称并快速搜索**：一次载入全量、本地即时过滤（作品名/中文名/导入原文/备注），行内改名并把用户手改的中文名记住 | 用户要求（0.1.4 日常迭代） |
| 13 | 媒体库不能只平铺全部视频：要能按**第一季/第二季/第三季、一部番十几集**做树状展示；原平铺列表仍需保留可切换 | 用户要求（0.1.4 日常迭代） |
| 14 | **单实例**：任务栏重复点击只能有一个窗口；启动要快，不能刷 WebView2 COM 报错 | 用户要求（0.1.4 日常迭代） |
| 15 | 剧集展示参考 `seeduck.cc` / `www.agedm.io`，**要有对应动漫的展示图**；后台起一次性任务抓取，不是定时任务 | 用户要求（0.1.4 日常迭代） |
| 16 | 集成 `D:\tool\Everything` 做快速检索；工具缺失时必须自动降级，功能不能消失 | 用户要求（0.1.4 日常迭代） |
| 17 | PotPlayer **就在当前软件内播放**；嵌入失败必须自动降级为原外部播放 | 用户要求（0.1.4 日常迭代） |
| 18 | 展示风格先做**黑 / 白两套**即可；设置要持久化 | 用户要求（0.1.4 日常迭代） |
| 19 | 一键 `./start` 直接连**真实数据库**；要能明确告知 `.db` 位置与 C 盘工作数据 | 用户要求（0.1.4 日常迭代） |
| 20 | 用户给的真实「看过的列表」（含 `S1/S2`、`（完）`、剧场版、番号等噪声）必须能正确解析入库 | 用户要求（0.1.4 日常迭代） |
| 21 | **后台记录默认自动启动**，不要再在左下角/总览让用户点「启动」，但可以在设置里关闭；左下角只留状态指示 | 用户明确要求（0.1.4 日常迭代） |
| 22 | 界面与数据体验 11 项：默认数据目录迁到 `D:\magicvideo`、窗口标题改软件名、媒体库树状改「剧集展示」、双击输入框即改并失焦自动保存、看过的列表脏数据清洗 + 从播放记录同步、列表撑满与表头固定、计数移到卡片右上角、顶栏主题即时切换、封面进度可见、切回窗口即时刷新、关闭播放器断点兜底 | 用户明确要求（0.1.4 日常迭代） |
| 23 | **全盘快搜（Everything）要单独占一个左侧导航名称**，不再作为「剧集墙」页里的附属卡片 | 用户明确要求（0.1.4 日常迭代） |
| 24 | **历史记录支持人工删除；整部剧看完后支持人工删除本地文件；但观看记录绝不自动删除**。本地文件只能移入回收站，数据库零 `DELETE`，进度/会话/看过的列表全部保留 | 用户明确要求（0.1.4 日常迭代） |
| 25 | 增加「追剧列表」导入，分已下载 / 未下载两栏；顺序默认按写入顺序但支持后续调整；未下载 ↔ 已下载可互转；扫描目录中已下载的剧集要能匹配并跳到媒体库搜索或固定展示 | 用户明确要求（0.1.4 日常迭代） |
| 26 | 剧集墙**不要「播放下一集」按钮**；「查看分集」跳转必须带搜索词，否则跳过去会忘记在看什么 | 用户明确要求（0.1.4 日常迭代） |
| 27 | 新增「后台进程管理」独立标签，用来启停 / 查看后台进程和剧集图片缓存进度；后台抓取封面整合进去，**不再在剧集墙展示** | 用户明确要求（0.1.4 日常迭代） |
| 28 | 封面抓取失败率高：不要用全名，改用文件名中的关键字多试几次，从 AGE动漫 / SeedHub 两个网址获取对应背景图 | 用户明确要求（0.1.4 日常迭代） |
| 29 | 播放器使用优化：播放/停止键居中、上一集/下一集按钮、倍速下拉 1.25/1.5/2、全屏按钮移到右下角。**PotPlayer 闭源、GitHub 无官方源码，不下载不集成第三方同名仓库**；用软件内控制层实现（用户确认：保留 PotPlayer 加控制层，同时调研开源内核） | 用户明确要求（0.1.4 日常迭代） |
| 30 | 「继续观看」记录支持删除，用软删除；历史记录里同一作品只显示最新一条，旧观看会话原样保留、绝不自动删 | 用户明确要求（0.1.4 日常迭代） |
| 31 | 看过的列表 / 媒体库都做**卡片墙 + 表格可切换**：每部作品一张封面卡片、只显示一个作品名、不补中文名列；看过的列表默认平铺 | 用户明确要求（0.1.4 日常迭代，选择「两种都做可切换」） |
| 32 | 扫描结果按**磁盘目录结构**展示；设置页「保存设置」等按钮固化在内容顶部、上下滚动时常驻 | 用户明确要求（0.1.4 日常迭代） |
| 33 | 分组修正：`Anna Directors Cut E01–E08` 归成一部剧、`1.36G` 显示为「爱的躯壳」、尽量按目录统一归并、**去掉「未标注季」这一层**，并提供用户可编辑作品名的能力 | 用户明确要求（0.1.4 日常迭代） |
| 34 | 看过的列表中能匹配到**扫描目录对应目录**的作品，支持用文件资源管理器跳转到该文件夹；匹配不到就不显示入口 | 用户明确要求（0.1.4 日常迭代） |
| 35 | 用用户给的两个搜索 URL 拿到**正确的**封面（很多图是错的）；若实在拿不到，提供一处让用户**人工粘贴背景图**的入口 | 用户明确要求（0.1.4 日常迭代） |
| 36 | 看过的列表「第几季」也像改名一样**双击即可输入**（失焦/回车保存） | 用户明确要求（0.1.4 日常迭代） |
| 37 | 全盘快搜降级提示：评估能否彻底集成 / GitHub 有无源码 / 插件化 / 至少加快捷方式 | 用户明确要求（0.1.4 日常迭代，选 A+B） |
| 38 | **日常迭代只跑增量单元测试**（`run_changed_tests.py`），**不要全量跑**；全量 `run_tests.py` 仅在「用户明确要求推送 / 打 tag 发版 / 改了 `tests/base.py` 或 schema」三种情形下允许，禁止以「改动面大、兜底、收尾确认」为由加跑 | 用户明确要求（0.1.4 日常迭代，2026-11-15 二次纠正「怎么又开始全量跑单元测试了」） |
| 39 | 「从播放记录同步」**不允许再出现大量重复行**：同一作品同季必须合并成一条区间行（`episode_from`~`episode_to`），幂等且能清理旧的每集一条记录 | 用户明确要求（0.1.4 日常迭代） |
| 40 | 看过的列表切到卡片墙后**必须能下拉滚动**；媒体库切换**不能明显卡顿** | 用户明确要求（0.1.4 日常迭代） |
| 41 | 倍速与上一集 / 下一集按钮**必须能看见**（不能只藏在内嵌播放舞台里） | 用户明确要求（0.1.4 日常迭代） |
| 42 | 追剧列表的上移 / 下移 / 移回未下载等按钮**要对齐**，并新增**一键置顶** | 用户明确要求（0.1.4 日常迭代） |
| 43 | 新增**视频黑名单**设置（开关默认开启）：名称包含任一关键词（不区分大小写）的视频在**所有位置**默认不展示；关闭后正常展示全部 | 用户明确要求（0.1.4 日常迭代） |
| 44 | 快速放映厅的倍速 / 上一集 / 下一集 / 播放暂停**点下去必须真的生效**，不能只是"看起来点了" | 用户明确要求（0.1.4 日常迭代，2026-11-15「快进展厅倍速之类的按钮都没有用」） |
| 45 | 媒体库与剧集墙打开**要快**：不能因为分组、封面、黑名单判定反复重建索引而卡住 | 用户明确要求（0.1.4 日常迭代） |
| 46 | 看过的列表卡片墙**不能挤成一堆、不能丢标题**：外层普通块 + 内层 `poster-grid`，禁止整块铺满时把卡片压扁 | 用户明确要求（0.1.4 日常迭代） |
| 47 | 黑名单必须**彻底生效**：`FC2-PPV-1288` 要被 `FC2PPV` 挡住（两侧去连接符归一化再比），目录名派生出的大卡片标题也要过滤 | 用户明确要求（0.1.4 日常迭代） |
| 48 | 新增**网页搜索**视图：集成 `agedm.io` 与 `seeduck.cc` 的搜索，把两站结果渲染成本地卡片并**标明来源**；不做内嵌浏览器，点开走系统默认浏览器（`open_url` 只放行 `http/https`） | 用户明确要求（0.1.4 日常迭代） |
| 49 | 从剧集墙（以及追剧 / 看过的列表）跳进媒体库后，媒体库顶部要有**返回入口**，点了回到来源页，不用重新跳一遍；直接点左侧导航进媒体库**不显示**返回 | 用户明确要求（0.1.4 日常迭代，2026-11-15「从剧情集跳转到媒体库里后需要一个返回按钮」） |
| 50 | 剧集墙每张卡片要有**是否看过**的标记：本地全部集看完 = `已看完`、看过一部分 = `已看 3/12`、看了一半 = `在看`、只在「看过的列表」里 = `看过`、都没有 = `未看`。两路数据合并判定（`watch_progress` 精确到集 + `watched_list` 作品级），状态由后端算好后端字符串下发，前端只渲染 | 用户明确要求（0.1.4 日常迭代，2026-11-15「剧集墙中增加是否已经看过的标记」） |
| 51 | 安装包必须让**选定的安装路径真的生效**，并且**升级时自动读回上次的路径**：向导里选 `D:\...` 就装到那里；已经装过的，下次升级默认装回同一位置，不用每次重选 | 用户明确要求（2026-11-15「已经安装要进行版本更新时需要自动读到历史安装路径进行相关更新，而且我重新选择了路径为什么还是会安装到了c盘」） |
| 52 | v0.1.6 需求清单（P0 / P1 / P2 全部条目）一次性做完，外加补强方案 C-1（外部解析收敛到唯一适配层）/ C-2（缓存键 mtime 守护）与 OPTIMIZATION #5 的 CSV/NFO 导出、#6 的集数低置信度标记 | 用户明确要求（2026-11-16「开始开发这些任务」「继续做 p1 的所有需求」「都完成」） |
| 53 | 上述改动完成后**按完整发版清单出安装包**：升版本号、补 CHANGELOG、重出 ZIP + MSI、打 tag 并同步 GitHub Release | 用户明确要求（2026-11-16「我要生产具体的安装包…更新成新的版本提交，并更新对应 release」） |
| 54 | **追剧列表里词序不同的同一译名必须能关联到本地文件**：清单写「会长大人是女仆」、磁盘目录写「会长是女仆大人」，要显示「本地 N 个文件」并能直接点播；但**不能因此误配无关作品**（宁漏不错，短英文词与两字中文一律不做词序容错） | 用户反馈（2026-11-16「没有关联对应的磁盘中的链接，我也没办法真的开始看这个视频」） |
| 55 | **已下载的剧集墙封面要提交进仓库并随包分发**：`assets/covers/`（图片 + `index.json`）受版本控制，打包带进安装包，新装离线即有封面；本机缓存仍优先、资产只做单向补齐；后续**别人可以提 PR 换某部剧的封面**，本地也可随时换成新图（沿用已有的「设为封面 / 强制重抓」）；手工封面不进公共资产 | 用户明确要求（2026-11-16「我下载的剧集墙的封面对应的图片也提交到github上，后续也支持其他用户更新或者本地更新成新的图像」） |
| 56 | **在看过的列表里改名后，历史记录与剧集墙要跟着变**：同一季的媒体文件标题同步更新；只改同一季（第 1 季 / 第 2 季常共用同一中文名）、无季标记按第 1 季对齐；写回标记 `match_by='user'` 防被重刮覆盖；发布组 / 压制参数串（`KKTV AAC2 0 -BlackTV`、`1080p HEVC`、`[组名]`）不得当中文名写库或进词典 | 用户反馈（2026-11-16「已经重命名为更衣人偶坠入爱河第二季了，但是在历史记录中还是展示的原名称」） |
| 57 | **看完一集后继续观看要能直接点下一集**：已完成但仍存在下一集的作品，在首页「继续观看」、导航「继续观看」页、剧集墙顶部继续观看行三处都显示下一集卡片，点击从头播；全剧看完才消失。只在取数侧补集，**绝不改 watcher 完成判定 / `completed` / `position_ms` / 历史记录** | 用户反馈（2026-11-17「历史记录的最新一条是我看一个动漫第一集看完了，但是继续观看列表里就没信息了，我需要…点击后看第二集」） |
| 58 | **启动时首屏就是持久化的主题，不能先白后黑**：主题存 SQLite、private_mode 下 localStorage 不持久；由 `app/main.py::startup_url()` 把 theme 注入 URL 参数，`index.html` head 里的同步脚本在 CSS 加载前设 `data-theme`；JS 侧 `currentTheme` 初值跟随 DOM，禁止硬编码 light 后等异步 IPC 再切 | 用户反馈（2026-11-17「重新打开 magic video 时表头还是白色的，需要切换一下之后才会变成黑色的」） |
| 59 | 「看片统计」改名**「观影报告」**；`watch_sessions.watched_ms` 是 v0.1.6 才加的列、迁移只 `ADD COLUMN DEFAULT 0` 不回填，**不能把有真实播放区间的旧会话统计成 0**：读取时用区间保守估算并标「估」，不写库、不冒充精确口径；升级后必须重启守护，否则新会话仍不写该字段 | 用户反馈（2026-11-17「11-15 我看了一天的动漫统计的结果还是 0，另外看片统计这个名字有点不好」） |
| 60 | **任何单元测试不得改动真实数据库** `D:\magicvideo\library.db`：碰库的用例必须继承 `IsolatedDbTestCase`（临时库）；`tests/db_guard.py` 全局 patch `sqlite3.connect` 强拦真实库连接，`tests/__init__.py` 与两个运行器入口都装守卫；`tests/test_db_isolation.py` 静态扫描确保无漏网。禁止绕过守卫直连真库验证 | 用户要求（2026-11-17「所有的单元测试不能改动真实数据库中的内容」） |
| 61 | **剧集墙「按季」按钮保留**：点它没反应是 `handleAction` 把形参 `btn` 误写成未声明的 `el`（严格模式抛错被吞），共 6 处同类笔误（save-episode/export-csv/stats-range/calendar-shift/poster-mode/rate-work），一律从 `btn.dataset` 取值，禁止用 `el`；剧集墙移除「刷新」（后端按数据指纹+20s TTL 自动缓存，20s 内无效且不刷封面）与「去后台进程管理封面」（纯跳左侧已有页）两个按钮，通用 `goto` case 保留 | 用户反馈（2026-11-17「按季按钮没反应…去掉；去后台进程管理封面也没意义去掉；刷新按钮有什么意义」），用户拍板修按季、删另两个 |

**任何"要不要加个定时任务 / 要不要改成 Web 服务 / 要不要上传到云"的改动，都必须先问用户。**
**封面抓取与内嵌播放同样不是定时任务**：只在用户点按钮 / 点播放时执行一次。
**Obsidian 同步是纯本地文件写入，不是定时任务、不是云同步、不引入账号或插件。**
**Hy3 限时免费（用户 2026-11-15）**：后续任务优先把可并行的只读诊断 / 机械核对 / 文档一致性检查派给 Hy3 子代理，把免费额度用掉；主模型只做汇总复核与**串行落地改代码**（共用 `app.js` / `api.py`，单测与文档统一收口）。不必再排查模型清单、额度通道或重启 opencode。

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
                 │
                 └── app/embedded_player.py (SetParent 内嵌；HWND 写 settings 供守护跨进程回查)
   app/covers.py (一次性后台封面任务) / app/everything.py (es.exe → HTTP → 媒体库降级)
```

- **单写者原则**：SQLite 用 WAL + `busy_timeout`；写操作走 `db.session()` 短事务，禁止长事务包裹网络请求。`init_db()` 必须保持进程内幂等。
- **双进程模型**：主窗口只读为主，托盘守护负责轮询写入；两者不共享内存状态，只通过数据库通信。
- **不修改用户文件**：扫描、解析、刮削一律只读视频文件，绝不重命名、移动、删除或写入同目录 `.nfo`。
- **数据本地优先**：`data/` 不入库；元数据结果落库，不写回视频目录。
- **资源/数据分离**：源码版与打包版数据目录**统一默认 `D:\magicvideo`**（用户指定，`config.DEFAULT_DATA_DIR`）；打包版资源取 `sys._MEIPASS`（只读），绿色版由 exe 同级 `portable.txt` 改为同级 `data/`，`MAGIC_VIDEO_DATA_DIR` 优先级最高。守护/开机自启不继承环境变量，所以必须改默认值而非只靠 `start` 脚本 export。**禁止把数据库写进临时解压目录**。`config.resolve_layout()` 是路径唯一真源，`app/runtime.py` 负责两种形态的启动命令。
- **前端无框架**：原生 HTML/CSS/JS，通过 `window.pywebview.api.*` 调后端；新增后端方法需同步加前端 `call(...)`。
- **pywebview Window 必须私有**：`Api._window` 下划线开头，绝不改成公开属性，也不要在非 UI 线程访问 `window.native`（会遍历 .NET 控件树并抛 `E_NOINTERFACE`，同时拖慢启动）。要拿主窗口 HWND 用 `app/embedded_player.py::find_main_window()` 的 PID + 标题枚举。
- **内嵌播放的跨进程契约**：PotPlayer 变成子窗口后 `FindWindowW` 找不到它，必须把 HWND 写进 `settings.embed_hwnd`，`potplayer.find_window()` 从那里回查；主窗口关闭前必须 `detach()` 还原，否则会留下孤儿子窗口。
- **降级优先于报错**：封面、Everything、在线刮削、内嵌播放任何一步失败都要继续可用，不能让按钮点了没反应。
- **可测试性**：模块导入不得因平台差异崩溃（`winreg`、`ctypes.windll` 已做安全降级）；纯逻辑与副作用分离，便于 mock。

---

## 3. 版本管理

### 3.0 发版节奏（用户 2026-11-15 明确要求，优先级最高）

> **不要每来一个需求就打包、打 tag。** 用户原话：「不要我每一个需求都打包，打tag」。

**日常需求 = 一次轻量迭代（默认路径）**：

1. 改代码 + 补对应单测，**只跑增量 / 受影响用例**：`python run_changed_tests.py`（按 git 改动自动映射），或 `python -m unittest tests.test_xxx`；改了 Python / JS 再补 `compileall` / `node --check`。
2. 只更新**本次真正受影响**的功能/使用说明；版本号、CHANGELOG 留给发版时统一处理。
3. 按 3.3 规范做**本地提交**（`git commit` 即可，**不主动 `git push`**，见 3.0.2）。
4. 追加当天工作日志。
5. 门禁就是第 1 步的增量结果，**到此为止，不额外跑全量**；仅当命中 3.0.1 白名单 ①②③ 时才跑 `python run_tests.py`。

**默认禁止（除非用户明确要求）**：

- ❌ 升 `__version__`、改 `web/index.html` 的 `app.js?v=` 缓存串、逐个同步各文档版本号。
- ❌ 跑 `build_dist.py` 重出 ZIP / MSI、做 `exe --selftest` 与产物验收。
- ❌ `git tag` / 推 tag / 为每个需求写一段 CHANGELOG。
- ❌ 自动同步 Obsidian 笔记（默认 `obsidian_auto_sync=0`，需要时手动同步一次即可）。

### 3.0.1 测试节奏（用户 2026-11-15 追加并二次纠正，优先级最高）

> 用户原话①：「调整后续的开发逻辑只跑增量和影响到逻辑的单元测试，不要全量跑，仅在推送和打tag时全量跑单元测试」。
> 用户原话②：「怎么又开始全量跑单元测试了，把跑增量单元测试写到agent.md中」。

**默认动作（每次日常迭代都走这一条）**：`python run_changed_tests.py`。
它按 git 变更自动挑出受影响模块；精确控制用 `--module tests.test_x` / `--only` / `--list`，已提交想回看用 `--base HEAD~1`。改了 Python / JS 再补 `compileall` / `node --check`。只改前端语法 / 纯文案时，跑 `node --check` 即可，连 Python 增量都不必全跑。

**数据库隔离铁律（用户 2026-11-17 要求）**：单元测试**绝不能改动真实数据库** `D:\magicvideo\library.db`。所有用例默认应继承 `tests/base.py::IsolatedDbTestCase`（临时库）。`tests/db_guard.py` 在运行期全局 patch `sqlite3.connect`，任何指向真实库的连接立即抛错；守卫在 `tests/__init__.py` 及两个运行器入口安装。`tests/test_db_isolation.py` 静态扫描全部 `test_*.py`，凡碰 `db.*` / `Api()` / `Tray()` / `sqlite3.connect` 的类必须（传递地）继承隔离基类。新增测试类若需写库，**必须继承 `IsolatedDbTestCase`**，不要用裸 `unittest.TestCase` 去 `Api()`；取证真实库只能用 `mode=ro` 只读连接且写在测试脚本之外，不得进单测。

**允许跑全量 `python run_tests.py` 的封闭白名单（仅此三条，不得扩充）**：

| # | 触发条件 | 说明 |
|---|---|---|
| ① | 用户**本轮**明确要求 `git push` / 推到远端（见 3.0.2） | 全量必须全绿才推 |
| ② | 用户**本轮**明确要求打 tag / 发版 / 打包 | `compileall` + `node --check` + `run_tests.py` 全量 + 打包链验收 |
| ③ | 本轮改了 `tests/base.py` 或数据库 schema | 基类影响全部用例，增量工具会自动要求 `--all` |

**除 ①②③ 之外，任何情况都不得跑全量。** 特别点名以下常见借口，**全部无效**：
- ❌「这次改动面大 / 动的文件多 / 新增了好几个测试模块，跑个全量兜底」
- ❌「增量已经全绿了，但再跑一遍全量更稳妥」「收尾时顺手验一下」
- ❌「想确认总用例数 / 想刷新 `D:\magicvideo\test_last.json` 快照」
- ❌「提交前再完整确认一次」（提交前只要求增量门禁，见 3.3）
- ❌ 把「推送前要跑全量」提前到日常提交里预跑

**门禁判定**：增量全绿 + 语法检查通过 = **本次改动验收通过**，随后直接同步受影响文档、追加日志、本地提交。全量结果只允许出现在真正推送 / 发版的那一轮回复里；日常回复里不要报全量数字。

- `run_changed_tests.py` 的 `IMPACT` / `FRONTEND_MODULES` 是「改动 → 受影响用例」的单一真源，新增模块顺手补一行；共享文件只映射到最小契约集，跨模块影响用 `--module` 追加。
- 增量运行**不写** `data/test_last.json`（那是全量门禁快照，供 Obsidian 自测报告读取）。日常迭代不刷新该快照是**预期行为**，不是补跑全量的理由。
- 白名单 ①② 触发时全量仍是最终兜底（增量全绿不能替代推送前全量）；③ 是工具自动判定，不算「额外加跑」。**这三种之外的全量一律视为违规。**

**触发完整发版的唯一条件**：用户明确说「发版 / 打包 / 出安装包 / 打个 tag / 发布一个版本 / 升版本」等。此时才一次性走 3.2 清单：升版本 → 补齐累积的 CHANGELOG → 重出 ZIP + MSI → 验收产物 → 打 tag 并推送。

### 3.0.2 推送节奏（用户 2026-11-15 追加，优先级最高）

> 用户原话：「后续不要主动推送」。

- **默认只做本地 `git commit`，绝不主动 `git push` / 推分支 / 推 tag。** 本地工作区保持干净即可，远端是否更新由用户决定。
- 唯一例外：用户在本轮明确说出「推送 / push / 推到远端 / 同步到 GitHub / 发版并推送」等指令，才执行 `git push origin master`；推之前按 3.0.1 跑全量门禁。
- 提交信息与回复里要如实区分「本地已提交」和「已推送到远端」，不能把未推送说成已推送。
- 有未推送的本地提交时，只在回复里列出 commit 号提醒用户，**不要擅自代推**。

**版本号只在发版时变**：两次发版之间的多个需求共用同一个 `__version__`（当前 `0.1.7`），`tests/test_docs.py` 的版本一致性守护不会因日常提交而失败。不要为「记录进度」提前改版本或提前打 tag；已有 `v0.1.0`~`v0.1.7` 保持不动，不回溯、不删除。

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
| `web/index.html` | 把 `app.js?v=X.Y.Z` 同步到新版本，避免升级后 WebView2 仍加载缓存里的旧前端（由 `tests/test_docs.py` 守护） |
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

`type` 取 `feat` / `fix` / `refactor` / `test` / `docs` / `chore`。一次提交一个逻辑单元；**日常本地提交前只跑增量门禁**（`run_changed_tests.py`），增量全绿即可提交，**禁止以「改动面大 / 兜底 / 收尾确认」为由加跑 `python run_tests.py` 全量**；全量仅在 3.0.1 白名单 ①②③ 下允许。提交身份当前为本地占位 `Magic Video <magic-video@local>`，拿到用户 GitHub 邮箱后再统一改写。**提交不等于推送**，见 3.0.2。

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
| 测试入口 | `run_tests.py` | 零第三方依赖；写 `<默认数据目录>/test_last.json`（现为 `D:\magicvideo\test_last.json`） | ✅ |
| 内置离线词典 | `app/data/aliases_builtin.json` | `{"items":[{"cn","orig","aliases","bgm","season"}]}` | ✅ |
| 构建脚本 | `build_dist.py` | 一键生成 PyInstaller onedir + 绿色 ZIP + WiX MSI | ✅ |
| MSI 安装包定义 | `installer/product.wxs` | WiX v3，版本由 candle `-dProductVersion` 注入 | ✅ |
| 打包版路径与启动适配 | `app/config.py::resolve_layout` / `app/runtime.py` | 资源/数据分离、两种形态启动命令 | ✅ |
| 可执行产物 | `dist/` | `MagicVideo-<版本>-win64-portable.zip` / `.msi` | ❌ `.gitignore: dist/` |
| 构建中间物与工具 | `build/` | PyInstaller 输出、WiX 3.14 工具、wixobj、自检解包 | ❌ `.gitignore: build/` |
| 运行数据库 / 封面 / 日志 | `data/` | `library.db`（WAL） | ❌ `.gitignore: /data/` |
| 测试结果快照 / 同步历史 | `data/` | `test_last.json` / `obsidian_history.json` | ❌ |
| Obsidian 同步笔记 | vault 内 `Magic Video/` | `00 项目总览.md` 等 6 篇 + `06 开发日志/YYYY-MM-DD.md` | ❌ 仓库外 |
| 手动补充词典 | `data/alias_import.json` | 可选，用户自行放置 | ❌ |
| 项目工作日志（L2） | `.workbuddy/memory/YYYY-MM-DD.md` | **追加式**，不覆写；单日 <10KB | ❌ |
| 项目长期笔记（L1） | `.workbuddy/memory/MEMORY.md` | 原地更新，**≤3000 字**（固定注入，超了持续吃积分） | ❌ |
| 实现细节冷存储（L0） | `docs/IMPLEMENTATION_NOTES.md` | 按需检索，不注入；实现细节的唯一去处 | ✅ |

### 4.1 Agent 记忆分层（2026-11-16，解决反复触顶与整理耗时）

文件名大小写以本表为准：`AGENTS.md`、`MEMORY.md`、`IMPLEMENTATION_NOTES.md` 均大写扩展名，路径唯一，禁止另建同义文件（如 `agents.md`、`memory.md`）造成口径分裂。

| 层 | 文件 | 放什么 | 怎么读 | 字数 |
|---|---|---|---|---|
| L0 冷存储 | `docs/IMPLEMENTATION_NOTES.md` | 命令码、性能数字、函数名、踩坑细节、提交历史 | **不注入**，需要改某块代码时按关键词搜索 | 不限 |
| L1 长期笔记 | `.workbuddy/memory/MEMORY.md` | 铁律 / 不变量 / 指针（指向 L0 与本文档） | 每次会话全量注入 | **≤3000 字** |
| L2 工作日志 | `.workbuddy/memory/YYYY-MM-DD.md` | 当天做了什么：结论 + 待办，每条 1–3 行 | 只在回看历史时按需读 | 单日 <10KB |

**归类规则（写之前先判层）**：一句话能说清的「约束 / 铁律 / 不变量 / 去哪查」→ L1；具体函数名、参数、命令码、实测数字、历史流水 → L0；当天发生了什么 → L2。**禁止把实现细节写进 L1**（这是反复触顶的根因），也**禁止把 L2 日志全文搬进 L1**（一个月后必然再次爆炸）。

**维护节奏**：L1 用「替换式更新」——新约定进来先删同类旧条目，长度守恒，不是无限追加；超过 3000 字当场蒸馏，把细节移到 L0，不要等触顶再大整理。字数上限由 `tests/test_docs.py::test_project_memory_stays_under_budget` 守护。L2 日志满 30 天后蒸馏出仍有效的不变量并入 L1，随后删除旧日志。

**与 Obsidian 笔记的区别（不要混淆）**：`docs/OBSIDIAN.md` 契约下的 vault 笔记是**交付给用户的产品文档**（带 `managed-by` 标记、只写 `Magic Video/` 命名空间）；本节的 `.workbuddy/memory/` 是 **Agent 自己的工作记忆**，仓库外、不入库。两者互不覆盖、互不同步、职责不重叠。

**禁止**：在仓库根目录散落临时 `.txt` / `.json` 报告；把 `data/` 整目录加入版本库；把 `.gitignore` 写成裸 `data/`（会连带忽略 `app/data/aliases_builtin.json`）；删除 `.workbuddy/` 目录；在文档里编造未实测的数据或功能；
在 Obsidian 命名空间之外写任何文件；覆盖没有 `managed-by` 标记的用户笔记；把观看明细 / 视频路径写进笔记；为 Obsidian 引入定时任务或云同步；把 `dist/` / `build/` / `.venv/` 提交进版本库。

---

## 5. 测试门禁

- 新增/修改任何 `app/` 逻辑 → 必须在 `tests/` 里补对应用例；**日常只跑受影响模块**（`python run_changed_tests.py`），增量全绿即验收通过。**禁止在日常迭代里跑 `python run_tests.py` 全量**，全量只在 3.0.1 白名单 ①②③ 下允许（推送 / 发版 / 改 `tests/base.py` 或 schema），见 3.0.1 / 3.0.2 与文件顶部测试铁律。
- 涉及数据库 → 继承 `IsolatedDbTestCase`；**严禁**在测试中连接真实 `data/library.db`。
- 涉及网络 → 基类的 `search_online` mock 已禁用真实请求；要测在线分支就本地 patch 假数据。
- 涉及 PotPlayer → mock `read_status` / `resolve_media` / `subprocess.Popen`，不真的启动播放器。
- **推送 / 打 tag 前**：`python -m compileall -q app tests *.py` + `node --check web/app.js` + `python run_tests.py` 三项全过；日常改动只跑增量（见 3.0.1）。
- 文件列表 / 搜索相关改动：`tests/test_media_browser.py` 必须覆盖分页总数、搜索字段、组合筛选、排序白名单、注入/越界安全与点击播放链路；列表不得有硬条数上限。
- 树状视图改动：`tests/test_media_tree.py` 必须覆盖作品/季/集分组、脏文件名归并、目录树、筛选不截断、已看/在看计数与续播集、叶子点击播放链路；`get_media_tree` 与平铺列表共用 `_media_filters()`，不得另写一套搜索 SQL。**无季标记的文件直接挂作品组，禁止再生成「未标注季」节点**；分组改名只写 `settings.work_labels_json` 展示映射，绝不 `UPDATE media_files`，树 / 剧集墙 / 整剧树三处必须同源。
- 展示视图改动：`tests/test_watched_cleanup.py` 必须覆盖卡片墙 / 表格切换、看过的列表默认平铺与单作品名、四种文件视图（cards/series/folder/flat）与扫描页默认 folder、设置页 sticky 工具条、作品改名两端接线；看过的列表取封面只能调 `cover_data_uris` 读缓存，**不得触发后台抓取任务**。
  - **卡片墙结构固定为「外层普通块 + 内层 `.poster-grid`」**：看过的列表卡片模式**禁止再挂 `content-fill`**，也禁止给 `.watched-cards` 加 `flex: 1` 之类的铺满规则——那会把卡片压扁成一堆、标题被挤没。CSS 断言要盯住 `.watched-cards { display: block` 与 `.watched-cards .poster-grid`。
  - **跨视图跳转必须留返回入口**：带 `keyword` 跳进媒体库时记 `viewReturn = {view, label}`（来源视图取跳转前的 `currentView` 的 `VIEW_META` 名，**不要写死「剧集墙」**），媒体库顶部由 `backRow()` 渲染「← 返回X」；`case 'goto-back'` 要**先清 `viewReturn` 再 `switchView`**，否则会被当成一次新跳转重新记录。直接点导航进媒体库或无关键词跳转一律不显示返回。
  - **剧集墙「是否看过」标记**：`api._watch_state()` 是唯一判定源（`done` > `partial` > `watching` > `listed` > `none`），前端 `posterStateTag()` 只按 `card.watch_state` 渲染文案与配色，**禁止在 JS 里用 `watched_count` 重算状态**（两处判断迟早不一致）。「看过的列表」这一路必须**同时**满足「列表条目命中该卡片的文件」+「标题校验通过」（`_title_contains()`：归一化后完全相等，或一方包含另一方且**短侧 ≥ 4 字符**）；**禁止直接拿 `watchlist.match_files()` 的结果当结论**——它是给追剧列表用的宽松匹配，会把《斯巴达克斯》标成《斯巴达300勇士》、《内情 The inside story》标成《After Story》、`real真实` 标到一部英文片上。`watched_list` 的行数与标题长度必须进 `_data_fingerprint()`（否则导入 / 同步后标记停在旧缓存），而「看过的列表」匹配索引的缓存键**不得含 `db.write_count()`**（守护每 3 秒写播放进度，带上等于每 3 秒重算 200ms）。测试见 `tests/test_poster_watched.py`。
- 本地目录跳转改动：`tests/test_watched_folders.py` 必须覆盖目录白名单（绝对路径 / 真实存在 / 非系统目录 / 在扫描根内）、匹配与未匹配分支、文件直接在扫描根内、失效发布目录向上回退 2 层（但**不退到扫描根本身 / 不跨出扫描根**）、只传 `id` 不传路径、`os.startfile` 全程 mock 且失败不抛异常、前端两种视图共用按钮与接口失败降级。**前端绝不把 path 传给后端**；打开动作只允许 `os.startfile`，禁止 `explorer` 命令拼接；`get_watched_list` 返回结构不得为此改动；目录匹配必须复用 `watchlist.media_index()` + `match_files()`，不得另写一套搜索 SQL。
- 「从播放记录同步」联动：`sync_watched_from_progress()` **按 `(作品名, 季)` 聚合**，同一作品同季只允许一行（区间为 `episode_from`~`episode_to`），并顺带合并历史遗留的 `source='playback'` 每集一条记录（`match_by='user'` 行优先保留）。用例必须覆盖「12 集 → 1 行」「第二次同步 `added=0`」「既有区间被扩展而非新增」「整部行（NULL 区间）覆盖一切」「不同季各自一行」「手动行不动」「同步 → `watched_folders()` 每行都有按钮 → `open_watched_folder(id)` 能打开」整条链路、`title == title_cn` 只匹配一次、不同作品不串目录。**禁止退回「每集调一次 `commit()`」的写法**（判重键含集号，必然刷重复）。`watched_folders()` 的标题缓存**只允许本次调用内有效**，禁止跨调用进程级缓存（改名 / 删除 / 重扫后必须自然重算）。
- 历史去重改动：`tests/test_continue_dismiss.py` 必须覆盖「同一作品不同集只留最新一条会话、不同作品各留一条、无标题会话按 `file_id` 独立保留、旧会话零删除、人工删最新后露出次新」。`get_history()` 分组键必须是作品标题（中文名 > 推断名），**不得退回按 `file_id` 分组**（那样一部剧每集一条会刷屏）；只收拢展示，不新增 `DELETE FROM watch_sessions`（全项目仍只有 1 处）。
- 启动 / 单实例改动：`tests/test_startup.py` 必须覆盖互斥锁、已有窗口置前、js_api 反射安全与 `./start` 脚本；主题改动必须同时跑 `tests/test_theme.py` 与 `tests/test_theme_native.py`（DWM 原生标题栏、降级路径、CSS `color-scheme` 契约）。
- 默认数据目录改动：`tests/test_packaging.py` 的布局断言与 `tests/test_startup.py` 的 `start` 脚本契约必须同步；**改真实库前先用 `sqlite3.backup()` 做一致性快照、旧目录保留不删除**。
- 看过的列表解析 / 清洗 / 同步改动：`tests/test_watched_cleanup.py` 必须覆盖解析防再犯、清洗幂等与 `match_by='user'` 保护、区间覆盖判重、守护断点兜底与前端静态契约；清洗函数**只改 `title/title_cn/match_by`**，保留 `raw_text`、季、集、备注。
- 守护自动启动改动：`tests/test_daemon_autostart.py` 必须覆盖默认值、自动拉起 / 已运行不重复 / 关闭不启动、`stop_daemon()`、设置保存的即时生效、源码版与打包版进程识别、前端不再有 `start-daemon` 按钮；**禁止在 `Api.__init__` 拉起进程**，所有 `subprocess.Popen` / `psutil.process_iter` / `proc.terminate()` 一律 mock，绝不真的启停本机托盘守护。
  - **血泪坑**：`Api.save_settings({"daemon_auto_start": 0})` 会立刻调 `_apply_daemon_auto_start()` → `stop_daemon()` 真杀本机守护。曾有一个 `save_settings` 用例漏 mock，导致每跑一次 `run_tests.py` 用户的托盘守护就被杀掉、左下角误显示「后台记录未启动」。因此 `DaemonAutoStartApiTests.setUp` 统一把 `app.api.psutil.process_iter` 钉为空列表、`app.api.subprocess.Popen` 钉为抛 `AssertionError`；新增用例若需真实枚举，必须在自身作用域内层 patch 临时覆盖。任何新增的 `save_settings` 用例都要显式 mock `start_daemon` / `stop_daemon`。
- 播放控制改动：`tests/test_player_controls.py` 必须覆盖按键消息码（WM_KEYDOWN/WM_KEYUP 与 VK）、无窗口 / 非 Windows / PostMessage 失败降级、键位白名单与非法值回退、倍速步进的精确与不精确分支、上下相邻集排序与边界、设置页保存白名单、前端三文件静态契约。**所有 Win32 调用一律 mock，绝不启动真实播放器**；控制条必须放在 `#playerHost` 之外的 HTML 层，否则会被原生子窗口盖住。
  - **播放类动作必须走 PotPlayer 官方指令通道**：`potplayer.control()` 优先 `WM_COMMAND`（`play_pause=10014 / play=20001 / pause=20000 / stop=20002 / prev=10123 / next=10124`），再用 `POT_SET_PLAY_STATUS(0x5007)` 兜底，最后才投按键；三条通道**必须按需逐条发送并复查 `POT_GET_PLAY_STATUS(0x5006)`**。**禁止同时发**（一次点击会被连切三次，画面看着毫无变化）。用例必须覆盖「指令生效即返回」「状态没变继续换下一条」「三条全失败要报错」。
  - 倍速 / 全屏这类没有菜单命令号的动作走 `POT_SEND_VIRTUAL_KEY(0x5010)`（wParam=命令码、lParam=VK，不需要窗口焦点），被拒再投 `WM_KEYDOWN/WM_KEYUP`；返回里必须带 `via` 说明走了哪条通道。
- 安装包定义改动（`installer/product.wxs` / `build_dist.py`）：`tests/test_packaging.py::InstallerDefinitionTests` 必须覆盖以下五条，任何一条不满足都算缺陷：
  - **安装目录的 Directory Id 必须全大写**（`APPFOLDER`）。MSI 规定只有全大写属性才是 public property，值才能从 UI 序列传到执行序列；写成 `AppInstallFolder` 这类含小写的 Id，向导里选的路径会被静默丢弃、仍旧装到默认目录（0.1.4 用户原话「重新选择了路径为什么还是会安装到了c盘」就是这个）。
  - 目录属性要标 `Secure="yes"`（本机安装实测提权运行 `Privileged=1`，只有 `SecureCustomProperties` 里的属性会跨序列传递；**不能手写 `SecureCustomProperties` 属性**，WiX 会报 CNDL0070）。
  - `WIXUI_INSTALLDIR`、`heat -dr`、快捷方式 `Target`/`WorkingDirectory` 三处必须指向同一个 Id。
  - 升级记忆：安装时写 `HKCU\Software\Magic Video\InstallPath`，升级时用 `RegistrySearch` 读回；`ARPINSTALLLOCATION` 用 Type 51（`After="CostFinalize"`）赋值，**属性表不做格式化，直接写 `[APPFOLDER]` 会存成字面量**。
  - 端到端验证用 `installer/verify_install_flow.py`：它构建独立 `UpgradeCode`/组件 GUID 的测试包，真装真卸（指定路径 → 记住路径 → 不传路径升级仍在原位置 → 卸载无残留）。**禁止拿真实 MSI 反复静默装卸**，那会把用户实际安装升级/删除。

- 封面改动：`tests/test_covers.py` 必须覆盖两个源的解析、缓存命中不联网、脏标题过滤、中文名优先排序、一次性非定时任务；改 `app/textmatch.py` 或 `app/covers.py` 还要连带跑 `tests/test_watchlist.py`（关键词变体、两源共享索引、代理断连重试、非 200 换源）与 `tests/test_manual_cover.py`。站点搜索**不能只试完整长标题**。
  - **三条硬约束**：① AGE 候选**只取 `data-original`**（可从百度代理 `src=` 解出原始 CDN），`src` 是所有结果共用的占位图，绝不能进候选；② `_blocks()` 必须以字符串末尾兜底，不能丢最后一块（精确匹配常是最后一块）；③ 匹配按 `exact_pick()` 精确/去季精确 → `strict_pick()`（0.90 + 差值只能是季/续作后缀），**禁止退回 0.62 模糊凑数**，宁可返回未找到也不拿别的作品的图。Seeduck 优先用 `/s/<kw>/` 搜索页 `<h2>` 短标题，不用固定首页池凑数。
  - 人工封面：`source='manual'` 优先级最高，`is_manual()` 按**缓存文件名**（哈希）判定（标题含空格/大小写时 `lower(title_cn)` 会对不上）；`fetch_one()` 连 `force=True` 也不能覆盖，`cover_titles()` 要排除人工作品；`set_cover()` 三种来源都必须校验魔数与大小。
- Everything 改动：`tests/test_everything.py` + `tests/test_everything_open.py` 必须覆盖空串回退默认路径、Error 8 / `-svc` 会话隔离的可操作提示、HTTP Basic Auth、`open_app()` 只用后端固定路径且 `os.startfile` 全 mock。**绝不修改用户 `Everything.ini` 或重启其服务进程**；核心引擎闭源，禁止尝试「下载源码编译集成」。
- 看过的列表季数编辑：`tests/test_watched_season.py` 必须覆盖 `set_watched_season()` 只改 `season_no`、三种输入格式、留空清空、负数/超范围拒绝、前端 `watchedEditField` 字段分支与失焦保存；**不得复用 `update_watched` 改季数**，改名链路的隐藏中文名域契约必须保留。
- 视频黑名单改动：`tests/test_blacklist.py` 必须覆盖默认开启、分隔符解析与去重、不区分大小写的包含匹配、`%`/`_` 转义、SQL 片段只含 `filename`/`title_guess`/`title_cn`（**不含 `path`**）、媒体库四种视图 / 剧集墙 / 看过的列表 / 历史 / 继续观看 / 统计 / 追剧本地匹配 / 快搜结果全部被过滤、**只隐藏不删行**、关闭开关后全部恢复、`save_settings` 白名单。新增任何「返回视频名称列表」的 API 都必须接入 `blacklist`，否则视为缺陷。
  - **两段式匹配**：SQL 只用 `LIKE` 粗筛（便宜），Python 侧两侧去掉 `- _ . · 空格（含全角）` 归一化后再 `in` 精筛——这样 `FC2PPV` 才能挡住 `FC2-PPV-1288`、`MIDA` 挡住 `MIDA_077`。**禁止把多层 `replace()` 塞进 `WHERE`**：38 词 × 3 字段 OR 展开会让 SQLite 逐词重算 replace 链（实测 2100 行多 110 ms）。
  - **字形必须归一化**（用户 2026-11-15 追问「黑名单里有三上悠亚，为什么剧集墙里还是有」）：日语资源写的是新字体 `三上悠亜`（U+4E9C）、繁体 `三上悠亞`（U+4E9E）、简体 `三上悠亚`（U+4E9A），**三个码位互不相等**，纯 `in` 判断永远不命中。`normalize_name()` 先 NFKC（全角字母数字折半角）再查 `_VARIANTS` 异体表归到规范简体；**只改比较、不改展示**。异体表只收字形一一对应的（日文新字体 / 繁体 / 旧字形），**不做多义合并**（乾/干、著/着、藉/借一律不进），否则会把无关作品一起误伤。
  - **短英文词必须带词边界**：去连接符会把两个单词粘成一个（`BDrip X264` → `bdripx264` 里出现 `ipx`），`future` / `return` / `tsurezure` 天然含 `ure`，这些正常番会被误隐藏。3~4 个字母的纯 ASCII 词要求命中位置左右**原始字符都不是英文字母**（数字与符号放行，否则 `PRED733` 这种「词+数字」粘连的番号挡不住）。**这条必须同时下推到 SQL**：SQL 一旦把行删掉，Python 侧的复核就没机会把行放回来——短词改用 `lower(x) GLOB '*ipx*' AND NOT GLOB '*[a-z]ipx*' AND NOT GLOB '*ipx[a-z]*'`，GLOB 要求原样连续匹配，空格 / 连字符天然是边界，正好符合番号写法（`SSIS-001` / `MIDA_077` / `@PRED733`）。
  - **目录名派生的分组标题**（剧集墙卡片标题、媒体库树分组名）文件名里可能根本没有关键词，必须再由 `_prune_blacklisted_groups()` 做一次 Python 侧判断，否则「剧集墙还能看到黑名单剧集」必然复发。**季子节点标题也要看**：作品目录叫「合集」、季目录才带词时，只看 label 会漏。
  - 关键词与开关读取必须有进程内快照（键 = 库路径 + `db.write_count()`），**禁止每次 `hit()` 都 `db.get_setting()`**：每次 `get_conn()` 都会新开连接，190 个分组能耗掉 2 秒。

- 网页搜索改动：`tests/test_websearch.py` 必须覆盖 AGE / Seeduck 两站真实页面片段的解析（`data-original` 原始 CDN、`src` 占位图必须被丢掉、`<h2>` 短标题）、相对链接补全、`http://` 升级为 `https://`、两站并行 + 单站失败降级、缓存命中不联网、黑名单过滤、`open_url` 白名单（只放行 `http/https`，`file:` / `javascript:` / `ms-*` / 相对路径全拒）、前端 nav / `VIEW_META` / `RENDERERS` 三者同步。
  - **不做内嵌浏览器**：pywebview + WebView2 加载第三方站点要处理登录 / 广告 / 脚本冲突，代价远大于收益。只抓搜索结果这一层渲染成本地卡片，点开走系统默认浏览器（`webbrowser.open`，**禁止拼 shell 命令**）。
  - 解析器与封面抓取共用同一套硬约束（AGE 只认 `data-original`、Seeduck 用 `<h2>`），改任何一边都要连带跑 `tests/test_covers.py`。
- 追剧置顶 / 按钮对齐：`tests/test_watchlist_top.py` 必须覆盖同栏置顶、已置顶不移动、跨栏隔离、连续置顶、未知 id、`downloaded` 不变，以及 `.wl-ops .btn-icon` 等宽 + 整组右对齐 + 窄窗口换行的 CSS 契约。
- 播放条常驻 / 媒体库性能：控制条必须同时存在于顶栏（`#playerBar`，独立窗口也能用）与内嵌舞台；`get_player_status().file_id` 要能回填 `currentPlaying`；封面 data URI 必须走「后端 mtime 缓存 + 前端 `coverUriCache`」两层，**禁止在每个视图里各写一次 `call('cover_data_uris')`**。
  - **结果缓存必须按「数据指纹」失效，禁止用文件 mtime**：守护进程每 3 秒写播放进度，`library.db` / `-wal` 的 mtime 一直在变，用 mtime 会永远命中不了（表现为「加了缓存还是慢」）。指纹 = `(库路径, db.write_count(), 行数, MAX(id), SUM(LENGTH(filename)), SUM(LENGTH(title_cn)), 软删行数)`，播放进度故意不纳入。
  - 三处固定热点不许复发：`library_tree._is_tech_token` **不得每次 `re.escape()` 现拼正则**（会冲掉 re 的 512 条缓存，2020 文件 × 40 词 ≈ 8 万次编译）；批量取封面**必须走 `covers.resolve_cover_many()`**（禁止逐张 `resolve_cover` 重建归一化索引 + 逐个 `stat()`）；`db.get_conn()` 的 `PRAGMA journal_mode=WAL` **按库路径只设一次**。
  - 进程缓存键**必须带库路径**：测试反复切临时库，只靠写计数会把上一个库的数据带过来；`tests/base.py::reset_module_state()` 要清 `api._MEDIA_CACHE` / `websearch.clear_cache()` / `blacklist.invalidate()`。

- 追剧列表 / 后台进程改动：`tests/test_watchlist.py` 必须覆盖解析与编号边界、顺序/状态调整幂等、短英文泛词不误命中、进程状态只读、**新增左侧导航必须同步 `VIEW_META` / `RENDERERS` / nav 三者键集合**；`process_status()` 用例必须显式 mock `daemon_running` 与 `subprocess.Popen`，绝不启停本机守护。
- 剧集墙改动：「查看分集」必须带 `search_keyword`；脏分组名（如 `1.36G`）要退回组内 `title_cn` 或文件名括号里的中文片名；卡片不得再出现「播放下一集」或封面抓取控件。
- 内嵌播放改动：`tests/test_embedded_player.py` 必须覆盖坐标 DPR 换算、`SetParent`/`SetWindowPos` 失败、找不到主窗口 / 播放器窗口、降级为外部播放、`detach` 还原；`tests/test_potplayer.py` 必须覆盖从 `embed_hwnd` 回查子窗口 HWND 与陈旧句柄拒绝。所有 Win32 调用一律 mock，绝不启动真实播放器。
- 人工删除改动：`tests/test_manual_cleanup.py` 必须覆盖回收站路径安全、零 `DELETE FROM media_files`、历史单条删除、整剧看完判定、清单预览与伪造 id 拒绝、失败不落标记、扫描恢复标记、前端静态契约；所有 `SHFileOperationW` 调用一律 mock（`file_ops._shell_trash` 在 `setUp` 全局钉死），**绝不真删文件**；本地文件只走回收站 + `UPDATE deleted_at`，观看记录只能由用户逐条人工删除。
- **仅用户要求发版或改打包链时**：`python build_dist.py` 跑通，并确认 `exe --selftest` 全通过、ZIP 与 MSI 关键文件齐全（验收清单见 `docs/TESTING.md` 第 9 节）。
- 窗口冒烟：`webview.create_window(..., hidden=True)` + 线程内 `evaluate_js` 检查 8 页渲染，完成后 `destroy()`。

---

## 6. 标准改动流程

1. 读 `AGENTS.md` 与 `docs/FEATURES.md`，确认不违反第 1 节需求基线。
2. 先写/补测试（允许先红），再改实现，跑到全绿。
3. 只同步**本次受影响**的功能/使用说明；版本号、CHANGELOG、TESTING 报告留到发版时统一处理（见 3.0）。
4. 语法检查 + **增量测试**（`python run_changed_tests.py`，见 3.0.1）；仅当界面有可见改动才做窗口冒烟。**增量全绿就提交，不要再加跑全量**；只有命中 3.0.1 白名单 ①②③ 时才跑全量。
5. 按 3.3 规范做**本地提交**（不主动推送，见 3.0.2）；**仅用户明确要求发版**时才走 3.2 清单、构建产物并打 tag。
6. 按 4.1 分层写记忆：当天结论/待办以 1–3 行追加到 `.workbuddy/memory/YYYY-MM-DD.md`（L2，单日 <10KB）；有长期价值的**铁律/不变量**进 `MEMORY.md`（L1，先删同类旧条目、≤3000 字）；实现细节进 `docs/IMPLEMENTATION_NOTES.md`（L0）。禁止往 L1 堆细节。

---

### 6.1 本地调试入口（不发版，随时看改动）

- **日常直接 `./start`（Git Bash）**：源码启动 + 真实数据库，启动脚本会打印数据目录与 `.db` 路径；`./start --local` 用调试库，`./start --debug` 开 DevTools。
- 双击 `dev.bat`（等价于 `.venv\Scripts\python.exe run.py --debug`）即用**源码**启动窗口，改完代码马上能看，**不需要打包、不需要 tag、不需要重装 MSI**。

| 改了什么 | 怎么看 |
|---|---|
| `web/app.js` / `style.css` / `index.html` | 窗口内按 `Ctrl+R` 刷新（debug 模式已开启浏览器快捷键）；没生效就关窗重跑 |
| `app/*.py` / `run.py` / SQL | 关掉窗口，重跑 `dev.bat`（Python 不热重载） |
| 看报错、调样式 | `--debug` 默认打开 DevTools（`F12` / 右键检查） |

- `dev.bat` 默认调 `dev.py` 把 `D:\magicvideo\library.db` 复制成 `data/dev/library.db` **安全副本**：能看到全部 2105 条扫描结果，调试期间的播放/导入/扫描不会写进真实记录。
- 参数：`dev.bat --real` 直接连真实库（调试写入会进真实历史）；`dev.bat --nodevtools` 不弹 DevTools；两者可组合。
- **必须用 `.venv`**：系统 Python 缺 `psutil` / `pywebview` 等依赖。
- **别用已安装的 exe 验证源码改动**：exe 内的前端在 `_internal`，与源码无关，只有显式发版重新构建才会更新。
- 源码版现在也认 `MAGIC_VIDEO_DATA_DIR`（`app/config.py::resolve_layout` 2026-11-15 修复），由 `tests/test_packaging.py::test_source_layout_honors_data_override` 守护。

## 7. 明确禁止

- ❌ 恢复「每日定时中文名补全」或任何待补全队列 / `pending_titles.json`。
- ❌ 把元数据写回视频目录、修改用户文件名。
- ❌ 引入账号体系、公网服务、数据库上云（除非用户明确要求）。
- ❌ 用文件路径之外的东西当主键却不迁移旧数据；删表重建导致历史丢失。
- ❌ 在文件不可达时自动删除 `media_files` / 进度 / 会话记录。
- ❌ 自动删除观看记录（守护、扫描、导入、会话关闭都不清理；只有「历史记录」页逐条人工删除）。
- ❌ 永久删除本地文件（`os.remove` / `unlink` / `rmtree` 一律禁止，只能 `FOF_ALLOWUNDO` 进回收站，且必须用户二次确认）。
- ❌ 让在线刮削阻塞导入、扫描或播放记录；必须短超时 + 整批降级。
- ❌ 把封面抓取做成定时任务 / 开机自跑；入口只在「后台进程」页，进度轮询只在用户停留该页时存在，切走即停。
- ❌ 把 AGE 的共用占位 `src` 当封面候选；把 0.62 模糊相似度结果当命中；用固定首页池给未命中的作品凑图。
- ❌ 让自动抓取（含 `force=True`）覆盖用户人工设定的封面；`clear_cover()` 是唯一撤销入口。
- ❌ 修改用户 `Everything.ini`、重启其 `-svc` 服务进程，或尝试把闭源 Everything 引擎「编译进本软件」。
- ❌ 用一个长事务同时包住网络请求和数据库写。
- ❌ 让视频黑名单删除 / 修改任何数据：它只能是展示层过滤，不得 `DELETE FROM watched_list` / `media_files`，不得把 `path` 纳入判定（目录名会连带隐藏整棵子树），不得在开关关闭后仍过滤。
- ❌ 把播放控制条只放在内嵌舞台里（独立窗口播放时用户完全看不到倍速和上下集）；顶栏必须有常驻控制条。
- ❌ 在每个视图各写一次 `call('cover_data_uris')` 绕过缓存；封面 data URI 必须走统一 helper。
- ❌ 提交 `data/*.db`、日志、临时测试产物。
- ❌ 未经用户明确要求推送 / 打 tag / 发版，或没有改 `tests/base.py` / schema，就跑 `python run_tests.py` 全量测试；**尤其禁止**在增量全绿后以「改动面大 / 兜底 / 收尾确认」为借口补一次全量（见 3.0.1 封闭白名单与文件顶部测试铁律）。
- ❌ 未经用户明确要求就升版本 / 重出 ZIP、MSI / 打 tag / 自动同步 Obsidian（见 3.0）。

---

## 8. 本机环境事实（实测，勿凭猜测）

| 项 | 值 |
|---|---|
| Python / Node / SQLite | 3.13 / 22 / 3.53（WAL 可用） |
| PotPlayer | `D:\tool\DAUM\PotPlayer\PotPlayerMini64.exe`，窗口类 `PotPlayer64` |
| 进度回读 | `WM_USER(0x0400)` + `0x5002` 总时长 / `0x5004` 当前位置 / `0x5006` 状态（实测成功） |
| 续播参数 | `/seek=hh:mm:ss` `/current` `/autoplay`（官方 `CmdLine64.txt`） |
| 播放控制 | `WM_COMMAND(0x0111)`：`10014` 播放暂停 / `20001` 播放 / `20000` 暂停 / `20002` 停止 / `10123` 上一集 / `10124` 下一集；`WM_USER(0x0400)`：`0x5007` 设状态、`0x5008` 切集、`0x5010` 发虚拟键（免焦点，2023-08-30 后版本）。实测 `via=command:10014` 生效 |
| WebView2 | Edge WebView2 157 已安装，pywebview `gui="edgechromium"`；必须 `private_mode=True`、不传 `background_color` |
| 原生标题栏 | 由 DWM 绘制、CSS 管不到；`app/theme_native.py::apply()` 用 `DWMWA_USE_IMMERSIVE_DARK_MODE=20`（旧系统回退 19）同步，实测可设可读回 `dark=1`；`DWMWA_CAPTION_COLOR=35` 本机返回失败，静默忽略即可 |
| Everything | `D:\tool\Everything\Everything.exe` + 官方 `es.exe`（ES-1.1.0.38）都在。但 Everything 1.4.1.1032 以 `-svc`（会话 0 服务）运行，**es.exe 连不上 IPC（Error 8）**，`http_server_enabled=0`；要全盘检索需用户在 Everything 选项启用 HTTP 服务并填地址。核心引擎闭源、无法编译进本软件（详见 `docs/FEATURES.md` §22-C） |
| 封面源 | **两个搜索 URL 实测均 200 可用**：AGE动漫 `www.agedm.io/search?query=`（`data-original` 是百度代理，`src` 是共用占位图，必须排除）与 Seeduck `seeduck.cc/s/<kw>/`（`<h2>` 短标题 + `img src` 真封面）。旧结论「搜索被 CF 拦、只能用首页池」已作废。本机 Python 需 `verify=False` |
| 数据目录 | `./start` / 安装版 / 守护 / 开机自启 = `D:\magicvideo`（D 盘，真实库，2026-11-15 由 `%APPDATA%\Magic Video` 迁移，旧目录保留）；`data/dev` 是 `dev.bat` 的安全副本，别把封面/记录只写进副本 |
| 网络 | 本机直连 `api.bgm.tv` / TMDB / Jikan **超时**；GitHub / pypi 可达 |
| git / GitHub | `origin = git@github.com:tiantangliwai/magic-video.git`（私有）；SSH 密钥已认证，无 gh CLI / PAT；tag 为 `v0.1.0`~`v0.1.6`（已推送）+ `v0.1.7`（**仅本地**，2026-11-17 只打包未推送） |
| 打包工具链 | `.venv`（PyInstaller 6.22 / pywebview 6.2 / pythonnet 3.2）、WiX 3.14.1 在 `build/tools/wix/tools`（NuGet 镜像下载）；GitHub Release 直连 SSL 握手失败，改用 NuGet 镜像。**打包前必须先把 `build/pyi-dist/Magic Video` 改成 `Magic Video.prev`**：`COLLECT` 会先删旧目录，该操作被安全删除策略拦下（`SAFE_DELETE_BULK_CONFIRM_REQUIRED`）导致构建失败 |
| MSI 安装上下文 | 本机即使 `InstallScope="perUser"` 也会以提权方式执行（日志 `MsiRunningElevated=1`、`Privileged=1`），ARP 注册落在 `HKLM\Software\WOW6432Node\...\Uninstall`。因此：① 向导里的公共属性必须 `Secure="yes"` 才会传到执行序列；② 新旧包作用域一致，`MajorUpgrade` 能正常发现并卸载旧版 |
| Obsidian | `%APPDATA%\obsidian\obsidian.json` 注册 2 个 vault；主库 `C:\Users\Administrator\Documents\Obsidian Vault`，另一个是装修项目专用库（不写） |
| 同步产物 | `data/test_last.json`（测试结果）、`data/obsidian_history.json`（结果时间线）；均在 `.gitignore` 内 |

---

## 9. 常见任务速查

| 想做什么 | 从哪下手 |
|---|---|
| 加一个界面按钮 | `web/app.js` 的 `data-action` + `handleAction`，后端在 `app/api.py` 加同名方法 |
| 改文件名解析规则 | `app/scanner.py::parse_filename` + `tests/test_scanner.py::FilenameTests` |
| 改媒体库树分组 / 季集推断 | `app/library_tree.py` + `app/api.py::get_media_tree` + `web/app.js` 树渲染 + `tests/test_media_tree.py` |
| 扩充中文词典 | `app/data/aliases_builtin.json`（真实条目，禁止编造）+ `tests/test_metadata.py` |
| 改观看完成判定 | `app/watcher.py::_tick` + `tests/test_watcher.py` |
| 加数据库字段 | `app/db.py::SCHEMA` + `_migrate()` 补列，并写迁移不丢数据的用例 |
| 加 CLI 命令 | `cli.py` 的 `choices` 与分支 + README 第 5 节 |
| 改 Obsidian 笔记内容 | `app/obsidian.py` 的 `render_*` + `tests/test_obsidian.py`；改路线图请改 `docs/OPTIMIZATION.md` |
| 发新版 | 走第 3.2 节清单，跑 `tests/test_docs.py` 验证一致性 |
| 加/改网页搜索或播放指令 | `app/websearch.py`（解析 → 合并 → 来源标记 → `open_url` 白名单）或 `app/potplayer.py::control()`（`WM_COMMAND` → `POT_SET_PLAY_STATUS` → 按键，逐条退让），配 `tests/test_websearch.py` / `tests/test_player_controls.py` |
| 视图打开变慢 | 先看 `app/api.py::_db_signature`（缓存是否真命中）→ `blacklist` 是否每次开连接 → `library_tree._is_tech_token` 是否现拼正则 → 封面是否逐张 `resolve_cover` |
