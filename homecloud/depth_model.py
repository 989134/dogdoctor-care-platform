import torch
import numpy as np
from depth_anything_v2.dpt import DepthAnythingV2

# 載入 Depth Anything V2 模型
def load_depth_anything(model_path="models/depth_anything_v2_vits.pth", device="cuda" if torch.cuda.is_available() else "cpu"):
    depth_model = DepthAnythingV2(encoder='vits', features=64, out_channels=[48, 96, 192, 384])
    depth_model.load_state_dict(torch.load(model_path, map_location=device))
    depth_model.to(device).eval()
    print("DepthAnything is on:", next(depth_model.parameters()).device)
    return depth_model

# 深度推論函式
def estimate_depth(model, frame):
    depth = model.infer_image(frame, input_size=518)
    depth_normalized = (depth - depth.min()) / (depth.max() - depth.min()) * 255.0
    depth_normalized = depth_normalized.astype(np.uint8)
    return depth_normalized