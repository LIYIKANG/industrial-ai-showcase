# Stripe 決済セットアップ手順書

本書は pdf-analyzer-ai（FastAPI）に Stripe 決済（サブスクリプション）を設定するための運用担当者向け手順書です。Stripe Dashboard と Render での設定を順に行えば、Test mode での動作確認から本番（Live）稼働まで完遂できます。

---

## 概要

- 決済方式: **ホスト型 Stripe Checkout**（サブスクリプション契約）＋ **Stripe Billing Portal**（プラン変更・解約・カード更新）＋ **Webhook**（Stripe → DB 同期）。
- **真実の源は Stripe**。アプリは Webhook イベントを受けて DB（`companies` テーブル）の `plan` / `subscription_status` / 上限を Stripe に追従させます。
- **課金主体は Company（テナント）**。Stripe Customer と Company が 1:1、Subscription と Company が 1 件で対応します。
- 操作可能ロール:
  - **company_owner**: 自社（所属 Company）のみ Checkout / Portal を操作可能。
  - **super_admin / root_admin**: 対象 `company_id` を明示指定して操作可能（自身は Company 無所属）。
  - 決済 UI（契約ボタン等）はフロントの `/me` で **company_owner かつ `is_stripe_configured` が真** のときのみ表示されます。
- Stripe 未設定（`STRIPE_SECRET_KEY` 空）でもアプリは起動可能。決済 API を呼んだ時のみ 503 / 明示エラーになります。

---

## プラン構成

`core/config.py` の `PLAN_LIMITS` / `BILLABLE_PLANS` に基づく確定値です。各プランの月額 Price を Stripe で作成し、対応する env 変数に Price ID を設定します。

| プラン | env 変数名 | max_users | 月間ページ上限 (max_monthly_pages) | 課金対象 |
|--------|-----------|-----------|-----------------------------------|---------|
| basic | `STRIPE_PRICE_BASIC` | 2 | 1,500（PDF 50 枚 × 平均 30p） | ○ |
| standard | `STRIPE_PRICE_STANDARD` | 3 | 3,000（100 枚 × 30p） | ○ |
| pro | `STRIPE_PRICE_PRO` | 4 | 6,000（200 枚 × 30p） | ○ |
| flagship | `STRIPE_PRICE_FLAGSHIP` | 5 | 12,000（400 枚 × 30p） | ○ |
| internal | （なし） | 9,999 | 99,999,999 | **課金外**（システム標準 Company 用、社内利用） |

- `BILLABLE_PLANS = ["basic", "standard", "pro", "flagship"]`。`internal` は課金対象外で、Stripe 上に Price を作る必要はありません。
- 購読が `active` / `trialing` になると、Webhook 同期時に Price ID から該当プランを判定し、`PLAN_LIMITS` の上限が自動適用されます。
- 解約・支払い遅延時はプランと上限を据え置き（Phase 1 は非破壊）。

---

## 必要な環境変数一覧

`core/config.py` で参照される変数です。

| 変数名 | 用途 | 必須/任意 | 例 |
|--------|------|-----------|-----|
| `STRIPE_SECRET_KEY` | Stripe シークレットキー。決済 API 全般の認証。`is_stripe_configured` はこれが空でないかで判定 | 決済を使うなら必須 | `sk_test_xxx` / `sk_live_xxx` |
| `STRIPE_WEBHOOK_SECRET` | Webhook 署名検証シークレット。`/api/billing/webhook` の署名チェックに使用 | Webhook 利用時必須 | `whsec_xxx` |
| `STRIPE_PRICE_BASIC` | basic プランの月額 Price ID | basic を提供するなら必須 | `price_xxx` |
| `STRIPE_PRICE_STANDARD` | standard プランの月額 Price ID | standard を提供するなら必須 | `price_xxx` |
| `STRIPE_PRICE_PRO` | pro プランの月額 Price ID | pro を提供するなら必須 | `price_xxx` |
| `STRIPE_PRICE_FLAGSHIP` | flagship プランの月額 Price ID | flagship を提供するなら必須 | `price_xxx` |
| `APP_BASE_URL` | Checkout / Portal の戻り先ベース URL（末尾スラッシュは自動除去） | 必須（既定値 `http://localhost:9000`） | `https://pdf-analyzer-ai.onrender.com` |
| `STRIPE_PUBLISHABLE_KEY` | publishable キー。**現状アプリ内で未使用**（ホスト型 Checkout のため不要） | 任意 | `pk_test_xxx` |

戻り先 URL（`core/billing.py` で固定生成）:

- Checkout 成功: `{APP_BASE_URL}/me?billing=success`
- Checkout キャンセル: `{APP_BASE_URL}/me?billing=cancel`
- Billing Portal 戻り: `{APP_BASE_URL}/me`

---

## エンドポイント一覧

`api/billing_routes.py` で定義（プレフィックス `/api/billing`）。

| メソッド | パス | 権限 | 役割 |
|---------|------|------|------|
| POST | `/api/billing/checkout` | company_owner / super_admin / root_admin | Checkout Session を作成し URL を返す |
| POST | `/api/billing/portal` | company_owner / super_admin / root_admin | Billing Portal Session を作成し URL を返す |
| GET | `/api/billing/subscription` | ログインユーザー | 所属 Company の購読状態＋契約可能プラン一覧 |
| POST | `/api/billing/webhook` | 公開（認証なし・署名検証） | Stripe Webhook 受信口 |

---

## Stripe Dashboard 設定手順（Test mode 前提）

以降は Dashboard 右上のトグルが **Test mode** であることを確認して進めます。

### Step 1: アカウント

1. https://dashboard.stripe.com でアカウントを作成 / ログイン。
2. 右上トグルで **Test mode** をオンにする。

### Step 2: API キー取得

1. https://dashboard.stripe.com/test/apikeys を開く。
2. **Secret key**（`sk_test_...`）をコピー → env `STRIPE_SECRET_KEY` に設定する。
3. Publishable key（`pk_test_...`）は **現状アプリ未使用** のため設定不要（ホスト型 Checkout を使うため）。

### Step 3: Product と月額 Recurring Price の作成（4 プラン分）

1. https://dashboard.stripe.com/test/products で **Product** を作成（例: 「PDF Analyzer サブスクリプション」）。プランごとに Product を分けても、1 Product 配下に 4 Price を作っても構いません。
2. 各プランについて **Recurring（継続）/ 月次（monthly）** の Price を作成する。合計 4 つ:
   - basic 用 → Price ID を `STRIPE_PRICE_BASIC` に設定
   - standard 用 → `STRIPE_PRICE_STANDARD`
   - pro 用 → `STRIPE_PRICE_PRO`
   - flagship 用 → `STRIPE_PRICE_FLAGSHIP`
3. 各 Price の詳細画面に表示される **Price ID（`price_...`）** をコピーして、対応する env 変数に設定する。
   - 注: env に設定する値は Price ID（`price_...`）であり、Product ID（`prod_...`）ではない。

### Step 4: Customer Portal の設定（必須・保存しないとエラー）

Billing Portal はあらかじめ Dashboard で構成を **保存** しておく必要があります。未保存だと Portal 作成 API が「No configuration provided」エラーになります。

1. https://dashboard.stripe.com/test/settings/billing/portal を開く。
2. **Subscriptions** セクションで、プラン変更（Switch plans）を許可する。
3. **変更可能な製品（Products）** として、Step 3 で作成した **4 プランすべての Price** を登録する。
4. 必要に応じて解約（Cancel subscriptions）・支払い方法更新を許可。
5. 画面下部の **Save** を必ずクリックする（保存しないと Portal が動作しない）。

### Step 5: Webhook endpoint の登録

1. https://dashboard.stripe.com/test/webhooks で **Add endpoint** をクリック。
2. **Endpoint URL** に本番受信口を入力:
   ```
   https://pdf-analyzer-ai.onrender.com/api/billing/webhook
   ```
   （ローカルテスト時は後述の Stripe CLI を使用）
3. **受信イベント（Listen to）** に、アプリが処理する以下 **5 種** を選択する（`core/billing.py` の `apply_subscription_event` が処理する種別）:
   - `checkout.session.completed`
   - `customer.subscription.created`
   - `customer.subscription.updated`
   - `customer.subscription.deleted`
   - `invoice.payment_failed`
4. 作成後、エンドポイント詳細の **Signing secret（`whsec_...`）** をコピー → env `STRIPE_WEBHOOK_SECRET` に設定する。

---

## Render 環境変数設定

`render.yaml` に Stripe 関連の env は **定義済み（すべて `sync: false`）** です。`sync: false` の変数は値が Git に入らないため、Render ダッシュボードで実値を入力します。

設定手順:

1. Render の対象サービス（`pdf-analyzer-ai`）→ **Environment** タブを開く。
2. 以下のキーに値を入力（キー自体は render.yaml により既に存在）:

   | キー | 入力する値 |
   |------|-----------|
   | `STRIPE_SECRET_KEY` | `sk_test_...`（Test）/ `sk_live_...`（本番） |
   | `STRIPE_WEBHOOK_SECRET` | Step 5 の `whsec_...` |
   | `STRIPE_PRICE_BASIC` | basic の `price_...` |
   | `STRIPE_PRICE_STANDARD` | standard の `price_...` |
   | `STRIPE_PRICE_PRO` | pro の `price_...` |
   | `STRIPE_PRICE_FLAGSHIP` | flagship の `price_...` |
   | `APP_BASE_URL` | `https://pdf-analyzer-ai.onrender.com`（末尾スラッシュ無し） |

3. **Save Changes** をクリック。Render が自動で再デプロイし、新しい env が反映される。

---

## 起動時マイグレーション

`render.yaml` の `startCommand` は次の通りで、起動のたびに DB マイグレーションが実行されます。

```
python scripts/init_db.py && python scripts/migrate_add_billing.py && uvicorn app:app --host 0.0.0.0 --port $PORT
```

`scripts/migrate_add_billing.py` の役割:

- `companies` テーブルに課金用カラムを追加する:
  - `stripe_customer_id` VARCHAR(64)
  - `stripe_subscription_id` VARCHAR(64)
  - `subscription_status` VARCHAR(20) DEFAULT 'inactive' NOT NULL
  - `current_period_end` TIMESTAMP
- **冪等（idempotent）**: 各カラムは存在チェックを行い、既にあればスキップする。何度起動しても安全。
- `companies` テーブルが未作成の場合は「init_db.py を先に実行」と表示して終了（startCommand では先に `init_db.py` が走るため通常は問題なし）。

---

## 動作テスト（Test mode）

### 1. 契約フロー（成功）

1. company_owner でログインし `/me` を開く（決済 UI は company_owner かつ Stripe 設定済みのとき表示）。
2. プランを選んで契約 → Checkout 画面でテストカードを入力:
   - カード番号: `4242 4242 4242 4242`
   - 有効期限: 任意の未来日 / CVC: 任意 3 桁 / 郵便番号: 任意
3. 決済完了後、`{APP_BASE_URL}/me?billing=success` に戻る。
4. `GET /api/billing/subscription` または `/me` 画面で `subscription_status` が `active`、`plan` が選択プランになっていることを確認。

### 2. ローカルで Webhook をテスト（Stripe CLI）

ローカル開発サーバー（既定ポート 9000）に Webhook を転送する:

```bash
stripe listen --forward-to localhost:9000/api/billing/webhook
```

- 起動時に表示される `whsec_...` をローカル `.env` の `STRIPE_WEBHOOK_SECRET` に設定する。
- Checkout を実行すると、転送されたイベントで DB が同期されることを確認できる。

### 3. 支払い失敗 → past_due 確認

1. 支払い失敗用テストカードで契約 / 更新を試す:
   - カード番号: `4000 0000 0000 0002`（汎用 decline）
2. `invoice.payment_failed` イベントが届くと、該当 Company の `subscription_status` が `past_due` になることを `GET /api/billing/subscription` で確認。

---

## 本番（Live）移行

Test と Live はキー・Price・Webhook がすべて **別物** です。Test の値を本番に流用しないこと。

1. Dashboard を **Live mode** に切り替える。
2. Live の **Secret key**（`sk_live_...`）を取得。
3. Live mode で **Product / 月額 Price を 4 つ再作成**（Test の Price ID は Live では使えない）。
4. Live mode で **Customer Portal を再設定・保存**（Step 4 を Live 側でもう一度）。
5. Live mode で **Webhook endpoint を再登録**（同じ URL）し、新しい **Live signing secret**（`whsec_...`）を取得。
6. Render の env を Live 値へ差し替える:
   - `STRIPE_SECRET_KEY` → `sk_live_...`
   - `STRIPE_PRICE_BASIC` / `_STANDARD` / `_PRO` / `_FLAGSHIP` → Live の `price_...`
   - `STRIPE_WEBHOOK_SECRET` → Live の `whsec_...`
   - `APP_BASE_URL` → 本番 URL（既に本番値なら変更不要）
7. Save して再デプロイ後、少額の実カードまたは本番テスト手順で動作確認。

---

## トラブルシュート

| 症状 | 原因 | 対処 |
|------|------|------|
| Portal で "No configuration provided" エラー | Step 4 の Customer Portal 設定を保存していない（同 mode で未保存） | 該当 mode（Test / Live）の Portal 設定画面で 4 プランを登録し **Save** する |
| Webhook が 400（検証失敗） | 署名 secret 不一致。`STRIPE_WEBHOOK_SECRET` が endpoint の signing secret と違う / Test と Live を取り違え | 該当 endpoint の signing secret を再確認し env を更新。ローカルは `stripe listen` が出す secret を使う |
| Checkout が作成できない / 400 「Price が未設定」 | 該当プランの Price ID（`STRIPE_PRICE_*`）が空 | 該当プランの env に Stripe の `price_...` を設定 |
| 決済 UI（契約ボタン）が `/me` に出ない | ログインロールが company_owner でない、または `is_stripe_configured` が偽（`STRIPE_SECRET_KEY` 未設定） | company_owner でログインし、`STRIPE_SECRET_KEY` を設定。`GET /api/billing/subscription` の `stripe_configured` で状態確認 |
| 決済 API が 503「決済は現在利用できません」 | `STRIPE_SECRET_KEY` 未設定 | env に Secret key を設定して再デプロイ |
| 「既にサブスクリプション契約中」409 | 有効な購読がある状態で再度 Checkout を実行 | プラン変更は Billing Portal（「お支払い管理」）から行う |
| Webhook 後もプランが切り替わらない | Price ID とプランの対応がつかない（`PRICE_ID_TO_PLAN` に無い Price） | env の `STRIPE_PRICE_*` が、Stripe で契約に使った Price と一致しているか確認（ログに `unknown price_id` 警告） |
