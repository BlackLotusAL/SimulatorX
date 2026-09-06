#include "simulatorx_sdk.h"
#include <inttypes.h>
#include <stdio.h>
#include <string.h>

/* Separate native SUT process: reads a command, invokes the dynamically loaded SDK,
 * and reports only the actual ABI result. It contains no simulator IPC code. */
int main(void) {
    char line[256], command[32];
    setvbuf(stdout, NULL, _IOLBF, 0);
    puts("ready");
    while (fgets(line, sizeof(line), stdin)) {
        double angle = 0, speed = 90;
        if (sscanf(line, "%31s %lf %lf", command, &angle, &speed) < 1) continue;
        int32_t result;
        if (!strcmp(command, "enable")) result = SX_Enable(1);
        else if (!strcmp(command, "disable")) result = SX_Disable(1);
        else if (!strcmp(command, "home")) result = SX_Home(1, 90);
        else if (!strcmp(command, "move")) result = SX_MoveAbsolute(1, angle, speed);
        else if (!strcmp(command, "relative")) result = SX_MoveRelative(1, angle, speed);
        else if (!strcmp(command, "stop")) result = SX_Stop(1);
        else if (!strcmp(command, "clear")) result = SX_ClearFault(1);
        else if (!strcmp(command, "position")) {
            double position = -9999.0;
            result = SX_GetPosition(1, &position);
            printf("{\"return\":%" PRId32 ",\"position\":%.17g}\n", result, position);
            continue;
        } else if (!strcmp(command, "state")) {
            SX_State state = {0};
            result = SX_GetState(1, &state);
            printf("{\"return\":%" PRId32 ",\"position\":%.17g,\"velocity\":%.17g,"
                   "\"busy\":%" PRIu32 ",\"done\":%" PRIu32 ",\"homed\":%" PRIu32 "}\n",
                   result, state.position_deg, state.velocity_deg_s, state.busy, state.done, state.homed);
            continue;
        } else if (!strcmp(command, "quit")) return 0;
        else return 2;
        printf("{\"return\":%" PRId32 "}\n", result);
    }
    return 0;
}
