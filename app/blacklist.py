"""视频黑名单：名称包含任一关键词的视频在展示位置默认隐藏。

用户约定（2026-11-15）：
- 判定是「名称包含黑名单字段」，**不区分大小写**，不做分词或模糊相似度。
- 有独立开关，默认开启；关闭后所有位置恢复展示全部。
- 只影响「展示」：不删 ``media_files`` 行、不动播放进度 / 观看会话、
  也不从追剧清单里删条目（追剧清单是用户手写的待办，只过滤它的本地匹配结果）。

关键词来源是设置项 ``video_blacklist``，以逗号 / 顿号 / 分号 / 竖线 / 换行分隔。
短英文词（``jul`` / ``ipx`` / ``ure``）按包含匹配可能与正常片名撞车，
这是用户明确要的语义，改词表即可，代码不做二次猜测。

匹配分两段（2026-11-15 用户反馈「剧集墙还能看到黑名单剧集」后修正）：

1. SQL 粗筛（:func:`sql_clause`）：``lower(字段) LIKE '%词%'``，走数据库一次扫表，
   便宜但**对连接符敏感**——词表里的 ``FC2PPV`` 匹配不到文件名里的 ``FC2-PPV``。
2. Python 精确过滤（:func:`hit` / :func:`filter_rows`）：两侧都去掉
   ``- _ . · 空格`` 再比，能命中 ``FC2-PPV-1288053``、``mvsd.naiad``。
   另外目录名派生的分组标题（剧集墙卡片标题、媒体库树分组名）只能在这一层判断，
   因为文件名里可能根本没这个词。

**字形归并**（2026-11-15 用户追问「黑名单里有三上悠亚，为什么剧集墙里还是有」）：
片名大量来自日语资源，同一个字常有两种写法——日文新字体 ``亜`` / 繁体 ``亞`` /
简体 ``亚``。它们是**三个不同码位**，直接 ``in`` 判断永远不命中。因此
:func:`normalize_name` 在比较前先做 NFKC（全角字母数字转半角）再查
:data:`_VARIANTS` 把异体字归到规范简体。**只改比较、不改展示**：卡片标题、
文件名、词表原文一律原样保留。
"""
from __future__ import annotations

import re
import unicodedata

from . import config, db

_SPLIT_RE = re.compile(r"[,，、;；|\r\n]+")

# 归一化时要丢掉的连接符：片名里 `FC2-PPV-1288053`、`mvsd.naiad`、`SSIS_001`
# 和用户词表里的 `fc2ppv` / `mvsd.naiad` 写法不统一，只做包含比较会整片漏掉。
# 日文片名里的 `・`（片假名中点）和 `･`（半角）也算连接符。
_SEP_CHARS = " -_.·　・"

# 异体字 → 规范简体。**只收字形一一对应的异体**（日文新字体 / 繁体 / 旧字形），
# 不做多义合并（乾/干、著/着、藉/借这类不进来），否则会把无关作品一起误伤。
# 命中的场景：词表写「三上悠亚」，片名是「三上悠亜」（U+4E9C）或「三上悠亞」（U+4E9E）。
_VARIANTS = {
    "亜": "亚", "亞": "亚", "悪": "恶", "惡": "恶", "圧": "压", "壓": "压",
    "囲": "围", "圍": "围", "為": "为", "爲": "为", "隠": "隐", "隱": "隐",
    "栄": "荣", "榮": "荣", "営": "营", "營": "营", "衛": "卫", "衞": "卫",
    "駅": "驿", "驛": "驿", "円": "圆", "圓": "圆", "塩": "盐", "鹽": "盐",
    "奥": "奥", "奧": "奥", "応": "应", "應": "应", "欧": "欧", "歐": "欧",
    "殴": "殴", "毆": "殴", "桜": "樱", "櫻": "樱", "絵": "绘", "繪": "绘",
    "壊": "坏", "壞": "坏", "懐": "怀", "懷": "怀", "拡": "扩", "擴": "扩",
    "殻": "壳", "殼": "壳", "覚": "觉", "覺": "觉", "学": "学", "學": "学",
    "岳": "岳", "嶽": "岳", "楽": "乐", "樂": "乐", "巻": "卷", "卷": "卷",
    "陥": "陷", "陷": "陷", "勧": "劝", "勸": "劝", "寛": "宽", "寬": "宽",
    "関": "关", "關": "关", "歓": "欢", "歡": "欢", "気": "气", "氣": "气",
    "帰": "归", "歸": "归", "亀": "龟", "龜": "龟", "暁": "晓", "曉": "晓",
    "狭": "狭", "狹": "狭", "郷": "乡", "鄉": "乡", "拠": "据", "據": "据",
    "挙": "举", "舉": "举", "峡": "峡", "峽": "峡", "挟": "挟", "挾": "挟",
    "経": "经", "經": "经", "蛍": "萤", "螢": "萤", "軽": "轻", "輕": "轻",
    "継": "继", "繼": "继", "鶏": "鸡", "鷄": "鸡", "芸": "艺", "藝": "艺",
    "撃": "击", "擊": "击", "県": "县", "縣": "县", "倹": "俭", "儉": "俭",
    "剣": "剑", "劍": "剑", "圏": "圈", "圈": "圈", "検": "检", "檢": "检",
    "険": "险", "險": "险", "顕": "显", "顯": "显", "厳": "严", "嚴": "严",
    "広": "广", "廣": "广", "効": "效", "鉱": "矿", "礦": "矿", "号": "号",
    "號": "号", "国": "国", "國": "国", "黒": "黑", "黑": "黑", "済": "济",
    "濟": "济", "砕": "碎", "斎": "斋", "齋": "斋", "剤": "剂", "劑": "剂",
    "雑": "杂", "雜": "杂", "参": "参", "參": "参", "桟": "栈", "棧": "栈",
    "賛": "赞", "贊": "赞", "残": "残", "殘": "残", "糸": "丝", "絲": "丝",
    "歯": "齿", "齒": "齿", "児": "儿", "兒": "儿", "辞": "辞", "辭": "辞",
    "湿": "湿", "濕": "湿", "実": "实", "實": "实", "写": "写", "寫": "写",
    "釈": "释", "釋": "释", "寿": "寿", "壽": "寿", "収": "收", "收": "收",
    "従": "从", "從": "从", "渋": "涩", "澀": "涩", "獣": "兽", "獸": "兽",
    "縦": "纵", "縱": "纵", "粛": "肃", "肅": "肃", "処": "处", "處": "处",
    "緒": "绪", "緖": "绪", "叙": "叙", "敘": "叙", "将": "将", "將": "将",
    "奨": "奖", "獎": "奖", "称": "称", "稱": "称", "証": "证", "證": "证",
    "渉": "涉", "涉": "涉", "焼": "烧", "燒": "烧", "縄": "绳", "繩": "绳",
    "壌": "壤", "壤": "壤", "嬢": "娘", "孃": "娘", "譲": "让", "讓": "让",
    "醸": "酿", "釀": "酿", "触": "触", "觸": "触", "嘱": "嘱", "囑": "嘱",
    "寝": "寝", "寢": "寝", "尽": "尽", "盡": "尽", "儘": "尽", "図": "图",
    "圖": "图", "粋": "粹", "粹": "粹", "酔": "醉", "醉": "醉", "穂": "穗",
    "穗": "穗", "随": "随", "隨": "随", "髄": "髓", "髓": "髓", "数": "数",
    "數": "数", "枢": "枢", "樞": "枢", "瀬": "濑", "瀨": "濑", "声": "声",
    "聲": "声", "静": "静", "靜": "静", "斉": "齐", "齊": "齐", "摂": "摄",
    "攝": "摄", "窃": "窃", "竊": "窃", "専": "专", "專": "专", "浅": "浅",
    "淺": "浅", "戦": "战", "戰": "战", "銭": "钱", "錢": "钱", "禅": "禅",
    "禪": "禅", "曽": "曾", "曾": "曾", "双": "双", "雙": "双", "壮": "壮",
    "壯": "壮", "争": "争", "爭": "争", "荘": "庄", "莊": "庄", "捜": "搜",
    "搜": "搜", "挿": "插", "插": "插", "巣": "巢", "巢": "巢", "装": "装",
    "裝": "装", "総": "总", "總": "总", "騒": "骚", "騷": "骚", "増": "增",
    "增": "增", "蔵": "藏", "藏": "藏", "贈": "赠", "赠": "赠", "臓": "脏",
    "臟": "脏", "属": "属", "屬": "属", "続": "续", "續": "续", "堕": "堕",
    "墮": "堕", "対": "对", "對": "对", "体": "体", "體": "体", "滞": "滞",
    "滯": "滞", "台": "台", "臺": "台", "滝": "泷", "瀧": "泷", "択": "择",
    "擇": "择", "沢": "泽", "澤": "泽", "単": "单", "單": "单", "嘆": "叹",
    "歎": "叹", "団": "团", "團": "团", "断": "断", "斷": "断", "弾": "弹",
    "彈": "弹", "遅": "迟", "遲": "迟", "痴": "痴", "癡": "痴", "昼": "昼",
    "晝": "昼", "虫": "虫", "蟲": "虫", "鋳": "铸", "鑄": "铸", "庁": "厅",
    "廳": "厅", "聴": "听", "聽": "听", "逓": "递", "遞": "递", "鉄": "铁",
    "鐵": "铁", "転": "转", "轉": "转", "点": "点", "點": "点", "伝": "传",
    "傳": "传", "灯": "灯", "燈": "灯", "当": "当", "當": "当", "党": "党",
    "黨": "党", "盗": "盗", "盜": "盗", "闘": "斗", "鬥": "斗", "徳": "德",
    "德": "德", "独": "独", "獨": "独", "読": "读", "讀": "读", "届": "届",
    "屆": "届", "弐": "贰", "貳": "贰", "悩": "恼", "惱": "恼", "脳": "脑",
    "腦": "脑", "覇": "霸", "霸": "霸", "廃": "废", "廢": "废", "売": "卖",
    "賣": "卖", "麦": "麦", "麥": "麦", "発": "发", "發": "发", "髪": "发",
    "髮": "发", "抜": "拔", "拔": "拔", "蛮": "蛮", "蠻": "蛮", "秘": "秘",
    "祕": "秘", "浜": "滨", "濱": "滨", "瓶": "瓶", "甁": "瓶", "仏": "佛",
    "佛": "佛", "並": "并", "竝": "并", "変": "变", "變": "变", "辺": "边",
    "邊": "边", "舗": "铺", "舖": "铺", "歩": "步", "步": "步", "宝": "宝",
    "寶": "宝", "豊": "丰", "豐": "丰", "毎": "每", "每": "每", "万": "万",
    "萬": "万", "満": "满", "滿": "满", "黙": "默", "默": "默", "訳": "译",
    "譯": "译", "薬": "药", "藥": "药", "与": "与", "與": "与", "余": "余",
    "餘": "余", "誉": "誉", "譽": "誉", "様": "样", "樣": "样", "謡": "谣",
    "謠": "谣", "頼": "赖", "賴": "赖", "乱": "乱", "亂": "乱", "覧": "览",
    "覽": "览", "竜": "龙", "龍": "龙", "両": "两", "兩": "两", "猟": "猎",
    "獵": "猎", "緑": "绿", "綠": "绿", "涙": "泪", "淚": "泪", "塁": "垒",
    "壘": "垒", "励": "励", "勵": "励", "礼": "礼", "禮": "礼", "齢": "龄",
    "齡": "龄", "暦": "历", "歷": "历", "歴": "历", "恋": "恋", "戀": "恋",
    "炉": "炉", "爐": "炉", "労": "劳", "勞": "劳", "楼": "楼", "樓": "楼",
    "湾": "湾", "灣": "湾", "録": "录", "錄": "录", "姫": "姬", "姬": "姬",
    "髙": "高", "高": "高", "﨑": "崎", "崎": "崎", "島": "岛", "岛": "岛",
    "嶋": "岛", "嶌": "岛", "華": "华", "华": "华", "葉": "叶", "叶": "叶",
    "蓮": "莲", "莲": "莲", "綾": "绫", "绫": "绫", "紗": "纱", "纱": "纱",
    "純": "纯", "纯": "纯", "結": "结", "结": "结", "織": "织", "织": "织",
    "優": "优", "优": "优", "愛": "爱", "爱": "爱", "恵": "惠", "惠": "惠",
    "涼": "凉", "凉": "凉", "鈴": "铃", "铃": "铃", "宮": "宫", "宫": "宫",
    "岡": "冈", "冈": "冈", "倉": "仓", "仓": "仓", "絶": "绝", "绝": "绝",
    "頂": "顶", "顶": "顶", "態": "态", "态": "态", "濃": "浓", "浓": "浓",
    "選": "选", "选": "选",
}

# str.translate 表：去掉分隔符（映射到 None）+ 异体字归并，一次扫描搞定。
_TRANSLATE = {ord(ch): None for ch in _SEP_CHARS}
_TRANSLATE.update({ord(k): v for k, v in _VARIANTS.items()})


# 只判断「名称」字段：路径不参与，否则目录名里的一个词会把整棵子树的正常作品隐藏。
# 注意 SQL 表达式要自己带别名前缀，不能写成 ``f.COALESCE(...)``（别名后不能接函数）。
_ROW_KEYS = ("filename", "title_guess", "title_cn", "display_title", "title", "raw_text")


def normalize_name(text: object) -> str:
    """NFKC + 小写 + 去连接符 + 异体字归并，用于「名称包含」比较（两侧都要过一遍）。

    NFKC 顺手把全角字母数字（``ＡＢＰ``）折成半角，异体表把 ``亜`` / ``亞``
    折成 ``亚``——词表写简体也能命中日文/繁体片名。
    """
    folded = unicodedata.normalize("NFKC", str(text or ""))
    return folded.lower().translate(_TRANSLATE)


# ---- 「名称包含」的边界规则 ------------------------------------------------
# 去掉连接符能命中 `FC2-PPV-1288`，但也把两个单词粘成一个词：
# `BDrip X264` → `bdripx264` 里出现了 `ipx`（词表里 IP 社的番号词），
# `future` / `return` / `tsurezure` 里天然含 `ure`。这些正常番因此被误隐藏。
# 所以**短 ASCII 词**（番号基本都是 3~4 个字母）额外要求：命中位置的左右
# 原始字符都不是英文字母。数字与符号放行，否则 `PRED733` 这种「词+数字」
# 粘连的番号就挡不住了；左右是 `-` / `_` / `.` / 空格时也放行，
# 那也是番号的正常写法。中文词没有词边界，直接子串判断。
_SHORT_ASCII_MAX = 4


def _is_short_ascii(word: str) -> bool:
    return len(word) <= _SHORT_ASCII_MAX and word.isascii() and word.isalnum()


def _is_letter(ch: str) -> bool:
    return bool(ch) and ch.isascii() and ch.isalpha()


def _fold(text: str) -> tuple[str, list[int], str]:
    """逐字符折叠，同时记下每个折叠字符来自原文的哪个下标（用于回查边界）。"""
    source = unicodedata.normalize("NFKC", str(text or ""))
    chars: list[str] = []
    index: list[int] = []
    for pos, ch in enumerate(source):
        for out in ch.lower().translate(_TRANSLATE):
            chars.append(out)
            index.append(pos)
    return "".join(chars), index, source


def _short_word_hit(hay: str, index: list[int], source: str, word: str) -> bool:
    start = hay.find(word)
    while start != -1:
        head = index[start]
        tail = index[start + len(word) - 1] + 1
        left = source[head - 1] if head > 0 else ""
        right = source[tail] if tail < len(source) else ""
        if not _is_letter(left) and not _is_letter(right):
            return True
        start = hay.find(word, start + 1)
    return False


def _match_text(text: str) -> bool:
    """判断一段文本是否命中黑名单（:func:`hit` 与 :func:`filter_rows` 的唯一实现）。"""
    if not active():
        return False
    hay = normalize_name(text)
    if not hay.strip():
        return False
    kws = norm_keywords()
    if not any(word in hay for word in kws):
        return False
    matched = [word for word in kws if word in hay]
    # 长词 / 中文词直接算命中，不必回查边界。
    if any(not _is_short_ascii(word) for word in matched):
        return True
    folded, index, source = _fold(text)
    return any(_short_word_hit(folded, index, source, word) for word in matched)


def _sql_expressions(alias: str) -> tuple[str, ...]:
    return (
        f"lower({alias}.filename)",
        f"lower(COALESCE({alias}.title_guess, ''))",
        f"lower(COALESCE({alias}.title_cn, ''))",
    )


def raw() -> str:
    # 不传 default：让 db.get_setting 回退到 config.DEFAULTS 里的默认词表，
    # 否则首次运行（settings 表还没这个键）会拿到空串，等于黑名单失效。
    return str(db.get_setting("video_blacklist") or "")


# 词表 / 开关的进程内快照。命中判断会被「每个分组、每一行」反复调用，
# 每次都读一次 settings 等于每次开一条 SQLite 连接，剧集墙 190 个分组要 2 秒。
# 失效条件是 db.write_count()：任何写库（含保存设置）都会让它 +1。
_STATE: dict = {"version": -1, "keywords": [], "norm": [], "enabled": True}


def _state() -> dict:
    # 键里必须带库路径：测试会反复切到临时库，只靠写计数会把上一个库的词表带过来。
    version = (str(config.DB_PATH), db.write_count())
    if _STATE["version"] != version:
        words: list[str] = []
        seen: set[str] = set()
        for part in _SPLIT_RE.split(str(db.get_setting("video_blacklist") or "")):
            word = part.strip().lower()
            if not word:
                continue
            key = normalize_name(word)
            if not key or key in seen:
                continue
            seen.add(key)
            words.append(word)
        flag = str(db.get_setting("blacklist_enabled") or "").strip().lower()
        _STATE.update(
            version=version,
            keywords=words,
            norm=[normalize_name(w) for w in words],
            enabled=flag not in ("0", "false", "off", "no", ""),
        )
    return _STATE


def keywords() -> list[str]:
    """切分、trim、转小写并去重；保留用户书写顺序，便于在设置页对照。

    这里返回的是**原样小写词**（不动连接符），给 SQL 的 LIKE 用；
    归一化后的词由 :func:`norm_keywords` 提供。
    """
    return list(_state()["keywords"])


def norm_keywords() -> list[str]:
    """去掉连接符的关键词，供 :func:`hit` 与 Python 侧过滤使用。"""
    return list(_state()["norm"])


def enabled() -> bool:
    # 空库时回退 DEFAULTS 的 1（默认开启）。
    return bool(_state()["enabled"])


def active() -> bool:
    """开关打开**且**词表非空时才真正过滤。"""
    state = _state()
    return bool(state["enabled"]) and bool(state["keywords"])



def hit(*names: object) -> bool:
    """任一名称包含任一关键词即命中；开关关闭时永远不命中。

    两侧都过 :func:`normalize_name`，所以 `FC2-PPV-1288053` 能被词表里的
    `FC2PPV` 命中、`三上悠亜` 能被 `三上悠亚` 命中；短英文词另有词边界约束
    （见 :func:`_match_text`），避免 `BDrip X264` 被 `ipx` 误伤。
    """
    return _match_text("\n".join(str(n or "") for n in names))


def filter_rows(rows, keys: tuple[str, ...] = _ROW_KEYS) -> list[dict]:
    """给已经查出来的行做 Python 侧过滤（列表总条数不大时才用）。"""
    items = list(rows or [])
    if not active():
        return items
    out: list[dict] = []
    for r in items:
        if not _match_text("\n".join(str(r.get(k) or "") for k in keys if k in r)):
            out.append(r)
    return out


def _escape_like(text: str) -> str:
    return text.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")


def sql_clause(alias: str = "f") -> tuple[str, list]:
    """返回 ``(SQL 片段, 参数)``：名称命中任一关键词的行被排除。

    片段形如 ``NOT ((lower(f.filename) LIKE ? ESCAPE '\\' OR ...) OR (...))``，
    直接拼在 ``WHERE`` 后面即可；开关关闭或词表为空时返回 ``("", [])``。

    **这是粗筛**：连接符不同的写法（``FC2-PPV`` vs ``FC2PPV``）会漏，
    调用方拿到行之后还要过一遍 :func:`filter_rows` / :func:`hit`。
    不在这里做 replace 归一化，是因为 38 个词 × 3 个字段的 OR 展开会让
    SQLite 逐词重算 replace 链（实测 2100 行要多花 110ms，媒体库每次搜索都卡）。

    **短英文词改用 GLOB 做词边界**（``lower(x) GLOB '*ipx*'`` 再排除
    ``'*[a-z]ipx*'`` / ``'*ipx[a-z]*'``）：只靠 LIKE 的话，``future`` 会被
    ``ure`` 命中、``BDripX264`` 会被 ``ipx`` 命中，而 SQL 一旦把行删掉，
    Python 侧的边界复核根本没机会把它放回来。GLOB 要求原样连续匹配，
    空格 / 连字符天然是边界，正好符合番号写法（``SSIS-001`` / ``PRED733``）。
    """
    if not active():
        return "", []
    kws = keywords()
    fields = _sql_expressions(alias)
    parts: list[str] = []
    params: list[str] = []
    for kw in kws:
        if _is_short_ascii(kw):
            for expr in fields:
                parts.append(
                    f"({expr} GLOB ? AND {expr} NOT GLOB ? AND {expr} NOT GLOB ?)"
                )
                params.extend([f"*{kw}*", f"*[a-z]{kw}*", f"*{kw}[a-z]*"])
            continue
        like = f"%{_escape_like(kw)}%"
        ors = " OR ".join(f"{expr} LIKE ? ESCAPE '\\'" for expr in fields)
        parts.append(f"({ors})")
        params.extend([like] * len(fields))
    return "NOT (" + " OR ".join(parts) + ")", params


def status() -> dict:
    """供设置页 / 测试读取的只读快照。"""
    kws = keywords()
    return {"enabled": enabled(), "count": len(kws), "keywords": kws}


def invalidate() -> None:
    """丢弃词表快照，下一次判断重新读设置（测试与改设置后可用）。"""
    _STATE["version"] = None
