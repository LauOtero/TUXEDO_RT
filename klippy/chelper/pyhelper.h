#ifndef PYHELPER_H
#define PYHELPER_H

#include "compiler.h"

double __visible get_monotonic(void);
struct timespec fill_time(double time);
void __visible set_python_logging_callback(void (*func)(const char *));
void errorf(const char *fmt, ...) __attribute__ ((format (printf, 1, 2)));
void report_errno(char *where, int rc);
char *dump_string(char *outbuf, int outbuf_size, char *inbuf, int inbuf_size);
int __visible set_thread_name(char name[16]);

#endif // pyhelper.h
