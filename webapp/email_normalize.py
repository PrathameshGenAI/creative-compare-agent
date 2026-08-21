"""Normalize marketing-email text before creative validation.

Real proofs of marketing emails carry two kinds of noise that are NOT part of
the creative and must not be flagged as variances:

1. Email-client export chrome. When a Test proof is exported from Outlook it
   gains a header block like::

       Pawar, Girish (CNE)
       From:    USAA <offers@exmac.usaa.com>
       Sent:    Wednesday, January 28, 2026 6:57 AM
       To:      Pawar, Girish (CNE)
       Subject: Girish, discover what's waiting for you at USAA.

   None of that is creative content -- it's the reviewer's mail client.

   This block can appear in two places:
   a) **Before the <html> tag** – handled by ``_extract_creative_content``
      in ``webapp/app.py``.
   b) **Inside the HTML body** – Outlook injects it as a ``<div>`` (or bare
      text) near the top of the ``<body>``. ``strip_outlook_header_div``
      removes it from the raw HTML source before parsing.

2. Personalization. The PTR (master) contains placeholder tokens such as
   ``<%= postProcessing.file.FIRST_NAME_TITLE_CASE_TXT %>``, ``[NAME]`` or
   ``USAA # ending in: <%= ...USAA_LAST4_NR %>``. In the Test proof these are
   rendered with the *reviewer's* real details (e.g. ``Girish`` / ``Pawar`` /
   ``6789``). Those are expected substitutions, so both the tokens and their
   rendered values are masked to a common sentinel and compare as equal.

The goal: a reviewer's name/account (e.g. "Girish Pawar") never shows up as a
text/layout/brand variance.
"""
from __future__ import annotations

import re
from typing import List, Set, Tuple

# Sentinel that both a personalization token and its rendered value collapse to,
# so the two creatives compare equal on personalized fields.
PERSONALIZED = "\u2039PERSONALIZED\u203a"  # ‹PERSONALIZED›

# Standard email header field labels produced by mail clients on export.
_HEADER_LABELS = (
    "from",
    "to",
    "cc",
    "bcc",
    "sent",
    "date",
    "subject",
    "reply-to",
    "importance",
    "attachments",
)
_HEADER_LINE_RE = re.compile(
    r"^\s*(?:%s)\s*:" % "|".join(re.escape(h) for h in _HEADER_LABELS),
    re.IGNORECASE,
)

# A leading "Lastname, Firstname (DEPT)" recipient/sender line, e.g.
# "Pawar, Girish (CNE)".
_NAME_LINE_RE = re.compile(
    r"^\s*[A-Z][A-Za-z.'\u2019-]+,\s+[A-Z][A-Za-z.'\u2019-]+(?:\s*\([^)]*\))?\s*$"
)

# Personalization TOKENS found in the PTR/master.
# Note: the HTML parser inserts spaces around tag-like tokens (e.g.
# ``<%= X %>`` becomes ``< %= X %>``) so we allow optional spaces after
# the opening ``<`` and before the closing ``>``.
_TOKEN_RES = [
    re.compile(r"<\s*%=.*?%\s*>", re.DOTALL),  # <%= postProcessing.file.X %>
    re.compile(r"\$\{[^}]*\}"),                # ${firstName}
    re.compile(r"\{\{[^}]*\}\}"),              # {{ first_name }}
    re.compile(r"%%[^%]+%%"),                  # %%FIRST_NAME%%
    re.compile(r"\[[^\]\n]{1,60}\]"),          # [NAME], [Recipient's name]
]

# "USAA # ending in: 6789" / "...ending in: <%=...%>" -> mask the trailing value.
_ENDING_IN_RE = re.compile(
    r"(ending in:\s*)(?:\d{2,}|<%=.*?%>|[A-Za-z0-9_]+)", re.IGNORECASE | re.DOTALL
)


def _extract_recipient_names(text: str) -> Set[str]:
    """Collect the reviewer/recipient name tokens from email headers.

    Looks at the leading name line and any ``To:``/``From:`` header for a
    ``Lastname, Firstname`` or ``Firstname Lastname`` pattern and returns the
    individual name words so they can be masked wherever they appear in the
    body (greeting, security zone, etc.).
    """
    names: Set[str] = set()
    lines = text.splitlines()
    candidates: List[str] = []
    for line in lines[:15]:  # headers live at the very top
        stripped = line.strip()
        if not stripped:
            continue
        if _NAME_LINE_RE.match(stripped):
            candidates.append(stripped)
        m = re.match(r"^\s*(?:to|from)\s*:\s*(.+)$", stripped, re.IGNORECASE)
        if m:
            candidates.append(m.group(1).strip())

    for cand in candidates:
        # Strip a trailing "(DEPT)" and any email address.
        cand = re.sub(r"\([^)]*\)", "", cand)
        cand = re.sub(r"<[^>]*>", "", cand)
        cand = cand.strip()
        # "Lastname, Firstname" form
        if "," in cand:
            parts = [p.strip() for p in cand.split(",") if p.strip()]
        else:
            parts = cand.split()
        for part in parts:
            word = part.strip(".'\u2019-")
            # Only plausible name words: alphabetic, capitalized, length >= 2,
            # and not a brand/company token.
            if (
                len(word) >= 2
                and word.isalpha()
                and word[0].isupper()
                and word.upper() not in {"USAA", "CNE", "LLC", "INC"}
            ):
                names.add(word)
    return names


def _strip_email_headers(text: str) -> str:
    """Remove a leading email-client header block from the text.

    Scans the leading region of the document and drops standard header lines
    (``From:``/``To:``/``Sent:``/``Subject:`` …) and bare recipient name lines
    (``Lastname, Firstname (DEPT)``). Robust to stray lines that mail/PDF
    exporters inject between headers (e.g. a lone page-number ``1``): a couple
    of short non-header lines don't end header scanning, but the first block of
    genuine body copy does.
    """
    lines = text.splitlines()
    kept: List[str] = []
    n = len(lines)
    # Header block only ever lives near the very top. Cap the scan window so we
    # never touch body copy further down.
    scan_limit = min(n, 20)
    consecutive_content = 0
    header_zone = True
    for i, line in enumerate(lines):
        stripped = line.strip()
        if header_zone and i < scan_limit:
            if stripped == "":
                kept.append(line)
                continue
            if _HEADER_LINE_RE.match(stripped) or _NAME_LINE_RE.match(stripped):
                consecutive_content = 0
                continue  # drop the header / recipient-name line
            # A short, isolated line (e.g. a page number) is exporter noise;
            # skip it without ending the header zone.
            if len(stripped) <= 3 and consecutive_content == 0:
                continue  # drop stray tiny line inside header block
            consecutive_content += 1
            # Two real content lines in a row => header block is over.
            if consecutive_content >= 2:
                header_zone = False
            kept.append(line)
            continue
        header_zone = False
        kept.append(line)
    return "\n".join(kept)


def _mask_personalization(text: str, recipient_names: Set[str]) -> str:
    """Replace personalization tokens and rendered recipient values."""
    # 1) "ending in: <value>" -> "ending in: ‹PERSONALIZED›"
    text = _ENDING_IN_RE.sub(lambda m: m.group(1) + PERSONALIZED, text)
    # 2) Templating tokens -> sentinel
    for rx in _TOKEN_RES:
        text = rx.sub(PERSONALIZED, text)
    # 3) Rendered recipient name words -> sentinel (word-boundary, any case)
    for name in sorted(recipient_names, key=len, reverse=True):
        text = re.sub(
            r"\b%s\b" % re.escape(name), PERSONALIZED, text, flags=re.IGNORECASE
        )
    # Collapse runs of adjacent sentinels ("‹P› ‹P›" -> "‹P›") so a two-word
    # name and a single token don't differ by token count.
    text = re.sub(
        r"(?:%s)(?:[\s,]+(?:%s))+" % (re.escape(PERSONALIZED), re.escape(PERSONALIZED)),
        PERSONALIZED,
        text,
    )
    return text


# ---------------------------------------------------------------------------
# HTML-source stripping (Outlook in-body header div)
# ---------------------------------------------------------------------------

# Outlook and some mail clients inject a header block as an HTML element
# (often a <div> or <table>) near the top of the <body>. The element's
# *visible text* contains the standard header fields joined by whitespace
# because <br> tags replace newlines.  We detect it by looking for a
# contiguous run of header-field keywords in the element's text content.
_OUTLOOK_HDR_KEYWORDS = re.compile(
    r"\b(?:from|to|cc|sent|date|subject|reply-to)\s*:",
    re.IGNORECASE,
)

# We only inspect elements near the top of the body — first ~4 KB of the
# source — so we never accidentally strip real creative content further down.
_BODY_START_RE = re.compile(r"<body\b[^>]*>", re.IGNORECASE)

# Match a single top-level <tag …>…</tag> block (non-greedy, no nesting).
_TOP_ELEMENT_RE = re.compile(
    r"(<(?P<tag>div|table|p|span)\b[^>]*>)(.*?)(</(?P=tag)>)",
    re.IGNORECASE | re.DOTALL,
)


def _visible_text_of_fragment(html_fragment: str) -> str:
    """Rough visible-text extraction from a small HTML fragment."""
    # Strip tags, decode common entities.
    text = re.sub(r"<[^>]+>", " ", html_fragment)
    text = text.replace("&lt;", "<").replace("&gt;", ">").replace("&amp;", "&")
    text = re.sub(r"\s+", " ", text)
    return text.strip()


def strip_outlook_header_div(html: str) -> str:
    """Remove an Outlook-injected email-client header block from raw HTML.

    Scans the first few top-level elements immediately after ``<body>`` and
    drops any element whose visible text contains at least two distinct
    email-header field labels (``From:``, ``To:``, ``Sent:``, ``Subject:`` …).
    This reliably targets the injected header div without touching real content.
    """
    if not html:
        return html

    body_m = _BODY_START_RE.search(html)
    if not body_m:
        return html

    body_start = body_m.end()
    # Only look in the first 4 KB after <body>.
    scan_window = html[body_start: body_start + 4096]

    for m in _TOP_ELEMENT_RE.finditer(scan_window):
        fragment = m.group(0)
        visible = _visible_text_of_fragment(fragment)
        # Require at least 2 distinct header-keyword hits so we don't
        # accidentally drop a <div> that merely contains a word like "From".
        hits = _OUTLOOK_HDR_KEYWORDS.findall(visible)
        if len(hits) >= 2:
            # Remove this element from the source.
            abs_start = body_start + m.start()
            abs_end = body_start + m.end()
            html = html[:abs_start] + html[abs_end:]
            # Only strip the first such block — stop after one removal.
            break

    return html


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------


def normalize_html_source(html: str, names: "Set[str] | None" = None) -> str:
    """Normalize raw HTML source: mask tokens AND rendered recipient names.

    Combines ``normalize_html_tokens`` (token masking) with word-boundary
    replacement of rendered recipient name words (e.g. ``Girish``, ``Pawar``)
    so that both the PTR template and the Test proof produce matching HTML
    element signatures for the layout and brand validators.
    """
    if not html:
        return html
    html = normalize_html_tokens(html)
    if names:
        for name in sorted(names, key=len, reverse=True):
            html = re.sub(
                r"\b%s\b" % re.escape(name), PERSONALIZED, html, flags=re.IGNORECASE
            )
    return html


def normalize_email_text(text: str, extra_names: "Set[str] | None" = None) -> str:
    """Full normalization pipeline applied to extracted text before validation.

    ``extra_names`` allows the caller to supply additional recipient name tokens
    (e.g. extracted from the raw HTML *before* the Outlook header div was
    stripped) so they are still masked even after the header block is gone.
    """
    if not text:
        return text
    recipient_names = _extract_recipient_names(text)
    if extra_names:
        recipient_names |= extra_names
    text = _strip_email_headers(text)
    text = _mask_personalization(text, recipient_names)
    return text


def normalize_html_tokens(html: str) -> str:
    """Replace personalization tokens in raw HTML source with the sentinel.

    The layout and brand validators work on ``creative.raw`` (the HTML source).
    If the PTR has ``<%= X %>`` and the Test has the rendered value, the two
    ``<p>`` elements get different layout-validator signatures and surface as
    spurious missing/extra variances. Replacing tokens in the source before
    parsing eliminates those false-positive layout diffs.
    """
    if not html:
        return html
    for rx in _TOKEN_RES:
        html = rx.sub(PERSONALIZED, html)
    # Also mask "ending in: <digits>" in the raw source.
    html = _ENDING_IN_RE.sub(lambda m: m.group(1) + PERSONALIZED, html)
    return html


def extract_names_from_raw_html(html: str) -> "Set[str]":
    """Extract recipient name tokens visible in a raw HTML string.

    Parses the HTML visible text (including any Outlook header div still
    present), converts the flat text to lines on header-field boundaries,
    then delegates to ``_extract_recipient_names``.  Call this *before*
    ``strip_outlook_header_div`` so the names survive after the div is removed.
    """
    if not html:
        return set()
    try:
        import sys, os
        project_root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        if project_root not in sys.path:
            sys.path.insert(0, project_root)
        from creative_compare_agent.validators.htmldoc import parse_html
        visible = parse_html(html).visible_text()
        # The Outlook header renders as one flat string; re-split on labels.
        lined = re.sub(
            r"\b(From|To|Cc|Sent|Date|Subject|Reply-To)\s*:",
            r"\n\1:",
            visible,
            flags=re.IGNORECASE,
        )
        return _extract_recipient_names(lined)
    except Exception:
        return set()
