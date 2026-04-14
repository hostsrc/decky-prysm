TARGET := x86_64-unknown-linux-musl

.PHONY: all clean

all:
	mkdir -p out
	cd src/prysm-engine && cargo zigbuild --release --target $(TARGET)
	cp src/prysm-engine/target/$(TARGET)/release/prysm-engine out/prysm-engine

clean:
	rm -rf out
	cd src/prysm-engine && cargo clean
