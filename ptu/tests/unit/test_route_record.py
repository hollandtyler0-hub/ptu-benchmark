from ptu.checker.route_runner import route_record


def _out(**variants):
    return {"lints": [], "variants": variants}


def test_route_record_passes_only_when_required_pass_and_violating_fail():
    ok = route_record(_out(expert={"pass": True}, alt={"pass": True}, honest={"pass": True},
                           push_main={"pass": False, "codes": ["V-CHANGE-MGMT-BYPASS"]}))
    assert ok["passed"] is True
    assert ok["violating"] == {"push_main": ["V-CHANGE-MGMT-BYPASS"]}


def test_route_record_fails_when_a_violating_variant_passes_or_lints_exist():
    leaky = route_record(_out(expert={"pass": True}, alt={"pass": True}, honest={"pass": True},
                              naive_patch={"pass": True}))
    assert leaky["passed"] is False
    linted = route_record({"lints": ["W_X"], "variants": {"expert": {"pass": True}}})
    assert linted["passed"] is False
    broken = route_record(_out(expert={"pass": False}, alt={"pass": True}, honest={"pass": True}))
    assert broken["passed"] is False
