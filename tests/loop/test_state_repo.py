import json

import pytest

from orchlink.loop.adapters.lock import MkdirLock
from orchlink.loop.adapters.markdown_codec import OPENING_FENCE
from orchlink.loop.adapters.state_repo import LoopStateRepo
from orchlink.loop.domain import LockHeldError, LoopItem, StateCorrupt


def write_state(repo, state):
    repo.state_path.parent.mkdir(parents=True, exist_ok=True)
    repo.state_path.write_text(
        "# Notes\n\n"
        "```yaml orchloop.v1\n"
        f"{json.dumps(state)}\n"
        "```\n",
        encoding="utf-8",
    )


def test_repo_creates_state_file_and_roundtrips_items(tmp_path):
    repo = LoopStateRepo(tmp_path)

    with repo.transaction("tester") as state:
        state.add_item(LoopItem(item_id="I-1", title="first"))

    read = repo.read_only()
    assert len(read.items) == 1
    assert read.items[0].item_id == "I-1"
    assert OPENING_FENCE in repo.state_path.read_text(encoding="utf-8")


def test_markdown_notes_are_preserved_across_write(tmp_path):
    repo = LoopStateRepo(tmp_path)
    repo.state_path.parent.mkdir(parents=True)
    repo.state_path.write_text(
        "# Notes\n\nkeep this before\n\n"
        "```yaml orchloop.v1\n"
        '{"schema_version":"orchloop.v1","items":[]}\n'
        "```\n"
        "\nkeep this after\n",
        encoding="utf-8",
    )

    with repo.transaction("tester") as state:
        state.add_item(LoopItem(item_id="I-1"))

    text = repo.state_path.read_text(encoding="utf-8")
    assert text.startswith("# Notes\n\nkeep this before\n\n")
    assert text.endswith("\nkeep this after\n")
    assert repo.read_only().item("I-1").item_id == "I-1"


def test_concurrent_transactions_one_wins_and_other_gets_lock_error(tmp_path):
    repo = LoopStateRepo(tmp_path)

    with repo.transaction("winner") as state:
        state.add_item(LoopItem(item_id="I-1"))
        with pytest.raises(LockHeldError):
            with repo.transaction("loser"):
                pass

    recovered = repo.read_only()
    assert recovered.item("I-1").item_id == "I-1"


def test_precreated_lock_dir_raises_lockheld_not_fileexists(tmp_path):
    lock_path = tmp_path / ".orch" / "loop" / "state.lock"
    lock_path.mkdir(parents=True)

    with pytest.raises(LockHeldError):
        MkdirLock(lock_path).acquire("tester")


def test_failed_transaction_does_not_write(tmp_path):
    repo = LoopStateRepo(tmp_path)

    with pytest.raises(RuntimeError):
        with repo.transaction("tester") as state:
            state.add_item(LoopItem(item_id="I-1"))
            raise RuntimeError("boom")

    assert repo.read_only().items == ()


def test_corrupt_state_is_rejected(tmp_path):
    repo = LoopStateRepo(tmp_path)
    repo.state_path.parent.mkdir(parents=True)
    repo.state_path.write_text("# no machine block\n", encoding="utf-8")

    with pytest.raises(StateCorrupt):
        repo.read_only()


@pytest.mark.parametrize(
    "state",
    [
        {"schema_version": "orchloop.v1", "items": [{"state": "triaged"}]},
        {"schema_version": "orchloop.v1", "items": [{"item_id": "I-1", "state": "bogus"}]},
        {
            "schema_version": "orchloop.v1",
            "items": [
                {
                    "item_id": "I-1",
                    "state": "triaged",
                    "verify_policy": {"max_concurrent_attempts": 2},
                }
            ],
        },
    ],
)
def test_malformed_machine_state_rehydrates_only_as_statecorrupt(tmp_path, state):
    repo = LoopStateRepo(tmp_path)
    write_state(repo, state)

    with pytest.raises(StateCorrupt):
        repo.read_only()
