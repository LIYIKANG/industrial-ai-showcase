#!/usr/bin/env bash
set -Eeuo pipefail

APP_NAME="${APP_NAME:-pdf-analyzer}"
APP_USER="${APP_USER:-pdf-analyzer}"
APP_DIR="${APP_DIR:-/var/www/pdf-analyzer}"
SERVER_NAME="${SERVER_NAME:-_}"
BASE_URL="${BASE_URL:-}"
PYTHON_BIN="${PYTHON_BIN:-python3}"
CLIENT_MAX_BODY_SIZE_MB="${CLIENT_MAX_BODY_SIZE_MB:-110}"
ENABLE_HTTPS="${ENABLE_HTTPS:-0}"
CERTBOT_EMAIL="${CERTBOT_EMAIL:-}"

if [[ "${EUID}" -ne 0 ]]; then
  echo "Run this script with sudo/root."
  echo "Example: sudo APP_USER=pdf-analyzer SERVER_NAME=example.com CLAUDE_API_KEY=... INIT_ADMIN_PASSWORD=... bash deploy/setup_ubuntu.sh"
  exit 1
fi

SOURCE_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ ! -f "${SOURCE_DIR}/app.py" || ! -f "${SOURCE_DIR}/requirements.txt" ]]; then
  echo "Could not find project root from ${SOURCE_DIR}."
  exit 1
fi

if [[ -z "${BASE_URL}" ]]; then
  if [[ "${SERVER_NAME}" == "_" ]]; then
    BASE_URL="http://localhost:9000"
  elif [[ "${ENABLE_HTTPS}" == "1" ]]; then
    BASE_URL="https://${SERVER_NAME}"
  else
    BASE_URL="http://${SERVER_NAME}"
  fi
fi

echo "==> Installing OS packages"
apt-get update
DEBIAN_FRONTEND=noninteractive apt-get install -y \
  build-essential \
  curl \
  nginx \
  python3 \
  python3-pip \
  python3-venv \
  rsync

if [[ "${ENABLE_HTTPS}" == "1" ]]; then
  DEBIAN_FRONTEND=noninteractive apt-get install -y certbot python3-certbot-nginx
fi

if ! "${PYTHON_BIN}" -c 'import sys; raise SystemExit(sys.version_info < (3, 10))'; then
  echo "${PYTHON_BIN} must be Python 3.10 or newer because the pinned FastAPI version requires it."
  echo "Install a newer Python and rerun with PYTHON_BIN=python3.10 or PYTHON_BIN=python3.11."
  exit 1
fi

echo "==> Ensuring app user ${APP_USER}"
if ! id "${APP_USER}" >/dev/null 2>&1; then
  useradd --system --create-home --user-group --shell /usr/sbin/nologin "${APP_USER}"
fi
APP_GROUP="$(id -gn "${APP_USER}")"

echo "==> Syncing project to ${APP_DIR}"
mkdir -p "${APP_DIR}"
SOURCE_REAL="$(realpath "${SOURCE_DIR}")"
APP_REAL="$(realpath "${APP_DIR}")"
if [[ "${SOURCE_REAL}" != "${APP_REAL}" ]]; then
  rsync -a "${SOURCE_DIR}/" "${APP_DIR}/" \
    --exclude ".git/" \
    --exclude ".venv/" \
    --exclude "venv/" \
    --exclude ".env" \
    --exclude ".env.*" \
    --exclude "__pycache__/" \
    --exclude "*.pyc" \
    --exclude ".DS_Store" \
    --exclude "output/"
fi

mkdir -p \
  "${APP_DIR}/data" \
  "${APP_DIR}/data/user_templates" \
  "${APP_DIR}/output" \
  "${APP_DIR}/output/customer_exports"
chown -R "${APP_USER}:${APP_GROUP}" "${APP_DIR}"

echo "==> Creating Python virtualenv"
runuser -u "${APP_USER}" -- "${PYTHON_BIN}" -m venv "${APP_DIR}/.venv"
runuser -u "${APP_USER}" -- "${APP_DIR}/.venv/bin/python" -m pip install --upgrade pip wheel setuptools
runuser -u "${APP_USER}" -- "${APP_DIR}/.venv/bin/python" -m pip install -r "${APP_DIR}/requirements.txt"

ENV_FILE="${APP_DIR}/.env.production"
if [[ ! -f "${ENV_FILE}" ]]; then
  if [[ -z "${CLAUDE_API_KEY:-}" ]]; then
    echo "CLAUDE_API_KEY is required when creating ${ENV_FILE}."
    exit 1
  fi
  if [[ -z "${INIT_ADMIN_PASSWORD:-}" ]]; then
    echo "INIT_ADMIN_PASSWORD is required when creating ${ENV_FILE}."
    exit 1
  fi

  JWT_SECRET_VALUE="${JWT_SECRET:-$("${APP_DIR}/.venv/bin/python" -c 'import secrets; print(secrets.token_hex(32))')}"
  CORS_VALUE="${CORS_ORIGINS:-${BASE_URL}}"
  DATABASE_VALUE="${DATABASE_URL:-sqlite:///${APP_DIR}/data/app.db}"

  echo "==> Creating ${ENV_FILE}"
  install -m 600 -o "${APP_USER}" -g "${APP_GROUP}" /dev/null "${ENV_FILE}"
  {
    echo "APP_ENV=production"
    echo "CLAUDE_API_KEY=${CLAUDE_API_KEY}"
    echo "CLAUDE_MODEL=${CLAUDE_MODEL:-claude-sonnet-4-6}"
    echo "CLAUDE_CONCURRENCY=${CLAUDE_CONCURRENCY:-2}"
    echo "JWT_SECRET=${JWT_SECRET_VALUE}"
    echo "INIT_ADMIN_USERNAME=${INIT_ADMIN_USERNAME:-admin}"
    echo "INIT_ADMIN_PASSWORD=${INIT_ADMIN_PASSWORD}"
    echo "INIT_ADMIN_DISPLAY_NAME=${INIT_ADMIN_DISPLAY_NAME:-Admin}"
    echo "DATABASE_URL=${DATABASE_VALUE}"
    echo "APP_BASE_URL=${BASE_URL}"
    echo "CORS_ORIGINS=${CORS_VALUE}"
    echo "OUTPUT_DIR=${APP_DIR}/output"
    echo "CUSTOMER_EXPORT_DIR=${APP_DIR}/output/customer_exports"
    echo "TEMPLATE_UPLOAD_DIR=${APP_DIR}/data/user_templates"
    echo "CUSTOMER_KEYWORD_CONFIG_PATH=config/customer_keywords.json"
    echo "CUSTOMER_PRODUCT_SEED_FILE=data/customer_product_database_seed.json"
    echo "CUSTOMER_MAX_UPLOAD_FILES=${CUSTOMER_MAX_UPLOAD_FILES:-20}"
    echo "CUSTOMER_MAX_CONCURRENT_JOBS=${CUSTOMER_MAX_CONCURRENT_JOBS:-2}"
    echo "CUSTOMER_POLL_INTERVAL_SECONDS=${CUSTOMER_POLL_INTERVAL_SECONDS:-2}"
    echo "MAX_FILE_SIZE_MB=${MAX_FILE_SIZE_MB:-50}"
    echo "LOG_LEVEL=${LOG_LEVEL:-INFO}"
  } > "${ENV_FILE}"
  chown "${APP_USER}:${APP_GROUP}" "${ENV_FILE}"
  chmod 600 "${ENV_FILE}"
else
  echo "==> Keeping existing ${ENV_FILE}"
fi

echo "==> Initializing database"
cd "${APP_DIR}"
runuser -u "${APP_USER}" -- env APP_ENV=production "${APP_DIR}/.venv/bin/python" scripts/init_db.py

echo "==> Installing systemd service"
cat > "/etc/systemd/system/${APP_NAME}.service" <<EOF
[Unit]
Description=PDF Analyzer AI - FastAPI Application
After=network.target

[Service]
User=${APP_USER}
Group=${APP_GROUP}
WorkingDirectory=${APP_DIR}
Environment="APP_ENV=production"
ExecStart=${APP_DIR}/.venv/bin/gunicorn -c deploy/gunicorn.conf.py app:app
Restart=on-failure
RestartSec=5s
StandardOutput=journal
StandardError=journal

[Install]
WantedBy=multi-user.target
EOF

systemctl daemon-reload
systemctl enable "${APP_NAME}"
systemctl restart "${APP_NAME}"

echo "==> Installing Nginx site"
cat > "/etc/nginx/sites-available/${APP_NAME}" <<EOF
limit_req_zone \$binary_remote_addr zone=pdf_upload:10m rate=10r/m;

server {
    listen 80;
    server_name ${SERVER_NAME};

    client_max_body_size ${CLIENT_MAX_BODY_SIZE_MB}m;

    location /static/ {
        alias ${APP_DIR}/static/;
        expires 7d;
        add_header Cache-Control "public, immutable";
    }

    location /api/ {
        limit_req zone=pdf_upload burst=5 nodelay;

        proxy_pass         http://127.0.0.1:9000;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout    310s;
        proxy_connect_timeout 10s;
        proxy_send_timeout    60s;
    }

    location / {
        proxy_pass         http://127.0.0.1:9000;
        proxy_http_version 1.1;
        proxy_set_header   Host              \$host;
        proxy_set_header   X-Real-IP         \$remote_addr;
        proxy_set_header   X-Forwarded-For   \$proxy_add_x_forwarded_for;
        proxy_set_header   X-Forwarded-Proto \$scheme;
        proxy_read_timeout 310s;
    }
}
EOF

ln -sfn "/etc/nginx/sites-available/${APP_NAME}" "/etc/nginx/sites-enabled/${APP_NAME}"
nginx -t
systemctl enable nginx
systemctl reload nginx || systemctl restart nginx

if [[ "${ENABLE_HTTPS}" == "1" ]]; then
  if [[ "${SERVER_NAME}" == "_" ]]; then
    echo "ENABLE_HTTPS=1 requires SERVER_NAME to be a real domain."
    exit 1
  fi
  if [[ -z "${CERTBOT_EMAIL}" ]]; then
    echo "ENABLE_HTTPS=1 requires CERTBOT_EMAIL."
    exit 1
  fi
  certbot --nginx \
    --non-interactive \
    --agree-tos \
    --redirect \
    -m "${CERTBOT_EMAIL}" \
    -d "${SERVER_NAME}"
  nginx -t
  systemctl reload nginx
fi

echo "==> Checking service"
systemctl --no-pager --full status "${APP_NAME}" || true
curl -fsS "http://127.0.0.1:9000/health"
echo
curl -fsS "http://127.0.0.1/health"
echo

echo "Deployment complete."
echo "Local app: http://127.0.0.1:9000"
echo "Public app: ${BASE_URL}"
