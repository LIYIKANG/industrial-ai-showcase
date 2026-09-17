"""
scripts/backfill_customer_word.py
==================================
为已有的 customer 类型 done 任务补生成 Word 导出文件。

用法：
  cd pdf-analyzer-ai
  python scripts/backfill_customer_word.py           # 只补缺失的文件
  python scripts/backfill_customer_word.py --force   # 强制覆盖全部
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

# 确保项目根目录在 sys.path 中
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from core.config import load_customer_keyword_config, settings
from core.customer_word_writer import write_customer_result_word
from core.database import SessionLocal
from auth.models import ProcessingJob


def main():
    force = "--force" in sys.argv

    db = SessionLocal()
    try:
        jobs = (
            db.query(ProcessingJob)
            .filter(
                ProcessingJob.source_type == "customer",
                ProcessingJob.status == "done",
            )
            .all()
        )

        total = len(jobs)
        generated = 0
        skipped = 0
        failed = 0

        print(f"找到 {total} 个已完成客户关键词任务")
        if force:
            print("（--force 模式：强制覆盖已有文件）")
        print()

        for job in jobs:
            file_id = job.file_id
            docx_path = settings.CUSTOMER_EXPORT_DIR / f"{file_id}.docx"

            if docx_path.exists() and not force:
                print(f"  [跳过] {file_id} — Word 文件已存在")
                skipped += 1
                continue

            try:
                payload = json.loads(job.extracted_json or "{}")
            except json.JSONDecodeError:
                print(f"  [失败] {file_id} — extracted_json 解析错误")
                failed += 1
                continue

            if not payload.get("fields"):
                print(f"  [跳过] {file_id} — 无字段数据")
                skipped += 1
                continue

            config_id = payload.get("config_id")
            try:
                config = load_customer_keyword_config(config_id)
            except Exception:
                print(f"  [失败] {file_id} — 配置 {config_id} 不存在")
                failed += 1
                continue

            try:
                write_customer_result_word(
                    file_id=file_id,
                    fields=payload.get("fields", []),
                    line_items=payload.get("line_items", []),
                    business_result=payload.get("business_result", {}),
                    config=config,
                    source_filename=", ".join(payload.get("source_files", [])) or job.filename,
                )
                action = "覆盖" if docx_path.exists() else "生成"
                print(f"  [{action}] {file_id} → {docx_path.name}")
                generated += 1
            except Exception as exc:
                print(f"  [失败] {file_id} — {exc}")
                failed += 1

        print(f"\n完成: 生成 {generated}, 跳过 {skipped}, 失败 {failed} / 共 {total}")

    finally:
        db.close()


if __name__ == "__main__":
    main()
