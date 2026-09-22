import logging
import os
import threading
import time

try:
    import serial
except ImportError:  # pragma: no cover - lets the app run without hardware deps.
    serial = None


logger = logging.getLogger(__name__)


DEFAULT_COMMANDS = {
    "forward": "M:FORWARD:{speed}",
    "backward": "M:BACKWARD:{speed}",
    "left": "M:LEFT:{turn_speed}",
    "right": "M:RIGHT:{turn_speed}",
    "stop": "M:STOP",
    "pre": "A:PREPICK",
    "re": "A:RETURN",
    "pick": "A:PICK",
    "arrive": "A:ARRIVE",
}


class UartRobotController:
    """Small UART adapter between Python AI decisions and vehicle firmware."""

    def __init__(
        self,
        port=None,
        baud_rate=115200,
        timeout=0.1,
        enabled=True,
        dry_run=False,
        default_speed=80,
        turn_speed=65,
        startup_delay=2.0,
    ):
        self.port = port
        self.baud_rate = int(baud_rate)
        self.timeout = float(timeout)
        self.enabled = bool(enabled)
        self.dry_run = bool(dry_run or not port)
        self.default_speed = int(default_speed)
        self.turn_speed = int(turn_speed)
        self.startup_delay = float(startup_delay)
        self._serial = None
        self._lock = threading.Lock()

    @property
    def is_open(self):
        return self._serial is not None and self._serial.is_open

    def connect(self):
        if not self.enabled:
            logger.info("UART disabled; robot commands will be logged only.")
            self.dry_run = True
            return False

        if self.dry_run:
            logger.info("UART dry-run enabled; robot commands will be logged only.")
            return False

        if serial is None:
            logger.warning("pyserial is not installed; falling back to UART dry-run.")
            self.dry_run = True
            return False

        with self._lock:
            if self.is_open:
                return True

            try:
                self._serial = serial.Serial(
                    self.port,
                    self.baud_rate,
                    timeout=self.timeout,
                    write_timeout=self.timeout,
                )
                time.sleep(self.startup_delay)
                logger.info("UART connected on %s at %s baud.", self.port, self.baud_rate)
                return True
            except Exception as exc:
                logger.error("Could not open UART port %s: %s", self.port, exc)
                logger.warning("Continuing in UART dry-run mode.")
                self.dry_run = True
                self._serial = None
                return False

    def close(self):
        with self._lock:
            if self._serial is not None:
                try:
                    if self._serial.is_open:
                        self.send("stop")
                        self._serial.close()
                finally:
                    self._serial = None

    def encode_command(self, command, speed=None):
        normalized = (command or "").strip().lower()
        template = DEFAULT_COMMANDS.get(normalized)
        if template is None:
            raise ValueError(f"Unknown robot command: {command!r}")

        speed_value = self.default_speed if speed is None else int(speed)
        return template.format(speed=speed_value, turn_speed=self.turn_speed)

    def send(self, command, speed=None):
        packet = self.encode_command(command, speed=speed) + "\n"

        if self.dry_run or not self.enabled:
            logger.info("[UART dry-run] %s", packet.strip())
            return {"ok": True, "packet": packet.strip(), "dry_run": True}

        if not self.is_open and not self.connect():
            logger.info("[UART dry-run] %s", packet.strip())
            return {"ok": True, "packet": packet.strip(), "dry_run": True}

        payload = packet.encode("ascii")
        with self._lock:
            self._serial.write(payload)
            self._serial.flush()

        logger.info("[UART] %s", packet.strip())
        return {"ok": True, "packet": packet.strip(), "dry_run": False}


def create_uart_controller():
    enabled = os.getenv("UART_ENABLED", "1").strip() != "0"
    dry_run = os.getenv("UART_DRY_RUN", "").strip() == "1"
    port = os.getenv("UART_PORT", "").strip() or None
    baud_rate = int(os.getenv("UART_BAUD", "115200"))
    default_speed = int(os.getenv("UART_DEFAULT_SPEED", "80"))
    turn_speed = int(os.getenv("UART_TURN_SPEED", "65"))

    controller = UartRobotController(
        port=port,
        baud_rate=baud_rate,
        enabled=enabled,
        dry_run=dry_run,
        default_speed=default_speed,
        turn_speed=turn_speed,
    )
    controller.connect()
    return controller
