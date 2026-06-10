# inference-lae — LAE-DINO open-vocabulary RS detector (mmdetection fork).
#
# Pinned to torch 2.1.0 + cu121, which is the newest stack with PREBUILT mmcv
# wheels (download.openmmlab.com) and is what the mmdet 3.3.0 fork is validated
# against — so mmcv installs as a wheel, not a fragile source compile. cu121
# runs natively on every Ampere/Hopper card (A100 = sm_80) via the host's newer
# driver, so this is decoupled from the main stack's bleeding-edge cu130 build
# (configure_host's SAM3_* args) on purpose — mmcv has no cu130 wheels. We also
# deliberately avoid LAE-DINO's upstream `lae_requirements.txt` (torch 1.10+cu113,
# a 2021 wheel that can't target modern GPUs). See docs/inference/lae-dino-sidecar.md.

ARG CUDA_VERSION=12.1.1
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

# Torch 2.1.0 + cu121 — the stack mmcv ships prebuilt wheels for and the fork is
# validated against. Runs on A100 (sm_80) via the host's newer driver.
ARG TORCH_INDEX_URL=https://download.pytorch.org/whl/cu121
ARG TORCH_VERSION=2.1.0
ARG TORCHVISION_VERSION=0.16.0
RUN python -m pip install --no-cache-dir \
        "torch==${TORCH_VERSION}" "torchvision==${TORCHVISION_VERSION}" \
        --index-url ${TORCH_INDEX_URL} --extra-index-url https://pypi.org/simple

# mmengine + mmcv (prebuilt wheel for torch 2.1/cu121) + the LAE-DINO fork.
ARG TORCH_CUDA_ARCH_LIST="8.0;8.6;8.9;9.0+PTX"
ENV TORCH_CUDA_ARCH_LIST=${TORCH_CUDA_ARCH_LIST} \
    FORCE_CUDA=1 \
    HF_HOME=/models/hf \
    HUGGINGFACE_HUB_CACHE=/models/hf/hub
RUN python -m pip install --no-cache-dir -U openmim "huggingface_hub[cli]" \
    && mim install "mmengine==0.10.4" \
    && mim install "mmcv==2.1.0"

ARG LAE_REPO=https://github.com/jaychempan/LAE-DINO.git
ARG LAE_COMMIT=main
RUN git clone ${LAE_REPO} /opt/lae-dino \
    && git -C /opt/lae-dino checkout ${LAE_COMMIT} \
    && cd /opt/lae-dino/mmdetection_lae \
    # --no-build-isolation: the fork's setup.py imports torch at build time to
    # detect CUDA and compile mmdet ops; build isolation would hide the
    # already-installed torch (ModuleNotFoundError: No module named 'torch').
    && python -m pip install --no-cache-dir --no-build-isolation -v -e . \
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
    && hf download ${LAE_WEIGHTS_REPO} ${LAE_WEIGHTS_FILE} \
         --local-dir /models/lae \
    && hf download google-bert/bert-base-uncased \
         --local-dir /models/bert-base-uncased \
    && rm -rf /root/.cache/huggingface/token /root/.huggingface
ENV HF_TOKEN=

# Fail the build loudly if the required weights did not land.
RUN test -f "/models/lae/${LAE_WEIGHTS_FILE}" \
        || (echo "[bake] LAE-DINO checkpoint missing — check network/HF access" && exit 1) \
    && test -f /models/bert-base-uncased/config.json \
        || (echo "[bake] bert-base-uncased missing" && exit 1)

# Pin transformers/tokenizers to LAE-DINO's validated versions. The fork's
# requirements/multimodal.txt leaves transformers UNPINNED, which resolves to a
# 5.x that needs torch>=2.4 and crashes mmdet import (`NameError: name 'nn'` in
# transformers/integrations/accelerate.py). 4.42.3 is the upstream pin and
# matches the fork's GroundingDINO/BERT code. Done after the weight bake so the
# ~1.2 GB download layer stays cached. The build-time import asserts the fix.
RUN python -m pip install --no-cache-dir "transformers==4.42.3" "tokenizers==0.19.1" \
    && python -c "import transformers, torch; print('transformers', transformers.__version__, '| torch', torch.__version__); from transformers import BertModel"

# The fork's clip_text_backbone (imported at mmdet.models load) needs `clip`
# (clip-anytorch provides `import clip`) and `open_clip` + its tokenizer deps.
# The LAE-1M model never instantiates this CLIP backbone (grounds via BERT), so
# nothing is fetched at runtime — these only need to be importable. Asserted at
# build time.
RUN python -m pip install --no-cache-dir clip-anytorch "open_clip_torch==2.24.0" ftfy regex \
    && python -c "import clip, open_clip; import mmdet.models; print('mmdet.models import OK')"

COPY . /app

ENV PORT=8010 \
    PYTHONUNBUFFERED=1 \
    LAE_CONFIG=/opt/lae-dino/mmdetection_lae/configs/lae_dino/lae_dino_swin-t_pretrain_LAE-1M.py \
    LAE_WEIGHTS=/models/lae/checkpoints/lae_dino_swint_lae1m-28ca3a15.pth \
    LAE_BERT_DIR=/models/bert-base-uncased

CMD ["uvicorn", "app:app", "--host", "0.0.0.0", "--port", "8010", "--workers", "1"]
