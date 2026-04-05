# Klippy Optimization User Manual - TUXEDO_RT

This manual describes the new performance monitoring and diagnostic capabilities introduced in the optimized `klippy.py` core for TUXEDO_RT.

## New G-Code Commands

### REACTOR_STATS
Provides insights into the reactor's performance and event handling latency. This is crucial for diagnosing "Timer too close" errors or general system sluggishness.

*   **Usage**: `REACTOR_STATS [ENABLE=<0|1>]`
*   **Parameters**:
    *   `ENABLE`: Set to `1` to start tracking performance metrics, or `0` to stop. Note: Enabling stats tracking adds a very small overhead to every event dispatch.
*   **Output**:
    *   Total events processed since enabled.
    *   Maximum event duration detected (in milliseconds).

**Example**:
```gcode
REACTOR_STATS ENABLE=1
# Wait for some operations
REACTOR_STATS
```
**Response**:
```
// Reactor Performance Metrics:
// Events: 1250
// Max Event Duration: 0.1245ms
```

### MEMORY_STATS
Reports the current memory usage of the Klippy process and the state of the Python Garbage Collector (GC).

*   **Usage**: `MEMORY_STATS`
*   **Output**:
    *   **GC Counts**: Number of objects in each GC generation.
    *   **GC Thresholds**: Thresholds for triggering automatic GC.
    *   **Last GC Time**: How long ago the last manual/adaptive GC ran.
    *   **Total GC Objects**: Total number of objects currently tracked by the GC.
    *   **RSS Memory**: Resident Set Size (actual physical memory used by the process).
    *   **VMS Memory**: Virtual Memory Size.

**Example**:
```gcode
MEMORY_STATS
```
**Response**:
```
// Memory and GC Metrics:
// GC Counts: (540, 5, 2)
// GC Thresholds: (2000, 10, 10)
// Last GC Time: 15.42s ago
// Total GC Objects: 115420
// RSS Memory: 42.36 MB
// VMS Memory: 32.24 MB
```

## Performance Enhancements

The optimized core includes several "under-the-hood" improvements that require no configuration:

1.  **Parallel Startup**: Klipper now loads configuration sections in parallel, significantly reducing startup time on multi-core host systems.
2.  **Object Lookup Caching**: Repeated lookups for printer objects and extras are now nearly instantaneous (O(1) complexity).
3.  **Adaptive Garbage Collection**: When `REACTOR_STATS` is enabled, the system performantly manages memory by triggering short, non-blocking GC cycles during event idle times, preventing large memory spikes.
4.  **Reduced System Calls**: Improved caching of file existence checks reduces disk I/O during module loading.

## Troubleshooting

If you suspect performance issues:
1.  Enable reactor stats: `REACTOR_STATS ENABLE=1`.
2.  Perform the operation that seems slow.
3.  Check the maximum event duration: `REACTOR_STATS`. If any value exceeds 100ms, a warning will also be logged to `klippy.log`.
4.  Check memory usage: `MEMORY_STATS`. High RSS usage (e.g., >200MB) might indicate a memory leak in a specific extra or plugin.
