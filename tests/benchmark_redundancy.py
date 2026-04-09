import sys
import os
import time

# Asegurar que klippy esté en el path
sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))

def benchmark_gcode():
    print("--- Benchmark: G-Code Parsing ---")
    try:
        import gcode
        class DummyPrinter:
            def get_start_args(self): return {}
            def register_event_handler(self, event, handler): pass
            def get_reactor(self): 
                class DummyReactor:
                    def mutex(self): return None
                return DummyReactor()
            def config_error(self, msg): return Exception(msg)
            def invoke_shutdown(self, msg): pass
            def send_event(self, ev): pass
            def get_state_message(self): return ("Printer ready", "")
            
        printer = DummyPrinter()
        dispatch = gcode.GCodeDispatch(printer)
        
        def dummy_handler(gcmd):
            pass
        dispatch.register_command("M105", dummy_handler)
        dispatch._handle_ready()
        
        # Comandos simulados
        commands = [b"M105 X10.5 Y20.5 Z30.5 E40.5 *12\n" for _ in range(100000)]
        
        # Test Legacy
        start = time.time()
        dispatch._process_commands_legacy(commands, need_ack=False)
        legacy_time = time.time() - start
        print(f"Legacy Python Parsing (100,000 cmds): {legacy_time:.5f} s")
        
        # Test Fast
        if dispatch._c_parser:
            start = time.time()
            dispatch._process_commands_fast(commands, need_ack=False)
            fast_time = time.time() - start
            print(f"Fast C Parsing (100,000 cmds): {fast_time:.5f} s")
            if fast_time > 0:
                print(f"Speedup: {legacy_time / fast_time:.2f}x")
        else:
            print("C Parser no disponible. Se omitió la prueba rápida.")
    except Exception as e:
        print("GCode benchmark failed:", e)

def benchmark_msgproto():
    print("\n--- Benchmark: Protocol VLQ Encoding ---")
    try:
        import msgproto
        pt = msgproto.PT_uint32()
        
        out_legacy = bytearray()
        out_fast = bytearray()
        
        original_msgblock = msgproto.msgblock_encode_int
        
        # Test Legacy
        msgproto.msgblock_encode_int = None
        start = time.time()
        for i in range(100000):
            pt.encode(out_legacy, i)
        legacy_time = time.time() - start
        print(f"Legacy Python Encoding (100,000 ints): {legacy_time:.5f} s")
        
        # Test Fast
        msgproto.msgblock_encode_int = original_msgblock
        if original_msgblock:
            start = time.time()
            for i in range(100000):
                pt.encode(out_fast, i)
            fast_time = time.time() - start
            print(f"Fast C Encoding (100,000 ints): {fast_time:.5f} s")
            if fast_time > 0:
                print(f"Speedup: {legacy_time / fast_time:.2f}x")
        else:
            print("C msgblock no disponible. Se omitió la prueba rápida.")
    except Exception as e:
        print("Msgproto benchmark failed:", e)

if __name__ == '__main__':
    benchmark_gcode()
    benchmark_msgproto()
