from orchlink.loop.domain import Worktree


def test_worktree_cleanliness_is_constructor_snapshot():
    assert Worktree(path="/tmp/w", clean=True).is_clean() is True
    assert Worktree(path="/tmp/w", clean=False).is_clean() is False
    assert Worktree(path="/tmp/w").is_clean() is None
    assert Worktree(path="/tmp/w", cleanliness_probe=lambda: False).is_clean() is False
