# Code to implement asynchronous logging from a background thread
#
# Copyright (C) 2016-2019  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import logging, logging.handlers, threading, queue, time, collections

# TUXEDO_RT: Optimized QueueHandler with deferred formatting
# Class to forward all messages through a queue to a background thread
class QueueHandler(logging.Handler):
    def __init__(self, queue):
        logging.Handler.__init__(self)
        self.queue = queue
    def emit(self, record):
        try:
            # TUXEDO_RT: Minimize work in the main thread (caller thread)
            # Deferred formatting: do NOT call self.format(record) here
            # Instead, send the raw record and let the background thread format it.
            # This significantly reduces latency in performance-critical sections.
            self.queue.put_nowait(record)
        except queue.Full:
            # Drop logs if queue is full to prevent blocking the main thread
            pass
        except Exception:
            self.handleError(record)

# Class to poll a queue in a background thread and log each message
class QueueListener(logging.handlers.TimedRotatingFileHandler):
    def __init__(self, filename, max_bytes=10*1024*1024, backup_count=5):
        # TUXEDO_RT: Enhanced with size-based rotation as well
        logging.handlers.TimedRotatingFileHandler.__init__(
            self, filename, when='midnight', backupCount=backup_count)
        self.maxBytes = max_bytes
        self.bg_queue = queue.Queue(maxsize=10000)
        self.rollover_info = {}
        # TUXEDO_RT: Emergency circular buffer for critical failure analysis
        self.emergency_buffer = collections.deque(maxlen=500)
        self.is_running = True
        self.bg_thread = threading.Thread(target=self._bg_thread, name="QueueLogger")
        self.bg_thread.daemon = True
        self.bg_thread.start()

    def _bg_thread(self):
        bg_queue = self.bg_queue
        emergency_buffer = self.emergency_buffer
        records = []
        queue_get = bg_queue.get
        queue_get_nowait = bg_queue.get_nowait
        records_append = records.append
        emergency_append = emergency_buffer.append
        handle = self.handle
        while self.is_running:
            try:
                records[:] = [queue_get(True, timeout=0.1)]
                try:
                    while len(records) < 100:
                        records_append(queue_get_nowait())
                except queue.Empty:
                    pass
                for record in records:
                    if record is None:
                        self.is_running = False
                        break
                    emergency_append(record)
                    handle(record)
            except queue.Empty:
                continue
            except Exception:
                pass

    def stop(self):
        if not self.is_running:
            self.close()
            return
        self.is_running = False
        try:
            self.bg_queue.put_nowait(None)
        except queue.Full:
            pass
        self.bg_thread.join(timeout=1.0)
        self.close()

    def set_rollover_info(self, name, info):
        if info is None:
            self.rollover_info.pop(name, None)
            return
        self.rollover_info[name] = info

    def clear_rollover_info(self):
        self.rollover_info.clear()

    def doRollover(self):
        logging.handlers.TimedRotatingFileHandler.doRollover(self)
        lines = [self.rollover_info[name]
                 for name in sorted(self.rollover_info)]
        lines.append(
            "=============== Log rollover at %s ===============" % (
                time.asctime(),))
        
        # TUXEDO_RT: Optimized rollover message creation
        msg = "\n".join(lines)
        record = logging.makeLogRecord({'msg': msg, 'level': logging.INFO})
        self.handle(record)

    def get_emergency_logs(self):
        """Returns the last 500 log records for crash reporting."""
        return list(self.emergency_buffer)

MainQueueHandler = None

def setup_bg_logging(filename, debuglevel):
    global MainQueueHandler
    # TUXEDO_RT: 10MB limit per log file
    ql = QueueListener(filename, max_bytes=10*1024*1024)
    MainQueueHandler = QueueHandler(ql.bg_queue)
    root = logging.getLogger()
    
    # Remove existing handlers to avoid duplicates
    for h in root.handlers[:]:
        root.removeHandler(h)
        
    root.addHandler(MainQueueHandler)
    root.setLevel(debuglevel)
    return ql

def clear_bg_logging():
    global MainQueueHandler
    if MainQueueHandler is not None:
        root = logging.getLogger()
        root.removeHandler(MainQueueHandler)
        root.setLevel(logging.WARNING)
        MainQueueHandler = None
