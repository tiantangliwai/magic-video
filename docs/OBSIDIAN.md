# Obsidian 集成说明 · v0.1.5

> 目标：让**开发流程、自测结果、优化路线图、功能缺口**自动沉淀到本机 Obsidian，
> 后续优化和补齐功能时有一份可持续更新的、可手写的知识底稿，而不用翻 git log 或聊天记录。
> 本文是功能与契约说明；实现见 `app/obsidian.py`，用例见 `tests/test_obsidian.py`。

---

## 1. 它做了什么

| 同步什么 | 落到哪 | 数据来源 |
|---|---|---|
| 项目状态快照（版本、提交、媒体数、会话数、词典量） | `00 项目总览.md` | `db` / `git` / `__version__` |
| 版本变更历史 | `01 版本记录.md` | 仓库 `CHANGELOG.md` 镜像 |
| P0–P3 优化建议（可勾选清单） | `02 优化路线图.md` | `docs/OPTIMIZATION.md` 解析 |
| 最近一次单元测试结果 + 结果时间线 | `03 自测报告.md` | `data/test_last.json` + `data/obsidian_history.json` |
| 待补齐清单 + ADR 决策记录 | `04 功能缺口与决策.md` | `app/obsidian.py` 中的真实状态常量 |
| 启动 / CLI / 数据位置速查 | `05 使用速查.md` | 静态模板 |
| 按天的开发流程日志（带事件指纹去重） | `06 开发日志/YYYY-MM-DD.md` | 每次同步自动追加 |

所有笔记带 YAML frontmatter（`title` / `tags` / `version` / `generated_at` / `source` / `managed`）与
`[[wikilink]]` 互链，Obsidian 的图谱、搜索、标签面板可直接用。

---

## 2. 本机实际配置

从 `%APPDATA%\obsidian\obsidian.json` 只读探测到两个 vault：

| vault 路径 | 用途判断 | 是否写入 |
|---|---|---|
| `C:\Users\Administrator\Documents\Obsidian Vault` | 通用主库，仅 2 篇设计方案笔记 | ✅ **本工具使用** |
| `D:\project\房屋装修设计\装修设计参考` | 另一个项目的专用资料库 | ❌ 完全不碰 |

**多个 vault 时软件绝不擅自猜**：`default_vault()` 只在候选恰好 1 个时自动采用，
否则要求在设置页或 CLI 显式指定。首次接入时已把主库路径写入 `settings.obsidian_vault`。

- 笔记文件夹：`Magic Video/`（vault 内相对路径，可改）
- 实际目录：`C:\Users\Administrator\Documents\Obsidian Vault\Magic Video\`
- 配置存放：`data/library.db` 的 `settings` 表，键 `obsidian_vault` / `obsidian_folder` / `obsidian_auto_sync`

---

## 3. 安全边界（最重要）

1. **只读你已有的笔记**：`detect_vaults()` 只读 Obsidian 的 `obsidian.json`；扫描过程不读取、不修改任何已有 `.md`。
2. **只写独立命名空间**：所有写入都经 `safe_folder()` 校验为单层/多层相对路径，禁止 `..`、绝对路径、盘符；
   `resolve_vault()` 二次用 `relative_to()` 确认解析后仍在 vault 内。
3. **绝不覆盖同名用户笔记**：每篇托管笔记带 `<!-- managed-by: Magic Video v1 -->` 标记。
   遇到同名但没有标记的文件，一律返回 `skipped_unmanaged` 并跳过。
4. **批注永久保留**：自动内容只在 `<!-- BEGIN AUTO --> … <!-- END AUTO -->` 之间替换，
   区块外的「我的批注」区原样保留；frontmatter 只刷新 `version` / `generated_at` 两行，
   用户自己加的 `aliases` / `cssclasses` / `tags` 不会被冲掉。
5. **原子写**：先写 `.mvtmp` 临时文件再 `os.replace`，避免 Obsidian 读到半截内容。
6. **永不删除**：模块内没有任何 `unlink` 用户文件的路径（只清理自己刚创建的临时文件）。
7. **失败不阻塞主流程**：`sync()` 把所有异常转成 `{"ok": false, "error": ...}`，界面/CLI 不会因同步失败崩溃。

> 因此你可以直接在 Obsidian 里勾选路线图、写想法、加标签，下次同步不会丢失。

---

## 4. 三种同步入口

### 4.1 界面（推荐）

「设置」页 → **Obsidian 知识沉淀** 卡片：

- 显示检测到的所有 vault、是否可同步、已生成笔记数、上次同步时间、最近测试结论；
- 填写 vault 路径与笔记文件夹 → 点「保存设置」；
- 点「同步到 Obsidian」立即生成/更新全部笔记；
- 点「打开笔记目录」在资源管理器里打开；
- 开关「跑完单元测试自动同步」控制命令行 `cli.py test` 后的自动留痕。

### 4.2 命令行

```bat
python cli.py obsidian status                  :: 查看 vault / 配置 / 最近测试结果
python cli.py obsidian vaults                  :: 列出探测到的 vault
python cli.py obsidian sync                    :: 同步全部笔记
python cli.py obsidian path "D:\My Vault"      :: 指定 vault
python cli.py obsidian folder "Magic Video"    :: 指定笔记文件夹
python cli.py obsidian auto on                 :: 测试后自动同步开关
python cli.py obsidian open                    :: 打开笔记目录
```

### 4.3 测试触发（可选自动）

`python run_tests.py` 或 `python cli.py test` 会把结果写入 `data/test_last.json`：

```json
{
  "timestamp": "2026-11-15 01:20:00",
  "version": "0.1.5",
  "total": 144, "passed": 144, "failures": 0, "errors": 0, "skipped": 0,
  "success": true, "duration_sec": 4.2,
  "modules": {"tests.test_db": 12, "...": "..."}
}
```

若 `obsidian_auto_sync=1` 且测试全绿，`cli.py test` 会追加一条 `cli test` 事件到当天日志，
并更新 `03 自测报告.md` 的结果时间线。默认**关闭**，不会在你没同意时写 vault。

---

## 5. 笔记内部契约

```
---
title: 00 项目总览
tags: [magic-video, status]
version: 0.1.5
generated_at: "2026-11-15 01:20:00"
source: Magic Video
managed: true
---
<!-- managed-by: Magic Video v1 -->
# 00 项目总览

<!-- BEGIN AUTO -->
（每次同步替换：状态表 / 结果 / 路线图 / 索引）
<!-- END AUTO -->

## 我的批注（同步不会覆盖）
> 在这里写想法、决策和待办。
```

开发日志用 `<!-- BEGIN LOG --> … <!-- END LOG -->` + `<!-- event:<12位指纹> -->`：
指纹由「日期 + 事件类型 + 触发原因 + 版本 + 提交 + 用例数 + 会话数」算出，
同一状态重复同步不会刷屏；换一个触发原因或测试结论才会追加新事件。

历史结果同时保存在 `data/obsidian_history.json`（最近 200 条），
即使 Obsidian 笔记被删也能重建时间线。

---

## 6. 与版本管理的关系

- 笔记内容里的版本号来自**唯一真源** `app/__init__.py::__version__`；
- `01 版本记录.md` 镜像 `CHANGELOG.md`，发版同步一次即刷新；
- `02 优化路线图.md` 直接解析 `docs/OPTIMIZATION.md` 的 P0–P3 表格，
  **文档是单一真源**：改建议只改仓库文档，Obsidian 自动跟着变，不需要两边维护；
- `tests/test_docs.py` 守护 `docs/OBSIDIAN.md` 存在、README/AGENTS 声明 Obsidian、配置与 CLI 命令齐全；
- Obsidian vault 在仓库之外，**不入库、不受 git 管理**，笔记不会被 `git status` 污染。

---

## 7. 故障排查

| 现象 | 原因 | 处理 |
|---|---|---|
| `同步失败：检测到多个 Obsidian vault` | 本机注册了多个库，软件不猜 | 设置页填一个明确路径，或 `cli.py obsidian path "..."` |
| `vault 不存在或不是目录` | 路径变化 / 移动过库 | 重新指定；原笔记不会被删除 |
| 某篇笔记一直不更新 | 同名文件缺少 `managed-by` 标记（你自己的笔记） | 保留不动；换个笔记文件夹或给文件加标记 |
| `03 自测报告` 显示「尚无」 | 还没跑过 `run_tests.py` | 跑一次再同步 |
| frontmatter 的 tags 被改了但同步后没恢复 | 设计如此：只刷新版本与时间 | 用户自定义字段永久保留 |
| 自动同步没生效 | `obsidian_auto_sync` 默认 0 | 设置页打开开关，且必须用 `cli.py test` 才触发 |

---

## 8. 不做的事（避免误解）

- 不安装、不配置、不启动 Obsidian，不读写它的插件配置 `.obsidian/`；
- 不用 `obsidian://` URI、不依赖任何 Obsidian 插件或社区 API；
- 不把观看记录、视频路径、文件名写进笔记（笔记只有**聚合统计**与项目文档，不含隐私明细）；
- 不做定时自动同步、不做后台常驻轮询 vault —— 同步只在**你点按钮 / 跑命令 / 测试通过且开关打开**时发生；
- 不双向同步：Obsidian 里的手写内容只保留、不回写数据库或改代码。
