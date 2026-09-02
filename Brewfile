# Build tool for the demos/showcase sample app's .xcodeproj. Install with `brew bundle` or
# `make deps`. Not a bajutsu backend requirement: the iOS backend (XCUITest) needs only Xcode's
# `xcodebuild`, resolved by the config-aware installer from the requirements mapping
# (bajutsu/common/provisioning/requirements.py, BE-0164), not from here.
brew "xcodegen"  # generates the sample app's .xcodeproj for the build

# gitleaks (https://github.com/gitleaks/gitleaks) blocks a secret from being committed, configured
# by the tracked .gitleaks.toml. Not a bajutsu backend requirement either — the tracked hooks
# degrade gracefully when it's absent — but every contributor's machine should have it.
brew "gitleaks"
