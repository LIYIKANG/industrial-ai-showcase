#!/usr/bin/env python3
"""
load_test_phase3_v3.py
======================
Phase 3 v3: 非同期ジョブキュー対応 再検証

【v2との差分】
  検証対象: v2で NO CHANGE だった3シナリオ
  テスト方式:
    旧: POST /api/process → 処理完了まで同期待機 (タイムアウト 180s)
    新: POST /api/process → 202即時返却 → GET /api/jobs/{job_id} ポーリング

  期待:
    - HTTP タイムアウトエラー消滅（202は即座に返る）
    - 全ジョブが最終的に完了（タイムアウト→完了待ちに変わる）

  対象シナリオ:
    scan-large  / c=5   (v2: 60%)
    scan-large  / c=8   (新規: 上限検証)
    mixed c=3           (v2: 67%)
    mixed c=5           (v2: 60%)
    mixed c=8           (新規: 上限検証)

【実行】
  python load_test_phase3_v3.py
  python load_test_phase3_v3.py --url https://custom.onrender.com
"""

import argparse
import asyncio
import os
import statistics
import sys
import time
from collections import Counter
from dataclasses import dataclass, field
from io import BytesIO
from typing import Any, Dict, List, Optional, Tuple

import aiohttp
import fitz  # pymupdf

# ── 設定 ──────────────────────────────────────────────────────────────────────
BASE_URL       = os.environ.get("LOAD_TEST_URL", "http://127.0.0.1:18082")
USERNAME       = os.environ.get("LOAD_TEST_USERNAME", "admin")
PASSWORD       = os.environ.get("LOAD_TEST_PASSWORD", "")
SUBMIT_TIMEOUT = 30    # POST /api/process の HTTP タイムアウト（202返却まで）
POLL_INTERVAL  = 5     # ポーリング間隔（秒）
POLL_TIMEOUT   = 600   # ポーリング最大待機（秒）= 10 分
WARMUP_REQUESTS = 1

# v2で NO CHANGE だった3シナリオ + c=8 重量級（新規）
SCENARIOS: List[Tuple[str, int, bool, int]] = [
    ("scan-large / c=5",         12, True,  5),
    ("scan-large / c=8",         12, True,  8),   # 新規: 上限検証
    ("mixed / scan+light / c=3", 12, True,  3),
    ("mixed / scan+light / c=5", 12, True,  5),
    ("mixed / scan+light / c=8", 12, True,  8),   # 新規: 上限検証
]

V2_BASELINE: Dict[str, Tuple[int, int]] = {
    "scan-large / c=5":          (3, 5),   # 60%
    "mixed / scan+light / c=3":  (2, 3),   # 67%
    "mixed / scan+light / c=5":  (3, 5),   # 60%
}

# ── データ構造 ────────────────────────────────────────────────────────────────

@dataclass
class RequestResult:
    scenario:       str
    request_id:     int
    status:         int      # 200=done / 0=poll_timeout / -1=submit_failed or job_failed
    elapsed:        float    # submit開始〜ジョブ完了までの総時間
    phase_submit:   float = 0  # POST→202受け取りまで
    phase_process:  float = 0  # 202受け取り〜done まで
    error:          str = ""

@dataclass
class ScenarioReport:
    name: str
    results: List[RequestResult] = field(default_factory=list)

    @property
    def elapsed_list(self) -> List[float]:
        return [r.elapsed for r in self.results if r.status == 200]

    @property
    def status_count(self) -> Dict[str, int]:
        codes = [
            str(r.status) if r.status > 0
            else ("poll_timeout" if r.status == 0 else "failed")
            for r in self.results
        ]
        return dict(Counter(codes))

    def p(self, pct: float) -> Optional[float]:
        el = sorted(self.elapsed_list)
        if not el:
            return None
        return el[max(0, int(len(el) * pct / 100) - 1)]

    def summary_line(self) -> str:
        n    = len(self.results)
        ok   = sum(1 for r in self.results if r.status == 200)
        p50  = f"{self.p(50):.1f}s" if self.p(50) else "-"
        p95  = f"{self.p(95):.1f}s" if self.p(95) else "-"
        mx   = f"{max(self.elapsed_list):.1f}s" if self.elapsed_list else "-"
        codes = " | ".join(f"{k}:{v}" for k, v in sorted(self.status_count.items()))
        return f"  成功率:{ok}/{n}  P50={p50}  P95={p95}  Max={mx}  [{codes}]"

# ── テストPDF生成 ─────────────────────────────────────────────────────────────

def _create_pdf(pages: int, is_scan: bool) -> bytes:
    doc = fitz.open()
    for i in range(pages):
        page = doc.new_page(width=595, height=842)
        if is_scan:
            page.draw_rect(fitz.Rect(50, 50, 545, 792),
                           color=(0.9, 0.9, 0.9), fill=(1, 1, 1))
        else:
            lines = [
                f"重要事項説明書  ページ {i+1}/{pages}",
                "物件名称: テスト物件A",
                "所在地: 東京都渋谷区テスト1-2-3",
                "面積: 65.00㎡",
                "用途地域: 第一種住居地域",
                "建ぺい率: 60%  容積率: 200%",
                "取引態様: 売主  引渡時期: 即時",
                "管理費: 15,000円/月  修繕積立金: 8,000円/月",
            ]
            for j, line in enumerate(lines):
                page.insert_text((72, 100 + j * 30), line, fontsize=11)
    return doc.tobytes()

_PDF_CACHE: Dict[Tuple[int, bool], bytes] = {}

def get_pdf(pages: int, is_scan: bool) -> bytes:
    key = (pages, is_scan)
    if key not in _PDF_CACHE:
        _PDF_CACHE[key] = _create_pdf(pages, is_scan)
    return _PDF_CACHE[key]

# ── 認証 ──────────────────────────────────────────────────────────────────────

async def _get_token(session: aiohttp.ClientSession) -> Tuple[str, float]:
    t0 = time.perf_counter()
    async with session.post(
        f"{BASE_URL}/auth/login",
        json={"username": USERNAME, "password": PASSWORD},
        timeout=aiohttp.ClientTimeout(total=30),
    ) as resp:
        elapsed = time.perf_counter() - t0
        if resp.status != 200:
            raise RuntimeError(f"ログイン失敗: {resp.status}")
        data = await resp.json()
        return data["access_token"], elapsed

# ── 非同期ジョブ送信 + ポーリング ─────────────────────────────────────────────

async def _submit_job(
    session: aiohttp.ClientSession,
    pdf_bytes: bytes,
    token: str,
) -> Tuple[Optional[str], float]:
    """POST /api/process → 202 + job_id を返す。失敗時は (None, elapsed)。"""
    t0 = time.perf_counter()
    try:
        form = aiohttp.FormData()
        form.add_field("pdf_files", BytesIO(pdf_bytes),
                       filename="test.pdf", content_type="application/pdf")
        async with session.post(
            f"{BASE_URL}/api/process",
            data=form,
            headers={"Authorization": f"Bearer {token}"},
            timeout=aiohttp.ClientTimeout(total=SUBMIT_TIMEOUT),
        ) as resp:
            elapsed = time.perf_counter() - t0
            if resp.status == 202:
                body = await resp.json()
                return body.get("job_id"), elapsed
            body = await resp.text()
            return None, elapsed
    except Exception:
        return None, time.perf_counter() - t0


async def _poll_job(
    session: aiohttp.ClientSession,
    job_id: str,
    token: str,
    t_start: float,
) -> Tuple[str, Dict[str, Any]]:
    """ジョブ完了まで POLL_INTERVAL 秒ごとに確認。戻り値: (status_str, body)。"""
    deadline = t_start + POLL_TIMEOUT
    while time.perf_counter() < deadline:
        await asyncio.sleep(POLL_INTERVAL)
        try:
            async with session.get(
                f"{BASE_URL}/api/jobs/{job_id}",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=15),
            ) as resp:
                if resp.status != 200:
                    continue
                body = await resp.json()
                st = body.get("status")
                if st in ("done", "failed"):
                    return st, body
        except Exception:
            pass
    return "poll_timeout", {}


async def _run_single_async(
    session: aiohttp.ClientSession,
    scenario: str,
    req_id: int,
    pdf_bytes: bytes,
    token: str,
) -> RequestResult:
    t_start = time.perf_counter()

    job_id, submit_elapsed = await _submit_job(session, pdf_bytes, token)

    if not job_id:
        return RequestResult(
            scenario=scenario, request_id=req_id,
            status=-1, elapsed=time.perf_counter() - t_start,
            phase_submit=submit_elapsed, error="submit_failed",
        )

    print(f"    req#{req_id}: 202受け取り ({submit_elapsed:.2f}s)  job={job_id[:16]}...")

    poll_status, body = await _poll_job(session, job_id, token, t_start)
    total_elapsed = time.perf_counter() - t_start
    process_elapsed = total_elapsed - submit_elapsed

    if poll_status == "done":
        return RequestResult(
            scenario=scenario, request_id=req_id,
            status=200, elapsed=total_elapsed,
            phase_submit=submit_elapsed, phase_process=process_elapsed,
        )
    elif poll_status == "failed":
        return RequestResult(
            scenario=scenario, request_id=req_id,
            status=-1, elapsed=total_elapsed,
            phase_submit=submit_elapsed, phase_process=process_elapsed,
            error=f"job_failed: {body.get('error', '')}",
        )
    else:
        return RequestResult(
            scenario=scenario, request_id=req_id,
            status=0, elapsed=total_elapsed,
            phase_submit=submit_elapsed, phase_process=process_elapsed,
            error="poll_timeout",
        )

# ── 軽量リク（混在シナリオ用） ────────────────────────────────────────────────

async def _light_requests(
    session: aiohttp.ClientSession, token: str, duration: float
) -> List[float]:
    deadline = time.perf_counter() + duration
    timings: List[float] = []
    while time.perf_counter() < deadline:
        t0 = time.perf_counter()
        try:
            async with session.get(
                f"{BASE_URL}/auth/me",
                headers={"Authorization": f"Bearer {token}"},
                timeout=aiohttp.ClientTimeout(total=10),
            ) as r:
                await r.read()
                timings.append(time.perf_counter() - t0)
        except Exception:
            timings.append(-1)
        await asyncio.sleep(1)
    return timings

# ── シナリオ実行 ──────────────────────────────────────────────────────────────

async def run_scenario(
    name: str, pages: int, is_scan: bool,
    concurrency: int, with_light_load: bool = False,
) -> ScenarioReport:
    report    = ScenarioReport(name=name)
    pdf_bytes = get_pdf(pages, is_scan)
    v2_ok, v2_n = V2_BASELINE.get(name, (0, 0))

    print(f"\n{'─'*60}")
    print(f"  シナリオ: {name}")
    print(f"  PDF: {pages}ページ / {'スキャン' if is_scan else 'テキスト'} / {len(pdf_bytes)//1024}KB")
    print(f"  同時接続: {concurrency}  |  v2基準: {v2_ok}/{v2_n} ({v2_ok/v2_n*100:.0f}%)")
    print(f"  テスト方式: 非同期ジョブキュー (202 + ポーリング {POLL_INTERVAL}s間隔 / 最大{POLL_TIMEOUT}s)")
    if with_light_load:
        print("  モード: 軽量リク混在")
    print(f"{'─'*60}")

    connector = aiohttp.TCPConnector(limit=concurrency + 4)
    async with aiohttp.ClientSession(connector=connector) as session:
        try:
            token, auth_t = await _get_token(session)
            print(f"  認証: OK ({auth_t:.2f}s)")
        except Exception as e:
            print(f"  認証失敗: {e}")
            return report

        tasks = [
            _run_single_async(session, name, i, pdf_bytes, token)
            for i in range(concurrency)
        ]

        t_start = time.perf_counter()
        if with_light_load:
            light_task = asyncio.create_task(
                _light_requests(session, token, POLL_TIMEOUT))
            pdf_results = await asyncio.gather(*tasks, return_exceptions=True)
            light_task.cancel()
            light_timings: List[float] = []
            try:
                light_timings = await light_task
            except asyncio.CancelledError:
                pass
            if light_timings:
                ok_t = [t for t in light_timings if t >= 0]
                if ok_t:
                    print(f"  軽量リク({len(light_timings)}件): 成功{len(ok_t)}件 / 平均{statistics.mean(ok_t):.2f}s")
                else:
                    print("  軽量リク: 全失敗")
        else:
            pdf_results = await asyncio.gather(*tasks, return_exceptions=True)

        elapsed_total = time.perf_counter() - t_start

        for i, res in enumerate(pdf_results):
            if isinstance(res, Exception):
                report.results.append(RequestResult(
                    scenario=name, request_id=i,
                    status=-1, elapsed=0, error=str(res)))
            else:
                report.results.append(res)

    ok_results = [r for r in report.results if r.status == 200]
    print(f"\n  【結果】{report.summary_line()}")
    print(f"  合計経過: {elapsed_total:.1f}s")
    if ok_results:
        print(f"  フェーズ内訳(成功のみ):")
        print(f"    送信→202:          avg={statistics.mean(r.phase_submit  for r in ok_results):.1f}s")
        print(f"    AI処理完了まで:    avg={statistics.mean(r.phase_process for r in ok_results):.1f}s")
    for r in report.results:
        if r.status == 200:
            st_str = "done"
        elif r.status == 0:
            st_str = "poll_timeout"
        else:
            st_str = "failed"
        err_str = f" [{r.error}]" if r.error else ""
        print(f"    req#{r.request_id}: {st_str} / submit={r.phase_submit:.1f}s / total={r.elapsed:.1f}s{err_str}")

    return report

# ── ウォームアップ ────────────────────────────────────────────────────────────

async def warmup():
    print("\n[WARMUP] サーバーウォームアップ中...")
    async with aiohttp.ClientSession() as session:
        for i in range(WARMUP_REQUESTS):
            try:
                t0 = time.perf_counter()
                async with session.get(
                    f"{BASE_URL}/",
                    timeout=aiohttp.ClientTimeout(total=60),
                ) as r:
                    await r.read()
                    print(f"  warmup #{i+1}: {r.status} / {time.perf_counter()-t0:.1f}s")
            except Exception as e:
                print(f"  warmup #{i+1}: ERROR {e}")
    print("[WARMUP] 完了")

# ── レポート出力 ──────────────────────────────────────────────────────────────

def print_summary(reports: List[ScenarioReport]):
    print("\n" + "="*72)
    print("  PHASE 3 v3 — 非同期ジョブキュー 再検証サマリー")
    print("="*72)
    print(f"  {'シナリオ':<28} {'v2':>6} {'v3':>6} {'改善':>5} {'P50':>6} {'P95':>6} {'Max':>6}")
    print(f"  {'-'*68}")

    for rep in reports:
        n       = len(rep.results)
        ok      = sum(1 for r in rep.results if r.status == 200)
        v2_ok, v2_n = V2_BASELINE.get(rep.name, (0, 0))
        v2_pct  = f"{v2_ok/v2_n*100:.0f}%" if v2_n else "-"
        v3_pct  = f"{ok/n*100:.0f}%"
        diff    = (ok/n - v2_ok/v2_n) * 100 if v2_n else 0
        arrow   = f"+{diff:.0f}%" if diff > 0 else (f"{diff:.0f}%" if diff < 0 else "±0%")
        p50     = f"{rep.p(50):.0f}s" if rep.p(50) else "-"
        p95     = f"{rep.p(95):.0f}s" if rep.p(95) else "-"
        mx      = f"{max(rep.elapsed_list):.0f}s" if rep.elapsed_list else "-"
        print(f"  {rep.name:<28} {v2_pct:>6} {v3_pct:>6} {arrow:>5} {p50:>6} {p95:>6} {mx:>6}")

    print("\n" + "="*72)
    print("  判定")
    print("="*72)
    for rep in reports:
        n  = len(rep.results)
        ok = sum(1 for r in rep.results if r.status == 200)
        v2_ok, v2_n = V2_BASELINE.get(rep.name, (0, 0))
        rate    = ok / n if n else 0
        v2_rate = v2_ok / v2_n if v2_n else 0

        if v2_n == 0:
            print(f"  NEW        {rep.name}: {rate*100:.0f}% (新規シナリオ)")
        elif rate >= 1.0:
            print(f"  RESOLVED   {rep.name}: 100% 達成 (v2:{v2_rate*100:.0f}%→v3:100%)")
        elif rate > v2_rate + 0.15:
            print(f"  IMPROVED   {rep.name}: {v2_rate*100:.0f}%→{rate*100:.0f}% (+{(rate-v2_rate)*100:.0f}pt)")
        elif rate > v2_rate:
            print(f"  SLIGHT     {rep.name}: {v2_rate*100:.0f}%→{rate*100:.0f}% (+{(rate-v2_rate)*100:.0f}pt)")
        elif abs(rate - v2_rate) < 0.01:
            print(f"  NO CHANGE  {rep.name}: {rate*100:.0f}% (変化なし)")
        else:
            print(f"  REGRESSED  {rep.name}: {v2_rate*100:.0f}%→{rate*100:.0f}% ({(rate-v2_rate)*100:.0f}pt)")

# ── メイン ────────────────────────────────────────────────────────────────────

async def main(target_url: str):
    global BASE_URL
    BASE_URL = target_url.rstrip("/")

    print(f"\n{'='*72}")
    print(f"  PDF Analyzer AI — Phase 3 v3 (非同期ジョブキュー 再検証)")
    print(f"  対象: {BASE_URL}")
    print(f"  シナリオ数: {len(SCENARIOS)}  (v2 NO CHANGE 3件 + c=8 重量級 2件)")
    print(f"  送信タイムアウト: {SUBMIT_TIMEOUT}s  /  ポーリング最大: {POLL_TIMEOUT}s")
    print(f"{'='*72}")

    print("\n[SETUP] テストPDF生成中...")
    for pages, is_scan in {(s[1], s[2]) for s in SCENARIOS}:
        pdf = get_pdf(pages, is_scan)
        print(f"  {pages}p {'scan' if is_scan else 'text'}: {len(pdf)//1024}KB")

    await warmup()

    reports: List[ScenarioReport] = []
    for name, pages, is_scan, concurrency in SCENARIOS:
        is_mixed = "mixed" in name
        rep = await run_scenario(
            name=name, pages=pages, is_scan=is_scan,
            concurrency=concurrency, with_light_load=is_mixed,
        )
        reports.append(rep)
        await asyncio.sleep(15 if is_mixed else 10)

    print_summary(reports)


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Phase 3 v3 PDF負荷テスト（非同期ジョブキュー検証）")
    parser.add_argument("--url", default=BASE_URL,
                        help=f"テスト対象のベースURL (default: {BASE_URL})")
    args = parser.parse_args()
    asyncio.run(main(args.url))
