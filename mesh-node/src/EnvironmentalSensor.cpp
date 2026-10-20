#include "EnvironmentalSensor.h"

#include <Arduino.h>

EnvironmentalSensor::EnvironmentalSensor(const char *id, bool enabled) : Sensor(id, enabled) {}

bool EnvironmentalSensor::begin()
{
  if (!aht.begin(&Wire))
  {
    Serial.println("Failed to initialize AHT sensor");
    return false;
  }

  aht_humidity = aht.getHumiditySensor();
  aht_temp = aht.getTemperatureSensor();

  if (!bmp.begin())
  {
    Serial.println("Failed to initialize BMP280 sensor");
    return false;
  }

  bmp.setSampling(Adafruit_BMP280::MODE_FORCED,   /* Operating Mode. */
                  Adafruit_BMP280::SAMPLING_X2,   /* Temp. oversampling */
                  Adafruit_BMP280::SAMPLING_X16); /* Pressure oversampling */

  setInitialized(true);
  return true;
}

bool EnvironmentalSensor::read(SensorReading *readings, std::size_t capacity)
{
  if (!hasValidReadingBuffer(readings, capacity))
  {
    return false;
  }

  if (!aht_humidity->getEvent(&humidity) || !aht_temp->getEvent(&temp))
  {
    Serial.println("Failed to read from AHT sensor");
    return false;
  }

  if (!bmp.takeForcedMeasurement())
  {
    Serial.println("Failed to take forced measurement from BMP280 sensor");
    return false;
  }

  readings[0] = {'T', temp.temperature};
  readings[1] = {'H', humidity.relative_humidity};
  readings[2] = {'P', bmp.readPressure()};

  return true;
}
