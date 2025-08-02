#include "GearedStepper.h"
#include <math.h>   // lroundf()

/* ── Constructor ─────────────────────────────────────────────────────── */
GearedStepper::GearedStepper(uint8_t step_pin, uint8_t dir_pin, uint8_t enable_pin,
                  uint8_t ms1_pin,  uint8_t ms2_pin,  uint8_t ms3_pin,
                  long    base_steps_per_rot,
                  float   gear_ratio)
    : _enable_pin(enable_pin),
      _ms1_pin(ms1_pin), _ms2_pin(ms2_pin), _ms3_pin(ms3_pin),
      _base_steps_per_rot(base_steps_per_rot),
      _gear_ratio(gear_ratio),
      _microstep_resolution(1),
      _stepper(AccelStepper::DRIVER, step_pin, dir_pin)
{
    // MS pins are shared between steppers, so they are configured in main.cpp
}

/* ── Life-cycle ───────────────────────────────────────────────────────── */
void GearedStepper::begin() {
    pinMode(_enable_pin, OUTPUT);
    pinMode(_ms1_pin, OUTPUT);
    pinMode(_ms2_pin, OUTPUT);
    pinMode(_ms3_pin, OUTPUT);
    disable();
    setMicrostepResolution(16); // Default to 1/16
}

/* ── Motion control passthroughs ──────────────────────────────────────── */
void GearedStepper::setMaxSpeed(float s)          { _stepper.setMaxSpeed(s); }
void GearedStepper::setAcceleration(float a)      { _stepper.setAcceleration(a); }
void GearedStepper::moveTo(long absPos)           { _stepper.moveTo(absPos); }
void GearedStepper::move(long rel)                { _stepper.move(rel); }
bool GearedStepper::run()                         { return _stepper.run(); }
void GearedStepper::runToPosition()               { _stepper.runToPosition(); }
void GearedStepper::stop()                        { _stepper.stop(); }

long GearedStepper::currentPosition()             { return _stepper.currentPosition(); }
void GearedStepper::setCurrentPosition(long p)    { _stepper.setCurrentPosition(p); }
long GearedStepper::distanceToGo()                { return _stepper.distanceToGo(); }
long GearedStepper::targetPosition()              { return _stepper.targetPosition(); }
bool GearedStepper::isRunning()                   { return _stepper.isRunning(); }


/* ── Driver enable/disable ────────────────────────────────────────────── */
void GearedStepper::enable()  { digitalWrite(_enable_pin, LOW);  }
void GearedStepper::disable() { digitalWrite(_enable_pin, HIGH); }

/* ── Micro-step handling ──────────────────────────────────────────────── */
void GearedStepper::setMicrostepResolution(int res)
{
    _microstep_resolution = res;
    switch (res) {
        case 1:  digitalWrite(_ms1_pin, LOW ); digitalWrite(_ms2_pin, LOW ); digitalWrite(_ms3_pin, LOW ); break;
        case 2:  digitalWrite(_ms1_pin, HIGH); digitalWrite(_ms2_pin, LOW ); digitalWrite(_ms3_pin, LOW ); break;
        case 4:  digitalWrite(_ms1_pin, LOW ); digitalWrite(_ms2_pin, HIGH); digitalWrite(_ms3_pin, LOW ); break;
        case 8:  digitalWrite(_ms1_pin, HIGH); digitalWrite(_ms2_pin, HIGH); digitalWrite(_ms3_pin, LOW ); break;
        case 16: digitalWrite(_ms1_pin, HIGH); digitalWrite(_ms2_pin, HIGH); digitalWrite(_ms3_pin, HIGH); break;
    }
}
int GearedStepper::getMicrostepResolution() const { return _microstep_resolution; }

/* ── Gearbox helpers ──────────────────────────────────────────────────── */
float GearedStepper::getGearRatio() const               { return _gear_ratio; }
long  GearedStepper::getBaseStepsPerRotation() const     { return _base_steps_per_rot; }
float GearedStepper::maxSpeed()                  const { return maxSpeed(); }
float GearedStepper::acceleration()              const { return acceleration(); }
long  GearedStepper::getOutputStepsPerRotation() const
{
    return lroundf(_base_steps_per_rot * _gear_ratio);
}
 