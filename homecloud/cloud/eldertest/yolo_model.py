from ultralytics import YOLO
import torch

# 載入 YOLOv9 模型
def load_yolo(model_path="models/best.pt", device="cuda" if torch.cuda.is_available() else "cpu"):
    model = YOLO(model_path).to(device)
    print("YOLO is on:", next(model.model.parameters()).device)
    return model

# 物件偵測函式
def detect_objects(model, frame):
    results = model(frame)

    detections = []
    for r in results:
        boxes = r.boxes.xyxy.cpu().numpy()  # 邊界框 (x1, y1, x2, y2)
        confidences = r.boxes.conf.cpu().numpy()  # 信心分數
        class_ids = r.boxes.cls.cpu().numpy().astype(int)  # 類別 ID (整數)

        for box, conf, class_id in zip(boxes, confidences, class_ids):
            detections.append((box, conf, class_id))

    return detections  # 回傳包含 (框, 信心分數, 類別 ID) 的列表
