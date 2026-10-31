# Hardware validation

[English](hardware-validation.md) | [简体中文](hardware-validation.zh-CN.md)

Use a disposable or recoverable target first. Do not test on a production controller.

1. Confirm both boards use 3.3 V UART logic. Measure idle TX levels before connecting. Add a level shifter if either side is not 3.3 V compatible.
2. Connect only one transmitter to each receiver. Disable or isolate the target's onboard USB-UART transmitter before connecting the ESP32 TX.
3. Share ground. Use one power source per board or a verified isolation design. Never connect USB VBUS directly to another 5 V supply.
4. Configure the firmware TX/RX and optional DTR/RTS pins. Leave unknown control pins disabled.
5. Run a loopback test without the target, then a two-endpoint full-duplex test with different random data in each direction.
6. Verify each enabled control with a logic analyzer: polarity, timing, idle state and reset behavior.
7. Disconnect Wi-Fi during transfer. The session must fail, outputs must stop, and the next session must contain no stale bytes.
8. Only then compare a wired official programming session with the network path.

For targets with boot-enable or reset pins, document the exact line and polarity before mapping DTR or RTS. The ASR PRO path remains a planned example until these checks pass.
