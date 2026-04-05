"""P8-T15: Cassette-based integration tests — record and replay LLM interactions."""

import json
import pytest
from pathlib import Path
from dataclasses import dataclass, field, asdict
from typing import Any


@dataclass
class CassetteInteraction:
    """A single recorded LLM interaction."""
    request_model: str = ""
    request_system: str = ""
    request_messages: list[dict] = field(default_factory=list)
    response_text: str = ""
    response_usage: dict = field(default_factory=lambda: {"input_tokens": 100, "output_tokens": 200})


@dataclass
class Cassette:
    """A recorded sequence of LLM interactions for replay."""
    name: str = ""
    interactions: list[CassetteInteraction] = field(default_factory=list)

    def save(self, path: Path) -> None:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "w") as f:
            json.dump(asdict(self), f, indent=2)

    @classmethod
    def load(cls, path: Path) -> "Cassette":
        with open(path) as f:
            data = json.load(f)
        interactions = [CassetteInteraction(**i) for i in data.get("interactions", [])]
        return cls(name=data.get("name", ""), interactions=interactions)


class CassettePlayer:
    """Replays recorded LLM interactions for deterministic testing."""

    def __init__(self, cassette: Cassette) -> None:
        self.cassette = cassette
        self.call_index = 0

    async def create_message(self, **kwargs) -> dict[str, Any]:
        if self.call_index >= len(self.cassette.interactions):
            return {"text": "No more recorded interactions", "tool_calls": [], "content": [], "input_tokens": 0, "output_tokens": 0}
        interaction = self.cassette.interactions[self.call_index]
        self.call_index += 1
        return {
            "text": interaction.response_text,
            "tool_calls": [],
            "content": [],
            "input_tokens": interaction.response_usage.get("input_tokens", 0),
            "output_tokens": interaction.response_usage.get("output_tokens", 0),
        }

    @property
    def calls_made(self) -> int:
        return self.call_index


# ── Tests ────────────────────────────────────────

def test_cassette_save_and_load(tmp_path):
    cassette = Cassette(
        name="test_workflow",
        interactions=[
            CassetteInteraction(
                request_model="claude-sonnet-4-6",
                response_text="Here is the PRD document...",
                response_usage={"input_tokens": 500, "output_tokens": 1500},
            ),
            CassetteInteraction(
                request_model="claude-sonnet-4-6",
                response_text="Here are the user stories...",
                response_usage={"input_tokens": 800, "output_tokens": 2000},
            ),
        ],
    )
    path = tmp_path / "cassettes" / "test.json"
    cassette.save(path)
    assert path.exists()

    loaded = Cassette.load(path)
    assert loaded.name == "test_workflow"
    assert len(loaded.interactions) == 2
    assert loaded.interactions[0].response_text == "Here is the PRD document..."


def test_cassette_player_replays_in_order(tmp_path):
    cassette = Cassette(
        name="replay_test",
        interactions=[
            CassetteInteraction(response_text="First response"),
            CassetteInteraction(response_text="Second response"),
        ],
    )
    player = CassettePlayer(cassette)
    import asyncio

    async def play():
        r1 = await player.create_message(model="test")
        assert r1["text"] == "First response"
        r2 = await player.create_message(model="test")
        assert r2["text"] == "Second response"
        assert player.calls_made == 2

    asyncio.run(play())


def test_cassette_player_handles_exhaustion():
    cassette = Cassette(name="empty", interactions=[])
    player = CassettePlayer(cassette)
    import asyncio

    async def play():
        r = await player.create_message(model="test")
        assert "No more" in r["text"]

    asyncio.run(play())


def test_cassette_directory_structure(tmp_path):
    """Cassettes stored in tests/cassettes/ with workflow name."""
    cassette_dir = tmp_path / "tests" / "cassettes"
    c1 = Cassette(name="feature_workflow", interactions=[CassetteInteraction(response_text="ok")])
    c2 = Cassette(name="bugfix_workflow", interactions=[CassetteInteraction(response_text="fixed")])
    c1.save(cassette_dir / "feature_workflow.json")
    c2.save(cassette_dir / "bugfix_workflow.json")

    files = list(cassette_dir.glob("*.json"))
    assert len(files) == 2
