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
from __future__ import annotations

import copy
import re
import ssl
import sys
from collections.abc import AsyncGenerator
from functools import partial, wraps
from typing import TYPE_CHECKING, TypeVar, cast
from unittest.mock import Mock, patch
from importlib.metadata import version

import attr
import pytest
import trio
import trustme
import wsproto
from trio.testing import memory_stream_pair, memory_stream_pump
from wsproto.events import CloseConnection

try:
    from trio.lowlevel import current_task  # pylint: disable=ungrouped-imports
except ImportError:
    from trio.hazmat import current_task  # type: ignore # pylint: disable=ungrouped-imports


# only available on trio>=0.25, we don't use it when testing lower versions
try:
    from trio.testing import RaisesGroup
except ImportError:
    pass


from trio_websocket import (
    CloseReason,
    ConnectionClosed,
    ConnectionRejected,
    ConnectionTimeout,
    DisconnectionTimeout,
    Endpoint,
    HandshakeError,
    WebSocketConnection,
    WebSocketRequest,
    WebSocketServer,
    connect_websocket,
    connect_websocket_url,
    open_websocket,
    open_websocket_url,
    serve_websocket,
    wrap_client_stream,
    wrap_server_stream,
)
from trio_websocket._impl import _TRIO_EXC_GROUP_TYPE

if sys.version_info < (3, 11):
    from exceptiongroup import BaseExceptionGroup  # pylint: disable=redefined-builtin

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable
    from wsproto.events import Event

    from typing_extensions import ParamSpec, TypeAlias
    PS = ParamSpec("PS")

    StapledMemoryStream: TypeAlias = trio.StapledStream[
        trio.testing.MemorySendStream,
        trio.testing.MemoryReceiveStream,
    ]

WS_PROTO_VERSION = tuple(map(int, wsproto.__version__.split('.')))

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

T = TypeVar("T")


@pytest.fixture
async def echo_server(nursery: trio.Nursery) -> AsyncGenerator[WebSocketServer, None]:
    ''' A server that reads one message, sends back the same message,
    then closes the connection. '''
    serve_fn = partial(serve_websocket, echo_request_handler, HOST, 0,
        ssl_context=None)
    server = await nursery.start(serve_fn)
    # Cast needed because currently `nursery.start` has typing issues
    # blocked by https://github.com/python/mypy/pull/17512
    yield cast(WebSocketServer, server)


@pytest.fixture
async def echo_conn(echo_server: WebSocketServer) -> AsyncGenerator[WebSocketConnection, None]:
    ''' Return a client connection instance that is connected to an echo
    server. '''
    async with open_websocket(HOST, echo_server.port, RESOURCE,
            use_ssl=False) as conn:
        yield conn


async def echo_request_handler(request: WebSocketRequest) -> None:
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
    def __init__(self, seconds: int) -> None:
        self._seconds = seconds

    def __call__(self, fn: Callable[PS, Awaitable[T]]) -> Callable[PS, Awaitable[T | None]]:
        # Type of decorated function contains type `Any`
        @wraps(fn)
        async def wrapper(  # type: ignore[misc]
            *args: PS.args,
            **kwargs: PS.kwargs,
        ) -> T:
            with trio.move_on_after(self._seconds) as cancel_scope:
                return await fn(*args, **kwargs)
            if cancel_scope.cancelled_caught:
                pytest.fail(f'Test runtime exceeded the maximum {self._seconds} seconds')
            raise AssertionError("Should be unreachable")
        return wrapper


@attr.s(hash=False, eq=False)
class MemoryListener(trio.abc.Listener["StapledMemoryStream"]):
    closed: bool = attr.ib(default=False)
    accepted_streams: list[StapledMemoryStream] = attr.ib(factory=list)
    queued_streams: tuple[
        trio.MemorySendChannel[StapledMemoryStream],
        trio.MemoryReceiveChannel[StapledMemoryStream],
    ] = attr.ib(factory=lambda: trio.open_memory_channel["StapledMemoryStream"](1))
    accept_hook: Callable[[], Awaitable[object]] | None = attr.ib(default=None)

    async def connect(self) -> trio.StapledStream[
        trio.testing.MemorySendStream,
        trio.testing.MemoryReceiveStream,
    ]:
        assert not self.closed
        client, server = memory_stream_pair()
        await self.queued_streams[0].send(server)
        return client

    async def accept(self) -> trio.StapledStream[
        trio.testing.MemorySendStream,
        trio.testing.MemoryReceiveStream,
    ]:
        await trio.sleep(0)
        assert not self.closed
        if self.accept_hook is not None:
            await self.accept_hook()
        stream = await self.queued_streams[1].receive()
        self.accepted_streams.append(stream)
        return stream

    async def aclose(self) -> None:
        self.closed = True
        await trio.sleep(0)


async def test_endpoint_ipv4() -> None:
    e1 = Endpoint('10.105.0.2', 80, False)
    assert e1.url == 'ws://10.105.0.2'
    assert str(e1) == 'Endpoint(address="10.105.0.2", port=80, is_ssl=False)'
    e2 = Endpoint('127.0.0.1', 8000, False)
    assert e2.url == 'ws://127.0.0.1:8000'
    assert str(e2) == 'Endpoint(address="127.0.0.1", port=8000, is_ssl=False)'
    e3 = Endpoint('0.0.0.0', 443, True)
    assert e3.url == 'wss://0.0.0.0'
    assert str(e3) == 'Endpoint(address="0.0.0.0", port=443, is_ssl=True)'


async def test_listen_port_ipv6() -> None:
    e1 = Endpoint('2599:8807:6201:b7:16cf:bb9c:a6d3:51ab', 80, False)
    assert e1.url == 'ws://[2599:8807:6201:b7:16cf:bb9c:a6d3:51ab]'
    assert str(e1) == 'Endpoint(address="2599:8807:6201:b7:16cf:bb9c:a6d3' \
                      ':51ab", port=80, is_ssl=False)'
    e2 = Endpoint('::1', 8000, False)
    assert e2.url == 'ws://[::1]:8000'
    assert str(e2) == 'Endpoint(address="::1", port=8000, is_ssl=False)'
    e3 = Endpoint('::', 443, True)
    assert e3.url == 'wss://[::]'
    assert str(e3) == 'Endpoint(address="::", port=443, is_ssl=True)'


async def test_server_has_listeners(nursery: trio.Nursery) -> None:
    server = await nursery.start(serve_websocket, echo_request_handler, HOST, 0,
        None)
    assert isinstance(server, WebSocketServer)
    assert len(server.listeners) > 0
    assert isinstance(server.listeners[0], Endpoint)


async def test_serve(nursery: trio.Nursery) -> None:
    task = current_task()
    server = await nursery.start(serve_websocket, echo_request_handler, HOST, 0,
        None)
    assert isinstance(server, WebSocketServer)
    port = server.port
    assert server.port != 0
    # The server nursery begins with one task (server.listen).
    assert len(nursery.child_tasks) == 1
    no_clients_nursery_count = len(task.child_nurseries)
    async with open_websocket(HOST, port, RESOURCE, use_ssl=False):
        # The server nursery has the same number of tasks, but there is now
        # one additional nested nursery.
        assert len(nursery.child_tasks) == 1
        assert len(task.child_nurseries) == no_clients_nursery_count + 1


async def test_serve_ssl(nursery: trio.Nursery) -> None:
    server_context = ssl.create_default_context(ssl.Purpose.CLIENT_AUTH)
    client_context = ssl.create_default_context()
    ca = trustme.CA()
    ca.configure_trust(client_context)
    cert = ca.issue_server_cert(HOST)
    cert.configure_cert(server_context)

    server = await nursery.start(serve_websocket, echo_request_handler, HOST, 0,
        server_context)
    assert isinstance(server, WebSocketServer)
    port = server.port
    async with open_websocket(HOST, port, RESOURCE, use_ssl=client_context
            ) as conn:
        assert not conn.closed
        assert isinstance(conn.local, Endpoint)
        assert conn.local.is_ssl
        assert isinstance(conn.remote, Endpoint)
        assert conn.remote.is_ssl


async def test_serve_handler_nursery(nursery: trio.Nursery) -> None:
    async with trio.open_nursery() as handler_nursery:
        serve_with_nursery = partial(serve_websocket, echo_request_handler,
            HOST, 0, None, handler_nursery=handler_nursery)
        server = await nursery.start(serve_with_nursery)
        assert isinstance(server, WebSocketServer)
        port = server.port
        # The server nursery begins with one task (server.listen).
        assert len(nursery.child_tasks) == 1
        async with open_websocket(HOST, port, RESOURCE, use_ssl=False):
            # The handler nursery should have one task in it
            # (conn._reader_task).
            assert len(handler_nursery.child_tasks) == 1


async def test_serve_with_zero_listeners() -> None:
    with pytest.raises(ValueError):
        WebSocketServer(echo_request_handler, [])


def memory_listener() -> trio.SocketListener:
    return MemoryListener()  # type: ignore[return-value]


async def test_serve_non_tcp_listener(nursery: trio.Nursery) -> None:
    listeners = [memory_listener()]
    server = WebSocketServer(
        echo_request_handler,
        listeners,
    )
    await nursery.start(server.run)
    assert len(server.listeners) == 1
    with pytest.raises(RuntimeError):
        server.port  # pylint: disable=pointless-statement
    listener = server.listeners[0]
    assert isinstance(listener, str)
    assert listener.startswith('MemoryListener(')


async def test_serve_multiple_listeners(nursery: trio.Nursery) -> None:
    listener1 = (await trio.open_tcp_listeners(0, host=HOST))[0]
    listener2 = memory_listener()
    server = WebSocketServer(
        echo_request_handler, [
            listener1,
            listener2,
        ]
    )
    await nursery.start(server.run)
    assert len(server.listeners) == 2
    with pytest.raises(RuntimeError):
        # Even though the first listener has a port, this property is only
        # usable if you have exactly one listener.
        server.port  # pylint: disable=pointless-statement
    # The first listener metadata is a ListenPort instance.
    listener_zero = server.listeners[0]
    assert isinstance(listener_zero, Endpoint)
    assert listener_zero.port != 0
    # The second listener metadata is a string containing the repr() of a
    # MemoryListener object.
    listener_one = server.listeners[1]
    assert isinstance(listener_one, str)
    assert listener_one.startswith('MemoryListener(')


async def test_client_open(echo_server: WebSocketServer) -> None:
    async with open_websocket(HOST, echo_server.port, RESOURCE, use_ssl=False) \
        as conn:
        assert not conn.closed
        assert conn.is_client
        assert str(conn).startswith('client-')


@pytest.mark.parametrize('path, expected_path', [
    ('/', '/'),
    ('', '/'),
    (RESOURCE + '/path', RESOURCE + '/path'),
    (RESOURCE + '?foo=bar', RESOURCE + '?foo=bar')
])
async def test_client_open_url(path: str, expected_path: str, echo_server: WebSocketServer) -> None:
    url = f'ws://{HOST}:{echo_server.port}{path}'
    async with open_websocket_url(url) as conn:
        assert conn.path == expected_path


async def test_client_open_invalid_url(echo_server: WebSocketServer) -> None:
    with pytest.raises(ValueError):
        async with open_websocket_url('http://foo.com/bar'):
            pass

async def test_client_open_invalid_ssl(
    echo_server: WebSocketServer,
    nursery: trio.Nursery,
) -> None:
    with pytest.raises(TypeError, match='`use_ssl` argument must be bool or ssl.SSLContext'):
        await connect_websocket(
            nursery, HOST, echo_server.port, RESOURCE,
            use_ssl=1,  # type: ignore[arg-type]
        )

    url = f'ws://{HOST}:{echo_server.port}{RESOURCE}'
    with pytest.raises(ValueError, match='^SSL context must be None for ws: URL scheme$' ):
        await connect_websocket_url(
            nursery, url, ssl_context=ssl.SSLContext(ssl.PROTOCOL_TLS_CLIENT))


async def test_ascii_encoded_path_is_ok(echo_server: WebSocketServer) -> None:
    path = '%D7%90%D7%91%D7%90?%D7%90%D7%9E%D7%90'
    url = f'ws://{HOST}:{echo_server.port}{RESOURCE}/{path}'
    async with open_websocket_url(url) as conn:
        assert conn.path == RESOURCE + '/' + path


# Type ignore because @patch contains `Any`
@patch('trio_websocket._impl.open_websocket')
def test_client_open_url_options(  # type: ignore[misc]
    open_websocket_mock: Mock,
) -> None:
    """open_websocket_url() must pass its options on to open_websocket()"""
    port = 1234
    url = f'ws://{HOST}:{port}{RESOURCE}'
    options = {
        'subprotocols': ['chat'],
        'extra_headers': [(b'X-Test-Header', b'My test header')],
        'message_queue_size': 9,
        'max_message_size': 333,
        'receive_buffer_size': 999,
        'connect_timeout': 36,
        'disconnect_timeout': 37,
    }
    open_websocket_url(url, **options)  # type: ignore[arg-type]
    _, call_args, call_kwargs = open_websocket_mock.mock_calls[0]
    assert call_args == (HOST, port, RESOURCE)
    assert not call_kwargs.pop('use_ssl')
    assert call_kwargs == options

    open_websocket_url(url.replace('ws:', 'wss:'))
    _, call_args, call_kwargs = open_websocket_mock.mock_calls[1]
    assert call_kwargs['use_ssl']


async def test_client_connect(echo_server: WebSocketServer, nursery: trio.Nursery) -> None:
    conn = await connect_websocket(nursery, HOST, echo_server.port, RESOURCE,
        use_ssl=False)
    assert not conn.closed


async def test_client_connect_url(echo_server: WebSocketServer, nursery: trio.Nursery) -> None:
    url = f'ws://{HOST}:{echo_server.port}{RESOURCE}'
    conn = await connect_websocket_url(nursery, url)
    assert not conn.closed


async def test_connection_has_endpoints(echo_conn: WebSocketConnection) -> None:
    async with echo_conn:
        assert isinstance(echo_conn.local, Endpoint)
        assert str(echo_conn.local.address) == HOST
        assert echo_conn.local.port > 1024
        assert not echo_conn.local.is_ssl

        assert isinstance(echo_conn.remote, Endpoint)
        assert str(echo_conn.remote.address) == HOST
        assert echo_conn.remote.port > 1024
        assert not echo_conn.remote.is_ssl


@fail_after(1)
async def test_handshake_has_endpoints(nursery: trio.Nursery) -> None:
    async def handler(request: WebSocketRequest) -> None:
        assert isinstance(server, WebSocketServer)
        assert isinstance(request.local, Endpoint)
        assert str(request.local.address) == HOST
        assert request.local.port == server.port
        assert not request.local.is_ssl
        assert isinstance(request.remote, Endpoint)
        assert str(request.remote.address) == HOST
        assert not request.remote.is_ssl
        await request.accept()

    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    assert isinstance(server, WebSocketServer)
    async with open_websocket(HOST, server.port, RESOURCE, use_ssl=False):
        pass


async def test_handshake_subprotocol(nursery: trio.Nursery) -> None:
    async def handler(request: WebSocketRequest) -> None:
        assert request.proposed_subprotocols == ('chat', 'file')
        server_ws = await request.accept(subprotocol='chat')
        assert server_ws.subprotocol == 'chat'

    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    assert isinstance(server, WebSocketServer)
    async with open_websocket(HOST, server.port, RESOURCE, use_ssl=False,
            subprotocols=('chat', 'file')) as client_ws:
        assert client_ws.subprotocol == 'chat'


async def test_handshake_path(nursery: trio.Nursery) -> None:
    async def handler(request: WebSocketRequest) -> None:
        assert request.path == RESOURCE
        server_ws = await request.accept()
        assert server_ws.path == RESOURCE

    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    assert isinstance(server, WebSocketServer)
    async with open_websocket(HOST, server.port, RESOURCE, use_ssl=False,
            ) as client_ws:
        assert client_ws.path == RESOURCE


@fail_after(1)
async def test_handshake_client_headers(nursery: trio.Nursery) -> None:
    async def handler(request: WebSocketRequest) -> None:
        headers = dict(request.headers)
        assert b'x-test-header' in headers
        assert headers[b'x-test-header'] == b'My test header'
        server_ws = await request.accept()
        await server_ws.send_message('test')

    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    assert isinstance(server, WebSocketServer)
    headers = [(b'X-Test-Header', b'My test header')]
    async with open_websocket(HOST, server.port, RESOURCE, use_ssl=False,
            extra_headers=headers) as client_ws:
        await client_ws.get_message()


@fail_after(1)
async def test_handshake_server_headers(nursery: trio.Nursery) -> None:
    async def handler(request: WebSocketRequest) -> None:
        headers = [(b'X-Test-Header', b'My test header')]
        await request.accept(extra_headers=headers)

    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    assert isinstance(server, WebSocketServer)
    async with open_websocket(HOST, server.port, RESOURCE, use_ssl=False
            ) as client_ws:
        header_key, header_value = client_ws.handshake_headers[0]
        assert header_key == b'x-test-header'
        assert header_value == b'My test header'


@fail_after(5)
async def test_open_websocket_internal_ki(
    nursery: trio.Nursery,
    monkeypatch: pytest.MonkeyPatch,
    autojump_clock: trio.testing.MockClock,
) -> None:
    """_reader_task._handle_ping_event triggers KeyboardInterrupt.
    user code also raises exception.
    Make sure that KI is delivered, and the user exception is in the __cause__ exceptiongroup
    """
    async def ki_raising_ping_handler(*args: object, **kwargs: object) -> None:
        raise KeyboardInterrupt
    monkeypatch.setattr(WebSocketConnection, "_handle_ping_event", ki_raising_ping_handler)
    async def handler(request: WebSocketRequest) -> None:
        server_ws = await request.accept()
        await server_ws.ping(b"a")

    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    assert isinstance(server, WebSocketServer)
    with pytest.raises(KeyboardInterrupt) as exc_info:
        async with open_websocket(HOST, server.port, RESOURCE, use_ssl=False):
            with trio.fail_after(1) as cs:
                cs.shield = True
                await trio.sleep(2)

    e_cause = exc_info.value.__cause__
    assert isinstance(e_cause, _TRIO_EXC_GROUP_TYPE)
    assert any(isinstance(e, trio.TooSlowError) for e in e_cause.exceptions)

@fail_after(5)
async def test_open_websocket_internal_exc(
    nursery: trio.Nursery,
    monkeypatch: pytest.MonkeyPatch,
    autojump_clock: trio.testing.MockClock,
) -> None:
    """_reader_task._handle_ping_event triggers ValueError.
    user code also raises exception.
    internal exception is in __context__ exceptiongroup and user exc is delivered
    """
    internal_error = ValueError()
    internal_error.__context__ = TypeError()
    user_error = NameError()
    user_error_context = KeyError()
    async def raising_ping_event(*args: object, **kwargs: object) -> None:
        raise internal_error

    monkeypatch.setattr(WebSocketConnection, "_handle_ping_event", raising_ping_event)
    async def handler(request: WebSocketRequest) -> None:
        server_ws = await request.accept()
        await server_ws.ping(b"a")

    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    assert isinstance(server, WebSocketServer)
    with pytest.raises(type(user_error)) as exc_info:
        async with open_websocket(HOST, server.port, RESOURCE, use_ssl=False):
            await trio.lowlevel.checkpoint()
            user_error.__context__ = user_error_context
            raise user_error

    assert exc_info.value is user_error
    e_context = exc_info.value.__context__
    assert isinstance(e_context, BaseExceptionGroup)  # pylint: disable=possibly-used-before-assignment
    assert internal_error in e_context.exceptions
    assert user_error_context in e_context.exceptions

@fail_after(5)
async def test_open_websocket_cancellations(
    nursery: trio.Nursery,
    monkeypatch: pytest.MonkeyPatch,
    autojump_clock: trio.testing.MockClock,
) -> None:
    """Both user code and _reader_task raise Cancellation.
    Check that open_websocket reraises the one from user code for traceback reasons.
    """


    async def sleeping_ping_event(*args: object, **kwargs: object) -> None:
        await trio.sleep_forever()

    # We monkeypatch WebSocketConnection._handle_ping_event to ensure it will actually
    # raise Cancelled upon being cancelled. For some reason it doesn't otherwise.
    monkeypatch.setattr(WebSocketConnection, "_handle_ping_event", sleeping_ping_event)
    async def handler(request: WebSocketRequest) -> None:
        server_ws = await request.accept()
        await server_ws.ping(b"a")
    user_cancelled = None
    user_cancelled_cause = None
    user_cancelled_context = None

    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    assert isinstance(server, WebSocketServer)
    with trio.move_on_after(2):
        with pytest.raises(trio.Cancelled) as exc_info:
            async with open_websocket(HOST, server.port, RESOURCE, use_ssl=False):
                try:
                    await trio.sleep_forever()
                except trio.Cancelled as e:
                    user_cancelled = e
                    user_cancelled_cause = e.__cause__
                    user_cancelled_context = e.__context__
                    raise

    assert exc_info.value is user_cancelled
    assert exc_info.value.__cause__ is user_cancelled_cause
    assert exc_info.value.__context__ is user_cancelled_context

def _trio_default_non_strict_exception_groups() -> bool:
    trio_version = version("trio")
    assert re.match(r'^0\.\d\d\.', trio_version), "unexpected trio versioning scheme"
    return int(trio_version[2:4]) < 25

@fail_after(1)
async def test_handshake_exception_before_accept() -> None:
    ''' In #107, a request handler that throws an exception before finishing the
    handshake causes the task to hang. The proper behavior is to raise an
    exception to the nursery as soon as possible. '''
    async def handler(request: WebSocketRequest) -> None:
        raise ValueError()

    # pylint fails to resolve that BaseExceptionGroup will always be available
    with pytest.raises((BaseExceptionGroup, ValueError)) as exc:  # pylint: disable=possibly-used-before-assignment
        async with trio.open_nursery() as nursery:
            server = await nursery.start(serve_websocket, handler, HOST, 0,
                None)
            assert isinstance(server, WebSocketServer)
            async with open_websocket(HOST, server.port, RESOURCE,
                    use_ssl=False):
                pass

    if _trio_default_non_strict_exception_groups():
        assert isinstance(exc.value, ValueError)
    else:
        # there's 4 levels of nurseries opened, leading to 4 nested groups:
        # 1. this test
        # 2. WebSocketServer.run
        # 3. trio.serve_listeners
        # 4. WebSocketServer._handle_connection
        assert RaisesGroup(
            RaisesGroup(
                RaisesGroup(
                    RaisesGroup(ValueError)))).matches(exc.value)


async def test_user_exception_cause(nursery: trio.Nursery) -> None:
    async def handler(request: WebSocketRequest) -> None:
        await request.accept()
    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    assert isinstance(server, WebSocketServer)
    e_context = TypeError("foo")
    e_primary = ValueError("bar")
    e_cause = RuntimeError("zee")
    with pytest.raises(ValueError) as exc_info:
        async with open_websocket(HOST, server.port, RESOURCE, use_ssl=False):
            try:
                raise e_context
            except TypeError:
                raise e_primary from e_cause
    e = exc_info.value
    assert e is e_primary
    assert e.__cause__ is e_cause
    assert e.__context__ is e_context

@fail_after(1)
async def test_reject_handshake(nursery: trio.Nursery) -> None:
    async def handler(request: WebSocketRequest) -> None:
        body = b'My body'
        await request.reject(400, body=body)

    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    assert isinstance(server, WebSocketServer)
    with pytest.raises(ConnectionRejected) as exc_info:
        async with open_websocket(HOST, server.port, RESOURCE, use_ssl=False):
            pass
    exc = exc_info.value
    assert exc.body == b'My body'


@fail_after(1)
async def test_reject_handshake_invalid_info_status(nursery: trio.Nursery) -> None:
    '''
    An informational status code that is not 101 should cause the client to
    reject the handshake. Since it is an informational response, there will not
    be a response body, so this test exercises a different code path.
    '''
    async def handler(stream: trio.SocketStream) -> None:
        await stream.send_all(b'HTTP/1.1 100 CONTINUE\r\n\r\n')
        await stream.receive_some(max_bytes=1024)
    serve_fn = partial(trio.serve_tcp, handler, 0, host=HOST)
    raw_listeners = await nursery.start(serve_fn)
    listeners = cast("list[trio.SocketListener]", raw_listeners)
    port = listeners[0].socket.getsockname()[1]

    with pytest.raises(ConnectionRejected) as exc_info:
        async with open_websocket(HOST, port, RESOURCE, use_ssl=False):
            pass
    exc = exc_info.value
    assert exc.status_code == 100
    assert repr(exc) == 'ConnectionRejected<status_code=100>'
    assert exc.body is None


async def test_handshake_protocol_error(echo_server: WebSocketServer) -> None:
    '''
    If a client connects to a trio-websocket server and tries to speak HTTP
    instead of WebSocket, the server should reject the connection. (If the
    server does not catch the protocol exception, it will raise an exception up
    to the nursery level and fail the test.)
    '''
    client_stream = await trio.open_tcp_stream(HOST, echo_server.port)
    async with client_stream:
        await client_stream.send_all(b'GET / HTTP/1.1\r\n\r\n')
        response = await client_stream.receive_some(1024)
        assert response.startswith(b'HTTP/1.1 400')


async def test_client_send_and_receive(echo_conn: WebSocketConnection) -> None:
    async with echo_conn:
        await echo_conn.send_message('This is a test message.')
        received_msg = await echo_conn.get_message()
        assert received_msg == 'This is a test message.'


async def test_client_send_invalid_type(echo_conn: WebSocketConnection) -> None:
    async with echo_conn:
        with pytest.raises(ValueError):
            await echo_conn.send_message(object())  # type: ignore[arg-type]


async def test_client_ping(echo_conn: WebSocketConnection) -> None:
    async with echo_conn:
        await echo_conn.ping(b'A')
    with pytest.raises(ConnectionClosed):
        await echo_conn.ping(b'B')


async def test_client_ping_two_payloads(echo_conn: WebSocketConnection) -> None:
    pong_count = 0
    async def ping_and_count() -> None:
        nonlocal pong_count
        await echo_conn.ping()
        pong_count += 1
    async with echo_conn:
        async with trio.open_nursery() as nursery:
            nursery.start_soon(ping_and_count)
            nursery.start_soon(ping_and_count)
    assert pong_count == 2


async def test_client_ping_same_payload(echo_conn: WebSocketConnection) -> None:
    # This test verifies that two tasks can't ping with the same payload at the
    # same time. One of them should succeed and the other should get an
    # exception.
    exc_count = 0
    async def ping_and_catch() -> None:
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


async def test_client_pong(echo_conn: WebSocketConnection) -> None:
    async with echo_conn:
        await echo_conn.pong(b'A')
    with pytest.raises(ConnectionClosed):
        await echo_conn.pong(b'B')


async def test_client_default_close(echo_conn: WebSocketConnection) -> None:
    async with echo_conn:
        assert not echo_conn.closed
    assert isinstance(echo_conn.closed, CloseReason)
    assert echo_conn.closed.code == 1000
    assert echo_conn.closed.reason is None
    assert repr(echo_conn.closed) == 'CloseReason<code=1000, ' \
        'name=NORMAL_CLOSURE, reason=None>'


async def test_client_nondefault_close(echo_conn: WebSocketConnection) -> None:
    async with echo_conn:
        assert not echo_conn.closed
        await echo_conn.aclose(code=1001, reason='test reason')
    assert isinstance(echo_conn.closed, CloseReason)
    assert echo_conn.closed.code == 1001
    assert echo_conn.closed.reason == 'test reason'


async def test_wrap_client_stream(nursery: trio.Nursery) -> None:
    listener = MemoryListener()
    server = WebSocketServer(echo_request_handler, [listener])  # type: ignore[list-item]
    await nursery.start(server.run)
    stream = await listener.connect()
    conn = await wrap_client_stream(
        nursery,
        stream,  # type: ignore[arg-type]
        HOST, RESOURCE)
    async with conn:
        assert not conn.closed
        await conn.send_message('Hello from client!')
        msg = await conn.get_message()
        assert msg == 'Hello from client!'
        assert isinstance(conn.local, str)
        assert conn.local.startswith('StapledStream(')
    assert conn.closed


async def test_wrap_server_stream(nursery: trio.Nursery) -> None:
    async def handler(stream: trio.SocketStream) -> None:
        request = await wrap_server_stream(nursery, stream)
        server_ws = await request.accept()
        async with server_ws:
            assert not server_ws.closed
            msg = await server_ws.get_message()
            assert msg == 'Hello from client!'
        assert server_ws.closed
    serve_fn = partial(trio.serve_tcp, handler, 0, host=HOST)
    raw_listeners = await nursery.start(serve_fn)
    listeners = cast("list[trio.SocketListener]", raw_listeners)
    port = listeners[0].socket.getsockname()[1]
    async with open_websocket(HOST, port, RESOURCE, use_ssl=False) as client:
        await client.send_message('Hello from client!')


@fail_after(TIMEOUT_TEST_MAX_DURATION)
async def test_client_open_timeout(
    nursery: trio.Nursery,
    autojump_clock: trio.testing.MockClock,
) -> None:
    '''
    The client times out waiting for the server to complete the opening
    handshake.
    '''
    async def handler(request: WebSocketRequest) -> None:
        await trio.sleep(FORCE_TIMEOUT)
        await request.accept()
        pytest.fail('Should not reach this line.')

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None))
    assert isinstance(server, WebSocketServer)

    with pytest.raises(ConnectionTimeout):
        async with open_websocket(HOST, server.port, '/', use_ssl=False,
                connect_timeout=TIMEOUT):
            pass


@fail_after(TIMEOUT_TEST_MAX_DURATION)
async def test_client_close_timeout(
    nursery: trio.Nursery,
    autojump_clock: trio.testing.MockClock,
) -> None:
    '''
    This client times out waiting for the server to complete the closing
    handshake.

    To slow down the server's closing handshake, we make sure that its message
    queue size is 0, and the client sends it exactly 1 message. This blocks the
    server's reader so it won't do the closing handshake for at least
    ``FORCE_TIMEOUT`` seconds.
    '''
    async def handler(request: WebSocketRequest) -> None:
        server_ws = await request.accept()
        await trio.sleep(FORCE_TIMEOUT)
        # The next line should raise ConnectionClosed.
        await server_ws.get_message()
        pytest.fail('Should not reach this line.')

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None,
        message_queue_size=0))
    assert isinstance(server, WebSocketServer)

    with pytest.raises(DisconnectionTimeout):
        async with open_websocket(HOST, server.port, RESOURCE, use_ssl=False,
                disconnect_timeout=TIMEOUT) as client_ws:
            await client_ws.send_message('test')


async def test_client_connect_networking_error() -> None:
    with patch('trio_websocket._impl.connect_websocket') as \
            connect_websocket_mock:
        connect_websocket_mock.side_effect = OSError()
        with pytest.raises(HandshakeError):
            async with open_websocket(HOST, 0, '/', use_ssl=False):
                pass


@fail_after(TIMEOUT_TEST_MAX_DURATION)
async def test_server_open_timeout(autojump_clock: trio.testing.MockClock) -> None:
    '''
    The server times out waiting for the client to complete the opening
    handshake.

    Server timeouts don't raise exceptions, because handler tasks are launched
    in an internal nursery and sending exceptions wouldn't be helpful. Instead,
    timed out tasks silently end.
    '''
    async def handler(request: WebSocketRequest) -> None:
        pytest.fail('This handler should not be called.')

    async with trio.open_nursery() as nursery:
        server = await nursery.start(partial(serve_websocket, handler, HOST, 0,
            ssl_context=None, handler_nursery=nursery, connect_timeout=TIMEOUT))
        assert isinstance(server, WebSocketServer)

        old_task_count = len(nursery.child_tasks)
        # This stream is not a WebSocket, so it won't send a handshake:
        await trio.open_tcp_stream(HOST, server.port)
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
async def test_server_close_timeout(autojump_clock: trio.testing.MockClock) -> None:
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
    async def handler(request: WebSocketRequest) -> None:
        ws = await request.accept()
        # Send one message to block the client's reader task:
        await ws.send_message('test')

    async with trio.open_nursery() as outer:
        server = await outer.start(partial(serve_websocket, handler, HOST, 0,
            ssl_context=None, handler_nursery=outer,
            disconnect_timeout=TIMEOUT))
        assert isinstance(server, WebSocketServer)

        old_task_count = len(outer.child_tasks)
        # Spawn client inside an inner nursery so that we can cancel it's reader
        # so that it won't do a closing handshake.
        async with trio.open_nursery() as inner:
            await connect_websocket(inner, HOST, server.port, RESOURCE,
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


async def test_client_does_not_close_handshake(nursery: trio.Nursery) -> None:
    async def handler(request: WebSocketRequest) -> None:
        server_ws = await request.accept()
        with pytest.raises(ConnectionClosed):
            await server_ws.get_message()
    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    assert isinstance(server, WebSocketServer)
    stream = await trio.open_tcp_stream(HOST, server.port)
    client_ws = await wrap_client_stream(nursery, stream, HOST, RESOURCE)
    async with client_ws:
        await stream.aclose()
        with pytest.raises(ConnectionClosed):
            await client_ws.send_message('Hello from client!')


async def test_server_sends_after_close(nursery: trio.Nursery) -> None:
    done = trio.Event()

    async def handler(request: WebSocketRequest) -> None:
        server_ws = await request.accept()
        with pytest.raises(ConnectionClosed):
            while True:
                await server_ws.send_message('Hello from server')
        done.set()

    server = await nursery.start(serve_websocket, handler, HOST, 0, None)
    assert isinstance(server, WebSocketServer)
    stream = await trio.open_tcp_stream(HOST, server.port)
    client_ws = await wrap_client_stream(nursery, stream, HOST, RESOURCE)
    async with client_ws:
        # pump a few messages
        for x in range(2):
            await client_ws.send_message('Hello from client')
        await stream.aclose()
    await done.wait()


async def test_server_does_not_close_handshake(nursery: trio.Nursery) -> None:
    async def handler(stream: trio.SocketStream) -> None:
        request = await wrap_server_stream(nursery, stream)
        server_ws = await request.accept()
        async with server_ws:
            await stream.aclose()
            with pytest.raises(ConnectionClosed):
                await server_ws.send_message('Hello from client!')
    serve_fn = partial(trio.serve_tcp, handler, 0, host=HOST)
    raw_listeners = await nursery.start(serve_fn)
    listeners = cast("list[trio.SocketListener]", raw_listeners)
    port = listeners[0].socket.getsockname()[1]
    async with open_websocket(HOST, port, RESOURCE, use_ssl=False) as client:
        with pytest.raises(ConnectionClosed):
            await client.get_message()


async def test_server_handler_exit(
    nursery: trio.Nursery,
    autojump_clock: trio.testing.MockClock,
) -> None:
    async def handler(request: WebSocketRequest) -> None:
        await request.accept()
        await trio.sleep(1)

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None))
    assert isinstance(server, WebSocketServer)

    # connection should close when server handler exits
    with trio.fail_after(2):
        async with open_websocket(
                HOST, server.port, '/', use_ssl=False) as connection:
            with pytest.raises(ConnectionClosed) as exc_info:
                await connection.get_message()
            exc = exc_info.value
            assert isinstance(exc.reason, CloseReason)
            assert exc.reason.name == 'NORMAL_CLOSURE'


@fail_after(DEFAULT_TEST_MAX_DURATION)
async def test_read_messages_after_remote_close(nursery: trio.Nursery) -> None:
    '''
    When the remote endpoint closes, the local endpoint can still read all
    of the messages sent prior to closing. Any attempt to read beyond that will
    raise ConnectionClosed.

    This test also exercises the configuration of the queue size.
    '''
    server_closed = trio.Event()

    async def handler(request: WebSocketRequest) -> None:
        server = await request.accept()
        async with server:
            await server.send_message('1')
            await server.send_message('2')
        server_closed.set()

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None))
    assert isinstance(server, WebSocketServer)

    # The client needs a message queue of size 2 so that it can buffer both
    # incoming messages without blocking the reader task.
    async with open_websocket(HOST, server.port, '/', use_ssl=False,
            message_queue_size=2) as client:
        await server_closed.wait()
        assert await client.get_message() == '1'
        assert await client.get_message() == '2'
        with pytest.raises(ConnectionClosed):
            await client.get_message()


async def test_no_messages_after_local_close(nursery: trio.Nursery) -> None:
    '''
    If the local endpoint initiates closing, then pending messages are discarded
    and any attempt to read a message will raise ConnectionClosed.
    '''
    client_closed = trio.Event()

    async def handler(request: WebSocketRequest) -> None:
        # The server sends some messages and then closes.
        server = await request.accept()
        async with server:
            await server.send_message('1')
            await server.send_message('2')
            await client_closed.wait()

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None))
    assert isinstance(server, WebSocketServer)

    async with open_websocket(HOST, server.port, '/', use_ssl=False) as client:
        pass
    with pytest.raises(ConnectionClosed):
        await client.get_message()
    client_closed.set()


async def test_cm_exit_with_pending_messages(
    echo_server: WebSocketServer,
    autojump_clock: trio.testing.MockClock,
) -> None:
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
async def test_max_message_size(nursery: trio.Nursery) -> None:
    '''
    Set the client's max message size to 100 bytes. The client can send a
    message larger than 100 bytes, but when it receives a message larger than
    100 bytes, it closes the connection with code 1009.
    '''
    async def handler(request: WebSocketRequest) -> None:
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
    assert isinstance(server, WebSocketServer)

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


async def test_server_close_client_disconnect_race(
    nursery: trio.Nursery,
    autojump_clock: trio.testing.MockClock,
) -> None:
    """server attempts close just as client disconnects (issue #96)"""

    async def handler(request: WebSocketRequest) -> None:
        ws = await request.accept()
        ws._for_testing_peer_closed_connection = trio.Event()
        await ws.send_message('foo')
        await ws._for_testing_peer_closed_connection.wait()
        # with bug, this would raise ConnectionClosed from websocket internal task
        await trio.aclose_forcefully(ws._stream)

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None))
    assert isinstance(server, WebSocketServer)

    connection = await connect_websocket(nursery, HOST, server.port,
                                         RESOURCE, use_ssl=False)
    await connection.get_message()
    await connection.aclose()
    await trio.sleep(.1)


async def test_remote_close_local_message_race(
    nursery: trio.Nursery,
    autojump_clock: trio.testing.MockClock,
) -> None:
    """as remote initiates close, local attempts message (issue #175)

    This exposed multiple problems in the trio-websocket API and implementation:
        * send_message() silently fails if a close is in progress.  This was
            likely an oversight in the API, since send_message() raises `ConnectionClosed`
            only in the already-closed case, yet `ConnectionClosed` is defined to cover
            "in the process of closing".
        * with wsproto >= 1.2.0, LocalProtocolError will be leaked
    """

    async def handler(request: WebSocketRequest) -> None:
        ws = await request.accept()
        await ws.get_message()
        await ws.aclose()

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None))
    assert isinstance(server, WebSocketServer)

    client = await connect_websocket(nursery, HOST, server.port,
                                     RESOURCE, use_ssl=False)
    client._for_testing_peer_closed_connection = trio.Event()
    await client.send_message('foo')
    await client._for_testing_peer_closed_connection.wait()
    with pytest.raises(ConnectionClosed):
        await client.send_message('bar')


async def test_message_after_local_close_race(nursery: trio.Nursery) -> None:
    """test message send during local-initiated close handshake (issue #158)"""

    async def handler(request: WebSocketRequest) -> None:
        await request.accept()
        await trio.sleep_forever()

    server = await nursery.start(
        partial(serve_websocket, handler, HOST, 0, ssl_context=None))
    assert isinstance(server, WebSocketServer)

    client = await connect_websocket(nursery, HOST, server.port,
                                     RESOURCE, use_ssl=False)
    orig_send = client._send
    close_sent = trio.Event()

    async def _send_wrapper(event: Event) -> None:
        if isinstance(event, CloseConnection):
            close_sent.set()
        return await orig_send(event)

    client._send = _send_wrapper  # type: ignore[method-assign]
    assert not client.closed
    nursery.start_soon(client.aclose)
    await close_sent.wait()
    assert client.closed
    with pytest.raises(ConnectionClosed):
        await client.send_message('hello')


@fail_after(DEFAULT_TEST_MAX_DURATION)
async def test_server_tcp_closed_on_close_connection_event(nursery: trio.Nursery) -> None:
    """ensure server closes TCP immediately after receiving CloseConnection"""
    server_stream_closed = trio.Event()

    async def _close_stream_stub() -> None:
        assert not server_stream_closed.is_set()
        server_stream_closed.set()

    async def handle_connection(request: WebSocketRequest) -> None:
        ws = await request.accept()
        ws._close_stream = _close_stream_stub  # type: ignore[method-assign]
        await trio.sleep_forever()

    server = await nursery.start(
        partial(serve_websocket, handle_connection, HOST, 0, ssl_context=None))
    assert isinstance(server, WebSocketServer)
    client = await connect_websocket(nursery, HOST, server.port,
                                     RESOURCE, use_ssl=False)
    # send a CloseConnection event to server but leave client connected
    await client._send(CloseConnection(code=1000))
    await server_stream_closed.wait()


async def test_finalization_dropped_exception(
    echo_server: WebSocketServer,
    autojump_clock: trio.testing.MockClock,
) -> None:
    # Confirm that open_websocket finalization does not contribute to dropped
    # exceptions as described in https://github.com/python-trio/trio/issues/1559.
    with pytest.raises(ValueError):
        with trio.move_on_after(1):
            async with open_websocket(HOST, echo_server.port, RESOURCE, use_ssl=False):
                try:
                    await trio.sleep_forever()
                finally:
                    raise ValueError


async def test_remote_close_rude() -> None:
    """
    Bad ordering:
    1. Remote close
    2. TCP closed
    3. Local confirms
    => no ConnectionClosed raised, client hangs forever
    """
    client_stream, server_stream = memory_stream_pair()

    async def client() -> None:
        client_conn = await wrap_client_stream(
            nursery,
            client_stream,  # type: ignore[arg-type]
            HOST, RESOURCE)
        assert not client_conn.closed
        await client_conn.send_message('Hello from client!')
        with pytest.raises(ConnectionClosed):
            await client_conn.get_message()

    async def server() -> None:
        server_request = await wrap_server_stream(nursery, server_stream)
        server_ws = await server_request.accept()
        assert not server_ws.closed
        msg = await server_ws.get_message()
        assert msg == "Hello from client!"

        # disable pumping so that the CloseConnection arrives at the same time as the stream closure
        server_stream.send_stream.send_all_hook = None
        await server_ws._send(CloseConnection(code=1000, reason=None))
        await server_stream.aclose()

        # pump the messages over
        memory_stream_pump(server_stream.send_stream, client_stream.receive_stream)


    async with trio.open_nursery() as nursery:
        nursery.start_soon(server)
        nursery.start_soon(client)


def test_copy_exceptions() -> None:
    # test that exceptions are copy- and pickleable
    copy.copy(HandshakeError())
    copy.copy(ConnectionTimeout())
    copy.copy(DisconnectionTimeout())
    assert copy.copy(
        ConnectionClosed("foo")  # type: ignore[arg-type]
    ).reason == "foo"  # type: ignore[comparison-overlap]

    rej_copy = copy.copy(ConnectionRejected(404, ((b"a", b"b"),), b"c"))
    assert rej_copy.status_code == 404
    assert rej_copy.headers == ((b"a", b"b"),)
    assert rej_copy.body == b"c"
