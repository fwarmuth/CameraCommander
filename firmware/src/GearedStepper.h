#ifndef GEARED_STEPPER_H
#define GEARED_STEPPER_H

#include <Arduino.h>
#include <AccelStepper.h>

/*
  ─────────────────────────────────────────────────────────────
  GearedStepper  — A small wrapper around AccelStepper that
  also understands the gear-box mounted on the motor.
  ─────────────────────────────────────────────────────────────
  step_pin, dir_pin, enable_pin  →  A4988 / TMC step-dir-enable lines
  ms1 … ms3                      →  micro-step selector lines
  base_steps_per_rot             →  full steps per *motor* revolution
  gear_ratio                     →  (output-shaft rev) / (motor rev)
                                    e.g. 11.335 means one motor rev
                                         turns the table 1 / 11.335 rev
*/
class GearedStepper {
public:
    GearedStepper(uint8_t step_pin, uint8_t dir_pin, uint8_t enable_pin,
                  uint8_t ms1_pin,  uint8_t ms2_pin,  uint8_t ms3_pin,
                  long    base_steps_per_rot,
                  float   gear_ratio = 1.0f);

    /* ---------- life-cycle ---------- */
    void begin();

    /* ---------- motion -------------- */
    void setMaxSpeed(float speed);
    void setAcceleration(float acceleration);
    void moveTo(long absolute);
    void move(long relative);
    bool run();
    void runToPosition();
    void stop();

    /* ---------- position helpers ---- */
    long  currentPosition();
    void  setCurrentPosition(long position);
    long  distanceToGo();
    long  targetPosition();
    bool  isRunning();

    /* ---------- driver power -------- */
    void enable();
    void disable();

    /* ---------- micro-stepping ------- */
    void setMicrostepResolution(int resolution);   // 1,2,4,8,16
    int  getMicrostepResolution() const;

    /* ---------- gearing ------------- */
    float getGearRatio()              const;
    long  getBaseStepsPerRotation()   const;       // motor side
    long  getOutputStepsPerRotation() const;       // turntable side
    float maxSpeed();
    float acceleration();

private:
    /* pins */
    const uint8_t _enable_pin;
    const uint8_t _ms1_pin, _ms2_pin, _ms3_pin;

    /* mechanics */
    const long  _base_steps_per_rot;
    const float _gear_ratio;
    int         _microstep_resolution;

    /* low-level driver */
    AccelStepper _stepper;
};

#endif /* GEARED_STEPPER_H */
