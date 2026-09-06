#ifndef SIMULATORX_SDK_H
#define SIMULATORX_SDK_H

/* Reference ABI only. A vendor library requires its own header-compatible shim. */
#include <stdint.h>
#if defined(__GNUC__)
#define SX_API __attribute__((visibility("default")))
#else
#define SX_API
#endif
#ifdef __cplusplus
extern "C" {
#endif

enum {
    SX_OK = 0, SX_INVALID_ARGUMENT = 1, SX_NOT_ENABLED = 2, SX_NOT_HOMED = 3,
    SX_BUSY = 4, SX_LIMIT = 5, SX_FAULT = 6, SX_UNKNOWN_FUNCTION = 7
};
#define SX_CONTROL_ERROR INT32_MIN

typedef struct SX_State {
    double position_deg;
    double velocity_deg_s;
    double target_deg;
    uint32_t enabled;
    uint32_t homed;
    uint32_t busy;
    uint32_t done;
    uint32_t alarm;
    uint32_t faults; /* bit 0 stalled, bit 1 positive limit, bit 2 negative limit */
} SX_State;

SX_API int32_t SX_Enable(int32_t axis);
SX_API int32_t SX_Disable(int32_t axis);
SX_API int32_t SX_Home(int32_t axis, double speed_deg_s);
SX_API int32_t SX_MoveAbsolute(int32_t axis, double angle_deg, double speed_deg_s);
SX_API int32_t SX_MoveRelative(int32_t axis, double delta_deg, double speed_deg_s);
SX_API int32_t SX_Stop(int32_t axis);
SX_API int32_t SX_ClearFault(int32_t axis);
SX_API int32_t SX_GetPosition(int32_t axis, double *position_deg);
SX_API int32_t SX_GetState(int32_t axis, SX_State *state);

#ifdef __cplusplus
}
#endif
#endif
