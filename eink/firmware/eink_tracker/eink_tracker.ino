/*
  Wall tracker e-ink display
  ESP32 DevKit + WeAct 4.2" e-paper (400x300, B/W, SSD1683)

  Every hour: wake up -> Wi-Fi -> download screen.bin from GitHub ->
  redraw only if the image changed -> deep sleep.

  Libraries (Arduino Library Manager): GxEPD2 (by Jean-Marc Zingg) + Adafruit GFX.
  Board: "ESP32 Dev Module".

  Wiring (WeAct cable -> ESP32 DevKit):
    VCC  -> 3V3
    GND  -> GND
    SDA  -> GPIO 23  (SPI MOSI)
    SCL  -> GPIO 18  (SPI SCK)
    CS   -> GPIO 5
    D/C  -> GPIO 17
    RES  -> GPIO 16
    BUSY -> GPIO 4
*/

#include <WiFi.h>
#include <WiFiClientSecure.h>
#include <HTTPClient.h>
#include <GxEPD2_BW.h>
#include "secrets.h"  // defines WIFI_SSID and WIFI_PASS

// Pins
#define PIN_CS   5
#define PIN_DC   17
#define PIN_RST  16
#define PIN_BUSY 4

// Image source (raw file in the public repo)
const char* IMAGE_URL =
  "https://raw.githubusercontent.com/vldmrvm/wall-tracker/main/eink/out/screen.bin";

const uint64_t SLEEP_MINUTES = 60;
const int W = 400, H = 300;
const size_t IMG_BYTES = W * H / 8;  // 15000

GxEPD2_BW<GxEPD2_420_GDEY042T81, GxEPD2_420_GDEY042T81::HEIGHT>
  display(GxEPD2_420_GDEY042T81(PIN_CS, PIN_DC, PIN_RST, PIN_BUSY));

static uint8_t img[IMG_BYTES];
RTC_DATA_ATTR uint32_t lastHash = 0;  // survives deep sleep

// FNV-1a hash to detect whether the image changed
uint32_t fnv1a(const uint8_t* data, size_t len) {
  uint32_t h = 2166136261u;
  for (size_t i = 0; i < len; i++) { h ^= data[i]; h *= 16777619u; }
  return h;
}

bool connectWiFi() {
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASS);
  for (int i = 0; i < 40 && WiFi.status() != WL_CONNECTED; i++) delay(250);
  return WiFi.status() == WL_CONNECTED;
}

bool downloadImage() {
  WiFiClientSecure client;
  client.setInsecure();  // skip cert check: public, read-only data
  HTTPClient http;
  if (!http.begin(client, IMAGE_URL)) return false;
  int code = http.GET();
  if (code != HTTP_CODE_OK) {
    Serial.printf("HTTP error: %d\n", code);
    http.end();
    return false;
  }
  Serial.printf("Content-Length: %d\n", http.getSize());

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
      break;  // server closed the connection
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

void goToSleep() {
  WiFi.disconnect(true);
  WiFi.mode(WIFI_OFF);
  esp_sleep_enable_timer_wakeup(SLEEP_MINUTES * 60ULL * 1000000ULL);
  esp_deep_sleep_start();
}

void setup() {
  Serial.begin(115200);
  delay(200);

  if (connectWiFi() && downloadImage()) {
    uint32_t h = fnv1a(img, IMG_BYTES);
    if (h != lastHash) {
      Serial.println("New image, redrawing");
      drawImage();
      lastHash = h;
    } else {
      Serial.println("No change, skipping redraw");
    }
  } else {
    Serial.println("Update failed, keeping old image");
  }
  goToSleep();
}

void loop() {}
