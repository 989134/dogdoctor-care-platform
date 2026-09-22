# 畫YOLO框
# import cv2
# def draw_yolo_detections(frame, detections, class_names):
#     for box, confidence, class_id in detections:
#         x1, y1, x2, y2 = map(int, box)
#         cv2.rectangle(frame, (x1, y1), (x2, y2), (0, 255, 0), 2)
#         label = f"{class_names.get(class_id, 'Unknown')}: {confidence:.2f}"
#         cv2.putText(frame, label, (x1, y1 - 10),
#                     cv2.FONT_HERSHEY_SIMPLEX, 0.5, (0, 255, 0), 2)
#     return frame
# draw_yolo_detections.py
import cv2

def draw_yolo_detections(frame, detections, class_names):
    if not detections:
        return frame

    for det in detections:
        if len(det) == 6:
            x1, y1, x2, y2, confidence, class_id = det
        elif len(det) == 5:
            x1, y1, x2, y2, confidence = det
            class_id = 0
        else:
            continue

        x1, y1, x2, y2 = map(int, [x1, y1, x2, y2])
        color = (0, 255, 0)
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)
        label = f"{class_names.get(class_id, 'Unknown')}: {confidence:.2f}"
        cv2.putText(frame, label, (x1, y1 - 10), cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 2)
    return frame