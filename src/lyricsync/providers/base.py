"""The contract every lyrics source implements."""

from __future__ import annotations

from typing import Protocol

from ..models import Lyrics, TrackMeta


class LyricsProvider(Protocol):
    """Fetches lyrics for a track, synced or plain.

    Returning ``None`` means "I have nothing for this track"; the pipeline
    then moves to the next provider. Network or parsing errors should be
    swallowed into ``None`` so one flaky source cannot stop a batch.
    """

    name: str

    def fetch(self, meta: TrackMeta) -> Lyrics | None: ...
