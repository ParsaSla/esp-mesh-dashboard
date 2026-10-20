#include "LdrSensor.h"

#include <Arduino.h>

LdrSensor::LdrSensor(const char *id, std::uint8_t analogPin, bool enabled) : Sensor(id, enabled), mAnalogPin(analogPin) {}

bool LdrSensor::begin()
{
  pinMode(mAnalogPin, INPUT);
  setInitialized(true);
  return true;
}

bool LdrSensor::read(SensorReading *readings, std::size_t capacity)
{

  readings[0] = {'L', static_cast<float>(analogRead(mAnalogPin))};
  // Serial.printf("LDR Sensor %s: raw reading = %f\n", getId(), readings[0].value);
  return true;
}
