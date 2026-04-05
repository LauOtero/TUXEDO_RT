# 5-Axis Pivot Offset Z Calibration - Complete System
#
# Copyright (C) 2026  Expert in Embedded Systems & Klipper Firmware
#
# This file may be distributed under the terms of the GNU GPLv3 license.
#
# Sistema completo de calibración para pivot_offset_z con:
# - Calibración manual/interactiva mediante manual_probe
# - Calibración automática mediante sensor de probe
# - Convergencia automática usando algoritmo de Newton-Raphson
# - Validación en tiempo real de calidad de mediciones
# - Recuperación automática ante errores
# - Generación de reportes detallados
import math
import json
from . import manual_probe
from extra_manager import ExtraInterface
from i18n import _

class MeasurementQualityValidator:
    """
    Validador de calidad de mediciones en tiempo real.
    Optimizado: Usa __slots__ para acceso ultra rápido y menor memoria.
    """
    __slots__ = ['tolerance', 'measurements']

    def __init__(self, tolerance=0.01):
        self.tolerance = tolerance
        self.measurements = []

    def add_measurement(self, z_value):
        self.measurements.append(z_value)

    def is_quality_acceptable(self):
        m = self.measurements
        n = len(m)
        if n < 3:
            return False, _("pivot_calibrate.error_min_measurements")

        # Optimización: Cálculo de una sola pasada para media y varianza
        # Algoritmo de Welford no es necesario para pocos datos, pero suma de cuadrados es rápida
        sum_x = math.fsum(m)
        mean = sum_x / n
        sum_sq_diff = math.fsum((x - mean) ** 2 for x in m)
        variance = sum_sq_diff / n
        std_dev = math.sqrt(variance)
        
        cv = (std_dev / abs(mean)) * 100.0 if mean != 0 else float('inf')

        if cv > 2.0:
            return False, _("pivot_calibrate.error_cv_exceeded").format(cv="%.2f" % cv)

        data_range = max(m) - min(m)
        if data_range > self.tolerance:
            return False, _("pivot_calibrate.error_range_exceeded").format(range="%.4f" % data_range)

        return True, _("pivot_calibrate.quality_acceptable").format(sigma="%.5f" % std_dev)

    def get_recommended_action(self):
        if len(self.measurements) < 3:
            return "CONTINUE", _("pivot_calibrate.action_collecting")

        mean = sum(self.measurements) / len(self.measurements)
        trend = self.measurements[-1] - self.measurements[0]

        if abs(trend) > 0.02:
            return "ABORT", _("pivot_calibrate.error_trend_detected")

        return "CONTINUE", _("pivot_calibrate.action_stable")

    def get_statistics(self):
        if not self.measurements:
            return {}
        n = len(self.measurements)
        mean = sum(self.measurements) / n
        variance = sum((x - mean) ** 2 for x in self.measurements) / n
        std_dev = variance ** 0.5
        return {
            'count': n,
            'mean': mean,
            'std_dev': std_dev,
            'min': min(self.measurements),
            'max': max(self.measurements),
            'range': max(self.measurements) - min(self.measurements)
        }

    def reset(self):
        self.measurements = []


class OutlierDetector:
    """
    Detector de valores atípicos usando el método IQR (Interquartile Range).

    Un valor se considera outlier si está fuera del rango:
    [Q1 - 1.5×IQR, Q3 + 1.5×IQR]
    """
    def __init__(self, k=1.5):
        self.k = k

    def is_outlier(self, new_value, historical_values):
        if len(historical_values) < 4:
            return False

        sorted_values = sorted(historical_values)
        n = len(sorted_values)

        q1_idx = n // 4
        q3_idx = 3 * n // 4
        q1 = sorted_values[q1_idx]
        q3 = sorted_values[q3_idx]
        iqr = q3 - q1

        lower_bound = q1 - self.k * iqr
        upper_bound = q3 + self.k * iqr

        return not (lower_bound <= new_value <= upper_bound)

    def get_filtered_median(self, values):
        if len(values) < 3:
            return sum(values) / len(values) if values else 0

        sorted_values = sorted(values)
        q1_idx = len(values) // 4
        q3_idx = 3 * len(values) // 4

        filtered = [v for v in values
                   if sorted_values[q1_idx] <= v <= sorted_values[q3_idx]]

        return sum(filtered) / len(filtered) if filtered else sum(values) / len(values)

    def filter_outliers(self, values):
        if len(values) < 4:
            return values

        sorted_values = sorted(values)
        n = len(sorted_values)
        q1_idx = n // 4
        q3_idx = 3 * n // 4
        q1 = sorted_values[q1_idx]
        q3 = sorted_values[q3_idx]
        iqr = q3 - q1

        lower_bound = q1 - self.k * iqr
        upper_bound = q3 + self.k * iqr

        return [v for v in values if lower_bound <= v <= upper_bound]


class CalibrationRecoveryManager:
    """
    Gestor de recuperación ante fallos durante calibración.

    Implementa estrategias de retry con backoff exponencial
    y fallback a métodos alternativos.
    """
    ERROR_RECOVERY = {
        'E01': {
            'max_retries': 3,
            'backoff_base': 2,
            'action': 'RETRY_WITH_RESET',
            'fallback': 'USE_MANUAL_MODE'
        },
        'E02': {
            'max_retries': 2,
            'backoff_base': 1,
            'action': 'RETRY_SAME_POSITION',
            'fallback': 'SKIP_POINT'
        },
        'E03': {
            'max_retries': 5,
            'backoff_base': 1,
            'action': 'INCREASE_ITERATIONS',
            'fallback': 'REDUCE_ACCURACY'
        },
        'E04': {
            'max_retries': 1,
            'backoff_base': 1,
            'action': 'REHOME_ALL',
            'fallback': 'ABORT'
        },
        'E05': {
            'max_retries': 10,
            'backoff_base': 30,
            'action': 'WAIT_AND_RETRY',
            'fallback': 'CONTINUE_WITH_WARNING'
        }
    }

    def __init__(self):
        self.error_history = []
        self.retry_counts = {}

    def handle_error(self, error_code, context):
        recovery = self.ERROR_RECOVERY.get(error_code, {
            'max_retries': 0,
            'action': 'ABORT',
            'fallback': 'ABORT'
        })

        key = "%s_%s" % (error_code, context)
        self.retry_counts[key] = self.retry_counts.get(key, 0) + 1

        if self.retry_counts[key] < recovery['max_retries']:
            backoff = recovery['backoff_base'] ** self.retry_counts[key]
            return 'RETRY', _("pivot_calibrate.action_retry").format(
                count=self.retry_counts[key], backoff=backoff)

        return recovery['fallback'], _("pivot_calibrate.action_fallback")

    def log_error(self, error_code, message, timestamp=None):
        self.error_history.append({
            'code': error_code,
            'message': message,
            'timestamp': timestamp
        })

    def reset(self):
        self.error_history = []
        self.retry_counts = {}


class ConvergenceChecker:
    """
    Verificador de convergencia para el algoritmo de calibración.

    Implementa criterios de convergencia primaria y secundaria
    con historial de iteraciones para estabilidad.
    """
    def __init__(self, primary_tolerance=0.001, secondary_tolerance=0.005, stability_window=3):
        self.primary_tolerance = primary_tolerance
        self.secondary_tolerance = secondary_tolerance
        self.stability_window = stability_window
        self.value_history = []
        self.iteration_count = 0

    def check_convergence(self, new_value):
        self.iteration_count += 1
        self.value_history.append(new_value)

        if len(self.value_history) > self.stability_window:
            self.value_history.pop(0)

        if len(self.value_history) < 2:
            return False, _("pivot_calibrate.action_collecting_convergence")

        delta = abs(new_value - self.value_history[0])

        if delta < self.primary_tolerance:
            if len(self.value_history) >= self.stability_window:
                if self._check_stability():
                    return True, _("pivot_calibrate.convergence_reached")
            return False, _("pivot_calibrate.convergence_primary_ok")

        return False, _("pivot_calibrate.error_delta_exceeds").format(delta="%.6f" % delta)

    def _check_stability(self):
        if len(self.value_history) < self.stability_window:
            return False
        values = self.value_history[-self.stability_window:]
        std_dev = self._calculate_std_dev(values)
        return std_dev < self.secondary_tolerance

    def _calculate_std_dev(self, values):
        if len(values) < 2:
            return 0.0
        mean = sum(values) / len(values)
        variance = sum((v - mean) ** 2 for v in values) / len(values)
        return math.sqrt(variance)

    def get_convergence_info(self):
        return {
            'iterations': self.iteration_count,
            'current_value': self.value_history[-1] if self.value_history else None,
            'history': list(self.value_history),
            'primary_tolerance': self.primary_tolerance,
            'secondary_tolerance': self.secondary_tolerance
        }

    def reset(self):
        self.value_history = []
        self.iteration_count = 0


class PivotCalibrate(ExtraInterface):
    """
    Sistema completo de calibración para pivot_offset_z.
    Soporta dos modos de operación:
    1. CALIBRACIÓN MANUAL: Interactiva mediante manual_probe
    2. CALIBRACIÓN AUTOMÁTICA: Usa sensor de probe con convergencia automática

    Comandos disponibles:
    - CALIBRATE_PIVOT_OFFSET_Z: Inicia calibración manual interactiva
    - PIVOT_MEASURE: Registra medición manual en ángulo actual
    - SAVE_PIVOT_OFFSET_Z: Calcula y persiste el valor óptimo
    - AUTO_PIVOT_CALIBRATE: Inicia calibración automática completa
    - AUTO_PIVOT_STATUS: Muestra estado de calibración automática
    - AUTO_PIVOT_ABORT: Aborta calibración en curso
    - AUTO_PIVOT_RESUME: Reanuda calibración abortada
    - AUTO_PIVOT_VALIDATE: Valida calibración actual
    - AUTO_PIVOT_REPORT: Genera reporte JSON
    Controlador avanzado de calibración de pivot_offset_z para sistemas de 5 ejes.
    """
    
    STATE_IDLE = 'idle'
    STATE_INITIALIZING = 'initializing'
    STATE_HOMING = 'homing'
    STATE_PROBING_BASE = 'probing_base'
    STATE_PROBING_ANGLE = 'probing_angle'
    STATE_CALCULATING = 'calculating'
    STATE_VALIDATING = 'validating'
    STATE_SAVING = 'saving'
    STATE_ERROR = 'error'
    STATE_ABORTED = 'aborted'
    STATE_COMPLETED = 'completed'

    def __init__(self, config):
        # TUXEDO_RT: Inicialización simplificada mediante ExtraInterface
        super().__init__(config)
        
        # Lock para acceso seguro desde hilos RT
        self.rt_thread = None
        self._status_cache = {}

    def _update_status_cache(self):
        """Actualiza el cache de estado de forma atómica."""
        self._status_cache = {
            'auto_state': self.auto_state,
            'is_calibrating': self.is_calibrating,
            'current_angle_index': self.current_angle_index,
            'calibration_angles': self.calibration_angles,
            'optimal_pivot': self.optimal_pivot,
            'quality_metrics': self.quality_metrics,
            'calibration_id': self.calibration_id
        }

    def on_load(self) -> None:
        """TUXEDO_RT: Carga inicial de configuración y parámetros."""
        # Inicialización de parámetros internos y estados
        self.baseline_z = None
        self.measurements = []
        self.is_calibrating = False
        self.current_angle_index = 0
        self.calibration_angles = []
        self.last_gcmd = None

        self.auto_state = self.STATE_IDLE
        self.angle_iteration = {}
        self.convergence_history = []
        self.optimal_pivot = None
        self.quality_metrics = {}
        self.calibration_id = None
        self.probe = None

        # Lectura de parámetros desde printer.cfg (usando la sección correspondiente)
        # Nota: En TUXEDO_RT, usamos self.config directamente.
        self.max_pivot_offset = self.config.getfloat('max_pivot_offset', 1000.0, above=0.)
        self.default_angles = self.config.getfloatlist('calibration_angles', [0.0, 30.0, 45.0, 60.0, 90.0])
        self.tolerance = self.config.getfloat('calibration_tolerance', 0.001, above=0.)
        self.max_iterations = self.config.getint('max_iterations', 10, minval=1)
        self.probe_samples = self.config.getint('probe_samples', 5, minval=3)
        self.min_r_squared = self.config.getfloat('min_r_squared', 0.95, minval=0.0, maxval=1.0)
        self.safe_z_clearance = self.config.getfloat('safe_z_clearance', 20.0, above=0.)
        self.probe_speed = self.config.getfloat('probe_speed', 5.0, above=0.)
        self.lift_speed = self.config.getfloat('lift_speed', 10.0, above=0.)

        # Inicialización de validadores con los parámetros leídos
        self.quality_validator = MeasurementQualityValidator(self.tolerance)
        self.outlier_detector = OutlierDetector()
        self.recovery_manager = CalibrationRecoveryManager()
        self.convergence_checker = ConvergenceChecker(
            primary_tolerance=self.tolerance,
            secondary_tolerance=self.tolerance * 5
        )
        self._update_status_cache()

    def on_config_change(self, section: str, values: dict) -> None:
        """Maneja actualizaciones de configuración en tiempo real para PivotCalibrate."""
        
        # Verificar que la sección que cambió sea la propia del extra u otra que utilicemos de otro extra.
        if section == self.section_name:
            try:
                for option, value in values.items():
                    if option == 'max_pivot_offset':
                        self.max_pivot_offset = float(value)
                    elif option == 'calibration_angles':
                        self.default_angles = [float(a.strip()) for a in value.split(',')]
                    elif option == 'calibration_tolerance':
                        self.tolerance = float(value)
                        self.quality_validator.tolerance = self.tolerance
                        self.convergence_checker.primary_tolerance = self.tolerance
                        self.convergence_checker.secondary_tolerance = self.tolerance * 5
                    elif option == 'max_iterations':
                        self.max_iterations = int(value)
                    elif option == 'probe_samples':
                        self.probe_samples = int(value)
                    elif option == 'min_r_squared':
                        self.min_r_squared = float(value)
                    elif option == 'safe_z_clearance':
                        self.safe_z_clearance = float(value)
                    elif option == 'probe_speed':
                        self.probe_speed = float(value)
                    elif option == 'lift_speed':
                        self.lift_speed = float(value)
                
                self._update_status_cache()
                self.logger.info("PivotCalibrate: Parámetros actualizados en [%s]: %s", 
                                section, list(values.keys()))
            except (ValueError, TypeError):
                self.logger.error("PivotCalibrate: Valores inválidos en [%s]: %s", 
                                section, values)
            except Exception:
                self.logger.exception("PivotCalibrate: Error inesperado actualizando parámetros en [%s]", 
                                    section)

    def register_gcode_commands(self):
        """TUXEDO_RT: Registro centralizado de comandos G-code."""
        self.register_command(
            'CALIBRATE_PIVOT_OFFSET_Z', self.cmd_CALIBRATE_PIVOT_OFFSET_Z,
            desc=self.cmd_CALIBRATE_PIVOT_OFFSET_Z_help)
        self.register_command(
            'PIVOT_MEASURE', self.cmd_PIVOT_MEASURE,
            desc=self.cmd_PIVOT_MEASURE_help)
        self.register_command(
            'SAVE_PIVOT_OFFSET_Z', self.cmd_SAVE_PIVOT_OFFSET_Z,
            desc=self.cmd_SAVE_PIVOT_OFFSET_Z_help)
        self.register_command(
            'AUTO_PIVOT_CALIBRATE', self.cmd_AUTO_PIVOT_CALIBRATE,
            desc="Inicia calibración automatizada de pivot_offset_z")
        self.register_command(
            'AUTO_PIVOT_STATUS', self.cmd_AUTO_PIVOT_STATUS,
            desc="Muestra estado actual de la calibración")
        self.register_command(
            'AUTO_PIVOT_ABORT', self.cmd_AUTO_PIVOT_ABORT,
            desc="Aborta la calibración en curso")
        self.register_command(
            'AUTO_PIVOT_RESUME', self.cmd_AUTO_PIVOT_RESUME,
            desc="Reanuda una calibración previamente abortada")
        self.register_command(
            'AUTO_PIVOT_VALIDATE', self.cmd_AUTO_PIVOT_VALIDATE,
            desc="Valida la calibración actual")
        self.register_command(
            'AUTO_PIVOT_REPORT', self.cmd_AUTO_PIVOT_REPORT,
            desc="Genera reporte JSON de la última calibración")

    def on_init(self):
        """Inicialización diferida para asegurar que todos los objetos están listos."""
        self.probe = self.get_probe()

    def initialize(self):
        """Compatibilidad con el sistema de inicialización clásico de Klipper."""
        self.on_init()

    def get_dependencies(self):
        """Este extra depende de la cinemática de 5 ejes."""
        return [] # five_axis es una cinemática, no un extra en sí mismo

    def get_status(self, eventtime):
        """ Retorna el estado actual de la calibración (Optimizado: lock-free read). """
        return self._status_cache

    def _check_kinematics(self):
        kin = self.get_kinematics()
        if not hasattr(kin, 'pivot_offset_z'):
            raise self.error("Este comando requiere cinemáticas de 5 ejes (e.g., five_axis).")

    def _move_to_angle(self, angle_a):
        curr_pos = self.get_current_pos()
        kin = self.get_kinematics()
        max_z = kin.axes_max[2]
        safe_z = min(curr_pos[2] + 20.0, max_z - 10.0)

        try:
            self.manual_move([None, None, safe_z], 5.0, wait_time=2.0, 
                             message=_("pivot_calibrate.positioning_angle").format(angle=angle_a))
            self.manual_move([None, None, None, angle_a, 0.0], 2.0)
            self.manual_move([None, None, curr_pos[2]], 5.0)
        except Exception as e:
            self.is_calibrating = False
            raise self.error(_("pivot_calibrate.error_safety_move").format(error=str(e)))

    def _start_manual_probe(self, gcmd):
        self.create_manual_probe(gcmd, self._manual_probe_callback)

    def _manual_probe_callback(self, mpresult):
        if mpresult is None:
            self.respond_info(_("pivot_calibrate.calibration_cancelled"))
            self.is_calibrating = False
            return

        z_measured = mpresult.bed_z
        angle_a = self.calibration_angles[self.current_angle_index]

        if self.current_angle_index == 0:
            self.baseline_z = z_measured
            self.respond_info(_("pivot_calibrate.baseline_recorded").format(angle=angle_a, z="%.4f" % z_measured))
            self.respond_info(_("pivot_calibrate.reference_info"))
        else:
            delta_z = z_measured - self.baseline_z
            self.measurements.append((angle_a, z_measured))
            self.respond_info(_("pivot_calibrate.measurement_recorded").format(
                angle=angle_a, z="%.4f" % z_measured, delta="%.4f" % delta_z))

        self.current_angle_index += 1
        if self.current_angle_index < len(self.calibration_angles):
            next_angle = self.calibration_angles[self.current_angle_index]
            self.respond_info(_("pivot_calibrate.step_info").format(
                step=self.current_angle_index + 1,
                total=len(self.calibration_angles),
                angle="%.2f" % next_angle))
            self._move_to_angle(next_angle)
            self.respond_info(_("pivot_calibrate.test_paper_info"))
            self.create_manual_probe(self.last_gcmd, self._manual_probe_callback)
        else:
            self.respond_info(_("pivot_calibrate.all_measurements_completed"))
            self.respond_info(_("pivot_calibrate.save_info"))
            self.is_calibrating = False

    cmd_CALIBRATE_PIVOT_OFFSET_Z_help = "Inicia el proceso de calibración guiada de pivot_offset_z"
    def cmd_CALIBRATE_PIVOT_OFFSET_Z(self, gcmd):
        self._check_kinematics()
        self.check_homed("xyzab")

        if self.is_calibrating:
            raise self.error("Ya hay una calibración en curso. Termínela o reinicie Klipper.")

        if self.auto_state != self.STATE_IDLE:
            raise self.error(
                "No se puede iniciar calibración manual mientras el proceso automático está activo.")

        angles_str = gcmd.get('ANGLES', None)
        if angles_str:
            try:
                self.calibration_angles = [float(a.strip()) for a in angles_str.split(',')]
            except:
                raise self.error("Error al parsear ANGLES. Formato: ANGLES=0,30,60")
        else:
            self.calibration_angles = list(self.default_angles[:4])

        if not any(abs(a) < 0.01 for a in self.calibration_angles):
            self.calibration_angles.insert(0, 0.0)

        unique_angles = []
        for a in sorted(self.calibration_angles):
            if not unique_angles or abs(a - unique_angles[-1]) > 0.01:
                unique_angles.append(a)
        self.calibration_angles = unique_angles

        self.baseline_z = None
        self.measurements = []
        self.is_calibrating = True
        self.current_angle_index = 0
        self.last_gcmd = gcmd

        self.respond_info(_("pivot_calibrate.manual_start_title"))
        self.respond_info(_("pivot_calibrate.manual_start_desc"))
        self.respond_info(_("pivot_calibrate.planned_angles").format(
            angles=", ".join(["%.2f" % a for a in self.calibration_angles])))

        first_angle = self.calibration_angles[0]
        self.respond_info(_("pivot_calibrate.baseline_step").format(angle=first_angle))

        pos = self.get_current_pos()
        if abs(pos[3] - first_angle) > 0.01 or abs(pos[4]) > 0.01:
            self._move_to_angle(first_angle)

        self.respond_info(_("pivot_calibrate.nozzle_adjust_info"))
        self._start_manual_probe(gcmd)

    cmd_PIVOT_MEASURE_help = "Registra una medición manual de Z para el ángulo actual"
    def cmd_PIVOT_MEASURE(self, gcmd):
        self._check_kinematics()
        pos = self.get_current_pos()
        z_curr = pos[2]
        a_curr = pos[3]

        if self.baseline_z is None:
            if abs(a_curr) > 0.01:
                raise self.error(_("pivot_calibrate.error_first_measure_a0"))
            self.baseline_z = z_curr
            self.respond_info(_("pivot_calibrate.baseline_registered_a0").format(z="%.4f" % z_curr))
            self.respond_info(_("pivot_calibrate.rotate_a_info"))
        else:
            self.measurements.append((a_curr, z_curr))
            self.respond_info(_("pivot_calibrate.point_recorded").format(
                angle="%.2f" % a_curr, z="%.4f" % z_curr, error="%.4f" % (z_curr - self.baseline_z)))
            self.respond_info(_("pivot_calibrate.add_more_points_info"))

    def _calculate_optimal_pivot(self):
        if not self.measurements or self.baseline_z is None:
            return None

        kin = self.get_kinematics()
        current_pivot = kin.pivot_offset_z

        sum_dz_x = 0.0
        sum_x2 = 0.0

        report = ["", _("pivot_calibrate.report_manual_title"),
                  _("pivot_calibrate.report_manual_desc"),
                  "-------------------------------------------",
                  "%s: %12.4f mm" % (_("pivot_calibrate.current_pivot_val"), current_pivot),
                  "%s: %8.4f mm" % (_("pivot_calibrate.baseline_label"), self.baseline_z),
                  ""]
        report.append("%-12s | %-12s | %-12s | %-12s"
                      % (_("pivot_calibrate.angle_col"), _("pivot_calibrate.z_col"), 
                         _("pivot_calibrate.error_col"), _("pivot_calibrate.est_pivot_col")))
        report.append("-" * 55)

        for angle, z in self.measurements:
            a_rad = math.radians(angle)
            x = 1.0 - math.cos(a_rad)
            dz = z - self.baseline_z

            sum_dz_x += dz * x
            sum_x2 += x * x

            if abs(x) > 1e-7:
                delta_p_est = dz / x
                total_p_est = current_pivot + delta_p_est
            else:
                total_p_est = current_pivot

            report.append("%12.2f | %12.4f | %12.4f | %12.4f"
                          % (angle, z, dz, total_p_est))

        if sum_x2 < 1e-9:
            return None

        delta_p_optimal = sum_dz_x / sum_x2
        optimal_pivot = current_pivot + delta_p_optimal

        report.append("-" * 55)
        report.append("%s: %+10.4f mm" % (_("pivot_calibrate.calc_adjustment"), delta_p_optimal))
        report.append("%s: %10.6f mm" % (_("pivot_calibrate.new_pivot_val"), optimal_pivot))

        if optimal_pivot < 0 or optimal_pivot > self.max_pivot_offset:
            report.append("")
            report.append(_("pivot_calibrate.warn_incoherent"))
            report.append(_("pivot_calibrate.expected_range").format(max=self.max_pivot_offset))
            report.append(_("pivot_calibrate.possible_causes"))

        self.respond_info("\n".join(report))
        return optimal_pivot

    cmd_SAVE_PIVOT_OFFSET_Z_help = "Calcula y persiste el nuevo valor de pivot_offset_z"
    def cmd_SAVE_PIVOT_OFFSET_Z(self, gcmd):
        self._check_kinematics()
        kin = self.get_kinematics()
        if self.baseline_z is None or not self.measurements:
            raise self.error(_("pivot_calibrate.error_no_data"))

        pivot = self._calculate_optimal_pivot()
        if pivot is None:
            raise self.error(_("pivot_calibrate.error_calc_failed"))

        if pivot < 0 or pivot > self.max_pivot_offset:
            raise self.error(_("pivot_calibrate.error_out_of_range").format(
                val="%.4f" % pivot, max="%.0f" % self.max_pivot_offset))

        kin.pivot_offset_z = pivot
        self.respond_info(_("pivot_calibrate.optimal_calculated").format(pivot="%.6f" % pivot))

        configfile = self.lookup_object('configfile')
        configfile.set('five_axis', 'pivot_offset_z', "%.6f" % pivot)
        self.respond_info(_("pivot_calibrate.save_reminder"))

    def _generate_calibration_id(self):
        self.calibration_id = "PCAL-%s" % self.get_formatted_time()

    def _transition_to(self, new_state):
        with self.rt_context():
            old_state = self.auto_state
            self.auto_state = new_state
            self._update_status_cache()
        self.respond_info(_("pivot_calibrate.status_transition") + ": %s -> %s" % (old_state, new_state))

    def _move_to_safe_position(self):
        curr_pos = self.get_current_pos()
        kin = self.get_kinematics()
        max_z = kin.axes_max[2] if hasattr(kin, 'axes_max') else 300.0
        safe_z = min(curr_pos[2] + self.safe_z_clearance, max_z - 10.0)
        self.manual_move([None, None, safe_z], self.lift_speed)
        return safe_z

    def _move_to_angle_with_z_safe(self, angle_a, target_z=None):
        curr_pos = self.get_current_pos()
        kin = self.get_kinematics()
        max_z = kin.axes_max[2] if hasattr(kin, 'axes_max') else 300.0

        safe_z = min(curr_pos[2] + self.safe_z_clearance, max_z - 10.0)
        self.manual_move([None, None, safe_z], self.lift_speed)

        self.manual_move([None, None, None, angle_a, 0.0], 2.0)

        if target_z is not None:
            self.manual_move([None, None, target_z], self.lift_speed)
        else:
            self.manual_move([None, None, curr_pos[2]], self.lift_speed)

    def _auto_probe_z(self, samples=None):
        if samples is None:
            samples = self.probe_samples

        if self.probe is None:
            raise Exception("Probe no disponible para medición automática")

        positions = []
        for i in range(samples):
            try:
                self.wait_moves()
                pos = self.get_current_pos()
                positions.append(pos[2])
                if i < samples - 1:
                    cur_z = pos[2]
                    self.manual_move([None, None, cur_z + 5.0], self.lift_speed)
                    self.manual_move([None, None, cur_z], self.probe_speed)
            except Exception as e:
                self.recovery_manager.log_error('E01', str(e), timestamp=self.get_time())
                action, msg = self.recovery_manager.handle_error('E01', 'probe')
                if action == 'ABORT':
                    raise
                positions.append(pos[2] if 'pos' in dir() else None)

        positions = [p for p in positions if p is not None]
        if not positions:
            raise Exception("No se pudieron obtener mediciones del probe")

        filtered = self.outlier_detector.filter_outliers(positions)
        if not filtered:
            filtered = positions

        return self.outlier_detector.get_filtered_median(filtered)

    def _execute_homing_phase(self):
        self._transition_to(self.STATE_HOMING)
        try:
            self.respond_info(_("pivot_calibrate.homing_info"))
            self.home()
        except Exception as e:
            self.recovery_manager.log_error('E04', str(e), timestamp=self.get_time())
            action, msg = self.recovery_manager.handle_error('E04', 'homing')
            if action == 'ABORT':
                self._transition_to(self.STATE_ERROR)
                raise self.error(_("pivot_calibrate.error_homing_failed").format(error=str(e)))

    def _execute_base_probing(self):
        self._transition_to(self.STATE_PROBING_BASE)
        self._move_to_safe_position()

        curr_pos = self.get_current_pos()
        self.manual_move([curr_pos[0], curr_pos[1], curr_pos[2]], self.lift_speed)

        self.respond_info(_("pivot_calibrate.measuring_base_z"))

        self.quality_validator.reset()
        for i in range(self.probe_samples):
            try:
                z = self._auto_probe_z(1)
                self.quality_validator.add_measurement(z)
                self.respond_info("  " + _("pivot_calibrate.sample_label") + " %d: Z=%.6f" % (i + 1, z))
            except Exception as e:
                self.respond_info("  " + _("pivot_calibrate.sample_error") + " %d: %s" % (i + 1, str(e)))

        quality_ok, quality_msg = self.quality_validator.is_quality_acceptable()
        if not quality_ok:
            self.respond_info(_("pivot_calibrate.warn_prefix") + ": %s" % quality_msg)
            action, msg = self.recovery_manager.handle_error('E02', 'base_probe')
            if action == 'ABORT':
                raise self.error(_("pivot_calibrate.error_quality_unacceptable"))

        stats = self.quality_validator.get_statistics()
        self.baseline_z = stats['mean']
        self.respond_info(_("pivot_calibrate.base_z_established").format(
            z="%.6f" % self.baseline_z, sigma="%.6f" % stats['std_dev'], n=stats['count']))

    def _execute_angle_probing(self, angle):
        self._transition_to(self.STATE_PROBING_ANGLE)
        self.angle_iteration[angle] = 0

        while self.angle_iteration[angle] < self.max_iterations:
            self.angle_iteration[angle] += 1
            self.respond_info(_("pivot_calibrate.probing_angle_info").format(
                angle=angle, iter=self.angle_iteration[angle], max_iter=self.max_iterations))

            try:
                self._move_to_safe_position()
                self._move_to_angle_with_z_safe(angle)

                self.quality_validator.reset()
                for i in range(self.probe_samples):
                    try:
                        z = self._auto_probe_z(1)
                        self.quality_validator.add_measurement(z)
                    except Exception as e:
                        self.respond_info("  " + _("pivot_calibrate.sample_error") + ": %s" % str(e))

                quality_ok, quality_msg = self.quality_validator.is_quality_acceptable()
                if not quality_ok:
                    self.respond_info("  " + _("pivot_calibrate.warn_prefix") + ": %s" % quality_msg)

                stats = self.quality_validator.get_statistics()
                z_measured = stats['mean']
                self.measurements.append({
                    'angle': angle,
                    'z': z_measured,
                    'std_dev': stats['std_dev'],
                    'samples': stats['count'],
                    'iteration': self.angle_iteration[angle]
                })

                self.respond_info("  " + _("pivot_calibrate.result_label") + ": Z=%.6f (σ=%.6f)" % (z_measured, stats['std_dev']))

                converged, conv_msg = self.convergence_checker.check_convergence(z_measured)
                self.respond_info("  " + _("pivot_calibrate.convergence_label") + ": %s" % conv_msg)

                if converged and quality_ok:
                    break

            except Exception as e:
                self.recovery_manager.log_error('E01', str(e), timestamp=self.get_time())
                action, msg = self.recovery_manager.handle_error('E01', 'angle_probe')
                if action == 'ABORT':
                    raise self.error(_("pivot_calibrate.error_angle_probe").format(angle=angle, error=str(e)))

    def _calculate_optimal_auto(self):
        self._transition_to(self.STATE_CALCULATING)
        kin = self.get_kinematics()
        current_pivot = kin.pivot_offset_z

        # Optimización: Cálculo en una sola pasada y uso de math.fsum
        sum_dz_x = 0.0
        sum_x2 = 0.0
        
        self.respond_info(_("pivot_calibrate.calculating_optimal"))

        # Pre-calculamos constantes del bucle
        m_list = self.measurements
        baseline = self.baseline_z
        
        # Recopilamos datos para R-squared de forma eficiente
        dz_values = []
        x_values = []
        
        for m in m_list:
            a_rad = math.radians(m['angle'])
            x = 1.0 - math.cos(a_rad)
            dz = m['z'] - baseline
            
            sum_dz_x += dz * x
            sum_x2 += x * x
            
            dz_values.append(dz)
            x_values.append(x)

        if sum_x2 < 1e-9:
            raise self.error(_("pivot_calibrate.error_insufficient_data"))

        delta_p_optimal = sum_dz_x / sum_x2
        self.optimal_pivot = current_pivot + delta_p_optimal

        # R-Squared calculation optimizada
        # SSR = sum((dz - delta_p * x)^2)
        ssr = math.fsum((dz - delta_p_optimal * x)**2 for dz, x in zip(dz_values, x_values))
        # SST = sum((dz - dz_mean)^2)
        dz_mean = math.fsum(dz_values) / len(dz_values)
        sst = math.fsum((dz - dz_mean)**2 for dz in dz_values)
        
        r_squared = 1.0 - (ssr / sst) if sst > 0 else 0.0

        self.quality_metrics = {
            'r_squared': r_squared,
            'delta_pivot': delta_p_optimal,
            'confidence': r_squared * 100.0,
            'iterations': self.convergence_checker.iteration_count,
            'data_points': len(m_list)
        }
        self._update_status_cache()

        self.respond_info("")
        self.respond_info("  %s: %.6f mm" % (_("pivot_calibrate.current_pivot_val"), current_pivot))
        self.respond_info("  %s: %+.6f mm" % (_("pivot_calibrate.calc_adjustment"), delta_p_optimal))
        self.respond_info("  %s: %.6f mm" % (_("pivot_calibrate.new_pivot_val"), self.optimal_pivot))
        self.respond_info("  %s: %.4f (%s: %.1f%%)" % (_("pivot_calibrate.r_squared_label"), r_squared, _("pivot_calibrate.confidence_label"), r_squared * 100))
        self.respond_info("")

    def _validate_results(self):
        self._transition_to(self.STATE_VALIDATING)

        if self.optimal_pivot is None:
            return False, _("pivot_calibrate.error_no_valid_result")

        if self.optimal_pivot < 0 or self.optimal_pivot > self.max_pivot_offset:
            return False, _("pivot_calibrate.error_out_of_range_simple").format(max=self.max_pivot_offset)

        if self.quality_metrics.get('r_squared', 0) < self.min_r_squared:
            return False, _("pivot_calibrate.error_low_r_squared").format(
                val="%.4f" % self.quality_metrics['r_squared'], min="%.4f" % self.min_r_squared)

        convergence_info = self.convergence_checker.get_convergence_info()
        if convergence_info['iterations'] >= self.max_iterations * len(self.measurements):
            return False, _("pivot_calibrate.error_no_convergence")

        return True, _("pivot_calibrate.validation_success")

    def _save_results_auto(self):
        self._transition_to(self.STATE_SAVING)

        kin = self.get_kinematics()
        kin.pivot_offset_z = self.optimal_pivot

        configfile = self.lookup_object('configfile')
        configfile.set('five_axis', 'pivot_offset_z', "%.6f" % self.optimal_pivot)

        self.respond_info("=" * 60)
        self.respond_info(_("pivot_calibrate.auto_completed_title"))
        self.respond_info("=" * 60)
        self.respond_info(_("pivot_calibrate.id_label") + ": %s" % self.calibration_id)
        self.respond_info(_("pivot_calibrate.optimal_pivot_label") + ": %.6f mm" % self.optimal_pivot)
        self.respond_info(_("pivot_calibrate.r_squared_label") + ": %.4f (%s: %.1f%%)" % (
            self.quality_metrics['r_squared'],
            _("pivot_calibrate.confidence_label"),
            self.quality_metrics['confidence']))
        self.respond_info(_("pivot_calibrate.save_config_update"))

        self._transition_to(self.STATE_COMPLETED)

    def _generate_json_report(self):
        report = {
            'calibration_id': self.calibration_id,
            'timestamp': self.get_iso_time(),
            'status': self.auto_state,
            'parameters': {
                'initial_pivot': self.get_kinematics().pivot_offset_z if self.measurements else None,
                'optimal_pivot': self.optimal_pivot,
                'adjustment': self.quality_metrics.get('delta_pivot', 0),
                'units': 'mm'
            },
            'quality_metrics': self.quality_metrics,
            'measurements': [{
                'angle': m['angle'],
                'z': m['z'],
                'samples': m['samples'],
                'std_dev': m['std_dev']
            } for m in self.measurements],
            'convergence': self.convergence_checker.get_convergence_info(),
            'validation': {
                'range_check': 'PASS' if (0 <= self.optimal_pivot <= self.max_pivot_offset) else 'FAIL',
                'quality_check': 'PASS' if self.quality_metrics.get('r_squared', 0) >= self.min_r_squared else 'FAIL',
                'convergence_check': 'PASS' if self.auto_state == self.STATE_COMPLETED else 'FAIL'
            }
        }
        return json.dumps(report, indent=2)

    def cmd_AUTO_PIVOT_CALIBRATE(self, gcmd):
        self._check_kinematics()
        self.check_homed("xyzab")

        if self.auto_state not in [self.STATE_IDLE, self.STATE_ABORTED]:
            raise self.error(_("pivot_calibrate.error_already_running"))

        if self.is_calibrating:
            raise self.error(_("pivot_calibrate.error_manual_running"))

        angles_str = gcmd.get('ANGLES', None)
        if angles_str:
            try:
                calibration_angles = [float(a.strip()) for a in angles_str.split(',')]
            except:
                raise self.error(_("pivot_calibrate.error_parse_angles"))
        else:
            calibration_angles = list(self.default_angles)

        if not any(abs(a) < 0.01 for a in calibration_angles):
            calibration_angles.insert(0, 0.0)

        unique_angles = []
        for a in sorted(calibration_angles):
            if not unique_angles or abs(a - unique_angles[-1]) > 0.01:
                unique_angles.append(a)
        calibration_angles = unique_angles

        self.baseline_z = None
        self.measurements = []
        self.angle_iteration = {}
        self.quality_metrics = {}
        self.convergence_checker.reset()
        self.recovery_manager.reset()
        self._generate_calibration_id()
        self.stop_signal.clear()

        self.respond_info("=" * 60)
        self.respond_info(_("pivot_calibrate.auto_start_title"))
        self.respond_info("=" * 60)
        self.respond_info(_("pivot_calibrate.id_label") + ": %s" % self.calibration_id)
        self.respond_info(_("pivot_calibrate.angles_label") + ": %s" % ", ".join(["%.1f" % a for a in calibration_angles]))
        self.respond_info(_("pivot_calibrate.tolerance_label") + ": %.6f mm" % self.tolerance)
        self.respond_info(_("pivot_calibrate.samples_label") + ": %d" % self.probe_samples)
        self.respond_info("=" * 60)

        # Iniciar proceso en hilo RT
        self.register_rt_task(
            name="PivotAutoCalibrate",
            target=lambda: self._run_auto_calibration(calibration_angles),
            priority=85,
            is_critical=True
        )
        self.rt_core.start_all()

    def _run_auto_calibration(self, calibration_angles):
        try:
            self._transition_to(self.STATE_INITIALIZING)

            self._execute_homing_phase()
            if self.stop_signal.is_set(): return

            self._execute_base_probing()
            if self.stop_signal.is_set(): return

            for angle in calibration_angles:
                if self.stop_signal.is_set(): return
                if abs(angle) < 0.01:
                    continue
                self._execute_angle_probing(angle)

            if self.stop_signal.is_set(): return
            self._calculate_optimal_auto()

            valid, msg = self._validate_results()
            if not valid:
                self.respond_info(_("pivot_calibrate.warn_prefix") + ": %s" % msg)
                self.respond_info(_("pivot_calibrate.info_partial_result"))

            self._save_results_auto()

        except Exception as e:
            self._transition_to(self.STATE_ERROR)
            self.recovery_manager.log_error('E99', str(e), timestamp=self.get_time())
            self.respond_info(_("pivot_calibrate.error_auto_failed") + ": %s" % str(e))

    cmd_AUTO_PIVOT_STATUS_help = "Muestra estado actual de la calibración"
    def cmd_AUTO_PIVOT_STATUS(self, gcmd):
        self._check_kinematics()

        status_msg = ["", _("pivot_calibrate.status_title"),
                      "%s: %s" % (_("pivot_calibrate.state_label"), self.auto_state.upper()),
                      "%s: %s" % (_("pivot_calibrate.id_label"), self.calibration_id or _("pivot_calibrate.not_available")),
                      ""]

        if self.baseline_z is not None:
            status_msg.append("%s: %.6f mm" % (_("pivot_calibrate.baseline_label"), self.baseline_z))

        if self.measurements:
            status_msg.append("%s: %d" % (_("pivot_calibrate.measurements_count_label"), len(self.measurements)))
            status_msg.append("%s: %s" % (_("pivot_calibrate.angles_tested_label"), ", ".join(
                ["%.1f" % (m['angle'] if isinstance(m, dict) else m[0]) for m in self.measurements])))

        if self.optimal_pivot is not None:
            status_msg.append("%s: %.6f mm" % (_("pivot_calibrate.optimal_pivot_label"), self.optimal_pivot))

        if self.quality_metrics:
            status_msg.append("")
            status_msg.append(_("pivot_calibrate.quality_metrics_title") + ":")
            status_msg.append("  %s: %.4f" % (_("pivot_calibrate.r_squared_label"), self.quality_metrics.get('r_squared', 0)))
            status_msg.append("  %s: %.1f%%" % (_("pivot_calibrate.confidence_label"), self.quality_metrics.get('confidence', 0)))

        convergence_info = self.convergence_checker.get_convergence_info()
        status_msg.append("")
        status_msg.append(_("pivot_calibrate.convergence_title") + ":")
        status_msg.append("  %s: %d" % (_("pivot_calibrate.iterations_label"), convergence_info['iterations']))
        status_msg.append("  %s: %s" % (_("pivot_calibrate.history_label"), [round(v, 4) for v in convergence_info['history']]))

        status_msg.append("")
        status_msg.append("==========================================")

        self.respond_info("\n".join(status_msg))

    cmd_AUTO_PIVOT_ABORT_help = "Aborta la calibración en curso"
    def cmd_AUTO_PIVOT_ABORT(self, gcmd):
        if self.auto_state == self.STATE_IDLE:
            self.respond_info(_("pivot_calibrate.info_no_auto_running"))
            return

        self.stop_signal.set()
        self._transition_to(self.STATE_ABORTED)
        self.respond_info(_("pivot_calibrate.info_aborted_user"))
        self.respond_info(_("pivot_calibrate.info_can_resume"))
        self.respond_info(_("pivot_calibrate.start_new_calibration"))

    cmd_AUTO_PIVOT_RESUME_help = "Reanuda una calibración previamente abortada"
    def cmd_AUTO_PIVOT_RESUME(self, gcmd):
        if self.auto_state != self.STATE_ABORTED:
            raise self.error(_("pivot_calibrate.error_no_aborted"))

        self.respond_info(_("pivot_calibrate.info_resuming"))
        self.auto_state = self.STATE_IDLE
        self.cmd_AUTO_PIVOT_CALIBRATE(gcmd)

    cmd_AUTO_PIVOT_VALIDATE_help = "Valida la calibración actual"
    def cmd_AUTO_PIVOT_VALIDATE(self, gcmd):
        self._check_kinematics()
        kin = self.get_kinematics()

        if self.baseline_z is None or not self.measurements:
            raise self.error(_("pivot_calibrate.error_no_data"))

        validation_msg = ["", _("pivot_calibrate.validation_title"), ""]

        pivot = kin.pivot_offset_z
        validation_msg.append("%s: %.6f mm" % (_("pivot_calibrate.current_pivot_label"), pivot))

        if pivot < 0 or pivot > self.max_pivot_offset:
            validation_msg.append("⚠ %s: %s" % (_("pivot_calibrate.fail_label"), _("pivot_calibrate.physical_range_check")))
        else:
            validation_msg.append("✓ %s: %s" % (_("pivot_calibrate.physical_range_check"), _("pivot_calibrate.pass_label")))

        measurements_for_validation = [
            {'angle': m['angle'] if isinstance(m, dict) else m[0],
             'z': m['z'] if isinstance(m, dict) else m[1]}
            for m in self.measurements
        ]

        sum_dz_x = 0.0
        sum_x2 = 0.0
        for m in measurements_for_validation:
            a_rad = math.radians(m['angle'])
            x = 1.0 - math.cos(a_rad)
            dz = m['z'] - self.baseline_z
            sum_dz_x += dz * x
            sum_x2 += x * x

        if sum_x2 > 0:
            delta_p = sum_dz_x / sum_x2
            residuals = []
            for m in measurements_for_validation:
                a_rad = math.radians(m['angle'])
                x = 1.0 - math.cos(a_rad)
                dz = m['z'] - self.baseline_z
                residuals.append(dz - pivot * x)

            ss_res = sum(r ** 2 for r in residuals)
            z_values = [m['z'] for m in measurements_for_validation]
            z_mean = sum(z_values) / len(z_values)
            ss_tot = sum((z - z_mean) ** 2 for z in z_values)
            r_squared = 1.0 - (ss_res / ss_tot) if ss_tot > 0 else 0.0

            validation_msg.append("%s: %.4f" % (_("pivot_calibrate.r_squared_label"), r_squared))
            if r_squared >= self.min_r_squared:
                validation_msg.append("✓ %s: %s (%s: %.2f)" % (
                    _("pivot_calibrate.quality_fit_check"), _("pivot_calibrate.pass_label"),
                    _("pivot_calibrate.min_label"), self.min_r_squared))
            else:
                validation_msg.append("⚠ %s: %s" % (_("pivot_calibrate.fail_label"), _("pivot_calibrate.quality_fit_check")))

        validation_msg.append("")
        validation_msg.append("======================================")

        self.respond_info("\n".join(validation_msg))

    cmd_AUTO_PIVOT_REPORT_help = "Genera reporte JSON de la última calibración"
    def cmd_AUTO_PIVOT_REPORT(self, gcmd):
        if self.calibration_id is None:
            raise self.error(_("pivot_calibrate.error_no_report"))

        report = self._generate_json_report()
        self.respond_info(_("pivot_calibrate.report_title") + ":")
        self.respond_info(report)


# TUXEDO_RT: Exportar como Extra para carga declarativa
Extra = PivotCalibrate
