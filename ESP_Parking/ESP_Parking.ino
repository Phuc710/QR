#include <SPI.h>
#include <MFRC522.h>
#include <Servo.h>
#include <Wire.h>
#include <Adafruit_GFX.h>
#include <Adafruit_SSD1306.h>
#include <WiFi.h>
#include <PubSubClient.h>
#include <ArduinoJson.h>

#define SS_PIN 5
#define RST_PIN 27
#define SERVO_IN_PIN 13
#define SERVO_OUT_PIN 12
#define IR_IN_PIN 34
#define IR_OUT_PIN 35
#define SMOKE_PIN 32
#define BUZZER_PIN 15
#define BTN_IN_PIN 4
#define BTN_OUT_PIN 0
#define BTN_EMER_PIN 2
#define OLED_ADDR 0x3C

const int slotPins[3] = {25, 26, 27};

const char* ssid = "your_wifi_ssid";
const char* password = "your_wifi_password";
const char* mqtt_server = "192.168.1.4";
const int mqtt_port = 1883;

WiFiClient espClient;
PubSubClient client(espClient);

MFRC522 rfid(SS_PIN, RST_PIN);
Servo servoIn;
Servo servoOut;
Adafruit_SSD1306 display(128, 64, &Wire, -1);

bool slotsOccupied[3] = {false, false, false};
int freeSlot = -1;
String rfidTag = "";
unsigned long lastTime = 0;
bool smokeDetected = false;
bool emergency = false;

enum State {
  IDLE,
  DETECT_IN,
  SCAN_RFID_IN,
  WAIT_PLATE_IN,
  ASSIGN_SLOT,
  BARRIER_OPEN_IN,
  DETECT_OUT,
  SCAN_RFID_OUT,
  WAIT_PLATE_OUT,
  WAIT_PAYMENT,
  BARRIER_OPEN_OUT,
  ERROR_STATE,
  FULL,
  ALERT
};

State currentState = IDLE;

void setup() {
  Serial.begin(115200);
  WiFi.begin(ssid, password);
  while (WiFi.status() != WL_CONNECTED) delay(500);
  client.setServer(mqtt_server, mqtt_port);
  client.setCallback(callback);
  reconnect();

  pinMode(IR_IN_PIN, INPUT);
  pinMode(IR_OUT_PIN, INPUT);
  pinMode(SMOKE_PIN, INPUT);
  for (int i = 0; i < 3; i++) {
    pinMode(slotPins[i], INPUT);
  }
  pinMode(BUZZER_PIN, OUTPUT);
  pinMode(BTN_IN_PIN, INPUT_PULLUP);
  pinMode(BTN_OUT_PIN, INPUT_PULLUP);
  pinMode(BTN_EMER_PIN, INPUT_PULLUP);

  SPI.begin();
  rfid.PCD_Init();

  servoIn.attach(SERVO_IN_PIN);
  servoOut.attach(SERVO_OUT_PIN);
  closeBarrierIn();
  closeBarrierOut();

  display.begin(SSD1306_SWITCHCAPVCC, OLED_ADDR);
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  display.setCursor(30, 20);
  display.println(F("LOADING..."));
  display.display();
  delay(2000);
  updateDisplay("X PARKING", true);
  currentState = IDLE;
}

void loop() {
  if (!client.connected()) reconnect();
  client.loop();

  int smokeVal = analogRead(SMOKE_PIN);
  if (smokeVal > 2000) {
    if (!smokeDetected) {
      smokeDetected = true;
      currentState = ALERT;
      publishJson("event", "ALERT", "type", "SMOKE");
      buzz(3);
      openBarrierIn();
      openBarrierOut();
      updateDisplay("ALERT: FIRE", false);
    }
  } else {
    smokeDetected = false;
  }

  if (digitalRead(BTN_EMER_PIN) == LOW) {
    delay(50);
    if (digitalRead(BTN_EMER_PIN) == LOW && !emergency) {
      emergency = true;
      currentState = ALERT;
      publishJson("event", "ALERT", "type", "EMERGENCY");
      buzz(5);
      openBarrierIn();
      openBarrierOut();
      updateDisplay("EMERGENCY", false);
    }
  } else {
    emergency = false;
  }

  if (digitalRead(BTN_IN_PIN) == LOW) {
    delay(50);
    if (digitalRead(BTN_IN_PIN) == LOW) {
      publishJson("event", "BUTTON", "type", "IN");
    }
  }
  if (digitalRead(BTN_OUT_PIN) == LOW) {
    delay(50);
    if (digitalRead(BTN_OUT_PIN) == LOW) {
      publishJson("event", "BUTTON", "type", "OUT");
    }
  }

  updateSlots();

  int occupiedCount = 0;
  for (bool occ : slotsOccupied) if (occ) occupiedCount++;
  if (occupiedCount == 3 && currentState != ALERT && currentState != FULL) {
    currentState = FULL;
    updateDisplay("X PARKING\nFULL", true);
    publishJson("event", "PARKING", "status", "FULL");
  } else if (occupiedCount < 3 && currentState == FULL && currentState != ALERT) {
    currentState = IDLE;
    updateDisplay("X PARKING", true);
    publishJson("event", "PARKING", "status", "AVAILABLE");
  }

  switch (currentState) {
    case IDLE:
      if (digitalRead(IR_IN_PIN) == HIGH) {
        if (occupiedCount < 3) {
          currentState = DETECT_IN;
          updateDisplay("XE IN", false);
          publishJson("event", "CAR", "type", "DETECT_IN");
        } else {
          buzz(2);
          updateDisplay("FULL", false);
        }
      } else if (digitalRead(IR_OUT_PIN) == HIGH) {
        currentState = DETECT_OUT;
        updateDisplay("XE OUT", false);
        publishJson("event", "CAR", "type", "DETECT_OUT");
      }
      break;

    case DETECT_IN:
      currentState = SCAN_RFID_IN;
      updateDisplay("QUET RFID", false);
      lastTime = millis();
      break;

    case SCAN_RFID_IN:
      if (scanRFID()) {
        updateDisplay("RFID: " + rfidTag + "\nQUET BSX", false);
        publishJson("event", "RFID_IN", "value", rfidTag.c_str());
        currentState = WAIT_PLATE_IN;
        lastTime = millis();
      } else if (millis() - lastTime > 10000) {
        currentState = ERROR_STATE;
        updateDisplay("QUET RFID AGAIN", false);
        buzz(1);
        lastTime = millis();
      }
      break;

    case WAIT_PLATE_IN:
      if (millis() - lastTime > 15000) {
        currentState = ERROR_STATE;
        updateDisplay("BSX TIMEOUT", false);
        buzz(2);
        lastTime = millis();
      }
      break;

    case ASSIGN_SLOT:
      findFreeSlot();
      if (freeSlot != -1) {
        updateDisplay(rfidTag + " IN\nSLOT " + String(freeSlot + 1), false);
        publishJson("event", "SLOT_ASSIGN", "value", String(freeSlot).c_str());
        openBarrierIn();
        currentState = BARRIER_OPEN_IN;
        lastTime = millis();
      } else {
        currentState = FULL;
        updateDisplay("FULL", false);
        buzz(2);
      }
      break;

    case BARRIER_OPEN_IN:
      if (millis() - lastTime > 2000 && digitalRead(IR_IN_PIN) == LOW) {
        closeBarrierIn();
        currentState = IDLE;
        updateDisplay("X PARKING", true);
        publishJson("event", "CAR", "type", "IN_COMPLETE");
        rfidTag = "";
      }
      break;

    case DETECT_OUT:
      currentState = SCAN_RFID_OUT;
      updateDisplay("QUET RFID", false);
      lastTime = millis();
      break;

    case SCAN_RFID_OUT:
      if (scanRFID()) {
        updateDisplay("RFID: " + rfidTag + "\nQUET BSX", false);
        publishJson("event", "RFID_OUT", "value", rfidTag.c_str());
        currentState = WAIT_PLATE_OUT;
        lastTime = millis();
      } else if (millis() - lastTime > 10000) {
        currentState = ERROR_STATE;
        updateDisplay("QUET RFID AGAIN", false);
        buzz(1);
        lastTime = millis();
      }
      break;

    case WAIT_PLATE_OUT:
      if (millis() - lastTime > 15000) {
        currentState = ERROR_STATE;
        updateDisplay("BSX TIMEOUT", false);
        buzz(2);
        lastTime = millis();
      }
      break;

    case WAIT_PAYMENT:
      updateDisplay("DANG THANH TOAN", false);
      break;

    case BARRIER_OPEN_OUT:
      if (millis() - lastTime > 2000 && digitalRead(IR_OUT_PIN) == LOW) {
        closeBarrierOut();
        currentState = IDLE;
        updateDisplay("X PARKING", true);
        publishJson("event", "CAR", "type", "OUT_COMPLETE");
        rfidTag = "";
      }
      break;

    case ERROR_STATE:
      if (millis() - lastTime > 5000) {
        currentState = IDLE;
        updateDisplay("X PARKING", true);
        rfidTag = "";
      }
      break;

    case ALERT:
      if (!smokeDetected && !emergency) {
        currentState = IDLE;
        updateDisplay("X PARKING", true);
        closeBarrierIn();
        closeBarrierOut();
      }
      break;
  }
}

void reconnect() {
  while (!client.connected()) {
    if (client.connect("ESP32Client")) {
      client.subscribe("parking/command");
    } else {
      delay(1000);  // Giảm delay reconnect xuống 1s để nhanh hơn
    }
  }
}

void callback(char* topic, byte* payload, unsigned int length) {
  String message = "";
  for (int i = 0; i < length; i++) {
    message += (char)payload[i];
  }
  Serial.println("Received command: " + message);

  if (message == "PLATE_OK_IN") {
    if (currentState == WAIT_PLATE_IN) {
      currentState = ASSIGN_SLOT;
    }
  } else if (message == "PLATE_ERROR_IN") {
    if (currentState == WAIT_PLATE_IN) {
      currentState = ERROR_STATE;
      updateDisplay("BSX ERROR", false);
      buzz(2);
    }
  } else if (message == "PLATE_OK_OUT") {
    if (currentState == WAIT_PLATE_OUT) {
      currentState = WAIT_PAYMENT;
    }
  } else if (message == "PLATE_ERROR_OUT") {
    if (currentState == WAIT_PLATE_OUT) {
      currentState = ERROR_STATE;
      updateDisplay("BSX ERROR", false);
      buzz(2);
    }
  } else if (message.startsWith("PAYMENT_SUCCESS:")) {
    if (currentState == WAIT_PAYMENT) {
      // Có thể extract fee từ message nếu cần, ví dụ: int fee = message.substring(16).toInt();
      currentState = BARRIER_OPEN_OUT;
      lastTime = millis();
      updateDisplay("THANH TOAN OK\nRA KHOI BAI", false);
      openBarrierOut();
    }
  } else if (message == "PAYMENT_FAIL") {
    if (currentState == WAIT_PAYMENT) {
      currentState = ERROR_STATE;
      updateDisplay("THANH TOAN LOI", false);
      buzz(3);
    }
  }
}

void publishJson(const char* key1, const char* val1, const char* key2, const char* val2) {
  StaticJsonDocument<200> doc;
  doc[key1] = val1;
  doc[key2] = val2;
  char buffer[256];
  serializeJson(doc, buffer);
  client.publish("parking/data", buffer);
}

bool scanRFID() {
  if (!rfid.PICC_IsNewCardPresent()) return false;
  if (!rfid.PICC_ReadCardSerial()) return false;
  rfidTag = "";
  for (byte i = 0; i < rfid.uid.size; i++) {
    rfidTag += (rfid.uid.uidByte[i] < 0x10 ? "0" : "");
    rfidTag += String(rfid.uid.uidByte[i], HEX);
  }
  rfidTag.toUpperCase();
  rfid.PICC_HaltA();
  return true;
}

void openBarrierIn() {
  servoIn.write(90);
}

void closeBarrierIn() {
  servoIn.write(0);
}

void openBarrierOut() {
  servoOut.write(90);
}

void closeBarrierOut() {
  servoOut.write(0);
}

void buzz(int times) {
  for (int i = 0; i < times; i++) {
    digitalWrite(BUZZER_PIN, HIGH);
    delay(200);
    digitalWrite(BUZZER_PIN, LOW);
    delay(200);
  }
}

void updateDisplay(String text, bool isDefault = false) {
  display.clearDisplay();
  display.setTextSize(1);
  display.setTextColor(SSD1306_WHITE);
  if (isDefault) {
    display.setCursor(30, 20);
  } else {
    display.setCursor(0, 0);
  }
  display.println(text);
  display.display();
}

void updateSlots() {
  for (int i = 0; i < 3; i++) {
    slotsOccupied[i] = (digitalRead(slotPins[i]) == LOW);
  }
}

void findFreeSlot() {
  freeSlot = -1;
  for (int i = 0; i < 3; i++) {
    if (!slotsOccupied[i]) {
      freeSlot = i;
      break;
    }
  }
}