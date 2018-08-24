'''
This test client runs against the Autobahn test server. It is based on the
test_client.py in wsproto.
'''
import argparse
import logging
from urllib.parse import urlparse

import trio
from trio_websocket import WebSocketClient, ConnectionClosed


AGENT = 'trio-websocket'
logging.basicConfig()
logger = logging.getLogger('client')
logger.setLevel(logging.INFO)


async def get_case_count(url):
    uri = urlparse(url + '/getCaseCount')
    async with trio.open_nursery() as nursery:
        client = WebSocketClient(uri.hostname, uri.port, uri.path,
            use_ssl=False)
        conn = await client.connect(nursery)
        case_count = await conn.get_message()
        logger.info('Case count=%s', case_count)
    return int(case_count)


async def run_case(url, case):
    uri = urlparse(url + '/runCase?case={}&agent={}'.format(
        case, AGENT))
    async with trio.open_nursery() as nursery:
        client = WebSocketClient(uri.hostname, uri.port, '{}?{}'.format(uri.path, uri.query),
            use_ssl=False)
        conn = await client.connect(nursery)
        try:
            while True:
                data = await conn.get_message()
                await conn.send_message(data)
        except ConnectionClosed:
            pass

async def update_reports(url):
    uri = urlparse(url + '/updateReports?agent={}'.format(AGENT))
    async with trio.open_nursery() as nursery:
        client = WebSocketClient(uri.hostname, uri.port, '{}?{}'.format(uri.path, uri.query),
            use_ssl=False)
        conn = await client.connect(nursery)


async def run_tests(args):
    if args.debug_cases:
        # Don't fetch case count when debugging a subset of test cases. It adds
        # noise to the debug logging.
        case_count = None
        test_cases = args.debug_cases
        # Automatically enable debug logging when running individual test cases.
        logging.getLogger('trio-websocket').setLevel(logging.DEBUG)
    else:
        case_count = await get_case_count(args.url)
        test_cases = range(1, case_count + 1)
    for case in test_cases:
        if case_count:
            logger.info("Running test case %d of %d", case, case_count)
        else:
            logger.info("Debugging test case %d", case)
        await run_case(args.url, case)
    if not args.debug_cases:
        # Don't update report when debugging a single test case. It adds noise
        # to the debug logging.
        logger.info('Updating report')
        await update_reports(args.url)


def parse_args():
    ''' Parse command line arguments. '''
    parser = argparse.ArgumentParser(description='Autobahn client for'
        ' trio-websocket')
    parser.add_argument('url', help='WebSocket URL for server')
    parser.add_argument('debug_cases', type=int, nargs='*', help='Run'
        ' individual test cases with debug logging (optional)')
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    trio.run(run_tests, args)
