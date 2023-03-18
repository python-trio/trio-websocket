'''
This test client runs against the Autobahn test server. It is based on the
test_client.py in wsproto.
'''
import argparse
import json
import logging
import sys

import trio
from trio_websocket import open_websocket_url, ConnectionClosed


AGENT = 'trio-websocket'
MAX_MESSAGE_SIZE = 16 * 1024 * 1024
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger('client')


async def get_case_count(url):
    url = url + '/getCaseCount'
    async with open_websocket_url(url) as conn:
        case_count = await conn.get_message()
        logger.info('Case count=%s', case_count)
    return int(case_count)


async def get_case_info(url, case):
    url = f'{url}/getCaseInfo?case={case}'
    async with open_websocket_url(url) as conn:
        return json.loads(await conn.get_message())


async def run_case(url, case):
    url = f'{url}/runCase?case={case}&agent={AGENT}'
    try:
        async with open_websocket_url(url, max_message_size=MAX_MESSAGE_SIZE) as conn:
            while True:
                data = await conn.get_message()
                await conn.send_message(data)
    except ConnectionClosed:
        pass


async def update_reports(url):
    url = f'{url}/updateReports?agent={AGENT}'
    async with open_websocket_url(url) as conn:
        # This command runs as soon as we connect to it, so we don't need to
        # send any messages.
        pass


async def run_tests(args):
    logger = logging.getLogger('trio-websocket')
    if args.debug_cases:
        # Don't fetch case count when debugging a subset of test cases. It adds
        # noise to the debug logging.
        case_count = None
        test_cases = args.debug_cases
    else:
        case_count = await get_case_count(args.url)
        test_cases = list(range(1, case_count + 1))
    exception_cases = []
    for case in test_cases:
        case_id = (await get_case_info(args.url, case))['id']
        if case_count:
            logger.info("Running test case %s (%d of %d)", case_id, case, case_count)
        else:
            logger.info("Debugging test case %s (%d)", case_id, case)
            logger.setLevel(logging.DEBUG)
        try:
            await run_case(args.url, case)
        except Exception:  # pylint: disable=broad-exception-caught
            logger.exception('  runtime exception during test case %s (%d)', case_id, case)
            exception_cases.append(case_id)
        logger.setLevel(logging.INFO)
    logger.info('Updating report')
    await update_reports(args.url)
    if exception_cases:
        logger.error('Runtime exception in %d of %d test cases: %s',
                     len(exception_cases), len(test_cases), exception_cases)
        sys.exit(1)


def parse_args():
    ''' Parse command line arguments. '''
    parser = argparse.ArgumentParser(description='Autobahn client for'
        ' trio-websocket')
    parser.add_argument('url', help='WebSocket URL for server')
    # TODO: accept case ID's rather than indices
    parser.add_argument('debug_cases', type=int, nargs='*', help='Run'
        ' individual test cases with debug logging (optional)')
    return parser.parse_args()


if __name__ == '__main__':
    args = parse_args()
    trio.run(run_tests, args)
