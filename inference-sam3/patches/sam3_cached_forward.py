"""Monkey-patch upstream SAM3 to skip the vision encoder when cached features
are supplied via the input.

The upstream image-model forward at `Sam3Image.forward(input)` runs the ViT-L+
encoder via `self.backbone.forward_image(input.img_batch)`. The encoder is
70-80% of total forward cost. When our wrapper has already encoded the image
for a previous chunk (same /detect request, many text prompts), it can stash
the ``backbone_out`` dict on the input and we skip the encoder this call.

This is a runtime patch — no upstream source edit, no rebuild required when
the file lives in our bind-mounted /app. Idempotent.
"""
from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger("sam3_cached_forward")


def install() -> bool:
    """Install the cached-forward patch on Sam3Image. Returns True on success.

    Safe to call multiple times across replica builds.
    """
    _install_input_dtype_patch()
    install_decoder_topk()
    try:
        from sam3.model.sam3_image import Sam3Image
    except ImportError as exc:
        logger.warning("Sam3Image import failed; skipping patch: %s", exc)
        return False

    if getattr(Sam3Image, "_cached_forward_patched", False):
        return True

    orig_forward = Sam3Image.forward

    def forward_with_cache(self, input):
        """Drop-in replacement for Sam3Image.forward.

        If ``input._cached_backbone_out`` is present, reuse it and skip the
        vision encoder. Text features are re-encoded every call because text
        differs per chunk. Otherwise fall through to the original forward.
        """
        cached = getattr(input, "_cached_backbone_out", None)
        if cached is None:
            return orig_forward(self, input)

        # ---- Cached-encoder fast path: mirror the original forward but
        # replace the image-encoder call with the stashed features. ----
        import torch
        from sam3.model.sam3_image import SAM3Output, Prompt

        device = self.device
        # Start from the cached image-features dict (deep-share, no copy).
        backbone_out = dict(cached)
        # img_batch is needed downstream for some ops (e.g. mask upsample
        # logic reads input.img_batch shape). Keep it on the input.
        backbone_out["img_batch_all_stages"] = input.img_batch
        num_frames = len(input.find_inputs)
        assert num_frames == 1

        text_outputs = self.backbone.forward_text(input.find_text_batch, device=device)
        backbone_out.update(text_outputs)

        previous_stages_out = SAM3Output(
            iter_mode=SAM3Output.IterMode.LAST_STEP_PER_STAGE
        )
        find_input = input.find_inputs[0]
        find_target = input.find_targets[0]

        if find_input.input_points is not None and find_input.input_points.numel() > 0:
            print("Warning: Point prompts are ignored in PCS.")

        num_interactive_steps = 0 if self.training else self.num_interactive_steps_val
        geometric_prompt = Prompt(
            box_embeddings=find_input.input_boxes,
            box_mask=find_input.input_boxes_mask,
            box_labels=find_input.input_boxes_label,
        )

        stage_outs = []
        for cur_step in range(num_interactive_steps + 1):
            if cur_step > 0:
                geometric_prompt, _ = self.interactive_prompt_sampler.sample(
                    geo_prompt=geometric_prompt,
                    find_target=find_target,
                    previous_out=stage_outs[-1],
                )
            out = self.forward_grounding(
                backbone_out=backbone_out,
                find_input=find_input,
                find_target=find_target,
                geometric_prompt=geometric_prompt.clone(),
            )
            stage_outs.append(out)

        previous_stages_out.append(stage_outs)
        return previous_stages_out

    Sam3Image.forward = forward_with_cache
    Sam3Image._cached_forward_patched = True
    logger.info("Sam3Image.forward patched for cached-encoder reuse")
    return True


def is_installed() -> bool:
    try:
        from sam3.model.sam3_image import Sam3Image
        return bool(getattr(Sam3Image, "_cached_forward_patched", False))
    except ImportError:
        return False


def install_decoder_topk() -> None:
    """Top-K pruning over the SAM3 DETR decoder's 200 object queries.

    When ``SAM3_DECODER_TOPK > 0``, queries whose combined
    ``sigmoid(pred_logits) * sigmoid(presence_logits)`` falls outside the
    top-K are zeroed (pred_logits → -inf, pred_masks → -inf) so the
    downstream postprocessor drops them without doing mask sigmoid /
    threshold work for them.

    This is the *safe* version: we don't skip the mask-decoder upsample
    itself (that would require splicing the forward in upstream); we just
    zero the low-confidence outputs after they're produced. The win is
    purely in PostProcessImage — sigmoid + threshold + resize on 200×
    queries → on K× queries.

    Idempotent. No-op when SAM3_DECODER_TOPK <= 0.
    """
    import os
    try:
        K = int(os.getenv("SAM3_DECODER_TOPK", "0"))
    except ValueError:
        return
    if K <= 0:
        return
    try:
        # The decoder lives on Sam3Image as `.find_decoder` which holds the
        # decoder module; the forward_grounding helper returns a dict that
        # post-processing then chews through. The cleanest hook is to wrap
        # forward_grounding itself.
        from sam3.model.sam3_image import Sam3Image
    except ImportError:
        return
    if getattr(Sam3Image, "_topk_patched", False):
        return

    import torch
    orig_forward_grounding = Sam3Image.forward_grounding

    def patched_forward_grounding(self, *args, **kwargs):
        out = orig_forward_grounding(self, *args, **kwargs)
        try:
            if not isinstance(out, dict):
                return out
            pred_logits = out.get("pred_logits")
            if pred_logits is None or not isinstance(pred_logits, torch.Tensor):
                return out
            presence = out.get("presence_logit_dec")
            if isinstance(presence, torch.Tensor):
                # Broadcast presence (shape [B,1] or [B]) over the queries.
                presence_p = torch.sigmoid(presence).reshape(presence.shape[0], -1)
                if presence_p.shape[1] == 1:
                    combined = torch.sigmoid(pred_logits.squeeze(-1)) * presence_p
                else:
                    combined = torch.sigmoid(pred_logits.squeeze(-1)) * presence_p[:, :1]
            else:
                combined = torch.sigmoid(pred_logits.squeeze(-1))
            B, N = combined.shape[:2]
            k = min(K, N)
            _, topk_idx = combined.topk(k, dim=-1)
            keep = torch.zeros_like(combined, dtype=torch.bool)
            keep.scatter_(1, topk_idx, True)
            # Zero out non-topk pred_logits so postproc threshold drops them.
            out["pred_logits"] = pred_logits.masked_fill(
                ~keep.unsqueeze(-1), float("-inf"),
            )
            masks = out.get("pred_masks")
            if isinstance(masks, torch.Tensor):
                # masks shape: [B, N, ...]. Build a broadcastable keep mask.
                view_shape = [B, N] + [1] * (masks.dim() - 2)
                broadcast_keep = keep.view(*view_shape)
                out["pred_masks"] = masks.masked_fill(~broadcast_keep, float("-inf"))
        except Exception as exc:
            logger.warning("decoder_topk: skip iteration due to %s", exc)
        return out

    Sam3Image.forward_grounding = patched_forward_grounding
    Sam3Image._topk_patched = True
    logger.info("Sam3Image.forward_grounding patched: decoder top-K=%d", K)


def _install_input_dtype_patch() -> None:
    """Make Sam3Processor.set_image (and the batch variant) cast input to
    the model's weight dtype before the encoder runs.

    Required when the model is loaded in bf16 (via SAM3_NATIVE_BF16=1) —
    otherwise upstream calls ``model.backbone.forward_image(fp32_tensor)``
    against bf16 weights and dies with the classic input/weight-dtype
    mismatch. Idempotent. The patch is a thin wrapper around the original
    method; if Sam3Processor is missing we silently skip.
    """
    try:
        from sam3.model.sam3_image_processor import Sam3Processor
    except ImportError:
        return
    if getattr(Sam3Processor, "_dtype_cast_patched", False):
        return

    import torch

    def _model_dtype(processor) -> "torch.dtype":
        try:
            return next(processor.model.parameters()).dtype
        except Exception:
            return torch.float32

    orig_set_image = Sam3Processor.set_image
    orig_set_batch = Sam3Processor.set_image_batch

    @torch.inference_mode()
    def patched_set_image(self, image, state=None):
        if state is None:
            state = {}
        # Mirror the upstream call exactly but cast the tensor before the
        # backbone gets it.
        import PIL
        from torchvision.transforms import v2 as _v2
        from numpy import ndarray as _ndarray
        if isinstance(image, PIL.Image.Image):
            width, height = image.size
        elif isinstance(image, (torch.Tensor, _ndarray)):
            height, width = image.shape[-2:]
        else:
            raise ValueError("Image must be a PIL image or a tensor")

        img = _v2.functional.to_image(image).to(self.device)
        img = self.transform(img).unsqueeze(0)
        target_dtype = _model_dtype(self)
        if img.dtype != target_dtype:
            img = img.to(target_dtype)
        state["original_height"] = height
        state["original_width"] = width
        state["backbone_out"] = self.model.backbone.forward_image(img)
        inst_interactivity_en = self.model.inst_interactive_predictor is not None
        if inst_interactivity_en and "sam2_backbone_out" in state["backbone_out"]:
            sam2_backbone_out = state["backbone_out"]["sam2_backbone_out"]
            sam2_backbone_out["backbone_fpn"][0] = (
                self.model.inst_interactive_predictor.model.sam_mask_decoder.conv_s0(
                    sam2_backbone_out["backbone_fpn"][0]
                )
            )
            sam2_backbone_out["backbone_fpn"][1] = (
                self.model.inst_interactive_predictor.model.sam_mask_decoder.conv_s1(
                    sam2_backbone_out["backbone_fpn"][1]
                )
            )
        return state

    Sam3Processor.set_image = patched_set_image
    Sam3Processor._dtype_cast_patched = True
    logger.info("Sam3Processor.set_image patched to cast inputs to model dtype")


def encode_image_once(bundle: dict[str, Any], image_rgb_uint8) -> tuple[dict, Any]:
    """Build the image-side of a SAM3 datapoint once, run the encoder, and
    return ``(cached_backbone_out, transformed_img_tensor)``.

    Subsequent chunks reuse the returned tuple by stashing it on each new
    BatchedDatapoint via ``_cached_backbone_out`` attribute.

    Mirrors what `_run_text_prompts_batched` does for image preprocessing,
    minus the per-prompt text part. Returns the post-transform img tensor so
    the caller can put it on every chunk's BatchedDatapoint.
    """
    import numpy as np
    from PIL import Image
    import torch
    from sam3.model.utils.misc import copy_data_to_device
    from sam3.train.data.collator import collate_fn_api as collate
    from sam3.train.data.sam3_image_dataset import (
        Datapoint,
        Image as SAMImage,
    )
    from sam3.train.transforms.basic_for_api import (
        ComposeAPI,
        NormalizeAPI,
        RandomResizeAPI,
        ToTensorAPI,
    )

    device = bundle.get("device", "cpu")
    pil_image = Image.fromarray(image_rgb_uint8)
    width, height = pil_image.size
    datapoint = Datapoint(
        find_queries=[],
        images=[SAMImage(data=pil_image, objects=[], size=[height, width])],
    )
    transform = ComposeAPI(
        transforms=[
            RandomResizeAPI(sizes=1008, max_size=1008, square=True, consistent_transform=False),
            ToTensorAPI(),
            NormalizeAPI(mean=[0.5, 0.5, 0.5], std=[0.5, 0.5, 0.5]),
        ]
    )
    datapoint = transform(datapoint)
    # Collate with zero queries to get the image tensor on device.
    batch = collate([datapoint], dict_key="sam3")["sam3"]
    batch = copy_data_to_device(batch, torch.device(device), non_blocking=device.startswith("cuda"))

    model = bundle["sam3_image"]["model"]
    cached_backbone_out = {}
    cached_backbone_out["img_batch_all_stages"] = batch.img_batch
    cached_backbone_out.update(model.backbone.forward_image(batch.img_batch))
    return cached_backbone_out, batch.img_batch
