"""Thread-safe, standard-library TCP gateway with fail-closed sessions."""
from __future__ import annotations

import asyncio
import copy
import math
import threading
from collections import deque
from datetime import datetime, timezone

from ._transport import (SelfConnectionError, SendStalled, abort_writer,
                         drain_with_progress, open_tcp_connection,
                         resolve_endpoint, start_tcp_server)
from .models import GatewayProfile
from .rfc2217 import RFC2217Observer
from .simulator import Simulator


class _ByteQueue:
    def __init__(self, capacity, changed):
        self.capacity = capacity
        self.size = 0
        self.items = deque()
        self.condition = asyncio.Condition()
        self.changed = changed

    async def put(self, data):
        if len(data) > self.capacity:
            raise ValueError("读块超过队列容量。")
        async with self.condition:
            await self.condition.wait_for(lambda: self.size + len(data) <= self.capacity)
            self.items.append(data)
            self.size += len(data)
            self.changed(self.size)
            self.condition.notify_all()

    async def get(self):
        async with self.condition:
            await self.condition.wait_for(lambda: bool(self.items))
            data = self.items.popleft()
            self.size -= len(data)
            self.changed(self.size)
            self.condition.notify_all()
            return data

    def clear(self):
        self.items.clear()
        self.size = 0
        self.changed(0)


def _initial(profile=None):
    return {
        "state": "stopped", "message": "代理已停止。",
        "listen_host": profile.listen_host if profile else "", "listen_port": profile.listen_port if profile else 0,
        "remote_host": profile.remote_host if profile else "", "remote_port": profile.remote_port if profile else 0,
        "mode": profile.mode if profile else "", "protocol": profile.protocol if profile else "",
        "session_id": 0, "connections": 0, "disconnects": 0, "rejected_connections": 0,
        "bytes_from_client": 0, "bytes_to_remote": 0, "bytes_from_remote": 0, "bytes_to_client": 0,
        "payload_from_client": 0, "payload_from_remote": 0,
        "queue_to_remote": 0, "queue_to_client": 0, "peak_to_remote": 0, "peak_to_client": 0,
        "serial_requested": {}, "serial_confirmed": {}, "telnet_negotiation": {},
        "serial_source": "simulated_rfc2217_response" if profile and profile.mode == "simulator" else "rfc2217_remote_response",
        "last_error": "", "events": [],
    }


def _error_text(exc):
    # Do not interpolate arbitrary exception messages: they can contain data.
    if isinstance(exc, SelfConnectionError):
        return "远端回连本机代理入口，已拒绝递归连接。"
    if isinstance(exc, SendStalled):
        return "有待发送数据且无发送进展，连接已终止。"
    if isinstance(exc, TimeoutError):
        return "远端连接建立超时。"
    if isinstance(exc, OSError):
        code = getattr(exc, "winerror", None) or exc.errno
        return f"网络错误（{type(exc).__name__}，错误码 {code}）。"
    return f"代理错误（{type(exc).__name__}）。"


class GatewayService:
    """Synchronous facade. start() schedules startup; inspect snapshot for ready.

    An accepted session owns both TCP connections. EOF never performs a half
    close or drains queued data into a new session. rearm() only succeeds after
    all resources belonging to the previous session have been closed.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._lifecycle = threading.RLock()
        self._snapshot = _initial()
        self._thread = None
        self._loop = None
        self._main_task = None
        self._stop_requested = threading.Event()
        self._server = None
        self._simulator = None
        self._tasks: set[asyncio.Task] = set()
        self._writers: set[asyncio.StreamWriter] = set()
        self._busy = False
        self._locked = False
        self._stopping = False
        self._profile = None

    def snapshot(self) -> dict:
        with self._lock:
            return copy.deepcopy(self._snapshot)

    def _event(self, level, message):
        with self._lock:
            self._snapshot["events"].append({"time": datetime.now(timezone.utc).isoformat(timespec="milliseconds"),
                                              "level": level, "message": message})
            del self._snapshot["events"][:-100]

    def _state(self, state, message, error=""):
        with self._lock:
            self._snapshot.update(state=state, message=message)
            if error:
                self._snapshot["last_error"] = error
            self._event("error" if error else "info", message)

    def start(self, profile: GatewayProfile) -> None:
        with self._lifecycle:
            if self._thread is not None and self._thread.is_alive():
                raise RuntimeError("代理已启动；请先停止后再启动。")
            try:
                if not isinstance(profile, GatewayProfile):
                    raise TypeError("start 需要 GatewayProfile。")
                profile.validate()
            except (TypeError, ValueError):
                with self._lock:
                    self._snapshot = _initial()
                self._state("error", "配置无效，代理未启动。", "配置校验失败。")
                raise
            with self._lock:
                self._snapshot = _initial(profile)
                self._profile = profile
                self._stopping = self._busy = self._locked = False
                self._server = self._simulator = None
                self._tasks = set()
                self._writers = set()
                self._stop_requested.clear()
                self._state("starting", "正在启动本机代理。")
            self._thread = threading.Thread(target=self._thread_main, args=(profile,),
                                            name="SerialGateway-eventloop", daemon=True)
            try:
                self._thread.start()
            except BaseException:
                self._thread = None
                self._state("error", "无法启动代理线程。", "无法启动代理线程。")
                raise

    def stop(self, timeout: float = 3.0) -> None:
        if isinstance(timeout, bool) or not isinstance(timeout, (int, float)) or not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("停止超时必须是有限正数。")
        with self._lifecycle:
            thread = self._thread
            if thread is threading.current_thread():
                raise RuntimeError("不能从代理线程同步停止自身。")
            with self._lock:
                self._stopping = True
                already_requested = self._stop_requested.is_set()
                self._stop_requested.set()
                loop, main = self._loop, self._main_task
            # A repeated stop waits for the same cleanup. Cancelling its finally
            # block again could interrupt socket/simulator resource reclamation.
            if not already_requested and loop is not None and main is not None:
                try:
                    loop.call_soon_threadsafe(main.cancel)
                except RuntimeError:
                    pass  # The worker may already have closed its loop.
            if thread is not None:
                thread.join(timeout)
                if thread.is_alive():
                    self._state("error", "代理线程未在指定时间内停止。", "停止超时；后台清理尚未结束。")
                    raise TimeoutError("代理线程未在指定时间内停止；可再次调用 stop 等待回收。")
            self._thread = None
            self._state("stopped", "代理已停止。")

    def rearm(self) -> bool:
        with self._lock:
            if self._snapshot["state"] != "needs_reset" or self._busy or self._stopping or self._server is None:
                return False
            self._locked = False
            self._snapshot.update(last_error="", serial_requested={}, serial_confirmed={}, telnet_negotiation={})
            self._state("ready", "已重新待命，等待新的本机客户端。")
            return True

    def _thread_main(self, profile):
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        # Report metadata only, never the context's exception/data repr.
        loop.set_exception_handler(lambda _loop, context: self._event("error", "后台网络回调发生异常。"))
        main = loop.create_task(self._run(profile), name="gateway-service")
        with self._lock:
            self._loop, self._main_task = loop, main
            if self._stop_requested.is_set():
                main.cancel()
        try:
            loop.run_until_complete(main)
        except asyncio.CancelledError:
            pass
        except BaseException as exc:
            error = _error_text(exc)
            self._state("error", error, error)
        finally:
            pending = asyncio.all_tasks(loop)
            for task in pending:
                task.cancel()
            if pending:
                loop.run_until_complete(asyncio.gather(*pending, return_exceptions=True))
            loop.run_until_complete(loop.shutdown_asyncgens())
            loop.run_until_complete(loop.shutdown_default_executor())
            loop.close()
            asyncio.set_event_loop(None)
            with self._lock:
                self._loop = self._main_task = None
                self._server = self._simulator = None
                self._busy = False
                if self._stop_requested.is_set():
                    self._state("stopped", "代理已停止。")

    async def _run(self, profile):
        try:
            if profile.mode == "simulator":
                self._simulator = Simulator(profile.protocol, host=profile.listen_host,
                                            buffer_bytes=profile.buffer_bytes, chunk_bytes=profile.chunk_bytes,
                                            stall_timeout_s=profile.stall_timeout_s)
                await self._simulator.start()
                remote_host, remote_port = self._simulator.bound_host, self._simulator.bound_port
            else:
                remote_host, remote_port = profile.remote_host, profile.remote_port
            # Do not admit any clients until both the endpoint checks and the
            # complete startup have finished.
            self._server = await start_tcp_server(self._accept, profile.listen_host, profile.listen_port,
                                                   buffer_bytes=profile.buffer_bytes, nodelay=profile.nodelay,
                                                   keepalive_s=profile.keepalive_s)
            address = self._server.sockets[0].getsockname()
            with self._lock:
                self._snapshot.update(listen_host=address[0], listen_port=address[1],
                                      remote_host=remote_host, remote_port=remote_port)
            if remote_port == address[1]:
                async with asyncio.timeout(profile.connect_timeout_s):
                    await resolve_endpoint(remote_host, remote_port, (address[0], address[1]))
            self._state("ready", "模拟端已启动，等待本机客户端。" if profile.mode == "simulator" else "等待本机客户端连接。")
            await asyncio.Future()
        finally:
            with self._lock:
                self._stopping = True
            if self._server is not None:
                self._server.close()
            writers = list(self._writers)
            for writer in writers:
                writer.transport.abort()
            tasks = list(self._tasks)
            for task in tasks:
                task.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            with self._lock:
                if self._busy:
                    # A newly accepted session may be cancelled before its
                    # coroutine gets its first event-loop turn.
                    self._busy = False
                    self._snapshot["disconnects"] += 1
            if writers:
                await asyncio.gather(*(abort_writer(writer) for writer in writers))
            self._tasks.clear()
            self._writers.clear()
            if self._server is not None:
                await self._server.wait_closed()
            if self._simulator is not None:
                await self._simulator.close()

    def _track(self, coroutine, name):
        task = asyncio.create_task(coroutine, name=name)
        self._tasks.add(task)
        task.add_done_callback(self._tasks.discard)
        return task

    def _accept(self, reader, writer):
        self._writers.add(writer)
        with self._lock:
            if self._stopping or self._locked or self._busy or self._snapshot["state"] != "ready":
                self._snapshot["rejected_connections"] += 1
                self._event("warning", "已拒绝额外连接；仅允许单一客户端且断线后需手动重新待命。")
                writer.transport.abort()
                task = self._track(abort_writer(writer), "gateway-reject")
                task.add_done_callback(lambda _: self._writers.discard(writer))
                return
            self._busy = True
            self._snapshot["connections"] += 1
            self._snapshot["session_id"] += 1
            self._snapshot.update(serial_requested={}, serial_confirmed={}, telnet_negotiation={})
            self._state("connecting", "正在连接远端。")
        self._track(self._session(reader, writer), "gateway-session")

    def _queue_changed(self, direction, size):
        with self._lock:
            self._snapshot[f"queue_to_{direction}"] = size
            self._snapshot[f"peak_to_{direction}"] = max(self._snapshot[f"peak_to_{direction}"], size)

    async def _read(self, reader, queue, direction, observer):
        profile = self._profile
        while True:
            data = await reader.read(profile.chunk_bytes)
            if not data:
                return
            payload = observer.feed(data, direction) if observer is not None else len(data)
            with self._lock:
                self._snapshot[f"bytes_from_{direction}"] += len(data)
                self._snapshot[f"payload_from_{direction}"] += payload
                if observer is not None:
                    self._snapshot["serial_requested"] = dict(observer.serial_requested)
                    self._snapshot["serial_confirmed"] = dict(observer.serial_confirmed)
                    self._snapshot["telnet_negotiation"] = copy.deepcopy(observer.negotiation)
            await queue.put(data)

    async def _write(self, queue, writer, direction):
        while True:
            data = await queue.get()
            if writer.is_closing():
                raise ConnectionError("连接已关闭。")
            writer.write(data)
            await drain_with_progress(writer, self._profile.stall_timeout_s)
            with self._lock:
                self._snapshot[f"bytes_to_{direction}"] += len(data)

    async def _connect(self):
        profile = self._profile
        with self._lock:
            host, port = self._snapshot["remote_host"], self._snapshot["remote_port"]
            forbidden = self._snapshot["listen_host"], self._snapshot["listen_port"]
        async with asyncio.timeout(profile.connect_timeout_s):
            result = await open_tcp_connection(host, port, buffer_bytes=profile.buffer_bytes,
                                               nodelay=profile.nodelay, keepalive_s=profile.keepalive_s,
                                               forbidden=forbidden)
        self._writers.add(result[1])
        return result

    async def _session(self, client_reader, client_writer):
        profile = self._profile
        to_remote = _ByteQueue(profile.buffer_bytes, lambda size: self._queue_changed("remote", size))
        to_client = _ByteQueue(profile.buffer_bytes, lambda size: self._queue_changed("client", size))
        observer = RFC2217Observer() if profile.protocol == "rfc2217" else None
        pumps = []
        connect = None
        remote_writer = None
        failure = ""
        try:
            # Read immediately, so EOF or client failure also cancels DNS/connect.
            client_pump = asyncio.create_task(self._read(client_reader, to_remote, "client", observer), name="gateway-read-client")
            pumps.append(client_pump)
            connect = asyncio.create_task(self._connect(), name="gateway-connect")
            done, _ = await asyncio.wait((connect, client_reader.ended, client_pump), return_when=asyncio.FIRST_COMPLETED)
            if client_reader.ended in done or client_pump in done:
                self._raise_failure(done, [client_pump], [client_reader.ended])
                return
            remote_reader, remote_writer = connect.result()
            self._state("active", "透明转发中（模拟串口）。" if profile.mode == "simulator" else "透明转发中。")
            pumps.extend([
                asyncio.create_task(self._write(to_remote, remote_writer, "remote"), name="gateway-write-remote"),
                asyncio.create_task(self._read(remote_reader, to_client, "remote", observer), name="gateway-read-remote"),
                asyncio.create_task(self._write(to_client, client_writer, "client"), name="gateway-write-client"),
            ])
            endings = [client_reader.ended, remote_reader.ended]
            done, _ = await asyncio.wait([*pumps, *endings], return_when=asyncio.FIRST_COMPLETED)
            self._raise_failure(done, pumps, endings)
        except asyncio.CancelledError:
            raise
        except Exception as exc:
            failure = _error_text(exc)
        finally:
            with self._lock:
                self._locked = True
            for task in pumps:
                task.cancel()
            if connect is not None:
                connect.cancel()
            # Abort both endpoints before waiting for task cleanup: never flush
            # abandoned output, even when the other peer remains writable.
            client_writer.transport.abort()
            if remote_writer is not None:
                remote_writer.transport.abort()
            if connect is not None:
                await asyncio.gather(connect, return_exceptions=True)
                if not connect.cancelled() and connect.exception() is None:
                    _, remote_writer = connect.result()
                    remote_writer.transport.abort()
            if pumps:
                await asyncio.gather(*pumps, return_exceptions=True)
            to_remote.clear()
            to_client.clear()
            writers = [client_writer] + ([remote_writer] if remote_writer is not None else [])
            await asyncio.gather(*(abort_writer(writer) for writer in writers))
            for writer in writers:
                self._writers.discard(writer)
            with self._lock:
                self._busy = False
                self._snapshot["disconnects"] += 1
                if not self._stopping:
                    message = (failure + " 请手动重新待命。") if failure else "会话已结束，请手动重新待命。"
                    self._state("needs_reset", message, failure)

    @staticmethod
    def _raise_failure(done, pumps, endings):
        for task in pumps:
            if task in done and not task.cancelled():
                task.result()
        for ending in endings:
            if ending in done:
                error = ending.result()
                if error is not None:
                    raise error
