# Build tool for the demos/showcase sample app's .xcodeproj. Install with `brew bundle` or
# `make deps`. Not a bajutsu backend requirement: the iOS backend (XCUITest) needs only Xcode's
# `xcodebuild`, resolved by the config-aware installer from the requirements mapping
# (bajutsu/requirements.py, BE-0164), not from here.
brew "xcodegen"  # generates the sample app's .xcodeproj for the build

# git-secrets (https://github.com/awslabs/git-secrets) blocks a secret from being committed.
# Not a bajutsu backend requirement either — `make hooks` self-heals its pattern
# registration once it's on PATH, and the tracked hooks degrade gracefully when it's absent — but
# every contributor's machine should have it.
brew "git-secrets"
