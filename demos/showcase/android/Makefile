# Build targets for the Android showcase pair — four products from two codebases
# (Compose × Views, each in an accessibility-on / -off flavor). See ../SPEC.md §2.
# Invoke from anywhere with `make -C demos/showcase/android <target>`.
#
# `run` / `doctor` land with the BE-0007 adb backend; until then these targets only build.
.PHONY: build-all wrapper \
	compose-build compose-noax-build views-build views-noax-build clean

HERE := $(abspath $(dir $(lastword $(MAKEFILE_LIST))))

# The wrapper jar is a binary and not committed (.gitignore); materialize it once from an
# installed Gradle (brew install gradle), then every build goes through ./gradlew.
GRADLEW := $(HERE)/gradlew
wrapper:
	cd $(HERE) && gradle wrapper

$(HERE)/gradle/wrapper/gradle-wrapper.jar:
	$(MAKE) -C $(HERE) wrapper

build-all: compose-build compose-noax-build views-build views-noax-build

# Each build lands at <module>/build/outputs/apk/<flavor>/debug/<module>-<flavor>-debug.apk,
# exactly where showcase.config.yaml's android targets point their appPath.
compose-build: $(HERE)/gradle/wrapper/gradle-wrapper.jar
	cd $(HERE) && $(GRADLEW) :compose:assembleA11yDebug
compose-noax-build: $(HERE)/gradle/wrapper/gradle-wrapper.jar
	cd $(HERE) && $(GRADLEW) :compose:assembleNoaxDebug
views-build: $(HERE)/gradle/wrapper/gradle-wrapper.jar
	cd $(HERE) && $(GRADLEW) :views:assembleA11yDebug
views-noax-build: $(HERE)/gradle/wrapper/gradle-wrapper.jar
	cd $(HERE) && $(GRADLEW) :views:assembleNoaxDebug

clean:
	rm -rf $(HERE)/build $(HERE)/compose/build $(HERE)/views/build $(HERE)/.gradle
