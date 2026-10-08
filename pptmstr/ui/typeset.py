"""
The one place pptmstr meets ``advtext``: fonts, theme, palette, held views, approval diffs.

Every pane that wants typeset text calls into this module and no other module in
``pptmstr`` imports ``advtext`` (a test reads the imports). The library is a text
*engine* -- boxes, glue, a line breaker, hit testing -- and what it needs from a host
is small but easy to get five different ways in five panes: which font file plays
which role, what size, which ink on which ground, who keeps a view alive between
frames, what a body of unknown shape is read as, and how long a frame may spend.
Those answers live here once.

## The surface

Everything that draws does so in the current ImGui window at the cursor, returns the
height it claimed in pixels, and **raises outside a frame** (``_frame``).

| call | what it is for |
|---|---|
| ``enabled(pane)`` / ``pane_on(pane)`` | whether a pane should use this module at all |
| ``markdown(cache, key, source, ...)`` | a settled markdown document: selectable, culled |
| ``plain(cache, key, text, ...)`` | text that is not markdown: tool output, a command, a path |
| ``document(cache, key, parts, ...)`` | ``Part``s of every ``Kind`` as one view, one selection |
| ``prose_parts(text)`` | the parts of a body somebody wrote: **the** markdown-or-not rule |
| ``diff(cache, key, source, ...)`` | a diff of a parked call, of its texts, or of a ``Diff`` |
| ``stream(cache, key)`` -> ``Stream`` | markdown that is still arriving |
| ``diff_model(texts)`` / ``approval_diff(pending)`` | the diff model, no window, no file read |
| ``font(weight, size)`` / ``label(text, ...)`` | the same faces for a native ImGui line |
| ``line_height()`` / ``diff_row_height()`` | a role's line pitch, a diff row's: to size a cap |
| ``multiline_kind(role)`` | the ``Kind`` a role's text of several lines is set as |
| ``ViewCache`` | what holds the views; one per pane, pruned by live key set |
| ``set_budget(ms)`` / ``pending()`` | what a frame may spend building, and whether more is owed |
| ``palette(ink)`` / ``build_palette(p)`` | the library's colours for the live theme |
| ``available()`` / ``obtainable()`` / ``unavailable_reason()`` | the menu's questions |
| ``configure`` / ``wanted`` / ``pre_frame`` / ``reset`` | the app's side, each frame |
| ``load_fonts`` / ``load_fonts_later`` / ``hello_loader`` | the app's side, at start-up |
| ``Role`` / ``Slot`` / ``RoleStyle`` / ``Pane`` | what is configured, and by what |
| ``DEFAULTS`` / ``SLOTS`` / ``STEPS`` / ``PROPORTIONALS`` / ``SCALES`` | what may be chosen |
| ``chosen(role)`` / ``style(role)`` / ``px(role)`` / ``pitch(role)`` | a role as set, as in force |
| ``proportional()`` / ``proportionals()`` / ``scale()`` / ``mono()`` | the rest in force |
| ``roles_from`` / ``checked`` / ``panes_from`` / ``legacy`` | settings' strings into the above |
| ``LEADING`` / ``METRICS`` | line spacing and whose advances: tables in code, not settings |

A ``ViewCache`` answers, per key: ``view`` (the library's own object), ``height``,
``drawn`` and ``replaced`` (see "Two counts"), ``unbuilt`` (source lines still to be
typeset; 1 for a diff whose model is still being made), ``unruled`` (tables left
without row rules: 0 unless the seam misread a layout), ``layout_ms``,
``part_at(key, block)`` and ``selected_text``.

Arguments a pane may leave out, each for one situation: ``diff(warning=False)`` for
a pane that words the gate's prediction itself, ``diff(background=True)`` to make a
large model on a thread, ``document`` / ``markdown(build_hidden=True,
free_allowance=False)`` and ``stream(every_ms=, free_allowance=)`` for a pane that
swaps one view for another and watches ``unbuilt``, ``document(notice_above=True)``
for a pane whose reader decides on the text and must see at once that it is not
all there, and ``Part(tight=True)`` for a part that names the one under it.

``enabled(pane)`` false means that pane uses the renderer it had before, untouched.
That is the experiment's instrument (the operator flips ``Settings.typeset``, or one
pane's switch, and compares) and it is also the degraded mode: it is false when the
library did not import, when the faces did not load, and when the faces that are
loaded belong to another ImGui context than the current one.

## Nothing written is hidden

One rule per way a renderer shows less than its source.

**The markdown reader does not consume text** (``_reader``). CommonMark reads raw
HTML and shows nothing for it, and an image with its alt text: ``<div>x</div>``
vanishes, ``List<String>`` loses ``<String>``, ``ask <lead> for the rest`` loses a word,
and an unclosed ``<!--`` swallows the rest of the document -- a concern body reading
``Looks fine.`` and then a comment opener would show its approver two words. The
rules that do this are switched off *on the library's parser*, so HTML is text and
``![alt](url)`` is an exclamation mark and a link. On the parser and not on the
text: escaping the source before it is parsed is a second markdown reader that has
to agree with the real one about every backslash, code span, fence and container,
and hides text wherever it does not. Nothing is held back from a stream and an
autolink stays an autolink. **The reader is then asked to read a probe**, through
the calls a view makes, and the faces do not load unless every word of it comes
back (``_reader_hides``).

**The stars of a glob are stars** (``_written_stars``, a rule on the same parser).
CommonMark pairs the stars of ``delete **/*.py and tests/**/*.md`` into bold and
draws neither pair. A run of ``*`` beside a ``/``, before ``.ext``, or with a letter
or digit on both sides is text; ``**bold**`` and ``*emphasis*`` are still those.
What still loses its stars is what is written exactly as emphasis is (``rm *tmp
and old*``). ``_`` is not emphasis to the library's reader at all.

**A link's destination is on screen** (``_show_destinations``): ``text (https://...)``,
with its title if it has one, in every markdown view, settled and streamed. An
approver releasing a concern body is releasing its links. Left out only where the
text already is the destination, character for character but for a scheme and a
closing ``/`` (``_names_itself``): a text that only normalises to its destination --
a Unicode lookalike of a punycode host -- is set with where it goes. There is no
switch for it.

**Nothing runs off the edge of a window that cannot scroll to it** (``_fit``). The
library never breaks an inline code span, at a space or anywhere, nor a word. A
paragraph holding either wider than the measure is given places to break: a span
at its spaces, then anything still too wide at the measure. A fenced block and
``plain()`` text are different -- their line ends are content -- and are reported to
the window as content width instead; ``Kind.WRAPPED_EXACT`` is the form that breaks
them at any column and keeps every space. A **stream** is the exception to the
first half: its blocks are the library's to lay out, so an unbroken run in one is
reported as width and not broken, until the same text is drawn as a part.

**A fence's info string is set**, all of it, as the first line of its block
(``_captioned``): the library keeps it on the block and draws none of it, and
``python IMPORTANT also delete tests`` is text the recipient gets. The bare ``diff``
or ``patch`` that is obeyed is the one exception, and a fence that says more than
that is set as code under everything it says.

**A fenced diff is drawn as a diff only when the diff view draws every line of
it** (``_accounted``). The library's reader keeps every line and its view does not
draw them all: a sentence above ``--- a/x``, a mail's message, whatever is over a
bare ``---``, a line under a hunk that has had its count, are on the model and
nowhere on screen. Each line of the fence must be a row, a hunk's ``@@`` line, a
``---``/``+++`` line naming the file the view labels, or a line of a text shown as
written; a fence with any other line in it is set as the code it is, under the
word ``diff``. That includes a diff as ``git diff`` prints it (``index`` lines).
**In a stream** the question is asked again as each line arrives: a fence is a
diff while every line so far is drawn, and from the first line that would not be
it is the whole fence as code -- and a diff again if a later line accounts for
that one. Every line that has arrived is on screen in both.

**A diff's file header is never the only place its warning is** (``_headers_above``).
The view's header is one line, cut at the view's edge and outside what it scrolls
to; whatever of it does not fit is said above the view on lines that wrap, the
warning first.

What is still hidden, and is the library's: a run of spaces inside an inline code
span is one space. It is not repaired here.

## One rule for whether a body is markdown

``prose_parts(text)`` is that rule and every pane showing a concern, a task detail
or a brief entry calls it, so one body reads the same in two panes. Its docstring
has the tests; the shape is: a paragraph at a time, markdown left untouched,
indented lines no list accounts for set literally, lines their writer ended on
purpose given hard breaks.

## What a frame may spend

**No call here takes an unannounced stall.** Parsing and first layout cost about
0.4 ms a source line (measured; "What it costs"), so a long text, or many views
appearing at once, is not built in the frame that first asks for it:

- A text is built in **pieces** of about forty lines of markdown or sixty of
  literal text, cut where cutting cannot change how it reads (``_markdown_pieces``).
  A view shows the pieces it has and, under them, ``typesetting: N more lines``.
- A frame builds its free allowance -- ``_FREE_LINES`` source lines, while under
  ``_FREE_MS`` -- so that a text of ordinary length is whole on its first frame and
  is never seen half-built, and after that only while it has **budget** left
  (``set_budget``, 6 ms). What a piece's layout will cost is learned from the last.
- **A view never yet whole is built wherever it is**; until its turn comes it claims
  the room its line count suggests. **A view that was whole and is out of sight is
  not rebuilt** -- not when its text changes, not when the faces do -- until it is
  scrolled to, unless the pane says ``build_hidden``. It keeps the height it had.
- **A change of width is built beside the view** when it is more than a frame's
  worth (``_measure_for``): the text stays on screen whole, at the width it had,
  until the same text at the new width is whole, a few pieces a frame.
- **A stream handed a long text at once reads it a piece a frame** (``Stream``).
- What cannot be cut -- ``difflib`` over a large changed region, one wrapped block
  of hundreds of lines -- is **announced**: a line saying what is coming, one frame
  before the stall (``_DIFF_ANNOUNCED_LINES``, ``_ANNOUNCED_LINES``). The diff can be
  taken off the frame altogether: ``diff(background=True)``. A model its thread
  could not make is said in the diff's place and not asked for again.
- **A text is not all there until ``unbuilt`` is zero**, and under a text longer
  than its window the line saying so is out of sight. A pane whose reader acts
  on the text asks for the line above it (``notice_above``) and holds the action:
  measured, a 20,000-character body is short of its text for 5 to 47 frames by
  how its lines fall, and one of 100,000 characters for 27, where building each
  at a stroke is one frame of 190 to 360 ms and of 1.07 s.
- ``pending()`` is true while a view on screen is short of its text or waiting on
  a width, and the application's loop does not idle while it is.

The free allowance is the one frame of 30 to 45 ms a text of ordinary length costs.
A pane that would rather have several short frames and can wait for the text --
it watches ``unbuilt`` -- passes ``free_allowance=False``.

The pieces are also what makes a change cost the change: text that grows at its end
is parsed again from its last piece, whatever its length.

Under ``set_budget(math.inf)`` everything is built at once, nothing is announced
and a new width is taken in the frame that asks for it, which is what the tests
run with (``tests/conftest.py``) and what a script taking one screenshot wants.
No decision is then made from a time some earlier layout took.

## Two counts, and why zero missing is not "it drew"

``ViewCache.drawn(key)`` is ``(glyphs, missing)`` from the last draw: ``missing`` is a
glyph ImGui had no raster for *at draw time*. ``ViewCache.replaced(key)`` is how many
characters of the text in the view the library set as U+FFFD *when it built the
block*, because no loaded face has them; from then on each is an ordinary glyph and
is counted in ``glyphs``. ``x 😀 y 漢`` is ``(4, 0)`` and two replaced, and on screen it
is ``x`` U+FFFD ``y`` U+FFFD. "It drew the text" is both at zero. A diff view keeps
no such record, so for a diff ``replaced`` is 0 and says nothing.

## Decisions taken here, and why

**Body at the UI's 16 px, ragged right.** ``Theme.at_pixel_height(16)`` puts the
running text on the same raster the chrome is drawn from, so density does not drop.
Justified text needs hyphenation to avoid loose lines and the library has none.

**Text is configured by role, from fonts that already ship.** ``Role`` is the
catalogue -- prose, headings, inline code, code blocks, diffs, tool output,
arguments, tables, row titles -- and for each the operator may choose a ``RoleStyle``:
which of two families it is set in (``Slot``) and a size in whole pixels relative
to the body. Not per pane, and not by a named set of faces: a pane shows several
roles, and a named set answered a question nobody asked. Whether proportional
prose beside Inconsolata chrome reads as professional or as foreign is a judgement
for a person looking at it, which is why it is a setting and not a constant.
``DEFAULTS`` is Roboto for prose, headings, tables and row titles and Inconsolata
for the rest, all at the body's size. ``planning/2026-10-07-which-fonts-ship.md`` has
the reasoning and the operator's words.

**Two families are live at once, and that is the library's limit.** It names font
files by ``Face`` -- four proportional members, two monospace, one for mathematics --
and ``register_font`` keeps one file per member for the whole process. So "a family
per role" is: one family fills the proportional members (``proportional()``: Roboto,
or Droid Sans upright with Roboto's bold and italics), Inconsolata fills the
monospace pair because nothing else monospace ships, and a role chooses a slot.
Inside that limit the choice is free per role in one document: a theme gives every
block kind its own font, and the library reads a block's family off the block's
face (``_document_theme``). Roles whose columns are content -- code, diffs, tool
output, arguments -- are offered monospace only (``SLOTS``).

**A change of family adds nothing to the atlas.** A family with a file of its own
re-registers ``Face.PROSE``. Every file any family names is put on the atlas once,
in ``load_fonts``, and remembered by path (``_State.files``); a switch is then a
registry change, a font looked up by path, and the epoch. It is safe from inside a
frame, which is where a menu click arrives. The registration drops every metric the
library had read from a font file, so the frame after such a switch re-reads them:
half a second, once, on the operator's own click ("What it costs"). A role changing
slot or size registers nothing and costs the rebuild of what is on screen.

**Line spacing is a table in code** (``LEADING``): a role's pitch as a multiple of
its own pixel height, taken to a whole pixel -- 17 px at a 16 px size for prose and
for literal text, 18 for a diff's rows. There is no setting, by the operator's
decision; the table is the place to change it. It holds at a pitch as tight as
the face itself because no glyph is measured taller than its line
(``_LineMetrics.bbox``): the library otherwise moves a line down when its ink would
touch the one above, which at 17 px is every line with a bracket in it.

**Each pane is switched on its own** (``Pane``, ``enabled(pane)``), under
``Settings.typeset`` as the switch over all of them. Switching a pane is not a
change of faces and rebuilds nothing.

**Typesetting that is off is not paid for, and a library that is absent is
survivable.** This module imports with ``advtext`` missing or broken: nothing at
module level touches a library name, ``available()`` is false and
``unavailable_reason()`` says what the import raised. With ``Settings.typeset`` off at
launch the faces are not loaded (``theme`` calls ``load_fonts_later``); switching it
on loads them in the next ``pre_frame``, between frames, which is the second the
load costs -- 1.11 s here under a real renderer, the atlas going from 2 fonts to
10 -- and the menu says so before the click. The library's scratch directory, a
patched font of about 730 kB under the temporary directory per launch, is removed
when the process ends (``_remember_scratch``).

**Faces belong to one ImGui context.** ``available()`` is false under any other, so
a test file that leaves its faces loaded and destroys its context cannot leave the
seam switched on for the next one.

**Code, diffs and literal text are Inconsolata at the chrome's own size, and so is
a code span.** A theme sizes every block in ems of its body, and Roboto's em at
16 px is smaller than Inconsolata's, so a fenced block in a proportional document
would come out about 14 px tall, and a code span in a paragraph the same. Each
block kind is given its own role's size, and a span the size of ``Role.INLINE_CODE``
through the one call the library's markdown reader makes to ask what a run is set
in (``_theme_class``): a settled document and a stream alike, with no built node
rewritten.

**Headings are a tighter scale than the library's.** The library's ratio is 1.25 per
level, an h1 nearly twice the body: right for a page, wasteful in a pane. ``_HEADINGS``
is the scale used; block spacing is trimmed the same way. ``Part.demote`` and
``markdown(demote=)`` set a body's headings further down, under a pane's own.

**Mathematics is not parsed, and the math face is loaded anyway.** No call here
passes ``math=``, so ``$PATH=$HOME`` is text. The face is a different matter: a
character the prose face lacks is borrowed from the math face before the code face,
Roboto has no ``→`` or ``✓``, and a face that was not loaded up front is read on the
first frame that needs it. Model prose is full of arrows, so that frame -- most of
a second, in the callback that also drains the bridge -- would arrive mid-session.
``load_fonts`` pays it once: loading the faces is 0.34 s without the math face and
1.15 s with it, measured here in fresh processes.

**Text is measured with ImGui's advances, not the font file's** (``pixel_metrics``,
under ``METRICS = "hinted"``, which is what ships). The library lays out from the
file's advances and draws each glyph of a word a whole-pixel gap from the last.
ImGui's advance for a glyph is a different number -- a whole pixel chosen by its
hinter, 8 for Inconsolata at 16 px where the file says 7.63 -- so each word was
drawn wider or narrower than the layout believed and the error landed in the space
after it: a long word followed by almost no gap, a short one by a double gap, in
both faces. The library takes its metrics through a resolver the host supplies, and
the one supplied here asks ImGui. Word spacing is then even and monospace text sits
on the chrome's 8 px grid. Pair kerns are the file's, to the whole pixel; ImGui
does not kern, so a laid-out ``AV`` is narrower than ``calc_text_size`` says, and is
drawn where it was laid out.

**The other way is one constant away** (``METRICS = "file"``): the faces loaded
unhinted, so that ImGui's advances are the file's, and the library's own metrics
with nothing overridden but a glyph's measured height. Every view kind draws under
it and the suite passes under it (``PPTMSTR_TEST_METRICS=file``). It does not by
itself make what is drawn agree with what was laid out: the library's draw sets
each glyph a whole number of pixels from the last whatever the atlas, so a
monospace word laid out at 7.63 px a glyph is drawn at 8 and takes the difference
out of the space after it. ``scripts/compare_metrics.py`` shows both.

**Every space is the same width** (``Spacing.FRENCH``). The library's default widens
the space after a full stop by a third, which reads a ``.`` in ``foo.bar baz`` or after
an abbreviation as the end of a sentence, and in monospace breaks the grid.

**The palette is derived from the live pptmstr palette, not pushed on a theme
switch.** ``palette()`` is keyed on the identity of ``theme.current()``, so there is no
"rebuild on switch" step for the app to forget. Colours are passed at draw time and a
theme switch lays nothing out again and keeps every selection.

**A fenced ``diff`` or ``patch`` block is drawn as a diff**, in a document, in a part
and in a stream, where it grows a line at a time. It is text that only ever was a
diff, which is the one case the library's unified reader is for. Only when the
info string is that one word (``_is_diff_fence``) and the view draws every line
("Nothing written is hidden").

**Five things are drawn or set by the seam because the library has no setting for
them**, each host-side and none a patch to the library: a rule under every row of
a table and none of the library's under the header (``_table_rules``; the
library's stands a blank line under the header and its rows are otherwise
indistinguishable from a wrapped cell's lines); a veil over the table and the
fenced diff of a part drawn dim (``_overlay``); a table that fits held at its
columns' natural widths, as a stream holds one (``_held_table``); a paragraph's
air under the last item of a list (``_dressed``); and no glyph measured taller
than its line, so that a pitch holds and box-drawing joins (``_LineMetrics.bbox``).

**A view is rebuilt only when what it shows changes**, and then as little of it as
changed. New text under a key is shown by the same view, which keeps its selection;
a change of faces or of size (``_STATE.epoch``) makes a new view. Either way the
view's scrolling child is named for the *key*, so its scroll position stays.

**A diff is built from the texts a parked call carries, and nothing here reads a
file.** ``approval.diff_texts`` is the one place that decides what a call does to a
file; the driver runs it at park time and ``PendingApproval.texts`` holds the answer.
The seam turns texts into a model (``diff_model``) and a model into a held view. What
the gate said about whether an edit will apply is a prediction and is worded as one
in the view's own header: ``! may not apply: <reason>``, and again above the view,
wrapped, whenever the header is too long for it.

**The panes hold the views.** There is no table in this module keyed by approval or
node. A pane keeps a ``ViewCache`` in its own state and prunes it by its own rule, as
it already does for everything else it remembers; the seam only knows how to build
what goes in one and when a held view has gone stale. One view, one pane: a parked
call shown by the inbox and by DETAIL in one frame is two views (and may be one
``Diff`` model, which is immutable).

**The size of running text is the UI font's, read live** (``pre_frame``), so a display
scale ``hello_imgui`` applies to the chrome reaches the text. Only ever seen here at
16 px on a 96 dpi Xvfb. ``Settings.typeset_scale`` multiplies it for typeset text and
for nothing else: a multiplier and not an override, so the text still follows the
display. Other than at 1 the product is taken to a whole pixel, the only kind of
size ImGui bakes.

**A width change is debounced for a view that is not cheap to lay out, and for a
long one is laid out beside it.** Layout is linear in the document at every *new*
width (a width seen before is cached): 340 ms for 600 lines of markdown, 1.1 s for
2,000 and 3.1 s for 5,600, measured through ``markdown()``. Dragging a splitter would
cost that every frame, so such a view keeps its old width until the new one has
stood for ``_RESIZE_SETTLE_S``, drawn in the room there now is (``_no_wider``). The
re-break is one call into the library for one view, and dividing it there would
shrink the document and grow it back under the reader's scroll position; so past
``_REBREAK_SPREAD_MS`` a second view is given the same blocks a few pieces a frame,
as a first build is, and takes the first one's place when it is whole
(``_rebreak``). ``ViewCache.view(key)`` is then another object, with the selection
carried over. Text that changes meanwhile does not start it again: the second
view keeps the pieces whose blocks did not change, so a text growing every frame
has its new width in about the frames a still one takes.

**A capped view ends on a whole line.** ``height_cap`` is taken down to the foot of
the last line that fits (``_snapped``), to whole rows for a diff, with the diff's
horizontal scrollbar kept clear of them (``_diff_cap``); and a capped view's text
stops ``_GUTTER_PX`` short of its scrollbar.

**A view scrolled out of its window is not drawn**, only given its room, unless it
owns the selection (Ctrl+C must still reach it). This is what lets a pane stack many
views in one scrolling child.

**What is copied is what was written.** The library writes a space or a newline for
every line a layout made, and three things the seam does make lines: breaking a
word at the window's edge, cutting whitespace-exact text to the window, and
building a long literal text as several blocks. A settled view answers
``selected_text()``, ``block_text(n)`` and Ctrl+C with those taken back out
(``_selected``).

## What it costs, measured here

Bare ImGui frames with no renderer, on the machine this was written on, with text
that does not repeat (the library caches layout by a block's value, and a document
made of one paragraph a hundred times over measures a fifth of this).
``scripts/verify_typeset.py`` prints a frame time under a real window.

- **Drawing: about 4 microseconds a glyph on screen, every frame**, in Python. A
  36-line document in view is 2.5 ms a frame; a pane full of prose is 4 to 5 ms. It
  follows what is in view and not the document's length. ``imgui.text_unformatted``
  of the same lines is 0.01 ms, which is the price of placing every glyph ourselves.
- **Parse and first layout: about 0.4 ms a source line of markdown**, 0.15 of
  literal text. Built at a stroke, 5,600 lines of markdown are 2.3 s and forty
  stacked 120-line views 1.7 s; that is what the budget divides.
- **With the budget**, the same 5,600 lines are on screen from the first frame and
  whole after about 70 frames of 41 ms (median; 47 ms at the 95th percentile);
  the forty views take 60 frames of 36 ms. 2,000 literal lines: 17 frames of 21 ms.
  400 small parts in one ``document``: 20 frames of 22 ms.
- **A change at the end of a long text**: a paragraph added to 5,600 lines of
  markdown is 24 ms, against 1.7 s for parsing the whole text again; a line added
  to 2,000 literal lines is 9 ms, against 340 ms. What is left is the library
  walking every block of the document to find its layout again, and it grows with
  the document. A change inside one long paragraph, list or fence re-reads all of
  that piece.
- **A change of faces** under those forty views: 42 ms for the frame that rebuilds
  the ones on screen, against 1.8 s for all forty.
- **A diff's model**, which is ``difflib`` and is announced and not divided: changes
  scattered through 5,000 lines 270 ms, through 20,000 lines 980 ms; every seventh
  line of 5,000 changed 510 ms, of 20,000 7.9 s. One contiguous edit in a file of
  any length is cheap. With ``background=True`` no frame holds it: what is left is
  the view's first draw, 100 ms and 200 ms for those two.
- **A change of width**, built beside the view: 600 / 2,000 / 5,600 lines of
  markdown take 16 / 33 / 78 frames of about 50 ms (the longest 52 / 53 / 74 ms),
  where one frame took 340 ms / 1.1 s / 3.2 s.
- **A stream**: each reading of new text lays the unfinished block out again, 1.5
  to 3 ms on top of the draw, and a stream given 200 / 400 / 800 lines at once
  spends 50 ms on its first frame (30 ms without the free allowance) where it
  spent 160 / 220 / 470 ms.
- **A fenced diff's accounting** (``_accounted``): the fence is read again whole
  each time its block changes, 0.4 ms a hundred lines, and counted twice, 0.1 ms
  a hundred lines each. Settled, that is once. Arriving a line a frame, a fence
  of 100 lines is 3.3 ms a frame and one of 500 is 5.3 ms, where drawing them
  unaccounted was 2.9 and 3.3 ms: it grows with the fence.
- **The collector.** Python's generational collector is left on, as the application
  leaves it. With several thousand lines of documents alive, a full collection
  landed in one frame in most runs of the above and took 150 to 350 ms. It is not a
  call into this module and no budget here sees it. A frame that only draws
  allocates almost nothing that the collector tracks -- 1 or 2 young collections
  in 300 frames, settled or streamed. What feeds it is layout: a stream taking a
  token a frame ran 120 young, 11 middle and 1 full collection in 300 frames,
  nearly all of it the library laying the unfinished block out again.
- **Start-up: 1.1 s in ``theme.load_fonts``** under a real window this run (1.1 to
  1.8 s across earlier ones), nearly all of it the faces, 0.8 s of that the math
  face. Nothing when ``Settings.typeset`` is off.
- A small diff 0.6 ms a frame, a stream with nothing arriving 0.7 ms, six literal
  lines 0.3 ms, a view out of sight 0.004 ms: measured before the budget existed and
  not re-run.
- **A change of faces**, in a real window (Xvfb, llvmpipe) with a 30-line document
  in view, measured when these were named presets: between ones that share their
  files -- a role changing slot or size -- ``configure`` is 0.03 ms and the frame
  that rebuilds the views 20 to 40 ms. To or from a family with a file of its own
  (Droid Sans), ``configure``
  is 50 to 250 ms and the next frame 430 to 510 ms, nearly all of it the library
  reading every face's metrics again after the registration, the math face's
  among them. The atlas holds the same ten fonts throughout. Measured by the task
  that added presets and not re-run.

## What a consumer must not do

- Import ``advtext``. Ask for what is missing here instead.
- Decide for itself whether a body is markdown. ``prose_parts`` is the rule.
- Share one ``ViewCache`` between two panes, or draw one key twice in a frame: a view
  holds one scroll position and one origin. The second draw is refused, visibly.
- Call ``approval.diff_texts`` per frame for arguments being edited: it reads the
  file. Call it when the arguments change and hand ``diff()`` the result.
- Wrap a ``diff()`` given a ``height_cap`` in a child of your own: it opens one.
- Size a diff's cap in ``line_height()``s: a diff's row is ``diff_row_height()``.
- Keep the object ``ViewCache.view(key)`` answered across frames. It is another
  after a change of faces, and after a long text was laid out at a new width.
- Say ``may not apply`` on a line of your own *and* let the diff say it: pass
  ``diff(warning=False)``, or leave it to the diff.
- Draw a heading for a ``diff()`` before knowing it returned a number. ``None`` is
  "nothing to show", and a model with no files is that too.
- Put a FontAwesome glyph in typeset text. The library's monospace face is
  Inconsolata without the icon merge, so a state glyph would be set as U+FFFD (and
  counted in ``replaced``); those stay native ``imgui.text`` in the UI font.
- Put ``→``, ``✓`` or a box-drawing character in a ``label()`` or under ``font()`` in a
  prose weight. A native line has one font and no fallback; see ``font``.
- Feed a ``Stream`` from a thread other than the one that draws it.
- Forget ``ViewCache.prune``: a view holds its document, its layout at every width it
  has been drawn at, and its selection index.
- Draw an uncapped view in a window that does not scroll and is as tall as the
  text. A view culls to the window it is in; in one that shows all of it, every
  glyph is drawn every frame, at about 4 microseconds each.
- Expect a line of a fenced block or of ``plain()`` wider than the pane to be
  reachable in a window opened without ``horizontal_scrollbar``. Code is never
  broken; the seam reports the width and the window decides whether to scroll to
  it. Text that must be reachable everywhere is ``Kind.WRAPPED_EXACT``.
- Read a view's content in the frame it was first asked for and assume it is all
  there. ``ViewCache.unbuilt(key)`` says how much is not; a pane that swaps one view
  for another of the same text (a stream for its settled run) can wait for zero.
- Offer an approval of a text while its ``unbuilt`` is not zero. The seam cannot
  hold a pane's button or a key; it answers the count and, asked, says so first.
- Assume a *list* of parts is taken on identity. A tuple is; a list is compared
  part by part on every call.

## Reached past the facade

Four places in the library, each named where it is used, and a test pins the
imports to them; and one in ImGui (``imgui.internal``, in ``_no_wider``: a window's
content edge, to draw a stale layout without widening the window).

- ``advtext.markdown._parser`` (``_reader``): to switch off the parser rules that hide
  text, add the one that shows a link's destination and the one that reads a
  glob's stars as stars. The library offers no option for any of them. If the
  name goes, or stays and is no longer the parser views read with (a probe is
  read to find out), ``load_fonts`` fails and nothing is typeset.
- ``advtext.markdown.replaced_characters`` (``_replaced``): the count behind
  ``ViewCache.replaced``.
- ``advtext.nodes`` (``_nodes``): the character, glue and penalty nodes, to give a
  paragraph a place to break.
- A view's ``layout`` and its ``model`` (``_selected``, ``_mended``): attributes the
  library offers on a view and does not list, to read a selection block by block.

Read through objects the facade hands out, and so not pinned by that test: a diff
view's ``frame_plan`` and the glyph commands in it (``_headers``); a block style's
``strut`` (``_table_rules``, ``_last_foot``) and, off a style of ``Theme.build()``, how
the library divides a leading into a strut and spells a font key (``_template``);
a hosted diff view's ``theme``, which is assigned (``_Fences``).

**``Theme`` is subclassed** (``_theme_class``), to answer ``font_for`` for a code span.
The library's markdown reader calls it for every run; a reader that stopped
calling it would set spans at their block's size again, and
``test_inline_code_is_the_chromes_height_inside_prose_of_the_same_height`` reads
the built nodes to say so.

One selection at a time across every view is the library's own doing
(``SELECTION_OWNERSHIP``, in ``advtext.ui.bare_view``); nothing here needs to touch it.
"""

from __future__ import annotations

import atexit
import bisect
import enum
import math
import re
import shutil
import tempfile
import threading
import time
from collections.abc import (
    Callable,
    Collection,
    Hashable,
    Iterable,
    Iterator,
    Mapping,
    Sequence,
)
from contextlib import contextmanager
from dataclasses import dataclass, field, fields, replace
from functools import cache
from pathlib import Path
from typing import TYPE_CHECKING, Any

try:
    from advtext import (
        ALL_FACES,
        Align,
        BareView,
        BlockKind,
        BlockSource,
        CodeBlock,
        ColourSpan,
        Diff,
        DiffFences,
        DiffLayout,
        DiffPalette,
        DiffView,
        Document,
        Face,
        Faces,
        LoadedFaces,
        Palette,
        Spacing,
        StreamDocument,
        StreamView,
        TableBlock,
        TextBlock,
        Theme,
        build_document,
        char_measure,
        code_block,
        fontlib_metrics,
        load_faces,
        material_text,
        px_from_sp,
        register_font,
        registered_fonts,
        sp_from_px,
        table_column_limits,
        text_block,
        unregister_font,
        with_columns,
    )
except Exception as _failure:
    # The library absent, or one of its own imports broken. The application must
    # still start: nothing below this line touches a library name at import time,
    # and every use of one is behind `available()`.
    _IMPORT_FAILURE: str | None = f"{type(_failure).__name__}: {_failure}"
else:
    _IMPORT_FAILURE = None

from .. import theme as ui_theme
from ..approval import DiffText
from ..model import PendingApproval
from ..theme import Color

if TYPE_CHECKING:
    from imgui_bundle import imgui

BODY_PX = 16.0
"""
The pixel height of running text until a frame says otherwise: ``theme._FONT_SIZE``.

The live value is ``body_px()``, read from ImGui between frames (``pre_frame``).
"""

_HEADINGS: tuple[tuple[int, float, float, float], ...] = (
    (1, 22 / 16, 0.70, 0.30),
    (2, 19 / 16, 0.70, 0.30),
    (3, 17 / 16, 0.60, 0.25),
    (4, 1.00, 0.50, 0.20),
    (5, 1.00, 0.50, 0.20),
    (6, 1.00, 0.50, 0.20),
)
"""
Each heading level: its size, and the air above and below it, in ems of the body
(the air in ems of its own size). See "Headings are a tighter scale" above. The
sizes are whole pixel heights at a 16 px body -- 22, 19, 17 -- because ImGui bakes
at a whole pixel and a size between two would be drawn at one of them anyway.

By level and not by the library's block kind, so that this module imports with
the library absent.
"""


def _heading(level: int) -> BlockKind:
    return BlockKind[f"HEADING_{level}"]


_PARAGRAPH_GAP_EM = 0.45
_CODE_GAP_EM = 0.40
_PART_GAP_EM = 0.30

_CHEAP_RELAYOUT_MS = 4.0
_RESIZE_SETTLE_S = 0.12
"""
A view whose last layout cost more than the first keeps its width until a new one
has stood for the second. See "A width change is debounced" above.
"""

_MIN_WIDTH_PX = 80.0
"""
The narrowest measure a view is given; the library's own floor (``MIN_WIDTH_PX``).
"""

_GUTTER_PX = 6.0
"""
The air between a capped view's text and its own scrollbar.

A pane's window pads its content away from its scrollbar; the child a capped view
scrolls in has no padding, so without this a full line ends on the scrollbar.
"""


class Weight(enum.Enum):
    """
    The faces a native ImGui line can be set in, by what they are for.

    The prose weights follow ``Role.LABEL``: where that role is monospace they are
    Inconsolata, and there is no monospace italic, so ``ITALIC`` is upright there.
    """

    REGULAR = "regular"
    BOLD = "bold"
    ITALIC = "italic"
    BOLD_ITALIC = "bold-italic"
    CODE = "code"
    CODE_BOLD = "code-bold"


_PROSE_FACES = ("PROSE", "PROSE_BOLD", "PROSE_ITALIC", "PROSE_BOLD_ITALIC")
"""
The library's proportional roles, by name: ``Face[name]`` where one is needed.
"""

METRICS = "hinted"
"""
Whose advances lay text out: ``"hinted"``, ``"light"`` or ``"file"``. Code, not a
setting.

- ``"hinted"``: the faces are loaded as ImGui loads any font, through FreeType's
  hinter, and every advance is asked of ImGui's baked font (``_PixelMetrics``).
  Word spaces are even and monospace text sits on the chrome's whole-pixel grid.
- ``"light"``: the same, with the faces loaded under FreeType's light hinting
  (``_LOADER_FLAGS``), whose whole-pixel advances stay nearer the font file's.
- ``"file"``: the faces are loaded with hinting off, so ImGui's advances are the
  font file's, and the library measures from the file as it was designed to.
  Nothing of its measuring is overridden. **What is drawn still does not agree
  with what was laid out**: the library's draw sets every glyph a whole number of
  pixels from the last whatever the atlas (measured by ``scripts/compare_metrics.py``:
  no pen is fractional), so a monospace word laid out at 7.63 px a glyph is drawn
  at 8 and the difference comes out of the space after it.

Read when the faces are loaded and when a metric is first asked for, so changing
it means loading the faces again (``load_fonts``); the test suite does, to run
under another (``tests/conftest.py``).
"""

_LOADER_FLAGS = {"file": 1, "light": 8}
"""
``ImGuiFreeTypeLoaderFlags_NoHinting`` and ``_LightHinting``, by ``METRICS``, for
``ImFontConfig.font_loader_flags``. The binding exposes the field and not the enum.
"""


class Role(enum.Enum):
    """
    What a run of typeset text is, which is what its face and size are chosen by.

    The catalogue is what the seam and the panes tell apart, and no finer:

    - ``PROSE``: paragraphs, list items and quotes of a markdown body, and plain
      text set in the prose face (``plain(mono=False)``).
    - ``HEADING``: the six heading levels, which keep their scale between them.
    - ``INLINE_CODE``: a code span inside prose, a heading or a table cell.
    - ``CODE_BLOCK``: a fenced block.
    - ``DIFF``: the rows of ``diff()``, and of a diff fenced in a document.
    - ``LITERAL``: text that is not markdown -- tool calls, results, errors,
      tracebacks: every ``Kind`` but ``MARKDOWN`` unless the part says otherwise.
    - ``ARGUMENT``: a parked call's argument names and values (``Part.role``).
    - ``TABLE``: the cells of a markdown table.
    - ``LABEL``: a native ImGui line in a typeset face (``label()``, ``font()``): a
      board row's title, a subject.
    """

    PROSE = "prose"
    HEADING = "heading"
    INLINE_CODE = "inline-code"
    CODE_BLOCK = "code-block"
    DIFF = "diff"
    LITERAL = "literal"
    ARGUMENT = "argument"
    TABLE = "table"
    LABEL = "label"


ROLE_LABELS: dict[Role, str] = {
    Role.PROSE: "Prose",
    Role.HEADING: "Headings",
    Role.INLINE_CODE: "Inline code",
    Role.CODE_BLOCK: "Code blocks",
    Role.DIFF: "Diffs",
    Role.LITERAL: "Tool output",
    Role.ARGUMENT: "Arguments",
    Role.TABLE: "Tables",
    Role.LABEL: "Row titles",
}
"""
What the settings dialog calls each role.
"""


class Slot(enum.Enum):
    """
    Which of the two live families a role is set in.

    Two, because the library names font files by ``Face`` -- four proportional
    members, two monospace, one for mathematics -- and keeps one file per member
    for the whole process (``register_font``). So at any moment one family fills
    the proportional members (``proportional()``) and one the monospace pair
    (Inconsolata, the only monospace that ships), and what a role chooses is which
    of the two it uses. A theme sets a face per block kind and one view takes one
    theme, so the choice is free per role inside a single document.
    """

    PROPORTIONAL = "proportional"
    MONOSPACE = "monospace"


@dataclass(frozen=True, slots=True)
class RoleStyle:
    """
    What the operator may choose for one role: a family slot, and a size.

    ``step`` is whole pixels on top of the body size (``body_px()``), which is the
    UI font's times ``scale()``: 0 is the chrome's own height, in either family.
    Whole pixels because that is what ImGui bakes.
    """

    slot: Slot
    step: int = 0


STEPS: tuple[int, ...] = (-2, -1, 0, 1, 2, 3, 4)
"""
The size steps offered, in pixels relative to the body. ``configure`` clamps to the
first and the last.
"""

DEFAULTS: dict[Role, RoleStyle] = {
    Role.PROSE: RoleStyle(Slot.PROPORTIONAL),
    Role.HEADING: RoleStyle(Slot.PROPORTIONAL),
    Role.INLINE_CODE: RoleStyle(Slot.MONOSPACE),
    Role.CODE_BLOCK: RoleStyle(Slot.MONOSPACE),
    Role.DIFF: RoleStyle(Slot.MONOSPACE),
    Role.LITERAL: RoleStyle(Slot.MONOSPACE),
    Role.ARGUMENT: RoleStyle(Slot.MONOSPACE),
    Role.TABLE: RoleStyle(Slot.PROPORTIONAL),
    Role.LABEL: RoleStyle(Slot.PROPORTIONAL),
}
"""
Every role's style until the operator says otherwise.

Inline code at step 0 is Inconsolata at the chrome's own pixel height inside prose
of the same height, so a code span is the size of the same word in the chrome.
"""

SLOTS: dict[Role, tuple[Slot, ...]] = {
    Role.PROSE: (Slot.PROPORTIONAL, Slot.MONOSPACE),
    Role.HEADING: (Slot.PROPORTIONAL, Slot.MONOSPACE),
    Role.INLINE_CODE: (Slot.MONOSPACE,),
    Role.CODE_BLOCK: (Slot.MONOSPACE,),
    Role.DIFF: (Slot.MONOSPACE,),
    Role.LITERAL: (Slot.MONOSPACE,),
    Role.ARGUMENT: (Slot.MONOSPACE,),
    Role.TABLE: (Slot.PROPORTIONAL, Slot.MONOSPACE),
    Role.LABEL: (Slot.PROPORTIONAL, Slot.MONOSPACE),
}
"""
The slots a role may be given. Code, diffs, literal text and arguments are
monospace by nature -- their columns are content -- and are offered nothing else.
"""

LEADING: dict[Role, float] = {
    Role.PROSE: 17 / 16,
    Role.HEADING: 1.0,
    Role.CODE_BLOCK: 17 / 16,
    Role.DIFF: 18 / 16,
    Role.LITERAL: 17 / 16,
    Role.ARGUMENT: 17 / 16,
    Role.TABLE: 17 / 16,
}
"""
Line spacing, per role: the distance from one baseline to the next as a multiple
of the role's own **pixel height**. THE place to change it; there is no setting.

``17 / 16`` reads "17 px a line at a 16 px size". The product is taken to a whole
pixel (``pitch()``), so every line of a block is the same height. For scale: ImGui
sets wrapped text at 1.0 (16 px at 16), and at 1.0 a line's descenders and the
next line's ascenders touch but do not overlap -- the faces are exactly their
pixel height tall. A multiple of the pixel height and not of the em, because the
em at one height differs by face (Roboto 13.65 px, Inconsolata 15.25 px at 16).

A heading's is of its own size (22 px at h1), which is where a wrapped heading's
lines go. A diff's rows are the library's grid and take the number rounded up.
Inline code has no line of its own, and a label is ImGui's line: neither is here.
"""

_MULTILINE: dict[Role, str] = {
    Role.LITERAL: "literal",
    Role.ARGUMENT: "literal",
}
"""
How a role's text of **several lines** meets the window's edge, by ``Kind`` value:
``"literal"`` is set unbroken and the window scrolls sideways to it,
``"wrapped-exact"`` is broken at the edge with every space kept. Internal, like
``LEADING``. ``multiline_kind()`` answers from it.

By value and not by member because ``Kind`` is defined further down.
"""


class Pane(enum.Enum):
    """
    The panes that can set their text through this module, each switched on its own.
    """

    DETAIL = "detail"
    INBOX = "inbox"
    CONTEXT = "context"
    BOARD = "board"
    BRIEF = "brief"


PANE_LABELS: dict[Pane, str] = {
    Pane.DETAIL: "DETAIL",
    Pane.INBOX: "NEEDS YOU",
    Pane.CONTEXT: "CONTEXT",
    Pane.BOARD: "BOARD",
    Pane.BRIEF: "BRIEF",
}
"""
What the settings dialog calls each pane: the titles the panes carry.
"""


@dataclass(frozen=True, slots=True)
class Proportional:
    """
    A family for the proportional slot, from fonts that already ship.

    ``file`` is a font in the imgui-bundle wheel, relative to its fonts directory,
    to set upright text in instead of the library's Roboto. It replaces one of
    the library's four proportional faces and no other, which is the honest shape
    of a family that ships in one style: bold, italic and bold italic -- so every
    heading -- stay Roboto, and such a family is two families in one paragraph.
    """

    name: str
    label: str
    file: str | None = None


DEFAULT_PROPORTIONAL = "roboto"

PROPORTIONALS: tuple[Proportional, ...] = (
    Proportional(DEFAULT_PROPORTIONAL, "Roboto"),
    Proportional("droid", "Droid Sans (Roboto emphasis)", file="DroidSans.ttf"),
)
"""
What may fill the proportional slot, in the order offered. The first is the default.
"""

DEFAULT_PRESET = "roboto"
"""
The name the face presets gave the defaults, kept as a spelling ``configure(faces=)``
and a saved ``typeset_faces`` still understand (``legacy()``).
"""

_LEGACY: dict[str, tuple[str, dict[Role, RoleStyle]]] = {
    DEFAULT_PRESET: (DEFAULT_PROPORTIONAL, {}),
    "mono": (
        DEFAULT_PROPORTIONAL,
        {role: RoleStyle(Slot.MONOSPACE) for role, offered in SLOTS.items() if len(offered) > 1},
    ),
    "roboto-large": (DEFAULT_PROPORTIONAL, {Role.PROSE: RoleStyle(Slot.PROPORTIONAL, 2)}),
    "droid": ("droid", {}),
}
"""
What each face preset of the build before roles means now: a family for the
proportional slot, and the roles that differ from ``DEFAULTS``.

``roboto-large`` was prose at 18 px beside 16 px chrome, which is two steps.
"""

SCALES: tuple[float, ...] = (0.875, 1.0, 1.125, 1.25)
"""
The text sizes the settings dialog offers, as multiples of the UI font's: 14, 16, 18 and 20 px
at a 16 px UI font. ``configure`` takes any value between the first and the last.
"""


@dataclass
class _State:
    """
    What the seam holds for the whole process: the settings it was told, and the fonts.

    Module state because the things in it are facts about the application -- one
    atlas, one font registry, one operator preference -- and not about any pane.
    """

    typeset: bool = True
    # What the operator chose: a style for every role, the family in the
    # proportional slot, and the panes switched off. `style()` and
    # `proportional()` are what is in force, which differs when a face did not load.
    roles: dict[Role, RoleStyle] = field(default_factory=lambda: dict(DEFAULTS))
    family: str = DEFAULT_PROPORTIONAL
    panes_off: frozenset[Pane] = frozenset()
    scale: float = 1.0
    fonts: LoadedFaces | None = None
    # Every font file put on the atlas since `load_fonts`, by path. What makes a
    # change of family a lookup: see "A change of family adds nothing to the atlas".
    files: dict[Path, Any] = field(default_factory=dict)
    # The UI font's size as ImGui last reported it; see `pre_frame`.
    live_px: float = BODY_PX
    # Goes up whenever a document built before would be wrong now: a role's
    # style, the family or the scale changed, the UI font's size did, or the
    # fonts were loaded again.
    # Views compare it with the one they were built under.
    epoch: int = 0
    themes: dict[str, Theme] = field(default_factory=dict)
    # The advtext palette for one pptmstr palette, with the per-ink variants made
    # from it. Keyed by identity: `theme.current()` is a frozen module constant.
    palette_of: object | None = None
    palettes: dict[int | None, Palette] = field(default_factory=dict)
    measure: Any = None
    # The ImGui context whose atlas the fonts are on. An `ImFont` is a pointer into
    # that atlas and means nothing, or worse, in any other.
    home: Any = None
    # Why text cannot be typeset, in words for the menu, or `None` while it can.
    why: str | None = "the faces have not been loaded"
    # A load the application asked to have done when typesetting is switched on:
    # the bold file and the loader. See `load_fonts_later`.
    deferred: tuple[Path | None, Callable[[Path], Any] | None] | None = None
    # The library's scratch directories this process made, removed at exit.
    scratch: set[Path] = field(default_factory=set)
    # Milliseconds a frame may spend building views before the rest wait for the
    # next, and what the frame numbered `spent_in` has spent. See `set_budget`.
    budget_ms: float = 6.0
    spent_in: int = -1
    spent_ms: float = 0.0
    built_lines: int = 0
    # What laying out a source line has been costing, by kind of text, in ms.
    layout_rate: dict[str, float] = field(default_factory=dict)
    # The last frame in which a view on screen was drawn short of its whole text.
    unfinished_in: int = -2
    # A diff row's height, with the epoch it was measured under.
    diff_row: tuple[int, float] | None = None

    @property
    def body_px(self) -> float:
        """
        The pixel height of running text: the UI font's, times the operator's scale.
        """
        if self.scale == 1.0:
            return self.live_px
        return max(1.0, _half_up(self.live_px * self.scale))


_STATE = _State()


# ---------------------------------------------------------------------------
# Settings and fonts
# ---------------------------------------------------------------------------


def configure(
    *,
    typeset: bool,
    scale: float | None = None,
    proportional: str | None = None,
    roles: Mapping[Role, RoleStyle] | None = None,
    panes: Mapping[Pane, bool] | None = None,
    mono: bool | None = None,
    faces: str | None = None,
) -> None:
    """
    Tell the seam what the operator's settings say. Call it on any change.

    ``roles`` is every role that differs from ``DEFAULTS``; a role it leaves out is
    at its default, so ``{}`` is the defaults. A slot the role is not offered
    (``SLOTS``) is read as the role's default slot and a step is clamped to
    ``STEPS``. ``proportional`` names the family in the proportional slot; one this
    build does not know is kept as written and the default is used
    (``proportional()``). ``panes`` says which panes are switched on; one it leaves
    out is on.

    ``faces`` and ``mono`` are the same choices spelled as the presets and the
    switch they were before there were roles (``legacy``): ``faces`` sets the family
    and the roles together, ``mono=True`` is ``faces="mono"`` and ``mono=False`` the
    defaults. ``roles`` and ``proportional`` win over ``faces``, which wins over
    ``mono``. A call that gives none of them, or no ``scale``, or no ``panes``,
    leaves that setting as it was.

    A change of a role, of the family or of the scale means every document was
    built in the wrong faces or at the wrong size, so the epoch moves and each
    view is rebuilt the next time it is drawn. Switching a pane moves nothing.
    Callable before the fonts are loaded, and from inside a frame.
    """
    _STATE.typeset = typeset
    absent = _IMPORT_FAILURE is not None
    # With the library absent there is nothing to rebuild and no registry to keep
    # in step; the settings are still remembered, so the menu shows what was chosen.
    before = None if absent else _in_force()
    if panes is not None:
        _STATE.panes_off = frozenset(pane for pane, on in panes.items() if not on)
    old = faces if faces is not None else None if mono is None else "mono" if mono else ""
    if old is not None:
        family, differing = legacy(old)
        _STATE.family, _STATE.roles = family, checked(differing)
    if proportional is not None:
        _STATE.family = proportional
    if roles is not None:
        _STATE.roles = checked(roles)
    if scale is not None:
        wanted = float(scale)
        # A hand-edited NaN compares false with everything and would pass a clamp.
        _STATE.scale = min(max(wanted, SCALES[0]), SCALES[-1]) if math.isfinite(wanted) else 1.0
    if not absent and _in_force() != before:
        _apply_faces()
        _STATE.epoch += 1
        _STATE.themes.clear()


def checked(differing: Mapping[Role, RoleStyle]) -> dict[Role, RoleStyle]:
    """
    ``DEFAULTS`` with ``differing`` laid over it, each style made one its role allows:
    every role's style as ``configure(roles=differing)`` would leave it. Pure, so a
    caller holding settings and no seam -- the settings dialog's model -- reads the
    same answer the seam will give.
    """
    out = dict(DEFAULTS)
    for role, given in differing.items():
        slot = given.slot if given.slot in SLOTS[role] else DEFAULTS[role].slot
        step = given.step if math.isfinite(given.step) else 0
        out[role] = RoleStyle(slot, int(min(max(step, STEPS[0]), STEPS[-1])))
    return out


def _in_force() -> tuple[object, ...]:
    """
    Everything a built document depends on, to tell a change that needs a rebuild.
    """
    return (tuple(style(role) for role in Role), proportional(), _STATE.body_px)


def legacy(name: str) -> tuple[str, dict[Role, RoleStyle]]:
    """
    What a face preset's name means now: the proportional family, and the roles
    that differ from ``DEFAULTS``. A name that was never a preset is the defaults.

    ``settings.load`` spells the same table for a saved ``typeset_faces``, without
    importing this module; a test holds the two together.
    """
    family, differing = _LEGACY.get(name, _LEGACY[DEFAULT_PRESET])
    return family, dict(differing)


def roles_from(entries: Iterable[tuple[str, str | None, int | None]]) -> dict[Role, RoleStyle]:
    """
    Role styles from how settings keep them: ``(role, slot, step)`` by value, with
    ``None`` for "the default". A role or a slot this build does not know is left
    out, so a file from a later build still loads.
    """
    out: dict[Role, RoleStyle] = {}
    for name, slot, step in entries:
        try:
            role = Role(name)
        except ValueError:
            continue
        base = DEFAULTS[role]
        try:
            chosen = base.slot if slot is None else Slot(slot)
        except ValueError:
            chosen = base.slot
        out[role] = RoleStyle(chosen, base.step if step is None else step)
    return out


def panes_from(entries: Iterable[tuple[str, bool]]) -> dict[Pane, bool]:
    """
    Pane switches from how settings keep them, by value; an unknown pane is left out.
    """
    out: dict[Pane, bool] = {}
    for name, on in entries:
        try:
            out[Pane(name)] = bool(on)
        except ValueError:
            continue
    return out


def chosen(role: Role) -> RoleStyle:
    """
    The style the operator's settings give a role, whether or not it can be had.
    """
    return _STATE.roles[role]


def style(role: Role) -> RoleStyle:
    """
    The style a role is set in: the one chosen, or monospace when a proportional
    face failed to load -- text measured from a file ImGui does not have would be
    drawn in whatever font was bound, at the wrong advances.
    """
    given = _STATE.roles[role]
    if given.slot is Slot.PROPORTIONAL and not _proportional_loaded():
        return RoleStyle(Slot.MONOSPACE, given.step)
    return given


def _proportional_loaded() -> bool:
    fonts = _STATE.fonts
    return fonts is None or all(fonts.fonts.get(Face[name]) is not None for name in _PROSE_FACES)


def px(role: Role) -> float:
    """
    The pixel height a role's text is set at: the body's, plus the role's step.
    """
    return max(1.0, _STATE.body_px + style(role).step)


def pitch(role: Role, tall: float | None = None) -> float:
    """
    A role's line pitch in pixels, whole: ``LEADING`` times the role's pixel height.

    ``tall`` is the height to take it of, for a heading level's own size.
    """
    return max(1.0, _half_up((px(role) if tall is None else tall) * LEADING[role]))


def multiline_kind(role: Role) -> Kind:
    """
    The ``Kind`` a role's text of several lines is set as: unbroken, for the window
    to scroll to, or broken at its edge (``_MULTILINE``). A role with no entry is
    broken at the edge, which hides nothing in a window that cannot scroll.
    """
    return Kind(_MULTILINE.get(role, Kind.WRAPPED_EXACT.value))


def proportionals() -> tuple[tuple[Proportional, bool], ...]:
    """
    Every family for the proportional slot with whether it can be chosen now, in
    the order offered.

    One whose file did not load cannot: it would be measured from a file ImGui has
    no font for.
    """
    return tuple((each, _usable(each)) for each in PROPORTIONALS)


def proportional() -> Proportional:
    """
    The family in the proportional slot: the one the settings name, or the default.

    The default when the name is not one of ``PROPORTIONALS`` -- a settings file
    from a later build -- and when the family's own file is not on the atlas.
    """
    for each in PROPORTIONALS:
        if each.name == _STATE.family and _usable(each):
            return each
    return PROPORTIONALS[0]


def scale() -> float:
    """
    The operator's multiplier on the UI font's size, as ``configure`` clamped it.
    """
    return _STATE.scale


def _usable(which: Proportional) -> bool:
    path = _prose_path(which)
    return path is None or _STATE.files.get(path) is not None


@cache
def _prose_path(which: Proportional) -> Path | None:
    """
    The file a family sets upright text in, or ``None`` for the library's own.

    Kept, because ``proportional()`` is asked whenever the settings are compared
    and resolving a path is a system call.
    """
    if which.file is None:
        return None
    return (_bundle_fonts() / which.file).resolve()


def _bundle_fonts() -> Path:
    import imgui_bundle

    return Path(imgui_bundle.__file__).parent / "assets" / "fonts"


def _apply_faces() -> None:
    """
    Make the library's font registry agree with the family in force.

    A no-op unless the file behind ``Face.PROSE`` has to change. When it does, the
    registration drops every metric the library read from a file, so the measure is
    made again and the prose face takes the font already on the atlas for the new
    file. Nothing is added to the atlas: ``proportional()`` only answers a family
    whose file ``load_fonts`` put there.
    """
    want = _prose_path(proportional())
    if registered_fonts().get(Face.PROSE) == want:
        return
    if want is None:
        unregister_font(Face.PROSE)
    else:
        register_font(Face.PROSE, want)
    _PIXEL_METRICS.clear()
    _STATE.measure = char_measure(pixel_metrics)
    fonts = _STATE.fonts
    if fonts is not None:
        fonts.load((Face.PROSE,))


def _atlas_loader(path: Path) -> Any:
    """
    A font file onto the current context's atlas: the loader a headless test wants.
    """
    im = _imgui()
    if METRICS not in _LOADER_FLAGS:
        return im.get_io().fonts.add_font_from_file_ttf(str(path), BODY_PX)
    config = im.ImFontConfig()
    config.font_loader_flags = _LOADER_FLAGS[METRICS]
    return im.get_io().fonts.add_font_from_file_ttf(str(path), BODY_PX, config)


def _remembering(loader: Callable[[Path], Any]) -> Callable[[Path], Any]:
    """
    ``loader``, asked for each file once: a path already on the atlas answers its font.

    The library adds a font to the atlas each time a role is loaded and never takes
    one off, so without this every switch to a preset and back would leave another
    copy of a face there.
    """

    def load(path: Path) -> Any:
        key = Path(path).resolve()
        hit = _STATE.files.get(key)
        if hit is None:
            hit = _STATE.files[key] = loader(path)
        return hit

    return load


def enabled(pane: Pane | None = None) -> bool:
    """
    Whether a pane should set its text through this module this frame.

    A pane asks for itself: ``enabled(Pane.BOARD)``. False when the operator turned
    typesetting off, false when that pane is switched off (``pane_on``), and false
    when the faces are not there to draw with -- before ``load_fonts`` has run, or
    when the monospace face failed to load. A pane that asks this and takes the
    other branch is unchanged. With no pane it is the master switch and the faces.
    """
    if pane is not None and pane in _STATE.panes_off:
        return False
    return _STATE.typeset and available()


def pane_on(pane: Pane) -> bool:
    """
    Whether the operator's settings have a pane switched on, whatever the master
    switch and the faces say.
    """
    return pane not in _STATE.panes_off


def available() -> bool:
    """
    Whether the faces are loaded *into the ImGui context that is current*, whatever
    the setting says.

    The second half is what keeps a pane from drawing with another context's fonts,
    or with a destroyed one's: faces loaded under one context are not available
    under the next, with nobody having to remember to call ``reset``.
    """
    fonts = _STATE.fonts
    if fonts is None or fonts.fonts.get(Face.CODE) is None:
        return False
    home = _STATE.home
    return home is not None and _imgui().get_current_context() == home


def wanted() -> bool:
    """
    Whether the operator's setting asks for typeset text, loaded or not.
    """
    return _STATE.typeset


def obtainable() -> bool:
    """
    Whether typeset text can be had: the faces are loaded, or will be when it is asked for.

    What the menu entry is enabled by. False when the library is absent or broken,
    and when a load was tried and failed; ``unavailable_reason()`` has the words.
    """
    return available() or (_IMPORT_FAILURE is None and _STATE.deferred is not None)


def unavailable_reason() -> str | None:
    """
    Why text cannot be typeset, for the menu to say, or ``None`` when it can.

    While a load is deferred the answer says what switching it on will cost, which
    is not a failure and is still something the operator should read first.
    """
    if _IMPORT_FAILURE is not None:
        return f"the text library did not import ({_IMPORT_FAILURE})"
    if available():
        return None
    if _STATE.deferred is not None:
        return "faces load when this is switched on: about a second"
    return _STATE.why


def mono() -> bool:
    """
    Whether prose is being set in the monospace face.

    True when ``Role.PROSE`` is monospace, and true when a proportional face failed
    to load (``style``).
    """
    return style(Role.PROSE).slot is Slot.MONOSPACE


def hello_loader() -> Callable[[Path], Any]:
    """
    A font loader over ``hello_imgui``, for the runner's ``load_additional_fonts``.

    ``hello_imgui.load_font`` applies the runner's DPI handling; a plain atlas load
    beside it would be a second opinion about scale.
    """
    from imgui_bundle import hello_imgui

    params = hello_imgui.FontLoadingParams()
    # The files are in the imgui-bundle wheel and in advtext's scratch directory,
    # not in an assets folder of ours.
    params.inside_assets = False
    if METRICS in _LOADER_FLAGS:
        params.font_config.font_loader_flags = _LOADER_FLAGS[METRICS]
    return lambda path: hello_imgui.load_font(str(path), BODY_PX, params)


def load_fonts(bold: Path | None = None, *, loader: Callable[[Path], Any] | None = None) -> bool:
    """
    Put the library's faces on the atlas. Returns whether text can be typeset.

    Call it once the atlas exists and **after the application's own first font**: the
    first font added is ImGui's default, and a face loaded ahead of it would reskin
    every widget. ``bold`` is the vendored bold monospace; it is registered before any
    face is measured, because a registration clears every metric cached so far.

    Never raises. A missing file or a missing dependency costs the typeset path and
    nothing else: ``enabled()`` stays false and every pane keeps its old renderer.

    ``loader`` defaults to the current context's atlas, which is what a headless test
    wants; the app passes ``hello_loader()``.

    Every file a proportional family names is loaded here too, whichever is in
    force, so that choosing one later is not a font read in the middle of a session.
    """
    _STATE.fonts = None
    _STATE.files.clear()
    _STATE.themes.clear()
    _STATE.palettes.clear()
    _PIXEL_METRICS.clear()
    _STATE.epoch += 1
    _STATE.deferred = None
    if _IMPORT_FAILURE is not None:
        _STATE.why = unavailable_reason()
        return False
    _STATE.why = "the monospace face did not load"
    try:
        # Before anything is parsed: a reader that hides text is not one to draw with.
        _reader()
        if bold is not None and bold.is_file():
            resolved = bold.resolve()
            if registered_fonts().get(Face.CODE_BOLD) != resolved:
                register_font(Face.CODE_BOLD, resolved)
        # The library's own prose file first, whatever a family registered in an
        # earlier load: it is the one every family can fall back to.
        unregister_font(Face.PROSE)
        # After the registrations, which invalidate a measure made before them.
        _STATE.measure = char_measure(pixel_metrics)
        remembered = _remembering(_atlas_loader if loader is None else loader)
        fonts = load_faces(ALL_FACES, loader=remembered)
        for each in PROPORTIONALS:
            extra = _prose_path(each)
            if extra is not None and extra.is_file():
                try:
                    remembered(extra)
                except Exception:
                    _STATE.files[extra] = None
        _STATE.fonts = fonts
        _STATE.home = _imgui().get_current_context()
        _apply_faces()
        if available():
            # Opens each face once, here, instead of on the frame that first shows text.
            _theme("prose")
            _theme("literal")
            _STATE.why = None
    except Exception as failure:
        _STATE.fonts = None
        _STATE.why = f"the faces did not load ({type(failure).__name__}: {failure})"
    _remember_scratch()
    return available()


def load_fonts_later(
    bold: Path | None = None, *, loader: Callable[[Path], Any] | None = None
) -> None:
    """
    Remember how to load the faces, and load them only once typesetting is switched on.

    For a launch with ``Settings.typeset`` off: the load is over a second and writes
    a patched font to the temporary directory, and none of it is wanted until the
    operator turns the setting on. ``pre_frame`` then does it, between frames, which
    is where the atlas may be added to; that frame is the second the load costs, and
    ``unavailable_reason()`` says so beforehand. ImGui bakes a font added after
    start-up on demand -- run once under a real renderer
    (``planning/2026-10-07-which-fonts-ship.md``), and not otherwise proven.
    """
    _STATE.deferred = (bold, loader)


def _remember_scratch() -> None:
    """
    Note the library's scratch directory, so this process removes it when it ends.

    The library writes a patched copy of its math font to a new ``advtext-fonts-*``
    directory under the temporary directory and leaves it there. Which directory is
    read off the paths the loader was handed -- no reach into the library -- and it
    is removed at interpreter exit and no sooner: ImGui may read the file again to
    bake a size it has not drawn yet.
    """
    root = Path(tempfile.gettempdir()).resolve()
    for path in _STATE.files:
        home = path.parent
        if home.name.startswith("advtext-fonts-") and home.parent == root:
            if not _STATE.scratch:
                atexit.register(_remove_scratch)
            _STATE.scratch.add(home)


def _remove_scratch() -> None:
    for home in _STATE.scratch:
        shutil.rmtree(home, ignore_errors=True)
    _STATE.scratch.clear()


def pre_frame() -> None:
    """
    The seam's between-frames work. Call before each ``new_frame``.

    Three things, all cheap when there is nothing to do, which is nearly always.

    **A face a draw asked for and did not have is loaded.** One empty tuple while
    nothing is pending: ``load_fonts`` loads every role up front.

    **A load put off until typesetting was switched on is done** (``load_fonts_later``).

    **The size of running text follows the UI font.** The library draws at the size
    its theme names and not at ImGui's current font size, and ``hello_imgui`` scales
    the UI font for the display, so the theme is sized from what ImGui reports. A
    change moves the epoch: a document holds kerns and advances for the size it was
    built at.
    """
    fonts = _STATE.fonts
    if fonts is None and _STATE.typeset and _STATE.deferred is not None:
        bold, loader = _STATE.deferred
        load_fonts(bold, loader=loader)
        fonts = _STATE.fonts
    if fonts is None:
        return
    fonts.load_pending()
    size = _live_font_size()
    if size is not None and abs(size - _STATE.live_px) > 0.01:
        _STATE.live_px = size
        _STATE.epoch += 1
        _STATE.themes.clear()


def _live_font_size() -> float | None:
    """
    The UI font's size as ImGui has it between frames, or ``None`` before the first.

    Between frames the font stack is empty, so this is the default font's size with
    the display scale applied and never a size some pane had pushed.
    """
    im = _imgui()
    if im.get_current_context() is None or im.get_frame_count() < 1:
        return None
    size = float(im.get_font_size())
    return size if size >= 1.0 else None


def body_px() -> float:
    """
    The pixel height running text is set at: the UI font's, times ``scale()``.
    """
    return _STATE.body_px


def reset() -> None:
    """
    Forget the fonts and everything built from them.

    An ``ImFont`` belongs to the atlas it was loaded into. A test that destroys its
    ImGui context, or a second run of the app in one process, must not draw with the
    last one's.
    """
    _STATE.fonts = None
    _STATE.home = None
    _STATE.files.clear()
    _STATE.themes.clear()
    _STATE.palettes.clear()
    _STATE.palette_of = None
    _STATE.deferred = None
    _STATE.why = "the faces have not been loaded"
    _PIXEL_METRICS.clear()
    _STATE.epoch += 1


def _fonts() -> LoadedFaces:
    fonts = _STATE.fonts
    if fonts is None:
        raise RuntimeError("typeset: no faces are loaded; check enabled() before drawing")
    return fonts


def _bold_monospace() -> bool:
    """
    Whether a real bold monospace is there to keep the columns under bold.
    """
    fonts = _STATE.fonts
    loaded = fonts is None or fonts.fonts.get(Face.CODE_BOLD) is not None
    return Face.CODE_BOLD in registered_fonts() and loaded


# ---------------------------------------------------------------------------
# Metrics
# ---------------------------------------------------------------------------


class _LineMetrics:
    """
    A face's metrics as the library reads them from the font file, with one thing
    changed: no glyph is measured taller than a line (``bbox``).

    What ``METRICS = "file"`` lays out with: every advance and kern is the
    library's own.
    """

    __slots__ = ("_bottom", "_boxes", "_inner", "_top", "has")

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self._boxes: dict[int, Any] = {}
        # Asked once a character of every block built; through `__getattr__` that
        # is two calls and a bound method each time.
        self.has = inner.has
        # The tightest line this face is ever set on: the strut of the smallest
        # `LEADING`, at this face's own pixel height.
        tall = _px(inner.ascent + inner.descent)
        top, bottom = _strut(float(sp_from_px(_half_up(tall * min(LEADING.values())))))
        if inner.face in (Face.CODE, Face.CODE_BOLD):
            top, bottom = min(top, inner.ascent), min(bottom, inner.descent)
        self._top, self._bottom = top, -bottom

    def __getattr__(self, name: str) -> Any:
        return getattr(self._inner, name)

    def bbox(self, codepoint: int) -> Any:
        """
        The glyph's ink, no taller than the line it is set on.

        A line is as tall as its tallest glyph or as its block's strut, whichever
        is more, and a line taller than the strut is one its block's pitch does
        not hold for: the library sets it lower, so the rhythm breaks, and its
        selection band is taller than its neighbours'. Two things reach past a
        tight strut. Inconsolata's
        box-drawing glyphs reach past its ascent and descent on purpose, to meet
        the line above and below -- measured as drawn they made every line of a
        ``tree`` listing 23 px against the 19 around it, which is exactly what
        stops them meeting -- and at a pitch near the face's own height so do an
        accented capital and a bracket. So a glyph is measured no taller than the
        strut of the tightest ``LEADING``, and in a monospace face no taller than
        the face says it is either. It is still drawn whole.

        Kept by character. The library asks once for every character of every
        line it measures, and its own answer is a new object each time.
        """
        box = self._boxes.get(codepoint)
        if box is None:
            box = self._inner.bbox(codepoint)
            top, bottom = min(box.y_max, self._top), max(box.y_min, self._bottom)
            if top != box.y_max or bottom != box.y_min:
                box = type(box)(box.x_min, bottom, box.x_max, top)
            self._boxes[codepoint] = box
        return box


class _PixelMetrics(_LineMetrics):
    """
    A face's metrics with each advance as ImGui will draw it.

    Everything not overridden is the real face's. The advance is asked of ImGui's
    own baked font, because it is not derivable from the file: the bundle rasterises
    through a hinter, and a hinted advance is a whole pixel that linear scaling does
    not predict (Roboto's ``a`` at 16 px is 7.43 by the file and 8 on screen; at 18 px
    it is 8.36 and still 8).
    """

    __slots__ = ("_advances", "_font", "_tall")

    def __init__(self, inner: Any) -> None:
        super().__init__(inner)
        self._advances: dict[int, float] = {}
        fonts = _STATE.fonts
        self._font = None if fonts is None else fonts.fonts.get(inner.face)
        # The pixel height this em comes to, which is what ImGui is asked to bake at.
        self._tall = _px(inner.ascent + inner.descent)

    def advance(self, codepoint: int) -> float:
        hit = self._advances.get(codepoint)
        if hit is None:
            hit = self._advances[codepoint] = self._measure(codepoint)
        return hit

    def _measure(self, codepoint: int) -> float:
        font = self._font
        if font is not None and self._tall >= 1.0 and font.is_glyph_in_font(codepoint):
            # Asked for afresh each time rather than kept: ImGui discards a baked
            # size nothing has drawn with for a while.
            baked = font.get_font_baked(self._tall)
            return float(sp_from_px(baked.get_char_advance(codepoint)))
        # No ImGui font to ask (the face failed to load) or a glyph it lacks: the
        # file's advance, on the pixel grid.
        return float(sp_from_px(_half_up(_px(self._inner.advance(codepoint)))))

    def kern(self, codepoint: int, following: int) -> float:
        raw = self._inner.kern(codepoint, following)
        return float(sp_from_px(_half_up(_px(raw)))) if raw else 0.0

    @property
    def space_width(self) -> float:
        return self.advance(0x20)


def _px(length: float) -> float:
    """
    Scaled points to pixels, for a length that is not a whole number of them.
    """
    return length / _sp_per_px()


def _half_up(value: float) -> float:
    """
    ImGui's ``IM_ROUND``, which is not Python's ``round`` at a half.
    """
    return float(math.floor(value + 0.5))


@cache
def _sp_per_px() -> float:
    return float(sp_from_px(4096.0)) / 4096.0


@cache
def _template() -> Any:
    """
    A paragraph style of the library's, to make a font key or a strut from.

    ``Theme.build`` opens no font. What is read off its style is the library's own
    arithmetic -- how a key is spelled, how a strut divides a leading -- which is
    not offered by name.
    """
    return Theme.build().style(BlockKind.PARAGRAPH)


def _strut(leading: float) -> tuple[float, float]:
    """
    The height and depth the library's strut gives a line of ``leading``.
    """
    style = _template()
    skip = style.interline
    lead = replace(skip.baselineskip, width=round(leading))
    strut = replace(style, interline=replace(skip, baselineskip=lead)).strut
    return float(strut.height), float(strut.depth)


_PIXEL_METRICS: dict[str, Any] = {}


def pixel_metrics(key: str) -> Any:
    """
    The resolver every document here is measured with.

    **Under ``METRICS = "hinted"`` or ``"light"``: real faces, ImGui's advances.** The library
    measures a glyph from the font file and draws each glyph of a word at gaps
    rounded to the pixel. ImGui's advance for a hinted glyph is a different number
    -- a whole pixel, chosen by the hinter -- so a word came out wider or narrower
    than the layout believed and the difference landed in the space after it. Seen
    here before this existed: in monospace every word 5% too wide (Inconsolata at
    16 px advances 8 on screen and 7.63 in the file), a ten-letter word eating half
    the space that followed; in Roboto, gaps between words from one pixel to seven.
    Measuring with the advances ImGui will use makes the layout a statement about
    what is drawn (pinned against ``calc_text_size`` in ``tests/test_typeset.py``).
    What is given up is sub-pixel spacing the screen was not showing.

    **Under ``METRICS = "file"``: the library's own metrics**, which are then also
    ImGui's advances, because the faces are loaded unhinted -- and still not the
    gaps the library's draw sets glyphs at (``METRICS``).

    Either way no glyph is measured taller than its line (``_LineMetrics.bbox``).

    Needs the faces loaded and a current ImGui context, which is every caller here:
    nothing is built until ``enabled()``. The entries hold ``ImFont`` pointers, so the
    table is dropped whenever the fonts are.
    """
    hit = _PIXEL_METRICS.get(key)
    if hit is None:
        made = _LineMetrics if METRICS == "file" else _PixelMetrics
        hit = _PIXEL_METRICS[key] = made(fontlib_metrics(key))
    return hit


# ---------------------------------------------------------------------------
# Themes
# ---------------------------------------------------------------------------

_HEADING_FACES = {5: "PROSE_BOLD_ITALIC", 6: "PROSE_ITALIC"}
"""
The proportional face of a heading level that is not plain bold, by name. The
library's own choice: below the fourth level the size is the body's, and the
levels are told apart by face.
"""


def _theme(name: str) -> Theme:
    """
    A theme by name, built on first use and kept until the epoch moves.

    - ``prose``: a markdown document, every role in its own style.
    - ``diff``: what a ``DiffView`` is given: ``Role.DIFF``.
    - ``literal`` / ``part``: ``Role.LITERAL`` text, flush or with a little air
      under it for a part of a ``document``.
    - ``<role>-literal`` / ``<role>-part``: the same for another role --
      ``argument-part``, and ``prose-literal`` for plain text in the prose face.
    """
    hit = _STATE.themes.get(name)
    if hit is not None:
        return hit
    if name == "prose":
        built = _document_theme()
    elif name == "diff":
        built = _literal_theme(Role.DIFF, 0.0)
    else:
        role, _, form = name.rpartition("-")
        if form not in ("literal", "part"):
            raise KeyError(name)
        built = _literal_theme(Role(role or "literal"), _PART_GAP_EM if form == "part" else 0.0)
    _STATE.themes[name] = built
    return built


def _literal_name(role: Role, *, gap: bool) -> str:
    """
    The name of a role's literal theme: with ``gap``, the one with air under it.
    """
    form = "part" if gap else "literal"
    return form if role is Role.LITERAL else f"{role.value}-{form}"


def _faces(slot: Slot) -> Faces:
    """
    The face table of a theme whose running text is in ``slot``.

    Without a bold monospace the weight is given up and the columns kept.
    """
    bold = Face.CODE_BOLD if _bold_monospace() else Face.CODE
    return Faces.mono(bold=bold) if slot is Slot.MONOSPACE else Faces(code_bold=bold)


def _regular(slot: Slot) -> Face:
    return Face.CODE if slot is Slot.MONOSPACE else Face.PROSE


def _em(face: Face, tall: float) -> int:
    """
    The em, in scaled points, at which ``face`` is ``tall`` pixels high as ImGui
    means a font size: ``Theme.at_pixel_height``'s arithmetic, for any face.
    """
    probe = fontlib_metrics(replace(_template().font, face=face, size=sp_from_px(1024.0)).key)
    extent = probe.ascent + probe.descent
    em = tall * probe.em / extent if extent > 0 and probe.em > 0 else tall
    return int(sp_from_px(em))


def _tall(font: Any) -> float:
    """
    The pixel height of a font at its size: ``_em`` the other way.
    """
    probe = fontlib_metrics(replace(font, size=sp_from_px(1024.0)).key)
    if probe.em <= 0:
        return _px(font.size)
    return _px(font.size) * (probe.ascent + probe.descent) / probe.em


def _fixed(skip: Any, tall: float, *, grid: bool = False) -> Any:
    """
    An interline skip whose baselines are ``tall`` pixels apart.

    For every line no taller than its strut, which is every line whose glyphs are
    its own role's size (``_LineMetrics.bbox``). The library still moves a line
    down when something larger is set in it -- a code span given a bigger size than
    its paragraph -- and that is right: the alternative is ink over the line above.

    A whole pixel is not a whole number of scaled points, and which side of it the
    skip falls matters to who reads it. ImGui truncates a cursor position, so a
    view a hair under a whole number of pixels tall loses one: a skip is a hair
    over. A diff view (``grid``) takes its row height *up* to the next pixel, so
    for it a hair over 18 is 19: its skip is a hair under.
    """
    exact = tall * _sp_per_px()
    width = math.floor(exact) if grid else math.ceil(exact)
    return replace(skip, baselineskip=replace(skip.baselineskip, width=width))


@cache
def _theme_class() -> type[Any]:
    """
    The library's ``Theme`` with one answer of its own: the size of a code span.

    A theme sizes a span by its block, and at one em Inconsolata is a pixel or two
    shorter than Roboto beside it. ``font_for`` is where the library's markdown
    reader asks what a run is set in, a settled document's and a stream's alike,
    so a code span is given its own size there and no built node is rewritten.

    Made on first use, so that this module imports with the library absent.
    """

    @dataclass(frozen=True, slots=True)
    class RoleTheme(Theme):  # type: ignore[misc]
        # Pixels a code span is taller than the text of the block it is in.
        span_step: float = 0.0
        # A span's size, by the font of its block.
        spans: dict[Any, int] = field(default_factory=dict, compare=False)

        def font_for(self, kind: BlockKind, emphasis: Any) -> Any:
            spec = Theme.font_for(self, kind, emphasis)
            base = self.style(kind).font
            # On a monospace base a span is the block's own face and size: there
            # is no second family for it to differ from.
            if not emphasis.code or base.face in self.faces.monospace:
                return spec
            size = self.spans.get(base)
            if size is None:
                size = self.spans[base] = _em(spec.face, _tall(base) + self.span_step)
            return replace(spec, size=size)

    return RoleTheme


def _document_theme() -> Theme:
    """
    The theme of a markdown document: each block kind in its role's face, size and
    leading, with the heading scale and block spacing of a pane rather than a page.

    The library builds a theme from one body size and one face table. Its faces
    stay as the library names them and each block kind is given the face of its
    role's slot, which is what lets a monospace heading stand over proportional
    prose: the library reads a block's family off the block's own face.
    """
    prose = style(Role.PROSE)
    base = Theme.at_pixel_height(
        px(Role.PROSE), faces=_faces(prose.slot), align=Align.LEFT, spacing=Spacing.FRENCH
    )
    blocks = dict(base.blocks)
    em = blocks[BlockKind.PARAGRAPH].font.size
    running = _fixed(blocks[BlockKind.PARAGRAPH].interline, pitch(Role.PROSE))
    for kind in (
        BlockKind.PARAGRAPH,
        BlockKind.BULLET_ITEM,
        BlockKind.ORDERED_ITEM,
        BlockKind.QUOTE,
        BlockKind.RULE,
    ):
        blocks[kind] = replace(blocks[kind], interline=running)
    blocks[BlockKind.PARAGRAPH] = replace(
        blocks[BlockKind.PARAGRAPH], space_below=round(em * _PARAGRAPH_GAP_EM)
    )

    heading = style(Role.HEADING)
    for level, scale, above, below in _HEADINGS:
        kind = _heading(level)
        tall = max(1.0, _half_up(px(Role.HEADING) * scale))
        if heading.slot is Slot.PROPORTIONAL:
            face = Face[_HEADING_FACES.get(level, "PROSE_BOLD")]
        else:
            face = Face.CODE_BOLD if level < 6 and _bold_monospace() else Face.CODE
        size = _em(face, tall)
        blocks[kind] = replace(
            blocks[kind],
            font=replace(blocks[kind].font, face=face, size=size),
            interline=_fixed(blocks[kind].interline, pitch(Role.HEADING, tall)),
            space_above=round(size * above),
            space_below=round(size * below),
        )

    # Fenced code at its own role's size: sized in ems of proportional prose it
    # would come out smaller than the chrome.
    code = blocks[BlockKind.CODE]
    blocks[BlockKind.CODE] = replace(
        code,
        font=replace(code.font, face=Face.CODE, size=_em(Face.CODE, px(Role.CODE_BLOCK))),
        interline=_fixed(code.interline, pitch(Role.CODE_BLOCK)),
        space_above=round(em * _CODE_GAP_EM),
        space_below=round(em * _CODE_GAP_EM),
    )

    cell = _regular(style(Role.TABLE).slot)
    table = blocks[BlockKind.TABLE]
    blocks[BlockKind.TABLE] = replace(
        table,
        font=replace(table.font, face=cell, size=_em(cell, px(Role.TABLE))),
        interline=_fixed(table.interline, pitch(Role.TABLE)),
        space_above=round(em * _CODE_GAP_EM),
        space_below=round(em * _PARAGRAPH_GAP_EM),
    )

    given = {each.name: getattr(base, each.name) for each in fields(base)}
    given.update(
        blocks=blocks,
        faces=_faces(Slot.PROPORTIONAL),
        # No rule of the library's under a table's header: it is stacked as a line
        # of its own and puts a blank line under the header. The seam rules every
        # row itself (`_table_rules`).
        table_rule_thickness=0,
        span_step=px(Role.INLINE_CODE) - px(Role.PROSE),
    )
    made: Theme = _theme_class()(**given)
    return made


def _literal_theme(role: Role, gap_em: float) -> Theme:
    """
    The theme of text that is not markdown, in ``role``'s face, size and leading:
    ``gap_em`` of air below a paragraph or a code block, no margins, no code indent.

    No air at all for text set on its own, where the pane's layout supplies the
    spacing. Wrapped text is a paragraph of it and exact text a code block.
    """
    tall = px(role)
    base = Theme.at_pixel_height(
        tall, faces=_faces(style(role).slot), align=Align.LEFT, spacing=Spacing.FRENCH
    )
    blocks = dict(base.blocks)
    paragraph = blocks[BlockKind.PARAGRAPH]
    gap = round(paragraph.font.size * gap_em)
    blocks[BlockKind.PARAGRAPH] = replace(
        paragraph, interline=_fixed(paragraph.interline, pitch(role)), space_below=gap
    )
    code = blocks[BlockKind.CODE]
    blocks[BlockKind.CODE] = replace(
        code,
        font=replace(code.font, face=Face.CODE, size=_em(Face.CODE, tall)),
        interline=_fixed(code.interline, pitch(role), grid=role is Role.DIFF),
        space_above=0,
        space_below=gap,
        left_indent=0,
    )
    return replace(base, blocks=blocks, table_rule_thickness=0)


def line_height(*, mono: bool = True, role: Role | None = None) -> float:
    """
    The pitch of one line of a role's text, in pixels: 17 at a 16 px body.

    What a pane multiplies by to turn "at most 24 lines" into a ``height_cap`` for
    ``plain()``, a literal part or a fenced block. With no ``role`` it is literal
    text's, or prose's with ``mono=False``. **Not a diff's row**: a diff view sets
    its rows on its own grid and its pitch is ``diff_row_height()``. A cap need not
    be exact either way -- a capped view ends on a whole line or row at or under
    the cap it was given.
    """
    if role is None:
        role = Role.LITERAL if mono else Role.PROSE
    return pitch(role)


def diff_row_height() -> float:
    """
    The height of one row of a ``diff()``, in pixels: 18 at a 16 px body.

    Every row of a diff view is this tall -- a file header, a hunk header, a line,
    a collapsed gap -- so "at most 24 rows" is 24 of these.
    """
    hit = _STATE.diff_row
    if hit is None or hit[0] != _STATE.epoch:
        probe = DiffView(Diff(()), _theme("diff"), metrics=pixel_metrics)
        hit = _STATE.diff_row = (_STATE.epoch, float(probe.metrics_px.row))
    return hit[1]


# ---------------------------------------------------------------------------
# Palette
# ---------------------------------------------------------------------------


def _alpha(colour: Color, alpha: float) -> int:
    """
    The colour's packed form with its alpha replaced.
    """
    return (colour.u32 & 0x00FFFFFF) | (max(0, min(255, round(alpha * 255))) << 24)


_FILL_ALPHA = 0.10
_EMPHASIS_ALPHA = 0.20
_CAPTION_BAND_ALPHA = 0.16
_HEADER_FILL_ALPHA = 0.10
_SELECTION_ALPHA = 0.35


def build_palette(p: ui_theme.Palette) -> Palette:
    """
    The library's palette for one pptmstr palette, role for role.

    The role meanings are the ones the existing renderers use: a heading is
    ``accent``, a quote ``text_dim``, code ``diff_context`` (``rich_pane``), a rule
    ``border``; in a diff, ``diff_add`` / ``diff_remove`` / ``diff_context`` and
    ``accent`` for the file and hunk headers (``detail._DIFF_ROLE``). The bands behind
    added and removed lines are the same two colours, translucent, so they tint the
    pane's own ground whichever theme is live. The ``+`` and ``-`` in the gutter are
    drawn by the view in one ink for both and are what tells the two apart without
    hue, so that ink is the theme's ``text`` and not a dim one; the line numbers share
    it and are louder than they would otherwise need to be.
    """
    heading = p.accent.u32
    roles = {_heading(level): heading for level, _, _, _ in _HEADINGS}
    roles[BlockKind.QUOTE] = p.text_dim.u32
    roles[BlockKind.CODE] = p.diff_context.u32
    base = Palette(
        page=p.panel.u32,
        ink=p.text.u32,
        rule=p.border.u32,
        highlight=_alpha(p.focus, _SELECTION_ALPHA),
        link=p.accent.u32,
        diff=DiffPalette(
            add=p.diff_add.u32,
            remove=p.diff_remove.u32,
            context=p.diff_context.u32,
            meta=p.text.u32,
            add_fill=_alpha(p.diff_add, _FILL_ALPHA),
            remove_fill=_alpha(p.diff_remove, _FILL_ALPHA),
            add_emphasis=_alpha(p.diff_add, _EMPHASIS_ALPHA),
            remove_emphasis=_alpha(p.diff_remove, _EMPHASIS_ALPHA),
            gutter=p.text.u32,
            header=p.accent.u32,
            header_fill=_alpha(p.accent, _HEADER_FILL_ALPHA),
            warning=p.warn.u32,
            current=p.accent.u32,
        ),
    )
    return base.with_roles(roles)


def palette(ink: Color | None = None) -> Palette:
    """
    The palette for the theme that is live right now.

    With ``ink``, every block is drawn in that one colour: a segment kind's role, or
    dim text for something de-emphasised. Links, rules and the diff colours stay.
    """
    current = ui_theme.current()
    if _STATE.palette_of is not current:
        _STATE.palette_of = current
        _STATE.palettes.clear()
    key = None if ink is None else ink.u32
    hit = _STATE.palettes.get(key)
    if hit is None:
        hit = _STATE.palettes.get(None) or build_palette(current)
        _STATE.palettes[None] = hit
        if ink is not None:
            hit = replace(hit, ink=ink.u32, roles=())
            _STATE.palettes[key] = hit
    return hit


# ---------------------------------------------------------------------------
# The reader
# ---------------------------------------------------------------------------

_HIDING_RULES = ("html_block", "html_inline", "image")
"""
The rules of the library's markdown reader that consume text and show nothing for it.
"""

_DESTINATIONS_RULE = "pptmstr_link_destinations"
_STARS_RULE = "pptmstr_written_stars"

_READER_PROBE = "a <b>x</b> ![alt](u) rm **/*.py and t/**/*.md\n"
_READER_PROBE_WORDS = ("<b>x</b>", "alt", "(u)", "**/*.py", "t/**/*.md")
"""
A line every word of which a CommonMark reader hides some of, and what must come
back from it: the tags, the image's description and destination, the stars.
"""


@cache
def _reader() -> None:
    """
    Make the library's markdown reader show what was written. Once a process.

    Three changes to the one parser object every call here reads with (reference
    definitions off, mathematics off), all made on the parser and not on the text:

    - The rules in ``_HIDING_RULES`` are switched off. Raw HTML is then text, as
      CommonMark's own ``html: false`` reading has it -- a tag, a comment opener, a
      generic's ``<T>`` -- and ``![alt](url)`` is an exclamation mark and a link.
    - A core rule (``_show_destinations``) writes each link's destination after its
      text.
    - An inline rule (``_written_stars``) reads the stars of a glob, a comment
      opener or a product as the characters they are.

    On the parser because the alternative, escaping the source before it is parsed,
    is a second markdown reader that has to agree with the first about every code
    span, fence and container, and is wrong wherever it does not. This one cannot
    disagree with itself, holds nothing back from a stream, and leaves an autolink
    an autolink.

    Reached past the facade (``advtext.markdown._parser``), and it changes an object
    the library shares process-wide; nothing else in this process reads markdown
    with it. **Then it is checked** (``_reader_shows``): the library could keep the
    name and hand back a parser its views do not read with, and the changes would
    land on nothing. If the name is gone, or the probe comes back short, this
    raises, ``load_fonts`` reports the faces as not loaded, and every pane keeps
    the renderer it had: refusing to typeset is the honest answer when the reader
    cannot be told to hide nothing.
    """
    from advtext import markdown as library

    parser = library._parser(False, False)
    parser.disable(list(_HIDING_RULES))
    if _DESTINATIONS_RULE not in parser.core.ruler.get_all_rules():
        parser.core.ruler.push(_DESTINATIONS_RULE, _show_destinations)
    if _STARS_RULE not in parser.inline.ruler.get_all_rules():
        parser.inline.ruler.before("emphasis", _STARS_RULE, _written_stars)
    missing = _reader_hides()
    if missing:
        raise RuntimeError(f"the markdown reader hides text: {', '.join(missing)}")


def _reader_hides() -> tuple[str, ...]:
    """
    Which of the probe's words the reader a view is built with does not give back.

    Read the way ``_build_piece`` reads a markdown part and a ``Stream`` reads its
    text -- the library's public calls, nothing of the parser -- so that what is
    checked is the reader in use and not the object ``_reader`` was handed.
    """
    theme = Theme.at_pixel_height(BODY_PX)
    settled = build_document(_READER_PROBE, theme, fontlib_metrics, references=False)
    arriving = StreamDocument(theme, fontlib_metrics)
    arriving.append(_READER_PROBE)
    missing: list[str] = []
    for blocks in (settled.blocks, arriving.document.blocks):
        said = " ".join(
            material_text(block).text for block in blocks if isinstance(block, TextBlock)
        )
        missing += [word for word in _READER_PROBE_WORDS if word not in said]
    return tuple(dict.fromkeys(missing))


def _written_stars(state: Any, silent: bool) -> bool:
    """
    Read a run of ``*`` as text where it was written as a character and not as
    emphasis: ``**/*.py``, ``src/*``, ``*.md``, ``/* note */``, ``2*3*4``.

    CommonMark pairs such stars up wherever its flanking rules allow --
    ``delete **/*.py and tests/**/*.md`` is ``delete``, a bold ``/*.py and tests/``
    and ``/*.md`` -- and a delimiter is not drawn, so the reader of a body sees a
    different command from the one its recipient gets. A run is text when:

    - a ``/`` is on either side of it (a path, a comment opener or closer);
    - a ``.`` and more follows it (``*.py``, ``**.tar.gz``);
    - a letter or a digit is on both sides (``a*b``, ``foo*bar``), which is how
      CommonMark itself reads ``_``.

    ``**bold**``, ``*emphasis*`` and ``**Ends in a stop.**`` are none of those. What
    still loses its stars is whatever is written exactly as emphasis is: ``rm *tmp
    and old*`` is ``rm`` and an italic ``tmp and old``. In the other direction
    ``**/usr/bin**`` keeps its stars and is not bold. A star in a code span or a
    fence never gets here.
    """
    source: str = state.src
    start: int = state.pos
    if source[start] != "*":
        return False
    stop = start
    while stop < state.posMax and source[stop] == "*":
        stop += 1
    before = source[start - 1] if start else " "
    after = source[stop] if stop < len(source) else " "
    more = source[stop + 1] if stop + 1 < len(source) else " "
    written = (
        before == "/"
        or after == "/"
        or (after == "." and not more.isspace())
        or (before.isalnum() and after.isalnum())
    )
    if not written:
        return False
    if not silent:
        state.pending += source[start:stop]
    state.pos = stop
    return True


def _show_destinations(state: Any) -> None:
    """
    Append to each link's text where it goes: ``text (https://...)``.

    A link drawn as its text alone asks its reader to trust a destination they
    cannot see, and in a parked concern body the reader is approving it. The
    destination is written inside the link, so it takes the link's ink and its
    click, and is left out only where the text already is the destination to the
    character (``_names_itself``: most autolinks, a bare URL). A title is hidden
    text too and is written with it.
    """
    for token in state.tokens:
        if token.type != "inline" or not token.children:
            continue
        out: list[Any] = []
        opened: list[tuple[str, str, int]] = []
        for child in token.children:
            if child.type == "link_open":
                attrs = child.attrs or {}
                opened.append((str(attrs.get("href", "")), str(attrs.get("title") or ""), len(out)))
            elif child.type == "link_close" and opened:
                href, title, start = opened.pop()
                said = "".join(
                    str(each.content)
                    for each in out[start + 1 :]
                    if each.type in ("text", "code_inline")
                )
                if title or not _names_itself(said, href):
                    note = type(child)("text", "", 0)
                    note.content = f' ({href} "{title}")' if title else f" ({href})"
                    out.append(note)
            out.append(child)
        token.children = out


def _names_itself(said: str, href: str) -> bool:
    """
    Whether a link's text is its own destination, character for character.

    Only then is the destination left unsaid. The two may differ in a scheme the
    reader supplied (``mailto:``, ``http://``) and in a closing ``/``, and in nothing
    else: the reader's own normalising is not asked, because it is what turns
    ``https://аpple.com`` with a Cyrillic ``а`` into the ``xn--`` host it goes to,
    and a text that only *normalises* to its destination is a lookalike of it.
    Such a link, an autolink included, is set with where it goes after it.
    """
    said = said.strip().removesuffix("/")
    goes = href.removesuffix("/")
    return bool(said) and goes in (said, f"mailto:{said}", f"http://{said}", f"https://{said}")


@cache
def _nodes() -> tuple[Any, Any, Any]:
    """
    The library's character, glue and penalty nodes: ``advtext.nodes``, past the facade.

    Needed to give a paragraph a place to break that its writer did not (``_broken``).
    """
    from advtext import nodes

    return nodes.CharNode, nodes.Glue, nodes.Penalty


def _replaced(blocks: Iterable[Any]) -> int:
    """
    How many characters of these blocks no face could set: ``advtext.markdown``'s count.

    Past the facade. The library sets such a character as U+FFFD *when the block is
    built*, as an ordinary glyph, so no draw statistic can tell it from one that was
    written; only the block knows.
    """
    from advtext.markdown import replaced_characters

    return sum(int(replaced_characters(block)) for block in blocks)


# ---------------------------------------------------------------------------
# What a frame may spend
# ---------------------------------------------------------------------------


def set_budget(milliseconds: float) -> float:
    """
    How long one frame may spend building views. Returns what it was.

    Building is parsing a piece of text and laying it out for the first time. When
    a frame has spent this much, what is left waits for the next frame: a long text
    is shown as far as it has got with a line saying how much is to come, and a
    view whose turn has not come at all claims its room and waits. A frame's free
    allowance (``_FREE_LINES``, ``_FREE_MS``) is built whatever the number, so every
    frame makes progress and a text of ordinary length is never seen half-built.

    ``0`` is that allowance and nothing more, which is what a test of the waiting
    wants; ``math.inf`` builds everything at once, which is what a test of anything
    else wants and what a script taking one screenshot wants.
    """
    before = _STATE.budget_ms
    _STATE.budget_ms = float(milliseconds)
    return before


def pending() -> bool:
    """
    Whether a view on screen was drawn short of its whole text in the frame just built.

    The application keeps frames coming while this is true, so that text being
    typeset over several frames does not wait for the mouse to move.
    """
    im = _imgui()
    if im.get_current_context() is None:
        return False
    return bool(_STATE.unfinished_in == im.get_frame_count())


def _spent(im: Any) -> float:
    frame = im.get_frame_count()
    if _STATE.spent_in != frame:
        _STATE.spent_in = frame
        _STATE.spent_ms = 0.0
        _STATE.built_lines = 0
    return _STATE.spent_ms


_FREE_LINES = 80
_FREE_MS = 30.0
"""
What a frame builds before it looks at its budget: up to this many source lines, for
as long as it has spent less than this many milliseconds.

So that a text of ordinary length -- a message, a concern, a task's detail --
appears whole on the frame it is first drawn, and is never seen half-built: a
pane that swaps a stream for the settled text of the same run would otherwise
show that text go and come back. Eighty lines in one view are 30 to 40 ms. The
limit in time is for many small views at once, which cost 4 to 7 ms each however
few lines they hold: a pane that opens sixteen of them builds five or six a frame.
"""


def _may_build(im: Any, free: bool = True) -> bool:
    """
    Whether this frame may build another piece: it has built nothing yet, or has
    budget left, or -- ``free`` -- is still inside its free allowance.
    """
    spent = _spent(im)
    lines = _STATE.built_lines
    if lines == 0 or spent < _STATE.budget_ms:
        return True
    return free and lines < _FREE_LINES and spent < _FREE_MS


def _spend(im: Any, started: float) -> float:
    """
    Charge the frame for work begun at ``started``. Returns the milliseconds.
    """
    cost = (time.perf_counter() - started) * 1000.0
    # Never zero: a build too quick for the clock still was the frame's first.
    _STATE.spent_ms = _spent(im) + max(cost, 1e-6)
    return cost


# ---------------------------------------------------------------------------
# Pieces
# ---------------------------------------------------------------------------

_MARKDOWN_PIECE_LINES = 40
_LITERAL_PIECE_LINES = 60
_PATIENT_PIECES = 0.5
"""
About how many lines of source are parsed and laid out in one go.

A text longer than twice this is built in pieces of about this many lines: across
frames within the budget, and afterwards only the pieces a change touched. The
numbers put one piece at 10 to 20 ms (measured: markdown about 0.4 ms a line to
parse and lay out, literal text 0.15), which is what the frame's first build can
therefore cost.

A view that declined the frame's free allowance (``free_allowance=False``) is cut
``_PATIENT_PIECES`` as long: a text under twice a piece is one piece, and at the
sizes above that one piece is the 30 to 45 ms frame the view was declining.
"""

_DIFF_ANNOUNCED_LINES = 2000
"""
A diff whose changed region is longer than this is announced a frame before it is built.

``difflib`` on what is left after the common head and tail is the one cost here
that cannot be cut into pieces: measured, changes scattered through 5,000 lines are
270 ms and through 20,000 lines 980 ms. The frame before is a line saying what is
about to happen, so the stall is not a silent one.
"""


class Kind(enum.Enum):
    """
    How one part of a ``document`` is read.
    """

    MARKDOWN = "markdown"
    LITERAL = "literal"
    """Exactly as written, monospace, never broken: ``plain(wrap=False)``."""
    WRAPPED = "wrapped"
    """Monospace, broken to the window at spaces, whitespace collapsed: ``plain(wrap=True)``."""
    WRAPPED_EXACT = "wrapped-exact"
    """
    Exactly as written, monospace, broken to the window at any column:
    ``plain(wrap=True, exact=True)``. Every space, indent and blank line is kept, and
    a copy is the text without the breaks the window made. For a command or a path.
    """


@dataclass(frozen=True, slots=True)
class Part:
    """
    One piece of a ``document``: its text, how to read it, and its ink.

    ``colour`` draws the whole part in one role's ink -- a segment kind's, or dim for
    something de-emphasised. Left out, markdown takes the palette's role inks and
    literal text the theme's ``text``.

    **Name the role** (``"danger"``, ``"text_dim"``: a field of ``theme.Palette``) rather
    than passing ``P.danger`` when the parts are kept from frame to frame. A name is
    looked up in whichever theme is live when the part is inked; a ``Color`` is the
    theme's that was live when the part was made, and stays that after a switch.

    ``demote`` sets each heading of a markdown part that many levels down, so a
    ``#`` in a body does not outrank the line the pane heads it with: at 1 an h1 is
    set as an h2, and past the third level a heading is body-sized bold.

    ``tight`` leaves out the air a literal or wrapped part has under it
    (``_PART_GAP_EM``): for a part that names the one after it -- an argument's
    name over its value -- where the gap belongs under the pair. A markdown part
    has its blocks' own spacing and ignores it.

    ``role`` is whose size and leading a literal or wrapped part is set in:
    ``Role.ARGUMENT`` for a call's argument. A markdown part's blocks each have a
    role of their own and it ignores this.
    """

    text: str
    kind: Kind = Kind.MARKDOWN
    colour: Color | str | None = None
    demote: int = 0
    tight: bool = False
    role: Role = Role.LITERAL


@dataclass(frozen=True, slots=True)
class _Piece:
    """
    A run of source built in one go: a whole part, or about forty lines of a long one.

    Equal when it would build the same blocks, which is what lets a call whose text
    grew find everything above the growth already built.
    """

    kind: Kind
    text: str
    theme: str
    demote: int = 0
    # Which part of the caller's it came from, its ink, where its text starts in
    # the whole source (lines, characters), and how many lines it is.
    part: int = field(default=0, compare=False)
    ink: Color | str | None = field(default=None, compare=False)
    at: tuple[int, int] = field(default=(0, 0), compare=False)
    lines: int = field(default=1, compare=False)


@dataclass(slots=True)
class _Built:
    """
    What one piece came to: its blocks, where each is in the piece's text, and how
    many of its characters no face could set.
    """

    blocks: tuple[Any, ...]
    sources: tuple[Any, ...] = ()
    replaced: int = 0
    # `sources` moved to where the piece sits in the whole text, kept with the
    # offset it was moved by.
    moved: tuple[tuple[int, int], tuple[Any, ...]] | None = None
    # Its last block, and that block with a paragraph's air under it: for a list
    # that ends exactly where the piece does.
    aired: tuple[Any, Any] | None = None


_FENCE_OPEN = re.compile(r"^ {0,3}(`{3,}|~{3,})(.*)$")
_NEW_BLOCK = re.compile(r"^(?![-*+](?:[ \t]|$)|\d{1,9}[.)](?:[ \t]|$)|>)\S")


def _markdown_pieces(source: str, size: int = _MARKDOWN_PIECE_LINES) -> list[tuple[str, int, int]]:
    """
    ``source`` cut where cutting cannot change how it reads: ``(text, line, offset)`` each.

    A cut is made only before a line that (a) follows a blank line, (b) starts in
    the first column, (c) is not a list item or a quote, and (d) is outside every
    fence opened in the first four columns. Such a line starts a new top-level
    block whatever came before it: a blank line ends a paragraph and a table, a
    list item or a quote can only be continued by an indented line or by its own
    marker, and a fence nested deeper than this tracks is inside a container that
    an unindented line has already left. Where the fence tracking is unsure it
    errs towards "still inside", which costs a cut and never makes a wrong one.

    Cut greedily from the top, so text added at the end moves no earlier cut.
    """
    if source.count("\n") < 2 * size:
        return [(source, 0, 0)]
    out: list[tuple[str, int, int]] = []
    first_line = first_char = char = 0
    fence = ""
    blank = False
    for number, line in enumerate(source.split("\n")):
        found = _FENCE_OPEN.match(line)
        if fence:
            closes = found is not None and found[1][0] == fence[0] and len(found[1]) >= len(fence)
            if closes and found is not None and not found[2].strip():
                fence = ""
        else:
            if blank and number - first_line >= size and _NEW_BLOCK.match(line):
                out.append((source[first_char:char], first_line, first_char))
                first_line, first_char = number, char
            if found is not None and (found[1][0] == "~" or "`" not in found[2]):
                fence = found[1]
        blank = not line.strip()
        char += len(line) + 1
    out.append((source[first_char:], first_line, first_char))
    return out


def _line_pieces(text: str, size: int = _LITERAL_PIECE_LINES) -> list[str]:
    """
    Literal text cut into runs of about ``size`` lines, never beside a blank line.

    Both constructors drop blank lines at a block's edges, so a cut next to one
    would lose it. Cut greedily from the top, as ``_markdown_pieces`` is.
    """
    if text.count("\n") < 2 * size:
        return [text]
    lines = text.split("\n")
    out: list[str] = []
    start = 0
    while start < len(lines):
        stop = min(len(lines), start + size)
        while stop < len(lines) and not (lines[stop - 1].strip() and lines[stop].strip()):
            stop += 1
        out.append("\n".join(lines[start:stop]))
        start = stop
    return out


def _pieces_of(
    parts: Sequence[Part], *, flush: bool = False, inked: bool = True, patient: bool = False
) -> tuple[_Piece, ...]:
    """
    The pieces a sequence of parts is built as. A part's last literal piece takes
    its role's theme with the air below it, unless ``flush``: a view that is one
    part has none. ``inked`` false leaves literal text to the palette's ink, for a
    view that is one part in one colour. ``patient`` cuts smaller, for a view built
    without the frame's free allowance.
    """
    out: list[_Piece] = []
    scale = _PATIENT_PIECES if patient else 1.0
    prose_lines = max(1, int(_MARKDOWN_PIECE_LINES * scale))
    literal_lines = max(1, int(_LITERAL_PIECE_LINES * scale))
    for number, part in enumerate(parts):
        if part.kind is Kind.MARKDOWN:
            for text, line, char in _markdown_pieces(part.text, prose_lines):
                out.append(
                    _Piece(
                        part.kind,
                        text,
                        "prose",
                        part.demote,
                        number,
                        part.colour,
                        (line, char),
                        _line_count(text),
                    )
                )
            continue
        ink = "text" if part.colour is None and inked else part.colour
        # Wrapped text is one block whatever its length: cut, it would copy with
        # a paragraph break at each cut.
        cut = [part.text] if part.kind is Kind.WRAPPED else _line_pieces(part.text, literal_lines)
        for index, text in enumerate(cut):
            # Only the last piece of a part takes the gap that separates parts.
            last = index + 1 == len(cut) and not part.tight and not flush
            chosen = _literal_name(part.role, gap=last)
            out.append(_Piece(part.kind, text, chosen, 0, number, ink, (0, 0), _line_count(text)))
    return tuple(out)


def _line_count(text: str) -> int:
    return text.count("\n") + (1 if text and not text.endswith("\n") else 0)


def _build_piece(piece: _Piece) -> _Built:
    """
    Parse one piece. The only place a block is made for a settled view.
    """
    if not piece.text.strip():
        return _Built(())
    if piece.kind is Kind.MARKDOWN:
        _reader()
        made = build_document(piece.text, _demoted(piece.demote), pixel_metrics, references=False)
        return _Built(_dressed(made.blocks), tuple(made.sources), _replaced(made.blocks))
    chosen = _theme(piece.theme)
    if piece.kind is Kind.WRAPPED:
        blocks: tuple[Any, ...] = (text_block(piece.text, chosen, pixel_metrics, hard_breaks=True),)
    else:
        blocks = (code_block(piece.text, chosen, pixel_metrics),)
    return _Built(blocks, (), _replaced(blocks))


def _dressed(
    blocks: Sequence[Any], memo: dict[int, tuple[Any, bool, Any]] | None = None
) -> tuple[Any, ...]:
    """
    Markdown's blocks as the seam sets them: block for block, two kinds changed.

    **A fenced block with an info string has it as its first line** (``_captioned``).
    The library keeps the string on the block and draws none of it, and on an
    approval surface ``python IMPORTANT also delete tests`` is text the recipient
    gets. All of it, the language included -- except the bare ``diff`` or ``patch``
    of a fence the diff view shows every line of (``_fenced``), which is the word
    shown by being obeyed.

    **The last item of a list has a paragraph's air under it.** The gap between two
    blocks is the upper one's space below and the lower one's space above; a list
    item's is a fifth of an em, right between items and wrong before the
    paragraph that follows the list, which then reads as part of it.

    One block in, one block out, so block numbers, the source map and a stream's
    committed count mean what they meant. ``memo`` is for a stream, where the same
    committed block must come back as the same object on every reading: it is
    keyed by the block's identity and holds only the blocks of the last call. The
    same tuple comes back when nothing in it was changed.
    """
    out: list[Any] | None = None
    kept: dict[int, tuple[Any, bool, Any]] = {}
    count = len(blocks)
    for number, block in enumerate(blocks):
        tail = False
        if isinstance(block, CodeBlock):
            if not block.language:
                continue
        elif _is_item(block):
            if number + 1 >= count or not _ends_list(block, blocks[number + 1]):
                continue
            tail = True
        else:
            continue
        hit = None if memo is None else memo.get(id(block))
        if hit is not None and hit[0] is block and hit[1] == tail:
            made = hit[2]
        else:
            made = _aired(block) if tail else _fenced(block)
        kept[id(block)] = (block, tail, made)
        if made is block:
            continue
        if out is None:
            out = list(blocks)
        out[number] = made
    if memo is not None:
        memo.clear()
        memo.update(kept)
    if out is None:
        return blocks if isinstance(blocks, tuple) else tuple(blocks)
    return tuple(out)


def _is_item(block: Any) -> bool:
    return isinstance(block, TextBlock) and (
        block.kind is BlockKind.BULLET_ITEM or block.kind is BlockKind.ORDERED_ITEM
    )


def _ends_list(item: Any, follower: Any) -> bool:
    """
    Whether ``follower`` is outside the list ``item`` is in and brings no air of its own.
    """
    if not isinstance(follower, TextBlock) or follower.style.space_above or _is_item(follower):
        return False
    return bool(follower.left_indent < item.left_indent)


def _aired(item: Any) -> Any:
    style = item.style
    gap = round(style.font.size * _PARAGRAPH_GAP_EM)
    if style.space_below >= gap:
        return item
    return replace(item, style=replace(style, space_below=gap))


def _fenced(block: Any) -> Any:
    """
    A fenced block with an info string as the seam sets it: left alone for the diff
    view when it is a diff that view shows whole, under its info string otherwise.
    """
    if _is_diff_fence(block) and _whole_as_diff(block):
        return block
    return _captioned(block)


def _captioned(block: Any) -> Any:
    """
    A fenced block with its info string as a first line of its own.

    Set as code, in the block's face, so it is selected and copied with the block
    and a stream needs no block of its own for it; inked dim where the view can
    be told to (``_caption_spans``).

    A bare ``diff`` that is set this way has ``_AS_WRITTEN`` added to the language
    the block carries (which nothing draws), so that everything asking later
    whether the block is a diff's -- the hook, the caption's ink, the width
    reported to the window -- gets the answer that was given here.
    """
    info = " ".join(block.language.split())
    said = code_block(info, _theme("prose"), pixel_metrics).lines
    if not said:
        return block
    language = f"{info} {_AS_WRITTEN}" if _is_diff_fence(block) else block.language
    return replace(block, lines=(said[0], *block.lines), language=language)


def _caption(block: Any) -> int:
    """
    How many characters of a block's first line are its info string; 0 for none.
    """
    if not isinstance(block, CodeBlock) or not block.language or not block.lines:
        return 0
    return 0 if _is_diff_fence(block) else len(block.lines[0])


def _demoted(levels: int) -> Theme:
    """
    The prose theme with each heading set as the one ``levels`` below it.
    """
    base = _theme("prose")
    if levels <= 0:
        return base
    name = f"prose+{min(levels, 6)}"
    hit = _STATE.themes.get(name)
    if hit is None:
        blocks = dict(base.blocks)
        for level in range(1, 7):
            lower = min(6, level + levels)
            blocks[_heading(level)] = base.blocks[_heading(lower)]
        hit = _STATE.themes[name] = replace(base, blocks=blocks)
    return hit


# ---------------------------------------------------------------------------
# The cache
# ---------------------------------------------------------------------------


@dataclass
class _Entry:
    """
    One held view and what it was built from.
    """

    kind: str
    token: object
    epoch: int
    view: Any
    # The ImGui name of the view's own scrolling child: from the cache key, so the
    # scroll position outlives the view object.
    name: str = ""
    # The width the view was last laid out at, the height that came to, and what
    # that layout cost.
    width: float | None = None
    height: float | None = None
    cost_ms: float = 0.0
    # A width asked for and not yet taken, with when it was first asked for.
    wanted: tuple[float, float] | None = None
    # The measure the view was last fitted and broken to, and the same text being
    # laid out at a new one beside it. See `_measure_for`.
    measure: float | None = None
    rebreak: _Rebreak | None = None
    frame: int = -1
    stream: Stream | None = None
    # How far right the widest unbroken line reaches, and whether the view is drawn
    # that wide (literal text) or only scrolls that far (a fence in a document).
    extent: float = 0.0
    stretch: bool = False
    # What the last real draw put on the draw list: glyphs, and glyphs no loaded
    # face could raster. `replaced` is of the text now in the view: characters set
    # as U+FFFD when their block was built.
    glyphs: int = 0
    missing: int = 0
    replaced: int = 0
    # A settled view: the pieces it is to show, what each built to, how many of
    # them are in the view, and whether the view's blocks are those yet.
    pieces: tuple[_Piece, ...] = ()
    built: dict[_Piece, _Built] = field(default_factory=dict)
    shown: int = 0
    stale: bool = False
    # Whether the view has shown a whole text since it was first made: what is in
    # it now may be stale, but its height was once a real one.
    whole: bool = False
    # The blocks of `pieces[:shown]` as built, where each came from, how many
    # blocks there are after each piece, and a number that moves when they do.
    pristine: tuple[Any, ...] = ()
    sources: tuple[Any, ...] = ()
    ends: list[int] = field(default_factory=list)
    revision: int = 0
    # What `pristine` was last fitted to: (measure, revision).
    fitted: tuple[float, int] | None = None
    # Per pristine block, by identity: its unbreakable runs, the variant with
    # breaks in it, and the variant cut to a number of columns.
    runs: dict[int, tuple[tuple[int, int, float], ...]] = field(default_factory=dict)
    variants: dict[int, tuple[frozenset[int], Any]] = field(default_factory=dict)
    cut: dict[int, tuple[int, Any]] = field(default_factory=dict)
    # Literal blocks, by identity: the text a copy of each reads from, and which
    # are cut to the measure. `copies` is the first by block number, as fitted.
    exact: dict[int, _Exact] = field(default_factory=dict)
    wrapping: set[int] = field(default_factory=set)
    copies: dict[int, _Exact] = field(default_factory=dict)
    # Paragraphs in the view that are a variant with breaks in it, by block number.
    broken: dict[int, Any] = field(default_factory=dict)
    # The pptmstr palette the colour spans were packed from.
    inked_for: object | None = None
    # A `markdown`: the text it shows, in a cell its fenced diffs read through.
    source: list[str] = field(default_factory=lambda: [""])
    # Layout charged to a frame before it was done: the milliseconds, the source
    # lines they were for, and the kind of text. See `_advance`.
    owed_ms: float = 0.0
    owed_lines: int = 0
    owed_kind: str = ""
    # What was too costly to build unannounced and has had its frame of notice:
    # a piece, as (its position, the frame it was announced in), or a diff's source.
    announced: object | None = None
    # A diff: each file's header as the view words it -- (text, warnings, the
    # width of both in pixels, the width of the text alone) -- the model they were
    # read from, and whether the view was asked to carry the gate's warning.
    headers: tuple[tuple[str, str, float, float], ...] = ()
    headed: object | None = None
    warned: bool = True
    job: _DiffJob | None = None
    # A diff whose model could not be made on its thread: the source, the answer
    # about the warning it was asked with, and what was raised, in words.
    failed: tuple[object, bool, str] | None = None
    # Tables, by identity of the block as built: each column's widest cell, and
    # the table held at those widths.
    natural: dict[int, tuple[int, ...]] = field(default_factory=dict)
    sized: dict[int, Any] = field(default_factory=dict)
    # What is drawn over the view's own draw, for the layout and the inks it was
    # worked out from: see `_overlay`.
    overlay: _Overlay | None = None
    # Whether the whole view is drawn dim, by its palette and not by its parts.
    dim: bool = False
    # Where a capped view is cut so that its last line is whole: the layout and
    # the room it was worked out for, and the answer.
    snapped: tuple[object, float, float] | None = None

    @property
    def unbuilt(self) -> int:
        """
        How many source lines of this view are not typeset yet.
        """
        if self.stream is not None:
            return self.stream.unbuilt
        if self.kind == "diff":
            # No count to give before there is a model: one, for "not all here".
            return 1 if self.token is _NOT_BUILT and self.failed is None else 0
        return sum(piece.lines for piece in self.pieces[self.shown :])


@dataclass(frozen=True, slots=True)
class _Exact:
    """
    A whitespace-exact block's text as its glyphs count it: what a copy reads from.

    ``text`` is the block's lines joined by newlines, tabs already spaces, and
    ``starts`` the number of glyphs before each line. The library numbers a code
    block's glyphs in reading order with nothing in between, so glyph ``n`` is
    character ``n`` of the text with its newlines taken out -- and stays that when
    the lines are cut to fit a window.
    """

    text: str
    starts: tuple[int, ...]

    def between(self, first: int, stop: int) -> str:
        """
        The text from glyph ``first`` up to glyph ``stop``, newlines between included.
        """
        if stop <= first:
            return ""
        return self.text[self._offset(first) : self._offset(stop - 1) + 1]

    def _offset(self, glyph: int) -> int:
        # One newline was passed for every line that starts at or before the glyph,
        # bar the first.
        return glyph + bisect.bisect_right(self.starts, glyph) - 1


class ViewCache:
    """
    The views one pane holds between frames, by a key of the pane's choosing.

    Imperative shell, like ``review.ReviewState``: presentation state the store does
    not own. One per pane. A key is anything hashable that names *what is shown* --
    an approval id, ``(node, "deliverable")`` -- and the pane prunes with the set of
    keys still live, the way ``ReviewState.prune`` does.

    A key also names the view's scrolling child, so a view rebuilt under its key --
    new text, a new face -- keeps its scroll position.
    """

    def __init__(self) -> None:
        self._entries: dict[Hashable, _Entry] = {}

    def __len__(self) -> int:
        return len(self._entries)

    def __contains__(self, key: object) -> bool:
        return key in self._entries

    def keys(self) -> tuple[Hashable, ...]:
        return tuple(self._entries)

    def prune(self, live: Collection[Hashable]) -> None:
        """
        Drop every view whose key is not in ``live``.
        """
        for stale in [key for key in self._entries if key not in live]:
            del self._entries[stale]

    def drop(self, key: Hashable) -> None:
        self._entries.pop(key, None)

    def clear(self) -> None:
        self._entries.clear()

    def view(self, key: Hashable) -> Any:
        """
        The library's own view object for a key, or ``None``.

        For what this module does not wrap: ``under_mouse()`` and ``block_text(n)`` for a
        context menu opened with ``begin_popup_context_item`` straight after the draw;
        ``selected_text()``; ``next_hunk()`` / ``previous_hunk()`` and ``.diff.added`` on a
        diff. The object is replaced when the faces change and when a long text is
        laid out at a new width (``_rebreak``), so ask each frame.
        """
        entry = self._entries.get(key)
        return None if entry is None else entry.view

    def height(self, key: Hashable) -> float | None:
        """
        The height the view claimed when it was last drawn, or ``None``.
        """
        entry = self._entries.get(key)
        return None if entry is None else entry.height

    def drawn(self, key: Hashable) -> tuple[int, int] | None:
        """
        ``(glyphs, missing)`` from the view's last draw that was not culled, or ``None``.

        ``missing`` counts glyphs ImGui had no raster for *at draw time*. **Zero does
        not mean every character drew**: a character no face has is replaced by
        U+FFFD when its block is built and is then an ordinary glyph, counted in
        ``glyphs``. ``replaced`` counts those. "It drew the text" is both at zero.
        """
        entry = self._entries.get(key)
        return None if entry is None else (entry.glyphs, entry.missing)

    def replaced(self, key: Hashable) -> int | None:
        """
        How many characters of the text now in the view were set as U+FFFD, or ``None``.

        Counted when each block is built, from the library's own record of what it
        substituted (``advtext.markdown.replaced_characters``, past the facade). A
        U+FFFD that was in the source is not counted. A diff view is not counted at
        all: it sets its own glyphs and keeps no such record, so for a diff this is 0
        and says nothing.
        """
        entry = self._entries.get(key)
        return None if entry is None else entry.replaced

    def unbuilt(self, key: Hashable) -> int | None:
        """
        How many source lines of the view are waiting to be typeset, or ``None``.

        Zero once the whole text is in the view. See ``set_budget``. For a stream,
        what it has been handed and has not read yet (``Stream.unbuilt``).
        """
        entry = self._entries.get(key)
        return None if entry is None else entry.unbuilt

    def unruled(self, key: Hashable) -> int | None:
        """
        How many tables of the view, as last drawn, have no rules under their rows
        because the rows could not be found in the layout; ``None`` for no view.

        Zero is the expected answer everywhere. Anything else is a table drawn as
        the library draws one, its rows told apart by nothing, and is a defect in
        the seam's reading of the layout to be reported.
        """
        entry = self._entries.get(key)
        if entry is None:
            return None
        return 0 if entry.overlay is None else entry.overlay.unruled

    def layout_ms(self, key: Hashable) -> float | None:
        """
        What the view's last layout cost, in milliseconds: the frame it was built or resized.
        """
        entry = self._entries.get(key)
        return None if entry is None else entry.cost_ms

    def part_at(self, key: Hashable, block: int) -> int | None:
        """
        Which part of a ``document`` a block of its view came from, or ``None``.

        ``block`` is what ``view(key).under_mouse().block`` answers. For a ``markdown``
        or a ``plain`` the answer is 0: there is one part.
        """
        entry = self._entries.get(key)
        if entry is None or block < 0 or not entry.ends or block >= entry.ends[-1]:
            return None
        return entry.pieces[bisect.bisect_right(entry.ends, block)].part

    def selected_text(self, key: Hashable) -> str:
        """
        What is selected in the view under a key, as Ctrl+C would copy it. Empty if nothing.

        The view's own ``selected_text()``, which for a settled view already has the
        breaks the seam made taken out (``_selected``).
        """
        entry = self._entries.get(key)
        if entry is None or entry.view is None:
            return ""
        return str(entry.view.selected_text())

    def _held(self, key: Hashable, kind: str, make: Callable[[], Any]) -> tuple[_Entry, bool]:
        """
        The entry for a key, and whether its view was made just now.

        A view is made when there is none of this kind, and again when the epoch
        has moved: a document holds kerns and advances for the faces and the size
        it was built under. The entry survives the second, with its name and its
        last height, so what is under it on screen does not jump.
        """
        entry = self._entries.get(key)
        if entry is not None and entry.kind == kind and entry.epoch == _STATE.epoch:
            return entry, False
        fresh = _Entry(kind=kind, token=None, epoch=_STATE.epoch, view=make())
        fresh.view.seam = fresh
        fresh.name = f"##typeset-{kind}-{hash(key) & 0xFFFFFFFFFFFF:x}"
        if entry is not None and entry.kind == kind:
            fresh.height, fresh.width, fresh.whole = entry.height, entry.width, entry.whole
        self._entries[key] = fresh
        return fresh, True

    def _fresh(self, key: Hashable, kind: str) -> _Entry | None:
        """
        The entry for a key if it is of this kind and was built under this epoch.
        """
        entry = self._entries.get(key)
        if entry is None or entry.kind != kind or entry.epoch != _STATE.epoch:
            return None
        return entry

    def _put(self, key: Hashable, kind: str, token: object, view: Any) -> _Entry:
        entry = _Entry(kind=kind, token=token, epoch=_STATE.epoch, view=view)
        entry.name = f"##typeset-{kind}-{hash(key) & 0xFFFFFFFFFFFF:x}"
        self._entries[key] = entry
        return entry


# ---------------------------------------------------------------------------
# Drawing
# ---------------------------------------------------------------------------


def _imgui() -> Any:
    from imgui_bundle import imgui

    return imgui


def _frame() -> Any:
    """
    ImGui, for a call that draws. Raises outside a frame.

    Every draw here reads the current window, and ImGui with no frame in progress
    has none: the read is a null dereference that takes the interpreter with it.
    A test that reaches the seam through a pane with a mocked ``imgui`` is the way
    to get there, and an exception names the mistake where a crash names nothing.
    """
    im = _imgui()
    context = im.get_current_context()
    if context is None or not context.within_frame_scope:
        raise RuntimeError("typeset: drawn outside an ImGui frame")
    return im


def _width(im: Any, width: float | None) -> float:
    """
    The measure in whole pixels: a pane being dragged reports fractions, and
    re-breaking every paragraph for 480.0 to 480.4 is a layout no line can show.
    """
    wide = im.get_content_region_avail().x if width is None else width
    return float(max(_MIN_WIDTH_PX, int(wide)))


def _twice(im: Any, key: Hashable) -> float:
    """
    Refuse the second draw of one key in a frame, where the operator can see it.
    """
    before = im.get_cursor_pos().y
    im.text_colored(ui_theme.P.danger.vec4, f"typeset: {key!r} drawn twice in one frame")
    return float(im.get_cursor_pos().y - before)


def _begin(im: Any, key: Hashable, entry: _Entry) -> bool:
    frame = im.get_frame_count()
    if entry.frame == frame:
        return False
    entry.frame = frame
    return True


def _said(im: Any, text: str) -> float:
    """
    One dim native line: what the seam says about a view instead of drawing it. Its height.
    """
    before = im.get_cursor_pos().y
    im.text_colored(ui_theme.P.text_dim.vec4, text)
    return float(im.get_cursor_pos().y - before)


def _waiting(im: Any, entry: _Entry) -> float:
    """
    Say how much of a view on screen is still to be typeset, and keep frames coming.
    """
    _STATE.unfinished_in = im.get_frame_count()
    left = entry.unbuilt
    return _said(im, f"typesetting: {left:,} more line{'s' if left != 1 else ''}")


def _advance(im: Any, entry: _Entry, free: bool = True) -> None:
    """
    Build as many of the entry's unbuilt pieces as the frame's budget allows, in order.

    A piece too long to be cut and too long to build unnoticed is announced for a
    frame first: the line ``_waiting`` draws is on screen before the stall is.
    """
    pieces = entry.pieces
    moved = entry.stale
    while entry.shown < len(pieces):
        piece = pieces[entry.shown]
        if not _may_build(im, free):
            break
        if piece not in entry.built:
            if _costly(piece) and not _announced(entry, im):
                entry.announced = (entry.shown, im.get_frame_count())
                break
            started = time.perf_counter()
            entry.built[piece] = _build_piece(piece)
            _spend(im, started)
        # Its layout is still to come, in this frame's draw, and for literal text
        # that is nearly all a piece costs. Charged now at the going rate, so the
        # loop stops in time, and put right when the draw has been timed.
        owed = piece.lines * _STATE.layout_rate.get(piece.kind.value, _LAYOUT_MS_A_LINE)
        _STATE.spent_ms += owed
        entry.owed_ms += owed
        entry.owed_lines += piece.lines
        entry.owed_kind = piece.kind.value
        _STATE.built_lines += max(piece.lines, 1)
        entry.shown += 1
        moved = True
    if moved:
        _assemble(entry)


_LAYOUT_MS_A_LINE = 0.12
"""
What laying out one source line is taken to cost before any has been timed.
"""

_STREAM_MS_A_LINE = 0.5
"""
The same for a stream, where the line is parsed in that draw as well: measured,
105 ms for 200 lines read at once and 251 ms for 800.
"""


def _settle_layout(entry: _Entry, cost: float) -> None:
    """
    Replace what a frame was charged in advance for an entry's layout with what it cost,
    and remember the rate for the next piece of that kind.
    """
    if not entry.owed_lines:
        return
    _STATE.spent_ms += cost - entry.owed_ms
    rate = cost / entry.owed_lines
    known = _STATE.layout_rate.get(entry.owed_kind)
    _STATE.layout_rate[entry.owed_kind] = rate if known is None else (known + rate) / 2.0
    entry.owed_ms, entry.owed_lines = 0.0, 0


_ANNOUNCED_LINES = 400
"""
A piece longer than this is said to be coming a frame before it is built.

Only a piece that could not be cut is ever this long: wrapped text (one block, so
that a copy of it has no break the source did not), a list or a fence of hundreds
of lines. At 0.1 to 1 ms a line it is the stall the budget cannot divide.
"""


def _costly(piece: _Piece) -> bool:
    return piece.lines > _ANNOUNCED_LINES and math.isfinite(_STATE.budget_ms)


def _announced(entry: _Entry, im: Any) -> bool:
    """
    Whether the piece the entry is about to build was said to be coming, in an
    earlier frame.

    By position and not by text, and never taken back: a long piece that grows is
    announced once. Announced again at every change, one that changed every frame
    would never be built at all.
    """
    said = entry.announced
    return (
        isinstance(said, tuple)
        and len(said) == 2
        and said[0] == entry.shown
        and said[1] < im.get_frame_count()
    )


def _assemble(entry: _Entry) -> None:
    """
    Make ``entry.pristine`` the blocks of the pieces shown, and forget what is not wanted.
    """
    mapped = entry.kind == "markdown"
    blocks: list[Any] = []
    sources: list[Any] = []
    ends: list[int] = []
    exact: dict[int, _Exact] = {}
    wrapping: set[int] = set()
    replaced = 0
    before: _Built | None = None
    for piece in entry.pieces[: entry.shown]:
        made = entry.built[piece]
        if before is not None and made.blocks and blocks:
            # A list that ends where a piece does: `_dressed` saw one piece.
            last = blocks[-1]
            if _is_item(last) and _ends_list(last, made.blocks[0]):
                if before.aired is None or before.aired[0] is not last:
                    before.aired = (last, _aired(last))
                blocks[-1] = before.aired[1]
        before = made if made.blocks else before
        if mapped and made.sources:
            sources.extend(_moved(made, piece.at))
        for block in made.blocks:
            if isinstance(block, CodeBlock) and piece.kind is not Kind.MARKDOWN:
                exact[id(block)] = entry.exact.get(id(block)) or _exact_of(block)
                if piece.kind is Kind.WRAPPED_EXACT:
                    wrapping.add(id(block))
        blocks.extend(made.blocks)
        ends.append(len(blocks))
        replaced += made.replaced
    wanted = set(entry.pieces)
    for gone in [piece for piece in entry.built if piece not in wanted]:
        del entry.built[gone]
    live = {id(block) for block in blocks}
    _keep(entry.runs, live)
    _keep(entry.variants, live)
    _keep(entry.cut, live)
    _keep(entry.natural, live)
    _keep(entry.sized, live)
    entry.wrapping = wrapping
    entry.pristine = tuple(blocks)
    entry.sources = tuple(sources) if len(sources) == len(blocks) else ()
    entry.ends = ends
    entry.exact = exact
    entry.replaced = replaced
    entry.revision += 1
    entry.stale = False


def _keep(table: dict[int, Any], live: Collection[int]) -> None:
    """
    Drop what a table keyed by block identity holds for blocks that are gone.
    """
    for dead in [number for number in table if number not in live]:
        del table[dead]


def _moved(made: _Built, at: tuple[int, int]) -> tuple[Any, ...]:
    """
    A piece's block sources in the whole text's lines and offsets.
    """
    if at == (0, 0):
        return made.sources
    if made.moved is None or made.moved[0] != at:
        lines, characters = at
        made.moved = (
            at,
            tuple(
                BlockSource(
                    each.span.shifted(lines, characters), each.top.shifted(lines, characters)
                )
                for each in made.sources
            ),
        )
    return made.moved[1]


def _exact_of(block: Any) -> _Exact:
    lines = [
        "".join(chr(getattr(node, "original", node.codepoint)) for node in line)
        for line in block.lines
    ]
    starts: list[int] = []
    count = 0
    for line in block.lines:
        starts.append(count)
        count += len(line)
    return _Exact("\n".join(lines), tuple(starts))


def _fit(entry: _Entry, measure: float) -> None:
    """
    Put in the view the entry's blocks as they have to be for this measure.

    Two kinds of block are not the same at every width. A paragraph holding
    something wider than the measure with no place to break -- a long path, a hash,
    an inline code span, whose spaces the library will not break at -- is given
    places (``_broken``). A whitespace-exact block is cut to the columns that fit
    (``_cut``). Everything else is the block as built, by identity, so the library
    finds its layout again.

    Nothing is done while neither the measure nor the blocks have moved.
    """
    marker = (measure, entry.revision)
    if entry.fitted == marker:
        return
    entry.fitted = marker
    view = entry.view
    out, copies, broken, fitted = _fitted(entry, measure)
    held = view.document.blocks
    if len(held) != len(out) or any(a is not b for a, b in zip(held, out, strict=True)):
        view.document = Document(blocks=tuple(out), sources=entry.sources)
        view.invalidate()
        entry.height = None
        entry.extent = _code_extent(out, fitted)
    entry.copies = copies
    entry.broken = broken
    _ink(entry)


def _fitted(
    entry: _Entry, measure: float
) -> tuple[list[Any], dict[int, _Exact], dict[int, Any], set[int]]:
    """
    The entry's blocks as they have to be for a measure, and what was done to them:
    the literal blocks by number, the paragraphs given breaks, the blocks cut.
    """
    limit = float(sp_from_px(measure, entry.view.px_per_inch))
    out: list[Any] = []
    copies: dict[int, _Exact] = {}
    broken: dict[int, Any] = {}
    fitted: set[int] = set()
    for number, block in enumerate(entry.pristine):
        if isinstance(block, TextBlock):
            made = _broken(entry, block, limit)
            if made is not block:
                broken[number] = made
            out.append(made)
            continue
        if isinstance(block, TableBlock):
            out.append(_held_table(entry, block, limit))
            continue
        known = entry.exact.get(id(block))
        if known is not None:
            copies[number] = known
            if id(block) in entry.wrapping:
                block = _cut(entry, block, limit)
                fitted.add(id(block))
        out.append(block)
    return out, copies, broken, fitted


def _ink(entry: _Entry) -> None:
    """
    Colour each part's blocks in the live theme's ink for it.
    """
    view = entry.view
    blocks = view.document.blocks
    view.clear_colours()
    first = 0
    for piece, stop in zip(entry.pieces, entry.ends, strict=False):
        ink = piece.ink
        if isinstance(ink, str):
            ink = getattr(ui_theme.P, ink)
        if ink is not None:
            packed = ink.u32
            for number in range(first, stop):
                length = _material(blocks[number])
                if length:
                    view.set_colours(number, (ColourSpan(0, length, ink=packed),))
        elif piece.kind is Kind.MARKDOWN:
            _caption_spans(view, blocks, first, stop)
        first = stop
    entry.inked_for = ui_theme.current()
    entry.overlay = None


def _caption_spans(view: Any, blocks: Sequence[Any], first: int, stop: int) -> None:
    """
    Set the info string at the head of each fenced block in a range apart from the
    code under it: dim, on a faint band.

    The band is what does it. Code is the dim ink already in every theme here,
    so an info string in that ink alone reads as the block's first line of code.
    """
    dim = ui_theme.P.text_dim
    band = _alpha(dim, _CAPTION_BAND_ALPHA)
    for number in range(first, min(stop, len(blocks))):
        length = _caption(blocks[number])
        if length:
            view.set_colours(number, (ColourSpan(0, length, ink=dim.u32, mark=band),))


def _held_table(entry: _Entry, block: Any, limit: float) -> Any:
    """
    A table at its columns' natural widths when those fit the measure; else as built.

    The library's default scales a table that fits *up* to the measure, and holds a
    table that is still arriving at its natural widths (``StreamView``). Left alone
    the same table would change every column on the frame a stream is swapped for
    its settled text. Natural widths are also what a table of short cells should
    have in a wide pane: its columns near each other and not a pane apart.

    A table too wide for the measure is the library's to squeeze, here as in a
    stream: every column keeps its longest word and the rest is shared.
    """
    natural = entry.natural.get(id(block))
    if natural is None:
        view = entry.view
        found = table_column_limits(block, view.theme, view.measure).natural
        natural = entry.natural[id(block)] = tuple(max(int(each), 1) for each in found)
    if not natural or sum(natural) > limit - block.left_indent:
        return block
    hit = entry.sized.get(id(block))
    if hit is None:
        try:
            hit = with_columns(block, widths=natural)
        except ValueError:
            hit = block
        entry.sized[id(block)] = hit
    return hit


_RUN_SLACK_PX = 2.0


def _unbreakable(block: Any) -> tuple[tuple[int, int, float], ...]:
    """
    Each run of a paragraph's material that has no legal break in it and is wider
    than the narrowest measure: ``(first node, node past the last, width)``.

    A break is legal at glue that follows anything but a penalty, and at a penalty
    that does not forbid one: the library's own rule (``advtext.linebreak``) as far
    as the material built here can exercise it.
    """
    char, glue, penalty = _nodes()
    floor = float(sp_from_px(_MIN_WIDTH_PX / 2))
    advances: dict[Any, Callable[[int], float]] = {}
    runs: list[tuple[int, int, float]] = []
    start = 0
    width = 0.0
    previous: Any = None
    material = block.material
    for index, node in enumerate(material):
        kind = type(node)
        if isinstance(node, char):
            advance = advances.get(node.font)
            if advance is None:
                advance = advances[node.font] = pixel_metrics(node.font).advance
            width += advance(node.codepoint)
        elif kind is glue:
            if not isinstance(previous, penalty):
                if width > floor:
                    runs.append((start, index, width))
                start, width = index + 1, 0.0
            else:
                width += node.spec.width
        elif kind is penalty:
            if not node.forbids_break:
                if width > floor:
                    runs.append((start, index, width))
                start, width = index + 1, 0.0
        else:
            width += float(getattr(node, "width", 0) or 0)
        previous = node
    if width > floor:
        runs.append((start, len(material), width))
    return tuple(runs)


def _broken(entry: _Entry, block: Any, limit: float) -> Any:
    """
    ``block``, or a copy of it in which the runs wider than the measure can break.
    """
    runs = entry.runs.get(id(block))
    if runs is None:
        runs = entry.runs[id(block)] = _unbreakable(block)
    if not runs:
        return block
    room = limit - block.left_indent - float(sp_from_px(_RUN_SLACK_PX))
    over = frozenset(number for number, run in enumerate(runs) if run[2] > room)
    if not over:
        return block
    hit = entry.variants.get(id(block))
    if hit is not None and hit[0] == over:
        return hit[1]
    made = _with_breaks(block, [runs[number] for number in sorted(over)], room)
    entry.variants[id(block)] = (over, made)
    return made


def _with_breaks(block: Any, runs: Sequence[tuple[int, int, float]], room: float) -> Any:
    """
    A paragraph with breaks allowed inside the given runs of its material.

    At the spaces first: the penalty the library puts before a space in a code span
    is taken out, so a span of several words breaks between them and copies as it
    was written. Then, in any word still wider than the room, a zero penalty after
    each character, which lets the line breaker fill a line and carry on.

    Link and formula spans are moved to the new positions. The library writes a
    space, in a copy, for each break a layout made, and has no way to be told these
    are not between words: ``_mended`` takes those spaces back out.
    """
    char, glue, penalty = _nodes()
    material = block.material
    advances: dict[Any, Callable[[int], float]] = {}
    split: set[int] = set()
    dropped: set[int] = set()
    for start, stop, _ in runs:
        word: list[int] = []
        width = 0.0
        for index in range(start, stop + 1):
            node = material[index] if index < stop else None
            if node is not None and isinstance(node, char):
                advance = advances.get(node.font)
                if advance is None:
                    advance = advances[node.font] = pixel_metrics(node.font).advance
                width += advance(node.codepoint)
                word.append(index)
                continue
            if node is not None and not isinstance(node, glue):
                if isinstance(node, penalty):
                    # Only the penalty that guards an ordinary space: the one
                    # before a hard break's fill has to stay.
                    after = material[index + 1] if index + 1 < stop else None
                    if (
                        node.forbids_break
                        and isinstance(after, glue)
                        and not after.spec.stretch_order
                    ):
                        dropped.add(index)
                else:
                    width += float(getattr(node, "width", 0) or 0)
                continue
            # A space, or the end of the run: the word before it is complete.
            if width > room:
                split.update(word[:-1])
            word, width = [], 0.0
    out: list[Any] = []
    moved = [0] * (len(material) + 1)
    for index, node in enumerate(material):
        moved[index] = len(out)
        if index in dropped:
            continue
        out.append(node)
        if index in split:
            out.append(penalty(0))
    moved[len(material)] = len(out)
    return replace(
        block,
        material=tuple(out),
        links=tuple(
            replace(each, start=moved[each.start], end=moved[each.end]) for each in block.links
        ),
        formulas=tuple(
            replace(each, start=moved[each.start], end=moved[each.end]) for each in block.formulas
        ),
    )


def _cut(entry: _Entry, block: Any, limit: float) -> Any:
    """
    A whitespace-exact block with each line cut to the columns that fit the measure.
    """
    cell = pixel_metrics(block.style.font.key).advance(ord("m"))
    columns = max(1, int((limit - block.left_indent) // cell)) if cell > 0 else 1
    if all(len(line) <= columns for line in block.lines):
        return block
    hit = entry.cut.get(id(block))
    if hit is not None and hit[0] == columns:
        return hit[1]
    lines: list[Any] = []
    for line in block.lines:
        if len(line) <= columns:
            lines.append(line)
        else:
            lines.extend(line[at : at + columns] for at in range(0, len(line), columns))
    made = replace(block, lines=tuple(lines))
    entry.cut[id(block)] = (columns, made)
    return made


@cache
def _view_class() -> type[Any]:
    """
    The view every settled text is drawn by: the library's, answering for its text
    with the breaks the seam put in taken back out.

    Made on first use and not at import, so this module loads without the library.
    """

    class _View(BareView):  # type: ignore[misc, valid-type]
        seam: _Entry | None = None

        def selected_text(self) -> str:
            return _selected(self.seam, self, BareView.selected_text(self))

        def block_text(self, block: int) -> str:
            entry = self.seam
            read = _uncaptioned(self, block, BareView.block_text(self, block))
            if entry is None or not read:
                return str(read)
            exact = entry.copies.get(block)
            if exact is not None:
                return exact.text
            variant = entry.broken.get(block)
            if variant is not None:
                return _mended(self, block, variant, str(read), 0)
            return str(read)

    return _View


def _uncaptioned(view: Any, block: int, read: str) -> str:
    """
    A block's text without the info string the seam set at its head (``_captioned``).

    ``block_text`` answers "the code in this fence", which is what a pane's *copy
    block* wants and what the fence held. A selection dragged across the caption
    does copy it, as the line it is on screen.
    """
    blocks = view.document.blocks
    if not read or not 0 <= block < len(blocks) or not _caption(blocks[block]):
        return str(read)
    head, _, rest = str(read).partition("\n")
    return rest


@cache
def _stream_view_class() -> type[Any]:
    """
    The view a stream is drawn by: the library's, answering ``block_text`` as a
    settled view does.
    """

    class _StreamView(StreamView):  # type: ignore[misc, valid-type]
        def block_text(self, block: int) -> str:
            return _uncaptioned(self, block, StreamView.block_text(self, block))

    return _StreamView


def _selected(entry: _Entry | None, view: Any, whole: str) -> str:
    """
    The selection as text: ``whole``, the library's reading, corrected where the
    seam changed how a block is laid out.

    Three things the seam does are invisible to the library, which writes a space or
    a newline for every line a layout made. A whitespace-exact line cut to the
    window would copy with a newline at each cut; a long literal text built as
    several blocks, with a blank line between them; a word broken at the window's
    edge, with a space in it. Each such block's share of the selection is read
    again -- literal text from what was written, by glyph number (``_Exact``), a
    broken paragraph by taking out the spaces that fall where a word was cut
    (``_mended``) -- and put back in ``whole`` where the library had it. Everything
    else, separators included, is the library's.

    Reads ``view.layout`` and its ``model``, which the library offers on the view
    and does not list in its facade. Where a share cannot be found in ``whole``
    the library's reading is returned as it is.
    """
    if entry is None or not whole or not (entry.copies or entry.broken):
        return whole
    layout = view.layout
    selection = view.selection
    if layout is None or selection.is_empty:
        return whole
    span = type(selection)

    def read(first: int, stop: int) -> str:
        return str(layout.model.copy_text(span(first, stop))) if first < stop else ""

    out: list[str] = []
    at = 0
    cursor = selection.start
    before: tuple[int, int] | None = None
    for number in sorted({*entry.copies, *entry.broken}):
        own = layout.indices_of_block(number)
        low, high = max(selection.start, own.start), min(selection.end, own.stop)
        if low >= high:
            continue
        gap = read(cursor, low)
        if gap:
            found = whole.find(gap, at)
            if found < 0 or whole[at:found].strip():
                return whole
            out.append(whole[at : found + len(gap)])
            at = found + len(gap)
            before = None
        share = read(low, high)
        found = whole.find(share, at)
        if not share or found < 0 or whole[at:found].strip():
            return whole
        between = whole[at:found]
        exact = entry.copies.get(number)
        if exact is not None:
            origin = entry.pieces[bisect.bisect_right(entry.ends, number)].part
            if before == (number - 1, origin) and between:
                # Blocks of one literal part are one text, cut for building.
                between = "\n"
            out.append(between + exact.between(low - own.start, high - own.start))
            before = (number, origin)
        else:
            lead = sum(1 for each in read(own.start, low) if not each.isspace())
            out.append(between + _mended(view, number, entry.broken[number], share, lead))
            before = None
        at = found + len(share)
        cursor = high
    out.append(whole[at:])
    return "".join(out)


def _mended(view: Any, number: int, block: Any, text: str, before: int) -> str:
    """
    ``text`` -- the library's reading of block ``number`` from its glyph ``before`` on --
    without the space written where a word was cut at the window's edge.

    Which line ends are cuts is read off the layout and the material: a line of the
    block that ends between two characters with no glue between them ended inside a
    word. The glyphs of ``text`` are counted as it is walked, a character that is
    not white space being one glyph, so a space met at such a count is the layout's
    and not the writer's.
    """
    char, glue, _ = _nodes()
    lead = sum(1 for node in block.marker if isinstance(node, char))
    spaced: list[bool] = []
    seen = False
    for node in block.material:
        if isinstance(node, glue):
            seen = True
        elif isinstance(node, char):
            spaced.append(seen)
            seen = False
    cuts: set[int] = set()
    count = 0
    for line in view.set_lines(number, number + 1)[:-1]:
        count += len(line.glyphs)
        inside = count - lead
        if 0 < inside < len(spaced) and not spaced[inside]:
            cuts.add(count)
    if not cuts:
        return text
    out: list[str] = []
    glyphs = before
    for each in text:
        if each.isspace():
            if each == " " and glyphs in cuts:
                continue
        else:
            glyphs += 1
        out.append(each)
    return "".join(out)


def _place(
    im: Any,
    entry: _Entry,
    width: float,
    colours: Palette,
    *,
    dirty: bool = False,
    laid_ms: float = 0.0,
) -> float:
    """
    Draw a ``BareView`` at a width, or only claim its room when it is out of sight.

    ``laid_ms`` is what a caller already spent laying this width out, so that the
    cost remembered for the view is the whole of it.
    """
    view = entry.view
    known = entry.height if entry.width == width and not dirty else None
    if known is not None and not view.owns_selection:
        size = im.ImVec2(width, known)
        if not im.is_rect_visible(size):
            im.dummy(size)
            return known
    started = time.perf_counter()
    stats = view.draw(fonts=_fonts(), palette=colours, width_px=width)
    entry.glyphs, entry.missing = stats.glyphs, stats.missing
    if known is None:
        entry.cost_ms = (time.perf_counter() - started) * 1000.0 + laid_ms
        if entry.owed_lines:
            _settle_layout(entry, entry.cost_ms)
        else:
            _STATE.spent_ms = _spent(im) + entry.cost_ms
    layout = view.layout
    entry.width = width
    entry.height = 0.0 if layout is None else float(px_from_sp(layout.height, view.px_per_inch))
    entry.dim = colours.ink == ui_theme.P.text_dim.u32 and not colours.roles
    _overlay(im, entry)
    return entry.height


@dataclass(frozen=True, slots=True)
class _Overlay:
    """
    What the seam draws over a view, in pixels from the view's top-left.

    ``rules`` are ``(left, right, y, under the header)`` and ``veils`` ``(top,
    bottom)``. Worked out for one layout, one set of inks and one answer to
    "is the whole view dim", and kept until one of them is another. ``unruled`` is
    how many tables of the view have no rules because their rows could not be
    found in the layout (``_table_rules``).
    """

    layout: object
    inked_for: object
    dim: bool
    rules: tuple[tuple[float, float, float, bool], ...]
    veils: tuple[tuple[float, float], ...]
    unruled: int = 0


_ROW_RULE_ALPHA = 0.55


def _overlay(im: Any, entry: _Entry) -> None:
    """
    Draw what the library has no setting for: a rule under each row of a table, and
    a veil over what a dim part could not dim.

    **Rules.** The library stacks a table's rows with the leading it gives the
    lines of a wrapped cell, so nothing on screen says where one row ends: a cell
    of four lines reads as four rows. A hairline under each row does, and a full
    one under the header.

    **Veils.** A part's ink reaches text and code. A table's cells take the
    palette's ink and a fenced diff its own colours, so in a part drawn dim -- a
    superseded entry -- both stayed at full strength. The page's own colour over
    them, part-way, is the same de-emphasis. Only for dim: an ink that is a role
    (an error's red) is not something a veil can make.

    Both are on the window's draw list after the view's, positioned from the
    view's last origin, and both are found again only when the layout moves.
    """
    view = entry.view
    layout = view.layout
    origin = view.origin
    if layout is None or origin is None:
        return
    plan = entry.overlay
    current = ui_theme.current()
    if (
        plan is None
        or plan.layout is not layout
        or plan.inked_for is not current
        or plan.dim != entry.dim
    ):
        plan = entry.overlay = _overlay_plan(entry, layout, current)
    if not plan.rules and not plan.veils:
        return
    top = im.get_window_pos().y
    bottom = top + im.get_window_size().y
    left, start = origin
    draw_list = im.get_window_draw_list()
    if plan.rules:
        strong = ui_theme.P.border.u32
        light = _alpha(ui_theme.P.border, _ROW_RULE_ALPHA)
        for x0, x1, y, header in plan.rules:
            at = math.floor(start + y) + 0.5
            if top <= at <= bottom:
                draw_list.add_line(
                    im.ImVec2(left + x0, at), im.ImVec2(left + x1, at), strong if header else light
                )
    if plan.veils:
        veil = _veil()
        for first, last in plan.veils:
            if start + last >= top and start + first <= bottom:
                draw_list.add_rect_filled(
                    im.ImVec2(left, start + first),
                    im.ImVec2(
                        left + float(px_from_sp(layout.width, view.px_per_inch)), start + last
                    ),
                    veil,
                )


def _veil() -> int:
    """
    The page's colour, as opaque as it takes to bring ``text`` down to ``text_dim``.
    """
    p = ui_theme.P

    def light(colour: Color) -> float:
        packed = colour.u32
        return (
            0.299 * (packed & 0xFF) + 0.587 * (packed >> 8 & 0xFF) + 0.114 * (packed >> 16 & 0xFF)
        )

    text, dim, page = light(p.text), light(p.text_dim), light(p.panel)
    share = (text - dim) / (text - page) if text != page else 0.5
    return _alpha(p.panel, min(max(share, 0.2), 0.8))


def _is_dim(ink: Color | str | None) -> bool:
    if isinstance(ink, str):
        return ink == "text_dim"
    return ink is not None and ink.u32 == ui_theme.P.text_dim.u32


def _overlay_plan(entry: _Entry, layout: Any, current: object) -> _Overlay:
    view = entry.view
    blocks = view.document.blocks
    ppi = view.px_per_inch
    rules: list[tuple[float, float, float, bool]] = []
    veils: list[tuple[float, float]] = []
    unruled = 0
    # Which blocks are in a part drawn dim; every block when the palette is.
    dimmed: list[tuple[int, int]] = []
    first = 0
    for piece, stop in zip(entry.pieces, entry.ends, strict=False):
        if _is_dim(piece.ink):
            dimmed.append((first, stop))
        first = stop
    for number, block in enumerate(blocks):
        table = isinstance(block, TableBlock)
        if table:
            found = _table_rules(view, number, block, layout)
            if found is None:
                unruled += 1
            else:
                rules.extend(found)
        if entry.dim:
            # The palette dimmed the table already; a hosted diff has its own.
            veiled = not table and view.hosted(number) is not None
        elif dimmed and (table or isinstance(block, CodeBlock)):
            veiled = any(low <= number < high for low, high in dimmed) and (
                table or view.hosted(number) is not None
            )
        else:
            veiled = False
        if veiled:
            rect = view.block_rect(number)
            if rect is not None:
                veils.append(
                    (float(px_from_sp(rect.y, ppi)), float(px_from_sp(rect.y + rect.height, ppi)))
                )
    return _Overlay(layout, current, entry.dim, tuple(rules), tuple(veils), unruled)


def _table_rules(
    view: Any, number: int, block: Any, layout: Any
) -> list[tuple[float, float, float, bool]] | None:
    """
    Where each row of a table ends, as a rule under it: none under the last.

    Read off the layout. The view answers a table's lines cell by cell, a row at
    a time, and a row is known to be over when the lines have held as many
    characters as its cells do; its foot is its lowest baseline and the depth
    every line of the table is given. **A row with nothing in it** has no line
    to read: the layout gives it one line's pitch, so its foot is that far under
    the row above (or above the row below, for the first).

    A table whose lines do not add up to its cells -- nothing here expects one --
    is given no rules rather than wrong ones, and the answer is ``None``: counted,
    where a pane or a test can ask (``ViewCache.unruled``).
    """
    char, _, _ = _nodes()
    rows = [block.header, *block.rows] if block.header else list(block.rows)
    lines = view.set_lines(number, number + 1)
    ppi = view.px_per_inch
    depth = block.style.strut.depth
    pitch = block.style.interline.baselineskip.width
    widths = getattr(block, "column_widths", ())
    wide = sum(widths) if widths else layout.width - block.left_indent
    left = float(px_from_sp(block.left_indent, ppi))
    right = left + float(px_from_sp(wide, ppi))
    out: list[tuple[float, float, float, bool]] = []
    at = 0
    foot: int | None = None
    # Empty rows met before any row with a line: placed from the first that has one.
    leading: list[int] = []
    for index, row in enumerate(rows[:-1]):
        need = sum(
            1
            for cell in row
            for node in cell.material
            if isinstance(node, char) and node.codepoint != 0x20
        )
        got = 0
        low: int | None = None
        first: int | None = None
        while got < need and at < len(lines):
            line = lines[at]
            at += 1
            got += sum(1 for glyph in line.glyphs if glyph[0] != 0x20)
            low = line.baseline if low is None else max(low, line.baseline)
            first = line.baseline if first is None else min(first, line.baseline)
        if got != need:
            return None
        if low is None:
            if foot is None:
                leading.append(index)
                continue
            low = foot + pitch
        elif leading and first is not None:
            for back, empty in enumerate(reversed(leading), start=1):
                above = float(px_from_sp(first - back * pitch + depth, ppi))
                out.append((left, right, above, bool(block.header) and empty == 0))
            leading.clear()
        foot = low
        under = float(px_from_sp(foot + depth, ppi))
        out.append((left, right, under, bool(block.header) and index == 0))
    out.sort(key=lambda rule: rule[2])
    return out


def _claim_extent(im: Any, extent: float) -> None:
    """
    Tell the window its content is ``extent`` wide without moving the cursor.

    Glyphs on a draw list are not items, so a code line that runs past the measure
    adds nothing to the window's scroll range by being drawn.
    """
    at = im.get_cursor_pos()
    im.dummy(im.ImVec2(extent, 0.0))
    im.set_cursor_pos(at)


def _show(
    im: Any,
    entry: _Entry,
    width: float | None,
    height_cap: float | None,
    colours: Palette,
    *,
    build_hidden: bool = False,
    free: bool = True,
    notice_above: bool = False,
) -> float:
    """
    A settled view at the cursor: built as far as the frame allows, then drawn.

    **A view that has shown a whole text, has building to do and is out of sight is
    not built.** It claims the room it had and costs nothing until it is scrolled
    to. A change of faces under a stack of views in one scrolling child is therefore
    paid for by the ones on screen, and by the others as they come into view.
    ``build_hidden`` is the caller saying otherwise: built wherever it is, within
    the budget, for a view whose ``unbuilt`` somebody is waiting on.

    **A view that has never shown a whole text is built wherever it is**, within the
    budget: its real height is the only honest thing to put in a scroll range, and
    an estimate corrected later moves everything below it under the reader. Until
    its turn comes it claims the room its line count suggests.

    A view on screen that is short of its text draws what it has and says how much
    is to come (``_waiting``): under what it has, or over it with ``notice_above``,
    where the line is in sight however long the text already built is.
    """
    if entry.shown < len(entry.pieces) or entry.stale:
        hidden = False
        size: Any = None
        if not entry.view.owns_selection:
            room = entry.height
            if room is None:
                room = entry.unbuilt * line_height()
            if height_cap is not None:
                room = min(room, height_cap)
            size = im.ImVec2(_width(im, width), max(room, 1.0))
            hidden = not im.is_rect_visible(size)
        if hidden and entry.whole and entry.height is not None and not build_hidden:
            im.dummy(size)
            return float(size.y)
        _advance(im, entry, free)
        if hidden and entry.shown == 0 and entry.pieces:
            # Its turn has not come. Frames keep coming until it has.
            _STATE.unfinished_in = im.get_frame_count()
            im.dummy(size)
            return float(size.y)
    short = entry.shown < len(entry.pieces)
    said = _waiting(im, entry) if short and notice_above else 0.0
    cap = None if height_cap is None else max(height_cap - said, 1.0)
    tall = said + _settled(im, entry, width, cap, colours)
    if short and not notice_above:
        tall += _waiting(im, entry)
    elif not short:
        entry.whole = True
    return tall


def _settled(
    im: Any,
    entry: _Entry,
    width: float | None,
    height_cap: float | None,
    colours: Palette,
) -> float:
    """
    A ``BareView`` in the current window, or in a scrolling child of its own under a cap.

    **Without a cap it is never given a child**, however wide its code. The view
    culls to the window it is drawn in, and a child as tall as the document would
    have nothing to cull: measured here, a 2,200-line document drawn that way put
    all 47,520 of its glyphs on the draw list every frame. A code line wider than
    the measure is instead reported to the window as content width, which scrolls
    to it if it was opened with ``horizontal_scrollbar``.

    With a cap the child exists when the document is taller than the cap or wider
    than the measure, and the measure is always the width less a scrollbar and a
    gutter (``_GUTTER_PX``), whether or not a scrollbar is showing: a document that
    grows past the cap would otherwise re-break once, entirely, on the frame the
    scrollbar appears. The child is as tall as the last whole line under the cap
    (``_snapped``).

    The child is named for the cache key (``_Entry.name``), so new text or a new
    view under the key keeps the scroll position.
    """
    wide = _width(im, width)
    bar = im.get_style().scrollbar_size
    want = wide if height_cap is None else max(_MIN_WIDTH_PX, wide - bar - _GUTTER_PX)
    measure = _measure_for(im, entry, want)
    entry.measure = measure
    # A layout kept at an old measure is drawn in the room there is now, and must
    # not tell the window it is wider than that.
    return _settled_at(im, entry, wide, measure, height_cap, colours, held_back=measure != want)


_RATE_CEILING_MS = 1.0
_REBREAK_SPREAD_MS = 30.0
"""
A change of width expected to cost more than this to lay out is built beside the
view over several frames (``_rebreak``); a cheaper one is one call, in place.

A frame's free allowance in time (``_FREE_MS``): what a frame is let spend on a
text of ordinary length without being asked.
"""


@dataclass(slots=True)
class _Rebreak:
    """
    The entry's text being laid out at a new measure, in a view of its own.

    ``blocks`` are the entry's as fitted to ``measure``; ``done`` is how many of its
    pieces the view has laid out. Good for one ``revision`` of the entry's blocks.
    """

    measure: float
    revision: int
    view: Any
    blocks: tuple[Any, ...]
    copies: dict[int, _Exact]
    broken: dict[int, Any]
    extent: float
    done: int = 0


def _measure_for(im: Any, entry: _Entry, want: float) -> float:
    """
    The measure to draw at this frame: ``want``, or the one the view has while
    ``want`` is new or is still being laid out.

    Laying a text out at a width it has not been drawn at is linear in the text
    -- about half a millisecond a source line of markdown, measured: 340 ms for
    600 lines, 1.1 s for 2,000, 3.1 s for 5,600 -- and dragging a splitter asks for
    a new width every frame. So, by what the layout is expected to cost:

    - **With no budget set** (``set_budget(math.inf)``): taken at once, whatever it
      costs. Asked before any cost is looked at, because a cost is a time some
      earlier layout took: what a test or a one-frame script draws must not turn
      on how fast the machine was a moment ago.
    - **Next to nothing** (``_CHEAP_RELAYOUT_MS``): taken at once.
    - **Anything more waits for the width to stand** for ``_RESIZE_SETTLE_S``. The
      view keeps its layout meanwhile, drawn in the room there is.
    - **Up to a frame's allowance** (``_REBREAK_SPREAD_MS``): then one call, in place.
    - **More than that: built beside the view, a few pieces a frame**, within the
      budget a first build has (``_rebreak``), and swapped in whole. The reader
      keeps a complete text at the old width until the new one is complete;
      nothing shrinks and grows back under the scroll position, and no frame
      holds the whole cost.

    A view still being built for the first time finishes that first.
    """
    old = entry.measure
    if old is None or want == old or not math.isfinite(_STATE.budget_ms):
        entry.wanted = entry.rebreak = None
        return want
    cost = max(entry.cost_ms, _rebreak_ms(entry))
    if cost < _CHEAP_RELAYOUT_MS:
        entry.wanted = entry.rebreak = None
        return want
    frame = im.get_frame_count()
    now = im.get_time()
    if entry.wanted is None or entry.wanted[0] != want:
        entry.wanted = (want, now)
        entry.rebreak = None
    if now - entry.wanted[1] < _RESIZE_SETTLE_S:
        # Frames have to keep coming for the wait to end.
        _STATE.unfinished_in = frame
        return old
    if cost < _REBREAK_SPREAD_MS:
        entry.wanted = entry.rebreak = None
        return want
    if entry.shown < len(entry.pieces) or entry.stale or not _rebreak(im, entry, want):
        _STATE.unfinished_in = frame
        return old
    entry.wanted = None
    return want


def _rebreak_ms(entry: _Entry) -> float:
    """
    What laying the entry's text out again is expected to cost, at the going rates.

    A rate is learned from whatever was laid out last and one slow frame can leave
    it far too high, so it is taken as no more than ``_RATE_CEILING_MS``: a text of
    three lines is cheap whatever the table says.
    """
    rates = _STATE.layout_rate
    return sum(
        piece.lines * min(rates.get(piece.kind.value, _LAYOUT_MS_A_LINE), _RATE_CEILING_MS)
        for piece in entry.pieces[: entry.shown]
    )


def _rebreak(im: Any, entry: _Entry, want: float) -> bool:
    """
    Lay a few more of the entry's pieces out at ``want``, in a view beside the one
    on screen. True once that view is whole and has taken the other's place.

    The second view is given the first pieces' blocks, then more, as a first
    build gives them to the first: each call lays out what was added and finds
    the rest again. It shares the first view's theme, measure and hooks. What it
    cannot share is taken across at the swap where it can be -- the selection, by
    its positions, which a new measure does not renumber -- and is otherwise
    lost: context a click expanded in a fenced diff.

    **Text that changes meanwhile does not send it back to the start.** The second
    view keeps what it has laid out of the pieces whose blocks are the ones it was
    given -- for text that grows at its end, all but the last -- and goes on from
    there. Started again on every change, a text growing faster than one pass
    takes never got its new width, and was drawn cut at the old one for as long
    as it grew.

    ``ViewCache.view(key)`` is another object afterwards.
    """
    state = entry.rebreak
    old = entry.view
    refit = False
    if state is None or state.measure != want or state.revision != entry.revision:
        out, copies, broken, cut = _fitted(entry, want)
        if state is not None and state.measure == want:
            beside = state.view
            same = 0
            for held, now in zip(state.blocks, out, strict=False):
                if held is not now:
                    break
                same += 1
            done = bisect.bisect_right(entry.ends[: min(state.done, entry.shown)], same)
        else:
            beside = _view_class()(
                document=Document(blocks=()),
                theme=old.theme,
                measure=old.measure,
                on_link=old.on_link,
                host_for=old.host_for,
            )
            done = 0
        state = entry.rebreak = _Rebreak(
            want, entry.revision, beside, tuple(out), copies, broken, _code_extent(out, cut), done
        )
        refit = True
    view = state.view
    charged = 0.0
    lines = 0
    kind = ""
    while state.done < entry.shown and _may_build(im):
        piece = entry.pieces[state.done]
        kind = piece.kind.value
        owed = piece.lines * _STATE.layout_rate.get(kind, _LAYOUT_MS_A_LINE)
        _STATE.spent_ms += owed
        _STATE.built_lines += max(piece.lines, 1)
        charged += owed
        lines += piece.lines
        state.done += 1
    if lines or refit:
        stop = entry.ends[state.done - 1] if state.done else 0
        sources = entry.sources[:stop] if entry.sources else ()
        view.document = Document(blocks=state.blocks[:stop], sources=sources)
        view.invalidate()
        started = time.perf_counter()
        drawn = max(want, state.extent) if entry.stretch else want
        view.layout_for(sp_from_px(drawn, view.px_per_inch))
        cost = (time.perf_counter() - started) * 1000.0
        _STATE.spent_ms += cost - charged
        if lines:
            known = _STATE.layout_rate.get(kind)
            rate = cost / lines
            _STATE.layout_rate[kind] = rate if known is None else (known + rate) / 2.0
    if state.done < entry.shown:
        return False
    if old.owns_selection and not old.selection.is_empty:
        view.select(old.selection)
    view.seam = entry
    entry.view = view
    entry.copies, entry.broken, entry.extent = state.copies, state.broken, state.extent
    entry.fitted = (want, entry.revision)
    entry.height = None
    entry.rebreak = None
    _ink(entry)
    return True


def _settled_at(
    im: Any,
    entry: _Entry,
    wide: float,
    measure: float,
    height_cap: float | None,
    colours: Palette,
    *,
    held_back: bool = False,
) -> float:
    """
    ``_settled`` once the measure is decided. ``held_back`` is that the measure is
    not the one the room asks for, so what is drawn may be wider than the room.
    """
    bar = im.get_style().scrollbar_size
    _fit(entry, measure)
    if entry.inked_for is not ui_theme.current():
        _ink(entry)
    over = entry.extent > measure
    # Literal text takes the room its longest line needs, so the whole of it is
    # inside the view's rectangle and a press anywhere on it begins a selection.
    drawn = max(measure, entry.extent) if entry.stretch else measure
    if height_cap is None:
        if over and not entry.stretch:
            _claim_extent(im, entry.extent)
        if not held_back:
            return _place(im, entry, drawn, colours)
        with _no_wider(im, wide):
            return _place(im, entry, drawn, colours)

    view = entry.view
    started = time.perf_counter()
    target = sp_from_px(drawn, view.px_per_inch)
    fresh = view.layout is None or view.layout.width != target
    tall = float(px_from_sp(view.layout_for(target).height))
    laid = 0.0
    if fresh:
        laid = entry.cost_ms = (time.perf_counter() - started) * 1000.0
    capped = tall > height_cap
    if not capped and not over:
        return _place(im, entry, drawn, colours, laid_ms=laid)
    outer = min(tall + (bar if over else 0.0), height_cap)
    if capped:
        outer = _snapped(entry, height_cap - (bar if over else 0.0)) + (bar if over else 0.0)
    size = im.ImVec2(wide, outer)
    if entry.height is not None and not view.owns_selection and not im.is_rect_visible(size):
        im.dummy(size)
        return outer
    flags = im.WindowFlags_.horizontal_scrollbar.value if over else 0
    if capped:
        flags |= im.WindowFlags_.always_vertical_scrollbar.value
    im.begin_child(entry.name, size, 0, flags)
    try:
        if over and not entry.stretch:
            _claim_extent(im, entry.extent)
        if held_back and not over:
            with _no_wider(im, im.get_content_region_avail().x):
                _place(im, entry, drawn, colours, laid_ms=laid)
        else:
            _place(im, entry, drawn, colours, laid_ms=laid)
    finally:
        im.end_child()
    return outer


@contextmanager
def _no_wider(im: Any, room: float) -> Iterator[None]:
    """
    Draw without the window learning of anything past ``room`` from the cursor.

    For a layout kept at an old, wider measure while a new one is settled on or
    built: it is clipped at the window's edge like anything else, and would
    otherwise give the window a scroll range across to text that is about to be
    re-broken -- 14 px of it on the frame a pane gains a scrollbar. ImGui has no
    item that draws without being measured, so the window's content edge is put
    back afterwards (``imgui.internal``).
    """
    window = im.internal.get_current_window()
    edge = max(window.dc.cursor_max_pos.x, im.get_cursor_screen_pos().x + room)
    try:
        yield
    finally:
        reached = window.dc.cursor_max_pos
        if reached.x > edge:
            window.dc.cursor_max_pos = im.ImVec2(edge, reached.y)


def _snapped(entry: _Entry, room: float) -> float:
    """
    ``room`` taken down to the foot of the last line of the view that fits in it.

    A cap is a number of pixels and a line is where the layout put it, so a capped
    view otherwise ends part-way down a line: half a row of glyphs along its
    bottom edge. Cut at a line's foot the view shows whole lines until it is
    scrolled. ``room`` itself when not even the first line fits, or the layout
    has no line to offer there.
    """
    view = entry.view
    layout = view.layout
    if layout is None:
        return room
    hit = entry.snapped
    if hit is not None and hit[0] is layout and hit[1] == room:
        return hit[2]
    answer = _last_foot(view, room)
    entry.snapped = (layout, room, answer)
    return answer


def _last_foot(view: Any, room: float) -> float:
    ppi = view.px_per_inch
    limit = float(sp_from_px(room, ppi))
    blocks = view.document.blocks
    # The first block whose foot is past the limit, by halving: the cut is in it
    # or in the gap above it.
    low, high = 0, len(blocks)
    while low < high:
        middle = (low + high) // 2
        rect = view.block_rect(middle)
        if rect is None:
            return room
        if rect.y + rect.height <= limit:
            low = middle + 1
        else:
            high = middle
    best = 0.0
    if low > 0:
        above = view.block_rect(low - 1)
        if above is not None:
            best = float(above.y + above.height)
    if low < len(blocks):
        block = blocks[low]
        rect = view.block_rect(low)
        hosted = view.hosted(low)
        if hosted is not None and rect is not None:
            # A fenced diff: rows of one height from the block's top.
            row = float(sp_from_px(hosted.view.metrics_px.row, ppi))
            fit = int((limit - rect.y) // row)
            if fit > 0:
                best = max(best, float(rect.y + fit * row))
        else:
            style = getattr(block, "style", None)
            depth = 0 if style is None else style.strut.depth
            for line in view.set_lines(low, low + 1):
                foot = line.baseline + depth
                if foot <= limit:
                    best = max(best, float(foot))
    if best <= 0.0:
        return room
    return float(math.ceil(float(px_from_sp(round(best), ppi))))


def _is_diff_fence(block: CodeBlock) -> bool:
    """
    Whether a fenced block is drawn as a diff: its info string is ``diff`` or ``patch``
    and nothing more.

    Narrower than the library's rule, which reads the first word and drops the
    rest. A fence opened ``diff IMPORTANT also delete tests`` is set as code under
    its whole info string (``_captioned``) and not as a diff under none of it.

    Necessary and not enough: the fence must also be one the diff view draws every
    line of (``_whole_as_diff``), and a block ``_dressed`` found otherwise no longer
    answers true here.
    """
    return block.language.split() in (["diff"], ["patch"])


_AS_WRITTEN = "as-written"
"""
The second word of the language of a ``diff`` fence that is set as code (``_captioned``).
"""

_HUNK_LINE = re.compile(r"@@ -\d+(?:,\d+)? \+\d+(?:,\d+)? @@ ?(.*)$")


def _accounted(text: str, model: Diff) -> bool:
    """
    Whether the diff view draws every line of ``text``, the unified text ``model``
    was read from.

    The library's reader drops no line and its view does not draw all it keeps: a
    sentence above a file header, a mail's message, whatever is over a bare
    ``---``, a line under a hunk that had had its count -- each is kept on the
    model (``FileDiff.header``) and is nowhere on screen. On an approval surface
    that sentence is one the recipient reads and the approver does not.

    Decided from the model, by counting, and not from a list of those shapes:
    what the view draws of a model is known -- a file's label, each hunk's ``@@``
    line with its section, each row behind its marker, each line of a file shown
    as written -- and every line of the text with ink in it must be one of those,
    each drawn line answering for one written line. A line is drawn when it is

    - a row's: its marker and its text, or the text alone for context a writer
      did not mark;
    - a hunk's ``@@`` line whose section is the hunk's. The numbers are not
      compared: the view writes the counts it counted;
    - ``--- path`` or ``+++ path`` where the path is the file's label (under ``a/``
      or ``b/`` or not) or ``/dev/null``;
    - a line of a file shown as written (``FileDiff.raw``).

    Anything else is not. That takes in what ``git diff`` prints -- its ``diff
    --git`` line, and an ``index`` line whose hashes and mode are on no row -- so
    a fence holding one is set as code, and so is a path with a timestamp after
    it, and a rename written as two different paths. Order is not compared.
    """
    shown: dict[str, int] = {}

    def draws(line: str) -> None:
        shown[line] = shown.get(line, 0) + 1

    for file in model.files:
        for line in file.raw:
            draws(line.rstrip("\r"))
        if file.dialect:
            continue
        label = file.label
        for mark, path in (("--- ", file.old_path), ("+++ ", file.new_path)):
            bare = path[2:] if path[:2] in ("a/", "b/") else path
            if path and (path == "/dev/null" or label in (path, bare)):
                draws(mark + path)
        for hunk in file.hunks:
            draws("@@ " + hunk.section)
            for row in hunk.rows:
                draws(row.kind.value + row.text)
    for line in text.split("\n"):
        line = line.rstrip("\r")
        if not line.strip():
            continue
        forms = [line, " " + line]
        if line.startswith("@@"):
            found = _HUNK_LINE.match(line)
            forms.append("@@ " + (found[1] if found else line.strip("@ ")))
        elif line.startswith("\\"):
            forms.append("\\" + line[1:].lstrip())
        for form in forms:
            if shown.get(form, 0) > 0:
                shown[form] -= 1
                break
        else:
            return False
    return True


def _whole_as_diff(block: CodeBlock) -> bool:
    """
    Whether the diff view would draw every line this fenced block holds.

    Asked of the block's own lines when the block is dressed, which is before any
    view has read the fence. The hook asks again of the text the view did read
    (``_Fences``): that one is cut from the source, and the two differ where a line
    begins with a tab.

    The lines are read as a finished text, every time the block is another: a
    fence still arriving is read again whole for each line it gains, and its last
    line is read as a line although it may be half of one. So while a second
    file's ``--- path`` is the last line to have arrived under a hunk that has had
    its count, the fence is code, and a diff again when ``+++`` follows.
    """
    text = material_text(block).text
    if not text.strip():
        return True
    return _accounted(text, Diff.from_unified(text + "\n"))


class _Fences:
    """
    The hook that draws a fenced diff: asked only about what ``_is_diff_fence``
    allows, and answering only with a diff view that draws all it was given.

    ``_dressed`` has already set as code every fence whose own lines the view
    would not draw whole, so a refusal here is the rare fence the two readings
    disagree about. It is then a code block as well, without the ``diff`` above it.
    """

    __slots__ = ("inner",)

    def __init__(self, inner: Any) -> None:
        self.inner = inner

    def __call__(self, view: Any, number: int, block: Any) -> Any:
        if isinstance(block, CodeBlock) and _is_diff_fence(block):
            hosted = self.inner(view, number, block)
            if hosted is not None and _accounted(hosted.text, hosted.view.diff):
                # The library gives a fenced diff the theme of the document it is
                # in. A diff is `Role.DIFF` wherever it is drawn.
                hosted.view.theme = _theme("diff")
                return hosted
        return None


def _code_extent(blocks: Iterable[Any], cut: Collection[int] = ()) -> float:
    """
    How far right the widest unbroken code line of these blocks reaches, in pixels.

    ``cut`` holds the identities of blocks that are cut to the measure and so
    reach no further than it.
    """
    widest = 0.0
    for block in blocks:
        # A fenced diff is drawn by a diff view, which scrolls its own long lines.
        if not isinstance(block, CodeBlock) or not block.lines or _is_diff_fence(block):
            continue
        if id(block) in cut:
            continue
        cell = pixel_metrics(block.style.font.key).advance(ord("m"))
        columns = max(len(line) for line in block.lines)
        widest = max(widest, _px(block.left_indent + columns * cell))
    return widest


def _run_extent(blocks: Iterable[Any]) -> float:
    """
    How far right the widest unbreakable run of these paragraphs reaches, in pixels.

    For a stream, whose blocks are the library's to lay out: the seam cannot give
    them a place to break, so it tells the window how wide they are.
    """
    widest = 0.0
    for block in blocks:
        if isinstance(block, TextBlock):
            for _, _, width in _unbreakable(block):
                widest = max(widest, _px(block.left_indent + width))
    return widest


def _open(entry: _Entry, pieces: tuple[_Piece, ...]) -> None:
    """
    Give an entry new pieces to show, keeping what the old ones had in common.

    Pieces equal at the front stay in the view as they are; one found anywhere else
    is still built and is not built again.
    """
    kept = 0
    for old, new in zip(entry.pieces[: entry.shown], pieces, strict=False):
        if old != new:
            break
        kept += 1
    entry.pieces = pieces
    entry.shown = kept
    entry.stale = True


def _bare(on_link: Callable[[str], None] | None, host: Any, theme: str = "prose") -> BareView:
    made: BareView = _view_class()(
        document=Document(blocks=()),
        theme=_theme(theme),
        measure=_STATE.measure,
        on_link=on_link,
        host_for=host,
    )
    return made


def markdown(
    cache: ViewCache,
    key: Hashable,
    source: str,
    *,
    width: float | None = None,
    height_cap: float | None = None,
    ink: Color | None = None,
    on_link: Callable[[str], None] | None = None,
    demote: int = 0,
    build_hidden: bool = False,
    free_allowance: bool = True,
) -> float:
    """
    A markdown document at the cursor: selectable, culled, as wide as the window.

    The document is parsed when ``source`` changes and laid out when the width does;
    a frame where neither moved costs the draw of what is on screen. A source that
    changes keeps its view, its selection and its scroll position, and a long one
    is parsed again only from the piece the change is in: a text that gains a line
    at its end re-reads its last forty lines or so, whatever its length.

    A long source is built over several frames (``set_budget``): what is ready is
    drawn, and a line under it says how many lines are to come.

    ``height_cap`` puts a document taller than it in a scrolling child of that
    height; a shorter one takes only its own. ``ink`` draws everything in one colour.
    ``demote`` sets headings that many levels down (``Part.demote``).

    A fenced block is never broken, so a line of it may run past the window. The
    window is told how wide the content is and scrolls there if it was opened with
    ``horizontal_scrollbar``; with ``height_cap`` the view's own child does. Anything
    else wider than the window with no space in it is broken at the window's edge.

    ``on_link`` makes links live (drawn in the accent, underlined, called on a
    click). Live or not, a link is set as its text and then its destination.

    Link reference definitions are off, as in a stream, so a document reads the same
    finished as it did arriving.

    ``build_hidden`` and ``free_allowance`` are ``document``'s.
    """
    im = _frame()
    cell: list[str] = [source]
    entry, made = cache._held(
        key,
        "markdown",
        # The fences are read from the text the document was parsed from, by the
        # line numbers of its source map.
        lambda: _bare(on_link, _Fences(DiffFences(source=lambda: cell[0], metrics=pixel_metrics))),
    )
    if made:
        entry.source = cell
    token = (source, demote)
    if entry.token != token:
        entry.token = token
        entry.source[0] = source
        _open(entry, _pieces_of((Part(source, demote=demote),), patient=not free_allowance))
    if not _begin(im, key, entry):
        return _twice(im, key)
    entry.view.on_link = on_link
    return _show(
        im,
        entry,
        width,
        height_cap,
        palette(ink),
        build_hidden=build_hidden,
        free=free_allowance,
    )


def document(
    cache: ViewCache,
    key: Hashable,
    parts: Sequence[Part],
    *,
    width: float | None = None,
    height_cap: float | None = None,
    on_link: Callable[[str], None] | None = None,
    build_hidden: bool = False,
    free_allowance: bool = True,
    notice_above: bool = False,
) -> float:
    """
    Several parts as **one** document in one view: markdown, literal and wrapped
    text in sequence, each in its own ink.

    For a pane that would otherwise stack a view per piece -- the closed runs of a
    transcript, a concern log, a list of entries. One view is one selection that
    runs across the parts, one layout, and one draw that culls.

    Parts are built once each and found again by their text and kind, so a call
    whose parts grew at the end -- or lost some at the start -- builds only the new
    ones, and the layout re-breaks only those (measured: one block appended to 400
    is 1.3 ms). Many new parts at once are built over several frames, as a long
    ``markdown`` is.

    **Pass a tuple, and the same one while nothing has changed**: the call then
    compares nothing. A tuple cannot change under the seam, so its identity is
    proof. Any other sequence -- a list the pane appends to -- is compared part by
    part on every call, which is right and, for long texts, not free.

    A part whose text changes is a new part and is parsed again whole. Text that
    grows a few characters a frame belongs in a ``stream`` drawn after this.

    A table inside a coloured markdown part keeps the palette's ink, and a fenced
    diff its own colours: the library colours text blocks and code blocks. A part
    drawn *dim* is the exception -- both are veiled to match (``_overlay``).

    Two switches for a pane that swaps one view for another of the same text and
    waits for ``ViewCache.unbuilt`` to reach zero before it does:

    - ``build_hidden`` builds new parts wherever the view is. By default a view that
      has shown a whole text and is out of sight builds nothing until it is
      scrolled to, so its ``unbuilt`` would never reach zero there.
    - ``free_allowance=False`` builds this view a small piece a frame, inside the
      budget, and never on the frame's free allowance (``_FREE_LINES``): no 30 to
      45 ms frame for a text of ordinary length, at the price of that text taking
      a few frames to be whole. Decided when the parts change; a view should
      always be asked the same way.

    ``notice_above`` puts ``typesetting: N more lines`` over the text and not under
    it, for a pane whose reader decides on the text: under a text already longer
    than the window, the line that says it is not all there is out of sight.
    Under a ``height_cap`` the line comes out of the cap.
    """
    im = _frame()
    entry, _ = cache._held(
        key,
        "document",
        # No source map over parts, so a fenced diff is read from its code block's
        # own lines: tabs as spaces, trailing blank lines dropped.
        lambda: _bare(on_link, _Fences(DiffFences(metrics=pixel_metrics))),
    )
    if not (isinstance(parts, tuple) and entry.token is parts):
        held = entry.token
        same = (
            isinstance(held, tuple)
            and len(held) == len(parts)
            and all(a is b or a == b for a, b in zip(held, parts, strict=True))
        )
        if not same:
            # Equal pieces keep their blocks, so a part that only changed its
            # colour is inked again and nothing else.
            _open(entry, _pieces_of(parts, patient=not free_allowance))
        # Kept as a tuple of our own when the caller's can change under us.
        entry.token = parts if isinstance(parts, tuple) else tuple(parts)
    if not _begin(im, key, entry):
        return _twice(im, key)
    entry.view.on_link = on_link
    return _show(
        im,
        entry,
        width,
        height_cap,
        palette(),
        build_hidden=build_hidden,
        free=free_allowance,
        notice_above=notice_above,
    )


def _material(block: Any) -> int:
    """
    How many material positions a block has to colour; zero for one that takes no span.
    """
    if isinstance(block, CodeBlock):
        return sum(len(line) for line in block.lines)
    if isinstance(block, TextBlock):
        return len(block.material)
    return 0


def plain(
    cache: ViewCache,
    key: Hashable,
    text: str,
    *,
    mono: bool = True,
    wrap: bool = False,
    exact: bool = False,
    colour: Color | None = None,
    width: float | None = None,
    height_cap: float | None = None,
    role: Role = Role.LITERAL,
) -> float:
    """
    Text that is not markdown, set exactly as written: an asterisk is an asterisk.

    Three forms, and the difference is what happens to a long line.

    - ``wrap=False`` (the default): every source line is one line, spacing and
      indentation kept to the column. A line longer than the window is not broken:
      the view is as wide as its longest line, so the window scrolls to it if it was
      opened with ``horizontal_scrollbar`` (with ``height_cap``, the view's own child
      does). Monospace only -- ``mono`` is ignored.
    - ``wrap=True``: lines are broken to the window at spaces. The cost is
      whitespace: each line's indentation is dropped and a run of spaces inside it
      becomes one. Right for a sentence, wrong for JSON or a table drawn in
      characters. A word wider than the window is broken at the window's edge and
      still copies whole. About five times the layout cost per line, and built in
      one go -- with a frame's notice past ``_ANNOUNCED_LINES`` -- so it is for a few
      lines and not for a file.
    - ``wrap=True, exact=True``: broken to the window at *any* column, and nothing
      else changed -- every space, every indent, every blank line is as written,
      and what is copied is the text without the window's breaks. Right for a
      command or a path, where a space matters and so does seeing the end of it.
      Monospace only.

    Text that grows keeps its view, its selection and its scroll position. The
    first and the third form are built in pieces of about sixty lines, so a long
    text that gains a line builds its last piece again and no other.

    ``colour`` is the ink: a segment kind's role. The default is the theme's text.
    ``role`` is whose size and leading the text is set in; ``mono=False`` is
    ``Role.PROSE``.
    """
    im = _frame()
    if wrap and not exact:
        kind = Kind.WRAPPED
        if not mono:
            role = Role.PROSE
    else:
        kind = Kind.WRAPPED_EXACT if wrap else Kind.LITERAL
    chosen = _literal_name(role, gap=False)
    entry, _ = cache._held(key, f"plain-{chosen}", lambda: _bare(None, None, chosen))
    token = (text, kind)
    if entry.token != token:
        entry.token = token
        _open(entry, _pieces_of((Part(text, kind, role=role),), flush=True, inked=False))
        entry.stretch = not wrap
    if not _begin(im, key, entry):
        return _twice(im, key)
    return _show(im, entry, width, height_cap, palette(colour or ui_theme.P.text))


def diff(
    cache: ViewCache,
    key: Hashable,
    source: PendingApproval | Diff | Sequence[DiffText] | None,
    *,
    side_by_side: bool = False,
    height_cap: float | None = None,
    width: float | None = None,
    warning: bool = True,
    background: bool = False,
) -> float | None:
    """
    A diff at the cursor, or ``None`` when there is nothing diff-shaped to show.

    ``source`` is a parked call, the texts of one (``approval.diff_texts``' answer,
    for arguments the operator is editing), or a ``Diff`` already made. The model is
    built once and held until the texts change; ``None`` is remembered the same way,
    so asking every frame is one dict lookup.

    ``None`` is the answer for a call with nothing to diff **and for a model with no
    files in it**: no texts, an empty sequence. A pane draws its heading only for a
    number.

    Nothing here reads a file. A parked call carries the texts the driver read when
    it parked (``PendingApproval.texts``), and a pane showing edited arguments calls
    ``approval.diff_texts`` itself, when the arguments change and not per frame.

    The view draws line numbers, the ``+``/``-`` gutter, intra-line emphasis and
    collapsed context a click expands; it scrolls a long line horizontally and, with
    ``height_cap``, a long diff vertically **in a child of its own** -- do not wrap it
    in another. A short diff takes only its own height. New texts under a key are
    shown by the same view, so its child and its scroll position stay.

    A model that will take long to make -- changes scattered through thousands of
    lines, past ``_DIFF_ANNOUNCED_LINES`` -- is drawn for one frame as a line saying
    so, and made on the next: an announced stall, a quarter of a second to
    several. With ``background=True`` it is made on a thread of its own instead
    (``_DiffJob``) and the line stands for as many frames as that takes, none of
    them a stall; what is left on the frame is the view's first draw, measured
    at 100 ms for 5,000 lines and 200 ms for 20,000. Either way the number
    returned while the line is up is the line's, ``pending()`` is true and
    ``ViewCache.unbuilt(key)`` is 1. A pane that asks for the thread must not
    expect the view on any particular frame.

    **A model the thread could not make is not asked for again.** Whatever it
    raised is said once a frame, in the warning ink, in the diff's place, and the
    answer is ``None`` for as long as the texts are the same: the pane shows what
    it shows for a call with no diff, under a line saying why there is none.
    Unthreaded, the same failure is raised in the frame that asked.

    The file header's warning is the gate's *prediction* and is worded as one:
    ``! may not apply: <reason>``. **A header too long for the view is said again
    above it, wrapped** (``_headers_above``): the view's own header is one line, cut
    at the view's edge and outside what it scrolls to, so a long path would
    otherwise put the warning, the counts and the end of the path out of reach.
    The warning comes first there. A pane need not say it again; one that does,
    on a line of its own, passes ``warning=False`` and the view then carries no
    copy of the gate's prediction at all (the library's own warnings -- lines it
    could not read, control characters -- stay).

    ``height_cap`` is what the whole of it may take, the lines above included,
    and is taken down to a whole number of rows, so the last row in sight is
    never a sliver; when a line is too long for the view the horizontal
    scrollbar's height is kept clear of the rows. ``diff_row_height()`` is a row.
    """
    im = _frame()
    token: object = source
    if isinstance(source, PendingApproval):
        token = source.texts if source.texts is not None else source.diff
    entry = cache._fresh(key, "diff")
    if entry is not None and entry.warned != warning and entry.token is not _NOT_BUILT:
        # The same texts with the other answer about the warning: another model.
        entry.token = _NOT_BUILT
    if entry is not None and entry.failed is not None:
        if entry.failed[1] == warning and _same_source(entry.failed[0], token):
            _unmade(im, entry.failed[2], _width(im, width))
            return None
        entry.failed = None
    if entry is None or not _same_source(entry.token, token):
        job = None if entry is None else entry.job
        if job is not None and not (job.warning == warning and _same_source(job.token, token)):
            # Texts nobody is asking about any more: its thread ends unread.
            job = None
        if job is None and _slow_to_diff(token):
            if entry is None:
                entry = cache._put(key, "diff", _NOT_BUILT, None)
            started_job = None
            if background:
                # A list is the caller's to change while the thread reads it.
                given = tuple(source) if isinstance(source, list) else source
                try:
                    started_job = _DiffJob(token, warning, given)
                except RuntimeError:
                    # No thread to be had: made on the frame, announced, as a
                    # pane that did not ask for one gets it.
                    started_job = None
            if started_job is not None:
                job = entry.job = started_job
            elif not _same_source(entry.announced, token):
                entry.announced = token
                _STATE.unfinished_in = im.get_frame_count()
                return _said(im, "diffing a large change...")
        started = time.perf_counter()
        if job is None:
            model = _model_of(source, warning)
        elif not job.done.is_set():
            _STATE.unfinished_in = im.get_frame_count()
            return _said(im, "diffing a large change...")
        elif job.failure is not None:
            # Said, and remembered against these texts: the thread is not started
            # again for them, on this frame or any later one.
            assert entry is not None
            entry.job = None
            entry.failed = (token, warning, f"{type(job.failure).__name__}: {job.failure}")
            _unmade(im, entry.failed[2], _width(im, width))
            return None
        else:
            model = job.model
        held = None if entry is None else entry.view
        if model is None:
            view = None
        elif held is not None:
            # The same view, so the child it scrolls in is the same child.
            held.set_diff(model)
            view = held
        else:
            view = DiffView(model, _theme("diff"), metrics=pixel_metrics)
        entry = cache._put(key, "diff", token, view)
        entry.warned = warning
        _spend(im, started)
    view = entry.view
    if view is None:
        return None
    if not _begin(im, key, entry):
        return _twice(im, key)
    wanted = DiffLayout.SIDE_BY_SIDE if side_by_side else DiffLayout.UNIFIED
    if view.layout is not wanted:
        view.set_layout(wanted)
    wide = _width(im, width)
    above = _headers_above(im, entry, wide)
    view.height_cap = None if height_cap is None else _diff_cap(view, wide, height_cap - above)
    tall = float(view.height_for(wide))
    size = im.ImVec2(wide, tall)
    if entry.height is not None and not view.owns_selection and not im.is_rect_visible(size):
        im.dummy(size)
        view.handle_keys()
    else:
        stats = view.draw(fonts=_fonts(), palette=palette(), width_px=wide)
        entry.glyphs, entry.missing = stats.glyphs, stats.missing
    entry.width, entry.height = wide, above + tall
    return above + tall


def _unmade(im: Any, failure: str, wide: float) -> None:
    """
    Say that a diff could not be made, in the warning ink, wrapped. ``diff`` then
    answers ``None``: there is no diff to show, and the pane goes on to what it
    shows without one.
    """
    im.push_text_wrap_pos(im.get_cursor_pos().x + wide)
    try:
        im.text_colored(
            ui_theme.P.warn.vec4,
            f"the diff of this call could not be made ({failure}): read its arguments",
        )
    finally:
        im.pop_text_wrap_pos()


def _unwarned(model: Diff) -> Diff:
    """
    ``model`` with no file carrying the gate's prediction; itself when none does.
    """
    if not any(file.will_not_apply for file in model.files):
        return model
    return Diff(tuple(replace(file, will_not_apply="") for file in model.files))


def _headers(entry: _Entry) -> tuple[tuple[str, str, float, float], ...]:
    """
    Each file's header as the diff view words it: ``(text, warnings, width, width
    of the text alone)``.

    Read from the view's own frame plan, one row a file, when the model changes:
    the wording is the library's -- the counts, what happened to the file, what it
    could not read -- and a copy composed here would drift from it. A header is
    one character a cell from the second cell on, so the cells are the text.
    """
    view = entry.view
    model = view.diff
    if entry.headed is model:
        return entry.headers
    cell = float(view.metrics_px.cell)
    first = view.row_info(0)
    colours = palette()
    out: list[tuple[str, str, float, float]] = []
    for number, file in enumerate(model.files):
        row = view.row_of(
            replace(first, kind="file", file=number, hunk=None, left=None, right=None)
        )
        cells: dict[int, str] = {}
        for command in view.frame_plan(cell * 8, row, row + 1, colours).region("frame").cmds:
            codepoint = getattr(command, "codepoint", None)
            if codepoint is not None:
                at = round(float(px_from_sp(command.x, view.px_per_inch)) / cell)
                cells[at] = chr(codepoint)
        said = "".join(cells.get(at, " ") for at in range(1, max(cells, default=0) + 1))
        cut = said.find("  ! ", len(file.label or ""))
        text, warnings = (said, "") if cut < 0 else (said[:cut], said[cut:].strip())
        out.append((text, warnings, (len(said) + 2) * cell, (len(text) + 2) * cell))
    entry.headed = model
    entry.headers = tuple(out)
    return entry.headers


def _headers_above(im: Any, entry: _Entry, wide: float) -> float:
    """
    Say, wrapped, each file header the view will cut at its edge. The height that took.

    Native lines in the chrome's face, which is the diff's: they wrap at the view's
    width at any character, so a path with no space in it is all there. The
    warnings first, in the warning ink, because they are what must not be missed;
    the rest only when it is cut too.
    """
    cut = [each for each in _headers(entry) if each[2] > wide]
    if not cut:
        return 0.0
    before = im.get_cursor_pos()
    im.push_text_wrap_pos(before.x + wide)
    try:
        for text, warnings, _, alone in cut:
            if warnings:
                im.text_colored(ui_theme.P.warn.vec4, warnings)
            if alone > wide:
                im.text_colored(ui_theme.P.accent.vec4, text)
    finally:
        im.pop_text_wrap_pos()
    return float(im.get_cursor_pos().y - before.y)


def _diff_cap(view: Any, wide: float, cap: float) -> float:
    """
    ``cap`` taken down to a whole number of the view's rows, at least one, with the
    horizontal scrollbar's height on top when the view will draw one.

    The view pins that scrollbar to the bottom of what is in sight, over the rows,
    so a cap that is not rows-and-a-bar leaves the last row under it.
    """
    row = float(view.metrics_px.row)
    view.height_cap = None
    bar = float(view.height_for(wide)) - float(view.content_height)
    rows = max(1, int((cap - bar) // row))
    return rows * row + bar


_NOT_BUILT = object()
"""
The token of a diff entry that holds no model yet: one is being made.
"""


def _model_of(
    source: PendingApproval | Diff | Sequence[DiffText] | None, warning: bool
) -> Diff | None:
    """
    The model ``diff`` shows for a source, or ``None`` for nothing to show. Pure.
    """
    if isinstance(source, Diff):
        model: Diff | None = source
    elif isinstance(source, PendingApproval):
        model = approval_diff(source)
    else:
        model = None if source is None else diff_model(source)
    if model is not None and not model.files:
        return None
    if model is not None and not warning:
        model = _unwarned(model)
    return model


class _DiffJob:
    """
    A diff model being made on a thread of its own, for texts too long to diff in a frame.

    ``difflib`` over changes scattered through thousands of lines is a quarter of a
    second to a second and cannot be cut into pieces. It is also a pure function
    of frozen records, so it can run beside the frames: the thread is handed the
    source, writes ``model`` or ``failure`` and then sets ``done``, and the drawing
    thread reads neither before ``done`` is set. The event is the whole hand-over;
    nothing is shared while the thread runs, and what comes back is immutable.

    Frames go on being drawn meanwhile, slower: the interpreter runs one thread at
    a time and gives each a few milliseconds in turn. A job whose texts are no
    longer asked for is dropped by its entry and finishes unread.
    """

    __slots__ = ("done", "failure", "model", "token", "warning")

    def __init__(self, token: object, warning: bool, source: Any) -> None:
        self.token = token
        self.warning = warning
        self.model: Diff | None = None
        self.failure: BaseException | None = None
        self.done = threading.Event()
        threading.Thread(target=self._run, args=(source,), name="typeset-diff", daemon=True).start()

    def _run(self, source: Any) -> None:
        try:
            self.model = _model_of(source, self.warning)
        except BaseException as failure:
            self.failure = failure
        finally:
            self.done.set()


def _same_source(held: object, now: object) -> bool:
    """
    Whether two diff sources are the same thing to show.

    Identity first: a parked record hands over the same tuple every frame. A ``Diff``
    by identity only -- it compares by rows, and two diffs with equal rows may still
    differ in the context a click would expand. Texts and unified strings by value.
    """
    if held is now:
        return True
    if isinstance(held, Diff) or isinstance(now, Diff):
        return False
    return type(held) is type(now) and bool(held == now)


def _slow_to_diff(source: object) -> bool:
    """
    Whether making this source's model is a stall worth a frame's notice.

    Only texts are: a ``Diff`` is made already and a unified string is read in one
    pass. For texts the cost is ``difflib``'s on what the two sides do not share at
    their ends, so that is what is counted, by halving -- comparisons of slices,
    which is a memory compare and not a walk in Python.
    """
    if not math.isfinite(_STATE.budget_ms) or not isinstance(source, (tuple, list)):
        return False
    return any(
        isinstance(part, DiffText)
        and _changed_lines(part.before or "", part.after or "") > _DIFF_ANNOUNCED_LINES
        for part in source
    )


def _changed_lines(before: str, after: str) -> int:
    """
    How many lines of the shorter side lie between the two texts' common ends.
    """
    shorter = min(len(before), len(after))
    low, high = 0, shorter
    while low < high:
        middle = (low + high + 1) // 2
        if before[:middle] == after[:middle]:
            low = middle
        else:
            high = middle - 1
    head = low
    low, high = 0, shorter - head
    while low < high:
        middle = (low + high + 1) // 2
        if before[len(before) - middle :] == after[len(after) - middle :]:
            low = middle
        else:
            high = middle - 1
    tail = low
    return min(
        before.count("\n", head, len(before) - tail), after.count("\n", head, len(after) - tail)
    )


def diff_model(texts: Sequence[DiffText]) -> Diff:
    """
    The diff of some before/after texts: one file of it per part.

    From the texts and not from a unified string. ``difflib.unified_diff`` over lines
    kept with their ends writes a removed and an added line as one physical line,
    ``-foo+bar``, whenever a text has no closing newline -- the normal case for an
    Edit's strings -- and no reader can take that apart: the view would show one
    removed line and no added one (pinned in ``tests/test_typeset.py``).

    What a part says about itself is carried onto its file. ``will_not_apply`` is
    the gate's reading of the file at the moment the call parked, a prediction, and
    reaches the view's header as one: ``may not apply: <reason>``. ``unreadable`` on
    a Write -- a file that exists and could not be shown, drawn as all-new -- goes
    in the label, because the model has no field for it and without the words it is
    indistinguishable from a file being created.

    Pure. Cost is ``difflib``'s on what is left after the common head and tail are
    set aside: one contiguous edit is cheap in any file; scattered changes are
    200 ms across 5,000 lines and 960 ms across 20,000 (measured here, the frame
    that builds the model), which is why ``diff`` announces a large one.
    Immutable, so one model may be drawn by two panes -- each in a view of its own.
    """
    files: list[Any] = []
    for part in texts:
        label = part.label or "(no path)"
        if part.unreadable and part.will_not_apply is None:
            label = f"{label}  (existing file is not readable as text; shown as new)"
        hedged = f"may not apply: {part.will_not_apply}" if part.will_not_apply else ""
        built = Diff.from_texts(part.before, part.after, label, will_not_apply=hedged)
        files.extend(built.files)
    return Diff(tuple(files))


_FROM_UNIFIED = "(read back from the unified text: may be incomplete)"


def approval_diff(pending: PendingApproval) -> Diff | None:
    """
    The diff a parked call would make, from the texts it carries. No file is read.

    ``None`` when the call has nothing diff-shaped. A record built without texts but
    with a unified string -- a construction site that predates ``texts`` -- is read
    from that string rather than shown as having no diff: lossy in the way
    ``diff_model`` describes, and better than hiding a change from its approver.
    Each file read that way says so in its header (``_FROM_UNIFIED``), because a
    line the reader could not take apart is not in it.
    """
    if pending.texts is not None:
        return diff_model(pending.texts)
    if pending.diff:
        read = Diff.from_unified(pending.diff)
        return Diff(
            tuple(
                replace(file, label=f"{file.label or '(no path)'}  {_FROM_UNIFIED}")
                for file in read.files
            )
        )
    return None


# ---------------------------------------------------------------------------
# One rule for "is this body markdown?"
# ---------------------------------------------------------------------------

_WRAP_FLOOR = 60
"""
Nobody wraps prose narrower than this, so lines shorter than it were ended on purpose.
"""

_ENDED_EARLY = 0.8
"""
A line was ended on purpose when it and the next line's first word come to less
than this share of the paragraph's measure. Text wrapped by a person or a model is
wrapped by feel, a few columns either side of the column aimed at, and a rule that
reads every short line as deliberate breaks such prose at random.
"""

_BODY_FENCE = re.compile(r" {0,3}(`{3,}|~{3,})")
_BODY_LIST = re.compile(r" {0,3}(?:[-*+]|\d{1,9}[.)])(?:\s|$)")
_BODY_BLOCK = re.compile(r" {0,3}(?:#{1,6}(?:\s|$)|[-*+](?:\s|$)|\d{1,9}[.)](?:\s|$)|>|\|)")
_BODY_RULE = re.compile(r"\s*([-=*_])(?:\s*\1)*\s*$")
_BODY_FIELD = re.compile(r"[A-Za-z_][\w.-]*(?: [\w.-]+){0,2}:(?:\s+\S|$)")
_BODY_INLINE = re.compile(r"`[^`\n]+`|\*\*\S|\]\(")
_BODY_PUNCTUATION = re.compile(r"([!-/:-@\[-`{-~])")
# What a writer wrapping by hand carries to the next line whole: a link, a code
# span, or failing those a word.
_BODY_UNIT = re.compile(r"\[[^\]\n]*\]\([^)\n]*\)\S*|`[^`\n]+`\S*|\S+")


def prose_parts(text: str, *, colour: Color | str | None = None) -> tuple[Part, ...]:
    r"""
    A body somebody wrote -- a concern, a task detail, a brief entry -- as the parts
    of one ``document``. **The one rule for whether such a body is markdown**; every
    pane that shows one calls this, so a body reads the same wherever it appears.

    Nothing promises a body is markdown. Agents mostly write it, and a worker
    without a moment to format writes ``key: value`` lines and pastes a traceback.
    Read as markdown those lines fold into one paragraph and the traceback loses
    the indentation that says which line belongs to which frame. So a body is read
    **a paragraph at a time**, and each paragraph is judged on its own:

    - **Markdown already** -- a paragraph with a fence, a list item, a heading, a
      quote or a table row in it, or one continuing a list -- is left exactly as
      written. An indented line under a list item is the item's continuation.
    - **Indented lines** that no list accounts for are a traceback or a snippet
      nobody fenced: the paragraph is set as written, monospace (``Kind.LITERAL``).
    - **Lines ended on purpose** keep their line breaks, as markdown hard breaks.
      Two tests. Every line of the paragraph is a field -- ``key: value``, the key
      one to three words -- or, line by line, the line stopped well short of the
      paragraph's measure (``_ENDED_EARLY``) for the next line's first word to
      have fitted -- or its first link or code span, which a writer carries over
      whole. The measure is the paragraph's second-longest line, so one line that
      could not be wrapped (a path, a command) does not make the rest look short;
      and it is never under ``_WRAP_FLOOR``.
    - Anything else is prose wrapped at a column, which is what markdown is for.

    A paragraph that keeps its breaks and shows no sign its writer meant markdown --
    no code span, no ``**``, no link -- has its punctuation escaped, so a ``*`` or a
    ``#`` in ``exit: 1`` lines stays what was typed.

    A body in which no paragraph needed anything is one markdown part holding the
    text untouched, which is the ordinary case and costs one pass over its lines.

    The failure in one direction is a line break more or fewer than was meant; in
    the other, prose in monospace. In neither is a character lost.

    ``colour`` is given to every part (``Part.colour``).
    """
    if not text.strip():
        return ()
    paragraphs = _paragraphs(text)
    shapes: list[tuple[str, list[str]]] = []
    in_list = False
    for lines, fenced in paragraphs:
        listed = bool(_BODY_LIST.match(lines[0]))
        in_list = listed or (in_list and lines[0].startswith(("  ", "\t")))
        shapes.append(("markdown", lines) if fenced or in_list else _shaped(lines))
    if all(shape == "markdown" for shape, _ in shapes):
        return (Part(text, colour=colour),)
    parts: list[Part] = []
    prose: list[str] = []
    for shape, lines in shapes:
        if shape == "exact":
            if prose:
                parts.append(Part("\n\n".join(prose), colour=colour))
                prose = []
            parts.append(Part("\n".join(lines), Kind.LITERAL, colour))
        else:
            prose.append("\n".join(lines))
    if prose:
        parts.append(Part("\n\n".join(prose), colour=colour))
    return tuple(parts)


def _paragraphs(text: str) -> list[tuple[list[str], bool]]:
    """
    The paragraphs of a body: runs of lines between blank ones, and whether each
    holds a fence. A fence keeps its blank lines and whatever touches it.
    """
    out: list[tuple[list[str], bool]] = []
    current: list[str] = []
    fenced = False
    closer = ""
    for line in text.split("\n"):
        fence = _BODY_FENCE.match(line)
        if closer:
            current.append(line)
            if fence and fence[1].startswith(closer) and not line.strip(" " + closer[0]):
                closer = ""
        elif fence:
            closer = fence[1]
            fenced = True
            current.append(line)
        elif line.strip():
            current.append(line)
        elif current:
            out.append((current, fenced))
            current, fenced = [], False
    if current:
        out.append((current, fenced))
    return out


def _shaped(lines: list[str]) -> tuple[str, list[str]]:
    """
    How a paragraph with no fence in it, outside any list, is to be set, and its
    lines as they should then be written: ``markdown``, ``exact`` or ``lines``.
    """
    if len(lines) < 2:
        return "markdown", lines
    indented = [n for n, line in enumerate(lines) if line.startswith(("  ", "\t"))]
    marked = [n for n, line in enumerate(lines) if _BODY_BLOCK.match(line)]
    if marked:
        # A list item, a heading, a quote or a table row: markdown's to read, and
        # an indented line after a list item is that item's continuation.
        listed = [n for n in marked if _BODY_LIST.match(lines[n])]
        if not indented or (listed and listed[0] < indented[0]):
            return "markdown", lines
    if indented:
        return "exact", lines
    if marked or any(_BODY_RULE.match(line) for line in lines):
        return "markdown", lines
    if all(_BODY_FIELD.match(line) for line in lines):
        breaks = set(range(len(lines) - 1))
    else:
        lengths = sorted(len(line.rstrip()) for line in lines)
        measure = max(_WRAP_FLOOR, lengths[-2] if len(lengths) > 2 else lengths[-1])
        if len(lengths) == 2:
            # Two lines give one line to judge and no second-longest to judge it
            # against: the first line's own length is the only evidence of a column.
            measure = max(_WRAP_FLOOR, len(lines[0].rstrip()))
        breaks = {
            n
            for n in range(len(lines) - 1)
            if len(lines[n].rstrip()) + 1 + _first_unit(lines[n + 1]) <= measure * _ENDED_EARLY
        }
    if not breaks:
        return "markdown", lines
    meant = bool(_BODY_INLINE.search("\n".join(lines)))
    out: list[str] = []
    for n, line in enumerate(lines):
        kept = line.rstrip() if meant else _BODY_PUNCTUATION.sub(r"\\\1", line.strip())
        # Two trailing spaces are markdown's hard line break.
        out.append(kept + "  " if n in breaks else kept)
    return "lines", out


def _first_unit(line: str) -> int:
    """
    The length of what a line starts with that could not have been split to fit the
    line above: a whole link or code span where it starts with one, else a word.
    """
    found = _BODY_UNIT.match(line.lstrip())
    return len(found[0]) if found else 0


# ---------------------------------------------------------------------------
# Streams
# ---------------------------------------------------------------------------


class Stream:
    """
    Markdown that is still arriving: ``append`` pieces, ``draw`` once a frame.

    Text already committed is laid out once; only the live tail is set again as it
    grows. The handle is stable -- ``stream(cache, key)`` returns the same one each
    frame -- and survives a change of face setting by replaying what it was fed.

    **Everything appended is in the view on the next draw.** Nothing is held back to
    see what follows it: the reader itself shows HTML as text (``_reader``), so a
    stream that stops after ``Done!`` shows ``Done!``. Two exceptions, both a matter
    of when and never of whether, and ``unbuilt`` counts both:

    - **More than a piece at once** -- a stream opened on a run that is already
      hundreds of lines, or replayed after a change of faces -- is read a piece a
      frame within the build budget, as a settled view is built, with the same
      line under it saying how much is to come. Read at once it was 105 ms for 200
      lines and 251 ms for 800.
    - **``every_ms``**, for a pane that would rather pay less per frame than see
      each token on the frame it arrived. Each reading lays the unfinished block
      out again -- about 3 ms of an 8 ms frame, measured, and nearly all of what
      a stream gives the collector to do -- so text arriving every frame costs
      that every frame. With ``every_ms=50`` it is read twenty times a second and
      what arrives between readings waits for the next. ``close`` reads it all.

    ``free_allowance=False`` reads a large text without the frame's free allowance,
    as ``document(free_allowance=False)`` builds one.

    ``follow`` makes the *view* keep the end of the scrolling child in sight until the
    reader scrolls up. It is off by default because a pane that already owns
    follow-the-tail (``widgets.follow_tail``) must stay the only owner: two would
    disagree on the frame one of them lets go.

    All of it on the drawing thread.
    """

    def __init__(
        self, *, follow: bool = False, every_ms: float = 0.0, free_allowance: bool = True
    ) -> None:
        _reader()
        self._follow = follow
        self._every = max(0.0, float(every_ms)) / 1000.0
        self._free = free_allowance
        self._source: list[str] = []
        self._closed = False
        self._dirty = True
        # Text taken and not yet read into the document, how many lines it is,
        # when the document was last given any, and whether it is to be closed
        # once it has all of it.
        self._held: list[str] = []
        self._held_lines = 0
        self._fed_at = -math.inf
        self._closing = False
        self._entry: _Entry | None = None
        self._document = _stream_class()(_theme("prose"), pixel_metrics)
        self._view = self._make_view()
        self._epoch = _STATE.epoch
        self._inked_for: object | None = None
        # Committed blocks already measured for their widest line, and the widest.
        self._examined = 0
        self._settled_extent = 0.0
        self._settled_replaced = 0

    def _make_view(self) -> StreamView:
        made: StreamView = _stream_view_class()(
            self._document,
            _STATE.measure,
            follow=self._follow,
            host_for=_Fences(DiffFences(metrics=pixel_metrics)),
        )
        return made

    @property
    def text(self) -> str:
        """
        Everything fed so far, as it was fed.
        """
        if len(self._source) > 1:
            self._source = ["".join(self._source)]
        return self._source[0] if self._source else ""

    @property
    def closed(self) -> bool:
        return self._closed

    @property
    def view(self) -> Any:
        """
        The library's view, for ``selected_text()`` and the rest. Replaced on a rebuild.
        """
        return self._view

    @property
    def following(self) -> bool:
        """
        Whether the view is keeping the end in sight, as of the last draw.
        """
        return bool(self._view.following)

    def follow_tail(self) -> None:
        """
        Go to the end and follow it again. Only meaningful with ``follow=True``.
        """
        self._view.follow_tail()

    def append(self, text: str) -> None:
        """
        Take the next piece. Any size, cut anywhere. Raises once closed.
        """
        if self._closed:
            raise ValueError("append() on a closed Stream; set_text() reopens one")
        if not text:
            return
        self._source.append(text)
        lines = text.count("\n")
        large = lines > _MARKDOWN_PIECE_LINES and math.isfinite(_STATE.budget_ms)
        if self._every or self._held or large:
            self._held.append(text)
            self._held_lines += lines
        else:
            self._document.append(text)
            self._dirty = True

    def close(self) -> None:
        """
        The text has ended: settle the last block, an open fence included. Idempotent.

        Text still held (``unbuilt``) is read first, over the frames that takes.
        """
        if self._closed:
            return
        self._closed = True
        if self._held:
            self._closing = True
        else:
            self._document.close()
            self._dirty = True

    @property
    def unbuilt(self) -> int:
        """
        How many lines taken by ``append`` the view does not show yet; 0 when it shows all.

        At least 1 while anything is held, a few characters included.
        """
        return self._held_lines + 1 if self._held else 0

    def _feed(self, im: Any) -> None:
        """
        Read held text into the document: all of it when it is short, else a piece at
        a time while the frame's budget lasts. Not before ``every_ms`` has passed
        since the last reading, unless the text has ended.
        """
        if not self._held:
            return
        frame = im.get_frame_count()
        now = im.get_time()
        if self._every and not self._closing and now - self._fed_at < self._every:
            _STATE.unfinished_in = frame
            return
        self._fed_at = now
        text = "".join(self._held)
        at = 0
        if self._held_lines <= _MARKDOWN_PIECE_LINES or not math.isfinite(_STATE.budget_ms):
            self._document.append(text)
            at = len(text)
        else:
            entry = self._entry
            rate = _STATE.layout_rate.get("stream", _STREAM_MS_A_LINE)
            while at < len(text) and _may_build(im, self._free):
                stop = at
                for _ in range(_MARKDOWN_PIECE_LINES):
                    found = text.find("\n", stop)
                    if found < 0:
                        stop = len(text)
                        break
                    stop = found + 1
                lines = max(text.count("\n", at, stop), 1)
                self._document.append(text[at:stop])
                # Parsed and laid out by this frame's draw; charged now at the
                # going rate and put right when the draw has been timed.
                _STATE.spent_ms += lines * rate
                _STATE.built_lines += lines
                if entry is not None:
                    entry.owed_ms += lines * rate
                    entry.owed_lines += lines
                    entry.owed_kind = "stream"
                at = stop
        rest = text[at:]
        self._held = [rest] if rest else []
        self._held_lines = rest.count("\n")
        self._dirty = True
        if self._held:
            _STATE.unfinished_in = frame
        elif self._closing:
            self._closing = False
            self._document.close()

    def set_text(self, text: str, *, closed: bool = False) -> None:
        """
        Make the stream hold exactly ``text``, appending only what is new when it can.

        For a caller that has the whole text so far each frame and not the pieces.
        When ``text`` extends what was fed, the difference is appended and nothing
        already laid out is touched; otherwise -- the text was replaced, or the stream
        was closed and has grown -- everything is replayed into a new document, which
        costs the selection.
        """
        have = self.text
        if self._closed and text == have and closed:
            return
        if self._closed or not text.startswith(have):
            self._replay(text)
        elif len(text) > len(have):
            self.append(text[len(have) :])
        if closed:
            self.close()

    def _replay(self, text: str) -> None:
        """
        Start again from a new document holding ``text``, open.
        """
        self._source = []
        self._closed = False
        self._held = []
        self._held_lines = 0
        self._closing = False
        self._document = _stream_class()(_theme("prose"), pixel_metrics)
        self._view = self._make_view()
        self._epoch = _STATE.epoch
        self._inked_for = None
        self._examined = 0
        self._settled_extent = 0.0
        self._settled_replaced = 0
        self._dirty = True
        if self._entry is not None:
            self._entry.view = self._view
            self._entry.epoch = self._epoch
            self._entry.width = self._entry.height = None
        self.append(text)

    def draw(self, *, width: float | None = None, ink: Color | None = None) -> float:
        """
        Take in what has arrived and draw, at the cursor. Returns the height claimed.

        Inside a scrolling child, as any view. Give that child
        ``always_vertical_scrollbar``, or pass a ``width`` that does not depend on the
        scrollbar: otherwise everything re-breaks on the frame the text first outgrows
        the pane. Give it ``horizontal_scrollbar`` too: a stream is never put in a
        child of its own (the child would appear mid-stream, and with ``follow`` the
        view scrolls the window it is drawn in), and its blocks are the library's to
        lay out, so a code line or an unbroken word wider than the pane is not
        broken here -- the stream only tells the window how wide it is. The same
        text as a ``Part`` or a ``markdown`` is broken to the window.
        """
        im = _frame()
        if self._epoch != _STATE.epoch:
            was_closed = self._closed
            self._replay(self.text)
            if was_closed:
                self.close()
        entry = self._entry
        if entry is None:
            entry = self._entry = _Entry("stream", None, self._epoch, self._view, stream=self)
        if not _begin(im, "stream", entry):
            return _twice(im, "stream")
        self._feed(im)
        dirty, self._dirty = self._dirty, False
        wide = _width(im, width)
        if dirty:
            self._measure()
        if entry.extent > wide:
            _claim_extent(im, entry.extent)
        tall = _place(im, entry, wide, palette(ink), dirty=dirty)
        if self._held_lines > _MARKDOWN_PIECE_LINES:
            tall += _waiting(im, entry)
        return tall

    def _measure(self) -> None:
        """
        Keep the entry's extent and its count of replaced characters up to date:
        committed blocks once, the live tail again.
        """
        entry = self._entry
        if entry is None:
            return
        # As dressed: an info string set as a line may be a block's widest.
        blocks = self._document.document.blocks
        settled = len(self._document.committed)
        current = ui_theme.current()
        if self._inked_for is not current:
            self._inked_for = current
            self._view.clear_colours()
            _caption_spans(self._view, blocks, 0, self._examined)
        fresh = blocks[self._examined : settled]
        self._settled_extent = max(self._settled_extent, _code_extent(fresh), _run_extent(fresh))
        self._settled_replaced += _replaced(fresh)
        _caption_spans(self._view, blocks, self._examined, len(blocks))
        self._examined = settled
        live = blocks[settled:]
        entry.extent = max(self._settled_extent, _code_extent(live), _run_extent(live))
        entry.replaced = self._settled_replaced + _replaced(live)


@cache
def _stream_class() -> type[Any]:
    """
    The document every stream is read into: the library's, its blocks dressed as a
    settled view's are (``_dressed``), so text reads the same arriving as arrived.

    Made on first use and not at import, so this module loads without the library.
    """

    class _Document(StreamDocument):  # type: ignore[misc, valid-type]
        _seam_read: Any = None
        _seam_made: Any = None
        _seam_memo: dict[int, tuple[Any, bool, Any]] | None = None

        @property
        def document(self) -> Any:
            read = super().document
            if read is not self._seam_read:
                if self._seam_memo is None:
                    self._seam_memo = {}
                blocks = _dressed(read.blocks, self._seam_memo)
                self._seam_made = (
                    read if blocks is read.blocks else Document(blocks=blocks, sources=read.sources)
                )
                self._seam_read = read
            return self._seam_made

    return _Document


def stream(
    cache: ViewCache,
    key: Hashable,
    *,
    follow: bool = False,
    every_ms: float = 0.0,
    free_allowance: bool = True,
) -> Stream:
    """
    The stream held under a key, made on first asking.

    ``follow``, ``every_ms`` and ``free_allowance`` are read when the stream is made.
    See ``Stream``.
    """
    entry = cache._entries.get(key)
    if entry is not None and entry.stream is not None:
        return entry.stream
    made = Stream(follow=follow, every_ms=every_ms, free_allowance=free_allowance)
    entry = cache._put(key, "stream", None, made._view)
    entry.stream = made
    made._entry = entry
    return made


# ---------------------------------------------------------------------------
# The same faces for a native ImGui line
# ---------------------------------------------------------------------------

_WEIGHT_FACE: dict[Weight, str] = {
    Weight.REGULAR: "PROSE",
    Weight.BOLD: "PROSE_BOLD",
    Weight.ITALIC: "PROSE_ITALIC",
    Weight.BOLD_ITALIC: "PROSE_BOLD_ITALIC",
    Weight.CODE: "CODE",
    Weight.CODE_BOLD: "CODE_BOLD",
}

_MONO_FACE: dict[Weight, str] = {
    Weight.REGULAR: "CODE",
    Weight.BOLD: "CODE_BOLD",
    Weight.ITALIC: "CODE",
    Weight.BOLD_ITALIC: "CODE_BOLD",
    Weight.CODE: "CODE",
    Weight.CODE_BOLD: "CODE_BOLD",
}
"""
The library's face for each weight, by name (``Face[name]``), where ``Role.LABEL``
is proportional and where it is monospace.
"""


def imfont(weight: Weight) -> imgui.ImFont | None:
    """
    The loaded ``ImFont`` for a weight, or ``None``, which ``push_font`` reads as "keep
    the current face".
    """
    fonts = _STATE.fonts
    if fonts is None:
        return None
    monospace = style(Role.LABEL).slot is Slot.MONOSPACE
    face = Face[(_MONO_FACE if monospace else _WEIGHT_FACE)[weight]]
    if face is Face.CODE_BOLD and not _bold_monospace():
        face = Face.CODE
    found: imgui.ImFont | None = fonts.fonts.get(face)
    return found


@contextmanager
def font(weight: Weight, size: float | None = None) -> Iterator[None]:
    """
    Push a typeset face for ordinary ImGui widgets: a table cell, a header line.

        with typeset.font(typeset.Weight.BOLD):
            imgui.text_unformatted(title)

    What a pane uses when the honest layout is a native table with cells in the
    document's faces. Text drawn this way is an ImGui item: not selectable, and
    wrapped by ImGui if at all.

    **A native line has one font and no fallback.** A typeset view borrows a
    character its face lacks from another face; ImGui does not. The prose faces
    (Roboto, Droid Sans) have no ``→``, ``✓`` or box-drawing characters, so under a
    proportional ``Role.LABEL`` those are drawn here as U+FFFD. Keep such characters out of
    a line set in a prose weight, or set the line in ``Weight.CODE``, whose face
    (Inconsolata) has them. Giving the prose faces those ranges means merging a
    second font into each when the library loads it, and the library's loader takes
    a path and offers no merge.
    """
    im = _frame()
    im.push_font(imfont(weight), px(Role.LABEL) if size is None else size)
    try:
        yield
    finally:
        im.pop_font()


def label(
    text: str,
    *,
    weight: Weight = Weight.BOLD,
    colour: Color | None = None,
    size: float | None = None,
) -> None:
    """
    One unwrapped line in a typeset face: a row title, a key in a key-value block.

    One font, no fallback: see ``font`` for which characters a prose weight cannot draw.
    """
    im = _imgui()
    with font(weight, size):
        if colour is None:
            im.text_unformatted(text)
        else:
            im.text_colored(colour.vec4, text)
