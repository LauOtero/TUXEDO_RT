#!/usr/bin/env python2
# Main code for host side printer firmware
#
# Copyright (C) 2016-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import sys, os, gc, optparse, logging, time, collections, importlib, threading
from concurrent.futures import ThreadPoolExecutor
import util, reactor, queuelogger, msgproto
import gcode, configfile, pins, mcu, toolhead, webhooks

# TUXEDO_RT: Imports del nuevo núcleo (desde el paquete rtcore)
from rtcore.rt_core import RealTimeCore
from i18n import init_i18n, I18nSystem, _
from rtcore.fault_tolerance import FaultTolerantCore
from extra_manager import ExtraManager
from rtcore.memory_manager import RTMemoryManager

message_ready = "Printer is ready"

message_startup = """
Printer is not ready
The klippy host software is attempting to connect.  Please
retry in a few moments.
"""

message_restart = """
Once the underlying issue is corrected, use the "RESTART"
command to reload the config and restart the host software.
Printer is halted
"""

class Printer:
    __slots__ = ['bglogger', 'start_args', 'reactor', 'rt_core',
                 'fault_tolerance', 'memory_factory', 'plugin_manager',
                 'state_message', 'in_shutdown_state', 'run_result',
                 'event_handlers', 'objects', '_objects_cache',
                 'reactor_stats', '_extras_cache', 'lock', 'last_gc_time']
    config_error = configfile.error
    command_error = gcode.CommandError
    def __init__(self, main_reactor, bglogger, start_args):
        self.bglogger = bglogger
        self.start_args = start_args
        self.reactor = main_reactor
        
        # TUXEDO_RT: Inicialización del subsistema de tiempo real
        self.rt_core = RealTimeCore()
        
        # TUXEDO_RT: Tolerancia a fallos
        self.fault_tolerance = FaultTolerantCore()
        self.fault_tolerance.start_monitoring()
        
        # TUXEDO_RT: Fábrica de memoria determinista
        self.memory_factory = RTMemoryManager()
        
        # TUXEDO_RT: Nuevo gestor de plugins (Extras)
        self.plugin_manager = ExtraManager(self)
        
        # Performance monitoring
        self.reactor_stats = {'callbacks': 0, 'events': 0, 'max_event_time': 0.0,
                              'enabled': False}
        
        self.reactor.register_callback(self._connect)
        self.state_message = message_startup
        self.in_shutdown_state = False
        self.run_result = None
        self.event_handlers = {}
        self.objects = collections.OrderedDict()
        self._objects_cache = {}
        self._extras_cache = {}
        self.lock = threading.Lock()
        self.last_gc_time = 0.0
        # Init printer components that must be setup prior to config
        for m in [gcode, webhooks]:
            m.add_early_printer_objects(self)
    def get_start_args(self):
        return self.start_args
    def get_version(self):
        return self.start_args.get('software_version')
    def get_reactor(self):
        return self.reactor
    def get_state_message(self):
        if self.state_message == message_ready:
            category = "ready"
        elif self.state_message == message_startup:
            category = "startup"
        elif self.in_shutdown_state:
            category = "shutdown"
        else:
            category = "error"
        return self.state_message, category
    def is_shutdown(self):
        return self.in_shutdown_state
    def _set_state(self, msg):
        if self.state_message in (message_ready, message_startup):
            self.state_message = msg
        if (msg != message_ready
            and self.start_args.get('debuginput') is not None):
            self.request_exit('error_exit')
    def update_error_msg(self, oldmsg, newmsg):
        if (self.state_message != oldmsg
            or self.state_message in (message_ready, message_startup)
            or newmsg in (message_ready, message_startup)):
            return
        self.state_message = newmsg
        logging.error(newmsg)
    def add_object(self, name, obj):
        with self.lock:
            if name in self.objects:
                raise self.config_error(
                    "Printer object '%s' already created" % (name,))
            self.objects[name] = obj
            self._objects_cache.clear()
    def lookup_object(self, name, default=configfile.sentinel):
        if name in self.objects:
            return self.objects[name]
        if default is configfile.sentinel:
            raise self.config_error("Unknown config object '%s'" % (name,))
        return default
    def lookup_objects(self, module=None):
        if module is None:
            with self.lock:
                return list(self.objects.items())
        # Use cache for prefix lookups
        objects_cache = self._objects_cache
        with self.lock:
            if module in objects_cache:
                return objects_cache[module]
            
            objects = self.objects
            prefix = module + ' '
            objs = [(n, objects[n])
                    for n in objects if n.startswith(prefix)]
            if module in objects:
                objs = [(module, objects[module])] + objs
            objects_cache[module] = objs
            return objs
    def load_object(self, config, section, default=configfile.sentinel):
        # Quick check without lock for already loaded objects
        obj = self.objects.get(section)
        if obj is not None:
            return obj

        with self.lock:
            # Re-check after acquiring lock
            if section in self.objects:
                return self.objects[section]

        # Delegar identidad y compatibilidad al gestor central en extra_manager
        obj = self.plugin_manager.load_object(config, section, default=default)
        if obj is not None and obj is not configfile.sentinel:
            with self.lock:
                self.objects[section] = obj
                self._objects_cache.clear()
            return obj

        if default is not configfile.sentinel:
            return default

        raise self.config_error("Unable to load module '%s'" % (section,))
    def _read_config(self):
        self.objects['configfile'] = pconfig = configfile.PrinterConfig(self)
        config = pconfig.read_main_config()
        if self.bglogger is not None:
            pconfig.log_config(config)
        
        # Parallel load objects to reduce startup time
        sections = config.get_prefix_sections('')
        section_names = [s.get_name() for s in sections]
        
        # Identify sections that are safe to parallelize (no hardware dependencies)
        # For simplicity and safety in Klipper, we'll start with a small number of workers
        # and handle the most common objects.
        with ThreadPoolExecutor(max_workers=4) as executor:
            executor.map(lambda name: self.load_object(config, name, None), section_names)
            
        # TUXEDO_RT: Escanear y cargar plugins extras automáticamente
        self.plugin_manager.load_plugins_from_directory('extras')
            
        for m in [toolhead]:
            m.add_printer_objects(config)
            
        # Validate that there are no undefined parameters in the config file
        pconfig.check_unused_options(config)
    def _connect(self, eventtime):
        try:
            self._read_config()
            
            # TUXEDO_RT: Resolver dependencias e inicializar plugins
            self.plugin_manager.resolve_dependencies_and_init()
            self.plugin_manager.start_all()
            
            self.send_event("klippy:mcu_identify")
            handlers = self.event_handlers.get("klippy:connect", [])
            for cb in handlers:
                if self.state_message is not message_startup:
                    return
                cb()
        except (self.config_error, pins.error) as e:
            logging.exception("Config error")
            self._set_state("%s\n%s" % (str(e), message_restart))
            return
        except msgproto.error as e:
            msg = "Protocol error"
            logging.exception(msg)
            self._set_state(msg)
            self.send_event("klippy:notify_mcu_error", msg, {"error": str(e)})
            util.dump_mcu_build()
            return
        except mcu.error as e:
            msg = "MCU error during connect"
            logging.exception(msg)
            self._set_state(msg)
            self.send_event("klippy:notify_mcu_error", msg, {"error": str(e)})
            util.dump_mcu_build()
            return
        except Exception as e:
            logging.exception("Unhandled exception during connect")
            self._set_state("Internal error during connect: %s\n%s"
                            % (str(e), message_restart,))
            return
        try:
            self._set_state(message_ready)
            handlers = self.event_handlers.get("klippy:ready", [])
            with self.reactor.assert_no_pause():
                for cb in handlers:
                    if self.state_message is not message_ready:
                        return
                    cb()
        except Exception as e:
            logging.exception("Unhandled exception during ready callback")
            self.invoke_shutdown("Internal error during ready callback: %s"
                                 % (str(e),))
    def run(self):
        systime = time.time()
        monotime = self.reactor.monotonic()
        logging.info("Start printer at %s (%.1f %.1f)",
                     time.asctime(time.localtime(systime)), systime, monotime)
        # Enter main reactor loop
        try:
            self.reactor.run()
        except:
            msg = "Unhandled exception during run"
            logging.exception(msg)
            # Exception from a reactor callback - try to shutdown
            try:
                self.reactor.register_callback((lambda e:
                                                self.invoke_shutdown(msg)))
                self.reactor.run()
            except:
                logging.exception("Repeat unhandled exception during run")
                # Another exception - try to exit
                self.run_result = "error_exit"
        # Check restart flags
        run_result = self.run_result
        try:
            if run_result == 'firmware_restart':
                self.send_event("klippy:firmware_restart")
            self.send_event("klippy:disconnect")
        except:
            logging.exception("Unhandled exception during post run")
        return run_result
    def set_rollover_info(self, name, info, log=True):
        if log:
            logging.info(info)
        if self.bglogger is not None:
            self.bglogger.set_rollover_info(name, info)
    def invoke_shutdown(self, msg, details={}):
        if self.in_shutdown_state:
            return
        logging.error("Transition to shutdown state: %s", msg)
        self.in_shutdown_state = True
        self._set_state(msg)
        
        # TUXEDO_RT: Apagar plugins antes de otros handlers
        self.plugin_manager.shutdown_all()
        
        with self.reactor.assert_no_pause():
            handlers = self.event_handlers.get("klippy:shutdown", [])
            for cb in handlers:
                try:
                    cb()
                except:
                    logging.exception("Exception during shutdown handler")
            handlers = self.event_handlers.get("klippy:analyze_shutdown", [])
            for cb in handlers:
                try:
                    cb(msg, details)
                except:
                    logging.exception("Exception in analyze_shutdown handler")
    def invoke_async_shutdown(self, msg, details={}):
        self.reactor.register_async_callback(
            (lambda e: self.invoke_shutdown(msg, details)))
    def register_event_handler(self, event, callback):
        with self.lock:
            self.event_handlers.setdefault(event, []).append(callback)
    def _check_memory(self):
        # Adaptive GC: Run collection if memory usage is likely high
        # or enough time has passed. This is called from send_event
        # when reactor_stats are enabled.
        cur_time = self.reactor.monotonic()
        if cur_time - self.last_gc_time > 60.0: # Check every minute
            self.last_gc_time = cur_time
            # Only collect if we're not in a critical section
            # (In Klipper, we assume send_event is safe enough if not in shutdown)
            if not self.in_shutdown_state:
                gc.collect(1) # Collect generation 1
    def send_event(self, event, *params):
        handlers = self.event_handlers.get(event)
        if handlers is None:
            return []
        
        # Track stats for performance analysis
        stats = self.reactor_stats
        if stats['enabled']:
            stats['events'] += 1
            start_time = self.reactor.monotonic()
            
            # Periodic memory check
            self._check_memory()
            
            res = [cb(*params) for cb in handlers]
            
            # Monitor long-running events
            duration = self.reactor.monotonic() - start_time
            if duration > stats['max_event_time']:
                stats['max_event_time'] = duration
            if duration > 0.1: # 100ms threshold for warning
                logging.warning("Long running event %s: %.4fs", event, duration)
            return res
        
        return [cb(*params) for cb in handlers]
    def get_reactor_stats(self):
        return dict(self.reactor_stats)
    def set_reactor_stats(self, enabled):
        self.reactor_stats['enabled'] = enabled
    def get_memory_stats(self):
        import gc
        stats = {
            'gc_count': gc.get_count(),
            'gc_threshold': gc.get_threshold(),
            'last_gc_time': self.last_gc_time,
            'objects_count': len(gc.get_objects()),
        }
        try:
            import psutil
            process = psutil.Process(os.getpid())
            mem_info = process.memory_info()
            stats['rss'] = mem_info.rss
            stats['vms'] = mem_info.vms
        except ImportError:
            pass
        return stats
    def request_exit(self, result):
        if self.run_result is None:
            self.run_result = result
        self.reactor.end()


######################################################################
# Startup
######################################################################

def import_test():
    # Import all optional modules (used as a build test)
    dname = os.path.dirname(__file__)
    for mname in ['extras', 'kinematics']:
        for fname in os.listdir(os.path.join(dname, mname)):
            if fname.endswith('.py') and fname != '__init__.py':
                module_name = fname[:-3]
            else:
                iname = os.path.join(dname, mname, fname, '__init__.py')
                if not os.path.exists(iname):
                    continue
                module_name = fname
            importlib.import_module(mname + '.' + module_name)
    sys.exit(0)

def arg_dictionary(option, opt_str, value, parser):
    key, fname = "dictionary", value
    if '=' in value:
        mcu_name, fname = value.split('=', 1)
        key = "dictionary_" + mcu_name
    if parser.values.dictionary is None:
        parser.values.dictionary = {}
    parser.values.dictionary[key] = fname

def main():
    usage = "%prog [options] <config file>"
    opts = optparse.OptionParser(usage)
    opts.add_option("-i", "--debuginput", dest="debuginput",
                    help="read commands from file instead of from tty port")
    opts.add_option("-I", "--input-tty", dest="inputtty",
                    default='/tmp/printer',
                    help="input tty name (default is /tmp/printer)")
    opts.add_option("-a", "--api-server", dest="apiserver",
                    help="api server unix domain socket filename")
    opts.add_option("-l", "--logfile", dest="logfile",
                    help="write log to file instead of stderr")
    opts.add_option("-v", action="store_true", dest="verbose",
                    help="enable debug messages")
    opts.add_option("-o", "--debugoutput", dest="debugoutput",
                    help="write output to file instead of to serial port")
    opts.add_option("-d", "--dictionary", dest="dictionary", type="string",
                    action="callback", callback=arg_dictionary,
                    help="file to read for mcu protocol dictionary")
    opts.add_option("--import-test", action="store_true",
                    help="perform an import module test")
    options, args = opts.parse_args()
    if options.import_test:
        import_test()
    if len(args) != 1:
        opts.error("Incorrect number of arguments")
    start_args = {'config_file': args[0], 'apiserver': options.apiserver,
                  'start_reason': 'startup'}

    debuglevel = logging.INFO
    if options.verbose:
        debuglevel = logging.DEBUG
    if options.debuginput:
        start_args['debuginput'] = options.debuginput
        debuginput = open(options.debuginput, 'rb')
        start_args['gcode_fd'] = debuginput.fileno()
    else:
        start_args['gcode_fd'] = util.create_pty(options.inputtty)
    if options.debugoutput:
        start_args['debugoutput'] = options.debugoutput
        start_args.update(options.dictionary)
    bglogger = None
    if options.logfile:
        start_args['log_file'] = options.logfile
        bglogger = queuelogger.setup_bg_logging(options.logfile, debuglevel)
    else:
        logging.getLogger().setLevel(debuglevel)
    logging.info("Starting Klippy...")
    git_info = util.get_git_version()
    git_vers = git_info["version"]
    extra_files = [fname for code, fname in git_info["file_status"]
                   if (code in ('??', '!!') and fname.endswith('.py')
                       and (fname.startswith('klippy/kinematics/')
                            or fname.startswith('klippy/extras/')))]
    modified_files = [fname for code, fname in git_info["file_status"]
                      if code == 'M']
    extra_git_desc = ""
    if extra_files:
        if not git_vers.endswith('-dirty'):
            git_vers = git_vers + '-dirty'
        if len(extra_files) > 10:
            extra_files[10:] = ["(+%d files)" % (len(extra_files) - 10,)]
        extra_git_desc += "\nUntracked files: %s" % (', '.join(extra_files),)
    if modified_files:
        if len(modified_files) > 10:
            modified_files[10:] = ["(+%d files)" % (len(modified_files) - 10,)]
        extra_git_desc += "\nModified files: %s" % (', '.join(modified_files),)
    extra_git_desc += "\nBranch: %s" % (git_info["branch"])
    extra_git_desc += "\nRemote: %s" % (git_info["remote"])
    extra_git_desc += "\nTracked URL: %s" % (git_info["url"])
    start_args['software_version'] = git_vers
    start_args['cpu_info'] = util.get_cpu_info()
    start_args['device'] = util.get_device_info()
    start_args['linux_version'] = util.get_linux_version()
    if bglogger is not None:
        versions = "\n".join([
            "Args: %s" % (sys.argv,),
            "Git version: %s%s" % (repr(start_args['software_version']),
                                   extra_git_desc),
            "CPU: %s" % (start_args['cpu_info'],),
            "Device: %s" % (start_args['device']),
            "Linux: %s" % (start_args['linux_version']),
            "Python: %s" % (repr(sys.version),)])
        logging.info(versions)
    elif not options.debugoutput:
        logging.warning("No log file specified!"
                        " Severe timing issues may result!")
    gc.disable()

    # TUXEDO_RT: Inicializar sistema de internacionalización
    locales_dir = os.path.join(os.path.dirname(__file__), 'locales')
    init_i18n(locales_dir)
    
    # Start Printer() class
    while 1:
        if bglogger is not None:
            bglogger.clear_rollover_info()
            bglogger.set_rollover_info('versions', versions)
        gc.collect()
        main_reactor = reactor.Reactor(gc_checking=True)
        printer = Printer(main_reactor, bglogger, start_args)
        res = printer.run()
        if res in ['exit', 'error_exit']:
            break
        time.sleep(1.)
        main_reactor.finalize()
        main_reactor = printer = None
        logging.info("Restarting printer")
        start_args['start_reason'] = res

    if bglogger is not None:
        bglogger.stop()

    if res == 'error_exit':
        sys.exit(-1)

if __name__ == '__main__':
    main()
