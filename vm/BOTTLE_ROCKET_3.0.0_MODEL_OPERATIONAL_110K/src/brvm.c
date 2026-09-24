#define _POSIX_C_SOURCE 200809L
#include "brvm.h"
#include "brasm.h"

#include <errno.h>
#include <fcntl.h>
#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <openssl/sha.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <unistd.h>

#define BR_IMAGE_FLAG_FACTORY 1u
#define BR_IMAGE_FLAG_SIGNED 2u
#define BR_APDU_FLAG_MORE 1u
#define BR_PERSIST_MAGIC 0x42525053u

static uint16_t get16(const uint8_t *p) {
    return (uint16_t)((uint16_t)p[0] | ((uint16_t)p[1] << 8));
}

static uint32_t get32(const uint8_t *p) {
    return (uint32_t)p[0] | ((uint32_t)p[1] << 8) |
           ((uint32_t)p[2] << 16) | ((uint32_t)p[3] << 24);
}

static uint64_t get64(const uint8_t *p) {
    uint64_t value = 0;
    unsigned i;
    for (i = 0; i < 8; ++i) value |= (uint64_t)p[i] << (8u * i);
    return value;
}

static void put16(uint8_t *p, uint16_t value) {
    p[0] = (uint8_t)value; p[1] = (uint8_t)(value >> 8);
}

static void put32(uint8_t *p, uint32_t value) {
    p[0] = (uint8_t)value; p[1] = (uint8_t)(value >> 8);
    p[2] = (uint8_t)(value >> 16); p[3] = (uint8_t)(value >> 24);
}

static void put64(uint8_t *p, uint64_t value) {
    unsigned i;
    for (i = 0; i < 8; ++i) p[i] = (uint8_t)(value >> (8u * i));
}

static uint32_t crc32_bytes(const uint8_t *data, size_t length) {
    uint32_t crc = 0xffffffffu;
    size_t i;
    unsigned bit;
    for (i = 0; i < length; ++i) {
        crc ^= data[i];
        for (bit = 0; bit < 8; ++bit)
            crc = (crc >> 1) ^ (0xedb88320u & (0u - (crc & 1u)));
    }
    return ~crc;
}

static uint64_t *regp(br_vm *vm, unsigned reg) {
    return vm->regs + ((size_t)reg * BR_LIMBS);
}

static void diag_add(br_vm *vm, uint16_t event, uint8_t opcode) {
    br_diag *d = &vm->diag[vm->diag_head++ % BR_DIAG_RECORDS];
    memset(d, 0, sizeof(*d));
    d->seq = ++vm->diag_seq;
    d->event = event;
    d->trap = (uint16_t)vm->trap;
    d->ip = vm->ip;
    d->opcode = opcode;
    d->status = (uint8_t)vm->status;
}

static int trap(br_vm *vm, unsigned reason, uint8_t opcode) {
    vm->trap = reason;
    vm->status = BR_TRAPPED;
    diag_add(vm, 2u, opcode);
    return -1;
}

int br_vm_init(br_vm *vm) {
    size_t words = (size_t)BR_REGS * BR_LIMBS;
    if (vm == NULL) return -1;
    memset(vm, 0, sizeof(*vm));
    vm->regs = calloc(words, sizeof(uint64_t));
    vm->tmp = calloc(BR_LIMBS, sizeof(uint64_t));
    vm->tmp2 = calloc(BR_LIMBS, sizeof(uint64_t));
    vm->stack = calloc((size_t)BR_STACK_WORDS * BR_LIMBS, sizeof(uint64_t));
    if (vm->regs == NULL || vm->tmp == NULL || vm->tmp2 == NULL ||
        vm->stack == NULL) {
        br_vm_free(vm);
        vm->trap=BR_TRAP_OOM;vm->status=BR_TRAPPED;
        return -1;
    }
    vm->caps[0] = BR_CAP_ALL;
    vm->instruction_budget = 4096u;
    vm->service_budget = 64u;
    vm->capability_allowlist = BR_CAP_ALL;
    vm->status = BR_READY;
    vm->initialized = 1u;
    diag_add(vm, 1u, BR_NOP);
    return 0;
}

void br_vm_free(br_vm *vm) {
    if (vm == NULL) return;
    if (vm->regs != NULL)
        OPENSSL_cleanse(vm->regs, (size_t)BR_REGS * BR_LIMBS * sizeof(uint64_t));
    if (vm->tmp != NULL) OPENSSL_cleanse(vm->tmp, BR_LIMBS * sizeof(uint64_t));
    if (vm->tmp2 != NULL) OPENSSL_cleanse(vm->tmp2, BR_LIMBS * sizeof(uint64_t));
    if (vm->stack != NULL) OPENSSL_cleanse(vm->stack,
        (size_t)BR_STACK_WORDS * BR_LIMBS * sizeof(uint64_t));
    free(vm->regs); free(vm->tmp); free(vm->tmp2); free(vm->stack);
    memset(vm, 0, sizeof(*vm));
}

void br_vm_reset(br_vm *vm, int cold) {
    uint32_t generation, version, txid, config;
    if (vm == NULL || vm->regs == NULL) return;
    generation = vm->persistent_generation;
    version = vm->image_version;
    txid = vm->last_txid;
    config = vm->config_word;
    memset(vm->regs, 0, (size_t)BR_REGS * BR_LIMBS * sizeof(uint64_t));
    memset(vm->tmp, 0, BR_LIMBS * sizeof(uint64_t));
    memset(vm->tmp2, 0, BR_LIMBS * sizeof(uint64_t));
    memset(vm->memory, 0, sizeof(vm->memory));
    memset(vm->stack, 0, (size_t)BR_STACK_WORDS * BR_LIMBS * sizeof(uint64_t));
    memset(vm->fragments, 0, sizeof(vm->fragments));
    vm->ip = vm->sp = vm->flags = vm->trap = vm->services_used = 0;
    vm->fragment_txid = vm->fragment_next_seq = vm->fragment_length = 0;
    vm->cancel_requested = 0;
    vm->arithmetic_mode = 0;
    vm->instruction_budget = 4096u;
    vm->service_budget = 64u;
    vm->status = BR_READY;
    /* Capability grants are volatile and never survive any reset class. */
    memset(vm->caps, 0, sizeof(vm->caps));
    vm->caps[0] = vm->capability_allowlist;
    vm->persistent_generation = generation;
    vm->image_version = version;
    vm->last_txid = txid;
    vm->config_word = config;
    diag_add(vm, cold ? 3u : 4u, BR_NOP);
}

int br_vm_load_code(br_vm *vm, const br_insn *code, size_t count) {
    if (vm == NULL || code == NULL || count == 0 || count > BR_MAX_CODE)
        return -1;
    memcpy(vm->code, code, count * sizeof(*code));
    vm->code_count = (uint32_t)count;
    vm->ip = 0;
    vm->trap = BR_TRAP_NONE;
    vm->status = BR_READY;
    return 0;
}

static size_t active_limbs(const uint64_t *a) {
    size_t n = BR_LIMBS;
    while (n != 0 && a[n - 1] == 0) --n;
    return n;
}

static int word_cmp(const uint64_t *a, const uint64_t *b) {
    size_t i = BR_LIMBS;
    while (i != 0) {
        --i;
        if (a[i] < b[i]) return -1;
        if (a[i] > b[i]) return 1;
    }
    return 0;
}

static int word_cmp_n(const uint64_t *a, size_t an, const uint64_t *b, size_t bn) {
    size_t i;
    while (an != 0 && a[an - 1] == 0) --an;
    while (bn != 0 && b[bn - 1] == 0) --bn;
    if (an < bn) return -1;
    if (an > bn) return 1;
    i = an;
    while (i != 0) {
        --i;
        if (a[i] < b[i]) return -1;
        if (a[i] > b[i]) return 1;
    }
    return 0;
}

static int word_add(uint64_t *out, const uint64_t *a, const uint64_t *b) {
    unsigned __int128 total;
    uint64_t carry = 0;
    size_t i;
    for (i = 0; i < BR_LIMBS; ++i) {
        total = (unsigned __int128)a[i] + b[i] + carry;
        out[i] = (uint64_t)total;
        carry = (uint64_t)(total >> 64);
    }
    return carry != 0;
}

static int word_sub(uint64_t *out, const uint64_t *a, const uint64_t *b) {
    uint64_t borrow = 0;
    size_t i;
    for (i = 0; i < BR_LIMBS; ++i) {
        uint64_t x = a[i], y = b[i];
        uint64_t next = (x < y) || (borrow && x == y);
        out[i] = x - y - borrow;
        borrow = next;
    }
    return borrow != 0;
}

static int word_mul(uint64_t *out, const uint64_t *a, const uint64_t *b) {
    size_t an = active_limbs(a), bn = active_limbs(b), i, j;
    int overflow = 0;
    memset(out, 0, BR_LIMBS * sizeof(uint64_t));
    for (i = 0; i < an; ++i) {
        uint64_t carry = 0;
        for (j = 0; j < bn; ++j) {
            size_t k = i + j;
            unsigned __int128 total;
            if (k >= BR_LIMBS) {
                if (a[i] != 0 && b[j] != 0) overflow = 1;
                continue;
            }
            total = (unsigned __int128)a[i] * b[j] + out[k] + carry;
            out[k] = (uint64_t)total;
            carry = (uint64_t)(total >> 64);
        }
        if (carry != 0) {
            size_t k = i + bn;
            while (carry != 0 && k < BR_LIMBS) {
                unsigned __int128 total = (unsigned __int128)out[k] + carry;
                out[k++] = (uint64_t)total;
                carry = (uint64_t)(total >> 64);
            }
            if (carry != 0) overflow = 1;
        }
    }
    return overflow;
}

static int highest_bit(const uint64_t *a) {
    size_t n = active_limbs(a);
    uint64_t top;
    if (n == 0) return -1;
    top = a[n - 1];
    return (int)((n - 1) * 64u + 63u - (unsigned)__builtin_clzll(top));
}

static void word_shift_left_one_n(uint64_t *a, size_t *length) {
    uint64_t carry = 0;
    size_t i;
    size_t n = *length;
    for (i = 0; i < n; ++i) {
        uint64_t next = a[i] >> 63;
        a[i] = (a[i] << 1) | carry;
        carry = next;
    }
    if (carry != 0 && n < BR_LIMBS) a[n++] = carry;
    *length = n;
}

static void word_sub_n(uint64_t *a, size_t *an, const uint64_t *b, size_t bn) {
    uint64_t borrow = 0;
    size_t i;
    for (i = 0; i < bn; ++i) {
        uint64_t x = a[i], y = b[i];
        uint64_t next = (x < y) || (borrow && x == y);
        a[i] = x - y - borrow; borrow = next;
    }
    while (borrow != 0 && i < *an) {
        uint64_t x = a[i]; a[i] = x - 1u; borrow = x == 0; ++i;
    }
    while (*an != 0 && a[*an - 1] == 0) --*an;
}

static int word_divmod(uint64_t *q, uint64_t *rem, const uint64_t *a,
                       const uint64_t *b, uint32_t budget) {
    int bit = highest_bit(a);
    uint32_t used = 0;
    size_t bn = active_limbs(b), rn = 0;
    if (bn == 0) return -1;
    memset(q, 0, BR_LIMBS * sizeof(uint64_t));
    memset(rem, 0, BR_LIMBS * sizeof(uint64_t));
    while (bit >= 0) {
        word_shift_left_one_n(rem, &rn);
        rem[0] |= (a[(unsigned)bit / 64u] >> ((unsigned)bit % 64u)) & 1u;
        if (rem[0] != 0 && rn == 0) rn = 1;
        if (word_cmp_n(rem, rn, b, bn) >= 0) {
            word_sub_n(rem, &rn, b, bn);
            q[(unsigned)bit / 64u] |= (uint64_t)1u << ((unsigned)bit % 64u);
        }
        if (++used > budget) return -2;
        --bit;
    }
    return 0;
}

static int word_shift(uint64_t *out, const uint64_t *a, uint32_t bits,
                      int left) {
    uint32_t limb = bits / 64u, sub = bits % 64u;
    size_t i;
    int overflow = 0;
    memset(out, 0, BR_LIMBS * sizeof(uint64_t));
    if (bits >= BR_WORD_BITS) {
        overflow = active_limbs(a) != 0;
        return overflow;
    }
    if (left) {
        for (i = 0; i < BR_LIMBS; ++i) {
            if (a[i] == 0) continue;
            if (i + limb < BR_LIMBS) {
                out[i + limb] |= a[i] << sub;
                if (sub && i + limb + 1 < BR_LIMBS)
                    out[i + limb + 1] |= a[i] >> (64u - sub);
                else if (sub && (a[i] >> (64u - sub)) != 0) overflow = 1;
            } else overflow = 1;
        }
    } else {
        for (i = limb; i < BR_LIMBS; ++i) {
            out[i - limb] |= a[i] >> sub;
            if (sub && i > limb) out[i - limb - 1] |= a[i] << (64u - sub);
        }
    }
    return overflow;
}

static uint32_t opcode_capability(uint8_t op) {
    if (op <= BR_HALT) return BR_CAP_CONTROL;
    if (op <= BR_CMP) return BR_CAP_ARITH;
    if (op <= BR_STORE) return BR_CAP_MEMORY;
    if (op <= BR_POP) return BR_CAP_STACK;
    return BR_CAP_SERVICE;
}

static int validate_insn(const br_insn *in) {
    if (in->op >= BR_OPCODES || in->mode > BR_TRAPPING || in->cap >= BR_CAPS)
        return -1;
    if (in->rd >= BR_REGS || in->ra >= BR_REGS || in->rb >= BR_REGS)
        return -1;
    return 0;
}

static int arithmetic_result(br_vm *vm, const br_insn *in, int overflow,
                             int underflow) {
    uint64_t *dst = regp(vm, in->rd);
    unsigned mode = in->mode;
    vm->flags &= ~(BR_FLAG_CARRY | BR_FLAG_OVERFLOW);
    if (overflow || underflow) vm->flags |= BR_FLAG_OVERFLOW;
    if ((mode == BR_CHECKED || mode == BR_TRAPPING) && (overflow || underflow))
        return trap(vm, BR_TRAP_OVERFLOW, in->op);
    if (mode == BR_SATURATE && (overflow || underflow)) {
        memset(dst, underflow ? 0x00 : 0xff, BR_LIMBS * sizeof(uint64_t));
    } else {
        memcpy(dst, vm->tmp, BR_LIMBS * sizeof(uint64_t));
    }
    if (overflow) vm->flags |= BR_FLAG_CARRY;
    return 0;
}

static int verify_ed25519(const uint8_t *, size_t, const uint8_t *, size_t,
                          const uint8_t[64]);

static int service_call(br_vm *vm, const br_insn *in) {
    uint64_t *rd = regp(vm, in->rd);
    unsigned target, requested;
    size_t off, length, output;
    if (++vm->services_used > vm->service_budget)
        return trap(vm, BR_TRAP_BUDGET, in->op);
    switch ((unsigned)in->imm) {
        case 0: /* cooperative yield */
            return 0;
        case 1: /* compact status */
            memset(rd, 0, BR_LIMBS * sizeof(uint64_t));
            rd[0] = ((uint64_t)vm->trap << 32) | vm->status;
            return 0;
        case 2: /* revoke target capability register */
            target = (unsigned)(regp(vm, in->ra)[0] & 15u);
            vm->caps[target] = 0;
            return 0;
        case 3: /* delegate subset: target in RA, mask in RB */
            target = (unsigned)(regp(vm, in->ra)[0] & 15u);
            requested = (unsigned)regp(vm, in->rb)[0];
            if ((requested & ~vm->caps[in->cap]) != 0)
                return trap(vm, BR_TRAP_CAPABILITY, in->op);
            vm->caps[target] = requested;
            return 0;
        case 4: /* bounded configuration propagation */
            vm->config_word = (uint32_t)regp(vm, in->ra)[0];
            return 0;
        case 5: /* persistent generation commit marker */
            ++vm->persistent_generation;
            return 0;
        case 6: /* diagnostic event */
            diag_add(vm, (uint16_t)regp(vm, in->ra)[0], in->op);
            return 0;
        case 7: /* SHA-256(memory[RA..RA+RB), memory[RD..RD+32)) */
            off = (size_t)regp(vm, in->ra)[0];
            length = (size_t)regp(vm, in->rb)[0]; output = (size_t)rd[0];
            if (off > BR_MEMORY_BYTES || length > BR_MEMORY_BYTES - off ||
                output > BR_MEMORY_BYTES - SHA256_DIGEST_LENGTH)
                return trap(vm, BR_TRAP_BOUNDS, in->op);
            if (SHA256(vm->memory + off, length, vm->memory + output) == NULL)
                return trap(vm, BR_TRAP_STATE, in->op);
            return 0;
        case 8: /* Ed25519(memory message, memory signature, issuer key) */
            off = (size_t)regp(vm, in->ra)[0];
            length = (size_t)regp(vm, in->rb)[0]; output = (size_t)rd[0];
            if (off > BR_MEMORY_BYTES || length > BR_MEMORY_BYTES - off ||
                output > BR_MEMORY_BYTES - 64u || !vm->issuer_key_set)
                return trap(vm, !vm->issuer_key_set ? BR_TRAP_TRUST : BR_TRAP_BOUNDS,
                            in->op);
            memset(rd, 0, BR_LIMBS * sizeof(uint64_t));
            rd[0] = (uint64_t)verify_ed25519(vm->issuer_public_key, 32,
                vm->memory + off, length, vm->memory + output);
            return 0;
        default:
            return trap(vm, BR_TRAP_BAD_OPCODE, in->op);
    }
}

int br_vm_step(br_vm *vm) {
    br_insn in;
    uint64_t *dst, *a, *b;
    uint32_t next;
    int overflow = 0, rc;
    size_t i;
    if (vm == NULL || vm->regs == NULL || vm->code_count == 0)
        return -1;
    if (vm->cancel_requested) return trap(vm, BR_TRAP_CANCEL, BR_NOP);
    if (vm->ip >= vm->code_count) return trap(vm, BR_TRAP_BOUNDS, BR_NOP);
    in = vm->code[vm->ip];
    if (validate_insn(&in) != 0) return trap(vm, BR_TRAP_BAD_OPCODE, in.op);
    if ((vm->caps[in.cap] & opcode_capability(in.op)) == 0)
        return trap(vm, BR_TRAP_CAPABILITY, in.op);
    if (in.op == BR_SVC) {
        uint32_t needed = in.imm <= 3u || in.imm == 7u ? BR_CAP_SERVICE :
                          in.imm <= 5u ? BR_CAP_STATE :
                          in.imm == 6u ? BR_CAP_DIAG :
                          in.imm == 8u ? BR_CAP_UPDATE : 0u;
        if (needed == 0 || (vm->caps[in.cap] & needed) == 0)
            return trap(vm, needed ? BR_TRAP_CAPABILITY : BR_TRAP_BAD_OPCODE,
                        in.op);
    }
    vm->status = BR_RUNNING;
    next = vm->ip + 1u;
    dst = regp(vm, in.rd); a = regp(vm, in.ra); b = regp(vm, in.rb);
    switch (in.op) {
        case BR_NOP: break;
        case BR_MOVI:
            memset(dst, 0, BR_LIMBS * sizeof(uint64_t)); dst[0] = in.imm; break;
        case BR_MOV: memcpy(dst, a, BR_LIMBS * sizeof(uint64_t)); break;
        case BR_JMP:
            if (in.imm >= vm->code_count) return trap(vm, BR_TRAP_BOUNDS, in.op);
            next = (uint32_t)in.imm; break;
        case BR_JZ:
            if ((vm->flags & BR_FLAG_ZERO) != 0) {
                if (in.imm >= vm->code_count) return trap(vm, BR_TRAP_BOUNDS, in.op);
                next = (uint32_t)in.imm;
            }
            break;
        case BR_JNZ:
            if ((vm->flags & BR_FLAG_ZERO) == 0) {
                if (in.imm >= vm->code_count) return trap(vm, BR_TRAP_BOUNDS, in.op);
                next = (uint32_t)in.imm;
            }
            break;
        case BR_HALT: vm->status = BR_HALTED; break;
        case BR_ADD:
            overflow = word_add(vm->tmp, a, b);
            if (arithmetic_result(vm, &in, overflow, 0) != 0) return -1;
            break;
        case BR_SUB:
            overflow = word_sub(vm->tmp, a, b);
            if (arithmetic_result(vm, &in, 0, overflow) != 0) return -1;
            break;
        case BR_MUL:
            overflow = word_mul(vm->tmp, a, b);
            if (arithmetic_result(vm, &in, overflow, 0) != 0) return -1;
            break;
        case BR_DIVU: case BR_MODU:
            rc = word_divmod(vm->tmp, vm->tmp2, a, b,
                             vm->instruction_budget >= BR_WORD_BITS / 256u ?
                             BR_WORD_BITS : vm->instruction_budget * 256u);
            if (rc == -1) return trap(vm, BR_TRAP_DIV_ZERO, in.op);
            if (rc == -2) return trap(vm, BR_TRAP_BUDGET, in.op);
            memcpy(dst, in.op == BR_DIVU ? vm->tmp : vm->tmp2,
                   BR_LIMBS * sizeof(uint64_t));
            break;
        case BR_AND: case BR_OR: case BR_XOR:
            for (i = 0; i < BR_LIMBS; ++i)
                dst[i] = in.op == BR_AND ? (a[i] & b[i]) :
                         in.op == BR_OR ? (a[i] | b[i]) : (a[i] ^ b[i]);
            break;
        case BR_NOT:
            for (i = 0; i < BR_LIMBS; ++i) dst[i] = ~a[i];
            break;
        case BR_SHL: case BR_SHR:
            overflow = word_shift(vm->tmp, a, (uint32_t)in.imm, in.op == BR_SHL);
            if (arithmetic_result(vm, &in, overflow, 0) != 0) return -1;
            break;
        case BR_CMP:
            rc = word_cmp(a, b);
            vm->flags &= ~(BR_FLAG_ZERO | BR_FLAG_LESS | BR_FLAG_GREATER);
            vm->flags |= rc == 0 ? BR_FLAG_ZERO : rc < 0 ? BR_FLAG_LESS : BR_FLAG_GREATER;
            break;
        case BR_LOAD:
            if (in.imm > BR_MEMORY_BYTES - 8u) return trap(vm, BR_TRAP_BOUNDS, in.op);
            memset(dst, 0, BR_LIMBS * sizeof(uint64_t)); dst[0] = get64(vm->memory + in.imm);
            break;
        case BR_STORE:
            if (in.imm > BR_MEMORY_BYTES - 8u) return trap(vm, BR_TRAP_BOUNDS, in.op);
            put64(vm->memory + in.imm, a[0]);
            break;
        case BR_PUSH:
            if (vm->sp >= BR_STACK_WORDS) return trap(vm, BR_TRAP_STACK, in.op);
            memcpy(vm->stack + (size_t)vm->sp++ * BR_LIMBS, a,
                   BR_LIMBS * sizeof(uint64_t));
            break;
        case BR_POP:
            if (vm->sp == 0) return trap(vm, BR_TRAP_STACK, in.op);
            memcpy(dst, vm->stack + (size_t)--vm->sp * BR_LIMBS,
                   BR_LIMBS * sizeof(uint64_t));
            break;
        case BR_SVC:
            if (service_call(vm, &in) != 0) return -1;
            break;
        default: return trap(vm, BR_TRAP_BAD_OPCODE, in.op);
    }
    vm->ip = next;
    if (vm->status == BR_RUNNING && vm->ip >= vm->code_count)
        return trap(vm, BR_TRAP_BOUNDS, in.op);
    return 0;
}

int br_vm_run(br_vm *vm, uint32_t budget) {
    uint32_t used = 0;
    if (vm == NULL || budget == 0) return -1;
    vm->instruction_budget = budget;
    vm->services_used = 0;
    while (vm->status != BR_HALTED && vm->status != BR_TRAPPED &&
           vm->status != BR_CANCELLED) {
        if (used++ >= budget) return trap(vm, BR_TRAP_BUDGET, BR_NOP);
        if (br_vm_step(vm) != 0) return -1;
    }
    return vm->status == BR_HALTED ? 0 : -1;
}

static void persist_encode(const br_vm *vm, uint8_t out[512], uint32_t generation) {
    unsigned i;
    memset(out, 0, 512);
    put32(out + 0, BR_PERSIST_MAGIC);
    put32(out + 4, generation);
    put32(out + 8, vm->image_version);
    put32(out + 12, vm->last_txid);
    put32(out + 16, vm->config_word);
    put32(out + 20, vm->diag_seq);
    put32(out + 24, vm->active_slot); put32(out + 28, vm->active_image_length);
    memcpy(out + 32, vm->active_image_hash, 32);
    put32(out + 64, vm->capability_allowlist);
    /* Capability grants are always volatile. */
    for (i = 0; i < BR_DIAG_RECORDS; ++i) {
        const br_diag *d = &vm->diag[i];
        uint8_t *p = out + 88 + i * 16u;
        put32(p, d->seq); put16(p + 4, d->event); put16(p + 6, d->trap);
        put32(p + 8, d->ip); p[12] = d->opcode; p[13] = d->status;
    }
    put32(out + 508, crc32_bytes(out, 508));
}

static int persist_decode(br_vm *vm, const uint8_t in[512], uint32_t *generation) {
    unsigned i;
    if (get32(in) != BR_PERSIST_MAGIC || get32(in + 508) != crc32_bytes(in, 508))
        return -1;
    *generation = get32(in + 4);
    vm->image_version = get32(in + 8);
    vm->last_txid = get32(in + 12);
    vm->config_word = get32(in + 16);
    vm->diag_seq = get32(in + 20);
    vm->active_slot = get32(in + 24); vm->active_image_length = get32(in + 28);
    memcpy(vm->active_image_hash, in + 32, 32);
    vm->capability_allowlist = (get32(in + 64) & BR_CAP_ALL) &
        (vm->platform ? vm->platform->capability_allowlist : BR_CAP_ALL);
    if (vm->active_slot > 1u || vm->active_image_length > BR_MAX_IMAGE)
        return -1;
    memset(vm->caps, 0, sizeof(vm->caps));
    vm->caps[0] = vm->capability_allowlist;
    for (i = 0; i < BR_DIAG_RECORDS; ++i) {
        br_diag *d = &vm->diag[i];
        const uint8_t *p = in + 88 + i * 16u;
        d->seq = get32(p); d->event = get16(p + 4); d->trap = get16(p + 6);
        d->ip = get32(p + 8); d->opcode = p[12]; d->status = p[13];
    }
    return 0;
}

static int read_record(const char *path, uint8_t out[512], uint32_t *generation) {
    FILE *fp = fopen(path, "rb");
    size_t n;
    if (fp == NULL) return -1;
    n = fread(out, 1, 512, fp);
    if (fclose(fp) != 0 || n != 512 || get32(out) != BR_PERSIST_MAGIC ||
        get32(out + 508) != crc32_bytes(out, 508)) return -1;
    *generation = get32(out + 4);
    return 0;
}

static int platform_path(char out[512], const char *prefix, const char *name) {
    return prefix != NULL && name != NULL && strlen(prefix) <= 480u &&
           snprintf(out, 512, "%s.%s", prefix, name) > 0 ? 0 : -1;
}

static int posix_read(void *ctx, const char *name, uint8_t *out, size_t cap,
                      size_t *length) {
    char path[512]; FILE *fp; long n;
    if (out == NULL || length == NULL || platform_path(path, ctx, name) != 0 ||
        (fp = fopen(path, "rb")) == NULL) return -1;
    if (fseek(fp, 0, SEEK_END) != 0 || (n = ftell(fp)) < 0 ||
        (size_t)n > cap || fseek(fp, 0, SEEK_SET) != 0 ||
        fread(out, 1, (size_t)n, fp) != (size_t)n) { fclose(fp); return -1; }
    if(fclose(fp)!=0)return -1;
    *length = (size_t)n; return 0;
}

static int sync_parent(const char *path){
    char p[512],*slash;int fd;snprintf(p,sizeof(p),"%s",path);slash=strrchr(p,'/');
    if(!slash)strcpy(p,".");else if(slash==p)slash[1]=0;else *slash=0;
    fd=open(p,O_RDONLY);if(fd<0)return -1;
    if(fsync(fd)!=0){close(fd);return -1;}return close(fd);
}

static int posix_write(void *ctx, const char *name, const uint8_t *data,
                       size_t length, int append) {
    char path[512], tmp[516]; int fd, flags; size_t done = 0; ssize_t n;
    if ((length != 0 && data == NULL) || platform_path(path, ctx, name) != 0)
        return -1;
    if (append) { strcpy(tmp, path); flags = O_WRONLY|O_CREAT|O_APPEND; }
    else { if (snprintf(tmp, sizeof(tmp), "%s.tmp", path) <= 0) return -1;
           flags = O_WRONLY|O_CREAT|O_TRUNC; }
    fd = open(tmp, flags|O_NOFOLLOW, 0600); if (fd < 0) return -1;
    while (done != length) {
        n = write(fd, data + done, length - done);
        if (n < 0 && errno == EINTR) continue;
        if (n <= 0) { close(fd); if (!append) unlink(tmp); return -1; }
        done += (size_t)n;
    }
    {int ok=fsync(fd)==0;if(close(fd))ok=0;if(!ok){
        if (!append) unlink(tmp);
        return -1;
    }}
    if (!append && rename(tmp, path) != 0) { unlink(tmp); return -1; }
    return append ? 0 : sync_parent(path);
}

int br_platform_posix_init(br_platform *p, const char *prefix,
                           uint32_t capability_allowlist) {
    if (p == NULL || prefix == NULL || strlen(prefix) >= sizeof(p->prefix))
        return -1;
    memset(p, 0, sizeof(*p)); strcpy(p->prefix, prefix); p->ctx = p->prefix;
    p->read = posix_read; p->write = posix_write;
    p->capability_allowlist = capability_allowlist & BR_CAP_ALL;
    return 0;
}

int br_vm_bind_platform(br_vm *vm, br_platform *p) {
    uint8_t key[32];size_t n=0;
    if (vm == NULL || !vm->initialized || p == NULL || p->read == NULL ||
        p->write == NULL) return -1;
    vm->platform = p; vm->capability_allowlist = p->capability_allowlist;
    vm->caps[0] &= vm->capability_allowlist;
    if(p->read(p->ctx,"issuer",key,sizeof(key),&n)==0&&n==sizeof(key))
        (void)br_vm_set_issuer_key(vm,key,n);
    return 0;
}

int br_vm_save(br_vm *vm, const char *prefix) {
    uint8_t record[512];
    char slot[2];
    uint32_t generation;
    if (vm == NULL || prefix == NULL || strlen(prefix) > 480 ||
        vm->persistent_generation==UINT32_MAX) return -1;
    generation = vm->persistent_generation + 1u;
    persist_encode(vm, record, generation);
    slot[0]=(char)('0'+(generation&1u));slot[1]=0;
    if(posix_write((void *)prefix,slot,record,sizeof(record),0)!=0)return -1;
    vm->persistent_generation = generation;
    diag_add(vm, 5u, BR_SVC);
    return 0;
}

int br_vm_recover(br_vm *vm, const char *prefix) {
    uint8_t a[512], b[512], *chosen = NULL;
    char p0[512], p1[512];
    uint32_t ga = 0, gb = 0, generation = 0;
    int va, vb;
    if (vm == NULL || vm->regs == NULL || vm->tmp == NULL || vm->tmp2 == NULL ||
        prefix == NULL || strlen(prefix) > 480) return -1;
    (void)snprintf(p0, sizeof(p0), "%s.0", prefix);
    (void)snprintf(p1, sizeof(p1), "%s.1", prefix);
    va = read_record(p0, a, &ga) == 0;
    vb = read_record(p1, b, &gb) == 0;
    if (!va && !vb) return trap(vm, BR_TRAP_STATE, BR_SVC);
    chosen = va && (!vb || ga >= gb) ? a : b;
    if (persist_decode(vm, chosen, &generation) != 0)
        return trap(vm, BR_TRAP_STATE, BR_SVC);
    memset(vm->regs, 0, (size_t)BR_REGS * BR_LIMBS * sizeof(uint64_t));
    memset(vm->tmp, 0, BR_LIMBS * sizeof(uint64_t));
    memset(vm->tmp2, 0, BR_LIMBS * sizeof(uint64_t));
    memset(vm->memory, 0, sizeof(vm->memory));
    memset(vm->stack, 0, (size_t)BR_STACK_WORDS * BR_LIMBS * sizeof(uint64_t));
    memset(vm->fragments, 0, sizeof(vm->fragments));
    vm->ip = vm->sp = vm->flags = vm->services_used = 0;
    vm->fragment_txid = vm->fragment_next_seq = vm->fragment_length = 0;
    vm->cancel_requested = 0;
    vm->arithmetic_mode = 0;
    vm->instruction_budget = 4096u;
    vm->service_budget = 64u;
    vm->diag_head = vm->diag_seq % BR_DIAG_RECORDS;
    vm->persistent_generation = generation;
    vm->trap = BR_TRAP_NONE;
    vm->status = BR_READY;
    if (vm->active_image_length != 0) {
        uint8_t *image = malloc(BR_MAX_IMAGE); size_t length = 0;
        char name[8]; uint8_t hash[32];
        snprintf(name, sizeof(name), "image%u", vm->active_slot);
        if (image == NULL || vm->platform == NULL ||
            vm->platform->read(vm->platform->ctx, name, image, BR_MAX_IMAGE,
                               &length) != 0) {
            free(image); return trap(vm, BR_TRAP_STATE, BR_SVC);
        }
        if(length != vm->active_image_length || SHA256(image,length,hash)==NULL ||
           memcmp(hash,vm->active_image_hash,32)!=0){free(image);
           return trap(vm,BR_TRAP_INTEGRITY,BR_SVC);}
        if(br_image_load(vm,image,length,vm->issuer_public_key,
                         vm->issuer_key_set?32u:0u,vm->issuer_key_set)!=0){
            free(image);return -1;}
        free(image);
    }
    diag_add(vm, 6u, BR_SVC);
    return 0;
}

static void encode_insn(uint8_t out[BR_INSN_BYTES], const br_insn *in) {
    out[0] = in->op; out[1] = in->mode; out[2] = in->rd;
    out[3] = in->ra; out[4] = in->rb; out[5] = in->cap;
    put16(out + 6, in->flags); put64(out + 8, in->imm);
}

static void decode_insn(br_insn *in, const uint8_t data[BR_INSN_BYTES]) {
    in->op = data[0]; in->mode = data[1]; in->rd = data[2];
    in->ra = data[3]; in->rb = data[4]; in->cap = data[5];
    in->flags = get16(data + 6); in->imm = get64(data + 8);
}

static int verify_ed25519(const uint8_t *public_key, size_t key_length,
                          const uint8_t *message, size_t message_length,
                          const uint8_t signature[64]) {
    EVP_PKEY *key;
    EVP_MD_CTX *ctx;
    int ok = 0;
    if (public_key == NULL || key_length != 32) return 0;
    key = EVP_PKEY_new_raw_public_key(EVP_PKEY_ED25519, NULL, public_key, key_length);
    ctx = EVP_MD_CTX_new();
    if (key != NULL && ctx != NULL && EVP_DigestVerifyInit(ctx, NULL, NULL, NULL, key) == 1)
        ok = EVP_DigestVerify(ctx, signature, 64, message, message_length) == 1;
    EVP_MD_CTX_free(ctx); EVP_PKEY_free(key);
    return ok;
}

int br_ed25519_public(const uint8_t *private_key, size_t key_length,
                      uint8_t public_key[32]) {
    EVP_PKEY *key;
    size_t public_length = 32;
    int ok;
    if (private_key == NULL || key_length != 32 || public_key == NULL) return -1;
    key = EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519, NULL,
                                       private_key, key_length);
    if (key == NULL) return -1;
    ok = EVP_PKEY_get_raw_public_key(key, public_key, &public_length) == 1 &&
         public_length == 32;
    EVP_PKEY_free(key);
    return ok ? 0 : -1;
}

int br_image_sign(uint8_t *image, size_t *length, size_t capacity,
                  const uint8_t *private_key, size_t key_length) {
    EVP_PKEY *key = NULL;
    EVP_MD_CTX *ctx = NULL;
    uint8_t digest[SHA256_DIGEST_LENGTH];
    size_t payload, signature_length = 64;
    int ok = 0;
    if (image == NULL || length == NULL || private_key == NULL ||
        key_length != 32 || *length < BR_IMAGE_HEADER || capacity < *length + 64u ||
        memcmp(image, "BRIM", 4) != 0 || (image[7] & BR_IMAGE_FLAG_SIGNED) != 0)
        return -1;
    payload = (size_t)get16(image + 28) * BR_INSN_BYTES + get16(image + 30) +
              get32(image + 24);
    if (*length != BR_IMAGE_HEADER + payload ||
        SHA256(image + BR_IMAGE_HEADER, payload, digest) == NULL ||
        memcmp(digest, image + 32, sizeof(digest)) != 0) return -1;
    image[7] |= BR_IMAGE_FLAG_SIGNED;
    key = EVP_PKEY_new_raw_private_key(EVP_PKEY_ED25519, NULL,
                                       private_key, key_length);
    ctx = EVP_MD_CTX_new();
    if (key != NULL && ctx != NULL &&
        EVP_DigestSignInit(ctx, NULL, NULL, NULL, key) == 1 &&
        EVP_DigestSign(ctx, image + *length, &signature_length,
                       image, *length) == 1 && signature_length == 64) ok = 1;
    EVP_MD_CTX_free(ctx);
    EVP_PKEY_free(key);
    if (!ok) { image[7] &= (uint8_t)~BR_IMAGE_FLAG_SIGNED; return -1; }
    *length += 64u;
    return 0;
}

int br_vm_set_issuer_key(br_vm *vm, const uint8_t *public_key,
                         size_t key_length) {
    if (vm == NULL || !vm->initialized || public_key == NULL || key_length != 32)
        return -1;
    memcpy(vm->issuer_public_key, public_key, 32);
    vm->issuer_key_set = 1u;
    return 0;
}

int br_image_write(const char *path, const br_insn *code, size_t count,
                   const uint8_t *data, size_t data_length, uint32_t version,
                   uint32_t requested) {
    uint8_t *image; size_t i, payload, total; FILE *fp; int ok = 0;
    if (path == NULL || code == NULL || count == 0 || count > BR_MAX_CODE ||
        data_length > BR_MEMORY_BYTES || (data_length && data == NULL) ||
        version == 0 || requested == 0 || (requested & ~BR_CAP_ALL)) return -1;
    for (i = 0; i < count; ++i) if (validate_insn(code + i) != 0) return -1;
    payload = count * BR_INSN_BYTES + data_length; total = BR_IMAGE_HEADER + payload;
    if (total > BR_MAX_IMAGE || (image = calloc(total, 1)) == NULL) return -1;
    memcpy(image, "BRIM", 4); image[4]=BR_VERSION_MAJOR; image[5]=BR_VERSION_MINOR;
    image[6]=BR_VERSION_PATCH; image[7]=BR_IMAGE_FLAG_FACTORY;
    image[8]=1; image[9]=20; image[10]=BR_REGS; image[11]=BR_CAPS;
    image[12]=BR_OPCODES; image[13]=4; put16(image+14,BR_IMAGE_HEADER);
    put32(image+16,version); put32(image+20,requested);
    put16(image+28,(uint16_t)count); put16(image+30,(uint16_t)data_length);
    for(i=0;i<count;++i)encode_insn(image+BR_IMAGE_HEADER+i*BR_INSN_BYTES,code+i);
    if(data_length)memcpy(image+BR_IMAGE_HEADER+count*BR_INSN_BYTES,data,data_length);
    if(SHA256(image+BR_IMAGE_HEADER,payload,image+32)!=NULL) {
        memcpy(image+64,"Q17-MODEL-OPS-3",15); fp=fopen(path,"wb");
        if(fp!=NULL) { ok=fwrite(image,1,total,fp)==total; if(fclose(fp))ok=0; }
    }
    free(image); return ok ? 0 : -1;
}

int br_image_inspect(const uint8_t *image, size_t length, uint32_t *version,
                     uint16_t *code_count, uint16_t *data_length,
                     uint32_t *requested, int *signed_image) {
    size_t expected; int sig;
    if(image==NULL||length<BR_IMAGE_HEADER||memcmp(image,"BRIM",4)||
       get16(image+14)!=BR_IMAGE_HEADER||image[4]!=BR_VERSION_MAJOR||
       image[8]!=1||image[9]!=20||image[10]!=BR_REGS||image[11]!=BR_CAPS||
       image[12]!=BR_OPCODES||image[13]!=4||get32(image+16)==0)return -1;
    sig=(image[7]&BR_IMAGE_FLAG_SIGNED)!=0;
    expected=BR_IMAGE_HEADER+(size_t)get16(image+28)*BR_INSN_BYTES+
             get16(image+30)+get32(image+24)+(sig?64u:0u);
    if(length!=expected||length>BR_MAX_IMAGE)return -1;
    if(version)*version=get32(image+16);
    if(code_count)*code_count=get16(image+28);
    if(data_length)*data_length=get16(image+30);
    if(requested)*requested=get32(image+20);
    if(signed_image)*signed_image=sig;
    return 0;
}

int br_image_load(br_vm *vm, const uint8_t *image, size_t length,
                  const uint8_t *ed25519_public_key, size_t key_length,
                  int signature_required) {
    uint8_t digest[SHA256_DIGEST_LENGTH];
    size_t payload, expected, i;
    uint16_t code_count, data_length;
    uint32_t version, requested;
    int signed_image;
    if (vm == NULL) return -1;
    if (image == NULL || length < BR_IMAGE_HEADER ||
        memcmp(image, "BRIM", 4) != 0 || get16(image + 14) != BR_IMAGE_HEADER ||
        image[4] != BR_VERSION_MAJOR || image[8] != 1 || image[9] != 20 ||
        image[10] != BR_REGS || image[11] != BR_CAPS ||
        image[12] != BR_OPCODES || image[13] != 4 ||
        get32(image + 16) == 0 ||
        (image[7] & ~(BR_IMAGE_FLAG_FACTORY | BR_IMAGE_FLAG_SIGNED)) != 0)
        return trap(vm, BR_TRAP_BAD_IMAGE, BR_NOP);
    code_count = get16(image + 28); data_length = get16(image + 30);
    requested = get32(image + 20);
    if (code_count == 0 || code_count > BR_MAX_CODE || data_length > BR_MEMORY_BYTES)
        return trap(vm, BR_TRAP_BAD_IMAGE, BR_NOP);
    if (requested == 0 || (requested & ~BR_CAP_ALL) != 0)
        return trap(vm, BR_TRAP_BAD_IMAGE, BR_NOP);
    payload = (size_t)code_count * BR_INSN_BYTES + data_length + get32(image+24);
    if(payload>BR_MAX_IMAGE-BR_IMAGE_HEADER-64u)
        return trap(vm,BR_TRAP_BAD_IMAGE,BR_NOP);
    signed_image = (image[7] & BR_IMAGE_FLAG_SIGNED) != 0;
    expected = BR_IMAGE_HEADER + payload + (signed_image ? 64u : 0u);
    if (length != expected) return trap(vm, BR_TRAP_BAD_IMAGE, BR_NOP);
    if (SHA256(image + BR_IMAGE_HEADER, payload, digest) == NULL ||
        memcmp(digest, image + 32, SHA256_DIGEST_LENGTH) != 0)
        return trap(vm, BR_TRAP_INTEGRITY, BR_NOP);
    if (signature_required && !signed_image) return trap(vm, BR_TRAP_TRUST, BR_NOP);
    if (signed_image && !verify_ed25519(ed25519_public_key, key_length,
                                        image, BR_IMAGE_HEADER + payload,
                                        image + BR_IMAGE_HEADER + payload))
        return trap(vm, BR_TRAP_TRUST, BR_NOP);
    version = get32(image + 16);
    if (version < vm->image_version) return trap(vm, BR_TRAP_TRUST, BR_NOP);
    for (i = 0; i < code_count; ++i) {
        decode_insn(&vm->code[i], image + BR_IMAGE_HEADER + i * BR_INSN_BYTES);
        if (validate_insn(&vm->code[i]) != 0) return trap(vm, BR_TRAP_BAD_IMAGE, BR_NOP);
    }
    vm->code_count = code_count;
    memset(vm->memory, 0, sizeof(vm->memory));
    if (data_length != 0)
        memcpy(vm->memory, image + BR_IMAGE_HEADER +
               (size_t)code_count * BR_INSN_BYTES, data_length);
    vm->image_version = version;
    memset(vm->caps, 0, sizeof(vm->caps));
    vm->caps[0] = requested & vm->capability_allowlist;
    vm->ip = 0; vm->trap = BR_TRAP_NONE; vm->status = BR_READY;
    return 0;
}

static size_t apdu_reply(uint16_t sw, uint32_t txid, const uint8_t *payload,
                         size_t payload_length, uint8_t *response,
                         size_t response_capacity) {
    if (response == NULL || payload_length > 65535u ||
        response_capacity < 8u + payload_length) return 0;
    response[0] = (uint8_t)(sw >> 8); response[1] = (uint8_t)sw;
    put32(response + 2, txid); put16(response + 6, (uint16_t)payload_length);
    if (payload_length != 0) memcpy(response + 8, payload, payload_length);
    return 8u + payload_length;
}

static int state_changing(uint8_t cmd) {
    return cmd == BR_APDU_INIT || cmd == BR_APDU_LOAD || cmd == BR_APDU_EXEC ||
           cmd == BR_APDU_INPUT || cmd == BR_APDU_UPDATE ||
           cmd == BR_APDU_RECOVER || cmd == BR_APDU_RESET;
}

static uint16_t update_fragment(br_vm *vm, const uint8_t *p, uint16_t length,
                                uint8_t flags, uint16_t seq, uint32_t txid) {
    uint8_t *image = NULL, hash[32]; size_t n = 0; uint32_t total, slot;
    br_vm check; char name[8]; uint16_t sw = BR_SW_BAD_DATA;
    uint32_t old_version, old_txid, old_slot, old_length; uint8_t old_hash[32];
    if (!vm->issuer_key_set || vm->platform == NULL) return BR_SW_SECURITY;
    if (seq == 0) {
        if (length < 4u) return BR_SW_BAD_DATA;
        total = get32(p); p += 4; length = (uint16_t)(length - 4u);
        if (total == 0 || total > BR_MAX_IMAGE || length > total) return BR_SW_BAD_DATA;
        vm->update_txid=txid; vm->update_total=total; vm->update_received=length;
        vm->update_next_seq=1;
        if (vm->platform->write(vm->platform->ctx,"stage",p,length,0)!=0)
            return BR_SW_INTERNAL;
    } else {
        if (txid!=vm->update_txid || seq!=vm->update_next_seq ||
            vm->update_received+length>vm->update_total) goto clear;
        if(vm->platform->write(vm->platform->ctx,"stage",p,length,1)!=0) {
            sw=BR_SW_INTERNAL; goto clear;
        }
        vm->update_received+=length; ++vm->update_next_seq;
    }
    if(flags&BR_APDU_FLAG_MORE) {
        if(vm->update_received>=vm->update_total)goto clear;
        return BR_SW_MORE;
    }
    if(vm->update_received!=vm->update_total)goto clear;
    image=malloc(vm->update_total); if(image==NULL){sw=BR_SW_INTERNAL;goto clear;}
    if(vm->platform->read(vm->platform->ctx,"stage",image,vm->update_total,&n)!=0||
       n!=vm->update_total||br_vm_init(&check)!=0)goto clear;
    check.image_version=vm->image_version; check.capability_allowlist=vm->capability_allowlist;
    br_vm_set_issuer_key(&check,vm->issuer_public_key,32);
    if(br_image_load(&check,image,n,vm->issuer_public_key,32,1)!=0) {
        sw=check.trap==BR_TRAP_TRUST?BR_SW_SECURITY:BR_SW_BAD_DATA;
        br_vm_free(&check); goto clear;
    }
    slot=vm->active_image_length?(vm->active_slot^1u):0u;
    snprintf(name,sizeof(name),"image%u",slot);
    if(vm->platform->write(vm->platform->ctx,name,image,n,0)!=0||
       SHA256(image,n,hash)==NULL){br_vm_free(&check);sw=BR_SW_INTERNAL;goto clear;}
    old_version=vm->image_version; old_txid=vm->last_txid; old_slot=vm->active_slot;
    old_length=vm->active_image_length; memcpy(old_hash,vm->active_image_hash,32);
    vm->image_version=check.image_version; vm->last_txid=txid; vm->active_slot=slot;
    vm->active_image_length=(uint32_t)n; memcpy(vm->active_image_hash,hash,32);
    br_vm_free(&check);
    if(br_vm_save(vm,vm->platform->prefix)!=0) {
        vm->image_version=old_version;vm->last_txid=old_txid;vm->active_slot=old_slot;
        vm->active_image_length=old_length;memcpy(vm->active_image_hash,old_hash,32);
        sw=BR_SW_INTERNAL;goto clear;
    }
    if(br_image_load(vm,image,n,vm->issuer_public_key,32,1)!=0) {
        sw=BR_SW_INTERNAL;goto clear;
    }
    sw=BR_SW_OK;
clear:
    free(image); vm->update_txid=vm->update_total=vm->update_received=0;
    vm->update_next_seq=0; return sw;
}

size_t br_apdu(br_vm *vm, const uint8_t *request, size_t request_length,
               uint8_t *response, size_t response_capacity) {
    const uint8_t *payload;
    uint8_t cmd, flags, small[512];
    uint32_t txid, budget;
    uint16_t seq, length, sw = BR_SW_OK;
    size_t out_length = 0, i, offset, wanted, limit; const uint8_t *slice;
    if (vm == NULL || request == NULL || request_length < 12u || request[0] != 1u)
        return apdu_reply(BR_SW_BAD_DATA, 0, NULL, 0, response, response_capacity);
    cmd = request[1]; flags = request[2]; txid = get32(request + 4);
    if (request[3] != 0 || (flags & ~BR_APDU_FLAG_MORE) != 0)
        return apdu_reply(BR_SW_BAD_DATA, txid, NULL, 0, response, response_capacity);
    seq = get16(request + 8); length = get16(request + 10);
    if ((size_t)length + 12u != request_length)
        return apdu_reply(BR_SW_BAD_LENGTH, txid, NULL, 0, response, response_capacity);
    payload = request + 12;
    if ((flags & BR_APDU_FLAG_MORE) != 0 && length == 0)
        return apdu_reply(BR_SW_BAD_DATA, txid, NULL, 0, response, response_capacity);
    if (cmd == BR_APDU_UPDATE) {
        if (txid <= vm->last_txid)
            return apdu_reply(BR_SW_REPLAY,txid,NULL,0,response,response_capacity);
        sw=update_fragment(vm,payload,length,flags,seq,txid);
        return apdu_reply(sw,txid,NULL,0,response,response_capacity);
    }
    if ((flags & BR_APDU_FLAG_MORE) != 0 || vm->fragment_length != 0) {
        if (vm->fragment_length == 0) {
            if (seq != 0) sw = BR_SW_BAD_DATA;
            else { vm->fragment_txid = txid; vm->fragment_next_seq = 0; }
        }
        if (sw == BR_SW_OK && (txid != vm->fragment_txid || seq != vm->fragment_next_seq ||
            (size_t)vm->fragment_length + length > BR_FRAGMENT_BYTES)) sw = BR_SW_BAD_DATA;
        if (sw != BR_SW_OK) {
            vm->fragment_length = vm->fragment_next_seq = 0;
            return apdu_reply(sw, txid, NULL, 0, response, response_capacity);
        }
        memcpy(vm->fragments + vm->fragment_length, payload, length);
        vm->fragment_length = (uint16_t)(vm->fragment_length + length);
        ++vm->fragment_next_seq;
        if ((flags & BR_APDU_FLAG_MORE) != 0)
            return apdu_reply(BR_SW_MORE, txid, NULL, 0, response, response_capacity);
        payload = vm->fragments; length = vm->fragment_length;
        vm->fragment_length = vm->fragment_next_seq = 0;
    }
    if (state_changing(cmd) && txid <= vm->last_txid)
        return apdu_reply(BR_SW_REPLAY, txid, NULL, 0, response, response_capacity);
    switch (cmd) {
        case BR_APDU_INIT:
            br_vm_reset(vm, 1); break;
        case BR_APDU_STATUS:
            put32(small, vm->status); put32(small + 4, vm->trap);
            put32(small + 8, vm->image_version); put32(small + 12, vm->persistent_generation);
            out_length = 16; break;
        case BR_APDU_LOAD:
            if (vm->issuer_key_set) { sw = BR_SW_SECURITY; break; }
            if (length == 0 || length % BR_INSN_BYTES != 0 ||
                length / BR_INSN_BYTES > BR_MAX_CODE) { sw = BR_SW_BAD_DATA; break; }
            for (i = 0; i < length / BR_INSN_BYTES; ++i) {
                decode_insn(&vm->code[i], payload + i * BR_INSN_BYTES);
                if (validate_insn(&vm->code[i]) != 0) { sw = BR_SW_BAD_DATA; break; }
            }
            if (sw == BR_SW_OK) {
                vm->code_count = length / BR_INSN_BYTES; vm->ip = 0;
                vm->status = BR_READY; vm->trap = BR_TRAP_NONE;
            }
            break;
        case BR_APDU_EXEC:
            budget = length == 4 ? get32(payload) : 4096u;
            if (budget == 0 || br_vm_run(vm, budget) != 0) sw = BR_SW_INTERNAL;
            break;
        case BR_APDU_STATE:
            if(length==0){put32(small, vm->ip); put32(small + 4, vm->status);
                put32(small + 8, vm->trap); put32(small + 12, vm->flags);
                put64(small + 16, regp(vm, 0)[0]); put64(small + 24, regp(vm, 1)[0]);
                out_length = 32; break;}
            if(length!=8){sw=BR_SW_BAD_DATA;break;}
            offset=get32(payload+2);wanted=get16(payload+6);slice=NULL;limit=0;
            if(payload[0]==0&&payload[1]<BR_REGS){slice=(uint8_t*)regp(vm,payload[1]);limit=(size_t)BR_LIMBS*8u;}
            else if(payload[0]==1&&payload[1]==0){slice=vm->memory;limit=BR_MEMORY_BYTES;}
            else if(payload[0]==2&&payload[1]<vm->sp){slice=(uint8_t*)(vm->stack+(size_t)payload[1]*BR_LIMBS);limit=(size_t)BR_LIMBS*8u;}
            if(!slice||wanted>sizeof(small)||offset>limit||wanted>limit-offset){sw=BR_SW_BAD_DATA;break;}
            memcpy(small,slice+offset,wanted);out_length=wanted;break;
        case BR_APDU_INPUT:
            if (length < 2 || get16(payload) > BR_MEMORY_BYTES - (length - 2u))
                sw = BR_SW_BAD_DATA;
            else memcpy(vm->memory + get16(payload), payload + 2, length - 2u);
            break;
        case BR_APDU_RECOVER:
            if(vm->platform==NULL||br_vm_recover(vm,vm->platform->prefix)!=0)
                sw=BR_SW_INTERNAL;
            else { vm->last_txid=txid;
                   if(br_vm_save(vm,vm->platform->prefix)!=0)sw=BR_SW_INTERNAL;
                   else {put32(small,vm->persistent_generation);
                         put32(small+4,vm->image_version);put32(small+8,vm->active_slot);
                         put32(small+12,vm->status);out_length=16;} }
            break;
        case BR_APDU_DIAG:
            out_length = BR_DIAG_RECORDS * 16u;
            for (i = 0; i < BR_DIAG_RECORDS; ++i) {
                const br_diag *d = &vm->diag[i]; uint8_t *p = small + i * 16u;
                put32(p, d->seq); put16(p + 4, d->event); put16(p + 6, d->trap);
                put32(p + 8, d->ip); p[12] = d->opcode; p[13] = d->status;
                p[14] = p[15] = 0;
            }
            break;
        case BR_APDU_RESET:
            br_vm_reset(vm, length != 0 && payload[0] != 0); break;
        case BR_APDU_CAPS:
            for (i = 0; i < BR_CAPS; ++i) put32(small + i * 4u, vm->caps[i]);
            out_length = BR_CAPS * 4u; break;
        case BR_APDU_HELLO:
            small[0] = BR_VERSION_MAJOR; small[1] = BR_VERSION_MINOR;
            small[2] = BR_VERSION_PATCH; small[3] = 1;
            put32(small + 4, BR_WORD_BITS); put32(small + 8, BR_OPCODES);
            put32(small + 12, BR_CAPS); out_length = 16; break;
        default: sw = BR_SW_BAD_INS; break;
    }
    if (state_changing(cmd) && sw == BR_SW_OK) vm->last_txid = txid;
    return apdu_reply(sw, txid, small, out_length, response, response_capacity);
}

#ifndef BR_NO_SELFTEST
static int positive_opcode(br_vm *vm, uint8_t op) {
    br_insn code[4];
    uint64_t *r0, *r1, *r2;
    int ok;
    memset(code, 0, sizeof(code));
    br_vm_reset(vm, 1);
    r0 = regp(vm, 0); r1 = regp(vm, 1); r2 = regp(vm, 2);
    r0[0] = 40; r1[0] = 2;
    code[0].op = op; code[0].mode = BR_WRAP; code[0].rd = 2;
    code[0].ra = 0; code[0].rb = 1; code[0].cap = 0;
    code[1].op = BR_HALT; code[1].cap = 0;
    switch (op) {
        case BR_MOVI: code[0].imm = 42; break;
        case BR_JMP: code[0].imm = 1; break;
        case BR_JZ: vm->flags = BR_FLAG_ZERO; code[0].imm = 1; break;
        case BR_JNZ: vm->flags = 0; code[0].imm = 1; break;
        case BR_LOAD: put64(vm->memory, 42); code[0].imm = 0; break;
        case BR_STORE: code[0].imm = 8; break;
        case BR_PUSH: code[0].ra = 0; break;
        case BR_POP: vm->stack[0] = 42; vm->sp = 1; break;
        case BR_SVC: code[0].imm = 0; break;
        case BR_SHL: case BR_SHR: code[0].imm = 1; break;
        default: break;
    }
    if (op == BR_HALT) {
        if (br_vm_load_code(vm, code, 1) != 0) return 0;
    } else if (br_vm_load_code(vm, code, 2) != 0) return 0;
    ok = br_vm_run(vm, 128) == 0;
    if (!ok) return 0;
    switch (op) {
        case BR_MOVI: return r2[0] == 42;
        case BR_MOV: return r2[0] == 40;
        case BR_ADD: return r2[0] == 42;
        case BR_SUB: return r2[0] == 38;
        case BR_MUL: return r2[0] == 80;
        case BR_DIVU: return r2[0] == 20;
        case BR_MODU: return r2[0] == 0;
        case BR_AND: return r2[0] == 0;
        case BR_OR: return r2[0] == 42;
        case BR_XOR: return r2[0] == 42;
        case BR_NOT: return r2[0] == ~(uint64_t)40;
        case BR_SHL: return r2[0] == 80;
        case BR_SHR: return r2[0] == 20;
        case BR_CMP: return (vm->flags & BR_FLAG_GREATER) != 0;
        case BR_LOAD: return r2[0] == 42;
        case BR_STORE: return get64(vm->memory + 8) == 40;
        case BR_PUSH: return vm->sp == 1 && vm->stack[0] == 40;
        case BR_POP: return vm->sp == 0 && r2[0] == 42;
        default: return 1;
    }
}

static int arithmetic_mode_tests(br_vm *vm) {
    br_insn code[2];
    uint64_t *r0, *r1, *r2;
    unsigned mode;
    memset(code, 0, sizeof(code));
    code[0].op = BR_ADD; code[0].rd = 2; code[0].ra = 0;
    code[0].rb = 1; code[0].cap = 0; code[1].op = BR_HALT;
    for (mode = 0; mode < 4; ++mode) {
        br_vm_reset(vm, 1); r0 = regp(vm, 0); r1 = regp(vm, 1); r2 = regp(vm, 2);
        memset(r0, 0xff, BR_LIMBS * sizeof(uint64_t)); r1[0] = 1;
        code[0].mode = (uint8_t)mode;
        if (br_vm_load_code(vm, code, 2) != 0) return 0;
        if (mode == BR_WRAP && (br_vm_run(vm, 8) != 0 || active_limbs(r2) != 0)) return 0;
        if (mode == BR_SATURATE && (br_vm_run(vm, 8) != 0 || r2[BR_LIMBS - 1] != UINT64_MAX)) return 0;
        if ((mode == BR_CHECKED || mode == BR_TRAPPING) &&
            (br_vm_run(vm, 8) == 0 || vm->trap != BR_TRAP_OVERFLOW)) return 0;
    }
    return 1;
}

static int wide_boundary_tests(br_vm *vm) {
    uint64_t *a, *b, *product, *sum;
    size_t i;
    int rc;
    br_vm_reset(vm, 1);
    a = regp(vm, 0); b = regp(vm, 1); product = regp(vm, 2); sum = regp(vm, 3);
    a[BR_LIMBS - 1] = UINT64_C(1) << 63; b[0] = 3;
    rc = word_divmod(vm->tmp, vm->tmp2, a, b, BR_WORD_BITS);
    if (rc != 0 || vm->tmp2[0] != 2 || active_limbs(vm->tmp2) != 1) return 0;
    if (word_mul(product, vm->tmp, b) != 0) return 0;
    if (word_add(sum, product, vm->tmp2) != 0 || word_cmp(sum, a) != 0) return 0;
    memset(b, 0, BR_LIMBS * sizeof(uint64_t)); b[0] = 1;
    if (word_shift(vm->tmp, b, BR_WORD_BITS - 1u, 1) != 0 ||
        vm->tmp[BR_LIMBS - 1] != (UINT64_C(1) << 63)) return 0;
    if (word_shift(vm->tmp2, vm->tmp, BR_WORD_BITS - 1u, 0) != 0 ||
        vm->tmp2[0] != 1 || active_limbs(vm->tmp2) != 1) return 0;

    /* Dense alternating operands exercise every limb without allocation. */
    for (i = 0; i < BR_LIMBS; ++i)
        a[i] = (i & 1u) ? UINT64_C(0xaaaaaaaaaaaaaaaa)
                        : UINT64_C(0x5555555555555555);
    memset(b, 0, BR_LIMBS * sizeof(uint64_t));
    b[0] = 1;
    if (word_mul(product, a, b) != 0 || word_cmp(product, a) != 0) return 0;
    if (word_add(sum, a, b) != 0) return 0;
    if (word_sub(vm->tmp, sum, b) != 0 || word_cmp(vm->tmp, a) != 0) return 0;

    /* All-ones is the frozen maximum value and must wrap exactly at width. */
    memset(a, 0xff, BR_LIMBS * sizeof(uint64_t));
    if (word_add(sum, a, b) == 0 || active_limbs(sum) != 0) return 0;
    return 1;
}

static int stack_width_tests(br_vm *vm) {
    br_insn code[4]={{BR_PUSH,BR_WRAP,0,0,0,0,0,0},
                     {BR_MOVI,BR_WRAP,0,0,0,0,0,0},
                     {BR_POP,BR_WRAP,1,0,0,0,0,0},
                     {BR_HALT,BR_WRAP,0,0,0,0,0,0}};
    uint64_t *a,*b; br_vm_reset(vm,1); a=regp(vm,0); b=regp(vm,1);
    a[0]=1;a[BR_LIMBS/2]=2;a[BR_LIMBS-1]=3;memcpy(vm->tmp,a,BR_LIMBS*8u);
    if(br_vm_load_code(vm,code,4)||br_vm_run(vm,8)||active_limbs(a)||
       memcmp(vm->tmp,b,BR_LIMBS*8u)||vm->sp)return 0;
    br_vm_reset(vm,1);code[0].op=BR_POP;
    if(br_vm_load_code(vm,code,1)||br_vm_run(vm,2)==0||vm->trap!=BR_TRAP_STACK)return 0;
    br_vm_reset(vm,1);vm->sp=BR_STACK_WORDS;code[0].op=BR_PUSH;
    return br_vm_load_code(vm,code,1)==0&&br_vm_run(vm,2)!=0&&vm->trap==BR_TRAP_STACK;
}

static int service_tests(br_vm *vm) {
    br_insn code[2]={{BR_SVC,BR_WRAP,2,0,1,0,0,7},
                     {BR_HALT,BR_WRAP,0,0,0,0,0,0}};
    static const uint32_t need[9]={16,16,16,16,32,32,128,16,64};
    uint8_t digest[32],key[32]={0};unsigned s; br_vm_reset(vm,1); memcpy(vm->memory,"abc",3);
    regp(vm,1)[0]=3;regp(vm,2)[0]=64;
    if(SHA256(vm->memory,3,digest)==NULL||br_vm_load_code(vm,code,2)!=0||
       br_vm_run(vm,8)!=0||memcmp(vm->memory+64,digest,32))return 0;
    br_vm_set_issuer_key(vm,key,32);
    for(s=0;s<9;++s){br_vm_reset(vm,1);vm->caps[0]=BR_CAP_CONTROL|BR_CAP_SERVICE|need[s];
        regp(vm,0)[0]=1;regp(vm,2)[0]=64;code[0].imm=s;
        if(br_vm_load_code(vm,code,2)!=0||br_vm_run(vm,8)!=0)return 0;
        br_vm_reset(vm,1);vm->caps[0]=BR_CAP_CONTROL|(need[s]==BR_CAP_SERVICE?0:BR_CAP_SERVICE);
        regp(vm,0)[0]=1;regp(vm,2)[0]=64;
        if(br_vm_load_code(vm,code,2)!=0||br_vm_run(vm,8)==0||vm->trap!=BR_TRAP_CAPABILITY)return 0;
    }
    return 1;
}

static int persistence_tests(br_vm *vm) {
    char prefix[128], p0[132], p1[132];
    FILE *fp;
    uint32_t newest;
    (void)snprintf(prefix, sizeof(prefix), "/tmp/brvm-selftest-%ld", (long)getpid());
    (void)snprintf(p0, sizeof(p0), "%s.0", prefix);
    (void)snprintf(p1, sizeof(p1), "%s.1", prefix);
    (void)unlink(p0); (void)unlink(p1);
    vm->config_word = 0x11223344u;
    vm->caps[1] = BR_CAP_ALL;
    regp(vm, 0)[0] = 99;
    vm->memory[0] = 0xaa;
    vm->stack[0] = 77;
    vm->sp = 1;
    vm->fragments[0] = 0xbb;
    vm->fragment_length = 1;
    if (br_vm_save(vm, prefix) != 0 || br_vm_save(vm, prefix) != 0) return 0;
    newest = vm->persistent_generation & 1u;
    fp = fopen(newest ? p1 : p0, "r+b");
    if (fp == NULL || fputc(0, fp) == EOF || fclose(fp) != 0) return 0;
    vm->config_word = 0;
    if (br_vm_recover(vm, prefix) != 0 || vm->config_word != 0x11223344u ||
        vm->caps[0] != BR_CAP_ALL || vm->caps[1] != 0 || regp(vm, 0)[0] != 0 ||
        vm->memory[0] != 0 || vm->sp != 0 || vm->fragment_length != 0)
        return 0;
    (void)unlink(p0); (void)unlink(p1);
    return 1;
}

static int image_tests(br_vm *vm) {
    char path[128];
    uint8_t image[1024];
    FILE *fp;
    size_t n;
    (void)snprintf(path, sizeof(path), "/tmp/brvm-image-%ld.brimg", (long)getpid());
    if (br_assemble_file("examples/boot.mssl",path,3) != 0) return 0;
    fp = fopen(path, "rb");
    if (fp == NULL) return 0;
    n = fread(image, 1, sizeof(image), fp);
    if (fclose(fp) != 0 || n == 0 || br_image_load(NULL, image, n, NULL, 0, 0) != -1)
        return 0;
    br_vm_reset(vm, 1);
    if (br_image_load(vm, image, n, NULL, 0, 0) != 0 || br_vm_run(vm, 32) != 0 ||
        regp(vm, 2)[0] != 42 || (vm->flags & BR_FLAG_ZERO) == 0) return 0;
    image[BR_IMAGE_HEADER] ^= 1u;
    br_vm_reset(vm, 1);
    if (br_image_load(vm, image, n, NULL, 0, 0) == 0 || vm->trap != BR_TRAP_INTEGRITY)
        return 0;
    image[BR_IMAGE_HEADER] ^= 1u;
    image[7] |= 0x80u;
    br_vm_reset(vm, 1);
    if (br_image_load(vm, image, n, NULL, 0, 0) == 0 ||
        vm->trap != BR_TRAP_BAD_IMAGE) return 0;
    image[7] &= 0x7fu;

    /* BRIM bounded data follows code, is integrity sealed, and initializes memory. */
    put16(image + 30, 3);
    image[n++] = 0x11; image[n++] = 0x22; image[n++] = 0x33;
    if (SHA256(image + BR_IMAGE_HEADER, n - BR_IMAGE_HEADER, image + 32) == NULL)
        return 0;
    br_vm_reset(vm, 1);
    if (br_image_load(vm, image, n, NULL, 0, 0) != 0 ||
        vm->memory[0] != 0x11 || vm->memory[1] != 0x22 || vm->memory[2] != 0x33)
        return 0;
    put32(image+16,2);br_vm_reset(vm,1);
    if(br_image_load(vm,image,n,NULL,0,0)==0||vm->trap!=BR_TRAP_TRUST)return 0;
    put32(image+16,3);vm->capability_allowlist=BR_CAP_CONTROL;
    br_vm_reset(vm,1);
    if(br_image_load(vm,image,n,NULL,0,0)!=0||vm->caps[0]!=BR_CAP_CONTROL)return 0;
    vm->capability_allowlist=BR_CAP_ALL;
    (void)unlink(path);
    return 1;
}

static int apdu_tests(br_vm *vm) {
    uint8_t req[64], rsp[512];
    size_t n; br_insn halt={BR_HALT,BR_WRAP,0,0,0,0,0,0};unsigned j;
    memset(req, 0, sizeof(req));
    req[0] = 1; req[1] = BR_APDU_HELLO; put32(req + 4, 1); put16(req + 10, 0);
    n = br_apdu(vm, req, 12, rsp, sizeof(rsp));
    if (n != 24 || rsp[0] != 0x90 || rsp[1] != 0x00) return 0;
    req[3] = 1;
    if (br_apdu(vm, req, 12, rsp, sizeof(rsp)) != 8 || rsp[0] != 0x6a) return 0;
    req[3] = 0;
    req[1] = BR_APDU_RESET; put32(req + 4, 100);
    vm->caps[1] = BR_CAP_ALL;
    n = br_apdu(vm, req, 12, rsp, sizeof(rsp));
    if (n != 8 || rsp[0] != 0x90 || vm->caps[1] != 0 ||
        vm->caps[0] != BR_CAP_ALL) return 0;
    n = br_apdu(vm, req, 12, rsp, sizeof(rsp));
    if (n != 8 || rsp[0] != 0x69 || rsp[1] != 0x85) return 0;
    req[1] = BR_APDU_INPUT; req[2] = BR_APDU_FLAG_MORE; put32(req + 4, 101);
    put16(req + 8, 0); put16(req + 10, 2); req[12] = 0; req[13] = 0;
    if (br_apdu(vm, req, 14, rsp, sizeof(rsp)) != 8 || rsp[0] != 0x61) return 0;
    req[2] = 0; put16(req + 8, 1); put16(req + 10, 2); req[12] = 0xaa; req[13] = 0xbb;
    if (br_apdu(vm, req, 14, rsp, sizeof(rsp)) != 8 || rsp[0] != 0x90 ||
        vm->memory[0] != 0xaa || vm->memory[1] != 0xbb) return 0;
    memset(req,0,20);req[0]=1;req[1]=BR_APDU_STATE;put16(req+10,8);
    req[12]=1;put16(req+18,2);
    if(br_apdu(vm,req,20,rsp,sizeof(rsp))!=10||rsp[8]!=0xaa||rsp[9]!=0xbb||
       br_apdu(vm,req,20,rsp,9)!=0)return 0;
    memset(req,0,sizeof(req));req[0]=1;req[1]=BR_APDU_INIT;put32(req+4,102);
    vm->issuer_key_set=0;if(br_apdu(vm,req,12,rsp,sizeof(rsp))!=8||rsp[0]!=0x90)return 0;
    req[1]=BR_APDU_LOAD;put32(req+4,103);put16(req+10,BR_INSN_BYTES);encode_insn(req+12,&halt);
    if(br_apdu(vm,req,12+BR_INSN_BYTES,rsp,sizeof(rsp))!=8||rsp[0]!=0x90)return 0;
    req[1]=BR_APDU_EXEC;put32(req+4,104);put16(req+10,0);
    if(br_apdu(vm,req,12,rsp,sizeof(rsp))!=8||rsp[0]!=0x90)return 0;
    for(j=0;j<4;++j){req[1]=(uint8_t[]){BR_APDU_STATUS,BR_APDU_DIAG,BR_APDU_CAPS,BR_APDU_HELLO}[j];
        if(br_apdu(vm,req,12,rsp,sizeof(rsp))<=8||rsp[0]!=0x90)return 0;}
    return 1;
}

static int signed_update_tests(br_vm *vm) {
    static const uint8_t private_key[32] = {
        0x00,0x01,0x02,0x03,0x04,0x05,0x06,0x07,
        0x08,0x09,0x0a,0x0b,0x0c,0x0d,0x0e,0x0f,
        0x10,0x11,0x12,0x13,0x14,0x15,0x16,0x17,
        0x18,0x19,0x1a,0x1b,0x1c,0x1d,0x1e,0x1f
    };
    char path[128], prefix[128], file[160]; br_platform platform;
    uint8_t image[1024], request[12+BR_FRAGMENT_BYTES], response[32], public_key[32],*large;
    FILE *fp;
    size_t image_length, response_length, first,large_length,payload,pad,off,chunk;
    uint32_t txid;uint16_t seq;br_vm recovered,reboot;
    br_insn crypto[2]={{BR_SVC,BR_WRAP,2,0,1,0,0,8},{BR_HALT,BR_WRAP,0,0,0,0,0,0}};
    (void)snprintf(path, sizeof(path), "/tmp/brvm-update-%ld.brimg", (long)getpid());
    if (br_assemble_file("examples/boot.mssl",path,3) != 0 || br_ed25519_public(private_key, 32, public_key) != 0)
        return 0;
    fp = fopen(path, "rb");
    if (fp == NULL) return 0;
    image_length = fread(image, 1, sizeof(image) - 64u, fp);
    if (fclose(fp) != 0 || image_length == 0 ||
        br_image_sign(image, &image_length, sizeof(image), private_key, 32) != 0)
        return 0;
    (void)snprintf(prefix,sizeof(prefix),"/tmp/brvm-state-%ld",(long)getpid());
    for(txid=0;txid<5;++txid){snprintf(file,sizeof(file),"%s.%s",prefix,
        (const char*[]){"0","1","stage","image0","image1"}[txid]);unlink(file);}
    if(br_platform_posix_init(&platform,prefix,BR_CAP_ALL)!=0||
       br_vm_bind_platform(vm,&platform)!=0)return 0;
    memset(request, 0, sizeof(request));
    request[0] = 1; request[1] = BR_APDU_UPDATE;
    txid = vm->last_txid + 1u;
    put32(request + 4, txid); put16(request + 10, (uint16_t)(image_length+4u));
    put32(request+12,(uint32_t)image_length);memcpy(request + 16, image, image_length);
    vm->issuer_key_set = 0;
    response_length = br_apdu(vm, request, 16u + image_length,
                              response, sizeof(response));
    if (response_length != 8 || response[0] != 0x69 || response[1] != 0x82 ||
        vm->last_txid == txid) return 0;
    public_key[0] ^= 1u;
    if (br_vm_set_issuer_key(vm, public_key, sizeof(public_key)) != 0) return 0;
    request[1] = BR_APDU_LOAD; put16(request + 10, BR_INSN_BYTES);
    response_length = br_apdu(vm, request, 12u + BR_INSN_BYTES,
                              response, sizeof(response));
    if (response_length != 8 || response[0] != 0x69 || response[1] != 0x82 ||
        vm->last_txid == txid) return 0;
    request[1] = BR_APDU_UPDATE; put16(request + 10, (uint16_t)(image_length+4u));
    response_length = br_apdu(vm, request, 16u + image_length,
                              response, sizeof(response));
    if (response_length != 8 || response[0] != 0x69 || response[1] != 0x82 ||
        vm->last_txid == txid) return 0;
    public_key[0] ^= 1u;
    if (br_vm_set_issuer_key(vm, public_key, sizeof(public_key)) != 0) return 0;
    br_vm_reset(vm,1);vm->caps[0]=BR_CAP_CONTROL|BR_CAP_SERVICE|BR_CAP_UPDATE;
    memcpy(vm->memory,image,image_length-64u);memcpy(vm->memory+256,image+image_length-64u,64);
    regp(vm,1)[0]=image_length-64u;regp(vm,2)[0]=256;
    if(br_vm_load_code(vm,crypto,2)!=0||br_vm_run(vm,8)!=0||regp(vm,2)[0]!=1)return 0;
    br_vm_reset(vm,1);vm->caps[0]=BR_CAP_CONTROL|BR_CAP_SERVICE|BR_CAP_UPDATE;
    memcpy(vm->memory,image,image_length-64u);memcpy(vm->memory+256,image+image_length-64u,64);
    vm->memory[256]^=1;regp(vm,1)[0]=image_length-64u;regp(vm,2)[0]=256;
    if(br_vm_load_code(vm,crypto,2)!=0||br_vm_run(vm,8)!=0||regp(vm,2)[0]!=0)return 0;
    ++txid;put32(request+4,txid);first=image_length/2u;request[2]=BR_APDU_FLAG_MORE;
    put16(request+8,0);put16(request+10,(uint16_t)(first+4u));
    put32(request+12,(uint32_t)image_length);memcpy(request+16,image,first);
    response_length=br_apdu(vm,request,16u+first,response,sizeof(response));
    if(response_length!=8||response[0]!=0x61)return 0;
    request[2]=0;put16(request+8,1);put16(request+10,(uint16_t)(image_length-first));
    memcpy(request+12,image+first,image_length-first);
    response_length = br_apdu(vm, request, 12u + image_length-first,
                              response, sizeof(response));
    if (response_length != 8 || response[0] != 0x90 || response[1] != 0x00 ||
        vm->last_txid != txid || br_vm_run(vm, 32) != 0 || regp(vm, 2)[0] != 42)
        return 0;
    payload=image_length-BR_IMAGE_HEADER-64u;large=calloc(BR_MAX_IMAGE,1);
    if(!large)return 0;
    memcpy(large,image,BR_IMAGE_HEADER+payload);
    large[7]&=(uint8_t)~BR_IMAGE_FLAG_SIGNED;put32(large+16,4u);
    pad=BR_MAX_IMAGE-BR_IMAGE_HEADER-payload-64u;put32(large+24,(uint32_t)pad);
    memset(large+BR_IMAGE_HEADER+payload,0xa5,pad);large_length=BR_IMAGE_HEADER+payload+pad;
    if(SHA256(large+BR_IMAGE_HEADER,payload+pad,large+32)==NULL||
       br_image_sign(large,&large_length,BR_MAX_IMAGE,private_key,32)!=0||
       large_length!=BR_MAX_IMAGE){free(large);return 0;}
    ++txid;off=0;seq=0;
    while(off<large_length){chunk=large_length-off;
        if(chunk>BR_FRAGMENT_BYTES-(seq?0u:4u))chunk=BR_FRAGMENT_BYTES-(seq?0u:4u);
        memset(request,0,12);request[0]=1;request[1]=BR_APDU_UPDATE;
        request[2]=off+chunk<large_length?BR_APDU_FLAG_MORE:0;put32(request+4,txid);
        put16(request+8,seq);put16(request+10,(uint16_t)(chunk+(seq?0u:4u)));
        if(!seq)put32(request+12,(uint32_t)large_length);
        memcpy(request+12+(seq?0u:4u),large+off,chunk);
        response_length=br_apdu(vm,request,12u+chunk+(seq?0u:4u),response,sizeof(response));
        if(response_length!=8||response[0]!=(off+chunk<large_length?0x61:0x90)){
            free(large);return 0;}off+=chunk;++seq;
    }
    free(large);if(vm->image_version!=4u||br_vm_run(vm,32)!=0)return 0;
    if(br_vm_init(&recovered)!=0||br_vm_bind_platform(&recovered,&platform)!=0||
       br_vm_set_issuer_key(&recovered,public_key,32)!=0||
       br_vm_recover(&recovered,prefix)!=0||recovered.last_txid!=txid||recovered.image_version!=4u||
       br_vm_run(&recovered,32)!=0||regp(&recovered,2)[0]!=42){br_vm_free(&recovered);return 0;}
    memset(request,0,12);request[0]=1;request[1]=BR_APDU_RECOVER;put32(request+4,++txid);
    response_length=br_apdu(&recovered,request,12,response,sizeof(response));
    if(response_length!=24||response[0]!=0x90||recovered.last_txid!=txid||
       get32(response+12)!=4u){br_vm_free(&recovered);return 0;}
    response_length=br_apdu(&recovered,request,12,response,sizeof(response));
    if(response_length!=8||response[0]!=0x69||response[1]!=0x85){br_vm_free(&recovered);return 0;}
    memset(request,0,12);request[0]=1;request[1]=BR_APDU_UPDATE;put32(request+4,txid+1u);
    put16(request+10,(uint16_t)(image_length+4u));put32(request+12,(uint32_t)image_length);
    memcpy(request+16,image,image_length);
    response_length=br_apdu(&recovered,request,16u+image_length,response,sizeof(response));
    if(response_length!=8||response[0]!=0x69||response[1]!=0x82||recovered.last_txid!=txid){
        br_vm_free(&recovered);return 0;}
    request[1]=BR_APDU_UPDATE;request[2]=BR_APDU_FLAG_MORE;put32(request+4,txid+1u);
    put16(request+10,(uint16_t)(first+4u));put32(request+12,(uint32_t)image_length);
    memcpy(request+16,image,first);
    if(br_apdu(&recovered,request,16u+first,response,sizeof(response))!=8||response[0]!=0x61){br_vm_free(&recovered);return 0;}
    br_vm_free(&recovered);
    if(br_vm_init(&reboot)!=0||br_vm_bind_platform(&reboot,&platform)!=0||
       br_vm_set_issuer_key(&reboot,public_key,32)!=0||br_vm_recover(&reboot,prefix)!=0||
       reboot.last_txid!=txid||br_vm_run(&reboot,32)!=0){br_vm_free(&reboot);return 0;}
    request[2]=0;put16(request+8,1);put16(request+10,(uint16_t)(image_length-first));
    memcpy(request+12,image+first,image_length-first);
    response_length=br_apdu(&reboot,request,12u+image_length-first,response,sizeof(response));
    if(response_length!=8||response[0]!=0x6a){br_vm_free(&reboot);return 0;}
    br_vm_free(&reboot);
    (void)unlink(path);
    for(txid=0;txid<5;++txid){snprintf(file,sizeof(file),"%s.%s",prefix,
        (const char*[]){"0","1","stage","image0","image1"}[txid]);unlink(file);}
    return 1;
}

static int exhaustion_tests(br_vm *vm) {
    br_insn loop = {BR_JMP, BR_WRAP, 0, 0, 0, 0, 0, 0};
    br_vm_reset(vm, 1);
    if (br_vm_load_code(vm, &loop, 1) != 0 || br_vm_run(vm, 8) == 0 ||
        vm->trap != BR_TRAP_BUDGET) return 0;
    br_vm_reset(vm, 1); vm->cancel_requested = 1;
    if (br_vm_load_code(vm, &loop, 1) != 0 || br_vm_run(vm, 8) == 0 ||
        vm->trap != BR_TRAP_CANCEL) return 0;
    return 1;
}

static int fuzz_tests(br_vm *vm) {
    uint8_t req[96], rsp[512];
    uint32_t x = 0x6751a021u;
    unsigned round, i;
    for (round = 0; round < 10000; ++round) {
        size_t n;
        for (i = 0; i < sizeof(req); ++i) {
            x ^= x << 13; x ^= x >> 17; x ^= x << 5; req[i] = (uint8_t)x;
        }
        n = (size_t)(x % sizeof(req));
        (void)br_apdu(vm, req, n, rsp, sizeof(rsp));
        if ((round & 3u) == 0) {
            br_vm_reset(vm, 1);
            (void)br_image_load(vm, req, n, NULL, 0, 0);
        }
        if ((round & 7u) == 0) {
            req[0] = 1; req[1] = BR_APDU_LOAD; req[2] = req[3] = 0;
            put32(req + 4, 1000u + round); put16(req + 8, 0); put16(req + 10, 64);
            (void)br_apdu(vm, req, 76, rsp, sizeof(rsp));
        }
    }
    return 1;
}

int br_selftest(void) {
    br_vm vm;
    br_insn bad[2];
    unsigned op;
    int failures = 0;
    if (br_vm_init(&vm) != 0) return 1;
    for (op = 0; op < BR_OPCODES; ++op)
        if (!positive_opcode(&vm, (uint8_t)op)) ++failures;
    for (op = 0; op < BR_OPCODES; ++op) {
        memset(bad, 0, sizeof(bad)); br_vm_reset(&vm, 1);
        bad[0].op = (uint8_t)op; bad[0].cap = 1; bad[1].op = BR_HALT;
        if (br_vm_load_code(&vm, bad, op == BR_HALT ? 1u : 2u) != 0 ||
            br_vm_run(&vm, 8) == 0 || vm.trap != BR_TRAP_CAPABILITY) ++failures;
    }
#define TEST(N,X) do{if(!(X)){fprintf(stderr,"FAIL:%s\n",N);++failures;}}while(0)
    TEST("arith",arithmetic_mode_tests(&vm));
    TEST("wide",wide_boundary_tests(&vm));
    TEST("stack",stack_width_tests(&vm));
    TEST("service",service_tests(&vm));
    TEST("assembler",br_assembler_selftest());
    TEST("persist",persistence_tests(&vm));
    TEST("image",image_tests(&vm));
    TEST("apdu",apdu_tests(&vm));
    TEST("update",signed_update_tests(&vm));
    TEST("exhaust",exhaustion_tests(&vm));
    TEST("fuzz",fuzz_tests(&vm));
#undef TEST
    br_vm_free(&vm);
    return failures;
}
#endif
