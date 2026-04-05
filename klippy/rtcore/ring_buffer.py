import ctypes

class LockFreeRingBuffer:
    """
    Implementación básica de un Ring Buffer circular lock-free utilizando variables atómicas.
    Ideal para comunicación entre hilos sin bloqueos (non-blocking communication).
    """
    def __init__(self, capacity):
        self.capacity = capacity
        # Asegurar que la capacidad es potencia de 2 para optimización de módulo (opcional pero recomendado)
        self.buffer = [None] * capacity
        
        # Punteros atómicos simulados mediante c_int para lectura y escritura
        self.head = ctypes.c_int(0)
        self.tail = ctypes.c_int(0)

    def push(self, item):
        """Inserta un elemento en el buffer sin bloquear."""
        current_tail = self.tail.value
        next_tail = (current_tail + 1) % self.capacity
        
        if next_tail == self.head.value:
            return False # Buffer lleno
            
        self.buffer[current_tail] = item
        # Operación atómica de actualización (en Python puro el GIL protege esto, en C se usaría atomic_store)
        self.tail.value = next_tail
        return True

    def pop(self):
        """Extrae un elemento del buffer sin bloquear."""
        current_head = self.head.value
        
        if current_head == self.tail.value:
            return None # Buffer vacío
            
        item = self.buffer[current_head]
        self.buffer[current_head] = None # Evitar retener referencias (ayuda al GC)
        self.head.value = (current_head + 1) % self.capacity
        return item
