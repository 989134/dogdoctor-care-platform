def move_decision_person(smooth_x, smooth_depth, frame_width):
    """
    人員跟隨決策邏輯
    """
    tolerance = 100  # 水平容差（像素）

    if smooth_x is None or smooth_depth is None:
        return None

    center_x = frame_width // 2

    # 水平方向調整
    if smooth_x < center_x - tolerance:
        return "right"  # 人偏左，車右轉
    elif smooth_x > center_x + tolerance:
        return "left"  # 人偏右，車左轉

    # 深度方向調整（根據您的深度值範圍調整）
    if smooth_depth < 80:  # 太近
        return "backward"
    elif smooth_depth > 180:  # 太遠
        return "forward"
    else:  # 距離合適
        return "stop"