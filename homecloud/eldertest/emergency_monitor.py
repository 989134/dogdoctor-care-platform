import logging
import threading
import time
from typing import Callable, Optional

import config

logger = logging.getLogger(__name__)

try:
    from gpiozero import Button
    GPIOZERO_AVAILABLE = True
except Exception:
    Button = None
    GPIOZERO_AVAILABLE = False


class EmergencyMonitor:
    def __init__(self, stop_event: threading.Event, status: dict, alert_callback: Callable[[str], None]):
        self.stop_event = stop_event
        self.status = status
        self.alert_callback = alert_callback
        self.button_pin = int(config.EMERGENCY_BUTTON_PIN)
        self.hold_time = float(config.EMERGENCY_BUTTON_HOLD_SEC)
        self.cooldown_sec = float(config.EMERGENCY_BUTTON_COOLDOWN_SEC)
        self.bounce_time = float(getattr(config, "EMERGENCY_BUTTON_BOUNCE_SEC", 0.05))
        self.active_high = bool(getattr(config, "EMERGENCY_BUTTON_ACTIVE_HIGH", True))
        self.pull_up = bool(getattr(config, "EMERGENCY_BUTTON_USE_PULL_UP", False))
        self.sensor_mode = getattr(config, "EMERGENCY_BUTTON_MODE", "touch")
        self.last_trigger_time = 0.0
        self.button: Optional[Button] = None
        self.thread = threading.Thread(target=self._run, name="EmergencyMonitor", daemon=True)
        self._init_status()

    def _init_status(self):
        self.status.setdefault("emergency", {})
        self.status["emergency"].update(
            {
                "gpio_available": GPIOZERO_AVAILABLE,
                "button_enabled": bool(config.EMERGENCY_BUTTON_ENABLED),
                "button_pin": self.button_pin,
                "button_mode": self.sensor_mode,
                "active_high": self.active_high,
                "pull_up": self.pull_up,
                "last_event": "",
                "last_update": "",
                "active": False,
            }
        )

    def start(self):
        if not config.EMERGENCY_BUTTON_ENABLED:
            logger.info("緊急按鈕/觸控感測器已停用")
            return
        self.thread.start()

    def _trigger_alert(self, reason: str):
        now = time.time()
        if now - self.last_trigger_time < self.cooldown_sec:
            return
        self.last_trigger_time = now
        self.status["emergency"]["last_event"] = reason
        self.status["emergency"]["last_update"] = time.strftime("%H:%M:%S")
        logger.warning(reason)
        self.alert_callback(f"{reason}，請立即與長者聯繫或到場查看。")

    def _run(self):
        self.status["emergency"]["active"] = True
        if not GPIOZERO_AVAILABLE:
            logger.warning("gpiozero 未安裝，EmergencyMonitor 停用")
            self.status["emergency"]["active"] = False
            return

        try:
            self.button = Button(
                pin=self.button_pin,
                pull_up=self.pull_up,
                active_state=self.active_high,
                hold_time=self.hold_time,
                bounce_time=self.bounce_time,
            )
            self.button.when_pressed = lambda: self._trigger_alert("SOS 緊急感測器已觸發")
            logger.info(
                "EmergencyMonitor 啟動 pin=%s mode=%s active_high=%s pull_up=%s",
                self.button_pin,
                self.sensor_mode,
                self.active_high,
                self.pull_up,
            )
            while not self.stop_event.is_set():
                time.sleep(0.2)
        except Exception as exc:
            logger.error("EmergencyMonitor 啟動失敗：%s", exc)
        finally:
            try:
                if self.button is not None:
                    self.button.close()
            except Exception:
                pass
            self.status["emergency"]["active"] = False
            logger.info("EmergencyMonitor 已停止")