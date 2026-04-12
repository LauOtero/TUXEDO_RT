"""
Moonraker UPS NUT Management Component

Este componente proporciona integración con servidores NUT (Network UPS Tools)
para monitoreo y gestión de UPS en impresoras 3D con Klipper.

Características:
- Monitoreo en tiempo real del estado de la UPS
- Notificaciones automáticas a clientes conectados vía WebSocket
- Endpoints REST para configuración y consulta de estado
- Gestión de eventos de fallo de energía y apagado seguro
- Integración con el sistema de notificaciones de Moonraker

Autor: TUXEDO_RT Project
Licencia: GPL-3.0
"""

from __future__ import annotations
import logging
import asyncio
import socket
import time
import threading
from typing import Any, Dict, List, Optional, Union
from moonraker.server import Server
from moonraker.components.websocket import WebsocketManager
from moonraker.components.history import History
from moonraker.utils import get_server_version

# =============================================================================
# Excepciones NUT
# =============================================================================

class NUTProtocolError(Exception):
    """Error inesperado en el protocolo NUT."""
    pass

class NUTAuthError(NUTProtocolError):
    """Error de autenticación en servidor NUT."""
    pass

class NUTConnectionError(NUTProtocolError):
    """Error de conexión TCP con servidor NUT."""
    pass

class NUTTimeoutError(NUTProtocolError):
    """Tiempo de espera agotado en operación NUT."""
    pass

class NUTDataError(NUTProtocolError):
    """Datos malformados en respuesta NUT."""
    pass


# =============================================================================
# Cliente NUT Asíncrono
# =============================================================================

class NUTClient:
    """
    Cliente asíncrono para comunicación con servidores NUT.
    
    Implementa el protocolo NUT 2.x sobre TCP/IP.
    """
    
    DEFAULT_PORT = 3493
    DEFAULT_TIMEOUT = 5.0
    RECONNECT_DELAY = 5.0
    
    def __init__(
        self,
        host: str = "127.0.0.1",
        port: int = DEFAULT_PORT,
        username: str = "monuser",
        password: str = "secret",
        ups_name: str = "ups",
        timeout: float = DEFAULT_TIMEOUT
    ):
        self.host = host
        self.port = port
        self.username = username
        self.password = password
        self.ups_name = ups_name
        self.timeout = timeout
        
        self._reader: Optional[asyncio.StreamReader] = None
        self._writer: Optional[asyncio.StreamWriter] = None
        self._connected = False
        self._login_success = False
        self._lock = asyncio.Lock()
        
        # Estado cacheado de la UPS
        self._ups_state: Dict[str, Any] = {}
        self._last_update: float = 0.0
        
    async def connect(self) -> bool:
        """Establece conexión con el servidor NUT."""
        try:
            self._reader, self._writer = await asyncio.wait_for(
                asyncio.open_connection(self.host, self.port),
                timeout=self.timeout
            )
            self._connected = True
            
            # Intentar autenticación
            if await self._login():
                self._login_success = True
                logging.info(f"NUT: Connected to {self.host}:{self.port}")
                return True
            else:
                logging.warning(f"NUT: Authentication failed for {self.username}")
                return False
                
        except asyncio.TimeoutError:
            logging.error(f"NUT: Connection timeout to {self.host}:{self.port}")
            self._connected = False
            return False
        except socket.error as e:
            logging.error(f"NUT: Connection error: {e}")
            self._connected = False
            return False
        except Exception as e:
            logging.error(f"NUT: Unexpected connection error: {e}")
            self._connected = False
            return False
    
    async def disconnect(self) -> None:
        """Cierra la conexión con el servidor NUT."""
        if self._writer:
            try:
                self._writer.close()
                await self._writer.wait_closed()
            except Exception:
                pass
        self._connected = False
        self._login_success = False
        self._reader = None
        self._writer = None
    
    async def _login(self) -> bool:
        """Autentica con el servidor NUT."""
        try:
            # Comando INSTCMD USER_LOGIN no es estándar, usamos enfoque directo
            # En NUT, la autenticación es implícita al hacer queries
            # Algunos servidores requieren LOGIN explícito
            response = await self._send_command("USERNAME %s" % self.username)
            if response and "OK" in response:
                response = await self._send_command("PASSWORD %s" % self.password)
                return response and "OK" in response
            return True  # Si no requiere auth explícita, continuar
        except Exception:
            return True  # Asumir que no requiere auth explícita
    
    async def _send_command(self, command: str) -> Optional[str]:
        """Envía un comando al servidor NUT y retorna la respuesta."""
        if not self._writer:
            raise NUTConnectionError("Not connected")
        
        try:
            self._writer.write((command + "\n").encode('ascii'))
            await self._writer.drain()
            
            response = await asyncio.wait_for(
                self._reader.readline(),
                timeout=self.timeout
            )
            return response.decode('ascii').strip()
        except asyncio.TimeoutError:
            raise NUTTimeoutError("Command timeout")
        except Exception as e:
            raise NUTConnectionError(f"Send failed: {e}")
    
    async def _read_until_end(self) -> List[str]:
        """Lee líneas hasta encontrar END COMMAND."""
        lines = []
        try:
            while True:
                line = await asyncio.wait_for(
                    self._reader.readline(),
                    timeout=self.timeout
                )
                line = line.decode('ascii').strip()
                if line == "END COMMAND":
                    break
                if line and not line.startswith("#"):
                    lines.append(line)
        except asyncio.TimeoutError:
            raise NUTTimeoutError("Read timeout")
        return lines
    
    async def get_ups_status(self) -> Dict[str, Any]:
        """
        Obtiene el estado completo de la UPS.
        
        Retorna un diccionario con:
        - status: Estado general (OL, OB, LB, etc.)
        - battery.charge: Porcentaje de carga
        - battery.runtime: Tiempo restante estimado (segundos)
        - input.voltage: Voltaje de entrada
        - output.load: Porcentaje de carga
        - ups.temperature: Temperatura de la UPS
        """
        if not self._connected:
            await self.connect()
        
        if not self._connected:
            return {"error": "Not connected"}
        
        try:
            async with self._lock:
                # Obtener todas las variables de la UPS
                cmd = f"VAR {self.ups_name}"
                response = await self._send_command(cmd)
                
                if not response or "BEGIN VAR" not in response:
                    raise NUTProtocolError(f"Unexpected response: {response}")
                
                lines = await self._read_until_end()
                
                # Parsear variables
                state = {
                    "name": self.ups_name,
                    "status": "unknown",
                    "battery": {},
                    "input": {},
                    "output": {},
                    "ups": {}
                }
                
                for line in lines:
                    parts = line.split(None, 2)
                    if len(parts) < 3:
                        continue
                    
                    var_name = parts[1].strip('"')
                    var_value = parts[2].strip('"')
                    
                    # Clasificar variables
                    if var_name.startswith("battery."):
                        key = var_name[8:]
                        state["battery"][key] = self._parse_value(var_value)
                    elif var_name.startswith("input."):
                        key = var_name[6:]
                        state["input"][key] = self._parse_value(var_value)
                    elif var_name.startswith("output."):
                        key = var_name[7:]
                        state["output"][key] = self._parse_value(var_value)
                    elif var_name.startswith("ups."):
                        key = var_name[4:]
                        state["ups"][key] = self._parse_value(var_value)
                
                # Obtener estado de comandos (STATUS)
                cmd = f"CMDINST {self.ups_name}"
                response = await self._send_command(cmd)
                if response and "BEGIN LIST CMD" in response:
                    await self._read_until_end()
                
                # Calcular estado derivado
                ups_status = state["ups"].get("status", "")
                state["on_battery"] = "OB" in ups_status
                state["low_battery"] = "LB" in ups_status
                state["running"] = "OL" in ups_status
                state["charging"] = "RB" in ups_status or "CHRG" in ups_status
                
                self._ups_state = state
                self._last_update = time.time()
                
                return state
                
        except NUTProtocolError:
            logging.error("NUT: Protocol error getting UPS status")
            return {"error": "Protocol error"}
        except Exception as e:
            logging.error(f"NUT: Error getting UPS status: {e}")
            return {"error": str(e)}
    
    def _parse_value(self, value: str) -> Union[str, int, float]:
        """Convierte un valor de cadena a tipo numérico si es posible."""
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            return value
    
    @property
    def is_connected(self) -> bool:
        return self._connected
    
    @property
    def last_update(self) -> float:
        return self._last_update


# =============================================================================
# Componente Moonraker UPS NUT
# =============================================================================

class UpsNut:
    """
    Componente de Moonraker para gestión de UPS mediante NUT.
    
    Proporciona:
    - Polling periódico del estado de la UPS
    - Notificaciones push a clientes WebSocket
    - Endpoints REST para configuración y consulta
    - Gestión de eventos de fallo de energía
    """
    
    COMPONENT_VERSION = "1.0.0"
    DEFAULT_POLL_INTERVAL = 5.0  # segundos
    LOW_BATTERY_THRESHOLD = 20  # porcentaje
    CRITICAL_BATTERY_THRESHOLD = 10  # porcentaje
    
    def __init__(self, server: Server) -> None:
        self.server = server
        self.event_loop = server.get_event_loop()
        
        # Configuración
        self._host = "127.0.0.1"
        self._port = 3493
        self._username = "monuser"
        self._password = "secret"
        self._ups_name = "ups"
        self._poll_interval = self.DEFAULT_POLL_INTERVAL
        self._enabled = True
        
        # Estado interno
        self._nut_client: Optional[NUTClient] = None
        self._current_state: Dict[str, Any] = {}
        self._previous_state: Dict[str, Any] = {}
        self._last_poll_time: float = 0.0
        self._poll_task: Optional[asyncio.Task] = None
        self._shutdown_pending = False
        
        # Manejadores de eventos
        self._event_callbacks: List[callable] = []
        
        # Registro de logs
        self.logger = logging.getLogger("moonraker:ups_nut")
        
    async def component_init(self) -> None:
        """Inicialización del componente."""
        self.logger.info("UPS NUT Component initializing...")
        
        # Cargar configuración
        config = self.server.get_config()
        
        if config.has_section("ups_nut"):
            self._host = config.get("ups_nut", "host", fallback="127.0.0.1")
            self._port = config.getint("ups_nut", "port", fallback=3493)
            self._username = config.get("ups_nut", "username", fallback="monuser")
            self._password = config.get("ups_nut", "password", fallback="secret")
            self._ups_name = config.get("ups_nut", "ups_name", fallback="ups")
            self._poll_interval = config.getfloat(
                "ups_nut", "poll_interval", fallback=self.DEFAULT_POLL_INTERVAL
            )
            self._enabled = config.getboolean("ups_nut", "enabled", fallback=True)
        
        if not self._enabled:
            self.logger.info("UPS NUT Component disabled by configuration")
            return
        
        # Inicializar cliente NUT
        self._nut_client = NUTClient(
            host=self._host,
            port=self._port,
            username=self._username,
            password=self._password,
            ups_name=self._ups_name
        )
        
        # Registrar endpoints API
        self._register_api_endpoints()
        
        # Suscribirse a eventos del servidor
        self._subscribe_to_events()
        
        # Iniciar polling
        self._start_polling()
        
        self.logger.info("UPS NUT Component initialized successfully")
    
    def _register_api_endpoints(self) -> None:
        """Registra los endpoints REST de la API."""
        webapi = self.server.lookup_component("webapi")
        if webapi is None:
            self.logger.warning("WebAPI component not found, skipping endpoint registration")
            return
        
        # Endpoint: GET /printer/ups/status
        webapi.register_endpoint(
            "GET",
            "/printer/ups/status",
            self._handle_get_status,
            desc="Get current UPS status"
        )
        
        # Endpoint: GET /printer/ups/config
        webapi.register_endpoint(
            "GET",
            "/printer/ups/config",
            self._handle_get_config,
            desc="Get UPS configuration"
        )
        
        # Endpoint: POST /printer/ups/config
        webapi.register_endpoint(
            "POST",
            "/printer/ups/config",
            self._handle_set_config,
            desc="Update UPS configuration"
        )
        
        # Endpoint: POST /printer/ups/test_connection
        webapi.register_endpoint(
            "POST",
            "/printer/ups/test_connection",
            self._handle_test_connection,
            desc="Test connection to NUT server"
        )
        
        # Endpoint: POST /printer/ups/shutdown
        webapi.register_endpoint(
            "POST",
            "/printer/ups/shutdown",
            self._handle_initiate_shutdown,
            desc="Initiate safe shutdown"
        )
        
        self.logger.info("Registered UPS NUT API endpoints")
    
    def _subscribe_to_events(self) -> None:
        """Suscribe a eventos del servidor."""
        # Suscribirse a eventos de conexión/desconexión de clientes
        event_manager = self.server.lookup_component("event_manager")
        if event_manager:
            event_manager.on_event("server:klippy_pong", self._on_klippy_pong)
            event_manager.on_event("server:klippy_disconnect", self._on_klippy_disconnect)
    
    async def _on_klippy_pong(self) -> None:
        """Maneja evento de reconexión con Klipper."""
        self.logger.info("Klipper reconnected, refreshing UPS state")
        await self._poll_ups_state()
    
    async def _on_klippy_disconnect(self) -> None:
        """Maneja evento de desconexión de Klipper."""
        self.logger.warning("Klipper disconnected")
    
    def _start_polling(self) -> None:
        """Inicia el polling periódico del estado de la UPS."""
        if self._poll_task is not None:
            self._poll_task.cancel()
        
        self._poll_task = self.event_loop.create_task(self._poll_loop())
    
    async def _poll_loop(self) -> None:
        """Bucle de polling del estado de la UPS."""
        while not self._shutdown_pending:
            try:
                await self._poll_ups_state()
            except Exception as e:
                self.logger.error(f"Error polling UPS state: {e}")
            
            # Esperar hasta el próximo poll
            await asyncio.sleep(self._poll_interval)
    
    async def _poll_ups_state(self) -> None:
        """Realiza un poll del estado de la UPS."""
        if not self._nut_client:
            return
        
        try:
            state = await self._nut_client.get_ups_status()
            
            if "error" not in state:
                self._previous_state = self._current_state.copy()
                self._current_state = state
                self._last_poll_time = time.time()
                
                # Detectar cambios de estado importantes
                await self._check_state_changes()
                
                # Notificar a clientes suscritos
                await self._notify_clients()
                
        except Exception as e:
            self.logger.error(f"Failed to poll UPS: {e}")
            # Intentar reconectar
            if self._nut_client.is_connected:
                await self._nut_client.disconnect()
    
    async def _check_state_changes(self) -> None:
        """Verifica cambios de estado importantes y genera eventos."""
        prev = self._previous_state
        curr = self._current_state
        
        # Cambio a batería (fallo de energía)
        if not prev.get("on_battery", False) and curr.get("on_battery", False):
            self.logger.warning("UPS: Power failure detected - running on battery")
            await self._send_event_notification(
                "ups_on_battery",
                {
                    "message": "Power failure detected - UPS running on battery",
                    "battery_charge": curr.get("battery", {}).get("charge_pct", 0),
                    "runtime_remaining": curr.get("battery", {}).get("runtime_s", 0)
                }
            )
        
        # Retorno de energía
        if prev.get("on_battery", False) and not curr.get("on_battery", False):
            self.logger.info("UPS: Power restored")
            await self._send_event_notification(
                "ups_power_restored",
                {
                    "message": "Power restored - UPS charging",
                    "battery_charge": curr.get("battery", {}).get("charge_pct", 0)
                }
            )
        
        # Batería baja
        if curr.get("low_battery", False) and not prev.get("low_battery", False):
            self.logger.critical("UPS: Low battery warning!")
            await self._send_event_notification(
                "ups_low_battery",
                {
                    "message": "Low battery warning - shutdown may be imminent",
                    "battery_charge": curr.get("battery", {}).get("charge_pct", 0),
                    "runtime_remaining": curr.get("battery", {}).get("runtime_s", 0)
                }
            )
        
        # Batería crítica
        battery_charge = curr.get("battery", {}).get("charge_pct", 100)
        if battery_charge <= self.CRITICAL_BATTERY_THRESHOLD:
            if not prev.get("battery", {}).get("charge_pct", 100) <= self.CRITICAL_BATTERY_THRESHOLD:
                self.logger.critical("UPS: Critical battery level!")
                await self._send_event_notification(
                    "ups_critical_battery",
                    {
                        "message": "Critical battery level - immediate shutdown recommended",
                        "battery_charge": battery_charge,
                        "runtime_remaining": curr.get("battery", {}).get("runtime_s", 0)
                    }
                )
    
    async def _send_event_notification(self, event: str, data: Dict[str, Any]) -> None:
        """Envía una notificación de evento a los clientes."""
        event_manager = self.server.lookup_component("event_manager")
        if event_manager:
            event_manager.send_event(event, data)
        
        # También enviar como notificación si está disponible
        notifier = self.server.lookup_component("notifier")
        if notifier:
            await notifier.notify(event, data)
    
    async def _notify_clients(self) -> None:
        """Notifica el estado actual a los clientes WebSocket."""
        ws_manager: Optional[WebsocketManager] = self.server.lookup_component("websocket")
        if ws_manager:
            notification = {
                "method": "notify_ups_update",
                "params": self.get_status()
            }
            ws_manager.send_event(notification)
    
    def _handle_get_status(self, web_request):
        """Manejador para GET /printer/ups/status"""
        return self.get_status()
    
    def _handle_get_config(self, web_request):
        """Manejador para GET /printer/ups/config"""
        return self.get_config()
    
    async def _handle_set_config(self, web_request):
        """Manejador para POST /printer/ups/config"""
        args = web_request.get_args()
        
        # Validar y actualizar configuración
        if "poll_interval" in args:
            interval = float(args["poll_interval"])
            if interval < 1.0:
                raise self.server.error("Poll interval must be at least 1 second")
            self._poll_interval = interval
            self._start_polling()
        
        if "enabled" in args:
            self._enabled = bool(args["enabled"])
        
        return self.get_config()
    
    async def _handle_test_connection(self, web_request):
        """Manejador para POST /printer/ups/test_connection"""
        if not self._nut_client:
            return {"success": False, "error": "NUT client not initialized"}
        
        try:
            connected = await self._nut_client.connect()
            if connected:
                state = await self._nut_client.get_ups_status()
                await self._nut_client.disconnect()
                return {
                    "success": True,
                    "message": "Connection successful",
                    "ups_state": state
                }
            else:
                return {"success": False, "error": "Failed to connect"}
        except Exception as e:
            return {"success": False, "error": str(e)}
    
    async def _handle_initiate_shutdown(self, web_request):
        """Manejador para POST /printer/ups/shutdown"""
        self.logger.warning("Manual shutdown initiated via API")
        
        # Notificar a Klipper para guardar estado
        event_manager = self.server.lookup_component("event_manager")
        if event_manager:
            event_manager.send_event("ups:initiate_shutdown", {
                "reason": "manual_api_request",
                "battery_charge": self._current_state.get("battery", {}).get("charge_pct", 0)
            })
        
        return {
            "success": True,
            "message": "Shutdown sequence initiated"
        }
    
    def get_status(self) -> Dict[str, Any]:
        """Obtiene el estado actual de la UPS."""
        return {
            "enabled": self._enabled,
            "connected": self._nut_client.is_connected if self._nut_client else False,
            "last_update": self._last_poll_time,
            "poll_interval": self._poll_interval,
            "ups": self._current_state
        }
    
    def get_config(self) -> Dict[str, Any]:
        """Obtiene la configuración actual."""
        return {
            "host": self._host,
            "port": self._port,
            "username": self._username,
            "ups_name": self._ups_name,
            "poll_interval": self._poll_interval,
            "enabled": self._enabled,
            "low_battery_threshold": self.LOW_BATTERY_THRESHOLD,
            "critical_battery_threshold": self.CRITICAL_BATTERY_THRESHOLD
        }
    
    async def component_close(self) -> None:
        """Limpieza al cerrar el componente."""
        self.logger.info("UPS NUT Component shutting down...")
        self._shutdown_pending = True
        
        if self._poll_task:
            self._poll_task.cancel()
            try:
                await self._poll_task
            except asyncio.CancelledError:
                pass
        
        if self._nut_client:
            await self._nut_client.disconnect()
        
        self.logger.info("UPS NUT Component shut down complete")


# =============================================================================
# Registro del Componente
# =============================================================================

def load_component(server: Server) -> UpsNut:
    """Carga el componente UPS NUT en Moonraker."""
    return UpsNut(server)
