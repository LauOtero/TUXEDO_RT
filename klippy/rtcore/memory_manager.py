class PooledDict(dict):
    __slots__ = ('__pool_index__',)

class DeterministicMemoryPool:
    """
    Gestor de memoria pre-asignada. Evita el uso del recolector de basura (GC)
    estándar de Python durante operaciones críticas en tiempo real (Object Pooling).
    En lugar de crear/destruir objetos frecuentemente, se reutilizan.
    """
    def __init__(self, factory=PooledDict, initial_size=1000):
        self.object_class = factory
        self.pool = [self.object_class() for _ in range(initial_size)]
        self.free_indices = list(range(initial_size))
        
        # Uso de un lock rápido o lock-free para hilos (simplificado para diseño conceptual)
        self.lock = __import__('threading').Lock()

    def acquire(self):
        """Obtiene un objeto pre-asignado del pool en O(1)."""
        with self.lock:
            if not self.free_indices:
                # El pool se ha quedado sin objetos. En un entorno de tiempo real estricto, 
                # esto es un error fatal (OOM determinista).
                raise MemoryError("Pool de memoria exhausto: no hay más objetos pre-asignados disponibles.")
            
            index = self.free_indices.pop()
            obj = self.pool[index]
            obj.__pool_index__ = index # Rastrear su índice para liberarlo
            return obj

    def release(self, obj):
        """Devuelve un objeto al pool para su reutilización."""
        with self.lock:
            if hasattr(obj, '__pool_index__'):
                # Limpiar estado del objeto si es necesario (ej: obj.reset())
                if hasattr(obj, 'reset'):
                    obj.reset()
                self.free_indices.append(obj.__pool_index__)
                # Do NOT delattr, just reuse it
            else:
                raise ValueError("El objeto no pertenece a este pool.")

class RTMemoryManager:
    """
    Fábrica que centraliza múltiples pools de memoria pre-asignados.
    Permite controlar las asignaciones de todos los subsistemas críticos.
    """
    def __init__(self):
        self.pools = {}

    def register_pool(self, name, object_class=PooledDict, size=1000):
        self.pools[name] = DeterministicMemoryPool(object_class, size)

    def get(self, name):
        return self.pools[name].acquire()

    def free(self, name, obj):
        self.pools[name].release(obj)
