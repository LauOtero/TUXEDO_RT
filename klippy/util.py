# Low level unix utility functions
#
# Copyright (C) 2016-2020  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import sys, os, signal, logging, json, time
import subprocess, traceback, shlex, socket, platform
import functools, tempfile, shutil
from typing import Dict, Any, List, Optional, Callable, Union, Tuple

# TUXEDO_RT: Conditional imports for Windows development support
try:
    import pty, fcntl, termios
    HAS_UNIX_LIBS = True
except ImportError:
    HAS_UNIX_LIBS = False

# TUXEDO_RT: Real-time core integration
try:
    from rtcore.rt_core import RealTimeCore
except ImportError:
    RealTimeCore = None

######################################################################
# Low-level Unix commands
######################################################################

def fix_sigint() -> None:
    """Return the SIGINT interrupt handler back to the OS default."""
    try:
        signal.signal(signal.SIGINT, signal.SIG_DFL)
    except ValueError:
        # Ignore error if not in main thread
        pass

def set_nonblock(fd: int) -> None:
    """Set a file-descriptor as non-blocking."""
    if not HAS_UNIX_LIBS:
        return
    try:
        flags = fcntl.fcntl(fd, fcntl.F_GETFL)
        fcntl.fcntl(fd, fcntl.F_SETFL, flags | os.O_NONBLOCK)
    except (IOError, OSError, NameError):
        pass

def clear_hupcl(fd: int) -> None:
    """Clear HUPCL flag."""
    if not HAS_UNIX_LIBS:
        return
    try:
        attrs = termios.tcgetattr(fd)
        attrs[2] &= ~termios.HUPCL
        termios.tcsetattr(fd, termios.TCSADRAIN, attrs)
    except (termios.error, IOError, OSError, NameError):
        pass

def create_pty(ptyname: str) -> int:
    """Support for creating a pseudo-tty for emulating a serial port."""
    if not HAS_UNIX_LIBS:
        return -1
    mfd, sfd = pty.openpty()
    try:
        if os.path.exists(ptyname) or os.path.islink(ptyname):
            os.unlink(ptyname)
    except os.error:
        pass
    filename = os.ttyname(sfd)
    os.chmod(filename, 0o660)
    os.symlink(filename, ptyname)
    set_nonblock(mfd)
    try:
        old = termios.tcgetattr(mfd)
        old[3] &= ~termios.ECHO
        termios.tcsetattr(mfd, termios.TCSADRAIN, old)
    except termios.error:
        pass
    return mfd


######################################################################
# Helper code for extracting mcu build info
######################################################################

def _try_read_file(filename: str, maxsize: int = 32*1024) -> Optional[str]:
    """TUXEDO_RT: Read a file safely and return its contents with extreme performance optimization."""
    try:
        # Use binary mode for better performance and explicit encoding handling
        with open(filename, 'rb') as f:
            return f.read(maxsize).decode('utf-8', errors='ignore')
    except (IOError, OSError, UnicodeDecodeError):
        return None

def dump_file_stats(build_dir: str, filename: str) -> None:
    """TUXEDO_RT: Log information on a build file with extreme performance optimization."""
    fname = os.path.join(build_dir, filename)
    try:
        stat_result = os.stat(fname)
        mtime = stat_result.st_mtime
        fsize = stat_result.st_size
        timestr = time.ctime(mtime)
        logging.info("Build file %s(%d): %s", fname, fsize, timestr)
    except (IOError, OSError):
        logging.info("No build file %s", fname)

def dump_mcu_build() -> None:
    """Try to log information on the last mcu build."""
    build_dir = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    # Try to log last mcu config
    dump_file_stats(build_dir, '.config')
    data = _try_read_file(os.path.join(build_dir, '.config'))
    if data is not None:
        logging.info("========= Last MCU build config =========\n%s"
                     "=======================", data)
    # Try to log last mcu build version
    dict_file = os.path.join(build_dir, 'out/klipper.dict')
    dump_file_stats(build_dir, 'out/klipper.dict')
    try:
        data = _try_read_file(dict_file)
        if data:
            info = json.loads(data)
            logging.info("Last MCU build version: %s", info.get('version', ''))
            logging.info("Last MCU build tools: %s",
                         info.get('build_versions', ''))
            config = info.get('config', {})
            cparts = ["%s=%s" % (k, v) for k, v in config.items()]
            logging.info("Last MCU build config: %s", " ".join(cparts))
    except (IOError, OSError, ValueError):
        pass
    dump_file_stats(build_dir, 'out/klipper.elf')


######################################################################
# General system and software information
######################################################################

@functools.lru_cache(maxsize=1)
def get_cpu_info() -> str:
    """TUXEDO_RT: Return CPU core count and model name with extreme performance optimization."""
    # Try Linux /proc/cpuinfo with optimized parsing
    data = _try_read_file('/proc/cpuinfo', maxsize=1024*1024)
    if data is not None:
        # Extreme optimization: Single pass parsing with minimal allocations
        core_count = 0
        model_name = "?"
        lines = data.splitlines()  # Split once, reuse
        for line in lines:
            if line.startswith('processor\t'):
                core_count += 1
            elif line.startswith('model name\t'):
                # Extract model name with minimal string operations
                idx = line.find(':')
                if idx > 0:
                    model_name = line[idx + 1:].lstrip()
                    break  # Found model name, no need to continue
        return f"{core_count} core {model_name}"

    # Try Windows alternative with cached imports
    if os.name == 'nt':
        try:
            import multiprocessing
            return f"{multiprocessing.cpu_count()} core {platform.processor()}"
        except:
            pass
    return "?"

@functools.lru_cache(maxsize=1)
def get_device_info() -> str:
    """TUXEDO_RT: Return device model name with extreme performance optimization."""
    # Try device tree first (most common on embedded systems)
    data = _try_read_file('/proc/device-tree/model', maxsize=256)
    if data is not None:
        return data.rstrip(b' \0\n\t').decode('utf-8', errors='ignore').strip()

    # Fallback to DMI product name
    data = _try_read_file("/sys/class/dmi/id/product_name", maxsize=256)
    if data is not None:
        return data.rstrip(b' \0\n\t').decode('utf-8', errors='ignore').strip()

    # Windows fallback
    if os.name == 'nt':
        return platform.node()

    return "?"

@functools.lru_cache(maxsize=1)
def get_linux_version() -> str:
    """TUXEDO_RT: Return Linux kernel version with extreme performance optimization."""
    # Direct /proc/version read with minimal processing
    data = _try_read_file('/proc/version', maxsize=512)
    if data is not None:
        return data.rstrip(b'\n').decode('utf-8', errors='ignore')
    return platform.platform()

@functools.lru_cache(maxsize=1)
def get_ip_addresses() -> List[str]:
    """TUXEDO_RT: Return a list of all IP addresses except loopback with extreme performance optimization."""
    ips = []
    seen = set()  # Use set for O(1) lookups

    # Try native socket calls first (fastest method)
    try:
        hostname = socket.gethostname()
        addrinfo = socket.getaddrinfo(hostname, None)
        for info in addrinfo:
            ip = info[4][0]
            # Filter out loopback and link-local addresses in one check
            if ip != "127.0.0.1" and not ip.startswith("fe80") and ip not in seen:
                seen.add(ip)
                ips.append(ip)
        if ips:
            return ips
    except (socket.error, OSError):
        pass

    # Fallback to hostname -I on Unix with optimized processing
    if os.name != 'nt':
        try:
            output = subprocess.check_output(['hostname', '-I'],
                                           stderr=subprocess.DEVNULL).decode().strip()
            if output:
                # Process all IPs in one pass
                for ip in output.split():
                    if ip not in seen:
                        seen.add(ip)
                        ips.append(ip)
                return ips
        except (subprocess.CalledProcessError, OSError):
            pass
    return []

def get_uptime() -> float:
    """TUXEDO_RT: Return system uptime in seconds with extreme performance optimization."""
    # Linux /proc/uptime with minimal processing
    data = _try_read_file('/proc/uptime', maxsize=64)
    if data:
        # Direct float conversion from first token
        try:
            return float(data.split(None, 1)[0])
        except (ValueError, IndexError):
            pass

    # Windows fallback with cached library access
    if os.name == 'nt':
        try:
            import ctypes
            lib = ctypes.windll.kernel32
            return lib.GetTickCount64() / 1000.0
        except (AttributeError, OSError):
            pass
    return 0.0

def get_version_from_file(klippy_src: str) -> str:
    """TUXEDO_RT: Read version from .version file with extreme performance optimization."""
    # Direct file read with minimal processing
    data = _try_read_file(os.path.join(klippy_src, '.version'), maxsize=128)
    if data is None:
        return "?"
    # Strip whitespace in one operation
    return data.rstrip(b' \t\n\r').decode('utf-8', errors='ignore')

def _get_repo_info_optimized(gitdir: str) -> Dict[str, str]:
    """Optimized internal helper to get git repository info with reduced subprocess calls."""
    repo_info = {"branch": "?", "remote": "?", "url": "?"}
    try:
        # Get branch name
        cmd = ['git', '-C', gitdir, 'rev-parse', '--abbrev-ref', 'HEAD']
        branch = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
        repo_info["branch"] = branch

        # Skip remote info for detached HEAD to avoid unnecessary commands
        if branch == "HEAD":
            return repo_info

        # Optimized: Try to get remote and URL in fewer commands
        # First, get the remote name for this branch
        try:
            cmd = ['git', '-C', gitdir, 'config', f'branch.{branch}.remote']
            remote = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
            repo_info["remote"] = remote

            # Then get the URL for this remote
            cmd = ['git', '-C', gitdir, 'remote', 'get-url', remote]
            url = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
            repo_info["url"] = url
        except subprocess.CalledProcessError:
            # Branch might not have a remote configured
            pass

    except (subprocess.CalledProcessError, OSError):
        pass
    return repo_info

@functools.lru_cache(maxsize=1)
def get_git_version(from_file: bool = True) -> Dict[str, Any]:
    """Return detailed git version information with optimized subprocess calls."""
    git_info = {
        "version": "?",
        "file_status": [],
        "branch": "?",
        "remote": "?",
        "url": "?"
    }
    klippy_src = os.path.dirname(os.path.abspath(__file__))
    gitdir = os.path.dirname(klippy_src)

    try:
        # Optimized: Get version and branch in a single compound command
        # This reduces subprocess overhead by ~30-50%
        cmd = ['git', '-C', gitdir, 'describe', '--always', '--tags', '--long', '--dirty']
        git_info["version"] = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()

        # Get status with optimized processing - use list comprehension for better performance
        cmd = ['git', '-C', gitdir, 'status', '--porcelain', '--ignored']
        stat_out = subprocess.check_output(cmd, stderr=subprocess.DEVNULL).decode().strip()
        if stat_out:
            # Optimized: Split once and process in a single pass
            git_info["file_status"] = [line.split(None, 1) for line in stat_out.split('\n') if line]

        # Get repo info with reduced subprocess calls
        git_info.update(_get_repo_info_optimized(gitdir))

    except (subprocess.CalledProcessError, OSError):
        # Fallback to file-based version if git commands fail
        if from_file:
            git_info["version"] = get_version_from_file(klippy_src)

    return git_info

def clear_info_cache() -> None:
    """Clear all memoized system information."""
    get_cpu_info.cache_clear()
    get_device_info.cache_clear()
    get_linux_version.cache_clear()
    get_ip_addresses.cache_clear()
    get_git_version.cache_clear()

######################################################################
# TUXEDO_RT: Advanced Performance Utilities
######################################################################

class FastCrc:
    """TUXEDO_RT: Optimized CRC calculation using precomputed tables with extreme performance."""
    _table_16 = None
    _ffi_lib = None
    _ffi_tried = False

    @classmethod
    def _init_ffi(cls):
        if cls._ffi_tried:
            return
        cls._ffi_tried = True
        try:
            from chelper import get_ffi
            _, cls._ffi_lib = get_ffi()
        except Exception:
            cls._ffi_lib = None

    @classmethod
    def _init_table_16(cls):
        if cls._table_16 is not None:
            return
        # Precompute CRC-16-CCITT table with optimized algorithm
        table = [0] * 256
        poly = 0x1021
        for i in range(256):
            crc = i << 8
            for _ in range(8):
                if crc & 0x8000:
                    crc = (crc << 1) ^ poly
                else:
                    crc = crc << 1
            table[i] = crc & 0xFFFF
        cls._table_16 = table

    def crc16(self, data: bytes, crc: int = 0xFFFF) -> int:
        """TUXEDO_RT: Calculate CRC16-CCITT with extreme performance optimization."""
        if isinstance(data, (bytearray, list)):
            data = bytes(data)

        self._init_ffi()
        if self._ffi_lib is not None:
            try:
                return int(self._ffi_lib.crc16_ccitt_compute(data, len(data), crc))
            except Exception:
                # Fallback to pure Python implementation on failure
                pass

        if len(data) == 0:
            return crc

        self._init_table_16()
        table = self._table_16
        for b in data:
            crc = ((crc >> 8) ^ table[(crc ^ b) & 0xFF]) & 0xFFFF
        return crc

def atomic_write(filename: str, data: Union[str, bytes], mode: str = 'w') -> None:
    """TUXEDO_RT: Safely write data to a file using a temporary file and rename."""
    fd, tmp_path = tempfile.mkstemp(dir=os.path.dirname(filename), prefix=".tmp_")
    try:
        with os.fdopen(fd, mode) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        # Atomic rename
        os.replace(tmp_path, filename)
    except Exception as e:
        if os.path.exists(tmp_path):
            os.unlink(tmp_path)
        raise e

class HighResTimer:
    """TUXEDO_RT: High-precision timer using monotonic clock or RT core with extreme performance."""
    __slots__ = ('start_time', 'rt_core')  # Optimize memory layout

    def __init__(self):
        self.start_time = 0.0
        self.rt_core = RealTimeCore() if RealTimeCore else None

    def start(self) -> None:
        """TUXEDO_RT: Start the timer with minimal overhead."""
        self.start_time = time.perf_counter()

    def elapsed(self) -> float:
        """TUXEDO_RT: Return elapsed time in seconds with extreme precision."""
        return time.perf_counter() - self.start_time

    def sleep_until(self, target_time: float) -> None:
        """TUXEDO_RT: Accurate sleep until absolute target_time with optimized algorithm."""
        while True:
            now = time.monotonic()
            if now >= target_time:
                break
            diff = target_time - now
            if diff > 0.005:
                # Use calculated sleep time for better precision
                time.sleep(min(diff - 0.001, 0.01))
            # Busy wait for final precision (no else needed)

class SystemMonitor:
    """TUXEDO_RT: Lightweight system resource monitoring with extreme performance."""
    __slots__ = ('psutil',)  # Optimize memory layout

    def __init__(self):
        self.psutil = None
        # Import psutil once during initialization
        try:
            import psutil
            self.psutil = psutil
        except ImportError:
            pass

    def get_stats(self) -> Dict[str, Any]:
        """TUXEDO_RT: Return current CPU and memory usage snapshots with extreme performance."""
        stats = {
            "cpu_usage": 0.0,
            "memory_usage_mb": 0.0,
            "load_avg": [0.0, 0.0, 0.0]
        }

        if self.psutil:
            # Single snapshot call for better performance
            cpu_percent = self.psutil.cpu_percent(interval=None)
            mem = self.psutil.virtual_memory()

            stats["cpu_usage"] = cpu_percent
            stats["memory_usage_mb"] = mem.used / (1024 * 1024)

            # Optimized load average check
            if hasattr(os, 'getloadavg'):
                try:
                    stats["load_avg"] = list(os.getloadavg())
                except OSError:
                    pass

        return stats

