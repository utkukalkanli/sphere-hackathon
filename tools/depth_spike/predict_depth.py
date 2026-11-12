"""Spike, part 2: a pretrained single-image depth model on the six straight views of each case.

Runs in its own environment (torch, transformers); writes <out>/<case>_pred.npz with `<view>` arrays
of the model's relative depth (larger = nearer) at SIZE px.

    python tools/depth_spike/predict_depth.py <inputs dir> <out dir> <model id> test_case_236 ...
"""

import sys
import time
from pathlib import Path

import numpy as np
import torch
from PIL import Image
from transformers import AutoImageProcessor, AutoModelForDepthEstimation

SIZE = 512
VIEWS = ("front", "back", "left", "right", "top", "bottom")

inputs, out, model_id, names = Path(sys.argv[1]), Path(sys.argv[2]), sys.argv[3], sys.argv[4:]
processor = AutoImageProcessor.from_pretrained(model_id)
model = AutoModelForDepthEstimation.from_pretrained(model_id).cuda().eval()
tag = model_id.split("/")[-1]
started = time.time()
for name in names:
    arrays = {}
    for view in VIEWS:
        path = next((inputs / name / "images").glob(f"view_*_{view}.png"))
        image = Image.open(path).convert("RGB")
        batch = processor(images=image, return_tensors="pt").to("cuda")
        with torch.no_grad():
            depth = model(**batch).predicted_depth
        depth = torch.nn.functional.interpolate(depth[None], size=(SIZE, SIZE), mode="bicubic", align_corners=False)[0, 0]
        arrays[view] = depth.float().cpu().numpy()
    np.savez_compressed(out / f"{name}_pred_{tag}.npz", **arrays)
print(f"{len(names)} cases, {6 * len(names)} views in {time.time() - started:.1f} s with {model_id}")
