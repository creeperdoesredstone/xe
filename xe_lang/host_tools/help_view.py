"""Searchable offline help tab for Xe, XAssembly, and the desktop IDE."""

from __future__ import annotations

from html import escape

from PyQt6.QtCore import Qt, QUrl
from PyQt6.QtGui import QDesktopServices
from PyQt6.QtWidgets import (
	QHBoxLayout,
	QLabel,
	QLineEdit,
	QListWidget,
	QListWidgetItem,
	QPushButton,
	QSplitter,
	QTextBrowser,
	QVBoxLayout,
	QWidget,
)

from .help_content import HELP_TOPICS, OFFICIAL_DOCS_URL, HelpTopic


def _basic_markdown_to_html(markdown: str) -> str:
	"""Render the small, trusted help subset without another dependency."""

	lines = markdown.strip().splitlines()
	parts: list[str] = []
	paragraph: list[str] = []
	in_code = False
	in_list = False
	in_table = False

	def flush_paragraph() -> None:
		if paragraph:
			parts.append(f"<p>{_inline_markup(' '.join(paragraph))}</p>")
			paragraph.clear()

	def close_list() -> None:
		nonlocal in_list
		if in_list:
			parts.append("</ul>")
			in_list = False

	def close_table() -> None:
		nonlocal in_table
		if in_table:
			parts.append("</table>")
			in_table = False

	for raw in lines:
		line = raw.rstrip()
		if line.startswith("```"):
			flush_paragraph()
			close_list()
			close_table()
			if in_code:
				parts.append("</code></pre>")
			else:
				parts.append("<pre><code>")
			in_code = not in_code
			continue
		if in_code:
			parts.append(escape(line) + "\n")
			continue
		if line.startswith("|-"):
			continue
		if line.startswith("|") and line.endswith("|"):
			flush_paragraph()
			close_list()
			cells = [cell.strip() for cell in line.strip("|").split("|")]
			if not in_table:
				parts.append("<table>")
				in_table = True
			parts.append("<tr>" + "".join(f"<td>{escape(cell)}</td>" for cell in cells) + "</tr>")
			continue
		if line.startswith("- "):
			flush_paragraph()
			close_table()
			if not in_list:
				parts.append("<ul>")
				in_list = True
			parts.append(f"<li>{_inline_markup(line[2:])}</li>")
			continue
		if not line:
			flush_paragraph()
			close_list()
			close_table()
			continue
		if line.startswith("# "):
			flush_paragraph()
			close_list()
			close_table()
			parts.append(f"<h1>{escape(line[2:])}</h1>")
		else:
			close_list()
			close_table()
			paragraph.append(line)
	flush_paragraph()
	close_list()
	close_table()
	if in_code:
		parts.append("</code></pre>")
	return "".join(parts)


def _inline_markup(text: str) -> str:
	result = escape(text)
	result = result.replace(
		f"[{OFFICIAL_DOCS_URL}]({OFFICIAL_DOCS_URL})",
		f'<a href="{OFFICIAL_DOCS_URL}">{OFFICIAL_DOCS_URL}</a>',
	)
	while "**" in result:
		result = result.replace("**", "<strong>", 1)
		if "**" not in result:
			break
		result = result.replace("**", "</strong>", 1)
	segments = result.split("`")
	if len(segments) > 1:
		result = "".join(
			f"<code>{segment}</code>" if index % 2 else segment
			for index, segment in enumerate(segments)
		)
	return result


class HelpPane(QWidget):
	def __init__(self, parent: QWidget | None = None):
		super().__init__(parent)
		self._visible_topics: list[HelpTopic] = []
		self._build_ui()
		self._filter_topics("")

	def _build_ui(self) -> None:
		root = QVBoxLayout(self)
		root.setContentsMargins(18, 16, 18, 18)
		root.setSpacing(12)
		header = QHBoxLayout()
		titles = QVBoxLayout()
		title = QLabel("Xe Help")
		title.setObjectName("ToolTitle")
		subtitle = QLabel("Offline language, XAssembly, and IDE guidance")
		subtitle.setObjectName("MutedText")
		titles.addWidget(title)
		titles.addWidget(subtitle)
		header.addLayout(titles, 1)
		official = QPushButton("Official docs ↗")
		official.setObjectName("SecondaryButton")
		official.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(OFFICIAL_DOCS_URL)))
		header.addWidget(official)
		root.addLayout(header)

		self.search = QLineEdit()
		self.search.setClearButtonEnabled(True)
		self.search.setPlaceholderText("Search Xe, XAssembly, syscalls, or IDE actions…")
		self.search.setAccessibleName("Search help")
		self.search.textChanged.connect(self._filter_topics)
		root.addWidget(self.search)

		splitter = QSplitter(Qt.Orientation.Horizontal)
		self.topic_list = QListWidget()
		self.topic_list.setObjectName("HelpTopics")
		self.topic_list.setMinimumWidth(210)
		self.topic_list.setMaximumWidth(330)
		self.topic_list.currentRowChanged.connect(self._show_topic)
		splitter.addWidget(self.topic_list)

		self.article = QTextBrowser()
		self.article.setObjectName("HelpArticle")
		self.article.setOpenExternalLinks(True)
		self.article.document().setDefaultStyleSheet(
			"h1 { font-size: 22px; margin-bottom: 8px; }"
			"p, li, td { line-height: 1.45; }"
			"pre { padding: 12px; border-radius: 5px; }"
			"code { font-family: 'Cascadia Code', 'Consolas', monospace; }"
			"table { border-collapse: collapse; }"
			"td { padding: 5px 12px 5px 0; }"
		)
		splitter.addWidget(self.article)
		splitter.setStretchFactor(0, 0)
		splitter.setStretchFactor(1, 1)
		root.addWidget(splitter, 1)

	def focus_search(self) -> None:
		self.search.setFocus(Qt.FocusReason.ShortcutFocusReason)
		self.search.selectAll()

	def _filter_topics(self, query: str) -> None:
		current_title = None
		if 0 <= self.topic_list.currentRow() < len(self._visible_topics):
			current_title = self._visible_topics[self.topic_list.currentRow()].title
		self._visible_topics = [topic for topic in HELP_TOPICS if topic.matches(query)]
		self.topic_list.clear()
		selected_row = 0
		last_category = None
		for row, topic in enumerate(self._visible_topics):
			label = topic.title
			if topic.category != last_category:
				label = f"{topic.category}\n{topic.title}"
			last_category = topic.category
			item = QListWidgetItem(label)
			item.setToolTip(topic.title)
			self.topic_list.addItem(item)
			if topic.title == current_title:
				selected_row = row
		if self._visible_topics:
			self.topic_list.setCurrentRow(selected_row)
		else:
			self.article.setHtml(
				"<h1>No matching help</h1><p>Try a broader term such as "
				"<code>graphics</code>, <code>run</code>, or <code>syscall</code>.</p>"
			)

	def _show_topic(self, row: int) -> None:
		if 0 <= row < len(self._visible_topics):
			self.article.setHtml(_basic_markdown_to_html(self._visible_topics[row].markdown))
