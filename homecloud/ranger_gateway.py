"""Pi serial owner. Manual control plus read-only telemetry; autonomous output disabled."""
import argparse
import threading
import time
from flask import Flask, jsonify, request
from navigation.telemetry import parse_line

ALLOWED = {'forward','backward','left','right','stop'}


def create_app(serial_port, done_timeout=2.7):
    app = Flask(__name__)
    lock = threading.Lock()
    changed = threading.Condition(lock)
    command_lock = threading.Lock()
    state = {
        'board': '', 'latest': None, 'last_line': '', 'line_updated': 0.0,
        'telemetry_updated': 0.0, 'done_seq': 0, 'done_events': [],
    }

    def reader():
        while True:
            try:
                line = serial_port.readline().decode('ascii', 'strict').strip()
            except Exception:
                time.sleep(.05)
                continue
            if not line:
                continue
            try:
                kind, data = parse_line(line)
            except ValueError:
                continue
            now = time.monotonic()
            with changed:
                state['last_line'] = line
                state['line_updated'] = now
                if kind == 'telemetry':
                    state['latest'] = data
                    state['telemetry_updated'] = now
                elif kind == 'board':
                    state['board'] = data
                elif kind == 'done':
                    state['done_seq'] += 1
                    state['done_events'].append((state['done_seq'], data))
                    del state['done_events'][:-16]
                    changed.notify_all()

    threading.Thread(target=reader, daemon=True, name='RangerSerialReader').start()

    @app.get('/health')
    def health():
        with lock:
            telemetry_age = time.monotonic() - state['telemetry_updated']
            return jsonify(
                ok=True,
                serial_open=serial_port.is_open,
                board=state['board'],
                telemetry_fresh=state['latest'] is not None and telemetry_age < .8,
            )

    @app.get('/telemetry')
    def telemetry():
        with lock:
            if state['latest'] is None:
                return jsonify(ok=False, error='no telemetry'), 503
            return jsonify(
                ok=True,
                age_sec=round(time.monotonic() - state['telemetry_updated'], 3),
                **state['latest'],
            )

    @app.post('/control')
    def control():
        data = request.get_json(silent=True) or {}
        command = str(data.get('command', '')).lower().strip()
        if command not in ALLOWED:
            return jsonify(ok=False, error='invalid command'), 400

        # Only one movement transaction at a time. Stop always bypasses this gate.
        owns_command_lock = command != 'stop' and command_lock.acquire(blocking=False)
        if command != 'stop' and not owns_command_lock:
            return jsonify(ok=False, error='robot busy'), 409
        try:
            with changed:
                before = state['done_seq']
            try:
                serial_port.write((command + '\n').encode('ascii'))
                serial_port.flush()
            except Exception as exc:
                return jsonify(ok=False, error='serial write failed', detail=str(exc)), 503

            deadline = time.monotonic() + done_timeout
            with changed:
                done = ''
                while not done:
                    for sequence, candidate in state['done_events']:
                        if sequence > before and candidate.split(',', 1)[0] == command:
                            done = candidate
                            break
                    if done:
                        break
                    remaining = deadline - time.monotonic()
                    if remaining <= 0:
                        break
                    changed.wait(remaining)

            if done:
                return jsonify(ok=True, accepted=command, done=done)

            # A lost completion means motion state is uncertain. Best effort stop.
            try:
                serial_port.write(b'stop\n')
                serial_port.flush()
            except Exception:
                pass
            return jsonify(ok=False, error='DONE timeout', expected=command), 504
        finally:
            if owns_command_lock:
                command_lock.release()
    return app


def main():
    ap=argparse.ArgumentParser();ap.add_argument('--port',default='/dev/ttyUSB0');ap.add_argument('--baud',type=int,default=9600);args=ap.parse_args()
    import serial
    ser=serial.Serial(args.port,args.baud,timeout=.15,write_timeout=.5)
    create_app(ser).run(host='0.0.0.0',port=5000,debug=False,use_reloader=False,threaded=True)


if __name__=='__main__':main()
