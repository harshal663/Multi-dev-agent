FROM python:3.11-slim

# Install system dependencies including curl for healthchecks and docker CLI if needed
RUN apt-get update && apt-get install -y --no-install-recommends \
    curl \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /srv

# Install Python dependencies first to leverage Docker layer caching
COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code and frontend static files
COPY backend/app ./app
COPY frontend ./frontend

# Ensure Python outputs directly to terminal without buffering
ENV PYTHONUNBUFFERED=1

EXPOSE 8000

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]