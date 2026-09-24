CC ?= cc
CPPFLAGS := -Isrc
CFLAGS := -std=c11 -O2 -Wall -Wextra -Werror -fno-common -fstack-protector-strong
LDLIBS := -lcrypto
BUILD := .build
CORE := src/brvm.c src/brasm.c
IMAGE := deploy/BOTTLE_ROCKET_SIM_CORE_3.0.0_MODEL.brimg
DIST_MAX := 110000

.PHONY: all test image check-size check-dist-size repro signed-smoke verify operational sanitize clean

all: $(BUILD)/brctl $(BUILD)/br_tests $(BUILD)/br_sizes

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/brctl: $(CORE) src/brvm.h src/brasm.h host/brctl.c | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(CORE) host/brctl.c -o $@ $(LDLIBS)

$(BUILD)/br_tests: $(CORE) src/brvm.h src/brasm.h tests/test_main.c | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) $(CORE) tests/test_main.c -o $@ $(LDLIBS)

$(BUILD)/br_sizes: src/brvm.h host/br_sizes.c | $(BUILD)
	$(CC) $(CPPFLAGS) $(CFLAGS) host/br_sizes.c -o $@

test: $(BUILD)/br_tests
	$(BUILD)/br_tests

image: $(BUILD)/brctl examples/boot.mssl
	$(BUILD)/brctl assemble examples/boot.mssl $(IMAGE)

check-size: image
	test "$$(wc -c < $(IMAGE))" -le 51200

check-dist-size:
	@bytes="$$(find . -type f ! -path './.build/*' -exec wc -c {} \; | awk '{n+=$$1} END {print n}')"; \
	 echo "distribution_uncompressed_bytes=$$bytes ceiling=$(DIST_MAX)"; \
	 test "$$bytes" -lt "$(DIST_MAX)"

repro: $(BUILD)/brctl
	$(BUILD)/brctl assemble examples/boot.mssl $(BUILD)/repro-a.brimg
	$(BUILD)/brctl assemble examples/boot.mssl $(BUILD)/repro-b.brimg
	cmp $(BUILD)/repro-a.brimg $(BUILD)/repro-b.brimg
	rm -f $(BUILD)/repro-a.brimg $(BUILD)/repro-b.brimg

signed-smoke: image
	$(BUILD)/brctl keygen $(BUILD)/smoke.private $(BUILD)/smoke.public
	$(BUILD)/brctl sign-image $(IMAGE) $(BUILD)/smoke-signed.brimg $(BUILD)/smoke.private
	$(BUILD)/brctl verify-image $(BUILD)/smoke-signed.brimg $(BUILD)/smoke.public
	$(BUILD)/brctl run-signed $(BUILD)/smoke-signed.brimg $(BUILD)/smoke.public
	$(BUILD)/brctl update $(BUILD)/smoke-signed.brimg $(BUILD)/smoke.public --state $(BUILD)/smoke-state
	$(BUILD)/brctl recover-state $(BUILD)/smoke-state
	$(BUILD)/brctl status --state $(BUILD)/smoke-state
	printf '010c00000200000000000000\n' | $(BUILD)/brctl serve --state $(BUILD)/smoke-state | grep 9000
	rm -f $(BUILD)/smoke.private $(BUILD)/smoke.public $(BUILD)/smoke-signed.brimg
	rm -f $(BUILD)/smoke-state.*

verify: test check-size repro signed-smoke $(BUILD)/br_sizes
	$(BUILD)/brctl inspect-image $(IMAGE)
	$(BUILD)/brctl run $(IMAGE)
	$(BUILD)/br_sizes

operational: verify check-dist-size

sanitize: | $(BUILD)
	$(CC) $(CPPFLAGS) -std=c11 -O1 -g -Wall -Wextra -Werror \
		-fsanitize=address,undefined -fno-omit-frame-pointer \
		$(CORE) tests/test_main.c -o $(BUILD)/br_tests_san $(LDLIBS)
	ASAN_OPTIONS=detect_leaks=0 $(BUILD)/br_tests_san

clean:
	rm -f $(BUILD)/brctl $(BUILD)/br_tests $(BUILD)/br_tests_san $(BUILD)/br_sizes \
		$(BUILD)/smoke.private $(BUILD)/smoke.public $(BUILD)/smoke-signed.brimg \
		$(BUILD)/smoke-state.* \
		$(BUILD)/repro-a.brimg $(BUILD)/repro-b.brimg
