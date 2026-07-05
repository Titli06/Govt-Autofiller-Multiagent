FROM python:3.11-slim

# Tesseract is the fallback OCR engine for clean typed documents; curl backs the
# container healthcheck.
RUN apt-get update && apt-get install -y --no-install-recommends \
    tesseract-ocr \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app
# The app package must be present for the editable install to discover it (packages.find).
COPY pyproject.toml .
COPY app ./app
RUN pip install --no-cache-dir -e ".[dev]"

COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
