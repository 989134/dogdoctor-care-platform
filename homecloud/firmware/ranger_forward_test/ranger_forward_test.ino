#include <MeAuriga.h>

MeEncoderOnBoard motor1(SLOT1);
MeEncoderOnBoard motor2(SLOT2);

const long SERIAL_BAUD = 9600;
const int FORWARD_PWM = 150;
const unsigned long FORWARD_TIME_MS = 1500;

String inputLine;

void stopMotors() {
  motor1.setMotorPwm(0);
  motor2.setMotorPwm(0);
}

void moveForward() {
  // The signs match the direction previously confirmed on this Ranger.
  motor1.setMotorPwm(-FORWARD_PWM);
  motor2.setMotorPwm(FORWARD_PWM);
  delay(FORWARD_TIME_MS);
  stopMotors();
}

void processCommand(String command) {
  command.trim();
  command.toLowerCase();

  if (command == "forward") {
    Serial.println("RECEIVE:forward");
    moveForward();
    Serial.println("DONE:forward,reason=timed_test");
  } else if (command == "stop") {
    stopMotors();
    Serial.println("RECEIVE:stop");
    Serial.println("DONE:stop,reason=command");
  } else if (command.length() > 0) {
    stopMotors();
    Serial.print("ERROR:unknown_command,");
    Serial.println(command);
  }
}

void setup() {
  // PWM timer settings used by Makeblock's Auriga encoder-motor example.
  TCCR1A = _BV(WGM10);
  TCCR1B = _BV(CS11) | _BV(WGM12);
  TCCR2A = _BV(WGM21) | _BV(WGM20);
  TCCR2B = _BV(CS21);

  Serial.begin(SERIAL_BAUD);
  stopMotors();
  Serial.println("BOARD:MBOT_RANGER_AURIGA_FORWARD_TEST_V1");
}

void loop() {
  while (Serial.available()) {
    const char c = Serial.read();
    if (c == '\n' || c == '\r') {
      if (inputLine.length() > 0) {
        processCommand(inputLine);
        inputLine = "";
      }
    } else if (inputLine.length() < 48) {
      inputLine += c;
    } else {
      inputLine = "";
      stopMotors();
      Serial.println("ERROR:line_too_long");
    }
  }
}
