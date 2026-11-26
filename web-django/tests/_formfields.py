"""Reading a rendered admin form back off the page that rendered it.

The point of a frozen primary key is that the field is *absent* from the
generated form, so a hand-built payload proves nothing about it: a POST that
names `id` because the test author typed it would be accepted or refused for
reasons unrelated to what the page offered. The examples in
`tests/test_admin_primary_key_frozen.py` therefore GET the change page, submit
exactly the fields it rendered, and put the key back by hand — which needs the
rendered form read as data.

Stdlib `html.parser` only: invariant (d), pinned by
`tests/test_no_http_client.py`, forbids an HTTP-client import anywhere under
`web-django/`, and a test helper must not add a line to
`web-django/requirements.txt` either.

Three faithfulness decisions, all of them the browser's behaviour rather than
Django's:

  * submit buttons are left out. A browser sends the name/value of the button
    the operator clicked and no other, so the caller adds the one it means
    (`{'_save': '...'}`) instead of this helper guessing which of the admin's
    submit buttons was pressed.
  * an unchecked checkbox or radio contributes nothing, and a `<select>` with
    no `selected` option contributes its first option — which is what a
    browser submits for a list the operator never touched.
  * a `<textarea>`'s value drops one leading newline. HTML inserts one
    directly after the start tag, which is how the admin's own
    `<textarea>\\n{{ value }}` rendering is read back without a blank first
    line.
"""

from html.parser import HTMLParser

# Input types that carry no value a browser would submit from a form the
# operator merely rendered.
_SKIPPED_INPUT_TYPES = {'submit', 'button', 'reset', 'image', 'file'}

# Elements with no end tag: `handle_starttag` fires for them and
# `handle_endtag` never does, so anything counting tag depth must skip them or
# it never comes back down.
_VOID_ELEMENTS = {
    'area', 'base', 'br', 'col', 'embed', 'hr', 'img', 'input', 'link',
    'meta', 'param', 'source', 'track', 'wbr',
}


def _attributes(attrs):
    """A tag's attributes as a dict, with a valueless attribute as ''."""
    return {key: ('' if value is None else value) for key, value in attrs}


class _FormReader(HTMLParser):
    """Collects every form on a page as an ordered name → value mapping.

    Forms are kept separately and selected by `id` afterwards: an admin change
    page carries more than one (the header's POST logout form, for a start),
    and merging them would put a second `csrfmiddlewaretoken` into the payload
    and silently change which form the test is talking about.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.forms = {}
        self._form_id = None
        self._fields = None
        # The name of the <textarea> currently open, if any.
        self._textarea = None
        # The <select> currently open, the option values seen inside it, and
        # the value of its first `selected` option.
        self._select = None
        self._select_options = None
        self._select_chosen = None
        # The <option> currently open.
        self._option_value = None
        self._option_selected = False
        self._option_text = None

    def _record(self, name, value):
        if self._fields is None or not name:
            return
        self._fields[name] = value

    def handle_starttag(self, tag, attrs):
        attributes = _attributes(attrs)
        if tag == 'form':
            self._form_id = attributes.get('id', '')
            self._fields = {}
        elif self._fields is None:
            return
        elif tag == 'input':
            kind = attributes.get('type', 'text').lower()
            if kind in _SKIPPED_INPUT_TYPES:
                return
            if kind in ('checkbox', 'radio') and 'checked' not in attributes:
                return
            self._record(attributes.get('name', ''), attributes.get('value', ''))
        elif tag == 'textarea':
            self._textarea = attributes.get('name', '')
            self._record(self._textarea, '')
        elif tag == 'select':
            self._select = attributes.get('name', '')
            self._select_options = []
            self._select_chosen = None
        elif tag == 'option' and self._select is not None:
            # None, not '': an <option> with no value attribute at all submits
            # its own text, while `value=""` submits the empty string — which
            # is what the admin's "---------" empty choice does.
            self._option_value = attributes.get('value')
            self._option_selected = 'selected' in attributes
            self._option_text = []

    def handle_data(self, data):
        if self._textarea is not None:
            self._fields[self._textarea] = self._fields[self._textarea] + data
        elif self._option_text is not None:
            self._option_text.append(data)

    def handle_endtag(self, tag):
        if tag == 'form':
            if self._fields is not None:
                self.forms[self._form_id] = self._fields
            self._form_id = None
            self._fields = None
        elif self._fields is None:
            return
        elif tag == 'textarea' and self._textarea is not None:
            value = self._fields.get(self._textarea, '')
            for newline in ('\r\n', '\n'):
                if value.startswith(newline):
                    value = value[len(newline):]
                    break
            self._fields[self._textarea] = value
            self._textarea = None
        elif tag == 'option' and self._select is not None:
            value = self._option_value
            if value is None:
                value = ''.join(self._option_text or []).strip()
            self._select_options.append(value)
            if self._option_selected and self._select_chosen is None:
                self._select_chosen = value
            self._option_value = None
            self._option_selected = False
            self._option_text = None
        elif tag == 'select' and self._select is not None:
            chosen = self._select_chosen
            if chosen is None:
                chosen = self._select_options[0] if self._select_options else ''
            self._record(self._select, chosen)
            self._select = None
            self._select_options = None
            self._select_chosen = None


class _ReadonlyReader(HTMLParser):
    """Collects the text of every `<div class="readonly">` on a page."""

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.values = []
        self._depth = 0
        self._text = []

    def handle_starttag(self, tag, attrs):
        if tag in _VOID_ELEMENTS:
            return
        if self._depth:
            self._depth += 1
        elif tag == 'div' and 'readonly' in _attributes(attrs).get('class', '').split():
            self._depth = 1
            self._text = []

    def handle_endtag(self, tag):
        if not self._depth or tag in _VOID_ELEMENTS:
            return
        self._depth -= 1
        if self._depth == 0:
            self.values.append(''.join(self._text).strip())

    def handle_data(self, data):
        if self._depth:
            self._text.append(data)


def form_payload(html, form_id):
    """What a browser would submit for the form with this `id`, unedited.

    `form_id` is the admin's own `<model_name>_form`, named explicitly rather
    than guessed: picking "the biggest form on the page" would quietly change
    which surface an example is about the first time a template grows another.

    Submit buttons are not included — add the one you mean (`_save`).
    """
    reader = _FormReader()
    reader.feed(html)
    if form_id not in reader.forms:
        raise ValueError(
            f'no <form id="{form_id}"> on the page; found {sorted(reader.forms)}'
        )
    return dict(reader.forms[form_id])


def readonly_values(html):
    """The values the page renders as readonly, in document order.

    A readonly field is not an input: `admin/includes/fieldset.html` renders it
    inside `<div class="readonly">`. This is how an example asserts a frozen
    key is still *shown* — "absent from the form" and "gone from the page" are
    different claims, and only the first one is wanted here.
    """
    reader = _ReadonlyReader()
    reader.feed(html)
    return reader.values
