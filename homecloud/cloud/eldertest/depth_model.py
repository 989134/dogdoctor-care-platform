# import torch
# import numpy as np
# from depth_anything_v2.dpt import DepthAnythingV2
#
# # 載入 Depth Anything V2 模型
# def load_depth_anything(model_path="models/depth_anything_v2_vits.pth", device="cuda" if torch.cuda.is_available() else "cpu"):
#     model_configs = {
#         'vits': {'encoder': 'vits', 'features': 64, 'out_channels': [48, 96, 192, 384]},
#         'vitb': {'encoder': 'vitb', 'features': 128, 'out_channels': [96, 192, 384, 768]},
#         'vitl': {'encoder': 'vitl', 'features': 256, 'out_channels': [256, 512, 1024, 1024]},
#         'vitg': {'encoder': 'vitg', 'features': 384, 'out_channels': [1536, 1536, 1536, 1536]}
#     }
#     depth_model = DepthAnythingV2(**model_configs['vits'])  # 使用 'vits' 模型
#     depth_model = depth_model.to(device).eval()
#     return depth_model
#
# # 深度推論函式
# def estimate_depth(model, frame):
#     depth = model.infer_image(frame, input_size=518)
#     #depth_normalized = (depth - depth.min()) / (depth.max() - depth.min()) * 255.0
#     #depth_normalized = depth_normalized.astype(np.uint8)
#     return depth

# depth_model.py
# depth_model.py
import torch
import numpy as np
import os
import sys

# === 添加 depth_anything_v2 到 Python 路徑 ===
current_dir = os.path.dirname(os.path.abspath(__file__))
depth_anything_path = os.path.join(current_dir, "depth_anything_v2")

if depth_anything_path not in sys.path:
    sys.path.insert(0, current_dir)

# === 載入 Depth Anything V2 模型 ===
try:
    from depth_anything_v2.dpt import DepthAnythingV2

    print("✅ 成功匯入 DepthAnythingV2")
except ImportError as e:
    print(f"❌ 匯入失敗：{e}")
    print("請確認 depth_anything_v2 資料夾存在於同一目錄")
    raise


def load_depth_anything(model_path="models/depth_anything_v2_vits.pth",
                        device="cuda" if torch.cuda.is_available() else "cpu"):
    """
    載入 Depth Anything V2 模型
    """
    # 檢查模型檔案是否存在
    if not os.path.exists(model_path):
        # 嘗試替代路徑
        alt_path = os.path.join(current_dir, model_path)
        if os.path.exists(alt_path):
            model_path = alt_path
            print(f"✅ 使用替代路徑：{model_path}")
        else:
            raise FileNotFoundError(
                f"模型檔案不存在：{model_path}\n請確認 models/ 資料夾中有 depth_anything_v2_vits.pth")

    print(f"📂 載入模型：{model_path}")
    print(f"💻 使用裝置：{device}")

    # 初始化模型
    depth_model = DepthAnythingV2(
        encoder='vits',
        features=64,
        out_channels=[48, 96, 192, 384]
    )

    # 載入權重 (添加 weights_only=False 避免警告)
    state_dict = torch.load(model_path, map_location=device, weights_only=False)
    depth_model.load_state_dict(state_dict)

    # 設置裝置和評估模式
    depth_model.to(device).eval()

    print(f"✅ DepthAnything 已載入到：{next(depth_model.parameters()).device}")
    return depth_model


def estimate_depth(model, frame, input_size=518):
    """
    深度推論函式
    輸入：model, frame (BGR), input_size
    輸出：depth_normalized (uint8, 0-255)
    """
    # 確保模型在評估模式
    model.eval()

    # 執行推論
    with torch.no_grad():
        depth = model.infer_image(frame, input_size=input_size)

    # 常態化到 0-255
    depth_min = depth.min()
    depth_max = depth.max()

    if depth_max - depth_min > 0:
        depth_normalized = (depth - depth_min) / (depth_max - depth_min) * 255.0
    else:
        depth_normalized = np.zeros_like(depth)

    # 轉換為 uint8
    depth_normalized = depth_normalized.astype(np.uint8)

    return depth_normalized