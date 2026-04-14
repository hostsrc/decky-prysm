.PHONY: build dist clean deploy

VERSION := $(shell node -p "require('./package.json').version")
DIST_DIR := /tmp/prysm-dist
ZIP_NAME := Prysm-v$(VERSION).zip
DECK_IP ?= 192.168.88.197
PLUGIN_DIR := /home/deck/homebrew/plugins/Prysm

build:
	pnpm build

dist: build
	rm -rf $(DIST_DIR)
	mkdir -p $(DIST_DIR)/Prysm/{dist,backend,bin,defaults}
	cp plugin.json package.json main.py LICENSE README.md $(DIST_DIR)/Prysm/
	cp dist/index.js $(DIST_DIR)/Prysm/dist/
	cp server/stream_server.py $(DIST_DIR)/Prysm/backend/
	cp backend/mediamtx.yml $(DIST_DIR)/Prysm/bin/
	cp defaults/settings.json $(DIST_DIR)/Prysm/defaults/
	@if [ -f /tmp/mediamtx ]; then \
		cp /tmp/mediamtx $(DIST_DIR)/Prysm/bin/mediamtx; \
		chmod +x $(DIST_DIR)/Prysm/bin/mediamtx; \
	else \
		echo "WARNING: MediaMTX binary not found at /tmp/mediamtx (WebRTC mode unavailable)"; \
	fi
	cd $(DIST_DIR) && zip -r $(CURDIR)/$(ZIP_NAME) Prysm/
	@echo "Created $(ZIP_NAME) ($$(du -sh $(ZIP_NAME) | cut -f1))"

deploy: build
	scp dist/index.js main.py deck@$(DECK_IP):/tmp/
	scp server/stream_server.py deck@$(DECK_IP):/tmp/stream_server.py
	ssh deck@$(DECK_IP) "sudo cp /tmp/index.js $(PLUGIN_DIR)/dist/index.js && \
		sudo cp /tmp/main.py $(PLUGIN_DIR)/main.py && \
		sudo cp /tmp/stream_server.py $(PLUGIN_DIR)/server/stream_server.py && \
		sudo systemctl restart plugin_loader && \
		sudo pkill -f steamwebhelper"
	@echo "Deployed to $(DECK_IP)"

clean:
	rm -rf dist/ $(DIST_DIR) *.zip
