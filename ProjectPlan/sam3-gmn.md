# SAM3 + Geospatial Foundation Models (GMN) Inference Endpoint

## 1. Architecture Overview

**Goal:** Create a new inference endpoint `inference-sam3-gmn` that leverages the Segment Anything Model 3 (SAM3) for class-agnostic segmentation (zero-shot object localization), and subsequently assigns semantic labels to each detected region using fused feature embeddings extracted from Prithvi-EO-2.0 and DINOv3. 

**Core Constraint:** Do not train or fine-tune any models. Use the specified pre-trained models exclusively for inference and zero-shot labeling.

**Components:**
1. **Localization Engine (SAM3):** Utilizes `sam3` to generate high-quality class-agnostic masks and bounding boxes from the input satellite or aerial image.
2. **Crop & Align Service:** Extracts the regions of interest (ROIs) corresponding to the SAM3 masks.
3. **Dual Feature Extractor:**
   - **Prithvi-EO-2.0** (`ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL` or `300M`): Extracts geospatial-specific temporal/spectral feature vectors.
   - **DINOv3** (`facebook/dinov3-vit7b16-pretrain-lvd1689m`): Extracts highly robust generic visual features.
4. **Zero-Shot Classifier:** Concatenates and normalizes the features from both foundation models, computing cosine similarity against pre-computed exemplar anchor embeddings for the target taxonomy to determine the final label.

## 2. High-Level Plan

1. **Service Initialization & Infrastructure:**
   - Create a new microservice directory `inference-sam3-gmn`.
   - Define a `Dockerfile.gpu` with the necessary CUDA bases (e.g., `nvidia/cuda:12.1.1-cudnn8-devel-ubuntu22.04`).
   - Configure a `requirements.txt` locking in PyTorch, `transformers`, `timm`, and the SAM3 GitHub dependency.
   - Add environment variables to support dynamic loading of Hugging Face weights.

2. **Model Loading Strategy:**
   - Load models into GPU memory during the FastAPI startup event.
   - Apply `torch.float16` or `torch.bfloat16` to the DINOv3 and Prithvi backbones to constrain VRAM usage.
   - Optionally apply `torch.compile()` for optimized throughput during the feature extraction phase.

3. **Inference Pipeline (FastAPI `/detect`):**
   - **Phase A (Segmentation):** Pass the raw image to SAM3 using the Automatic Mask Generation (AMG) pipeline to extract all distinct object masks.
   - **Phase B (Feature Fusing):** For each mask, generate a tight image crop. Pass batches of crops to DINOv3 and Prithvi-EO. Extract the `[CLS]` token or pooled `last_hidden_state`. Concatenate the vectors to form a unified geospatial-visual representation.
   - **Phase C (Similarity Matching):** Compare the fused representation against a loaded JSON vocabulary containing averaged exemplar embeddings for "all the labels that can be detected" (e.g., airplanes, ships, buildings). Assign the highest-scoring label if it exceeds a minimum confidence threshold.

4. **SentinelOS Backend Integration:**
   - Add `sam3-gmn` to `_KNOWN_INFERENCE_PROVIDERS` in `backend/main.py`.
   - Expose the endpoint URL via `INFERENCE_SAM3_GMN_URL` in `backend/worker.py` and `provider_lifecycle.py`.
   - Update the Docker Compose configuration to include the new container.

## 3. Low-Level Plan & Pseudocode

### 3.1 Dependencies
```text
fastapi==0.103.1
uvicorn==0.23.2
torch>=2.1.0
torchvision
transformers
timm
git+https://github.com/facebookresearch/sam3.git
```

### 3.2 Service Implementation (`main.py`)
```python
import os
import torch
import torch.nn.functional as F
from fastapi import FastAPI, UploadFile, File
from transformers import AutoModel
# Assume sam3 provides a similar API to sam2
from sam3 import build_sam3, Sam3AutomaticMaskGenerator 

app = FastAPI(title="SentinelOS AIP Node - SAM3+GMN Inference")

# 1. Load Models (Pseudocode for startup event)
@app.on_event("startup")
def load_models():
    global sam3_generator, dino_model, prithvi_model, class_anchors
    
    device = "cuda" if torch.cuda.is_available() else "cpu"
    
    # Load SAM3
    sam_model = build_sam3(checkpoint="sam3_weights.pt").to(device)
    sam3_generator = Sam3AutomaticMaskGenerator(sam_model)
    
    # Load Foundation Models
    dino_model = AutoModel.from_pretrained(
        "facebook/dinov3-vit7b16-pretrain-lvd1689m", 
        torch_dtype=torch.float16
    ).eval().to(device)
    
    prithvi_model = AutoModel.from_pretrained(
        "ibm-nasa-geospatial/Prithvi-EO-2.0-600M-TL", 
        trust_remote_code=True,
        torch_dtype=torch.float16
    ).eval().to(device)
    
    # Load Pre-computed class anchors (embeddings for zero-shot)
    class_anchors = load_anchor_embeddings("models/anchors.pt") 

def extract_fused_features(crop_tensor: torch.Tensor) -> torch.Tensor:
    with torch.no_grad():
        # Get DINOv3 features
        dino_out = dino_model(crop_tensor)
        dino_feat = dino_out.last_hidden_state.mean(dim=1)
        
        # Get Prithvi features
        prithvi_out = prithvi_model(crop_tensor)
        prithvi_feat = prithvi_out.last_hidden_state.mean(dim=1)
        
        # Fuse and normalize
        fused = torch.cat([dino_feat, prithvi_feat], dim=-1)
        return F.normalize(fused, p=2, dim=1)

@app.post("/detect")
async def detect(image: UploadFile = File(...)):
    image_array = decode_image(await image.read())
    image_tensor = preprocess_for_backbones(image_array).cuda()
    
    # Phase A: Generate all class-agnostic masks
    masks = sam3_generator.generate(image_array)
    
    results = []
    for mask_data in masks:
        bbox = mask_data["bbox"] # [x, y, w, h]
        
        # Crop image to bounding box
        crop = crop_image(image_tensor, bbox)
        
        # Phase B: Extract features
        feat = extract_fused_features(crop)
        
        # Phase C: Zero-shot labeling via cosine similarity
        best_label = "unknown"
        best_score = 0.0
        
        for label, anchor in class_anchors.items():
            score = torch.dot(feat.squeeze(), anchor.squeeze()).item()
            if score > best_score:
                best_score = score
                best_label = label
                
        if best_score > 0.65: # Configurable threshold
            results.append({
                "label": best_label,
                "confidence": round(best_score, 4),
                "box": bbox,
                "segmentation": mask_data["segmentation"]
            })
            
    return {"detections": results}
```

### 3.3 Target Taxonomy

The zero-shot labeling approach supports dynamic vocabularies. The user can specify any list of labels, and the system will cross-reference the feature similarity. By default, it will support the expansive SentinelOS detection taxonomy (e.g., Maritime Vessels, Aircraft, Vehicles, Fixed Installations).