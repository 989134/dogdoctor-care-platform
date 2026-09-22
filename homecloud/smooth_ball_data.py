import numpy as np

def smooth_ball_data(detections, depth_map, x_window, y_window, z_window):
    """
    平滑球的 X, Y 座標和 "原始深度值 (Raw Depth)"。
    不再進行公制轉換 (a, b)。
    """
    for box, confidence, class_id in detections:
        if class_id == 0:  # 乒乓球
            x1, y1, x2, y2 = map(int, box)
            ball_x = (x1 + x2) / 2
            ball_y = (y1 + y2) / 2

            # 1. 獲取球中心的 "原始深度值"
            raw_depth_value = depth_map[int(ball_y), int(ball_x)]

            # 2. 將所有原始值加入佇列
            x_window.append(ball_x)
            y_window.append(ball_y)
            z_window.append(raw_depth_value) # 放入原始深度值

            # 3. 計算平滑後的 X, Y 和 "平滑後的原始深度"
            smooth_x = np.mean(x_window)
            smooth_y = np.mean(y_window)
            smooth_raw_depth = np.mean(z_window) # 這是平滑後的原始值

            # 4. 【關鍵】回傳平滑後的 "原始深度值"，而不是公尺
            return x1, y1, smooth_x, smooth_raw_depth

    return None, None, None, None  # 沒偵測到球

# def smooth_ball_data(detections, depth_map, x_win, y_win, z_win):
#     if not detections:
#         return None, None, None, None
#     det = detections[0]
#     x1, y1, x2, y2 = det[0], det[1], det[2], det[3]
#     cx, cy = (x1+x2)//2, (y1+y2)//2
#     x_win.append(cx); y_win.append(cy)
#     if len(x_win) > 5: x_win.pop(0); y_win.pop(0)
#     avg_x, avg_y = int(sum(x_win)/len(x_win)), int(sum(y_win)/len(y_win))
#
#     # ★ 重點：depth_map 現在是 metric depth (meter)
#     roi = depth_map[max(0,y1):min(y2,depth_map.shape[0]), max(0,x1):min(x2,depth_map.shape[1])]
#     if roi.size == 0:
#         avg_z = None
#     else:
#         avg_z = float(np.mean(roi))  # unit: meter
#
#     z_win.append(avg_z)
#     if len(z_win) > 5: z_win.pop(0)
#     valid_z = [z for z in z_win if z is not None]
#     avg_z_smooth = float(np.mean(valid_z)) if valid_z else None
#
#     return avg_x, avg_y, avg_x, avg_z_smooth  # ← 最後一個是 meter！