/*
 * gcode_parser.h
 * Header para parser de G-code ultra-rápido y determinista
 * 
 * Copyright (C) 2026 Klipper Contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef GCODE_PARSER_H
#define GCODE_PARSER_H

#include <stdint.h>
#include <stddef.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * ESTRUCTURA DE RESULTADO DE PARSEO (ABI estable para CFFI)
 * ============================================================================ */
typedef struct {
    const uint8_t *cmd;          /* Puntero al token de comando (ej: "M105") */
    uint32_t       cmd_len;      /* Longitud del comando */
    const uint8_t *params;       /* Puntero a la cadena de parámetros (ej: "X10 Y20") */
    uint32_t       params_len;   /* Longitud de parámetros */
    int32_t        star_pos;     /* Posición de '*' para checksum (-1 si ausente) */
    uint32_t       crc32;        /* CRC-32 del contenido de línea (excluyendo checksum) */
    uint8_t        checksum_ok;  /* 1=válido, 0=inválido, 2=sin checksum presente */
} GCodeParseResult;

/* ============================================================================
 * FUNCIÓN PRINCIPAL DE PARSEO
 * ============================================================================ */

/**
 * Parsea una línea de G-code de forma ultra-rápida y determinista.
 * 
 * @param line  Puntero a la línea de entrada (no necesita null-termination)
 * @param len   Longitud de la línea en bytes
 * @param out   Puntero a estructura de resultado (debe ser asignada por el llamador)
 * @return      1 si el parseo fue exitoso, 0 si hubo error
 */
int parse_gcode_line_fast(const uint8_t *line, int32_t len, GCodeParseResult *out);

/* ============================================================================
 * FUNCIONES DE UTILIDAD
 * ============================================================================ */

/**
 * Valida el checksum de una línea de G-code.
 * 
 * @param line          Línea completa
 * @param len           Longitud de la línea
 * @param star_pos      Posición del carácter '*'
 * @param expected_crc  CRC esperado (calculado previamente)
 * @return              1 si válido, 0 si inválido, 2 si no hay checksum
 */
uint8_t gcode_validate_checksum(const uint8_t *line, int32_t len,
                                int32_t star_pos, uint32_t expected_crc);

/**
 * Inicializa las tablas LUT y calienta la caché para el parser.
 * Debe llamarse una vez al inicio del programa.
 */
void gcode_parser_warmup(void);

/**
 * Obtiene la versión del parser como cadena.
 */
const char *gcode_parser_get_version(void);

#ifdef __cplusplus
}
#endif

#endif /* GCODE_PARSER_H */
