"""
Messages and decisions between leads: the fields a peer message must hold, and
what a read covers.

Pure: no SDK, no clock, no IO. Nothing outside ``pptmstr.store`` imports it
(``tests/test_layers.py``); ``store._apply`` is the step it is part of.
"""

from __future__ import annotations

from collections.abc import Mapping

from ..channels.records import (
    EVERY_CHANNEL,
    Channel,
    ChannelId,
    ChannelScope,
    PeerIdentity,
    channel_as_linked,
)
from ..effects import peer_identity
from ..ids import is_concern_id
from ..model import ConcernId, Snapshot


def _link_peer(snap: Snapshot, session_id: str, channel: str | None) -> PeerIdentity | None:
    """
    The lead at the other end of the live link called ``channel`` from
    ``session_id``, or None where there is no such link or it does not join
    that session.
    """
    link = None if channel is None else snap.live_channel(channel)
    if link is None or not link.joins(session_id):
        return None
    others = sorted(link.members - {session_id})
    return peer_identity(snap, others[0]) if others else None


def _read_covers(snap: Snapshot, reader: str, channel: ChannelScope) -> tuple[Channel, ...]:
    """
    The live links of ``reader``'s that a read of ``channel`` by its lead
    covers: the one of that name, or every one. The one test of "this read
    covers it" for both what the read lists as owed (``PeerInboxRead``'s arm
    of ``_apply``) and the wake it lets go of (``_peer_waking``), so a
    question's wake is let go of only by a read that listed it.
    """
    return tuple(
        link
        for link in snap.channels_of(reader)
        if channel is EVERY_CHANNEL or channel == link.name
    )


def _peer_fields_hold(
    channel: str | None,
    link: ChannelId | None,
    cites: ConcernId | None,
    channels: Mapping[ChannelId, Channel],
) -> bool:
    """
    Whether a peer message's own fields are ones ``send_to_peer`` could have
    filed: its channel as ``records.channel_as_linked`` reads it, and an ``re`` that
    is absent or in the concern-id shape the tool checks.
    """
    return (
        channel is not None
        and channel_as_linked(channel, link, channels)
        and (cites is None or is_concern_id(cites))
    )
