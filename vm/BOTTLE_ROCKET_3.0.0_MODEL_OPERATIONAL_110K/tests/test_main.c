#include "brvm.h"

#include <stdio.h>

int main(void) {
    int failures = br_selftest();
    printf("{\"suite\":\"BOTTLE_ROCKET_3.0.0_MODEL\",\"failures\":%d,\"result\":\"%s\"}\n",
           failures, failures == 0 ? "PASS" : "FAIL");
    return failures == 0 ? 0 : 1;
}
