from ptu.common.ids import IdFactory


def test_ids_are_deterministic_by_seed_kind_and_count():
    factory = IdFactory(7)
    assert factory.next("commit") == "c_0001cc4b4618"
    assert factory.next("commit") != factory.next("issue")
    assert IdFactory(7).next("commit") == "c_0001cc4b4618"
