# ============================================================
# deploy/gunicorn.conf.py — Gunicorn 生产环境配置
#
# 启动命令（在项目根目录执行）：
#   APP_ENV=production gunicorn -c deploy/gunicorn.conf.py app:app
#
# 或通过 systemd 自动管理（推荐），见 deploy/pdf-analyzer.service
# ============================================================

import multiprocessing
import os

# ── 绑定地址 ──────────────────────────────────────────────────
# 只监听本机回环，由 Nginx 反向代理对外暴露
bind = "127.0.0.1:9000"

# ── Worker 配置 ───────────────────────────────────────────────
# uvicorn worker 支持 FastAPI 的异步特性
worker_class = "uvicorn.workers.UvicornWorker"

# CPU 核数 × 2 + 1 是常用经验值；小型服务器建议固定为 2~4
workers = int(os.getenv("GUNICORN_WORKERS", multiprocessing.cpu_count() * 2 + 1))

# 每个 worker 的并发线程数（UvicornWorker 用协程，threads 设为 1 即可）
threads = 1

# ── 超时 ──────────────────────────────────────────────────────
# PDF + AI 处理耗时较长，设置宽松超时（秒）
timeout = int(os.getenv("GUNICORN_TIMEOUT", 300))
keepalive = 5

# ── 进程管理 ──────────────────────────────────────────────────
# 由 systemd 管理进程，不需要 Gunicorn 自己 daemonize
daemon = False
pidfile = None

# ── 日志 ─────────────────────────────────────────────────────
# "-" 表示输出到 stdout，由 systemd/journald 统一收集
accesslog = "-"
errorlog = "-"
loglevel = os.getenv("LOG_LEVEL", "info").lower()
access_log_format = '%(h)s "%(r)s" %(s)s %(b)s %(D)sμs'

# ── 安全 ─────────────────────────────────────────────────────
# 限制请求体大小（字节），与 MAX_FILE_SIZE_MB 对应，留一定余量
limit_request_body = int(os.getenv("MAX_FILE_SIZE_MB", 50)) * 1024 * 1024 * 2
