"""Lightweight HTML parsing built on the Python standard library.

We deliberately avoid a hard dependency on BeautifulSoup. The stdlib
``html.parser.HTMLParser`` is always available, so parsing degrades
gracefully everywhere. If ``bs4`` happens to be installed it is *not*
required — this module is fully self-contained.

The parser produces a simple tree of :class:`Node` objects that the
layout and brand validators walk. Inline ``style`` attributes are
parsed into dicts, and ``<style>`` blocks are collected as raw CSS.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser
from typing import Dict, List, Optional


# Tags that do not have closing counterparts.
VOID_TAGS = {
    "area", "base", "br", "col", "embed", "hr", "img", "input",
    "link", "meta", "param", "source", "track", "wbr",
}

# Tags whose text content we ignore when extracting visible text.
NON_VISIBLE_TAGS = {"script", "style", "head", "title", "meta", "link"}


@dataclass
class Node:
    """A single element node in the parsed HTML tree."""

    tag: str
    attrs: Dict[str, str] = field(default_factory=dict)
    children: List["Node"] = field(default_factory=list)
    text: str = ""  # direct text belonging to this node (concatenated)
    parent: Optional["Node"] = None

    @property
    def style(self) -> Dict[str, str]:
        """Inline style attribute parsed into a lowercase-keyed dict."""
        return parse_style(self.attrs.get("style", ""))

    def get(self, name: str, default: str = "") -> str:
        return self.attrs.get(name, default)

    def iter_descendants(self):
        """Depth-first iteration over all descendant nodes."""
        for child in self.children:
            yield child
            yield from child.iter_descendants()

    def visible_text(self) -> str:
        """Concatenated visible text of this node and descendants."""
        parts: List[str] = []
        self._collect_text(parts)
        joined = " ".join(p for p in parts if p)
        return re.sub(r"\s+", " ", joined).strip()

    def _collect_text(self, parts: List[str]) -> None:
        if self.tag in NON_VISIBLE_TAGS:
            return
        if self.text:
            parts.append(self.text)
        for child in self.children:
            child._collect_text(parts)


class _TreeBuilder(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.root = Node(tag="#document")
        self._stack: List[Node] = [self.root]
        self.style_blocks: List[str] = []
        self._in_style = False

    # -- element handling ---------------------------------------------------

    def handle_starttag(self, tag, attrs):
        tag = tag.lower()
        node = Node(tag=tag, attrs={k.lower(): (v or "") for k, v in attrs})
        node.parent = self._stack[-1]
        self._stack[-1].children.append(node)
        if tag == "style":
            self._in_style = True
        if tag not in VOID_TAGS:
            self._stack.append(node)

    def handle_startendtag(self, tag, attrs):
        # Self-closing tag like <img ... />
        tag = tag.lower()
        node = Node(tag=tag, attrs={k.lower(): (v or "") for k, v in attrs})
        node.parent = self._stack[-1]
        self._stack[-1].children.append(node)

    def handle_endtag(self, tag):
        tag = tag.lower()
        if tag == "style":
            self._in_style = False
        # Pop back to the matching open tag if present.
        for i in range(len(self._stack) - 1, 0, -1):
            if self._stack[i].tag == tag:
                del self._stack[i:]
                break

    def handle_data(self, data):
        if self._in_style:
            self.style_blocks.append(data)
            return
        stripped = data.strip()
        if not stripped:
            return
        current = self._stack[-1]
        if current.text:
            current.text = current.text + " " + stripped
        else:
            current.text = stripped


@dataclass
class ParsedHTML:
    """Result of parsing an HTML string."""

    root: Node
    css_blocks: List[str]

    def iter_nodes(self):
        yield from self.root.iter_descendants()

    def visible_text(self) -> str:
        return self.root.visible_text()

    def find_all(self, *tags: str) -> List[Node]:
        wanted = {t.lower() for t in tags}
        return [n for n in self.iter_nodes() if n.tag in wanted]

    @property
    def all_css(self) -> str:
        return "\n".join(self.css_blocks)


def parse_html(html: str) -> ParsedHTML:
    """Parse an HTML string into a :class:`ParsedHTML` tree."""
    builder = _TreeBuilder()
    builder.feed(html or "")
    builder.close()
    return ParsedHTML(root=builder.root, css_blocks=builder.style_blocks)


def parse_style(style: str) -> Dict[str, str]:
    """Parse an inline ``style`` attribute into a dict.

    Keys are lowercased and stripped; values keep their original case
    (important for text values) but are stripped of surrounding space.
    """
    result: Dict[str, str] = {}
    if not style:
        return result
    for declaration in style.split(";"):
        if ":" not in declaration:
            continue
        prop, _, value = declaration.partition(":")
        prop = prop.strip().lower()
        value = value.strip()
        if prop:
            result[prop] = value
    return result
