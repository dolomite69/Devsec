FROM python:3.11-slim

RUN apt-get update && \
    apt-get install -y --no-install-recommends git docker.io && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

# Writable work directory for cloned repos / generated build files.
# Guaranteed to exist regardless of the host platform (Render, plain docker run, etc.).
ENV DOCKERFORGE_WORK_DIR=/app/tmp
RUN mkdir -p /app/tmp

EXPOSE 8000

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8000"]
