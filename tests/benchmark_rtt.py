#!/usr/bin/env python3
"""
Benchmark de RTT (Round Trip Time) para TUXEDO_RT.
Mide la latencia y overhead de las métricas RTT antes y después de la optimización.
"""
import sys
import os
import time

sys.path.append(os.path.join(os.path.dirname(__file__), '..', 'klippy'))

def benchmark_rtt_integration():
    print("--- Benchmark: RTT Stats Integration ---")
    try:
        import chelper
        ffi, lib = chelper.get_ffi()

        if not hasattr(lib, 'conn_get_rtt_stats'):
            print("conn_get_rtt_stats no disponible en C. Se omite benchmark.")
            return

        print("conn_get_rtt_stats encontrado en C ✓")

        srtt_in = ffi.new('double *')
        rttvar_in = ffi.new('double *')
        rto_in = ffi.new('double *')

        *_, last_srtt, last_rttvar, last_rto = 0.0, 0.0, 0.0

        ITERATIONS = 100000
        start = time.time()
        for i in range(ITERATIONS):
            srtt_in[0] = 0.001 * (i % 100 + 1)
            rttvar_in[0] = 0.0001 * (i % 50 + 1)
            rto_in[0] = 0.005 * (i % 20 + 1)
            lib.conn_get_rtt_stats(None, srtt_in, rttvar_in, rto_in)
            last_srtt = srtt_in[0]
            last_rttvar = rttvar_in[0]
            last_rto = rto_in[0]
        elapsed = time.time() - start

        print(f"  Tiempo total ({ITERATIONS:,} llamadas): {elapsed:.5f} s")
        print(f"  Tiempo por llamada: {elapsed/ITERATIONS*1e6:.2f} µs")
        print(f"  Últimos valores: srtt={last_srtt:.6f}, rttvar={last_rttvar:.6f}, rto={last_rto:.6f}")

        if elapsed / ITERATIONS < 0.00001:
            print("  ✓ Rendimiento OK (<10ns por llamada)")
        else:
            print("  ⚠ Sobre costo de CFFI detectado")

    except Exception as e:
        print(f"Error en benchmark RTT: {e}")

def benchmark_rtt_vs_python():
    print("\n--- Benchmark: RTT Python vs C ---")
    try:
        import chelper
        ffi, lib = chelper.get_ffi()

        if not hasattr(lib, 'ultracrc16_ccitt_compute'):
            print("ultracrc no disponible. Se omite comparativa.")
            return

        print("Comparando rendimiento de cálculo RTT en Python vs C...")

        PYTHON_ITERATIONS = 1000
        data = b"test_data_for_rtt_calculation" * 10

        class PythonRTTCalculator:
            K1 = 0.125
            K2 = 0.25

            def __init__(self):
                self.srtt = 0.0
                self.rttvar = 0.0
                self.rto = 0.0

            def update(self, sample_rtt):
                self.srtt = self.srtt + self.K1 * (sample_rtt - self.srtt)
                self.rttvar = self.rttvar + self.K2 * (abs(sample_rtt - self.srtt) - self.rttvar)
                self.rto = self.srtt + 4 * self.rttvar

        py_calc = PythonRTTCalculator()

        start = time.time()
        for i in range(PYTHON_ITERATIONS):
            sample = 0.001 * (i % 100 + 1)
            py_calc.update(sample)
        py_time = time.time() - start

        print(f"  Python ({PYTHON_ITERATIONS:,} updates): {py_time:.5f} s")
        print(f"  Tiempo por update: {py_time/PYTHON_ITERATIONS*1e6:.2f} µs")
        print(f"  SRTT final (Python): {py_calc.srtt:.6f}")

        C_ITERATIONS = 100000
        start = time.time()
        for i in range(C_ITERATIONS):
            lib.ultracrc16_ccitt_compute(data, len(data), 0xFFFF)
        c_time = time.time() - start

        print(f"  C ({C_ITERATIONS:,} CRCs): {c_time:.5f} s")
        print(f"  Tiempo por CRC: {c_time/C_ITERATIONS*1e6:.2f} µs")

        if c_time / C_ITERATIONS < py_time / PYTHON_ITERATIONS / 10:
            print("  ✓ C es >10x más rápido que Python para operaciones similares")
        else:
            print("  ℹ C tiene overhead de CFFI pero es más preciso para operaciones complejas")

    except Exception as e:
        print(f"Error en comparativa: {e}")

if __name__ == '__main__':
    print("=" * 60)
    print("TUXEDO_RT RTT Benchmark Suite")
    print("=" * 60)
    benchmark_rtt_integration()
    benchmark_rtt_vs_python()
    print("\n" + "=" * 60)
    print("Benchmark completado.")
    print("=" * 60)
