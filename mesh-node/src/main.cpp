#include <Arduino.h>
#include "EspNowMesh.h"
#include "SensorManager.h"
#include "AdcSensor.h"
#include "EnvironmentalSensor.h"

#include <climits>
#include <cstring>
#include <limits>
#include <Wire.h>
#include <esp_sleep.h>

#define SDA 18
#define SCL 19

// put function declarations here:
void sendMessage(const char *message);
void sendReadings();
void processMessage(const MeshMessage &message, const MeshReceiveInfo &receiveInfo, void *context);

EspNowMesh mesh;
SensorManager sensorManager;
AdcSensor ldrSensor("ldr1", 35, 'L');
AdcSensor soilMoistureSensor("soil1", 32, 'S');
// AdcSensor uvSensor("uv1", 33, 'U');
AdcSensor microphoneSensor("mic1", 33, 'M');
EnvironmentalSensor environmentalSensor("env1");

const char kSendDataCommand[] = "SEND_DATA";
const char kChangeIntervalCommand[] = "CHANGE_INTERVAL";
const char kDeepSleepCommand[] = "DEEP_SLEEP";
const char kMeasureRssiCommand[] = "MEASURE_RSSI";

unsigned long lastUpdate = 0;
unsigned long updateInterval = 3000;

void setup()
{
  Serial.begin(115200);
  Wire.begin(SDA, SCL);

  if (!sensorManager.add(ldrSensor))
  {
    Serial.println("Failed to add LDR sensor");
  }
  if (!sensorManager.add(soilMoistureSensor))
  {
    Serial.println("Failed to add soil moisture sensor");
  }
  // if (!sensorManager.add(uvSensor))
  // {
  //   Serial.println("Failed to add UV sensor");
  // }
  if (!sensorManager.add(microphoneSensor))
  {
    Serial.println("Failed to add microphone sensor");
  }
  if (!sensorManager.add(environmentalSensor))
  {
    Serial.println("Failed to add environmental sensor");
  }
  if (!sensorManager.begin())
  {
    Serial.println("Failed to begin sensor manager");
  }

  // Give the serial monitor time to connect when using the ESP32-S3 USB port.
  const unsigned long serialStart = millis();
  while (!Serial && millis() - serialStart < 5000)
  {
    delay(10);
  }

  Serial.println("ESP-NOW Mesh Example");
  if (!mesh.begin())
  {
    Serial.println("Failed to initialize ESP-NOW Mesh");
    Serial.flush();
    return;
  }
  Serial.println("ESP-NOW Mesh initialized successfully");
  mesh.setMessageHandler(&processMessage);
}

void loop()
{
  mesh.update();

  unsigned long currentTime = millis();
  if (currentTime - lastUpdate >= updateInterval)
  {
    std::string *message = new std::string();
    sensorManager.getReadingsJson(*message);
    // Serial.println(message->c_str());
    const bool queued = mesh.publish(
        reinterpret_cast<const uint8_t *>(message->c_str()), message->length());

    lastUpdate = currentTime;
  }
}

void sendMessage(const char *message)
{
  const bool queued = mesh.publish(
      reinterpret_cast<const uint8_t *>(message), strlen(message));
  if (!queued)
  {
    Serial.println("Failed to queue message");
  }
}

void sendReadings()
{
  std::string *message = new std::string();
  sensorManager.getReadingsJson(*message);
  sendMessage(message->c_str());
}

// put function definitions here:
void processMessage(const MeshMessage &message,
                    const MeshReceiveInfo &receiveInfo, void *context)
{
  (void)context;

  if (message.payloadLength == sizeof(kMeasureRssiCommand) - 1 &&
      std::memcmp(message.payload, kMeasureRssiCommand,
                  message.payloadLength) == 0)
  {
    if (!mesh.publishRssiProbe())
    {
      Serial.println("Failed to queue RSSI probe");
    }
    return;
  }

  if (mesh.isRssiProbe(message))
  {
    return;
  }

  if (message.payloadLength == strlen(kSendDataCommand) &&
      std::memcmp(message.payload, kSendDataCommand, message.payloadLength) == 0)
  {
    sendReadings();
  }

  const size_t commandLength = sizeof(kChangeIntervalCommand) - 1;
  if (message.payloadLength > commandLength + 1 &&
      std::memcmp(message.payload, kChangeIntervalCommand,
                  commandLength) == 0 &&
      message.payload[commandLength] == ' ')
  {
    unsigned long requestedInterval = 0;
    bool validInterval = true;
    for (size_t i = commandLength + 1; i < message.payloadLength; ++i)
    {
      if (message.payload[i] < '0' || message.payload[i] > '9')
      {
        validInterval = false;
        break;
      }
      const unsigned long digit = message.payload[i] - '0';
      if (requestedInterval > (ULONG_MAX - digit) / 10)
      {
        validInterval = false;
        break;
      }
      requestedInterval = requestedInterval * 10 + digit;
    }

    if (validInterval && requestedInterval > 0)
    {
      updateInterval = requestedInterval;
      Serial.printf("Update interval set to %lu ms\n", updateInterval);
    }
  }

  Serial.printf("Received from %02X:%02X:%02X:%02X:%02X:%02X, RSSI ",
                receiveInfo.sender[0], receiveInfo.sender[1],
                receiveInfo.sender[2], receiveInfo.sender[3],
                receiveInfo.sender[4], receiveInfo.sender[5]);
  if (receiveInfo.rssi == static_cast<int8_t>(-128))
  {
    Serial.print("unavailable");
  }
  else
  {
    Serial.printf("%d dBm", receiveInfo.rssi);
  }
  Serial.printf(", origin %02X:%02X:%02X:%02X:%02X:%02X, sequence %u, ttl %u, hops %u",
                message.origin[0], message.origin[1], message.origin[2],
                message.origin[3], message.origin[4], message.origin[5],
                message.sequence, message.ttl, message.hops);
  Serial.print(" | message: ");
  for (size_t i = 0; i < message.payloadLength; ++i)
  {
    Serial.write(message.payload[i]);
  }
  Serial.println();

  const size_t sleepCommandLength = sizeof(kDeepSleepCommand) - 1;
  if (message.payloadLength > sleepCommandLength + 1 &&
      std::memcmp(message.payload, kDeepSleepCommand, sleepCommandLength) == 0 &&
      message.payload[sleepCommandLength] == ' ')
  {
    uint64_t requestedSleepMs = 0;
    bool validSleepDuration = true;
    for (size_t i = sleepCommandLength + 1; i < message.payloadLength; ++i)
    {
      if (message.payload[i] < '0' || message.payload[i] > '9')
      {
        validSleepDuration = false;
        break;
      }
      const uint64_t digit = message.payload[i] - '0';
      if (requestedSleepMs >
          (std::numeric_limits<uint64_t>::max() - digit) / 10)
      {
        validSleepDuration = false;
        break;
      }
      requestedSleepMs = requestedSleepMs * 10 + digit;
    }

    if (validSleepDuration && requestedSleepMs > 0 &&
        requestedSleepMs <= std::numeric_limits<uint64_t>::max() / 1000)
    {
      const uint64_t sleepDurationUs = requestedSleepMs * 1000;
      const esp_err_t sleepTimerResult =
          esp_sleep_enable_timer_wakeup(sleepDurationUs);
      if (sleepTimerResult != ESP_OK)
      {
        Serial.printf("Failed to configure deep sleep timer: %s\n",
                      esp_err_to_name(sleepTimerResult));
      }
      else
      {
        Serial.printf("Entering deep sleep for %llu ms\n",
                      static_cast<unsigned long long>(requestedSleepMs));
        Serial.flush();
        esp_deep_sleep_start();
      }
    }
    else
    {
      Serial.println("Invalid DEEP_SLEEP duration; expected milliseconds > 0");
    }
  }
}
