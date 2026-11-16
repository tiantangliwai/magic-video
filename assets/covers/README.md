# 公共封面资产

这个目录是**随安装包分发的封面库**：80 张已抓好的动漫封面 + 一份清单 `index.json`。
新装、换机器、完全离线时都能直接显示封面，不必等一轮抓取。

> 本地抓到的封面仍然优先。这里的图只在**本机没有这张图**时被复制一份到
> 数据目录（`D:\magicvideo\covers\`），不会覆盖你已经抓到的或手工选过的封面。

## 目录约定

- 图片文件：`<文件名>.jpg` / `.png`，现在是 `sha1(归一化标题)[:20]` 的哈希名，
  但**不强制**——真正的对应关系写在 `index.json` 里。
- `index.json`：`{file, title, aliases, source, cover_url, sha256, bytes, updated_at}`，
  应用按 `title` 与 `aliases`（原名）匹配卡片。

## 换掉某部剧的封面（最常见）

1. 在 `index.json` 里搜作品名，找到它的 `file`，例如
   `{"file": "ae28d343db1a34c556ff.jpg", "title": "2.5次元的诱惑", ...}`。
2. 用新图**覆盖同名的那个 jpg**。
3. 在仓库根目录跑一次，让清单里的哈希与体积跟上新图：

   ```bash
   .venv/Scripts/python.exe export_covers.py --refresh-index
   ```

4. 提交 PR。改动只有「一张图 + `index.json` 里那一条的 `sha256`/`bytes`/`updated_at`」。

## 新增一部作品

1. 把图片放到本目录（文件名随意，建议仍用哈希名），例如 `my-new-cover.jpg`。
2. 在 `index.json` 的 `covers` 数组里加一条：

   ```json
   {
     "file": "my-new-cover.jpg",
     "title": "作品中文名",
     "aliases": ["作品原名"],
     "source": "community",
     "sha256": "",
     "bytes": 0,
     "updated_at": "2026-11-17 10:00:00"
   }
   ```

3. 跑 `export_covers.py --refresh-index` 补齐 `sha256` / `bytes`，提交 PR。

## 图片要求

- 竖版 2:3 海报（剧中卡片按 2:3 显示，横图会被裁切）。
- 建议宽 ≥ 300px，单张 < 1.5 MB；jpg 或 png。
- 别放二维码、水印条、字幕组宣传图。

## 不要放什么

- 不要把 `source` 写成 `manual`：那是「用户自己手选的封面」的标记，导出脚本会跳过它，
  也不该出现在公共资产里（否则别人「清除封面」会被这张图复活）。
- 不要删除别人的条目：导出脚本不会清理资产里「本机没有」的文件。

## 版权提示

这里收录的是各作品的宣传海报，版权归原作者与发行方所有，仅用于本地软件识别作品时
显示封面。如果你是该作品的版权方并要求移除，请提 issue，我们会删掉对应文件。
