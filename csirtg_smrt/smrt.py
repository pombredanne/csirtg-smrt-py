#!/usr/bin/env python

import logging
import os.path
import textwrap
from argparse import ArgumentParser
from argparse import RawDescriptionHelpFormatter
from random import randint
from time import sleep
from pprint import pprint
import traceback
import sys
import select

import csirtg_smrt.parser
from csirtg_smrt.archiver import Archiver
import csirtg_smrt.client
from csirtg_smrt.constants import REMOTE_ADDR, SMRT_RULES_PATH, SMRT_CACHE, CONFIG_PATH, RUNTIME_PATH
from csirtg_smrt.rule import Rule
from csirtg_smrt.fetcher import Fetcher
from csirtg_smrt.utils import setup_logging, get_argument_parser, load_plugin, setup_signals, read_config, \
    setup_runtime_path
from csirtg_smrt.exceptions import AuthError, TimeoutError
from csirtg_indicator.format import FORMATS

PARSER_DEFAULT = "pattern"
TOKEN = os.environ.get('CSIRTG_TOKEN', None)
TOKEN = os.environ.get('CSIRTG_SMRT_TOKEN', TOKEN)
ARCHIVE_PATH = os.environ.get('CSIRTG_SMRT_ARCHIVE_PATH', RUNTIME_PATH)
ARCHIVE_PATH = os.path.join(ARCHIVE_PATH, 'smrt.db')
FORMAT = os.environ.get('CSIRTG_SMRT_FORMAT', 'table')


# http://python-3-patterns-idioms-test.readthedocs.org/en/latest/Factory.html
# https://gist.github.com/pazdera/1099559
logging.getLogger("requests").setLevel(logging.WARNING)


class Smrt(object):
    def __exit__(self, exc_type, exc_val, exc_tb):
        pass

    def __enter__(self):
        return self

    def __init__(self, remote=REMOTE_ADDR, token=TOKEN, client='stdout', username=None, feed=None, archiver=None):

        self.logger = logging.getLogger(__name__)

        self.logger.debug(csirtg_smrt.client.__path__[0])
        self.client = load_plugin(csirtg_smrt.client.__path__[0], client)(remote, token, username=username, feed=feed)
        self.archiver = archiver

    def ping_remote(self):
        return self.client.ping(write=True)

    def _process(self, rule, feed, limit=None, data=None):
        fetch = Fetcher(rule, feed, data=data)

        parser_name = rule.parser or PARSER_DEFAULT
        parser = load_plugin(csirtg_smrt.parser.__path__[0], parser_name)

        if parser is None:
            self.logger.info('trying z{}'.format(parser_name))
            parser = load_plugin(csirtg_smrt.parser.__path__[0], 'z{}'.format(parser_name))
            if parser is None:
                raise SystemError('Unable to load parser: {}'.format(parser_name))

        self.logger.debug("loading parser: {}".format(parser))

        parser = parser(self.client, fetch, rule, feed, limit=limit, archiver=self.archiver)

        rv = parser.process()

        return rv

    def process(self, rule, data=None, feed=None, limit=None):
        rv = []
        if isinstance(rule, str) and os.path.isdir(rule):
            for f in os.listdir(rule):
                if not f.startswith('.'):
                    self.logger.debug("processing {0}/{1}".format(rule, f))
                    r = Rule(path=os.path.join(rule, f))

                    if not r.feeds:
                        continue

                    for feed in r.feeds:
                        try:
                            rv = self._process(r, feed, limit=limit)
                        except Exception as e:
                            self.logger.error('failed to process: {}'.format(feed))
                            self.logger.error(e)
                            traceback.print_exc()

        else:
            self.logger.debug("processing {0}".format(rule))
            r = rule
            if isinstance(rule, str):
                r = Rule(path=rule)

            if not r.feeds:
                self.logger.error("rules file contains no feeds")
                raise RuntimeError

            if feed:
                try:
                    rv = self._process(r, feed, limit=limit, data=data)
                except Exception as e:
                    self.logger.error('failed to process feed: {}'.format(feed))
                    self.logger.error(e)
                    traceback.print_exc()
            else:
                for feed in r.feeds:
                    try:
                        rv = self._process(Rule(path=rule), feed=feed, limit=limit, data=data)
                    except Exception as e:
                        self.logger.error('failed to process feed: {}'.format(feed))
                        self.logger.error(e)
                        traceback.print_exc()

        return rv


def main():
    p = get_argument_parser()
    p = ArgumentParser(
        description=textwrap.dedent('''\
        Env Variables:
            CSIRTG_RUNTIME_PATH
            CSIRTG_TOKEN

        example usage:
            $ csirtg-smrt --rule rules/default
            $ csirtg-smrt --rule default/csirtg.yml --feed port-scanners --remote http://localhost:5000
        '''),
        formatter_class=RawDescriptionHelpFormatter,
        prog='csirtg-smrt',
        parents=[p],
    )

    p.add_argument("-r", "--rule", help="specify the rules directory or specific rules file [default: %(default)s",
                   default=SMRT_RULES_PATH)

    p.add_argument("-f", "--feed", help="specify the feed to process")

    p.add_argument("--remote", help="specify the remote api url")
    p.add_argument('--remote-type', help="specify remote type [cif, csirtg, elasticsearch, syslog, etc]")
    p.add_argument('--client', default='stdout')

    p.add_argument('--cache', help="specify feed cache [default %(default)s]", default=SMRT_CACHE)

    p.add_argument("--limit", help="limit the number of records processed [default: %(default)s]",
                   default=None)

    p.add_argument("--token", help="specify token [default: %(default)s]", default=TOKEN)

    p.add_argument('--service', action='store_true', help="start in service mode")
    p.add_argument('--sleep', default=60)
    p.add_argument('--ignore-unknown', action='store_true')

    p.add_argument('--config', help='specify csirtg-smrt config path [default %(default)s', default=CONFIG_PATH)

    p.add_argument('--user')

    p.add_argument('--delay', help='specify initial delay', default=randint(5, 55))

    p.add_argument('--archive-path', help='specify logger path [default: %(default)s', default=ARCHIVE_PATH)
    p.add_argument('--no-archiver', action='store_true')

    p.add_argument('--format', help='specify output format [default: %(default)s]"', default=FORMAT,
                   choices=FORMATS.keys())

    args = p.parse_args()

    o = read_config(args)
    options = vars(args)
    for v in options:
        if options[v] is None:
            options[v] = o.get(v)

    setup_logging(args)
    logger = logging.getLogger(__name__)
    logger.info('loglevel is: {}'.format(logging.getLevelName(logger.getEffectiveLevel())))

    setup_signals(__name__)

    setup_runtime_path(args.runtime_path)

    archiver = None
    if not args.no_archiver:
        archiver = Archiver(dbfile=args.archive_path)

    stop = False
    service = args.service
    if not args.remote:
        service = False

    if service:
        r = args.delay
        logger.info("random delay is {}, then running every 60min after that".format(r))
        sleep((r * 60))

    while not stop:
        if not service:
            stop = True

        data = False
        if select.select([sys.stdin, ], [], [], 0.0)[0]:
            data = sys.stdin.read()

        logger.info('starting...')

        try:
            with Smrt(options.get('remote'), options.get('token'), client=args.client, username=args.user,
                      feed=args.feed, archiver=archiver) as s:


                s.ping_remote()

                x = s.process(args.rule, feed=args.feed, limit=args.limit, data=data)

                if args.client == 'stdout':
                    print(FORMATS[options.get('format')](data=x))
                logger.info('complete')

                if args.service:
                    logger.info('sleeping for 1 hour')
                    sleep((60 * 60))

        except AuthError as e:
            logger.error(e)
            stop = True
        except RuntimeError as e:
            logger.error(e)
            if str(e).startswith('submission failed'):
                stop = True
            else:
                logging.exception('Got exception on main handler')
        except TimeoutError as e:
            logger.error(e)
            stop = True
        except KeyboardInterrupt:
            logger.info('shutting down')
            stop = True

        if archiver:
            rv = archiver.cleanup()
            logger.info('cleaning up archive: %i' % rv)

        logger.info('completed')

if __name__ == "__main__":
    main()
