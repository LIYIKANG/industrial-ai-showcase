"""
api/customer_routes.py
======================
客户指定关键词识别流程。
"""

from __future__ import annotations

import asyncio
import json
import logging
import secrets
import traceback
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Tuple

import fitz
from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, JSONResponse
from sqlalchemy.orm import Session

from auth.dependencies import get_current_user
from auth.models import ProcessingJob, User
from auth.service import add_audit
from core.ai_extractor import (
    ALLOWED_IMAGE_EXTS,
    extract_from_image,
    extract_from_single_pdf,
    extract_line_items_from_image_vlm,
    extract_line_items_from_pdf_vlm,
)
from core.config import (
    list_customer_keyword_configs,
    load_customer_keyword_config,
    settings,
)
from core.customer_business_logic import (
    build_customer_result_fields,
    commit_customer_line_item_product,
    get_business_key_field,
    process_customer_product,
)
from core.customer_exporter import write_customer_result_workbook
from core.customer_word_writer import write_customer_result_word
from core.customer_keyword_parser import (
    build_ai_field_definitions,
    merge_parsed_results,
    parse_customer_text,
    parse_extracted_values,
)
from core.customer_keyword_validator import validate_customer_fields
from core.customer_line_item_parser import normalize_customer_line_items
from core.database import SessionLocal, get_db
from core.quota import check_page_quota_or_429
from repository.customer_repository import CustomerRepository


customer_router = APIRouter()
logger = logging.getLogger(__name__)
_customer_processing_semaphore: asyncio.Semaphore | None = None
_customer_processing_loop: asyncio.AbstractEventLoop | None = None


def _new_file_id() -> str:
    return secrets.token_urlsafe(16)


def _new_batch_id() -> str:
    return f"batch_{secrets.token_urlsafe(12)}"


def _get_customer_processing_semaphore() -> asyncio.Semaphore:
    global _customer_processing_loop, _customer_processing_semaphore
    loop = asyncio.get_running_loop()
    if _customer_processing_semaphore is None or _customer_processing_loop is not loop:
        _customer_processing_loop = loop
        _customer_processing_semaphore = asyncio.Semaphore(
            max(1, settings.CUSTOMER_MAX_CONCURRENT_JOBS)
        )
    return _customer_processing_semaphore


def _check_file_size(filename: str, contents: bytes) -> None:
    max_bytes = settings.MAX_FILE_SIZE_MB * 1024 * 1024
    if len(contents) > max_bytes:
        raise HTTPException(
            status_code=413,
            detail=f"文件大小超过上限({settings.MAX_FILE_SIZE_MB}MB): {filename}",
        )


def _get_pdf_page_count(contents: bytes) -> int:
    try:
        doc = fitz.open(stream=contents, filetype="pdf")
        count = len(doc)
        doc.close()
        return count
    except Exception:
        return 0


def _total_pages(file_data: List[Tuple[str, bytes]]) -> int:
    total = 0
    for filename, contents in file_data:
        ext = Path(filename).suffix.lower()
        total += 1 if ext in ALLOWED_IMAGE_EXTS else _get_pdf_page_count(contents)
    return total


def _fields_json(config: Dict[str, Any]) -> str:
    return json.dumps(build_ai_field_definitions(config), ensure_ascii=False)


def _line_items_enabled(config: Dict[str, Any]) -> bool:
    line_config = config.get("line_items")
    return isinstance(line_config, dict) and line_config.get("enabled", True) is not False


def _line_items_json(config: Dict[str, Any]) -> str:
    return json.dumps(config.get("line_items", {}), ensure_ascii=False)


async def _extract_one_file(
    filename: str,
    contents: bytes,
    fields_json: str,
    config: Dict[str, Any],
) -> Tuple[str, int, Dict[str, Any], Dict[str, str], List[Dict[str, Any]]]:
    ext = Path(filename).suffix.lower()
    line_items_task = None
    if _line_items_enabled(config):
        line_items_spec = _line_items_json(config)
        if ext in ALLOWED_IMAGE_EXTS:
            line_items_task = extract_line_items_from_image_vlm(contents, filename, line_items_spec)
        else:
            line_items_task = extract_line_items_from_pdf_vlm(contents, line_items_spec)

    if ext in ALLOWED_IMAGE_EXTS:
        if line_items_task is None:
            extracted, notes = await extract_from_image(contents, filename, fields_json)
            return filename, 1, extracted, notes, []
        (extracted, notes), raw_line_items = await asyncio.gather(
            extract_from_image(contents, filename, fields_json),
            line_items_task,
        )
        line_items = normalize_customer_line_items(
            raw_line_items,
            source=filename,
            config=config,
        )
        return filename, 1, extracted, notes, line_items

    pages = _get_pdf_page_count(contents)
    if line_items_task is None:
        extracted, notes = await extract_from_single_pdf(contents, fields_json)
        return filename, pages, extracted, notes, []

    (extracted, notes), raw_line_items = await asyncio.gather(
        extract_from_single_pdf(contents, fields_json),
        line_items_task,
    )
    line_items = normalize_customer_line_items(
        raw_line_items,
        source=filename,
        config=config,
    )
    return filename, pages, extracted, notes, line_items


def _parse_job_payload(job: ProcessingJob) -> Dict[str, Any]:
    try:
        return json.loads(job.extracted_json or "{}")
    except json.JSONDecodeError:
        return {}


def _job_payload_with_meta(job: ProcessingJob, updates: Dict[str, Any] | None = None) -> Dict[str, Any]:
    payload = _parse_job_payload(job)
    if updates:
        payload.update(updates)
    return payload


def _set_job_queue_state(
    db: Session,
    job: ProcessingJob,
    *,
    status: str,
    progress: int,
    message: str,
    extra: Dict[str, Any] | None = None,
    commit: bool = True,
) -> Dict[str, Any]:
    payload = _job_payload_with_meta(
        job,
        {
            "job_id": job.file_id,
            "file_id": job.file_id,
            "status": status,
            "progress": max(0, min(100, int(progress))),
            "status_message": message,
            "updated_at": datetime.utcnow().isoformat() + "Z",
            **(extra or {}),
        },
    )
    job.status = status
    job.extracted_json = json.dumps(payload, ensure_ascii=False)
    if commit:
        db.commit()
    return payload


def _job_summary(job: ProcessingJob) -> Dict[str, Any]:
    payload = _parse_job_payload(job)
    filename = (
        payload.get("original_filename")
        or (payload.get("source_files") or [None])[0]
        or job.filename
    )
    return {
        "job_id": job.file_id,
        "file_id": job.file_id,
        "batch_id": payload.get("batch_id"),
        "filename": filename,
        "output_filename": payload.get("filename") or job.filename,
        "status": job.status,
        "progress": payload.get("progress", 100 if job.status == "done" else 0),
        "status_message": payload.get("status_message") or job.error_msg or "",
        "line_items_count": payload.get("line_items_count", 0),
        "fields_filled": job.fields_filled,
        "fields_total": job.fields_total,
        "page_count": job.page_count,
        "error": job.error_msg,
        "created_at": job.created_at.isoformat() + "Z" if job.created_at else "",
        "finished_at": job.finished_at.isoformat() + "Z" if job.finished_at else "",
    }


def _query_customer_batch_jobs(
    db: Session,
    batch_id: str,
    current_user: User,
) -> list[ProcessingJob]:
    query = db.query(ProcessingJob).filter(ProcessingJob.source_type == "customer")
    if current_user.role not in ("super_admin", "root_admin"):
        query = query.filter(ProcessingJob.username == current_user.username)
    candidates = query.order_by(ProcessingJob.created_at.asc(), ProcessingJob.id.asc()).all()
    return [
        job
        for job in candidates
        if _parse_job_payload(job).get("batch_id") == batch_id
    ]


def _batch_response(batch_id: str, jobs: list[ProcessingJob]) -> Dict[str, Any]:
    summaries = [_job_summary(job) for job in jobs]
    total = len(summaries)
    done = sum(1 for job in summaries if job["status"] == "done")
    failed = sum(1 for job in summaries if job["status"] == "failed")
    active = sum(1 for job in summaries if job["status"] == "processing")
    progress = round(
        sum(int(job.get("progress") or 0) for job in summaries) / total
    ) if total else 0
    if total and done + failed == total:
        status = "done" if failed == 0 else "partial_failed"
    elif active:
        status = "processing"
    else:
        status = "queued"
    return {
        "batch_id": batch_id,
        "status": status,
        "progress": progress,
        "total_files": total,
        "done_count": done,
        "failed_count": failed,
        "processing_count": active,
        "queued_count": sum(1 for job in summaries if job["status"] in ("pending", "queued")),
        "max_concurrent_jobs": settings.CUSTOMER_MAX_CONCURRENT_JOBS,
        "poll_interval_seconds": settings.CUSTOMER_POLL_INTERVAL_SECONDS,
        "jobs": summaries,
    }


def _assert_job_owner(job: ProcessingJob | None, current_user: User) -> ProcessingJob:
    if not job:
        raise HTTPException(status_code=404, detail="任务不存在。")
    if current_user.role not in ("super_admin", "root_admin") and current_user.username != job.username:
        raise HTTPException(status_code=403, detail="没有访问该任务的权限。")
    return job


def _build_done_payload(
    *,
    job_id: str,
    config: Dict[str, Any],
    parsed: Dict[str, Any],
    validation: Dict[str, Any],
    business_result: Dict[str, Any],
    source_files: list[str],
    output_filename: str,
    line_items: list[Dict[str, Any]] | None = None,
) -> Dict[str, Any]:
    fields = build_customer_result_fields(parsed, validation, config)
    items = line_items or []
    return {
        "job_id": job_id,
        "file_id": job_id,
        "filename": output_filename,
        "config_id": config.get("id"),
        "config_label": config.get("label", config.get("id")),
        "input_format": parsed.get("input_format", ""),
        "source_files": source_files,
        "fields": fields,
        "extracted": fields,
        "line_items": items,
        "line_items_count": len(items),
        "validation": validation,
        "business_result": business_result,
        "requires_manual_confirmation": bool(
            business_result.get("requires_manual_confirmation")
        ),
        "missing_fields": validation.get("missing_fields", []),
        "errors": validation.get("errors", []),
    }


def _write_job_export(
    *,
    file_id: str,
    payload: Dict[str, Any],
    config: Dict[str, Any],
) -> None:
    """同时生成 Excel 和 Word 两种导出格式。"""
    fields = payload.get("fields", [])
    line_items = payload.get("line_items", [])
    business_result = payload.get("business_result", {})
    source_filename = ", ".join(payload.get("source_files", [])) or "manual_input"

    kwargs = dict(
        file_id=file_id,
        fields=fields,
        line_items=line_items,
        business_result=business_result,
        config=config,
        source_filename=source_filename,
    )
    write_customer_result_workbook(**kwargs)
    write_customer_result_word(**kwargs)


def _compact_line_item_commit_result(result: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "result_type": result.get("result_type"),
        "action": result.get("action"),
        "business_key_id": result.get("business_key_id"),
        "business_key_value": result.get("business_key_value"),
        "old_value": result.get("old_value"),
        "new_value": result.get("new_value"),
        "submitted_price": result.get("submitted_price"),
        "selected_price": result.get("selected_price"),
        "message": result.get("message", ""),
    }


def _append_customer_log(
    repo: CustomerRepository,
    *,
    username: str,
    company_id: int | None,
    job_id: str,
    raw_input: str,
    parsed: Dict[str, Any],
    business_result: Dict[str, Any],
) -> None:
    try:
        parsed_fields = dict(parsed.get("normalized", {}))
        if parsed.get("line_items"):
            parsed_fields["_line_items"] = parsed.get("line_items", [])
        repo.append_operation_log(
            username=username,
            company_id=company_id,
            job_id=job_id,
            operation_type=business_result.get("result_type", "unknown"),
            business_key_id=business_result.get("business_key_id"),
            business_key_value=business_result.get("business_key_value"),
            raw_input=raw_input,
            parsed_fields=parsed_fields,
            process_result=business_result,
            old_value=business_result.get("old_value"),
            new_value=business_result.get("new_value"),
        )
    except Exception:
        logger.exception("客户操作日志写入失败")


async def _run_customer_job_bg(
    *,
    job_id: str,
    filename: str,
    contents: bytes,
    config_id: str | None,
    username: str,
    company_id: int | None,
    output_filename: str,
    batch_id: str | None = None,
) -> None:
    semaphore = _get_customer_processing_semaphore()
    async with semaphore:
        db = SessionLocal()
        job = None
        try:
            job = db.query(ProcessingJob).filter(ProcessingJob.file_id == job_id).first()
            if not job:
                return
            _set_job_queue_state(
                db,
                job,
                status="processing",
                progress=8,
                message="开始处理，正在准备文件。",
                extra={
                    "batch_id": batch_id,
                    "config_id": config_id,
                    "original_filename": filename,
                    "source_files": [filename],
                },
            )

            config = load_customer_keyword_config(config_id)
            fields_json = _fields_json(config)
            _set_job_queue_state(
                db,
                job,
                status="processing",
                progress=20,
                message="正在提取文档字段。",
                extra={"config_label": config.get("label", config.get("id"))},
            )
            extracted_filename, pages, extracted, notes, line_items = await _extract_one_file(
                filename,
                contents,
                fields_json,
                config,
            )

            _set_job_queue_state(
                db,
                job,
                status="processing",
                progress=68,
                message="识别完成，正在执行字段校验和业务规则。",
                extra={"page_count": pages, "line_items_count": len(line_items)},
            )
            sources = {key: extracted_filename for key, value in extracted.items() if value}
            parsed = parse_extracted_values(extracted, notes, config, sources)
            parsed["line_items"] = line_items
            validation = validate_customer_fields(parsed, config)
            repo = CustomerRepository(db)
            business_result = process_customer_product(
                parsed,
                validation,
                config,
                repo,
                company_id=company_id,
            )
            payload = _build_done_payload(
                job_id=job_id,
                config=config,
                parsed=parsed,
                validation=validation,
                business_result=business_result,
                source_files=[filename],
                output_filename=output_filename,
                line_items=line_items,
            )
            payload.update(
                {
                    "batch_id": batch_id,
                    "original_filename": filename,
                    "status": "done",
                    "progress": 100,
                    "status_message": "处理完成，可以查看和下载结果。",
                }
            )

            _set_job_queue_state(
                db,
                job,
                status="processing",
                progress=88,
                message="正在生成导出文件。",
                extra={"line_items_count": len(line_items)},
            )
            _write_job_export(file_id=job_id, payload=payload, config=config)

            valid_count = sum(
                1
                for field in payload["fields"]
                if field.get("filled") and field.get("validation_status") == "ok"
            )
            job.status = "done"
            job.filename = output_filename
            job.fields_filled = valid_count
            job.fields_total = len(payload["fields"])
            job.page_count = pages
            job.source_type = "customer"
            job.extracted_json = json.dumps(payload, ensure_ascii=False)
            job.finished_at = datetime.utcnow()
            db.commit()

            _append_customer_log(
                repo,
                username=username,
                company_id=company_id,
                job_id=job_id,
                raw_input=filename,
                parsed=parsed,
                business_result=business_result,
            )
            add_audit(
                db,
                username,
                "customer_keyword_processed",
                "",
                detail=f"batch_id={batch_id} file_id={job_id} fields={valid_count}/{len(payload['fields'])} line_items={len(line_items)} result={business_result.get('result_type')}",
            )
        except Exception as exc:
            logger.exception("[%s] 客户关键词任务失败", job_id)
            if job:
                job.status = "failed"
                job.error_msg = str(exc)
                _set_job_queue_state(
                    db,
                    job,
                    status="failed",
                    progress=100,
                    message=f"处理失败：{exc}",
                    extra={
                        "batch_id": batch_id,
                        "config_id": config_id,
                        "original_filename": filename,
                        "source_files": [filename],
                        "error": str(exc),
                    },
                    commit=False,
                )
                job.finished_at = datetime.utcnow()
                db.commit()
        finally:
            db.close()


async def _run_customer_batch_bg(
    *,
    batch_id: str,
    job_files: List[Tuple[str, str, bytes, str]],
    config_id: str | None,
    username: str,
    company_id: int | None,
) -> None:
    tasks = [
        _run_customer_job_bg(
            job_id=job_id,
            filename=filename,
            contents=contents,
            config_id=config_id,
            username=username,
            company_id=company_id,
            output_filename=output_filename,
            batch_id=batch_id,
        )
        for job_id, filename, contents, output_filename in job_files
    ]
    if tasks:
        await asyncio.gather(*tasks)


@customer_router.get("/api/customer/configs")
async def list_keyword_configs(
    _current_user: User = Depends(get_current_user),
):
    configs = list_customer_keyword_configs()
    return JSONResponse(content={"configs": configs})


@customer_router.post("/api/customer/process", status_code=202)
async def process_customer_files(
    background_tasks: BackgroundTasks,
    pdf_files: List[UploadFile] = File(...),
    config_id: str | None = Form(None),
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    config = load_customer_keyword_config(config_id)
    file_data: list[tuple[str, bytes]] = []
    if len(pdf_files) > settings.CUSTOMER_MAX_UPLOAD_FILES:
        raise HTTPException(
            status_code=400,
            detail=f"单批最多上传 {settings.CUSTOMER_MAX_UPLOAD_FILES} 个文件。",
        )
    for upload in pdf_files:
        ext = Path(upload.filename or "").suffix.lower()
        if ext != ".pdf" and ext not in ALLOWED_IMAGE_EXTS:
            raise HTTPException(
                status_code=400,
                detail=f"不支持的文件格式: {upload.filename}（支持 PDF 和图片）",
            )
        contents = await upload.read()
        if not contents:
            continue
        _check_file_size(upload.filename or "upload", contents)
        file_data.append((upload.filename or "upload", contents))

    if not file_data:
        raise HTTPException(status_code=400, detail="请至少上传一个 PDF 或图片文件。")

    total_pages = _total_pages(file_data)
    check_page_quota_or_429(db, current_user, total_pages)

    batch_id = _new_batch_id()
    job_files: list[tuple[str, str, bytes, str]] = []
    created_jobs: list[ProcessingJob] = []
    for index, (filename, contents) in enumerate(file_data, start=1):
        job_id = _new_file_id()
        output_filename = f"customer_result_{Path(filename).stem}_{job_id[:6]}.xlsx"
        job = ProcessingJob(
            file_id=job_id,
            username=current_user.username,
            filename=filename,
            fields_filled=0,
            fields_total=len(config.get("fields", [])),
            page_count=1 if Path(filename).suffix.lower() in ALLOWED_IMAGE_EXTS else _get_pdf_page_count(contents),
            extracted_json="{}",
            source_type="customer",
            status="queued",
            company_id=current_user.company_id,
        )
        db.add(job)
        db.flush()
        _set_job_queue_state(
            db,
            job,
            status="queued",
            progress=0,
            message=f"已进入队列，等待后台处理。队列位置 {index}",
            extra={
                "batch_id": batch_id,
                "config_id": config.get("id"),
                "config_label": config.get("label", config.get("id")),
                "original_filename": filename,
                "source_files": [filename],
                "filename": output_filename,
                "queue_position": index,
            },
            commit=False,
        )
        created_jobs.append(job)
        job_files.append((job_id, filename, contents, output_filename))
    db.commit()

    background_tasks.add_task(
        _run_customer_batch_bg,
        batch_id=batch_id,
        job_files=job_files,
        config_id=config.get("id"),
        username=current_user.username,
        company_id=current_user.company_id,
    )

    return JSONResponse(
        status_code=202,
        content={
            "batch_id": batch_id,
            "job_id": created_jobs[0].file_id,
            "status": "queued",
            "config_id": config.get("id"),
            "fields_total": len(config.get("fields", [])),
            "total_files": len(created_jobs),
            "max_concurrent_jobs": settings.CUSTOMER_MAX_CONCURRENT_JOBS,
            "poll_interval_seconds": settings.CUSTOMER_POLL_INTERVAL_SECONDS,
            "jobs": [_job_summary(job) for job in created_jobs],
        },
    )


@customer_router.get("/api/customer/batches/{batch_id}")
async def get_customer_batch(
    batch_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    jobs = _query_customer_batch_jobs(db, batch_id, current_user)
    if not jobs:
        raise HTTPException(status_code=404, detail="批次不存在或无权访问。")
    return JSONResponse(content=_batch_response(batch_id, jobs))


@customer_router.get("/api/customer/jobs/{job_id}")
async def get_customer_job(
    job_id: str,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _assert_job_owner(
        db.query(ProcessingJob).filter(ProcessingJob.file_id == job_id).first(),
        current_user,
    )
    payload = _parse_job_payload(job)
    response: Dict[str, Any] = {
        "job_id": job_id,
        "file_id": job.file_id,
        "status": job.status,
        "batch_id": payload.get("batch_id"),
        "progress": payload.get("progress", 100 if job.status == "done" else 0),
        "status_message": payload.get("status_message", ""),
        "filename": payload.get("filename") or job.filename,
        "original_filename": payload.get("original_filename") or job.filename,
    }
    if job.status == "done":
        response.update(payload)
        response.update(
            {
                "file_id": job.file_id,
                "filename": job.filename,
                "fields_filled": job.fields_filled,
                "fields_total": job.fields_total,
                "page_count": job.page_count,
            }
        )
    elif job.status == "failed":
        response.update(payload)
        response["error"] = job.error_msg or "处理失败。"
    return JSONResponse(content=response)


@customer_router.get("/api/customer/history")
async def get_customer_history(
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    query = db.query(ProcessingJob).filter(ProcessingJob.source_type == "customer")
    if current_user.role not in ("super_admin", "root_admin"):
        query = query.filter(ProcessingJob.username == current_user.username)
    jobs = query.order_by(ProcessingJob.created_at.desc()).limit(20).all()
    rows = []
    for job in jobs:
        payload = _parse_job_payload(job)
        rows.append(
            {
                "file_id": job.file_id,
                "batch_id": payload.get("batch_id"),
                "filename": payload.get("original_filename") or job.filename,
                "output_filename": payload.get("filename") or job.filename,
                "status": job.status,
                "progress": payload.get("progress", 100 if job.status == "done" else 0),
                "status_message": payload.get("status_message", ""),
                "fields_filled": job.fields_filled,
                "fields_total": job.fields_total,
                "page_count": job.page_count,
                "line_items_count": payload.get("line_items_count", 0),
                "created_at": job.created_at.isoformat() + "Z",
                "username": job.username if current_user.role in ("super_admin", "root_admin") else None,
            }
        )
    return rows


@customer_router.get("/api/customer/products")
async def list_customer_products(
    q: str = "",
    material_special: str = "",
    color: str = "",
    hardness: str = "",
    has_price: str = "",
    price_min: float | None = None,
    price_max: float | None = None,
    sort_by: str = "updated_at",
    sort_dir: str = "desc",
    page: int = 1,
    size: int = 20,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = CustomerRepository(db)
    result = repo.list_product_rows(
        company_id=current_user.company_id,
        q=q,
        material_special=material_special,
        color=color,
        hardness=hardness,
        has_price=has_price,
        price_min=price_min,
        price_max=price_max,
        sort_by=sort_by,
        sort_dir=sort_dir,
        page=page,
        size=size,
    )
    return JSONResponse(content=result)


@customer_router.get("/api/customer/products/{product_id}")
async def get_customer_product_detail(
    product_id: int,
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    repo = CustomerRepository(db)
    record = repo.get_product_record_by_id(product_id, current_user.company_id)
    if not record:
        raise HTTPException(status_code=404, detail="产品不存在或无权访问。")
    product = repo.product_to_dict(record) or {}
    row = repo.product_to_row(record)
    logs = [
        {
            "id": log.id,
            "operation_type": log.operation_type,
            "business_key_id": log.business_key_id,
            "business_key_value": log.business_key_value,
            "old_value": json.loads(log.old_value_json) if log.old_value_json else None,
            "new_value": json.loads(log.new_value_json) if log.new_value_json else None,
            "username": log.username,
            "created_at": log.created_at.isoformat() + "Z" if log.created_at else "",
        }
        for log in repo.product_logs(product=row, limit=20)
    ]
    return JSONResponse(content={"product": product, "row": row, "logs": logs})


@customer_router.post("/api/customer/regenerate")
async def regenerate_customer_result(
    payload: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _assert_job_owner(
        db.query(ProcessingJob).filter(ProcessingJob.file_id == payload.get("job_id")).first(),
        current_user,
    )
    config = load_customer_keyword_config(payload.get("config_id"))
    old_payload = _parse_job_payload(job)
    submitted_fields = payload.get("fields", [])
    if not submitted_fields:
        submitted_fields = old_payload.get("fields", [])
    field_values = {
        str(item.get("id")): item.get("value", "")
        for item in submitted_fields
        if item.get("id")
    }
    parsed = parse_extracted_values(field_values, {}, config, {})
    validation = validate_customer_fields(parsed, config)
    repo = CustomerRepository(db)
    business_result = process_customer_product(
        parsed,
        validation,
        config,
        repo,
        company_id=current_user.company_id,
    )
    submitted_line_items = payload.get("line_items")
    if submitted_line_items is not None:
        line_items = normalize_customer_line_items(
            submitted_line_items,
            source="manual_correction",
            config=config,
        )
    else:
        line_items = old_payload.get("line_items", [])
    output_filename = job.filename or f"customer_result_{job.file_id}.xlsx"
    new_payload = _build_done_payload(
        job_id=job.file_id,
        config=config,
        parsed=parsed,
        validation=validation,
        business_result=business_result,
        source_files=old_payload.get("source_files", ["manual_correction"]),
        output_filename=output_filename,
        line_items=line_items,
    )
    _write_job_export(file_id=job.file_id, payload=new_payload, config=config)

    job.status = "done"
    job.fields_filled = sum(
        1
        for field in new_payload["fields"]
        if field.get("filled") and field.get("validation_status") == "ok"
    )
    job.fields_total = len(new_payload["fields"])
    job.extracted_json = json.dumps(new_payload, ensure_ascii=False)
    job.finished_at = datetime.utcnow()
    db.commit()

    _append_customer_log(
        repo,
        username=current_user.username,
        company_id=current_user.company_id,
        job_id=job.file_id,
        raw_input="manual_correction",
        parsed=parsed,
        business_result=business_result,
    )
    return JSONResponse(
        content={
            **new_payload,
            "file_id": job.file_id,
            "fields_filled": job.fields_filled,
            "fields_total": job.fields_total,
            "page_count": job.page_count,
        }
    )


@customer_router.post("/api/customer/line-items/commit")
async def commit_customer_line_item(
    payload: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _assert_job_owner(
        db.query(ProcessingJob).filter(ProcessingJob.file_id == payload.get("job_id")).first(),
        current_user,
    )
    data = _parse_job_payload(job)
    config = load_customer_keyword_config(payload.get("config_id") or data.get("config_id"))

    try:
        line_index = int(payload.get("line_index"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="缺少有效的 SKU 行号。")

    existing_line_items = data.get("line_items", [])
    submitted_line_item = payload.get("line_item")
    if submitted_line_item is None:
        if line_index < 0 or line_index >= len(existing_line_items):
            raise HTTPException(status_code=404, detail="未找到要录入的 SKU 行。")
        submitted_line_item = existing_line_items[line_index]

    normalized_items = normalize_customer_line_items(
        [submitted_line_item],
        source="manual_line_item_commit",
        config=config,
    )
    if not normalized_items:
        raise HTTPException(status_code=400, detail="SKU 行数据为空，无法录入。")

    line_item = normalized_items[0]
    repo = CustomerRepository(db)
    commit_result = commit_customer_line_item_product(
        line_item,
        config,
        repo,
        company_id=current_user.company_id,
    )
    compact_result = _compact_line_item_commit_result(commit_result)
    line_item["database_status"] = commit_result.get("result_type")
    line_item["database_message"] = commit_result.get("message", "")
    line_item["database_committed_at"] = datetime.utcnow().isoformat() + "Z"
    line_item["database_commit_result"] = compact_result

    if not isinstance(existing_line_items, list):
        existing_line_items = []
    if 0 <= line_index < len(existing_line_items):
        existing_line_items[line_index] = line_item
    else:
        existing_line_items.append(line_item)
    data["line_items"] = existing_line_items
    data["line_items_count"] = len(existing_line_items)
    job.extracted_json = json.dumps(data, ensure_ascii=False)
    job.finished_at = datetime.utcnow()
    db.commit()

    _write_job_export(file_id=job.file_id, payload=data, config=config)
    _append_customer_log(
        repo,
        username=current_user.username,
        company_id=current_user.company_id,
        job_id=job.file_id,
        raw_input="line_item_commit",
        parsed={"normalized": line_item},
        business_result=commit_result,
    )
    add_audit(
        db,
        current_user.username,
        "customer_line_item_committed",
        "",
        detail=f"file_id={job.file_id} sku={commit_result.get('business_key_value')} result={commit_result.get('result_type')}",
    )

    return JSONResponse(
        content={
            "ok": True,
            "line_index": line_index,
            "line_item": line_item,
            "commit_result": compact_result,
        }
    )


@customer_router.post("/api/customer/confirm")
async def confirm_customer_result(
    payload: Dict[str, Any],
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    job = _assert_job_owner(
        db.query(ProcessingJob).filter(ProcessingJob.file_id == payload.get("job_id")).first(),
        current_user,
    )
    data = _parse_job_payload(job)
    business_result = data.get("business_result", {})
    business_result["manual_confirmation"] = {
        "confirmed_by": current_user.username,
        "confirmed_at": datetime.utcnow().isoformat() + "Z",
        "decision": payload.get("decision", "confirmed"),
        "note": payload.get("note", ""),
    }
    business_result["requires_manual_confirmation"] = False
    data["business_result"] = business_result
    data["requires_manual_confirmation"] = False
    job.extracted_json = json.dumps(data, ensure_ascii=False)
    db.commit()

    repo = CustomerRepository(db)
    business_key = get_business_key_field(load_customer_keyword_config(data.get("config_id")))
    _append_customer_log(
        repo,
        username=current_user.username,
        company_id=current_user.company_id,
        job_id=job.file_id,
        raw_input="manual_confirmation",
        parsed={"normalized": business_result.get("submitted_product", {})},
        business_result={
            **business_result,
            "result_type": "manual_confirmed",
            "business_key_id": business_result.get("business_key_id", business_key["id"]),
        },
    )
    return JSONResponse(content={"ok": True, "business_result": business_result})


@customer_router.get("/api/customer/download/{file_id}")
async def download_customer_file(
    file_id: str,
    filename: str = "customer_result.xlsx",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    _assert_job_owner(
        db.query(ProcessingJob).filter(ProcessingJob.file_id == file_id).first(),
        current_user,
    )
    file_path = settings.CUSTOMER_EXPORT_DIR / f"{file_id}.xlsx"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="导出文件不存在，请重新生成。")
    return FileResponse(
        path=str(file_path),
        media_type="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        filename=filename,
    )


@customer_router.get("/api/customer/download/{file_id}/word")
async def download_customer_word_file(
    file_id: str,
    filename: str = "customer_result.docx",
    current_user: User = Depends(get_current_user),
    db: Session = Depends(get_db),
):
    """下载客户关键词识别结果的 Word 文档。"""
    _assert_job_owner(
        db.query(ProcessingJob).filter(ProcessingJob.file_id == file_id).first(),
        current_user,
    )
    file_path = settings.CUSTOMER_EXPORT_DIR / f"{file_id}.docx"
    if not file_path.exists():
        raise HTTPException(status_code=404, detail="Word 导出文件不存在，请重新生成。")
    return FileResponse(
        path=str(file_path),
        media_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
        filename=filename,
    )
