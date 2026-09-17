"""Local-only showcase supervisor. All project paths are relative to this file."""
from __future__ import annotations
import concurrent.futures
import fcntl
import hashlib
import json
import os
from pathlib import Path
import secrets
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
import webbrowser

BASE = Path(__file__).resolve().parent
RUNTIME = BASE / 'runtime'
RUNTIME.mkdir(exist_ok=True)
STATE = RUNTIME / 'session.json'
BIND_HOST = os.getenv('SHOWCASE_BIND_HOST', '127.0.0.1')
PORT_START = int(os.getenv('SHOWCASE_PORT', '8760'))
FIXED_PORTS = os.getenv('SHOWCASE_FIXED_PORTS') == '1'
PUBLIC_PORT_START = int(os.getenv('SHOWCASE_PUBLIC_PORT', str(PORT_START)))

if '--stop' in sys.argv:
    try:
        state = json.loads(STATE.read_text())
        req = urllib.request.Request(state['url'] + '/api/shutdown', data=b'{}',
            headers={'X-Showcase-Token':state['token'], 'Content-Type':'application/json'})
        urllib.request.urlopen(req, timeout=5).read()
        print('已请求停止本展示中心。')
    except Exception:
        print('展示中心当前未运行。')
    sys.exit(0)

lock = (RUNTIME / 'launch.lock').open('w')
try:
    fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
except BlockingIOError:
    try:
        state = json.loads(STATE.read_text())
        webbrowser.open(state['url'])
        print('展示中心已经在运行：' + state['url'])
    except Exception:
        print('展示中心正在启动，请稍候。')
    sys.exit(0)

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse
from fastapi.staticfiles import StaticFiles
import uvicorn

catalog = json.loads((BASE / 'catalog.json').read_text())
token = secrets.token_urlsafe(32)
stopping = threading.Event()
process_lock = threading.Lock()
processes = {}
logfiles = {}
statuses = {}
ports = set()

def free_port(preferred):
    for port in range(preferred, preferred + (1 if FIXED_PORTS else 200)):
        if port in ports:
            continue
        with socket.socket() as sock:
            # Match Uvicorn's reuse behavior so recently stopped demo ports can
            # be reused without treating TIME_WAIT connections as live servers.
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
            try:
                sock.bind((BIND_HOST, port))
            except OSError:
                continue
        ports.add(port)
        return port
    raise RuntimeError('没有可用的本地端口')

portal_port = free_port(PORT_START)
url = f'http://127.0.0.1:{portal_port}'
for index, service in enumerate(catalog['services'], 1):
    service['port'] = free_port(portal_port + index)
    service['origin'] = f"http://127.0.0.1:{service['port']}"
    service['url'] = service['origin'] + service['path']
    statuses[service['id']] = {'state':'starting','message':'正在启动'}

def start_service(service):
    sid = service['id']
    env = os.environ.copy()
    env.update({'PYTHONUNBUFFERED':'1', 'SHOWCASE_LOCAL':'1',
        'OMP_NUM_THREADS':'2', 'OPENBLAS_NUM_THREADS':'2', 'POLARS_MAX_THREADS':'2',
        'STREAMLIT_BROWSER_GATHER_USAGE_STATS':'false'})
    if service['kind'] == 'uvicorn':
        cmd = [sys.executable, '-m', 'uvicorn', service['entry'], '--host', BIND_HOST,
               '--port', str(service['port']), '--log-level', 'warning']
    else:
        cmd = [sys.executable, '-m', 'streamlit', 'run', service['entry'],
               f'--server.address={BIND_HOST}', f"--server.port={service['port']}",
               '--server.headless=true', '--browser.gatherUsageStats=false',
               '--server.fileWatcherType=none', '--server.enableStaticServing=false']
    if sid in logfiles:
        logfiles[sid].close()
    logfiles[sid] = (RUNTIME / f'{sid}.log').open('a')
    logfiles[sid].write('\n--- 展示中心启动 ---\n')
    logfiles[sid].flush()
    processes[sid] = subprocess.Popen(cmd, cwd=BASE / service['cwd'], env=env,
        stdout=logfiles[sid], stderr=subprocess.STDOUT, start_new_session=True)
    statuses[sid] = {'state':'starting','message':'正在启动'}

def check_service(service):
    sid = service['id']
    proc = processes.get(sid)
    if not proc or proc.poll() is not None:
        return sid, {'state':'error','message':'服务未运行，可重试或查看日志'}
    try:
        with urllib.request.urlopen(service['origin'] + service['health'], timeout=1.5) as resp:
            ready = resp.status == 200
        return sid, {'state':'ready' if ready else 'starting','message':'运行中' if ready else '正在准备'}
    except Exception:
        return sid, {'state':'starting','message':'正在准备数据或等待服务响应'}

def monitor():
    with concurrent.futures.ThreadPoolExecutor(max_workers=max(1, len(catalog['services']))) as pool:
        while not stopping.is_set():
            statuses.update(dict(pool.map(check_service, catalog['services'])))
            stopping.wait(3)

app = FastAPI(docs_url=None, redoc_url=None)
app.mount('/assets', StaticFiles(directory=BASE / 'portal'), name='assets')
app.mount('/media', StaticFiles(directory=BASE / 'media'), name='media')

@app.middleware('http')
async def prevent_stale_portal(request: Request, call_next):
    response = await call_next(request)
    if request.url.path == '/' or request.url.path.startswith(('/assets/', '/api/')):
        # Local upgrades must not mix a new page with scripts from an older release.
        response.headers['Cache-Control'] = 'no-store, max-age=0'
        response.headers['Pragma'] = 'no-cache'
    return response

@app.get('/')
def home():
    portal = BASE / 'portal'
    html = (portal / 'index.html').read_text(encoding='utf-8')
    for name in ('app.js', 'style.css'):
        version = hashlib.sha256((portal / name).read_bytes()).hexdigest()[:16]
        html = html.replace(f'/assets/{name}', f'/assets/{name}?v={version}')
    return HTMLResponse(html)

@app.get('/api/status')
def status(request: Request):
    services = []
    for index, s in enumerate(catalog['services'], 1):
        public = {k:v for k,v in s.items() if k not in ('cwd','entry')} | statuses[s['id']]
        if FIXED_PORTS:
            # iframe URLs must refer to the browser's host, not container-local localhost.
            host = request.url.hostname or '127.0.0.1'
            if ':' in host:
                host = '[' + host + ']'
            public['port'] = PUBLIC_PORT_START + index
            public['origin'] = f'{request.url.scheme}://{host}:{public["port"]}'
            public['url'] = public['origin'] + s['path']
        services.append(public)
    return {'agents':catalog['agents'], 'services':services, 'token':token,
            'url':str(request.base_url).rstrip('/')}

@app.get('/api/health')
def health():
    from fastapi.responses import JSONResponse
    snapshot = {sid: info['state'] for sid, info in statuses.items()}
    ready = all(value == 'ready' for value in snapshot.values())
    return JSONResponse({'ok':ready, 'services':snapshot}, status_code=200 if ready else 503)

DEMO_MATERIALS = {
    'document-sample.pdf': {'agent': 'document', 'name': '单据演示样例.pdf',
                          'source': 'projects/anbicheng/demo-inputs/单据演示样例.pdf'},
    'energy-sample.csv': {'agent': 'energy', 'name': '能耗模拟数据.csv',
                       'source': 'projects/mvr/mvr_soft_sensor_demo/data/sample_mvr_data.csv'},
}

@app.get('/api/materials')
def materials():
    # Client-facing downloads use an explicit allowlist. Original business documents
    # and recordings stay on disk for a separate redaction review.
    return [{'agent': item['agent'], 'name': item['name'], 'path': key,
             'size': (BASE / item['source']).stat().st_size,
             'type': Path(key).suffix[1:].upper()}
            for key, item in DEMO_MATERIALS.items()]

@app.get('/materials/{path:path}')
def material(path: str):
    item = DEMO_MATERIALS.get(path)
    if item is None:
        raise HTTPException(404)
    return FileResponse(BASE / item['source'], filename=item['name'])

def authorize(request):
    if request.headers.get('X-Showcase-Token') != token:
        raise HTTPException(403)
    origin = request.headers.get('origin')
    if origin and origin.rstrip('/') != str(request.base_url).rstrip('/'):
        raise HTTPException(403)

@app.get('/api/logs/{sid}')
def logs(sid: str):
    if sid not in statuses:
        raise HTTPException(404)
    p = RUNTIME / f'{sid}.log'
    return {'text':p.read_text(errors='replace')[-7000:] if p.exists() else '暂无日志'}

@app.post('/api/restart/{sid}')
def restart(sid: str, request: Request):
    authorize(request)
    service = next((s for s in catalog['services'] if s['id'] == sid), None)
    if not service:
        raise HTTPException(404)
    with process_lock:
        proc = processes.get(sid)
        if proc and proc.poll() is None:
            os.killpg(proc.pid, signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(proc.pid, signal.SIGKILL)
                proc.wait(timeout=3)
        start_service(service)
    return {'ok':True}

@app.post('/api/shutdown')
def shutdown(request: Request):
    authorize(request)
    threading.Timer(0.4, lambda: setattr(server, 'should_exit', True)).start()
    return {'ok':True}

def cleanup():
    stopping.set()
    with process_lock:
        for proc in processes.values():
            if proc.poll() is None:
                try:
                    os.killpg(proc.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
        for proc in processes.values():
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        for f in logfiles.values():
            f.close()
    STATE.unlink(missing_ok=True)

if __name__ == '__main__':
    try:
        STATE.write_text(json.dumps({'url':url,'token':token,'pid':os.getpid()}, ensure_ascii=False))
        STATE.chmod(0o600)
        for service in catalog['services']:
            start_service(service)
        threading.Thread(target=monitor, daemon=True).start()
        print(f"\n项目演示中心：{url}\n{len(catalog['services'])} 个系统正在同时启动。按 Control+C 可全部停止。\n", flush=True)
        if '--no-browser' not in sys.argv:
            threading.Timer(2, lambda: webbrowser.open(url)).start()
        server = uvicorn.Server(uvicorn.Config(app, host=BIND_HOST, port=portal_port, log_level='warning'))
        server.run()
    finally:
        cleanup()
