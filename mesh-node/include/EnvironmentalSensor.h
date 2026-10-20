#pragma once

#include "Sensor.h"

#include <Adafruit_AHTX0.h>
#include <Adafruit_BMP280.h>
#include <cstdint>
#include <Wire.h>

class EnvironmentalSensor : public Sensor
{
public:
  EnvironmentalSensor(const char *id, bool enabled = true);

  bool begin() override;
  std::size_t getReadingCount() const { return mReadingCount; };
  bool read(SensorReading *readings, std::size_t capacity) override;
  bool end() override { return true; };

protected:
private:
  Adafruit_AHTX0 aht;
  Adafruit_Sensor *aht_humidity, *aht_temp;
  Adafruit_BMP280 bmp{&Wire};

  sensors_event_t humidity;
  sensors_event_t temp;

  std::size_t mReadingCount = 3;
};
