"""
api/beta_routes.py
==================
Beta ページ用ルート定義（APIRouter として app.py に登録）。

エンドポイント:
  GET  /beta                           → Beta ページ HTML
  POST /api/beta/analyze-template      → テンプレート解析 → フィールド候補リスト返却
  POST /api/beta/process               → PDF/画像処理 + カスタムテンプレートへの出力
  GET  /api/beta/download/{file_id}    → 生成ファイルダウンロード
"""

import asyncio
import json
import os
import re
import secrets
import shutil
import tempfile
import traceback
from collections import OrderedDict
from io import BytesIO
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import fitz  # pymupdf
from fastapi import APIRouter, Depends, File, Form, HTTPException, Request, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from fastapi.templating import Jinja2Templates

from datetime import datetime

from auth.dependencies import get_current_user
from auth.models import ProcessingJob, User
from auth.service import add_audit
from core.ai_extractor import (
    ALLOWED_IMAGE_EXTS,
    analyze_pdf_for_fields,
    extract_by_user_prompt,
    extract_from_image,
    extract_from_single_pdf,
)
from core.config import BASE_DIR, settings
from core.database import get_db
from core.quota import check_page_quota_or_429
from core.template_analyzer import analyze_template
from sqlalchemy.orm import Session

from api.routes import _check_file_size

beta_router = APIRouter()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

# 対応するテンプレート拡張子
ALLOWED_TEMPLATE_EXTS = {".xlsx", ".xls", ".docx", ".doc"}

# file_id → username のオーナー管理（メモリ上・LRU上限付き）
# RAM 漸増を防ぐため OrderedDict で件数上限を設け、古いものから破棄する。
_JOB_CACHE_MAX = 2
_file_owners: "OrderedDict[str, str]" = OrderedDict()


def _remember_owner(file_id: str, username: str) -> None:
    """所有者をメモリに記録し、LRU 上限を超えた古いエントリを破棄する。"""
    _file_owners[file_id] = username
    _file_owners.move_to_end(file_id)
    while len(_file_owners) > _JOB_CACHE_MAX:
        _file_owners.popitem(last=False)


# ── 内部ユーティリティ ─────────────────────────────────────────────────────────


def _get_beta_tmp_dir() -> Path:
    """Beta 用ディレクトリ（明示ディスク。/tmp が tmpfs(RAM) の環境でも RAM を消費しない）。"""
    d = settings.OUTPUT_DIR / "beta_output"
    d.mkdir(parents=True, exist_ok=True)
    return d


def _new_file_id() -> str:
    """推測困難なファイル ID を生成（128bit エントロピー、IDOR 対策）。"""
    return secrets.token_urlsafe(16)


def _get_pdf_page_count(contents: bytes) -> int:
    try:
        doc = fitz.open(stream=contents, filetype="pdf")
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return 0


async def _extract_one_file(
    contents: bytes, filename: str, fields_str: str
) -> Tuple[str, int, Dict[str, Any], Dict[str, str]]:
    """1ファイル分の AI 抽出を実行し (filename, pages, extracted, notes) を返す。"""
    file_ext = Path(filename).suffix.lower()
    if file_ext in ALLOWED_IMAGE_EXTS:
        pages = 1
        extracted, notes = await extract_from_image(contents, filename, fields_str)
    else:
        pages = _get_pdf_page_count(contents)
        extracted, notes = await extract_from_single_pdf(contents, fields_str)
    return filename, pages, extracted, notes


async def _extract_one_file_prompt(
    contents: bytes, filename: str, user_prompt: str
) -> Tuple[str, int, Dict[str, Any], Dict[str, str]]:
    """ユーザープロンプトベースで AI 抽出を実行する。"""
    file_ext = Path(filename).suffix.lower()
    if file_ext in ALLOWED_IMAGE_EXTS:
        pages = 1
    else:
        pages = _get_pdf_page_count(contents)
    extracted, notes = await extract_by_user_prompt(contents, filename, user_prompt)
    return filename, pages, extracted, notes


def _fill_excel_custom(
    data: Dict[str, Any], template_contents: bytes, confirmed_fields: List[Dict]
) -> Path:
    """カスタム Excel テンプレートに {{field_id}} プレースホルダーで書き込む。

    プレースホルダーが存在しない場合は、確認済みフィールドのラベルに一致するセルを
    隣接セルで上書きする（簡易ラベルマッチング）。
    """
    from openpyxl import load_workbook
    from openpyxl.styles import Font

    wb = load_workbook(BytesIO(template_contents))
    placeholder_pattern = re.compile(r"\{\{(\w+)\}\}")

    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                if not cell.value or not isinstance(cell.value, str):
                    continue
                if "{{" in cell.value:
                    new_val = cell.value
                    for m in placeholder_pattern.finditer(cell.value):
                        field_id = m.group(1)
                        replacement = str(data.get(field_id, ""))
                        new_val = new_val.replace(m.group(0), replacement)
                    if new_val != cell.value:
                        cell.value = new_val
                        cell.font = Font(color="FF0000")  # AI 入力値は赤字

    tmp = tempfile.NamedTemporaryFile(delete=False, suffix=".xlsx")
    wb.save(tmp.name)
    tmp.close()
    return Path(tmp.name)


# ── ルート定義 ────────────────────────────────────────────────────────────────


@beta_router.get("/beta")
async def beta_index(request: Request):
    """Beta ページを返す。"""
    return templates.TemplateResponse(request=request, name="beta.html")


@beta_router.post("/api/beta/analyze-template")
async def analyze_output_template(
    template_file: UploadFile = File(...),
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """ユーザーがアップロードしたテンプレートを AI で解析し、
    抽出すべきフィールド候補リストを返す。

    Response:
      {
        "template_id":  "tmpl_<id>",
        "template_ext": ".xlsx" | ".docx",
        "fields": [ {id, label, description, required}, ... ]
      }
    """
    ext = Path(template_file.filename).suffix.lower()
    if ext not in ALLOWED_TEMPLATE_EXTS:
        raise HTTPException(
            status_code=400,
            detail=(
                f"サポートされていないテンプレート形式: {template_file.filename}"
                "（.xlsx / .docx に対応）"
            ),
        )

    max_bytes = settings.MAX_TEMPLATE_SIZE_MB * 1024 * 1024
    contents = await template_file.read()
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"テンプレートが大きすぎます（最大 {settings.MAX_TEMPLATE_SIZE_MB} MB）",
        )

    try:
        fields = await analyze_template(contents, template_file.filename)

        # テンプレートを一時ディレクトリに保存（後の /process で参照）
        file_id = _new_file_id()
        tmp_dir = _get_beta_tmp_dir()
        tmpl_path = tmp_dir / f"tmpl_{file_id}{ext}"
        tmpl_path.write_bytes(contents)

        add_audit(
            db,
            _current_user.username,
            "template_analyzed",
            "",
            detail=(
                f"filename={template_file.filename} size={len(contents)} "
                f"fields={len(fields)} ext={ext} template_id=tmpl_{file_id}"
            ),
        )

        return JSONResponse(
            content={
                "template_id": f"tmpl_{file_id}",
                "template_ext": ext,
                "fields": fields,
            }
        )
    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": "処理中にエラーが発生しました。管理者にお問い合わせください。"})


@beta_router.post("/api/beta/analyze-pdf")
async def analyze_pdf_content(
    pdf_file: UploadFile = File(...),
    _current_user: User = Depends(get_current_user),
):
    """アップロードされた PDF/画像を AI で解析し、抽出可能なフィールド候補を返す。

    テンプレート未指定時に使用。AI が文書の種類を判定し、
    ユーザーにとって有用な抽出フィールドを提案する。

    Response:
      {
        "fields": [ {id, label, description, required}, ... ]
      }
    """
    ext = Path(pdf_file.filename).suffix.lower()
    if ext != ".pdf" and ext not in ALLOWED_IMAGE_EXTS:
        raise HTTPException(
            status_code=400,
            detail=f"サポートされていないファイル形式: {pdf_file.filename}",
        )

    contents = await pdf_file.read()
    max_bytes = 50 * 1024 * 1024  # 50MB
    if len(contents) > max_bytes:
        raise HTTPException(status_code=413, detail="ファイルが大きすぎます（最大 50MB）")

    try:
        fields = await analyze_pdf_for_fields(contents, pdf_file.filename)
        return JSONResponse(content={"fields": fields})
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": "処理中にエラーが発生しました。管理者にお問い合わせください。"})


@beta_router.post("/api/beta/process")
async def beta_process(
    pdf_files: List[UploadFile] = File(...),
    fields_json: str = Form("[]"),             # 確認済みフィールド定義 JSON 配列
    user_prompt: Optional[str] = Form(None),   # ユーザー自由記述プロンプト
    template_id: Optional[str] = Form(None),   # テンプレートファイル ID
    template_ext: Optional[str] = Form(None),  # テンプレート拡張子
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """PDF / 画像を AI で解析し、確認済みフィールドを抽出後、テンプレートに書き込む。

    Form params:
      pdf_files    : ソースファイル（PDF / 画像、複数可）
      fields_json  : [{id, label, description, required}, ...] の JSON 文字列
      user_prompt  : ユーザー自由記述の抽出指示（キーワード/プロンプト）
      template_id  : /analyze-template で返却された template_id（任意）
      template_ext : テンプレート拡張子（任意）
    """
    # ── ファイル形式バリデーション ────────────────────────────────────────────
    for f in pdf_files:
        ext = Path(f.filename).suffix.lower()
        if ext != ".pdf" and ext not in ALLOWED_IMAGE_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"サポートされていないファイル形式: {f.filename}",
            )

    file_id = _new_file_id()

    try:
        confirmed_fields: List[Dict] = json.loads(fields_json) if fields_json else []

        # プロンプトモード: ユーザーの自由記述で抽出
        is_prompt_mode = bool(user_prompt and user_prompt.strip()) and not template_id

        if not is_prompt_mode:
            # フィールドモード（テンプレートあり or デフォルト）
            if not confirmed_fields:
                from core.config import load_config
                _default_cfg = load_config()
                confirmed_fields = [
                    {"id": f["id"], "label": f["label"]}
                    for f in _default_cfg.get("fields", [])
                ]

            fields_for_ai = [
                {"id": f["id"], "label": f.get("label", f["id"])}
                for f in confirmed_fields
            ]
            fields_str = json.dumps(fields_for_ai, ensure_ascii=False)

        # ── ファイル内容を逐次読み込み（UploadFile は並列読み込み不可）────────────
        file_data: List[Tuple[str, bytes]] = []
        for pdf_file in pdf_files:
            contents = await pdf_file.read()
            if contents:
                _check_file_size(pdf_file.filename, contents)
                file_data.append((pdf_file.filename, contents))

        # ── ページ数クォータチェック（全ファイル合計で1回） ─────────────
        total_pages_requested = 0
        for filename, contents in file_data:
            ext_l = Path(filename).suffix.lower()
            if ext_l in ALLOWED_IMAGE_EXTS:
                total_pages_requested += 1
            else:
                total_pages_requested += _get_pdf_page_count(contents)
        check_page_quota_or_429(db, _current_user, total_pages_requested)

        # ── AI 抽出を並列実行 ────────────────────────────────────────────────────
        if is_prompt_mode:
            tasks = [
                _extract_one_file_prompt(contents, filename, user_prompt.strip())
                for filename, contents in file_data
            ]
        else:
            tasks = [
                _extract_one_file(contents, filename, fields_str)
                for filename, contents in file_data
            ]
        results = await asyncio.gather(*tasks)

        # ── 元の順序でマージ ─────────────────────────────────────────────────────
        merged_extracted: Dict[str, Any] = {}
        merged_notes: Dict[str, str] = {}
        field_source: Dict[str, str] = {}
        total_pages = 0
        file_results = []

        for filename, file_pages, extracted, notes in results:
            total_pages += file_pages
            file_filled = sum(1 for v in extracted.values() if v)
            file_results.append(
                {
                    "filename": filename,
                    "pages": file_pages,
                    "fields_filled": file_filled,
                }
            )

            # 後ファイルの非空値で上書き（既存の0以外の値は "0" で上書きしない）
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

        # プロンプトモードの場合、AI の出力結果から confirmed_fields を構築
        if is_prompt_mode and not confirmed_fields:
            confirmed_fields = [
                {"id": key, "label": key}
                for key in merged_extracted.keys()
            ]

        # ── 出力ファイル生成 ──────────────────────────────────────────────────
        tmp_dir = _get_beta_tmp_dir()

        if template_id and template_ext:
            # カスタムテンプレートを使用
            tmpl_path = tmp_dir / f"{template_id}{template_ext}"
            if not tmpl_path.exists():
                raise HTTPException(
                    status_code=404,
                    detail=(
                        "テンプレートファイルが見つかりません。"
                        "再度テンプレートをアップロードしてください。"
                    ),
                )
            tmpl_contents = tmpl_path.read_bytes()
            ext_lower = template_ext.lower()

            if ext_lower in (".xlsx", ".xls"):
                output_path = _fill_excel_custom(merged_extracted, tmpl_contents, confirmed_fields)
                out_ext = ".xlsx"
                media_type = (
                    "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )
            elif ext_lower in (".docx", ".doc"):
                from core.word_writer import fill_word_template

                output_path = fill_word_template(
                    merged_extracted, tmpl_contents, f"template{template_ext}"
                )
                out_ext = ".docx"
                media_type = (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
            else:
                raise HTTPException(status_code=400, detail="不明なテンプレート形式です。")
        elif is_prompt_mode:
            # プロンプトモード → 簡易 Excel に結果を書き出す
            from openpyxl import Workbook
            from openpyxl.styles import Font

            wb = Workbook()
            ws = wb.active
            ws.title = "抽出結果"
            ws.append(["フィールド", "値", "備考"])
            # ヘッダー太字
            for cell in ws[1]:
                cell.font = Font(bold=True)
            for key in merged_extracted:
                ws.append([
                    key,
                    str(merged_extracted.get(key, "")),
                    str(merged_notes.get(key, "")),
                ])
            # 列幅調整
            ws.column_dimensions["A"].width = 30
            ws.column_dimensions["B"].width = 60
            ws.column_dimensions["C"].width = 30

            output_path = Path(tempfile.NamedTemporaryFile(
                delete=False, suffix=".xlsx"
            ).name)
            wb.save(str(output_path))
            out_ext = ".xlsx"
            media_type = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )
        else:
            # デフォルト Excel テンプレート（既存システムと同じ）
            from core.config import load_config
            from core.excel_writer import fill_excel_template

            current_config = load_config()
            output_path = fill_excel_template(merged_extracted, current_config)
            out_ext = ".xlsx"
            media_type = (
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            )

        # ダウンロードディレクトリへ移動
        download_path = tmp_dir / f"{file_id}{out_ext}"
        shutil.move(str(output_path), str(download_path))
        _remember_owner(file_id, _current_user.username)

        # ── レスポンス構築 ────────────────────────────────────────────────────
        extracted_details = [
            {
                "id": f["id"],
                "label": f.get("label", f["id"]),
                "value": merged_extracted.get(f["id"], ""),
                "filled": bool(merged_extracted.get(f["id"], "")),
                "source": field_source.get(f["id"], ""),
                "note": merged_notes.get(f["id"], ""),
            }
            for f in confirmed_fields
        ]

        filled_count = sum(1 for v in merged_extracted.values() if v)
        if len(file_data) == 1:
            out_filename = f"filled_{Path(file_data[0][0]).stem}{out_ext}"
        else:
            out_filename = f"filled_merged_{len(file_data)}files{out_ext}"

        # 抽出結果をDBに永続化（再ダウンロード・履歴用）
        db.add(ProcessingJob(
            file_id=file_id,
            username=_current_user.username,
            filename=out_filename,
            fields_filled=filled_count,
            fields_total=len(confirmed_fields),
            page_count=total_pages,
            extracted_json=json.dumps(extracted_details, ensure_ascii=False),
            source_type="beta",
            status="done",
            finished_at=datetime.utcnow(),
            company_id=_current_user.company_id,
        ))
        db.commit()

        add_audit(
            db,
            _current_user.username,
            "pdf_processed_beta",
            "",
            detail=(
                f"file_id={file_id} files={len(file_data)} pages={total_pages} "
                f"filled={filled_count}/{len(confirmed_fields)}"
            ),
        )

        return JSONResponse(
            content={
                "file_id": file_id,
                "filename": out_filename,
                "file_ext": out_ext,
                "fields_filled": filled_count,
                "fields_total": len(confirmed_fields),
                "page_count": total_pages,
                "extracted": extracted_details,
                "file_results": file_results,
            }
        )

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        try:
            db.rollback()
            db.add(ProcessingJob(
                file_id=file_id,
                username=_current_user.username,
                filename="failed",
                fields_filled=0,
                fields_total=0,
                page_count=0,
                extracted_json="[]",
                source_type="beta",
                status="failed",
                error_msg=str(e)[:1000],
                finished_at=datetime.utcnow(),
                company_id=_current_user.company_id,
            ))
            db.commit()
        except Exception:
            traceback.print_exc()
            db.rollback()
        return JSONResponse(status_code=500, content={"error": "処理中にエラーが発生しました。管理者にお問い合わせください。"})


@beta_router.get("/api/beta/download/{file_id}")
async def beta_download(
    file_id: str,
    filename: str = "filled.xlsx",
    ext: str = ".xlsx",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """生成されたファイルをダウンロード。ファイルが消えていればDBから再生成（標準テンプレートのみ）。"""
    # オーナーチェック（DB優先、メモリフォールバック）
    job = db.query(ProcessingJob).filter(ProcessingJob.file_id == file_id).first()
    if job:
        if current_user.role not in ("super_admin", "root_admin") and current_user.username != job.username:
            raise HTTPException(status_code=403, detail="このファイルへのアクセス権がありません。")
    else:
        owner = _file_owners.get(file_id)
        if owner is not None and current_user.role not in ("super_admin", "root_admin") and current_user.username != owner:
            raise HTTPException(status_code=403, detail="このファイルへのアクセス権がありません。")

    tmp_dir = _get_beta_tmp_dir()

    # 渡された拡張子 → .xlsx → .docx の順で探す
    for try_ext in [ext, ".xlsx", ".docx"]:
        file_path = tmp_dir / f"{file_id}{try_ext}"
        if file_path.exists():
            if try_ext in (".xlsx", ".xls"):
                media = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
            else:
                media = (
                    "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
                )
            return FileResponse(path=str(file_path), media_type=media, filename=filename)

    # ファイルが消えていればDBから再生成（カスタムテンプレートは不可、標準のみ）
    if job:
        try:
            from core.config import load_config
            from core.excel_writer import fill_excel_template
            extracted = json.loads(job.extracted_json)
            data = {f["id"]: f.get("value", "") for f in extracted}
            current_config = load_config()
            output_path = fill_excel_template(data, current_config)
            dest = tmp_dir / f"{file_id}.xlsx"
            shutil.move(str(output_path), str(dest))
            return FileResponse(
                path=str(dest),
                media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                filename=filename,
            )
        except Exception:
            traceback.print_exc()
            raise HTTPException(status_code=500, detail="ファイルの再生成に失敗しました。")

    raise HTTPException(status_code=404, detail="ファイルが見つかりません。再度処理を実行してください。")
