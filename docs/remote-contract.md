# Remote endpoint contract

[English](remote-contract.md) | [简体中文](remote-contract.zh-CN.md) · [README](../README.md)

## Roles and protocol selection

The PC gateway accepts one local client and connects to one configured remote TCP endpoint. In device mode it forwards the network stream unchanged. It does not implement a target-specific programming protocol, synthesize a Windows COM device, or translate between raw TCP and RFC2217.

| Profile protocol | Required local client | Required remote endpoint |
| --- | --- | --- |
| `raw` | Raw TCP stream | Raw byte stream with UART parameters configured out of band. |
| `rfc2217` | Telnet COM-port/RFC2217 client | RFC2217 server that negotiates and implements the required UART settings. |

A remote endpoint can be a compatible implementation other than the proposed ESP32-S3 firmware. Compatibility must be demonstrated for the required features; an open TCP port is insufficient.

## Binary transport and UART settings

Raw TCP must preserve all byte values and arbitrary read/write fragmentation. TCP chunks are not application packet boundaries. In raw mode, the gateway cannot infer baud, parity, stop bits, modem control, or boot sequencing from the payload.

RFC2217 extends Telnet. Endpoints must negotiate required options, handle commands split across TCP reads, and escape literal IAC (`0xFF`) correctly. The gateway observes RFC2217 requests and replies for statistics while forwarding the original bytes. It does not repair unsupported or incorrect negotiations at either end.

Distinguish the following evidence:

- `serial_requested`: a serial/control request observed from the local client.
- `serial_confirmed`: a reply observed from the remote endpoint; in simulator mode this is a simulated reply.
- Physical measurement: UART bit timing or a control-pin waveform measured on the actual board.
- Target success: the application and target confirm the intended operation, including verification/readback where supported.

These levels are not interchangeable. A firmware implementation must document its supported baud range, data bits, parity, stop bits, flow control, DTR/RTS mapping, BREAK behavior, and modem/line status handling. Unsupported features must not be presented as physically implemented because a simulator accepted them.

No generic DTR/RTS-to-reset or boot-pin mapping is assumed. Target boot entry, polarity, level translation, and pulse duration require board-specific design and [hardware validation](hardware-validation.md).

## Session lifecycle

The PC lifecycle is `ready` → `connecting` → `active` → `needs_reset` after a session ends. Startup failures may instead report `error`. A second concurrent client is rejected. Disconnect, stalled sending, or a transport error closes both directions and clears the session's application queues.

In the GUI, close the client's COM and redirector session before explicitly rearming. In the CLI, `serve` ends after the session and must be started again. A fresh session must not receive old pending data. Automatic replay of a partial firmware image or command stream is outside this contract.

A remote implementation should accept a new connection only after releasing its prior UART/network session, document what happens to buffered UART data, and prevent cross-session leakage. Its own client limit and cleanup behavior require separate tests; they cannot be inferred from the PC gateway's behavior.

## Buffering and diagnostics

The gateway uses separate bounded application queues for the two directions and applies backpressure. A send-stall timeout ends a session instead of silently dropping bytes and continuing. The configured buffer bound applies to those queues, not total process memory, TCP kernel buffers, a redirector, or UART hardware.

TCP counters include transport protocol bytes; payload counters remove recognized RFC2217/Telnet overhead for observation. Neither counter proves that a target consumed or programmed the bytes. Exported status can include endpoint/profile information, errors, and device metadata from other diagnostic actions; sanitize before publication.

## Device profile example

This is a documentation-only example. `192.0.2.10` is a reserved documentation address, not a reachable service. Replace it locally only after the remote implementation and network access have been verified.

```json
{
  "schema_version": 1,
  "selected": "uart-lab",
  "profiles": [
    {
      "name": "uart-lab",
      "mode": "device",
      "protocol": "rfc2217",
      "listen_host": "127.0.0.1",
      "listen_port": 7000,
      "remote_host": "192.0.2.10",
      "remote_port": 2217
    }
  ]
}
```

Port `2217` here is an example, not a firmware-default claim. Save a local configuration, verify its contents, and run `python -m serial_gateway --config local-device.json serve --profile uart-lab` only when ready for the device gate. Use `--simulate` to override the target with a local simulator during PC checks.

## Network boundary

The PC listener is restricted to loopback. Raw TCP and RFC2217 do not themselves provide authentication or encryption; this project does not add those protections to the forwarded protocol. Limit access to the remote endpoint through a trusted network boundary or a separately configured authenticated tunnel. Do not expose an unauthenticated UART endpoint to the public Internet.

See [SECURITY](../SECURITY.md) for reporting and data-handling guidance. Wi-Fi association alone does not authenticate an RFC2217 client, and loopback binding does not prevent other local processes from connecting.

## Protocol references

- [RFC 2217: Telnet Com Port Control Option](https://www.rfc-editor.org/rfc/rfc2217)
- [pySerial RFC2217 support](https://pyserial.readthedocs.io/en/latest/pyserial_api.html#rfc2217-network-ports)
- [igrr RFC2217 server for ESP-IDF](https://github.com/igrr/rfc2217-server)
