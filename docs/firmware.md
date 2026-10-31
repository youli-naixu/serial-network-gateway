# ESP32-S3 firmware build

[English](firmware.md) | [简体中文](firmware.zh-CN.md) · [README](../README.md)

## Scope and status

The firmware work targets a standalone ESP-IDF application for an ESP32-S3 network-to-UART gateway, using the Apache-2.0 [igrr/rfc2217-server component](https://github.com/igrr/rfc2217-server). It is separate from the PC package and is not a copy of another device's application firmware.

**Implementation, reproducible build, and hardware acceptance are pending for `0.2.0-alpha.1`.** The steps below define the build and review gate. They do not certify that a firmware artifact already exists or boots. Verify the actual project files before running them; if the standalone project or dependency manifest is incomplete, stop at that prerequisite and record it as pending.

The PC supports raw TCP and RFC2217. That does not establish both protocols as implemented firmware features. The standalone firmware's supported protocols, settings, and control mappings must be confirmed from its final implementation and tests.

## Toolchain and project prerequisites

Use the official [ESP-IDF setup for ESP32-S3](https://docs.espressif.com/projects/esp-idf/en/latest/esp32s3/get-started/index.html) and an activated ESP-IDF terminal. Record the exact ESP-IDF release and compiler version. Upstream component compatibility is not the same as this project's validated toolchain matrix.

Before building, confirm all of the following:

- `firmware/` is a standalone ESP-IDF project with a root `CMakeLists.txt`, application component, and dependency manifest.
- The manifest resolves `igrr/rfc2217-server` with a reproducible version constraint; retain the resolved dependency lock information for the build.
- Board settings match the actual module. For an ESP32-S3-WROOM-1-N16R8, flash is 16 MB and PSRAM is 8 MB Octal/OPI. Whether the application uses PSRAM must be explicit; do not apply Quad-PSRAM settings to an Octal module.
- The console/download path is separated from the target UART. The board pin map and any control outputs are reviewed before connecting a target.
- Network configuration is supplied locally. Generated configuration, build logs, flash images, and NVS can contain network credentials; inspect artifacts before sharing.

Exact ESP-IDF/component versions, menu labels, selected UART pins, listening port, and enabled control outputs remain pending until the implementation and successful build establish them.

## Build procedure

From the repository root, in the activated ESP-IDF terminal, use the standard project commands **after the prerequisites above are present**:

```powershell
idf.py --version
idf.py -C firmware set-target esp32s3
idf.py -C firmware menuconfig
idf.py -C firmware build
```

`set-target` can regenerate build configuration. Preserve any local configuration you need before changing target. In `menuconfig`, check flash size/mode, PSRAM mode if enabled, console routing, target UART pins, network setup, and listening port against the implementation. No specific menu name or credential mechanism is promised until implemented.

Do not manually add an unpinned dependency to work around an incomplete project. Resolve the manifest as part of the firmware implementation, then build with the recorded toolchain. Do not edit generated dependency code without documenting the patch and upstream license notices.

A successful build must report exit code `0` and produce the expected application, bootloader, partition table, and generated flash arguments. Record the source revision, resolved component version, relevant nonsecret configuration, image sizes, and SHA-256 values. Use the build's generated flash arguments; do not copy fixed offsets from another chip or project.

## Firmware behavior review

Before describing the firmware as a usable remote endpoint, check its implementation against the [remote contract](remote-contract.md):

| Area | Review requirement |
| --- | --- |
| RFC2217 | Negotiation, literal `0xFF`, fragmented commands, supported UART settings, and honest handling of unsupported requests. |
| UART | Selected peripheral and pins, baud/line configuration, RX/TX integrity, buffering, overflow handling. |
| Control | Document whether DTR, RTS, BREAK, flow control, modem state, and reset/boot outputs are supported, unmapped, or rejected. |
| Sessions | Client limit, disconnect cleanup, buffered UART data policy, network-loss behavior, no cross-session replay. |
| Diagnostics | Console output stays off the target UART; public reports omit credentials and unique device information. |
| Network | No claim of RFC2217 authentication/encryption; access is limited by the deployment network boundary. |

The upstream UART or `uart_esptool` example does not prove compatibility with another bootloader. In particular, ESP-specific auto-reset behavior is not an ASR programming contract.

## Flash and first-boot gate

Flashing overwrites the selected ESP32-S3 board. Perform it only after the [electrical and board-identity checks](hardware-validation.md) and when the operator has selected a disposable or backed-up board. Disconnect the target UART/control wiring for the first firmware boot.

In PowerShell, identify the confirmed gateway download port locally:

```powershell
$GatewayPort = Read-Host "Confirmed ESP32-S3 gateway download port"
idf.py -C firmware -p "$GatewayPort" flash monitor
```

Use the connector and boot procedure appropriate to the board. On a DevKitC-style board, the USB-to-UART connector and native USB connector are different paths. If manual ROM download mode is needed, hold BOOT, tap RESET, then release BOOT. This sequence applies to the ESP32-S3 gateway, not the downstream target. Exit the ESP-IDF monitor with its documented escape sequence.

Confirm boot stability, selected memory settings, console routing, and the configured network listener before attaching a loopback or target. Do not publish raw boot logs containing network names or device identifiers. A boot log and an open TCP port still do not validate UART or target programming.

## Acceptance and release evidence

Progress through the [hardware validation stages](hardware-validation.md), then update the matching [verification rows](verification.md). Keep build-only, boot-only, UART-loopback, driver, and target-application results separate.

Before publishing firmware binaries, verify that the artifact contains no embedded credentials or private configuration, retain the applicable component/framework notices, and record exact build provenance. A local credential-bearing build is not a public release artifact. Until all claimed stages pass, describe the firmware as experimental and keep the unresolved stages pending.
