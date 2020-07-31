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

import math

from hypothesis import assume, given, note, reject, settings, strategies as st
from hypothesis.internal.conjecture.dfa import DEAD, ConcreteDFA


def test_enumeration_when_sizes_do_not_agree():
    dfa = ConcreteDFA([{0: 1, 1: 2}, {}, {1: 3}, {}], {1, 3})  # 0  # 1  # 2  # 3

    assert list(dfa.all_matching_strings()) == [b"\0", b"\1\1"]


def test_enumeration_of_very_long_strings():
    """This test is mainly testing that it terminates. If we were
    to use a naive breadth first search for this it would take
    forever to run because it would run in time roughly 256 ** 50.
    """
    size = 50
    dfa = ConcreteDFA(
        [{c: n + 1 for c in range(256)} for n in range(100)] + [{}], {size}
    )

    for i, s in enumerate(dfa.all_matching_strings()):
        assert len(s) == size
        assert int.from_bytes(s, "big") == i
        if i >= 1000:
            break


def test_max_length_of_empty_dfa_is_zero():
    dfa = ConcreteDFA([{}], {0})
    assert dfa.max_length(dfa.start) == 0


def test_mixed_dfa_initialization():
    d = ConcreteDFA([[(2, 1)], [(0, 5, 2)], {4: 0, 3: 1}], {0})

    assert d.transition(0, 2) == 1
    assert d.transition(0, 3) == DEAD

    for n in range(6):
        assert d.transition(1, n) == 2
    assert d.transition(1, 6) == DEAD

    assert d.transition(2, 4) == 0
    assert d.transition(2, 3) == 1
    assert d.transition(2, 5) == DEAD


@st.composite
def dfas(draw):
    states = draw(st.integers(1, 20))

    a_state = st.integers(0, states - 1)
    a_byte = st.integers(0, 255)

    start = draw(a_state)
    accepting = draw(st.sets(a_state, min_size=1))

    transitions = [
        draw(st.one_of(
            st.lists(st.tuples(a_byte, a_state) | st.tuples(a_byte, a_byte, a_state).map(lambda t: (t[1], t[0], t[2]) if t[0] > t[1] else t)),
            st.dictionaries(a_byte, a_state),
        ))
        for _ in range(states)
    ]

    return ConcreteDFA(transitions, accepting, start)


@settings(max_examples=20)
@given(dfas(), st.booleans())
def test_canonicalised_matches_same_strings(dfa, via_repr):
    canon = dfa.canonicalise()
    note(canon)

    if via_repr:
        canon = eval(repr(canon))

    assert dfa.max_length(dfa.start) == canon.max_length(canon.start)

    try:
        minimal = next(dfa.all_matching_strings())
    except StopIteration:
        reject()

    assert minimal == next(canon.all_matching_strings())

    assert dfa.count_strings(dfa.start, len(minimal)) == canon.count_strings(
        canon.start, len(minimal)
    )


@settings(max_examples=20)
@given(dfas())
def test_has_string_of_max_length(dfa):
    length = dfa.max_length(dfa.start)
    assume(math.isfinite(length))
    assume(not dfa.is_dead(dfa.start))

    assert dfa.count_strings(dfa.start, length) > 0


def test_converts_long_tables_to_dicts():
    dfa = ConcreteDFA([[(0, 0), (1, 1), (2, 2), (3, 1), (4, 0)], [(0, 0)], []], {2})
    list(dfa.transitions(0))

    assert isinstance(dfa._ConcreteDFA__transitions[0], dict)


@settings(max_examples=20)
@given(dfas(), dfas())
def test_dfa_with_different_string_is_not_equivalent(x, y):
    assume(not x.is_dead(x.start))

    s = next(x.all_matching_strings())
    assume(not y.matches(s))

    assert not x.equivalent(y)
