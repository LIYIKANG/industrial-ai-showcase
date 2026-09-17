"""
core/job_queue.py
=================
PDF 処理ジョブの同時実行数を制限する共有セマフォ。

単一 uvicorn プロセス内で同時に走る重いジョブ（PDF→画像変換でメモリを大量消費）を
JOB_CONCURRENCY 件までに制限し、同時アップロード時の OOM/Crash を防ぐ。

- 既定は 3（同時3ジョブまで）。同時投入時のスループットと OOM 抑制のバランス。
- メモリが厳しい場合は環境変数 JOB_CONCURRENCY=1 等で同時数を下げられる。
- 202 + ポーリング方式のため、待機中もクライアントはジョブ状態を取得できる。
"""

import asyncio
import os
from typing import Optional

_JOB_CONCURRENCY: int = int(os.getenv("JOB_CONCURRENCY", "3"))
_job_semaphore: Optional[asyncio.Semaphore] = None


def get_job_semaphore() -> asyncio.Semaphore:
    """ジョブ同時実行制限用セマフォを返す（遅延初期化）。"""
    global _job_semaphore
    if _job_semaphore is None:
        _job_semaphore = asyncio.Semaphore(_JOB_CONCURRENCY)
    return _job_semaphore
