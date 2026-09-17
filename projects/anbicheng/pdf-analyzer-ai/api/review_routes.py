"""
api/review_routes.py
====================
PDF 確認ビューア用 API。

抽出完了後、元の PDF ページを画像として表示し、
抽出されたフィールド値の位置をハイライト表示するためのデータを返す。

- PDF テキスト層がある場合: page.search_for() で値の位置を特定
- スキャン済み PDF: Tesseract OCR でテキスト認識後に search_for()
- 画像ファイル: 画像のみ返す（ハイライトなし）
"""

import base64
import gc
import json
import logging
import re
from io import BytesIO
from typing import Any, Dict, List, Optional

import fitz  # pymupdf
from fastapi import APIRouter, Depends, File, Form, UploadFile
from PIL import Image, ImageEnhance, ImageFilter

from auth.dependencies import get_current_user
from auth.models import User
from core.job_queue import get_job_semaphore
from core.pdf_utils import rotate_image

logger = logging.getLogger(__name__)

review_router = APIRouter(tags=["review"])

# ── 定数 ────────────────────────────────────────────────────────────────────
_RENDER_SCALE = 1.5
_MAX_SIDE = 1800
_MAX_PAGES = 12

# テキスト検索の最小文字数（短すぎる値は誤検出が多い）
_MIN_SEARCH_LEN = 3
# 長い値で全文一致しない場合のフォールバック文字数
_FALLBACK_SEARCH_LEN = 10

# OCR の DPI 設定（高いほど精度が上がるが処理時間も増加）
_OCR_DPI = 150

# 画像ファイル拡張子
_IMAGE_EXTENSIONS = {".jpg", ".jpeg", ".png", ".gif", ".bmp", ".tiff", ".tif", ".webp"}

# Tesseract OCR が利用可能かどうかのフラグ（初回判定後キャッシュ）
_ocr_available: Optional[bool] = None


def _check_ocr_available() -> bool:
    """Tesseract OCR が利用可能かを判定（初回のみ実行）。"""
    global _ocr_available
    if _ocr_available is not None:
        return _ocr_available
    try:
        doc = fitz.open()
        page = doc.new_page(width=100, height=100)
        page.get_textpage_ocr(language="eng", dpi=72)
        doc.close()
        _ocr_available = True
        logger.info("Tesseract OCR is available — scanned PDF highlights enabled")
    except Exception:
        _ocr_available = False
        logger.info("Tesseract OCR not available — scanned PDF highlights disabled")
    return _ocr_available


def _render_page_image(page: fitz.Page) -> tuple:
    """PDF ページを画像にレンダリングし、座標変換スケールを計算する。

    Returns:
        (base64_str, img_width, img_height, total_scale)
    """
    pix = page.get_pixmap(matrix=fitz.Matrix(_RENDER_SCALE, _RENDER_SCALE))
    pix_width = pix.width
    pix_height = pix.height

    img = Image.frombytes("RGB", [pix_width, pix_height], pix.samples)

    # 画像強化
    img = ImageEnhance.Contrast(img).enhance(1.4)
    img = ImageEnhance.Sharpness(img).enhance(2.0)
    img = img.filter(ImageFilter.SHARPEN)

    # 最大サイズ制限
    if img.width > _MAX_SIDE or img.height > _MAX_SIDE:
        img.thumbnail((_MAX_SIDE, _MAX_SIDE), Image.LANCZOS)

    final_width = img.width
    final_height = img.height

    # 座標変換スケール計算
    thumbnail_scale = final_width / pix_width
    total_scale = _RENDER_SCALE * thumbnail_scale

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=75)
    b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")

    del pix, img, buf
    return b64_str, final_width, final_height, total_scale


def _pil_image_to_review(img: Image.Image, fields: List[Dict[str, Any]]) -> Dict[str, Any]:
    """方向補正済み PIL 画像を fitz ページ化し、表示用 base64 と OCR ハイライトを返す。

    スキャンページを上向きに揃えてから表示＆OCR するために使用する。
    AI 抽出側も同じ向きに補正しているため、AI 返却座標と表示が一致する。
    """
    buf = BytesIO()
    img.save(buf, format="PNG")
    doc = fitz.open(stream=buf.getvalue(), filetype="png")
    try:
        page = doc[0]
        b64_str, w, h, total_scale = _render_page_image(page)
        highlights: List[Dict[str, Any]] = []
        if fields and _check_ocr_available():
            tp = _get_ocr_textpage(page)
            if tp:
                highlights = _search_fields_on_page(page, fields, total_scale, textpage=tp)
        return {"image_b64": b64_str, "width": w, "height": h, "highlights": highlights}
    finally:
        doc.close()


def _number_variants(text: str) -> List[str]:
    """数値文字列の検索バリエーションを生成する。

    OCR は数値区切りをカンマ/ドット/なしのいずれかで認識するため、
    全パターンを試す。例: '100,000' → ['100000', '100.000']
    """
    variants = []
    # カンマ除去版
    no_sep = text.replace(",", "")
    if no_sep != text:
        variants.append(no_sep)
    # ドット区切り版（千の位区切りとして）
    if re.match(r"^\d{1,3}(,\d{3})+$", text):
        variants.append(text.replace(",", "."))
    # 元がカンマなし数字 → ドット区切り版を追加
    if re.match(r"^\d{4,}$", text):
        # 例: 100000 → 100.000
        formatted = ""
        digits = text
        while len(digits) > 3:
            formatted = "." + digits[-3:] + formatted
            digits = digits[:-3]
        formatted = digits + formatted
        if formatted != text:
            variants.append(formatted)
    return variants


def _search_field_on_page(
    page: fitz.Page,
    value: str,
    total_scale: float,
    textpage=None,
) -> List[Dict[str, float]]:
    """ページ上でフィールド値を検索し、画像座標系のハイライト矩形を返す。

    textpage が指定された場合（OCR 結果）はそちらを使用する。
    数値の場合、カンマなし形式でもフォールバック検索する。
    """
    if not value or len(value.strip()) < _MIN_SEARCH_LEN:
        return []

    search_text = value.strip()
    search_kwargs = {"textpage": textpage} if textpage else {}

    # 1. そのまま検索
    rects = page.search_for(search_text, **search_kwargs)

    # 2. 数値バリエーションで再検索（カンマなし、ドット区切り等）
    if not rects:
        for variant in _number_variants(search_text):
            if len(variant) >= _MIN_SEARCH_LEN:
                rects = page.search_for(variant, **search_kwargs)
                if rects:
                    break

    # 3. 全文一致しない場合、先頭 N 文字でフォールバック検索
    if not rects and len(search_text) > _FALLBACK_SEARCH_LEN:
        prefix = search_text[:_FALLBACK_SEARCH_LEN]
        rects = page.search_for(prefix, **search_kwargs)
        if not rects:
            for variant in _number_variants(prefix):
                rects = page.search_for(variant, **search_kwargs)
                if rects:
                    break

    highlights = []
    for rect in rects:
        highlights.append({
            "x": round(rect.x0 * total_scale, 1),
            "y": round(rect.y0 * total_scale, 1),
            "w": round((rect.x1 - rect.x0) * total_scale, 1),
            "h": round((rect.y1 - rect.y0) * total_scale, 1),
        })

    return highlights


def _get_ocr_textpage(page: fitz.Page):
    """スキャンページの OCR テキストページを取得する。失敗時は None。"""
    try:
        return page.get_textpage_ocr(language="jpn+eng", dpi=_OCR_DPI)
    except Exception as e:
        logger.warning("OCR failed for page: %s", e)
        return None


def _search_fields_on_page(
    page: fitz.Page,
    fields: List[Dict[str, Any]],
    total_scale: float,
    textpage=None,
) -> List[Dict[str, Any]]:
    """ページ上で全フィールドを検索し、重複排除済みハイライトリストを返す。"""
    highlights = []
    seen_rects = set()

    for field in fields:
        value = field.get("value", "")
        if not value:
            continue

        field_rects = _search_field_on_page(page, value, total_scale, textpage=textpage)
        for rect_data in field_rects:
            rect_key = (rect_data["x"], rect_data["y"],
                        rect_data["w"], rect_data["h"])
            if rect_key in seen_rects:
                continue
            seen_rects.add(rect_key)

            highlights.append({
                "field_id": field.get("id", ""),
                "label": field.get("label", ""),
                "cell": field.get("cell", ""),
                "value": value,
                **rect_data,
            })

    return highlights


def _pdf_to_review_data(
    pdf_bytes: bytes,
    fields: List[Dict[str, Any]],
    rotate: int = 0,
) -> List[Dict[str, Any]]:
    """PDF を解析してページ画像＋ハイライト座標を生成する。

    テキスト層がない場合は OCR にフォールバックする。
    rotate（時計回り度数）が指定された場合、各ページ画像を回転して上向きに揃える
    （契約書のみモードで AI が判定した向きを反映するため）。
    """
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pages_data = []

    for page_idx, page in enumerate(list(doc)[:_MAX_PAGES]):
        # テキスト層判定
        page_text = page.get_text("text").strip()
        has_text_layer = bool(page_text)

        highlights = []
        if has_text_layer and not rotate:
            # ボーンデジタル PDF かつ回転不要 → 従来通りレンダリング＋テキスト検索
            b64_str, img_w, img_h, total_scale = _render_page_image(page)
            highlights = _search_fields_on_page(page, fields, total_scale)
        else:
            # スキャンページ or 回転指定あり → 画像化＋回転（上向き）してから表示＆OCR。
            # AI 返却の bbox も上向き基準なので、表示と座標が一致する。
            pix = page.get_pixmap(matrix=fitz.Matrix(_RENDER_SCALE, _RENDER_SCALE))
            pil = Image.frombytes("RGB", [pix.width, pix.height], pix.samples)
            pil = rotate_image(pil, rotate)
            res = _pil_image_to_review(pil, fields)
            b64_str = res["image_b64"]
            img_w, img_h = res["width"], res["height"]
            highlights = res["highlights"]
            # OCR でハイライトが見つかった場合はバッジを非表示にする
            if highlights:
                has_text_layer = True
            del pix, pil

        pages_data.append({
            "page_num": page_idx,
            "image_b64": b64_str,
            "width": img_w,
            "height": img_h,
            "has_text_layer": has_text_layer,
            "highlights": highlights,
        })

    doc.close()
    return pages_data


def _image_to_review_data(image_bytes: bytes, rotate: int = 0) -> List[Dict[str, Any]]:
    """画像ファイルを単一ページのレビューデータとして返す。"""
    img = Image.open(BytesIO(image_bytes))
    if img.mode != "RGB":
        img = img.convert("RGB")

    # AI が判定した向きに回転して上向きへ（AI 返却 bbox と揃える）
    img = rotate_image(img, rotate)

    # 最大サイズ制限
    if img.width > _MAX_SIDE or img.height > _MAX_SIDE:
        img.thumbnail((_MAX_SIDE, _MAX_SIDE), Image.LANCZOS)

    buf = BytesIO()
    img.save(buf, format="JPEG", quality=75)
    b64_str = base64.b64encode(buf.getvalue()).decode("utf-8")

    return [{
        "page_num": 0,
        "image_b64": b64_str,
        "width": img.width,
        "height": img.height,
        "has_text_layer": False,
        "highlights": [],
    }]


@review_router.post("/api/review")
async def review_pdf(
    file: UploadFile = File(...),
    fields_json: str = Form("[]"),
    rotate: int = Form(0),
    _current_user: User = Depends(get_current_user),
):
    """PDF/画像ファイルのレビューデータを生成する。

    ページ画像（base64）と、抽出値の位置ハイライト座標を返す。
    rotate: 表示画像を時計回りに回転する角度（契約書のみモードで AI が判定した向き）。
    """
    contents = await file.read()
    filename = file.filename or ""
    ext = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    try:
        rotate = int(rotate) % 360
    except (ValueError, TypeError):
        rotate = 0

    # フィールド情報をパース
    try:
        fields = json.loads(fields_json)
    except (json.JSONDecodeError, TypeError):
        fields = []

    # ファイル種別に応じて処理（抽出ジョブと同じ同時実行枠で制限しメモリ加算を防ぐ）
    sem = get_job_semaphore()
    await sem.acquire()
    try:
        if ext in _IMAGE_EXTENSIONS:
            pages = _image_to_review_data(contents, rotate=rotate)
        else:
            pages = _pdf_to_review_data(contents, fields, rotate=rotate)
        return {"pages": pages}
    finally:
        sem.release()
        gc.collect()
