FROM nvidia/cuda:12.8.1-cudnn-runtime-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEVICE=auto
ENV MMROTATE_MODEL_DIR=/models
ENV MMROTATE_CONFIG=/opt/mmrotate/configs/oriented_rcnn/oriented-rcnn-le90_r50_fpn_1x_dota.py
ENV MMROTATE_CHECKPOINT=/models/oriented_rcnn_r50_fpn_1x_dota_le90-6d2b2ce0.pth
ENV MODEL_VERSION=mmrotate-oriented-rcnn-dota

ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu128
ARG TORCH_VERSION=2.9.1+cu128
ARG TORCHVISION_VERSION=0.24.1+cu128
ARG MMENGINE_VERSION=0.10.5
ARG MMCV_WHEEL_URL=https://download.openmmlab.com/mmcv/dist/cu117/torch2.0.0/mmcv-2.0.0-cp310-cp310-manylinux1_x86_64.whl
ARG MMDET_VERSION=3.1.0
ARG MMROTATE_REF=dev-1.x

RUN apt-get update && apt-get install -y --no-install-recommends \
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
    && rm -rf /var/lib/apt/lists/* \
    && ln -sf /usr/bin/python3 /usr/local/bin/python

WORKDIR /app

RUN pip install --no-cache-dir --upgrade pip setuptools wheel

RUN pip install --no-cache-dir \
    torch==${TORCH_VERSION} \
    torchvision==${TORCHVISION_VERSION} \
    --index-url ${TORCH_INDEX_URL}

COPY requirements.txt .
RUN pip install --no-cache-dir --timeout 1000 --retries 10 -r requirements.txt

# Install openmmlab stack with versions that satisfy all constraints without patching:
#   mmdet 3.1.0 requires mmcv>=2.0.0rc4, <2.1.0
#   mmrotate 1.0.0rc1 requires mmcv>=2.0.0rc4, <=2.1.0
#   mmcv 2.0.0 satisfies both
RUN pip install --no-cache-dir "numpy<2" "mmengine==${MMENGINE_VERSION}" \
    && pip install --no-cache-dir "${MMCV_WHEEL_URL}" \
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
