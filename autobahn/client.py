'''
This test client runs against the Autobahn test server. It is based on the
test_client.py in wsproto.
'''
import argparse
import logging

import trio
from trio_websocket import open_websocket_url, ConnectionClosed
from yarl import URL


AGENT = 'trio-websocket'
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('client')


async def get_case_count(url):
    url = URL(url).with_path('/getCaseCount')
    async with open_websocket_url(url) as conn:
        case_count = await conn.get_message()
        logger.info('Case count=%s', case_count)
    return int(case_count)


async def run_case(url, case):
    url = URL(url).with_path('/runCase').with_query(case=case, agent=AGENT)
    logger.info('run_case %s', url)
    try:
        async with open_websocket_url(url) as conn:
            while True:
                data = await conn.get_message()
                await conn.send_message(data)
    except ConnectionClosed:
        pass


async def update_reports(url):
    url = URL(url).with_path('/updateReports').with_query(agent=AGENT)
    async with open_websocket_url(url) as conn:
        # This command runs as soon as we connect to it, so we don't need to
        # send any messages.
        pass


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
