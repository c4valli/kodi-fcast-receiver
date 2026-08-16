ADDON_ID=service.fcast.receiver
VERSION=$(shell sed -n 's/.*<addon .*version="\([^"]*\)".*/\1/p' addon.xml)

DIST=dist
STAGE=$(DIST)/$(ADDON_ID)
ZIP=$(DIST)/$(ADDON_ID)-$(VERSION).zip

# Source of the landing page, and the tree published to GitHub Pages -- which
# is also where `repo` writes the add-on repository. See the `site` target.
SITE=site
PAGES=repo

# The add-on payload: everything that must exist on a device, and nothing else.
# Kept explicit so neither a zip nor a deploy can land a subset of the files.
# CHANGELOG.md is deliberately not in here: Kodi shows the <news> element
# from addon.xml, and only falls back to a changelog.txt inside the add-on
# when that element is empty.
PAYLOAD=addon.xml icon.png LICENSE.txt resources

# Override per device: make deploy KODI_HOST=pi@raspberrypi
KODI_HOST ?=
KODI_ADDON_DIR ?= ~/.kodi/addons/$(ADDON_ID)

# Where the add-on repository will be served from. Point it at GitHub Pages,
# or at any web server on the LAN.
REPO_URL ?= https://ozankiratli.github.io/kodi-fcast-receiver

all: $(ZIP)

# Kodi requires the archive to hold a single top-level directory named exactly
# after the add-on id, so stage it under that name rather than zipping the
# checkout (which is called kodi-fcast-receiver).
$(ZIP): $(PAYLOAD) addon.xml
	@rm -rf $(STAGE)
	@mkdir -p $(STAGE)
	@cp -r $(PAYLOAD) $(STAGE)/
	@find $(STAGE) -name '__pycache__' -type d -prune -exec rm -rf {} +
	@cd $(DIST) && rm -f $(ADDON_ID)-$(VERSION).zip && zip -qr $(ADDON_ID)-$(VERSION).zip $(ADDON_ID)
	@rm -rf $(STAGE)
	@echo "built $(ZIP)"

clean:
	@rm -rf $(DIST) $(PAGES)
	@echo "cleaned"

# Runs against stubbed Kodi modules, so no device and no Kodi install needed.
test:
	@python3 -m unittest discover -s dev/tests -t dev/tests -v

# Push the whole add-on directory in one shot. --delete matters: copying
# individual files by hand is how a new module ends up importing a symbol from
# a stale one, which Kodi only reports as a bare ImportError.
#
# Deploys into KODI_ADDON_DIR on this machine when KODI_HOST is unset (clone
# the repo on the Kodi box and run it there), or over ssh when it is set.
deploy:
ifeq ($(KODI_HOST),)
	@target=$$(eval echo "$(KODI_ADDON_DIR)"); \
	if [ "$$(cd "$$target" 2>/dev/null && pwd)" = "$$(pwd)" ]; then \
		echo "refusing to deploy: KODI_ADDON_DIR is this checkout" >&2; \
		echo "clone the repo somewhere else, e.g. ~/src/kodi-fcast-receiver" >&2; \
		exit 1; \
	fi; \
	mkdir -p "$$target"; \
	rsync -av --delete --exclude '__pycache__' $(PAYLOAD) "$$target/"; \
	echo; \
	echo "deployed $(VERSION) to $$target"
else
	@rsync -av --delete --exclude '__pycache__' \
		$(PAYLOAD) $(KODI_HOST):$(KODI_ADDON_DIR)/
	@echo
	@echo "deployed $(VERSION) to $(KODI_HOST):$(KODI_ADDON_DIR)"
endif
	@echo "restart the service: disable/enable it in Settings > Add-ons, or restart Kodi"
	@echo "changed resources/language? restart Kodi itself - add-on strings are only"
	@echo "loaded at start-up, so settings labels stay blank until it does"

# Same thing for hosts with no rsync -- notably LibreELEC and CoreELEC, whose
# base images ship neither rsync nor git nor make. Needs only ssh and tar on
# the far end. Stages beside the target and swaps, so a failed transfer leaves
# the running install untouched, and .bak is the rollback.
deploy-ssh:
ifeq ($(KODI_HOST),)
	$(error set KODI_HOST, e.g. make deploy-ssh KODI_HOST=root@libreelec)
endif
	@tar -cf - --exclude '__pycache__' $(PAYLOAD) | \
		ssh $(KODI_HOST) 'set -e; \
			rm -rf "$(KODI_ADDON_DIR).new" "$(KODI_ADDON_DIR).bak"; \
			mkdir -p "$(KODI_ADDON_DIR).new"; \
			tar -xf - -C "$(KODI_ADDON_DIR).new"; \
			if [ -d "$(KODI_ADDON_DIR)" ]; then \
				mv "$(KODI_ADDON_DIR)" "$(KODI_ADDON_DIR).bak"; \
			fi; \
			mv "$(KODI_ADDON_DIR).new" "$(KODI_ADDON_DIR)"'
	@echo
	@echo "deployed $(VERSION) to $(KODI_HOST):$(KODI_ADDON_DIR)"
	@echo "previous install kept at $(KODI_ADDON_DIR).bak"
	@echo "restart the service: disable/enable it in Settings > Add-ons, or restart Kodi"
	@echo "changed resources/language? restart Kodi itself - add-on strings are only"
	@echo "loaded at start-up, so settings labels stay blank until it does"

# Build the static Kodi repository tree (addons.xml + md5 + zips) in repo/.
repo: $(ZIP)
	@python3 dev/tools/build_repo.py --url $(REPO_URL)

# The landing page, copied to the root of the same tree the repository is built
# in. GitHub Pages serves exactly one artifact per site, so the page and the
# repository have to be published together: deploying either on its own takes
# the other one down with it, and a missing addons.xml stops every installed
# device from seeing updates.
site:
	@mkdir -p $(PAGES)
	@cp $(SITE)/index.html $(SITE)/style.css icon.png $(PAGES)/
	@echo "site copied into $(PAGES)/"

# What CI uploads to Pages. Sequential, not a plain prerequisite list: the
# repository build wipes its output directory, so a parallel make could copy
# the page in first and watch it be deleted.
pages: repo
	@$(MAKE) --no-print-directory site

.PHONY: all clean deploy deploy-ssh pages repo site test
