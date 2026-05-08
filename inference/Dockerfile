ARG CUDA_VERSION=12.8.1
FROM nvidia/cuda:${CUDA_VERSION}-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEVICE=auto
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
ARG TORCH_VERSION=2.7.1
ARG TORCHVISION_VERSION=0.22.1
ARG INSTALL_TENSORRT=false
ARG TENSORRT_VERSION=10.16.1.11
ARG UBUNTU_MIRROR=http://archive.ubuntu.com/ubuntu

# Install system dependencies for OpenCV and image processing
RUN sed -i "s|http://archive.ubuntu.com/ubuntu|${UBUNTU_MIRROR}|g; s|http://azure.archive.ubuntu.com/ubuntu|${UBUNTU_MIRROR}|g" /etc/apt/sources.list && \
    apt-get update -o Acquire::Retries=10 && apt-get install -y --no-install-recommends --fix-missing \
    ca-certificates \
    python3 \
    python3-pip \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    && ln -sf /usr/bin/python3 /usr/local/bin/python \
    && rm -rf /var/lib/apt/lists/*

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

WORKDIR /app

# Install Python dependencies
COPY requirements.txt .

# Install CUDA-capable PyTorch by default. Override TORCH_INDEX_URL at build time
# if your NVIDIA driver requires a different CUDA wheel family.
RUN echo "Installing torch=${TORCH_VERSION} torchvision=${TORCHVISION_VERSION} from ${TORCH_INDEX_URL} on CUDA image ${CUDA_VERSION}"
RUN pip install --no-cache-dir \
    "torch==${TORCH_VERSION}" \
    "torchvision==${TORCHVISION_VERSION}" \
    --index-url ${TORCH_INDEX_URL} \
    --extra-index-url https://pypi.org/simple

# Install remaining dependencies (ultralytics, sahi, etc.)
RUN pip install --no-cache-dir \
    -r requirements.txt

# TensorRT export/runtime support is enabled for GPU builds. Engines are still
# generated on the target GPU host because TensorRT plans are hardware/runtime specific.
RUN if [ "$INSTALL_TENSORRT" = "true" ]; then \
        pip install --no-cache-dir --extra-index-url https://pypi.nvidia.com \
            onnx onnxslim onnxruntime-gpu "tensorrt_cu12==${TENSORRT_VERSION}"; \
    fi

COPY . .

EXPOSE 8001

CMD ["python", "serve.py"]
