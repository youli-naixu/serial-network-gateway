import asyncio
import json
import random
import socket
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace

import pytest

from serial_gateway import engine
from serial_gateway.engine import GatewayService
from serial_gateway.models import GatewayProfile
from serial_gateway.rfc2217 import escape_data, subnegotiation


def wait_for(service, predicate, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        snapshot = service.snapshot()
        if predicate(snapshot):
            return snapshot
        time.sleep(0.005)
    raise AssertionError(service.snapshot())


def wait_state(service, state, timeout=5):
    return wait_for(service, lambda value: value["state"] == state, timeout)


def connect(service):
    snapshot = service.snapshot()
    connection = socket.create_connection((snapshot["listen_host"], snapshot["listen_port"]), timeout=3)
    connection.settimeout(3)
    return connection


def recv_exact(connection, count):
    data = bytearray()
    while len(data) < count:
        chunk = connection.recv(min(65536, count - len(data)))
        if not chunk:
            raise AssertionError(f"unexpected EOF after {len(data)} of {count} bytes")
        data.extend(chunk)
    return bytes(data)


def assert_closed(connection):
    try:
        assert connection.recv(1) == b""
    except (ConnectionResetError, ConnectionAbortedError):
        pass


def send_fragments(connection, data, seed):
    rng = random.Random(seed)
    offset = 0
    while offset < len(data):
        end = offset + rng.randint(1, 4097)
        connection.sendall(data[offset:end])
        offset = end


@pytest.fixture
def service():
    result = GatewayService()
    yield result
    result.stop()
    assert result._thread is None
    assert not result._tasks
    assert not result._writers


@pytest.fixture
def peer_listener():
    with socket.socket() as listener:
        listener.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
        listener.bind(("127.0.0.1", 0))
        listener.listen(8)
        listener.settimeout(3)
        yield listener


def device_profile(listener, **kwargs):
    return GatewayProfile(mode="device", protocol="raw", listen_port=0,
                          remote_host="127.0.0.1", remote_port=listener.getsockname()[1], **kwargs)


@pytest.mark.parametrize("protocol", ["raw", "rfc2217"])
def test_random_chunks_full_binary_independent_simultaneous_directions(service, peer_listener, protocol):
    profile = replace(device_profile(peer_listener, buffer_bytes=1024, chunk_bytes=137), protocol=protocol)
    service.start(profile)
    wait_state(service, "ready")
    with connect(service) as client:
        remote, _ = peer_listener.accept()
        with remote:
            remote.settimeout(5)
            wait_state(service, "active")
            outbound = bytes(range(256)) + random.Random(125).randbytes(128 * 1024)
            inbound = bytes(reversed(range(256))) + random.Random(731).randbytes(128 * 1024)
            with ThreadPoolExecutor(max_workers=4) as workers:
                send_client = workers.submit(send_fragments, client, outbound, 1)
                send_remote = workers.submit(send_fragments, remote, inbound, 2)
                receive_remote = workers.submit(recv_exact, remote, len(outbound))
                receive_client = workers.submit(recv_exact, client, len(inbound))
                assert receive_remote.result(15) == outbound
                assert receive_client.result(15) == inbound
                send_client.result(15)
                send_remote.result(15)
            snapshot = wait_for(service, lambda value: value["bytes_to_remote"] == len(outbound)
                                and value["bytes_to_client"] == len(inbound))
            assert snapshot["bytes_from_client"] == len(outbound)
            assert snapshot["bytes_from_remote"] == len(inbound)
            assert 0 < snapshot["peak_to_remote"] <= profile.buffer_bytes
            assert 0 < snapshot["peak_to_client"] <= profile.buffer_bytes
            if protocol == "raw":
                assert snapshot["payload_from_client"] == len(outbound)
                assert snapshot["payload_from_remote"] == len(inbound)
    wait_state(service, "needs_reset")


def test_rfc2217_wire_is_unmodified_and_observation_is_separate(service, peer_listener):
    service.start(replace(device_profile(peer_listener), protocol="rfc2217"))
    wait_state(service, "ready")
    with connect(service) as client:
        remote, _ = peer_listener.accept()
        with remote:
            remote.settimeout(3)
            request = subnegotiation(1, (1500000).to_bytes(4, "big")) + escape_data(bytes(range(256)))
            response = subnegotiation(101, (9600).to_bytes(4, "big")) + escape_data(b"\xffresponse")
            send_fragments(client, request, 4)
            assert recv_exact(remote, len(request)) == request
            send_fragments(remote, response, 11)
            assert recv_exact(client, len(response)) == response
            snapshot = service.snapshot()
            assert snapshot["serial_requested"]["baudrate"] == 1500000
            assert snapshot["serial_confirmed"]["baudrate"] == 9600
            assert snapshot["payload_from_client"] == 256
            assert snapshot["payload_from_remote"] == 9


def test_second_client_is_rejected_and_snapshot_is_detached_bounded(service, peer_listener):
    service.start(device_profile(peer_listener))
    wait_state(service, "ready")
    with connect(service) as first:
        remote, _ = peer_listener.accept()
        with remote:
            remote.settimeout(3)
            wait_state(service, "active")
            for _ in range(105):
                with connect(service) as second:
                    assert_closed(second)
            snapshot = service.snapshot()
            assert snapshot["rejected_connections"] == 105
            assert snapshot["connections"] == 1
            assert len(snapshot["events"]) == 100
            json.dumps(snapshot)
            snapshot["events"].clear()
            snapshot["serial_confirmed"]["injected"] = 1
            assert len(service.snapshot()["events"]) == 100
            assert service.snapshot()["serial_confirmed"] == {}
            first.sendall(b"still-active")
            assert recv_exact(remote, 12) == b"still-active"


def test_remote_disconnect_locks_and_rearm_does_not_replay_queued_bytes(service, peer_listener):
    service.start(device_profile(peer_listener, buffer_bytes=512, chunk_bytes=128))
    wait_state(service, "ready")
    first = connect(service)
    remote, _ = peer_listener.accept()
    finished = threading.Event()

    def flood():
        try:
            first.sendall(b"old-session-data" * (1024 * 1024))
        except OSError:
            pass
        finally:
            finished.set()

    flood_thread = threading.Thread(target=flood)
    flood_thread.start()
    try:
        wait_for(service, lambda value: value["queue_to_remote"] > 0)
        remote.close()
        snapshot = wait_state(service, "needs_reset")
        assert snapshot["queue_to_remote"] == snapshot["queue_to_client"] == 0
        with connect(service) as rejected:
            assert_closed(rejected)
        assert service.snapshot()["connections"] == 1
        assert not service.snapshot()["state"] == "ready"
        assert service.rearm() is True
        assert service.rearm() is False
        with connect(service) as second:
            new_remote, _ = peer_listener.accept()
            with new_remote:
                new_remote.settimeout(0.1)
                with pytest.raises(socket.timeout):
                    new_remote.recv(1)
                marker = b"new-session-only"
                second.sendall(marker)
                assert recv_exact(new_remote, len(marker)) == marker
                new_remote.sendall(b"fresh")
                assert recv_exact(second, 5) == b"fresh"
                assert service.snapshot()["session_id"] == 2
    finally:
        remote.close()
        first.close()
        flood_thread.join(3)
        assert finished.is_set()
    snapshot = wait_state(service, "needs_reset")
    assert snapshot["connections"] == snapshot["disconnects"] == 2
    assert snapshot["peak_to_remote"] <= 512


def test_idle_is_not_a_stall_and_single_byte_sends_immediately(service, peer_listener):
    service.start(device_profile(peer_listener, stall_timeout_s=0.1))
    wait_state(service, "ready")
    with connect(service) as client:
        remote, _ = peer_listener.accept()
        with remote:
            remote.settimeout(0.5)
            wait_state(service, "active")
            time.sleep(0.35)
            assert service.snapshot()["state"] == "active"
            client.sendall(b"x")
            assert recv_exact(remote, 1) == b"x"
            remote.sendall(b"y")
            assert recv_exact(client, 1) == b"y"
            assert service.snapshot()["last_error"] == ""


def test_pending_output_stall_is_fatal(service, peer_listener):
    service.start(device_profile(peer_listener, buffer_bytes=512, chunk_bytes=128, stall_timeout_s=0.15))
    wait_state(service, "ready")
    client = connect(service)
    remote, _ = peer_listener.accept()
    errors = []

    def flood():
        try:
            client.sendall(b"blocked" * (2 * 1024 * 1024))
        except OSError as exc:
            errors.append(type(exc).__name__)

    worker = threading.Thread(target=flood)
    worker.start()
    try:
        snapshot = wait_state(service, "needs_reset", 8)
        assert "无发送进展" in snapshot["last_error"]
        assert snapshot["queue_to_remote"] == 0
    finally:
        remote.close()
        client.close()
        worker.join(3)
        assert not worker.is_alive()


@pytest.mark.parametrize("reason", ["eof", "timeout", "stop"])
def test_cancel_connect_and_queued_client_input(service, monkeypatch, peer_listener, reason):
    entered, cancelled = threading.Event(), threading.Event()

    async def slow_connect(*args, **kwargs):
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    monkeypatch.setattr(engine, "open_tcp_connection", slow_connect)
    timeout = 0.15 if reason == "timeout" else 5
    service.start(device_profile(peer_listener, connect_timeout_s=timeout, buffer_bytes=512, chunk_bytes=128))
    wait_state(service, "ready")
    client = connect(service)
    try:
        client.sendall(b"pending")
        assert entered.wait(2)
        if reason == "eof":
            client.close()
        elif reason == "stop":
            service.stop()
        snapshot = wait_state(service, "stopped" if reason == "stop" else "needs_reset")
        assert cancelled.wait(1)
        assert snapshot["queue_to_remote"] == 0
        if reason == "timeout":
            assert "连接建立超时" in snapshot["last_error"]
    finally:
        client.close()


def test_connect_failure_requires_rearm(service):
    with socket.socket() as unavailable:
        unavailable.bind(("127.0.0.1", 0))
        port = unavailable.getsockname()[1]
        service.start(GatewayProfile(mode="device", protocol="raw", listen_port=0,
                                     remote_host="127.0.0.1", remote_port=port))
        wait_state(service, "ready")
        with connect(service) as client:
            assert_closed(client)
        snapshot = wait_state(service, "needs_reset")
        assert snapshot["last_error"]
        assert snapshot["connections"] == snapshot["disconnects"] == 1
        assert service.rearm()


def test_stop_restart_many_rounds_reclaims_tasks_sockets_and_threads(service):
    original_threads = {thread.ident for thread in threading.enumerate()}
    for round_number in range(8):
        service.start(GatewayProfile(protocol="raw", listen_port=0))
        ready = wait_state(service, "ready")
        loop, thread = service._loop, service._thread
        with connect(service) as client:
            marker = f"round-{round_number}".encode()
            client.sendall(marker)
            assert recv_exact(client, len(marker)) == marker
            if round_number % 2:
                service.stop()
                assert_closed(client)
        service.stop()
        assert not thread.is_alive()
        assert loop.is_closed()
        assert not service._tasks and not service._writers
        assert service.snapshot()["connections"] == 1
        assert service.snapshot()["session_id"] == 1
        for port in (ready["listen_port"], ready["remote_port"]):
            with pytest.raises(OSError):
                socket.create_connection(("127.0.0.1", port), timeout=0.2)
    assert {thread.ident for thread in threading.enumerate()} <= original_threads


@pytest.mark.parametrize("remote_alias", ["localhost", "127.0.0.1", "::1", "::ffff:127.0.0.1"])
def test_invalid_config_listener_conflict_and_recursive_remote(service, peer_listener, remote_alias):
    with pytest.raises(ValueError):
        service.start(GatewayProfile(buffer_bytes=10))
    assert service.snapshot()["state"] == "error"
    service.start(GatewayProfile(listen_port=peer_listener.getsockname()[1]))
    wait_state(service, "error")
    service.stop()
    with socket.socket() as temporary:
        temporary.bind(("127.0.0.1", 0))
        free_port = temporary.getsockname()[1]
    service.start(GatewayProfile(mode="device", listen_port=free_port, remote_host=remote_alias, remote_port=free_port))
    snapshot = wait_state(service, "error")
    assert "递归" in snapshot["last_error"]
    assert snapshot["connections"] == 0
    service.stop()
    service.start(GatewayProfile(protocol="raw", listen_port=0))
    wait_state(service, "ready")
    with pytest.raises(RuntimeError):
        service.start(GatewayProfile(listen_port=0))
    assert service.rearm() is False


def test_stop_timeout_raises_and_later_stop_reaps_worker(service, monkeypatch):
    gate = threading.Event()
    real_worker = service._thread_main

    def delayed_worker(profile):
        gate.wait(2)
        real_worker(profile)

    monkeypatch.setattr(service, "_thread_main", delayed_worker)
    service.start(GatewayProfile(listen_port=0))
    try:
        with pytest.raises(TimeoutError):
            service.stop(timeout=0.01)
        with pytest.raises(RuntimeError):
            service.start(GatewayProfile(listen_port=0))
    finally:
        gate.set()
    service.stop()
    assert service.snapshot()["state"] == "stopped"


def test_repeated_stop_does_not_cancel_in_progress_cleanup(service, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    real_close = engine.Simulator.close

    async def slow_close(simulator):
        entered.set()
        while not release.is_set():
            await asyncio.sleep(0.01)
        await real_close(simulator)

    monkeypatch.setattr(engine.Simulator, "close", slow_close)
    service.start(GatewayProfile(protocol="raw", listen_port=0))
    ready = wait_state(service, "ready")
    try:
        with pytest.raises(TimeoutError):
            service.stop(timeout=0.05)
        assert entered.wait(1)
        with pytest.raises(TimeoutError):
            service.stop(timeout=0.05)
        assert service._thread.is_alive()
    finally:
        release.set()
        service.stop()
    with pytest.raises(OSError):
        socket.create_connection(("127.0.0.1", ready["remote_port"]), timeout=0.2)


def test_device_ready_is_lazy_and_rearm_is_thread_safe(service, peer_listener):
    service.start(device_profile(peer_listener))
    wait_state(service, "ready")
    peer_listener.settimeout(0.1)
    with pytest.raises(socket.timeout):
        peer_listener.accept()
    peer_listener.settimeout(3)
    with connect(service):
        remote, _ = peer_listener.accept()
        remote.close()
    wait_state(service, "needs_reset")
    with ThreadPoolExecutor(max_workers=8) as pool:
        results = list(pool.map(lambda _: service.rearm(), range(16)))
    assert results.count(True) == 1
    assert service.snapshot()["state"] == "ready"


def test_ipv6_loopback_simulator(service):
    try:
        with socket.socket(socket.AF_INET6) as probe:
            probe.bind(("::1", 0))
    except OSError:
        pytest.skip("IPv6 loopback is unavailable")
    service.start(GatewayProfile(protocol="raw", listen_host="::1", listen_port=0))
    wait_state(service, "ready")
    with connect(service) as client:
        client.sendall(b"ipv6\x00\xff")
        assert recv_exact(client, 6) == b"ipv6\x00\xff"
    wait_state(service, "needs_reset")


def test_cancel_during_listener_start_closes_bound_socket(service, monkeypatch):
    entered = threading.Event()
    addresses = []
    real_start = asyncio.Server.start_serving

    async def pause_after_bind(server):
        await real_start(server)
        addresses.append(server.sockets[0].getsockname()[:2])
        entered.set()
        await asyncio.Future()

    monkeypatch.setattr(asyncio.Server, "start_serving", pause_after_bind)
    service.start(GatewayProfile(protocol="raw", listen_port=0))
    assert entered.wait(2)
    service.stop()
    with pytest.raises(OSError):
        socket.create_connection(addresses[0], timeout=0.2)


def test_stop_immediately_after_start_is_repeatable(service):
    for _ in range(20):
        service.start(GatewayProfile(protocol="raw", listen_port=0))
        service.stop()
        assert service.snapshot()["state"] == "stopped"
        assert not service._tasks and not service._writers


def test_pyserial_control_changes_through_transparent_gateway(service):
    serial = pytest.importorskip("serial")
    service.start(GatewayProfile(listen_port=0))
    ready = wait_state(service, "ready")
    with serial.serial_for_url(f"rfc2217://127.0.0.1:{ready['listen_port']}?timeout=2", timeout=3,
                               baudrate=1500000, bytesize=7, parity="E", stopbits=2) as client:
        client.dtr = False
        client.rts = False
        client.break_condition = True
        snapshot = wait_for(service, lambda value: value["serial_confirmed"].get("break") is True)
        for key, expected in {"baudrate": 1500000, "bytesize": 7, "parity": "E", "stopbits": 2,
                              "dtr": False, "rts": False, "break": True}.items():
            assert snapshot["serial_requested"][key] == expected
            assert snapshot["serial_confirmed"][key] == expected
        client.break_condition = False
        client.bytesize, client.parity, client.stopbits = 8, "N", 1
        payload = bytes(range(256)) * 256
        client.write(payload)
        assert client.read(len(payload)) == payload
    wait_state(service, "needs_reset")
    assert service.rearm() is True
    snapshot = service.snapshot()
    assert snapshot["serial_requested"] == snapshot["serial_confirmed"] == snapshot["telnet_negotiation"] == {}
