# OOM 根本原因分析（2026-06-10 調査記録）

Render 512MB 環境で OOM/Crash が対策後も根治しない理由の調査結果。
**本ドキュメントは調査記録であり、修正は未実施**（実装時の指針として残す）。

## 実施済み対策（効果はあるが根治に至らず）

1. stripe 遅延 import（起動時常駐 -54MB）
2. ジョブ同時実行 Semaphore（`core/job_queue.py`、`JOB_CONCURRENCY=3`）
3. メモリ辞書 LRU 上限 2（`_job_meta` / `_file_owners`）
4. `pdf_utils.py` の中間オブジェクト `del`
5. 原本確認（review）機能の無効化
6. 出力ファイルの明示ディスク化（`OUTPUT_DIR`）

## 根本原因（3つの構造問題）

### 問題1（本丸）: Semaphore がジョブ内並列を制御していない

`JOB_CONCURRENCY=3` は**ジョブ単位**の制限。しかし各 `_run_*_bg` の内部では
複数ファイル × 2パスを `asyncio.gather` で**無制限並列実行**している
（`api/routes.py` の `_run_job_bg` の pass_tasks gather、
seisansho / seikyusho / beta の `asyncio.gather(*tasks)` も同型）。

```
3ジョブ並行 × 各2ファイル × 2パス = 実効12並列の Claude 呼び出し
→ 各タスクの base64 画像リストが応答待ちの間ずっとメモリ保持
→ Semaphore=3 のつもりが、メモリ加算は実質無制限
```

これが「Semaphore を入れたのに同時投入で落ちる」の正体。

### 問題2: 画像変換チェーンの瞬間ピーク

`pdf_bytes_to_base64_images`（`core/pdf_utils.py`）は1ページにつき
pixmap(raw RGB ~17MB @2400px) → PIL → Contrast → Sharpness → SHARPEN と
**各段で新しい画像コピーを生成**。1ページあたり瞬間 50-70MB 級のピークが
発生し、問題1の並列度ぶん重なる。

### 問題3: CPython 断片化で RSS が OS に返らない

`del` + `gc.collect()` は Python 内部の参照を解放するだけで、
malloc arena の断片化により **RSS は高止まり**する。
アイドル時ベースライン約 406MB の内訳:
- Python + uvicorn/FastAPI: ~70MB
- fitz(PyMuPDF) ~30MB / anthropic SDK ~25MB / SQLAlchemy ~15MB /
  PIL ~8MB / jose・passlib ~10MB ほか（モジュールレベル import、推計）
- arena 断片化・ヒープ未返却: 50-80MB（推計）

長期稼働での漸増は「リーク」ではなく断片化が主因の可能性が高い。

### 採用しない案（検討済み・効果なし）

**fitz / PIL / anthropic の遅延 import** はベースライン削減に**ならない**。
stripe と違いこれらは本業（PDF処理）で必ず使うため、初回ジョブで
ロードされ常駐に戻る。アイドル直後の見かけが下がるだけ。

## 有効な修正候補（効果順・未実施）

| # | 対策 | 効果 | コスト |
|---|------|------|--------|
| 1 | **ジョブ内 gather を直列化**（ファイル毎に変換→抽出→解放） | ピークを「1ファイル分×JOB_CONCURRENCY」に制限。本丸 | 4ファイル・中（2-3h） |
| 2 | **解像度引き下げ**（`_RENDER_SCALE` 2.0→1.5 / `_MAX_SIDE` 2400→1800） | 変換ピーク・API送信量 -44%。OCR精度 2-3% 低下 | 2行・小 |
| 3 | **enhance 中間の即時解放**（変換チェーンの段毎 del） | ページ毎瞬間ピーク削減 | 小 |
| 4 | **subprocess 分離**（PDF変換を子プロセス化） | 終了時に OS がメモリ全回収。断片化への唯一の根本解 | 大（4-6h） |
| 5 | **プラン増強** 512MB→2GB | 全ピーク対応 | 月額コスト |

**推奨順**: 1+2+3 を実装 → 実測（memory.current / oom_kill）→
不足なら 4 または 5。

トレードオフ: #1 は複数ファイル同時アップロード時の処理時間が 1-2 割増。
#2 はスキャン文書の OCR 精度がわずかに低下。

## 実装時の参照箇所

- ジョブ内 gather: `api/routes.py` `_run_job_bg`（pass_tasks）、
  `api/seisansho_routes.py:62-66` 付近、`api/seikyusho_routes.py:62-66` 付近、
  `api/beta_routes.py`（process 内 `asyncio.gather(*tasks)`）
- 解像度定数: `core/pdf_utils.py:19-23`（`_RENDER_SCALE` / `_MAX_SIDE` / `_MAX_PAGES`）
- 変換チェーン: `core/pdf_utils.py` `pdf_bytes_to_base64_images`
