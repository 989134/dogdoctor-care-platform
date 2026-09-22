import cv2
import numpy as np
import mediapipe as mp
import time
import logging
from collections import deque
import config

logger = logging.getLogger(__name__)


class FallDetector:
    def __init__(self):
        self.mp_pose = mp.solutions.pose
        self.pose = self.mp_pose.Pose(
            static_image_mode=False,
            min_detection_confidence=0.5,
            min_tracking_confidence=0.5
        )
        self.mp_draw = mp.solutions.drawing_utils

        # 狀態追蹤
        self.fall_start_time = None
        self.is_falling = False
        self.last_movement_time = time.time()
        self.last_position = None
        self.position_history = deque(maxlen=30)  # 1 秒的位置歷史

        # 跌倒閾值
        self.fall_angle_threshold = config.FALL_ANGLE_THRESHOLD
        self.fall_duration_threshold = config.FALL_DURATION_THRESHOLD
        self.immobile_duration_threshold = config.IMMOBILE_DURATION_THRESHOLD

    def detect_pose(self, frame):
        """檢測人體姿態"""
        rgb_frame = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
        results = self.pose.process(rgb_frame)
        return results

    def calculate_body_angle(self, landmarks):
        """計算身體傾斜角度（肩膀到臀部）"""
        if not landmarks:
            return None

        try:
            # 獲取關鍵點
            left_shoulder = landmarks[self.mp_pose.PoseLandmark.LEFT_SHOULDER.value]
            right_shoulder = landmarks[self.mp_pose.PoseLandmark.RIGHT_SHOULDER.value]
            left_hip = landmarks[self.mp_pose.PoseLandmark.LEFT_HIP.value]
            right_hip = landmarks[self.mp_pose.PoseLandmark.RIGHT_HIP.value]

            # 計算肩膀和臀部的中心點
            shoulder_center = ((left_shoulder.x + right_shoulder.x) / 2,
                               (left_shoulder.y + right_shoulder.y) / 2)
            hip_center = ((left_hip.x + right_hip.x) / 2,
                          (left_hip.y + right_hip.y) / 2)

            # 計算垂直角度
            dx = shoulder_center[0] - hip_center[0]
            dy = shoulder_center[1] - hip_center[1]

            # 計算角度（0 度 = 直立，90 度 = 水平）
            angle = np.abs(np.arctan2(dx, dy) * 180 / np.pi)

            return angle

        except Exception as e:
            logger.error(f"計算身體角度失敗：{e}")
            return None

    def check_fall(self, landmarks, frame_shape):
        """檢查是否跌倒"""
        angle = self.calculate_body_angle(landmarks)

        if angle is None:
            return False, None

        H, W = frame_shape[:2]

        # 檢查角度是否小於閾值（表示身體接近水平）
        is_fall_angle = angle < self.fall_angle_threshold

        # 檢查是否在地面附近（y 座標大於畫面 70%）
        hip_y = (landmarks[self.mp_pose.PoseLandmark.LEFT_HIP.value].y +
                 landmarks[self.mp_pose.PoseLandmark.RIGHT_HIP.value].y) / 2
        is_on_ground = hip_y > 0.7

        is_falling = is_fall_angle and is_on_ground

        if is_falling:
            if self.fall_start_time is None:
                self.fall_start_time = time.time()
                logger.info("開始檢測跌倒...")

            duration = time.time() - self.fall_start_time

            if duration >= self.fall_duration_threshold:
                logger.warning(f"確認跌倒！持續時間：{duration:.2f}秒")
                return True, duration
        else:
            self.fall_start_time = None

        return False, None

    def check_immobile(self, landmarks, frame_shape):
        """檢查是否長時間不動"""
        if not landmarks:
            return False, 0

        try:
            # 獲取鼻子位置作為參考點
            nose = landmarks[self.mp_pose.PoseLandmark.NOSE.value]
            current_position = (nose.x, nose.y)

            # 記錄位置
            self.position_history.append(current_position)

            # 計算移動距離
            if self.last_position:
                dx = current_position[0] - self.last_position[0]
                dy = current_position[1] - self.last_position[1]
                distance = np.sqrt(dx ** 2 + dy ** 2)

                # 如果有明顯移動
                if distance > 0.02:  # 2% 畫面寬度
                    self.last_movement_time = time.time()

            self.last_position = current_position

            # 計算不動時間
            immobile_duration = time.time() - self.last_movement_time

            if immobile_duration >= self.immobile_duration_threshold:
                logger.warning(f"長時間未活動：{immobile_duration:.2f}秒")
                return True, immobile_duration

            return False, immobile_duration

        except Exception as e:
            logger.error(f"檢查不動狀態失敗：{e}")
            return False, 0

    def draw_pose_landmarks(self, frame, results):
        """繪製姿態關鍵點"""
        if results.pose_landmarks:
            self.mp_draw.draw_landmarks(
                frame,
                results.pose_landmarks,
                self.mp_pose.POSE_CONNECTIONS,
                landmark_drawing_spec=self.mp_draw.DrawingSpec(
                    color=(255, 0, 0), thickness=2, circle_radius=2
                ),
                connection_drawing_spec=self.mp_draw.DrawingSpec(
                    color=(0, 255, 0), thickness=2
                )
            )
        return frame

    def draw_status(self, frame, is_falling, fall_duration, is_immobile, immobile_duration):
        """繪製檢測狀態"""
        H, W = frame.shape[:2]

        # 跌倒狀態
        if is_falling:
            cv2.putText(frame, "⚠️ FALL DETECTED!", (10, H - 80),
                        cv2.FONT_HERSHEY_SIMPLEX, 1, (0, 0, 255), 2)
            cv2.putText(frame, f"Duration: {fall_duration:.1f}s", (10, H - 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        else:
            cv2.putText(frame, "✅ Normal Posture", (10, H - 80),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 255, 0), 2)

        # 不動狀態
        if is_immobile:
            cv2.putText(frame, "⚠️ IMMOBILE!", (W - 200, H - 80),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.8, (0, 0, 255), 2)
            cv2.putText(frame, f"{immobile_duration:.0f}s", (W - 200, H - 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.7, (0, 0, 255), 2)
        else:
            cv2.putText(frame, f"Moving: {immobile_duration:.1f}s", (W - 200, H - 50),
                        cv2.FONT_HERSHEY_SIMPLEX, 0.6, (255, 255, 0), 2)

        return frame

    def process(self, frame):
        """處理單幀影像"""
        results = self.detect_pose(frame)

        is_falling = False
        fall_duration = 0
        is_immobile = False
        immobile_duration = 0

        if results.pose_landmarks:
            landmarks = results.pose_landmarks.landmark

            # 檢查跌倒
            is_falling, fall_duration = self.check_fall(landmarks, frame.shape)

            # 檢查不動
            is_immobile, immobile_duration = self.check_immobile(landmarks, frame.shape)

        # 繪製結果
        frame = self.draw_pose_landmarks(frame, results)
        frame = self.draw_status(frame, is_falling, fall_duration,
                                 is_immobile, immobile_duration)

        return frame, is_falling, fall_duration, is_immobile, immobile_duration

    def close(self):
        """釋放資源"""
        self.pose.close()


# 全域實例
fall_detector = FallDetector()