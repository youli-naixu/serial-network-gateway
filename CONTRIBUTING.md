# Contributing

[English](CONTRIBUTING.md) | [简体中文](CONTRIBUTING.zh-CN.md)

Use small pull requests with a clear problem, behavioral change and verification evidence. Keep transport, driver adapters, GUI and board configuration separate. Do not add target-specific flashing bytes to the transparent data path.

## Local checks

```sh
python -m pip install -e ".[dev]"
python -m pytest -q -W error::ResourceWarning
python -m serial_gateway --language en selftest
python tools/check_public_tree.py
python firmware/scripts/build.py
python firmware/scripts/build.py --check-enabled
```

A firmware build is not a hardware test. The compile-check image deliberately stops before network or GPIO initialization. Do not remove this protection to claim a test passed.

Update English and Simplified Chinese documentation together. Preserve user-defined profile names. For GUI changes, inspect both languages in the actual app before packaging; do not replace visual checks with syntax checks.

## Hardware and driver work

Only open explicitly selected, verified virtual ports during automated tests. Physical UART tests need a confirmed pin map, voltage levels and transmitter isolation. Never automatically disable driver signing, Secure Boot or memory integrity. Driver installation and UAC require the user's normal Windows authorization.

Use a new branch and descriptive commits. Before a pull request, inspect staged files and exclude credentials, local paths, IP/MAC/device identities, downloaded installers, private reports and build outputs. Sample addresses must use loopback or documentation ranges. Do not bundle third-party virtual COM products.

## Release gate

Record the source revision, tool versions, exact tests and limits. A release claiming virtual COM support must demonstrate a real Windows COM client reaching the network endpoint, including required configuration/control changes and disconnect behavior. Do not label simulation as hardware validation. This initial project is not to be uploaded until the agreed local acceptance and sanitization checks pass.
