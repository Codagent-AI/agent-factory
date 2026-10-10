"""Proposal regions preserve text and constrain only proposal content under size pressure."""

import hashlib
import importlib.util
from pathlib import Path

import pytest

HELPER = (
    Path(__file__).parents[2]
    / "src/agent_factory/work_kinds/pull_request/workflow/pr_description.py"
)
spec = importlib.util.spec_from_file_location("pr_description", HELPER)
assert spec is not None and spec.loader is not None
pr = importlib.util.module_from_spec(spec)
spec.loader.exec_module(pr)
HREF = "https://github.com/o/r/blob/branch/proposal.md"


def body(text: str) -> str:
    return "\n".join(["report before", *pr.render(text, HREF), "report after\n"])


def test_token_is_deterministic_and_retries_collisions(monkeypatch: pytest.MonkeyPatch) -> None:
    text = "a proposal"
    assert pr.token(text) == hashlib.sha256(f"0\0{text}".encode()).hexdigest()[:12]
    assert pr.token(text) == pr.token(text)
    # Inject a hash collision: a real preimage would make this test impractical.
    seen: list[bytes] = []

    class Digest:
        def hexdigest(self) -> str:
            return "a" * 64 if len(seen) == 1 else "b" * 64

    def digest(value: bytes) -> Digest:
        seen.append(value)
        return Digest()

    monkeypatch.setattr(pr.hashlib, "sha256", digest)
    assert pr.token("a" * 12) == "b" * 12
    assert seen == [f"{n}\0{'a' * 12}".encode() for n in range(2)]


@pytest.mark.parametrize("level", [1, 2, 3, 4])
def test_render_demotes_only_atx_headings_outside_matching_fences(level: int) -> None:
    text = (
        "Introduction\n\n" + "#" * level + " Why\nunchanged  \n###### Deep\n"
        "```markdown\n## fenced\n~~~\n````\n"
        "~~~\n# also fenced\n~~~~\n"
        "Setext\n======\n\n\n"
    )
    lines = pr.render(text, HREF + "?a=1&b=2")
    assert "#" * max(3, level) + " Why" in lines
    assert "###### Deep" in lines
    assert "## fenced" in lines and "# also fenced" in lines
    assert "unchanged  " in lines
    assert "Setext" in lines and "======" in lines
    found = pr.region(lines)
    assert found is not None
    assert "&amp;" in found[3]
    section = pr.SECTION.format(token=found[2])
    assert lines.count(section) == 2
    assert lines[-2] == "======"


@pytest.mark.parametrize("text", ["Plain text\nwith blanks\n\n", "", "\n\n"])
def test_render_without_headings(text: str) -> None:
    lines = pr.render(text, HREF)
    found = pr.region(lines)
    assert found is not None
    expected = text.rstrip("\n").splitlines()
    assert lines[3:-1] == expected
    assert pr.fit("\n".join(lines)) == "\n".join(lines)


def test_measure_normalizes_line_endings_and_counts_utf8() -> None:
    assert pr.measure("a\nb\n") == pr.measure("a\r\nb\r\n") == 6
    assert pr.measure("a\rb\r") == 6
    assert pr.measure("é\n😀") == 8


def quoted_markers() -> str:
    return "\n".join(
        [
            "<!-- agent-factory:proposal:start proposal.md -->",
            "<!-- agent-factory:proposal-section -->",
            "<!-- agent-factory:proposal:end -->",
            pr.START.format(token="a" * 12, href=HREF),
            pr.SECTION.format(token="a" * 12),
            pr.END.format(token="a" * 12),
        ]
    )


def test_quoted_markers_do_not_end_or_split_region() -> None:
    quotes = quoted_markers()
    text = f"## Why\n{quotes}\n```\n{quotes}\n```\n## Impact\nend"
    rendered = pr.render(text, HREF)
    lines = ["before", *rendered, "after"]
    found = pr.region(lines)
    assert found == (1, len(lines) - 2, pr.token(text), HREF)
    assert pr.token(text) not in text
    assert lines.count(pr.SECTION.format(token=found[2])) == 2
    shortened, removed = pr.masked(lines)
    assert shortened == ["before", rendered[0], "after"]
    assert pr.unmasked(["inserted", *shortened], removed) == ["inserted", *lines]
    original = "\n".join(lines)
    assert pr.fit(original) == original


def test_region_requires_first_start_and_exact_matching_end() -> None:
    lines = pr.render("## Why\ntext", HREF)
    assert pr.region(lines[:-1]) is None
    assert pr.region(["x" + lines[0], *lines[1:]]) is None
    assert pr.region([lines[0], lines[-1] + " "]) is None
    assert pr.region([lines[0], pr.END.format(token="f" * 12), lines[-1]]) == (
        0,
        2,
        pr.token("## Why\ntext"),
        HREF,
    )
    assert pr.region([pr.START.format(token="a" * 12, href=HREF), *lines]) is None
    assert pr.masked(["no region"]) == (["no region"], None)
    assert pr.unmasked(["no region"], None) == ["no region"]


def template(size: int = 80) -> str:
    return "\n".join(
        f"## {name}\n{name}: " + "x" * size
        for name in (
            "Why",
            "What Changes",
            "Capabilities",
            "Technical Approach",
            "Out of Scope",
            "Impact",
        )
    )


@pytest.mark.parametrize(
    "kept",
    [5, 4, 3, 2, 1, 0],
)
def test_fit_drops_whole_sections_in_priority_order(
    monkeypatch: pytest.MonkeyPatch, kept: int
) -> None:
    original = body(template(500))
    lines = original.splitlines()
    found = pr.region(lines)
    assert found is not None
    identifier = found[2]
    order = ["Impact", "Technical Approach", "Capabilities", "Out of Scope", "What Changes", "Why"]
    dropped = order[: 6 - kept]
    proposal_order = [
        "Why",
        "What Changes",
        "Capabilities",
        "Technical Approach",
        "Out of Scope",
        "Impact",
    ]
    expected_notice = (
        pr.OMITTED.format(names=", ".join(n for n in proposal_order if n in dropped), href=HREF)
        if kept
        else pr.ALL_OMITTED.format(href=HREF)
    )
    # Construct the exact expected body to exercise every size boundary.
    marker = pr.SECTION.format(token=identifier)
    boundaries = [i for i in range(found[0] + 1, found[1]) if lines[i] == marker]
    sections = [lines[a:b] for a, b in zip(boundaries, [*boundaries[1:], found[1]], strict=True)]
    expected_region: list[str] = [lines[found[0]], expected_notice]
    for name, section in zip(proposal_order, sections, strict=True):
        if kept:
            expected_region.extend([marker] if name in dropped else section)
    expected_lines: list[str] = [
        "report before",
        *expected_region,
        lines[found[1]],
        "report after\n",
    ]
    expected = "\n".join(expected_lines)
    limit = pr.measure(expected)
    monkeypatch.setattr(pr, "LIMIT", limit)
    result = pr.fit(original)
    assert pr.measure(result) <= limit
    assert expected_notice in result
    assert result.startswith("report before\n") and result.endswith("report after\n")
    for name in proposal_order:
        assert (f"### {name}\n{name}: " + "x" * 500 in result) == (name not in dropped)


def test_fit_extends_notice_in_original_order_across_rounds(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = body(
        "## Out of Scope\n" + "scope" * 40 + "\n## Custom\n" + "x" * 500 + "\n## Why\nwhy"
    )
    monkeypatch.setattr(pr, "LIMIT", pr.measure(original) - 100)
    first = pr.fit(original)
    assert "_Omitted for length: Custom." in first
    monkeypatch.setattr(pr, "LIMIT", pr.measure(first) - 15)
    second = pr.fit(first)
    assert "_Omitted for length: Out of Scope, Custom." in second
    assert second.count("_Omitted for length:") == 1
    assert "### Why\nwhy" in second
    assert pr.fit(second) == second


def test_fit_headingless_leading_section_and_quoted_markers(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    original = body("intro" * 100 + "\n## Why\n" + quoted_markers() + "\n## Impact\n" + "x" * 500)
    monkeypatch.setattr(pr, "LIMIT", 950)
    result = pr.fit(original)
    assert "_Omitted for length: Introduction, Impact." in result
    assert quoted_markers() in result
    assert pr.measure(result) <= 950


def test_fit_oversized_report_and_no_region(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pr, "LIMIT", 20)
    original = "report" * 100 + body("## Why\ntext")
    result = pr.fit(original)
    assert pr.ALL_OMITTED.format(href=HREF) in result
    assert "### Why" not in result
    assert pr.measure(result) > 20
    assert pr.fit(result) == result
    assert pr.fit("report" * 100) == "report" * 100


def test_fit_preserves_crlf_and_unescapes_href(monkeypatch: pytest.MonkeyPatch) -> None:
    href = HREF + "?x=1&y=2"
    original = "\r\n".join(pr.render("## Why\n" + "é" * 100, href))
    monkeypatch.setattr(pr, "LIMIT", pr.measure(original) - 50)
    result = pr.fit(original)
    assert pr.ALL_OMITTED.format(href=href) in result
    assert "\n" not in result.replace("\r\n", "")


def test_fit_cli_preserves_crlf_and_handles_an_oversized_proposal(tmp_path: Path) -> None:
    import subprocess
    import sys

    path = tmp_path / "body.md"
    original = body("## Why\n" + "é" * 40_000).replace("\n", "\r\n")
    path.write_text(original, newline="")
    result = subprocess.run(
        [sys.executable, str(HELPER), "fit", str(path)], capture_output=True, text=True
    )
    assert result.returncode == 0, result.stderr
    with path.open(newline="") as source:
        updated = source.read()
    assert pr.measure(updated) <= pr.LIMIT
    assert pr.ALL_OMITTED.format(href=HREF) in updated
    assert "\n" not in updated.replace("\r\n", "")
    assert updated.startswith("report before\r\n") and updated.endswith("report after\r\n")
