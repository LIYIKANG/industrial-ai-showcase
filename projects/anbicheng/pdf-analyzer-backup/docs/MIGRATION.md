# Company マルチテナント化 Migration 手順

`feat/company-multitenancy` ブランチで導入された DB スキーマ変更を本番 Supabase
に適用する手順。

---

## 適用順序

3 つの migration を **この順** で実行する。順序を間違えると整合性が壊れる。

```
1. scripts/migrate_add_allowed_pages.py   # PR0 (既存・未適用環境用)
2. scripts/migrate_add_company.py         # PR1
3. scripts/migrate_role_upgrade.py        # PR2
```

各 script は **冪等**。複数回実行しても安全 (2 回目以降は "no changes" を出力)。

---

## 各 migration の概要

### 1. `migrate_add_allowed_pages.py` (PR0 / 既存)

- `users.allowed_pages` カラム (JSON, default 全ページ許可) を追加
- 既に適用済みなら何もしない

### 2. `migrate_add_company.py` (PR1)

- 新規テーブル: `companies`
- 新規カラム: `users.company_id`, `processing_jobs.company_id` (両方とも nullable + FK)
- `companies.name = 'default'` を 1 件 INSERT (plan='internal', max_users=9999, max_monthly_pages=99999999)
- 既存 users.company_id が NULL かつ role != 'super_admin' なら default Company に紐付け
- 既存 processing_jobs.company_id が NULL なら default Company に紐付け

### 3. `migrate_role_upgrade.py` (PR2)

- `users.role` 値変換: `owner` → `super_admin`, `admin` → `super_admin`, `operator` → `company_member`
- `super_admin` ユーザーの `company_id` を NULL に戻す (Companyに属さない設計)

---

## 本番 (Supabase PostgreSQL) 適用手順

### 事前準備

1. **Supabase Dashboard でダンプを取得**
   - Project Settings → Database → Backups で時点を確認
   - もしくは `pg_dump` でローカルにバックアップ取得:
     ```bash
     pg_dump "$DATABASE_URL" > backup_pre_company_$(date +%Y%m%d_%H%M).sql
     ```

2. **ステージング環境で先に検証** (理想)
   - 本番と同等のスキーマを持つ別 Supabase プロジェクトを用意して試行

3. **メンテナンスモード周知**
   - migration 自体は 1 分以内に完了するが、デプロイと併せて 5-10 分のサービス
     一時停止をユーザーに告知

### 実行

```powershell
# プロジェクトルートで、本番 .env を読む設定で実行
$env:PYTHONUTF8 = "1"
$env:APP_ENV = "production"   # .env.production を読む場合

.\.venv\Scripts\python.exe scripts\migrate_add_allowed_pages.py
.\.venv\Scripts\python.exe scripts\migrate_add_company.py
.\.venv\Scripts\python.exe scripts\migrate_role_upgrade.py
```

各 script の出力で `Done.` または `no changes needed.` が表示されれば成功。

### デプロイ順

migration とコード反映の関係:

| タイミング | 状態 |
|------------|------|
| 旧コード (master) + 旧スキーマ | 通常稼働 |
| **旧コード + 新スキーマ (migration 後)** | OK (新カラムは nullable のため互換) |
| 新コード (feat/company-multitenancy) + 新スキーマ | 完成形 |

migration → デプロイの順で行うのが安全。逆順だと新コードが古いスキーマで起動し
500 が発生する。

---

## 検証手順

migration 後に以下を実行して整合性を確認:

```sql
-- 1. 全 users に company_id が設定されているか (super_admin を除く)
SELECT id, username, role, company_id FROM users WHERE role != 'super_admin' AND company_id IS NULL;
-- 0 件であるべき

-- 2. super_admin の company_id は NULL であるか
SELECT id, username, company_id FROM users WHERE role = 'super_admin' AND company_id IS NOT NULL;
-- 0 件であるべき

-- 3. 旧 role 値が残っていないか
SELECT id, username, role FROM users WHERE role IN ('owner', 'admin', 'operator');
-- 0 件であるべき

-- 4. default Company が存在するか
SELECT id, name, plan FROM companies WHERE name = 'default';
-- 1 件であるべき

-- 5. 全 processing_jobs に company_id が設定されているか
SELECT COUNT(*) FROM processing_jobs WHERE company_id IS NULL;
-- 0 件であるべき
```

---

## ロールバック

万が一問題が発生した場合:

### 即時ロールバック (1 分以内)

旧コードに戻す:
```bash
git revert <マージcommit>   # or
git checkout <マージ前のcommit>
```

DB は新スキーマのままだが、旧コードは追加カラム/テーブルを無視するので動作する
(新カラムは全て nullable、Company テーブルは旧コードから参照されない)。

ただし `users.role` の値変換は元に戻らない:
- `super_admin` → 旧コードは `admin` として扱う必要があるが、旧の
  `require_admin` は `role in ("owner", "admin")` のためマッチしない → 旧
  admin ユーザーがログイン後 admin 画面にアクセス不可になる

→ ロールバック時は role 値も戻す必要がある:

```sql
UPDATE users SET role = 'owner' WHERE role = 'super_admin';
-- 旧 admin と owner の区別は失われる (両者を super_admin に統合済みのため)。
-- 'owner' に戻すのが安全 (権限が広い方に倒す)。
UPDATE users SET role = 'operator' WHERE role = 'company_member';
-- 念のため company_id を default に戻す
UPDATE users SET company_id = (SELECT id FROM companies WHERE name = 'default')
  WHERE company_id IS NULL;
```

新規追加した Company / カラムは残置で構わない (旧コードは無視する)。

### バックアップからの完全復元

致命的データ破損時:
```bash
psql "$DATABASE_URL" < backup_pre_company_<timestamp>.sql
```

---

## ダウンタイム見積もり

| 項目 | 時間 |
|------|------|
| migration 実行 (users 数 100 程度) | < 10 秒 |
| migration 実行 (users 数 10,000) | < 1 分 |
| デプロイ (Render web service) | 2-3 分 |
| **合計サービス停止** | **3-5 分** |

migration は `ALTER TABLE ADD COLUMN` (PostgreSQL では即時) と
小規模 UPDATE のみのため、テーブルロックは短時間。

---

## トラブルシューティング

### `ModuleNotFoundError: No module named 'tzdata'`

Windows ローカルで PR3 以降のコードを動かす場合に発生。
```powershell
pip install tzdata
```

Linux (Render など) では通常 OS のタイムゾーンデータが使われるため不要。

### `sqlite3.OperationalError: no such column: users.allowed_pages`

PR0 の migration (migrate_add_allowed_pages.py) を先に適用していない。
順序通り実行する。

### `IntegrityError: NOT NULL constraint failed: users.company_id`

未来の PR で `company_id` を NOT NULL にした場合に発生しうる。
現状の PR では company_id は全て nullable のため発生しない。
