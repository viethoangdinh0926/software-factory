"""Quick checks for per-session interaction leases."""

from architect_agent.session_presence import SessionBusyError, SessionPresence


def test_sessions_do_not_block_each_other() -> None:
    presence = SessionPresence()
    presence.heartbeat("a", "holder-a")
    presence.heartbeat("b", "holder-b")
    presence.require("a", "holder-a")
    presence.require("b", "holder-b")


def test_same_session_second_holder_is_blocked() -> None:
    presence = SessionPresence()
    presence.heartbeat("s", "one")
    try:
        presence.heartbeat("s", "two")
    except SessionBusyError:
        pass
    else:
        raise AssertionError("second holder should be rejected")
    try:
        presence.require("s", "two")
    except SessionBusyError:
        pass
    else:
        raise AssertionError("non-holder mutation should be rejected")
    snap = presence.snapshot("s", "two")
    assert snap["locked"] is True
    assert snap["interactive"] is False


def test_no_lease_allows_scripts() -> None:
    presence = SessionPresence()
    presence.require("s", None)
    presence.require("s", "anyone")


def test_release_lets_next_holder_in() -> None:
    presence = SessionPresence()
    presence.heartbeat("s", "one")
    presence.release("s", "one")
    presence.heartbeat("s", "two")
    presence.require("s", "two")


if __name__ == "__main__":
    test_sessions_do_not_block_each_other()
    test_same_session_second_holder_is_blocked()
    test_no_lease_allows_scripts()
    test_release_lets_next_holder_in()
    print("session presence ok")
