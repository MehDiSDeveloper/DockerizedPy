#!/bin/sh
set -e
python -m app.scripts.bootstrap_db
# Guarantees the operator account named by SEED_ADMIN_* exists (creating
# or promoting it). Idempotent, and a no-op when SEED_ADMIN_EMAIL is unset.
python -m app.scripts.seed_admin
# --proxy-headers makes uvicorn trust the X-Forwarded-Proto Liara's TLS
# terminator sends. Without it every url_for() renders an http:// URL onto an
# https:// page and the browser blocks the CSS and JS as mixed content.
exec uvicorn app.main:app --host 0.0.0.0 --port 8000 \
     --proxy-headers --forwarded-allow-ips='*'
