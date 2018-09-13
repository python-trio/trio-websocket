import pytest
from trio_websocket import ConnectionClosed, WebSocketClient, WebSocketServer
import trio


import logging
logging.basicConfig(level=logging.DEBUG)


@pytest.fixture
async def echo_server(nursery):
    async def handler(conn):
        try:
            msg = await conn.get_message()
            await conn.send_message(msg)
        except ConnectionClosed:
            pass
    server = WebSocketServer(handler, 'localhost', 0, ssl_context=None)
    await nursery.start(server.listen)
    yield server


def client_for_server(server):
    ''' Create a client configured to connect to ``server``. '''
    return WebSocketClient('localhost', server.port, 'resource', use_ssl=False)


async def test_client_send_and_receive(echo_server, nursery):
    client = client_for_server(echo_server)
    conn = await client.connect(nursery)
    await conn.send_message('This is a test message.')
    received_msg = await conn.get_message()
    assert received_msg == 'This is a test message.'
    await conn.close()


async def test_client_default_close(echo_server, nursery):
    client = client_for_server(echo_server)
    conn = await client.connect(nursery)
    assert conn.closed is None
    await conn.close()
    assert conn.closed.code == 1000
    assert conn.closed.reason is None


async def test_client_nondefault_close(echo_server, nursery):
    client = client_for_server(echo_server)
    conn = await client.connect(nursery)
    assert conn.closed is None
    await conn.close(code=1001, reason='test reason')
    assert conn.closed.code == 1001
    assert conn.closed.reason == 'test reason'
