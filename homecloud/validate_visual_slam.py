"""Run the offline monocular visual-localization B validation."""

import argparse
import json
from pathlib import Path

from navigation.visual_slam import RelativeDepthMasker, analyze_video


def main():
    parser = argparse.ArgumentParser(description="機器狗高度影片：單目視覺定位 B 版驗證")
    parser.add_argument("video", type=Path, help="鏡頭在室內移動並回到起點的影片")
    parser.add_argument("--calibration", type=Path, default=Path("calibration_data.pkl"))
    parser.add_argument("--no-calibration", action="store_true",
                        help="前端串流若已去畸變，改用估計內參避免重複校正")
    parser.add_argument("--output", type=Path, default=Path("navigation/output/visual_slam"))
    parser.add_argument("--frame-step", type=int, default=2, help="每幾個來源影格分析一次")
    parser.add_argument("--max-frames", type=int, default=0, help="0 表示分析完整影片")
    parser.add_argument("--route-length-m", type=float, help="已量測的錄影行走總長；只做近似整體尺度")
    parser.add_argument("--depth", action="store_true", help="啟用 Depth Anything 相對近物特徵過濾")
    parser.add_argument("--depth-model", type=Path, default=Path("models/depth_anything_v2_vits.pth"))
    parser.add_argument("--depth-input-size", type=int, default=322)
    parser.add_argument("--frontend-recording", action="store_true",
                        help="排除左上狀態字與下方固定車體，僅用環境特徵")
    parser.add_argument("--duplicate-threshold", type=float,
                        help="跳過近似重複畫面的平均像素差；前端錄影預設 0.25")
    parser.add_argument("--minimum-motion-steps", type=int,
                        help="迴環判定前至少需要的有效移動步驟；前端錄影預設 8")
    args = parser.parse_args()

    if not args.video.exists():
        parser.error(f"找不到影片：{args.video}")
    if not args.no_calibration and args.calibration and not args.calibration.exists():
        parser.error(f"找不到校正檔：{args.calibration}")
    depth = None
    if args.depth:
        if not args.depth_model.exists():
            parser.error(f"找不到 Depth 模型：{args.depth_model}")
        depth = RelativeDepthMasker(args.depth_model, input_size=args.depth_input_size)
    exclusions = ()
    if args.frontend_recording:
        exclusions = (
            (0.0, 0.0, 0.46, 0.39),       # fixed status overlay
            (0.32, 0.68, 0.68, 1.0),      # camera/robot mount in foreground
        )
    duplicate_threshold = args.duplicate_threshold
    if duplicate_threshold is None:
        duplicate_threshold = 0.25 if args.frontend_recording else 0.0
    minimum_motion_steps = args.minimum_motion_steps
    if minimum_motion_steps is None:
        minimum_motion_steps = 8 if args.frontend_recording else 20
    report = analyze_video(
        args.video, args.output, None if args.no_calibration else args.calibration,
        args.frame_step, args.max_frames, args.route_length_m, depth, exclusions,
        duplicate_threshold, minimum_motion_steps,
    )
    summary = {
        "assessment": report["assessment"], "quality": report["quality"],
        "loop_check": report["loop_check"], "scale": report["scale"],
        "report": str((args.output / "report.html").resolve()),
    }
    print(json.dumps(summary, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
