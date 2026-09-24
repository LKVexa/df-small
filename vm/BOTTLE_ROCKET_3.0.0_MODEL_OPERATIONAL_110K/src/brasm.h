#ifndef BOTTLE_ROCKET_BRASM_H
#define BOTTLE_ROCKET_BRASM_H

#include <stdint.h>

int br_assemble_file(const char *source, const char *image,
                     uint32_t version_override);
int br_assembler_selftest(void);

#endif
