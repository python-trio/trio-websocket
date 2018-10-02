import logging

import pytest
from trio_websocket import ConnectionClosed, connect_websocket, \
    connect_websocket_url, open_websocket, open_websocket_url, WebSocketServer
import trio
import trio.hazmat


HOST = 'localhost'
RESOURCE = '/resource'

@pytest.fixture
async def echo_server(nursery):
    ''' A server that reads one message, sends back the same message,
    then closes the connection. '''
    server = WebSocketServer(echo_handler, HOST, 0, ssl_context=None)
    await nursery.start(server.listen)
    yield server


@pytest.fixture
async def echo_conn(echo_server):
    ''' Return a client connection instance that is connected to an echo
    server. '''
    async with open_websocket(HOST, echo_server.port, RESOURCE, use_ssl=False) \
        as conn:
        yield conn


async def echo_handler(conn):
    ''' A connection handler that reads one message, sends back the same
    message, then exits. '''
    try:
        msg = await conn.get_message()
        await conn.send_message(msg)
    except ConnectionClosed:
        pass


async def test_serve(nursery):
    task = trio.hazmat.current_task()
    server = WebSocketServer(echo_handler, HOST, 0, ssl_context=None)
    await nursery.start(server.listen)
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


async def test_serve_handler_nursery(nursery):
    task = trio.hazmat.current_task()
    async with trio.open_nursery() as handler_nursery:
        server = WebSocketServer(echo_handler, HOST, 0, ssl_context=None,
            handler_nursery=handler_nursery)
        await nursery.start(server.listen)
        port = server.port
        # The server nursery begins with one task (server.listen).
        assert len(nursery.child_tasks) == 1
        no_clients_nursery_count = len(task.child_nurseries)
        async with open_websocket(HOST, port, RESOURCE, use_ssl=False) as conn:
            # The handler nursery should have one task in it
            # (conn._reader_task).
            assert len(handler_nursery.child_tasks) == 1


async def test_client_open(echo_server):
    async with open_websocket(HOST, echo_server.port, RESOURCE, use_ssl=False) \
        as conn:
        assert conn.closed is None


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
    assert conn.closed is None


async def test_client_connect_url(echo_server, nursery):
    url = 'ws://{}:{}{}'.format(HOST, echo_server.port, RESOURCE)
    conn = await connect_websocket_url(nursery, url)
    assert conn.closed is None


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

