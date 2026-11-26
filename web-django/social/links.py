"""The one scheme rule for turning a stored value into an `href`.

Every "where" column in `schema.sql` is free-form `text` with no CHECK —
`drafts.target_url`, `people.profile`, `questions.url`, `interactions.post_url`,
`ledger.url` — and nothing on the writer side narrows them: `bin/soc.mjs` puts
`--url` into its `drafts` INSERT with no `need()`, and `bin/import.mjs` copies
whatever the backup carries. So the stored value is not trusted to be a URL at
all, and the project's own backup proves it is often not one: three of its
eleven drafts store a note such as `(ссылку достану при отправке)` in
`target_url`.

Rendered unchecked, such a value becomes a live attribute: a `javascript:`
value turns into a working `href` on the very page whose forms carry the
approval CSRF token, a bare word resolves inside the app and 404s the operator
away from the queue, and a whitespace-only value links to the current page.

The rule itself is older than this module — `social/admin.py`'s `_link`
(slice 10) has applied it to the five admin link columns all along. It lived
*inside* that module, so the queue page, written before it existed, had nothing
to import and kept minting «куда» from the raw column: one rule, two render
sites, applied in one of them. It lives here so a third site cannot drift the
same way.

Deliberately stricter than the Node screen: `web/index.html` escapes
`target_url` but does not check its scheme. Widening what counts as a link is a
one-line change to `LINK_SCHEMES`.
"""

# The two schemes a page will put in an `href`. Everything else — `mailto:`, a
# protocol-relative `//host/x`, a bare `linkedin.com/in/x`, prose — is text.
LINK_SCHEMES = ('http://', 'https://')


def web_href(value):
    """`value` as an `href`, or None when it must not become one.

    None for a missing, blank or whitespace-only value as well as for a
    rejected scheme, so a caller has a single "there is no link here" answer to
    branch on instead of re-testing those shapes itself. What to render
    *instead* is the caller's decision: the admin falls through to its
    truncated preview, the queue card shows the value in full.

    The scheme is matched case-insensitively and the value is returned in its
    ORIGINAL case: `HTTPS://host/x` is a working address, so it is a link, and
    lowering the value would corrupt a path or query that is case-sensitive.
    """
    if value is None:
        return None
    href = str(value).strip()
    if not href:
        return None
    if not href.lower().startswith(LINK_SCHEMES):
        return None
    return href
