#include "AdcSensor.h"

#include <Arduino.h>

AdcSensor::AdcSensor(const char *id, std::uint8_t analogPin, char readingId, bool enabled)
    : Sensor(id, enabled), mAnalogPin(analogPin), mReadingId(readingId) {}

bool AdcSensor::begin()
{
  pinMode(mAnalogPin, INPUT);
  setInitialized(true);
  return true;
}

bool AdcSensor::read(SensorReading *readings, std::size_t capacity)
{
  if (!hasValidReadingBuffer(readings, capacity))
  {
    return false;
  }

  readings[0] = {mReadingId, static_cast<float>(analogRead(mAnalogPin))};
  return true;
}
