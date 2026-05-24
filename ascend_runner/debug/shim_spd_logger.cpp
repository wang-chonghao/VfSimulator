#include <string>

extern "C" bool spd_logger_is_log_enable(std::string, std::string)
    asm("_ZN10SPD_LOGGER7esl_log13is_log_enableESsSs");

extern "C" bool spd_logger_is_log_enable(std::string, std::string)
{
    return false;
}