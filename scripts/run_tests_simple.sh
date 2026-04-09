#!/bin/bash
# Script simplificado para ejecutar tests en WSL
# Uso: bash scripts/run_tests_simple.sh

cd "$(dirname "$0")/.." || exit 1

echo "=========================================="
echo "Ejecutando Tests Unitarios TUXEDO_RT"
echo "=========================================="

# Usar venv si existe, si no usar python3 del sistema
if [ -x ".venv/bin/python3" ]; then
    PYTHON=".venv/bin/python3"
elif [ -x "../../../Python/Python312/python.exe" ]; then
    PYTHON="../../../Python/Python312/python.exe"
else
    PYTHON="python3"
fi

echo "Python: $PYTHON"
echo ""

# Ejecutar tests
$PYTHON -m pytest tests/test_unit_optimizations.py -v --tb=short
exit_code=$?

echo ""
if [ $exit_code -eq 0 ]; then
    echo "=========================================="
    echo "TODOS LOS TESTS PASARON"
    echo "=========================================="
else
    echo "=========================================="
    echo "ALGUNOS TESTS FALLARON (código: $exit_code)"
    echo "=========================================="
fi

exit $exit_code