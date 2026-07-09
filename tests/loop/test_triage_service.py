from __future__ import annotations

import asyncio

from orchlink.loop.adapters.state_repo import LoopStateRepo
from orchlink.loop.domain import LoopItemState
from orchlink.loop.services import ItemCandidate, LoopService, SkillRef, TriageService


class FakeConnector:
    name = "fake"

    def __init__(self, candidates):
        self.candidates = candidates

    async def discover(self):
        return list(self.candidates)


class RaisingConnector:
    name = "raising"

    async def discover(self):
        raise RuntimeError("boom")


def make_service(tmp_path, connectors):
    loop_service = LoopService({}, LoopStateRepo(tmp_path))
    return loop_service, TriageService({}, loop_service, connectors)


def test_run_once_creates_items_from_single_connector(tmp_path):
    loop_service, triage = make_service(
        tmp_path,
        [FakeConnector([ItemCandidate(id="C-1", source_type="manual", source_ref="one", title="One", objective="Do one")])],
    )

    created = asyncio.run(triage.run_once())

    assert [item.item_id for item in created] == ["C-1"]
    assert loop_service.get("C-1").state is LoopItemState.TRIAGED


def test_run_once_skips_duplicates_by_source_ref_and_does_not_overwrite(tmp_path):
    loop_service, triage = make_service(
        tmp_path,
        [FakeConnector([ItemCandidate(id="C-1", source_type="github", source_ref="issue/1", title="Original", objective="Do")])],
    )
    asyncio.run(triage.run_once())
    _, triage_again = make_service(
        tmp_path,
        [
            FakeConnector(
                [
                    ItemCandidate(id="C-2", source_type="github", source_ref="issue/1", title="Changed", objective="Overwrite"),
                    ItemCandidate(id="C-3", source_type="github", source_ref="issue/2", title="New", objective="Do new"),
                ]
            )
        ],
    )

    created = asyncio.run(triage_again.run_once())

    assert [item.item_id for item in created] == ["C-3"]
    assert loop_service.get("C-1").title == "Original"
    assert loop_service.get("C-2") is None


def test_run_once_continues_when_connector_raises(tmp_path):
    loop_service, triage = make_service(
        tmp_path,
        [RaisingConnector(), FakeConnector([ItemCandidate(id="C-1", source_type="manual", source_ref="ok", title="Ok", objective="Do")])],
    )

    created = asyncio.run(triage.run_once())

    assert [item.item_id for item in created] == ["C-1"]
    assert loop_service.get("C-1") is not None


def test_item_candidate_normalizes_legacy_git_source_type():
    candidate = ItemCandidate(id="C-1", source_type="git", source_ref="abc", title="T", objective="O")

    assert candidate.source_type == "local_git"
    assert candidate.source == "local_git:abc"


def test_empty_source_ref_dedupes_by_item_id_not_source_pair(tmp_path):
    loop_service, triage = make_service(
        tmp_path,
        [
            FakeConnector(
                [
                    ItemCandidate(id="M-1", source_type="manual", source_ref="", title="One", objective="Do one"),
                    ItemCandidate(id="M-2", source_type="manual", source_ref="", title="Two", objective="Do two"),
                ]
            )
        ],
    )

    created = asyncio.run(triage.run_once())

    assert [item.item_id for item in created] == ["M-1", "M-2"]
    assert loop_service.get("M-1") is not None
    assert loop_service.get("M-2") is not None


def test_suggested_skill_is_preserved_on_created_loop_item(tmp_path):
    loop_service, triage = make_service(
        tmp_path,
        [
            FakeConnector(
                [
                    ItemCandidate(
                        id="C-1",
                        source_type="manual",
                        source_ref="skill",
                        title="Skill",
                        objective="Use skill",
                        suggested_skill=SkillRef(name="review", path="/skills/review.md"),
                    )
                ]
            )
        ],
    )

    asyncio.run(triage.run_once())

    item = loop_service.get("C-1")
    assert item.skill.name == "review"
    assert item.skill.path == "/skills/review.md"


def test_run_once_returns_empty_without_connectors(tmp_path):
    _, triage = make_service(tmp_path, [])

    assert asyncio.run(triage.run_once()) == []


def test_items_created_land_in_triaged_state(tmp_path):
    loop_service, triage = make_service(
        tmp_path,
        [FakeConnector([ItemCandidate(id="C-1", source_type="linear", source_ref="LIN-1", title="Ticket", objective="Do")])],
    )

    asyncio.run(triage.run_once())

    assert loop_service.get("C-1").state is LoopItemState.TRIAGED


def test_triage_service_wires_real_loop_service_and_repo(tmp_path):
    repo = LoopStateRepo(tmp_path)
    loop_service = LoopService({}, repo)
    triage = TriageService(
        {},
        loop_service,
        [FakeConnector([ItemCandidate(id="C-1", source_type="local_git", source_ref="dirty_tree", title="Dirty", objective="Clean")])],
    )

    created = asyncio.run(triage.run_once())

    assert len(created) == 1
    assert LoopStateRepo(tmp_path).read_only().item("C-1").state is LoopItemState.TRIAGED
