import math
from dataclasses import dataclass


FIELDS = {
    't_ms': int, 'left_pulse': int, 'right_pulse': int,
    'left_deg': float, 'right_deg': float, 'left_rpm': float,
    'right_rpm': float, 'front_cm': float, 'moving': int,
}


def parse_line(line):
    line = line.strip()
    if line.startswith('TEL:'):
        values = {}
        for part in line[4:].split(','):
            if '=' not in part: raise ValueError('bad telemetry field')
            key, raw = part.split('=', 1)
            if key not in FIELDS or key in values: raise ValueError('unknown or duplicate field')
            values[key] = FIELDS[key](raw)
        if set(values) != set(FIELDS): raise ValueError('incomplete telemetry')
        if not all(math.isfinite(v) for v in values.values()): raise ValueError('nonfinite telemetry')
        # distanceCm(400) returns the 400 cm cap when no echo is received.
        # Treat that cap as unavailable so an unplugged sensor cannot be
        # mistaken for a clear route.
        values['ultrasonic_valid'] = 1 <= values['front_cm'] < 399.5
        return 'telemetry', values
    for prefix, kind in [('BOARD:', 'board'), ('RECEIVE:', 'receive'),
                         ('DONE:', 'done'), ('ERROR:', 'error')]:
        if line.startswith(prefix): return kind, line[len(prefix):]
    return 'unknown', line


@dataclass
class PulseCalibration:
    left_m_per_pulse: float
    right_m_per_pulse: float
    left_sign: int = 1
    right_sign: int = 1

    def __post_init__(self):
        if not 0 < self.left_m_per_pulse < .1 or not 0 < self.right_m_per_pulse < .1:
            raise ValueError('invalid meters per pulse')
        if self.left_sign not in (-1, 1) or self.right_sign not in (-1, 1):
            raise ValueError('invalid encoder sign')

    def meters(self, packet):
        return (packet['left_pulse'] * self.left_m_per_pulse * self.left_sign,
                packet['right_pulse'] * self.right_m_per_pulse * self.right_sign)
