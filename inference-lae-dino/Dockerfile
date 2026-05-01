# GPU-capable LAE-DINO image — supports modern NVIDIA GPUs including
# sm_120 (Blackwell, RTX 50-series). Uses CUDA 12.8 + PyTorch 2.7 wheels.
# Upstream LAE-DINO targets torch 1.10+cu113 which only ships kernels up to
# sm_86; this rebuild lifts the stack so we can JIT for newer compute caps.
FROM nvidia/cuda:12.8.1-cudnn-devel-ubuntu22.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEVICE=auto
ENV TRANSFORMERS_OFFLINE=1
ENV HF_HUB_OFFLINE=1

ARG LAE_DINO_REPO=https://github.com/jaychempan/LAE-DINO.git
ARG LAE_DINO_SHA=44726020759dd5e72fd05d5e5c1059619ead2284
ARG LAE_DINO_CHECKPOINT=lae_dino_swint_lae1m-28ca3a15.pth
# Compute caps for mmcv source build. Includes sm_120 + PTX for forward compat.
ARG TORCH_CUDA_ARCH_LIST="7.5;8.0;8.6;8.9;9.0;12.0+PTX"

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        wget \
        ca-certificates \
        python3.10 \
        python3.10-dev \
        python3-pip \
        build-essential \
        ninja-build \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev \
        libgomp1 \
    && ln -sf /usr/bin/python3.10 /usr/local/bin/python3 \
    && ln -sf /usr/bin/python3.10 /usr/local/bin/python \
    && rm -rf /var/lib/apt/lists/*

RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel

# PyTorch 2.7.1 with CUDA 12.8 wheels — ships sm_90 binaries plus PTX
# fall-through. NVIDIA driver JIT-compiles for sm_120 at first launch.
RUN pip3 install --no-cache-dir \
        torch==2.7.1+cu128 \
        torchvision==0.22.1+cu128 \
        --index-url https://download.pytorch.org/whl/cu128

WORKDIR /opt

RUN git clone ${LAE_DINO_REPO} LAE-DINO \
    && cd LAE-DINO \
    && git checkout ${LAE_DINO_SHA}

# mmengine first (pure Python, no CUDA build).
RUN pip install --no-cache-dir -U openmim \
    && mim install "mmengine>=0.10.0"

# mmcv must compile against the installed torch version. No precompiled wheels
# exist for torch 2.7+cu128 yet; force source build with our CUDA arch list.
ENV TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST}
ENV MMCV_WITH_OPS=1
ENV FORCE_CUDA=1
RUN pip install --no-cache-dir --no-build-isolation "mmcv==2.2.0"

WORKDIR /opt/LAE-DINO/mmdetection_lae
RUN pip install --no-cache-dir -v -e .

# pycocoevalcap pulls puccinialin (Python>=3.9) — fine on Python 3.10 now,
# but it's eval-only so we still skip it. Keep the inference-only deps.
RUN pip install --no-cache-dir fairscale jsonlines nltk transformers emoji \
    && pip install --no-cache-dir "git+https://github.com/openai/CLIP.git" \
    && pip install --no-cache-dir open-clip-torch

RUN pip install --no-cache-dir "huggingface_hub[cli]"

RUN HF_HUB_OFFLINE=0 huggingface-cli download \
        --resume-download \
        google-bert/bert-base-uncased \
        --local-dir /opt/LAE-DINO/weights/bert-base-uncased

RUN HF_HUB_OFFLINE=0 huggingface-cli download \
        --resume-download \
        jaychempan/LAE-DINO \
        checkpoints/${LAE_DINO_CHECKPOINT} \
        --local-dir /opt/LAE-DINO/weights

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY . /app

ENV LAE_DINO_ROOT=/opt/LAE-DINO
ENV LAE_DINO_CONFIG=/opt/LAE-DINO/mmdetection_lae/configs/lae_dino/lae_dino_swin-t_pretrain_LAE-1M.py
ENV LAE_DINO_CHECKPOINT=/opt/LAE-DINO/weights/checkpoints/lae_dino_swint_lae1m-28ca3a15.pth
ENV LAE_DINO_BERT_PATH=/opt/LAE-DINO/weights/bert-base-uncased
ENV PYTHONPATH=/opt/LAE-DINO/mmdetection_lae

EXPOSE 8001

CMD ["python", "serve.py"]
