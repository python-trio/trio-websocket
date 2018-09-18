# Trio WebSockets

This project implements WebSockets [for
Trio](https://trio.readthedocs.io/en/latest/) and is based on the Sans-I/O
[wsproto project](https://wsproto.readthedocs.io/en/latest/).

## Installation

To install from PyPI:

    pip install trio-websocket

If you want to help develop `trio-websocket`, clone [the
repository](https://github.com/hyperiongray/trio-websocket) and run this command
from the repository root:

    pip install --editable .[dev]

## Sample client

The following example demonstrates opening a WebSocket by URL. The connection
may also be opened with `open_websocket(…)`, which takes a host, port, and
resource as arguments.

    import trio
    from trio_websocket import open_websocket_url


    async def main():
        try:
            async with open_websocket_url('ws://localhost/foo') as conn:
                await conn.send_message('hello world!')
        except OSError as ose:
            logging.error('Connection attempt failed: %s', ose)
            return

    trio.run(main)

A more detailed example is in `examples/client.py`. **Note:** if you want to run
this example client with SSL, you'll need to install the `trustme` module from
PyPI (installed automtically if you used the `[dev]` extras when installing
`trio-websocket`) and then generate a self-signed certificate by running
`example/generate-cert.py`.

## Sample server

A WebSocket server requires a bind address, a port, and a coroutine to handle
incoming connections. This example demonstrates an "echo server" that replies
to each incoming message with an identical outgoing message.

    import trio
    from trio_websocket import WebSocketServer, ConnectionClosed

    async def main():
        server = WebSocketServer(echo_server, '127.0.0.1', 8000, ssl_context=None)
        await server.listen()

    async def echo_server(websocket):
        while True:
            try:
                message = await websocket.get_message()
                await websocket.send_message(message)
            except ConnectionClosed:
                break

    trio.run(main)

A longer example is in `examples/server.py`. **See the note above about using
SSL with the example client.**

## Integration Testing with Autobahn

The Autobahn Test Suite contains over 500 integration tests for WebSocket
servers and clients. These test suites are contained in a
[Docker](https://www.docker.com/) container. You will need to install Docker
before you can run these integration tests.

### Client Tests

To test the client, you will need two terminal windows. In the first terminal,
run the following commands:

    $ cd autobahn
    $ docker run -it --rm \
          -v "${PWD}/config:/config" \
          -v "${PWD}/reports:/reports" \
          -p 9001:9001 \
          --name autobahn \
          crossbario/autobahn-testsuite

The first time you run this command, Docker will download some files, which may
take a few minutes. When the test suite is ready, it will display:

    Autobahn WebSocket 0.8.0/0.10.9 Fuzzing Server (Port 9001)
    Ok, will run 249 test cases for any clients connecting

Now in the second terminal, run the Autobahn client:

    $ cd autobahn
    $ python client.py ws://localhost:9001
    INFO:client:Case count=249
    INFO:client:Running test case 1 of 249
    INFO:client:Running test case 2 of 249
    INFO:client:Running test case 3 of 249
    INFO:client:Running test case 4 of 249
    INFO:client:Running test case 5 of 249
    <snip>

When the client finishes running, an HTML report is published to the
`autobahn/reports/clients` directory. If any tests fail, you can debug
individual tests by specifying the integer test case ID (not the dotted test
case ID), e.g. to run test case #29:

    $ python client.py ws://localhost:9001 29

### Server Tests

Once again, you will need two terminal windows. In the first terminal, run:

    $ cd autobahn
    $ python server.py

In the second terminal, you will run the Docker image.

    $ cd autobahn
    $ docker run -it --rm \
          -v "${PWD}/config:/config" \
          -v "${PWD}/reports:/reports" \
          --name autobahn \
          crossbario/autobahn-testsuite \
          /usr/local/bin/wstest --mode fuzzingclient --spec /config/fuzzingclient.json

If a test fails, `server.py` does not support the same `debug_cases` argument as
`client.py`, but you can modify `fuzzingclient.json` to specify a subset of
cases to run, e.g. `3.*` to run all test cases in section 3.
