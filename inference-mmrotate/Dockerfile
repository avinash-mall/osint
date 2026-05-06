ARG CUDA_VERSION=12.8.1
FROM nvidia/cuda:${CUDA_VERSION}-cudnn-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEVICE=auto
ENV MMROTATE_MODEL_DIR=/models
ENV MMROTATE_CONFIG=/opt/mmrotate/configs/oriented_rcnn/oriented-rcnn-le90_r50_fpn_1x_dota.py
ENV MMROTATE_CHECKPOINT=/models/oriented_rcnn_r50_fpn_1x_dota_le90-6d2b2ce0.pth
ENV MODEL_VERSION=mmrotate-oriented-rcnn-dota

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
ARG TORCH_VERSION=2.7.1
ARG TORCHVISION_VERSION=0.22.1
ARG TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0;12.0+PTX"
ARG MMENGINE_VERSION=0.10.5
ARG MMDET_VERSION=3.1.0
ARG MMROTATE_REF=dev-1.x

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

# Install PyTorch for the selected CUDA wheel index.
RUN pip install --no-cache-dir \
    "torch==${TORCH_VERSION}" \
    "torchvision==${TORCHVISION_VERSION}" \
    --index-url ${TORCH_INDEX_URL} \
    --extra-index-url https://pypi.org/simple \
    && pip uninstall -y nvidia-cudnn-cu12

COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 1000 --retries 10 -r requirements.txt

ENV TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST}
ENV MMCV_WITH_OPS=1
ENV FORCE_CUDA="1"
ENV NVIDIA_VISIBLE_DEVICES=all
ENV NVIDIA_DRIVER_CAPABILITIES=compute,utility
# Limit parallel ninja jobs to prevent OOM crash during CUDA extension compilation
ENV MAX_JOBS=4

RUN echo "Force rebuild mmcv 2"
RUN pip install --no-cache-dir "numpy<2" "mmengine==${MMENGINE_VERSION}" ninja cython \
    && git clone -b v2.0.0 https://github.com/open-mmlab/mmcv.git /opt/mmcv \
    && cd /opt/mmcv \
    && sed -i 's/c++14/c++17/g' setup.py \
    && pip install --no-cache-dir --no-build-isolation -e . \
    && pip install --no-cache-dir "mmdet==${MMDET_VERSION}"

RUN git clone --depth 1 --branch ${MMROTATE_REF} https://github.com/open-mmlab/mmrotate.git /opt/mmrotate \
    && pip install --no-cache-dir -e /opt/mmrotate \
    && pip install --no-cache-dir "numpy<2" "opencv-python<4.12" "opencv-python-headless<4.12"

RUN mkdir -p /models \
    && wget --continue --tries=20 --timeout=60 --read-timeout=300 -O /models/oriented_rcnn_r50_fpn_1x_dota_le90-6d2b2ce0.pth \
    https://download.openmmlab.com/mmrotate/v0.1.0/oriented_rcnn/oriented_rcnn_r50_fpn_1x_dota_le90/oriented_rcnn_r50_fpn_1x_dota_le90-6d2b2ce0.pth

COPY . .

EXPOSE 8001

CMD ["python", "serve.py"]
