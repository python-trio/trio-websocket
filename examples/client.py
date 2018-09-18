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
from trio_websocket import open_websocket_url, ConnectionClosed
import yarl


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
    parser.add_argument('url', help='WebSocket URL to connect to')
    return parser.parse_args()


async def main(args):
    ''' Main entry point, returning False in the case of logged error. '''
    if yarl.URL(args.url).scheme == 'wss':
        # Configure SSL context to handle our self-signed certificate. Most
        # clients won't need to do this.
        try:
            ssl_context = ssl.create_default_context()
            ssl_context.load_verify_locations(here / 'fake.ca.pem')
        except FileNotFoundError:
            logging.error('Did not find file "fake.ca.pem". You need to run'
                ' generate-cert.py')
            return False
    else:
        ssl_context = None
    try:
        logging.debug('Connecting to WebSocket…')
        async with open_websocket_url(args.url, ssl_context) as conn:
            logging.debug('Connected!')
            await handle_connection(conn)
        logging.debug('Connection closed')
    except OSError as ose:
        logging.error('Connection attempt failed: %s', ose)
        return False


async def handle_connection(connection):
    ''' Handle the connection. '''
    while True:
        try:
            logger.debug('top of loop')
            await trio.sleep(0.1) # allow time for connection logging
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
                await connection.aclose(code=1000, reason=reason)
                break
            else:
                commands()
        except ConnectionClosed:
            break


if __name__ == '__main__':
    try:
        if not trio.run(main, parse_args()):
            sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print()
