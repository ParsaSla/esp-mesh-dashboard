#include "LdrSensor.h"

#include <Arduino.h>

LdrSensor::LdrSensor(const char *id, std::uint8_t analogPin, bool enabled) : Sensor(id, enabled), mAnalogPin(analogPin) {}

bool LdrSensor::begin()
{
  pinMode(mAnalogPin, INPUT);
  return true;
}

bool LdrSensor::read(SensorReading *readings, std::size_t capacity)
{

  readings[0] = {getId(), "raw", static_cast<float>(analogRead(mAnalogPin)),
                 "adc_counts"};
  return true;
}
