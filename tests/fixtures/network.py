"""Keep test subprocesses off github.com."""

from __future__ import annotations

import socketserver
from http.server import ThreadingHTTPServer

# Git never reaches github.com from a test: any https://github.com/ remote resolves to a
# missing local path and fails at once, as an unreadable private repository would. A test
# that maps a GitHub URL to a local repository adds a longer insteadOf entry (index 1 or
# later), which wins, or sets its own GIT_CONFIG_* variables.
NO_GITHUB = {
    "GIT_CONFIG_COUNT": "1",
    "GIT_CONFIG_KEY_0": "url.file:///nonexistent-github/.insteadOf",
    "GIT_CONFIG_VALUE_0": "https://github.com/",
}


class LoopbackHTTPServer(ThreadingHTTPServer):
    """A loopback HTTP server that starts at once.

    ``HTTPServer.server_bind`` names the server with ``socket.getfqdn``, a reverse DNS
    lookup of the bound address. On GitHub's macOS runners the first lookup in each
    process stalled for about 35 seconds. Test servers only need the address.
    """

    def server_bind(self) -> None:
        socketserver.TCPServer.server_bind(self)
        host, port = self.server_address[:2]
        self.server_name = str(host)
        self.server_port = int(port)
