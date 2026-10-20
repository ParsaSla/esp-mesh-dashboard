#pragma once

#include <cstddef>

struct SensorReading
{
  char id;
  float value;
};

class Sensor
{
public:
  // Constructor/destructor
  explicit Sensor(const char *id, bool enabled) : mId(id), mEnabled(enabled), mInitialized(false) {}
  virtual ~Sensor() = default;

  // Disable copy and assignment
  Sensor(const Sensor &) = delete;
  Sensor &operator=(const Sensor &) = delete;

  // Public methods
  virtual std::size_t getReadingCount() const = 0;
  virtual bool read(SensorReading *readings, std::size_t capacity) = 0;

  // Accessors
  const char *getId() const { return mId; }
  bool isEnabled() const { return mEnabled; };
  bool isReady() const { return mInitialized && mEnabled; }
  void setEnabled(bool enabled) { mEnabled = enabled; }

protected:
  // Buffer validation helper; must be called before read().
  bool hasValidReadingBuffer(SensorReading *readings, std::size_t capacity)
  {
    if (readings == nullptr || capacity < getReadingCount())
    {
      return false;
    }
    return true;
  }

  void setInitialized(bool initialized) { mInitialized = initialized; }

private:
  // Private members
  virtual bool begin() = 0;
  virtual bool end() = 0;

  const char *mId;
  bool mEnabled;
  bool mInitialized;

  friend class SensorManager;
};
