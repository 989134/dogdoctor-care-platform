"""Calculate odometry calibration from measured lifted/ground tests."""
import argparse
import json
import math
from pathlib import Path


def straight_calibration(distance_m, left_delta, right_delta):
    if not 0.1 <= distance_m <= 20:
        raise ValueError('distance_m must be between 0.1 and 20')
    if left_delta == 0 or right_delta == 0:
        raise ValueError('both encoder deltas must be nonzero')
    return {
        'left_m_per_pulse': distance_m / abs(left_delta),
        'right_m_per_pulse': distance_m / abs(right_delta),
        'left_sign': 1 if left_delta > 0 else -1,
        'right_sign': 1 if right_delta > 0 else -1,
    }


def track_width_calibration(angle_deg, left_delta, right_delta, calibration):
    angle = math.radians(abs(angle_deg))
    if not math.radians(45) <= angle <= math.radians(1080):
        raise ValueError('turn angle must be between 45 and 1080 degrees')
    dl = left_delta * calibration['left_m_per_pulse'] * calibration['left_sign']
    dr = right_delta * calibration['right_m_per_pulse'] * calibration['right_sign']
    width = abs(dr - dl) / angle
    if not 0.08 <= width <= 0.6:
        raise ValueError('calculated track width is implausible; check signs and measurements')
    return width


def main():
    parser = argparse.ArgumentParser(description='Ranger encoder/track-width calibration')
    parser.add_argument('--distance-m', type=float, required=True)
    parser.add_argument('--straight-left-pulses', type=int, required=True)
    parser.add_argument('--straight-right-pulses', type=int, required=True)
    parser.add_argument('--turn-deg', type=float)
    parser.add_argument('--turn-left-pulses', type=int)
    parser.add_argument('--turn-right-pulses', type=int)
    parser.add_argument('--output', type=Path, default=Path(__file__).with_name('calibration.json'))
    args = parser.parse_args()
    result = straight_calibration(
        args.distance_m, args.straight_left_pulses, args.straight_right_pulses
    )
    turn_values = (args.turn_deg, args.turn_left_pulses, args.turn_right_pulses)
    if any(value is not None for value in turn_values):
        if any(value is None for value in turn_values):
            parser.error('all three turn arguments are required together')
        result['track_width_m'] = track_width_calibration(
            args.turn_deg, args.turn_left_pulses, args.turn_right_pulses, result
        )
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False))
    print(args.output.resolve())


if __name__ == '__main__':
    main()
