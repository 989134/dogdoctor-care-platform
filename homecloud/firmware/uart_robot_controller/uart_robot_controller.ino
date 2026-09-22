// UART robot firmware for Arduino/ESP32 style boards.
// Protocol from Python:
//   M:FORWARD:80
//   M:BACKWARD:80
//   M:LEFT:65
//   M:RIGHT:65
//   M:STOP
//   A:PICK
//   A:ARRIVE
//
// Adjust the motor pins below to match your driver wiring.

#if defined(ESP32)
const int LEFT_PWM_PIN = 25;
const int LEFT_IN1_PIN = 26;
const int LEFT_IN2_PIN = 27;
const int RIGHT_PWM_PIN = 14;
const int RIGHT_IN1_PIN = 12;
const int RIGHT_IN2_PIN = 13;

const int LEFT_PWM_CHANNEL = 0;
const int RIGHT_PWM_CHANNEL = 1;
const int PWM_FREQ = 20000;
const int PWM_RESOLUTION = 8;
#else
const int LEFT_PWM_PIN = 5;
const int LEFT_IN1_PIN = 7;
const int LEFT_IN2_PIN = 8;
const int RIGHT_PWM_PIN = 6;
const int RIGHT_IN1_PIN = 9;
const int RIGHT_IN2_PIN = 10;
#endif

const unsigned long COMMAND_TIMEOUT_MS = 500;

String inputLine;
unsigned long lastCommandMs = 0;

int clampSpeed(int speedValue) {
  if (speedValue < 0) {
    return 0;
  }
  if (speedValue > 255) {
    return 255;
  }
  return speedValue;
}

void writePwm(int leftSpeed, int rightSpeed) {
  leftSpeed = clampSpeed(leftSpeed);
  rightSpeed = clampSpeed(rightSpeed);

#if defined(ESP32)
  ledcWrite(LEFT_PWM_CHANNEL, leftSpeed);
  ledcWrite(RIGHT_PWM_CHANNEL, rightSpeed);
#else
  analogWrite(LEFT_PWM_PIN, leftSpeed);
  analogWrite(RIGHT_PWM_PIN, rightSpeed);
#endif
}

void setLeftMotor(int direction, int speedValue) {
  if (direction > 0) {
    digitalWrite(LEFT_IN1_PIN, HIGH);
    digitalWrite(LEFT_IN2_PIN, LOW);
  } else if (direction < 0) {
    digitalWrite(LEFT_IN1_PIN, LOW);
    digitalWrite(LEFT_IN2_PIN, HIGH);
  } else {
    digitalWrite(LEFT_IN1_PIN, LOW);
    digitalWrite(LEFT_IN2_PIN, LOW);
  }

#if defined(ESP32)
  ledcWrite(LEFT_PWM_CHANNEL, clampSpeed(speedValue));
#else
  analogWrite(LEFT_PWM_PIN, clampSpeed(speedValue));
#endif
}

void setRightMotor(int direction, int speedValue) {
  if (direction > 0) {
    digitalWrite(RIGHT_IN1_PIN, HIGH);
    digitalWrite(RIGHT_IN2_PIN, LOW);
  } else if (direction < 0) {
    digitalWrite(RIGHT_IN1_PIN, LOW);
    digitalWrite(RIGHT_IN2_PIN, HIGH);
  } else {
    digitalWrite(RIGHT_IN1_PIN, LOW);
    digitalWrite(RIGHT_IN2_PIN, LOW);
  }

#if defined(ESP32)
  ledcWrite(RIGHT_PWM_CHANNEL, clampSpeed(speedValue));
#else
  analogWrite(RIGHT_PWM_PIN, clampSpeed(speedValue));
#endif
}

void stopMotors() {
  setLeftMotor(0, 0);
  setRightMotor(0, 0);
}

void moveForward(int speedValue) {
  setLeftMotor(1, speedValue);
  setRightMotor(1, speedValue);
}

void moveBackward(int speedValue) {
  setLeftMotor(-1, speedValue);
  setRightMotor(-1, speedValue);
}

void turnLeft(int speedValue) {
  setLeftMotor(-1, speedValue);
  setRightMotor(1, speedValue);
}

void turnRight(int speedValue) {
  setLeftMotor(1, speedValue);
  setRightMotor(-1, speedValue);
}

int parseSpeed(const String &line, int defaultSpeed) {
  int secondColon = line.indexOf(':', 2);
  if (secondColon < 0) {
    return defaultSpeed;
  }
  return clampSpeed(line.substring(secondColon + 1).toInt());
}

void handleActionCommand(const String &action) {
  if (action == "PICK") {
    stopMotors();
    Serial.println("OK:A:PICK");
  } else if (action == "ARRIVE") {
    stopMotors();
    Serial.println("OK:A:ARRIVE");
  } else if (action == "PREPICK") {
    Serial.println("OK:A:PREPICK");
  } else if (action == "RETURN") {
    Serial.println("OK:A:RETURN");
  } else {
    Serial.print("ERR:UNKNOWN_ACTION:");
    Serial.println(action);
  }
}

void handleLine(String line) {
  line.trim();
  line.toUpperCase();
  if (line.length() == 0) {
    return;
  }

  lastCommandMs = millis();

  if (line.startsWith("M:FORWARD")) {
    moveForward(parseSpeed(line, 80));
    Serial.println("OK:M:FORWARD");
  } else if (line.startsWith("M:BACKWARD")) {
    moveBackward(parseSpeed(line, 80));
    Serial.println("OK:M:BACKWARD");
  } else if (line.startsWith("M:LEFT")) {
    turnLeft(parseSpeed(line, 65));
    Serial.println("OK:M:LEFT");
  } else if (line.startsWith("M:RIGHT")) {
    turnRight(parseSpeed(line, 65));
    Serial.println("OK:M:RIGHT");
  } else if (line == "M:STOP") {
    stopMotors();
    Serial.println("OK:M:STOP");
  } else if (line.startsWith("A:")) {
    handleActionCommand(line.substring(2));
  } else {
    Serial.print("ERR:UNKNOWN:");
    Serial.println(line);
  }
}

void setup() {
  pinMode(LEFT_IN1_PIN, OUTPUT);
  pinMode(LEFT_IN2_PIN, OUTPUT);
  pinMode(RIGHT_IN1_PIN, OUTPUT);
  pinMode(RIGHT_IN2_PIN, OUTPUT);

#if defined(ESP32)
  ledcSetup(LEFT_PWM_CHANNEL, PWM_FREQ, PWM_RESOLUTION);
  ledcSetup(RIGHT_PWM_CHANNEL, PWM_FREQ, PWM_RESOLUTION);
  ledcAttachPin(LEFT_PWM_PIN, LEFT_PWM_CHANNEL);
  ledcAttachPin(RIGHT_PWM_PIN, RIGHT_PWM_CHANNEL);
#else
  pinMode(LEFT_PWM_PIN, OUTPUT);
  pinMode(RIGHT_PWM_PIN, OUTPUT);
#endif

  stopMotors();
  Serial.begin(115200);
  lastCommandMs = millis();
  Serial.println("READY:UART_ROBOT");
}

void loop() {
  while (Serial.available() > 0) {
    char ch = static_cast<char>(Serial.read());
    if (ch == '\n') {
      handleLine(inputLine);
      inputLine = "";
    } else if (ch != '\r') {
      inputLine += ch;
    }
  }

  if (millis() - lastCommandMs > COMMAND_TIMEOUT_MS) {
    stopMotors();
  }
}
