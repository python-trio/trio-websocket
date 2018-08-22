'''
This interactive WebSocket client allows the user to send frames to a WebSocket
server, including text message, ping, and close frames.
'''
import logging
import sys

import trio
from trio_websocket import WebSocketClient, WebSocketConnectionClosed


logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger()


def commands():
    ''' Print the supported commands. '''
    print('Commands: ')
    print('send <MESSAGE>   -> send message')
    print('ping <PAYLOAD>   -> send ping with payload')
    print('close [<REASON>] -> politely close connection with optional reason')
    print('[ctrl+D]         -> rudely close connection')
    print()


async def main(host, port, resource):
    ''' Main entry point. '''
    print('Connecting to websocket server {}:{}/{}…'.format(
        host, port, resource))
    print('Nursery open')
    async with trio.open_nursery() as nursery:
        client = WebSocketClient(host, port, resource)
        connection = await client.connect(nursery)
        print('Connected!')
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
            except (EOFError, KeyboardInterrupt):
                print('\nClosing rudely!')
                sys.exit(1)
            except WebSocketConnectionClosed:
                print('Connection closed')
                break
    print('Nursery closed')


if __name__ == '__main__':
    if len(sys.argv) != 4:
        print('Usage: {} <HOST> <PORT> <RESOURCE>'.format(sys.argv[0]))
        sys.exit(1)
    trio.run(main, sys.argv[1], int(sys.argv[2]), sys.argv[3])
