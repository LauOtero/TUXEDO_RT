# Klipper G-Code Optimization User Manual

This manual describes the updated G-Code processing capabilities and performance improvements in the TUXEDO_RT version of Klipper.

## Overview
The G-Code engine has been significantly optimized for higher throughput and lower memory consumption. Key improvements include:
- **Fast-path parameter parsing** for extended commands.
- **Command memoization** for frequent lines (e.g., repeating G1 commands).
- **In-memory parameter caching** within individual command instances.
- **Enhanced execution metrics** for performance monitoring.

## New G-Code Commands

### `GCODE_STATS`
Allows monitoring and management of G-Code execution metrics.

**Usage:**
- `GCODE_STATS ENABLE=1`: Enable execution metrics collection.
- `GCODE_STATS ENABLE=0`: Disable execution metrics collection (default, best performance).
- `GCODE_STATS`: Report current metrics (if enabled).

**Output Example:**
```
G-Code Execution Metrics:
G1             : count=12500 total=0.0354s avg=0.0028ms max=0.1542ms
M105           : count=45    total=0.0001s avg=0.0022ms max=0.0123ms
----------------------------------------
TOTAL: count=12545 time=0.0355s avg=0.0028ms
```

### `CLEAR_GCODE_CACHE`
Clears the internal command parsing cache. This may be useful after changing firmware configuration or for debugging.

**Usage:**
- `CLEAR_GCODE_CACHE`

### `M117 <message>`
Display a message on the printer LCD (simulated via host response).

### `M118 <message>`
Echo a message back to the host interface.

## Performance Considerations
For maximum throughput (e.g., during high-speed printing with small segments), it is recommended to:
1. Keep `GCODE_STATS` disabled (`ENABLE=0`).
2. Avoid excessively long G-Code lines with unnecessary comments.
3. Use traditional G-Code (G0, G1, etc.) for motion commands, as they are processed via the fastest path.

## Memory Usage
The new command cache is limited to 1,000 unique entries to ensure that memory usage remains stable even during very long prints.
Each `GCodeCommand` instance uses `__slots__` to minimize memory overhead per command object.
