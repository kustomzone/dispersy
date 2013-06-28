import logging
logger = logging.getLogger(__name__)
summary = logging.getLogger("test-overlay-summary")

from pprint import pformat
from itertools import chain
from time import time
from unittest import skipUnless
from collections import defaultdict

from ..conversion import DefaultConversion
from .debugcommunity.community import DebugCommunity
from .debugcommunity.conversion import DebugCommunityConversion
from .dispersytestclass import DispersyTestFunc, call_on_dispersy_thread


class TestOverlay(DispersyTestFunc):

    @skipUnless(summary.isEnabledFor(logging.DEBUG), "This 'unittest' tests the health of a live overlay, as such, this is not part of the code review process")
    def test_all_channel_community(self):
        return self.check_live_overlay(cid_hex="8164f55c2f828738fa779570e4605a81fec95c9d",
                                       version="\x01",
                                       enable_fast_walker=False)

    @skipUnless(summary.isEnabledFor(logging.DEBUG), "This 'unittest' tests the health of a live overlay, as such, this is not part of the code review process")
    def test_barter_community(self):
        return self.check_live_overlay(cid_hex="4fe1172862c649485c25b3d446337a35f389a2a2",
                                       version="\x01",
                                       enable_fast_walker=False)

    @skipUnless(summary.isEnabledFor(logging.DEBUG), "This 'unittest' tests the health of a live overlay, as such, this is not part of the code review process")
    def test_search_community(self):
        # TODO because the search community uses a modified search message, the introduction request is larger than
        # normal.  This causes incoming requests to be dropped.
        return self.check_live_overlay(cid_hex="2782dc9253cef6cc9272ee8ed675c63743c4eb3a",
                                       version="\x01",
                                       enable_fast_walker=True)

    @call_on_dispersy_thread
    def check_live_overlay(self, cid_hex, version, enable_fast_walker):
        class Community(DebugCommunity):
            def __init__(self, dispersy, master):
                super(Community, self).__init__(dispersy, master)
                self.dispersy.callback.register(self.fast_walker)

            def initiate_conversions(self):
                return [DefaultConversion(self), DebugCommunityConversion(self, version)]

            def dispersy_claim_sync_bloom_filter(self, request_cache):
                # we only want to walk in the community, not exchange data
                return None

            def fast_walker(self):
                for _ in xrange(10):
                    now = time()

                    # count -everyone- that is active (i.e. walk or stumble)
                    active_canidates = [candidate
                                        for candidate
                                        in self._candidates.itervalues()
                                        if candidate.is_active(self, now)]
                    if len(active_canidates) > 20:
                        logger.debug("there are %d active non-bootstrap candidates available, prematurely quitting fast walker", len(active_canidates))
                        break

                    eligible_candidates = [candidate
                                           for candidate
                                           in chain(self._dispersy.bootstrap_candidates)
                                           if candidate.is_eligible_for_walk(self, now)]
                    for count, candidate in enumerate(eligible_candidates[:len(eligible_candidates) / 2], 1):
                        logger.debug("%d/%d extra walk to %s", count, len(eligible_candidates), candidate)
                        self.create_introduction_request(candidate, allow_sync=False)

                    # request peers that is eligible
                    eligible_candidates = [candidate
                                           for candidate
                                           in self._candidates.itervalues()
                                           if candidate.is_eligible_for_walk(self, now)]
                    for count, candidate in enumerate(eligible_candidates[:len(eligible_candidates) / 2], 1):
                        logger.debug("%d/%d extra walk to %s", count, len(eligible_candidates), candidate)
                        self.create_introduction_request(candidate, allow_sync=False)

                    # wait for NAT hole punching
                    yield 1.0

                summary.debug("finished")

        class Info(object):
            pass

        assert isinstance(cid_hex, str)
        assert len(cid_hex) == 40
        assert isinstance(enable_fast_walker, bool)
        cid = cid_hex.decode("HEX")

        self._dispersy.statistics.enable_debug_statistics(True)
        community = Community.join_community(self._dispersy, self._dispersy.get_temporary_member_from_id(cid), self._my_member)
        summary.info(community.cid.encode("HEX"))

        history = []
        begin = time()
        for _ in xrange(60 * 15):
            yield 1.0
            now = time()
            info = Info()
            info.diff = now - begin
            info.candidates = [(candidate, candidate.get_category(community, now)) for candidate in community.dispersy_yield_candidates()]
            info.verified_candidates = [(candidate, candidate.get_category(community, now)) for candidate in community.dispersy_yield_verified_candidates()]
            info.bootstrap_attempt = self._dispersy.statistics.walk_bootstrap_attempt
            info.bootstrap_success = self._dispersy.statistics.walk_bootstrap_success
            info.bootstrap_ratio = 100.0 * info.bootstrap_success / info.bootstrap_attempt if info.bootstrap_attempt else 0.0
            info.candidate_attempt = self._dispersy.statistics.walk_attempt - self._dispersy.statistics.walk_bootstrap_attempt
            info.candidate_success = self._dispersy.statistics.walk_success - self._dispersy.statistics.walk_bootstrap_success
            info.candidate_ratio = 100.0 * info.candidate_success / info.candidate_attempt if info.candidate_attempt else 0.0
            history.append(info)

            minimum = min(int(info.diff / 5) - 5, 20)
            summary.info("after %.1f seconds there are %d verified candidates (%d minimum) [w%d:s%d:i%d:n%d]",
                         info.diff,
                         len([_ for _, category in info.candidates if category in (u"walk", u"stumble")]),
                         minimum,
                         len([_ for _, category in info.candidates if category == u"walk"]),
                         len([_ for _, category in info.candidates if category == u"stumble"]),
                         len([_ for _, category in info.candidates if category == u"intro"]),
                         len([_ for _, category in info.candidates if category == u"none"]))
            summary.debug("bootstrap walking: %d/%d ~%.1f%%", info.bootstrap_success, info.bootstrap_attempt, info.bootstrap_ratio)
            summary.debug("candidate walking: %d/%d ~%.1f%%", info.candidate_success, info.candidate_attempt, info.candidate_ratio)

        helper_requests = defaultdict(lambda: defaultdict(int))
        helper_responses = defaultdict(lambda: defaultdict(int))

        for destination, requests in self._dispersy.statistics.outgoing_introduction_request.iteritems():
            responses = self._dispersy.statistics.incoming_introduction_response[destination]

            # who introduced me to DESTINATION?
            for helper, introductions in self._dispersy.statistics.received_introductions.iteritems():
                if destination in introductions:
                    helper_requests[helper][destination] = requests
                    helper_responses[helper][destination] = responses

        l = [(100.0 * sum(helper_responses[helper].itervalues()) / sum(helper_requests[helper].itervalues()),
              sum(helper_requests[helper].itervalues()),
              sum(helper_responses[helper].itervalues()),
              helper_requests[helper],
              helper_responses[helper],
              helper)
             for helper
             in helper_requests]

        for ratio, req, res, req_dict, res_dict, helper, in sorted(l):
            summary.debug("%.1f%% %3d %3d %15s:%-4d  #%d %s", ratio, req, res, helper[0], helper[1],
                          len(req_dict),
                          "; ".join("%s:%d:%d/%d" % (addr[0], addr[1], res_dict[addr], req_dict[addr])
                                    for addr
                                    in req_dict))

        self._dispersy.statistics.update()
        summary.debug("\n%s", pformat(self._dispersy.statistics.get_dict()))

        # write graph statistics
        handle = open("%s_connections.txt" % cid_hex, "w+")
        handle.write("# TIME VERIFIED_CANDIDATES CANDIDATES B_ATTEMPTS B_SUCCESSES C_ATTEMPTS C_SUCCESSES\n")
        for info in history:
            handle.write("%.2f %d %d %d %d %d %d\n" % (
                    info.diff,
                    len(info.verified_candidates),
                    len(info.candidates),
                    info.bootstrap_attempt,
                    info.bootstrap_success,
                    info.candidate_attempt,
                    info.candidate_success))

        # determine test success or failure
        average_verified_candidates = 1.0 * sum(len(info.verified_candidates) for info in history) / len(history)
        summary.debug("Average verified candidates: %.1f", average_verified_candidates)
        self.assertGreater(average_verified_candidates, 10.0)
