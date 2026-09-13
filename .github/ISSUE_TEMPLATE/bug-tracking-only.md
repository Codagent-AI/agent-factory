---
name: Bug (tracking only)
about: File a bug for human tracking without factory admission
title: "Bug: "
labels: ["factory-hold"]
type: Bug
---

<!-- The factory-hold label must already be applied when this issue is created;
     routing does not retroactively rescan issues it has already seen. Removing
     this label after filing does not admit the bug — a human must explicitly
     set Owner=factory and move it to Ready. -->

Describe the bug: what happened, what you expected, and how to reproduce it.

This issue is filed as tracking-only. The factory will not pick it up unless a
human later sets `Owner=factory` and moves it to Ready on the board. To have
the factory attempt a fix immediately instead, file a plain Bug-typed issue
without this template (or without the `factory-hold` label) as a repository
writer.
