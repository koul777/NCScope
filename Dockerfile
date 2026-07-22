FROM node:22-bookworm-slim

ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV PORT=8000
ENV VIRTUAL_ENV=/opt/venv
ENV PATH="/opt/venv/bin:$PATH"

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates python3 python3-venv python3-pip \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv "$VIRTUAL_ENV"

COPY requirements.txt package.json package-lock.json ./
RUN pip install --no-cache-dir -r requirements.txt \
    && npm ci --omit=dev

COPY . .

EXPOSE 8000

CMD ["sh", "-c", "uvicorn app.main:app --host 0.0.0.0 --port ${PORT:-8000}"]
