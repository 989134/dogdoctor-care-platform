"""Read Ranger telemetry and optionally run a lifted-wheel direction test."""
import argparse
import json
import time
import urllib.error
import urllib.request
from pathlib import Path


def request_json(url, method='GET', payload=None, timeout=3):
    body = None if payload is None else json.dumps(payload).encode('utf-8')
    request = urllib.request.Request(
        url, data=body, method=method,
        headers={'Content-Type': 'application/json'} if body else {},
    )
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return response.status, json.loads(response.read())
    except urllib.error.HTTPError as exc:
        try:
            detail = json.loads(exc.read())
        except Exception:
            detail = {'error': str(exc)}
        return exc.code, detail


def telemetry(base_url):
    status, data = request_json(base_url + '/telemetry')
    if status != 200 or not data.get('ok'):
        raise RuntimeError(f'telemetry unavailable: HTTP {status} {data}')
    return data


def movement_test(base_url, minimum_pulses=2):
    results = []
    for command in ('forward', 'backward', 'left', 'right'):
        before = telemetry(base_url)
        status, reply = request_json(
            base_url + '/control', 'POST', {'command': command}, timeout=3
        )
        time.sleep(.35)
        after = telemetry(base_url)
        dl = after['left_pulse'] - before['left_pulse']
        dr = after['right_pulse'] - before['right_pulse']
        results.append({
            'command': command,
            'http_status': status,
            'done': reply.get('done', ''),
            'left_delta_pulse': dl,
            'right_delta_pulse': dr,
            'left_moved': abs(dl) >= minimum_pulses,
            'right_moved': abs(dr) >= minimum_pulses,
            'pass': status == 200 and abs(dl) >= minimum_pulses and abs(dr) >= minimum_pulses,
        })
    request_json(base_url + '/control', 'POST', {'command': 'stop'}, timeout=3)
    return results


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--url', default='http://172.20.10.2:5000')
    parser.add_argument(
        '--confirm-wheels-lifted', action='store_true',
        help='run four short motor commands; omit for a read-only check',
    )
    parser.add_argument('--output', type=Path, default=Path('navigation/output/ranger_bench.json'))
    args = parser.parse_args()
    base = args.url.rstrip('/')

    health_status, health = request_json(base + '/health')
    result = {'url': base, 'health_http_status': health_status, 'health': health}
    if health_status != 200 or not health.get('telemetry_fresh'):
        raise SystemExit('FAIL: gateway or telemetry is not ready: ' + json.dumps(result, ensure_ascii=False))
    result['telemetry'] = telemetry(base)

    if args.confirm_wheels_lifted:
        result['motion_tests'] = movement_test(base)
        result['all_motion_tests_passed'] = all(item['pass'] for item in result['motion_tests'])
    else:
        result['motion_tests'] = []
        result['note'] = 'read-only check; add --confirm-wheels-lifted only after lifting all wheels'

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    print(args.output.resolve())


if __name__ == '__main__':
    main()
