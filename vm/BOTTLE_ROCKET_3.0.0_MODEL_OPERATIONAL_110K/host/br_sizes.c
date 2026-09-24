#include "brvm.h"

#include <stdio.h>

int main(void) {
    const size_t word_bytes = (size_t)BR_LIMBS * sizeof(uint64_t);
    const size_t allocated = ((size_t)BR_REGS + 2u + BR_STACK_WORDS) * word_bytes;
    printf("{\"record\":\"BOTTLE_ROCKET.ReferenceResourceMeasurement\","
           "\"word_bytes\":%zu,\"register_bytes\":%zu,\"scratch_bytes\":%zu,"
           "\"vm_inline_bytes\":%zu,\"allocated_wide_bytes\":%zu,"
           "\"reference_total_bytes\":%zu}\n",
           word_bytes, (size_t)BR_REGS * word_bytes, 2u * word_bytes,
           sizeof(br_vm), allocated, sizeof(br_vm) + allocated);
    return 0;
}
