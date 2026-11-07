#include "link.h"

#include <ArduinoJson.h>
#include <WiFi.h>

#include "config.h"   // FW_VERSION

const char* resetReason();   // main.cpp

// Written by the WiFi event task, read in update(): the reason the router (or the chip) gave for the last drop.
static volatile uint8_t lastDropReason = 0;

void Link::begin(const char* ssid, const char* pass, const char* host,
                 uint16_t port, const char* token, CommandHandler onCommand,
                 StateHandler onState) {
  ssid_ = ssid;
  pass_ = pass;
  token_ = token;
  onCommand_ = std::move(onCommand);
  onState_ = std::move(onState);

  WiFi.mode(WIFI_STA);
  WiFi.setSleep(false);  // lower latency; we're on wall power anyway
  // Hear every channel before joining, then pick the strongest access point with this name. The default stops at
  // the first one it hears, which with a mesh or a second router can be a far one: a slow, gappy link until it drops.
  WiFi.setScanMethod(WIFI_ALL_CHANNEL_SCAN);
  WiFi.setSortMethod(WIFI_CONNECT_AP_BY_SIGNAL);
  WiFi.onEvent([](WiFiEvent_t, WiFiEventInfo_t info) { lastDropReason = info.wifi_sta_disconnected.reason; },
               ARDUINO_EVENT_WIFI_STA_DISCONNECTED);
  WiFi.begin(ssid_, pass_);
  Serial.printf("wifi: joining \"%s\"...\n", ssid_);

  ws_.begin(host, port, "/");
  ws_.onEvent([this](WStype_t type, uint8_t* payload, size_t length) {
    onEvent(type, payload, length);
  });
  ws_.setReconnectInterval(3000);
  ws_.enableHeartbeat(15000, 3000, 2);  // ping every 15 s, drop after 2 misses
}

void Link::update(uint32_t nowMs) {
  bool wifiUp = WiFi.status() == WL_CONNECTED;
  if (wifiUp && !wifiReported_) {
    Serial.printf("wifi: connected, ip %s\n", WiFi.localIP().toString().c_str());
    if (lastDropAtMs_) lastDownMs_ = nowMs - lastDropAtMs_;
    wifiReported_ = true;
    lowTxPower_ = false;           // a fresh join starts at full power; the check below turns it down again
    lastTxCheckMs_ = nowMs - 5000;
  } else if (!wifiUp && wifiReported_) {
    Serial.printf("wifi: lost (reason %u), retrying\n", static_cast<unsigned>(lastDropReason));
    wifiReported_ = false;
    ++wifiDrops_;
    lastDropAtMs_ = nowMs ? nowMs : 1;
  }
  if (!wifiUp) {
    // The WiFi stack rejoins by itself after most drops, but not after every failed join: after one reboot it sat
    // on "joining" for minutes. So if it has been down for a while, start the join over.
    if (wifiDownSinceMs_ == 0) {
      wifiDownSinceMs_ = nowMs ? nowMs : 1;
    } else if (nowMs - wifiDownSinceMs_ > 20000) {
      Serial.printf("wifi: still not connected (status %d), joining \"%s\" again\n", static_cast<int>(WiFi.status()), ssid_);
      WiFi.disconnect();
      WiFi.begin(ssid_, pass_);
      wifiDownSinceMs_ = nowMs ? nowMs : 1;
    }
    return;
  }
  wifiDownSinceMs_ = 0;

  // Transmit power to suit the signal. Sending (camera pictures, all day) is the chip's biggest power draw and so its
  // biggest heat source, and full power is only needed far from the router: 11 dBm while the router comes in loud,
  // full power again once it fades. RSSI is how loud the router is to us, which our own power doesn't change, so
  // this can't feed back on itself; the gap between the two thresholds stops it flapping.
  if (WIFI_SAVE_TX_POWER && nowMs - lastTxCheckMs_ >= 5000) {
    lastTxCheckMs_ = nowMs;
    int rssi = WiFi.RSSI();
    bool low = lowTxPower_ ? rssi > -62 : rssi >= -50;
    if (rssi != 0 && low != lowTxPower_) {
      lowTxPower_ = low;
      WiFi.setTxPower(low ? WIFI_POWER_11dBm : WIFI_POWER_19_5dBm);
      Serial.printf("wifi: signal %d dBm, transmit power %s\n", rssi, low ? "11 dBm (router close)" : "full");
    }
  }

  // Each ws_.loop() reads at most ONE message. While a reply streams in (ten audio messages a second, plus the
  // brain's heartbeat replies) a single read per pass of loop() falls behind as soon as a pass also sends a camera
  // frame: the audio arrives late (gaps), and the heartbeat reply waits behind seconds of audio until the link
  // gives up on it and drops the connection. So read everything that's waiting, for a few ms at most.
  ws_.loop();
  uint32_t t0 = millis();
  for (int i = 0; connected_ && i < 32 && millis() - t0 < 12; ++i) ws_.loop();
}

void Link::sendJson(const String& json) {
  if (!connected_) return;
  ws_.sendTXT(json.c_str(), json.length());
}

void Link::sendBinary(uint8_t type, const uint8_t* data, size_t len) {
  if (!connected_) return;
  static uint8_t* buf = nullptr;
  static const size_t CAP = 512 * 1024;  // mic frames are ~1 KB, stream JPEGs ~10-20 KB, snapshots up to ~300 KB
  if (buf == nullptr) buf = static_cast<uint8_t*>(ps_malloc(CAP));
  if (buf == nullptr || len > CAP - 1) return;
  buf[0] = type;
  memcpy(buf + 1, data, len);
  ws_.sendBIN(buf, len + 1);
}

void Link::sendState(float panDeg, const char* emotion) {
  if (!connected_) return;
  JsonDocument doc;
  doc["type"] = "state";
  doc["pan"] = panDeg;
  doc["emotion"] = emotion;
  String out;
  serializeJson(doc, out);
  sendJson(out);
}

void Link::onEvent(WStype_t type, uint8_t* payload, size_t length) {
  switch (type) {
    case WStype_CONNECTED: {
      connected_ = true;
      Serial.println(F("brain: connected"));
      JsonDocument doc;
      doc["type"] = "hello";
      doc["who"] = "desk-robot";
      doc["fw"] = FW_VERSION;
      doc["token"] = token_;  // the brain drops connections without it
      doc["boot"] = resetReason();
      doc["up_s"] = millis() / 1000;
      doc["rssi"] = WiFi.RSSI();
      doc["tx_low"] = lowTxPower_;
      doc["wifi_drops"] = wifiDrops_;
      doc["link_drops"] = linkDrops_;
      if (wifiDrops_) {
        doc["drop_reason"] = lastDropReason;
        doc["drop_ago_s"] = (millis() - lastDropAtMs_) / 1000;
        doc["down_s"] = lastDownMs_ / 1000.0f;
      }
      if (onHello_) onHello_(doc);
      String out;
      serializeJson(doc, out);
      sendJson(out);
      if (onState_) onState_(true);
      break;
    }
    case WStype_DISCONNECTED:
      if (connected_) {
        Serial.println(F("brain: disconnected, will retry"));
        ++linkDrops_;
        if (onState_) onState_(false);
      }
      connected_ = false;
      break;
    case WStype_TEXT: {
      // Copy so we can guarantee a terminator.
      String msg(reinterpret_cast<const char*>(payload), length);
      handleMessage(msg.c_str());
      break;
    }
    case WStype_BIN:
      if (length > 1 && payload[0] == 0x01 && onAudio_) {
        onAudio_(payload + 1, length - 1);
      }
      break;
    default:
      break;
  }
}

void Link::handleMessage(const char* json) {
  JsonDocument doc;
  if (deserializeJson(doc, json)) {
    Serial.printf("brain: bad json: %s\n", json);
    return;
  }
  const char* type = doc["type"] | "";
  String cmd;
  if (!strcmp(type, "emotion")) {
    cmd = String("emo ") + (doc["name"] | "neutral");
  } else if (!strcmp(type, "pan") || !strcmp(type, "tilt")) {
    cmd = String(type) + " " + String(doc["deg"] | 0.0f, 1);
  } else if (!strcmp(type, "speak_begin")) {
    cmd = String("speak_begin ") + String(doc["bytes"] | 0);
  } else if (!strcmp(type, "speak_end")) {
    cmd = type;
  } else if (!strcmp(type, "mouth")) {
    cmd = String("mouth ") + String(doc["level"] | -1.0f, 3);
  } else if (!strcmp(type, "volume")) {
    cmd = String("volume ") + String(doc["level"] | 0.5f, 2);
  } else if (!strcmp(type, "mic")) {
    cmd = String("mic ") + ((doc["on"] | false) ? "on" : "off");
  } else if (!strcmp(type, "glance")) {
    cmd = String("glance ") + ((doc["on"] | true) ? "on" : "off");
  } else if (!strcmp(type, "asleep")) {
    cmd = String("sleep ") + ((doc["on"] | false) ? "on" : "off");
  } else if (!strcmp(type, "settings")) {
    if (onSettings_) onSettings_(json);   // structured: not a console command
    return;
  } else if (!strcmp(type, "face")) {
    if (onFace_) onFace_(json);
    return;
  } else if (!strcmp(type, "motion")) {
    // {"type":"motion","axis":"pan","speed":450,"accel":4000}: the neck's speed limits (dances run it fast)
    cmd = String("motion ") + (doc["axis"] | "pan") + " " + String(doc["speed"] | 0.0f, 0) + " " +
          String(doc["accel"] | 0.0f, 0);
  } else if (!strcmp(type, "snapshot")) {
    cmd = String("snapshot ") + (doc["size"] | "xga");
  } else if (!strcmp(type, "stream")) {
    cmd = String("stream ") + ((doc["on"] | false) ? "on " : "off ") + String(doc["fps"] | 10.0f, 1) + " " +
          (doc["size"] | "qvga");
  } else {
    Serial.printf("brain: unknown message type \"%s\"\n", type);
    return;
  }
  if (onCommand_) onCommand_(cmd);
}
