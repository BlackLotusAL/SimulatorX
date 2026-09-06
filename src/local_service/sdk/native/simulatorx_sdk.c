#define _POSIX_C_SOURCE 200809L
#include "simulatorx_sdk.h"
#include <arpa/inet.h>
#include <errno.h>
#include <fcntl.h>
#include <limits.h>
#include <math.h>
#include <poll.h>
#include <stddef.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/socket.h>
#include <sys/un.h>
#include <time.h>
#include <unistd.h>

_Static_assert(sizeof(double) == 8, "Reference wire ABI requires binary64 double");
_Static_assert(sizeof(SX_State) == 48, "Unexpected reference SX_State layout");

static int64_t milliseconds(void) {
    struct timespec now;
    if (clock_gettime(CLOCK_MONOTONIC, &now) < 0) return -1;
    return (int64_t)now.tv_sec * 1000 + now.tv_nsec / 1000000;
}

static int wait_ready(int fd, short events, int64_t deadline) {
    for (;;) {
        int64_t now = milliseconds();
        int64_t remaining = deadline - now;
        if (now < 0) return -1;
        if (remaining <= 0) { errno = ETIMEDOUT; return -1; }
        struct pollfd item = {fd, events, 0};
        int result = poll(&item, 1, remaining > INT_MAX ? INT_MAX : (int)remaining);
        if (result > 0) {
            if (item.revents & POLLNVAL) { errno = EBADF; return -1; }
            return 0; /* send/recv/getsockopt will classify ERR/HUP. */
        }
        if (result == 0) { errno = ETIMEDOUT; return -1; }
        if (errno != EINTR) return -1;
    }
}

static int transfer(int fd, unsigned char *buffer, size_t length, int sending, int64_t deadline) {
    size_t offset = 0;
    while (offset < length) {
        if (wait_ready(fd, sending ? POLLOUT : POLLIN, deadline) < 0) return -1;
        ssize_t count = sending
            ? send(fd, buffer + offset, length - offset, MSG_NOSIGNAL)
            : recv(fd, buffer + offset, length - offset, 0);
        if (count > 0) { offset += (size_t)count; continue; }
        if (count == 0) { errno = ECONNRESET; return -1; }
        if (errno != EINTR && errno != EAGAIN && errno != EWOULDBLOCK) return -1;
    }
    return 0;
}

static void put_u32(unsigned char *p, uint32_t value) {
    uint32_t network = htonl(value);
    memcpy(p, &network, 4);
}

static uint32_t get_u32(const unsigned char *p) {
    uint32_t network;
    memcpy(&network, p, 4);
    return ntohl(network);
}

static void put_double(unsigned char *p, double value) {
    uint64_t bits;
    memcpy(&bits, &value, 8);
    for (int i = 7; i >= 0; --i) { p[i] = (unsigned char)(bits & 255); bits >>= 8; }
}

static double get_double(const unsigned char *p) {
    uint64_t bits = 0;
    double value;
    for (int i = 0; i < 8; ++i) bits = (bits << 8) | p[i];
    memcpy(&value, &bits, 8);
    return value;
}

static int32_t control_failure(int saved_errno) {
    char line[192];
    int length = snprintf(line, sizeof(line), "pid=%ld SDK control failure errno=%d\n",
                          (long)getpid(), saved_errno);
    const char *path = getenv("SIMULATORX_SDK_ERROR_FILE");
    int fd = path && *path ? open(path, O_WRONLY | O_CREAT | O_APPEND | O_CLOEXEC, 0600) : -1;
    if (fd >= 0) {
        ssize_t written = write(fd, line, (size_t)length);
        if (written != length) fputs(line, stderr);
        close(fd);
    } else {
        fputs(line, stderr);
    }
    return SX_CONTROL_ERROR;
}

static int32_t call(uint32_t opcode, int32_t axis, double first, double second, SX_State *state) {
    const char *path = getenv("SIMULATORX_CONTROL_SOCKET");
    const char *setting = getenv("SIMULATORX_CONTROL_TIMEOUT_MS");
    long timeout = 1000;
    struct sockaddr_un address;
    unsigned char request[28] = {0}, response[56];
    if (!isfinite(first) || !isfinite(second)) return SX_INVALID_ARGUMENT;
    if (!path || !*path || strlen(path) >= sizeof(address.sun_path))
        return control_failure(EINVAL);
    if (setting && *setting) {
        char *end;
        errno = 0;
        timeout = strtol(setting, &end, 10);
        if (errno || *end || timeout < 1 || timeout > 60000) return control_failure(EINVAL);
    }
    int64_t now = milliseconds();
    if (now < 0) return control_failure(errno);
    int64_t deadline = now + timeout;
    int fd = socket(AF_UNIX, SOCK_STREAM | SOCK_NONBLOCK | SOCK_CLOEXEC, 0);
    if (fd < 0) return control_failure(errno);
    memset(&address, 0, sizeof(address));
    address.sun_family = AF_UNIX;
    memcpy(address.sun_path, path, strlen(path) + 1);
    if (connect(fd, (struct sockaddr *)&address,
                (socklen_t)(offsetof(struct sockaddr_un, sun_path) + strlen(path) + 1)) < 0) {
        if (errno != EINPROGRESS && errno != EINTR) goto failed;
        if (wait_ready(fd, POLLOUT, deadline) < 0) goto failed;
        int error = 0;
        socklen_t size = sizeof(error);
        if (getsockopt(fd, SOL_SOCKET, SO_ERROR, &error, &size) < 0) goto failed;
        if (error) { errno = error; goto failed; }
    }
    memcpy(request, "SX01", 4);
    put_u32(request + 4, opcode);
    put_u32(request + 8, (uint32_t)axis);
    put_double(request + 12, first);
    put_double(request + 20, second);
    /* One exchange, no reconnect/retry: a lost reply may follow an executed command. */
    if (transfer(fd, request, sizeof(request), 1, deadline) < 0 ||
        transfer(fd, response, sizeof(response), 0, deadline) < 0) goto failed;
    close(fd);
    if (memcmp(response, "SX01", 4)) return control_failure(EPROTO);
    uint32_t raw_code = get_u32(response + 4);
    int32_t code;
    memcpy(&code, &raw_code, sizeof(code));
    if (code == SX_CONTROL_ERROR) return control_failure(EPROTO);
    if (code == SX_OK && state) {
        SX_State decoded;
        decoded.position_deg = get_double(response + 8);
        decoded.velocity_deg_s = get_double(response + 16);
        decoded.target_deg = get_double(response + 24);
        decoded.enabled = get_u32(response + 32);
        decoded.homed = get_u32(response + 36);
        decoded.busy = get_u32(response + 40);
        decoded.done = get_u32(response + 44);
        decoded.alarm = get_u32(response + 48);
        decoded.faults = get_u32(response + 52);
        if (!isfinite(decoded.position_deg) || !isfinite(decoded.velocity_deg_s) ||
            !isfinite(decoded.target_deg) || decoded.enabled > 1 || decoded.homed > 1 ||
            decoded.busy > 1 || decoded.done > 1 || decoded.faults > 7)
            return control_failure(EPROTO);
        *state = decoded;
    }
    return code;
failed:
    {
        int saved_errno = errno;
        close(fd);
        return control_failure(saved_errno);
    }
}

int32_t SX_Enable(int32_t axis) { return call(1, axis, 0, 0, NULL); }
int32_t SX_Disable(int32_t axis) { return call(2, axis, 0, 0, NULL); }
int32_t SX_Home(int32_t axis, double speed) { return call(3, axis, speed, 0, NULL); }
int32_t SX_MoveAbsolute(int32_t axis, double angle, double speed) { return call(4, axis, angle, speed, NULL); }
int32_t SX_MoveRelative(int32_t axis, double angle, double speed) { return call(5, axis, angle, speed, NULL); }
int32_t SX_Stop(int32_t axis) { return call(6, axis, 0, 0, NULL); }
int32_t SX_ClearFault(int32_t axis) { return call(7, axis, 0, 0, NULL); }
int32_t SX_GetPosition(int32_t axis, double *position) {
    if (!position) return SX_INVALID_ARGUMENT;
    SX_State state;
    int32_t code = call(8, axis, 0, 0, &state);
    if (code == SX_OK) *position = state.position_deg;
    return code;
}
int32_t SX_GetState(int32_t axis, SX_State *state) {
    if (!state) return SX_INVALID_ARGUMENT;
    return call(9, axis, 0, 0, state);
}
