# File descriptor and timer event helper
#
# Copyright (C) 2016-2026  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import os, gc, select, math, time, logging, queue, heapq, collections
import greenlet
from typing import Callable, List, Dict, Optional, Tuple, Any, Deque
import chelper, util
from rtcore.rt_core import RealTimeCore
from rtcore.priority_queue import MultiPriorityQueue

# TUXEDO_RT: Constants for performance
_NOW = 0.
_NEVER = 9999999999999999.

# TUXEDO_RT: Fast Timer object for heapq
class ReactorTimer:
    """
    Optimized timer handler for the Reactor.
    Uses __slots__ for minimal memory footprint and supports priority sorting.
    """
    __slots__ = ['callback', 'waketime', 'timer_is_running', 'id', 'priority']
    
    def __init__(self, callback: Callable[[float], float], waketime: float, 
                 timer_id: int, priority: int = 2):
        self.callback = callback
        self.waketime = waketime
        self.timer_is_running = False
        self.id = timer_id
        self.priority = priority # 0: Critical, 1: High, 2: Normal, 3: Low
    
    def __lt__(self, other: 'ReactorTimer') -> bool:
        # Primary sort by waketime
        if self.waketime != other.waketime:
            return self.waketime < other.waketime
        # Secondary sort by priority (lower number = higher priority)
        if self.priority != other.priority:
            return self.priority < other.priority
        # Tertiary sort by insertion order (id) to maintain stability
        return self.id < other.id

class ReactorError(Exception):
    """Exception raised for errors in the Reactor."""
    pass

class ReactorCompletion:
    """Helper for waiting on a result from an asynchronous operation."""
    class sentinel: pass
    
    def __init__(self, reactor: 'SelectReactor'):
        self.reactor = reactor
        self.result: Any = self.sentinel
        self.waiting: List[greenlet.greenlet] = []
    
    def test(self) -> bool:
        """Check if the completion has finished."""
        return self.result is not self.sentinel
    
    def complete(self, result: Any):
        """Mark the completion as finished and resume waiting greenlets."""
        self.result = result
        for wait in self.waiting:
            self.reactor.update_timer(wait.timer, self.reactor.NOW)
    
    def wait(self, waketime: float = _NEVER, waketime_result: Any = None) -> Any:
        """Wait for the completion to finish, or until waketime is reached."""
        if self.result is self.sentinel:
            wait = greenlet.getcurrent()
            self.waiting.append(wait)
            self.reactor.pause(waketime)
            self.waiting.remove(wait)
            if self.result is self.sentinel:
                return waketime_result
        return self.result

class ReactorCallback:
    """Helper for scheduling a callback to run at a specific time."""
    def __init__(self, reactor: 'SelectReactor', callback: Callable[[float], Any], 
                 waketime: float):
        self.reactor = reactor
        self.timer = reactor.register_timer(self.invoke, waketime)
        self.callback = callback
        self.completion = ReactorCompletion(reactor)
        
    def invoke(self, eventtime: float) -> float:
        self.reactor.unregister_timer(self.timer)
        res = self.callback(eventtime)
        self.completion.complete(res)
        return self.reactor.NEVER

class ReactorFileHandler:
    """Handler for file descriptor events."""
    def __init__(self, fd: int, read_callback: Callable[[float], None], 
                 write_callback: Optional[Callable[[float], None]]):
        self.fd = fd
        self.read_callback = read_callback
        self.write_callback = write_callback

class ReactorGreenlet(greenlet.greenlet):
    """Specialized greenlet with a timer reference."""
    def __init__(self, run: Callable[[], None]):
        greenlet.greenlet.__init__(self, run=run)
        self.timer: Optional[ReactorTimer] = None

class ReactorMutex:
    """Cooperative mutex for synchronizing greenlets."""
    def __init__(self, reactor: 'SelectReactor', is_locked: bool):
        self.reactor = reactor
        self.is_locked = is_locked
        self.next_pending = False
        self.queue: List[ReactorGreenlet] = []
        self.lock = self.__enter__
        self.unlock = self.__exit__
        
    def test(self) -> bool:
        return self.is_locked
        
    def __enter__(self):
        if not self.is_locked:
            self.is_locked = True
            return
        g = greenlet.getcurrent()
        self.queue.append(g)
        while 1:
            self.reactor.pause(self.reactor.NEVER)
            if self.next_pending and self.queue[0] is g:
                self.next_pending = False
                self.queue.pop(0)
                return
                
    def __exit__(self, type=None, value=None, tb=None):
        if not self.queue:
            self.is_locked = False
            return
        self.next_pending = True
        self.reactor.update_timer(self.queue[0].timer, self.reactor.NOW)

class ReactorPreventPause:
    """Context manager to temporarily disable reactor pauses."""
    def __init__(self, reactor: 'SelectReactor'):
        self.reactor = reactor
    def __enter__(self):
        self.reactor._prevent_pause_count += 1
    def __exit__(self, type=None, value=None, tb=None):
        self.reactor._prevent_pause_count -= 1

class SelectReactor:
    """
    Main Event Loop and Scheduler.
    Optimized for high-performance and low-latency in real-time environments.
    """
    NOW = _NOW
    NEVER = _NEVER
    
    def __init__(self, gc_checking: bool = False):
        self._process = False
        # TUXEDO_RT: Handle missing C helper gracefully
        ffi_main, ffi_lib = chelper.get_ffi()
        if ffi_lib is not None:
            self.monotonic = ffi_lib.get_monotonic
        else:
            self.monotonic = time.monotonic
            
        # TUXEDO_RT: Real-Time Core Integration
        self.rt_core = RealTimeCore()
        
        # Python garbage collection
        self._gc_checking = gc_checking
        self._last_gc_times = [0., 0., 0.]
        
        # TUXEDO_RT: Optimized Timer Management (Min-Heap)
        self._timers: List[ReactorTimer] = []
        self._timer_counter = 0
        self._next_timer = self.NEVER
        
        # TUXEDO_RT: Latency Monitoring and Watchdog
        self._latency_stats: Deque[float] = collections.deque(maxlen=1000)
        self._max_latency = 0.
        self._watchdog_threshold = 0.050 # 50ms threshold for warnings
        
        # TUXEDO_RT: Advanced Statistics
        self._stats = {
            'timers_processed': 0,
            'io_events_processed': 0,
            'total_callback_duration': 0.0,
            'max_callback_duration': 0.0,
            'greenlet_switches': 0,
            'last_dispatch_time': self.monotonic()
        }
        self._deadlock_threshold = 5.0 # 5s without progress = potential deadlock
        
        # Callbacks
        self._pipe_fds: Optional[Tuple[int, int]] = None
        self._async_queue = queue.Queue()
        
        # File descriptors
        self._dummy_fd_hdl = ReactorFileHandler(-1, (lambda e: None), (lambda e: None))
        self._fds: Dict[int, ReactorFileHandler] = {}
        self._read_fds: List[int] = []
        self._write_fds: List[int] = []
        self._READ = 1
        self._WRITE = 2
        
        # Greenlets
        self._g_dispatch: Optional[ReactorGreenlet] = None
        self._cached_dispatch_greenlets: List[ReactorGreenlet] = []
        self._all_greenlets: List[ReactorGreenlet] = []
        self._prevent_pause_count = 0

    def get_latency_stats(self) -> Tuple[float, float]:
        """Returns average and maximum latency observed."""
        if not self._latency_stats:
            return 0., 0.
        avg_lat = sum(self._latency_stats) / len(self._latency_stats)
        return avg_lat, self._max_latency

    def get_stats(self) -> Dict[str, Any]:
        """Returns a copy of all performance statistics."""
        stats = self._stats.copy()
        avg_lat, max_lat = self.get_latency_stats()
        stats.update({
            'avg_latency': avg_lat,
            'max_latency': max_lat,
            'heap_size': len(self._timers),
            'last_progress_age': self.monotonic() - stats['last_dispatch_time']
        })
        return stats

    def get_gc_stats(self) -> Tuple[float, float, float]:
        """Returns timestamps of the last garbage collections for each generation."""
        return tuple(self._last_gc_times)

    def _check_gc(self, eventtime: float) -> bool:
        if not self._gc_checking:
            return False
        gi = gc.get_count()
        if gi[0] < 700:
            return False
        # Reactor looks idle and gc is due - run it
        gc_level = 0
        if gi[1] >= 10:
            gc_level = 1
            if gi[2] >= 10:
                gc_level = 2
        self._last_gc_times[gc_level] = eventtime
        gc.collect(gc_level)
        return True

    def update_timer(self, timer_handler: ReactorTimer, waketime: float):
        """Update the scheduled wake time of an existing timer."""
        if timer_handler.timer_is_running:
            timer_handler.waketime = waketime
            return
        if waketime != timer_handler.waketime:
            timer_handler.waketime = waketime
            heapq.heappush(self._timers, timer_handler)
        self._next_timer = min(self._next_timer, waketime)

    def register_timer(self, callback: Callable[[float], float], 
                       waketime: float = _NEVER, priority: int = 2) -> ReactorTimer:
        """
        Registers a new timer with the specified priority.
        TUXEDO_RT: Automatically adjusts thread priority if a critical timer (0) is registered.
        """
        if priority == 0:
            self.rt_core.set_realtime_priority(self.rt_core.map_klipper_priority(0))
        
        self._timer_counter += 1
        timer_handler = ReactorTimer(callback, waketime, self._timer_counter, priority)
        heapq.heappush(self._timers, timer_handler)
        if waketime < self._next_timer:
            self._next_timer = waketime
        return timer_handler

    def unregister_timer(self, timer_handler: ReactorTimer):
        """Unregister a timer (Lazy removal, O(1))."""
        timer_handler.waketime = self.NEVER

    def _check_timers(self, eventtime: float, busy: bool) -> float:
        """Check and execute expired timers. Returns the delay until the next event."""
        # Deadlock check: has it been too long since last successful dispatch?
        if eventtime - self._stats['last_dispatch_time'] > self._deadlock_threshold:
            logging.warning("Reactor: Potential deadlock/blocking detected! "
                            "Last progress was %.3fs ago.", 
                            eventtime - self._stats['last_dispatch_time'])
            # Reset time to avoid continuous warnings
            self._stats['last_dispatch_time'] = eventtime

        if eventtime < self._next_timer:
            if busy:
                return 0.
            if self._check_gc(eventtime):
                return 0.
            return min(1., max(.001, self._next_timer - eventtime))
        
        # Mark activity
        self._stats['last_dispatch_time'] = eventtime
        
        # Periodic cleanup of unregistered timers (every 1000 dispatches if heap is large)
        if len(self._timers) > 2000 and self._timer_counter % 1000 == 0:
            self._cleanup_timers()

        self._next_timer = self.NEVER
        g_dispatch = self._g_dispatch
        
        # Process timers in batches to allow I/O to be interleaved
        batch_count = 0
        while self._timers and batch_count < 100:
            t = self._timers[0]
            waketime = t.waketime
            
            if waketime == self.NEVER:
                heapq.heappop(self._timers)
                continue
                
            if eventtime < waketime:
                self._next_timer = waketime
                break
                
            heapq.heappop(self._timers)
            batch_count += 1
            self._stats['timers_processed'] += 1
            
            # Latency tracking
            latency = eventtime - waketime
            if latency > 0.0001:
                self._latency_stats.append(latency)
                if latency > self._max_latency:
                    self._max_latency = latency
            
            t.timer_is_running = True
            start_time = eventtime
            new_waketime = t.callback(eventtime)
            end_time = self.monotonic()
            t.timer_is_running = False
            
            # Record execution time
            duration = end_time - start_time
            self._stats['total_callback_duration'] += duration
            if duration > self._stats['max_callback_duration']:
                self._stats['max_callback_duration'] = duration
            
            # Watchdog: Warn if a callback took too long
            if duration > self._watchdog_threshold:
                logging.warning("Reactor: Timer callback took %.3fs (waketime=%.3f)", 
                                duration, waketime)
            
            if new_waketime != self.NEVER:
                t.waketime = new_waketime
                heapq.heappush(self._timers, t)
                self._next_timer = min(self._next_timer, new_waketime)
            
            if g_dispatch is not self._g_dispatch:
                self._stats['greenlet_switches'] += 1
                if self._timers:
                    self._next_timer = min(self._next_timer, self._timers[0].waketime)
                self._end_greenlet(g_dispatch)
                return 0.
                
        if self._timers and self._next_timer == self.NEVER:
            self._next_timer = self._timers[0].waketime

        return 0.

    def completion(self) -> ReactorCompletion:
        return ReactorCompletion(self)

    def register_callback(self, callback: Callable[[float], Any], 
                          waketime: float = NOW) -> ReactorCompletion:
        rcb = ReactorCallback(self, callback, waketime)
        return rcb.completion

    def register_async_callback(self, callback: Callable[[float], Any], 
                                waketime: float = NOW):
        """Thread-safe registration of a callback."""
        self._async_queue.put_nowait((ReactorCallback, (self, callback, waketime)))
        if self._pipe_fds:
            try:
                os.write(self._pipe_fds[1], b'.')
            except os.error:
                pass

    def async_complete(self, completion: ReactorCompletion, result: Any):
        """Thread-safe completion of an asynchronous operation."""
        self._async_queue.put_nowait((completion.complete, (result,)))
        if self._pipe_fds:
            try:
                os.write(self._pipe_fds[1], b'.')
            except os.error:
                pass

    def _got_pipe_signal(self, eventtime: float):
        if self._pipe_fds:
            try:
                os.read(self._pipe_fds[0], 4096)
            except os.error:
                pass
        while 1:
            try:
                func, args = self._async_queue.get_nowait()
            except queue.Empty:
                break
            func(*args)

    def _setup_async_callbacks(self):
        self._pipe_fds = os.pipe()
        util.set_nonblock(self._pipe_fds[0])
        util.set_nonblock(self._pipe_fds[1])
        self.register_fd(self._pipe_fds[0], self._got_pipe_signal)

    def _sys_pause(self, waketime: float) -> float:
        delay = waketime - self.monotonic()
        if delay > 0.:
            time.sleep(delay)
        return self.monotonic()

    def pause(self, waketime: float) -> float:
        """Pause execution of the current greenlet until waketime."""
        if self._g_dispatch is None:
            return self._sys_pause(waketime)
        if self._prevent_pause_count:
            self.verify_can_pause()
        g = greenlet.getcurrent()
        if g is not self._g_dispatch:
            return self._g_dispatch.switch(waketime)
        g.timer = self.register_timer(g.switch, waketime, priority=0) # High priority for resume
        self._next_timer = self.NOW
        if self._cached_dispatch_greenlets:
            g_next = self._cached_dispatch_greenlets.pop()
            eventtime = g_next.switch()
        else:
            eventtime = g.parent.switch()
        return eventtime

    def _end_greenlet(self, g_old: ReactorGreenlet):
        self.unregister_timer(g_old.timer)
        g_old.timer = None
        self._cached_dispatch_greenlets.append(g_old)
        self._g_dispatch.switch(self.NEVER)
        self._g_dispatch = g_old

    def assert_no_pause(self) -> ReactorPreventPause:
        return ReactorPreventPause(self)

    def verify_can_pause(self):
        if self._prevent_pause_count:
            raise ReactorError("Internal error - reactor pause disabled")

    def mutex(self, is_locked: bool = False) -> ReactorMutex:
        return ReactorMutex(self, is_locked)

    def register_fd(self, fd: int, read_callback: Callable[[float], None], 
                    write_callback: Optional[Callable[[float], None]] = None) -> ReactorFileHandler:
        file_handler = ReactorFileHandler(fd, read_callback, write_callback)
        self._fds[fd] = file_handler
        self.set_fd_wake(file_handler, True, write_callback is not None)
        return file_handler

    def unregister_fd(self, file_handler: ReactorFileHandler):
        self.set_fd_wake(file_handler, False, False)
        del self._fds[file_handler.fd]

    def set_fd_wake(self, file_handler: ReactorFileHandler, 
                    is_readable: bool = True, is_writeable: bool = False):
        fd = file_handler.fd
        if fd in self._read_fds:
            if not is_readable:
                self._read_fds.remove(fd)
        elif is_readable:
            self._read_fds.append(fd)
        if fd in self._write_fds:
            if not is_writeable:
                self._write_fds.remove(fd)
        elif is_writeable:
            self._write_fds.append(fd)

    def _check_fds(self, eventtime: float, hdls: List[Tuple[int, int]]) -> float:
        g_dispatch = self._g_dispatch
        self._stats['io_events_processed'] += len(hdls)
        self._stats['last_dispatch_time'] = eventtime
        for fd, event in hdls:
            hdl = self._fds.get(fd, self._dummy_fd_hdl)
            if event & self._READ:
                hdl.read_callback(eventtime)
                if g_dispatch is not self._g_dispatch:
                    self._end_greenlet(g_dispatch)
                    return self.monotonic()
            if event & self._WRITE:
                hdl.write_callback(eventtime)
                if g_dispatch is not self._g_dispatch:
                    self._end_greenlet(g_dispatch)
                    return self.monotonic()
        return eventtime

    def _dispatch_loop(self):
        busy = True
        read_fds = self._read_fds
        write_fds = self._write_fds
        eventtime = self.monotonic()
        while self._process:
            timeout = self._check_timers(eventtime, busy)
            busy = False
            try:
                res = select.select(read_fds, write_fds, [], timeout)
            except select.error:
                eventtime = self.monotonic()
                continue
            eventtime = self.monotonic()
            if res[0] or res[1]:
                busy = True
                hdls = ([(fd, self._READ) for fd in res[0]]
                        + [(fd, self._WRITE) for fd in res[1]])
                eventtime = self._check_fds(eventtime, hdls)

    def _cleanup_timers(self):
        """Purge all unregistered timers from the heap."""
        self._timers = [t for t in self._timers if t.waketime != self.NEVER]
        heapq.heapify(self._timers)
        if self._timers:
            self._next_timer = self._timers[0].waketime
        else:
            self._next_timer = self.NEVER

    def run(self):
        """Main entry point for the reactor. Runs the event loop."""
        if self._pipe_fds is None:
            self._setup_async_callbacks()
        
        # TUXEDO_RT: Try to set real-time priority for the main loop
        self.rt_core.set_realtime_priority(99)
        
        self._process = True
        self._prevent_pause_count = 0
        try:
            while self._process:
                g_next = ReactorGreenlet(run=self._dispatch_loop)
                self._all_greenlets.append(g_next)
                self._g_dispatch = g_next
                g_next.switch()
        finally:
            self._g_dispatch = None

    def end(self):
        """Signal the reactor to stop after the current iteration."""
        self._process = False

    def finalize(self):
        """Cleanup resources and terminate all greenlets."""
        self._g_dispatch = None
        self._cached_dispatch_greenlets = []
        for g in self._all_greenlets:
            try:
                g.throw()
            except:
                pass
        self._all_greenlets = []
        if self._pipe_fds is not None:
            os.close(self._pipe_fds[0])
            os.close(self._pipe_fds[1])
            self._pipe_fds = None

class PollReactor(SelectReactor):
    """Reactor implementation using select.poll() for better scalability."""
    def __init__(self, gc_checking: bool = False):
        SelectReactor.__init__(self, gc_checking)
        self._poll = select.poll()
        self._READ = select.POLLIN | select.POLLHUP
        self._WRITE = select.POLLOUT

    def register_fd(self, fd: int, read_callback: Callable[[float], None], 
                    write_callback: Optional[Callable[[float], None]] = None) -> ReactorFileHandler:
        file_handler = ReactorFileHandler(fd, read_callback, write_callback)
        self._fds[fd] = file_handler
        self._poll.register(file_handler.fd, select.POLLIN | select.POLLHUP)
        return file_handler

    def unregister_fd(self, file_handler: ReactorFileHandler):
        try:
            self._poll.unregister(file_handler.fd)
        except (KeyError, ValueError):
            pass
        del self._fds[file_handler.fd]

    def set_fd_wake(self, file_handler: ReactorFileHandler, 
                    is_readable: bool = True, is_writeable: bool = False):
        flags = select.POLLHUP
        if is_readable:
            flags |= select.POLLIN
        if is_writeable:
            flags |= select.POLLOUT
        try:
            self._poll.modify(file_handler.fd, flags)
        except (KeyError, ValueError):
            pass

    def _dispatch_loop(self):
        busy = True
        poll_obj = self._poll
        poll_flags = select.POLLIN | select.POLLHUP
        eventtime = self.monotonic()
        while self._process:
            timeout = self._check_timers(eventtime, busy)
            busy = False
            try:
                res = poll_obj.poll(int(math.ceil(timeout * 1000.)))
            except select.error:
                eventtime = self.monotonic()
                continue
            eventtime = self.monotonic()
            if res:
                busy = True
                eventtime = self._check_fds(eventtime, res)

class EPollReactor(SelectReactor):
    """Reactor implementation using select.epoll() for Linux high-performance."""
    def __init__(self, gc_checking: bool = False):
        SelectReactor.__init__(self, gc_checking)
        self._epoll = select.epoll()
        self._READ = select.EPOLLIN | select.EPOLLHUP
        self._WRITE = select.EPOLLOUT

    def register_fd(self, fd: int, read_callback: Callable[[float], None], 
                    write_callback: Optional[Callable[[float], None]] = None) -> ReactorFileHandler:
        file_handler = ReactorFileHandler(fd, read_callback, write_callback)
        self._fds[fd] = file_handler
        self._epoll.register(fd, select.EPOLLIN | select.EPOLLHUP)
        return file_handler

    def unregister_fd(self, file_handler: ReactorFileHandler):
        try:
            self._epoll.unregister(file_handler.fd)
        except (IOError, ValueError):
            pass
        del self._fds[file_handler.fd]

    def set_fd_wake(self, file_handler: ReactorFileHandler, 
                    is_readable: bool = True, is_writeable: bool = False):
        flags = select.EPOLLHUP
        if is_readable:
            flags |= select.EPOLLIN
        if is_writeable:
            flags |= select.EPOLLOUT
        try:
            self._epoll.modify(file_handler.fd, flags)
        except (IOError, ValueError):
            pass

    def _dispatch_loop(self):
        busy = True
        epoll_obj = self._epoll
        eventtime = self.monotonic()
        while self._process:
            timeout = self._check_timers(eventtime, busy)
            busy = False
            try:
                res = epoll_obj.poll(timeout)
            except (IOError, select.error):
                eventtime = self.monotonic()
                continue
            eventtime = self.monotonic()
            if res:
                busy = True
                eventtime = self._check_fds(eventtime, res)

# Determine the best available reactor
if hasattr(select, "epoll"):
    Reactor = EPollReactor
elif hasattr(select, "poll"):
    Reactor = PollReactor
else:
    Reactor = SelectReactor
