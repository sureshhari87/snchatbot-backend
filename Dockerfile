FROM python:3.11-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    APP_ENV=production \
    HOST=0.0.0.0 \
    PORT=7860 \
    WEB_CONCURRENCY=1 \
    PROXY_HEADERS=1 \
    FORWARDED_ALLOW_IPS=*

WORKDIR /app

COPY requirements.txt ./
RUN python -m pip install --no-cache-dir --upgrade pip \
    && python -m pip install --no-cache-dir -r requirements.txt

COPY . .
RUN sed -i 's/\r$//' scripts/start.sh \
    && chmod +x scripts/start.sh \
    && adduser --system --group app \
    && chown -R app:app /app

USER app

EXPOSE 7860

HEALTHCHECK --interval=30s --timeout=5s --start-period=30s --retries=3 \
    CMD python -c "import json, os, urllib.request; port=os.getenv('PORT', '7860'); data=json.load(urllib.request.urlopen(f'http://127.0.0.1:{port}/health', timeout=3)); raise SystemExit(0 if data.get('status') == 'ok' else 1)"

CMD ["scripts/start.sh"]
