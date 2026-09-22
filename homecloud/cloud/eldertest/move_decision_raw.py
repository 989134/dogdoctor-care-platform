# def move_decision_raw(smooth_x, smooth_raw_depth, frame_width):
#     """
#     使用「原始深度值」來進行決策，完全不使用公制單位。
#     """
#
#     tolerance = 50  # 容差範圍，單位為像素
#
#     if smooth_x is not None and smooth_raw_depth is not None:
#         center_x = (frame_width // 2)  # 畫面中心點的X座標
#
#         #  1. 優先處理轉向
#         if smooth_x < center_x - tolerance:
#             return "left"
#         elif smooth_x > center_x + tolerance:
#             return "right"
#
#         # 3. 球已在正前方，開始根據「原始深度」判斷距離
#
#         if smooth_raw_depth < 40 :
#             return "pre"
#
#         elif smooth_raw_depth < 200 :
#             # 情況一：太遠
#             return "forward"
#
#         else:
#             # 情況三：剛剛好
#             # 機器人處於「撿球區間」
#             return "pick" # 準備撿球
#
#     else:
#         return None  # 無球
# move_decision_raw.py
# move_decision_raw.py
def move_decision_raw(smooth_x, smooth_raw_depth, frame_width):
    tolerance = 60  # 放寬容差

    if smooth_x is None or smooth_raw_depth is None:
        return None

    center_x = frame_width // 2

    # ✅ 修正：球偏左 → 車子「右轉」；球偏右 → 車子「左轉」
    if smooth_x < center_x - tolerance:
        return "right"   # 車子右轉，讓鏡頭往右看，把球拉回中心
    elif smooth_x > center_x + tolerance:
        return "left"    # 車子左轉

    # 距離判斷（仍用 raw_depth，但你可改為 meter）
    if smooth_raw_depth >= 180:      # 近（≥180 → 30cm 內）
        return "pick"
    elif smooth_raw_depth >= 80:     # 中（80~180）
        return "forward"
    else:                            # 遠（<80）
        return "pre"