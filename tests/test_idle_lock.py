def test_idle_policy_disables_and_rearms_after_input():
    from glance.agent.idle_lock import IdlePolicy
    p = IdlePolicy()
    assert not p.due(300000, 1000, 0, False)
    assert p.due(301000, 1000, 300, False)
    p.locked(1000)
    assert not p.due(400000, 1000, 300, False)
    assert not p.due(401000, 400000, 300, False)
    assert p.due(700000, 400000, 300, False)
    assert not p.due(700000, 400000, 300, True)


def test_idle_tick_wraparound():
    from glance.agent.idle_lock import IdlePolicy
    assert IdlePolicy().due(1000, 0xFFFFFFFF - 300000, 300, False)


def test_bad_idle_configuration_is_disabled():
    from glance.agent.idle_lock import idle_seconds
    for config in [{}, {'idle_lock_enabled': False}, {'idle_lock_enabled': True, 'idle_lock_minutes': 'bad'},
                   {'idle_lock_enabled': True, 'idle_lock_minutes': -1}]:
        assert idle_seconds(config) == 0
    assert idle_seconds({'idle_lock_enabled': True}) == 300
