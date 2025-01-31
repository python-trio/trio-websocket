'''
This simple WebSocket server responds to text messages by reversing each
message string and sending it back.

It also handles ping/pong automatically and will correctly close down a
connection when the client requests it.

To use SSL/TLS: install the `trustme` package from PyPI and run the
`generate-cert.py` script in this directory.
'''
import argparse
import logging

import trio
from trio_websocket import serve_websocket, ConnectionClosed, WebSocketRequest

BIND_IP = '0.0.0.0'
BIND_PORT = 9000
MAX_MESSAGE_SIZE = 16 * 1024 * 1024
logging.basicConfig()
logger = logging.getLogger('client')
logger.setLevel(logging.INFO)
connection_count = 0


async def main() -> None:
    ''' Main entry point. '''
    logger.info('Starting websocket server on ws://%s:%d', BIND_IP, BIND_PORT)
    await serve_websocket(handler, BIND_IP, BIND_PORT, ssl_context=None,
                          max_message_size=MAX_MESSAGE_SIZE)


async def handler(request: WebSocketRequest) -> None:
    ''' Reverse incoming websocket messages and send them back. '''
    global connection_count  # pylint: disable=global-statement
    connection_count += 1
    logger.info('Connection #%d', connection_count)
    ws = await request.accept()
    while True:
        try:
            message = await ws.get_message()
            await ws.send_message(message)
        except ConnectionClosed:
            break
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception('  runtime exception handling connection #%d', connection_count)


def parse_args() -> argparse.Namespace:
    ''' Parse command line arguments. '''
    parser = argparse.ArgumentParser(description='Autobahn server for'
        ' trio-websocket')
    parser.add_argument('-d', '--debug', action='store_true',
        help='WebSocket URL for server')
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    if args.debug:
        logging.getLogger('trio-websocket').setLevel(logging.DEBUG)
    trio.run(main)
