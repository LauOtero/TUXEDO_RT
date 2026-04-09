// Helper functions for C / Python interface
//
// Copyright (C) 2016-2018  Kevin O'Connor <kevin@koconnor.net>
//
// This file may be distributed under the terms of the GNU GPLv3 license.

#include <errno.h> // errno
#include <stdarg.h> // va_start
#include <stdint.h> // uint8_t
#include <stdio.h> // fprintf
#include <string.h> // strerror
#include <time.h> // struct timespec
#include <math.h> // sqrt
#include <sys/prctl.h>  // prctl
#include <sched.h>      // cpu_set_t
#include <pthread.h>    // pthread_setaffinity_np
#include "compiler.h" // __visible
#include "pyhelper.h" // get_monotonic

// Return the monotonic system time as a double
double __visible
get_monotonic(void)
{
    struct timespec ts;
    int ret = clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    if (ret) {
        report_errno("clock_gettime", ret);
        return 0.;
    }
    return (double)ts.tv_sec + (double)ts.tv_nsec * .000000001;
}

// Fill a 'struct timespec' with a system time stored in a double
struct timespec
fill_time(double time)
{
    time_t t = time;
    return (struct timespec) {t, (time - t)*1000000000. };
}

static void
default_logger(const char *msg)
{
    fprintf(stderr, "%s\n", msg);
}

static void (*python_logging_callback)(const char *msg) = default_logger;

void __visible
set_python_logging_callback(void (*func)(const char *))
{
    python_logging_callback = func;
}

// Log an error message
void
errorf(const char *fmt, ...)
{
    char buf[512];
    va_list args;
    va_start(args, fmt);
    vsnprintf(buf, sizeof(buf), fmt, args);
    va_end(args);
    buf[sizeof(buf)-1] = '\0';
    python_logging_callback(buf);
}

// Report 'errno' in a message written to stderr
void
report_errno(char *where, int rc)
{
    int e = errno;
    errorf("Got error %d in %s: (%d)%s", rc, where, e, strerror(e));
}

// Return a hex character for a given number
#define GETHEX(x) ((x) < 10 ? '0' + (x) : 'a' + (x) - 10)

// Translate a binary string into an ASCII string with escape sequences
char *
dump_string(char *outbuf, int outbuf_size, char *inbuf, int inbuf_size)
{
    char *outend = &outbuf[outbuf_size-5], *o = outbuf;
    uint8_t *inend = (void*)&inbuf[inbuf_size], *p = (void*)inbuf;
    while (p < inend && o < outend) {
        uint8_t c = *p++;
        if (c > 31 && c < 127 && c != '\\') {
            *o++ = c;
            continue;
        }
        *o++ = '\\';
        *o++ = 'x';
        *o++ = GETHEX(c >> 4);
        *o++ = GETHEX(c & 0x0f);
    }
    *o = '\0';
    return outbuf;
}

// Set custom thread names
int __visible
set_thread_name(char name[16])
{
    return prctl(PR_SET_NAME, name);
}

// Set thread affinity to a specific CPU
int
set_thread_affinity(pthread_t thread, int cpu_id)
{
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    CPU_SET(cpu_id, &cpuset);
    return pthread_setaffinity_np(thread, sizeof(cpu_set_t), &cpuset);
}

// Set thread affinity to a list of CPUs
int
set_thread_affinity_list(pthread_t thread, const int *cpu_list, int count)
{
    cpu_set_t cpuset;
    CPU_ZERO(&cpuset);
    for (int i = 0; i < count; i++) {
        CPU_SET(cpu_list[i], &cpuset);
    }
    return pthread_setaffinity_np(thread, sizeof(cpu_set_t), &cpuset);
}

// ─── Statistical Functions (TUXEDO_RT) ─────────────────────────────────

// Calculate mean of an array of doubles
double __visible
stats_mean(const double *data, int32_t count)
{
    if (count <= 0) return 0.0;
    double sum = 0.0;
    for (int32_t i = 0; i < count; i++) {
        sum += data[i];
    }
    return sum / count;
}

// Calculate variance of an array of doubles
double __visible
stats_variance(const double *data, int32_t count)
{
    if (count <= 0) return 0.0;
    double mean = stats_mean(data, count);
    double sum_sq = 0.0;
    for (int32_t i = 0; i < count; i++) {
        double diff = data[i] - mean;
        sum_sq += diff * diff;
    }
    return sum_sq / count;
}

// Calculate standard deviation of an array of doubles
double __visible
stats_stddev(const double *data, int32_t count)
{
    double var = stats_variance(data, count);
    if (var <= 0.0) return 0.0;
    return sqrt(var);
}
