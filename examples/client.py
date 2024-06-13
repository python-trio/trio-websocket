"""
This interactive WebSocket client allows the user to send frames to a WebSocket
server, including text message, ping, and close frames.

To use SSL/TLS: install the `trustme` package from PyPI and run the
`generate-cert.py` script in this directory.
"""

import argparse
import logging
import pathlib
import ssl
import sys
import urllib.parse

import trio
from trio_websocket import open_websocket_url, ConnectionClosed, HandshakeError


logging.basicConfig(level=logging.DEBUG)
here = pathlib.Path(__file__).parent


def commands():
    """Print the supported commands."""
    print("Commands: ")
    print("send <MESSAGE>   -> send message")
    print("ping <PAYLOAD>   -> send ping with payload")
    print("close [<REASON>] -> politely close connection with optional reason")
    print()


def parse_args():
    """Parse command line arguments."""
    parser = argparse.ArgumentParser(description="Example trio-websocket client")
    parser.add_argument(
        "--heartbeat", action="store_true", help="Create a heartbeat task"
    )
    parser.add_argument("url", help="WebSocket URL to connect to")
    return parser.parse_args()


async def main(args):
    """Main entry point, returning False in the case of logged error."""
    if urllib.parse.urlsplit(args.url).scheme == "wss":
        # Configure SSL context to handle our self-signed certificate. Most
        # clients won't need to do this.
        try:
            ssl_context = ssl.create_default_context()
            ssl_context.load_verify_locations(here / "fake.ca.pem")
        except FileNotFoundError:
            logging.error(
                'Did not find file "fake.ca.pem". You need to run generate-cert.py'
            )
            return False
    else:
        ssl_context = None
    try:
        logging.debug("Connecting to WebSocket…")
        async with open_websocket_url(args.url, ssl_context) as conn:
            await handle_connection(conn, args.heartbeat)
    except HandshakeError as e:
        logging.error("Connection attempt failed: %s", e)
        return False


async def handle_connection(ws, use_heartbeat):
    """Handle the connection."""
    logging.debug("Connected!")
    try:
        async with trio.open_nursery() as nursery:
            if use_heartbeat:
                nursery.start_soon(heartbeat, ws, 1, 15)
            nursery.start_soon(get_commands, ws)
            nursery.start_soon(get_messages, ws)
    except ConnectionClosed as cc:
        reason = "<no reason>" if cc.reason.reason is None else f'"{cc.reason.reason}"'
        print(f"Closed: {cc.reason.code}/{cc.reason.name} {reason}")


async def heartbeat(ws, timeout, interval):
    """
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
    """
    while True:
        with trio.fail_after(timeout):
            await ws.ping()
        await trio.sleep(interval)


async def get_commands(ws):
    """In a loop: get a command from the user and execute it."""
    while True:
        cmd = await trio.to_thread.run_sync(input, "cmd> ", cancellable=True)
        if cmd.startswith("ping"):
            payload = cmd[5:].encode("utf8") or None
            await ws.ping(payload)
        elif cmd.startswith("send"):
            message = cmd[5:] or None
            if message is None:
                logging.error('The "send" command requires a message.')
            else:
                await ws.send_message(message)
        elif cmd.startswith("close"):
            reason = cmd[6:] or None
            await ws.aclose(code=1000, reason=reason)
            break
        else:
            commands()
        # Allow time to receive response and log print logs:
        await trio.sleep(0.25)


async def get_messages(ws):
    """In a loop: get a WebSocket message and print it out."""
    while True:
        message = await ws.get_message()
        print(f"message: {message}")


if __name__ == "__main__":
    try:
        if not trio.run(main, parse_args()):
            sys.exit(1)
    except (KeyboardInterrupt, EOFError):
        print()
