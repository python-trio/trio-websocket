'''
Unit tests for trio_websocket.

Many of these tests involve networking, i.e. real TCP sockets. To maximize
reliability, all networking tests should follow the following rules:

- Use localhost only. This is stored in the ``HOST`` global variable.
- Servers use dynamic ports: by passing zero as a port, the system selects a
  port that is guaranteed to be available.
- The sequence of events between servers and clients should be controlled as
  much as possible to make tests as deterministic. More on determinism below.
- If a test involves timing, e.g. a task needs to ``trio.sleep(…)`` for a bit,
  then the ``autojump_clock`` fixture should be used.
- Most tests that involve I/O should have an absolute timeout placed on it to
  prevent a hung test from blocking the entire test suite. If a hung test is
  cancelled with ctrl+C, then PyTest discards its log messages, which makes
  debugging really difficult! The ``fail_after(…)`` decorator places an absolute
  timeout on test execution that as measured by Trio's clock.

`Read more about writing tests with pytest-trio.
<https://pytest-trio.readthedocs.io/en/latest/>`__

Determinism is an important property of tests, but it can be tricky to
accomplish with network tests. For example, if a test has a client and a server,
then they may race each other to close the connection first. The test author
should select one side to always initiate the closing handshake. For example, if
a test needs to ensure that the client closes first, then it can have the server
call ``ws.get_message()`` without actually sending it a message. This will cause
the server to block until the client has sent the closing handshake. In other
circumstances
'''
from functools import partial, wraps
import ssl

import attr
import pytest
import trio
import trustme
from async_generator import async_generator, yield_

from trio_websocket import (
    connect_websocket,
    connect_websocket_url,
    ConnectionClosed,
    open_websocket,
    open_websocket_url,
    serve_websocket,
    WebSocketServer,
    wrap_client_stream,
    wrap_server_stream
)
from trio_websocket._impl import ListenPort


HOST = '127.0.0.1'
RESOURCE = '/resource'
DEFAULT_TEST_MAX_DURATION = 1

# Timeout tests follow a general pattern: one side waits TIMEOUT seconds for an
# event. The other side delays for FORCE_TIMEOUT seconds to force the timeout
# to trigger. Each test also has maximum runtime (measure by Trio's clock) to
# prevent a faulty test from hanging the entire suite.
TIMEOUT = 1
FORCE_TIMEOUT = 2
TIMEOUT_TEST_MAX_DURATION = 3


@pytest.fixture
@async_generator
async def echo_server(nursery):
    ''' A server that reads one message, sends back the same message,
    then closes the connection. '''
    serve_fn = partial(serve_websocket, echo_request_handler, HOST, 0,
        ssl_context=None)
    server = await nursery.start(serve_fn)
    await yield_(server)


@pytest.fixture
@async_generator
async def echo_conn(echo_server):
    ''' Return a client connection instance that is connected to an echo
    server. '''
    async with open_websocket(HOST, echo_server.port, RESOURCE,
            use_ssl=False) as conn:
        await yield_(conn)


async def echo_request_handler(request):
    '''
    Accept incoming request and then pass off to echo connection handler.
    '''
    conn = await request.accept()
    try:
        msg = await conn.get_message()
        await conn.send_message(msg)
    except ConnectionClosed:
        pass


class fail_after:
    ''' This decorator fails if the runtime of the decorated function (as
    measured by the Trio clock) exceeds the specified value. '''
    def __init__(self, seconds):
        self._seconds = seconds

    def __call__(self, fn):
        @wraps(fn)
        async def wrapper(*args, **kwargs):
            with trio.move_on_after(self._seconds) as cancel_scope:
                await fn(*args, **kwargs)
            if cancel_scope.cancelled_caught:
                pytest.fail('Test runtime exceeded the maximum {} seconds'
                    .format(self._seconds))
        return wrapper


@attr.s(hash=False, cmp=False)
class MemoryListener(trio.abc.Listener):
    closed = attr.ib(default=False)
    accepted_streams = attr.ib(factory=list)
    queued_streams = attr.ib(factory=(lambda: trio.open_memory_channel(1)))
    accept_hook = attr.ib(default=None)

    async def connect(self):
        assert not self.closed
        client, server = memory_stream_pair()
        await self.queued_streams[0].send(server)
        return client

    async def accept(self):
        await trio.hazmat.checkpoint()
        assert not self.closed
        if self.accept_hook is not None:
            await self.accept_hook()
        stream = await self.queued_streams[1].receive()
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
    server = await nursery.start(serve_websocket, echo_request_handler, HOST, 0,
        None)
    assert len(server.listeners) > 0
    assert isinstance(server.listeners[0], ListenPort)


async def test_serve(nursery):
    task = trio.hazmat.current_task()
    server = await nursery.start(serve_websocket, echo_request_handler, HOST, 0,
        None)
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
    server_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    client_context = ssl.create_default_context()
    ca = trustme.CA()
    ca.configure_trust(client_context)
    cert = ca.issue_server_cert(HOST)
    cert.configure_cert(server_context)

    server = await nursery.start(serve_websocket, echo_request_handler, HOST, 0,
        server_context)
    port = server.port
    async with open_websocket(HOST, port, RESOURCE, use_ssl=client_context
            ) as conn:
        assert not conn.closed


async def test_serve_handler_nursery(nursery):
    task = trio.hazmat.current_task()
    async with trio.open_nursery() as handler_nursery:
        serve_with_nursery = partial(serve_websocket, echo_request_handler,
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
        server = WebSocketServer(echo_request_handler, [])


async def test_serve_non_tcp_listener(nursery):
    listeners = [MemoryListener()]
    server = WebSocketServer(echo_request_handler, listeners)
    await nursery.start(server.run)
    assert len(server.listeners) == 1
    with pytest.raises(RuntimeError):
        server.port
    assert server.listeners[0].startswith('MemoryListener(')


async def test_serve_multiple_listeners(nursery):
    listener1 = (await trio.open_tcp_listeners(0, host=HOST))[0]
    listener2 = MemoryListener()
    server = WebSocketServer(echo_request_handler, [listener1, listener2])
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
        assert not conn.closed


async def test_client_open_url(echo_server):
    url = 'ws://{}:{}{}/path'.format(HOST, echo_server.port, RESOURCE)
    async with open_websocket_url(url) as conn:
        assert conn.path == RESOURCE + '/path'

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
    assert not conn.closed


async def test_client_connect_url(echo_server, nursery):
    url = 'ws://{}:{}{}'.format(HOST, echo_server.port, RESOURCE)
    conn = await connect_websocket_url(nursery, url)
    assert not conn.closed


async def test_handshake_subprotocol(nursery):
    async def handler(request):
        assert request.proposed_subprotocols == ('chat', 'file')
        assert request.subprotocol is None
        request.subprotocol = 'chat'
        assert request.subprotocol == 'chat'
        server_ws = await request.accept()
        assert server_ws.subprotocol == 'chat'

    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    async with open_websocket(HOST, server.port, RESOURCE, use_ssl=False,
        subprotocols=('chat', 'file')) as client_ws:
        assert client_ws.subprotocol == 'chat'


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
        except ValueError:
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
        assert not echo_conn.closed
    assert echo_conn.closed.code == 1000
    assert echo_conn.closed.reason is None


async def test_client_nondefault_close(echo_conn):
    async with echo_conn:
        assert not echo_conn.closed
        await echo_conn.aclose(code=1001, reason='test reason')
    assert echo_conn.closed.code == 1001
    assert echo_conn.closed.reason == 'test reason'


async def test_wrap_client_stream(echo_server, nursery):
    stream = await trio.open_tcp_stream(HOST, echo_server.port)
    conn = await wrap_client_stream(nursery, stream, HOST, RESOURCE)
    async with conn:
        assert not conn.closed
        await conn.send_message('Hello from client!')
        msg = await conn.get_message()
        assert msg == 'Hello from client!'
    assert conn.closed


async def test_wrap_server_stream(nursery):
    async def handler(stream):
        request = await wrap_server_stream(nursery, stream)
        server_ws = await request.accept()
        async with server_ws:
            assert not server_ws.closed
            msg = await server_ws.get_message()
            assert msg == 'Hello from client!'
        assert server_ws.closed
    serve_fn = partial(trio.serve_tcp, handler, 0, host=HOST)
    listeners = await nursery.start(serve_fn)
    port = listeners[0].socket.getsockname()[1]
    async with open_websocket(HOST, port, RESOURCE, use_ssl=False) as client:
        await client.send_message('Hello from client!')


@fail_after(TIMEOUT_TEST_MAX_DURATION)
async def test_client_open_timeout(nursery, autojump_clock):
    '''
    The client times out waiting for the server to complete the opening
    handshake.
    '''
    async def handler(request):
        await trio.sleep(FORCE_TIMEOUT)
        server_ws = await request.accept()
        pytest.fail('Should not reach this line.')

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None))

    with pytest.raises(trio.TooSlowError):
        async with open_websocket(HOST, server.port, '/', use_ssl=False,
                connect_timeout=TIMEOUT) as client_ws:
            pass


@fail_after(TIMEOUT_TEST_MAX_DURATION)
async def test_client_close_timeout(nursery, autojump_clock):
    '''
    This client times out waiting for the server to complete the closing
    handshake.

    To slow down the server's closing handshake, we make sure that its message
    queue size is 0, and the client sends it exactly 1 message. This blocks the
    server's reader so it won't do the closing handshake for at least
    ``FORCE_TIMEOUT`` seconds.
    '''
    async def handler(request):
        server_ws = await request.accept()
        await trio.sleep(FORCE_TIMEOUT)
        # The next line should raise ConnectionClosed.
        await server_ws.get_message()
        pytest.fail('Should not reach this line.')

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None,
        message_queue_size=0))

    with pytest.raises(trio.TooSlowError):
        async with open_websocket(HOST, server.port, RESOURCE, use_ssl=False,
                disconnect_timeout=TIMEOUT) as client_ws:
            await client_ws.send_message('test')


@fail_after(TIMEOUT_TEST_MAX_DURATION)
async def test_server_open_timeout(autojump_clock):
    '''
    The server times out waiting for the client to complete the opening
    handshake.

    Server timeouts don't raise exceptions, because handler tasks are launched
    in an internal nursery and sending exceptions wouldn't be helpful. Instead,
    timed out tasks silently end.
    '''
    async def handler(request):
        pytest.fail('This handler should not be called.')

    async with trio.open_nursery() as nursery:
        server = await nursery.start(partial(serve_websocket, handler, HOST, 0,
            ssl_context=None, handler_nursery=nursery, connect_timeout=TIMEOUT))

        old_task_count = len(nursery.child_tasks)
        # This stream is not a WebSocket, so it won't send a handshake:
        stream = await trio.open_tcp_stream(HOST, server.port)
        # Checkpoint so the server's handler task can spawn:
        await trio.sleep(0)
        assert len(nursery.child_tasks) == old_task_count + 1, \
            "Server's reader task did not spawn"
        # Sleep long enough to trigger server's connect_timeout:
        await trio.sleep(FORCE_TIMEOUT)
        assert len(nursery.child_tasks) == old_task_count, \
            "Server's reader task is still running"
        # Cancel the server task:
        nursery.cancel_scope.cancel()


@fail_after(TIMEOUT_TEST_MAX_DURATION)
async def test_server_close_timeout(autojump_clock):
    '''
    The server times out waiting for the client to complete the closing
    handshake.

    Server timeouts don't raise exceptions, because handler tasks are launched
    in an internal nursery and sending exceptions wouldn't be helpful. Instead,
    timed out tasks silently end.

    To prevent the client from doing the closing handshake, we make sure that
    its message queue size is 0 and the server sends it exactly 1 message. This
    blocks the client's reader and prevents it from doing the client handshake.
    '''
    async def handler(request):
        ws = await request.accept()
        # Send one message to block the client's reader task:
        await ws.send_message('test')

    async with trio.open_nursery() as outer:
        server = await outer.start(partial(serve_websocket, handler, HOST, 0,
            ssl_context=None, handler_nursery=outer,
            disconnect_timeout=TIMEOUT))

        old_task_count = len(outer.child_tasks)
        # Spawn client inside an inner nursery so that we can cancel it's reader
        # so that it won't do a closing handshake.
        async with trio.open_nursery() as inner:
            ws = await connect_websocket(inner, HOST, server.port, RESOURCE,
                use_ssl=False)
            # Checkpoint so the server can spawn a handler task:
            await trio.sleep(0)
            assert len(outer.child_tasks) == old_task_count + 1, \
                "Server's reader task did not spawn"
            # The client waits long enough to trigger the server's disconnect
            # timeout:
            await trio.sleep(FORCE_TIMEOUT)
            # The server should have cancelled the handler:
            assert len(outer.child_tasks) == old_task_count, \
                "Server's reader task is still running"
            # Cancel the client's reader task:
            inner.cancel_scope.cancel()

        # Cancel the server task:
        outer.cancel_scope.cancel()


async def test_client_does_not_close_handshake(nursery):
    async def handler(request):
        server_ws = await request.accept()
        with pytest.raises(ConnectionClosed):
            await server_ws.get_message()
    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    stream = await trio.open_tcp_stream(HOST, server.port)
    client_ws = await wrap_client_stream(nursery, stream, HOST, RESOURCE)
    async with client_ws:
        await stream.aclose()
        with pytest.raises(ConnectionClosed):
            await client_ws.send_message('Hello from client!')


async def test_server_does_not_close_handshake(nursery):
    async def handler(stream):
        request = await wrap_server_stream(nursery, stream)
        server_ws = await request.accept()
        async with server_ws:
            await stream.aclose()
            with pytest.raises(ConnectionClosed):
                await server_ws.send_message('Hello from client!')
    serve_fn = partial(trio.serve_tcp, handler, 0, host=HOST)
    listeners = await nursery.start(serve_fn)
    port = listeners[0].socket.getsockname()[1]
    async with open_websocket(HOST, port, RESOURCE, use_ssl=False) as client:
        with pytest.raises(ConnectionClosed):
            await client.get_message()


async def test_server_handler_exit(nursery, autojump_clock):
    async def handler(request):
        server_ws = await request.accept()
        await trio.sleep(1)

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None))

    # connection should close when server handler exists
    with trio.fail_after(2):
        async with open_websocket(
                HOST, server.port, '/', use_ssl=False) as connection:
            with pytest.raises(ConnectionClosed) as exc_info:
                await connection.get_message()
            exc = exc_info.value
            assert exc.reason.name == 'NORMAL_CLOSURE'


@fail_after(DEFAULT_TEST_MAX_DURATION)
async def test_read_messages_after_remote_close(nursery):
    '''
    When the remote endpoint closes, the local endpoint can still read all
    of the messages sent prior to closing. Any attempt to read beyond that will
    raise ConnectionClosed.

    This test also exercises the configuration of the queue size.
    '''
    server_closed = trio.Event()

    async def handler(request):
        server = await request.accept()
        async with server:
            await server.send_message('1')
            await server.send_message('2')
        server_closed.set()

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None))

    # The client needs a message queue of size 2 so that it can buffer both
    # incoming messages without blocking the reader task.
    async with open_websocket(HOST, server.port, '/', use_ssl=False,
            message_queue_size=2) as client:
        await server_closed.wait()
        assert await client.get_message() == '1'
        assert await client.get_message() == '2'
        with pytest.raises(ConnectionClosed):
            await client.get_message()


async def test_no_messages_after_local_close(nursery):
    '''
    If the local endpoint initiates closing, then pending messages are discarded
    and any attempt to read a message will raise ConnectionClosed.
    '''
    client_closed = trio.Event()

    async def handler(request):
        # The server sends some messages and then closes.
        server = await request.accept()
        async with server:
            await server.send_message('1')
            await server.send_message('2')
            await client_closed.wait()

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None))

    async with open_websocket(HOST, server.port, '/', use_ssl=False) as client:
        pass
    with pytest.raises(ConnectionClosed):
        await client.get_message()
    client_closed.set()


async def test_cm_exit_with_pending_messages(echo_server, autojump_clock):
    '''
    Regression test for #74, where a context manager was not able to exit when
    there were pending messages in the receive queue.
    '''
    with trio.fail_after(1):
        async with open_websocket(HOST, echo_server.port, RESOURCE,
                use_ssl=False) as ws:
            await ws.send_message('hello')
            # allow time for the server to respond
            await trio.sleep(.1)


@fail_after(DEFAULT_TEST_MAX_DURATION)
async def test_max_message_size(nursery):
    '''
    Set the client's max message size to 100 bytes. The client can send a
    message larger than 100 bytes, but when it receives a message larger than
    100 bytes, it closes the connection with code 1009.
    '''
    async def handler(request):
        ''' Similar to the echo_request_handler fixture except it runs in a
        loop. '''
        conn = await request.accept()
        while True:
            try:
                msg = await conn.get_message()
                await conn.send_message(msg)
            except ConnectionClosed:
                break

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None))

    async with open_websocket(HOST, server.port, RESOURCE, use_ssl=False,
            max_message_size=100) as client:
        # We can send and receive 100 bytes:
        await client.send_message(b'A' * 100)
        msg = await client.get_message()
        assert len(msg) == 100
        # We can send 101 bytes but cannot receive 101 bytes:
        await client.send_message(b'B' * 101)
        with pytest.raises(ConnectionClosed):
            await client.get_message()
        assert client.closed
        assert client.closed.code == 1009


async def test_close_race(nursery, autojump_clock):
    """server attempts close just as client disconnects (issue #96)"""

    async def handler(request):
        ws = await request.accept()
        await ws.send_message('foo')
        await ws._for_testing_peer_closed_connection.wait()
        # with bug, this would raise ConnectionClosed from websocket internal task
        await trio.aclose_forcefully(ws._stream)

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None))

    connection = await connect_websocket(nursery, HOST, server.port,
                                         RESOURCE, use_ssl=False)
    await connection.get_message()
    await connection.aclose()
    await trio.sleep(.1)
