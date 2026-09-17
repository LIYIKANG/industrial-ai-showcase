"""上传文件来源分类。

本模块只判断输入是 native、scan、photo 或 unknown，不提取任何业务字段。
"""

import re
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Any, Dict

import fitz


_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".bmp", ".gif", ".webp"}
_MIN_DOCUMENT_CHARS = 100
_MIN_RELIABLE_PAGE_CHARS = 40
_MIN_RELIABLE_PAGE_RATIO = 0.5


@dataclass(frozen=True)
class FileSourceClassification:
    """文件来源分类结果；只描述输入质量，不参与业务字段提取。"""

    file_source: str
    confidence: float
    page_count: int
    total_text_characters: int
    reliable_text_pages: int
    reliable_page_ratio: float
    reason: str

    def to_dict(self) -> Dict[str, Any]:
        return asdict(self)


def _meaningful_text_length(text: str) -> int:
    """统计文字层中的可见字符，忽略空白和控制字符。"""
    return len(re.sub(r"\s+", "", text or ""))


def classify_file_source(file_bytes: bytes, filename: str) -> FileSourceClassification:
    """将文件分类为 native、scan、photo 或 unknown。

    第一版只观察文件格式和 PDF 文字层：图片视为 photo；具有可靠文字层的
    PDF 视为 native；其他可读取 PDF 视为 scan。该结果不会替代 AI 字段提取。
    """
    extension = Path(filename or "").suffix.lower()
    if extension in _IMAGE_EXTENSIONS:
        return FileSourceClassification(
            file_source="photo",
            confidence=1.0,
            page_count=1,
            total_text_characters=0,
            reliable_text_pages=0,
            reliable_page_ratio=0.0,
            reason="image_file",
        )
    if extension != ".pdf" or not file_bytes:
        return FileSourceClassification(
            file_source="unknown",
            confidence=0.0,
            page_count=0,
            total_text_characters=0,
            reliable_text_pages=0,
            reliable_page_ratio=0.0,
            reason="unsupported_or_empty_file",
        )

    try:
        document = fitz.open(stream=file_bytes, filetype="pdf")
        try:
            page_character_counts = [
                _meaningful_text_length(page.get_text("text")) for page in document
            ]
        finally:
            document.close()
    except Exception:
        return FileSourceClassification(
            file_source="unknown",
            confidence=0.0,
            page_count=0,
            total_text_characters=0,
            reliable_text_pages=0,
            reliable_page_ratio=0.0,
            reason="unreadable_pdf",
        )

    page_count = len(page_character_counts)
    total_characters = sum(page_character_counts)
    reliable_pages = sum(
        count >= _MIN_RELIABLE_PAGE_CHARS for count in page_character_counts
    )
    reliable_ratio = reliable_pages / page_count if page_count else 0.0
    is_native = (
        total_characters >= _MIN_DOCUMENT_CHARS
        and reliable_ratio >= _MIN_RELIABLE_PAGE_RATIO
    )
    if is_native:
        confidence = min(1.0, max(total_characters / 500, reliable_ratio))
        file_source = "native"
        reason = "reliable_pdf_text_layer"
    else:
        confidence = min(
            1.0,
            max(1 - min(total_characters / _MIN_DOCUMENT_CHARS, 1), 1 - reliable_ratio),
        )
        file_source = "scan"
        reason = "no_reliable_pdf_text_layer"

    return FileSourceClassification(
        file_source=file_source,
        confidence=round(confidence, 3),
        page_count=page_count,
        total_text_characters=total_characters,
        reliable_text_pages=reliable_pages,
        reliable_page_ratio=round(reliable_ratio, 3),
        reason=reason,
    )
