#include <MeAuriga.h>

// Standalone mBot Ranger / Me Auriga ultrasonic diagnostic.
// No motors, encoder interrupts, Wi-Fi, or navigation logic are started.
// Keep the Makeblock ultrasonic module on PORT_10 and open Serial Monitor at
// 9600 baud. A stable target should produce mostly OK lines with similar cm.

MeUltrasonicSensor ultrasonic(PORT_10);
const unsigned long SAMPLE_INTERVAL_MS = 100;
const unsigned long ECHO_TIMEOUT_US = 30000UL;
unsigned long lastSampleMs = 0;
unsigned long sequence = 0;

void setup() {
  Serial.begin(9600);
  Serial.println("BOARD:MBOT_RANGER_AURIGA_ULTRASONIC_PORT10_DIAGNOSTIC_NETWORK_V2");
}

void loop() {
  const unsigned long now = millis();
  if (now - lastSampleMs < SAMPLE_INTERVAL_MS) return;
  lastSampleMs = now;

  const uint8_t signalPin = ultrasonic.pin2();
  pinMode(signalPin, OUTPUT);
  digitalWrite(signalPin, LOW);
  delayMicroseconds(2);
  digitalWrite(signalPin, HIGH);
  delayMicroseconds(10);
  digitalWrite(signalPin, LOW);
  pinMode(signalPin, INPUT);

  const unsigned long echoUs = pulseIn(signalPin, HIGH, ECHO_TIMEOUT_US);
  ++sequence;
  const double cm = echoUs == 0 ? -1.0 : (double)echoUs / 58.0;
  Serial.print("US:seq="); Serial.print(sequence);
  Serial.print(",echo_us="); Serial.print(echoUs);
  if (echoUs == 0) {
    Serial.println(",cm=-1.0,status=NO_ECHO");
  } else {
    Serial.print(",cm="); Serial.print(cm, 1);
    Serial.println(",status=OK");
  }

  // Compatible with ranger_gateway.py so the exact same minimal firmware can
  // be measured through the Raspberry Pi without adding any navigation logic.
  Serial.print("TEL:t_ms="); Serial.print(millis());
  Serial.print(",left_pulse=0,right_pulse=0,left_deg=0,right_deg=0");
  Serial.print(",left_rpm=0,right_rpm=0,front_cm="); Serial.print(cm, 1);
  Serial.println(",moving=0");
}
