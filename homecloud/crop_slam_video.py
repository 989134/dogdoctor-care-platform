"""Crop a front-end screen recording to its camera panel."""

import argparse
from pathlib import Path

import cv2


def crop_video(source: Path, output: Path, crop: tuple[int, int, int, int]):
    x, y, width, height = crop
    if min(x, y) < 0 or min(width, height) <= 0 or width % 2 or height % 2:
        raise ValueError("裁切位置不可為負，寬高必須是正偶數")
    capture = cv2.VideoCapture(str(source))
    if not capture.isOpened():
        raise RuntimeError(f"無法開啟影片：{source}")
    source_width = int(capture.get(cv2.CAP_PROP_FRAME_WIDTH))
    source_height = int(capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
    if x + width > source_width or y + height > source_height:
        raise ValueError(f"裁切範圍超過來源尺寸 {source_width}x{source_height}")
    fps = capture.get(cv2.CAP_PROP_FPS) or 30.0
    output.parent.mkdir(parents=True, exist_ok=True)
    writer = cv2.VideoWriter(
        str(output), cv2.VideoWriter_fourcc(*"mp4v"), fps, (width, height)
    )
    frames = 0
    while True:
        ok, frame = capture.read()
        if not ok:
            break
        writer.write(frame[y:y + height, x:x + width])
        frames += 1
    capture.release()
    writer.release()
    if not frames:
        raise RuntimeError("來源影片沒有可裁切影格")
    return {"source_size": [source_width, source_height], "crop": list(crop), "frames": frames}


def main():
    parser = argparse.ArgumentParser(description="將前端錄影裁成純攝影機區域")
    parser.add_argument("source", type=Path)
    parser.add_argument("output", type=Path)
    parser.add_argument("--crop", required=True, help="x,y,width,height；寬高必須為偶數")
    args = parser.parse_args()
    try:
        crop = tuple(int(value.strip()) for value in args.crop.split(","))
    except ValueError:
        parser.error("--crop 格式必須為 x,y,width,height")
    if len(crop) != 4:
        parser.error("--crop 格式必須為 x,y,width,height")
    result = crop_video(args.source, args.output, crop)
    print(result)


if __name__ == "__main__":
    main()
