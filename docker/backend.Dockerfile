FROM python:3.12-slim

WORKDIR /app
COPY backend/requirements.txt backend/requirements.txt
RUN pip install --no-cache-dir -r backend/requirements.txt

COPY backend ./backend
COPY ml ./ml
COPY data ./data

WORKDIR /app/backend
EXPOSE 8000
# On first boot with an empty DB, run the seeding steps manually or via an entrypoint:
#   python -m app.seed_db && python -m app.services.backfill_db
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
