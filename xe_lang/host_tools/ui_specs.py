"""Declarative metadata shared by the host workbench surfaces.

Keeping labels, stable keys, shortcuts, and aliases here lets new tools and assets be
added without changing the window-composition code.  The records deliberately carry
no QWidget instances or callbacks, so importing them remains side-effect free.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class WorkbenchTabSpec:
	key: str
	label: str
	shortcut: str
	aliases: tuple[str, ...]


@dataclass(frozen=True, slots=True)
class ToolbarActionSpec:
	key: str
	label: str


WORKBENCH_TABS: tuple[WorkbenchTabSpec, ...] = (
	WorkbenchTabSpec("code", "Code", "Alt+1", ("editor",)),
	WorkbenchTabSpec("converter", "Xe → SB3", "Alt+2", ("xe-to-sb3", "sb3")),
	WorkbenchTabSpec("image-studio", "Image Studio", "Alt+3", ("image", "image-editor")),
	WorkbenchTabSpec("help", "Help", "Alt+4", ("docs",)),
)


CODE_TOOLBAR_ACTIONS: tuple[ToolbarActionSpec, ...] = (
	ToolbarActionSpec("new", "New"),
	ToolbarActionSpec("open", "Open"),
	ToolbarActionSpec("save", "Save"),
	ToolbarActionSpec("save-as", "Save As"),
)


def workbench_tab_index(name: str) -> int | None:
	normalized = name.strip().casefold().replace("_", "-").replace(" ", "-")
	for index, spec in enumerate(WORKBENCH_TABS):
		if normalized == spec.key or normalized in spec.aliases:
			return index
	return None
