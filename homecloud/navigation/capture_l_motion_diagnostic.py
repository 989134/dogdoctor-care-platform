"""Capture a bounded physical L-motion sequence without planner corrections."""

from __future__ import annotations

import json
import time
from datetime import datetime
from pathlib import Path

from navigation.ranger_patrol_adapter import RangerPatrolAdapter


def main():
    adapter = RangerPatrolAdapter.from_calibration_file(
        Path("navigation/calibration.json"),
        base_url="http://10.129.211.235:5000",
        motion_enabled=True,
    )
    sequence = ["forward", "forward", "right", "right", "right", "forward", "forward"]
    report = {
        "schema": "homecloud.l_motion_diagnostic.v1",
        "started_at": datetime.now().isoformat(),
        "sequence": sequence,
        "steps": [],
    }
    try:
        check = adapter.preflight()
        if not check["ok"]:
            raise RuntimeError("；".join(check["errors"]))
        for index, command in enumerate(sequence, 1):
            check = adapter.preflight()
            front_cm = float(check["telemetry"].get("front_cm", -1))
            if command == "forward" and front_cm < 50:
                raise RuntimeError(f"第 {index} 步前方僅 {front_cm:.1f} cm，停止測試")
            response = adapter.apply(command)
            row = {
                "index": index,
                "command": command,
                "front_cm_before": front_cm,
                "done": response.get("done"),
                "pulse_delta": response.get("pulse_delta"),
                "telemetry_before": response.get("telemetry_before"),
                "telemetry_after": response.get("telemetry_after"),
            }
            report["steps"].append(row)
            print(json.dumps(row, ensure_ascii=False), flush=True)
            time.sleep(.8)
    finally:
        try:
            stop_response = adapter.apply("stop")
            report["stop"] = stop_response
        except Exception as exc:
            report["stop_error"] = str(exc)
        report["finished_at"] = datetime.now().isoformat()
        output = Path("navigation/output") / (
            "l_motion_diagnostic_" + datetime.now().strftime("%Y%m%d_%H%M%S") + ".json"
        )
        output.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        print(output.resolve(), flush=True)


if __name__ == "__main__":
    main()
