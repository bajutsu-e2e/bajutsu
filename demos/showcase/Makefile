# Build / run targets for the showcase suite — four products from two codebases
# (UIKit × SwiftUI, each in an accessibility-on / -off variant). See SPEC.md.
# Invoke from anywhere with `make -C demos/showcase <target>`.
.PHONY: build-all \
	swiftui-gen swiftui-build swiftui-noax-build \
	uikit-gen uikit-build uikit-noax-build \
	run-swiftui run-uikit doctor record clean

ROOT   := $(abspath $(dir $(lastword $(MAKEFILE_LIST)))/../..)
HERE   := $(ROOT)/demos/showcase
CONFIG := $(HERE)/showcase.config.yaml
SWIFTUI := $(HERE)/swiftui
UIKIT   := $(HERE)/uikit

# A built scheme lands at build/dd/Build/Products/Debug-iphonesimulator/<Scheme>.app,
# which is exactly the appPath each apps.<name> entry in showcase.config.yaml points to.
define build_scheme
	cd $(1) && xcodegen generate
	cd $(1) && xcodebuild -project $(2).xcodeproj -scheme $(3) \
		-destination 'generic/platform=iOS Simulator' -derivedDataPath build/dd build
endef

build-all: swiftui-build swiftui-noax-build uikit-build uikit-noax-build

swiftui-gen:
	cd $(SWIFTUI) && xcodegen generate
swiftui-build:
	$(call build_scheme,$(SWIFTUI),BajutsuShowcaseSwiftUI,BajutsuShowcaseSwiftUI)
swiftui-noax-build:
	$(call build_scheme,$(SWIFTUI),BajutsuShowcaseSwiftUI,BajutsuShowcaseSwiftUINoAx)

uikit-gen:
	cd $(UIKIT) && xcodegen generate
uikit-build:
	$(call build_scheme,$(UIKIT),BajutsuShowcaseUIKit,BajutsuShowcaseUIKit)
uikit-noax-build:
	$(call build_scheme,$(UIKIT),BajutsuShowcaseUIKit,BajutsuShowcaseUIKitNoAx)

# Run the shared id-based scenarios against the a11y apps on a booted Simulator (idb).
# Prereqs: a booted simulator, `brew install facebook/fb/idb-companion`, `uv sync --extra idb`.
SIM ?= $(shell xcrun simctl list devices booted | grep -oE '[0-9A-F-]{36}' | head -1)

run-swiftui: swiftui-build
	cd $(ROOT) && uv run bajutsu run --app showcase-swiftui --udid $(SIM) --backend idb --config $(CONFIG)
run-uikit: uikit-build
	cd $(ROOT) && uv run bajutsu run --app showcase-uikit --udid $(SIM) --backend idb --config $(CONFIG)

# The accessibility A/B: doctor grades the a11y app Ready and the no-a11y app Blocked.
doctor: swiftui-build swiftui-noax-build
	cd $(ROOT) && uv run bajutsu doctor --app showcase-swiftui      --udid $(SIM) --backend idb --config $(CONFIG)
	cd $(ROOT) && uv run bajutsu doctor --app showcase-swiftui-noax --udid $(SIM) --backend idb --config $(CONFIG)

# AI authoring against the no-a11y app — record falls down the stability ladder (DESIGN §5).
# Needs ANTHROPIC_API_KEY. GOAL overrides the first non-comment line of record/goals.txt.
GOAL ?= $(shell grep -vE '^[[:space:]]*#|^[[:space:]]*$$' $(HERE)/record/goals.txt | head -1)
record: swiftui-noax-build
	cd $(ROOT) && uv run bajutsu record --app showcase-swiftui-noax --udid $(SIM) --backend idb \
		--config $(CONFIG) --goal "$(GOAL)"

clean:
	rm -rf $(SWIFTUI)/build $(UIKIT)/build \
		$(SWIFTUI)/*.xcodeproj $(UIKIT)/*.xcodeproj
