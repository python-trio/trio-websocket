[![PyPI](https://img.shields.io/pypi/v/trio-websocket.svg?style=flat-square)](https://pypi.org/project/trio-websocket/)
![Python Versions](https://img.shields.io/pypi/pyversions/trio-websocket.svg?style=flat-square)
![MIT License](https://img.shields.io/github/license/HyperionGray/trio-websocket.svg?style=flat-square)
[![Build Status](https://img.shields.io/travis/HyperionGray/trio-websocket.svg?style=flat-square)](https://travis-ci.org/HyperionGray/trio-websocket)
[![Coverage](https://img.shields.io/coveralls/github/HyperionGray/trio-websocket.svg?style=flat-square)](https://coveralls.io/github/HyperionGray/trio-websocket?branch=master)

# Trio WebSocket

This project implements [the WebSocket
protocol](https://tools.ietf.org/html/rfc6455). It is based on the [wsproto
project](https://wsproto.readthedocs.io/en/latest/), which is a [Sans-IO](https://sans-io.readthedocs.io/) state machine that implements the majority of
the WebSocket protocol, including framing, codecs, and events. This library
implements the I/O using [Trio](https://trio.readthedocs.io/en/latest/).

## Installation

`trio-websocket` requires Python v3.5 or greater. To install from PyPI:

    pip install trio-websocket

If you want to help develop `trio-websocket`, clone [the
repository](https://github.com/hyperiongray/trio-websocket) and run this command
from the repository root:

    pip install --editable .[dev]

## Sample client

The following example demonstrates opening a WebSocket by URL. The connection
may also be opened with `open_websocket(…)`, which takes a host, port, and
resource as arguments.

```python
import trio
from trio_websocket import open_websocket_url


async def main():
    try:
        async with open_websocket_url('ws://localhost/foo') as ws:
            await ws.send_message('hello world!')
    except OSError as ose:
        logging.error('Connection attempt failed: %s', ose)

trio.run(main)
```

A more detailed example is in `examples/client.py`. **Note:** if you want to run
this example client with SSL, you'll need to install the `trustme` module from
PyPI (installed automtically if you used the `[dev]` extras when installing
`trio-websocket`) and then generate a self-signed certificate by running
`example/generate-cert.py`.

## Sample server

A WebSocket server requires a bind address, a port, and a coroutine to handle
incoming connections. This example demonstrates an "echo server" that replies
to each incoming message with an identical outgoing message.

```python
import trio
from trio_websocket import serve_websocket, ConnectionClosed

async def echo_server(request):
    ws = await request.accept()
    while True:
        try:
            message = await ws.get_message()
            await ws.send_message(message)
        except ConnectionClosed:
            break

async def main():
    await serve_websocket(echo_server, '127.0.0.1', 8000, ssl_context=None)

trio.run(main)
```

A longer example is in `examples/server.py`. **See the note above about using
SSL with the example client.**

## Heartbeat recipe

If you wish to keep a connection open for long periods of time but do not need
to send messages frequently, then a heartbeat holds the connection open and also
detects when the connection drops unexpectedly. The following recipe
demonstrates how to implement a connection heartbeat using WebSocket's ping/pong
feature.

```python
async def heartbeat(ws, timeout, interval):
    '''
    Send periodic pings on WebSocket ``ws``.

    Wait up to ``timeout`` seconds to send a ping and receive a pong. Raises
    ``TooSlowError`` if the timeout is exceeded. If a pong is received, then
    wait ``interval`` seconds before sending the next ping.

    This function runs until cancelled.

    :param ws: A WebSocket to send heartbeat pings on.
    :param float timeout: Timeout in seconds.
    :param float interval: Interval between receiving pong and sending next
        ping, in seconds.
    :raises: ``ConnectionClosed`` if ``ws`` is closed.
    :raises: ``TooSlowError`` if the timeout expires.
    :returns: This function runs until cancelled.
    '''
    while True:
        with trio.fail_after(timeout):
            await ws.ping()
        await trio.sleep(interval)

async def main():
    async with open_websocket_url('ws://localhost/foo') as ws:
        async with trio.open_nursery() as nursery:
            nursery.start_soon(heartbeat, ws, 5, 1)
            # Your application code goes here:
            pass

trio.run(main)
```

Note that the `ping()` method waits until it receives a pong frame, so it
ensures that the remote endpoint is still responsive. If the connection is
dropped unexpectedly or takes too long to respond, then `heartbeat()` will raise
an exception that will cancel the nursery. You may wish to implement additional
logic to automatically reconnect.

A heartbeat feature can be enabled in the example client with the
``--heartbeat`` flag.

**Note that the WebSocket RFC does not require a WebSocket to send a pong for each
ping:**

> If an endpoint receives a Ping frame and has not yet sent Pong frame(s) in
> response to previous Ping frame(s), the endpoint MAY elect to send a Pong
> frame for only the most recently processed Ping frame.

Therefore, if you have multiple pings in flight at the same time, you may not
get an equal number of pongs in response. The simplest strategy for dealing with
this is to only have one ping in flight at a time, as seen in the example above.
As an alternative, you can send a `bytes` payload with each ping. The server
will return the payload with the pong:

```python
await ws.ping(b'my payload')
pong == await ws.wait_pong()
assert pong == b'my payload'
```

You may want to embed a nonce or counter in the payload in order to correlate
pong events to the pings you have sent.

## Unit Tests

Unit tests are written in the pytest style. You must install the development
dependencies as described in the installation section above. The
``--cov=trio_websocket`` flag turns on code coverage.

    $ pytest --cov=trio_websocket
    === test session starts ===
    platform linux -- Python 3.6.6, pytest-3.8.0, py-1.6.0, pluggy-0.7.1
    rootdir: /home/mhaase/code/trio-websocket, inifile: pytest.ini
    plugins: trio-0.5.0, cov-2.6.0
    collected 21 items

    tests/test_connection.py ..................... [100%]

    --- coverage: platform linux, python 3.6.6-final-0 ---
    Name                         Stmts   Miss  Cover
    ------------------------------------------------
    trio_websocket/__init__.py     297     40    87%
    trio_websocket/_channel.py     140     52    63%
    trio_websocket/version.py        1      0   100%
    ------------------------------------------------
    TOTAL                          438     92    79%

    === 21 passed in 0.54 seconds ===

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

## Release Process

* Remove `-dev` suffix from `version.py`.
* Commit and push version change.
* Create and push tag, e.g. `git tag 1.0.0 && git push origin 1.0.0`.
* Clean build directory: `rm -fr dist`
* Build package: `python setup.py sdist`
* Upload to PyPI: `twine upload dist/*`
* Increment version and add `-dev` suffix.
* Commit and push version change.
