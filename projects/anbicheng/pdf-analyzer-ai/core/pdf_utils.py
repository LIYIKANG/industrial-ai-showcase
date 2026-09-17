"""
core/pdf_utils.py
=================
PDF 处理工具：
  - extract_text_from_pdf_bytes()  →  提取文字层（用于判断是否为扫描件）
  - pdf_bytes_to_base64_images()   →  将 PDF 每页转为 base64 JPEG（扫描件增强）
  - rotate_image()                 →  按指定角度（顺时针度数）旋转 PIL 图像
"""

import base64
from io import BytesIO
from typing import List

import fitz  # pymupdf
from PIL import Image, ImageEnhance, ImageFilter
from pypdf import PdfReader

# 扫描件渲染倍率（越高质量越好，但体积越大）
# Opus 4.7/4.8 の高解像度ビジョン（長辺 2576px まで）を活かすため 3.0 に。
# A4(842pt) × 3.0 ≈ 2526px で API 上限 2576px をほぼ使い切る。
_RENDER_SCALE = 3.0
# 图片最大边长（超过此值等比缩放）。Opus 4.7/4.8 の上限 2576px に合わせる。
_MAX_SIDE = 2576
# AI に送信するページ数の上限（重要事項説明書の主要フィールドは先頭ページに集中）
_MAX_PAGES = 12


def rotate_image(img: Image.Image, clockwise_deg: int) -> Image.Image:
    """PIL 画像を時計回りに clockwise_deg 度（0/90/180/270）回転して返す。

    AI が判定した「上向きにするための時計回り回転角」を表示側・OCR 側で
    そのまま適用するためのヘルパー。PIL.rotate は反時計回り正なので符号反転する。
    """
    deg = clockwise_deg % 360
    if deg == 0:
        return img
    return img.rotate(-deg, expand=True)


def extract_text_from_pdf_bytes(pdf_bytes: bytes) -> str:
    """用 pypdf 读文字层，返回空字符串表示是扫描件。"""
    texts: List[str] = []
    try:
        reader = PdfReader(BytesIO(pdf_bytes))
        for page in reader.pages:
            page_text = page.extract_text() or ""
            if page_text:
                texts.append(page_text)
    except Exception:
        pass
    return "\n".join(texts).strip()


def pdf_bytes_to_base64_images(pdf_bytes: bytes) -> List[str]:
    """将 PDF 每页渲染为 base64 JPEG，并做扫描增强处理。

    增强步骤：提升对比度 → 提升锐度 → 再次锐化 → 限制最大尺寸。
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    b64_images: List[str] = []

    for page in list(doc)[:_MAX_PAGES]:
        pix = page.get_pixmap(matrix=fitz.Matrix(_RENDER_SCALE, _RENDER_SCALE))
        img = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)

        # 扫描质量增强
        img = ImageEnhance.Contrast(img).enhance(1.4)
        img = ImageEnhance.Sharpness(img).enhance(1.3)

        # 限制最大尺寸
        if img.width > _MAX_SIDE or img.height > _MAX_SIDE:
            img.thumbnail((_MAX_SIDE, _MAX_SIDE), Image.LANCZOS)

        buf = BytesIO()
        img.save(buf, format="JPEG", quality=95)
        b64_images.append(base64.b64encode(buf.getvalue()).decode("utf-8"))

        # 各ページの中間オブジェクトを明示解放（同時大量処理時のピークメモリ削減）
        del pix, img, buf

    doc.close()
    return b64_images
