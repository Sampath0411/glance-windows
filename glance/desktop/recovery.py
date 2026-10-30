"""Policy for recovering a previously active background agent."""


def should_recover_agent(
    was_active_this_session: bool,
    unlock_installed: bool,
    agent_running: bool,
    paused_until_wake: bool,
) -> bool:
    """Recover only unexpected exits; never override setup, pause, or live state."""
    return (
        was_active_this_session
        and unlock_installed
        and not agent_running
        and not paused_until_wake
    )
