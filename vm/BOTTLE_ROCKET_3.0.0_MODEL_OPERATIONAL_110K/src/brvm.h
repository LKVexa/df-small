#ifndef BOTTLE_ROCKET_BRVM_H
#define BOTTLE_ROCKET_BRVM_H

#include <stddef.h>
#include <stdint.h>

#define BR_VERSION_MAJOR 3u
#define BR_VERSION_MINOR 0u
#define BR_VERSION_PATCH 0u
#define BR_WORD_BITS 1048576u
#define BR_LIMBS 16384u
#define BR_REGS 16u
#define BR_CAPS 16u
#define BR_OPCODES 24u
#define BR_MAX_CODE 256u
#define BR_MEMORY_BYTES 4096u
#define BR_STACK_WORDS 256u
#define BR_DIAG_RECORDS 16u
#define BR_FRAGMENT_BYTES 4096u
#define BR_IMAGE_HEADER 80u
#define BR_INSN_BYTES 16u
#define BR_MAX_IMAGE 51200u

enum br_opcode {
    BR_NOP = 0, BR_MOVI, BR_MOV, BR_JMP, BR_JZ, BR_JNZ, BR_HALT,
    BR_ADD, BR_SUB, BR_MUL, BR_DIVU, BR_MODU, BR_AND, BR_OR, BR_XOR,
    BR_NOT, BR_SHL, BR_SHR, BR_CMP, BR_LOAD, BR_STORE, BR_PUSH, BR_POP,
    BR_SVC
};

enum br_arithmetic_mode {
    BR_WRAP = 0, BR_CHECKED = 1, BR_SATURATE = 2, BR_TRAPPING = 3
};

enum br_machine_status {
    BR_READY = 0, BR_RUNNING = 1, BR_HALTED = 2, BR_TRAPPED = 3,
    BR_CANCELLED = 4
};

enum br_trap {
    BR_TRAP_NONE = 0, BR_TRAP_BAD_IMAGE = 1, BR_TRAP_BAD_OPCODE = 2,
    BR_TRAP_CAPABILITY = 3, BR_TRAP_BOUNDS = 4, BR_TRAP_OVERFLOW = 5,
    BR_TRAP_DIV_ZERO = 6, BR_TRAP_STACK = 7, BR_TRAP_BUDGET = 8,
    BR_TRAP_CANCEL = 9, BR_TRAP_OOM = 10, BR_TRAP_STATE = 11,
    BR_TRAP_REPLAY = 12, BR_TRAP_INTEGRITY = 13, BR_TRAP_TRUST = 14,
    BR_TRAP_PROTOCOL = 15
};

enum br_capability {
    BR_CAP_CONTROL = 1u << 0, BR_CAP_ARITH = 1u << 1,
    BR_CAP_MEMORY = 1u << 2, BR_CAP_STACK = 1u << 3,
    BR_CAP_SERVICE = 1u << 4, BR_CAP_STATE = 1u << 5,
    BR_CAP_UPDATE = 1u << 6, BR_CAP_DIAG = 1u << 7,
    BR_CAP_ALL = 0xffu
};

enum br_flag {
    BR_FLAG_ZERO = 1u << 0, BR_FLAG_LESS = 1u << 1,
    BR_FLAG_GREATER = 1u << 2, BR_FLAG_CARRY = 1u << 3,
    BR_FLAG_OVERFLOW = 1u << 4
};

enum br_apdu_command {
    BR_APDU_INIT = 1, BR_APDU_STATUS = 2, BR_APDU_LOAD = 3,
    BR_APDU_EXEC = 4, BR_APDU_STATE = 5, BR_APDU_INPUT = 6,
    BR_APDU_UPDATE = 7, BR_APDU_RECOVER = 8, BR_APDU_DIAG = 9,
    BR_APDU_RESET = 10, BR_APDU_CAPS = 11, BR_APDU_HELLO = 12
};

enum br_apdu_status {
    BR_SW_OK = 0x9000, BR_SW_MORE = 0x6100, BR_SW_BAD_LENGTH = 0x6700,
    BR_SW_SECURITY = 0x6982, BR_SW_REPLAY = 0x6985,
    BR_SW_BAD_DATA = 0x6a80, BR_SW_NOT_FOUND = 0x6a88,
    BR_SW_BAD_INS = 0x6d00, BR_SW_INTERNAL = 0x6f00
};

typedef struct br_platform {
    void *ctx;
    int (*read)(void *ctx, const char *name, uint8_t *out, size_t capacity,
                size_t *length);
    int (*write)(void *ctx, const char *name, const uint8_t *data,
                 size_t length, int append);
    uint32_t capability_allowlist;
    char prefix[240];
} br_platform;

typedef struct {
    uint8_t op, mode, rd, ra, rb, cap;
    uint16_t flags;
    uint64_t imm;
} br_insn;

typedef struct {
    uint32_t seq;
    uint16_t event;
    uint16_t trap;
    uint32_t ip;
    uint8_t opcode;
    uint8_t status;
    uint16_t reserved;
} br_diag;

typedef struct {
    uint64_t *regs;
    uint64_t *tmp;
    uint64_t *tmp2;
    uint32_t caps[BR_CAPS];
    uint8_t memory[BR_MEMORY_BYTES];
    uint64_t *stack;
    br_insn code[BR_MAX_CODE];
    br_diag diag[BR_DIAG_RECORDS];
    uint8_t fragments[BR_FRAGMENT_BYTES];
    uint32_t ip, code_count, sp, flags, trap, status;
    uint32_t instruction_budget, service_budget, services_used;
    uint32_t persistent_generation, image_version, last_txid;
    uint32_t config_word, diag_seq, diag_head;
    uint32_t fragment_txid;
    uint32_t update_txid, update_total, update_received;
    uint32_t active_image_length, active_slot, capability_allowlist;
    uint16_t fragment_next_seq, fragment_length;
    uint16_t update_next_seq;
    uint8_t issuer_public_key[32];
    uint8_t active_image_hash[32];
    uint8_t arithmetic_mode, cancel_requested, initialized, issuer_key_set;
    br_platform *platform;
} br_vm;

int br_vm_init(br_vm *vm);
void br_vm_free(br_vm *vm);
void br_vm_reset(br_vm *vm, int cold);
int br_vm_load_code(br_vm *vm, const br_insn *code, size_t count);
int br_vm_step(br_vm *vm);
int br_vm_run(br_vm *vm, uint32_t budget);
int br_vm_save(br_vm *vm, const char *prefix);
int br_vm_recover(br_vm *vm, const char *prefix);
int br_platform_posix_init(br_platform *platform, const char *prefix,
                           uint32_t capability_allowlist);
int br_vm_bind_platform(br_vm *vm, br_platform *platform);
int br_image_write(const char *path, const br_insn *code, size_t count,
                   const uint8_t *data, size_t data_length, uint32_t version,
                   uint32_t requested_capabilities);
int br_image_load(br_vm *vm, const uint8_t *image, size_t length,
                  const uint8_t *ed25519_public_key, size_t key_length,
                  int signature_required);
int br_image_sign(uint8_t *image, size_t *length, size_t capacity,
                  const uint8_t *ed25519_private_key, size_t key_length);
int br_ed25519_public(const uint8_t *ed25519_private_key, size_t key_length,
                      uint8_t public_key[32]);
int br_vm_set_issuer_key(br_vm *vm, const uint8_t *public_key,
                         size_t key_length);
int br_image_inspect(const uint8_t *image, size_t length, uint32_t *version,
                     uint16_t *code_count, uint16_t *data_length,
                     uint32_t *requested_capabilities, int *signed_image);
size_t br_apdu(br_vm *vm, const uint8_t *request, size_t request_length,
               uint8_t *response, size_t response_capacity);
int br_selftest(void);

#endif
