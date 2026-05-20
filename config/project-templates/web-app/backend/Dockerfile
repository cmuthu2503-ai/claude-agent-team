# Backend for {{PROJECT_NAME}} — FastAPI on Python 3.12.
FROM python:3.12-slim

WORKDIR /app

# System deps: only what FastAPI + uvicorn need. Keep slim — agents can
# add anything they need by editing requirements.txt.
RUN apt-get update \
 && apt-get install -y --no-install-recommends curl \
 && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app

EXPOSE 8000

# --reload keeps editing snappy; the bind-mount in docker-compose lets
# you change app/main.py and see the reload. For production builds,
# drop --reload and add `--workers N`.
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]
