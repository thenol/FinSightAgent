"""HTML → 可读正文抽取（采集侧，无第三方依赖）。

目标：丢掉 script/style/导航噪音，优先 article/main，并按中文/句子密度挑选正文块。
"""

from __future__ import annotations

import re
from html.parser import HTMLParser

# 跳过整棵子树（不含 header：许多站点把标题放在 article>header 内）
_SKIP_TAGS = frozenset(
    {
        "script",
        "style",
        "noscript",
        "svg",
        "template",
        "iframe",
        "canvas",
        "head",
        "meta",
        "link",
        "button",
        "input",
        "select",
        "textarea",
        "form",
        "nav",
        "footer",
        "aside",
    }
)

# HTML void 元素无 end tag；计入 skip_depth 会导致后续正文被永久跳过
_VOID_TAGS = frozenset(
    {
        "area",
        "base",
        "br",
        "col",
        "embed",
        "hr",
        "img",
        "input",
        "link",
        "meta",
        "param",
        "source",
        "track",
        "wbr",
    }
)

# 倾向正文的容器
_ARTICLE_TAGS = frozenset({"article", "main"})
_BLOCK_TAGS = frozenset(
    {"p", "div", "section", "li", "blockquote", "h1", "h2", "h3", "h4", "h5", "h6", "br", "tr"}
)

_JS_NOISE = re.compile(
    r"(?i)("
    r"window\.|document\.|dataLayer|gtag\s*\(|function\s*\(|=>|"
    r"import\.meta|__vite|console\.|typeof\s+|localStorage|"
    r"sessionStorage|webpack|chunk\.js|\.css\b|\{json\}|"
    r"cookie\b|navigator\.|MutationObserver"
    r")"
)

_NAV_NOISE = re.compile(
    r"(?i)^("
    r"首页|资讯|股票|债券|商品|外汇|公司|快讯|会员|登录|注册|下载APP|"
    r"home|menu|sign\s*in|log\s*in|subscribe|cookie\s*policy"
    r")$"
)

_CHROME_MARKERS = (
    "{json}",
    "VIP会员",
    "版权声明",
    "用户协议",
    "隐私政策",
    "广告投放",
    "意见反馈",
    "下载APP",
    "法律信息",
    "硬AI",
    "大师课",
    "付费内容订阅协议",
    "关于我们",
    "版权和商务合作",
    "联系方式",
)


def html_to_article_text(html: str) -> str:
    """从 HTML 抽取可读正文，段落以换行分隔。"""
    if not html or not html.strip():
        return ""
    parser = _ArticleExtractor()
    try:
        parser.feed(html)
        parser.close()
    except Exception:
        # 残缺 HTML 时退回已收集片段
        pass
    candidates = parser.article_blocks or parser.body_blocks
    if not candidates:
        return scrub_extracted_text("\n".join(parser.loose_parts))
    scored = sorted(
        (( _score_block(block), block) for block in candidates),
        key=lambda item: item[0],
        reverse=True,
    )
    # 取高分块；若最高分明显更好则只保留头部高分簇。
    # 对于 RSS 摘要等短片段，候选总长很小，直接保留全部候选，避免 1.0 硬阈值把短段落丢弃。
    total_candidate_len = sum(len(block) for block in candidates)
    if total_candidate_len < 200:
        selected = list(candidates)
    else:
        top_score = scored[0][0]
        selected = [block for score, block in scored if score >= max(0.1, top_score * 0.35)]
    # 保持文档顺序
    order = {block: index for index, block in enumerate(candidates)}
    selected.sort(key=lambda block: order.get(block, 0))
    return scrub_extracted_text("\n".join(selected))


def scrub_extracted_text(text: str) -> str:
    """清洗已抽取文本中的脚本/导航行（也可用于历史脏数据展示）。"""
    if not text:
        return ""
    lines: list[str] = []
    for raw in text.splitlines():
        line = " ".join(raw.split()).strip()
        if not line:
            continue
        if _is_noise_line(line):
            continue
        # 单独导航/页脚碎片
        if len(line) <= 8 and (
            _NAV_NOISE.match(line) or any(marker in line for marker in _CHROME_MARKERS)
        ):
            continue
        lines.append(line)
    return "\n".join(lines).strip()


def text_quality_score(text: str) -> float:
    """用于在摘要/正文候选之间择优。"""
    cleaned = scrub_extracted_text(text)
    if not cleaned:
        return 0.0
    length = len(cleaned)
    cjk = sum(1 for ch in cleaned if "\u4e00" <= ch <= "\u9fff")
    letters = sum(1 for ch in cleaned if ch.isalpha() or "\u4e00" <= ch <= "\u9fff")
    noise = sum(1 for line in cleaned.splitlines() if _is_noise_line(line))
    density = cjk / max(length, 1)
    alpha = letters / max(length, 1)
    return length * (0.55 * density + 0.35 * alpha + 0.1) - 40.0 * noise


def choose_better_text(*candidates: str) -> str:
    best = ""
    best_score = -1.0
    for candidate in candidates:
        score = text_quality_score(candidate or "")
        if score > best_score:
            best_score = score
            best = scrub_extracted_text(candidate or "")
    return best


def _score_block(block: str) -> float:
    length = len(block)
    if length < 12:
        return 0.0
    if _is_noise_line(block):
        return 0.0
    cjk = sum(1 for ch in block if "\u4e00" <= ch <= "\u9fff")
    punct = sum(1 for ch in block if ch in "。！？；，、.:;!?")
    return cjk * 2.0 + punct * 1.5 + min(length, 400) * 0.05


def _is_noise_line(line: str) -> bool:
    if len(line) > 20 and _JS_NOISE.search(line):
        return True
    if _NAV_NOISE.match(line):
        return True
    if line in _CHROME_MARKERS or line.strip("{}") == "json":
        return True
    if line in {"华尔街见闻", "首页", "资讯"}:
        return True
    chrome_hits = sum(1 for marker in _CHROME_MARKERS if marker in line)
    if chrome_hits >= 2:
        return True
    # 短页脚/导航碎片（无句号、无数字）
    if len(line) <= 12 and "。" not in line and not any(ch.isdigit() for ch in line):
        if any(marker == line or marker in line for marker in _CHROME_MARKERS):
            return True
    # 高符号/低文字密度的脚本残留
    if len(line) >= 40:
        useful = sum(1 for ch in line if ch.isalnum() or "\u4e00" <= ch <= "\u9fff")
        if useful / len(line) < 0.35 and ("{" in line or ";" in line or "()" in line):
            return True
    return False


class _ArticleExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.skip_depth = 0
        self.article_depth = 0
        self.body_blocks: list[str] = []
        self.article_blocks: list[str] = []
        self.loose_parts: list[str] = []
        self._buf: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        lower = tag.lower()
        attr = {key.lower(): (value or "") for key, value in attrs}
        if self.skip_depth:
            if lower not in _VOID_TAGS:
                self.skip_depth += 1
            return
        # 正文区内的 header 常承载标题，不整段跳过
        if lower == "header" and self.article_depth == 0:
            self.skip_depth = 1
            return
        if lower in _SKIP_TAGS or _is_chrome_node(lower, attr):
            if lower in _VOID_TAGS:
                return
            self.skip_depth = 1
            return
        if lower in _ARTICLE_TAGS or attr.get("role") == "main":
            self.article_depth += 1
        if lower == "br":
            self._flush()

    def handle_endtag(self, tag: str) -> None:
        lower = tag.lower()
        if lower in _VOID_TAGS:
            return
        if self.skip_depth:
            self.skip_depth -= 1
            return
        if lower in _BLOCK_TAGS or lower in _ARTICLE_TAGS:
            self._flush()
        if lower in _ARTICLE_TAGS and self.article_depth:
            self.article_depth -= 1

    def handle_data(self, data: str) -> None:
        if self.skip_depth:
            return
        value = " ".join(data.split())
        if value:
            self._buf.append(value)

    def _flush(self) -> None:
        if not self._buf:
            return
        text = " ".join(self._buf).strip()
        self._buf.clear()
        if not text:
            return
        self.loose_parts.append(text)
        if self.article_depth > 0:
            self.article_blocks.append(text)
        else:
            self.body_blocks.append(text)


def _is_chrome_node(tag: str, attr: dict[str, str]) -> bool:
    class_id = f"{attr.get('class', '')} {attr.get('id', '')}".lower()
    markers = (
        "nav",
        "menu",
        "footer",
        "header",
        "sidebar",
        "breadcrumb",
        "cookie",
        "subscribe",
        "share",
        "comment",
        "recommend",
        "advert",
        "adsbox",
        "toolbar",
    )
    return any(marker in class_id for marker in markers)
