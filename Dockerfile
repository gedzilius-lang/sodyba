FROM python:3.12-slim

ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1
WORKDIR /srv

COPY backend/requirements.txt /srv/requirements.txt
RUN pip install --no-cache-dir -r /srv/requirements.txt

COPY backend /srv/backend
COPY frontend /srv/frontend

ENV SR_DATA_DIR=/srv/data SR_FRONTEND_DIR=/srv/frontend
RUN useradd -u 10001 -m radar && mkdir -p /srv/data && chown -R radar /srv/data
USER radar

EXPOSE 8000
CMD ["uvicorn", "backend.app.main:app", "--host", "0.0.0.0", "--port", "8000"]
