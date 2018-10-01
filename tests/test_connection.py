import logging

import pytest
from trio_websocket import ConnectionClosed, connect_websocket, \
    connect_websocket_url, open_websocket, open_websocket_url, WebSocketServer
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
