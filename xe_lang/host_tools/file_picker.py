"""Read-only Xe source picker rooted in the VM's private virtual drive."""

from __future__ import annotations

from pathlib import Path

from PyQt6.QtCore import Qt
from PyQt6.QtWidgets import (
	QDialog,
	QDialogButtonBox,
	QFileDialog,
	QHBoxLayout,
	QLabel,
	QListWidget,
	QListWidgetItem,
	QPushButton,
	QVBoxLayout,
	QWidget,
)

from xe_lang.devices.filesystem import FileSystemDevice, default_virtual_drive_root


ENTRY_PATH_ROLE = int(Qt.ItemDataRole.UserRole)
ENTRY_DIRECTORY_ROLE = ENTRY_PATH_ROLE + 1


class XeSourcePicker(QDialog):
	"""Pick a `.xe` file without exposing VM file operations to the host OS."""

	def __init__(self, parent: QWidget | None = None, *, root: str | Path | None = None):
		super().__init__(parent)
		self.setWindowTitle("Open Xe source")
		self.resize(620, 430)
		self.files = FileSystemDevice(root or default_virtual_drive_root())
		self.current_directory = "."
		self.selected_path: Path | None = None
		self._build_ui()
		self._refresh_entries()

	def _build_ui(self) -> None:
		layout = QVBoxLayout(self)
		layout.setContentsMargins(14, 14, 14, 14)
		layout.setSpacing(10)

		header = QHBoxLayout()
		self.back_button = QPushButton("Back")
		self.back_button.setAccessibleName("Go to parent folder")
		self.back_button.clicked.connect(self._go_back)
		header.addWidget(self.back_button)
		self.path_label = QLabel("Virtual Drive / ")
		self.path_label.setObjectName("PickerPath")
		self.path_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
		header.addWidget(self.path_label, 1)
		self.computer_button = QPushButton("Browse computer…")
		self.computer_button.setToolTip("Choose a Xe file outside the private Xenon virtual drive")
		self.computer_button.clicked.connect(self._browse_computer)
		header.addWidget(self.computer_button)
		layout.addLayout(header)

		help_text = QLabel("Double-click a folder to open it, or a .xe file to select it.")
		help_text.setObjectName("MutedText")
		layout.addWidget(help_text)
		self.entries = QListWidget()
		self.entries.setObjectName("SourcePickerEntries")
		self.entries.setAlternatingRowColors(False)
		self.entries.itemSelectionChanged.connect(self._selection_changed)
		self.entries.itemDoubleClicked.connect(self._activate_item)
		layout.addWidget(self.entries, 1)

		self.selection_label = QLabel("No Xe file selected")
		self.selection_label.setObjectName("MutedText")
		layout.addWidget(self.selection_label)
		self.buttons = QDialogButtonBox(
			QDialogButtonBox.StandardButton.Open | QDialogButtonBox.StandardButton.Cancel
		)
		self.open_button = self.buttons.button(QDialogButtonBox.StandardButton.Open)
		self.open_button.setEnabled(False)
		self.buttons.accepted.connect(self._accept_selection)
		self.buttons.rejected.connect(self.reject)
		layout.addWidget(self.buttons)

	def _refresh_entries(self) -> None:
		self.entries.clear()
		for entry in self.files.entries(self.current_directory):
			if not entry.is_directory and Path(entry.name).suffix.casefold() != ".xe":
				continue
			relative = entry.name if self.current_directory == "." else f"{self.current_directory}/{entry.name}"
			item = QListWidgetItem(("Folder  " if entry.is_directory else "Xe file  ") + entry.name)
			item.setData(ENTRY_PATH_ROLE, relative)
			item.setData(ENTRY_DIRECTORY_ROLE, entry.is_directory)
			item.setToolTip(relative)
			self.entries.addItem(item)
		self.path_label.setText(
			"Virtual Drive / " + ("" if self.current_directory == "." else self.current_directory)
		)
		self.back_button.setEnabled(self.current_directory != ".")
		self.selected_path = None
		self.open_button.setEnabled(False)
		self.selection_label.setText("No Xe file selected")

	def _selection_changed(self) -> None:
		items = self.entries.selectedItems()
		is_file = bool(items and not items[0].data(ENTRY_DIRECTORY_ROLE))
		self.open_button.setEnabled(is_file)
		self.selection_label.setText(items[0].data(ENTRY_PATH_ROLE) if is_file else "No Xe file selected")

	def _activate_item(self, item: QListWidgetItem) -> None:
		relative = str(item.data(ENTRY_PATH_ROLE))
		if item.data(ENTRY_DIRECTORY_ROLE):
			self.current_directory = self.files.normalize(relative) or "."
			self._refresh_entries()
			return
		self.entries.setCurrentItem(item)
		self._accept_selection()

	def _go_back(self) -> None:
		if self.current_directory == ".":
			return
		parent = Path(self.current_directory).parent.as_posix()
		self.current_directory = "." if parent in {"", "."} else parent
		self._refresh_entries()

	def _accept_selection(self) -> None:
		items = self.entries.selectedItems()
		if not items or items[0].data(ENTRY_DIRECTORY_ROLE):
			return
		candidate = (self.files.root / str(items[0].data(ENTRY_PATH_ROLE))).resolve()
		if candidate.is_file() and candidate.suffix.casefold() == ".xe":
			self.selected_path = candidate
			self.accept()

	def _browse_computer(self) -> None:
		path, _ = QFileDialog.getOpenFileName(
			self,
			"Open Xe source",
			str(self.files.root),
			"Xe source (*.xe);;All files (*)",
		)
		candidate = Path(path).resolve() if path else None
		if candidate is not None and candidate.is_file() and candidate.suffix.casefold() == ".xe":
			self.selected_path = candidate
			self.accept()


def select_xe_source(parent: QWidget | None = None, *, root: str | Path | None = None) -> Path | None:
	dialog = XeSourcePicker(parent, root=root)
	return dialog.selected_path if dialog.exec() == QDialog.DialogCode.Accepted else None
