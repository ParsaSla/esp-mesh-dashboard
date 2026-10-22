#pragma once

#include "Sensor.h"

#include <cstdint>

class AdcSensor : public Sensor
{
public:
  AdcSensor(const char *id, std::uint8_t analogPin, char readingId, bool enabled = true);

  std::size_t getReadingCount() const { return mReadingCount; };
  bool read(SensorReading *readings, std::size_t capacity) override;

private:
  bool begin() override;
  bool end() override { return true; }

  std::uint8_t mAnalogPin;
  char mReadingId;
  std::size_t mReadingCount = 1;
};
