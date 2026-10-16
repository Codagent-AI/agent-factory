"""Directory bundles preserve the guest destination's metadata."""

import tarfile
from pathlib import Path
from unittest.mock import patch

from agent_factory.fly.transport import FlyTransport


def test_put_directory_omits_root_metadata_and_excluded_top_level_names(tmp_path: Path) -> None:
    local = tmp_path / "source"
    (local / "nested" / ".factory").mkdir(parents=True)
    (local / "nested" / "file.txt").write_bytes(b"checkpoint\x00bytes")
    (local / "nested" / ".factory" / "keep.txt").write_text("nested entry")
    (local / ".factory").mkdir()
    (local / ".factory" / "machine.json").write_text("host-managed")
    (local / "excluded.txt").write_text("excluded file")
    bundle = tmp_path / "captured.tar"

    def capture(local: Path, remote: str) -> None:
        bundle.write_bytes(local.read_bytes())

    transport = FlyTransport("app", "machine")
    with (
        patch.object(transport, "put_file", side_effect=capture),
        patch.object(transport, "command"),
    ):
        transport.put_directory(local, "/artifacts", exclude=(".factory", "excluded.txt"))

    with tarfile.open(bundle) as archive:
        assert set(archive.getnames()) == {
            "./nested",
            "./nested/.factory",
            "./nested/.factory/keep.txt",
            "./nested/file.txt",
        }
        contents = archive.extractfile("./nested/file.txt")
        assert contents is not None
        assert contents.read() == b"checkpoint\x00bytes"
