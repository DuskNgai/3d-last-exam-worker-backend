"""Worker process, run, and verification status transitions."""

from enum import StrEnum
from typing import Final


class ProcessStatus(StrEnum):
    """Process states exposed through GET /status."""

    STARTING = 'starting'
    IDLE = 'idle'
    EVALUATING = 'evaluating'
    STOPPING = 'stopping'


class RunStatus(StrEnum):
    """Run status values exposed through GET /run."""

    QUEUED = 'queued'
    PREPARING_RUN = 'preparing_run'
    RUNNING = 'running'
    PAUSED = 'paused'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


class VerificationStatus(StrEnum):
    """Verification status values exposed through GET /verification."""

    QUEUED = 'queued'
    PREPARING_VERIFY = 'preparing_verify'
    VERIFYING = 'verifying'
    PAUSED = 'paused'
    SUCCEEDED = 'succeeded'
    FAILED = 'failed'
    CANCELLED = 'cancelled'


RUN_UNFINISHED: Final = frozenset({
    RunStatus.QUEUED,
    RunStatus.PREPARING_RUN,
    RunStatus.RUNNING,
    RunStatus.PAUSED,
})
RUN_COMPLETED: Final = frozenset({RunStatus.SUCCEEDED, RunStatus.FAILED})
VERIFICATION_UNFINISHED: Final = frozenset({
    VerificationStatus.QUEUED,
    VerificationStatus.PREPARING_VERIFY,
    VerificationStatus.VERIFYING,
    VerificationStatus.PAUSED,
})
VERIFICATION_COMPLETED: Final = frozenset({VerificationStatus.SUCCEEDED, VerificationStatus.FAILED})


class InvalidTransitionError(ValueError):
    """An attempt cannot accept the requested transition."""


class AttemptState:
    """Own the run and verification status pair of one attempt."""

    def __init__(self) -> None:
        self.run_status = RunStatus.QUEUED
        self.run_reason = ''
        self.run_paused_from: RunStatus | None = None
        self.verification_status = VerificationStatus.QUEUED
        self.verification_reason = ''
        self.verification_paused_from: VerificationStatus | None = None
        self.verification_dispatched = False

    @property
    def is_run_unfinished(self) -> bool:
        """Whether the run has not reached a terminal status."""
        return self.run_status in RUN_UNFINISHED

    @property
    def is_verification_unfinished(self) -> bool:
        """Whether the verification has not reached a terminal status."""
        return self.verification_status in VERIFICATION_UNFINISHED

    @property
    def is_unfinished(self) -> bool:
        """Whether either stage of the attempt is still unfinished."""
        return self.is_run_unfinished or self.is_verification_unfinished

    @property
    def is_deletable(self) -> bool:
        """Whether both stages are cancelled and the attempt may be deleted."""
        return self.run_status == RunStatus.CANCELLED and self.verification_status == VerificationStatus.CANCELLED

    def start_run(self) -> None:
        """Move a queued attempt into run preparation."""
        if self.run_status != RunStatus.QUEUED:
            raise InvalidTransitionError(f'cannot prepare a run from {self.run_status}')
        self.run_status = RunStatus.PREPARING_RUN
        self.run_reason = ''

    def mark_running(self) -> None:
        """Move a prepared attempt into the running stage."""
        if self.run_status != RunStatus.PREPARING_RUN:
            raise InvalidTransitionError(f'cannot run from {self.run_status}')
        self.run_status = RunStatus.RUNNING
        self.run_reason = ''

    def run_succeeded(self) -> None:
        """Complete a run whose output was retrieved."""
        if self.run_status != RunStatus.RUNNING:
            raise InvalidTransitionError(f'cannot succeed from {self.run_status}')
        self.run_status = RunStatus.SUCCEEDED
        self.run_reason = ''

    def run_failed(self, reason: str) -> None:
        """Finish an unfinished run with a recorded failure reason."""
        if self.run_status not in RUN_UNFINISHED:
            raise InvalidTransitionError(f'cannot fail from {self.run_status}')
        if not reason:
            raise ValueError('run failure requires a reason')
        self.run_status = RunStatus.FAILED
        self.run_reason = reason
        self.run_paused_from = None
        if self.verification_status != VerificationStatus.CANCELLED:
            self.verification_status = VerificationStatus.CANCELLED
            self.verification_paused_from = None
            self.verification_reason = reason

    def dispatch_verification(self) -> None:
        """Queue a verification after the run succeeded, preserving a pending pause."""
        if self.run_status != RunStatus.SUCCEEDED:
            raise InvalidTransitionError(f'cannot dispatch verification from run status {self.run_status}')
        if self.verification_dispatched and self.verification_status in VERIFICATION_UNFINISHED:
            raise InvalidTransitionError(f'verification is already {self.verification_status}')
        if self.verification_status == VerificationStatus.PAUSED:
            self.verification_dispatched = True
            return
        self.verification_status = VerificationStatus.QUEUED
        self.verification_reason = ''
        self.verification_paused_from = None
        self.verification_dispatched = True

    def start_verification(self) -> None:
        """Move a queued verification into preparation."""
        if self.verification_status != VerificationStatus.QUEUED:
            raise InvalidTransitionError(f'cannot prepare verification from {self.verification_status}')
        self.verification_status = VerificationStatus.PREPARING_VERIFY
        self.verification_reason = ''

    def mark_verifying(self) -> None:
        """Move a prepared verification into the verifying stage."""
        if self.verification_status != VerificationStatus.PREPARING_VERIFY:
            raise InvalidTransitionError(f'cannot verify from {self.verification_status}')
        self.verification_status = VerificationStatus.VERIFYING
        self.verification_reason = ''

    def verification_succeeded(self) -> None:
        """Complete a verification that produced its score."""
        if self.verification_status != VerificationStatus.VERIFYING:
            raise InvalidTransitionError(f'cannot succeed verification from {self.verification_status}')
        self.verification_status = VerificationStatus.SUCCEEDED
        self.verification_reason = ''

    def verification_failed(self, reason: str) -> None:
        """Finish an unfinished verification with a recorded failure reason."""
        if self.verification_status not in VERIFICATION_UNFINISHED:
            raise InvalidTransitionError(f'cannot fail verification from {self.verification_status}')
        if not reason:
            raise ValueError('verification failure requires a reason')
        self.verification_status = VerificationStatus.FAILED
        self.verification_reason = reason
        self.verification_paused_from = None

    def pause(self, reason: str) -> None:
        """Pause the unfinished stage the control matrix selects."""
        if self.run_status in RUN_UNFINISHED:
            if self.run_status != RunStatus.PAUSED:
                self.run_paused_from = self.run_status
                self.run_status = RunStatus.PAUSED
                self.run_reason = reason
            return
        if self.run_status == RunStatus.SUCCEEDED and self.verification_status in VERIFICATION_UNFINISHED:
            if self.verification_status != VerificationStatus.PAUSED:
                self.verification_paused_from = self.verification_status
                self.verification_status = VerificationStatus.PAUSED
                self.verification_reason = reason
            return
        raise InvalidTransitionError('run and verification are already finished')

    def resume(self) -> None:
        """Restore the stage retained by the most recent pause."""
        if self.run_status == RunStatus.PAUSED and self.run_paused_from is not None:
            self.run_status = self.run_paused_from
            self.run_paused_from = None
            self.run_reason = ''
            return
        if (self.run_status == RunStatus.SUCCEEDED and self.verification_status == VerificationStatus.PAUSED
                and self.verification_paused_from is not None):
            self.verification_status = self.verification_paused_from
            self.verification_paused_from = None
            self.verification_reason = ''
            return
        raise InvalidTransitionError('neither run nor verification is paused')

    def cancel(self, reason: str) -> None:
        """Cancel both stages, leaving an already cancelled stage unchanged."""
        if not reason:
            raise ValueError('cancellation requires a reason')
        if self.run_status != RunStatus.CANCELLED:
            self.run_status = RunStatus.CANCELLED
            self.run_reason = reason
            self.run_paused_from = None
        if self.verification_status != VerificationStatus.CANCELLED:
            self.verification_status = VerificationStatus.CANCELLED
            self.verification_reason = reason
            self.verification_paused_from = None

    def recover(self, reason: str) -> bool:
        """Finish every stage a Worker restart left unfinished; return whether anything changed."""
        changed = False
        if self.run_status in RUN_UNFINISHED:
            self.run_failed(reason)
            changed = True
        elif self.run_status in {RunStatus.FAILED, RunStatus.CANCELLED}:
            if self.verification_status != VerificationStatus.CANCELLED:
                self.verification_status = VerificationStatus.CANCELLED
                self.verification_reason = self.run_reason
                self.verification_paused_from = None
                changed = True
        if self.run_status == RunStatus.SUCCEEDED and self.verification_status in VERIFICATION_UNFINISHED:
            self.verification_failed(reason)
            changed = True
        return changed

    def restore(
        self,
        run_status: str,
        run_reason: str,
        run_paused_from: str | None,
        verification_status: str,
        verification_reason: str,
        verification_paused_from: str | None,
        verification_dispatched: bool,
    ) -> None:
        """Load one persisted status pair and check its internal consistency."""
        self.run_status = RunStatus(run_status)
        self.run_reason = run_reason
        self.run_paused_from = RunStatus(run_paused_from) if run_paused_from is not None else None
        self.verification_status = VerificationStatus(verification_status)
        self.verification_reason = verification_reason
        self.verification_paused_from = (
            VerificationStatus(verification_paused_from) if verification_paused_from is not None else None
        )
        self.verification_dispatched = verification_dispatched
        if self.run_status == RunStatus.PAUSED:
            if self.run_paused_from is None or self.run_paused_from not in RUN_UNFINISHED - {RunStatus.PAUSED}:
                raise InvalidTransitionError('paused run has no restorable status')
        elif self.run_paused_from is not None:
            raise InvalidTransitionError('run holds a pause source while not paused')
        if self.verification_status == VerificationStatus.PAUSED:
            if (self.verification_paused_from is None
                    or self.verification_paused_from not in VERIFICATION_UNFINISHED - {VerificationStatus.PAUSED}):
                raise InvalidTransitionError('paused verification has no restorable status')
        elif self.verification_paused_from is not None:
            raise InvalidTransitionError('verification holds a pause source while not paused')
