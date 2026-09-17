"""
api/routes.py
=============
FastAPI 路由定义（使用 APIRouter，由 app.py 挂载）。

路由：
  GET  /                       → 返回前端页面
  POST /api/process            → 上传 PDF/图片，AI 提取字段，生成 Excel
  GET  /api/download/{file_id} → 下载已生成的 Excel 文件
  POST /api/regenerate         → 手动修正后重新生成 Excel
"""

import asyncio
import calendar
import gc
import json
import logging
import secrets
import os
import re
import shutil
import tempfile
import traceback
from collections import OrderedDict
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import fitz  # pymupdf（用于统计 PDF 页数）
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from auth.dependencies import get_current_user
from auth.models import ProcessingJob, User
from auth.service import add_audit
from core.ai_extractor import (
    ALLOWED_IMAGE_EXTS,
    extract_focused_page,
    extract_from_image,
    extract_from_single_pdf,
)
from core.config import BASE_DIR, load_config, settings
from core.database import SessionLocal, get_db
from core.excel_writer import fill_excel_template
from core.file_source import classify_file_source
from core.job_queue import get_job_semaphore
from core.pdf_utils import pdf_bytes_to_base64_images
from core.quota import check_page_quota_or_429
from sqlalchemy import func
from sqlalchemy.orm import Session

# ── 初始化 ────────────────────────────────────────────────────────────────────

router = APIRouter()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))
logger = logging.getLogger(__name__)

# file_id → username のオーナー管理（メモリ上・LRU上限付き）
# RAM 漸増を防ぐため OrderedDict で件数上限を設け、古いものから破棄する。
# 所有者チェック・再DLは DB(ProcessingJob) 優先で、本辞書は補助/高速参照用。
_JOB_CACHE_MAX = 2
_file_owners: "OrderedDict[str, str]" = OrderedDict()

# file_id → 追加メタ情報（メモリ上・LRU上限付き）。契約書のみモードの検出ページ等を保持。
# 例: {"mode": "contract", "pages": {filename: page_index_0based}}
# 上限超過で破棄された古いジョブは原本ハイライト表示のみ不可（抽出値・再DLはDB/ディスクで維持）。
_job_meta: "OrderedDict[str, Dict[str, Any]]" = OrderedDict()


def _remember_owner(file_id: str, username: str) -> None:
    """所有者をメモリに記録し、LRU 上限を超えた古いエントリを破棄する。"""
    _file_owners[file_id] = username
    _file_owners.move_to_end(file_id)
    while len(_file_owners) > _JOB_CACHE_MAX:
        _file_owners.popitem(last=False)


def _remember_meta(file_id: str, meta: Dict[str, Any]) -> None:
    """ジョブメタをメモリに記録し、LRU 上限を超えた古いエントリを破棄する。"""
    _job_meta[file_id] = meta
    _job_meta.move_to_end(file_id)
    while len(_job_meta) > _JOB_CACHE_MAX:
        _job_meta.popitem(last=False)


# 「契約書のみアップロード」モードで抽出・表示する 16 フィールドの ID。
# （物件情報・貸主・管理委託先。金額系・契約期間は含めない）
_CONTRACT_FIELD_IDS = [
    "customer_name", "property_name", "room_number", "property_address",
    "floor_area", "layout", "building_type", "build_date", "structure",
    "floors_above", "floors_below", "landlord_address", "landlord_name",
    "management_name", "management_address", "management_phone",
]

# 「精算書のみアップロード」モードで抽出・表示する 14 の金額フィールドの ID。
# （賃貸決済明細書・契約金明細書・精算書の金額系。物件情報・契約期間は含めない）
_SETTLEMENT_FIELD_IDS = [
    "rent", "common_fee", "key_exchange_fee", "fixed_water_fee", "deposit",
    "key_money", "insurance_fee", "guarantee_fee", "daily_rent", "daily_common_fee",
    "advance_water_fee", "club_fee", "electronic_contract_fee", "parking_fee",
]

# 対象ページを1ページに絞るモードの設定（契約書のみ / 精算書のみ）。
# slot キー = アップロード窓口のキー（file_slots）と一致させる。
_FOCUSED_MODES: Dict[str, Dict[str, Any]] = {
    "contract": {
        "field_ids": _CONTRACT_FIELD_IDS,
        "page_name": "賃貸借契約書（賃貸契約書）",
        "page_features": (
            "賃貸借契約書のページには通常「賃貸借契約書」「賃貸契約書」等の表題があり、"
            "物件の表示（物件名称・所在地・床面積・間取り・構造・新築年月等）、"
            "貸主の住所・氏名、管理の委託先（称号又は名称・主たる事務所の所在地・電話番号）"
            "などの情報が記載されています。"
        ),
        "system_role": (
            "あなたは日本の不動産賃貸借契約書を正確に読み取る専門家です。"
            "まず賃貸借契約書のページを特定し、そのページのみから指定フィールドを抽出します。"
            "指示されたJSON形式のみを出力してください。"
        ),
    },
    "settlement": {
        "field_ids": _SETTLEMENT_FIELD_IDS,
        "page_name": "賃貸決済明細書・契約金明細書・精算書",
        "page_features": (
            "このページには通常「賃貸決済明細書」「契約金明細書」「精算書」等の表題があり、"
            "敷金・礼金・賃料・共益費などの金額や、『項目・請求金額・精算金額』の表が記載されています。"
            "金額を読み取る際は各行の項目名と金額を正確に対応させ、隣接行の値を取り違えないこと。"
        ),
        "system_role": (
            "あなたは日本の不動産の賃貸決済明細書・契約金明細書・精算書を正確に読み取る専門家です。"
            "まず精算書（決済明細書）のページを特定し、そのページのみから指定された金額フィールドを抽出します。"
            "表形式では各行の項目名と金額の対応を慎重に確認し、隣接行の値を取り違えないでください。"
            "指示されたJSON形式のみを出力してください。"
        ),
    },
}


# ── 内部工具函数 ──────────────────────────────────────────────────────────────


def _get_download_dir() -> Path:
    """生成 Excel の保存先（明示ディスク。/tmp が tmpfs(RAM) の環境でも RAM を消費しない）。"""
    d = settings.OUTPUT_DIR / "downloads"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _new_file_id() -> str:
    """推測困難なファイル ID を生成（128bit エントロピー、IDOR 対策）。"""
    return secrets.token_urlsafe(16)


def _get_pdf_page_count(contents: bytes) -> int:
    """安全获取 PDF 页数，失败返回 0。"""
    try:
        doc = fitz.open(stream=contents, filetype="pdf")
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return 0


def _check_file_size(filename: str, contents: bytes) -> None:
    """アップロードサイズ上限（MAX_FILE_SIZE_MB）を検証。超過時は 413。"""
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"ファイルサイズが上限({settings.MAX_FILE_SIZE_MB}MB)を超えています: {filename}",
        )


async def _extract_one_file(
    contents: bytes, filename: str, fields_json: str
) -> Tuple[str, int, Dict[str, Any], Dict[str, str]]:
    """1ファイル分の AI 抽出を実行し (filename, pages, extracted, notes) を返す。

    精算書ページ・請求書ページ（seisansho_routes / seikyusho_routes）が利用する。
    """
    file_ext = Path(filename).suffix.lower()
    if file_ext in ALLOWED_IMAGE_EXTS:
        pages = 1
        extracted, notes = await extract_from_image(contents, filename, fields_json)
    else:
        pages = _get_pdf_page_count(contents)
        extracted, notes = await extract_from_single_pdf(contents, fields_json)
    return filename, pages, extracted, notes


def _field_prompt_def(field: Dict[str, Any]) -> Dict[str, Any]:
    """AI 抽出プロンプトへ渡すフィールド定義を作る。

    display/cell は UI・Excel 用なので送らず、Claude が抽出判断に使う説明だけを渡す。
    """
    prompt_def: Dict[str, Any] = {
        "id": field["id"],
        "label": field.get("label", field["id"]),
    }
    for key in ("description", "source_hint", "not_allowed", "format", "examples"):
        if field.get(key):
            prompt_def[key] = field[key]
    return prompt_def


def _fields_json_for(current_config: Dict[str, Any], field_ids: List[str]) -> str:
    """指定 ID のフィールド定義だけを JSON 文字列にする（抽出プロンプト用）。"""
    field_defs = [
        _field_prompt_def(f)
        for f in current_config.get("fields", [])
        if f["id"] in field_ids
    ]
    return json.dumps(field_defs, ensure_ascii=False)


def _total_pages_of(file_data: List[Tuple[str, bytes]]) -> int:
    """全ファイルの合計ページ数（画像は1ページ扱い）。"""
    return sum(
        1 if Path(fn).suffix.lower() in ALLOWED_IMAGE_EXTS else _get_pdf_page_count(c)
        for fn, c in file_data
    )


# ── 路由处理器 ────────────────────────────────────────────────────────────────


@router.get("/")
async def index(request: Request):
    """返回前端 SPA 页面。"""
    return templates.TemplateResponse(request=request, name="index.html")


@router.get("/seisansho")
async def seisansho_page(request: Request):
    """精算書処理ページ。"""
    return templates.TemplateResponse(request=request, name="seisansho.html")


@router.get("/seikyusho")
async def seikyusho_page(request: Request):
    """請求書処理ページ。"""
    return templates.TemplateResponse(request=request, name="seikyusho.html")


async def _run_focused_job_bg(
    job_id: str,
    file_data: List[Tuple[str, bytes]],
    fields_json: str,
    username: str,
    current_config: Dict[str, Any],
    out_filename: str,
    mode_key: str,
) -> None:
    """対象ページを1ページに絞るモード（契約書のみ / 精算書のみ）の共通処理。

    指定モードの対象ページ（賃貸借契約書 or 精算書）を特定し、そのページから
    モード固有のフィールドのみを抽出 → Excel 生成 → 検出ページ・向きをメタ情報に保存。
    """
    mode_cfg = _FOCUSED_MODES[mode_key]
    mode_field_ids = mode_cfg["field_ids"]

    await get_job_semaphore().acquire()
    db = SessionLocal()
    job = None
    try:
        job = db.query(ProcessingJob).filter(ProcessingJob.file_id == job_id).first()
        job.status = "processing"
        logger.info("[%s] 処理開始 files=%d user=%s", job_id, len(file_data), username)
        db.commit()

        tasks = [
            extract_focused_page(
                contents, filename, fields_json,
                mode_cfg["page_name"], mode_cfg["page_features"], mode_cfg["system_role"],
            )
            for filename, contents in file_data
        ]
        results = await asyncio.gather(*tasks)  # [(page_idx, extracted, notes, boxes, rotation), ...]

        # フィールド表示名・セルの参照表（AI 座標ハイライトのラベル用）
        field_meta = {
            f["id"]: {
                "label": f.get("display") or f.get("label", f["id"]),
                "cell": f.get("cell", ""),
            }
            for f in current_config.get("fields", [])
        }

        merged_extracted: Dict[str, Any] = {}
        merged_notes: Dict[str, str] = {}
        page_map: Dict[str, int] = {}
        rotation_map: Dict[str, int] = {}
        highlights_by_file: Dict[str, List[Dict[str, Any]]] = {}

        for (filename, _contents), (page_idx, extracted, notes, boxes, page_rotation) in zip(file_data, results):
            page_map[filename] = page_idx
            rotation_map[filename] = page_rotation

            # AI が返した座標から、原本確認ビューア用のハイライト情報を構築
            file_hls: List[Dict[str, Any]] = []
            for fid, box in boxes.items():
                val = extracted.get(fid, "")
                if not val:
                    continue
                meta = field_meta.get(fid, {})
                file_hls.append({
                    "field_id": fid,
                    "label": meta.get("label", fid),
                    "value": val,
                    "cell": meta.get("cell", ""),
                    "bbox": box,
                })
            highlights_by_file[filename] = file_hls

            for key, value in extracted.items():
                if value:
                    merged_extracted[key] = value
                    if notes.get(key):
                        merged_notes[key] = notes[key]
                elif key not in merged_extracted:
                    merged_extracted[key] = ""

        # 重説テンプレートを生成（このモードの対象項目のみ記入、他セルは空欄）
        output_path = fill_excel_template(merged_extracted, current_config)

        # 表示・集計対象はこのモードの項目のみ（cell 定義のあるもの）
        focused_fields = [
            f for f in current_config.get("fields", [])
            if f["id"] in mode_field_ids and f.get("cell", "")
        ]
        filled_count = sum(1 for f in focused_fields if merged_extracted.get(f["id"], ""))
        total_count = len(focused_fields)

        extracted_details = [
            {
                "id": f["id"],
                "label": f.get("display") or f.get("label", f["id"]),
                "cell": f.get("cell", ""),
                "value": merged_extracted.get(f["id"], ""),
                "filled": bool(merged_extracted.get(f["id"], "")),
                "source": "",
                "note": merged_notes.get(f["id"], ""),
            }
            for f in focused_fields
        ]

        download_path = _get_download_dir() / f"{job_id}.xlsx"
        shutil.move(str(output_path), str(download_path))
        _remember_owner(job_id, username)
        _remember_meta(job_id, {
            "mode": mode_key,
            "pages": page_map,
            "rotations": rotation_map,
            "highlights": highlights_by_file,
        })

        job.status = "done"
        job.filename = out_filename
        job.fields_filled = filled_count
        job.fields_total = total_count
        logger.info("[%s] 完了 filled=%d/%d", job_id, filled_count, total_count)
        job.page_count = len(file_data)  # 表示は対象ページのみ（ファイルあたり1ページ）
        job.source_type = mode_key
        job.extracted_json = json.dumps(extracted_details, ensure_ascii=False)
        job.finished_at = datetime.utcnow()
        db.commit()

        add_audit(
            db, username, "pdf_processed", "",
            detail=f"file_id={job_id} mode={mode_key} files={len(file_data)} filled={filled_count}/{total_count}",
        )

    except Exception as e:
        logger.exception("[%s] 処理失敗", job_id)
        if job:
            job.status = "failed"
            job.error_msg = str(e)
            job.finished_at = datetime.utcnow()
            db.commit()
    finally:
        del file_data
        gc.collect()
        db.close()
        get_job_semaphore().release()


async def _run_job_bg(
    job_id: str,
    file_data: List[Tuple[str, bytes]],
    fields_json: str,
    username: str,
    current_config: Dict[str, Any],
    out_filename: str,
) -> None:
    """総合モード（単一聚焦窓口以外）：契約書聚焦・精算書聚焦の2パス抽出を全ファイルに
    対して実行し、結果を統合 → 動的ラベル解決 → Excel 生成 → ジョブ状態更新。

    各パスが対象ページ（賃貸借契約書 / 精算書）を特定し、針対性のあるプロンプトで
    抽出するため、1回の汎用抽出よりも認識精度が高い。
    （fields_json 引数は後方互換のため受け取るが、本モードではパスごとに専用の
    フィールド定義を内部生成するため未使用。）
    """
    await get_job_semaphore().acquire()
    db = SessionLocal()
    job = None
    try:
        job = db.query(ProcessingJob).filter(ProcessingJob.file_id == job_id).first()
        job.status = "processing"
        logger.info("[%s] 処理開始 files=%d user=%s", job_id, len(file_data), username)
        db.commit()

        # 契約書パスは物件・貸主・管理委託先＋契約期間、精算書パスは金額＋補助項目を担当。
        # 2パスの担当フィールドの和集合で全 34 項目をカバーする。
        contract_ids = _CONTRACT_FIELD_IDS + ["contract_start", "contract_end"]
        settlement_ids = _SETTLEMENT_FIELD_IDS + ["advance_fee_month", "club_fee_name"]
        contract_json = _fields_json_for(current_config, contract_ids)
        settlement_json = _fields_json_for(current_config, settlement_ids)
        c_cfg = _FOCUSED_MODES["contract"]
        s_cfg = _FOCUSED_MODES["settlement"]

        # 全ファイルに対して契約書聚焦・精算書聚焦の両パスを実行
        pass_tasks = []  # [(pass_type, filename, coroutine), ...]
        for filename, contents in file_data:
            # PDF は画像を1回だけ生成し、契約書・精算書の両パスで共有する。
            # （各パスが個別に画像化すると同一PDFの base64 画像をメモリに2重保持し
            #   512MB プランで OOM を誘発するため）。画像ファイルは extract_focused_page
            #   内で単一画像として処理するため None を渡す。
            ext = Path(filename).suffix.lower()
            shared_imgs = None if ext in ALLOWED_IMAGE_EXTS else pdf_bytes_to_base64_images(contents)
            pass_tasks.append(("contract", filename, extract_focused_page(
                contents, filename, contract_json,
                c_cfg["page_name"], c_cfg["page_features"], c_cfg["system_role"],
                b64_images=shared_imgs)))
            pass_tasks.append(("settlement", filename, extract_focused_page(
                contents, filename, settlement_json,
                s_cfg["page_name"], s_cfg["page_features"], s_cfg["system_role"],
                b64_images=shared_imgs)))
        gathered = await asyncio.gather(*[t[2] for t in pass_tasks])

        merged_extracted: Dict[str, Any] = {}
        merged_notes: Dict[str, str] = {}
        field_source: Dict[str, str] = {}

        # 契約書パスを先、精算書パスを後に統合（金額系は精算書パスの値を優先）
        merge_order = sorted(
            range(len(pass_tasks)),
            key=lambda i: 0 if pass_tasks[i][0] == "contract" else 1,
        )
        for i in merge_order:
            _pass_type, filename, _coro = pass_tasks[i]
            _page_idx, extracted, notes, _boxes, _rot = gathered[i]
            for key, value in extracted.items():
                existing = merged_extracted.get(key, "")
                if value and not (value == "0" and existing and existing != "0"):
                    merged_extracted[key] = value
                    field_source[key] = filename
                    if notes.get(key):
                        merged_notes[key] = notes[key]
                    else:
                        merged_notes.pop(key, None)
                elif key not in merged_extracted:
                    merged_extracted[key] = ""

        total_pages = _total_pages_of(file_data)

        # ── 動的ラベル解決（Excel 書き込みの前に計算）─────────────
        # contract_start から月・日を解析
        contract_start_val = merged_extracted.get("contract_start", "")
        _start_month = ""
        _start_day = ""
        _daily_suffix = ""

        m_date = re.search(r"(\d{1,2})\s*月\s*(\d{1,2})\s*日", contract_start_val)
        if m_date:
            _start_month = m_date.group(1)
            _start_day = m_date.group(2)
            sm, sd = int(_start_month), int(_start_day)
            if sd > 1:
                y_match = re.search(r"(\d{4})\s*年", contract_start_val)
                year = int(y_match.group(1)) if y_match else datetime.now().year
                days_in_month = calendar.monthrange(year, sm)[1]
                daily_days = days_in_month - sd + 1
                _daily_suffix = f"（{sm}/{sd}〜 {daily_days}日分）"

        # advance_fee_month（前家賃の対象月）を解決
        advance_month = merged_extracted.get("advance_fee_month", "").strip()
        if not advance_month and _start_month:
            advance_month = str(int(_start_month) % 12 + 1)

        # club_fee の名称を AI 抽出結果から取得
        club_fee_name = merged_extracted.get("club_fee_name", "").strip()
        club_fee_val = merged_extracted.get("club_fee", "").strip()

        # ── Excel ラベル上書き＆クリア ─────────────────────────────
        label_overrides: Dict[str, str] = {}
        clear_cells: List[str] = []

        # Q19="3月賃料" → "{advance_month}月賃料"
        if advance_month:
            label_overrides["Q19"] = f"{advance_month}月賃料"
            label_overrides["Q20"] = f"{advance_month}月共益費"

        # Q22="ベルヴィクラブ会費" / Q23="電子契約手数料" の動的配置
        e_contract_val = merged_extracted.get("electronic_contract_fee", "").strip()
        has_club_fee = bool(club_fee_val and club_fee_val != "0")

        if has_club_fee:
            # 会費あり → Q22 にそのまま表示
            if club_fee_name:
                label_overrides["Q22"] = club_fee_name
        else:
            # 会費なし → 電子契約手数料を Q22/S22 に繰り上げ
            if e_contract_val and e_contract_val != "0":
                label_overrides["Q22"] = "電子契約手数料"
                # S22 に電子契約手数料の値を書き込み、S23 はクリア
                merged_extracted["club_fee"] = e_contract_val
                merged_extracted["electronic_contract_fee"] = ""
                clear_cells.extend(["Q23", "S23"])
            else:
                # 両方なし → Q22, Q23 両方クリア
                clear_cells.extend(["Q22", "S22", "Q23", "S23"])

        output_path = fill_excel_template(
            merged_extracted, current_config,
            label_overrides=label_overrides,
            clear_cells=clear_cells,
        )

        # ── 値が 0 または空のオプション項目は UI から非表示 ─────────
        _OPTIONAL_HIDE_IDS = {"club_fee", "electronic_contract_fee",
                              "parking_fee", "advance_water_fee",
                              "fixed_water_fee"}

        def _should_hide(field: Dict[str, Any]) -> bool:
            """値が空 or 0 のオプション項目は非表示。"""
            if field["id"] not in _OPTIONAL_HIDE_IDS:
                return False
            v = merged_extracted.get(field["id"], "").strip()
            return not v or v == "0"

        def _resolve_display(field: Dict[str, Any]) -> str:
            display = field.get("display") or field.get("label", field["id"])
            if advance_month:
                display = display.replace("○月", f"{advance_month}月")
            if _daily_suffix and field["id"] in ("daily_rent", "daily_common_fee"):
                display = display.replace("（日割）", _daily_suffix)
            if field["id"] == "club_fee" and club_fee_name:
                display = club_fee_name
            return display

        extracted_details = [
            {
                "id": f["id"],
                "label": _resolve_display(f),
                "cell": f.get("cell", ""),
                "value": merged_extracted.get(f["id"], ""),
                "filled": bool(merged_extracted.get(f["id"], "")),
                "source": field_source.get(f["id"], ""),
                "note": merged_notes.get(f["id"], ""),
            }
            for f in current_config.get("fields", [])
            if f.get("cell", "") and not _should_hide(f)
        ]

        # 集計は実際に表示する項目に揃える（非表示の空オプション項目は分母から除外）
        total_count = len(extracted_details)
        filled_count = sum(1 for d in extracted_details if d["filled"])

        download_path = _get_download_dir() / f"{job_id}.xlsx"
        shutil.move(str(output_path), str(download_path))
        _remember_owner(job_id, username)

        job.status = "done"
        job.filename = out_filename
        job.fields_filled = filled_count
        job.fields_total = total_count
        logger.info("[%s] 完了 filled=%d/%d", job_id, filled_count, total_count)
        job.page_count = total_pages
        job.extracted_json = json.dumps(extracted_details, ensure_ascii=False)
        job.finished_at = datetime.utcnow()
        db.commit()

        add_audit(
            db, username, "pdf_processed", "",
            detail=f"file_id={job_id} files={len(file_data)} pages={total_pages} filled={filled_count}/{total_count}",
        )

    except Exception as e:
        logger.exception("[%s] 処理失敗", job_id)
        if job:
            job.status = "failed"
            job.error_msg = str(e)
            job.finished_at = datetime.utcnow()
            db.commit()
    finally:
        del file_data
        gc.collect()
        db.close()
        get_job_semaphore().release()


@router.post("/api/process", status_code=202)
async def process_pdf(
    background_tasks: BackgroundTasks,
    pdf_files: List[UploadFile] = File(...),
    file_slots: str = Form(None),
    request: Request = None,
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """PDF/画像を受け取り、ジョブIDを即座に返却。AI抽出・Excel生成はバックグラウンドで実行。

    file_slots: pdf_files と整列した槽位キーの JSON 配列（例: ["contract","contract"]）。
                全て "contract" の場合のみ「契約書のみ」モードで処理する。
    """
    for f in pdf_files:
        ext = Path(f.filename).suffix.lower()
        if ext != ".pdf" and ext not in ALLOWED_IMAGE_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式: {f.filename}（支持 PDF 和图片）",
            )

    try:
        current_config = load_config()

        try:
            slots = json.loads(file_slots) if file_slots else []
        except (json.JSONDecodeError, TypeError):
            slots = []

        file_data: List[Tuple[str, bytes]] = []
        file_slot_list: List[str] = []
        for idx, pdf_file in enumerate(pdf_files):
            contents = await pdf_file.read()
            if contents:
                _check_file_size(pdf_file.filename, contents)
                file_data.append((pdf_file.filename, contents))
                file_slot_list.append(slots[idx] if idx < len(slots) else "")

        # 絞り込みモード判定：全ファイルが同一槽位（契約書のみ / 精算書のみ）からのアップロード。
        # その槽位が _FOCUSED_MODES に定義されていれば対象ページ1枚に絞るモードで処理する。
        focused_mode = None
        if file_data:
            uniq_slots = set(file_slot_list)
            if len(uniq_slots) == 1:
                only_slot = next(iter(uniq_slots))
                if only_slot in _FOCUSED_MODES:
                    focused_mode = only_slot

        if focused_mode:
            mode_field_ids = _FOCUSED_MODES[focused_mode]["field_ids"]
            field_defs = [
                _field_prompt_def(f)
                for f in current_config.get("fields", [])
                if f["id"] in mode_field_ids
            ]
        else:
            field_defs = [
                _field_prompt_def(f)
                for f in current_config.get("fields", [])
            ]
        fields_json = json.dumps(field_defs, ensure_ascii=False)

        # ── ページ数クォータチェック（全ファイル合計で1回） ─────────────
        total_pages_requested = 0
        for filename, contents in file_data:
            ext = Path(filename).suffix.lower()
            if ext in ALLOWED_IMAGE_EXTS:
                total_pages_requested += 1
            else:
                total_pages_requested += _get_pdf_page_count(contents)
        check_page_quota_or_429(db, _current_user, total_pages_requested)

        out_filename = (
            f"filled_{Path(file_data[0][0]).stem}.xlsx"
            if len(file_data) == 1
            else f"filled_merged_{len(file_data)}files.xlsx"
        )

        job_id = _new_file_id()
        source_classifications = [
            {
                "filename": filename,
                **classify_file_source(contents, filename).to_dict(),
            }
            for filename, contents in file_data
        ]
        detected_sources = {
            item["file_source"] for item in source_classifications
        }
        file_source = (
            next(iter(detected_sources)) if len(detected_sources) == 1 else "unknown"
        )
        logger.info(
            "[%s] file_source=%s details=%s",
            job_id,
            file_source,
            source_classifications,
        )
        job = ProcessingJob(
            file_id=job_id,
            username=_current_user.username,
            filename=out_filename,
            fields_filled=0,
            fields_total=len(field_defs),
            page_count=0,
            extracted_json="[]",
            source_type=focused_mode if focused_mode else "standard",
            status="pending",
            company_id=_current_user.company_id,
        )
        db.add(job)
        db.commit()

        if focused_mode:
            background_tasks.add_task(
                _run_focused_job_bg,
                job_id,
                file_data,
                fields_json,
                _current_user.username,
                current_config,
                out_filename,
                focused_mode,
            )
        else:
            background_tasks.add_task(
                _run_job_bg,
                job_id,
                file_data,
                fields_json,
                _current_user.username,
                current_config,
                out_filename,
            )

        return JSONResponse(
            status_code=202,
            content={
                "job_id": job_id,
                "status": "pending",
                "file_source": file_source,
            },
        )

    except HTTPException:
        raise
    except Exception as e:
        logger.exception("PDF処理リクエストの受付に失敗")
        return JSONResponse(status_code=500, content={"error": "処理中にエラーが発生しました。管理者にお問い合わせください。"})


@router.get("/api/jobs/{job_id}")
async def get_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ジョブ状態を返す。done の場合は抽出結果も含む。"""
    job = db.query(ProcessingJob).filter(ProcessingJob.file_id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません。")
    if current_user.role not in ("super_admin", "root_admin") and current_user.username != job.username:
        raise HTTPException(status_code=403, detail="アクセス権がありません。")

    resp: Dict[str, Any] = {"job_id": job_id, "status": job.status}
    if job.status == "done":
        resp.update(
            {
                "file_id": job.file_id,
                "filename": job.filename,
                "fields_filled": job.fields_filled,
                "fields_total": job.fields_total,
                "page_count": job.page_count,
                "extracted": json.loads(job.extracted_json or "[]"),
                "file_results": [],
            }
        )
        # 契約書のみモード: 原本確認ビューアで該当ページだけ表示するための情報
        meta = _job_meta.get(job_id)
        if meta and meta.get("mode") in _FOCUSED_MODES:
            resp["mode"] = meta.get("mode")
            resp["focus_pages"] = meta.get("pages", {})
            resp["focus_rotations"] = meta.get("rotations", {})
            resp["focus_highlights"] = meta.get("highlights", {})
    elif job.status == "failed":
        resp["error"] = job.error_msg or "処理に失敗しました。"
    return JSONResponse(content=resp)


@router.get("/api/jobs/stats")
async def get_jobs_stats(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ジョブ状態の件数集計。管理系ロールは全体、それ以外は自分のジョブのみ。"""
    q = db.query(ProcessingJob.status, func.count(ProcessingJob.id))
    if current_user.role not in ("super_admin", "root_admin", "admin", "owner"):
        q = q.filter(ProcessingJob.username == current_user.username)
    counts = {"pending": 0, "processing": 0, "done": 0, "failed": 0}
    for st, n in q.group_by(ProcessingJob.status).all():
        if st in counts:
            counts[st] = n
    counts["total"] = sum(counts[k] for k in ("pending", "processing", "done", "failed"))
    logger.info(
        "ジョブ状態集計 user=%s scope=%s counts=%s",
        current_user.username,
        "all" if current_user.role in ("super_admin", "root_admin", "admin", "owner") else "self",
        counts,
    )
    return JSONResponse(content=counts)


@router.get("/api/download/{file_id}")
async def download_file(
    file_id: str,
    filename: str = "filled.xlsx",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """Excel ファイルをダウンロード。ファイルが消えていればDBから再生成。"""
    # オーナーチェック（DB優先、メモリフォールバック）
    job = db.query(ProcessingJob).filter(ProcessingJob.file_id == file_id).first()
    if job:
        if current_user.role not in ("super_admin", "root_admin") and current_user.username != job.username:
            raise HTTPException(status_code=403, detail="このファイルへのアクセス権がありません。")
    else:
        owner = _file_owners.get(file_id)
        if owner is not None and current_user.role not in ("super_admin", "root_admin") and current_user.username != owner:
            raise HTTPException(status_code=403, detail="このファイルへのアクセス権がありません。")

    file_path = _get_download_dir() / f"{file_id}.xlsx"

    # ファイルが消えていればDBから再生成
    if not file_path.exists():
        if not job:
            raise HTTPException(status_code=404, detail="ファイルが見つかりません。再度処理を実行してください。")
        try:
            extracted = json.loads(job.extracted_json)
            data = {f["id"]: f.get("value", "") for f in extracted}
            current_config = load_config()
            output_path = fill_excel_template(data, current_config)
            shutil.move(str(output_path), str(file_path))
        except Exception:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail="ファイルの再生成に失敗しました。")

    return FileResponse(
        path=str(file_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )


@router.get("/api/history")
async def get_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """処理履歴を返す（最新20件）。owner は全ユーザー分、それ以外は自分のみ。"""
    query = db.query(ProcessingJob)
    if current_user.role not in ("super_admin", "root_admin"):
        query = query.filter(ProcessingJob.username == current_user.username)
    elif current_user.role != "root_admin":
        # super_admin は root_admin の username を含むジョブを見られない
        from auth.models import User as _User
        root_usernames = [
            u.username for u in db.query(_User).filter(_User.role == "root_admin").all()
        ]
        if root_usernames:
            query = query.filter(ProcessingJob.username.notin_(root_usernames))
    jobs = (
        query
        .order_by(ProcessingJob.created_at.desc())
        .limit(20)
        .all()
    )
    return [
        {
            "file_id": j.file_id,
            "filename": j.filename,
            "fields_filled": j.fields_filled,
            "fields_total": j.fields_total,
            "page_count": j.page_count,
            "source_type": j.source_type,
            "created_at": j.created_at.isoformat() + "Z",
            "username": j.username if current_user.role in ("super_admin", "root_admin") else None,
        }
        for j in jobs
    ]


@router.post("/api/regenerate")
async def regenerate_excel(
    payload: Dict[str, Any],
    _current_user: User = Depends(get_current_user),
):
    """根据手动修正的字段值重新生成 Excel。

    Payload: {"fields": [{"id": "...", "value": "..."}], "filename": "..."}
    """
    try:
        current_config = load_config()
        fields_list = payload.get("fields", [])
        data = {f["id"]: f.get("value", "") for f in fields_list}

        output_path = fill_excel_template(data, current_config)

        filename = payload.get("filename", "filled_manual.xlsx")
        file_id = _new_file_id()
        download_path = _get_download_dir() / f"{file_id}.xlsx"
        shutil.move(str(output_path), str(download_path))
        _remember_owner(file_id, _current_user.username)

        return JSONResponse(content={"file_id": file_id, "filename": filename})

    except Exception as e:
        logger.exception("Excel再生成に失敗")
        return JSONResponse(status_code=500, content={"error": "処理中にエラーが発生しました。管理者にお問い合わせください。"})
