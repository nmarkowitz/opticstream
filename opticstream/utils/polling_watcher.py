from __future__ import annotations

import logging
import signal
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from typing import Generic, TypeVar

logger = logging.getLogger(__name__)

T = TypeVar("T")
K = TypeVar("K")


@dataclass
class StabilityRecord:
    fingerprint: object
    stable_since: float | None = None


_shutdown_requested = False


def _signal_handler(signum: int, frame: object) -> None:
    global _shutdown_requested
    logger.info("Received signal %s. Initiating graceful shutdown...", signum)
    _shutdown_requested = True


class PollingStableWatcher(Generic[T, K]):
    """
    Polling-only watcher with stability gating.

    A candidate is processed only after:
    - it is discovered by ``discover_candidates()``
    - its fingerprint remains unchanged for ``stability_seconds``

    The watcher is generic and domain-agnostic. Domain code supplies:
    - ``discover_candidates``: returns the current set of candidates
    - ``candidate_key``: stable identity for a candidate
    - ``fingerprint``: changes whenever the candidate's relevant on-disk state changes
    - ``process``: dispatch logic for a stable candidate

    Notes
    -----
    - After ``process(candidate)`` is attempted, the candidate is removed from the
      internal stability map for this cycle. If it still exists on disk and is still
      discoverable later, it may re-enter tracking, but domain-level dedupe should
      prevent duplicate work.
    - This watcher intentionally does not know anything about state services,
      event emitters, flows, or refresh hooks.
    """

    def __init__(
        self,
        *,
        discover_candidates: Callable[[], Iterable[T]],
        candidate_key: Callable[[T], K],
        fingerprint: Callable[[T], object],
        process: Callable[[T], int],
        poll_interval: int = 5,
        stability_seconds: int = 15,
        running_message: str = "Polling watcher running",
    ) -> None:
        if poll_interval <= 0:
            raise ValueError(f"poll_interval must be > 0, got {poll_interval}")
        if stability_seconds < 0:
            raise ValueError(f"stability_seconds must be >= 0, got {stability_seconds}")

        self.discover_candidates = discover_candidates
        self.candidate_key = candidate_key
        self.fingerprint = fingerprint
        self.process = process
        self.poll_interval = poll_interval
        self.stability_seconds = stability_seconds
        self.running_message = running_message

        self._stability: dict[K, StabilityRecord] = {}

    def run(self, *companions: "PollingStableWatcher") -> None:
        """Poll until Ctrl+C / SIGTERM.

        ``companions`` (e.g. enface previews alongside batch dispatch) are polled in
        a background thread, so they react as soon as their files are stable instead
        of waiting for this watcher's processing (e.g. a MATLAB batch). Their errors
        are logged and never stop this watcher.
        """
        global _shutdown_requested
        _shutdown_requested = False
        companion_thread = None
        if companions:
            companion_thread = threading.Thread(
                target=_run_companions, args=(companions,), name="companion-watchers",
                daemon=True,
            )

        signal.signal(signal.SIGINT, _signal_handler)
        signal.signal(signal.SIGTERM, _signal_handler)

        logger.info("%s", self.running_message)
        logger.info(
            "poll_interval=%ss; stability_seconds=%ss",
            self.poll_interval,
            self.stability_seconds,
        )
        logger.info("Press Ctrl+C to stop")

        if companion_thread is not None:
            companion_thread.start()
        iteration = 0
        try:
            while not _shutdown_requested:
                iteration += 1
                logger.info("--- iteration %s ---", iteration)
                self._run_iteration()

                if _shutdown_requested:
                    break

                time.sleep(self.poll_interval)
        except KeyboardInterrupt:
            logger.info("Keyboard interrupt, shutting down")
        finally:
            if companion_thread is not None:
                _shutdown_requested = True
                # Daemon thread: an in-progress companion job does not block exit.
                companion_thread.join(timeout=1)
            logger.info("Watcher stopped after %s iterations", iteration)

    def _run_iteration(self) -> None:
        now = time.time()
        seen_keys: set[K] = set()

        candidates = list(self.discover_candidates())
        logger.debug("_run_iteration: %s candidate(s) discovered", len(candidates))

        for candidate in candidates:
            key = self.candidate_key(candidate)
            seen_keys.add(key)

            try:
                current_fingerprint = self.fingerprint(candidate)
            except Exception as exc:
                logger.warning("Failed to fingerprint candidate %r: %s", candidate, exc)
                continue

            record = self._stability.get(key)

            if record is None:
                self._stability[key] = StabilityRecord(
                    fingerprint=current_fingerprint,
                    stable_since=now if self.stability_seconds == 0 else None,
                )
                logger.info("Discovered new candidate: %r", candidate)
                continue

            if record.fingerprint != current_fingerprint:
                record.fingerprint = current_fingerprint
                record.stable_since = now if self.stability_seconds == 0 else None
                logger.info(
                    "Candidate changed; resetting stability timer: %r", candidate
                )
                continue

            if record.stable_since is None:
                record.stable_since = now
                logger.info("Candidate became stable: %r", candidate)
                continue

            stable_for = now - record.stable_since
            if stable_for < self.stability_seconds:
                logger.debug(
                    "Candidate waiting for stability: %.1fs / %ss: key=%r",
                    stable_for,
                    self.stability_seconds,
                    key,
                )
                continue

            try:
                dispatched = self.process(candidate)
                logger.info(
                    "Processed candidate=%r dispatched=%s", candidate, dispatched
                )
            except Exception as exc:
                logger.exception("Error processing candidate %r: %s", candidate, exc)
            finally:
                # Remove after an attempt. If the candidate is still present on disk,
                # it will be rediscovered and domain dedupe should prevent rework.
                self._stability.pop(key, None)

        disappeared_keys = set(self._stability) - seen_keys
        for key in disappeared_keys:
            logger.debug("Candidate disappeared from disk, dropping from tracking: key=%r", key)
            self._stability.pop(key, None)


def _run_companions(companions) -> None:
    """Background loop for ``PollingStableWatcher.run`` companions."""
    while not _shutdown_requested:
        for companion in companions:
            if _shutdown_requested:
                return
            try:
                companion._run_iteration()
            except Exception as exc:
                logger.exception("Companion watcher iteration failed: %s", exc)
        interval = min(companion.poll_interval for companion in companions)
        deadline = time.monotonic() + interval
        while not _shutdown_requested and time.monotonic() < deadline:
            time.sleep(min(0.5, interval))
