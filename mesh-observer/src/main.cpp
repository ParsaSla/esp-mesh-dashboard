#include <Arduino.h>

#include "EspNowMesh.h"

EspNowMesh mesh;

constexpr size_t kMeshPayloadCapacity = 200;
uint8_t serialMessage[kMeshPayloadCapacity + 1];
size_t serialMessageLength = 0;
bool serialMessageOverflow = false;

void relaySerialMessages()
{
  while (Serial.available() > 0)
  {
    const int incoming = Serial.read();
    if (incoming < 0)
    {
      return;
    }

    if (incoming == '\n')
    {
      if (!serialMessageOverflow)
      {
        if (serialMessageLength > 0 &&
            serialMessage[serialMessageLength - 1] == '\r')
        {
          --serialMessageLength;
        }

        if (serialMessageLength > kMeshPayloadCapacity)
        {
          Serial.println("Serial message exceeds mesh payload limit; discarded");
        }
        else if (serialMessageLength > 0 &&
                 !mesh.publish(serialMessage, serialMessageLength))
        {
          Serial.println("Failed to queue serial message for mesh");
        }
      }
      else
      {
        Serial.println("Serial message exceeds mesh payload limit; discarded");
      }

      serialMessageLength = 0;
      serialMessageOverflow = false;
      continue;
    }

    if (!serialMessageOverflow)
    {
      if (serialMessageLength < sizeof(serialMessage))
      {
        serialMessage[serialMessageLength++] = static_cast<uint8_t>(incoming);
      }
      else
      {
        serialMessageOverflow = true;
      }
    }
  }
}

void printMessage(const MeshMessage &message,
                  const MeshReceiveInfo &receiveInfo, void *context)
{
  (void)context;

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
  Serial.println(" | message: ");
  Serial.write(message.payload, message.payloadLength);
  Serial.println();
}

void printMessageJson(const MeshMessage &message,
                      const MeshReceiveInfo &receiveInfo, void *context)
{
  (void)context;

  Serial.printf(
      "{\"sender\":\"%02X:%02X:%02X:%02X:%02X:%02X\",\"rssi\":",
      receiveInfo.sender[0], receiveInfo.sender[1], receiveInfo.sender[2],
      receiveInfo.sender[3], receiveInfo.sender[4], receiveInfo.sender[5]);
  if (receiveInfo.rssi == static_cast<int8_t>(-128))
  {
    Serial.print("null");
  }
  else
  {
    Serial.print(static_cast<int>(receiveInfo.rssi));
  }

  Serial.printf(
      ",\"origin\":\"%02X:%02X:%02X:%02X:%02X:%02X\","
      "\"destination\":\"%02X:%02X:%02X:%02X:%02X:%02X\","
      "\"sequence\":%u,\"ttl\":%u,\"hops\":%u,\"payload\":\"",
      message.origin[0], message.origin[1], message.origin[2],
      message.origin[3], message.origin[4], message.origin[5],
      message.destination[0], message.destination[1], message.destination[2],
      message.destination[3], message.destination[4], message.destination[5],
      static_cast<unsigned int>(message.sequence),
      static_cast<unsigned int>(message.ttl),
      static_cast<unsigned int>(message.hops));

  for (size_t i = 0; i < message.payloadLength; ++i)
  {
    const uint8_t byte = message.payload[i];
    if (byte == '"' || byte == '\\')
    {
      Serial.write('\\');
      Serial.write(byte);
    }
    else if (byte < 0x20 || byte >= 0x7F)
    {
      Serial.printf("\\u%04X", static_cast<unsigned int>(byte));
    }
    else
    {
      Serial.write(byte);
    }
  }

  Serial.println("\"}");
}

void setup()
{
  Serial.begin(115200);

  const unsigned long serialStart = millis();
  while (!Serial && millis() - serialStart < 5000)
  {
    delay(10);
  }

  mesh.setMessageHandler(&printMessageJson);
  if (!mesh.begin())
  {
    Serial.println("Failed to initialize ESP-NOW Mesh");
    return;
  }
  Serial.println("ESP-NOW Mesh observer initialized");
}

void loop()
{
  mesh.update();
  relaySerialMessages();
}
