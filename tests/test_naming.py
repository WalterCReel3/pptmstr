"""
The one allocator of sub-agent addresses and the table it is given.
"""

from __future__ import annotations

import inspect
import itertools

from pptmstr import addresses, driver
from pptmstr.naming import ROOT_NAMES, address_for, reserved


def test_an_address_is_stepped_over_whoever_or_nobody_holds_it() -> None:
    held = {"builder": "a-1", "builder-2": None, "reviewer": "a-9"}

    assert address_for("a-new", "Builder ", held, ()) == "builder-3"
    assert address_for("a-new", "builder", held, ("builder-3",)) == "builder-4"
    assert address_for("a-new", "lead", {}, ()) == "lead-2"
    assert address_for("a-new", " ", {}, ()) == "agent"


def test_an_id_the_table_names_gets_its_address_back_wherever_it_starts() -> None:
    held = {"builder": "a-1", "builder-2": "a-2", "builder-3": "a-new"}

    assert address_for("a-2", "builder", held, ()) == "builder-2"
    # By id and never by role: the type it reports now is not consulted.
    assert address_for("a-2", "reviewer", held, ()) == "builder-2"


def test_a_table_is_the_same_whatever_order_its_pairs_arrive_in() -> None:
    """
    The driver's copy is made from a log's ``Board.addresses`` and the board's
    from the ``Restore.parties`` the store made of that log, and the two do not
    share an order. An address written under two ids is reserved for neither.
    """
    pairs = [
        ("builder", "a-1"),
        (" Builder ", None),
        ("builder-2", "a-2"),
        ("builder-2", "a-3"),
        ("reviewer", None),
        ("", "a-4"),
        ("lead", None),
        ("main", "a-5"),
    ]
    expected = {"builder": "a-1", "builder-2": None, "reviewer": None}

    for order in itertools.permutations(pairs):
        assert reserved(order) == expected


def test_the_driver_and_the_board_share_the_root_names_and_the_allocator() -> None:
    """
    The wiring. Two spellings of the root's names, or a driver with a loop of its
    own, is two allocators again however well each is tested.
    """
    assert driver.ROOT_ADDRESSES is ROOT_NAMES
    # Reached on purpose: the check is that driver's name is the allocator itself.
    assert driver.address_for is address_for  # type: ignore[attr-defined]
    source = inspect.getsource(driver.AgentSession._address_for)
    assert "return address_for(" in source and "while" not in source
    assert "address_for(" in inspect.getsource(addresses._addresses)
