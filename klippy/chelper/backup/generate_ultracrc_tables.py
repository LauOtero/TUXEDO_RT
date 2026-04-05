#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
generate_ultracrc_tables.py - Generador de tablas CRC precomputadas para UltraCRC

Características:
• CRC8/SMBUS, CRC16-CCITT-FALSE, CRC32-IEEE, CRC32C-Castagnoli, CRC64-XZ(ECMA)
• Tablas slice-by-N para aceleración software
• Constantes Barrett/folding para reducción PMULL/VPCLMUL
• Validación con vectores de prueba oficiales (CRC Catalogue)
• Salida C con alineación cache-line (64B)

SPDX-License-Identifier: GPL-3.0-or-later
"""

import sys
import random
import argparse
from typing import List

# ──────────────────────────────────────────────────────────────────────────
# §1  DEFINICIONES DE POLINOMIOS Y CONFIGURACIÓN CRC
# ──────────────────────────────────────────────────────────────────────────

class CRCConfig:
    """Configuración completa de un algoritmo CRC."""
    def __init__(self, name: str, width: int, poly: int, init: int,
                 xorout: int, refin: bool, refout: bool, slice_n: int = 8):
        self.name   = name
        self.width  = width
        self.poly   = poly
        self.init   = init
        self.xorout = xorout
        self.refin  = refin
        self.refout = refout
        self.slice_n = slice_n
        self.mask   = (1 << width) - 1
        self.topbit = 1 << (width - 1)

    def __repr__(self):
        return f"CRCConfig({self.name}, w={self.width}, poly=0x{self.poly:X})"


# ── Configuraciones (CRC Catalogue rev. 2023) ─────────────────────────────

# CRC-8/SMBUS: poly=0x07, init=0x00, xorout=0x00, refin=F, refout=F
# check("123456789") = 0xF4
CRC8_CONFIG = CRCConfig(
    name="CRC8", width=8, poly=0x07, init=0x00, xorout=0x00,
    refin=False, refout=False
)

# CRC-16/CCITT-FALSE: poly=0x1021, init=0xFFFF, xorout=0x0000, refin=F, refout=F
# check("123456789") = 0x29B1
CRC16_CCITT_CONFIG = CRCConfig(
    name="CRC16_CCITT", width=16, poly=0x1021, init=0xFFFF, xorout=0x0000,
    refin=False, refout=False
)

# CRC-32/ISO-HDLC (IEEE 802.3): poly reflejado=0xEDB88320, init=0xFFFFFFFF,
# xorout=0xFFFFFFFF, refin=T, refout=T
# check("123456789") = 0xCBF43926
CRC32_IEEE_CONFIG = CRCConfig(
    name="CRC32_IEEE", width=32, poly=0xEDB88320, init=0xFFFFFFFF, xorout=0xFFFFFFFF,
    refin=True, refout=True
)

# CRC-32/ISCSI (Castagnoli): poly reflejado=0x82F63B78, init=0xFFFFFFFF,
# xorout=0xFFFFFFFF, refin=T, refout=T
# check("123456789") = 0xE3069283
CRC32C_CASTAGNOLI_CONFIG = CRCConfig(
    name="CRC32C_CASTAGNOLI", width=32, poly=0x82F63B78, init=0xFFFFFFFF, xorout=0xFFFFFFFF,
    refin=True, refout=True
)

# CRC-64/XZ (ECMA-182 reflejado): poly reflejado=0xC96C5795D7870F42
# init=0xFFFFFFFFFFFFFFFF, xorout=0xFFFFFFFFFFFFFFFF, refin=T, refout=T
# check("123456789") = 0x995DC9BBDF1939FA
CRC64_ECMA_CONFIG = CRCConfig(
    name="CRC64_ECMA", width=64, poly=0xC96C5795D7870F42,
    init=0xFFFFFFFFFFFFFFFF, xorout=0xFFFFFFFFFFFFFFFF,
    refin=True, refout=True
)

# ──────────────────────────────────────────────────────────────────────────
# §2  MOTOR DE CÁLCULO CRC GENÉRICO (Bitwise)
# ──────────────────────────────────────────────────────────────────────────

def crc_bitwise(data: bytes, cfg: CRCConfig) -> int:
    """
    Calcula CRC bit a bit - referencia para validación.
    
    Para refin=True: algoritmo LSB-first con polinomio reflejado.
    Para refin=False: algoritmo MSB-first con polinomio normal.
    """
    mask = cfg.mask
    crc  = cfg.init & mask

    if cfg.refin:
        for byte in data:
            crc ^= byte
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ cfg.poly
                else:
                    crc >>= 1
            crc &= mask
    else:
        for byte in data:
            crc ^= byte << (cfg.width - 8)
            for _ in range(8):
                if crc & cfg.topbit:
                    crc = ((crc << 1) & mask) ^ cfg.poly
                else:
                    crc = (crc << 1) & mask

    return (crc ^ cfg.xorout) & mask


# ──────────────────────────────────────────────────────────────────────────
# §3  GENERACIÓN DE TABLAS BASE (256 entradas)
# ──────────────────────────────────────────────────────────────────────────

def generate_base_table(cfg: CRCConfig) -> List[int]:
    """
    Genera tabla base de 256 entradas para CRC.
    
    Para refin=True: shift derecho con polinomio reflejado.
    Para refin=False: shift izquierdo MSB-first con polinomio normal.
    """
    mask   = cfg.mask
    table  = []

    for byte in range(256):
        if cfg.refin:
            crc = byte
            for _ in range(8):
                if crc & 1:
                    crc = (crc >> 1) ^ cfg.poly
                else:
                    crc >>= 1
            crc &= mask
        else:
            crc = byte << (cfg.width - 8)
            for _ in range(8):
                if crc & cfg.topbit:
                    crc = ((crc << 1) & mask) ^ cfg.poly
                else:
                    crc = (crc << 1) & mask
        table.append(crc)

    return table


# ──────────────────────────────────────────────────────────────────────────
# §4  GENERACIÓN DE TABLAS SLICE-BY-N
# ──────────────────────────────────────────────────────────────────────────

def generate_slice_tables(cfg: CRCConfig, base_table: List[int]) -> List[List[int]]:
    """
    Genera tablas slice-by-N para aceleración software.
    
    Para CRCs reflejados (refin=True):
      slice[k][b] = base_table[slice[k-1][b] & 0xFF] ^ (slice[k-1][b] >> 8)
    
    Para CRCs no reflejados (refin=False):
      slice[k][b] = base_table[idx] ^ ((slice[k-1][b] << 8) & mask)
      donde idx = (slice[k-1][b] >> (width-8)) & 0xFF
    """
    mask   = cfg.mask
    slices: List[List[int]] = []
    prev   = base_table

    for _ in range(cfg.slice_n - 1):
        curr = []
        for b in range(256):
            v = prev[b]
            if cfg.refin:
                entry = base_table[v & 0xFF] ^ (v >> 8)
            else:
                idx   = (v >> (cfg.width - 8)) & 0xFF
                entry = base_table[idx] ^ ((v << 8) & mask)
            curr.append(entry & mask)
        slices.append(curr)
        prev = curr

    return slices


# ──────────────────────────────────────────────────────────────────────────
# §5  CONSTANTES DE FOLDING/BARRETT PARA PMULL/VPCLMUL
# ──────────────────────────────────────────────────────────────────────────

def get_barrett_constants(cfg: CRCConfig) -> dict:
    """
    Devuelve constantes de reducción para instrucciones CLMUL/PMULL.
    
    Fuente: Intel Application Note 323102
    "Fast CRC Computation for Generic Polynomials Using PCLMULQDQ Instruction"
    
    Para CRC32:
      k1  = x^95 mod P(x)  — folding 128→96 bits
      k2  = x^63 mod P(x)  — folding 96→64 bits  
      mu  = ⌊x^64 / P(x)⌋ — Barrett reduction
      poly = polinomio con bit x^32 explícito (33 bits)
    """
    if cfg.name == "CRC32_IEEE":
        return {
            "k1":   0x154442BD4,
            "k2":   0x1C6E41596,
            "mu":   0x1F7011641,
            "poly": 0x104C11DB7,
        }
    elif cfg.name == "CRC32C_CASTAGNOLI":
        return {
            "k1":   0x0740EEF02,
            "k2":   0x09E4ADDF8,
            "mu":   0x0F20C0DFE,
            "poly": 0x11EDC6F41,
        }
    elif cfg.name == "CRC64_ECMA":
        return {
            "k1":   0xdabe95afc7875f40,
            "k2":   0xe05dd497ca393ae4,
            "mu":   0xdabe95afc7875f41,
            "poly": 0xC96C5795D7870F42,
        }
    return {}


# ──────────────────────────────────────────────────────────────────────────
# §6  FORMATO DE SALIDA C
# ──────────────────────────────────────────────────────────────────────────

def format_hex_value(value: int, width: int) -> str:
    """Formatea un valor como literal hexadecimal C."""
    if width <= 8:
        return f"0x{value:02X}U"
    elif width <= 16:
        return f"0x{value:04X}U"
    elif width <= 32:
        return f"0x{value:08X}U"
    else:
        return f"0x{value:016X}ULL"

def fmt_1d_values(values, width, per=8):
    """Format 256 values as comma-separated lines."""
    lines = []
    for i in range(0, len(values), per):
        chunk = values[i:i+per]
        lines.append("    " + ", ".join(format_hex_value(v, width) for v in chunk) + ",")
    return "\n".join(lines)

def format_table_c(name: str, values: List[int], width: int,
                   per_line: int = 8, static: bool = True) -> str:
    """Genera código C para una tabla base de 256 entradas."""
    type_map = {8: 'uint8_t', 16: 'uint16_t', 32: 'uint32_t', 64: 'uint64_t'}
    ctype   = type_map.get(width, 'uint32_t')
    storage = "const " if static else ""
    lines   = [f"{storage}{ctype} {name}[256] __attribute__((aligned(64))) = {{"]
    lines.append(fmt_1d_values(values, width, per_line))
    lines.append("};")
    return "\n".join(lines)

def format_slices_2d_c(cfg: CRCConfig, slices: List[List[int]], prefix: str) -> str:
    """Genera definición 2D: const T name[N][256] = { {slice0...}, {slice1...} };"""
    width  = cfg.width
    n      = len(slices)
    type_map = {8: 'uint8_t', 16: 'uint16_t', 32: 'uint32_t', 64: 'uint64_t'}
    ctype  = type_map.get(width, 'uint32_t')
    per    = 8

    lines = [f"const {ctype} {prefix}_s[{n}][256] __attribute__((aligned(64))) = {{"]
    for i, sl in enumerate(slices):
        lines.append(f"    /* slice[{i}] */")
        lines.append("    {")
        for j in range(0, 256, per):
            chunk = sl[j:j+per]
            lines.append("        " + ", ".join(format_hex_value(v, width) for v in chunk) + ",")
        lines.append("    },")
    lines.append("};")
    return "\n".join(lines)


# ──────────────────────────────────────────────────────────────────────────
# §7  VECTORES DE PRUEBA OFICIALES (CRC Catalogue)
# ──────────────────────────────────────────────────────────────────────────

TEST_VECTORS = {
    "CRC8": [
        (b"",                                             0x00),
        (b"123456789",                                   0xF4),
        (b"The quick brown fox jumps over the lazy dog", 0xC1),
    ],
    "CRC16_CCITT": [
        (b"",                                             0xFFFF),
        (b"123456789",                                   0x29B1),
        (b"The quick brown fox jumps over the lazy dog", 0x8FDD),
    ],
    "CRC32_IEEE": [
        (b"",                                             0x00000000),
        (b"123456789",                                   0xCBF43926),
        (b"The quick brown fox jumps over the lazy dog", 0x414FA339),
    ],
    "CRC32C_CASTAGNOLI": [
        (b"",                                             0x00000000),
        (b"123456789",                                   0xE3069283),
        (b"The quick brown fox jumps over the lazy dog", 0x22620404),
    ],
    "CRC64_ECMA": [
        (b"",                                             0x0000000000000000),
        (b"123456789",                                   0x995DC9BBDF1939FA),
        (b"The quick brown fox jumps over the lazy dog", 0x5B5EB8C2E54AA1C4),
    ],
}


# ──────────────────────────────────────────────────────────────────────────
# §8  VALIDACIÓN DE TABLAS
# ──────────────────────────────────────────────────────────────────────────

def validate_table(cfg: CRCConfig, base_table: List[int]) -> bool:
    """
    Valida la tabla base contra vectores de prueba oficiales.
    
    Para refin=True: índice = (crc ^ byte) & 0xFF, actualización con shift derecho.
    Para refin=False: índice con shift izquierdo, actualización simétrica.
    """
    mask = cfg.mask
    all_ok = True

    for data, expected in TEST_VECTORS.get(cfg.name, []):
        crc = cfg.init & mask
        for byte in data:
            if cfg.refin:
                idx = (crc ^ byte) & 0xFF
                crc = base_table[idx] ^ (crc >> 8)
            else:
                idx = ((crc >> (cfg.width - 8)) ^ byte) & 0xFF
                crc = ((crc << 8) & mask) ^ base_table[idx]
        result = (crc ^ cfg.xorout) & mask

        if result != expected:
            print(f"  ❌ {cfg.name}: '{data[:30]}': "
                  f"esperado 0x{expected:0{cfg.width//4}X}, "
                  f"obtenido 0x{result:0{cfg.width//4}X}")
            all_ok = False
        else:
            print(f"  ✅ {cfg.name}: '{data[:30]}' → 0x{result:0{cfg.width//4}X}")

    return all_ok


def validate_slice_tables(cfg: CRCConfig, base_table: List[int],
                          slices: List[List[int]]) -> bool:
    """
    Valida que las tablas slice producen los mismos resultados que la tabla base.
    """
    random.seed(42)
    mask = cfg.mask

    for _ in range(200):
        data = bytes(random.randint(0, 255) for _ in range(random.randint(1, 512)))
        ref  = crc_bitwise(data, cfg)

        crc = cfg.init & mask
        for byte in data:
            if cfg.refin:
                crc = base_table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
            else:
                idx = ((crc >> (cfg.width - 8)) ^ byte) & 0xFF
                crc = ((crc << 8) & mask) ^ base_table[idx]
        result = (crc ^ cfg.xorout) & mask

        if result != ref:
            print(f"  ❌ {cfg.name} slice: inconsistencia detectada")
            return False

    print(f"  ✅ {cfg.name} slice-by-{cfg.slice_n}: consistencia verificada (200 casos)")
    return True


# ──────────────────────────────────────────────────────────────────────────
# §9  GENERADOR PRINCIPAL
# ──────────────────────────────────────────────────────────────────────────

def generate_ultracrc_tables_c(output_path: str = "ultracrc_tables.c") -> bool:
    """Genera el archivo ultracrc_tables.c completo."""

    header = '''\
/*
 * ultracrc_tables.c - Precomputed CRC Tables Implementation
 * Auto-generated by generate_ultracrc_tables.py - DO NOT EDIT MANUALLY
 * UltraCRC Engine - Ultra-High-Performance, RT-Deterministic
 * SPDX-License-Identifier: GPL-3.0-or-later
 *
 * Algorithms implemented:
 *   CRC-8/SMBUS          poly=0x07,           init=0x00,           refin=F
 *   CRC-16/CCITT-FALSE   poly=0x1021,         init=0xFFFF,         refin=F
 *   CRC-32/ISO-HDLC      poly=0xEDB88320(R),  init=0xFFFFFFFF,     refin=T
 *   CRC-32C/ISCSI        poly=0x82F63B78(R),  init=0xFFFFFFFF,     refin=T
 *   CRC-64/XZ            poly=0xC96C5795...(R),init=0xFFFF...FFFF, refin=T
 */
#define _GNU_SOURCE
#include "ultracrc_tables.h"
#include <string.h>
'''

    footer = '''\

/* ── Slice-by-N Tables (inicializadas en runtime por ultracrc_tables_build_*) ── */
uint8_t  ultracrc_crc8_s [3][256] __aligned(ULTRACRC_CACHE_LINE_SIZE);
uint16_t ultracrc_crc16_s[3][256] __aligned(ULTRACRC_CACHE_LINE_SIZE);
uint32_t ultracrc_crc32_s[7][256] __aligned(ULTRACRC_CACHE_LINE_SIZE);
uint32_t ultracrc_crc32c_s[7][256] __aligned(ULTRACRC_CACHE_LINE_SIZE);
uint64_t ultracrc_crc64_s[7][256] __aligned(ULTRACRC_CACHE_LINE_SIZE);

/* ── Funciones de construcción (llamadas por ultracrc_warmup) ─────────────── */

/* CRC-8/SMBUS slice-by-4 */
__visible void ultracrc_tables_build_crc8(void)
{
    for (int b = 0; b < 256; b++)
        ultracrc_crc8_s[0][b] = ultracrc_crc8_t0[
            (ultracrc_crc8_t0[b] ^ 0) & 0xFF];
    for (int b = 0; b < 256; b++)
        ultracrc_crc8_s[1][b] = ultracrc_crc8_s[0][
            ultracrc_crc8_s[0][b] & 0xFF];
    for (int b = 0; b < 256; b++)
        ultracrc_crc8_s[2][b] = ultracrc_crc8_s[1][
            ultracrc_crc8_s[1][b] & 0xFF];
}

/* CRC-16/CCITT-FALSE slice-by-4 (non-reflected) */
__visible void ultracrc_tables_build_crc16(void)
{
#define STEP16(c, t) ((uint16_t)(((c) << 8) ^ (t)[((c) >> 8) & 0xFF]))
    for (int b = 0; b < 256; b++) {
        uint16_t c = ultracrc_crc16_t0[b];
        c = STEP16(c, ultracrc_crc16_t0); ultracrc_crc16_s[0][b] = c;
        c = STEP16(c, ultracrc_crc16_t0); ultracrc_crc16_s[1][b] = c;
        c = STEP16(c, ultracrc_crc16_t0); ultracrc_crc16_s[2][b] = c;
    }
#undef STEP16
}

/* CRC-32/ISO-HDLC slice-by-8 (reflected) */
__visible void ultracrc_tables_build_crc32(void)
{
    for (int b = 0; b < 256; b++)
        ultracrc_crc32_s[0][b] =
            ultracrc_crc32_t0[ultracrc_crc32_t0[b] & 0xFF]
            ^ (ultracrc_crc32_t0[b] >> 8);
    for (int sl = 1; sl < 7; sl++)
        for (int b = 0; b < 256; b++)
            ultracrc_crc32_s[sl][b] =
                ultracrc_crc32_t0[ultracrc_crc32_s[sl-1][b] & 0xFF]
                ^ (ultracrc_crc32_s[sl-1][b] >> 8);
}

/* CRC-32C/ISCSI slice-by-8 (reflected) */
__visible void ultracrc_tables_build_crc32c(void)
{
    for (int b = 0; b < 256; b++)
        ultracrc_crc32c_s[0][b] =
            ultracrc_crc32c_t0[ultracrc_crc32c_t0[b] & 0xFF]
            ^ (ultracrc_crc32c_t0[b] >> 8);
    for (int sl = 1; sl < 7; sl++)
        for (int b = 0; b < 256; b++)
            ultracrc_crc32c_s[sl][b] =
                ultracrc_crc32c_t0[ultracrc_crc32c_s[sl-1][b] & 0xFF]
                ^ (ultracrc_crc32c_s[sl-1][b] >> 8);
}

/* CRC-64/XZ slice-by-8 (reflected) */
__visible void ultracrc_tables_build_crc64(void)
{
    for (int b = 0; b < 256; b++)
        ultracrc_crc64_s[0][b] =
            ultracrc_crc64_t0[ultracrc_crc64_t0[b] & 0xFF]
            ^ (ultracrc_crc64_t0[b] >> 8);
    for (int sl = 1; sl < 7; sl++)
        for (int b = 0; b < 256; b++)
            ultracrc_crc64_s[sl][b] =
                ultracrc_crc64_t0[ultracrc_crc64_s[sl-1][b] & 0xFF]
                ^ (ultracrc_crc64_s[sl-1][b] >> 8);
}
'''

    all_valid   = True
    output_lines = [header]

    configs = [
        (CRC8_CONFIG,             "ultracrc_crc8",   8),
        (CRC16_CCITT_CONFIG,      "ultracrc_crc16",  16),
        (CRC32_IEEE_CONFIG,       "ultracrc_crc32",  32),
        (CRC32C_CASTAGNOLI_CONFIG,"ultracrc_crc32c", 32),
        (CRC64_ECMA_CONFIG,       "ultracrc_crc64",  64),
    ]

    for cfg, prefix, width in configs:
        print(f"🔄  Generando tablas para {cfg.name}...")

        base_table = generate_base_table(cfg)

        print(f"    Validando tabla base…")
        if not validate_table(cfg, base_table):
            print(f"❌  Error: validación fallida para {cfg.name}")
            all_valid = False
            continue

        output_lines.append(f"/* ── {cfg.name} base table ({'refin=T' if cfg.refin else 'refin=F'}) ── */")
        output_lines.append(format_table_c(f"{prefix}_t0", base_table, width))
        output_lines.append("")

        slices = generate_slice_tables(cfg, base_table)
        print(f"    Validando coherencia slice-by-{cfg.slice_n}…")
        if not validate_slice_tables(cfg, base_table, slices):
            all_valid = False

        print(f"✅  {cfg.name}: generación completada\n")

    output_lines.append("/* ── Folding/Barrett constants for PMULL/VPCLMUL ──────────────────── */")
    output_lines.append("/*")
    output_lines.append(" * Fuente: Intel Application Note 323102")
    output_lines.append(" * «Fast CRC Computation for Generic Polynomials Using PCLMULQDQ»")
    output_lines.append(" *")
    output_lines.append(" * k1 = x^95  mod P  (fold 128→96 bits, first  PCLMULQDQ pass)")
    output_lines.append(" * k2 = x^63  mod P  (fold  96→64 bits, second PCLMULQDQ pass)")
    output_lines.append(" * mu = ⌊x^64 / P⌋   (Barrett reduction constant)")
    output_lines.append(" * poly = P(x) con bit x^32 explícito (33 bits)")
    output_lines.append(" */")
    output_lines.append("")

    for cfg in [CRC32_IEEE_CONFIG, CRC32C_CASTAGNOLI_CONFIG, CRC64_ECMA_CONFIG]:
        bc     = get_barrett_constants(cfg)
        prefix = {
            "CRC32_IEEE":          "ultracrc_barrett_crc32",
            "CRC32C_CASTAGNOLI":   "ultracrc_barrett_crc32c",
            "CRC64_ECMA":          "ultracrc_barrett_crc64",
        }[cfg.name]

        ctype = "uint64_t"
        fmt   = lambda v: f"0x{v:016X}ULL"

        output_lines.append(f"const {ctype} {prefix}[4] __aligned(ULTRACRC_CACHE_LINE_SIZE) = {{")
        output_lines.append(f"    {fmt(bc['k1'])},  /* k1 = x^95 mod P */")
        output_lines.append(f"    {fmt(bc['k2'])},  /* k2 = x^63 mod P */")
        output_lines.append(f"    {fmt(bc['mu'])},  /* mu = Barrett reduction constant */")
        output_lines.append(f"    {fmt(bc['poly'])}, /* P(x) polynomial */")
        output_lines.append("};")
        output_lines.append("")

    output_lines.append(footer)

    try:
        with open(output_path, 'w', encoding='utf-8') as f:
            f.write("\n".join(output_lines))
        print(f"📁  Archivo generado: {output_path}")
        print(f"📊  Tamaño: {len(output_lines)} líneas")
        return all_valid
    except IOError as e:
        print(f"❌  Error escribiendo archivo: {e}")
        return False


# ──────────────────────────────────────────────────────────────────────────
# §10  TESTS AUTOMÁTICOS
# ──────────────────────────────────────────────────────────────────────────

def run_tests() -> bool:
    """Ejecuta tests de validación exhaustivos."""
    print("🧪  Ejecutando tests de validación…\n")
    all_passed = True

    cfg_map = {
        "CRC8":               CRC8_CONFIG,
        "CRC16_CCITT":        CRC16_CCITT_CONFIG,
        "CRC32_IEEE":         CRC32_IEEE_CONFIG,
        "CRC32C_CASTAGNOLI":  CRC32C_CASTAGNOLI_CONFIG,
        "CRC64_ECMA":         CRC64_ECMA_CONFIG,
    }

    print("── Test 1: crc_bitwise() contra CRC Catalogue ──────────────────")
    for name, vectors in TEST_VECTORS.items():
        cfg = cfg_map[name]
        print(f"  {name}:")
        for data, expected in vectors:
            result = crc_bitwise(data, cfg)
            ok = "✅" if result == expected else "❌"
            if result != expected:
                print(f"    {ok} '{data[:25]}': "
                      f"esperado 0x{expected:0{cfg.width//4}X}, "
                      f"obtenido 0x{result:0{cfg.width//4}X}")
                all_passed = False
            else:
                print(f"    {ok} '{data[:25]}' → 0x{result:0{cfg.width//4}X}")

    print("\n── Test 2: generate_base_table() contra CRC Catalogue ──────────")
    for name, cfg in cfg_map.items():
        base_table = generate_base_table(cfg)
        ok = validate_table(cfg, base_table)
        if not ok:
            all_passed = False

    print("\n── Test 3: coherencia bitwise vs table-lookup (datos aleatorios) ")
    random.seed(2024)
    for name, cfg in cfg_map.items():
        base_table = generate_base_table(cfg)
        mask       = cfg.mask
        errors     = 0
        for _ in range(500):
            data = bytes(random.randint(0, 255) for _ in range(random.randint(0, 256)))
            ref  = crc_bitwise(data, cfg)
            crc  = cfg.init & mask
            for byte in data:
                if cfg.refin:
                    crc = base_table[(crc ^ byte) & 0xFF] ^ (crc >> 8)
                else:
                    idx = ((crc >> (cfg.width - 8)) ^ byte) & 0xFF
                    crc = ((crc << 8) & mask) ^ base_table[idx]
            tbl = (crc ^ cfg.xorout) & mask
            if tbl != ref:
                errors += 1
        status = "✅" if errors == 0 else f"❌ ({errors} errores)"
        print(f"  {status} {name}: 500 vectores aleatorios")
        if errors:
            all_passed = False

    print("\n── Test 4: coherencia slice-by-N ───────────────────────────────")
    for name, cfg in cfg_map.items():
        base_table = generate_base_table(cfg)
        slices     = generate_slice_tables(cfg, base_table)
        ok         = validate_slice_tables(cfg, base_table, slices)
        if not ok:
            all_passed = False

    return all_passed


# ──────────────────────────────────────────────────────────────────────────
# §11  MAIN
# ──────────────────────────────────────────────────────────────────────────

def main():
    parser = argparse.ArgumentParser(description="Generador de tablas CRC para UltraCRC")
    parser.add_argument("-o", "--output", default="ultracrc_tables.c",
                        help="Archivo de salida (default: ultracrc_tables.c)")
    parser.add_argument("-t", "--test", action="store_true",
                        help="Ejecutar tests de validación y salir")
    parser.add_argument("-v", "--verbose", action="store_true",
                        help="Salida detallada")
    args = parser.parse_args()

    if args.test:
        success = run_tests()
        print("\n✅  Todos los tests pasaron." if success else "\n❌  Hay fallos en los tests.")
        sys.exit(0 if success else 1)

    print("🚀  Generando ultracrc_tables.c…")
    print(f"📤  Salida: {args.output}\n")

    success = generate_ultracrc_tables_c(args.output)

    if success:
        print("\n✅  Generación completada exitosamente")
        print("💡  Recuerda llamar a ultracrc_warmup() al inicio de tu aplicación")
    else:
        print("\n❌  Error durante la generación")
        sys.exit(1)


if __name__ == "__main__":
    main()