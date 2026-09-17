# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 概要

日本の不動産重要事項説明書（PDF/画像）を Claude AI で解析し、Excel テンプレートに自動入力する FastAPI アプリケーション。

## 起動・開発コマンド

```bash
# 依存インストール
pip install -r requirements.txt

# DB 初期化（初回のみ）
python scripts/init_db.py

# 開発サーバー起動（hot reload あり）
python app.py

# 本番起動（Gunicorn）
APP_ENV=production gunicorn -c deploy/gunicorn.conf.py app:app
```

## 環境設定

`.env.example` をコピーして `.env` を作成。必須項目：

- `CLAUDE_API_KEY` — Anthropic API キー
- `JWT_SECRET` — 32 文字以上のランダム文字列
- `INIT_ADMIN_USERNAME` / `INIT_ADMIN_PASSWORD` — DB 初期化時の管理者アカウント
- `APP_ENV=production` にすると `.env.production` を読み込む（環境切り替え）

## アーキテクチャ

### 処理フロー

```
PDF/画像アップロード
  → pdf_utils.py: テキスト層判定 → ページを高品質JPEG変換（2倍スケール + コントラスト強化）
  → ai_extractor.py: Claude API に画像＋テキストを送信 → JSON でフィールド値取得
  → excel_writer.py: field_mapping.json のセル定義に従い Excel テンプレートに書き込み（赤字フォント）
  → ダウンロード用ファイルを output/ に保存
```

### 主要ファイル

| ファイル | 役割 |
|---------|------|
| `app.py` | FastAPI 初期化・ルーター登録・CORS |
| `core/config.py` | 全環境変数・グローバル設定・Claude クライアント初期化 |
| `core/ai_extractor.py` | Claude API 呼び出し・フィールド抽出ロジック |
| `core/pdf_utils.py` | PDF→画像変換・スキャン品質向上（PyMuPDF + Pillow） |
| `core/excel_writer.py` | openpyxl で Excel テンプレートへ値書き込み |
| `config/field_mapping.json` | 抽出フィールド定義（31 項目）と Excel セル位置マッピング |
| `api/routes.py` | メイン API（`/api/process`, `/api/download`, `/api/regenerate`） |
| `api/beta_routes.py` | Beta 機能：ユーザー独自テンプレートのアップロード・解析 |
| `auth/` | JWT 認証・ユーザー管理・ブルートフォース対策・監査ログ |
| `scripts/init_db.py` | SQLAlchemy テーブル作成＋初期管理者ユーザー作成 |

### 認証フロー

- Access Token（15 分）+ Refresh Token（7 日、HTTP-only Cookie）の二段階 JWT
- ブルートフォース対策：5 回失敗で 15 分ロック（`auth/models.py` の `LoginAttempt`）
- ログアウト時は Refresh Token の JTI を `TokenBlacklist` テーブルに登録

### フロントエンド

Jinja2 テンプレート（`templates/`）+ バニラ JS（`static/`）。SPA 的な構成で `api.js` → `app.js` → `ui.js` の役割分担。多言語対応は `i18n.js`。

## フィールドマッピングの追加・変更

`config/field_mapping.json` に `{"id": "field_id", "label": "表示名", "cell": "A2"}` 形式で追加するだけで、AI 抽出とExcel書き込みの両方に反映される。`ai_extractor.py` が `field_mapping.json` を読み込んで Claude へのプロンプトを生成するため、マッピングを変更すれば抽出プロンプトも自動的に更新される。

## Beta 機能

`/beta` ページでユーザーが独自の Excel/Word テンプレートをアップロードすると、Claude がテンプレート構造を解析して抽出フィールド候補を自動生成する。`core/template_analyzer.py` が担当。

## デプロイ構成

`deploy/` に Gunicorn（UvicornWorker）・Nginx・systemd の設定テンプレートあり。Gunicorn のタイムアウトは 300 秒（AI + PDF 処理時間考慮）。Nginx で `/static/` は直接配信。
