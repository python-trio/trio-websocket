# Trio WebSocket

This library implements [the WebSocket
protocol](https://tools.ietf.org/html/rfc6455), striving for safety,
correctness, and ergonomics. It is based on the [wsproto
project](https://wsproto.readthedocs.io/en/latest/), which is a
[Sans-IO](https://sans-io.readthedocs.io/) state machine that implements the
majority of the WebSocket protocol, including framing, codecs, and events. This
library handles I/O using [the Trio
framework](https://trio.readthedocs.io/en/latest/). This library passes the
[Autobahn Test Suite](https://github.com/crossbario/autobahn-testsuite).

This README contains a brief introduction to the project. Full documentation [is
available here](https://trio-websocket.readthedocs.io).

[![PyPI](https://img.shields.io/pypi/v/trio-websocket.svg?style=flat-square)](https://pypi.org/project/trio-websocket/)
![Python Versions](https://img.shields.io/pypi/pyversions/trio-websocket.svg?style=flat-square)
![MIT License](https://img.shields.io/github/license/HyperionGray/trio-websocket.svg?style=flat-square)
[![Build Status](https://img.shields.io/travis/com/HyperionGray/trio-websocket.svg?style=flat-square&branch=master)](https://travis-ci.com/HyperionGray/trio-websocket)
[![Coverage](https://img.shields.io/coveralls/github/HyperionGray/trio-websocket.svg?style=flat-square)](https://coveralls.io/github/HyperionGray/trio-websocket?branch=master)
[![Read the Docs](https://img.shields.io/readthedocs/trio-websocket.svg)](https://trio-websocket.readthedocs.io)

## Installation

This library requires Python 3.5 or greater. To install from PyPI:

    pip install trio-websocket

## Client Example

This example demonstrates how to open a WebSocket URL:

```python
import trio
from sys import stderr
from trio_websocket import open_websocket_url


async def main():
    try:
        async with open_websocket_url('wss://echo.websocket.org') as ws:
            await ws.send_message('hello world!')
            message = await ws.get_message()
            print('Received message: %s' % message)
    except OSError as ose:
        print('Connection attempt failed: %s' % ose, file=stderr)

trio.run(main)
```

The WebSocket context manager connects automatically before entering the block
and disconnects automatically before exiting the block. The full API offers a
lot of flexibility and additional options.

## Server Example

A WebSocket server requires a bind address, a port, and a coroutine to handle
incoming connections. This example demonstrates an "echo server" that replies to
each incoming message with an identical outgoing message.

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

The server's handler ``echo_server(…)`` receives a connection request object.
This object can be used to inspect the client's request and modify the
handshake, then it can be exchanged for an actual WebSocket object ``ws``.
Again, the full API offers a lot of flexibility and additional options.
