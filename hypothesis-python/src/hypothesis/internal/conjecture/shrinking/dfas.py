# This file is part of Hypothesis, which may be found at
# https://github.com/HypothesisWorks/hypothesis/
#
# Most of this work is copyright (C) 2013-2020 David R. MacIver
# (david@drmaciver.com), but it contains contributions by others. See
# CONTRIBUTING.rst for a full list of people who may hold copyright, and
# consult the git log if you need to determine who owns an individual
# contribution.
#
# This Source Code Form is subject to the terms of the Mozilla Public License,
# v. 2.0. If a copy of the MPL was not distributed with this file, You can
# obtain one at https://mozilla.org/MPL/2.0/.
#
# END HEADER

import hashlib
from itertools import islice

from hypothesis import HealthCheck, settings
from hypothesis.errors import HypothesisException
from hypothesis.internal.conjecture.data import Status
from hypothesis.internal.conjecture.dfa.lstar import LStar
from hypothesis.internal.conjecture.shrinking.learned_dfas import (
    SHRINKING_DFAS,
    __file__ as learned_dfa_file,
)

"""
This is a module for learning new DFAs that help normalize test
functions. That is, given a test function that sometimes shrinks
to one thing and sometimes another, this module is designed to
help learn new DFA-based shrink passes that will cause it to
always shrink to the same thing.
"""


class FailedToNormalise(HypothesisException):
    pass


def update_learned_dfas():
    """Write any modifications to the SHRINKING_DFAS dictionary
    back to the learned DFAs file."""

    with open(learned_dfa_file) as i:
        source = i.read()

    lines = source.splitlines()

    i = lines.index("# AUTOGENERATED BEGINS")

    del lines[i + 1 :]

    lines.append("")
    lines.append("# fmt: off")
    lines.append("")

    for k, v in sorted(SHRINKING_DFAS.items()):
        lines.append("SHRINKING_DFAS[%r] = %r # noqa: E501" % (k, v))

    lines.append("")
    lines.append("# fmt: on")

    new_source = "\n".join(lines) + "\n"

    if new_source != source:
        with open(learned_dfa_file, "w") as o:
            o.write(new_source)


def normalize(
    base_name,
    test_function,
    *,
    required_successes=100,
    allowed_to_update=False,
    max_dfas=10
):
    """Attempt to ensure that this test function successfully normalizes - i.e.
    whenever it declares a test case to be interesting, we are able
    to shrink that to the same interesting test case (which logically should
    be the shortlex minimal interesting test case, though we may not be able
    to detect if it is).

    Will run until we have seen ``required_successes`` many interesting test
    cases in a row normalize to the same value.

    If ``allowed_to_update`` is True, whenever we fail to normalize we will
    learn a new DFA-based shrink pass that allows us to make progress. Any
    learned DFAs will be written back into this file at the end of this
    function. If ``allowed_to_update`` is False, this will raise an error
    as soon as it encounters a failure to normalize.

    Additionally, if more than ``max_dfas` DFAs are required to normalize
    this test function, this function will raise an error - it's essentially
    designed for small patches that other shrink passes don't cover, and
    if it's learning too many patches then you need a better shrink pass
    than this can provide.
    """
    # Need import inside the function to avoid circular imports
    from hypothesis.internal.conjecture.engine import BUFFER_SIZE, ConjectureRunner
    from hypothesis.internal.conjecture.shrinker import sort_key

    runner = ConjectureRunner(
        test_function,
        settings=settings(database=None, suppress_health_check=HealthCheck.all()),
        ignore_limits=True,
    )

    seen = {}

    dfas_added = 0

    found_interesting = False
    consecutive_successes = 0
    failures_to_find_interesting = 0
    while consecutive_successes < required_successes:
        attempt = runner.cached_test_function(b"", extend=BUFFER_SIZE)
        if attempt.status < Status.INTERESTING:
            failures_to_find_interesting += 1
            assert (
                found_interesting or failures_to_find_interesting <= 1000
            ), "Test function seems to have no interesting test cases"
            continue

        found_interesting = True

        target = attempt.interesting_origin

        while True:
            # We may need to add multiple DFAs to get a single
            # example to shrink fully, so we run this in a loop
            # and break out once we've seen it normalise.

            shrunk = runner.shrink(
                attempt,
                lambda d: d.status == Status.INTERESTING
                and d.interesting_origin == target,
            )

            if target not in seen:
                seen[target] = shrunk
                break

            existing = seen[target]

            if shrunk.buffer == existing.buffer:
                consecutive_successes += 1
                break

            consecutive_successes = 0

            u, v = sorted((shrunk.buffer, existing.buffer), key=sort_key)

            if not allowed_to_update:
                raise FailedToNormalise(
                    "Shrinker failed to normalize %r to %r and we are not allowed to learn new DFAs."
                    % (v, u)
                )

            if dfas_added >= max_dfas:
                raise FailedToNormalise(
                    "Test function is too hard to learn: Added 10 DFAs and still not done."
                )

            dfas_added += 1

            assert not v.startswith(u)

            # We would like to avoid using LStar on large strings as its
            # behaviour can be quadratic or worse. In order to help achieve
            # this we peel off a common prefix and suffix of the two final
            # results and just learn the internal bit where they differ.
            #
            # This potentially reduces the length quite far if there's
            # just one tricky bit of control flow we're struggling to
            # reduce inside a strategy somewhere and the rest of the
            # test function reduces fine.
            i = 0
            while u[i] == v[i]:
                i += 1
            prefix = u[:i]
            assert u.startswith(prefix)
            assert v.startswith(prefix)

            i = 1
            while u[-i] == v[-i]:
                i += 1

            suffix = u[len(u) + 1 - i :]
            assert u.endswith(suffix)
            assert v.endswith(suffix)

            u_core = u[len(prefix) : len(u) - len(suffix)]
            v_core = v[len(prefix) : len(v) - len(suffix)]

            assert u == prefix + u_core + suffix
            assert v == prefix + v_core + suffix

            allow_discards = shrunk.has_discards or existing.has_discards

            def is_valid_core(s):
                buf = prefix + s + suffix
                result = runner.cached_test_function(buf)
                return (
                    result.status == Status.INTERESTING
                    and result.interesting_origin == target
                    # Because we're often using this to learn strategies
                    # rather than entire complex test functions, it's
                    # important that our replacements are precise and
                    # don't leave the rest of the test case in a weird
                    # state.
                    and result.buffer == buf
                    # Because the shrinker is good at removing discarded
                    # data, unless we need discards to allow one or both
                    # of u and v to result in valid shrinks, we don't
                    # count attempts that have them as valid. This will
                    # cause us to match fewer strings, which will make
                    # the resulting shrink pass more efficient when run
                    # on test functions it wasn't really intended for.
                    and (allow_discards or not result.has_discards)
                )

            assert sort_key(u_core) < sort_key(v_core)

            assert is_valid_core(u_core)
            assert is_valid_core(v_core)

            learner = LStar(is_valid_core)

            prev = -1
            while learner.generation != prev:
                prev = learner.generation
                learner.learn(u_core)
                learner.learn(v_core)

                # We mostly care about getting the right answer on the
                # minimal test case, but because we're doing this offline
                # anyway we might as well spend a little more time trying
                # small examples to make sure the learner gets them right.
                for v in islice(learner.dfa.all_matching_strings(), 10):
                    learner.learn(v)

            # We've now successfully learned a DFA that works for shrinking
            # our failed normalisation further. Canonicalise it into a concrete
            # DFA so we can save it for later.
            new_dfa = learner.dfa.canonicalise()

            name = (
                base_name
                + "-"
                + hashlib.sha1(repr(new_dfa).encode("utf-8")).hexdigest()[:10]
            )

            # If there is a name collision this DFA should already be being
            # used for shrinking, so we should have shrunk this case better
            # then we already have.
            assert name not in SHRINKING_DFAS
            SHRINKING_DFAS[name] = new_dfa

            seen[target] = runner.interesting_examples[target]

    if dfas_added > 0:
        # We've learned one or more DFAs in the course of normalising, so now
        # we update the file to record those for posterity.
        update_learned_dfas()
