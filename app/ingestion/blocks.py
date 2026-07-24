"""文档块解析器。

把一篇文档的规范化正文解析为带稳定位置的文本块，供证据中心生成可定位、
可逐字回溯的 EvidenceSpan。定位统一使用 0-based 字符偏移（参见 DD-40 §4）。

MVP 阶段只处理已归一化的纯文本/简单 HTML 正文：按段落（换行）切分。后续接入
真实 HTML/PDF 解析器时，只需新增解析分支并升级 ``PARSER_VERSION``，已落库的
历史 Evidence 仍可通过其绑定的 ``revision_id`` 与 ``extraction_version`` 回放。
"""

from dataclasses import dataclass
from typing import Optional

PARSER_VERSION = "html-blocks-v1"


@dataclass(frozen=True)
class DocumentBlock:
    """文档中一个可定位的文本块。"""

    block_id: str
    locator_type: str
    char_start: int
    char_end: int
    text: str
    dom_path: Optional[str] = None
    page: Optional[int] = None
    bbox: Optional[tuple[float, float, float, float]] = None


class DocumentBlockReader:
    """按段落把归一化正文解析为 ``DocumentBlock`` 列表。

    偏移保证 ``content[char_start:char_end] == text``，使引用能够逐字回到原文。
    空白行不产生块；非空块按出现顺序从 1 编号，``block_id`` 形如 ``body-p-001``。
    """

    def parse(self, content: str) -> list[DocumentBlock]:
        blocks: list[DocumentBlock] = []
        index = 0
        offset = 0
        for raw_line in content.splitlines(keepends=True):
            leading = len(raw_line) - len(raw_line.lstrip())
            trailing = len(raw_line) - len(raw_line.rstrip())
            text = raw_line.strip()
            if text:
                index += 1
                char_start = offset + leading
                char_end = offset + len(raw_line) - trailing
                # 双重保险：保证偏移精确指向正文片段。
                if content[char_start:char_end] != text:
                    # 归一化后理论上不会发生；若发生则回退到 find，避免偏移错位。
                    found = content.find(text, offset)
                    if found < 0:
                        continue
                    char_start = found
                    char_end = found + len(text)
                blocks.append(
                    DocumentBlock(
                        block_id=f"body-p-{index:03d}",
                        locator_type="html",
                        char_start=char_start,
                        char_end=char_end,
                        text=text,
                        dom_path=f"body/p[{index}]",
                    )
                )
            offset += len(raw_line)
        return blocks
