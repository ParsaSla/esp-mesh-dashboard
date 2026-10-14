#include "SensorManager.h"

#include <cmath>
#include <cstdio>
#include <cstring>
#include <vector>

namespace
{
  void appendJsonString(std::string &json, const char *value)
  {
    static constexpr char hexDigits[] = "0123456789abcdef";
    json.push_back('"');

    if (value != nullptr)
    {
      for (const unsigned char *character =
               reinterpret_cast<const unsigned char *>(value);
           *character != '\0'; ++character)
      {
        switch (*character)
        {
        case '"':
          json += "\\\"";
          break;
        case '\\':
          json += "\\\\";
          break;
        case '\b':
          json += "\\b";
          break;
        case '\f':
          json += "\\f";
          break;
        case '\n':
          json += "\\n";
          break;
        case '\r':
          json += "\\r";
          break;
        case '\t':
          json += "\\t";
          break;
        default:
          if (*character < 0x20)
          {
            json += "\\u00";
            json.push_back(hexDigits[*character >> 4]);
            json.push_back(hexDigits[*character & 0x0f]);
          }
          else
          {
            json.push_back(static_cast<char>(*character));
          }
          break;
        }
      }
    }

    json.push_back('"');
  }

  void appendOptionalJsonString(std::string &json, const char *value)
  {
    if (value == nullptr)
    {
      json += "null";
      return;
    }
    appendJsonString(json, value);
  }
} // namespace

SensorManager::SensorManager() : mSensors{}, mSensorCount(0) {}

bool SensorManager::add(Sensor &sensor)
{
  // sensor invalid, or manager full
  if (sensor.getId() == nullptr || sensor.getId()[0] == '\0' || mSensorCount == kMaxSensors)
  {
    return false;
  }

  // Check for duplicate sensor ID
  for (std::size_t i = 0; i < mSensorCount; ++i)
  {
    if (std::strcmp(mSensors[i]->getId(), sensor.getId()) == 0)
    {
      return false;
    }
  }

  mSensors[mSensorCount++] = &sensor;
  return true;
}

bool SensorManager::remove(Sensor &sensor)
{
  for (std::size_t i = 0; i < mSensorCount; ++i)
  {
    if (mSensors[i] == &sensor)
    {
      // Shift remaining sensors down to fill the gap
      for (std::size_t j = i; j < mSensorCount - 1; ++j)
      {
        mSensors[j] = mSensors[j + 1];
      }
      mSensors[--mSensorCount] = nullptr;
      return true;
    }
  }
  return false; // Sensor not found
}

std::size_t SensorManager::requiredReadingCount() const
{
  std::size_t total = 0;
  for (std::size_t i = 0; i < mSensorCount; ++i)
  {
    const Sensor &sensor = *mSensors[i];
    if (!sensor.isReady())
    {
      continue;
    }

    const std::size_t count = sensor.getReadingCount();

    total += count;
  }
  return total;
}

bool SensorManager::sampleEnabled(SensorReading *readings,
                                  std::size_t capacity)
{
  // Check if the provided buffer is valid and has enough capacity
  const std::size_t required = requiredReadingCount();
  if (capacity < required || (readings == nullptr && required != 0))
  {
    return false;
  }

  std::size_t offset = 0;
  for (std::size_t i = 0; i < mSensorCount; ++i)
  {
    Sensor &sensor = *mSensors[i];
    if (!sensor.isReady())
    {
      continue;
    }

    const std::size_t count = sensor.getReadingCount();
    if (count == 0)
    {
      continue;
    }

    if (!sensor.read(readings + offset, count))
    {
      return false;
    }
    offset += count;
  }
  return true;
}

bool SensorManager::getReadingsJson(std::string &json)
{
  std::vector<SensorReading> readings(requiredReadingCount());
  if (!sampleEnabled(readings.data(), readings.size()))
  {
    return false;
  }

  std::string result = "[";
  for (std::size_t i = 0; i < readings.size(); ++i)
  {
    if (i != 0)
    {
      result.push_back(',');
    }

    const SensorReading &reading = readings[i];
    result += "{\"sensorId\":";
    appendOptionalJsonString(result, reading.sensorId);
    result += ",\"channel\":";
    appendOptionalJsonString(result, reading.channel);
    result += ",\"value\":";
    if (std::isfinite(reading.value))
    {
      char value[32];
      const int length = std::snprintf(value, sizeof(value), "%.9g",
                                       static_cast<double>(reading.value));
      if (length < 0 || static_cast<std::size_t>(length) >= sizeof(value))
      {
        return false;
      }
      result.append(value, static_cast<std::size_t>(length));
    }
    else
    {
      result += "null";
    }
    result += ",\"unit\":";
    appendOptionalJsonString(result, reading.unit);
    result.push_back('}');
  }
  result.push_back(']');

  json.swap(result);
  return true;
}
