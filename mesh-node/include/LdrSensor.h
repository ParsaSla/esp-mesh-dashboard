#pragma once

#include "Sensor.h"

#include <cstdint>

class LdrSensor : public Sensor
{
public:
  LdrSensor(const char *id, std::uint8_t analogPin, bool enabled = true);

  std::size_t getReadingCount() const { return mReadingCount; };
  bool read(SensorReading *readings, std::size_t capacity) override;

private:
  bool begin() override;
  bool end() override { return true; }

  std::uint8_t mAnalogPin;
  std::size_t mReadingCount = 1;
};
