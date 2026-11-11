"""checks/existing_members.py: members of code that existed before the feature, asserted by the
feature's tests without a fixture precondition test (a note for the test reviewer)."""

from __future__ import annotations

import pathlib

from agentflow.adapters import get_adapter
from agentflow.checks import existing_members
from agentflow.config import parse_config
from conftest import git, init_git


def write(root: pathlib.Path, rel: str, text: str) -> None:
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(text, encoding="utf-8")


def commit(root: pathlib.Path, msg: str) -> None:
    git(root, "add", "-A")
    git(root, "commit", "-q", "-m", msg)


RECORD_CS = """namespace Game
{
    public sealed class KeyMomentRecord
    {
        public int Tick { get; init; }
        /// <summary>Foreseen goal: set only in MomentPlan.TryStartMoment.</summary>
        public bool Goal { get; init; }
    }

    public record Point(double Px, double Py);
}
"""

REVIEW_CS = """namespace Game
{
    public sealed class FreeKickReview
    {
        public StrikeOutcome Outcome { get; init; }
        public double AimY { get; init; }
        public KeyMomentRecord Rec { get; init; } = new();
    }
}
"""

TESTS_CS = """using System.Linq;
using Game;
using Xunit;

namespace Game.Tests
{
    public class SetPieceReviewMatchTests
    {
        [Fact]
        public void FreeKick_ReviewMatchesTheGoal()
        {
            var review = Build();
            var rec = review.Rec;
            // a comment naming rec.Tick is not an assertion
            Assert.Equal(rec.Goal, review.Outcome == StrikeOutcome.Goal);
            Assert.True(review.AimY > 0, "rec.Tick in a string");
            Assert.Contains(new[] { rec }, r => r.Goal);
            Assert.Equal(1.0, new Point(1, 2).Px);
        }

        [Fact]
        public void Fixture_TheSeedHasAWalledKick()
        {
            var rec = Build().Rec;
            Assert.True(rec.Tick > 0);
        }

        private static FreeKickReview Build() => new();
    }
}
"""


def dotnet_repo(tmp_path: pathlib.Path) -> pathlib.Path:
    """main: the existing KeyMomentRecord; feat/f: the review stub (spec commit, tagged) and the
    tests (uncommitted, as at the TESTS checks)."""
    init_git(tmp_path)
    write(tmp_path, "src/Game/KeyMoments.cs", RECORD_CS)
    commit(tmp_path, "base")
    git(tmp_path, "checkout", "-q", "-b", "feat/f")
    write(tmp_path, "src/Game/FreeKickReview.cs", REVIEW_CS)
    commit(tmp_path, "f: spec approved")
    git(tmp_path, "tag", "spec-approved/f")
    write(tmp_path, "tests/Game.Tests/SetPieceReviewMatchTests.cs", TESTS_CS)
    return tmp_path


def test_dotnet_lists_existing_members_without_a_fixture_test(tmp_path: pathlib.Path) -> None:
    """Seen live: `rec.Goal` (an existing flag with another meaning) asserted as the moment's
    goal event; the red run could not see it. Members of the feature's stubs (`Outcome`,
    `AimY`), members a Fixture_* test asserts (`Tick`), enum values (`StrikeOutcome.Goal`) and
    text in comments and strings are not listed."""
    root = dotnet_repo(tmp_path)
    a = get_adapter(parse_config(root, {"project": {"language": "dotnet"}}))
    res = existing_members.check(a, "f")
    assert res.status == "PASS"
    listed = [line for line in res.output.splitlines() if line.startswith("- ")]
    test = "tests/Game.Tests/SetPieceReviewMatchTests.cs"
    assert listed == [
        f"- KeyMomentRecord.Goal (src/Game/KeyMoments.cs:7): asserted at {test}:15, {test}:17"
    ]
    assert "does the test read the member the spec means" in res.output
    # the positional record parameter is a member too (asserted only via a new object here: the
    # chain starts upper case at `new Point`, so it is not read from a local)
    assert "Px" not in res.output
    # a fixture precondition test that asserts the claim on the existing code clears it
    write(
        root,
        "tests/Game.Tests/FixtureTests.cs",
        "using Xunit;\nnamespace Game.Tests\n{\n    public class FixtureTests\n    {\n"
        "        [Fact]\n        public void Fixture_GoalFlagOnScoringKicks()\n        {\n"
        "            var rec = new Game.KeyMomentRecord();\n            Assert.False(rec.Goal);\n"
        "        }\n    }\n}\n",
    )
    res = existing_members.check(a, "f")
    assert "No member of existing code is asserted" in res.output


def test_dotnet_base_without_a_feature_branch(tmp_path: pathlib.Path) -> None:
    """Working on the base branch itself: the spec commit (with the dialogue's stubs) is the
    merge base, so the commit before it is where the feature's code starts."""
    init_git(tmp_path)
    write(tmp_path, "src/Game/KeyMoments.cs", RECORD_CS)
    commit(tmp_path, "base")
    write(tmp_path, "src/Game/FreeKickReview.cs", REVIEW_CS)
    commit(tmp_path, "f: spec approved")
    git(tmp_path, "tag", "spec-approved/f")
    write(tmp_path, "tests/Game.Tests/SetPieceReviewMatchTests.cs", TESTS_CS)
    a = get_adapter(parse_config(tmp_path, {"project": {"language": "dotnet"}}))
    assert existing_members.base_ref(a, "f") == "spec-approved/f~1"
    out = existing_members.check(a, "f").output
    assert "KeyMomentRecord.Goal" in out and "AimY" not in out


def test_python_lists_existing_members(tmp_path: pathlib.Path) -> None:
    init_git(tmp_path)
    write(tmp_path, "pyproject.toml", "[project]\nname = 'x'\n")
    write(
        tmp_path,
        "src/pkg/moments.py",
        "class Record:\n    tick: int = 0\n\n    def __init__(self) -> None:\n"
        "        self.goal = False\n",
    )
    commit(tmp_path, "base")
    git(tmp_path, "checkout", "-q", "-b", "feat/f")
    write(
        tmp_path,
        "src/pkg/review.py",
        "from pkg.moments import Record\n\n\nclass Review:\n    outcome: str = ''\n"
        "    rec: Record = Record()\n",
    )
    commit(tmp_path, "f: spec approved")
    git(tmp_path, "tag", "spec-approved/f")
    write(
        tmp_path,
        "tests/test_review.py",
        "from pkg.review import Review\n\n\ndef test_review_goal() -> None:\n"
        "    review = Review()\n    assert review.rec.goal == (review.outcome == 'goal')\n"
        "    assert review.rec.tick >= 0\n\n\ndef test_fixture_tick() -> None:\n"
        "    assert Review().rec.tick == 0\n",
    )
    a = get_adapter(parse_config(tmp_path, {}))
    res = existing_members.check(a, "f")
    listed = [line for line in res.output.splitlines() if line.startswith("- ")]
    assert listed == ["- Record.goal (src/pkg/moments.py:5): asserted at tests/test_review.py:6"]


def test_generic_adapter_skips(tmp_path: pathlib.Path) -> None:
    init_git(tmp_path)
    data = {
        "project": {"language": "generic"},
        "generic": {
            "test_all": ["x"],
            "test_ids": ["x"],
            "tag_pattern": "x",
            "test_files": ["**/*.x"],
        },
    }
    a = get_adapter(parse_config(tmp_path, data))
    assert existing_members.check(a, "f").status == "SKIPPED"
