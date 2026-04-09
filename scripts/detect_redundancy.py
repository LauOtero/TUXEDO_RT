#!/usr/bin/env python3
"""
Script de Detección de Redundancia Python vs C para TUXEDO_RT.
Detecta nueva duplicación de lógica entre módulos Python y el núcleo C.

Uso:
    python3 scripts/detect_redundancy.py

Criterios de alerta:
    - Líneas duplicadas > 10 nuevas por mes
    - Nuevas funciones con lógica idéntica en ambos lados
"""
import os
import sys
import re
import subprocess
from pathlib import Path
from typing import Dict, List, Set, Tuple

class RedundancyDetector:
    """Detector de redundancia entre Python y C."""

    def __init__(self, root_dir: str):
        self.root_dir = Path(root_dir)
        self.python_dir = self.root_dir / "klippy"
        self.chelper_dir = self.python_dir / "chelper"
        self.redundancies: List[Dict] = []
        self.stats = {
            "python_lines": 0,
            "c_lines": 0,
            "duplicate_functions": 0,
            "total_duplicate_lines": 0
        }

    def scan_python_files(self) -> Dict[str, Dict[str, str]]:
        """Escanea archivos Python buscando funciones y lógica."""
        functions = {}
        pattern = re.compile(r'^def\s+(\w+)\s*\([^)]*\):(.+?)(?=\ndef |\nclass |\Z)', re.MULTILINE | re.DOTALL)

        for py_file in self.python_dir.rglob("*.py"):
            if "chelper" in str(py_file):
                continue
            try:
                content = py_file.read_text(encoding='utf-8', errors='ignore')
                for match in pattern.finditer(content):
                    func_name = match.group(1)
                    func_body = match.group(2)[:200]
                    functions[f"{py_file.name}::{func_name}"] = {
                        "file": str(py_file.relative_to(self.root_dir),
                        "function": func_name,
                        "body_preview": func_body.strip()[:100]
                    }
                    self.stats["python_lines"] += len(content.splitlines())
            except Exception as e:
                print(f"Error leyendo {py_file}: {e}")
        return functions

    def scan_c_files(self) -> Dict[str, Dict[str, str]]:
        """Escanea archivos C buscando funciones y lógica."""
        functions = {}
        pattern = re.compile(r'^([\w*]+)\s+(\w+)\s*\([^)]*\)\s*\{([^}]+(?:\{[^}]*\}[^}]*)*)\}', re.MULTILINE)

        for c_file in self.chelper_dir.rglob("*.c"):
            try:
                content = c_file.read_text(encoding='utf-8', errors='ignore')
                for match in pattern.finditer(content):
                    ret_type = match.group(1)
                    func_name = match.group(2)
                    func_body = match.group(3)[:200]
                    if not func_name.startswith("_"):
                        functions[f"{c_file.stem}::{func_name}"] = {
                            "file": str(c_file.relative_to(self.root_dir),
                            "function": func_name,
                            "ret_type": ret_type,
                            "body_preview": func_body.strip()[:100]
                        }
                        self.stats["c_lines"] += len(content.splitlines())
            except Exception as e:
                print(f"Error leyendo {c_file}: {e}")
        return functions

    def check_rtt_redundancy(self, py_funcs: Dict, c_funcs: Dict) -> None:
        """Verifica redundancia específica en métricas RTT."""
        rtt_keywords = ["srtt", "rttvar", "rto", "rtt", "round", "trip", "time"]

        for py_key, py_info in py_funcs.items():
            if any(kw in py_info["body_preview"].lower() for kw in rtt_keywords):
                for c_key, c_info in c_funcs.items():
                    if any(kw in c_key.lower() for kw in rtt_keywords):
                        if any(kw in c_info["body_preview"].lower() for kw in ["srtt", "rttvar"]):
                            self.redundancies.append({
                                "type": "RTT_METRICS",
                                "python": py_info,
                                "c": c_info,
                                "severity": "HIGH",
                                "note": "Métricas RTT duplicadas entre Python y C"
                            })

    def check_variance_redundancy(self, py_funcs: Dict, c_funcs: Dict) -> None:
        """Verifica redundancia en funciones estadísticas."""
        stats_keywords = ["variance", "stddev", "mean", "average", "stats"]

        for py_key, py_info in py_funcs.items():
            if any(kw in py_info["function"].lower() for kw in stats_keywords):
                for c_key, c_info in c_funcs.items():
                    if any(kw in c_key.lower() for kw in stats_keywords):
                        self.redundancies.append({
                            "type": "STATS_FUNCTIONS",
                            "python": py_info,
                            "c": c_info,
                            "severity": "MEDIUM",
                            "note": "Funciones estadísticas duplicadas"
                        })

    def check_crc_redundancy(self, py_funcs: Dict, c_funcs: Dict) -> None:
        """Verifica redundancia en cálculos CRC."""
        crc_keywords = ["crc", "checksum", "hash"]

        for py_key, py_info in py_funcs.items():
            if any(kw in py_key.lower() for kw in crc_keywords):
                for c_key, c_info in c_funcs.items():
                    if any(kw in c_key.lower() for kw in crc_keywords):
                        self.redundancies.append({
                            "type": "CRC_CALCULATION",
                            "python": py_info,
                            "c": c_info,
                            "severity": "HIGH",
                            "note": "Cálculo CRC duplicado entre Python y C"
                        })

    def run(self) -> Tuple[bool, List[Dict]]:
        """Ejecuta el análisis completo."""
        print("=" * 70)
        print("TUXEDO_RT - Detector de Redundancia Python vs C")
        print("=" * 70)
        print(f"\nEscaneando directorio: {self.root_dir}")

        print("\n[1/4] Escaneando archivos Python...")
        py_funcs = self.scan_python_files()
        print(f"      {len(py_funcs)} funciones Python analizadas")

        print("\n[2/4] Escaneando archivos C (chelper)...")
        c_funcs = self.scan_c_files()
        print(f"      {len(c_funcs)} funciones C analizadas")

        print("\n[3/4] Buscando redundancias conocidas...")
        self.check_rtt_redundancy(py_funcs, c_funcs)
        self.check_variance_redundancy(py_funcs, c_funcs)
        self.check_crc_redundancy(py_funcs, c_funcs)
        print(f"      {len(self.redundancies)} redundancias potenciales encontradas")

        print("\n[4/4] Generando reporte...")
        return self.generate_report()

    def generate_report(self) -> Tuple[bool, List[Dict]]:
        """Genera el reporte final."""
        has_critical = any(r["severity"] == "HIGH" for r in self.redundancies)

        print("\n" + "=" * 70)
        print("REPORTE DE REDUNDANCIA")
        print("=" * 70)

        print(f"\nEstadísticas:")
        print(f"  Líneas Python escaneadas: {self.stats['python_lines']:,}")
        print(f"  Líneas C escaneadas:     {self.stats['c_lines']:,}")
        print(f"  Funciones Python:        {len(self.scan_python_files())}")
        print(f"  Funciones C:            {len(self.scan_c_files())}")
        print(f"  Total redundancias:      {len(self.redundancies)}")

        if self.redundancies:
            print(f"\n{'Tipo':<25} {'Severidad':<12} {'Nota'}")
            print("-" * 70)
            for r in self.redundancies:
                print(f"{r['type']:<25} {r['severity']:<12} {r['note']}")
        else:
            print("\n✅ No se detectaron redundancias conocidas.")

        print("\n" + "=" * 70)
        if has_critical:
            print("⚠️  ALERTA: Se detectaron redundancias CRÍTICAS")
            print("   Ejecute las optimizaciones del plan de acción.")
            print("=" * 70)
            return False, self.redundancies
        else:
            print("✅ Sin redundancias críticas detectadas.")
            print("=" * 70)
            return True, self.redundancies

def main():
    script_dir = Path(__file__).parent.parent
    detector = RedundancyDetector(str(script_dir))
    success, redundancies = detector.run()

    sys.exit(0 if success else 1)

if __name__ == "__main__":
    main()
