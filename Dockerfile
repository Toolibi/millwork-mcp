# Copyright (c) 2026 Prince Boafo. All rights reserved. PolyForm Noncommercial 1.0.0.
# Hosted millwork-mcp for Microsoft Foundry / remote MCP clients.
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1 \
    PORT=8000 MILLWORK_OUTPUT=/tmp/millwork-output

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir "ezdxf>=1.3" "mcp>=1.0,<2" "uvicorn>=0.30"

# code + sample pricing only; real shop pricing is mounted or set at deploy time
COPY LICENSE COMMERCIAL.md ./
COPY millwork_mcp ./millwork_mcp
COPY examples ./examples

# run as an unprivileged user with no write access to the code
RUN useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin millwork
USER 10001

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request,os;urllib.request.urlopen(f'http://127.0.0.1:{os.environ.get(\"PORT\",\"8000\")}/healthz')"
CMD ["python", "-m", "millwork_mcp.http_server"]
