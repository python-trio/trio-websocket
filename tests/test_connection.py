import functools

import pytest
import trio.hazmat
import trio.ssl
import trio.testing
import trustme
from async_generator import async_generator, yield_
from trio_websocket import *


HOST = '127.0.0.1'
RESOURCE = '/resource'

@pytest.fixture
@async_generator
async def echo_server(nursery):
    ''' A server that reads one message, sends back the same message,
    then closes the connection. '''
    serve_fn = functools.partial(serve_websocket, echo_handler, HOST, 0,
        ssl_context=None)
    server = await nursery.start(serve_fn)
    await yield_(server)


@pytest.fixture
@async_generator
async def echo_conn(echo_server):
    ''' Return a client connection instance that is connected to an echo
    server. '''
    async with open_websocket(HOST, echo_server.port, RESOURCE, use_ssl=False) \
            as conn:
        await yield_(conn)


async def echo_handler(conn):
    ''' A connection handler that reads one message, sends back the same
    message, then exits. '''
    try:
        msg = await conn.get_message()
        await conn.send_message(msg)
    except ConnectionClosed:
        pass


@attr.s(hash=False, cmp=False)
class MemoryListener(trio.abc.Listener):
    ''' This class is copied from trio's own test suite. '''
    closed = attr.ib(default=False)
    accepted_streams = attr.ib(default=attr.Factory(list))
    queued_streams = attr.ib(default=attr.Factory(lambda: trio.Queue(1)))
    accept_hook = attr.ib(default=None)

    async def connect(self):
        assert not self.closed
        client, server = trio.testing.memory_stream_pair()
        await self.queued_streams.put(server)
        return client

    async def accept(self):
        await trio.hazmat.checkpoint()
        assert not self.closed
        if self.accept_hook is not None:
            await self.accept_hook()
        stream = await self.queued_streams.get()
        self.accepted_streams.append(stream)
        return stream

    async def aclose(self):
        self.closed = True
        await trio.hazmat.checkpoint()


async def test_listen_port_ipv4():
    assert str(ListenPort('10.105.0.2', 80, False)) == 'ws://10.105.0.2:80'
    assert str(ListenPort('127.0.0.1', 8000, False)) == 'ws://127.0.0.1:8000'
    assert str(ListenPort('0.0.0.0', 443, True)) == 'wss://0.0.0.0:443'


async def test_listen_port_ipv6():
    assert str(ListenPort('2599:8807:6201:b7:16cf:bb9c:a6d3:51ab', 80, False)) \
        == 'ws://[2599:8807:6201:b7:16cf:bb9c:a6d3:51ab]:80'
    assert str(ListenPort('::1', 8000, False)) == 'ws://[::1]:8000'
    assert str(ListenPort('::', 443, True)) == 'wss://[::]:443'


async def test_server_has_listeners(nursery):
    server = await nursery.start(serve_websocket, echo_handler, HOST, 0, None)
    assert len(server.listeners) > 0
    assert isinstance(server.listeners[0], ListenPort)


async def test_serve(nursery):
    task = trio.hazmat.current_task()
    server = await nursery.start(serve_websocket, echo_handler, HOST, 0, None)
    port = server.port
    assert server.port != 0
    # The server nursery begins with one task (server.listen).
    assert len(nursery.child_tasks) == 1
    no_clients_nursery_count = len(task.child_nurseries)
    async with open_websocket(HOST, port, RESOURCE, use_ssl=False) as conn:
        # The server nursery has the same number of tasks, but there is now
        # one additional nested nursery.
        assert len(nursery.child_tasks) == 1
        assert len(task.child_nurseries) == no_clients_nursery_count + 1


async def test_serve_ssl(nursery):
    server_context = trio.ssl.create_default_context(
        trio.ssl.Purpose.CLIENT_AUTH)
    client_context = trio.ssl.create_default_context()
    ca = trustme.CA()
    ca.configure_trust(client_context)
    cert = ca.issue_server_cert(HOST)
    cert.configure_cert(server_context)
    server = await nursery.start(serve_websocket, echo_handler, HOST, 0,
        server_context)
    port = server.port
    async with open_websocket(HOST, port, RESOURCE, client_context) as conn:
        assert not conn.is_closed


async def test_serve_handler_nursery(nursery):
    task = trio.hazmat.current_task()
    async with trio.open_nursery() as handler_nursery:
        serve_with_nursery = functools.partial(serve_websocket, echo_handler,
            HOST, 0, None, handler_nursery=handler_nursery)
        server = await nursery.start(serve_with_nursery)
        port = server.port
        # The server nursery begins with one task (server.listen).
        assert len(nursery.child_tasks) == 1
        no_clients_nursery_count = len(task.child_nurseries)
        async with open_websocket(HOST, port, RESOURCE, use_ssl=False) as conn:
            # The handler nursery should have one task in it
            # (conn._reader_task).
            assert len(handler_nursery.child_tasks) == 1


async def test_serve_with_zero_listeners(nursery):
    task = trio.hazmat.current_task()
    with pytest.raises(ValueError):
        server = WebSocketServer(echo_handler, [])


async def test_serve_non_tcp_listener(nursery):
    listeners = [MemoryListener()]
    server = WebSocketServer(echo_handler, listeners)
    await nursery.start(server.run)
    assert len(server.listeners) == 1
    with pytest.raises(RuntimeError):
        server.port
    assert server.listeners[0].startswith('MemoryListener(')
    # TODO add support for arbitrary client streams and test here


async def test_serve_multiple_listeners(nursery):
    listener1 = (await trio.open_tcp_listeners(0, host=HOST))[0]
    listener2 = MemoryListener()
    server = WebSocketServer(echo_handler, [listener1, listener2])
    await nursery.start(server.run)
    assert len(server.listeners) == 2
    with pytest.raises(RuntimeError):
        # Even though the first listener has a port, this property is only
        # usable if you have exactly one listener.
        server.port
    # The first listener metadata is a ListenPort instance.
    assert server.listeners[0].port != 0
    # The second listener metadata is a string containing the repr() of a
    # MemoryListener object.
    assert server.listeners[1].startswith('MemoryListener(')


async def test_client_open(echo_server):
    async with open_websocket(HOST, echo_server.port, RESOURCE, use_ssl=False) \
        as conn:
        assert not conn.is_closed


async def test_client_open_url(echo_server):
    url = 'ws://{}:{}{}?foo=bar'.format(HOST, echo_server.port, RESOURCE)
    async with open_websocket_url(url) as conn:
        assert conn.path == RESOURCE + '?foo=bar'


async def test_client_open_invalid_url(echo_server):
    with pytest.raises(ValueError):
        async with open_websocket_url('http://foo.com/bar') as conn:
            pass


async def test_client_connect(echo_server, nursery):
    conn = await connect_websocket(nursery, HOST, echo_server.port, RESOURCE,
        use_ssl=False)
    assert not conn.is_closed


async def test_client_connect_url(echo_server, nursery):
    url = 'ws://{}:{}{}'.format(HOST, echo_server.port, RESOURCE)
    conn = await connect_websocket_url(nursery, url)
    assert not conn.is_closed


async def test_client_send_and_receive(echo_conn):
    async with echo_conn:
        await echo_conn.send_message('This is a test message.')
        received_msg = await echo_conn.get_message()
        assert received_msg == 'This is a test message.'


async def test_client_ping(echo_conn):
    async with echo_conn:
        await echo_conn.ping(b'A')
    with pytest.raises(ConnectionClosed):
        await echo_conn.ping(b'B')


async def test_client_ping_two_payloads(echo_conn):
    pong_count = 0
    async def ping_and_count():
        nonlocal pong_count
        await echo_conn.ping()
        pong_count += 1
    async with echo_conn:
        async with trio.open_nursery() as nursery:
            nursery.start_soon(ping_and_count)
            nursery.start_soon(ping_and_count)
    assert pong_count == 2


async def test_client_ping_same_payload(echo_conn):
    # This test verifies that two tasks can't ping with the same payload at the
    # same time. One of them should succeed and the other should get an
    # exception.
    exc_count = 0
    async def ping_and_catch():
        nonlocal exc_count
        try:
            await echo_conn.ping(b'A')
        except Exception:
            exc_count += 1
    async with echo_conn:
        async with trio.open_nursery() as nursery:
            nursery.start_soon(ping_and_catch)
            nursery.start_soon(ping_and_catch)
    assert exc_count == 1


async def test_client_pong(echo_conn):
    async with echo_conn:
        await echo_conn.pong(b'A')
    with pytest.raises(ConnectionClosed):
        await echo_conn.pong(b'B')


async def test_client_default_close(echo_conn):
    async with echo_conn:
        assert not echo_conn.is_closed
    assert echo_conn.close_reason.code == 1000
    assert echo_conn.close_reason.reason is None


async def test_client_nondefault_close(echo_conn):
    async with echo_conn:
        assert not echo_conn.is_closed
        await echo_conn.aclose(code=1001, reason='test reason')
    assert echo_conn.close_reason.code == 1001
    assert echo_conn.close_reason.reason == 'test reason'


async def test_wrap_client_stream(echo_server, nursery):
    stream = await trio.open_tcp_stream(HOST, echo_server.port)
    conn = await wrap_client_stream(nursery, stream, HOST, RESOURCE)
    async with conn:
        assert not conn.is_closed
        await conn.send_message('Hello from client!')
        msg = await conn.get_message()
        assert msg == 'Hello from client!'
    assert conn.is_closed


async def test_wrap_server_stream(nursery):
    async def handler(stream):
        server = await wrap_server_stream(nursery, stream)
        async with server:
            assert not server.is_closed
            msg = await server.get_message()
            assert msg == 'Hello from client!'
        assert server.is_closed
    serve_fn = functools.partial(trio.serve_tcp, handler, 0, host=HOST)
    listeners = await nursery.start(serve_fn)
    port = listeners[0].socket.getsockname()[1]
    async with open_websocket(HOST, port, RESOURCE, use_ssl=False) as client:
        await client.send_message('Hello from client!')


async def test_client_does_not_close_handshake(nursery):
    async def handler(server):
        with pytest.raises(ConnectionClosed):
            await server.get_message()
    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    port = server.port
    stream = await trio.open_tcp_stream(HOST, server.port)
    client = await wrap_client_stream(nursery, stream, HOST, RESOURCE)
    async with client:
        await stream.aclose()
        with pytest.raises(ConnectionClosed):
            await client.send_message('Hello from client!')


async def test_server_does_not_close_handshake(nursery):
    async def handler(stream):
        server = await wrap_server_stream(nursery, stream)
        async with server:
            await stream.aclose()
            with pytest.raises(ConnectionClosed):
                await server.send_message('Hello from client!')
    serve_fn = functools.partial(trio.serve_tcp, handler, 0, host=HOST)
    listeners = await nursery.start(serve_fn)
    port = listeners[0].socket.getsockname()[1]
    async with open_websocket(HOST, port, RESOURCE, use_ssl=False) as client:
        with pytest.raises(ConnectionClosed):
            await client.get_message()
