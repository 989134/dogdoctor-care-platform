import numpy as np


def smooth_person_data(detections, depth_map, x_window, y_window, z_window):
    """
    平滑人員的 X, Y 座標和深度值
    class_id = 0 代表「人」
    """
    for box, confidence, class_id in detections:
        if class_id == 0:  # 人
            x1, y1, x2, y2 = map(int, box)
            person_x = (x1 + x2) / 2
            person_y = (y1 + y2) / 2

            # 獲取深度值
            raw_depth_value = depth_map[int(person_y), int(person_x)]

            # 加入佇列
            x_window.append(person_x)
            y_window.append(person_y)
            z_window.append(raw_depth_value)

            # 計算平滑值
            smooth_x = np.mean(x_window)
            smooth_y = np.mean(y_window)
            smooth_depth = np.mean(z_window)

            return x1, y1, smooth_x, smooth_depth

    return None, None, None, None