import copy
import re

from csirtg_smrt.parser import Parser
from csirtg_indicator import Indicator
import logging


class Pattern(Parser):

    def __init__(self, *args, **kwargs):
        super(Pattern, self).__init__(*args, **kwargs)

        self.pattern = self.rule.defaults.get('pattern')

        if self.rule.feeds[self.feed].get('pattern'):
            self.pattern = self.rule.feeds[self.feed].get('pattern')

        self.pattern = re.compile(self.pattern)
        self.split = "\n"

    def process(self):
        if self.rule.feeds[self.feed].get('values'):
            cols = self.rule.feeds[self.feed].get('values')
        else:
            cols = self.rule.defaults['values']
        defaults = self._defaults()

        if isinstance(cols, str):
            cols = cols.split(',')

        rv = []
        res = []
        for l in self.fetcher.process(split=self.split):

            if self.ignore(l):  # comment or skip
                continue

            try:
                m = self.pattern.search(l).groups()
                self.logger.debug(m)
                if isinstance(m, str):
                    m = [m]
            except ValueError as e:
                #self.logger.error(e)  # ignore non matched lines
                continue
            except AttributeError as e:
                #self.logger.error(e)
                continue

            if len(cols):
                i = copy.deepcopy(defaults)

                for idx, col in enumerate(cols):
                    if col:
                        i[col] = m[idx]

                i.pop("values", None)
                i.pop("pattern", None)

                self.logger.debug(i)

                try:
                    i = Indicator(**i)
                except NotImplementedError as e:
                    self.logger.error(e)
                    self.logger.info('skipping: {}'.format(i['indicator']))
                else:
                    self.logger.debug(i)
                    if self.is_archived(i.indicator, i.provider, i.group, i.tags, i.firsttime, i.lasttime):
                        self.logger.info('skipping: {}/{}'.format(i.provider, i.indicator))
                    else:
                        r = self.client.indicators_create(i)
                        self.archive(i.indicator, i.provider, i.group, i.tags, i.firsttime, i.lasttime)
                        rv.append(r)

                    if self.limit:
                        self.limit -= 1

                        if self.limit == 0:
                            self.logger.debug('limit reached...')
                            break

        return rv

Plugin = Pattern