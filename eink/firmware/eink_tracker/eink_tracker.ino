/*
  Wall tracker e-ink display + NFC screen switch
  ESP32 DevKit + WeAct 4.2" e-paper (400x300, B/W, SSD1683) + PN532 NFC module (I2C)
  Powered from USB, so no deep sleep: always listening for a tag.

  Tap a tag -> show its screen. Screens are rendered by eink/render.py:
    0 = main (screen.bin), 1 = holdings (screen1.bin), 2 = vs plan / VUAA (screen2.bin)
  Unknown tag -> next screen; its UID is printed to Serial so you can add it to TAGS.
  The current screen is re-downloaded every hour (redrawn only if it changed).
  After RETURN_MIN minutes without a tap it goes back to the main screen.

  Libraries (Arduino Library Manager): GxEPD2, Adafruit GFX, Adafruit PN532.
  Board: "ESP32 Dev Module".

  Wiring, display (WeAct cable -> ESP32):
    VCC -> 3V3, GND -> GND
    SDA -> GPIO 23 (SPI MOSI), SCL -> GPIO 18 (SPI SCK)
    CS -> GPIO 5, D/C -> GPIO 17, RES -> GPIO 16, BUSY -> GPIO 4

  Wiring, PN532 (switches on the board set to I2C: SW1 = ON, SW2 = OFF):
    VCC -> 3V3, GND -> GND
    SDA -> GPIO 21, SCL -> GPIO 22
    IRQ -> GPIO 19
*/

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <Wire.h>
#include <Adafruit_PN532.h>
#include <GxEPD2_BW.h>
#include "secrets.h"  // defines WIFI_SSID and WIFI_PASS

// Display pins
#define PIN_CS   5
#define PIN_DC   17
#define PIN_RST  16
#define PIN_BUSY 4

// PN532 pins
#define NFC_SDA  21
#define NFC_SCL  22
#define NFC_IRQ  19
#define NFC_RST  25  // not connected, the library just needs a pin number

const char* SCREEN_URLS[] = {
  "https://raw.githubusercontent.com/vldmrvm/wall-tracker/main/eink/out/screen.bin",
  "https://raw.githubusercontent.com/vldmrvm/wall-tracker/main/eink/out/screen1.bin",
  "https://raw.githubusercontent.com/vldmrvm/wall-tracker/main/eink/out/screen2.bin",
};
const int NUM_SCREENS = sizeof(SCREEN_URLS) / sizeof(SCREEN_URLS[0]);

// Tag UID (hex, as printed to Serial) -> screen number
struct TagScreen { const char* uid; int screen; };
const TagScreen TAGS[] = {
  // { "04A1B2C3D4E580", 0 },
  // { "04F1E2D3C4B580", 1 },
  // { "04112233445580", 2 },
};
const int NUM_TAGS = sizeof(TAGS) / sizeof(TAGS[0]);

const uint32_t REFRESH_MIN = 60;  // re-download current screen
const uint32_t RETURN_MIN  = 10;  // back to main screen after no taps (0 = never)

const int W = 400, H = 300;
const size_t IMG_BYTES = W * H / 8;  // 15000

GxEPD2_BW<GxEPD2_420_GDEY042T81, GxEPD2_420_GDEY042T81::HEIGHT>
  display(GxEPD2_420_GDEY042T81(PIN_CS, PIN_DC, PIN_RST, PIN_BUSY));
Adafruit_PN532 nfc(NFC_IRQ, NFC_RST);

static uint8_t img[IMG_BYTES];
int current = -1;          // screen on display now
uint32_t shownHash = 0;    // hash of the image on display
uint32_t lastFetch = 0;    // millis of last download
uint32_t lastTap = 0;      // millis of last tag tap
bool nfcOk = false;

// FNV-1a hash to detect whether the image changed
uint32_t fnv1a(const uint8_t* data, size_t len) {
  uint32_t h = 2166136261u;
  for (size_t i = 0; i < len; i++) { h ^= data[i]; h *= 16777619u; }
  return h;
}

bool ensureWiFi() {
  if (WiFi.status() == WL_CONNECTED) return true;
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);
  return WiFi.status() == WL_CONNECTED;
}

bool downloadImage(const char* url) {
  WiFiClientSecure client;
  client.setInsecure();  // skip cert check: public, read-only data
  HTTPClient http;
  if (!http.begin(client, url)) return false;
  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    Serial.printf("HTTP error: %d\n", code);
    http.end();
    return false;
  }

  // TLS data arrives in chunks: keep reading until full image or 20 s timeout
  WiFiClient* stream = http.getStreamPtr();
  size_t got = 0;
  uint32_t start = millis();
  while (got < IMG_BYTES && millis() - start < 20000) {
    size_t avail = stream->available();
    if (avail) {
      size_t want = min(avail, IMG_BYTES - got);
      got += stream->readBytes(img + got, want);
    } else if (!stream->connected()) {
      break;
    } else {
      delay(10);
    }
  }
  http.end();
  Serial.printf("Downloaded %u of %u bytes\n", (unsigned)got, (unsigned)IMG_BYTES);
  return got == IMG_BYTES;
}

void drawImage() {
  display.init(115200, true, 2, false);
  display.setRotation(0);
  display.setFullWindow();
  display.fillScreen(GxEPD_WHITE);
  display.drawBitmap(0, 0, img, W, H, GxEPD_BLACK);
  display.display(false);  // full refresh, no ghosting
  display.hibernate();
}

// Download a screen and draw it if it differs from what is shown
void showScreen(int idx) {
  lastFetch = millis();
  if (!ensureWiFi() || !downloadImage(SCREEN_URLS[idx])) {
    Serial.println("Update failed, keeping old image");
    return;
  }
  uint32_t h = fnv1a(img, IMG_BYTES);
  if (idx != current || h != shownHash) {
    Serial.printf("Drawing screen %d\n", idx);
    drawImage();
    current = idx;
    shownHash = h;
  } else {
    Serial.println("No change, skipping redraw");
  }
}

String uidToHex(const uint8_t* uid, uint8_t len) {
  String s;
  char buf[3];
  for (uint8_t i = 0; i < len; i++) { sprintf(buf, "%02X", uid[i]); s += buf; }
  return s;
}

int screenForTag(const String& uid) {
  for (int i = 0; i < NUM_TAGS; i++)
    if (uid.equalsIgnoreCase(TAGS[i].uid)) return TAGS[i].screen % NUM_SCREENS;
  return (current + 1) % NUM_SCREENS;  // unknown tag: next screen
}

void setup() {
  Serial.begin(115200);
  delay(200);

  Wire.begin(NFC_SDA, NFC_SCL);
  nfc.begin();
  uint32_t ver = nfc.getFirmwareVersion();
  if (ver) {
    nfc.SAMConfig();
    nfcOk = true;
    Serial.printf("PN532 found, firmware %d.%d\n", (ver >> 16) & 0xFF, (ver >> 8) & 0xFF);
  } else {
    Serial.println("PN532 not found, check wiring and I2C switches");
  }

  showScreen(0);
}

void loop() {
  if (nfcOk) {
    uint8_t uid[7];
    uint8_t len = 0;
    if (nfc.readPassiveTargetID(PN532_MIFARE_ISO14443A, uid, &len, 100)) {
      String hex = uidToHex(uid, len);
      int idx = screenForTag(hex);
      Serial.printf("Tag %s -> screen %d\n", hex.c_str(), idx);
      lastTap = millis();
      if (idx != current) showScreen(idx);
      delay(1500);  // ignore the same tag while it is still on the reader
    }
  }

  if (millis() - lastFetch > REFRESH_MIN * 60000UL) {
    showScreen(current < 0 ? 0 : current);
  }

  if (RETURN_MIN && current > 0 && millis() - lastTap > RETURN_MIN * 60000UL) {
    showScreen(0);
  }

  delay(50);
}
