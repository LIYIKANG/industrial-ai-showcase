"""
auth/router.py
==============
認証・ユーザー管理ルーター。

ページルート（HTML）：
  GET  /login                    → ログインページ
  GET  /change-password          → パスワード変更ページ
  GET  /admin                    → ユーザー管理ページ（admin 専用）

認証 API：
  POST /auth/login               → ログイン → Access Token + Refresh Cookie
  POST /auth/logout              → ログアウト → Refresh Token 無効化
  POST /auth/refresh             → Refresh Token → 新しい Access Token
  GET  /auth/me                  → 現在のユーザー情報

パスワード API：
  POST /auth/change-password     → パスワード変更（要認証）

管理者 API（super_admin / company_owner 必須）：
  GET    /api/admin/users            → ユーザー一覧
  POST   /api/admin/users            → ユーザー作成
  PUT    /api/admin/users/{user_id}  → ユーザー更新
  DELETE /api/admin/users/{user_id}  → ユーザー無効化
  GET    /api/admin/audit-logs       → 監査ログ（super_admin のみ）
  GET    /api/admin/usage/summary    → 利用サマリー（super_admin のみ）
"""

from datetime import datetime, timezone
from typing import List, Optional

from fastapi import APIRouter, Cookie, Depends, HTTPException, Request, Response, status
from fastapi.responses import HTMLResponse, RedirectResponse
from fastapi.templating import Jinja2Templates
from sqlalchemy import extract, func
from sqlalchemy.orm import Session

from auth import dependencies as deps
from auth import service
from auth.models import ALL_PAGES, AuditLog, Company, ProcessingJob, User
from auth.schemas import (
    CompanyCreate,
    CompanyOut,
    CompanyUpdate,
    LoginRequest,
    PasswordChange,
    TokenResponse,
    UserCreate,
    UserOut,
    UserTransferRequest,
    UserUpdate,
)
from core.config import BASE_DIR, settings
from core.database import get_db
from core.quota import (
    check_user_quota_or_429,
    get_company_active_user_count,
    get_company_monthly_pages,
    month_window_jst,
)

router = APIRouter()
templates = Jinja2Templates(directory=str(BASE_DIR / "templates"))

_REFRESH_COOKIE = "refresh_token"


def _client_ip(request: Request) -> str:
    """リアル IP（Nginx X-Real-IP 対応）を取得する。"""
    ip = (
        request.headers.get("X-Real-IP")
        or request.headers.get("X-Forwarded-For", "")
        or (request.client.host if request.client else "unknown")
    )
    return ip.split(",")[0].strip() or "unknown"


def _set_refresh_cookie(response: Response, token: str) -> None:
    response.set_cookie(
        key=_REFRESH_COOKIE,
        value=token,
        httponly=True,
        secure=settings.is_production,  # 本番のみ HTTPS 必須
        samesite="lax",
        max_age=settings.JWT_REFRESH_EXPIRE_DAYS * 86400,
        path="/auth",  # /auth/* にのみ送信（余分なルートへの漏洩防止）
    )


def _filter_root_invisible(query, current_user, model=User):
    """current_user が root_admin でなければ root_admin 行を全て隠す。"""
    if current_user.role != "root_admin":
        query = query.filter(model.role != "root_admin")
    return query


def _root_usernames(db: Session) -> set:
    """root_admin 全ユーザーの username 集合（監査ログ / ジョブの username 列フィルタ用）。"""
    return {
        u.username
        for u in db.query(User).filter(User.role == "root_admin").all()
    }


def _user_to_out(db: Session, user: User) -> UserOut:
    """UserOut に company_name を補完して返す共通ヘルパー。"""
    item = UserOut.model_validate(user)
    if user.company_id:
        c = db.query(Company).filter(Company.id == user.company_id).first()
        item.company_name = c.name if c else None
    else:
        item.company_name = None
    return item


def _validate_password(password: str) -> None:
    """パスワード強度チェック（8 文字以上・大文字・小文字・数字）。"""
    errors = []
    if len(password) < 8:
        errors.append("8文字以上")
    if not any(c.isupper() for c in password):
        errors.append("大文字を含む")
    if not any(c.islower() for c in password):
        errors.append("小文字を含む")
    if not any(c.isdigit() for c in password):
        errors.append("数字を含む")
    if errors:
        raise HTTPException(
            status_code=400,
            detail=f"パスワードの要件を満たしていません：{'、'.join(errors)}。",
        )


# ── ページルート ───────────────────────────────────────────────────────────────

@router.get("/login", response_class=HTMLResponse)
async def login_page(request: Request):
    return templates.TemplateResponse(request=request, name="login.html")


@router.get("/change-password", response_class=HTMLResponse)
async def change_password_page(request: Request):
    return templates.TemplateResponse(request=request, name="change_password.html")


@router.get("/admin", response_class=HTMLResponse)
async def admin_page(request: Request):
    return templates.TemplateResponse(request=request, name="admin_users.html")


@router.get("/admin/companies", response_class=HTMLResponse)
async def admin_companies_page(request: Request):
    return templates.TemplateResponse(request=request, name="admin_companies.html")


# ── 認証 API ──────────────────────────────────────────────────────────────────

@router.post("/auth/login", response_model=TokenResponse)
async def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Session = Depends(get_db),
):
    ip = _client_ip(request)

    # ロックアウト確認
    if service.is_locked(db, body.username):
        service.add_audit(db, body.username, "login_blocked", ip)
        raise HTTPException(
            status_code=status.HTTP_429_TOO_MANY_REQUESTS,
            detail=(
                f"ログイン試行が上限を超えました。"
                f"{settings.LOGIN_LOCKOUT_MINUTES}分後に再試行してください。"
            ),
        )

    user = service.get_user(db, body.username)

    if not user or not service.verify_password(body.password, user.hashed_password):
        service.record_attempt(db, body.username, ip, success=False)
        service.add_audit(db, body.username, "login_failed", ip)
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="ユーザー名またはパスワードが正しくありません。",
        )

    if not user.is_active:
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail="このアカウントは無効です。管理者にお問い合わせください。",
        )

    service.record_attempt(db, body.username, ip, success=True)
    service.add_audit(db, body.username, "login_success", ip)

    access_token = service.create_access_token(user.username)
    refresh_token, _ = service.create_refresh_token(user.username)
    _set_refresh_cookie(response, refresh_token)

    return TokenResponse(
        access_token=access_token,
        expires_in=settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
        must_change_password=user.must_change_password,
    )


@router.post("/auth/refresh", response_model=TokenResponse)
async def refresh_token(
    request: Request,
    response: Response,
    refresh_token: Optional[str] = Cookie(default=None),
    db: Session = Depends(get_db),
):
    if not refresh_token:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="リフレッシュトークンがありません。")

    payload = service.decode_token(refresh_token)
    if not payload or payload.get("type") != "refresh":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="無効なリフレッシュトークンです。")

    jti = payload.get("jti")
    if jti and service.is_blacklisted(db, jti):
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="トークンは無効化されています。再度ログインしてください。",
        )

    username = payload.get("sub")
    user = service.get_user(db, username) if username else None
    if not user or not user.is_active:
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="ユーザーが見つかりません。")

    # 旧 Refresh Token をブラックリスト登録（リフレッシュローテーション）
    exp = payload.get("exp")
    if jti and exp:
        service.blacklist_jti(
            db, jti,
            datetime.fromtimestamp(exp, tz=timezone.utc).replace(tzinfo=None),
        )

    new_access = service.create_access_token(user.username)
    new_refresh, _ = service.create_refresh_token(user.username)
    _set_refresh_cookie(response, new_refresh)

    return TokenResponse(
        access_token=new_access,
        expires_in=settings.JWT_ACCESS_EXPIRE_MINUTES * 60,
        must_change_password=user.must_change_password,
    )


@router.post("/auth/logout")
async def logout(
    response: Response,
    refresh_token: Optional[str] = Cookie(default=None),
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(get_db),
):
    if refresh_token:
        payload = service.decode_token(refresh_token)
        if payload:
            jti = payload.get("jti")
            exp = payload.get("exp")
            if jti and exp:
                service.blacklist_jti(
                    db, jti,
                    datetime.fromtimestamp(exp, tz=timezone.utc).replace(tzinfo=None),
                )
    response.delete_cookie(key=_REFRESH_COOKIE, path="/auth")
    service.add_audit(db, current_user.username, "logout", "")
    return {"message": "ログアウトしました。"}


@router.get("/me")
async def me_page(request: Request):
    """マイページ（利用状況・ログ確認）。"""
    return templates.TemplateResponse(request=request, name="me.html")


@router.get("/auth/me", response_model=UserOut)
async def me(current_user: User = Depends(deps.get_current_user)):
    return current_user


@router.get("/api/me/company-usage")
async def my_company_usage(
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(get_db),
):
    """ログインユーザー所属 Company のクォータと当月使用状況を返す。

    super_admin は Company に紐付かないので全項目 null。
    """
    if current_user.role in ("super_admin", "root_admin") or current_user.company_id is None:
        return {
            "company_id": None,
            "company_name": None,
            "max_users": None,
            "max_monthly_pages": None,
            "active_user_count": None,
            "monthly_pages_used": None,
            "month_start_jst": None,
        }
    company = db.query(Company).filter(Company.id == current_user.company_id).first()
    if company is None:
        raise HTTPException(status_code=404, detail="所属 Company が見つかりません。")
    start, _ = month_window_jst()
    return {
        "company_id": company.id,
        "company_name": company.name,
        "max_users": company.max_users,
        "max_monthly_pages": company.max_monthly_pages,
        "active_user_count": get_company_active_user_count(db, company.id),
        "monthly_pages_used": get_company_monthly_pages(db, company.id),
        "month_start_jst": start.isoformat() + "Z",
    }


@router.get("/api/me/usage/summary")
async def my_usage_summary(
    year: int,
    month: int,
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(get_db),
):
    """ログインユーザー自身の月次利用サマリーを返す。"""
    row = db.query(
        func.count(ProcessingJob.id).label("job_count"),
        func.sum(ProcessingJob.page_count).label("total_pages"),
        func.sum(ProcessingJob.fields_filled).label("total_filled"),
        func.sum(ProcessingJob.fields_total).label("total_total"),
        func.max(ProcessingJob.created_at).label("last_used"),
    ).filter(
        ProcessingJob.username == current_user.username,
        extract("year", ProcessingJob.created_at) == year,
        extract("month", ProcessingJob.created_at) == month,
    ).first()

    if not row or not row.job_count:
        return {"job_count": 0, "total_pages": 0, "avg_accuracy": 0, "last_used": None}

    accuracy = round(row.total_filled / row.total_total * 100, 1) if row.total_total else 0
    return {
        "job_count": row.job_count,
        "total_pages": row.total_pages or 0,
        "avg_accuracy": accuracy,
        "last_used": row.last_used.isoformat() + "Z" if row.last_used else None,
    }


@router.get("/api/me/usage/jobs")
async def my_usage_jobs(
    year: int,
    month: int,
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(get_db),
):
    """ログインユーザー自身の月次ジョブ一覧を返す。"""
    jobs = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.username == current_user.username,
            extract("year", ProcessingJob.created_at) == year,
            extract("month", ProcessingJob.created_at) == month,
        )
        .order_by(ProcessingJob.created_at.desc())
        .all()
    )
    return [
        {
            "file_id": j.file_id,
            "filename": j.filename,
            "page_count": j.page_count,
            "fields_filled": j.fields_filled,
            "fields_total": j.fields_total,
            "accuracy": round(j.fields_filled / j.fields_total * 100, 1) if j.fields_total else 0,
            "source_type": j.source_type or "",
            "created_at": j.created_at.isoformat() + "Z",
        }
        for j in jobs
    ]


@router.get("/api/me/activity")
async def my_activity(
    limit: int = 50,
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(get_db),
):
    """ログインユーザー自身の監査ログを返す。"""
    logs = (
        db.query(AuditLog)
        .filter(AuditLog.username == current_user.username)
        .order_by(AuditLog.created_at.desc())
        .limit(min(limit, 200))
        .all()
    )
    return [
        {
            "action": log.action,
            "detail": log.detail,
            "created_at": log.created_at.isoformat() + "Z",
        }
        for log in logs
    ]


@router.post("/auth/change-password")
async def change_password(
    body: PasswordChange,
    current_user: User = Depends(deps.get_current_user),
    db: Session = Depends(get_db),
):
    if not service.verify_password(body.current_password, current_user.hashed_password):
        raise HTTPException(status_code=400, detail="現在のパスワードが正しくありません。")
    _validate_password(body.new_password)
    if body.current_password == body.new_password:
        raise HTTPException(status_code=400, detail="新しいパスワードは現在のパスワードと異なる必要があります。")
    service.update_user(db, current_user, password=body.new_password, must_change_password=False)
    return {"message": "パスワードを変更しました。"}


# ── 管理者 API ────────────────────────────────────────────────────────────────

@router.get("/api/admin/users", response_model=List[UserOut])
async def list_users(
    company_id: Optional[int] = None,
    admin: User = Depends(deps.require_company_admin),
    db: Session = Depends(get_db),
):
    users = service.list_users(db)
    # root_admin 不可視フィルタ（root_admin 自身以外は root_admin 行を見られない）
    if admin.role != "root_admin":
        users = [u for u in users if u.role != "root_admin"]
    if admin.role in ("super_admin", "root_admin"):
        if company_id is not None:
            users = [u for u in users if u.company_id == company_id]
    else:
        # company_owner: 自分の Company のユーザーのみ
        users = [u for u in users if u.company_id == admin.company_id]
    # company_id -> company_name のマップを作成して各ユーザーに付与する
    # （N+1 を避けるため一括取得 → _user_to_out と同等の出力にマップを当てる）
    company_ids = {u.company_id for u in users if u.company_id is not None}
    name_map: dict = {}
    if company_ids:
        for c in db.query(Company).filter(Company.id.in_(company_ids)).all():
            name_map[c.id] = c.name
    result: List[UserOut] = []
    for u in users:
        item = UserOut.model_validate(u)
        item.company_name = name_map.get(u.company_id) if u.company_id else None
        result.append(item)
    return result


@router.post("/api/admin/users", response_model=UserOut, status_code=201)
async def create_user(
    body: UserCreate,
    admin: User = Depends(deps.require_company_admin),
    db: Session = Depends(get_db),
):
    if service.get_user(db, body.username):
        raise HTTPException(status_code=400, detail="このユーザー名は既に使用されています。")
    _validate_password(body.password)
    if body.role not in ("root_admin", "super_admin", "company_owner", "company_member"):
        raise HTTPException(status_code=400, detail="無効なロールです。")
    # root_admin の作成は root_admin のみ可能
    if body.role == "root_admin" and admin.role != "root_admin":
        raise HTTPException(
            status_code=403,
            detail="root_admin の作成はrootユーザーのみ可能です。",
        )
    # role 作成権限: company_owner は company_member のみ作成可
    if admin.role == "company_owner" and body.role != "company_member":
        raise HTTPException(
            status_code=403,
            detail="company_owner が作成できるのは company_member のみです。",
        )
    if body.role == "super_admin" and admin.role not in ("super_admin", "root_admin"):
        raise HTTPException(status_code=403, detail="権限が不足しています。")
    # allowed_pages のバリデーション
    allowed_pages = body.allowed_pages
    if allowed_pages is not None:
        invalid = [p for p in allowed_pages if p not in ALL_PAGES]
        if invalid:
            raise HTTPException(status_code=400, detail=f"無効なページ指定: {', '.join(invalid)}")

    # company_id の確定とバリデーション
    if admin.role in ("super_admin", "root_admin"):
        if body.role in ("super_admin", "root_admin"):
            # super_admin / root_admin を作成する場合、company_id は指定不可
            if body.company_id is not None:
                raise HTTPException(
                    status_code=400,
                    detail=f"{body.role} 作成時に company_id は指定できません。",
                )
            company_id = None
        else:
            # それ以外のロールを作成する場合、company_id は必須
            if body.company_id is None:
                raise HTTPException(
                    status_code=400,
                    detail="このロールの作成には company_id が必須です。",
                )
            company_id = body.company_id
    else:
        # company_owner: body.company_id は無視、自分の company_id を強制
        company_id = admin.company_id

    # 対象 Company の存在・有効性チェック（company_id がある場合）
    if company_id is not None:
        target_company = db.query(Company).filter(Company.id == company_id).first()
        if target_company is None:
            raise HTTPException(status_code=404, detail="指定された Company が存在しません。")
        if not target_company.is_active:
            raise HTTPException(status_code=403, detail="指定された Company は無効です。")

    # ユーザー数クォータチェック（super_admin / root_admin は Company に紐付かないので除外）
    if body.role not in ("super_admin", "root_admin") and company_id is not None:
        check_user_quota_or_429(db, company_id)
    new_user = service.create_user(
        db, body.username, body.display_name, body.password, body.role,
        allowed_pages=allowed_pages,
        company_id=company_id,
    )
    return _user_to_out(db, new_user)


@router.put("/api/admin/users/{user_id}", response_model=UserOut)
async def update_user(
    user_id: int,
    body: UserUpdate,
    admin: User = Depends(deps.require_company_admin),
    db: Session = Depends(get_db),
):
    user = service.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
    # root_admin は root_admin にしか見えない → 非 root の操作は 404 で隠蔽
    if user.role == "root_admin" and admin.role != "root_admin":
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
    if user.role == "super_admin" and admin.role not in ("super_admin", "root_admin"):
        raise HTTPException(status_code=403, detail="権限が不足しています。")
    # company_owner はテナント外のユーザーを操作不可
    if admin.role == "company_owner":
        if user.company_id != admin.company_id:
            raise HTTPException(status_code=403, detail="権限が不足しています。")
        # company_owner は同一テナント内の company_owner を変更不可（super_admin のみ可）
        if user.role == "company_owner" and user.id != admin.id:
            raise HTTPException(status_code=403, detail="権限が不足しています。")

    updates: dict = {}
    if body.display_name is not None:
        updates["display_name"] = body.display_name
    if body.role is not None:
        if body.role not in ("root_admin", "super_admin", "company_owner", "company_member"):
            raise HTTPException(status_code=400, detail="無効なロールです。")
        if body.role == "root_admin" and admin.role != "root_admin":
            raise HTTPException(status_code=403, detail="権限が不足しています。")
        if body.role == "super_admin" and admin.role not in ("super_admin", "root_admin"):
            raise HTTPException(status_code=403, detail="権限が不足しています。")
        # company_owner はロール変更不可（company_member 以外への変更も、company_member への変更も不許可）
        if admin.role == "company_owner":
            raise HTTPException(status_code=403, detail="ロール変更権限がありません。")
        updates["role"] = body.role
    if body.is_active is not None:
        if not body.is_active and user.id == admin.id:
            raise HTTPException(status_code=400, detail="自分自身を無効化することはできません。")
        updates["is_active"] = body.is_active
    if body.password is not None:
        _validate_password(body.password)
        updates["password"] = body.password
        updates["must_change_password"] = True  # 管理者リセット後は必ず変更を要求
    if body.allowed_pages is not None:
        invalid = [p for p in body.allowed_pages if p not in ALL_PAGES]
        if invalid:
            raise HTTPException(status_code=400, detail=f"無効なページ指定: {', '.join(invalid)}")
        updates["allowed_pages"] = body.allowed_pages

    updated = service.update_user(db, user, **updates)
    return _user_to_out(db, updated)


@router.delete("/api/admin/users/{user_id}")
async def delete_user(
    user_id: int,
    admin: User = Depends(deps.require_company_admin),
    db: Session = Depends(get_db),
):
    user = service.get_user_by_id(db, user_id)
    if not user:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
    # root_admin は root_admin にしか見えない → 非 root の操作は 404 で隠蔽
    if user.role == "root_admin" and admin.role != "root_admin":
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
    if user.id == admin.id:
        raise HTTPException(status_code=400, detail="自分自身を削除することはできません。")
    if user.role == "super_admin":
        raise HTTPException(status_code=403, detail="権限が不足しています。")
    # root_admin の削除は root_admin のみ可（ここに到達する root_admin は admin=root_admin のみ）
    # company_owner はテナント外のユーザーを操作不可、また同一テナントの company_owner を削除不可
    if admin.role == "company_owner":
        if user.company_id != admin.company_id:
            raise HTTPException(status_code=403, detail="権限が不足しています。")
        if user.role == "company_owner":
            raise HTTPException(status_code=403, detail="権限が不足しています。")
    service.update_user(db, user, is_active=False)
    service.add_audit(db, admin.username, "user_deactivated", "", detail=f"target={user.username}")
    return {"message": f"ユーザー {user.username} を無効化しました。"}


@router.patch("/api/admin/users/{user_id}/transfer", response_model=UserOut)
def admin_transfer_user(
    user_id: int,
    body: UserTransferRequest,
    admin: User = Depends(deps.require_super_admin),
    db: Session = Depends(get_db),
):
    """super_admin が user を別 Company に移動する。
    - super_admin ユーザーは Company に属さないため移動不可（400）
    - 同じ Company の場合は no-op（200）
    - 移動先が存在しない → 404 / 無効 → 403
    - 移動先の max_users に達している → 409（target が is_active のときのみ）
    - 元 Company の最後の active company_owner は移動不可 → 409
    - 過去の ProcessingJob.company_id は変更しない（履歴境界保持）
    """
    target = service.get_user_by_id(db, user_id)
    if not target:
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
    # root_admin は非 root には不可視。アクセス時は 404 で隠蔽
    if target.role == "root_admin" and admin.role != "root_admin":
        raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
    if target.role == "root_admin":
        raise HTTPException(
            status_code=400,
            detail="root_admin はCompanyに所属しないため移動できません。",
        )
    if target.role == "super_admin":
        raise HTTPException(
            status_code=400,
            detail="super_admin はCompanyに所属しないため移動できません。",
        )
    if target.company_id == body.company_id:
        # no-op
        return _user_to_out(db, target)
    dest = db.query(Company).filter(Company.id == body.company_id).first()
    if not dest:
        raise HTTPException(status_code=404, detail="移動先Companyが存在しません。")
    if not dest.is_active:
        raise HTTPException(status_code=403, detail="移動先Companyは無効です。")
    # 移動先の user quota（is_active な対象のみカウントに影響）
    if target.is_active and get_company_active_user_count(db, dest.id) >= dest.max_users:
        raise HTTPException(
            status_code=409,
            detail=(
                f"移動先Company '{dest.name}' のユーザー数上限 "
                f"({dest.max_users}) に達しています。"
            ),
        )
    # 元 Company の最後の active company_owner ガード
    if target.role == "company_owner" and target.company_id is not None:
        remaining_owners = (
            db.query(User)
            .filter(
                User.company_id == target.company_id,
                User.role == "company_owner",
                User.id != target.id,
                User.is_active == True,  # noqa: E712
            )
            .count()
        )
        if remaining_owners == 0:
            raise HTTPException(
                status_code=409,
                detail="このユーザーは元 Company の最後の company_owner です。移動できません。",
            )
    old_company_id = target.company_id
    target.company_id = body.company_id
    target.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(target)
    service.add_audit(
        db, admin.username, "user_transfer", "",
        detail=(
            f"user_id={user_id} from_company={old_company_id} "
            f"to_company={body.company_id}"
        ),
    )
    return _user_to_out(db, target)


# ── Company 管理 API（super_admin のみ） ───────────────────────────────────────

@router.post("/api/admin/companies", response_model=CompanyOut, status_code=201)
def admin_create_company(
    body: CompanyCreate,
    admin: User = Depends(deps.require_super_admin),
    db: Session = Depends(get_db),
):
    existing = db.query(Company).filter(Company.name == body.name).first()
    if existing:
        raise HTTPException(409, "同名のCompanyが既に存在します。")
    try:
        c = service.create_company(
            db,
            name=body.name,
            plan=body.plan,
            max_users=body.max_users,
            max_monthly_pages=body.max_monthly_pages,
            contract_started_at=body.contract_started_at,
        )
    except ValueError as e:
        raise HTTPException(400, str(e))
    service.add_audit(db, admin.username, "company_create", "", f"name={body.name}")
    return c


@router.get("/api/admin/companies")
def admin_list_companies(
    admin: User = Depends(deps.require_super_admin),
    db: Session = Depends(get_db),
):
    items = []
    for c in service.list_companies(db):
        items.append({
            "id": c.id,
            "name": c.name,
            "plan": c.plan,
            "max_users": c.max_users,
            "max_monthly_pages": c.max_monthly_pages,
            "is_active": c.is_active,
            "contract_started_at": c.contract_started_at.isoformat() + "Z" if c.contract_started_at else None,
            "active_user_count": get_company_active_user_count(db, c.id),
            "monthly_pages_used": get_company_monthly_pages(db, c.id),
            "created_at": c.created_at.isoformat() + "Z",
        })
    return items


@router.get("/api/admin/companies/{company_id}")
def admin_get_company(
    company_id: int,
    admin: User = Depends(deps.require_super_admin),
    db: Session = Depends(get_db),
):
    c = service.get_company(db, company_id)
    if not c:
        raise HTTPException(404, "Company not found")
    return {
        "id": c.id,
        "name": c.name,
        "plan": c.plan,
        "max_users": c.max_users,
        "max_monthly_pages": c.max_monthly_pages,
        "is_active": c.is_active,
        "contract_started_at": c.contract_started_at.isoformat() + "Z" if c.contract_started_at else None,
        "active_user_count": get_company_active_user_count(db, c.id),
        "monthly_pages_used": get_company_monthly_pages(db, c.id),
        "created_at": c.created_at.isoformat() + "Z",
    }


@router.patch("/api/admin/companies/{company_id}", response_model=CompanyOut)
def admin_update_company(
    company_id: int,
    body: CompanyUpdate,
    admin: User = Depends(deps.require_super_admin),
    db: Session = Depends(get_db),
):
    c = service.get_company(db, company_id)
    if not c:
        raise HTTPException(404, "Company not found")
    updates = body.model_dump(exclude_unset=True)
    try:
        c = service.update_company(db, c, **updates)
    except ValueError as e:
        raise HTTPException(400, str(e))
    service.add_audit(db, admin.username, "company_update", "", f"id={company_id} updates={list(updates.keys())}")
    return c


@router.get("/api/admin/plans")
def admin_list_plans(admin: User = Depends(deps.require_super_admin)):
    from core.config import PLAN_LIMITS
    return [
        {"plan": p, "max_users": v["max_users"], "max_monthly_pages": v["max_monthly_pages"]}
        for p, v in PLAN_LIMITS.items()
    ]


@router.get("/api/admin/audit-logs")
async def get_audit_logs(
    limit: int = 100,
    username: Optional[str] = None,
    admin: User = Depends(deps.require_super_admin),
    db: Session = Depends(get_db),
):
    query = db.query(AuditLog)
    # root_admin の username は root_admin 以外には完全に不可視
    if admin.role != "root_admin":
        root_usernames = _root_usernames(db)
        if root_usernames:
            query = query.filter(AuditLog.username.notin_(root_usernames))
            for uname in root_usernames:
                query = query.filter(~AuditLog.detail.contains(uname))
    if admin.role not in ("super_admin", "root_admin"):
        super_admin_usernames = [
            u.username for u in db.query(User).filter(User.role == "super_admin").all()
        ]
        if super_admin_usernames:
            query = query.filter(AuditLog.username.notin_(super_admin_usernames))
            for uname in super_admin_usernames:
                query = query.filter(~AuditLog.detail.contains(uname))
    if username:
        query = query.filter(AuditLog.username == username)
    query = query.filter(AuditLog.action != "login_failed")
    logs = query.order_by(AuditLog.created_at.desc()).limit(min(limit, 500)).all()
    return [
        {
            "id": log.id,
            "username": log.username,
            "action": log.action,
            "ip_address": log.ip_address,
            "detail": log.detail,
            "created_at": log.created_at.isoformat() + "Z",
        }
        for log in logs
    ]


@router.get("/api/admin/usage/summary")
async def get_usage_summary(
    year: int,
    month: int,
    admin: User = Depends(deps.require_super_admin),
    db: Session = Depends(get_db),
):
    """ユーザー別月次利用サマリーを返す。"""
    query = db.query(
        ProcessingJob.username,
        func.count(ProcessingJob.id).label("job_count"),
        func.sum(ProcessingJob.page_count).label("total_pages"),
        func.sum(ProcessingJob.fields_filled).label("total_filled"),
        func.sum(ProcessingJob.fields_total).label("total_total"),
        func.max(ProcessingJob.created_at).label("last_used"),
    ).filter(
        extract("year", ProcessingJob.created_at) == year,
        extract("month", ProcessingJob.created_at) == month,
    )
    if admin.role != "root_admin":
        root_usernames = _root_usernames(db)
        if root_usernames:
            query = query.filter(ProcessingJob.username.notin_(root_usernames))
    if admin.role not in ("super_admin", "root_admin"):
        super_admin_usernames = [
            u.username for u in db.query(User).filter(User.role == "super_admin").all()
        ]
        if super_admin_usernames:
            query = query.filter(ProcessingJob.username.notin_(super_admin_usernames))
    rows = query.group_by(ProcessingJob.username).all()
    result = []
    for r in rows:
        accuracy = (
            round(r.total_filled / r.total_total * 100, 1) if r.total_total else 0
        )
        result.append({
            "username": r.username,
            "job_count": r.job_count,
            "total_pages": r.total_pages or 0,
            "avg_accuracy": accuracy,
            "last_used": r.last_used.isoformat() + "Z" if r.last_used else None,
        })
    return result


@router.get("/api/admin/usage/detail")
async def get_usage_detail(
    username: str,
    year: int,
    month: int,
    admin: User = Depends(deps.require_company_admin),
    db: Session = Depends(get_db),
):
    """ユーザーの月次ジョブ明細を返す。"""
    # root_admin の明細は root_admin 以外には不可視（404 で隠蔽）
    if admin.role != "root_admin":
        root_usernames = _root_usernames(db)
        if username in root_usernames:
            raise HTTPException(status_code=404, detail="ユーザーが見つかりません。")
    # company_admin は super_admin のデータを参照不可
    if admin.role not in ("super_admin", "root_admin"):
        super_admin_usernames = [
            u.username for u in db.query(User).filter(User.role == "super_admin").all()
        ]
        if username in super_admin_usernames:
            raise HTTPException(status_code=403, detail="権限が不足しています。")

    jobs = (
        db.query(ProcessingJob)
        .filter(
            ProcessingJob.username == username,
            extract("year", ProcessingJob.created_at) == year,
            extract("month", ProcessingJob.created_at) == month,
        )
        .order_by(ProcessingJob.created_at.desc())
        .all()
    )
    return [
        {
            "file_id": j.file_id,
            "filename": j.filename,
            "page_count": j.page_count,
            "fields_filled": j.fields_filled,
            "fields_total": j.fields_total,
            "accuracy": (
                round(j.fields_filled / j.fields_total * 100, 1) if j.fields_total else 0
            ),
            "source_type": j.source_type,
            "created_at": j.created_at.isoformat() + "Z",
        }
        for j in jobs
    ]
