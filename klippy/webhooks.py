# Klippy WebHooks registration and server connection
#
# Copyright (C) 2020 Eric Callahan <arksine.code@gmail.com>
#
# This file may be distributed under the terms of the GNU GPLv3 license
import logging, socket, os, sys, errno, collections, zlib
import gcode, util

# TUXEDO_RT: Real-time core integration
try:
    from rtcore.rt_core import RealTimeCore
except ImportError:
    RealTimeCore = None

try:
    import msgspec
    HAS_MSGSPEC = True
except ImportError:
    import json
    HAS_MSGSPEC = False

    # Json decodes strings as unicode types in Python 2.x.  This doesn't
    # play well with some parts of Klipper (particularly displays), so we
    # need to create an object hook. This solution borrowed from:
    #
    # https://stackoverflow.com/questions/956867/
    #
    json_loads_byteify = None
    if sys.version_info.major < 3:
        def json_loads_byteify(data, ignore_dicts=False):
            if isinstance(data, unicode):
                return data.encode('utf-8')
            if isinstance(data, list):
                return [json_loads_byteify(i, True) for i in data]
            if isinstance(data, dict) and not ignore_dicts:
                return {json_loads_byteify(k, True): json_loads_byteify(v, True)
                        for k, v in data.items()}
            return data
    def json_dumps(obj):
        return json.dumps(obj, separators=(',', ':')).encode()
    def json_loads(data):
        return json.loads(data, object_hook=json_loads_byteify)
else:
    json_dumps = msgspec.json.encode
    json_loads = msgspec.json.decode

REQUEST_LOG_SIZE = 20

class WebRequestError(gcode.CommandError):
    def __init__(self, message,):
        Exception.__init__(self, message)

    def to_dict(self):
        return {
            'error': 'WebRequestError',
            'message': str(self)}

class Sentinel:
    pass

class WebRequest:
    """
    TUXEDO_RT: Optimized WebRequest class with __slots__ for memory efficiency.
    """
    __slots__ = ('client_conn', 'id', 'method', 'params', 'response', 'is_error')
    error = WebRequestError
    
    def __init__(self, client_conn, request):
        self.client_conn = client_conn
        base_request = json_loads(request)
        if not isinstance(base_request, dict):
            raise ValueError("Not a top-level dictionary")
        self.id = base_request.get('id')
        self.method = base_request.get('method')
        self.params = base_request.get('params', {})
        if not isinstance(self.method, str) or not isinstance(self.params, dict):
            raise ValueError("Invalid request type")
        self.response = None
        self.is_error = False

    def get_client_connection(self):
        return self.client_conn

    def get(self, item, default=Sentinel, types=None):
        value = self.params.get(item, default)
        if value is Sentinel:
            raise WebRequestError("Missing Argument [%s]" % (item,))
        if (types is not None and not isinstance(value, types)
            and item in self.params):
            raise WebRequestError("Invalid Argument Type [%s]" % (item,))
        return value

    def get_str(self, item, default=Sentinel):
        return self.get(item, default, types=(str,))

    def get_int(self, item, default=Sentinel):
        return self.get(item, default, types=(int,))

    def get_float(self, item, default=Sentinel):
        val = self.params.get(item, default)
        if val is Sentinel:
            raise WebRequestError("Missing Argument [%s]" % (item,))
        if isinstance(val, (int, float)):
            return float(val)
        raise WebRequestError("Invalid Argument Type [%s]" % (item,))

    def get_dict(self, item, default=Sentinel):
        return self.get(item, default, types=(dict,))

    def get_method(self):
        return self.method

    def set_error(self, error):
        self.is_error = True
        self.response = error.to_dict()

    def send(self, data):
        if self.response is not None:
            raise WebRequestError("Multiple calls to send not allowed")
        self.response = data

    def finish(self):
        if self.id is None:
            return None
        rtype = "error" if self.is_error else "result"
        if self.response is None:
            self.response = {}
        return {"id": self.id, rtype: self.response}

class ServerSocket:
    def __init__(self, webhooks, printer):
        self.printer = printer
        self.webhooks = webhooks
        self.reactor = printer.get_reactor()
        self.sock = self.fd_handle = None
        self.clients = {}
        
        # TUXEDO_RT: Real-time core for high-priority IO
        self.rt_core = None
        if RealTimeCore is not None:
            self.rt_core = RealTimeCore()

        start_args = printer.get_start_args()
        server_address = start_args.get('apiserver')
        is_fileinput = (start_args.get('debuginput') is not None)
        if not server_address or is_fileinput:
            return
        self._remove_socket_file(server_address)
        
        # Windows safety: check for AF_UNIX
        if not hasattr(socket, 'AF_UNIX'):
            logging.error("webhooks: AF_UNIX not supported on this platform")
            return

        self.sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.sock.setblocking(0)
        self.sock.bind(server_address)
        self.sock.listen(5) # TUXEDO_RT: Increased backlog
        self.fd_handle = self.reactor.register_fd(
            self.sock.fileno(), self._handle_accept)
        printer.register_event_handler(
            'klippy:disconnect', self._handle_disconnect)
        printer.register_event_handler(
            "klippy:analyze_shutdown", self._handle_analyze_shutdown)

    def _handle_accept(self, eventtime):
        try:
            sock, addr = self.sock.accept()
        except socket.error:
            return
        sock.setblocking(0)
        client = ClientConnection(self, sock)
        self.clients[client.uid] = client

    def _handle_disconnect(self):
        for client in list(self.clients.values()):
            client.close()
        if self.sock is not None:
            self.reactor.unregister_fd(self.fd_handle)
            try:
                self.sock.close()
            except socket.error:
                pass
            self.sock = None

    def _handle_analyze_shutdown(self, msg, details):
        for client in self.clients.values():
            client.dump_request_log()

    def _remove_socket_file(self, file_path):
        try:
            os.remove(file_path)
        except OSError:
            if os.path.exists(file_path):
                logging.exception(
                    "webhooks: Unable to delete socket file '%s'"
                    % (file_path))
                raise

    def pop_client(self, client_id):
        self.clients.pop(client_id, None)

    def stats(self, eventtime):
        for client in list(self.clients.values()):
            if client.is_blocking:
                client.blocking_count -= 1
                if client.blocking_count < 0:
                    logging.info("Closing unresponsive client %s", client.uid)
                    client.close()
        return False, ""

class ClientConnection:
    """
    TUXEDO_RT: Optimized ClientConnection with compressed transmission support.
    """
    def __init__(self, server, sock):
        self.printer = server.printer
        self.webhooks = server.webhooks
        self.reactor = server.reactor
        self.server = server
        self.uid = id(self)
        self.sock = sock
        self.fd_handle = self.reactor.register_fd(
            self.sock.fileno(), self._process_received, self._do_send)
        self.partial_data = b""
        self.send_buffer = b""
        self.is_blocking = False
        self.blocking_count = 0
        self.use_compression = False
        self.set_client_info("?", "New connection")
        self.request_log = collections.deque([], REQUEST_LOG_SIZE)

    def dump_request_log(self):
        out = ["Dumping %d requests for client %d" % (len(self.request_log), self.uid)]
        for eventtime, request in self.request_log:
            out.append("Received %f: %s" % (eventtime, request))
        logging.info("\n".join(out))

    def set_client_info(self, client_info, state_msg=None):
        if state_msg is None:
            state_msg = "Client info %s" % (repr(client_info),)
        logging.info("webhooks client %s: %s", self.uid, state_msg)
        log_id = "webhooks %s" % (self.uid,)
        if client_info is None:
            self.printer.set_rollover_info(log_id, None, log=False)
            return
        
        # TUXEDO_RT: Enable compression via client_info
        if isinstance(client_info, dict) and client_info.get('compression') == 'zlib':
            self.use_compression = True
            logging.info("webhooks client %s: Compression enabled", self.uid)

        rollover_msg = "webhooks client %s: %s" % (self.uid, repr(client_info))
        self.printer.set_rollover_info(log_id, rollover_msg, log=False)

    def close(self):
        if self.fd_handle is None:
            return
        self.set_client_info(None, "Disconnected")
        self.reactor.unregister_fd(self.fd_handle)
        self.fd_handle = None
        try:
            self.sock.close()
        except socket.error:
            pass
        self.server.pop_client(self.uid)

    def is_closed(self):
        return self.fd_handle is None

    def _process_received(self, eventtime):
        try:
            data = self.sock.recv(16384) # TUXEDO_RT: Increased buffer size
        except socket.error as e:
            if e.errno == errno.EBADF:
                data = b""
            else:
                return
        if not data:
            self.close()
            return
        
        requests = data.split(b'\x03')
        if self.partial_data:
            requests[0] = self.partial_data + requests[0]
        self.partial_data = requests.pop()
        
        for req in requests:
            if not req: continue
            self.request_log.append((eventtime, req))
            try:
                web_request = WebRequest(self, req)
                # TUXEDO_RT: Use endpoint-specific priority for processing
                method = web_request.get_method()
                priority = self.webhooks.get_priority(method)
                self.reactor.register_timer(
                    lambda e, wr=web_request: self._process_request(wr),
                    eventtime, priority=priority)
            except Exception:
                logging.exception("webhooks: Error decoding Server Request %s" % (req))

    def _process_request(self, web_request):
        try:
            method = web_request.get_method()
            func = self.webhooks.get_callback(method)
            func(web_request)
        except self.printer.command_error as e:
            web_request.set_error(WebRequestError(str(e)))
        except Exception as e:
            msg = "Internal Error on WebRequest: %s" % (web_request.get_method())
            logging.exception(msg)
            web_request.set_error(WebRequestError(str(e)))
            self.printer.invoke_shutdown(msg)
        
        result = web_request.finish()
        if result is not None:
            self.send(result)
        return self.reactor.NEVER

    def send(self, data):
        try:
            jmsg = json_dumps(data)
            msg = jmsg + b"\x03"
            # TUXEDO_RT: Compression support
            if self.use_compression and len(msg) > 512:
                msg = b"\x02" + zlib.compress(msg)
            
            self.send_buffer += msg
        except (TypeError, ValueError) as e:
            msg = "json encoding error: %s" % (str(e))
            logging.exception(msg)
            self.printer.invoke_shutdown(msg)
            return
        
        if not self.is_blocking:
            self._do_send()

    def _do_send(self, eventtime=None):
        if self.fd_handle is None or not self.send_buffer:
            return
        try:
            sent = self.sock.send(self.send_buffer)
        except socket.error as e:
            if e.errno not in [errno.EAGAIN, errno.EWOULDBLOCK]:
                logging.info("webhooks: socket write error %d" % (self.uid,))
                self.close()
                return
            sent = 0
        
        if sent < len(self.send_buffer):
            if not self.is_blocking:
                self.reactor.set_fd_wake(self.fd_handle, False, True)
                self.is_blocking = True
                self.blocking_count = 5
        elif self.is_blocking:
            self.reactor.set_fd_wake(self.fd_handle, True, False)
            self.is_blocking = False
        
        self.send_buffer = self.send_buffer[sent:]

class WebHooks:
    """
    TUXEDO_RT: Optimized WebHooks manager with status caching.
    """
    def __init__(self, printer):
        self.printer = printer
        self._endpoints = {}
        self._endpoint_priorities = {}
        self._remote_methods = {}
        self._mux_endpoints = {}
        self._status_cache = {}
        self._last_status_time = 0.0
        
        self.register_endpoint("list_endpoints", self._handle_list_endpoints, priority=2)
        self.register_endpoint("info", self._handle_info_request, priority=2)
        self.register_endpoint("emergency_stop", self._handle_estop_request, priority=0)
        self.register_endpoint("register_remote_method",
                               self._handle_rpc_registration, priority=2)
        self.sconn = ServerSocket(self, printer)

    def register_endpoint(self, path, callback, priority=2):
        if path in self._endpoints:
            raise WebRequestError("Path already registered to an endpoint")
        self._endpoints[path] = callback
        self._endpoint_priorities[path] = priority

    def register_mux_endpoint(self, path, key, value, callback, priority=2):
        prev = self._mux_endpoints.get(path)
        if prev is None:
            self.register_endpoint(path, self._handle_mux, priority=priority)
            self._mux_endpoints[path] = prev = (key, {})
        prev_key, prev_values = prev
        if prev_key != key:
            raise self.printer.config_error(
                "mux endpoint %s %s %s may have only one key (%s)"
                % (path, key, value, prev_key))
        if value in prev_values:
            raise self.printer.config_error(
                "mux endpoint %s %s %s already registered (%s)"
                % (path, key, value, prev_values))
        prev_values[value] = callback

    def get_priority(self, path):
        return self._endpoint_priorities.get(path, 2)

    def _handle_mux(self, web_request):
        key, values = self._mux_endpoints[web_request.get_method()]
        key_param = web_request.get(key, None if None in values else Sentinel)
        if key_param not in values:
            raise web_request.error("The value '%s' is not valid for %s"
                                    % (key_param, key))
        values[key_param](web_request)

    def _handle_list_endpoints(self, web_request):
        web_request.send({'endpoints': list(self._endpoints.keys())})

    def _handle_info_request(self, web_request):
        client_info = web_request.get_dict('client_info', None)
        if client_info is not None:
            web_request.get_client_connection().set_client_info(client_info)
        
        state_message, state = self.printer.get_state_message()
        src_path = os.path.dirname(__file__)
        klipper_path = os.path.normpath(os.path.join(src_path, ".."))
        
        response = {
            'state': state,
            'state_message': state_message,
            'hostname': socket.gethostname(),
            'klipper_path': klipper_path,
            'python_path': sys.executable,
            'process_id': os.getpid(),
            'cpu_info': util.get_cpu_info()
        }
        
        if hasattr(os, 'getuid'):
            response['user_id'] = os.getuid()
            response['group_id'] = os.getgid()
            
        start_args = self.printer.get_start_args()
        for sa in ['log_file', 'config_file', 'software_version']:
            response[sa] = start_args.get(sa)
        web_request.send(response)

    def _handle_estop_request(self, web_request):
        self.printer.invoke_shutdown("Shutdown due to webhooks request")

    def _handle_rpc_registration(self, web_request):
        template = web_request.get_dict('response_template')
        method = web_request.get_str('remote_method')
        new_conn = web_request.get_client_connection()
        logging.info("webhooks: registering remote method '%s' "
                     "for connection id: %d" % (method, id(new_conn)))
        self._remote_methods.setdefault(method, {})[new_conn] = template

    def get_connection(self):
        return self.sconn

    def get_callback(self, path):
        cb = self._endpoints.get(path)
        if cb is None:
            msg = "webhooks: No registered callback for path '%s'" % (path)
            logging.info(msg)
            raise WebRequestError(msg)
        return cb

    def get_status(self, eventtime):
        # TUXEDO_RT: Status caching (100ms)
        if eventtime < self._last_status_time + 0.1:
            return self._status_cache
        
        state_message, state = self.printer.get_state_message()
        self._status_cache = {'state': state, 'state_message': state_message}
        self._last_status_time = eventtime
        return self._status_cache

    def stats(self, eventtime):
        # TUXEDO_RT: Low priority stats collection
        return self.sconn.stats(eventtime)

    def call_remote_method(self, method, **kwargs):
        conn_map = self._remote_methods.get(method)
        if conn_map is None:
            raise self.printer.command_error("Remote method '%s' not registered" % (method))
        
        valid_conns = {}
        for conn, template in conn_map.items():
            if not conn.is_closed():
                valid_conns[conn] = template
                out = {'params': kwargs}
                out.update(template)
                conn.send(out)
        
        if not valid_conns:
            del self._remote_methods[method]
            raise self.printer.command_error("No active connections for method '%s'" % (method))
        self._remote_methods[method] = valid_conns

class GCodeHelper:
    def __init__(self, printer):
        self.printer = printer
        self.gcode = printer.lookup_object("gcode")
        self.is_output_registered = False
        self.clients = {}
        wh = printer.lookup_object('webhooks')
        wh.register_endpoint("gcode/help", self._handle_help, priority=2)
        wh.register_endpoint("gcode/script", self._handle_script, priority=1)
        wh.register_endpoint("gcode/restart", self._handle_restart, priority=0)
        wh.register_endpoint("gcode/firmware_restart",
                             self._handle_firmware_restart, priority=0)
        wh.register_endpoint("gcode/subscribe_output",
                             self._handle_subscribe_output, priority=2)

    def _handle_help(self, web_request):
        web_request.send(self.gcode.get_command_help())

    def _handle_script(self, web_request):
        self.gcode.run_script(web_request.get_str('script'))

    def _handle_restart(self, web_request):
        self.gcode.run_script('restart')

    def _handle_firmware_restart(self, web_request):
        self.gcode.run_script('firmware_restart')

    def _output_callback(self, msg):
        if not self.clients: return
        for cconn, template in list(self.clients.items()):
            if cconn.is_closed():
                del self.clients[cconn]
                continue
            tmp = dict(template)
            tmp['params'] = {'response': msg}
            cconn.send(tmp)

    def _handle_subscribe_output(self, web_request):
        cconn = web_request.get_client_connection()
        template = web_request.get_dict('response_template', {})
        self.clients[cconn] = template
        if not self.is_output_registered:
            self.gcode.register_output_handler(self._output_callback)
            self.is_output_registered = True

# TUXEDO_RT: Increased refresh time to reduce CPU usage
SUBSCRIPTION_REFRESH_TIME = .25

class QueryStatusHelper:
    """
    TUXEDO_RT: Optimized QueryStatusHelper with batch processing.
    """
    def __init__(self, printer):
        self.printer = printer
        self.clients = {}
        self.pending_queries = []
        self.query_timer = None
        self.last_query = {}
        webhooks = printer.lookup_object('webhooks')
        webhooks.register_endpoint("objects/list", self._handle_list, priority=2)
        webhooks.register_endpoint("objects/query", self._handle_query, priority=1)
        webhooks.register_endpoint("objects/subscribe",
                                   self._handle_subscribe, priority=1)

    def _handle_list(self, web_request):
        objects = [n for n, o in self.printer.lookup_objects() if hasattr(o, 'get_status')]
        web_request.send({'objects': objects})

    def _do_query(self, eventtime):
        last_query = self.last_query
        query = self.last_query = {}
        msglist = self.pending_queries
        self.pending_queries = []
        msglist.extend(self.clients.values())
        
        reactor = self.printer.get_reactor()
        with reactor.assert_no_pause():
            for cconn, subscription, send_func, template in msglist:
                is_query = cconn is None
                if not is_query and cconn.is_closed():
                    del self.clients[cconn]
                    continue
                
                # TUXEDO_RT: Optimized status collection
                status = {}
                for obj_name, req_items in subscription.items():
                    res = query.get(obj_name)
                    if res is None:
                        po = self.printer.lookup_object(obj_name, None)
                        if po is None or not hasattr(po, 'get_status'):
                            res = query[obj_name] = {}
                        else:
                            res = query[obj_name] = po.get_status(eventtime)
                    
                    if req_items is None:
                        req_items = list(res.keys())
                        if req_items:
                            subscription[obj_name] = req_items
                    
                    lres = last_query.get(obj_name, {})
                    cres = {}
                    for ri in req_items:
                        rd = res.get(ri)
                        if is_query or rd != lres.get(ri):
                            cres[ri] = rd
                    if cres or is_query:
                        status[obj_name] = cres
                
                if status or is_query:
                    if not is_query:
                        last_query[id(cconn)] = status
                    send_func(status)
        
        # TUXEDO_RT: Use RT priority (1) for subscriptions
        priority = 1 if self.printer.lookup_object('webhooks').sconn.rt_core else 2
        if self.clients:
            self.query_timer = reactor.register_timer(
                self._do_query, eventtime + SUBSCRIPTION_REFRESH_TIME,
                priority=priority)
        else:
            self.query_timer = None

    def _handle_query(self, web_request):
        objects = web_request.get_dict('objects')
        self.pending_queries.append((None, objects, web_request.send, {}))
        if self.query_timer is None:
            # TUXEDO_RT: Use RT priority (1) for immediate queries
            priority = 1 if self.printer.lookup_object('webhooks').sconn.rt_core else 2
            self.printer.get_reactor().register_timer(self._do_query, priority=priority)

    def _handle_subscribe(self, web_request):
        cconn = web_request.get_client_connection()
        objects = web_request.get_dict('objects')
        template = web_request.get_dict('response_template', {})
        
        def send_func(status):
            out = dict(template)
            out['params'] = {'status': status, 'eventtime': self.printer.get_reactor().monotonic()}
            cconn.send(out)
            
        self.clients[cconn] = (cconn, objects, send_func, template)
        if self.query_timer is None:
            # TUXEDO_RT: Use RT priority (1) for starting subscriptions
            priority = 1 if self.printer.lookup_object('webhooks').sconn.rt_core else 2
            self.printer.get_reactor().register_timer(self._do_query, priority=priority)
