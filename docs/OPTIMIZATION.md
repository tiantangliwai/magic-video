# 同类软件调研与优化建议 · v0.1.2

> 调研时间：2026-11-15。对象为「本地视频播放历史 + 追番/看剧记录 + 断点续播 + 元数据刮削」类软件。
> 目的：找出成熟产品的通用做法与常见坑，给 Magic Video 排出可落地的优化优先级。
> **本文是建议，不是已实现功能**；已落地项在第 5 节单独标注。

---

## 1. 结论速览

1. **进度模型高度收敛**：主流方案都用「双阈值」——低于下限不记进度，高于上限直接判已看并清续播点。Kodi 的 90% / 180 秒 / 8% 三参数是最值得抄的成熟模型。
2. **播放器级方案的通病是「以路径为键」**：VLC、MPC-HC、PotPlayer 都用文件路径或最近列表做主键，文件一移动记录就丢。这是本工具最该补的差异化点。
3. **元数据识别的关键不是刮削，而是稳定 ID**：Plex 用 `{tmdb-123}` 标签把猜测变成直查，Shoko 用 AniDB ED2K 哈希完全绕开文件名。中文名问题本质是多标题映射 + 稳定 ID。
4. **「输入一份看过的清单 → 自动与本地文件和播放历史对齐」没有一家提供完整一键能力**。最接近的是 AniDB MyList Import、Kodi NFO 导入导出、Trakt/Shoko 同步三条路径。这恰是本项目的核心机会点。
5. **坑集中在四处**：SQLite 并发写、刮削限流与负缓存、路径失效/网络盘、会话切分与重复计数。

---

## 2. 竞品对比表

| 软件 | 进度记录方式 | 元数据来源 / 文件名解析 | 最值得借鉴的点 |
|---|---|---|---|
| **Jellyfin** | `MinResumePct` / `MaxResumePct` / `MinResumeDurationSeconds` 三阈值；约 90% 标已看并重置；REST `PlayedItems` 标记 | TMDB/TVDB/IMDb，支持 `[tvdbid-]` 等 ID 标签、`PathSubstitutions` | 三阈值可配置、路径重映射、Playback Reporting 统计插件 |
| **Plex** | 90% "video played threshold"（可配）+ On Deck；`Ctrl+W`/`Ctrl+U` 批量标记、scrobble/unscrobble | 事实标准命名规范：`Title (Year)`、`Season NN`、`sXXeYY`、`{imdb-/tmdb-/tvdb-}`、`{edition-}` | **命名规范最完整**；ID 标签强制直查；Merge / Split Apart / Fix Match / Empty Trash 重定位流程 |
| **Emby** | `MaxResumePct`(90) / `MinResumePct`(5)；每 10 秒心跳 + 服务器自行递增并校准 | 多 agent、NFO、语言偏好 | **Playback Check-ins 协议**（Playing → Progress → Stopped）是会话切分的现成范本 |
| **Kodi** | `playcountminimumpercent` 90 / `ignoresecondsatstart` 180 / `ignorepercentatend` 8；`W` 键切换已看并清 resume | NFO 本地优先 + 多语言刮削；正则排除 sample/trailer | **最干净的双阈值模型**；NFO 把元数据 + playcount + resume 放同一文件，天然可备份 |
| **Shoko Server** | 无秒级续播，只有 AniDB MyList 集级 watched 状态 | **AniDB ED2K 文件哈希**（不看文件名）；原生处理 OVA/SP/多标题/分割季度 | 内容哈希识别、缺失文件管理、系列关系图、Upcoming Anime 日历 |
| **AniList / MAL / Bangumi** | 状态 + 已看集数 + 评分 + 起止日期 | 站点条目库；Bangumi 提供中文名/原名/每日放送 | MAL XML 是事实上的清单交换格式；导入「进度取 max / 不覆盖手工修改 / 未匹配进待办」 |
| **tinyMediaManager / MediaElch** | 可标 watched（tMM 有 Trakt 同步） | TMDB/TVDB/IMDb，写 Kodi 兼容 NFO + 图片 | **刮削结果落盘成 NFO**；任意字段手工可编辑；批量重命名 |
| **FileBot** | 无（纯整理） | `{plex}` 命名方案、`--q`/数字 ID 强制匹配、`excludeList` | **重命名可回滚**、排除列表防重复处理 |
| **PotPlayer / MPC-HC / VLC** | 记忆播放位置（PotPlayer 默认关）、播放历史、书签；VLC 本地文件 >3 分钟且停在 5%–95% 才记 | 无 | VLC 的 5%–95% 防呆区间；MPC-HC 历史对话框可重置位置；`.pbf` 书签随视频目录走 |

### 「清单 → 自动对齐」能力现状

| 能力 | 谁提供 | 局限 |
|---|---|---|
| 清单 → 追番站点 watched 状态 | AniDB MyList Import（上传 MAL XML） | 只对齐条目/集，不碰本地文件 |
| 站点间清单迁移 | MAL → AniList 导入 | 与本地文件无关 |
| 清单 → 本地文件（最接近） | Shoko：ED2K 哈希绑定具体集 + MyList/Plex 同步 | 需 AniDB 账号、仅动画、首次全库哈希慢 |
| 播放历史 → 可移植清单 | Kodi Import/Export Library（NFO 含 playcount/resume） | 需提前配置 advancedsettings |
| 增量同步 | Jellyfin Trakt 插件、Ani-Sync、ShokoRelay | 依赖外部账号/插件 |

**正确做法是本项目的三段式**：① 清单每行 → 稳定作品 ID；② 本地文件 →（作品 ID, 季, 集）；③ 清单「已看」× 本地播放历史 → 冲突消解 → 统一进度表。冲突规则抄 AniList：进度取 max、状态不降级、默认不覆盖手工修改、未匹配进待确认、限流失败可重试。

---

## 3. 优化建议（按优先级）

> 改动量：小 <0.5 人日 / 中 1–3 人日 / 大 >1 人周。模块名对应本仓库实际文件。

### P0 · 低成本高价值，建议优先做

| # | 建议 | 落地模块 | 改动量 | 价值 |
|---|---|---|---|---|
| 1 | **三阈值可配置**：`min_resume_percent`(5)、`max_resume_percent`(90)、`ignore_seconds_at_start`(180)、`ignore_percent_at_end`(8)；超上限标已看+清续播点，低于下限不生成续播记录 | `watcher.py`、`config.py`、设置页 | 小 | 消除「剩 5 分钟判没看」「点开 3 秒生成续播记录」两类最常见投诉 |
| 2 | **主键从路径换成文件指纹 + 稳定作品 ID**：存 `size/mtime/partial_hash`，进度挂到 `(作品ID, 季, 集)`；移动/重命名后按指纹重关联 | `db.py`、`scanner.py` | 中 | 直击 VLC/MPC-HC/PotPlayer 最大痛点 |
| 3 | **一键标记已看/未看/重置进度 + 批量多选** | `api.py`、`web/app.js` | 小 | 纠错成本趋近于零（**v0.1.2 已做单机版，批量待补**） |
| 4 | **会话切分与去重**：仿 Emby 的 `Started → Progress(10s + 事件立即上报) → Stopped`，按真实播放时间累计；同一集只保留一个活跃 session；连播按「最后一次用户输入」判定有效性 | `watcher.py` | 中 | 避免快进到结尾算看完、睡着后自动连播虚增时长、重复计数 |
| 5 | **导入/导出备份做成一等公民**：JSON 完整备份 + 可选 NFO/CSV 导出，带 `覆盖/仅新增` 开关 | 新 `app/backup.py` | 中 | 换机/换盘不丢数据，降低被锁定感 |

### P1 · 中等成本

| # | 建议 | 落地模块 | 改动量 | 价值 |
|---|---|---|---|---|
| 6 | **文件名解析「ID 优先 → 严格 SxxEyy → 模糊」**：支持 `{tmdb-}` / `{tvdb-}` / `[tvdbid-]` 直查；对 `1x01`、`第01话`、绝对集数标低置信度进待确认；排除 sample/trailer | `scanner.py` | 中 | 把「猜错」变成「请用户确认」（**v0.1.2 已修基础解析与 SP 误杀，ID 直查待补**） |
| 7 | **别名表 + 稳定作品 ID 层**：`alias → work_id`，展示标题按语言偏好选；元数据含 year/season/episode/poster/externalIds | `metadata.py`、`db.py` | 中 | 解决中文名、别名、重名翻拍、动画多罗马字 |
| 8 | **清单导入自动对齐三段式**：支持 MAL XML / Bangumi Takeout JSON；导入预览给差异报告；冲突按 max/不降级/不覆盖规则合并 | `importer.py`、新 `app/align.py` | 大 | **被调研软件集体留下的空位，最贴合本产品定位** |
| 9 | **路径失效重定位向导**：不可达先标「缺失」并保留进度，提供批量前缀替换与指纹重匹配，最后才由用户确认清理 | `scanner.py`、UI | 中 | 移动硬盘/网络盘高频场景，不误删历史 |
| 10 | **刮削限速 + 缓存 + TTL 负缓存**：请求间隔可配（Bangumi ≥200ms~5s），成功长期缓存，失败设 TTL，429 退避并暂停而非狂重试；提供「强制重刮」 | `metadata.py` | 中 | 防封禁、防错误匹配永久固化 |

### P2 · 体验层

| # | 建议 | 落地模块 | 改动量 | 价值 |
|---|---|---|---|---|
| 11 | **按季分组海报墙 + 继续观看置顶行** | `web/` + `subjects.cover_local` | 中 | 从文件管理器升级为追番工具 |
| 12 | **看片时长统计页**：按日/周/月、按作品、按时段统计有效观看分钟数、完成率，明确统计口径 | `api.py` + 新页面 | 中 | 播放历史升级成有反馈感的产品 |
| 13 | **多版本文件合并/拆分**：同一 `(作品, 季, 集)` 多文件合并为一个条目，播放时选版本 | `db.py`、UI | 中 | 收藏党刚需，避免一集三张卡片 |
| 14 | **观看日历 + 标签/评分 + 自定义字段** | UI + 新表 | 中 | 低成本丰富度提升 |

### P3 · 高成本，核心稳定后再考虑

| # | 建议 | 说明 |
|---|---|---|
| 15 | 外部站点双向同步（Bangumi / AniList / MAL / Trakt） | 涉及 OAuth、隐私、限流；默认关闭、显式授权、本地优先 |
| 16 | 内容哈希识别（ED2K / 头尾部分哈希） | 大库首次扫描 I/O 重，需缓存与后台化 |
| 17 | 跨设备共享进度 | 需要服务端或共享数据库，与「纯本地」定位冲突，需用户确认 |

---

## 4. 避坑清单

1. **不要把进度表主键设为文件路径** —— VLC/MPC-HC/PotPlayer 的续播丢失全源于此；至少存 `(size, mtime, 部分哈希)`。
2. **不要在存储不可达时自动删库条目** —— Jellyfin/Plex 都把「自动清理 + 外部盘偶尔离线」列为状态丢失成因；先标「缺失」、让用户确认。
3. **不要并发写 SQLite、不要把 DB 放网络盘** —— WAL 只解决并发读，不消除写-写冲突；用短事务 + `busy_timeout` + 写队列，刮削/扫描/备份错峰。
4. **不要无节流批量刮削，也不要把「匹配失败」永久缓存** —— AniDB 会因洪水封禁 15 分钟~24 小时；负结果设 TTL 并留「强制重刮」入口。
5. **不要按墙钟时间算观看时长、不给自动连播无条件记完成** —— 需合并重叠区间，统计口径写明是否含快进/重看/连播。
6. **不要假设路径是 ASCII / 本地盘** —— 全链路 UTF-8；Windows 长路径、特殊字符归一化；NFS/rclone 不支持实时监看，退回定时扫描。
7. **不要让刮削刷新覆盖用户手工修改** —— 区分「刮削值」与「用户覆盖值」，字段带锁语义（**v0.1.2 已保证用户修正优先级最高**）。
8. **不要把观看记录悄悄上传、不要默认开启云同步** —— 默认本地、显式授权、一键可关、可导出迁移。
9. **不要让「标记未看」删掉历史条目** —— Plex `unscrobble` 只清 viewCount 保留 history（**v0.1.2 已按此实现**）。
10. **不要让导入静默丢弃未匹配项或默认覆盖手工数据** —— 给差异预览、未匹配进待确认（**v0.1.2 预览可编辑，但尚无正式「待确认」区**）。

---

## 5. 本轮（v0.1.2）已吸收的建议

| 建议 | 落地情况 |
|---|---|
| #3 一键标记已看/未看/重置进度 | ✅ `Api` 三个方法 + UI「已看完（可纠错）」区块 + CLI `mark`；纠错不删会话 |
| #6 文件名解析基础修复 | ✅ `SxxEyy` 优先、年份/季/集标记清除、结尾集数识别、`SP` 不再误杀正片 |
| #7 用户修正优先级 | ✅ `rebuild_index` 让 `user`/`manual` 词典覆盖内置词典与在线结果 |
| #9 不误删历史 | ✅ 目录不可达只跳过；单文件不可读不中断扫描；纠错保留会话 |
| #10 负缓存思想（部分） | ✅ `local_only` 不再虚报在线可用，在线失败整批降级并留「重新匹配」入口 |
| #4 会话稳定性（部分） | ✅ 修复首次轮询不写进度/完成判定的缺陷 |

**下一版建议优先做**：#1 三阈值可配置、#5 JSON 备份导出、#9 路径重定位向导 —— 三项都是小到中等改动，且直接提升「历史不丢、续播准确」这一核心体验。

---

## 6. 主要来源

- Kodi Wiki：《HOW-TO:Modify automatic watch and resume points》《advancedsettings.xml》《Import-Export library》 — https://kodi.wiki/view/HOW-TO:Modify_automatic_watch_and_resume_points
- Plex Support：《Naming and organizing your Movie media files》《Naming and Organizing Your TV Show Files》《Credits Detection》 — https://support.plex.tv/articles/naming-and-organizing-your-tv-show-files/
- Jellyfin：`ServerConfiguration` SDK、官方博客《SQLite concurrency and why you should care about it》 — https://jellyfin.org/posts/SQLite-locking/
- Emby DEV：《Playback Check-ins》《LibraryOptions》 — https://dev.emby.media/doc/restapi/Playback-Check-ins.html
- Shoko Docs：《Actions》《Understanding AniDB Bans》《Watch State Sync》 — https://docs.shokoanime.com/
- AniDB Wiki：《MyList Import》《UDP API Definition》 — https://wiki.anidb.net/MyList_Import
- FileBot CLI manual — http://www.filebot.net/cli.html
- tinyMediaManager 官网 — http://www.tinymediamanager.org/
- AniList MAL import 合并策略（第三方 Wiki）— https://github.com/hacan359/tonkatsu_box/wiki/MyAnimeList-Import
- PotPlayer `CmdLine64.txt`（本机安装目录内官方命令行说明）

> 说明：VLC 的 5%–95% 规则、PotPlayer 记忆播放位置细节、Jellyfin `MinResumePct` 具体默认值来自开发者引述或社区资料，非官方文档确证；实现前需自行验证。

---

## 7. 与 Obsidian 的联动（v0.1.3 新增）

本文第 3 节的 P0–P3 表格是 Obsidian 笔记 `02 优化路线图.md` 的**单一真源**：`app/obsidian.py::parse_roadmap()`
按 `### P0`…`### P3` 分节读取表格，自动生成可勾选清单。要增删或调整建议，只改本文，同步一次笔记即更新。

第 4 节避坑清单与本页「下一版建议」同时出现在 `04 功能缺口与决策.md` 的待办中；
在 Obsidian 里勾选、批注、排优先级都不影响本仓库，也不会被下次同步覆盖。详细机制见 `docs/OBSIDIAN.md`。
