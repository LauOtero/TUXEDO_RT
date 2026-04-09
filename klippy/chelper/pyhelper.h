#ifndef PYHELPER_H
#define PYHELPER_H

#include "compiler.h"
#include <stdarg.h>

double __visible get_monotonic(void);
struct timespec fill_time(double time);
void __visible set_python_logging_callback(void (*func)(const char *));
void errorf(const char *fmt, ...) __attribute__ ((format (printf, 1, 2)));
void report_errno(char *where, int rc);
char *dump_string(char *outbuf, int outbuf_size, char *inbuf, int inbuf_size);
int __visible set_thread_name(char name[16]);

/* Logging macros for RT backends */
#define logging_info(fmt, ...) errorf("INFO: " fmt, ##__VA_ARGS__)
#define logging_error(fmt, ...) errorf("ERROR: " fmt, ##__VA_ARGS__)

/* Thread affinity helpers */
int set_thread_affinity(pthread_t thread, int cpu_id);
int set_thread_affinity_list(pthread_t thread, const int *cpu_list, int count);

#endif // pyhelper.h
