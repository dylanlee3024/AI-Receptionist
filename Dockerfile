FROM python:3.11-slim

# libgomp1 is required by Silero VAD (PyTorch/ONNX uses OpenMP for CPU parallelism).
# Without it the container crashes at import time with libgomp.so.1 not found.
# python:3.11-slim (glibc) is used instead of alpine (musl) because PyTorch
# CPU wheels are not built for musl and will fail to install.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . .

EXPOSE 8080

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8080"]
