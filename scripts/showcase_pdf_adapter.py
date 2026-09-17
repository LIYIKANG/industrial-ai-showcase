"""Standalone local-demo entrypoint, copied into each PDF application.

The original routes, validation, business rules, persistence and exports are reused.
Only the extraction stage has a clearly labelled local text mode by default.
"""
import json
import os
from pathlib import Path
import secrets

ROOT = Path(__file__).resolve().parent
os.environ['APP_ENV'] = 'showcase'
os.environ['DATABASE_URL'] = 'sqlite:///' + str(ROOT / 'data' / 'showcase.db')
os.environ.setdefault('CLAUDE_API_KEY', 'local-text-demo-no-cloud-key')
secret_file = ROOT / 'data' / '.showcase-secret'
secret_file.parent.mkdir(exist_ok=True)
if not secret_file.exists():
    secret_file.write_text(secrets.token_hex(32))
    secret_file.chmod(0o600)
os.environ['JWT_SECRET'] = secret_file.read_text().strip()
os.environ['JWT_ACCESS_EXPIRE_MINUTES'] = '1440'

from core.config import settings
from core.database import Base, SessionLocal, engine
from auth.models import Company
from auth.service import create_user, get_user, create_access_token

# A separate local demonstration database; original customer/account data is untouched.
Base.metadata.create_all(bind=engine)
with SessionLocal() as db:
    company = db.query(Company).filter(Company.name == '展示样例').first()
    if not company:
        company = Company(name='展示样例', plan='internal', max_users=9999,
                          max_monthly_pages=99999999, is_active=True)
        db.add(company)
        db.commit()
        db.refresh(company)
    if not get_user(db, 'showcase'):
        create_user(db, username='showcase', display_name='本地演示',
                    password=secrets.token_urlsafe(24), role='company_member',
                    must_change_password=False, company_id=company.id)

from app import app
from api import customer_routes
from fastapi import Request, HTTPException
from fastapi.responses import HTMLResponse, FileResponse

cloud = os.getenv('SHOWCASE_USE_CLOUD') == '1'
if not cloud:
    import fitz
    from core.customer_keyword_parser import parse_customer_text
    from core.customer_line_item_parser import normalize_customer_line_items

    async def local_extract(filename, contents, fields_json, config):
        if not filename.lower().endswith('.pdf'):
            raise ValueError('本地演示模式支持带文字层的 PDF；扫描件和图片请配置 AI 服务后使用。')
        with fitz.open(stream=contents, filetype='pdf') as doc:
            text = '\n'.join(page.get_text() for page in doc)
            pages = len(doc)
        if not text.strip():
            raise ValueError('此 PDF 没有文字层。本地模式无法识别扫描件，请使用配套示例 PDF 或配置 AI 服务。')
        parsed = parse_customer_text(text, config)
        normalized = parsed['normalized']
        if not any(v not in ('', None) for v in normalized.values()):
            raise ValueError('未发现配置中的字段。本地演示支持“SKU: …”等标签格式，请先使用配套示例 PDF；复杂版式需 AI 服务。')
        notes = {k:'本地 PDF 文字层 + 关键词规则解析（未调用大模型）' for k in normalized}
        raw_items = []
        if normalized.get('internal_sku'):
            raw_items.append({'sku':normalized['internal_sku'],
                'product_code':normalized['internal_sku'],
                'material_special':normalized.get('material_special',''),
                'hardness':normalized.get('hardness',''), 'color':normalized.get('color',''),
                'tax_included_price':normalized.get('tax_included_price',''),
                'moq':normalized.get('moq',''), 'note':'本地关键词演示解析'})
        items = normalize_customer_line_items(raw_items, source=filename, config=config)
        return filename, pages, normalized, notes, items

    customer_routes._extract_one_file = local_extract

@app.middleware("http")
async def refresh_demo_assets(request, call_next):
    response = await call_next(request)
    if request.url.path in ('/', '/showcase') or request.url.path.startswith('/static/'):
        response.headers['Cache-Control'] = 'no-store, max-age=0'
    return response

@app.get('/showcase/health')
def showcase_health():
    return {'ok':True, 'mode':'cloud' if cloud else 'local-text'}

@app.get('/showcase/sample')
def showcase_sample():
    return FileResponse(ROOT.parent / 'demo-inputs' / '单据演示样例.pdf', media_type='application/pdf')

@app.get('/showcase')
def showcase_login(request: Request):
    if os.getenv('SHOWCASE_DOCKER') != '1' and request.client.host not in ('127.0.0.1','::1'):
        raise HTTPException(403)
    token = create_access_token('showcase')
    return HTMLResponse('<script>sessionStorage.setItem("access_token", ' + json.dumps(token) + '); location.replace("/");</script>')

# Allow a long-running demonstration to renew its local session without a password.
@app.get('/showcase/token')
def showcase_token(request: Request):
    if os.getenv('SHOWCASE_DOCKER') != '1' and request.client.host not in ('127.0.0.1','::1'):
        raise HTTPException(403)
    return {'access_token':create_access_token('showcase'), 'mode':'cloud' if cloud else 'local-text'}
