#include <MeAuriga.h>

// Candidate firmware for bench testing. Upload only after checking wheel direction.
// Text commands: forward, backward, left, right, stop
// Output: BOARD, RECEIVE, DONE, and TEL lines. Serial baud matches the current Pi service.

MeEncoderOnBoard motor1(SLOT1);
MeEncoderOnBoard motor2(SLOT2);

const long SERIAL_BAUD = 9600;
const int DRIVE_PWM = 70;
// Current physical tuning reported after ground testing.
const int LEFT_TURN_PWM = 130;
const int RIGHT_TURN_PWM = 100;
// Follow steps are intentionally longer so consecutive HTTP commands feel
// continuous. Forward/back finish after both tracks move about 12 cm.
// Turns finish at about 20 degrees
// using direction-specific pulse targets because left/right belt slip differs.
const long DRIVE_LEFT_TARGET_PULSES = 260;
const long DRIVE_RIGHT_TARGET_PULSES = 260;
const long LEFT_TURN_LEFT_TARGET_PULSES = 151;
const long LEFT_TURN_RIGHT_TARGET_PULSES = 88;
const long RIGHT_TURN_LEFT_TARGET_PULSES = 422;
const long RIGHT_TURN_RIGHT_TARGET_PULSES = 321;
// Keep this below ranger_gateway.py's 2.7 second DONE timeout.
const unsigned long MAX_COMMAND_MS = 2500;
const int STRAIGHT_MIN_PWM = 52;
const int STRAIGHT_MAX_PWM = 88;
const int STRAIGHT_CORRECTION_DIVISOR = 4;
const unsigned long WATCHDOG_MS = 2500;
// At 9600 baud, 4 packets/second leaves enough serial capacity for commands and DONE.
const unsigned long TELEMETRY_MS = 250;

// Confirmed by the lifted-wheel test: the previous polarity was reversed on both sides.
const int MOTOR1_FORWARD_SIGN = -1;
const int MOTOR2_FORWARD_SIGN = 1;

// Auriga's official Ranger obstacle-avoidance firmware uses PORT_10.  Keep the
// navigation firmware on the same port as the user's known-good sensor test.
// The telemetry below derives centimeters from the raw echo duration so that
// -1.0 means "no echo" rather than the library's ambiguous 400 cm cap.
const bool ULTRASONIC_ENABLED = true;
const uint8_t ULTRASONIC_PORT = PORT_10;
MeUltrasonicSensor ultrasonic(ULTRASONIC_PORT);

// The Makeblock library version bundled with this project accepts a timeout
// argument in measure(), but its implementation calls pulseIn() without that
// timeout. A missing echo can therefore block the control loop for far too
// long. Drive the same single-wire signal pin directly with a bounded 30 ms
// wait. The official example requires at least 100 ms between measurements.
const unsigned long ULTRASONIC_SAMPLE_MS = 100;
const unsigned long ULTRASONIC_VALID_HOLD_MS = 700;
const uint8_t ULTRASONIC_INVALID_LIMIT_MOVING = 2;
const double FORWARD_OBSTACLE_STOP_CM = 25.0;
double latestFrontCm = -1.0;
unsigned long lastFrontValidMs = 0;
unsigned long lastUltrasonicSampleMs = 0;
uint8_t consecutiveInvalidEchoes = 0;
bool latestEchoValid = false;

double readUltrasonicOnceCm() {
  if (!ULTRASONIC_ENABLED) return -1.0;
  const uint8_t signalPin = ultrasonic.pin2();
  pinMode(signalPin, OUTPUT);
  digitalWrite(signalPin, LOW);
  delayMicroseconds(2);
  digitalWrite(signalPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(signalPin, LOW);
  pinMode(signalPin, INPUT);
  const unsigned long echoUs = pulseIn(signalPin, HIGH, 30000UL);
  if (echoUs == 0) return -1.0;
  const double cm = (double)echoUs / 58.0;
  return (cm >= 2.0 && cm < 400.0) ? cm : -1.0;
}

void updateUltrasonic() {
  const unsigned long now = millis();
  if (now - lastUltrasonicSampleMs < ULTRASONIC_SAMPLE_MS) return;
  lastUltrasonicSampleMs = now;
  const double cm = readUltrasonicOnceCm();
  latestEchoValid = cm > 0;
  if (latestEchoValid) {
    latestFrontCm = cm;
    lastFrontValidMs = millis();
    consecutiveInvalidEchoes = 0;
  } else if (consecutiveInvalidEchoes < 255) {
    ++consecutiveInvalidEchoes;
  }
}

double frontCmForTelemetry() {
  if (lastFrontValidMs > 0 && millis() - lastFrontValidMs <= ULTRASONIC_VALID_HOLD_MS) {
    return latestFrontCm;
  }
  return -1.0;
}

String inputLine;
String activeCommand = "stop";
unsigned long commandStartedMs = 0;
unsigned long lastValidCommandMs = 0;
unsigned long lastTelemetryMs = 0;
bool commandActive = false;
long commandStartPulse1 = 0;
long commandStartPulse2 = 0;
long commandTargetPulse1 = 0;
long commandTargetPulse2 = 0;

void encoder1Isr() {
  if (digitalRead(motor1.getPortB()) == 0) motor1.pulsePosMinus();
  else motor1.pulsePosPlus();
}

void encoder2Isr() {
  if (digitalRead(motor2.getPortB()) == 0) motor2.pulsePosMinus();
  else motor2.pulsePosPlus();
}

void stopMotors() {
  motor1.setMotorPwm(0);
  motor2.setMotorPwm(0);
}

void balanceStraightMotion(long moved1, long moved2) {
  if (activeCommand != "forward" && activeCommand != "backward") return;
  const int direction = activeCommand == "forward" ? 1 : -1;
  long rawCorrection = (moved1 - moved2) / STRAIGHT_CORRECTION_DIVISOR;
  int correction = constrain((int)rawCorrection,
                             -(STRAIGHT_MAX_PWM - DRIVE_PWM),
                             STRAIGHT_MAX_PWM - DRIVE_PWM);
  int leftPwm = constrain(DRIVE_PWM - correction, STRAIGHT_MIN_PWM, STRAIGHT_MAX_PWM);
  int rightPwm = constrain(DRIVE_PWM + correction, STRAIGHT_MIN_PWM, STRAIGHT_MAX_PWM);
  // Once one track reaches its target, hold it while the other finishes. This
  // prevents the faster track from pulling the Ranger sideways at step end.
  if (moved1 >= commandTargetPulse1) leftPwm = 0;
  if (moved2 >= commandTargetPulse2) rightPwm = 0;
  motor1.setMotorPwm(MOTOR1_FORWARD_SIGN * direction * leftPwm);
  motor2.setMotorPwm(MOTOR2_FORWARD_SIGN * direction * rightPwm);
}

bool applyCommand(const String &command) {
  int left = 0;
  int right = 0;
  long target1 = 0;
  long target2 = 0;
  if (command == "forward") {
    left = DRIVE_PWM; right = DRIVE_PWM;
    target1 = DRIVE_LEFT_TARGET_PULSES; target2 = DRIVE_RIGHT_TARGET_PULSES;
  }
  else if (command == "backward") {
    left = -DRIVE_PWM; right = -DRIVE_PWM;
    target1 = DRIVE_LEFT_TARGET_PULSES; target2 = DRIVE_RIGHT_TARGET_PULSES;
  }
  else if (command == "left") {
    left = LEFT_TURN_PWM; right = -LEFT_TURN_PWM;
    target1 = LEFT_TURN_LEFT_TARGET_PULSES; target2 = LEFT_TURN_RIGHT_TARGET_PULSES;
  }
  else if (command == "right") {
    left = -RIGHT_TURN_PWM; right = RIGHT_TURN_PWM;
    target1 = RIGHT_TURN_LEFT_TARGET_PULSES; target2 = RIGHT_TURN_RIGHT_TARGET_PULSES;
  }
  else if (command != "stop") { return false; }

  motor1.setMotorPwm(MOTOR1_FORWARD_SIGN * left);
  motor2.setMotorPwm(MOTOR2_FORWARD_SIGN * right);
  activeCommand = command;
  commandStartedMs = millis();
  lastValidCommandMs = commandStartedMs;
  commandActive = command != "stop";
  commandStartPulse1 = motor1.getPulsePos();
  commandStartPulse2 = motor2.getPulsePos();
  commandTargetPulse1 = target1;
  commandTargetPulse2 = target2;
  if (!commandActive) stopMotors();
  return true;
}

void finishCommand(const char *reason) {
  if (!commandActive) return;
  stopMotors();
  commandActive = false;
  Serial.print("DONE:"); Serial.print(activeCommand);
  Serial.print(",reason="); Serial.println(reason);
  activeCommand = "stop";
}

void processLine(String line) {
  line.trim();
  line.toLowerCase();
  if (line.length() == 0) return;
  Serial.print("RECEIVE:"); Serial.println(line);
  if (!applyCommand(line)) {
    stopMotors(); commandActive = false;
    Serial.print("ERROR:unknown_command,"); Serial.println(line);
    return;
  }
  if (line == "stop") Serial.println("DONE:stop,reason=command");
}

void emitTelemetry() {
  double cm = frontCmForTelemetry();
  Serial.print("TEL:t_ms="); Serial.print(millis());
  Serial.print(",left_pulse="); Serial.print(motor1.getPulsePos());
  Serial.print(",right_pulse="); Serial.print(motor2.getPulsePos());
  Serial.print(",left_deg="); Serial.print(motor1.getCurPos());
  Serial.print(",right_deg="); Serial.print(motor2.getCurPos());
  Serial.print(",left_rpm="); Serial.print(motor1.getCurrentSpeed(), 2);
  Serial.print(",right_rpm="); Serial.print(motor2.getCurrentSpeed(), 2);
  Serial.print(",front_cm="); Serial.print(cm, 1);
  Serial.print(",moving="); Serial.println(commandActive ? 1 : 0);
}

void setup() {
  // Same PWM timer setup used by Makeblock's Auriga encoder example.
  TCCR1A = _BV(WGM10);
  TCCR1B = _BV(CS11) | _BV(WGM12);
  TCCR2A = _BV(WGM21) | _BV(WGM20);
  TCCR2B = _BV(CS21);
  // Official Makeblock Auriga encoder example uses these motor constants.
  motor1.setPulse(9);
  motor2.setPulse(9);
  motor1.setRatio(39.267);
  motor2.setRatio(39.267);
  attachInterrupt(motor1.getIntNum(), encoder1Isr, RISING);
  attachInterrupt(motor2.getIntNum(), encoder2Isr, RISING);
  Serial.begin(SERIAL_BAUD);
  stopMotors();
  Serial.println("BOARD:MBOT_RANGER_AURIGA_TELEMETRY_V2_ENCODER_STEPS_US10_GUARDED");
}

void loop() {
  motor1.loop(); motor2.loop();
  updateUltrasonic();
  while (Serial.available()) {
    char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (inputLine.length()) { processLine(inputLine); inputLine = ""; }
    } else if (inputLine.length() < 48) inputLine += c;
    else { inputLine = ""; stopMotors(); commandActive = false; Serial.println("ERROR:line_too_long"); }
  }
  unsigned long now = millis();
  if (commandActive) {
    if (activeCommand == "forward") {
      if (latestEchoValid && latestFrontCm <= FORWARD_OBSTACLE_STOP_CM) {
        finishCommand("obstacle");
      } else if (consecutiveInvalidEchoes >= ULTRASONIC_INVALID_LIMIT_MOVING) {
        finishCommand("sensor_invalid");
      }
    }
  }
  if (commandActive) {
    long moved1 = labs(motor1.getPulsePos() - commandStartPulse1);
    long moved2 = labs(motor2.getPulsePos() - commandStartPulse2);
    balanceStraightMotion(moved1, moved2);
    if (moved1 >= commandTargetPulse1 && moved2 >= commandTargetPulse2) {
      finishCommand("encoder_target");
    } else if (now - commandStartedMs >= MAX_COMMAND_MS) {
      finishCommand("motion_timeout");
    }
  }
  if (commandActive && now - lastValidCommandMs >= WATCHDOG_MS) finishCommand("watchdog");
  if (now - lastTelemetryMs >= TELEMETRY_MS) { lastTelemetryMs = now; emitTelemetry(); }
}
