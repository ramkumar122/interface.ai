"""Shared HTML assertions for CoreDesk screen tests.

Uses only the stdlib html.parser (no BeautifulSoup) so there is no new
dependency. These are reused by every screen spec, and the AX-contract helpers
(caption / column-header / iframe-title) are what actually protect the
multi-tenant locator claims: they fail if a template edit drops a caption or
"tidies" one tenant's column order to match the other.
"""

from html.parser import HTMLParser


class _Collector(HTMLParser):
    def __init__(self):
        super().__init__()
        self.inputs = []          # list of attr dicts
        self.label_fors = []      # list of <label for> values
        self.iframes = {}         # id -> title (or None)
        self.tables = {}          # id -> {"caption": str|None, "headers": [str]}
        self._table_stack = []    # table ids (or None) in nesting order
        self._cap_buf = None
        self._th_buf = None

    def _current_table_id(self):
        for tid in reversed(self._table_stack):
            if tid:
                return tid
        return None

    def handle_starttag(self, tag, attrs):
        a = dict(attrs)
        if tag == "table":
            self._table_stack.append(a.get("id"))
            if a.get("id") and a["id"] not in self.tables:
                self.tables[a["id"]] = {"caption": None, "headers": []}
        elif tag == "input":
            self.inputs.append(a)
        elif tag == "label" and a.get("for"):
            self.label_fors.append(a["for"])
        elif tag == "iframe":
            self.iframes[a.get("id")] = a.get("title")
        elif tag == "caption" and self._current_table_id():
            self._cap_buf = []
        elif tag == "th" and self._current_table_id():
            self._th_buf = []

    def handle_endtag(self, tag):
        if tag == "table" and self._table_stack:
            self._table_stack.pop()
        elif tag == "caption" and self._cap_buf is not None:
            tid = self._current_table_id()
            if tid is not None:
                self.tables[tid]["caption"] = "".join(self._cap_buf).strip()
            self._cap_buf = None
        elif tag == "th" and self._th_buf is not None:
            tid = self._current_table_id()
            if tid is not None:
                self.tables[tid]["headers"].append("".join(self._th_buf).strip())
            self._th_buf = None

    def handle_data(self, data):
        if self._cap_buf is not None:
            self._cap_buf.append(data)
        if self._th_buf is not None:
            self._th_buf.append(data)


def _collect(html):
    c = _Collector()
    c.feed(html)
    return c


def assert_no_data_testid(html):
    """Fail if a data-testid attribute appears anywhere in the HTML."""
    assert "data-testid" not in html, "data-testid found in rendered HTML"


def assert_visible_inputs_have_labels(html):
    """Every visible <input> (not type=hidden) must have a matching <label for>."""
    c = _collect(html)
    fors = set(c.label_fors)
    for inp in c.inputs:
        if inp.get("type", "text").lower() == "hidden":
            continue
        input_id = inp.get("id")
        assert input_id, "visible <input> without an id: %r" % inp
        assert input_id in fors, "no <label for> matching input id %r" % input_id


def assert_table_has_caption(html, table_id, expected_caption):
    """The table with the given id has a <caption> equal to expected_caption."""
    c = _collect(html)
    assert table_id in c.tables, "table id %r not found" % table_id
    actual = c.tables[table_id]["caption"]
    assert actual == expected_caption, (
        "table %r caption %r != %r" % (table_id, actual, expected_caption)
    )


def assert_column_headers(html, table_id, expected_headers):
    """The non-empty <th> text under the table, in order, equals expected_headers."""
    c = _collect(html)
    assert table_id in c.tables, "table id %r not found" % table_id
    headers = [h for h in c.tables[table_id]["headers"] if h]
    assert headers == list(expected_headers), (
        "table %r headers %r != %r" % (table_id, headers, list(expected_headers))
    )


def assert_iframe_has_title(html, iframe_id):
    """The iframe with the given id carries a non-empty title attribute."""
    c = _collect(html)
    assert iframe_id in c.iframes, "iframe id %r not found" % iframe_id
    title = c.iframes[iframe_id]
    assert title, "iframe %r has an empty title" % iframe_id
