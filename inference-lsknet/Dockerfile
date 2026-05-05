FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEVICE=auto
ENV LSKNET_MODEL_DIR=/models
ENV LSKNET_CONFIG=/opt/lsknet/configs/lsknet/lsk_s_fpn_1x_dota_le90.py
ENV LSKNET_CHECKPOINT=/models/lsk_s_fpn_1x_dota_le90_20230116-99749191.pth
ENV MODEL_VERSION=lsknet-s-dota

# GPU Compilation constraints
ENV TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0+PTX"
ENV MMCV_WITH_OPS=1
ENV FORCE_CUDA="1"
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility

# CPU Threading constraints
ENV OMP_NUM_THREADS=1
ENV MKL_NUM_THREADS=1
ENV OPENBLAS_NUM_THREADS=1
ENV VECLIB_MAXIMUM_THREADS=1
ENV NUMEXPR_NUM_THREADS=1

RUN sed -i 's/archive.ubuntu.com/azure.archive.ubuntu.com/g' /etc/apt/sources.list && \
    apt-get update -o Acquire::Retries=10 && \
    apt-get install -y --no-install-recommends --fix-missing \
    python3 \
    python3-pip \
    python3-dev \
    git \
    wget \
    ca-certificates \
    libgl1 \
    libglib2.0-0 \
    libsm6 \
    libxext6 \
    libxrender-dev \
    libgomp1 \
    ninja-build \
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/local/bin/python

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip "setuptools<70" wheel

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
# Install PyTorch for specified CUDA version
RUN pip install --no-cache-dir torch torchvision torchaudio --index-url ${TORCH_INDEX_URL} \
    --extra-index-url https://pypi.org/simple \
    && pip uninstall -y nvidia-cudnn-cu12

COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 1000 --retries 10 -r requirements.txt

# Compile MMCV from source
RUN echo "Force rebuild mmcv 1"
RUN pip install --no-cache-dir "numpy<2" cython ninja \
    && git clone -b v1.7.1 https://github.com/open-mmlab/mmcv.git /opt/mmcv \
    && cd /opt/mmcv \
    && sed -i 's/c++14/c++17/g' setup.py \
    && pip install --no-cache-dir --no-build-isolation -e . \
    && pip install --no-cache-dir "mmdet==2.28.2"

# Clone LSKNet and install it
RUN git clone --depth 1 https://github.com/zcablii/LSKNet.git /opt/lsknet \
    && cd /opt/lsknet \
    && pip install -v -e . \
    && pip install --no-cache-dir "numpy<2" "opencv-python<4.12" "opencv-python-headless<4.12"

RUN mkdir -p /models \
    && wget --continue --tries=20 --timeout=60 --read-timeout=300 -O /models/lsk_s_fpn_1x_dota_le90_20230116-99749191.pth \
    https://download.openmmlab.com/mmrotate/v1.0/lsknet/lsk_s_fpn_1x_dota_le90/lsk_s_fpn_1x_dota_le90_20230116-99749191.pth

RUN pip install --no-cache-dir timm==0.6.13

COPY . .

EXPOSE 8001

CMD ["python", "serve.py"]
