"""Integration tests.
 
These run against a real Postgres, because the guarantee in Problem 4 is a
database constraint. Testing it against SQLite or a mock would test something
other than what actually ships.
 
    docker compose up -d db
    cd backend && DATABASE_URL=postgresql+asyncpg://portal:portal@localhost:5432/portal \
        pytest -v
"""
 
import asyncio
import threading
import time
from datetime import UTC, datetime, timedelta
 
import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from sqlalchemy import delete, select
 
from app.db import SessionLocal
from app.main import app
from app.models import Appointment, AppointmentHistory, Role, User
 
pytestmark = pytest.mark.asyncio
 
 
def soon(days: int = 4, hour: int = 9, minute: int = 0) -> str:
    when = (datetime.now(UTC) + timedelta(days=days)).replace(
        hour=hour, minute=minute, second=0, microsecond=0
    )
    return when.isoformat()
 
 
@pytest.fixture(scope="session")
def live_server():
    """A real uvicorn server on a spare port.
 
    Only the Problem 3 timing test needs this; everything else goes through
    the faster in-process transport.
    """
    import uvicorn
 
    config = uvicorn.Config(app, host="127.0.0.1", port=8021, log_level="warning")
    server = uvicorn.Server(config)
    threading.Thread(target=server.run, daemon=True).start()
 
    deadline = time.time() + 30
    while not server.started and time.time() < deadline:
        time.sleep(0.1)
    if not server.started:
        pytest.skip("uvicorn did not start")
 
    yield "http://127.0.0.1:8021"
    server.should_exit = True
 
 
@pytest_asyncio.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as c:
        yield c
 
 
@pytest_asyncio.fixture(autouse=True)
async def clean():
    """Drop appointments between tests; seeded users stay."""
    async with SessionLocal() as s:
        await s.execute(delete(AppointmentHistory))
        await s.execute(delete(Appointment))
        await s.commit()
    yield
 
 
@pytest_asyncio.fixture
async def people():
    async with SessionLocal() as s:
        patients = (
            await s.execute(select(User).where(User.role == Role.patient))
        ).scalars().all()
        providers = (
            await s.execute(select(User).where(User.role == Role.provider))
        ).scalars().all()
    return {"patients": list(patients), "providers": list(providers)}
 
 
def as_user(user) -> dict[str, str]:
    return {"X-User-Id": str(user.id)}
 
 
async def make_request(client, patient, provider, *, when=None, kind="consultation"):
    res = await client.post(
        "/api/appointments",
        headers=as_user(patient),
        json={
            "provider_id": provider.id,
            "starts_at": when or soon(),
            "appointment_type": kind,
            "reason": "Test request",
        },
    )
    assert res.status_code == 201, res.text
    return res.json()
 
 
# --------------------------------------------------------------- baseline
 
 
async def test_request_lands_in_pending_with_derived_end_time(client, people):
    patient, provider = people["patients"][0], people["providers"][0]
    appt = await make_request(client, patient, provider, kind="follow_up")
 
    assert appt["status"] == "pending"
    assert appt["version"] == 1
    # 15 minutes for a follow-up, derived server-side from the type.
    start = datetime.fromisoformat(appt["starts_at"])
    end = datetime.fromisoformat(appt["ends_at"])
    assert end - start == timedelta(minutes=15)
 
 
async def test_pending_cannot_be_cancelled(client, people):
    patient, provider = people["patients"][0], people["providers"][0]
    appt = await make_request(client, patient, provider)
 
    res = await client.post(
        f"/api/appointments/{appt['id']}/cancel",
        headers=as_user(patient),
        json={"expected_version": appt["version"]},
    )
    assert res.status_code == 409
 
 
async def test_confirm_then_cancel(client, people):
    patient, provider = people["patients"][0], people["providers"][0]
    appt = await make_request(client, patient, provider)
 
    confirmed = (
        await client.post(
            f"/api/appointments/{appt['id']}/confirm",
            headers=as_user(provider),
            json={"expected_version": appt["version"]},
        )
    ).json()
    assert confirmed["status"] == "confirmed"
    assert confirmed["version"] == 2
 
    cancelled = (
        await client.post(
            f"/api/appointments/{appt['id']}/cancel",
            headers=as_user(patient),
            json={"expected_version": confirmed["version"]},
        )
    ).json()
    assert cancelled["status"] == "cancelled"
 
 
async def test_reschedule_does_not_confirm(client, people):
    """Confirm is the only transition that changes status."""
    patient, provider = people["patients"][0], people["providers"][0]
    appt = await make_request(client, patient, provider)
 
    moved = (
        await client.patch(
            f"/api/appointments/{appt['id']}/reschedule",
            headers=as_user(provider),
            json={"starts_at": soon(hour=15), "expected_version": appt["version"]},
        )
    ).json()
 
    assert moved["status"] == "pending"
    assert moved["starts_at"] != appt["starts_at"]
 
 
async def test_ownership_is_enforced_server_side(client, people):
    """Hiding a button is not the rule. This is."""
    patient, provider = people["patients"][0], people["providers"][0]
    other_provider = people["providers"][1]
    appt = await make_request(client, patient, provider)
 
    res = await client.post(
        f"/api/appointments/{appt['id']}/confirm",
        headers=as_user(other_provider),
        json={"expected_version": appt["version"]},
    )
    assert res.status_code == 403
 
 
async def test_patient_cannot_confirm(client, people):
    patient, provider = people["patients"][0], people["providers"][0]
    appt = await make_request(client, patient, provider)
 
    res = await client.post(
        f"/api/appointments/{appt['id']}/confirm",
        headers=as_user(patient),
        json={"expected_version": appt["version"]},
    )
    assert res.status_code == 403
 
 
async def test_missing_identity_is_rejected(client):
    assert (await client.get("/api/appointments")).status_code == 401
 
 
# ------------------------------------------------------- Problem 1: stale
 
 
async def test_cancel_against_a_stale_version_is_refused(client, people):
    """The live case: the patient's screen still says 2:00.
 
    Appointment is confirmed. The patient loads it. The provider moves it.
    Status stays confirmed, so the cancel button never disappeared. The
    patient clicks cancel on a time they can no longer see.
    """
    patient, provider = people["patients"][0], people["providers"][0]
    appt = await make_request(client, patient, provider, when=soon(hour=14))
 
    confirmed = (
        await client.post(
            f"/api/appointments/{appt['id']}/confirm",
            headers=as_user(provider),
            json={"expected_version": appt["version"]},
        )
    ).json()
 
    # What the patient's screen is holding.
    stale_version = confirmed["version"]
 
    # Provider moves it to 16:00.
    await client.patch(
        f"/api/appointments/{appt['id']}/reschedule",
        headers=as_user(provider),
        json={"starts_at": soon(hour=16), "expected_version": stale_version},
    )
 
    res = await client.post(
        f"/api/appointments/{appt['id']}/cancel",
        headers=as_user(patient),
        json={"expected_version": stale_version},
    )
    assert res.status_code == 409
    assert res.json()["detail"]["code"] == "stale_version"
 
    # Nothing was written.
    async with SessionLocal() as s:
        row = await s.get(Appointment, appt["id"])
        assert row.status.value == "confirmed"
 
 
async def test_cancel_succeeds_once_the_patient_has_seen_the_change(client, people):
    """The 409 is not a dead end: re-ask against current truth."""
    patient, provider = people["patients"][0], people["providers"][0]
    appt = await make_request(client, patient, provider, when=soon(hour=14))
 
    confirmed = (
        await client.post(
            f"/api/appointments/{appt['id']}/confirm",
            headers=as_user(provider),
            json={"expected_version": appt["version"]},
        )
    ).json()
    await client.patch(
        f"/api/appointments/{appt['id']}/reschedule",
        headers=as_user(provider),
        json={"starts_at": soon(hour=16), "expected_version": confirmed["version"]},
    )
 
    fresh = (
        await client.get(f"/api/appointments/{appt['id']}", headers=as_user(patient))
    ).json()
    res = await client.post(
        f"/api/appointments/{appt['id']}/cancel",
        headers=as_user(patient),
        json={"expected_version": fresh["version"]},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "cancelled"
 
 
# ----------------------------------------------------- Problem 2: history
 
 
async def test_history_answers_when_who_and_what_before(client, people):
    patient, provider = people["patients"][0], people["providers"][0]
    appt = await make_request(client, patient, provider, when=soon(hour=9))
 
    v = appt["version"]
    moved = (
        await client.patch(
            f"/api/appointments/{appt['id']}/reschedule",
            headers=as_user(provider),
            json={"starts_at": soon(hour=11), "expected_version": v},
        )
    ).json()
    await client.post(
        f"/api/appointments/{appt['id']}/confirm",
        headers=as_user(provider),
        json={"expected_version": moved["version"]},
    )
 
    detail = (
        await client.get(f"/api/appointments/{appt['id']}", headers=as_user(patient))
    ).json()
    trail = detail["history"]
 
    # created (status + starts_at), rescheduled, confirmed
    assert [h["action"] for h in trail] == [
        "created", "created", "rescheduled", "confirmed",
    ]
 
    move = next(h for h in trail if h["action"] == "rescheduled")
    assert move["actor_name"] == provider.name          # who changed it
    assert move["old_value"] is not None                # what it was before
    assert move["created_at"] is not None               # when it changed
    # Values are structured, not prose, so the old time is a real timestamp.
    assert datetime.fromisoformat(move["old_value"]).hour is not None
 
 
async def test_history_is_not_erased_by_later_changes(client, people):
    patient, provider = people["patients"][0], people["providers"][0]
    appt = await make_request(client, patient, provider)
 
    v = appt["version"]
    for hour in (10, 11, 12):
        moved = (
            await client.patch(
                f"/api/appointments/{appt['id']}/reschedule",
                headers=as_user(provider),
                json={"starts_at": soon(hour=hour), "expected_version": v},
            )
        ).json()
        v = moved["version"]
 
    detail = (
        await client.get(f"/api/appointments/{appt['id']}", headers=as_user(patient))
    ).json()
    assert sum(1 for h in detail["history"] if h["action"] == "rescheduled") == 3
 
 
# ------------------------------------------------ Problem 3: notification
 
 
async def test_confirm_does_not_wait_on_the_notification(live_server, people):
    """A slow notification must not make the confirm slow.
 
    This one runs against a real uvicorn socket rather than ASGITransport.
    The in-process transport awaits background tasks before handing back the
    response, so it would measure the wrong thing: it reports the handler
    plus the background task, where a real client sees only the handler.
    """
    from app import notifications
 
    patient, provider = people["patients"][0], people["providers"][0]
 
    async with AsyncClient(base_url=live_server, timeout=30) as client:
        appt = await make_request(client, patient, provider)
 
        notifications.SIMULATED_DELAY_SECONDS = 3.0
        try:
            started = time.perf_counter()
            res = await client.post(
                f"/api/appointments/{appt['id']}/confirm",
                headers=as_user(provider),
                json={"expected_version": appt["version"]},
            )
            elapsed = time.perf_counter() - started
        finally:
            notifications.SIMULATED_DELAY_SECONDS = 0.0
 
    assert res.status_code == 200
    assert res.json()["status"] == "confirmed"
    assert elapsed < 1.0, f"confirm blocked on the notification ({elapsed:.2f}s)"
 
 
async def test_confirm_survives_a_broken_notification(client, people, monkeypatch):
    from app import notifications
 
    def explode(**_):
        raise RuntimeError("SMTP is down")
 
    monkeypatch.setattr(notifications, "send_confirmation", explode)
 
    patient, provider = people["patients"][0], people["providers"][0]
    appt = await make_request(client, patient, provider)
 
    res = await client.post(
        f"/api/appointments/{appt['id']}/confirm",
        headers=as_user(provider),
        json={"expected_version": appt["version"]},
    )
    assert res.status_code == 200
    assert res.json()["status"] == "confirmed"
 
 
# ------------------------------------------------ Problem 4: double-book
 
 
async def test_overlapping_confirm_is_refused(client, people):
    patient_a, patient_b = people["patients"][0], people["patients"][1]
    provider = people["providers"][0]
 
    first = await make_request(client, patient_a, provider, when=soon(hour=14))
    # 14:15 lands inside the first 30-minute slot.
    second = await make_request(client, patient_b, provider, when=soon(hour=14, minute=15))
 
    ok = await client.post(
        f"/api/appointments/{first['id']}/confirm",
        headers=as_user(provider),
        json={"expected_version": first["version"]},
    )
    assert ok.status_code == 200
 
    clash = await client.post(
        f"/api/appointments/{second['id']}/confirm",
        headers=as_user(provider),
        json={"expected_version": second["version"]},
    )
    assert clash.status_code == 409
    assert clash.json()["detail"]["code"] == "slot_taken"
 
 
async def test_back_to_back_slots_do_not_collide(client, people):
    """Half-open ranges: 14:00-14:30 and 14:30-15:00 are fine."""
    patient_a, patient_b = people["patients"][0], people["patients"][1]
    provider = people["providers"][0]
 
    first = await make_request(client, patient_a, provider, when=soon(hour=14))
    second = await make_request(client, patient_b, provider, when=soon(hour=14, minute=30))
 
    for appt in (first, second):
        res = await client.post(
            f"/api/appointments/{appt['id']}/confirm",
            headers=as_user(provider),
            json={"expected_version": appt["version"]},
        )
        assert res.status_code == 200, res.text
 
 
async def test_two_simultaneous_confirms_produce_exactly_one_booking(client, people):
    """The ugly case, run for real.
 
    Two overlapping pending requests, confirmed concurrently. Both requests
    read "no conflict" before either commits, so the application-level check
    passes twice. The exclusion constraint is what makes the second write
    impossible rather than merely unlikely.
    """
    patient_a, patient_b = people["patients"][0], people["patients"][1]
    provider = people["providers"][0]
 
    a = await make_request(client, patient_a, provider, when=soon(hour=14))
    b = await make_request(client, patient_b, provider, when=soon(hour=14, minute=10))
 
    results = await asyncio.gather(
        client.post(
            f"/api/appointments/{a['id']}/confirm",
            headers=as_user(provider),
            json={"expected_version": a["version"]},
        ),
        client.post(
            f"/api/appointments/{b['id']}/confirm",
            headers=as_user(provider),
            json={"expected_version": b["version"]},
        ),
        return_exceptions=True,
    )
 
    codes = sorted(r.status_code for r in results if hasattr(r, "status_code"))
    assert codes == [200, 409], f"expected one winner and one loser, got {codes}"
 
    async with SessionLocal() as s:
        confirmed = (
            await s.execute(
                select(Appointment).where(
                    Appointment.provider_id == provider.id,
                    Appointment.status == "confirmed",
                )
            )
        ).unique().scalars().all()
    assert len(confirmed) == 1