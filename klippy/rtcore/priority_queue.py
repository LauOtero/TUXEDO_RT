from collections import deque
import threading

class MultiPriorityQueue:
    """
    Sistema de colas de prioridad múltiple (Critical, High, Normal, Low)
    garantizando que las tareas de movimiento y térmicas no sean bloqueadas.
    """
    PRIORITY_CRITICAL = 0
    PRIORITY_HIGH = 1
    PRIORITY_NORMAL = 2
    PRIORITY_LOW = 3

    def __init__(self):
        # 4 niveles de colas (0 = máxima prioridad, 3 = mínima)
        self.queues = {
            self.PRIORITY_CRITICAL: deque(),
            self.PRIORITY_HIGH: deque(),
            self.PRIORITY_NORMAL: deque(),
            self.PRIORITY_LOW: deque()
        }
        # Lock de grano fino para protección entre hilos
        self.lock = threading.Lock()
        self.event = threading.Event()

    def enqueue(self, item, priority=PRIORITY_NORMAL):
        """Añade una tarea a la cola especificada."""
        if priority not in self.queues:
            priority = self.PRIORITY_NORMAL
            
        with self.lock:
            self.queues[priority].append(item)
            self.event.set()

    def dequeue(self):
        """
        Extrae y devuelve la tarea de mayor prioridad.
        Bloquea si no hay tareas, pero se despierta inmediatamente al insertar.
        """
        self.event.wait()
        
        with self.lock:
            for priority in range(4): # De 0 a 3
                if self.queues[priority]:
                    item = self.queues[priority].popleft()
                    # Si todas las colas están vacías, resetear evento
                    if all(not q for q in self.queues.values()):
                        self.event.clear()
                    return item, priority
        return None, None

    def size(self, priority):
        with self.lock:
            return len(self.queues[priority])

    def is_empty(self):
        with self.lock:
            return all(not q for q in self.queues.values())
