"""Literal patch counting and lossless description updates."""

import importlib.util
import sys
from pathlib import Path

import pytest

SCRIPT = (
    Path(__file__).parents[2] / "src/agent_factory/work_kinds/pull_request/workflow/count-loc.py"
)
spec = importlib.util.spec_from_file_location("count_loc", SCRIPT)
assert spec and spec.loader
loc = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = loc
spec.loader.exec_module(loc)


def patch(path: str, hunks: str, old: str | None = None, index: str = "") -> str:
    return f"diff --git a/{path} b/{path}\n{index}--- a/{old or path}\n+++ b/{path}\n{hunks}"


@pytest.mark.parametrize(
    ("hunk", "expected"),
    [
        ("@@ -1,3 +1,5 @@", (2, 3, 0)),
        ("@@ -1,4 +1 @@", (0, 1, 3)),
        ("@@ -0,0 +1,20 @@", (20, 0, 0)),
        ("@@ -1,7 +0,0 @@", (0, 0, 7)),
        ("@@ -2 +2 @@", (0, 1, 0)),
    ],
)
def test_hunk_math(hunk: str, expected: tuple[int, int, int]) -> None:
    row = loc.count(patch("source.py", hunk)).code
    assert (row.files, row.added, row.modified, row.deleted) == (1, *expected)


def test_new_deleted_renamed_and_distinct_files() -> None:
    text = patch("new.py", "@@ -0,0 +1,20 @@\n", old="/dev/null")
    text += "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1,7 +0,0 @@\n"
    text += patch("new.py", "@@ -25 +25 @@\n")
    row = loc.count(text).code
    assert (row.files, row.added, row.modified, row.deleted) == (2, 20, 1, 7)
    # --no-renames represents a rename as separate deletion and addition patches.
    renamed = loc.count(
        "diff --git a/old.py b/old.py\n--- a/old.py\n+++ /dev/null\n@@ -1,7 +0,0 @@\n"
        + patch("new.py", "@@ -0,0 +1,7 @@\n")
    )
    assert (renamed.code.files, renamed.code.added, renamed.code.deleted) == (2, 7, 7)


@pytest.mark.parametrize(
    ("path", "kind"),
    [
        ("openspec/changes/x/proposal.md", "excluded"),
        ("docs/operations.md", "excluded"),
        ("config/codagent.toml", "excluded"),
        ("uv.lock", "excluded"),
        (".github/workflows/ci.yml", "excluded"),
        ("tests/fixtures/data.json", "excluded"),
        ("a/LICENSE-MIT", "excluded"),
        ("testdata/a.py", "excluded"),
        ("runner/step_test.go", "tests"),
        ("tests/unit/test_counter.py", "tests"),
        ("src/pkg/tests/helpers.py", "tests"),
        ("web/app.spec.ts", "tests"),
        ("x/test_util.py", "tests"),
        ("x/util_test.py", "tests"),
        ("web/app.test.ts", "tests"),
        ("src/agent_factory/controller.py", "code"),
        ("scripts/deploy.sh", "code"),
        ("Tests/helper.py", "code"),
    ],
)
def test_classification(path: str, kind: str) -> None:
    assert loc.classify(path) == kind


def test_header_like_content_and_control_characters_do_not_change_path() -> None:
    text = patch(
        "source.py",
        "@@ -1,2 +1,4 @@\n--- fake.md\n+++ fake.json\n"
        "+content\x0b@@ -1,999 +1,999 @@\n+content\x0c--- fake.md\n"
        "\\ No newline at end of file\n@@ -9 +11 @@\n",
    )
    result = loc.count(text)
    assert (result.code.files, result.code.added, result.code.modified) == (1, 2, 3)
    assert result.tests.files == 0


@pytest.mark.parametrize(
    "path",
    [
        '"b/test_hello\\tworld.py"',
        '"b/test_\\303\\251.py"',
        '"b/test_é\\t.py"',
        "b/tests/space name.py\t",
    ],
)
def test_c_quoted_and_space_paths(path: str) -> None:
    text = f"diff --git a/x b/x\n--- a/x\n+++ {path}\n@@ -0,0 +1 @@\n"
    assert loc.count(text).tests.added == 1


def test_binary_and_mode_only_have_no_counted_files() -> None:
    text = patch("binary.py", "@@ -1 +1 @@\n", index="index abcd..def0 100644\n")
    text += "diff --git a/mode.py b/mode.py\nold mode 100644\nnew mode 100755\n"
    text += "diff --git a/image b/image\nBinary files a/image and b/image differ\n"
    assert loc.count(text, {"abcd"}).code.files == 0
    assert loc.count(text).code.modified == 1


@pytest.mark.parametrize(
    ("code", "tests"),
    [((55, 17, 10), (306, 32, 1)), ((19, 4, 1), (130, 0, 0)), ((0, 0, 0), (0, 0, 0))],
)
def test_issue_reference_diff_totals(
    code: tuple[int, int, int], tests: tuple[int, int, int]
) -> None:
    text = ""
    for path, totals in (("source.go", code), ("source_test.go", tests)):
        for hunk, (old, new) in enumerate(((0, totals[0]), (totals[1], totals[1]), (totals[2], 0))):
            if old or new:
                text += patch(path, f"@@ -{hunk},{old} +{hunk},{new} @@\n")
    result = loc.count(text)
    assert (result.code.added, result.code.modified, result.code.deleted) == code
    assert (result.tests.added, result.tests.modified, result.tests.deleted) == tests


def test_render_zero_rows_and_measured_commits() -> None:
    section = loc.render(loc.Counts(merge_base="a" * 40), "main", "b" * 40)
    assert "| code | 0 | 0 | 0 | 0 | +0 −0 |" in section
    assert "| tests | 0 | 0 | 0 | 0 | +0 −0 |" in section
    assert "Measured `aaaaaaaaaaaa..bbbbbbbbbbbb`" in section
    assert section.startswith(loc.START) and section.endswith(loc.END)
    assert "configuration are excluded." in section


def test_unavailable_is_one_escaped_line() -> None:
    section = loc.render(loc.Unavailable("fetch failed\n" + loc.START), "", "")
    assert (
        "Lines-of-code count unavailable: fetch failed &lt;!-- agent-factory:loc --&gt;." in section
    )
    assert "| code |" not in section


@pytest.mark.parametrize(
    ("body", "anchor"),
    [
        ("prefix\n## Change summary\n\nlinks\n\nsuffix", "links\n"),
        ("Refs #1\n<!-- agent-factory:claim:x -->\n\ntext", "<!-- agent-factory:claim:x -->\n"),
        ("plain body", "plain body"),
        ("", ""),
    ],
)
def test_insert_anchors_and_idempotence(body: str, anchor: str) -> None:
    section = loc.render(loc.Counts(merge_base="a"), "main", "b")
    result = loc.upsert(body, section)
    assert result.index(section) >= result.index(anchor) + len(anchor)
    assert loc.upsert(result, section) == result


def test_replace_duplicates_and_preserve_outside_bytes() -> None:
    block = f"{loc.START}\nold\n{loc.END}\n"
    body = "prefix  \n" + block + "middle\n" + block + "suffix"
    section = loc.render(loc.Counts(merge_base="a"), "main", "b")
    assert loc.upsert(body, section) == "prefix  \n" + section + "\nmiddle\nsuffix"


def test_dangling_marker_cleans_known_lines_only() -> None:
    body = (
        f"header\n{loc.START}\n### Lines of code\n\n| old table |\nMeasured old\n"
        "OpenSpec note\n\n## Human text\nkeep\n"
    )
    result = loc.upsert(body, "replacement")
    assert loc.START not in result and "old" not in result
    assert result.startswith("header\n## Human text\nkeep\n")


@pytest.mark.parametrize("fence", ["```", "~~~~"])
@pytest.mark.parametrize("existing", [False, True])
def test_inline_and_fenced_markers_are_literal(fence: str, existing: bool) -> None:
    example = (
        f"Inline `{loc.START}` and `{loc.END}`.\n{fence}markdown\n"
        f"{loc.START}\nexample\n{loc.END}\n{fence}\n"
    )
    old = f"{loc.START}\nstale\n{loc.END}\n" if existing else ""
    section = loc.render(loc.Counts(merge_base="a"), "main", "b")
    updated = loc.upsert(example + old, section)
    assert updated.startswith(example)
    assert sum(text == loc.START for _, _, text in loc.live_lines(updated)) == 1


def test_crlf_preserved_and_marker_whitespace_recognized() -> None:
    body = f"prefix\r\n{loc.START}  \r\nstale\r\n{loc.END}\r\nsuffix\r\n"
    assert loc.upsert(body, "new\nsection") == "prefix\r\nnew\r\nsection\r\nsuffix\r\n"


def test_section_at_eof_preserves_missing_final_newline() -> None:
    body = f"prefix\n{loc.START}\nold\n{loc.END}"
    assert loc.upsert(body, "new") == "prefix\nnew"


def test_insertion_uses_existing_blank_line_without_changing_other_text() -> None:
    body = "## Change summary\n\nlinks\n\nhuman text\n"
    assert loc.upsert(body, "section") == ("## Change summary\n\nlinks\n\nsection\n\nhuman text\n")
    assert loc.upsert("human text\n\n", "section") == "human text\n\nsection\n\n"


def test_quoted_path_syntax_error_renders_unavailable_section(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    import subprocess

    malformed = 'diff --git a/x b/x\n--- a/x\n+++ "b/unterminated\n@@ -0,0 +1 @@\n'

    def command(*args: str, env: dict[str, str] | None = None) -> bytes:
        if "diff" in args:
            return malformed.encode()
        return b"a" * 40

    def exists(*args: object, **kwargs: object) -> subprocess.CompletedProcess[bytes]:
        return subprocess.CompletedProcess(["git", "cat-file", "-e"], 0, b"", b"")

    monkeypatch.setattr(loc, "command", command)
    monkeypatch.setattr(loc.subprocess, "run", exists)
    monkeypatch.setattr(
        sys, "argv", [str(SCRIPT), "section", "--base-ref", "main", "--head", "b" * 40]
    )
    assert loc.main() == 0
    output = capsys.readouterr()
    assert "Lines-of-code count unavailable:" in output.out
    assert "unterminated string literal" in output.out
    assert loc.START in output.out and loc.END in output.out
    assert not output.err
