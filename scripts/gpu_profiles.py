from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Mapping


class UnsupportedGpuError(ValueError):
    """Raised when a GPU model is known but below the supported inference floor."""


@dataclass(frozen=True)
class GpuBuildProfile:
    name: str
    cuda_version: str
    torch_index_url: str
    torch_version: str
    torchvision_version: str
    torchaudio_version: str
    torch_cuda_arch_list: str
    compute_capability: str
    min_driver_version: str
    ubuntu_version: str  # "22.04" for cu126, "24.04" for cu130+

    # ------------------------------------------------------------------
    # Runtime-tuning defaults for the inference / worker containers.
    # These get written into the generated `.env` block by
    # `scripts/configure_host.py` so each host (T4, RTX 5070 Ti, H100, …)
    # picks up the appropriate values without code changes. Operators can
    # still override any of them per-host in `.env` after generation.
    # ------------------------------------------------------------------

    # --- Precision & compilation ---
    # TF32 matmul: supported sm_80 and above (Ampere/Ada/Hopper/Blackwell).
    enable_tf32: bool = True
    # --- YOLO specialist optimizations ---
    # model.fuse() folds Conv+BN (5-15% latency drop). Safe on all CUDA archs;
    # left on by default and overridden off only where the cu126/torch 2.7
    # stack has known fuse regressions.
    yolo_fuse: bool = True
    # model.half() runs YOLO specialists in fp16. Requires tensor cores
    # (sm_70+); Turing keeps fp32 because its fp16 throughput vs fp32 is
    # marginal for the small specialist models we run.
    yolo_half: bool = True
    # channels_last memory format — 8-35% conv speedup on Volta/Ampere+.
    yolo_channels_last: bool = True
    # cudnn.benchmark = True picks the fastest conv kernel per input shape.
    # Safe when YOLOE_IMGSZ / DOTA_OBB_IMGSZ are fixed (env vars, defaults
    # 640 / 1024). Disabled on Turing because the cu126 stack re-benchmarks
    # on every shape change, which dominates for short request bursts.
    cudnn_benchmark: bool = True

    # --- SAM3 precision & attention backend ---
    # Native bf16 weights for SAM3 image model. Default OFF: while
    # mlx-community/sam3-bf16 demonstrates feasibility, the upstream
    # `_get_dummy_prompt` + geometry-encoder paths in /opt/sam3 keep
    # fp32 buffers that fail bf16 nn.Linear forward without patching
    # every site. Autocast already gives ~bf16 activations with zero
    # extra patching, so the marginal speedup isn't worth it on this
    # release. Re-enable per-profile + add the corresponding input
    # casts in patches.sam3_cached_forward if reactivated.
    sam3_native_bf16: bool = False
    # SDPA backend preference: "flash" prefers FLASH_ATTENTION when
    # available, then EFFICIENT_ATTENTION; "efficient" pins
    # EFFICIENT_ATTENTION only; "auto" lets PyTorch choose.
    sam3_sdpa_backend: str = "auto"
    # DETR-decoder top-K pruning over SAM3's 200 object queries. Computed
    # as sigmoid(pred_logits) * sigmoid(presence_logits). 0 disables.
    sam3_decoder_topk: int = 0
    # torch.compile the SAM3 vision encoder (Hiera/ViT). Requires the
    # patches.sam3_cached_forward to be installed since compile breaks the
    # selective-AC wrapper. First call pays ~30-60 s warmup.
    sam3_compile_vision_encoder: bool = False
    # torch.compile() of the image model: datacenter cards only; consumer
    # Blackwell/Ada leave this off because branchy paths trip the compiler.
    compile_image: bool = False
    # torch.compile() of the video predictor — risky default-on because
    # SAM3's branchy text/box paths sometimes trip the compiler; only
    # enable on datacenter cards where the win is worth the failure mode.
    compile_video: bool = False

    # --- Video session sizing ---
    # SAM3 video session sizing — the prep-clip height (px) and the max
    # number of frames per SAM3 session. Smaller GPUs need smaller windows
    # to fit decoded-frame tensors + activations during propagation.
    fmv_track_height: int = 540
    fmv_track_frames_per_window: int = 48

    # --- Object Multiplex (SAM3.1 multi-frame predictor) ---
    # Profile flag is the *policy*; the runtime VRAM gate (below) is the
    # *measurement*. Both must be true for multiplex to activate.
    use_multiplex: bool = False
    # Minimum total GPU VRAM (MiB) required to activate multiplex.
    # SAM3.1 multiplex weights are ~14 GiB; 20 GiB leaves headroom for
    # session activations. Tune down for cards with fast HBM bandwidth.
    sam3_multiplex_min_vram_mib: int = 20_480

    # --- Loading policy (hot vs dynamic) ---
    # Cards at or above this total VRAM run the "hot" policy: every model the
    # profile permits stays resident, no swap latency (honours the profile's
    # own sam3_preload_* fields). Below it, configure_host emits the "dynamic"
    # policy — models load on demand per *modality* profile (imagery_rgb /
    # imagery_msi / imagery_sar / fmv, split in inference-sam3/main.py) so only
    # one modality's models are resident at a time. 24 GiB is the floor at which
    # the full imagery union fits SAM3 + every specialist with inference
    # headroom. See docs/decisions/why-dynamic-modality-loading-on-tight-vram.md.
    sam3_hot_load_min_vram_mib: int = 24_576

    # --- Optional satellite-intelligence models ---
    # Master switch; individual flags below are only honoured when this is True.
    # Disabling saves ~3.5 GiB VRAM and ~290 ms/chip (sum of all three heads).
    sam3_load_optional_models: bool = True
    # DINOv3-SAT: satellite-tuned visual embeddings for detection re-ID.
    # +1.5 GiB VRAM, +217 ms/chip. Off on Turing to preserve headroom.
    sam3_load_dinov3_sat: bool = True
    # Prithvi EO-2.0: flood + burn semantic segmentation heads.
    # +0.8 GiB VRAM, +20 ms/chip (multispectral imagery only).
    sam3_load_prithvi: bool = True
    # Terramind: SAR-to-RGB synthesis backbone for SAR imagery.
    # +1.2 GiB VRAM, ~0 ms overhead on RGB (SAR-only pipeline).
    sam3_load_terramind: bool = True

    # --- Specialist open-set detectors (run alongside SAM3 on every chip) ---
    # DOTA-OBB: oriented-bbox aerial vehicle detector (yolo11n ≈ 0.1 GiB, +50 ms/chip).
    sam3_load_dota_obb: bool = True
    # Grounding-DINO: open-vocab text-to-box detector (tiny ≈ 0.6 GiB, +241 ms/chip).
    sam3_load_grounding_dino: bool = True
    # YOLOE-26x open-vocab instance segmentation (pf + seg, ~1 GiB combined).
    # Powers the standalone YOLO 26 FMV tracker that bypasses SAM 3.1 multiplex.
    sam3_load_yoloe: bool = True

    # --- Detection embedding (DINOv3-SAT re-ID) ---
    # Generates a 768-d embedding for every detection crop; enables track
    # re-identification across imagery sessions. Requires sam3_load_dinov3_sat.
    sam3_embed_detections: bool = True

    # --- Batching & startup ---
    # Number of text prompts sent to SAM3 in a single batched inference call.
    # Larger values reduce round-trips but consume more VRAM per call.
    sam3_batched_text_chunk_size: int = 8
    # Master switch for startup preloading. When True, sam3_preload_profile
    # is loaded eagerly during container boot (~30-60 s init cost) so the
    # first inference request doesn't pay model-load latency. Datacenter
    # servers want this on; dev boxes typically leave it off.
    sam3_preload_models: bool = False
    # Which model profile to warm at container startup. Only takes effect
    # when sam3_preload_models=True. "fmv" / "imagery" / "".
    sam3_preload_profile: str = ""

    # --- Memory allocator ---
    # expandable_segments avoids fragmentation on long-running inference
    # servers; safe for all supported GPU architectures.
    pytorch_cuda_alloc_conf: str = "expandable_segments:True"

    # --- Backend worker: chip pipeline ---
    # "fast_review" caps at 256 chips with 1 concurrent thread (safe default).
    # "recall_review" is unlimited chips with 2 concurrent threads (Hopper+).
    inference_speed_profile: str = "fast_review"
    # Parallel chip POST threads. Scales throughput on GPUs with headroom for
    # overlapping decode + encode while inference is running.
    inference_chip_concurrency: int = 1

    def build_env(self, prefix: str = "SAM3_") -> dict[str, str]:
        return {
            f"{prefix}CUDA_VERSION": self.cuda_version,
            f"{prefix}UBUNTU_VERSION": self.ubuntu_version,
            f"{prefix}TORCH_INDEX_URL": self.torch_index_url,
            f"{prefix}TORCH_VERSION": self.torch_version,
            f"{prefix}TORCHVISION_VERSION": self.torchvision_version,
            f"{prefix}TORCHAUDIO_VERSION": self.torchaudio_version,
            f"{prefix}TORCH_CUDA_ARCH_LIST": self.torch_cuda_arch_list,
        }

    def runtime_env(self, vram_mib: int | None = None) -> dict[str, str]:
        """Profile-driven runtime knobs, written into .env by configure_host.

        ``vram_mib`` is the live `nvidia-smi --query-gpu=memory.total` value;
        passed in so we can gate multiplex on actual hardware regardless of
        what the profile permits (e.g. a profile that says
        ``use_multiplex=True`` will still emit ``SAM3_USE_MULTIPLEX=0`` on
        an undersized card)."""
        multiplex_ok = self.use_multiplex and (
            vram_mib is None or vram_mib >= self.sam3_multiplex_min_vram_mib
        )
        # Loading-policy gate (see sam3_hot_load_min_vram_mib). Roomy cards run
        # "hot" (everything the profile permits resident, honouring its own
        # preload choice). Tight cards run "dynamic": no preload, per-modality
        # profiles. Grounding-DINO stays available on all cards — it is
        # runtime auto-gated (only fires on novel-vocab prompts) so it costs
        # nothing on the common path. See
        # docs/decisions/why-grounding-dino-auto-gated.md.
        hot_load = vram_mib is None or vram_mib >= self.sam3_hot_load_min_vram_mib
        if hot_load:
            preload_models = self.sam3_preload_models
            preload_profile = self.sam3_preload_profile
        else:
            preload_models = False
            preload_profile = ""
        env: dict[str, str] = {
            # Loading policy (informational; consumed by ops dashboards/docs)
            "SAM3_LOAD_POLICY": "hot" if hot_load else "dynamic",
            # Precision & compilation
            "SAM3_ENABLE_TF32": "1" if self.enable_tf32 else "0",
            "SAM3_COMPILE_IMAGE": "1" if self.compile_image else "0",
            "SAM3_COMPILE_VIDEO": "1" if self.compile_video else "0",
            # Video session sizing
            "FMV_TRACK_HEIGHT": str(self.fmv_track_height),
            "FMV_TRACK_FRAMES_PER_WINDOW": str(self.fmv_track_frames_per_window),
            # Multiplex
            "SAM3_USE_MULTIPLEX": "1" if multiplex_ok else "0",
            "SAM3_MULTIPLEX_MIN_VRAM_MIB": str(self.sam3_multiplex_min_vram_mib),
            # Optional satellite models
            "SAM3_LOAD_OPTIONAL_MODELS": "1" if self.sam3_load_optional_models else "0",
            "SAM3_LOAD_DINOV3_SAT": "1" if self.sam3_load_dinov3_sat else "0",
            "SAM3_LOAD_PRITHVI": "1" if self.sam3_load_prithvi else "0",
            "SAM3_LOAD_TERRAMIND": "1" if self.sam3_load_terramind else "0",
            # Specialist detectors.
            "SAM3_LOAD_DOTA_OBB": "1" if self.sam3_load_dota_obb else "0",
            "SAM3_LOAD_GROUNDING_DINO": "1" if self.sam3_load_grounding_dino else "0",
            "SAM3_LOAD_YOLOE": "1" if self.sam3_load_yoloe else "0",
            # Embedding & batching
            "SAM3_EMBED_DETECTIONS": "1" if self.sam3_embed_detections else "0",
            "SAM3_BATCHED_TEXT_CHUNK_SIZE": str(self.sam3_batched_text_chunk_size),
            "SAM3_PRELOAD_MODELS": "1" if preload_models else "0",
            "SAM3_PRELOAD_PROFILE": preload_profile,
            # Resting profile the inference lifespan loads at startup (keeps the
            # healthcheck's model_loaded=true). Tight cards rest on the light
            # per-modality RGB profile (fits VRAM); the first MSI/SAR/FMV
            # request swaps to its own profile. Roomy cards rest on the full
            # imagery union. Never the union on a tight card — that OOMs.
            "SAM3_RESTING_PROFILE": "imagery" if hot_load else "imagery_rgb",
            # Memory allocator
            "PYTORCH_CUDA_ALLOC_CONF": self.pytorch_cuda_alloc_conf,
            # PyTorch 2.8+ renamed PYTORCH_CUDA_ALLOC_CONF → PYTORCH_ALLOC_CONF
            # (the new variable applies to all allocators, not just CUDA).
            # Keep both for backward compat across the 2.7→2.10 transition.
            "PYTORCH_ALLOC_CONF": self.pytorch_cuda_alloc_conf,
            # YOLO optimization knobs (consumed by inference_utils +
            # yoloe / dota_obb / grounding_dino loaders).
            "SAM3_YOLO_FUSE": "1" if self.yolo_fuse else "0",
            "SAM3_YOLO_HALF": "1" if self.yolo_half else "0",
            "SAM3_YOLO_CHANNELS_LAST": "1" if self.yolo_channels_last else "0",
            "SAM3_CUDNN_BENCHMARK": "1" if self.cudnn_benchmark else "0",
            # Cross-tile NMS dedup defaults. agnostic=1 collapses cases where
            # SAM3 and DOTA-OBB label the same object differently; soft=0
            # keeps the dropped-detection behaviour. Operators flip
            # SAM3_NMS_SOFT=1 for crowded scenes (ports, parking lots).
            "SAM3_NMS_AGNOSTIC": "1",
            "SAM3_NMS_SOFT": "0",
            # SAM3 precision + attention backend (consumed by sam3_runner).
            "SAM3_NATIVE_BF16": "1" if self.sam3_native_bf16 else "0",
            "SAM3_SDPA_BACKEND": self.sam3_sdpa_backend,
            "SAM3_DECODER_TOPK": str(self.sam3_decoder_topk),
            "SAM3_COMPILE_VISION_ENCODER": "1" if self.sam3_compile_vision_encoder else "0",
            # Backend chip pipeline
            "INFERENCE_SPEED_PROFILE": self.inference_speed_profile,
            "INFERENCE_CHIP_CONCURRENCY": str(self.inference_chip_concurrency),
            # Cross-frame tracker (used by YOLOE FMV path). Profile-agnostic
            # defaults; operators can override per-host in .env after the
            # generated block.
            "SAM3_TRACK_IOU_MIN": "0.30",
            "SAM3_TRACK_BUFFER": "12",
            "SAM3_TRACK_MIN_CONSECUTIVE_FRAMES": "2",
        }
        if vram_mib is not None:
            env["SAM3_GPU_VRAM_GIB"] = f"{vram_mib / 1024:.1f}"
        return env


GPU_BUILD_PROFILES: dict[str, GpuBuildProfile] = {
    "turing_sm75": GpuBuildProfile(
        name="turing_sm75",
        cuda_version="12.6.3",
        torch_index_url="https://download.pytorch.org/whl/cu126",
        torch_version="2.7.1+cu126",
        torchvision_version="0.22.1+cu126",
        torchaudio_version="2.7.1+cu126",
        torch_cuda_arch_list="7.5;8.0;8.6;8.9;9.0+PTX",
        compute_capability="7.5",
        min_driver_version="560.28.03",
        ubuntu_version="22.04",
        # sm_75 has no native TF32 tensor cores; smaller 16 GiB working set.
        enable_tf32=False,
        # Turing keeps fuse() (helps even without tensor cores) but skips
        # half/channels_last/cudnn_benchmark — fp16 throughput vs fp32 on
        # sm_75 is marginal for the specialist models, and cu126 + torch 2.7
        # re-benchmarks on every input-shape change which dominates short
        # request bursts.
        yolo_fuse=True,
        yolo_half=False,
        yolo_channels_last=False,
        cudnn_benchmark=False,
        # Turing (T4 16 GiB, 2080 Ti 11 GiB) — fp16 tensor cores but bf16
        # throughput is poor on sm_75, so keep SAM3 in fp32+autocast.
        # FA2/FA3 don't run here either; pin SDPA to EFFICIENT_ATTENTION
        # so PyTorch doesn't fall back to the MATH kernel. Decoder top-K
        # at 32 is a free postproc-side filter (the patched
        # forward_grounding just zeroes sub-K logits before postproc) —
        # safe to enable. torch.compile is off because cu126 + torch 2.7
        # + sm_75 hasn't been validated and Turing rarely needs it.
        sam3_native_bf16=False,
        sam3_sdpa_backend="efficient",
        sam3_decoder_topk=32,
        sam3_compile_vision_encoder=False,
        compile_image=False,
        compile_video=False,
        fmv_track_height=360,
        fmv_track_frames_per_window=24,
        use_multiplex=False,
        # Optional satellite models disabled: SAM3 base (~8 GiB) + DOTA-OBB
        # + GD-tiny leaves only ~7 GiB headroom on a 16 GiB T4, which is
        # consumed by video session activations. Enable per-model overrides
        # in .env once VRAM budget is measured on the specific workload.
        sam3_load_optional_models=False,
        sam3_load_dinov3_sat=False,
        sam3_load_prithvi=False,
        sam3_load_terramind=False,
        sam3_load_dota_obb=True,
        sam3_load_grounding_dino=True,
        sam3_embed_detections=False,
        sam3_batched_text_chunk_size=4,
        sam3_preload_profile="",
        inference_speed_profile="fast_review",
        inference_chip_concurrency=1,
        # leaves ~6 GiB headroom — not enough for a 16² grid + propagation
        # grid 16 = 256 prompts on seed frame; reseed every 12 frames (≤ 4
        # seeds per 48-frame window).
    ),
    "ampere_sm80_86": GpuBuildProfile(
        name="ampere_sm80_86",
        # Consumer / workstation Ampere (RTX 30-series, RTX A-series).
        # CUDA 13.2 + cu130. Conservative runtime defaults — 10-24 GiB
        # cards can't carry the same workload as datacenter A100s.
        cuda_version="13.2.0",
        torch_index_url="https://download.pytorch.org/whl/cu130",
        torch_version="2.10.0+cu130",
        torchvision_version="0.25.0+cu130",
        torchaudio_version="2.10.0+cu130",
        torch_cuda_arch_list="8.0;8.6;8.9;9.0+PTX",
        compute_capability="8.x",
        min_driver_version="575.51",
        ubuntu_version="24.04",
        # TF32 capable. Multiplex permitted at profile level; runtime VRAM
        # gate downgrades to base predictor on cards with < 20 GiB (RTX
        # 3080 10/12 GiB, RTX A4000 16 GiB).
        enable_tf32=True,
        compile_image=False,
        compile_video=False,
        fmv_track_height=540,
        fmv_track_frames_per_window=48,
        use_multiplex=True,
        sam3_load_optional_models=True,
        sam3_load_dinov3_sat=True,
        sam3_load_prithvi=True,
        # Terramind (~6 GiB SAR→optical synthesis) is OFF by default on
        # consumer Ampere because this profile covers 8-12 GiB cards
        # (RTX 3050 8 GiB, RTX 3060 12 GiB, RTX 3070 8 GiB, RTX 3080
        # 10/12 GiB, RTX A2000 6/12 GiB). SAM3 base + DINOv3-SAT + Prithvi
        # + heads + Terramind overruns the budget on /detect calls. Same
        # rationale as blackwell_sm120. Operators on 24 GiB consumer
        # Ampere (RTX 3090/3090 Ti, RTX A4500 20 GiB) can override
        # SAM3_LOAD_TERRAMIND=1 in .env after configure_host.py.
        sam3_load_terramind=False,
        sam3_load_dota_obb=True,
        sam3_load_grounding_dino=True,
        sam3_embed_detections=True,
        sam3_batched_text_chunk_size=8,
        sam3_preload_profile="",
        inference_speed_profile="fast_review",
        inference_chip_concurrency=1,
        # Consumer Ampere (RTX 30xx, 8-24 GiB). bf16 hw accelerated;
        # PyTorch ships FA2 wheels for sm_80/86 → sdpa=flash actually
        # picks FA2 (real win). Decoder top-K=32 conservative for the
        # 8 GiB cards in the profile; bumps to 48 on 24 GiB cards via
        # .env override are safe. compile_vision_encoder off: cu130 +
        # SAM3's selective-AC + dynamo conflict is the same one
        # documented on the ampere_sm80_86_datacenter profile below.
        sam3_native_bf16=False,
        sam3_sdpa_backend="flash",
        sam3_decoder_topk=32,
        sam3_compile_vision_encoder=False,
        # in one video session, so a smaller grid (16² = 256 prompts) and
        # less frequent reseed (every 12 frames) cuts wall-clock ~5× vs
        # Phase 1 defaults without measurable recall loss on FMV fixtures.
    ),
    "ampere_sm80_86_datacenter": GpuBuildProfile(
        name="ampere_sm80_86_datacenter",
        # Datacenter Ampere (A100 40/80GB, A40, A30, A10, A10G). Same
        # build stack as consumer Ampere but aggressive runtime defaults:
        # 40-80 GiB VRAM, full memory bandwidth, multi-GPU racks. Worth
        # paying the compile JIT cost on long-running servers.
        cuda_version="13.2.0",
        torch_index_url="https://download.pytorch.org/whl/cu130",
        torch_version="2.10.0+cu130",
        torchvision_version="0.25.0+cu130",
        torchaudio_version="2.10.0+cu130",
        torch_cuda_arch_list="8.0;8.6;8.9;9.0+PTX",
        compute_capability="8.x",
        min_driver_version="575.51",
        ubuntu_version="24.04",
        enable_tf32=True,
        # Both compile_image and compile_video are OFF on this stack — two
        # independent upstream incompatibilities on cu130 + sm_80:
        #   * compile_video: multiplex-warmup matmul shape trips
        #     `cublasLtMatmulAlgoGetHeuristic → CUBLAS_STATUS_NOT_INITIALIZED`
        #     on torch 2.10.0+cu130 (observed 2026-05-12 on A100 80GB,
        #     driver ≥575.51).
        #   * compile_image: SAM3's `vl_combiner._forward_image_no_act_ckpt`
        #     runs through `activation_ckpt_wrapper` →
        #     `torch.utils.checkpoint.checkpoint(use_reentrant=False)`, whose
        #     selective-recompute path uses FX `proxy_tensor.make_fx`. When
        #     the vitdet trunk's inner forward is `torch.compile`'d, the FX
        #     trace hits a dynamo boundary and raises `RuntimeError: Detected
        #     that you are using FX to symbolically trace a dynamo-optimized
        #     function` from `processor.set_image` — breaking both
        #     4× A100, torch 2.10.0+cu130).
        # Both run in eager mode. Revisit when upstream PyTorch / SAM3
        # releases a fix; flip back to True and verify warmup completes plus
        compile_image=False,
        compile_video=False,
        # 720p prep clip + 96 frames/window: ~7.5 GiB per video session,
        # trivial against 40-80 GiB cards.
        fmv_track_height=720,
        fmv_track_frames_per_window=96,
        use_multiplex=True,
        sam3_load_optional_models=True,
        sam3_load_dinov3_sat=True,
        sam3_load_prithvi=True,
        sam3_load_terramind=True,
        sam3_load_dota_obb=True,
        sam3_load_grounding_dino=True,
        sam3_embed_detections=True,
        # Larger batch fits comfortably in 40-80 GiB HBM.
        sam3_batched_text_chunk_size=16,
        # Preload the "all" superset profile — 40-80 GiB cards have room
        # to keep both fmv and imagery components resident, so requests
        # of either kind serve immediately with no /load unload+reload
        # pause. `_ensure_profile` in main.py recognises "all" as
        # satisfying any single-profile request whose components are a
        # subset.
        sam3_preload_models=True,
        sam3_preload_profile="all",
        # Full-coverage chip sweep + parallel chip threads.
        inference_speed_profile="recall_review",
        inference_chip_concurrency=2,
        # Datacenter Ampere (A100 40/80GB, A40, A30, A10, RTX A6000).
        # 24-80 GiB HBM, full memory bandwidth. sdpa=flash → FA2 on sm_80.
        # Decoder top-K=64 widens the candidate set vs consumer (32) since
        # datacenter chips can be cluttered (port photos, parking lots).
        # compile_vision_encoder stays OFF: the cu130 + sm_80 FX/dynamo
        # bug documented above on compile_image=False applies identically
        # here (selective-AC + activation_ckpt_wrapper trips dynamo
        # boundary inside the vitdet trunk). Flip True when upstream PyTorch
        # ships the make_fx fix.
        sam3_native_bf16=False,
        sam3_sdpa_backend="flash",
        sam3_decoder_topk=64,
        sam3_compile_vision_encoder=False,
        # 32² grid (1024 prompts/seed) and every-4-frames reseed cheap.
        # so density matters more than per-frame frequency.
    ),
    "ada_sm89": GpuBuildProfile(
        name="ada_sm89",
        cuda_version="12.6.3",
        torch_index_url="https://download.pytorch.org/whl/cu126",
        torch_version="2.7.1+cu126",
        torchvision_version="0.22.1+cu126",
        torchaudio_version="2.7.1+cu126",
        torch_cuda_arch_list="8.9;9.0+PTX",
        compute_capability="8.9",
        min_driver_version="560.28.03",
        ubuntu_version="22.04",
        enable_tf32=True,
        compile_image=False,
        compile_video=False,
        fmv_track_height=540,
        fmv_track_frames_per_window=48,
        use_multiplex=True,
        sam3_load_optional_models=True,
        sam3_load_dinov3_sat=True,
        sam3_load_prithvi=True,
        # Terramind (~6 GiB) is OFF by default on Ada: this profile covers
        # 8-12 GiB consumer cards (RTX 4060 8 GiB, RTX 4060 Ti 8/16 GiB,
        # RTX 4070 12 GiB) where the full satellite-model stack OOMs. The
        # higher-VRAM members of this profile (RTX 4090 24 GiB, L40/L40s
        # 48 GiB, RTX 6000 Ada 48 GiB) can override SAM3_LOAD_TERRAMIND=1
        # in .env after configure_host.py.
        sam3_load_terramind=False,
        sam3_load_dota_obb=True,
        sam3_load_grounding_dino=True,
        sam3_embed_detections=True,
        sam3_batched_text_chunk_size=8,
        sam3_preload_profile="",
        inference_speed_profile="fast_review",
        inference_chip_concurrency=1,
        # Ada (sm_89, cu126) covers RTX 4060 8 GiB through L40s 48 GiB
        # plus the RTX 6000 Ada 48 GiB workstation cards. sdpa=flash uses
        # FA2 (sm_89 supported). Decoder top-K=32 is the conservative
        # floor for 8 GiB cards; .env can bump to 48 or 64 on 24+ GiB
        # cards safely. compile_vision_encoder is OFF: the cu126 stack +
        # torch 2.7 has not been validated for SAM3 compile, and the
        # Ada-side fix for the make_fx/dynamo issue hasn't been backported.
        sam3_native_bf16=False,
        sam3_sdpa_backend="flash",
        sam3_decoder_topk=32,
        sam3_compile_vision_encoder=False,
        # Ada (RTX 4090 / L40) is sm_89 with 24-48 GiB. Phase 2 hybrid path:
        # 16² grid + reseed-every-12 matches consumer Blackwell wall-clock
        # while leaving headroom for parallel chip+video sessions.
    ),
    "hopper_sm90": GpuBuildProfile(
        name="hopper_sm90",
        cuda_version="13.2.0",
        torch_index_url="https://download.pytorch.org/whl/cu130",
        torch_version="2.10.0+cu130",
        torchvision_version="0.25.0+cu130",
        torchaudio_version="2.10.0+cu130",
        torch_cuda_arch_list="9.0+PTX",
        compute_capability="9.0",
        min_driver_version="575.51",
        ubuntu_version="24.04",
        # H100 / H200: 80 GiB+ datacenter cards — full stack + compilation.
        # Both compile flags stay True on sm_90+: the FX/dynamo conflict in
        # SAM3's act_ckpt_wrapper (see ampere_sm80_86_datacenter note) hasn't
        # been observed on Hopper, and the flash-attn-3 wheel runs natively
        # here so the perflib path doesn't need the SDPA fallback. Revisit if
        # the same `Detected that you are using FX to symbolically trace a
        # dynamo-optimized function` shows up on H100/H200 logs.
        enable_tf32=True,
        compile_image=True,
        compile_video=True,
        fmv_track_height=720,
        fmv_track_frames_per_window=96,
        use_multiplex=True,
        sam3_load_optional_models=True,
        sam3_load_dinov3_sat=True,
        sam3_load_prithvi=True,
        sam3_load_terramind=True,
        sam3_load_dota_obb=True,
        sam3_load_grounding_dino=True,
        sam3_embed_detections=True,
        # Batched text chunk = 32 on H100/H200: each chunk's decoder
        # activations are ~6-7 GiB at chunk=32 (200 queries × 1008² masks
        # in bf16), trivial against 80-141 GiB HBM3. Doubling chunk size
        # halves the number of Python-level decoder dispatches.
        sam3_batched_text_chunk_size=32,
        sam3_preload_models=True,
        # H100/H200 carry 80 GiB+ HBM3 — keep the "all" superset resident
        # so requests of either kind serve immediately. `_ensure_profile`
        # in main.py treats "all" as satisfying any single-profile request.
        sam3_preload_profile="all",
        inference_speed_profile="recall_review",
        inference_chip_concurrency=2,
        # Hopper (sm_90): the only profile where every SAM3 perf knob is
        # ON. FA3 wheels run natively, compile_vision_encoder is the most
        # stable target (FX/dynamo + selective-AC fix landed for sm_90+
        # in torch 2.10), and 80-141 GiB HBM3 supports the widest
        # decoder top-K window.
        sam3_native_bf16=False,
        sam3_sdpa_backend="flash",
        sam3_decoder_topk=64,
        sam3_compile_vision_encoder=True,
        # remains within ~2 s on Hopper.
    ),
    "blackwell_sm100": GpuBuildProfile(
        name="blackwell_sm100",
        cuda_version="13.2.0",
        torch_index_url="https://download.pytorch.org/whl/cu130",
        torch_version="2.10.0+cu130",
        torchvision_version="0.25.0+cu130",
        torchaudio_version="2.10.0+cu130",
        torch_cuda_arch_list="9.0;10.0;12.0+PTX",
        compute_capability="10.0",
        min_driver_version="575.51",
        ubuntu_version="24.04",
        # B100 / B200 datacenter Blackwell — same generous budget as Hopper.
        # compile_image / compile_video stay True deliberately on sm_100; the
        # FX/dynamo conflict that forced them off for ampere_sm80_86_datacenter
        # hasn't reproduced on Blackwell. Revisit if it does.
        enable_tf32=True,
        compile_image=True,
        compile_video=True,
        fmv_track_height=720,
        fmv_track_frames_per_window=96,
        use_multiplex=True,
        sam3_load_optional_models=True,
        sam3_load_dinov3_sat=True,
        sam3_load_prithvi=True,
        sam3_load_terramind=True,
        sam3_load_dota_obb=True,
        sam3_load_grounding_dino=True,
        sam3_embed_detections=True,
        # Datacenter Blackwell chunk=32 mirrors Hopper. HBM3e at 192 GiB
        # (B200) makes decoder-output VRAM essentially free; doubling
        # chunk halves dispatch overhead.
        sam3_batched_text_chunk_size=32,
        sam3_preload_models=True,
        # B100/B200 datacenter Blackwell — 80-192 GiB HBM3e. Preload the
        # full superset like Hopper / Ampere datacenter so profile switches
        # are zero-pause.
        sam3_preload_profile="all",
        inference_speed_profile="recall_review",
        inference_chip_concurrency=2,
        # Datacenter Blackwell (B100/B200) — mirrors Hopper for SAM3 perf
        # knobs. sm_100 + cu130 has FA3 native, the make_fx/dynamo bug
        # hasn't reproduced (compile_image=True above), so the vision
        # encoder compile is the safest of any consumer profile.
        sam3_native_bf16=False,
        sam3_sdpa_backend="flash",
        sam3_decoder_topk=64,
        sam3_compile_vision_encoder=True,
        # B100/B200 datacenter Blackwell mirrors Hopper (Phase 2: 32²/4f).
    ),
    "blackwell_sm120": GpuBuildProfile(
        name="blackwell_sm120",
        cuda_version="13.2.0",
        torch_index_url="https://download.pytorch.org/whl/cu130",
        torch_version="2.10.0+cu130",
        torchvision_version="0.25.0+cu130",
        torchaudio_version="2.10.0+cu130",
        torch_cuda_arch_list="8.0;8.6;8.9;9.0;12.0+PTX",
        compute_capability="12.0",
        min_driver_version="575.51",
        ubuntu_version="24.04",
        # Consumer Blackwell (RTX 5060/5070/5080/5090). TF32 = yes. Multiplex
        # permitted at profile level; the runtime VRAM gate gates it off on
        # 16 GiB cards (RTX 5070 Ti: 15.9 GiB < 20 GiB threshold) and on for
        # 24+ GiB cards (RTX 5090: 32 GiB). compile_video stays off —
        # SAM3's branchy paths still trip the compiler on this arch.
        # Loading on 16 GiB consumer Blackwell (RTX 5070 Ti, 5080) is the
        # *dynamic* policy: the runtime VRAM gate (< sam3_hot_load_min_vram_mib)
        # makes inference-sam3 load one modality profile at a time
        # (imagery_rgb / imagery_msi / imagery_sar), so Terramind, Prithvi and
        # DINOv3-SAT only go resident for the modality that needs them — no
        # single profile exhausts the 16 GiB budget (the 2026-05-12 OOM was the
        # monolithic profile loading every model at once). Terramind is
        # therefore safe to enable now (resident only on SAR ingest). 24+ GiB
        # consumer Blackwells (RTX 5090 32 GiB) cross the hot-load threshold and
        # keep everything resident.
        enable_tf32=True,
        compile_image=False,
        compile_video=False,
        fmv_track_height=540,
        fmv_track_frames_per_window=48,
        use_multiplex=True,
        sam3_load_optional_models=True,
        sam3_load_dinov3_sat=True,
        sam3_load_prithvi=True,
        sam3_load_terramind=True,
        sam3_load_dota_obb=True,
        sam3_load_grounding_dino=True,
        sam3_embed_detections=True,
        sam3_batched_text_chunk_size=8,
        sam3_preload_profile="",
        inference_speed_profile="fast_review",
        inference_chip_concurrency=1,
        # Consumer Blackwell (RTX 5060/5070/5080/5090, 8-32 GiB).
        # On sm_120 today FA3 wheels are dead (Hopper-only) and FA4 is
        # pending; sdpa=flash lets PyTorch try FLASH first then fall
        # through to EFFICIENT_ATTENTION (today's actual path). Decoder
        # top-K=32 is right for the 8-16 GiB floor of this profile;
        # operators on RTX 5090 (32 GiB) can bump to 48 via .env. The
        # cached-encoder Phase A3 patch is the dominant win on this
        # profile (~4720 → ~3000 ms p50 measured on RTX 5070 Ti).
        # torch.compile of vision encoder is OFF — sm_120 compile path
        # via cu130 is unproven; revisit when nightly PyTorch wheels
        # ship validated sm_120 SDPA-Flash kernels.
        sam3_native_bf16=False,
        sam3_sdpa_backend="flash",
        sam3_decoder_topk=32,
        sam3_compile_vision_encoder=False,
        # Phase 2 hybrid path: grid 16 + reseed-every-12 takes ~30 s per 48-frame
        # window on the RTX 5070 Ti (measured) — ~5× faster than Phase 1.
    ),
}


GPU_MODELS: Mapping[str, str] = {
    # NVIDIA Turing.
    "nvidia tesla t4": "turing_sm75",
    "tesla t4": "turing_sm75",
    "nvidia t4": "turing_sm75",
    "nvidia quadro rtx 4000": "turing_sm75",
    "nvidia quadro rtx 5000": "turing_sm75",
    "nvidia quadro rtx 6000": "turing_sm75",
    "nvidia quadro rtx 8000": "turing_sm75",
    "nvidia geforce rtx 2060": "turing_sm75",
    "nvidia geforce rtx 2070": "turing_sm75",
    "nvidia geforce rtx 2070 super": "turing_sm75",
    "nvidia geforce rtx 2080": "turing_sm75",
    "nvidia geforce rtx 2080 super": "turing_sm75",
    "nvidia geforce rtx 2080 ti": "turing_sm75",
    # NVIDIA Ampere — datacenter (A100, A40, A30, A10): passive-cooled,
    # NVLink/HBM2e, 24-80 GiB. Routed to the aggressive runtime profile.
    "nvidia a10": "ampere_sm80_86_datacenter",
    "nvidia a10g": "ampere_sm80_86_datacenter",
    "nvidia a30": "ampere_sm80_86_datacenter",
    "nvidia a40": "ampere_sm80_86_datacenter",
    "nvidia a100": "ampere_sm80_86_datacenter",
    "nvidia a100 40gb pcie": "ampere_sm80_86_datacenter",
    "nvidia a100 80gb pcie": "ampere_sm80_86_datacenter",
    "nvidia a100-pcie-40gb": "ampere_sm80_86_datacenter",
    "nvidia a100-pcie-80gb": "ampere_sm80_86_datacenter",
    "nvidia a100-sxm4-40gb": "ampere_sm80_86_datacenter",
    "nvidia a100-sxm4-80gb": "ampere_sm80_86_datacenter",
    # NVIDIA Ampere — pro workstation (RTX A4000/A5000/A6000): 16/24/48 GiB,
    # ECC, NVLink on A6000. Treated as datacenter-class for runtime tuning.
    # (A4000's 16 GiB falls below the 20 GiB multiplex gate — VRAM gate
    # automatically downgrades it to base predictor.)
    "nvidia rtx a4000": "ampere_sm80_86_datacenter",
    "nvidia rtx a5000": "ampere_sm80_86_datacenter",
    "nvidia rtx a6000": "ampere_sm80_86_datacenter",
    # NVIDIA Ampere — consumer (RTX 30-series) + low-end workstation.
    "nvidia geforce rtx 3050": "ampere_sm80_86",
    "nvidia geforce rtx 3060": "ampere_sm80_86",
    "nvidia geforce rtx 3060 ti": "ampere_sm80_86",
    "nvidia geforce rtx 3070": "ampere_sm80_86",
    "nvidia geforce rtx 3070 ti": "ampere_sm80_86",
    "nvidia geforce rtx 3080": "ampere_sm80_86",
    "nvidia geforce rtx 3080 ti": "ampere_sm80_86",
    "nvidia geforce rtx 3090": "ampere_sm80_86",
    "nvidia geforce rtx 3090 ti": "ampere_sm80_86",
    "nvidia rtx a2000": "ampere_sm80_86",
    "nvidia rtx a4500": "ampere_sm80_86",
    # NVIDIA Ada.
    "nvidia l4": "ada_sm89",
    "nvidia l40": "ada_sm89",
    "nvidia l40s": "ada_sm89",
    "nvidia rtx 4000 ada generation": "ada_sm89",
    "nvidia rtx 4500 ada generation": "ada_sm89",
    "nvidia rtx 5000 ada generation": "ada_sm89",
    "nvidia rtx 6000 ada generation": "ada_sm89",
    "nvidia geforce rtx 4060": "ada_sm89",
    "nvidia geforce rtx 4060 ti": "ada_sm89",
    "nvidia geforce rtx 4070": "ada_sm89",
    "nvidia geforce rtx 4070 super": "ada_sm89",
    "nvidia geforce rtx 4070 ti": "ada_sm89",
    "nvidia geforce rtx 4070 ti super": "ada_sm89",
    "nvidia geforce rtx 4080": "ada_sm89",
    "nvidia geforce rtx 4080 super": "ada_sm89",
    "nvidia geforce rtx 4090": "ada_sm89",
    # NVIDIA Hopper.
    "nvidia h100": "hopper_sm90",
    "nvidia h100 80gb hbm3": "hopper_sm90",
    "nvidia h100 nvl": "hopper_sm90",
    "nvidia h200": "hopper_sm90",
    # NVIDIA Blackwell.
    "nvidia b200": "blackwell_sm100",
    "nvidia gb200": "blackwell_sm100",
    "nvidia geforce rtx 5060": "blackwell_sm120",
    "nvidia geforce rtx 5060 ti": "blackwell_sm120",
    "nvidia geforce rtx 5070": "blackwell_sm120",
    "nvidia geforce rtx 5070 ti": "blackwell_sm120",
    "nvidia geforce rtx 5080": "blackwell_sm120",
    "nvidia geforce rtx 5090": "blackwell_sm120",
}

UNSUPPORTED_GPU_MODELS: Mapping[str, str] = {
    "nvidia geforce gtx 1080": "sm_61",
    "nvidia geforce gtx 1080 ti": "sm_61",
    "nvidia tesla p100": "sm_60",
    "nvidia tesla v100": "sm_70",
}


def normalize_gpu_model(value: str) -> str:
    normalized = value.strip().lower()
    normalized = normalized.replace("(r)", "").replace("(tm)", "")
    normalized = normalized.replace("™", "").replace("®", "")
    normalized = re.sub(r"\s+", " ", normalized)
    return normalized


def resolve_gpu_profile(gpu_model: str) -> GpuBuildProfile:
    normalized = normalize_gpu_model(gpu_model)
    if not normalized:
        raise ValueError("GPU_MODEL is empty. Set GPU_MODEL in .env or pass --gpu-model.")
    if normalized in UNSUPPORTED_GPU_MODELS:
        arch = UNSUPPORTED_GPU_MODELS[normalized]
        raise UnsupportedGpuError(
            f"{gpu_model!r} ({arch}) is below the supported SAM3 GPU build floor. "
            "Use a Turing/sm_75 or newer NVIDIA GPU."
        )
    profile_name = GPU_MODELS.get(normalized)
    if profile_name is None:
        raise ValueError(
            f"Unsupported or unknown GPU_MODEL {gpu_model!r}. Add it to scripts/gpu_profiles.py "
            "with its compute capability and build profile."
        )
    return GPU_BUILD_PROFILES[profile_name]
