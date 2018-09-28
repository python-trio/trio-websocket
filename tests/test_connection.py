import pytest
from trio_websocket import ConnectionClosed, open_websocket, \
    open_websocket_url, WebSocketServer
import trio


HOST = 'localhost'
RESOURCE = '/resource'


@pytest.fixture
async def echo_server(nursery):
    ''' An echo server reads one message, sends back the same message,
    then exits. '''
    async def handler(conn):
        try:
            msg = await conn.get_message()
            await conn.send_message(msg)
        except ConnectionClosed:
            pass
    server = WebSocketServer(handler, HOST, 0, ssl_context=None)
    await nursery.start(server.listen)
    yield server


@pytest.fixture
async def echo_conn(echo_server):
    ''' Return a client connection instance that is connected to an echo
    server. '''
    async with open_websocket(HOST, echo_server.port, RESOURCE, use_ssl=False) \
        as conn:
        yield conn


async def null_handler(stream):
    ''' A connection handler that doesn't do anything. This is used for tests
    where a connection is never made. '''
    pass


async def test_client_open_url(echo_server):
    url = 'ws://{}:{}{}?foo=bar'.format(HOST, echo_server.port, RESOURCE)
    async with open_websocket_url(url) as conn:
        assert conn.path == RESOURCE + '?foo=bar'


async def test_client_open_in_nursery(echo_server, nursery):
    url = 'ws://{}:{}{}'.format(HOST, echo_server.port, RESOURCE)
    async with open_websocket_url(url, nursery=nursery) as conn:
        assert len(nursery.child_tasks) == 1


async def test_client_open_invalid_url(echo_server):
    with pytest.raises(ValueError):
        async with open_websocket_url('http://foo.com/bar') as conn:
            pass


async def test_client_send_and_receive(echo_conn):
    async with echo_conn:
        await echo_conn.send_message('This is a test message.')
        received_msg = await echo_conn.get_message()
        assert received_msg == 'This is a test message.'


async def test_client_default_close(echo_conn):
    async with echo_conn:
        assert echo_conn.closed is None
    assert echo_conn.closed.code == 1000
    assert echo_conn.closed.reason is None


async def test_client_nondefault_close(echo_conn):
    async with echo_conn:
        assert echo_conn.closed is None
        await echo_conn.aclose(code=1001, reason='test reason')
    assert echo_conn.closed.code == 1001
    assert echo_conn.closed.reason == 'test reason'


async def test_serve_in_internal_nursery(nursery):
    server = WebSocketServer(null_handler, HOST, 0, ssl_context=None)
    await nursery.start(server.listen)
    # The nursery has one child task (server.listen) and one sub-nursery.
    assert len(nursery.child_tasks) == 1


async def test_serve_in_specified_nursery(nursery):
    server = WebSocketServer(null_handler, HOST, 0, ssl_context=None)
    await nursery.start(server.listen_in_nursery, nursery)
    # The nursery has two child tasks (server.listen_in_nursery and the listener
    # itself) and no sub-nursery.
    assert len(nursery.child_tasks) == 2
