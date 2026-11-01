"""Win32 access to the hidden end of a com0com pair.

The application (for example an official flashing tool) opens the visible
``COMx`` end. This module opens the paired ``CNCBn`` end and uses the
driver's extended ``IOCTL_SERIAL_LSRMST_INSERT`` to receive the application's
baud rate and line control as in-band records, so changes such as switching
to 1.5 Mbaud after opening reach the network endpoint. DTR/RTS set by the
application appear as DSR/CTS on this end (default com0com pinout).

Constants come from com0com ``include/cncext.h`` (GPL-2.0, used here only as
an interface definition; no driver code is copied).
"""
from __future__ import annotations

import ctypes
import struct
import threading
from ctypes import wintypes
from dataclasses import dataclass

GENERIC_READ, GENERIC_WRITE = 0x80000000, 0x40000000
OPEN_EXISTING = 3
FILE_FLAG_OVERLAPPED = 0x40000000
INVALID_HANDLE = wintypes.HANDLE(-1).value
ERROR_IO_PENDING = 997

_CTL = lambda function: (0x1B << 16) | (function << 2)  # FILE_DEVICE_SERIAL_PORT, METHOD_BUFFERED
IOCTL_SERIAL_LSRMST_INSERT = _CTL(31)
IOCTL_SERIAL_GET_MODEMSTATUS = _CTL(26)

SERIAL_LSRMST_ESCAPE = 0x00
SERIAL_LSRMST_LSR_DATA = 0x01
SERIAL_LSRMST_LSR_NODATA = 0x02
SERIAL_LSRMST_MST = 0x03
C0CE_INSERT_RBR = 16
C0CE_INSERT_RLC = 17
C0CE_SIGNATURE = b"c0c\x00"
C0CE_INSERT_IOCTL_GET = 0x01000000
C0CE_INSERT_ENABLE_LSR = 0x00000001
C0CE_INSERT_ENABLE_MST = 0x00000002
C0CE_INSERT_ENABLE_RBR = 0x00000100
C0CE_INSERT_ENABLE_RLC = 0x00000200
C0CE_INSERT_ENABLE_LSR_BI = 0x00000400

MS_CTS_ON, MS_DSR_ON, MS_RING_ON, MS_RLSD_ON = 0x10, 0x20, 0x40, 0x80
_PARITY = {0: "N", 1: "O", 2: "E", 3: "M", 4: "S"}
_STOPBITS = {0: 1, 1: 1.5, 2: 2}


C0C_PIN_ROPEN = 0x80  # com0com include/com0com.h; setupc stores "ri=ropen" as this DWORD


def ri_wired_to_open(endpoint: str) -> bool:
    """Read-only: does this pair end have RI wired to 'remote port open'?"""
    try:
        import winreg
        key_path = rf"SYSTEM\CurrentControlSet\Services\com0com\Parameters\{endpoint}"
        with winreg.OpenKey(winreg.HKEY_LOCAL_MACHINE, key_path) as key:
            value, kind = winreg.QueryValueEx(key, "ri")
    except (OSError, ImportError):
        return False
    if isinstance(value, int):
        return value == C0C_PIN_ROPEN
    return str(value).strip().lower() == "ropen"


@dataclass(frozen=True)
class LineSettings:
    baudrate: int | None = None
    bytesize: int | None = None
    parity: str | None = None
    stopbits: float | None = None


class InsertParser:
    """Split a com0com insertion stream into data and setting events.

    The escape byte is configurable; a literal escape byte in user data is
    encoded by the driver as ``escape, SERIAL_LSRMST_ESCAPE``. Partial records
    are kept across reads.
    """

    def __init__(self, escape: int) -> None:
        if not 1 <= escape <= 255:
            raise ValueError("escape must be a non-zero byte")
        self.escape = escape
        self._pending = bytearray()

    def feed(self, data: bytes) -> list[tuple[str, object]]:
        buffer = self._pending + data
        self._pending = bytearray()
        events: list[tuple[str, object]] = []
        plain = bytearray()
        index = 0
        while index < len(buffer):
            byte = buffer[index]
            if byte != self.escape:
                plain.append(byte)
                index += 1
                continue
            if index + 1 >= len(buffer):
                break
            kind = buffer[index + 1]
            need = {SERIAL_LSRMST_ESCAPE: 2, SERIAL_LSRMST_LSR_DATA: 4, SERIAL_LSRMST_LSR_NODATA: 3,
                    SERIAL_LSRMST_MST: 3, C0CE_INSERT_RBR: 6, C0CE_INSERT_RLC: 5}.get(kind)
            if need is None:
                # Unknown record: keep the escape byte as data rather than drop bytes.
                plain.append(byte)
                index += 1
                continue
            if index + need > len(buffer):
                break
            record = bytes(buffer[index:index + need])
            index += need
            if kind == SERIAL_LSRMST_ESCAPE:
                plain.append(self.escape)
                continue
            if plain:
                events.append(("data", bytes(plain)))
                plain = bytearray()
            if kind == C0CE_INSERT_RBR:
                events.append(("settings", LineSettings(baudrate=struct.unpack("<I", record[2:6])[0])))
            elif kind == C0CE_INSERT_RLC:
                events.append(("settings", LineSettings(bytesize=record[2], parity=_PARITY.get(record[3], "N"),
                                                        stopbits=_STOPBITS.get(record[4], 1))))
            elif kind == SERIAL_LSRMST_MST:
                events.append(("modem", record[2]))
            elif kind == SERIAL_LSRMST_LSR_DATA:
                # LSR with a data byte: a BREAK start delivers its 0x00 byte here;
                # it is a line condition, not application data, so it is dropped.
                events.append(("line", record[2]))
            else:
                events.append(("line", record[2]))
        self._pending = buffer[index:]
        if plain:
            events.append(("data", bytes(plain)))
        return events


class _Overlapped(ctypes.Structure):
    _fields_ = [("Internal", ctypes.c_void_p), ("InternalHigh", ctypes.c_void_p),
                ("Offset", wintypes.DWORD), ("OffsetHigh", wintypes.DWORD), ("hEvent", wintypes.HANDLE)]


_DCB_BINARY = 0x1
_DTR_CONTROL_ENABLE = 0x1
_RTS_CONTROL_ENABLE = 0x1


class _Dcb(ctypes.Structure):
    _fields_ = [("DCBlength", wintypes.DWORD), ("BaudRate", wintypes.DWORD), ("flags", wintypes.DWORD),
                ("wReserved", wintypes.WORD), ("XonLim", wintypes.WORD), ("XoffLim", wintypes.WORD),
                ("ByteSize", ctypes.c_ubyte), ("Parity", ctypes.c_ubyte), ("StopBits", ctypes.c_ubyte),
                ("XonChar", ctypes.c_char), ("XoffChar", ctypes.c_char), ("ErrorChar", ctypes.c_char),
                ("EofChar", ctypes.c_char), ("EvtChar", ctypes.c_char), ("wReserved1", wintypes.WORD)]


class _CommTimeouts(ctypes.Structure):
    _fields_ = [(name, wintypes.DWORD) for name in (
        "ReadIntervalTimeout", "ReadTotalTimeoutMultiplier", "ReadTotalTimeoutConstant",
        "WriteTotalTimeoutMultiplier", "WriteTotalTimeoutConstant")]


class PairEnd:
    """Blocking-with-timeout handle on CNCBn. One reader and one writer thread."""

    ESCAPE = 0xF7

    def __init__(self, name: str, open_wired: bool | None = None) -> None:
        if not name.startswith("CNCB") or not name[4:].isdigit():
            raise ValueError("Only the hidden CNCBn end of a com0com pair can be opened.")
        self.open_wired = ri_wired_to_open(name) if open_wired is None else open_wired
        self._k = ctypes.WinDLL("kernel32", use_last_error=True)
        self._k.CreateFileW.restype = wintypes.HANDLE
        self._k.CreateEventW.restype = wintypes.HANDLE
        handle = self._k.CreateFileW("\\\\.\\" + name, GENERIC_READ | GENERIC_WRITE, 0, None,
                                     OPEN_EXISTING, FILE_FLAG_OVERLAPPED, None)
        if handle in (None, INVALID_HANDLE):
            raise OSError(ctypes.get_last_error(), f"Cannot open {name}")
        self.handle = handle
        self.name = name
        self._closed = threading.Event()
        # A freshly opened port inherits Windows defaults (1200 7E1). com0com masks
        # data to the receiving end's byte size, so 7 bits would silently clear
        # bit 7 of every byte. This end is a pipe: always 8N1, binary, no flow control.
        dcb = _Dcb()
        dcb.DCBlength = ctypes.sizeof(_Dcb)
        if not self._k.GetCommState(handle, ctypes.byref(dcb)):
            self._k.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), "GetCommState failed")
        dcb.BaudRate, dcb.ByteSize, dcb.Parity, dcb.StopBits = 115200, 8, 0, 0
        dcb.flags = _DCB_BINARY | (_DTR_CONTROL_ENABLE << 4) | (_RTS_CONTROL_ENABLE << 12)
        if not self._k.SetCommState(handle, ctypes.byref(dcb)):
            self._k.CloseHandle(handle)
            raise OSError(ctypes.get_last_error(), "SetCommState failed")
        timeouts = _CommTimeouts(0xFFFFFFFF, 0xFFFFFFFF, 100, 0, 0)  # return after <=100 ms
        self._k.SetCommTimeouts(handle, ctypes.byref(timeouts))
        self.parser = InsertParser(self.ESCAPE)
        request = bytes((self.ESCAPE,)) + C0CE_SIGNATURE + struct.pack(
            "<I", C0CE_INSERT_IOCTL_GET | C0CE_INSERT_ENABLE_RBR | C0CE_INSERT_ENABLE_RLC
            | C0CE_INSERT_ENABLE_LSR_BI)
        self.initial_events = self.parser.feed(self._ioctl(IOCTL_SERIAL_LSRMST_INSERT, request, 64))

    def _wait(self, overlapped: _Overlapped, ok: int, transferred: wintypes.DWORD) -> int:
        if not ok:
            error = ctypes.get_last_error()
            if error != ERROR_IO_PENDING:
                raise OSError(error, "serial I/O failed")
            if not self._k.GetOverlappedResult(self.handle, ctypes.byref(overlapped), ctypes.byref(transferred), True):
                raise OSError(ctypes.get_last_error(), "serial I/O failed")
        return transferred.value

    def _ioctl(self, code: int, data: bytes, out_size: int) -> bytes:
        inbuf = ctypes.create_string_buffer(data, len(data))
        outbuf = ctypes.create_string_buffer(max(out_size, 1))
        overlapped = _Overlapped()
        overlapped.hEvent = self._k.CreateEventW(None, True, False, None)
        transferred = wintypes.DWORD()
        try:
            ok = self._k.DeviceIoControl(self.handle, code, inbuf, len(data), outbuf, out_size,
                                         ctypes.byref(transferred), ctypes.byref(overlapped))
            count = self._wait(overlapped, ok, transferred)
        finally:
            self._k.CloseHandle(overlapped.hEvent)
        return outbuf.raw[:count]

    def app_open(self) -> bool | None:
        """Whether the application end is open, if the pair reports it.

        Requires the pair to be configured with ``ri=ropen`` on this end (the
        tool does this when it creates a pair). Returns None when RI is not
        wired to the open state, so callers must not infer closure from DTR:
        flashing tools pulse DTR on purpose to reset the target.
        """
        if not self.open_wired:
            return None
        status = struct.unpack("<I", self._ioctl(IOCTL_SERIAL_GET_MODEMSTATUS, b"", 4).ljust(4, b"\0"))[0]
        return bool(status & MS_RING_ON)

    def modem_lines(self) -> tuple[bool, bool]:
        """Application DTR and RTS, observed as DSR and CTS on this end."""
        status = struct.unpack("<I", self._ioctl(IOCTL_SERIAL_GET_MODEMSTATUS, b"", 4).ljust(4, b"\0"))[0]
        return bool(status & MS_DSR_ON), bool(status & MS_CTS_ON)

    def read(self, size: int = 4096) -> bytes:
        buffer = ctypes.create_string_buffer(size)
        overlapped = _Overlapped()
        overlapped.hEvent = self._k.CreateEventW(None, True, False, None)
        transferred = wintypes.DWORD()
        try:
            ok = self._k.ReadFile(self.handle, buffer, size, ctypes.byref(transferred), ctypes.byref(overlapped))
            count = self._wait(overlapped, ok, transferred)
        finally:
            self._k.CloseHandle(overlapped.hEvent)
        return buffer.raw[:count]

    def write(self, data: bytes) -> int:
        buffer = ctypes.create_string_buffer(bytes(data), len(data))
        overlapped = _Overlapped()
        overlapped.hEvent = self._k.CreateEventW(None, True, False, None)
        transferred = wintypes.DWORD()
        try:
            ok = self._k.WriteFile(self.handle, buffer, len(data), ctypes.byref(transferred), ctypes.byref(overlapped))
            return self._wait(overlapped, ok, transferred)
        finally:
            self._k.CloseHandle(overlapped.hEvent)

    def close(self) -> None:
        if not self._closed.is_set():
            self._closed.set()
            self._k.CancelIoEx(self.handle, None)
            self._k.CloseHandle(self.handle)
