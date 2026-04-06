#define _GNU_SOURCE
#include "zckb_shm.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>
#include <fcntl.h>
#include <sys/mman.h>
#include <sys/stat.h>
#include <errno.h>
#include <time.h>

#define SHM_PREFIX "/zckb_"

/* Obtener timestamp en nanosegundos (Alta precisión) */
__attribute__((hot))
uint64_t zckb_get_timestamp_ns(void) {
    struct timespec ts;
    clock_gettime(CLOCK_MONOTONIC_RAW, &ts);
    return (uint64_t)ts.tv_sec * 1000000000ULL + ts.tv_nsec;
}

/* Barrera de memoria para sincronización estricta */
__attribute__((always_inline))
inline void zckb_memory_barrier(void) {
    atomic_thread_fence(memory_order_seq_cst);
}

/* Crear contexto ZCKB */
zckb_context_t* zckb_create_context(const char *name, int is_master) {
    zckb_context_t *ctx = calloc(1, sizeof(zckb_context_t));
    if (!ctx) return NULL;
    
    ctx->is_master = is_master;
    ctx->owner_pid = getpid();
    ctx->region_count = 0;
    pthread_mutex_init(&ctx->lock, NULL);
    
    return ctx;
}

/* Destruir contexto y limpiar recursos */
void zckb_destroy_context(zckb_context_t *ctx) {
    if (!ctx) return;
    
    for (int i = 0; i < ctx->region_count; i++) {
        zckb_unmap_region(ctx, i);
    }
    
    pthread_mutex_destroy(&ctx->lock);
    free(ctx);
}

/* Mapear región de memoria compartida */
int zckb_map_region(zckb_context_t *ctx, const char *name, uint64_t size) {
    if (!ctx || ctx->region_count >= ZCKB_MAX_REGIONS) return -1;
    
    char shm_name[128];
    snprintf(shm_name, sizeof(shm_name), "%s%s", SHM_PREFIX, name);
    
    int fd;
    if (ctx->is_master) {
        /* Crear segmento nuevo */
        fd = shm_open(shm_name, O_CREAT | O_RDWR, 0666);
        if (fd < 0) return -1;
        
        if (ftruncate(fd, size) != 0) {
            close(fd);
            shm_unlink(shm_name);
            return -1;
        }
    } else {
        /* Abrir segmento existente */
        fd = shm_open(shm_name, O_RDWR, 0666);
        if (fd < 0) return -1;
    }
    
    /* Mapear memoria en espacio de usuario */
    void *addr = mmap(NULL, size, PROT_READ | PROT_WRITE, MAP_SHARED, fd, 0);
    if (addr == MAP_FAILED) {
        close(fd);
        return -1;
    }
    
    /* Inicializar región */
    zckb_region_t *region = (zckb_region_t*)addr;
    
    if (ctx->is_master) {
        memset(region, 0, size);
        region->magic = ZCKB_MAGIC;
        region->version = ZCKB_VERSION;
        region->fd = fd;
        region->size = size;
        strncpy(region->name, name, sizeof(region->name) - 1);
        
        atomic_store(&region->tx_ring.head, 0);
        atomic_store(&region->tx_ring.tail, 0);
        atomic_store(&region->rx_ring.head, 0);
        atomic_store(&region->rx_ring.tail, 0);
    } else {
        /* Verificar magia y versión */
        if (region->magic != ZCKB_MAGIC) {
            munmap(addr, size);
            close(fd);
            return -1;
        }
        region->fd = fd;
    }
    
    ctx->regions[ctx->region_count++] = region;
    return ctx->region_count - 1;
}

/* Desmapear región */
void zckb_unmap_region(zckb_context_t *ctx, int region_id) {
    if (!ctx || region_id < 0 || region_id >= ctx->region_count) return;
    
    zckb_region_t *region = ctx->regions[region_id];
    if (!region) return;
    
    munmap(region, region->size);
    close(region->fd);
    
    if (ctx->is_master) {
        char shm_name[128];
        snprintf(shm_name, sizeof(shm_name), "%s%s", SHM_PREFIX, region->name);
        shm_unlink(shm_name);
    }
    
    ctx->regions[region_id] = NULL;
}

/* Push lock-free al ring buffer (Zero-Copy) */
__attribute__((hot, optimize("O3")))
int zckb_ring_push(zckb_region_t *region, const void *data, uint32_t len) {
    if (!region || !data || len == 0) return -1;
    if (len > ZCKB_RING_SIZE - sizeof(zckb_ring_state_t)) return -1;
    
    zckb_ring_state_t *state = &region->tx_ring;
    uint64_t head = atomic_load_explicit(&state->head, memory_order_relaxed);
    uint64_t tail = atomic_load_explicit(&state->tail, memory_order_acquire);
    
    uint64_t next_head = (head + 1) % (ZCKB_RING_SIZE - sizeof(zckb_ring_state_t));
    
    /* Verificar espacio disponible */
    if (next_head == tail) {
        atomic_fetch_add(&state->overflow_count, 1);
        return -1;  /* Buffer lleno */
    }
    
    /* Copiar datos directamente a memoria compartida */
    uint8_t *buffer = region->data;
    buffer[head] = (uint8_t)len;
    memcpy(&buffer[(head + 1) % (ZCKB_RING_SIZE - sizeof(zckb_ring_state_t))], data, len);
    
    /* Actualizar head con barrera de memoria */
    atomic_store_explicit(&state->head, next_head, memory_order_release);
    zckb_memory_barrier();
    
    return 0;
}

/* Pop lock-free del ring buffer (Zero-Copy) */
__attribute__((hot, optimize("O3")))
int zckb_ring_pop(zckb_region_t *region, void *buffer, uint32_t max_len) {
    if (!region || !buffer || max_len == 0) return -1;
    
    zckb_ring_state_t *state = &region->rx_ring;
    uint64_t tail = atomic_load_explicit(&state->tail, memory_order_relaxed);
    uint64_t head = atomic_load_explicit(&state->head, memory_order_acquire);
    
    if (tail == head) {
        atomic_fetch_add(&state->underflow_count, 1);
        return 0;  /* Buffer vacío */
    }
    
    uint8_t *ring_buffer = region->data;
    uint8_t len = ring_buffer[tail];
    
    if (len > max_len) {
        return -1;  /* Buffer destino demasiado pequeño */
    }
    
    uint64_t data_offset = (tail + 1) % (ZCKB_RING_SIZE - sizeof(zckb_ring_state_t));
    memcpy(buffer, &ring_buffer[data_offset], len);
    
    uint64_t next_tail = (tail + 1) % (ZCKB_RING_SIZE - sizeof(zckb_ring_state_t));
    atomic_store_explicit(&state->tail, next_tail, memory_order_release);
    zckb_memory_barrier();
    
    return len;
}