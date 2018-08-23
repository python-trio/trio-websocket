'''
This simple WebSocket server responds to text messages by reversing each
message string and sending it back.

It also handles ping/pong automatically and will correctly close down a
connection when the client requests it.
'''
import logging
import sys

import trio
from trio_websocket import WebSocketServer, ConnectionClosed


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()


async def main(bind_ip, port):
    ''' Main entry point. '''
    print('Starting websocket server...')
    server = WebSocketServer(handler, sys.argv[1], int(sys.argv[2]))
    await server.listen()


async def handler(websocket):
    ''' Reverse incoming websocket messages and send them back. '''
    print('Handler starting')
    while True:
        try:
            message = await websocket.get_message()
            await websocket.send_message(message[::-1])
        except ConnectionClosed:
            print('Connection closed')
            break
    print('Handler exiting')


if __name__ == '__main__':
    if len(sys.argv) != 3:
        print('Usage: {} <BIND_IP> <PORT>'.format(sys.argv[0]))
        sys.exit(1)
    trio.run(main, sys.argv[1], sys.argv[2])
