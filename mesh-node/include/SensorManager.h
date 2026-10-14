#pragma once

#include "Sensor.h"

#include <string>

class SensorManager
{
public:
  // Maximum number of sensors that can be managed
  static constexpr std::size_t kMaxSensors = 8;

  // Constructor
  SensorManager();

  // Disable copy and assignment
  SensorManager(const SensorManager &) = delete;
  SensorManager &operator=(const SensorManager &) = delete;

  // Public methods
  bool add(Sensor &sensor);
  bool remove(Sensor &sensor);
  std::size_t requiredReadingCount() const;
  bool sampleEnabled(SensorReading *readings, std::size_t capacity);
  // Sample ready sensors and replace json with a JSON array of their readings.
  bool getReadingsJson(std::string &json);

private:
  Sensor *mSensors[kMaxSensors];
  std::size_t mSensorCount;
};
