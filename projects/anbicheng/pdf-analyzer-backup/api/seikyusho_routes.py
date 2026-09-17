"""
api/seikyusho_routes.py
========================
請求書（AD請求書）処理用 API ルート。

ルート：
  POST /api/seikyusho/process              → PDF/画像をアップロード、ジョブ登録（202）
  GET  /api/seikyusho/jobs/{job_id}        → ジョブ状態ポーリング
  GET  /api/seikyusho/download/{file_id}  → 生成済み Excel をダウンロード
  POST /api/seikyusho/regenerate          → 手動修正後に Excel を再生成
"""

import asyncio
import gc
import json
import logging
import shutil
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

from fastapi import APIRouter, BackgroundTasks, Depends, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from auth.dependencies import get_current_user
from auth.models import ProcessingJob, User
from auth.service import add_audit
from core.ai_extractor import ALLOWED_IMAGE_EXTS, extract_from_image, extract_from_single_pdf
from core.config import load_config
from core.database import SessionLocal, get_db
from core.excel_writer import fill_excel_template
from core.job_queue import get_job_semaphore
from core.quota import check_page_quota_or_429

from api.routes import _check_file_size, _get_download_dir, _new_file_id, _get_pdf_page_count, _extract_one_file

seikyusho_router = APIRouter()
logger = logging.getLogger(__name__)

SEIKYUSHO_CONFIG_PATH = Path(__file__).resolve().parent.parent / "config" / "seikyusho_field_mapping.json"


async def _run_seikyusho_job_bg(
    job_id: str,
    file_data: List[Tuple[str, bytes]],
    fields_json: str,
    username: str,
    current_config: Dict[str, Any],
    out_filename: str,
) -> None:
    await get_job_semaphore().acquire()
    db = SessionLocal()
    job = None
    try:
        job = db.query(ProcessingJob).filter(ProcessingJob.file_id == job_id).first()
        job.status = "processing"
        logger.info("[%s] 請求書 処理開始 files=%d user=%s", job_id, len(file_data), username)
        db.commit()

        tasks = [
            _extract_one_file(contents, filename, fields_json)
            for filename, contents in file_data
        ]
        results = await asyncio.gather(*tasks)

        merged_extracted: Dict[str, Any] = {}
        merged_notes: Dict[str, str] = {}
        field_source: Dict[str, str] = {}
        total_pages = 0
        file_results = []

        for filename, file_pages, extracted, notes in results:
            total_pages += file_pages
            file_results.append(
                {"filename": filename, "pages": file_pages,
                 "fields_filled": sum(1 for v in extracted.values() if v)}
            )
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

        output_path = fill_excel_template(merged_extracted, current_config)
        filled_count = sum(1 for v in merged_extracted.values() if v)
        total_count = len(current_config.get("fields", []))

        extracted_details = [
            {
                "id": f["id"],
                "label": f.get("label", f["id"]),
                "cell": f.get("cell", ""),
                "value": merged_extracted.get(f["id"], ""),
                "filled": bool(merged_extracted.get(f["id"], "")),
                "source": field_source.get(f["id"], ""),
                "note": merged_notes.get(f["id"], ""),
            }
            for f in current_config.get("fields", [])
        ]

        download_path = _get_download_dir() / f"{job_id}.xlsx"
        shutil.move(str(output_path), str(download_path))

        job.status = "done"
        job.filename = out_filename
        job.fields_filled = filled_count
        job.fields_total = total_count
        logger.info("[%s] 請求書 完了 filled=%d/%d", job_id, filled_count, total_count)
        job.page_count = total_pages
        job.extracted_json = json.dumps(extracted_details, ensure_ascii=False)
        job.finished_at = datetime.utcnow()
        db.commit()

        add_audit(
            db, username, "seikyusho_processed", "",
            detail=f"file_id={job_id} files={len(file_data)} pages={total_pages} filled={filled_count}/{total_count}",
        )

    except Exception as e:
        logger.exception("[%s] 請求書 処理失敗", job_id)
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


@seikyusho_router.post("/api/seikyusho/process", status_code=202)
async def process_seikyusho(
    background_tasks: BackgroundTasks,
    pdf_files: List[UploadFile] = File(...),
    _current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """請求書 PDF/画像を受け取り、ジョブ ID を即座に返す。AI 抽出・Excel 生成はバックグラウンドで実行。"""
    for f in pdf_files:
        ext = Path(f.filename).suffix.lower()
        if ext != ".pdf" and ext not in ALLOWED_IMAGE_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式: {f.filename}（支持 PDF 和图片）",
            )

    try:
        current_config = load_config(SEIKYUSHO_CONFIG_PATH)

        field_defs = [
            {"id": f["id"], "label": f.get("label", f["id"])}
            for f in current_config.get("fields", [])
        ]
        fields_json = json.dumps(field_defs, ensure_ascii=False)

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

        out_filename = (
            f"seikyusho_{Path(file_data[0][0]).stem}.xlsx"
            if len(file_data) == 1
            else f"seikyusho_merged_{len(file_data)}files.xlsx"
        )

        job_id = _new_file_id()
        job = ProcessingJob(
            file_id=job_id,
            username=_current_user.username,
            filename=out_filename,
            fields_filled=0,
            fields_total=len(field_defs),
            page_count=0,
            extracted_json="[]",
            source_type="seikyusho",
            status="pending",
            company_id=_current_user.company_id,
        )
        db.add(job)
        db.commit()

        background_tasks.add_task(
            _run_seikyusho_job_bg,
            job_id,
            file_data,
            fields_json,
            _current_user.username,
            current_config,
            out_filename,
        )

        return JSONResponse(
            status_code=202,
            content={"job_id": job_id, "status": "pending"},
        )

    except HTTPException:
        raise
    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": "処理中にエラーが発生しました。管理者にお問い合わせください。"})


@seikyusho_router.get("/api/seikyusho/jobs/{job_id}")
async def get_seikyusho_job_status(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """請求書ジョブ状態を返す。done の場合は抽出結果も含む。"""
    job = db.query(ProcessingJob).filter(ProcessingJob.file_id == job_id).first()
    if not job:
        raise HTTPException(status_code=404, detail="ジョブが見つかりません。")
    if current_user.role != "super_admin" and current_user.username != job.username:
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
    elif job.status == "failed":
        resp["error"] = job.error_msg or "処理に失敗しました。"
    return JSONResponse(content=resp)


@seikyusho_router.get("/api/seikyusho/download/{file_id}")
async def download_seikyusho(
    file_id: str,
    filename: str = "seikyusho.xlsx",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """生成済み請求書 Excel をダウンロード。ファイルが消えていれば DB から再生成。"""
    job = db.query(ProcessingJob).filter(ProcessingJob.file_id == file_id).first()
    if job:
        if current_user.role != "super_admin" and current_user.username != job.username:
            raise HTTPException(status_code=403, detail="このファイルへのアクセス権がありません。")

    file_path = _get_download_dir() / f"{file_id}.xlsx"

    if not file_path.exists():
        if not job:
            raise HTTPException(status_code=404, detail="ファイルが見つかりません。再度処理を実行してください。")
        try:
            current_config = load_config(SEIKYUSHO_CONFIG_PATH)
            extracted = json.loads(job.extracted_json)
            data = {f["id"]: f.get("value", "") for f in extracted}
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


@seikyusho_router.post("/api/seikyusho/regenerate")
async def regenerate_seikyusho(
    payload: Dict[str, Any],
    _current_user: User = Depends(get_current_user),
):
    """手動修正後に請求書 Excel を再生成。"""
    try:
        current_config = load_config(SEIKYUSHO_CONFIG_PATH)
        fields_list = payload.get("fields", [])
        data = {f["id"]: f.get("value", "") for f in fields_list}

        output_path = fill_excel_template(data, current_config)

        filename = payload.get("filename", "seikyusho_manual.xlsx")
        file_id = _new_file_id()
        download_path = _get_download_dir() / f"{file_id}.xlsx"
        shutil.move(str(output_path), str(download_path))

        return JSONResponse(content={"file_id": file_id, "filename": filename})

    except Exception as e:
        traceback.print_exc()
        return JSONResponse(status_code=500, content={"error": "処理中にエラーが発生しました。管理者にお問い合わせください。"})
