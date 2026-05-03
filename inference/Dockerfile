FROM python:3.11-slim

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEVICE=auto
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu124
ARG INSTALL_TENSORRT=false

# Install system dependencies for OpenCV and image processing
RUN apt-get update && apt-get install -y \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .

# Install CUDA-capable PyTorch by default. Override TORCH_INDEX_URL at build time
# if your NVIDIA driver requires a different CUDA wheel family.
RUN pip install --no-cache-dir \
    --index-url ${TORCH_INDEX_URL} \
    --extra-index-url https://pypi.org/simple \
    torch torchvision

# Install remaining dependencies (ultralytics, sahi, etc.)
RUN pip install --no-cache-dir \
    -r requirements.txt

# TensorRT export/runtime support is enabled for GPU builds. Engines are still
# generated on the target GPU host because TensorRT plans are hardware/runtime specific.
RUN if [ "$INSTALL_TENSORRT" = "true" ]; then \
        pip install --no-cache-dir --extra-index-url https://pypi.nvidia.com onnx onnxslim onnxruntime-gpu tensorrt; \
    fi

COPY . .

EXPOSE 8001

CMD ["python", "serve.py"]
