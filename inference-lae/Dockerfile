# inference-lae — LAE-DINO open-vocabulary RS detector (mmdetection fork).
#
# Built against the SAME CUDA / torch the host's GPU profile selects (the
# SAM3_* build args, populated by `python scripts/configure_host.py`), so this
# sidecar runs on the same modern GPUs as the rest of the stack. We deliberately
# do NOT use LAE-DINO's upstream `lae_requirements.txt` (torch 1.10+cu113) —
# that 2021 wheel cannot target H100/Blackwell. mmcv is compiled from source
# against the installed torch via `mim`, which is the #1 thing to validate on a
# real GPU host. See docs/inference/lae-dino-sidecar.md.

ARG CUDA_VERSION=12.6.3
ARG UBUNTU_VERSION=22.04
FROM nvidia/cuda:${CUDA_VERSION}-devel-ubuntu${UBUNTU_VERSION}

ENV DEBIAN_FRONTEND=noninteractive \
    PIP_BREAK_SYSTEM_PACKAGES=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates git wget curl \
        libgl1 libglib2.0-0 libgomp1 libsm6 libxext6 \
        python3 python3-dev python3-venv python3-pip \
    && ln -sf /usr/bin/python3 /usr/bin/python \
    && rm -rf /var/lib/apt/lists/*

RUN python -m pip install --no-cache-dir --upgrade pip "setuptools<70" wheel

# Torch matched to the host GPU profile (same args the inference-sam3 image uses).
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu126
ARG TORCH_VERSION=2.7.1
ARG TORCHVISION_VERSION=0.22.1
RUN python -m pip install --no-cache-dir \
        "torch==${TORCH_VERSION}" "torchvision==${TORCHVISION_VERSION}" \
        --index-url ${TORCH_INDEX_URL} --extra-index-url https://pypi.org/simple

# mmengine + mmcv (compiled against the torch above) + the LAE-DINO fork.
ARG TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0;12.0+PTX"
ENV TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST} \
    FORCE_CUDA=1 \
    HF_HOME=/models/hf \
    HUGGINGFACE_HUB_CACHE=/models/hf/hub
RUN python -m pip install --no-cache-dir -U openmim "huggingface_hub[cli]" \
    && mim install "mmengine==0.10.4" \
    && mim install "mmcv>=2.0.0,<2.2.0"

ARG LAE_REPO=https://github.com/jaychempan/LAE-DINO.git
ARG LAE_COMMIT=main
RUN git clone ${LAE_REPO} /opt/lae-dino \
    && git -C /opt/lae-dino checkout ${LAE_COMMIT} \
    && cd /opt/lae-dino/mmdetection_lae \
    && python -m pip install --no-cache-dir -v -e . \
    && python -m pip install --no-cache-dir -r requirements/multimodal.txt \
    && python -m pip install --no-cache-dir emoji ddd-dataset

# App-layer deps.
WORKDIR /app
COPY requirements.txt /tmp/requirements.txt
RUN python -m pip install --no-cache-dir -r /tmp/requirements.txt

# --- Pre-bake weights for air-gap deployment. -----------------------------
# (a) the LAE-DINO LAE-1M open-vocab checkpoint (MIT, ~725 MB), and
# (b) the BERT text encoder it grounds prompts with. Both are local at runtime;
# nothing reaches the network once built (HF_HUB_OFFLINE/TRANSFORMERS_OFFLINE=1
# are set by the compose service).
ARG LAE_WEIGHTS_REPO=ML4Sustain/LAE-DINO
ARG LAE_WEIGHTS_FILE=checkpoints/lae_dino_swint_lae1m-28ca3a15.pth
ARG HF_TOKEN=
ENV HF_TOKEN=${HF_TOKEN}
RUN mkdir -p /models/lae /models/bert-base-uncased \
    && huggingface-cli download ${LAE_WEIGHTS_REPO} ${LAE_WEIGHTS_FILE} \
         --local-dir /models/lae \
    && huggingface-cli download google-bert/bert-base-uncased \
         --local-dir /models/bert-base-uncased \
    && rm -rf /root/.cache/huggingface/token /root/.huggingface
ENV HF_TOKEN=

# Fail the build loudly if the required weights did not land.
RUN test -f "/models/lae/${LAE_WEIGHTS_FILE}" \
        || (echo "[bake] LAE-DINO checkpoint missing — check network/HF access" && exit 1) \
    && test -f /models/bert-base-uncased/config.json \
        || (echo "[bake] bert-base-uncased missing" && exit 1)

COPY . /app

ENV PORT=8010 \
    PYTHONUNBUFFERED=1 \
    LAE_CONFIG=/opt/lae-dino/mmdetection_lae/configs/lae_dino/lae_dino_swin-t_pretrain_LAE-1M.py \
    LAE_WEIGHTS=/models/lae/checkpoints/lae_dino_swint_lae1m-28ca3a15.pth \
    LAE_BERT_DIR=/models/bert-base-uncased

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]
