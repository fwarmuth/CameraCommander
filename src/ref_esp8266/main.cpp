/*
 * ─────────────────────────────────────────────────────────────────────────
 *  Dual-axis turntable – feedback build                    Jul-2025
 *  ----------------------------------------------------------------------
 *  Commands (case-insensitive)
 *  ----------------------------------------------------------------------
 *    V                         … report firmware version
 *    M <pan_deg> <tilt_deg>    … move both axes (relative, degrees) → DONE
 *
 *    1 2 4 8 6                 … set micro-step (6 == 16)
 *
 *    n                         … 1 µ-step on pan  (auto-bounce)
 *    c                         … full revolution on pan
 *    r                         … toggle pan direction
 *    x                         … stop pan
 *
 *    w                         … 1 µ-step on tilt (auto-bounce)
 *    p                         … full revolution on tilt
 *    t                         … toggle tilt direction
 *    z                         … stop tilt
 *
 *    X                         … stop BOTH axes
 *
 *    + / −                     … increase / decrease common output speed 10 %
 *
 *    d / e                     … disable / enable both drivers
 *  ----------------------------------------------------------------------
 *  Every accepted command replies:
 *      VERSION x.y.z   |  DONE   |  OK ...   |  ERR ...
 *  ----------------------------------------------------------------------
 */

#include <Arduino.h>
#include "GearedStepper.h"

/* ─── Firmware version ─────────────────────────────────────────────── */
#define FW_VERSION "1.0.1"

/* ─── Pin mapping (NodeMCU v3) ─────────────────────────────────────── */
#define TT_STEP_PIN     D4
#define TT_DIR_PIN      D5
#define TT_ENABLE_PIN   D0

#define VT_STEP_PIN     D6
#define VT_DIR_PIN      D7
#define VT_ENABLE_PIN   D0

#define MS1_PIN         D1
#define MS2_PIN         D2
#define MS3_PIN         D3

/* ─── Mechanics ────────────────────────────────────────────────────── */
constexpr long  MOTOR_STEPS_PER_REV = 100;
constexpr float GEAR_RATIO_TT       = 11.335f;
constexpr float GEAR_RATIO_VT       = 6.2f * 7.5f;

/* ─── Stepper instances ────────────────────────────────────────────── */
GearedStepper turntableStepper(
    TT_STEP_PIN, TT_DIR_PIN, TT_ENABLE_PIN,
    MS1_PIN, MS2_PIN, MS3_PIN,
    MOTOR_STEPS_PER_REV, GEAR_RATIO_TT);

GearedStepper tiltStepper(
    VT_STEP_PIN, VT_DIR_PIN, VT_ENABLE_PIN,
    MS1_PIN, MS2_PIN, MS3_PIN,
    MOTOR_STEPS_PER_REV, GEAR_RATIO_VT);

/* ─── Axis bookkeeping ────────────────────────────────────────────── */
struct Axis { GearedStepper& s; int dir = 1; };
Axis rot { turntableStepper };
Axis til { tiltStepper     };

/* ─── Helpers ──────────────────────────────────────────────────────── */
inline void ack(const char*  msg) { Serial.println(msg); }
inline void ack(const String& msg) { Serial.println(msg); }

long degToMicrosteps(GearedStepper& stp, float deg)
{
    long µ = stp.getOutputStepsPerRotation() *
             stp.getMicrostepResolution();
    return lroundf(deg / 360.0f * µ);
}

constexpr float RATIO_TT_TO_VT = GEAR_RATIO_VT / GEAR_RATIO_TT;
constexpr float ROT_SPEED0 = 150.0f, ROT_ACCEL0 = 80.0f;

void setRotaryMotorSpeed(float v, float a)
{
    turntableStepper.setMaxSpeed(v);
    turntableStepper.setAcceleration(a);
    tiltStepper.setMaxSpeed(v * RATIO_TT_TO_VT);
    tiltStepper.setAcceleration(a * RATIO_TT_TO_VT);
}

/* ─── setup() ──────────────────────────────────────────────────────── */
void setup()
{
    Serial.begin(9600);
    while (!Serial) ;

    rot.s.begin(); til.s.begin();
    setRotaryMotorSpeed(ROT_SPEED0, ROT_ACCEL0);

    /* full command list on boot */
    Serial.println(F(
      "Dual-axis turntable – firmware " FW_VERSION "\n"
      "--------------------------------------------------\n"
      "  V                         : firmware version\n"
      "  M <pan> <tilt>            : move axes (deg) → DONE\n"
      "  1 2 4 8 6                 : set micro-step (6=16)\n"
      "  n c r x                   : step / rev / dir / stop pan\n"
      "  w p t z                   : step / rev / dir / stop tilt\n"
      "  X                         : stop both axes\n"
      "  + / -                     : faster / slower\n"
      "  d / e                     : disable / enable drivers\n"
      "--------------------------------------------------"
    ));
}

/* ─── loop() ────────────────────────────────────────────────────────── */
void loop()
{
    /* keep steppers running */
    turntableStepper.run(); tiltStepper.run();
    if (!Serial.available()) return;

    /* read full line */
    String line = Serial.readStringUntil('\n');
    line.trim();
    if (!line.length()) return;
    char c = line.charAt(0);

    /* -------- Version ------------------------------------------------ */
    if (c == 'V' || c == 'v') { ack(String("VERSION ") + FW_VERSION); return; }

    /* -------- Move  M pan tilt --------------------------------------- */
    if (c == 'M' || c == 'm') {
        float panDeg = 0, tiltDeg = 0;
        if (sscanf(line.c_str() + 1, "%f %f", &panDeg, &tiltDeg) != 2) {
            ack("ERR Syntax"); return;
        }
        long ps = degToMicrosteps(turntableStepper, panDeg);
        long ts = degToMicrosteps(tiltStepper,    tiltDeg);
        turntableStepper.enable(); tiltStepper.enable();
        turntableStepper.move(ps); tiltStepper.move(ts);
        ack("OK M"); return;
    }

    if (c == 'Q' || c == 'q') {
        if (turntableStepper.distanceToGo() || tiltStepper.distanceToGo()) {
            ack("BUSY");
        } else {
            turntableStepper.disable(); tiltStepper.disable();
            ack("DONE");
        }
        return;
    }

    /* -------- μ-step -------------------------------------------------- */
    if (strchr("12486", c)) {
        int res = (c == '6') ? 16 : (c - '0');
        turntableStepper.setMicrostepResolution(res);
        tiltStepper.setMicrostepResolution(res);
        ack(String("OK MICROSTEP ") + res); return;
    }

    /* -------- Pan axis (rot) ----------------------------------------- */
    switch (c) {
        case 'n': case 'N': rot.s.move(rot.dir);                    ack("OK ROT STEP"); return;
        case 'c': case 'C': {
            long r = rot.s.getOutputStepsPerRotation() *
                     rot.s.getMicrostepResolution();
            rot.s.move(rot.dir * r);                                ack("OK ROT REV");  return;
        }
        case 'r': case 'R': rot.dir = -rot.dir;                     ack("OK ROT DIR");  return;
        case 'x': case 'X': rot.s.stop();                           ack("OK ROT STOP"); return;
    }

    /* -------- Tilt axis (til) ---------------------------------------- */
    switch (c) {
        case 'w': case 'W': til.s.move(til.dir);                    ack("OK TILT STEP"); return;
        case 'p': case 'P': {
            long r = til.s.getOutputStepsPerRotation() *
                     til.s.getMicrostepResolution();
            til.s.move(til.dir * r);                                ack("OK TILT REV");  return;
        }
        case 't': case 'T': til.dir = -til.dir;                     ack("OK TILT DIR");  return;
        case 'z':           til.s.stop();                           ack("OK TILT STOP"); return;
    }

    /* -------- Speed adjust ------------------------------------------- */
    if (c == '+' || c == '-') {
        float f = (c == '+') ? 1.10f : 0.90f;
        setRotaryMotorSpeed(turntableStepper.maxSpeed() * f, ROT_ACCEL0 * f);
        ack("OK SPEED"); return;
    }

    /* -------- Global stop -------------------------------------------- */
    if (c == 'X') { rot.s.stop(); til.s.stop(); ack("OK STOP"); return; }

    /* -------- Driver enable / disable -------------------------------- */
    if (c == 'd' || c == 'D') {
        turntableStepper.disable(); tiltStepper.disable(); ack("OK DRIVERS OFF"); return;
    }
    if (c == 'e' || c == 'E') {
        turntableStepper.enable();  tiltStepper.enable();  ack("OK DRIVERS ON");  return;
    }

    /* -------- Unknown ------------------------------------------------ */
    ack("ERR Unknown");
}
