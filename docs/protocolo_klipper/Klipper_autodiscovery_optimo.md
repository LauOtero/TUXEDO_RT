# Sistema de registro de decodificadores

## Decorador `@decoder_for` en la clase `Decoder`, con bootstrap automático por `pkgutil`.

El protocolo Klipper garantiza que el nombre de cada respuesta del MCU está disponible en el data dictionary antes de que llegue ningún dato. El data dictionary es un JSON comprimido con zlib, descargado durante el handshake de identificación, que contiene todas las respuestas declaradas con `sendf()` en el firmware en formato `"nombre_respuesta param1=%tipo param2=%tipo"`.

El nombre de la respuesta (e.g. `"adxl345_data"`, `"angle_data"`, `"temperature_result"`) es el identificador natural y estable del tipo de dato. El firmware de Klipper es consistente: cada sensor tiene su propio nombre de respuesta, y ese nombre no cambia entre versiones salvo por cambios mayores documentados. Mapear ese nombre a una clase decodificadora es exactamente lo que hace el registry estático, pero con el decorador ese mapeo vive junto al código en lugar de en un fichero separado.

El bootstrap con `pkgutil.iter_modules` garantiza que todos los decoradores se ejecutan al importar el pipeline, sin que nadie tenga que listar los módulos a importar. Es el mismo mecanismo que usa Flask para descubrir blueprints, pytest para descubrir plugins, y SQLAlchemy para registrar dialectos.

---

## Implementación completa

### Estructura de ficheros

```
klippy/extras/decompression/
├── __init__.py
├── pipeline.py          ← DecompressionPipeline
├── resolver.py          ← decoder_for() + bootstrap
├── base.py              ← clase base Decoder
└── decoders/
    ├── __init__.py
    ├── accel.py         ← @decoder_for("adxl345_data", "lis2dw_data", …)
    ├── angle.py         ← @decoder_for("angle_data")
    ├── eddy.py          ← @decoder_for("ldc1612_data")
    ├── loadcell.py      ← @decoder_for("hx71x_data", "ads1220_data")
    ├── temperature.py   ← @decoder_for("temperature_result", …)
    ├── pressure.py      ← @decoder_for("bme280_result", "bmp280_result", …)
    ├── tmc.py           ← @decoder_for("tmc2209_status", "tmc2130_status", …)
    ├── pwm.py           ← @decoder_for("fan_pwm_result", "heater_pwm_result")
    ├── stepper.py       ← @decoder_for("stepper_position")
    ├── endstop.py       ← @decoder_for("endstop_state")
    ├── stats.py         ← @decoder_for("mcu_stats")
    └── generic.py       ← fallback GenericBulkDecoder (sin @decoder_for)
```

### `resolver.py` — el núcleo del mecanismo

```python
# klippy/extras/decompression/resolver.py

import importlib
import logging
import pkgutil
from typing import Optional

from . import decoders as _decoders_pkg

log = logging.getLogger(__name__)

# Registry poblado por @decoder_for durante el bootstrap
_REGISTRY: dict[str, type] = {}


def decoder_for(*response_names: str):
    """
    Decorador que registra una clase Decoder para uno o más nombres
    de respuesta MCU. Se ejecuta en tiempo de definición de clase.

    Uso:
        @decoder_for("adxl345_data", "lis2dw_data", "mpu9250_data")
        class AccelDecoder(Decoder):
            ...
    """
    def decorator(cls):
        for name in response_names:
            existing = _REGISTRY.get(name)
            if existing is not None and existing is not cls:
                log.warning(
                    "decoder_for: '%s' registrado por %s, sobreescrito por %s",
                    name, existing.__name__, cls.__name__,
                )
            _REGISTRY[name] = cls
        return cls
    return decorator


def resolve(response_name: str) -> Optional[type]:
    """
    Devuelve la clase Decoder para un nombre de respuesta MCU,
    o None si no hay decodificador registrado.
    """
    return _REGISTRY.get(response_name)


def bootstrap():
    """
    Importa todos los módulos del paquete decoders para que los
    decoradores @decoder_for se ejecuten y pueblen _REGISTRY.
    Solo necesita llamarse una vez al inicializar el pipeline.
    """
    for _, module_name, _ in pkgutil.iter_modules(_decoders_pkg.__path__):
        importlib.import_module(
            f".{module_name}", package=_decoders_pkg.__name__
        )
    log.debug("resolver: %d decodificadores registrados: %s",
              len(_REGISTRY), sorted(_REGISTRY.keys()))
```

### `pipeline.py` — uso del resolver

```python
# klippy/extras/decompression/pipeline.py

import logging
from .resolver import bootstrap, resolve
from .decoders.generic import GenericBulkDecoder

log = logging.getLogger(__name__)

# Bootstrap en tiempo de importación del módulo.
# pkgutil.iter_modules es O(n_ficheros), se ejecuta una sola vez.
bootstrap()


class DecompressionPipeline:

    def __init__(self, config):
        self.printer       = config.get_printer()
        # nombre_respuesta → (DecoderClass, descriptor)
        self._classes:     dict[str, tuple[type, dict]] = {}
        # oid → instancia de Decoder (creada lazy al primer paquete)
        self._by_oid:      dict[int, object] = {}
        self._subscribers: dict[str, list]   = {}

    # ── Fase 1: handshake ──────────────────────────────────────────────────

    def on_mcu_identified(self, mcu_data_dict: dict):
        """
        Llamado tras descargar y parsear el data dictionary del MCU.
        Registra la clase decodificadora para cada respuesta con 'oid'.
        """
        for response in mcu_data_dict.get("responses", []):
            name   = response["name"]
            params = {p["name"] for p in response.get("params", [])}

            if "oid" not in params:
                continue

            decoder_cls = resolve(name)

            if decoder_cls is None:
                # Respuesta desconocida con oid: fallback a almacenado raw
                if "data" in params:
                    decoder_cls = GenericBulkDecoder
                    log.info("pipeline: '%s' sin decodificador → GenericBulkDecoder", name)
                else:
                    log.debug("pipeline: '%s' ignorada (sin decodificador ni blob)", name)
                    continue

            self._classes[name] = (decoder_cls, {})
            log.info("pipeline: '%s' → %s", name, decoder_cls.__name__)

    def register_descriptor(self, response_name: str, descriptor: dict):
        """
        Permite a un módulo extra enriquecer el descriptor del decodificador
        (p.ej. sensitivity_lsb, resolution_bits, scale).
        Debe llamarse después de on_mcu_identified.
        """
        if response_name in self._classes:
            cls, _ = self._classes[response_name]
            self._classes[response_name] = (cls, descriptor)

    # ── Fase 2: tiempo real ────────────────────────────────────────────────

    def handle_packet(self, response_name: str, oid: int,
                      raw_payload: bytes, timestamp: float):
        """
        Punto de entrada para cada paquete del MCU.
        El decodificador se instancia la primera vez que aparece el oid.
        """
        if oid not in self._by_oid:
            entry = self._classes.get(response_name)
            if entry is None:
                return
            decoder_cls, descriptor = entry
            self._by_oid[oid] = decoder_cls({**descriptor, "oid": oid})

        decoder = self._by_oid[oid]

        try:
            values = decoder.decode(raw_payload, timestamp)
        except Exception as exc:
            log.error("pipeline: error oid=%d (%s): %s", oid, response_name, exc)
            decoder.reset()
            return

        for cb in self._subscribers.get(decoder.data_type, []):
            cb(oid, values, timestamp)

    def on_crc_error(self, oid: int):
        decoder = self._by_oid.get(oid)
        if decoder:
            decoder.reset()

    def subscribe(self, data_type: str, callback):
        self._subscribers.setdefault(data_type, []).append(callback)
```

### Ejemplo de decodificador — `decoders/accel.py`

```python
# klippy/extras/decompression/decoders/accel.py

from ..resolver import decoder_for
from ..base import Decoder

SHIFT = 10

@decoder_for(
    "adxl345_data",
    "lis2dw_data",
    "lis3dh_data",
    "mpu9250_data",
    "bmi160_data",
    "icm20948_data",
)
class AccelDecoder(Decoder):
    data_type = "accel_xyz"

    def __init__(self, descriptor: dict):
        self._scale = descriptor.get("sensitivity_lsb", 1.0 / 3200)
        self._c     = [0, 0]
        self._hist  = {"x": [0, 0], "y": [0, 0], "z": [0, 0]}
        self._k     = 4
        self._ready = False

    def reset(self):
        self._ready = False
        for axis in self._hist:
            self._hist[axis] = [0, 0]

    def decode(self, payload: bytes, timestamp: float):
        header, samples = _parse_accel_block(payload)
        self._c, self._k = header.coefficients, header.k
        self._ready = True
        out = []
        for r in samples:
            vals = {"timestamp": timestamp}
            for axis in ("x", "y", "z"):
                h    = self._hist[axis]
                pred = (self._c[0] * h[-1] + self._c[1] * h[-2]) >> SHIFT
                real = pred + r[axis]
                h.append(real)
                h.pop(0)
                vals[f"{axis}_mm_s2"] = real * self._scale
            out.append(vals)
        return out
```

### Añadir un sensor de terceros — lo único que hay que escribir

```python
# klippy/extras/my_co2_sensor.py  (sensor de terceros, no toca nada del pipeline)

from .decompression.resolver import decoder_for
from .decompression.base import Decoder

@decoder_for("scd40_result")
class CO2Decoder(Decoder):
    data_type = "co2"

    def __init__(self, descriptor):
        self._co2   = 0
        self._scale = descriptor.get("scale", 1.0)

    def reset(self):
        self._co2 = 0

    def decode(self, payload, timestamp):
        delta     = _parse_delta(payload)
        self._co2 += delta
        return {"co2_ppm": self._co2 / self._scale, "timestamp": timestamp}
```

El pipeline lo descubre automáticamente en el próximo arranque porque `bootstrap()` itera todos los módulos del paquete `decoders/`. Si el fichero está en `decoders/`, no hay nada más que hacer.

---

## Cómo gestionar parámetros de calibración específicos de instancia

El decorador registra la **clase**. Los parámetros específicos de cada instancia (sensitivity, resolution_bits, etc.) llegan a través de `register_descriptor`, que el módulo extra puede llamar desde su `__init__` sin acoplarse al mecanismo de registro:

```python
# En adxl345.py — el módulo enriquece el descriptor, no gestiona el registro
class ADXL345:
    def __init__(self, config):
        sensitivity = config.getfloat("sensitivity", 1.0 / 3200)
        pipeline = self.printer.lookup_object("decompression_pipeline", None)
        if pipeline:
            pipeline.register_descriptor("adxl345_data", {
                "sensitivity_lsb": sensitivity,
                "chip": "adxl345",
            })
```

Separación clara: `@decoder_for` resuelve el **tipo** (clase), `register_descriptor` resuelve los **parámetros** (instancia). Son dos preguntas distintas y tienen dos respuestas distintas.

---

## Cómo funciona la descoberta automática de decodificadores

```python
# En cada fichero de decoder, junto al código que gestiona el dato:

@decoder_for("adxl345_data", "lis2dw_data", "lis3dh_data", "mpu9250_data", "bmi160_data")
class AccelDecoder(Decoder): ...

@decoder_for("angle_data")
class AngleDecoder(Decoder): ...

@decoder_for("ldc1612_data")
class EddyDecoder(Decoder): ...

# … y así para cada tipo. No hay fichero central que mantener.
```

---

## Resumen de la decisión

| Pregunta | Respuesta |
|---|---|
| ¿Qué mecanismo resuelve el tipo? | `@decoder_for` en la clase Decoder |
| ¿Cómo se descubren los módulos? | `pkgutil.iter_modules` + `importlib` en `bootstrap()` |
| ¿Cómo llegan los parámetros de instancia? | `pipeline.register_descriptor()` desde el módulo extra |
| ¿Qué pasa con tipos desconocidos? | `GenericBulkDecoder` como fallback |
| ¿Hay que tocar algo para añadir un sensor? | Solo añadir el fichero en `decoders/` con el decorador |
| ¿Funciona con sensores de terceros? | Sí, si ponen su `Decoder` en el paquete `decoders/` |
| ¿Complejidad total del mecanismo? | Un decorador + un `for` de `pkgutil` + un `dict.get` |