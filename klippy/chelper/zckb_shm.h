#ifndef ZCKB_SHM_H
#define ZCKB_SHM_H

#include <stdint.h>
#include <stdatomic.h>
#include <pthread.h>
#include <sys/types.h>

/* Magic number para validación */
#define ZCKB_MAGIC 0x5A434B42  /* "ZCKB" */
#define ZCKB_VERSION 1
#define ZCKB_MAX_REGIONS 8

/* Tamaño del ring buffer (1MB por defecto) */
#define ZCKB_RING_SIZE (1024 * 1024)

/* Estado atómico del ring buffer (Lock-free) */
typedef struct {
    atomic_uint_fast64_t head;
    atomic_uint_fast64_t tail;
    atomic_uint_fast64_t overflow_count;
    atomic_uint_fast64_t underflow_count;
} zckb_ring_state_t;

/* Región de memoria compartida */
typedef struct {
    uint32_t magic;
    uint32_t version;
    int32_t fd;
    uint64_t size;
    void *base_addr;
    char name[64];
    zckb_ring_state_t tx_ring;
    zckb_ring_state_t rx_ring;
    uint8_t data[];  /* Flexible array member */
} zckb_region_t;

/* Contexto principal ZCKB */
typedef struct {
    zckb_region_t *regions[ZCKB_MAX_REGIONS];
    int region_count;
    int is_master;
    pid_t owner_pid;
    pthread_mutex_t lock;
} zckb_context_t;

/* Funciones públicas */
zckb_context_t* zckb_create_context(const char *name, int is_master);
void zckb_destroy_context(zckb_context_t *ctx);

int zckb_map_region(zckb_context_t *ctx, const char *name, uint64_t size);
void zckb_unmap_region(zckb_context_t *ctx, int region_id);

int zckb_ring_push(zckb_region_t *region, const void *data, uint32_t len);
int zckb_ring_pop(zckb_region_t *region, void *buffer, uint32_t max_len);

uint64_t zckb_get_timestamp_ns(void);
void zckb_memory_barrier(void);

#endif /* ZCKB_SHM_H */