from ptu.common.state import StateStore


def test_state_round_trip_and_diff(tmp_path):
    seed = tmp_path / "seed"
    seed.mkdir()
    (seed / "github.json").write_text('{"repos":{"tidewater/web":{"x":1}}}')
    state = StateStore.load(seed)
    before = state.snapshot()
    state.get("github.repos['tidewater/web']")["x"] = 2
    after = state.snapshot()
    assert before["github"]["repos"]["tidewater/web"]["x"] == 1
    assert state.digest() != StateStore(before).digest()
    refs = StateStore.diff(before, after, lambda service, path: f"{service}:{'/'.join(path)}", set())
    assert refs == {"github:repos/tidewater/web/x"}
    state.dump(tmp_path / "out")
    assert StateStore.load(tmp_path / "out").digest() == state.digest()
