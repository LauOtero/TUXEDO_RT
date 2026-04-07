/*
 * kinematics_core.h
 * Header para núcleo matemático de cinemáticas optimizado
 * 
 * Copyright (C) 2026 Klipper Contributors
 * SPDX-License-Identifier: GPL-3.0-or-later
 */

#ifndef KINEMATICS_CORE_H
#define KINEMATICS_CORE_H

#include <stdint.h>

#ifdef __cplusplus
extern "C" {
#endif

/* ============================================================================
 * CONSTANTES Y MACROS
 * ============================================================================ */
#define KC_NOW     0.0
#define KC_NEVER   9999999999999999.0
#define KC_EPSILON 1e-15

/* ============================================================================
 * INICIALIZACIÓN
 * ============================================================================ */
void kc_init(void);

/* ============================================================================
 * FUNCIONES TRIGONOMÉTRICAS ACELERADAS
 * ============================================================================ */
double kc_fast_sin(double x);
double kc_fast_cos(double x);

/* ============================================================================
 * OPERACIONES MATRICIALES 3x3
 * ============================================================================ */

/* Multiplicación matriz 3x3 por vector 3x1: out = m * v */
void kc_matrix_mul_3x3_3x1(const double m[3][3], const double v[3], double out[3]);

/* Multiplicación de dos matrices 3x3: out = a * b */
void kc_matrix_mul_3x3(const double a[3][3], const double b[3][3], double out[3][3]);

/* Determinante de matriz 3x3 */
double kc_matrix_det_3x3(const double a[3][3]);

/* Inversa de matriz 3x3. Devuelve 1 si éxito, 0 si singular */
int kc_matrix_inv_3x3(const double a[3][3], double out[3][3]);

/* ============================================================================
 * OPERACIONES VECTORIALES 3D
 * ============================================================================ */

/* Producto cruz: out = a × b */
void kc_vector_cross(const double a[3], const double b[3], double out[3]);

/* Producto punto: dot = a · b */
double kc_vector_dot(const double a[3], const double b[3]);

/* Norma al cuadrado: mag_sq = |v|^2 */
double kc_vector_mag_sq(const double v[3]);

/* Norma: mag = |v| */
double kc_vector_mag(const double v[3]);

/* Normalizar vector. Devuelve 1 si éxito, 0 si vector cero */
int kc_vector_normalize(const double v[3], double out[3]);

/* ============================================================================
 * TRILATERACIÓN
 * ============================================================================ */

/*
 * Trilateración de tres esferas.
 * Encuentra el punto de intersección de tres esferas definidas por:
 * - Centros: p1, p2, p3
 * - Radios al cuadrado: r1_sq, r2_sq, r3_sq
 * 
 * Devuelve: 1 si hay solución válida, 0 si configuración degenerada
 * Resultado almacenado en result[3]
 */
int kc_trilateration(
    const double p1[3], const double p2[3], const double p3[3],
    double r1_sq, double r2_sq, double r3_sq,
    double result[3]
);

/*
 * Versión por lotes de trilateración (optimizada con SIMD)
 * count: número de casos a procesar
 * Devuelve: número de casos exitosos
 */
int kc_trilateration_batch(
    const double (*p1)[3], const double (*p2)[3], const double (*p3)[3],
    const double *r1_sq, const double *r2_sq, const double *r3_sq,
    double (*results)[3],
    int count
);

/* ============================================================================
 * FUNCIONES DE TEST Y VALIDACIÓN
 * ============================================================================ */

/* Test de precisión matricial: devuelve error máximo de A * A^-1 = I */
double kc_test_matrix_precision(void);

/* Test de precisión de trilateración: devuelve error respecto a solución conocida */
double kc_test_trilateration_precision(void);

#ifdef __cplusplus
}
#endif

#endif /* KINEMATICS_CORE_H */
