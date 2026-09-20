/* Windows transport for the reference ABI; all wire encoding remains shared. */
#define WIN32_LEAN_AND_MEAN
#ifndef _WIN32_WINNT
#define _WIN32_WINNT 0x0600
#endif
#include <winsock2.h>
#include <windows.h>
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

typedef SOCKET sx_socket;

static int64_t milliseconds(void) {
    LARGE_INTEGER count, frequency;
    if (!QueryPerformanceCounter(&count) || !QueryPerformanceFrequency(&frequency)) return -1;
    return (count.QuadPart / frequency.QuadPart) * 1000
           + (count.QuadPart % frequency.QuadPart) * 1000 / frequency.QuadPart;
}

static void sx_close(SOCKET fd) { closesocket(fd); WSACleanup(); }

static int wait_ready(SOCKET fd, int sending, int64_t deadline) {
    for (;;) {
        int64_t remaining = deadline - milliseconds();
        if (remaining <= 0) { errno = ETIMEDOUT; return -1; }
        fd_set read_set, write_set, errors;
        FD_ZERO(&read_set); FD_ZERO(&write_set); FD_ZERO(&errors);
        if (sending) FD_SET(fd, &write_set); else FD_SET(fd, &read_set);
        FD_SET(fd, &errors);
        struct timeval timeout = {(long)(remaining / 1000), (long)(remaining % 1000) * 1000};
        int result = select(0, &read_set, &write_set, &errors, &timeout);
        if (result > 0) return 0;
        if (result == 0) { errno = ETIMEDOUT; return -1; }
        int error = WSAGetLastError();
        if (error != WSAEINTR) { errno = error; return -1; }
    }
}

static SOCKET windows_connect(const char *endpoint, int64_t deadline) {
    const char *prefix = "tcp://127.0.0.1:";
    if (strncmp(endpoint, prefix, strlen(prefix))) { errno = EINVAL; return INVALID_SOCKET; }
    char *end;
    long port = strtol(endpoint + strlen(prefix), &end, 10);
    if (*end || port < 1 || port > 65535) { errno = EINVAL; return INVALID_SOCKET; }
    WSADATA data;
    int error = WSAStartup(MAKEWORD(2, 2), &data);
    if (error) { errno = error; return INVALID_SOCKET; }
    SOCKET fd = socket(AF_INET, SOCK_STREAM, IPPROTO_TCP);
    if (fd == INVALID_SOCKET) { errno = WSAGetLastError(); WSACleanup(); return fd; }
    u_long nonblocking = 1;
    if (ioctlsocket(fd, FIONBIO, &nonblocking)) goto failed;
    struct sockaddr_in address = {0};
    address.sin_family = AF_INET;
    address.sin_addr.s_addr = htonl(INADDR_LOOPBACK);
    address.sin_port = htons((unsigned short)port);
    if (connect(fd, (struct sockaddr *)&address, sizeof(address)) == SOCKET_ERROR) {
        error = WSAGetLastError();
        if (error != WSAEWOULDBLOCK && error != WSAEINPROGRESS) goto failed;
        if (wait_ready(fd, 1, deadline) < 0) { sx_close(fd); return INVALID_SOCKET; }
        int size = sizeof(error);
        if (getsockopt(fd, SOL_SOCKET, SO_ERROR, (char *)&error, &size)) goto failed;
        if (error) { sx_close(fd); errno = error; return INVALID_SOCKET; }
    }
    return fd;
failed:
    error = WSAGetLastError();
    sx_close(fd); errno = error; return INVALID_SOCKET;
}

static int transfer(SOCKET fd, unsigned char *buffer, size_t length, int sending, int64_t deadline) {
    size_t offset = 0;
    while (offset < length) {
        if (wait_ready(fd, sending, deadline) < 0) return -1;
        int count = sending ? send(fd, (char *)buffer + offset, (int)(length - offset), 0)
                            : recv(fd, (char *)buffer + offset, (int)(length - offset), 0);
        if (count > 0) { offset += (size_t)count; continue; }
        if (!count) { errno = ECONNRESET; return -1; }
        int error = WSAGetLastError();
        if (error != WSAEINTR && error != WSAEWOULDBLOCK) { errno = error; return -1; }
    }
    return 0;
}

static int32_t control_failure(int saved_errno) {
    char line[192];
    int length = snprintf(line, sizeof(line), "pid=%lu SDK control failure errno=%d\n",
                          (unsigned long)GetCurrentProcessId(), saved_errno);
    wchar_t path[32768];
    DWORD size = GetEnvironmentVariableW(L"SIMULATORX_SDK_ERROR_FILE", path, 32768);
    HANDLE file = size && size < 32768 ? CreateFileW(path, FILE_APPEND_DATA,
        FILE_SHARE_READ | FILE_SHARE_WRITE | FILE_SHARE_DELETE, NULL, OPEN_ALWAYS,
        FILE_ATTRIBUTE_NORMAL, NULL) : INVALID_HANDLE_VALUE;
    DWORD written = 0;
    if (file != INVALID_HANDLE_VALUE) {
        if (!WriteFile(file, line, (DWORD)length, &written, NULL) || written != (DWORD)length)
            fputs(line, stderr);
        CloseHandle(file);
    } else fputs(line, stderr);
    return SX_CONTROL_ERROR;
}
