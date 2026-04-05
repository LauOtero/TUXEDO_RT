import re
import logging
from typing import Dict, Any, Optional, List, Tuple

class error(Exception):
    pass

######################################################################
# Command translation
######################################################################

# Optimized regex for pin extraction
re_pin = re.compile(r'(?P<prefix>[ _]pin=)(?P<name>[^ ]*)')

class PinResolver:
    def __init__(self, validate_aliases=True):
        self.validate_aliases = validate_aliases
        self.reserved = {}
        self.aliases = {}
        self.active_pins = {}
        # TUXEDO_RT: Cache for resolved aliases to speed up command updates
        self._alias_cache = {}

    def reserve_pin(self, pin, reserve_name):
        if pin in self.reserved and self.reserved[pin] != reserve_name:
            raise error("Pin %s reserved for %s - can't reserve for %s" % (
                pin, self.reserved[pin], reserve_name))
        self.reserved[pin] = reserve_name

    def alias_pin(self, alias, pin):
        if alias in self.aliases and self.aliases[alias] != pin:
            raise error("Alias %s mapped to %s - can't alias to %s" % (
                alias, self.aliases[alias], pin))
        # Use a faster check for forbidden characters
        if any(c in pin for c in '^~!:') or pin.strip() != pin:
            raise error("Invalid pin alias '%s'\n" % (pin,))
        
        # Resolve transitive aliases immediately to flatten the map
        final_pin = self.aliases.get(pin, pin)
        self.aliases[alias] = final_pin
        
        # Update existing aliases that pointed to this alias
        for existing_alias, existing_pin in self.aliases.items():
            if existing_pin == alias:
                self.aliases[existing_alias] = final_pin
        
        # Invalidate cache when aliases change
        self._alias_cache.clear()

    def update_command(self, cmd: str) -> str:
        # TUXEDO_RT: Highly optimized pin fixup with caching
        def pin_fixup(m):
            name = m.group('name')
            # Fast path: check alias cache
            if name in self._alias_cache:
                return m.group('prefix') + self._alias_cache[name]
            
            pin_id = self.aliases.get(name, name)
            
            if self.validate_aliases:
                active_name = self.active_pins.get(pin_id)
                if active_name is None:
                    self.active_pins[pin_id] = name
                elif name != active_name:
                    raise error("pin %s is an alias for %s" % (
                        name, active_name))
            
            if pin_id in self.reserved:
                raise error("pin %s is reserved for %s" % (
                    name, self.reserved[pin_id]))
            
            res = str(pin_id)
            self._alias_cache[name] = res
            return m.group('prefix') + res
            
        return re_pin.sub(pin_fixup, cmd)


######################################################################
# Pin to chip mapping
######################################################################

class PrinterPins:
    error = error
    def __init__(self):
        self.chips = {}
        self.active_pins = {}
        self.pin_resolvers = {}
        self.allow_multi_use_pins = {}
        self._parse_cache = {}
        # TUXEDO_RT: New state monitoring
        self.pin_states = {}

    def parse_pin(self, pin_desc: str, can_invert=False, can_pullup=False) -> Dict[str, Any]:
        # TUXEDO_RT: Robust caching for pin parsing
        cache_key = (pin_desc, can_invert, can_pullup)
        if cache_key in self._parse_cache:
            return self._parse_cache[cache_key].copy()
            
        desc = pin_desc.strip()
        pullup = invert = 0
        
        # Optimization: Process prefixes more efficiently
        while desc:
            if can_pullup and desc[0] == '^':
                pullup = 1
                desc = desc[1:].lstrip()
            elif can_pullup and desc[0] == '~':
                pullup = -1
                desc = desc[1:].lstrip()
            elif can_invert and desc[0] == '!':
                invert = 1
                desc = desc[1:].lstrip()
            else:
                break

        if ':' not in desc:
            chip_name, pin = 'mcu', desc
        else:
            parts = desc.split(':', 1)
            chip_name, pin = parts[0].strip(), parts[1].strip()
            
        if chip_name not in self.chips:
            raise error("Unknown pin chip name '%s'" % (chip_name,))
            
        # Fast validation
        if any(c in pin for c in '^~!:') or ' ' in pin:
            format_str = ""
            if can_pullup: format_str += "[^~] "
            if can_invert: format_str += "[!] "
            raise error("Invalid pin description '%s'\nFormat is: %s[chip_name:] pin_name" 
                        % (pin_desc, format_str))
            
        pin_params = {
            'chip': self.chips[chip_name], 
            'chip_name': chip_name,
            'pin': pin, 
            'invert': invert, 
            'pullup': pullup
        }
        self._parse_cache[cache_key] = pin_params
        return pin_params.copy()

    def lookup_pin(self, pin_desc, can_invert=False, can_pullup=False,
                   share_type=None):
        pin_params = self.parse_pin(pin_desc, can_invert, can_pullup)
        pin = pin_params['pin']
        chip_name = pin_params['chip_name']
        share_name = f"{chip_name}:{pin}"
        
        if share_name in self.active_pins:
            share_params = self.active_pins[share_name]
            if share_name in self.allow_multi_use_pins:
                pass
            elif share_type is None or share_type != share_params['share_type']:
                raise error("pin %s used multiple times in config" % (pin,))
            elif (pin_params['invert'] != share_params['invert']
                  or pin_params['pullup'] != share_params['pullup']):
                raise error("Shared pin %s must have same polarity" % (pin,))
            return share_params
            
        pin_params['share_type'] = share_type
        self.active_pins[share_name] = pin_params
        return pin_params

    def setup_pin(self, pin_type, pin_desc):
        can_invert = pin_type in ['endstop', 'digital_out', 'pwm']
        can_pullup = pin_type in ['endstop']
        pin_params = self.lookup_pin(pin_desc, can_invert, can_pullup,
                                     share_type=pin_type)
        chip = pin_params['chip']
        
        # TUXEDO_RT: Advanced pin validation if supported by chip
        if hasattr(chip, 'validate_pin_type'):
            chip.validate_pin_type(pin_params['pin'], pin_type)
            
        # Record initial state for monitoring
        share_name = f"{pin_params['chip_name']}:{pin_params['pin']}"
        self.pin_states[share_name] = {'type': pin_type, 'status': 'initialized'}
        
        return chip.setup_pin(pin_type, pin_params)

    # --- NEW FUNCTIONALITY: Hardware Map Generation ---
    def generate_hardware_map(self) -> Dict[str, Any]:
        """
        Generates a structured map of all chips and their active pins.
        Useful for debugging and UI visualization.
        """
        h_map = {}
        for chip_name, chip in self.chips.items():
            h_map[chip_name] = {
                'type': type(chip).__name__,
                'pins': []
            }
        
        for share_name, params in self.active_pins.items():
            chip_name = params['chip_name']
            h_map[chip_name]['pins'].append({
                'pin': params['pin'],
                'type': params['share_type'],
                'invert': bool(params['invert']),
                'pullup': params['pullup']
            })
        return h_map

    # --- NEW FUNCTIONALITY: Real-time Monitoring ---
    def update_pin_status(self, pin_desc: str, status: str):
        """Updates the monitoring status of a specific pin."""
        try:
            params = self.parse_pin(pin_desc)
            share_name = f"{params['chip_name']}:{params['pin']}"
            if share_name in self.pin_states:
                self.pin_states[share_name]['status'] = status
        except Exception:
            pass

    def get_debug_info(self):
        """Extended debug info with TUXEDO_RT monitoring data."""
        return {
            'chips': list(self.chips.keys()),
            'active_pins': self.active_pins,
            'pin_states': self.pin_states,
            'hardware_map': self.generate_hardware_map()
        }

    def reset_pin_sharing(self, pin_params):
        share_name = f"{pin_params['chip_name']}:{pin_params['pin']}"
        if share_name in self.active_pins:
            del self.active_pins[share_name]
        if share_name in self.pin_states:
            del self.pin_states[share_name]
        self._parse_cache.clear()

    def get_pin_resolver(self, chip_name):
        if chip_name not in self.pin_resolvers:
            raise error("Unknown chip name '%s'" % (chip_name,))
        return self.pin_resolvers[chip_name]

    def register_chip(self, chip_name, chip):
        chip_name = chip_name.strip()
        if chip_name in self.chips:
            raise error("Duplicate chip name '%s'" % (chip_name,))
        self.chips[chip_name] = chip
        self.pin_resolvers[chip_name] = PinResolver()

    def allow_multi_use_pin(self, pin_desc):
        pin_params = self.parse_pin(pin_desc)
        share_name = f"{pin_params['chip_name']}:{pin_params['pin']}"
        self.allow_multi_use_pins[share_name] = True

def add_printer_objects(config):
    config.get_printer().add_object('pins', PrinterPins())

def add_printer_objects(config):
    config.get_printer().add_object('pins', PrinterPins())
