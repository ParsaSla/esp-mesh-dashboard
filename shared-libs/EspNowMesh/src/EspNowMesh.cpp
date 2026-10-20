#include "EspNowMesh.h"

#include <Arduino.h>
#include <WiFi.h>

#include <cstring>
#include <esp_wifi.h>

namespace
{

  // ESP-NOW broadcasts use this special all-FF MAC address.
  constexpr uint8_t kBroadcastAddress[6] = {0xff, 0xff, 0xff,
                                            0xff, 0xff, 0xff};
  constexpr size_t kMaxEspNowPacketSize = 250;
  constexpr uint8_t kMeshWifiChannel = 1; // Use the same channel on every node.

  // The whole struct is sent directly, so keep it within the ESP-NOW v1 limit.
  static_assert(sizeof(MeshMessage) <= kMaxEspNowPacketSize,
                "MeshMessage exceeds the ESP-NOW v1 packet limit");

  // Check whether an address is the special all-nodes broadcast address.
  // A broadcast destination means any node may deliver the message to its app.
  bool isBroadcast(const uint8_t address[6])
  {
    return std::memcmp(address, kBroadcastAddress, sizeof(kBroadcastAddress)) ==
           0;
  }

} // namespace

EspNowMesh *EspNowMesh::mActiveInstance = nullptr;

// Start with empty queues and packet-tracking state.
EspNowMesh::EspNowMesh()
    : mMessageHandler(nullptr),
      mMessageHandlerContext(nullptr),
      mReceivedQueue(nullptr),
      mSendStatusPending(false),
      mLastSendSucceeded(false),
      mSeenPackets{},
      mNextSeenPacket(0),
      mNextSequence(0),
      mMacAddress{},
      mInitialized(false) {}

// Stop ESP-NOW and release the receive queue if startup succeeded.
EspNowMesh::~EspNowMesh()
{
  if (!mInitialized)
  {
    return;
  }

  if (mActiveInstance == this)
  {
    mActiveInstance = nullptr;
  }
  esp_now_unregister_recv_cb();
  esp_now_unregister_send_cb();
  esp_now_deinit();
  vQueueDelete(mReceivedQueue);
}

// Configure Wi-Fi and ESP-NOW, then register packet reception and broadcasting.
bool EspNowMesh::begin()
{
  if (mInitialized)
  {
    return true;
  }
  // The ESP-NOW receive callback has no user-data argument; this stub routes
  // callbacks through one active instance.
  if (mActiveInstance != nullptr && mActiveInstance != this)
  {
    Serial.println("EspNowMesh: only one active instance is supported.");
    return false;
  }
  if (!WiFi.mode(WIFI_STA))
  {
    Serial.println("EspNowMesh: failed to enable Wi-Fi station mode.");
    return false;
  }
  const esp_err_t setChannelResult =
      esp_wifi_set_channel(kMeshWifiChannel, WIFI_SECOND_CHAN_NONE);
  if (setChannelResult != ESP_OK)
  {
    Serial.printf("EspNowMesh: failed to set Wi-Fi channel: %s\n",
                  esp_err_to_name(setChannelResult));
    return false;
  }
  if (esp_wifi_get_mac(WIFI_IF_STA, mMacAddress) != ESP_OK)
  {
    Serial.println("EspNowMesh: failed to read station MAC address.");
    return false;
  }
  wifi_second_chan_t secondaryChannel;
  uint8_t primaryChannel;
  const esp_err_t channelResult =
      esp_wifi_get_channel(&primaryChannel, &secondaryChannel);
  if (channelResult != ESP_OK)
  {
    Serial.printf("EspNowMesh: failed to read Wi-Fi channel: %s\n",
                  esp_err_to_name(channelResult));
    return false;
  }
  Serial.printf("EspNowMesh: MAC %02X:%02X:%02X:%02X:%02X:%02X, channel %u\n",
                mMacAddress[0], mMacAddress[1], mMacAddress[2], mMacAddress[3],
                mMacAddress[4], mMacAddress[5], primaryChannel);

  // Keep the radio callback short by processing packets later in update().
  mReceivedQueue =
      xQueueCreate(kReceiveQueueLength, sizeof(ReceivedPacket));
  if (mReceivedQueue == nullptr)
  {
    Serial.println("EspNowMesh: failed to create receive queue.");
    return false;
  }
  if (esp_now_init() != ESP_OK)
  {
    Serial.println("EspNowMesh: ESP-NOW initialization failed.");
    vQueueDelete(mReceivedQueue);
    mReceivedQueue = nullptr;
    return false;
  }

  mActiveInstance = this;
  if (esp_now_register_send_cb(&EspNowMesh::onSent) != ESP_OK)
  {
    Serial.println("EspNowMesh: failed to register send callback.");
    mActiveInstance = nullptr;
    esp_now_deinit();
    vQueueDelete(mReceivedQueue);
    mReceivedQueue = nullptr;
    return false;
  }
  if (esp_now_register_recv_cb(&EspNowMesh::onReceive) != ESP_OK)
  {
    Serial.println("EspNowMesh: failed to register receive callback.");
    mActiveInstance = nullptr;
    esp_now_unregister_send_cb();
    esp_now_deinit();
    vQueueDelete(mReceivedQueue);
    mReceivedQueue = nullptr;
    return false;
  }

  // All packets go to the radio broadcast address. The destination inside
  // MeshMessage decides which node should deliver the application payload.
  esp_now_peer_info_t broadcastPeer{};
  std::memcpy(broadcastPeer.peer_addr, kBroadcastAddress,
              sizeof(kBroadcastAddress));
  broadcastPeer.channel = 0;
  broadcastPeer.ifidx = WIFI_IF_STA;
  broadcastPeer.encrypt = false;
  const esp_err_t peerResult = esp_now_add_peer(&broadcastPeer);
  if (peerResult != ESP_OK && peerResult != ESP_ERR_ESPNOW_EXIST)
  {
    Serial.printf("EspNowMesh: failed to add broadcast peer: %s\n", esp_err_to_name(peerResult));
    esp_now_unregister_recv_cb();
    esp_now_unregister_send_cb();
    mActiveInstance = nullptr;
    esp_now_deinit();
    vQueueDelete(mReceivedQueue);
    mReceivedQueue = nullptr;
    return false;
  }

  mInitialized = true;
  return true;
}

// Handle queued packets: ignore duplicates, deliver relevant messages, relay.
void EspNowMesh::update()
{
  if (!mInitialized)
  {
    return;
  }

  // The radio reports send completion asynchronously after publish() returns.
  if (mSendStatusPending)
  {
    mSendStatusPending = false;
    // Serial.println(mLastSendSucceeded ? "ESP-NOW radio send: success" : "ESP-NOW radio send: failed");
  }

  ReceivedPacket packet{};
  while (xQueueReceive(mReceivedQueue, &packet, 0) == pdTRUE)
  {
    MeshMessage &message = packet.message;
    // Reject invalid packets and packets already handled or relayed here.
    if (message.payloadLength > sizeof(message.payload) ||
        hasSeen(message))
    {
      continue;
    }
    remember(message);

    // Relayed packets are heard by everyone, but only the target (or all nodes
    // for a broadcast) invokes the application's handler.
    const bool addressedToThisNode = isBroadcast(message.destination) || std::memcmp(message.destination, mMacAddress, sizeof(mMacAddress)) == 0;

    if (addressedToThisNode && mMessageHandler != nullptr)
    {
      mMessageHandler(message, packet.receiveInfo, mMessageHandlerContext);
    }

    // Relay one fewer time at each hop. TTL prevents packets circling forever.
    if (message.ttl > 0)
    {
      --message.ttl;
      if (message.hops < UINT8_MAX)
      {
        ++message.hops;
      }
      if (!sendPacket(message))
      {
        Serial.println("EspNowMesh: failed to relay received message.");
      }
    }
  }
}

// Send an application payload to every node in the mesh.
bool EspNowMesh::publish(const uint8_t *payload, size_t length)
{
  // A broadcast is just a message whose destination is the broadcast MAC.
  return publishTo(kBroadcastAddress, payload, length);
}

// Build and send a payload addressed to one MAC address or to the whole mesh.
bool EspNowMesh::publishTo(const uint8_t destination[6],
                           const uint8_t *payload, size_t length)
{
  MeshMessage message;
  if (!mInitialized || destination == nullptr ||
      (payload == nullptr && length != 0) ||
      length > sizeof(message.payload))
  {
    return false;
  }

  std::memset(&message, 0, sizeof(message));
  // Fill in the routing metadata before copying the application payload.
  std::memcpy(message.origin, mMacAddress, sizeof(mMacAddress));
  std::memcpy(message.destination, destination, sizeof(message.destination));
  message.sequence = ++mNextSequence;
  message.ttl = kInitialTtl;
  message.payloadLength = length;
  if (length > 0)
  {
    std::memcpy(message.payload, payload, length);
  }

  if (!sendPacket(message))
  {
    return false;
  }
  remember(message);
  return true;
}

// Send a complete mesh packet over ESP-NOW's radio broadcast address.
bool EspNowMesh::sendPacket(MeshMessage &message)
{
  // Even directed messages are sent as radio broadcasts so intermediate
  // nodes can relay them across the flooding mesh.
  const esp_err_t result = esp_now_send(
      kBroadcastAddress, reinterpret_cast<const uint8_t *>(&message),
      sizeof(message));
  if (result != ESP_OK)
  {
    Serial.printf("EspNowMesh: ESP-NOW send failed: %s\n",
                  esp_err_to_name(result));
    return false;
  }
  return true;
}

// Check the bounded cache for a packet with this origin and sequence number.
bool EspNowMesh::hasSeen(const MeshMessage &message) const
{
  // A packet is considered the same when both its sender and sequence match.
  for (const SeenPacket &seen : mSeenPackets)
  {
    if (seen.valid && seen.sequence == message.sequence &&
        std::memcmp(seen.origin, message.origin, sizeof(seen.origin)) == 0)
    {
      return true;
    }
  }
  return false;
}

// Store a packet in the duplicate cache, replacing its oldest slot.
void EspNowMesh::remember(const MeshMessage &message)
{
  // Replace cache entries in a ring so duplicate tracking uses fixed memory.
  SeenPacket &seen = mSeenPackets[mNextSeenPacket];
  std::memcpy(seen.origin, message.origin, sizeof(seen.origin));
  seen.sequence = message.sequence;
  seen.valid = true;
  mNextSeenPacket = (mNextSeenPacket + 1) % kDuplicateCacheSize;
}

// Queue an ESP-NOW packet received using the ESP-IDF 5 callback signature.
// ESP-IDF 5 adds receive metadata to the callback.
void EspNowMesh::onReceive(const esp_now_recv_info_t *info,
                           const uint8_t *data, int length)
{
  const uint8_t *sender = info == nullptr ? nullptr : info->src_addr;
  const int8_t rssi = info != nullptr && info->rx_ctrl != nullptr
                          ? info->rx_ctrl->rssi
                          : static_cast<int8_t>(-128);
  EspNowMesh *instance = mActiveInstance;
  if (instance == nullptr || instance->mReceivedQueue == nullptr ||
      sender == nullptr || data == nullptr || length != sizeof(MeshMessage))
  {
    return;
  }

  // Copy the bytes into our queue; update() will validate and process them.
  ReceivedPacket packet{};
  std::memcpy(&packet.message, data, sizeof(packet.message));
  std::memcpy(packet.receiveInfo.sender, sender,
              sizeof(packet.receiveInfo.sender));
  packet.receiveInfo.rssi = rssi;
  if (packet.message.payloadLength > sizeof(packet.message.payload))
  {
    return;
  }
  if (xQueueSend(instance->mReceivedQueue, &packet, 0) != pdTRUE)
  {
    Serial.println("EspNowMesh: receive queue full; dropping message.");
  }
}

// Save the asynchronous send result for printing later from update().
#if ESP_IDF_VERSION >= ESP_IDF_VERSION_VAL(5, 5, 0)
void EspNowMesh::onSent(const esp_now_send_info_t *info,
                        esp_now_send_status_t status)
{
  (void)info;
#else
void EspNowMesh::onSent(const uint8_t *destination,
                        esp_now_send_status_t status)
{
  (void)destination;
#endif
  EspNowMesh *instance = mActiveInstance;
  if (instance == nullptr)
  {
    return;
  }

  instance->mLastSendSucceeded = (status == ESP_NOW_SEND_SUCCESS);
  instance->mSendStatusPending = true;
}

// Save the callback that receives messages for this mesh instance.
void EspNowMesh::setMessageHandler(MessageHandler handler, void *context)
{
  mMessageHandler = handler;
  mMessageHandlerContext = context;
}
