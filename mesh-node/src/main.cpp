#include <Arduino.h>
#include "EspNowMesh.h"
#include "SensorManager.h"
#include "LdrSensor.h"
#include "EnvironmentalSensor.h"

#include <cstring>
#include <Wire.h>

#define SDA 18
#define SCL 19

// put function declarations here:
void sendMessage(const char *message);
void sendReadings();
void processMessage(const MeshMessage &message, const MeshReceiveInfo &receiveInfo, void *context);

EspNowMesh mesh;
SensorManager sensorManager;
LdrSensor ldrSensor("ldr1", 35);
EnvironmentalSensor environmentalSensor("env1");

const char kSendDataCommand[] = "SEND_DATA";

unsigned long lastUpdate = 0;

void setup()
{
  Serial.begin(115200);
  Wire.begin(SDA, SCL);

  if (!sensorManager.add(ldrSensor))
  {
    Serial.println("Failed to add LDR sensor");
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
  if (currentTime - lastUpdate >= 3000)
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

  if (message.payloadLength == strlen(kSendDataCommand) &&
      std::memcmp(message.payload, kSendDataCommand, message.payloadLength) == 0)
  {
    sendReadings();
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
}
