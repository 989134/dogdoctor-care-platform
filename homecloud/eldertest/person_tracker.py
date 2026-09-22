import cv2
import numpy as np
import logging
from collections import deque
import config

logger = logging.getLogger(__name__)


class PersonTracker:
    def __init__(self):
        # 追蹤參數
        self.person_class_id = config.PERSON_CLASS_ID
        self.follow_tolerance_x = config.FOLLOW_TOLERANCE_X
        self.follow_tolerance_y = config.FOLLOW_TOLERANCE_Y

        # 深度目標
        self.target_depth_min = config.FOLLOW_TARGET_DEPTH_MIN
        self.target_depth_max = config.FOLLOW_TARGET_DEPTH_MAX
        self.safe_depth = config.FOLLOW_SAFE_DEPTH

        # 平滑窗口
        self.x_window = deque(maxlen=5)
        self.y_window = deque(maxlen=5)
        self.depth_window = deque(maxlen=5)

        # 追蹤狀態
        self.is_tracking = False
        self.last_seen_time = 0
        self.tracked_person_id = None

    def find_person(self, detections):
        """從 YOLO 檢測結果中找到主人"""
        if not detections:
            self.is_tracking = False
            return None

        # 尋找置信度最高的人
        best_person = None
        best_confidence = 0

        for det in detections:
            if len(det) >= 6:
                box, conf, class_id = det[0], det[4], det[5]
            elif len(det) >= 5:
                box, conf = det[0], det[4]
                class_id = self.person_class_id
            else:
                continue

            if class_id == self.person_class_id and conf > best_confidence:
                best_confidence = conf
                best_person = det

        if best_person:
            self.is_tracking = True
            self.last_seen_time = cv2.getTickCount() / cv2.getTickFrequency()
        else:
            self.is_tracking = False

        return best_person

    def get_person_position(self, person_det, depth_map):
        """獲取主人位置和深度"""
        if not person_det:
            return None, None, None

        # 解析檢測框
        if len(person_det) >= 4:
            x1, y1, x2, y2 = person_det[0], person_det[1], person_det[2], person_det[3]
        else:
            return None, None, None

        # 計算中心點
        cx = (x1 + x2) / 2
        cy = (y1 + y2) / 2

        # 獲取深度值
        if depth_map is not None:
            h, w = depth_map.shape
            cy_int = min(max(int(cy), 0), h - 1)
            cx_int = min(max(int(cx), 0), w - 1)
            depth = depth_map[cy_int, cx_int]
        else:
            depth = None

        # 平滑處理
        self.x_window.append(cx)
        self.y_window.append(cy)
        if depth is not None:
            self.depth_window.append(depth)

        smooth_x = np.mean(self.x_window) if self.x_window else cx
        smooth_y = np.mean(self.y_window) if self.y_window else cy
        smooth_depth = np.mean(self.depth_window) if self.depth_window else depth

        return smooth_x, smooth_y, smooth_depth

    def get_follow_command(self, person_x, person_y, depth, frame_width, frame_height):
        """根據主人位置生成跟隨指令"""
        if person_x is None or depth is None:
            return None, "lost"

        center_x = frame_width // 2
        center_y = frame_height // 2

        # 檢查是否在鏡頭內
        in_frame = (0 < person_x < frame_width) and (0 < person_y < frame_height)

        if not in_frame:
            return None, "out_of_frame"

        # 水平方向調整
        if person_x < center_x - self.follow_tolerance_x:
            command = "left"
            status = "adjusting_left"
        elif person_x > center_x + self.follow_tolerance_x:
            command = "right"
            status = "adjusting_right"
        else:
            command = None
            status = "centered_x"

        # 深度方向調整
        if depth < self.target_depth_min:
            # 太近，後退
            if command is None:
                command = "backward"
            status = "too_close"
        elif depth > self.target_depth_max:
            # 太遠，前進
            if command is None:
                command = "forward"
            status = "too_far"
        else:
            # 距離合適
            if command is None:
                command = "stop"
            status = "good_distance"

        return command, status

    def draw_tracking_box(self, frame, person_det, status):
        """繪製追蹤框"""
        if not person_det:
            cv2.putText(frame, "❌ Person Lost", (10, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            return frame

        x1, y1, x2, y2 = map(int, [person_det[0], person_det[1],
                                   person_det[2], person_det[3]])

        # 根據狀態選擇顏色
        if status == "good_distance":
            color = (0, 255, 0)  # 綠色
        elif status in ["too_close", "too_far"]:
            color = (0, 255, 255)  # 黃色
        else:
            color = (0, 0, 255)  # 紅色

        # 繪製框
        cv2.rectangle(frame, (x1, y1), (x2, y2), color, 3)

        # 繪製中心點
        cx, cy = (x1 + x2) // 2, (y1 + y2) // 2
        cv2.circle(frame, (cx, cy), 5, color, -1)

        # 繪製狀態文字
        label = f"Person: {status}"
        cv2.putText(frame, label, (x1, y1 - 10),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.6, color, 2)

        return frame

    def process(self, frame, detections, depth_map):
        """處理單幀影像"""
        H, W = frame.shape[:2]

        # 尋找主人
        person_det = self.find_person(detections)

        # 獲取位置
        person_x, person_y, depth = self.get_person_position(person_det, depth_map)

        # 生成跟隨指令
        command, status = self.get_follow_command(person_x, person_y, depth, W, H)

        # 繪製結果
        frame = self.draw_tracking_box(frame, person_det, status)

        # 顯示深度資訊
        if depth is not None:
            cv2.putText(frame, f"Depth: {depth:.1f}", (W - 150, 30),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)

        return frame, command, status, person_x, person_y, depth

    def reset(self):
        """重置追蹤狀態"""
        self.x_window.clear()
        self.y_window.clear()
        self.depth_window.clear()
        self.is_tracking = False
        self.last_seen_time = 0


# 全域實例
person_tracker = PersonTracker()