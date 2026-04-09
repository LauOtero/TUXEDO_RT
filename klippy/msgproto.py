# Protocol definitions for firmware communication
#
# Copyright (C) 2016-2024  Kevin O'Connor <kevin@koconnor.net>
#
# This file may be distributed under the terms of the GNU GPLv3 license.
import json, zlib, logging
from typing import Dict, List, Tuple, Any, Optional, Union, Callable
import chelper

DefaultMessages = {
    "identify_response offset=%u data=%.*s": 0,
    "identify offset=%u count=%c": 1,
}

MESSAGE_MIN = 5
MESSAGE_MAX_V1 = 64
MESSAGE_MAX_V2 = 4096
MESSAGE_MAX = MESSAGE_MAX_V2
MESSAGE_HEADER_SIZE_V1  = 2
MESSAGE_HEADER_SIZE_V2  = 4
MESSAGE_TRAILER_SIZE = 3
MESSAGE_POS_LEN = 0
MESSAGE_POS_SEQ_V1 = 1
MESSAGE_TRAILER_CRC  = 3
MESSAGE_TRAILER_SYNC = 1
MESSAGE_PAYLOAD_MAX = MESSAGE_MAX - MESSAGE_MIN
MESSAGE_SEQ_MASK = 0x0f
MESSAGE_DEST = 0x10
MESSAGE_SYNC = 0x7e
MESSAGE_SYNC_V2 = 0x7f

class error(Exception):
    pass

# TUXEDO_RT: Fast CRC and packet check using chelper
try:
    ffi_main, ffi_lib = chelper.get_ffi()
    if ffi_lib is None:
        raise Exception("No C library found")
    def _coerce_buffer(buf: Union[bytes, bytearray, List[int]]) -> bytes:
        if isinstance(buf, (list, bytearray)):
            return bytes(buf)
        return buf
    def crc16_ccitt(buf: bytes) -> List[int]:
        """Calculate CRC16 CCITT for the given buffer using C helper."""
        buf = _coerce_buffer(buf)
        crc = ffi_lib.msgblock_crc16_ccitt(buf, len(buf))
        return [crc >> 8, crc & 0xff]
    def msgblock_encode_int(out: bytearray, v: int) -> None:
        """Encode an integer using Klipper VLQ format into a bytearray."""
        tmp = ffi_main.new("uint8_t[10]")
        p = ffi_lib.msgblock_encode_int(tmp, v & 0xffffffff)
        out.extend(tmp[0:p-tmp])
    def msgblock_parse_int(s: bytes, pos: int) -> Tuple[int, int]:
        """Parse a Klipper VLQ integer from a buffer at a given position."""
        s = _coerce_buffer(s)
        p_ptr = ffi_main.new("uint8_t **")
        buf = ffi_main.from_buffer(s[pos:])
        p_ptr[0] = ffi_main.cast("uint8_t *", buf)
        v = ffi_lib.msgblock_parse_int(p_ptr)
        new_pos = pos + (p_ptr[0] - ffi_main.cast("uint8_t *", buf))
        return v, new_pos
except:
    # TUXEDO_RT: Fast CRC16 CCITT lookup table for pure-python fallback
    _CRC_TABLE = []
    for i in range(256):
        data = i
        data ^= (data & 0x0f) << 4
        _CRC_TABLE.append(((data << 8) ^ (data >> 4) ^ (data << 3)) & 0xffff)
    
    def crc16_ccitt(buf: bytes) -> List[int]:
        """Calculate CRC16 CCITT for the given buffer using Python fallback."""
        crc = 0xffff
        # TUXEDO_RT: Ensure we have a bytes-like object for memoryview
        if isinstance(buf, list):
            buf = bytes(buf)
        mv = memoryview(buf)
        tab = _CRC_TABLE
        for data in mv:
            crc = (crc >> 8) ^ tab[data ^ (crc & 0xff)]
        return [(crc >> 8) & 0xff, crc & 0xff]
    msgblock_encode_int = None
    msgblock_parse_int = None

class PT_uint32:
    """Klipper protocol type for unsigned 32-bit integers."""
    is_int = True
    is_dynamic_string = False
    max_length = 5
    signed = False
    # TUXEDO_RT: Cache some calculations
    _encode_limits = [
        (0xc000000, -0x4000000, 28),
        (0x180000, -0x80000, 21),
        (0x3000, -0x1000, 14),
        (0x60, -0x20, 7)
    ]
    def encode(self, out: bytearray, v: int) -> None:
        """Encode an integer into the output buffer."""
        if msgblock_encode_int is not None:
            msgblock_encode_int(out, v)
            return
        raise RuntimeError("TUXEDO_RT: msgblock_encode_int C extension is required for RT determinism.")

    def parse(self, s: bytes, pos: int) -> Tuple[int, int]:
        """Parse an integer from the buffer."""
        if msgblock_parse_int is not None:
            v, pos = msgblock_parse_int(s, pos)
            if not self.signed:
                v = int(v & 0xffffffff)
            return v, pos
        raise RuntimeError("TUXEDO_RT: msgblock_parse_int C extension is required for RT determinism.")

class PT_int32(PT_uint32):
    """Klipper protocol type for signed 32-bit integers."""
    signed = True
class PT_uint16(PT_uint32):
    """Klipper protocol type for unsigned 16-bit integers."""
    max_length = 3
class PT_int16(PT_int32):
    """Klipper protocol type for signed 16-bit integers."""
    signed = True
    max_length = 3
class PT_byte(PT_uint32):
    """Klipper protocol type for 8-bit bytes."""
    max_length = 2

class PT_bool(PT_byte):
    """Klipper protocol type for booleans."""
    # TUXEDO_RT: Boolean type for easier logic handling
    def encode(self, out: bytearray, v: bool) -> None:
        PT_byte.encode(self, out, 1 if v else 0)
    def parse(self, s: bytes, pos: int) -> Tuple[bool, int]:
        v, pos = PT_byte.parse(self, s, pos)
        return bool(v), pos

class PT_string:
    """Klipper protocol type for dynamic strings."""
    is_int = False
    is_dynamic_string = True
    max_length = 64
    def encode(self, out: bytearray, v: bytes) -> None:
        """Encode a string into the output buffer."""
        out.append(len(v))
        out.extend(bytearray(v))
    def parse(self, s: bytes, pos: int) -> Tuple[bytes, int]:
        """Parse a string from the buffer."""
        l = s[pos]
        return bytes(bytearray(s[pos+1:pos+l+1])), pos+l+1
class PT_progmem_buffer(PT_string):
    """Klipper protocol type for progmem buffers."""
    pass
class PT_buffer(PT_string):
    """Klipper protocol type for generic buffers."""
    pass

class enumeration_error(error):
    """Error raised when an enumeration value is unknown."""
    def __init__(self, enum_name: str, value: Any) -> None:
        self.enum_name = enum_name
        self.value = value
        error.__init__(self, "Unknown value '%s' in enumeration '%s'"
                       % (value, enum_name))
    def get_enum_params(self) -> Tuple[str, Any]:
        """Return the enumeration name and value."""
        return self.enum_name, self.value

class Enumeration:
    """Klipper protocol type for enumerations."""
    is_int = False
    is_dynamic_string = False
    def __init__(self, pt: Any, enum_name: str, enums: Dict[str, int]) -> None:
        self.pt = pt
        self.max_length = pt.max_length
        self.enum_name = enum_name
        self.enums = enums
        self.reverse_enums = {v: k for k, v in enums.items()}
    def encode(self, out: bytearray, v: str) -> None:
        """Encode an enumeration value into the output buffer."""
        tv = self.enums.get(v)
        if tv is None:
            raise enumeration_error(self.enum_name, v)
        self.pt.encode(out, tv)
    def parse(self, s: bytes, pos: int) -> Tuple[str, int]:
        """Parse an enumeration value from the buffer."""
        v, pos = self.pt.parse(s, pos)
        tv = self.reverse_enums.get(v)
        if tv is None:
            tv = "?%d" % (v,)
        return tv, pos


MessageTypes = {
    '%u': PT_uint32(), '%i': PT_int32(),
    '%hu': PT_uint16(), '%hi': PT_int16(),
    '%c': PT_byte(), '%b': PT_bool(),
    '%s': PT_string(), '%.*s': PT_progmem_buffer(), '%*s': PT_buffer(),
}

# Lookup the message types for a format string
def lookup_params(msgformat: str, enumerations: Dict[str, Dict[str, int]] = {}) -> List[Tuple[str, Any]]:
    """
    Parse a message format string and return a list of parameter names and types.
    """
    out = []
    argparts = [arg.split('=') for arg in msgformat.split()[1:]]
    for name, fmt in argparts:
        pt = MessageTypes[fmt]
        for enum_name, enums in enumerations.items():
            if name == enum_name or name.endswith('_' + enum_name):
                pt = Enumeration(pt, enum_name, enums)
                break
        out.append((name, pt))
    return out

# Lookup the message types for a debugging "output()" format string
def lookup_output_params(msgformat: str) -> List[Any]:
    """
    Parse an output format string and return a list of parameter types.
    """
    param_types = []
    args = msgformat
    while 1:
        pos = args.find('%')
        if pos < 0:
            break
        if pos+1 >= len(args) or args[pos+1] != '%':
            for i in range(4):
                t = MessageTypes.get(args[pos:pos+1+i])
                if t is not None:
                    param_types.append(t)
                    break
            else:
                raise error("Invalid output format for '%s'" % (msgformat,))
        args = args[pos+1:]
    return param_types

# Update the message format to be compatible with python's % operator
_MSG_FORMAT_REPLACEMENTS = [
    ('%u', '%s'), ('%i', '%s'), ('%hu', '%s'), ('%hi', '%s'),
    ('%c', '%s'), ('%b', '%s'), ('%.*s', '%s'), ('%*s', '%s')
]
def convert_msg_format(msgformat: str) -> str:
    """
    Convert Klipper message format to Python-compatible format string.
    """
    # TUXEDO_RT: Optimized bulk replacement
    for old, new in _MSG_FORMAT_REPLACEMENTS:
        msgformat = msgformat.replace(old, new)
    return msgformat

class MessageFormat:
    """Helper for encoding and parsing a specific message format."""
    def __init__(self, msgid_bytes: bytes, msgformat: str, enumerations: Dict[str, Dict[str, int]] = {}):
        self.msgid_bytes = msgid_bytes
        self.msgformat = msgformat
        self.debugformat = convert_msg_format(msgformat)
        self.name = msgformat.split()[0]
        self.param_names = lookup_params(msgformat, enumerations)
        self.param_types = [t for name, t in self.param_names]
        self.name_to_type = dict(self.param_names)
        # TUXEDO_RT: Cache encode methods for speed
        self._encoders = [t.encode for t in self.param_types]
        self._parsers = [(name, t.parse) for name, t in self.param_names]
        self._num_params = len(self.param_types)
        # TUXEDO_RT: Cache encoded messages for frequently sent commands
        self._encode_cache: Dict[Tuple[Any, ...], Tuple[int, ...]] = {}
    def encode(self, params: List[Any]) -> List[int]:
        """Encode message parameters into a list of bytes."""
        # TUXEDO_RT: Optimized encoding using cached encoders and message cache
        tp = tuple(params)
        res = self._encode_cache.get(tp)
        if res is not None:
            return list(res)
        # TUXEDO_RT: Use bytearray for faster appending
        out = bytearray(self.msgid_bytes)
        for i in range(self._num_params):
            self._encoders[i](out, params[i])
        
        # Cache as tuple (immutable)
        if len(self._encode_cache) < 500: # Increased cache size
            self._encode_cache[tp] = tuple(out)
        return list(out)
    def encode_by_name(self, **params: Any) -> List[int]:
        """Encode message parameters by name into a list of bytes."""
        # TUXEDO_RT: Optimized encoding using cached encoders
        out = bytearray(self.msgid_bytes)
        for name, t in self.param_names:
            t.encode(out, params[name])
        return list(out)
    def parse(self, s: bytes, pos: int) -> Tuple[Dict[str, Any], int]:
        """Parse a message from the buffer."""
        # TUXEDO_RT: Optimized parsing using cached parsers
        pos += len(self.msgid_bytes)
        out = {}
        for name, parse in self._parsers:
            v, pos = parse(s, pos)
            out[name] = v
        return out, pos
    def parse_into(self, out: Dict[str, Any], s: bytes, pos: int) -> int:
        """Parse a message from the buffer directly into an existing dictionary."""
        # TUXEDO_RT: High-performance parsing into an existing dictionary
        pos += len(self.msgid_bytes)
        for name, parse in self._parsers:
            v, pos = parse(s, pos)
            out[name] = v
        return pos
    def format_params(self, params: Dict[str, Any]) -> str:
        """Format message parameters into a human-readable string."""
        out = []
        for name, t in self.param_names:
            v = params[name]
            if t.is_dynamic_string:
                v = repr(v)
            out.append(v)
        return self.debugformat % tuple(out)

class OutputFormat:
    """Helper for parsing MCU 'output()' messages."""
    name = '#output'
    def __init__(self, msgid_bytes: bytes, msgformat: str):
        self.msgid_bytes = msgid_bytes
        self.msgformat = msgformat
        self.debugformat = convert_msg_format(msgformat)
        self.param_types = lookup_output_params(msgformat)
    def parse(self, s: bytes, pos: int) -> Tuple[Dict[str, Any], int]:
        """Parse an output message from the buffer."""
        pos += len(self.msgid_bytes)
        out = []
        for t in self.param_types:
            v, pos = t.parse(s, pos)
            if t.is_dynamic_string:
                v = repr(v)
            out.append(v)
        outmsg = self.debugformat % tuple(out)
        return {'#msg': outmsg}, pos
    def parse_into(self, out: Dict[str, Any], s: bytes, pos: int) -> int:
        """Parse an output message directly into an existing dictionary."""
        pos += len(self.msgid_bytes)
        args = []
        for t in self.param_types:
            v, pos = t.parse(s, pos)
            if t.is_dynamic_string:
                v = repr(v)
            args.append(v)
        out['#msg'] = self.debugformat % tuple(args)
        return pos
    def format_params(self, params: Dict[str, Any]) -> str:
        """Format output message parameters into a human-readable string."""
        return "#output %s" % (params['#msg'],)

class UnknownFormat:
    """Fallback helper for unknown message formats."""
    name = '#unknown'
    def parse(self, s: bytes, pos: int) -> Tuple[Dict[str, Any], int]:
        """Parse an unknown message from the buffer."""
        msgid, param_pos = PT_int32().parse(s, pos)
        msg = bytes(bytearray(s))
        return {'#msgid': msgid, '#msg': msg}, len(s)-MESSAGE_TRAILER_SIZE
    def parse_into(self, out: Dict[str, Any], s: bytes, pos: int) -> int:
        """Parse an unknown message directly into an existing dictionary."""
        msgid, param_pos = PT_int32().parse(s, pos)
        out['#msgid'] = msgid
        out['#msg'] = bytes(bytearray(s))
        return len(s)-MESSAGE_TRAILER_SIZE
    def format_params(self, params: Dict[str, Any]) -> str:
        """Format unknown message parameters into a human-readable string."""
        return "#unknown %s" % (repr(params['#msg']),)


class MessageParser:
    error = error
    def __init__(self, warn_prefix: str = "") -> None:
        """
        Initialize the MessageParser.
        
        :param warn_prefix: Optional prefix for warning/error messages.
        """
        self.warn_prefix = warn_prefix
        self.unknown = UnknownFormat()
        self.enumerations: Dict[Any, Dict[str, int]] = {}
        self.messages: List[Tuple[int, str, str]] = []
        self.messages_by_id: Dict[int, Union[MessageFormat, OutputFormat, UnknownFormat]] = {}
        self.messages_by_name: Dict[str, Union[MessageFormat, OutputFormat]] = {}
        self.msgid_by_format: Dict[str, int] = {}
        self.msgid_parser = PT_int32()
        self.config: Dict[str, Any] = {}
        self.version = self.build_versions = ""
        self.raw_identify_data = b""
        self._init_messages(DefaultMessages)
        # TUXEDO_RT: Fast packet check using chelper
        self._need_sync: Any = None
        if 'ffi_main' in globals() and ffi_main is not None:
            self._need_sync = ffi_main.new('uint8_t *', 0)
    def _error(self, msg: str, *params: Any) -> None:
        """
        Raise a protocol error.
        
        :param msg: Error message format string.
        :param params: Format parameters.
        """
        raise error(self.warn_prefix + (msg % params))
    def check_packet(self, s: bytes) -> int:
        """
        Verify if a buffer contains a valid Klipper protocol packet.
        
        :param s: The buffer to check.
        :return: Packet length if valid, 0 if more data needed, -1 if invalid.
        """
        if self._need_sync is not None:
            s = _coerce_buffer(s)
            return ffi_lib.msgblock_check(self._need_sync, s, len(s))
        if len(s) < MESSAGE_MIN:
            return 0
        is_v2 = s[MESSAGE_POS_LEN] == MESSAGE_SYNC_V2
        if is_v2:
            if len(s) < MESSAGE_HEADER_SIZE_V2 + MESSAGE_TRAILER_SIZE:
                return 0
            msglen = (s[1] << 8) | s[2]
            if msglen < MESSAGE_HEADER_SIZE_V2 + MESSAGE_TRAILER_SIZE or msglen > MESSAGE_MAX_V2:
                return -1
            msgseq = s[3]
        else:
            msglen = s[MESSAGE_POS_LEN]
            if msglen < MESSAGE_MIN or msglen > MESSAGE_MAX_V1:
                return -1
            msgseq = s[MESSAGE_POS_SEQ_V1]
            
        if (msgseq & ~MESSAGE_SEQ_MASK) != MESSAGE_DEST:
            return -1
        if len(s) < msglen:
            # Need more data
            return 0
        if s[msglen-MESSAGE_TRAILER_SYNC] != MESSAGE_SYNC:
            return -1
        msgcrc = s[msglen-MESSAGE_TRAILER_CRC:msglen-MESSAGE_TRAILER_CRC+2]
        crc = crc16_ccitt(s[:msglen-MESSAGE_TRAILER_SIZE])
        if crc != list(msgcrc):
            #logging.debug("got crc %s vs %s", repr(crc), repr(msgcrc))
            return -1
        return msglen
    def dump(self, s: bytes) -> List[str]:
        """
        Deconstruct a message block into a list of human-readable strings.
        
        :param s: The message block buffer.
        :return: List of formatted strings representing the message contents.
        """
        is_v2 = s[MESSAGE_POS_LEN] == MESSAGE_SYNC_V2
        msgseq = s[3] if is_v2 else s[MESSAGE_POS_SEQ_V1]
        header_size = MESSAGE_HEADER_SIZE_V2 if is_v2 else MESSAGE_HEADER_SIZE_V1
        
        out = ["seq: %02x" % (msgseq,)]
        pos = header_size
        while 1:
            msgid, param_pos = self.msgid_parser.parse(s, pos)
            mid = self.messages_by_id.get(msgid, self.unknown)
            params, pos = mid.parse(s, pos)
            out.append(mid.format_params(params))
            if pos >= len(s)-MESSAGE_TRAILER_SIZE:
                break
        return out
    def format_params(self, params: Dict[str, Any]) -> str:
        """
        Format message parameters into a human-readable string.
        
        :param params: Dictionary of parsed message parameters.
        :return: Formatted string.
        """
        name = params.get('#name')
        mid = self.messages_by_name.get(name)
        if mid is not None:
            return mid.format_params(params)
        msg = params.get('#msg')
        if msg is not None:
            return "%s %s" % (name, msg)
        return str(params)
    def parse(self, s: bytes) -> Dict[str, Any]:
        """
        Parse a complete message packet into a dictionary.
        
        :param s: The packet buffer.
        :return: Dictionary containing parsed parameters.
        """
        is_v2 = s[MESSAGE_POS_LEN] == MESSAGE_SYNC_V2
        header_size = MESSAGE_HEADER_SIZE_V2 if is_v2 else MESSAGE_HEADER_SIZE_V1
        
        msgid, param_pos = self.msgid_parser.parse(s, header_size)
        mid = self.messages_by_id.get(msgid, self.unknown)
        params, pos = mid.parse(s, header_size)
        if pos != len(s)-MESSAGE_TRAILER_SIZE:
            self._error("Extra data at end of message")
        params['#name'] = mid.name
        return params
    def parse_into(self, out: Dict[str, Any], s: bytes) -> None:
        """
        Parse a message packet directly into an existing dictionary.
        
        :param out: Destination dictionary.
        :param s: The packet buffer.
        """
        is_v2 = s[MESSAGE_POS_LEN] == MESSAGE_SYNC_V2
        header_size = MESSAGE_HEADER_SIZE_V2 if is_v2 else MESSAGE_HEADER_SIZE_V1
        
        # TUXEDO_RT: Optimized parsing directly into pooled dictionary
        msgid, param_pos = self.msgid_parser.parse(s, header_size)
        mid = self.messages_by_id.get(msgid, self.unknown)
        pos = mid.parse_into(out, s, header_size)
        if pos != len(s)-MESSAGE_TRAILER_SIZE:
            self._error("Extra data at end of message")
        out['#name'] = mid.name
    def encode_msgblock(self, seq: int, cmd: List[int]) -> List[int]:
        """
        Encode a command into a protocol message block.
        
        :param seq: Sequence number.
        :param cmd: Encoded command bytes.
        :return: Complete message block as a list of bytes.
        """
        # TUXEDO_RT: Optimized msgblock encoding
        msglen = len(cmd) + MESSAGE_TRAILER_SIZE
        seq = (seq & MESSAGE_SEQ_MASK) | MESSAGE_DEST
        
        if msglen + MESSAGE_HEADER_SIZE_V1 <= MESSAGE_MAX_V1:
            msglen += MESSAGE_HEADER_SIZE_V1
            out = [msglen, seq]
        else:
            msglen += MESSAGE_HEADER_SIZE_V2
            out = [MESSAGE_SYNC_V2, msglen >> 8, msglen & 0xFF, seq]
            
        out.extend(cmd)
        out.extend(crc16_ccitt(bytes(out)))
        out.append(MESSAGE_SYNC)
        return out
    def _parse_buffer(self, value: str) -> List[int]:
        """
        Parse a hex string into a list of bytes.
        
        :param value: Hexadecimal string.
        :return: List of integers.
        """
        if not value:
            return []
        tval = int(value, 16)
        out = []
        for i in range(len(value) // 2):
            out.append(tval & 0xff)
            tval >>= 8
        out.reverse()
        return out
    def lookup_command(self, msgformat: str) -> Union[MessageFormat, OutputFormat]:
        """
        Lookup a message format by its format string.
        
        :param msgformat: The format string.
        :return: The message format helper.
        """
        parts = msgformat.strip().split()
        msgname = parts[0]
        mp = self.messages_by_name.get(msgname)
        if mp is None:
            self._error("Unknown command: %s", msgname)
        if msgformat != mp.msgformat:
            self._error("Command format mismatch: %s vs %s",
                        msgformat, mp.msgformat)
        return mp
    def lookup_msgid(self, msgformat: str) -> int:
        """
        Lookup the message ID for a given format string.
        
        :param msgformat: The format string.
        :return: The integer message ID.
        """
        msgid = self.msgid_by_format.get(msgformat)
        if msgid is None:
            self._error("Unknown command: %s", msgformat)
        return msgid
    def create_command(self, msg: str) -> List[int]:
        """
        Encode a text command into its protocol byte representation.
        
        :param msg: The command string.
        :return: List of encoded bytes.
        """
        # TUXEDO_RT: Fast command creation with caching
        parts = msg.strip().split()
        if not parts:
            return []
        msgname = parts[0]
        mp = self.messages_by_name.get(msgname)
        if mp is None:
            self._error("Unknown command: %s", msgname)
        
        # Fast path: if no parameters, return cached msgid_bytes
        if len(parts) == 1:
            return list(mp.msgid_bytes)
        
        # Optimization: pre-calculate arg dictionary to avoid repetitive lookups
        try:
            argparts = dict(arg.split('=', 1) for arg in parts[1:])
            params = []
            for name, t in mp.param_names:
                value = argparts.get(name)
                if value is None:
                    self._error("Missing parameter '%s' for '%s'", name, msgname)
                if t.is_dynamic_string:
                    tval = self._parse_buffer(value)
                elif t.is_int:
                    try:
                        tval = int(value, 0)
                    except ValueError:
                        # Maybe an enumeration?
                        enum = self.enumerations.get(t)
                        if enum is not None:
                            tval = enum.get(value)
                            if tval is None:
                                self._error("Unknown enum value '%s' for '%s'", value, name)
                        else:
                            self._error("Invalid integer '%s' for '%s'", value, name)
                else:
                    tval = value
                params.append(tval)
            return mp.encode(params)
        except (ValueError, KeyError) as e:
            self._error("Error parsing command '%s': %s", msgname, str(e))
    def create_dummy_response(self, msgname: str, params: Dict[str, Any] = {}) -> Dict[str, Any]:
        """
        Create a dummy parsed message for testing or simulation.
        
        :param msgname: Name of the message.
        :param params: Initial parameters.
        :return: Complete parsed message dictionary.
        """
        mp = self.messages_by_name.get(msgname)
        if mp is None:
            self._error("Unknown response: %s", msgname)
        argparts = dict(params)
        for name, t in mp.name_to_type.items():
            if name not in argparts:
                tval: Any = 0
                if t.is_dynamic_string:
                    tval = ()
                argparts[name] = tval
        try:
            msg = mp.encode_by_name(**argparts)
        except error as e:
            raise
        except:
            #logging.exception("Unable to encode")
            self._error("Unable to encode: %s", msgname)
        res, pos = mp.parse(bytes(msg), 0)
        res['#name'] = msgname
        return res
    def fill_enumerations(self, enumerations: Dict[str, Any]) -> None:
        """
        Populate the enumeration mappings from MCU data.
        
        :param enumerations: Dictionary of enumerations.
        """
        for add_name, add_enums in enumerations.items():
            enums = self.enumerations.setdefault(add_name, {})
            for enum, value in add_enums.items():
                if type(value) == type(0):
                    # Simple enumeration
                    enums[str(enum)] = value
                    continue
                # Enumeration range
                enum = enum_root = str(enum)
                while enum_root and enum_root[-1].isdigit():
                    enum_root = enum_root[:-1]
                start_enum = 0
                if len(enum_root) != len(enum):
                    start_enum = int(enum[len(enum_root):])
                start_value, count = value
                for i in range(count):
                    enums[enum_root + str(start_enum + i)] = start_value + i
    def _init_messages(self, messages: Dict[str, int], command_ids: List[int] = [], output_ids: List[int] = []) -> None:
        """
        Initialize internal message format mappings.
        
        :param messages: Dictionary of format strings to IDs.
        :param command_ids: List of IDs that are commands.
        :param output_ids: List of IDs that are output messages.
        """
        for msgformat, msgid in messages.items():
            msgtype = 'response'
            if msgid in command_ids:
                msgtype = 'command'
            elif msgid in output_ids:
                msgtype = 'output'
            self.messages.append((msgid, msgtype, msgformat))
            self.msgid_by_format[msgformat] = msgid
            mid_bytes_ba = bytearray()
            self.msgid_parser.encode(mid_bytes_ba, msgid)
            mid_bytes = bytes(mid_bytes_ba)
            if msgtype == 'output':
                self.messages_by_id[msgid] = OutputFormat(mid_bytes,
                                                          msgformat)
            else:
                msg = MessageFormat(mid_bytes, msgformat, self.enumerations)
                self.messages_by_id[msgid] = msg
                self.messages_by_name[msg.name] = msg
    def process_identify(self, data: bytes, decompress: bool = True) -> None:
        """
        Process the MCU identification data and configure the parser.
        
        :param data: Identification data bytes.
        :param decompress: Whether to decompress the data using zlib.
        """
        try:
            if decompress:
                data = zlib.decompress(data)
            self.raw_identify_data = data
            data_dict = json.loads(data)
            self.fill_enumerations(data_dict.get('enumerations', {}))
            commands = data_dict.get('commands', {})
            responses = data_dict.get('responses', {})
            output = data_dict.get('output', {})
            all_messages = dict(commands)
            all_messages.update(responses)
            all_messages.update(output)
            self._init_messages(all_messages, list(commands.values()),
                                list(output.values()))
            self.config.update(data_dict.get('config', {}))
            self.version = data_dict.get('version', '')
            self.build_versions = data_dict.get('build_versions', '')
        except error as e:
            raise
        except Exception as e:
            logging.exception("process_identify error")
            self._error("Error during identify: %s", str(e))
    def get_raw_data_dictionary(self) -> bytes:
        """Return the raw identification data."""
        return self.raw_identify_data
    def get_version_info(self) -> Tuple[str, str]:
        """Return the MCU version and build versions."""
        return self.version, self.build_versions
    def get_messages(self) -> List[Tuple[int, str, str]]:
        """Return a list of all known messages."""
        return list(self.messages)
    def get_enumerations(self) -> Dict[Any, Dict[str, int]]:
        """Return the dictionary of enumerations."""
        return dict(self.enumerations)
    def get_constants(self) -> Dict[str, Any]:
        """Return the dictionary of firmware constants."""
        return dict(self.config)
    class sentinel: pass
    def get_constant(self, name: str, default: Any = sentinel, parser: Callable[[Any], Any] = str) -> Any:
        """
        Fetch a firmware constant.
        
        :param name: Name of the constant.
        :param default: Default value if not found.
        :param parser: Function to parse the constant value.
        :return: The constant value.
        """
        if name not in self.config:
            if default is not self.sentinel:
                return default
            self._error("Firmware constant '%s' not found", name)
        try:
            value = parser(self.config[name])
        except:
            self._error("Unable to parse firmware constant %s: %s",
                        name, self.config[name])
        return value
    def get_constant_float(self, name: str, default: Any = sentinel) -> float:
        """Fetch a firmware constant as a float."""
        return self.get_constant(name, default, parser=float)
    def get_constant_int(self, name: str, default: Any = sentinel) -> int:
        """Fetch a firmware constant as an integer."""
        return self.get_constant(name, default, parser=int)
