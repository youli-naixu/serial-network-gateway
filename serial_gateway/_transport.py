"""Private bounded asyncio stream plumbing shared by the gateway and simulator."""
from __future__ import annotations

import asyncio
import ipaddress
import os
import socket


class SendStalled(TimeoutError):
    pass


class SelfConnectionError(ValueError):
    pass


class EndAwareReader(asyncio.StreamReader):
    def __init__(self, buffer_bytes: int) -> None:
        # StreamReader pauses above twice its limit. One receive completion may
        # overshoot that watermark (bounded by asyncio's OS receive chunk).
        super().__init__(limit=max(1, buffer_bytes // 2))
        self.ended = asyncio.get_running_loop().create_future()


class _Protocol(asyncio.StreamReaderProtocol):
    def __init__(self, reader, callback, buffer_bytes, nodelay, keepalive_s):
        super().__init__(reader, callback)
        self.reader = reader
        self.transport = None
        self.settings = buffer_bytes, nodelay, keepalive_s

    def connection_made(self, transport):
        self.transport = transport
        buffer_bytes, nodelay, keepalive_s = self.settings
        # Drain after every chunk, including when less than one buffer is ready.
        # A transport holds at most one writer's chunk, not a second large FIFO.
        transport.set_write_buffer_limits(high=0, low=0)
        sock = transport.get_extra_info("socket")
        try:
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, int(nodelay))
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_KEEPALIVE, 1)
            for option in (socket.SO_SNDBUF, socket.SO_RCVBUF):
                sock.setsockopt(socket.SOL_SOCKET, option, buffer_bytes)
            for name in ("TCP_KEEPIDLE", "TCP_KEEPINTVL", "TCP_KEEPALIVE"):
                option = getattr(socket, name, None)
                if option is not None:
                    try:
                        sock.setsockopt(socket.IPPROTO_TCP, option, keepalive_s)
                    except OSError:
                        pass
            count = getattr(socket, "TCP_KEEPCNT", None)
            if count is not None:
                try:
                    sock.setsockopt(socket.IPPROTO_TCP, count, 3)
                except OSError:
                    pass
            if os.name == "nt" and hasattr(socket, "SIO_KEEPALIVE_VALS"):
                try:
                    with sock.dup() as tuning_socket:
                        tuning_socket.ioctl(socket.SIO_KEEPALIVE_VALS,
                                            (1, keepalive_s * 1000, keepalive_s * 1000))
                except OSError:
                    pass
        except OSError as exc:
            # Still attach the stream protocol, so its close waiter is usable.
            super().connection_made(transport)
            if not self.reader.ended.done():
                self.reader.ended.set_result(exc)
            transport.abort()
            return
        super().connection_made(transport)

    def eof_received(self):
        if not self.reader.ended.done():
            self.reader.ended.set_result(None)
        return super().eof_received()

    def connection_lost(self, exc):
        if not self.reader.ended.done():
            self.reader.ended.set_result(exc)
        super().connection_lost(exc)


async def start_tcp_server(callback, host, port, *, buffer_bytes=4096, nodelay=True, keepalive_s=10):
    loop = asyncio.get_running_loop()

    def factory():
        reader = EndAwareReader(buffer_bytes)
        return _Protocol(reader, callback, buffer_bytes, nodelay, keepalive_s)

    # Own the Server before its first serving await. create_server's default
    # start_serving=True yields after binding, which otherwise leaves a listener
    # unreachable by our caller if cancellation lands in that yield.
    server = await loop.create_server(factory, host, port, backlog=8, start_serving=False)
    try:
        await server.start_serving()
        return server
    except BaseException:
        server.close()
        # Accepted writers, if any, belong to callback's owner and its cleanup.
        raise


def _is_self(address, port, forbidden):
    if not forbidden or port != forbidden[1]:
        return False
    remote = ipaddress.ip_address(address.split("%")[0])
    local = ipaddress.ip_address(forbidden[0].split("%")[0])
    if isinstance(remote, ipaddress.IPv6Address) and remote.ipv4_mapped:
        remote = remote.ipv4_mapped
    if isinstance(local, ipaddress.IPv6Address) and local.ipv4_mapped:
        local = local.ipv4_mapped
    return remote == local or remote.is_unspecified or (remote.is_loopback and local.is_loopback)


async def resolve_endpoint(host, port, forbidden=None):
    loop = asyncio.get_running_loop()
    addresses = await loop.getaddrinfo(host, port, type=socket.SOCK_STREAM)
    if any(_is_self(item[4][0], item[4][1], forbidden) for item in addresses):
        raise SelfConnectionError("远端地址回连本机代理入口，已拒绝递归连接。")
    return addresses


async def open_tcp_connection(host, port, *, buffer_bytes=4096, nodelay=True,
                              keepalive_s=10, forbidden=None):
    loop = asyncio.get_running_loop()
    addresses = await resolve_endpoint(host, port, forbidden)
    last_error = None
    for family, socktype, proto, _, address in addresses:
        reader = EndAwareReader(buffer_bytes)
        protocol = _Protocol(reader, None, buffer_bytes, nodelay, keepalive_s)
        try:
            numeric_host = address[0]
            if family == socket.AF_INET6 and len(address) > 3 and address[3]:
                numeric_host = f"{numeric_host}%{address[3]}"
            transport, _ = await loop.create_connection(
                lambda: protocol, numeric_host, address[1], family=family,
                proto=proto, flags=socket.AI_NUMERICHOST)
            writer = asyncio.StreamWriter(transport, protocol, reader, loop)
            return reader, writer
        except BaseException as exc:
            if protocol.transport is not None:
                protocol.transport.abort()
            if not isinstance(exc, OSError):
                raise
            last_error = exc
    if last_error is not None:
        raise last_error
    raise OSError("没有可连接的远端地址。")


async def drain_with_progress(writer, timeout):
    """Only pending output is timed; partial transport progress resets expiry."""
    loop = asyncio.get_running_loop()
    pending = writer.transport.get_write_buffer_size()
    deadline = loop.time() + timeout
    while True:
        if writer.is_closing():
            raise ConnectionError("连接已关闭。")
        try:
            async with asyncio.timeout(min(0.1, max(0.001, deadline - loop.time()))):
                await writer.drain()
            return
        except TimeoutError:
            remaining = writer.transport.get_write_buffer_size()
            if remaining < pending:
                deadline = loop.time() + timeout
            pending = remaining
            if loop.time() >= deadline:
                raise SendStalled("有待发送数据且发送进度停滞。") from None


async def abort_writer(writer):
    # Abort deliberately discards unsent bytes; close() alone could flush stale
    # output after the session has ended.
    writer.transport.abort()
    try:
        async with asyncio.timeout(0.5):
            await writer.wait_closed()
    except (OSError, ConnectionError, TimeoutError):
        pass
