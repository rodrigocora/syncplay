SINGLE_USER	= false

ifeq ($(shell uname)),FreeBSD)
	BSD          = true
endif
ifeq ($(shell uname)),NetBSD)
	BSD          = true
endif
ifeq ($(shell uname)),OpenBSD)
	BSD          = true
endif
ifeq ($(shell uname)),DragonFly)
	BSD          = true
endif

ifeq ($(SINGLE_USER),false)
ifneq ($(BSD),true)
	PREFIX      ?= /usr
	VLC_LIB_PATH = ${PREFIX}/lib
	VLC_LIB_PATH64 = ${PREFIX}/lib/x86_64-linux-gnu
else
	PREFIX      ?= /usr/local
	VLC_LIB_PATH = ${PREFIX}/lib
	VLC_LIB_PATH64 = ${PREFIX}/lib
endif
else
	PREFIX       = ${HOME}/.local
	VLC_LIB_PATH = ${HOME}/.local/share
	VLC_LIB_PATH64 = ${HOME}/.local/share
endif

	BIN_PATH          = ${DESTDIR}${PREFIX}/bin
	LIB_PATH          = ${DESTDIR}${PREFIX}/lib
	APP_SHORTCUT_PATH = ${DESTDIR}${PREFIX}/share/applications
	SHARE_PATH        = ${DESTDIR}${PREFIX}/share

common:
	-mkdir -p $(LIB_PATH)/syncplay/syncplay/resources/lua/intf
	-mkdir -p $(APP_SHORTCUT_PATH)
	-mkdir -p $(SHARE_PATH)/pixmaps/
	-mkdir -p $(SHARE_PATH)/icons/
	-mkdir -p $(SHARE_PATH)/man/man1/
	cp -r syncplay $(LIB_PATH)/syncplay/
	chmod 755 $(LIB_PATH)/syncplay/
	cp -r syncplay/resources/hicolor $(SHARE_PATH)/icons/
	cp -r syncplay/resources/*.png $(LIB_PATH)/syncplay/syncplay/resources/
	cp -r syncplay/resources/*.lua $(LIB_PATH)/syncplay/syncplay/resources/
	cp -r syncplay/resources/lua/intf/*.lua $(LIB_PATH)/syncplay/syncplay/resources/lua/intf/
	cp syncplay/resources/hicolor/128x128/apps/syncplay.png $(SHARE_PATH)/pixmaps/

u-common:
	-rm -rf $(LIB_PATH)/syncplay
	-rm $(SHARE_PATH)/icons/hicolor/*/apps/syncplay.png
	-rm $(SHARE_PATH)/pixmaps/syncplay.png
	-rm $(SHARE_PATH)/man/man1/syncplay.1.gz

client:
	-mkdir -p $(BIN_PATH)
	cp syncplayClient.py $(BIN_PATH)/syncplay
	sed -i -e '/# libpath/ a\import site\nsite.addsitedir\("${PREFIX}/lib/syncplay"\)' $(BIN_PATH)/syncplay
	chmod 755 $(BIN_PATH)/syncplay
	cp syncplayClient.py $(LIB_PATH)/syncplay/
	cp syncplay/resources/syncplay.desktop $(APP_SHORTCUT_PATH)/
	gzip docs/syncplay.1 --stdout > $(SHARE_PATH)/man/man1/syncplay.1.gz

u-client:
	-rm $(BIN_PATH)/syncplay
	-rm $(LIB_PATH)/syncplay/syncplayClient.py
	-rm ${DESTDIR}$(VLC_LIB_PATH)/vlc/lua/intf/syncplay.lua
	-rm ${DESTDIR}$(VLC_LIB_PATH64)/vlc/lua/intf/syncplay.lua
	-rm $(APP_SHORTCUT_PATH)/syncplay.desktop
	-rm $(SHARE_PATH)/man/man1/syncplay.1.gz

server:
	-mkdir -p $(BIN_PATH)
	cp syncplayServer.py $(BIN_PATH)/syncplay-server
	sed -i -e '/# libpath/ a\import site\nsite.addsitedir\("${PREFIX}/lib/syncplay"\)' $(BIN_PATH)/syncplay-server
	chmod 755 $(BIN_PATH)/syncplay-server
	cp syncplayServer.py $(LIB_PATH)/syncplay/
	cp syncplay/resources/syncplay-server.desktop $(APP_SHORTCUT_PATH)/
	gzip docs/syncplay-server.1 --stdout > $(SHARE_PATH)/man/man1/syncplay-server.1.gz

u-server:
	-rm $(BIN_PATH)/syncplay-server
	-rm $(LIB_PATH)/syncplay/syncplayServer.py
	-rm $(APP_SHORTCUT_PATH)/syncplay-server.desktop
	-rm $(SHARE_PATH)/man/man1/syncplay-server.1.gz

warnings:
ifeq ($(SINGLE_USER),true)
	@echo -e "\n**********\n**********\n \nRemeber to add ${HOME}/.local/bin to your \$$PATH with 'echo \"export PATH=\$$PATH:${HOME}/.local/bin\" >> ${HOME}/.profile' \nThis will take effect after you logoff.\n \n**********\n**********\n"
endif

install-client: common client warnings

uninstall-client: u-client u-common

install-server: common server warnings

uninstall: u-server u-common

# --- Docker (watch-history proxy) -----------------------------------------
CONTAINER   ?= syncplay-history
IMAGE       ?= syncplay-history
DOCKER_PORT ?= 12346

# Configuration is split into two files, both bind-mounted into the container
# and loaded by entrypoint.sh:
#   config.env    -- non-secret settings (committed)
#   .secrets.env  -- secrets only (DB_PASS, SYNCPLAY_PASSWORD, ...), gitignored
# Because the files are mounted and the password is read from inside the
# container, it never appears on the command line or in 'make -n' output.
# Real environment variables (docker run -e) override values in these files.
SECRETS_FILE ?= .secrets.env
CONFIG_FILE  ?= config.env

# Remove (if present) and start the container, printing logs. Mounts config.env
# and .secrets.env (if present).
up:
	docker rm -f $(CONTAINER) || true
	# ,z on file mounts: SELinux label so the container can read them (Fedora)
	docker run -d --name $(CONTAINER) -p $(DOCKER_PORT):$(DOCKER_PORT) \
		-v $(abspath $(CONFIG_FILE)):/app/config.env:ro,z \
		$(if $(wildcard $(SECRETS_FILE)),-v $(abspath $(SECRETS_FILE)):/app/.secrets.env:ro,z,) \
		$(IMAGE)
	docker logs $(CONTAINER)

# Build the image (--no-cache: BuildKit on this host has reused stale COPY
# layers and shipped the old history.py despite local edits)
build:
	docker build --no-cache -t $(IMAGE) .

# Remove and start the container again (no rebuild)
rerun: up

# Build the image, then remove and start the container
rebuild: build up

# Show recent debug/traceback lines from the container (wide tail: a real
# client sends a State ping every ~1s, which would otherwise push the early
# Hello/Set lines off a small window)
logs:
	docker logs --tail 500 $(CONTAINER)

# Stop and remove the container
down:
	docker rm -f $(CONTAINER) || true

install: common client server warnings

uninstall: u-server u-common
