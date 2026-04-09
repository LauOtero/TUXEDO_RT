#!/bin/bash
# ==============================================================================
# Script de Compilación y Tests para TUXEDO_RT - WSL/Linux
# ==============================================================================
# Este script compila el módulo chelper y ejecuta todos los tests en WSL/Linux
#
# Uso:
#   bash scripts/build_and_test.sh              # Compilar y tests completos
#   bash scripts/build_and_test.sh --compile    # Solo compilar
#   bash scripts/build_and_test.sh --unit       # Solo tests unitarios
#   bash scripts/build_and_test.sh --coverage   # Tests con coverage
#   bash scripts/build_and_test.sh --stress     # Solo tests de estrés
#
# Requisitos:
#   - WSL/Ubuntu con gcc, python3, pip, pytest, pytest-cov
#   - cffi: pip install cffi
#   - pytest: pip install pytest pytest-cov
# ==============================================================================

set -e

# Colores para output
RED='\033[0;31m'
GREEN='\033[0;32m'
YELLOW='\033[1;33m'
BLUE='\033[0;34m'
NC='\033[0m' # No Color

# Directorios
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
KLIPPY_DIR="$PROJECT_ROOT/klippy"
TESTS_DIR="$PROJECT_ROOT/tests"
CHELPER_DIR="$KLIPPY_DIR/chelper"

# Variables de configuración
COVERAGE_MIN=90
BUILD_LOG="$PROJECT_ROOT/build.log"
TEST_LOG="$PROJECT_ROOT/test.log"

# Detectar Python del venv
if [ -n "$VIRTUAL_ENV" ] || [ -d ".venv" ]; then
    if [ -x ".venv/bin/python3" ]; then
        PYTHON_BIN=".venv/bin/python3"
    else
        PYTHON_BIN="python3"
    fi
else
    PYTHON_BIN="python3"
fi
export PYTHON_BIN

# ==============================================================================
# Funciones de Utility
# ==============================================================================

log_info() {
    echo -e "${BLUE}[INFO]${NC} $1"
}

log_success() {
    echo -e "${GREEN}[SUCCESS]${NC} $1"
}

log_warning() {
    echo -e "${YELLOW}[WARNING]${NC} $1"
}

log_error() {
    echo -e "${RED}[ERROR]${NC} $1"
}

print_header() {
    echo ""
    echo "============================================================"
    echo " $1"
    echo "============================================================"
}

# ==============================================================================
# Verificación de Dependencias
# ==============================================================================

check_dependencies() {
    print_header "Verificando Dependencias"

    local missing_deps=0

    # Verificar gcc
    if command -v gcc &> /dev/null; then
        local gcc_version=$(gcc --version | head -n1)
        log_success "gcc encontrado: $gcc_version"
    else
        log_error "gcc no encontrado. Instalar: sudo apt install build-essential"
        missing_deps=1
    fi

    # Verificar python3
    if command -v python3 &> /dev/null; then
        local py_version=$(python3 --version)
        log_success "python3 encontrado: $py_version"
    else
        log_error "python3 no encontrado. Instalar: sudo apt install python3"
        missing_deps=1
    fi

    # Verificar pip
    if command -v pip3 &> /dev/null || python3 -m pip --version &> /dev/null; then
        log_success "pip3 encontrado"
    else
        log_error "pip3 no encontrado. Instalar: sudo apt install python3-pip"
        missing_deps=1
    fi

    # Instalar dependencias de Python si faltan (usando venv o --break-system-packages)
    log_info "Verificando dependencias de Python..."

    # Detectar si estamos en un venv
    local venv_python=""
    if [ -n "$VIRTUAL_ENV" ] || [ -d ".venv" ]; then
        venv_python=".venv/bin/python3"
        log_info "Usando virtual environment detectado"
    fi

    local PYTHON_CMD="${venv_python:-python3}"
    local PIP_CMD="${venv_python:+pip3}"; if [ -z "$PIP_CMD" ]; then PIP_CMD="pip3"; fi

    $PYTHON_BIN -c "import cffi" 2>/dev/null || {
        log_warning "cffi no instalado. Instalando..."
        pip3 install cffi --quiet --break-system-packages 2>/dev/null || \
        pip3 install cffi --quiet 2>/dev/null || true
    }

    $PYTHON_BIN -c "import pytest" 2>/dev/null || {
        log_warning "pytest no instalado. Instalando..."
        pip3 install pytest --quiet --break-system-packages 2>/dev/null || \
        pip3 install pytest --quiet 2>/dev/null || true
    }

    $PYTHON_BIN -c "import pytest_cov" 2>/dev/null || {
        log_warning "pytest-cov no instalado. Instalando..."
        pip3 install pytest-cov --quiet --break-system-packages 2>/dev/null || \
        pip3 install pytest-cov --quiet 2>/dev/null || true
    }

    if [ $missing_deps -eq 1 ]; then
        log_error "Dependencias faltantes. Abortando."
        exit 1
    fi

    log_success "Todas las dependencias verificadas"
}

# ==============================================================================
# Compilación del Módulo chelper
# ==============================================================================

compile_chelper() {
    print_header "Compilando Módulo chelper"

    log_info "Directorio de trabajo: $CHELPER_DIR"

    cd "$KLIPPY_DIR"

    # Limpiar compilaciones anteriores
    if [ -f "$KLIPPY_DIR/c_helper.so" ]; then
        log_info "Limpiando compilación anterior..."
        rm -f "$KLIPPY_DIR/c_helper.so"
    fi

    # Compilar usando Python/CFFI
    log_info "Ejecutando compilación mediante Python/CFFI..."
    echo "Compilación iniciada: $(date)" > "$BUILD_LOG"

    if $PYTHON_BIN -c "
import sys
sys.path.insert(0, '$KLIPPY_DIR')
from chelper import get_ffi
print('Iniciando compilación de c_helper.so...')
ffi, lib = get_ffi()
if lib is not None:
    print('Compilación exitosa!')
    sys.exit(0)
else:
    print('Error: lib es None')
    sys.exit(1)
" 2>&1 | tee -a "$BUILD_LOG"; then
        log_success "Compilación completada exitosamente"
    else
        log_error "Error durante la compilación. Ver logs: $BUILD_LOG"
        return 1
    fi

    # Verificar que el archivo .so fue creado
    if [ -f "$CHELPER_DIR/c_helper.so" ]; then
        local so_size=$(ls -lh "$CHELPER_DIR/c_helper.so" | awk '{print $5}')
        log_success "c_helper.so creado ($so_size)"
    else
        log_error "c_helper.so no fue creado"
        return 1
    fi
}

# ==============================================================================
# Tests Unitarios
# ==============================================================================

run_unit_tests() {
    print_header "Ejecutando Tests Unitarios"

    cd "$PROJECT_ROOT"

    log_info "Ejecutando tests unitarios con coverage..."

    $PYTHON_BIN -m pytest \
        tests/test_unit_optimizations.py \
        -v \
        --cov=klippy \
        --cov=scripts \
        --cov-report=term-missing \
        --cov-report=html:"$PROJECT_ROOT/coverage_unit" \
        --tb=short \
        2>&1 | tee "$TEST_LOG"

    local exit_code=${PIPESTATUS[0]}

    if [ $exit_code -eq 0 ]; then
        log_success "Tests unitarios completados exitosamente"
    else
        log_error "Tests unitarios fallaron (código: $exit_code)"
        return $exit_code
    fi
}

# ==============================================================================
# Tests de Integración
# ==============================================================================

run_integration_tests() {
    print_header "Ejecutando Tests de Integración"

    cd "$PROJECT_ROOT"

    log_info "Ejecutando tests de integración..."

    $PYTHON_BIN -m pytest \
        tests/test_integration_optimizations.py \
        -v \
        --cov=klippy \
        --cov=scripts \
        --cov-report=term-missing \
        --cov-report=html:"$PROJECT_ROOT/coverage_integration" \
        --tb=short \
        2>&1 | tee -a "$TEST_LOG"

    local exit_code=${PIPESTATUS[0]}

    if [ $exit_code -eq 0 ]; then
        log_success "Tests de integración completados exitosamente"
    else
        log_warning "Algunos tests de integración fallaron (código: $exit_code)"
        return $exit_code
    fi
}

# ==============================================================================
# Tests de Estrés/Carga
# ==============================================================================

run_stress_tests() {
    print_header "Ejecutando Tests de Estrés"

    cd "$PROJECT_ROOT"

    log_info "Ejecutando tests de carga/estrés..."

    # Tests de estrés cortos para CI
    $PYTHON_BIN -m pytest \
        tests/test_stress.py \
        -v \
        --tb=short \
        --timeout=300 \
        2>&1 | tee -a "$TEST_LOG"

    local exit_code=${PIPESTATUS[0]}

    if [ $exit_code -eq 0 ]; then
        log_success "Tests de estrés completados exitosamente"
    else
        log_warning "Algunos tests de estrés fallaron (código: $exit_code)"
        return $exit_code
    fi
}

# ==============================================================================
# Tests de Rendimiento (Benchmarks)
# ==============================================================================

run_benchmarks() {
    print_header "Ejecutando Benchmarks"

    cd "$PROJECT_ROOT"

    log_info "Ejecutando benchmarks de rendimiento..."

    # Benchmark de VLQ batch
    if [ -f "tests/benchmark_msgblock.py" ]; then
        log_info "Ejecutando benchmark_msgblock.py..."
        $PYTHON_BIN tests/benchmark_msgblock.py 2>&1 | tee -a "$TEST_LOG"
    fi

    # Benchmark de util
    if [ -f "tests/benchmark_util.py" ]; then
        log_info "Ejecutando benchmark_util.py..."
        $PYTHON_BIN tests/benchmark_util.py 2>&1 | tee -a "$TEST_LOG"
    fi

    log_success "Benchmarks completados"
}

# ==============================================================================
# Verificación de Cobertura
# ==============================================================================

check_coverage() {
    print_header "Verificando Cobertura de Tests"

    local coverage_html="$PROJECT_ROOT/coverage_total/index.html"

    if [ -d "$PROJECT_ROOT/coverage_unit" ] || [ -d "$PROJECT_ROOT/coverage_integration" ]; then
        log_info "Reportes de coverage generados:"
        [ -d "$PROJECT_ROOT/coverage_unit" ] && log_info "  - coverage_unit: $PROJECT_ROOT/coverage_unit"
        [ -d "$PROJECT_ROOT/coverage_integration" ] && log_info "  - coverage_integration: $PROJECT_ROOT/coverage_integration"
    else
        log_warning "No se encontraron reportes de coverage"
    fi

    log_info "Para ver el coverage detallado, ejecutar:"
    log_info "  firefox $PROJECT_ROOT/coverage_unit/index.html"
    log_info "  firefox $PROJECT_ROOT/coverage_integration/index.html"
}

# ==============================================================================
# Reporte Final
# ==============================================================================

print_summary() {
    print_header "Resumen de Ejecución"

    echo ""
    echo "Fecha: $(date)"
    echo "Proyecto: TUXEDO_RT"
    echo ""

    if [ -f "$BUILD_LOG" ]; then
        echo "Log de compilación: $BUILD_LOG"
    fi

    if [ -f "$TEST_LOG" ]; then
        echo "Log de tests: $TEST_LOG"
        echo ""
        echo "Resultados de tests:"
        grep -E "PASSED|FAILED|ERROR" "$TEST_LOG" | tail -20 || true
    fi

    echo ""
    echo "============================================================"
    echo "Ejecución completada"
    echo "============================================================"
}

# ==============================================================================
# Main
# ==============================================================================

main() {
    local mode="${1:-all}"

    echo ""
    echo "============================================================"
    echo " TUXEDO_RT - Script de Compilación y Tests"
    echo " WSL/Linux Environment"
    echo "============================================================"
    echo ""

    # Verificar que estamos en WSL o Linux
    if [[ "$(uname -s)" != *"Linux"* ]] && [[ "$(uname -s)" != *"MINGW"* ]]; then
        log_warning "Este script está diseñado para ejecutarse en WSL/Linux"
    fi

    case "$mode" in
        --compile)
            check_dependencies
            compile_chelper
            ;;
        --unit)
            check_dependencies
            run_unit_tests
            ;;
        --integration)
            check_dependencies
            run_integration_tests
            ;;
        --stress)
            check_dependencies
            run_stress_tests
            ;;
        --benchmarks)
            check_dependencies
            run_benchmarks
            ;;
        --coverage)
            check_dependencies
            compile_chelper
            run_unit_tests
            run_integration_tests
            check_coverage
            ;;
        --all)
            check_dependencies
            compile_chelper
            run_unit_tests
            run_integration_tests
            run_stress_tests
            run_benchmarks
            check_coverage
            print_summary
            ;;
        *)
            echo "Uso: $0 {--compile|--unit|--integration|--stress|--benchmarks|--coverage|--all}"
            echo ""
            echo "Opciones:"
            echo "  --compile     Solo compilar el módulo chelper"
            echo "  --unit        Solo tests unitarios"
            echo "  --integration Solo tests de integración"
            echo "  --stress      Solo tests de estrés"
            echo "  --benchmarks  Solo benchmarks"
            echo "  --coverage    Tests con coverage report"
            echo "  --all         Compilar y ejecutar todos los tests (default)"
            exit 1
            ;;
    esac
}

main "$@"