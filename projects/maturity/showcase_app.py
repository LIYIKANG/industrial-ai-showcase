"""Standalone local assessment; answers and reports stay in the browser."""
from pathlib import Path
from fastapi import FastAPI
from fastapi.staticfiles import StaticFiles

app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

@app.get('/health')
def health():
    return {'ok': True, 'mode': 'local-rules', 'questionnaire': 'website-ai-readiness-v1'}

@app.middleware('http')
async def private_browser_assets(request, call_next):
    response = await call_next(request)
    response.headers['Cache-Control'] = 'no-cache'
    response.headers['Referrer-Policy'] = 'no-referrer'
    response.headers['X-Content-Type-Options'] = 'nosniff'
    # This module has no remote model, tracking script, or data submission endpoint.
    response.headers['Content-Security-Policy'] = "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; img-src 'self' data:; connect-src 'self'; object-src 'none'; base-uri 'none'; form-action 'none'"
    return response

app.mount('/', StaticFiles(directory=Path(__file__).parent / 'web', html=True), name='assessment')
