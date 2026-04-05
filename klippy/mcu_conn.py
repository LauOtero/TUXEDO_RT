#!/usr/bin/env python3
# Klipper MCU Connection Management System
# Provides a pluggable architecture for UART, CAN, RS485, EtherCAT, and custom protocols.
# Copyright (C) 2016-2026 Kevin O'Connor <kevin@koconnor.net>
# SPDX-License-Identifier: GPL-3.0-or-later

import logging
from typing import Dict, Callable, Optional, Any
import connhdl

# =========================================================================
# Connection Registry & Factory
# =========================================================================
# Registry que mapea nombres de tipo de conexión a constructores.
# Permite añadir nuevos backends sin modificar el núcleo de Klipper.
_CONNECTION_BACKENDS: Dict[str, Callable[[Any, str], connhdl.ConnectionHandler]] = {}

def register_connection_backend(name: str, factory_func: Callable[[Any, str], connhdl.ConnectionHandler]) -> None:
    """
    Registra un nuevo tipo de conexión en el sistema.
    
    :param name: Nombre identificador usado en printer.cfg (ej. 'uart', 'can', 'ethertux').
    :param factory_func: Función que recibe (config, mcu_name) y devuelve un ConnectionHandler inicializado.
    """
    _CONNECTION_BACKENDS[name.lower()] = factory_func

def get_available_connections() -> list:
    """Retorna lista de tipos de conexión registrados."""
    return list(_CONNECTION_BACKENDS.keys())

def create_mcu_connection(config: Any, mcu_name: str) -> connhdl.ConnectionHandler:
    """
    Crea e inicializa el manejador de conexión según la configuración del MCU.
    
    :param config: Objeto de configuración de Klipper.
    :param mcu_name: Nombre del MCU (ej. 'mcu', 'mcu_bed').
    :return: Instancia de ConnectionHandler lista para usar.
    :raises ValueError: Si el tipo de conexión no está registrado o falta configuración.
    """
    # Detectar tipo de conexión desde printer.cfg o parámetros implícitos
    conn_type = config.get('conn_type', None)
    
    # Detección automática si no se especifica conn_type explícitamente
    if conn_type is None:
        if config.get('canbus_uuid', None) is not None:
            conn_type = 'can'
        elif config.get('ethertux_alias', None) is not None:
            conn_type = 'ethertux'
        elif config.get('rs485_rts_pin', None) is not None:
            conn_type = 'rs485'
        else:
            conn_type = 'uart'
            
    conn_type = conn_type.lower()
    factory = _CONNECTION_BACKENDS.get(conn_type)
    if factory is None:
        available = get_available_connections()
        raise ValueError(f"Unknown connection type '{conn_type}'. Available: {available}")
        
    logging.info("MCU '%s': Initializing '%s' connection backend", mcu_name, conn_type)
    return factory(config, mcu_name)

# =========================================================================
# Default Connection Backends Registration
# =========================================================================

def _create_uart_backend(config: Any, mcu_name: str) -> connhdl.ConnectionHandler:
    """Crea manejador para conexión UART/USB estándar."""
    serialport = config.get('serial')
    baud = config.getint('baud', 250000, minval=2400)
    reactor = config.get_printer().get_reactor()
    handler = connhdl.ConnectionHandler(reactor, mcu_name=mcu_name)
    handler._uart_port = serialport
    handler._uart_baud = baud
    return handler

def _create_can_backend(config: Any, mcu_name: str) -> connhdl.ConnectionHandler:
    """Crea manejador para conexión CAN Bus."""
    uuid = config.get('canbus_uuid')
    iface = config.get('canbus_interface', 'can0')
    reactor = config.get_printer().get_reactor()
    # Registrar UUID en el subsistema canbus_ids
    printer = config.get_printer()
    cbid = printer.load_object(config, 'canbus_ids')
    cbid.add_uuid(config, uuid, iface)
    handler = connhdl.ConnectionHandler(reactor, mcu_name=mcu_name)
    handler._can_uuid = uuid
    handler._can_iface = iface
    return handler

def _create_rs485_backend(config: Any, mcu_name: str) -> connhdl.ConnectionHandler:
    """Crea manejador para conexión RS-485 half-duplex."""
    serialport = config.get('serial')
    baud = config.getint('baud', 250000, minval=2400)
    reactor = config.get_printer().get_reactor()
    handler = connhdl.ConnectionHandler(reactor, mcu_name=mcu_name)
    handler._rs485_port = serialport
    handler._rs485_baud = baud
    return handler

def _create_ethertux_backend(config: Any, mcu_name: str) -> connhdl.ConnectionHandler:
    """Crea manejador para conexión EtherCAT (IGH Master)."""
    alias = config.getint('ethertux_alias', 0)
    position = config.getint('ethertux_position', 0)
    vendor = config.getint('ethertux_vendor_id', 0)
    product = config.getint('ethertux_product_id', 0)
    cycle_ns = config.getint('ethertux_cycle_ns', 250000)
    reactor = config.get_printer().get_reactor()
    handler = connhdl.ConnectionHandler(reactor, mcu_name=mcu_name)
    handler._et_alias = alias
    handler._et_pos = position
    handler._et_vendor = vendor
    handler._et_product = product
    handler._et_cycle = cycle_ns
    return handler

# Registrar backends por defecto al cargar el módulo
register_connection_backend('uart', _create_uart_backend)
register_connection_backend('serial', _create_uart_backend)  # Alias común
register_connection_backend('can', _create_can_backend)
register_connection_backend('canbus', _create_can_backend)   # Alias común
register_connection_backend('rs485', _create_rs485_backend)
register_connection_backend('ethertux', _create_ethertux_backend)

# =========================================================================
# Ejemplo: Cómo añadir un nuevo protocolo (ej. SPI o UDP)
# =========================================================================
# 1. Define tu función de creación:
# def _create_spi_backend(config, mcu_name):
#     handler = connhdl.ConnectionHandler(config.get_printer().get_reactor(), mcu_name)
#     handler._spi_bus = config.get('spi_bus')
#     handler._spi_cs = config.get('spi_cs_pin')
#     return handler
#
# 2. Regístralo:
# register_connection_backend('spi', _create_spi_backend)
#
# ¡Listo! Ahora `conn_type: spi` funcionará automáticamente.