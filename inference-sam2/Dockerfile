FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04

# Prevent interactive prompts
ENV DEBIAN_FRONTEND=noninteractive

# Install system dependencies
RUN apt-get update && apt-get install -y \
    python3.10 \
    python3.10-dev \
    python3-pip \
    git \
    wget \
    ffmpeg \
    libsm6 \
    libxext6 \
    && rm -rf /var/lib/apt/lists/*

# Set python3 as default
RUN ln -sf /usr/bin/python3.10 /usr/bin/python \
    && ln -sf /usr/bin/python3.10 /usr/bin/python3

WORKDIR /app

# Upgrade pip and install torch first as instructed
RUN pip install --no-cache-dir torch torchvision torchaudio --index-url https://download.pytorch.org/whl/cu124 \
    && pip uninstall -y nvidia-cudnn-cu12

# GPU Compilation constraints
ENV TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0+PTX"
ENV FORCE_CUDA="1"

# Clone and install SAM 2
RUN git clone https://github.com/facebookresearch/sam2.git /opt/sam2 \
    && cd /opt/sam2 \
    && pip install -e .

# Install FastAPI and other requirements
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy application code
COPY . .

# Environment settings
ENV PORT=8001
ENV HOST=0.0.0.0

EXPOSE 8001

CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8001"]
