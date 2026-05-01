# nvidia/cuda:11.3.1-cudnn8-devel-ubuntu20.04 gives us:
#   - Ubuntu 20.04 (Python 3.8 default, no stale NVIDIA apt keys)
#   - nvcc 11.3 already on PATH for mmcv compilation
#   - No GPG/repo issues
FROM nvidia/cuda:11.3.1-cudnn8-devel-ubuntu20.04

ENV DEBIAN_FRONTEND=noninteractive
ENV PYTHONDONTWRITEBYTECODE=1
ENV PYTHONUNBUFFERED=1
ENV DEVICE=auto
ENV TRANSFORMERS_OFFLINE=1
ENV HF_HUB_OFFLINE=1

ARG LAE_DINO_REPO=https://github.com/jaychempan/LAE-DINO.git
ARG LAE_DINO_SHA=44726020759dd5e72fd05d5e5c1059619ead2284
ARG LAE_DINO_CHECKPOINT=lae_dino_swint_lae1m-28ca3a15.pth

RUN apt-get update && apt-get install -y --no-install-recommends \
        git \
        wget \
        ca-certificates \
        python3.8 \
        python3.8-dev \
        python3-pip \
        libgl1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libxrender-dev \
        libgomp1 \
    && ln -sf /usr/bin/python3.8 /usr/local/bin/python3 \
    && ln -sf /usr/bin/python3.8 /usr/local/bin/python \
    && rm -rf /var/lib/apt/lists/*

# Upgrade pip and install torch 1.10.0+cu113 (same as LAE-DINO training env)
RUN pip3 install --no-cache-dir --upgrade pip setuptools wheel \
    && pip3 install --no-cache-dir \
        torch==1.10.0+cu113 \
        torchvision==0.11.1+cu113 \
        --extra-index-url https://download.pytorch.org/whl/cu113

WORKDIR /opt

RUN git clone ${LAE_DINO_REPO} LAE-DINO \
    && cd LAE-DINO \
    && git checkout ${LAE_DINO_SHA}

RUN pip install --no-cache-dir -U openmim \
    && mim install "mmengine>=0.10.0" \
    && mim install "mmcv>=2.0.0,<2.2.0"

WORKDIR /opt/LAE-DINO/mmdetection_lae
RUN pip install --no-cache-dir -v -e .

# pycocoevalcap (eval-only) pulls puccinialin which has no py3.8 wheel.
# Install only the inference-needed multimodal deps.
RUN pip install --no-cache-dir fairscale jsonlines nltk transformers emoji \
    && pip install --no-cache-dir "git+https://github.com/openai/CLIP.git" \
    && pip install --no-cache-dir open-clip-torch

RUN pip install --no-cache-dir "huggingface_hub[cli]"

# Bake weights at build time — container is fully offline at runtime
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
# Ensure mmdet editable install is importable regardless of which site-packages
# uvicorn workers activate. The editable .egg-link is registered under the pip
# that was active during build; PYTHONPATH covers any path divergence.
ENV PYTHONPATH=/opt/LAE-DINO/mmdetection_lae

EXPOSE 8001

CMD ["python", "serve.py"]
