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
import sys

import trio
from trio_websocket import WebSocketServer, ConnectionClosed


BIND_IP = '0.0.0.0'
BIND_PORT = 9000
logging.basicConfig()
logger = logging.getLogger('client')
logger.setLevel(logging.INFO)
connection_count = 0


async def main():
    ''' Main entry point. '''
    logger.info('Starting websocket server on ws://%s:%d', BIND_IP, BIND_PORT)
    server = WebSocketServer(handler, BIND_IP, BIND_PORT, ssl_context=None)
    await server.listen()


async def handler(websocket):
    ''' Reverse incoming websocket messages and send them back. '''
    global connection_count
    connection_count += 1
    logger.info('Connection #%d', connection_count)
    while True:
        try:
            message = await websocket.get_message()
            await websocket.send_message(message)
        except ConnectionClosed:
            break


def parse_args():
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
