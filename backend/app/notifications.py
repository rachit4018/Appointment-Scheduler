"""Notification stub (Problem 3).
 
Runs via FastAPI's BackgroundTasks, which executes after the response has
been sent. The confirm is already committed and returned by the time
anything here runs, so a slow or broken notification cannot make the confirm
slow and cannot make it fail.
 
Two layers of guard, deliberately:
 
  dispatch()          - the outer one. Catches anything the notification
                        raises, including failures before the inner try is
                        even reached: a bad argument, a missing import, a
                        misconfigured sender.
  send_confirmation() - the inner one. Handles the expected failure, a mail
                        provider that is down, and logs it usefully.
 
The outer guard is what makes "the confirm cannot fail because of the
notification" true by construction rather than by whoever writes the next
notification remembering to be careful. BackgroundTasks runs in the same
process, so an unhandled exception here surfaces in the logs as a failed
request even though the response already went out.
"""
 
import logging
import time
from collections.abc import Callable
from datetime import datetime
 
logger = logging.getLogger("notifications")
 
# Set to a positive number to demonstrate that the confirm still returns
# immediately. Used by the test suite.
SIMULATED_DELAY_SECONDS = 0.0
 
 
def dispatch(fn: Callable[..., None], /, **kwargs) -> None:
    """Run a notification with no way for it to affect the caller."""
    try:
        fn(**kwargs)
    except Exception:
        logger.exception(
            "notification failed (%s); the action that triggered it was "
            "committed and is unaffected",
            getattr(fn, "__name__", repr(fn)),
        )
 
 
def send_confirmation(
    *,
    to_email: str,
    patient_name: str,
    provider_name: str,
    starts_at: datetime,
    appointment_id: int,
) -> None:
    """Stub sender. Logs what a real one would have sent."""
    if SIMULATED_DELAY_SECONDS:
        time.sleep(SIMULATED_DELAY_SECONDS)
 
    logger.info(
        "would send email to %s: Hi %s, your appointment with %s on %s "
        "is confirmed. (appointment #%s)",
        to_email,
        patient_name,
        provider_name,
        starts_at.strftime("%a %d %b %Y at %H:%M %Z"),
        appointment_id,
    )