from ptu.common.clock import LogicalClock


def test_clock_increases_and_iso_sorts():
    clock = LogicalClock()
    stamps = [clock.iso()]
    for n in range(1, 70):
        assert clock.tick() == n
        stamps.append(clock.iso())
    assert stamps == sorted(stamps)
    assert clock.now() == 69
