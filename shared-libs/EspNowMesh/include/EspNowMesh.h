#pragma once

#include <cstddef>
#include <cstdint>

#include <esp_idf_version.h>
#include <esp_now.h>
#include <freertos/FreeRTOS.h>
#include <freertos/queue.h>

struct MeshMessage
{
  uint8_t origin[6];      // MAC address of the node that created the message.
  uint8_t destination[6]; // Target MAC, or FF:FF:FF:FF:FF:FF for everyone.
  uint32_t sequence;      // Per-sender number used to recognize repeated packets.
  uint8_t ttl;            // Remaining number of times the packet may be relayed.
  uint8_t hops;           // Number of relays the packet has passed through.
  uint8_t payload[200];   // Application data carried by this message.
  size_t payloadLength;   // Number of payload bytes that are in use.
};

struct MeshReceiveInfo
{
  uint8_t sender[6]; // MAC address of the node that transmitted this radio hop.
  int8_t rssi;       // Received signal strength in dBm, or -128 if unavailable.
};

class EspNowMesh
{
public:
  // Called for applicable messages with metadata for their last radio hop.
  using MessageHandler = void (*)(const MeshMessage &message,
                                  const MeshReceiveInfo &receiveInfo,
                                  void *context);

  EspNowMesh();
  ~EspNowMesh();

  EspNowMesh(const EspNowMesh &) = delete;
  EspNowMesh &operator=(const EspNowMesh &) = delete;

  bool begin();  // Set up Wi-Fi, ESP-NOW, the receive queue, and broadcast peer.
  void update(); // Process received packets; call regularly from Arduino loop().
  // Sends a message to every node in the mesh.
  bool publish(const uint8_t *payload, size_t length);
  // Sends a message addressed to the node with this MAC address.
  bool publishTo(const uint8_t destination[6], const uint8_t *payload,
                 size_t length);
  // Broadcasts "RSSI" followed by MAC/RSSI hop records; the origin RSSI is -128.
  bool publishRssiProbe();
  static bool isRssiProbe(const MeshMessage &message);
  // Register the function to call when an applicable message is received.
  void setMessageHandler(MessageHandler handler, void *context = nullptr);

private:
  struct ReceivedPacket
  {
    MeshMessage message;
    MeshReceiveInfo receiveInfo;
  };

  struct SeenPacket
  {
    uint8_t origin[6]; // Together with sequence, uniquely identifies a packet.
    uint32_t sequence;
    bool valid;
  };

  static void onReceive(const esp_now_recv_info_t *info, const uint8_t *data,
                        int length);
  // ESP-IDF 5.5 changed the send callback to pass transmit-info metadata.
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
  static void onSent(const esp_now_send_info_t *info,
                     esp_now_send_status_t status);
#else
  static void onSent(const uint8_t *destination, esp_now_send_status_t status);
#endif
  bool hasSeen(const MeshMessage &message) const;
  void remember(const MeshMessage &message);
  bool sendPacket(MeshMessage &message);

  static constexpr size_t kReceiveQueueLength = 8;
  static constexpr size_t kDuplicateCacheSize = 32;
  static constexpr uint8_t kInitialTtl = 5;

  // ESP-NOW callbacks are plain functions, so they route packets to this instance.
  static EspNowMesh *mActiveInstance;
  MessageHandler mMessageHandler;
  void *mMessageHandlerContext;
  QueueHandle_t mReceivedQueue; // Moves packets safely from callback to update().
  volatile bool mSendStatusPending;
  volatile bool mLastSendSucceeded;
  SeenPacket mSeenPackets[kDuplicateCacheSize];
  size_t mNextSeenPacket;
  uint8_t mMacAddress[6];
  bool mInitialized;
};
