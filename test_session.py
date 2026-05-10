#!/usr/bin/env python3
"""test_session.py — Standalone tests for ConversationSession state machine."""

import asyncio
from voice_satellite.session import ConversationSession, SessionState


async def test_initial_state():
    s = ConversationSession()
    assert s.state == SessionState.PASSIVE_LISTENING
    assert s.is_passive()
    assert not s.is_active()
    print("PASS: initial state is passive_listening")


async def test_state_transitions():
    s = ConversationSession()
    s.set_state(SessionState.ACTIVE_LISTENING)
    assert s.state == SessionState.ACTIVE_LISTENING
    assert s.is_active()
    assert not s.is_passive()

    s.set_state(SessionState.SPEAKING)
    assert s.is_busy()

    s.set_state(SessionState.PASSIVE_LISTENING)
    assert s.is_passive()
    print("PASS: state transitions work")


async def test_silence_timer_fires():
    fired = []

    s = ConversationSession(silence_timeout=0.1)

    async def on_expire():
        fired.append(True)

    s.set_silence_callback(on_expire)
    s.set_state(SessionState.ACTIVE_LISTENING)
    s.reset_silence_timer()

    await asyncio.sleep(0.25)
    assert fired, "Silence timer did not fire"
    print("PASS: silence timer fires after timeout")


async def test_silence_timer_reset():
    fired = []

    s = ConversationSession(silence_timeout=0.15)

    async def on_expire():
        fired.append(True)

    s.set_silence_callback(on_expire)
    s.reset_silence_timer()
    await asyncio.sleep(0.08)
    s.reset_silence_timer()  # reset before it fires
    await asyncio.sleep(0.08)
    assert not fired, "Timer should not have fired yet after reset"
    await asyncio.sleep(0.15)
    assert fired, "Timer should have fired after second interval"
    print("PASS: silence timer resets correctly")


async def test_cancel_generation_no_task():
    s = ConversationSession()
    partial = await s.cancel_generation()
    assert partial == ""
    print("PASS: cancel_generation with no task returns empty string")


async def test_barge_in_partial_context():
    s = ConversationSession()
    response = "Ordis is happy to check the weave for you Operator. Let me run a diagnostic now."
    s.set_current_response(response)
    s.update_words_played(6)  # "Ordis is happy to check the"

    # Create a dummy never-ending task
    async def _forever():
        await asyncio.sleep(999)

    task = asyncio.create_task(_forever())
    s.set_generation_task(task)

    partial = await s.cancel_generation()
    words = response.split()
    expected = " ".join(words[:6])
    assert partial == expected, f"Got {partial!r}, expected {expected!r}"
    print(f"PASS: barge-in partial context: {partial!r}")


async def test_words_played_update():
    s = ConversationSession()
    s.update_words_played(10)
    assert s._words_played_before_interrupt == 10
    s.update_words_played(20)
    assert s._words_played_before_interrupt == 20
    print("PASS: words_played updates correctly")


async def test_close_cancels_timer():
    s = ConversationSession(silence_timeout=99)
    fired = []

    async def on_expire():
        fired.append(True)

    s.set_silence_callback(on_expire)
    s.reset_silence_timer()
    await s.close()
    await asyncio.sleep(0.05)
    assert not fired, "Timer should not fire after close()"
    print("PASS: close() cancels silence timer")


async def main():
    await test_initial_state()
    await test_state_transitions()
    await test_silence_timer_fires()
    await test_silence_timer_reset()
    await test_cancel_generation_no_task()
    await test_barge_in_partial_context()
    await test_words_played_update()
    await test_close_cancels_timer()
    print("\nAll session tests passed.")


if __name__ == "__main__":
    asyncio.run(main())
