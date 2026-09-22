import cv2
import numpy as np
import logging
import config

logger = logging.getLogger(__name__)


class ObstacleAvoidance:
    def __init__(self):
        # 避障參數
        self.warning_depth = config.OBSTACLE_WARNING_DEPTH
        self.stop_depth = config.OBSTACLE_STOP_DEPTH
        self.enabled = config.OBSTACLE_DETECTION_ENABLED

        # 檢測區域
        self.detection_zones = {
            'center': (0.4, 0.6, 0.5, 1.0),  # (x1, x2, y1, y2) 比例
            'left': (0.0, 0.4, 0.5, 1.0),
            'right': (0.6, 1.0, 0.5, 1.0)
        }

    def analyze_depth_map(self, depth_map):
        """分析深度圖檢測障礙物"""
        if depth_map is None or not self.enabled:
            return None, "safe"

        H, W = depth_map.shape

        # 檢查不同區域
        zone_status = {}

        for zone_name, (x1_ratio, x2_ratio, y1_ratio, y2_ratio) in self.detection_zones.items():
            x1, x2 = int(W * x1_ratio), int(W * x2_ratio)
            y1, y2 = int(H * y1_ratio), int(H * y2_ratio)

            # 提取區域深度
            roi = depth_map[y1:y2, x1:x2]

            if roi.size == 0:
                zone_status[zone_name] = {'depth': None, 'status': 'unknown'}
                continue

            # 計算最小深度（最近的物體）
            min_depth = np.min(roi)
            avg_depth = np.mean(roi)

            # 判斷狀態
            if min_depth < self.stop_depth:
                status = 'danger'
            elif min_depth < self.warning_depth:
                status = 'warning'
            else:
                status = 'safe'

            zone_status[zone_name] = {
                'depth': min_depth,
                'avg_depth': avg_depth,
                'status': status
            }

        return zone_status, self.get_avoidance_command(zone_status)

    def get_avoidance_command(self, zone_status):
        """根據區域狀態生成避障指令"""
        if not zone_status:
            return None

        # 檢查中心區域
        center = zone_status.get('center', {})
        left = zone_status.get('left', {})
        right = zone_status.get('right', {})

        # 中心危險
        if center.get('status') == 'danger':
            # 選擇較安全的一側
            left_depth = left.get('depth', 999) or 999
            right_depth = right.get('depth', 999) or 999

            if left_depth > right_depth:
                return 'left'
            else:
                return 'right'

        # 中心警告
        if center.get('status') == 'warning':
            return 'slow'

        # 左側危險
        if left.get('status') == 'danger':
            return 'right'

        # 右側危險
        if right.get('status') == 'danger':
            return 'left'

        return None

    def draw_obstacle_overlay(self, frame, zone_status, depth_map):
        """繪製障礙物覆蓋層"""
        if depth_map is None:
            return frame

        H, W = frame.shape[:2]

        # 繪製檢測區域
        for zone_name, (x1_ratio, x2_ratio, y1_ratio, y2_ratio) in self.detection_zones.items():
            x1, x2 = int(W * x1_ratio), int(W * x2_ratio)
            y1, y2 = int(H * y1_ratio), int(H * y2_ratio)

            # 獲取狀態
            status = zone_status.get(zone_name, {}).get('status', 'unknown')

            # 根據狀態選擇顏色
            if status == 'danger':
                color = (0, 0, 255)  # 紅色
                alpha = 0.5
            elif status == 'warning':
                color = (0, 255, 255)  # 黃色
                alpha = 0.3
            else:
                color = (0, 255, 0)  # 綠色
                alpha = 0.1

            # 繪製覆蓋層
            overlay = frame.copy()
            cv2.rectangle(overlay, (x1, y1), (x2, y2), color, -1)
            cv2.addWeighted(overlay, alpha, frame, 1 - alpha, 0, frame)

            # 繪製邊框
            cv2.rectangle(frame, (x1, y1), (x2, y2), color, 2)

            # 繪製深度值
            depth_val = zone_status.get(zone_name, {}).get('depth')
            if depth_val is not None:
                label = f"{zone_name}: {depth_val:.1f}"
                cv2.putText(frame, label, (x1, y1 - 5),
                            cv2.FONT_HERSHEY_SIMPLEX, 0.5, color, 1)

        return frame

    def process(self, frame, depth_map):
        """處理單幀影像"""
        # 分析深度圖
        zone_status, command = self.analyze_depth_map(depth_map)

        # 繪製覆蓋層
        if zone_status:
            frame = self.draw_obstacle_overlay(frame, zone_status, depth_map)

        # 顯示避障狀態
        if command:
            status_text = f"🚧 Avoid: {command}"
            color = (0, 0, 255) if command in ['left', 'right'] else (0, 255, 255)
        else:
            status_text = "✅ Path Clear"
            color = (0, 255, 0)

        cv2.putText(frame, status_text, (10, frame.shape[0] - 100),
                    cv2.FONT_HERSHEY_SIMPLEX, 0.7, color, 2)

        return frame, command, zone_status


# 全域實例
obstacle_avoidance = ObstacleAvoidance()