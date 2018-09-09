'''
This interactive WebSocket client allows the user to send frames to a WebSocket
server, including text message, ping, and close frames.

To use SSL/TLS: install the `trustme` package from PyPI and run the
`generate-cert.py` script in this directory.
'''
import argparse
import logging
import pathlib
import ssl
import sys

import trio
from trio_websocket import WebSocketClient, ConnectionClosed


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()
here = pathlib.Path(__file__).parent


def commands():
    ''' Print the supported commands. '''
    print('Commands: ')
    print('send <MESSAGE>   -> send message')
    print('ping <PAYLOAD>   -> send ping with payload')
    print('close [<REASON>] -> politely close connection with optional reason')
    print('[ctrl+D]         -> rudely close connection')
    print()


def parse_args():
    ''' Parse command line arguments. '''
    parser = argparse.ArgumentParser(description='Example trio-websocket client')
    parser.add_argument('--ssl', action='store_true', help='Use SSL')
    parser.add_argument('host', help='Host to connect to')
    parser.add_argument('port', type=int, help='Port to connect to')
    parser.add_argument('resource', help='Path to access on server (without'
        ' leading slash)')
    return parser.parse_args()


async def main(args):
    ''' Main entry point, returning False in the case of logged error. '''
    async with trio.open_nursery() as nursery:
        logging.info('Connecting to WebSocket…')
        ssl_context = ssl.create_default_context()
        if args.ssl:
            try:
                ssl_context.load_verify_locations(here / 'fake.ca.pem')
            except FileNotFoundError:
                logging.error('Did not find file "fake.ca.pem". You need to run'
                    ' generate-cert.py')
                return False
            client = WebSocketClient(args.host, args.port, args.resource,
                use_ssl=ssl_context)
        else:
            client = WebSocketClient(args.host, args.port, args.resource,
                use_ssl=False)
        try:
            connection = await client.connect(nursery)
        except OSError as ose:
            logging.error('Connection attempt failed: %s', ose)
            return False
        logging.info('Connected!')
        while True:
            await trio.sleep(0.1) # allow time for connection logging
            try:
                cmd = await trio.run_sync_in_worker_thread(input, 'cmd> ',
                    cancellable=True)
                if cmd.startswith('ping '):
                    await connection.ping(cmd[5:].encode('utf8'))
                elif cmd.startswith('send '):
                    await connection.send_message(cmd[5:])
                    message = await connection.get_message()
                    print('response> {}'.format(message))
                elif cmd.startswith('close'):
                    try:
                        reason = cmd[6:]
                    except IndexError:
                        reason = None
                    await connection.close(reason=reason)
                    break
                else:
                    commands()
            except ConnectionClosed:
                logging.info('Connection closed')
                break


if __name__ == '__main__':
    try:
        if not trio.run(main, parse_args()):
            sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print()
