# A session can run in its own git worktree

**Dated:** 2026-10-07 · **Status:** scope for work not started; eight operator decisions, the
rest recommendation or open — nothing built except the probe script and, from 2026-10-10,
the operator's bootstrap script (D6) · **Found by:** operator
request — "multiple sessions/agent-teams can work independently on different features that could
normally be merged via normal git PRs" · **Sibling:**
[`2026-10-07-a-session-can-continue-under-a-different-mode.md`](2026-10-07-a-session-can-continue-under-a-different-mode.md),
which shares part of step 1 of the sequence below

The shape proposed: when the launch directory is a git checkout, the launcher can create a linked
worktree on a new branch and start the session in it. The session's cwd is the worktree, so
everything keyed on cwd — write region, brief, declaration units, rail lane — keeps working
without knowing a worktree is involved. The agent commits on its own branch; nothing pushes; the
operator lands the branch by an ordinary merge or PR. Whether pptmstr runs git itself, rather than
delegating to the CLI's `--worktree`, is a lead proposal (R3) and not an operator decision.

The decision that carries a cost is D2: the operator wants worktrees used for the autonomous mode,
where the autonomous record requires a clone, and a linked worktree is measurably not one.

**Three provenances, kept apart on purpose.** The tags match the sibling record's, and a section's
identifier carries the same letter as its tag.

- **(D)** — an operator decision. Each one quotes the operator's own words, from the launching
  session's transcript (2026-10-06 and 2026-10-07, local time), and **the decision is what the
  quoted words say and no more**. Reasoning, consequences and costs written under a (D) heading
  are the lead's or the builder's unless they are inside the quotation marks.
- **(R)** — a lead proposal. Not agreed. Most of them (R3, R4, R8, the `pptmstr/` branch prefix,
  the ref sensor, the autonomous-mode requirement, the sequence) were in a plan the lead put to
  the operator on 2026-10-07; the operator answered other items of that plan and did not address
  these. **Not objected to is not agreed**, and nothing tagged (R) is settled by appearing here,
  including where it sits inside a section whose heading is a (D).
- **(O)** — open. Options and costs are laid out and **no option is chosen**.

Untagged prose describes the code, an earlier record or a measurement, and carries an evidence
class instead.

**Evidence class.** Code citations were read on 2026-10-07 against `main` at `851978e` and are
given by symbol with the line as it stood. Measurements are from four probes, all on one host:
git 2.39.5, `claude-agent-sdk` 0.2.164, bundled CLI 2.1.292, Linux 6.1, ext4.

| Probe | Raw output | Re-runnable |
|---|---|---|
| git mechanics | `/tmp/pptmstr-wt-research/git-probe.txt` (cited as **G**) | `/tmp/pptmstr-wt-research/scripts/s*.sh` — not in the repo |
| SDK / CLI surface | `/tmp/pptmstr-wt-research/sdk-surface.txt` (**S**) | no; unauthenticated sessions, no model turn ran |
| containment, 4 runs | `/tmp/pptmstr-wt-research/containment-probe.txt` (**P**) | **`scripts/verify_worktree_containment.py`**, staged and uncommitted |
| two lead re-runs | the launching session's transcript only | commands quoted where cited |

**The `/tmp` files do not survive a reboot.** Every number this record leans on is written into
it for that reason, and `scripts/verify_worktree_containment.py` is the one instrument that can
reproduce its share of them. The git probe's scripts are not in the repository; if its numbers
need re-deriving, the scripts have to be rewritten or copied in first.

Two independent reviews corrected the builders' first claims. Where they disagree with a probe
note this record uses the review's narrower statement, and says so.

---

## D1 — One worktree per session (D)

The operator's words, opening the research: "The idea is that multiple sessions/agent-teams can
work independently on different features that could normally be merged via normal git PRs (or
merges)." And, once the research and a plan had been presented: "Let's output the planning for
these two features."

A team is a session here: a board is scoped by session id, and a sub-agent inherits its parent's
cwd in the reducer (`store._apply`, `store.py:208`) and is measured against the session's one
region (`driver._write_region` docstring, `driver.py:1638-1642`). There is no per-agent cwd
anywhere, so "per session" and "per team" are one granularity.

**The recorded objections are to a different granularity, and they stand.**

- [`2026-09-03-orca-made-the-opposite-bet-on-the-same-tradeoff.md`](2026-09-03-orca-made-the-opposite-bet-on-the-same-tradeoff.md)
  l.240-243: "Copying Orca wholesale — a worktree per task — would undercut `touches`, which
  exists *because* agents share a tree; that trades a declaration for filesystem separation and
  pays for a mechanism whose problem it just deleted."
- [`2026-09-03-a-dangerously-autonomous-mode.md`](2026-09-03-a-dangerously-autonomous-mode.md)
  §2 l.71-74: "`AgentRecord.cwd` per node, or a worktree per node, is the only clean answer, and
  worktree-per-node is a real design with a real cost."

Both are about agents *inside one board*. A worktree per session leaves the shared tree, and
therefore `touches`, intact inside each session. What it addresses is the case the code itself
names as unsolved — `store._auto_depends` (`store.py:748-750`): "**two sessions in one working
directory get no protection from this**, and that is a real gap rather than a solved case."

**What this does not buy, so it is not later assumed.** Per-node write attribution (§2's
problem) is untouched. A stable tree for the gate run
([`2026-08-15-what-the-board-does-not-carry.md`](2026-08-15-what-the-board-does-not-carry.md)
l.468-471) is not delivered either: a team still writes concurrently inside its worktree. And the
collision between two sessions does not disappear, it moves from the write to the merge, where
nothing on any board represents it.

## D2 — Worktrees are to be used for the autonomous mode (D)

The operator's words: "I want to use worktrees for fully autonomous. What's the concern?"

The autonomous record's §8 says "The clone must be mandatory or this is not containment" (l.307)
and its §12 says the clone "is still not built, and is still what gates shipping" (l.1321). That
the operator's sentence means a linked worktree stands in for that clone is the lead's reading of
it; the operator had asked earlier, as a question, "The worktree _should_ count as a clone, no?"

**When it was said, because that is all this record can say about what it accepts.** The sentence
came after the lead had presented the results of all four probe runs — the index, `HEAD`, the
rebase state, ref and object deletion and the second worktree among them — together with the
lead's own recommendation to keep a real clone for the autonomous mode. The lead then stated the
concern, called it "a reasonable risk to accept, and it's your call", and the operator went on to
answer design questions about the feature (D5, D6, D7) without withdrawing the sentence. It was
stated after the results were presented. It is **not an itemised acceptance** of each risk listed
below, and the operator has not been asked to give one.

The measured difference from a clone is this:

### What was measured

Contained sessions (`sandbox.containment_settings()` via `ClaudeAgentOptions.settings`, `sandbox`
left `None`, `permission_mode="dontAsk"`), model `claude-haiku-4-5`. Arm W: cwd is a *sibling*
linked worktree `W` of repo `R`. Arm C: cwd is an independent `git clone` `C` of `R`. Every outcome
is the script's own host-side read after the session, not the model's account; a host leg ran the
same command strings unsandboxed in a twin fixture and every one succeeded, so a denial is the
sandbox or the CLI and not the command. Total spend 0.6755 USD over ten sessions.

From W, through `Bash`, a contained session:

| Did | n | Evidence (P line) |
|---|---|---|
| committed; branch visible in `R` at the new commit | 4 | 186, 482, 780, 1332 |
| moved `refs/heads/main`, which `R` has checked out (`git update-ref`) | 3 | 193, 489, 1339 |
| rewrote `R/.git/HEAD` to another branch (`git --git-dir=… symbolic-ref`) | 2 | 195, 1341 |
| created an arbitrary file at the top of `R/.git` | 2, plus 1 by a second spelling of the path | 190, 1336; 1325 |
| changed `R/.git/index` (R's own staging area; mtime set, same inode) | 1 | 1313 |
| created `R/.git/rebase-merge/` | 1 | 1315 |
| deleted a branch ref in `R` (`update-ref -d`) | 1 | 1323 |
| deleted a loose object in `R/.git/objects` | 1 | 1324 |
| wrote a file into another worktree's admin dir, `R/.git/worktrees/W2/` | 1 | 1316 |
| moved the branch a second worktree `W2` has checked out; W2 went clean → `M  tracked.txt` | 1 | 1317-1318 |

From W, denied:

| Denied | How | n |
|---|---|---|
| `R/.git/hooks/pre-commit` (path via command substitution) | `Read-only file system` | 4 |
| direct append to `R/.git/config` | `Read-only file system`; file 303 bytes before and after | 1 |
| direct append to `R/.git/worktrees/W/config.worktree` | `Read-only file system` | 1 |
| `git config probe.key x` | `could not lock config file …: File exists` — `/dev/null` is bound over `config.lock` inside the sandbox | 3 |
| a file in `R`'s working tree | `Read-only file system` | 3 |
| push to a local bare remote | `remote unpack failed` | 2 |
| `git ls-remote https://github.com/…` | exit 128, `deny network-outbound github.com:443` | 2 |

From C the same hooks, config, working-tree, push and network denials hold, and **nothing in `R`
was reached**: C's commit, ref moves and deletions landed in `C/.git` only; a direct `touch` into
`R/.git` and into `R/.git/worktrees/W2/` failed `Read-only file system` (n=1 each, P 1481, 1486);
a push from C to `R` failed (n=2).

The task this record was written from gave n=3, 2 and 1 for the first three rows. Those counts
predate run 4, which repeated all three; the table has the counts as P shows them.

### The accepted risk, in those terms

1. **The autonomous record's premise does not hold for a linked-worktree cwd.** "The sandbox's
   writable region *is* cwd" (l.307, l.733, l.1321; restated in `tree.lies_inside_checkout`,
   `tree.py:112`, `launcher.refusal_for`, `launcher.py:363`, and `driver._write_region`,
   `driver.py:1624-1626`). On CLI 2.1.292, for a linked-worktree cwd, the region `Bash` can write
   is cwd **plus the main repository's git directory, minus `hooks/`, `config` and
   `config.worktree`**. The four write tools are still bounded to cwd by
   `driver._escapes_write_region`, so parity between the two layers breaks, in the direction of
   the gate being the narrower.
2. **A contained session in a worktree can alter the operator's checkout without touching its
   files.** Moving the checked-out branch or `HEAD` makes the operator's next `git status` in the
   main checkout report changes nobody made there, and an ordinary `commit -a`, `reset` or
   `checkout` then acts on that.
3. **It can reach sibling sessions' worktrees through the shared directory** — their branch and
   their admin dir (n=1 each). Per-session worktrees isolate working files; they do not isolate
   sessions from each other in git.
4. **It can destroy.** Refs and objects are shared and deletable. In a clone a session can destroy
   only its own copy.
5. **No act of the operator's stands between a session's commit and the operator's repository.** A
   clone's commits stay in the clone until someone fetches; a worktree's are in `R` as they
   happen.

What a worktree does do that the clone was wanted for: the main working tree is out of reach of
`Bash` (n=3) and of the write tools (by the gate; read-derived, and the CLI alone does **not**
stop it — with a hook that allowed everything, `Write` to `R/write-tool-escape.txt` landed, n=2,
P 640, 922); and hooks and config, the two routes into the operator's own unsandboxed git, were
denied at the paths tried. §8 l.313-315 also asks that the boundary be the mode's own action
rather than a typed directory; a worktree delivers that only if pptmstr creates it and the mode
requires it, and both of those are (R) — R3 and "Invoke".

**(R) Detection rather than prevention.** The lead's proposed answer to the above is the ref
sensor in "Land" below. Not agreed, and the operator was not asked to choose between detecting
and preventing. It does not see the index, `HEAD`, object deletion or stray files; see O3.

### What was not tested

Named paths were tested, not the boundary. Not attempted, from either review or the second-pass
builder's own list:

- `packed-refs`, `info/` (`exclude`, `attributes`), `logs/`, `objects/info/alternates`;
- planting a rebase todo — `rebase-merge/` was created, no `git-rebase-todo` with `exec` lines was
  written into it, and that route needs neither hooks nor config;
- clobbering `W2`'s admin `HEAD` or `index` (a marker file was written beside them);
- `R/.git/modules/<sub>/hooks` and `config` — no fixture had a submodule;
- a **nested** worktree (inside `R`), which is where the CLI's own `--worktree` puts them and
  where `.claude/` is a protected path;
- `merge`, `rebase`, `checkout`, `stash`, `fetch` under containment — only `add <path>` and
  `commit` ran, and autonomous §8b.5 names merge and checkout as the ones upstream says can fail;
- sub-agents; any repo with hooks, signing, `extensions.worktreeConfig`, or a relative
  `core.hooksPath` (which resolves inside each worktree's own top-level, G 1015-1021, and so
  inside the session-writable tree);
- `Bash` rewriting `W/.git` (the pointer file, inside cwd) or `R/.git/worktrees/W/commondir`.
  The `Write` tool was refused the pointer file by the CLI (n=2). Neither `Bash` route was tried,
  and either would let a session choose which `config` a later git command in `W` reads;
- anything on another CLI version. Where the CLI decides to grant the common dir was inferred from
  outcomes and not read, so whether it is deliberate or stable is unknown.

A real worktree would sit under `$HOME`; every fixture sat under `/tmp`.

## D3 — pptmstr owns the git calls and hands the CLI the worktree path as cwd (D)

Not the CLI's `--worktree`, reachable only as `extra_args={"worktree": name}` (no typed field;
confirmed working in SDK mode, S 1308-1311). The reasons, each from the SDK probe:

- **Location.** `<main repo>/.claude/worktrees/<name>`, nested in the checkout (S 1160-1186) —
  the placement D4 rules out — and launched from inside another linked worktree it still lands
  under the *main* checkout (n=1).
- **Base.** By source and docs the default is `origin/<default branch>`, falling back to local
  `HEAD`; `worktree.baseRef` chooses between those two and no arbitrary ref can be named. **Only
  the fallback was observed** — the probe repo had no remote and every run logged "fetch of
  origin/main failed — basing worktree on local HEAD" (S 1214). With a remote, a session's
  worktree would not contain the operator's unpushed commits, and each launch may run a network
  fetch into shared refs.
- **Branch.** Fixed prefix, `worktree-<name>`.
- **No teardown in SDK mode.** Eight of eight CLI-made worktrees persisted after their sessions,
  and the docs say non-interactive runs are not cleaned up. Narrower than first reported: the lock
  the CLI takes is released by the *next* CLI process in the repo about five seconds after it
  starts, so a CLI lock is not a durable ownership marker. All eight were unauthenticated,
  zero-change sessions.
- **pptmstr's cwd would stop being the CLI's cwd.** `_write_region` deliberately uses the value
  the operator set at launch and refuses the hook's (`driver.py:1644-1647`); `session_base`, the
  brief slug, the rail lane and `tag_session` all read the same value. Delegating moves the
  session out from under all five at once.

**If both own it.** A host-made worktree plus `--worktree` nests a second, CLI-made worktree under
the main checkout (n=1); a reused name silently joins the existing worktree as a "guest" rather
than failing (n=1); and `.claude.json` keeps one `activeWorktreeSession` slot per main checkout,
last writer wins (transcribed from tool output, not tee'd — the weakest evidence in S). So nothing
on a pptmstr launch path may pass `--worktree`.

**What owning it costs.** The transcript carries no worktree binding. A CLI-made worktree session
resumed from the main checkout re-enters its worktree; a host-made one **runs in whatever cwd the
resume was launched with** and keeps appending to the original transcript (S 1377-1388; n=1 each
for main checkout and an unrelated non-repo directory; unauthenticated, so `init` cwd was observed
and no tool call was). That is step 1 of the sequence. Docs say this needs CLI ≥ 2.1.223;
`pyproject.toml` floors the SDK at 0.2.158 and nobody checked which CLI that bundles.

An undocumented `CLAUDE_CODE_HOST_WORKTREE` / `_FENCE` pair exists in the binary and looks built
for this arrangement. It appears in no fetched doc and was never observed acting. A lead, not a
finding.

## D4 — Placement: `~/.pptmstr/worktrees/<repo>/<slug>` (D)

**Rewritten 2026-10-15.** Decided: outside the repository, under one home for every repository's
worktrees, `<repo>` the main checkout's directory name and `<slug>` named from the feature
branch; slug restricted to `[A-Za-z0-9-]` and unique *after* that restriction. The operator, on
2026-10-15: "If a worktree home off of an apps config path works for Claude it should work for
us. Full stop." This is the convention
`notes/2026-10-07-a-linked-worktree-as-the-clone-substitute.md` proposed the same day as the
first version of this decision, and the tree that note records,
`~/.pptmstr/worktrees/pptmstr/adv-text-experiment`, is at it.

- **Not nested.** A session launched at the repo root has the nested worktree inside its write
  region and can write into another session's tree. On the git side the task said "`git clean` in
  main removes it"; **the probe shows something narrower.** `git clean -ndx` reports "Would skip
  repository"; only the doubled force, `git clean -ndffx`, reports "Would remove" (G 836-842, dry
  runs, n=1). What a nested worktree does do unprompted is show as untracked in the main
  checkout's `git status`, and `git add -A` there warns "adding embedded git repository"
  (G 814-834). It is also refused by `launcher.refusal_for` for pptmstr's own repository, which
  is how the CLI's `.claude/worktrees/<name>` met this on 2026-10-15: an autonomous launch into
  one was refused (run 2026-10-15, `refusal_for` on the nested path and on both outside
  placements: refused, allowed, allowed).
- **Unique after restriction.** `feat-x`, `feat.x`, `feat_x` sanitise to one transcript directory
  (run 2026-10-07: `_sanitize_path` equal for all three). Uniqueness has to be checked on the
  sanitised form, not on the branch name. `worktree.collision` and `preflight` do.

**What the first version got wrong.** It required "no dots anywhere in the path", and that rule's
only ground was a defect of our own: `brief.session_dir` turned only the path separator into `-`
where the CLI turns every non-alphanumeric, so on a dotted path a session's briefs were filed
apart from its transcripts
([`2026-09-21-what-is-left-on-the-gate-policy-branch.md`](2026-09-21-what-is-left-on-the-gate-policy-branch.md)
l.115-120). The record called that defect unfixed and placed around it, and its parent path,
`<parent of repo>/<repo>-worktrees/<slug>`, was a lead proposal nothing depended on beyond
"sibling, dotless". The defect is now gone at its cause rather than avoided:
briefs are keyed by session id under `<config>/briefs/<id>/`
([`archive/2026-10-15-a-brief-is-keyed-by-its-session.md`](archive/2026-10-15-a-brief-is-keyed-by-its-session.md)),
so no path a session runs in has a character that can part its brief from anything.

**Built 2026-10-15.** `worktree.place` (no caller yet) and `scripts/worktree.py`'s
`worktree_path`; the `unsanitary` warning both carried, which existed only for the brief defect, is
gone. **Accepted:** two repositories with one directory name share `<repo>`, so one slug in
each is one path; `preflight` and `scripts/worktree.py create` refuse a path that exists, which is
where that shows. **Not measured:** the sandbox with a cwd under a dotted directory in `$HOME`.
Every D2 fixture sat under `/tmp`, and D2's arms have not been re-run at this placement.

## D5 — The rail lane is the feature-branch name, with no new "repo" fact (D)

Verified: `projects._derive` (`projects.py:54-64`) is the basename of `tree.session_base(cwd)`,
and `session_base` (`tree.py:46-70`) is the resolved cwd with "nothing inferred from it". A session
in `…/pptmstr-worktrees/feat-a` therefore files under lane `feat-a` with no code change (run
2026-10-07). `tests/test_projects.py::test_a_worktree_names_itself` already pins that a directory
whose `.git` is a file names itself.

**Accepted for now:** `group_roots` (`projects.py:86-89`) keys on that string alone, so the same
branch slug in two repositories shares a lane, and a worktree session is never grouped with its
repository's other sessions.

Grouping under the parent repository would need a new launch-resolved fact, because
`_derive`'s docstring requires lane and units to come from one resolution. That is the decision
"Records this one corrects" flags as living only in a docstring; D5 avoids reopening it.

## D6 — Bootstrapping a fresh worktree is the operator's or an agent's job (D)

A new worktree is a checkout of committed files: no ignored files (`.venv`, `node_modules`,
`.env`), no untracked files, no submodule contents, and none of the launching checkout's
uncommitted changes (G 683-705, G 871-996). There are too many project shapes to predict what
"set up" means, so pptmstr does not try. **It shows a notice that the worktree may need it.**

**(R) Derive the notice project-agnostically:** list ignored paths present in the launching
checkout and absent from the worktree. Not agreed; not prototyped.

**The constraint that orders this against containment.** Under the autonomous mode the network
allowlist has one entry (`sandbox.ALLOWED_DOMAIN`, `sandbox.py:40`), so a contained agent
**probably cannot install dependencies. That is inferred** from the allowlist and from the measured
denial of `github.com:443`; no install was attempted. It is why setup precedes containment, and
the sibling record is about making that sequence one action.

One thing the CLI already does that removes part of the need: the main checkout's
`.claude/settings.local.json` is read from any linked worktree and wins over a local copy (n=1,
one scalar key), and auto-memory is keyed on the main repository, so worktrees share it.

**Amended 2026-10-10 — the operator's own bootstrap borrows an environment (D).** The operator:
"Ideally composite would be fine in _almost_ all instances since worktree forking _should_ be off
of main or a shallow stack of work." `scripts/worktree.py` (`make worktree BRANCH=…`) is that, for
this repository and run by hand: a worktree at D4's placement whose `.venv` holds no packages and
names the launching checkout's site-packages in one `.pth` file. It is the operator's tool and not
the launcher's — nothing in `pptmstr/` calls it, and D3, the notice and the (R) derivation above
are untouched by it.

The instance outside "almost" is a branch that changes its dependencies: it would be tested
against the source's, with nothing to say so. `worktree.py check` compares the dependency tables
of the two `pyproject.toml` files and fails on a difference; `make`'s `venv-check` runs it. Run on
2026-10-10: the guard fired on an edited requirement, a worktree made from a worktree imported its
own tree and linted, and `make check` passed in `enhanced-sessions` on an environment the script
built. Not run: `make run` or any window from a borrowed environment, and any session, contained
or not, launched into one. What borrowing costs otherwise is in the script's docstring.

`enhanced-sessions` was the first tree the script made, `…/pptmstr-worktrees/enhanced-sessions`,
on a branch with no prefix. That is D4's first placement; the script has made trees at
`~/.pptmstr/worktrees/<repo>/<slug>` since D4 was rewritten on 2026-10-15, and the tree was left
where it is.

## D7 — Fork is left exactly as it is (D)

`LaunchSpec.from_record` (`model.py:500`, `cwd=record.cwd` at l.513) carries the cwd, so a fork of
a worktree session shares its worktree — two sessions in one tree, which is `store.py:748`'s gap
again. The operator's view is that forking is partly broken already and wants a real modal. Out of
scope here.

## D8 — Removal is an operator action only (D)

Never automatic, at any trigger: not at session end, not at launch, not on a sweep. Removal
destroys uncommitted and untracked work, and — measured — more than git's own refusal suggests;
see "Clean up".

---

## Open questions

### O1 — The base ref (O)

The operator asked for pros and cons and has not chosen.

| Option | For | Against |
|---|---|---|
| Local `HEAD` of the launching checkout | No network. The session starts from exactly what the operator is looking at, minus uncommitted work. | Whatever branch happens to be checked out becomes the base, silently. Launching from inside another worktree makes "the launching checkout" ambiguous. |
| Local default-branch tip | Predictable; a feature branch off `main` is the common PR shape. | "Default branch" has no local definition without a remote (`origin/HEAD`) or a guess. A stale local `main` gives a stale base with no warning. |
| `origin/<default>` after a fetch | Freshest base; smallest eventual PR diff. What the CLI does by default. | Network on the launch path. Does not contain unpushed local commits. Concurrent fetches from several worktrees: 1 of 6 succeeded and 5 exited 1 with "cannot lock ref" (30 of 36 failures over six rounds, end state correct, G 2163-2184) — the exit code reports harm that did not happen. Under AUTONOMOUS only pptmstr could fetch; the session cannot. |
| A ref field, prefilled with the current branch | The base is on screen and editable; covers the other three by typing. | One more field on a modal that already has several. A prefill is a default, and defaults get launched unread. |

**(R)** The lead recommends the last, with **no fetch ever** and a **warning when the launching
checkout is dirty**, since uncommitted changes are not carried. Not agreed.

### O2 — `git add -A` fails in every contained session (O)

The sandbox's deny stubs appear in cwd as untracked non-regular files (`.bash_profile`, `.bashrc`,
`.gitconfig`, `.gitmodules`, `.mcp.json`, … twelve names, P 1243). `git add -A` aborts on the
first — "can only add regular files, symbolic links or git-directories / fatal: adding files
failed" — and stages nothing. Measured in **both** arms, n=1 each (P 1243, 1466). `git add <path>`
and `commit` were unaffected (n=4). The stubs do not exist on the host afterwards and were not
committed.

This is a general autonomous-mode defect and not a worktree one, but a feature whose point is
"the agent commits its branch" runs straight into it. Options, none chosen: fix at the root (an
exclude for the stub names — but `info/exclude` is shared across worktrees, G 1071, and whether a
contained session or pptmstr should write it is its own question); tell agents to add by path;
leave it and let the failure teach. `commit -a` was not measured.

### O3 — What the operator wants measured before build (O)

Anything from D2's untested list. The three with the most bearing on the design as scoped:

- **The pointer-file and `commondir` routes.** "Land" and "Clean up" have pptmstr running git,
  unsandboxed, as the operator, against a tree an unattended agent has been writing. pptmstr runs
  no git today — `subprocess` appears in the package only in `cli_version.py`. If a session can
  redirect which `config` those commands read, pptmstr's own summary is an execution path. Config
  and hooks in `R/.git` were measured denied; these two redirections were not tried.
- **The rebase-todo plant**, which executes on the operator's next `git rebase --continue` in the
  main checkout and which the ref sensor cannot see.
- **`merge`/`rebase`/`checkout` under containment**, since a session that cannot rebase its own
  branch changes what "lands by an ordinary PR" asks of the operator.

### O4 — How a failed worktree requirement ends a launch (O)

Raised while writing this record; not in the task. See "Invoke": the two existing refusals end
differently — a refused directory downgrades to `STRICT`, a CLI below the floor holds the launch —
and the new requirement has to pick one, or offer creation in place. Not chosen.

---

## Scope

Everything in this section is **(R)** unless a line says otherwise.

### Invoke

**Detection** is one call, off the draw thread:

```
git rev-parse --path-format=absolute --is-inside-work-tree --show-toplevel --git-dir --git-common-dir
```

`git-dir == git-common-dir` is the main checkout; different is a linked worktree; the shared root
is read from the common dir. Cost 3-4 ms warm (five runs, G 2413-2414; ten runs in 33 ms,
G 400-404). Cold cache was not measured. What it mislabels:

- **Exit 128 covers three situations**: not a repository, inside a `.git` directory, and a bare
  repository (G 114-119, 148-150, 172-177). Stdout separates them — in a bare repo this exact form
  prints `false` before the fatal (lead re-run, n=1), a non-repo prints nothing.
- **A submodule checkout reads as "main"** (G 991-993).
- **An inherited `GIT_DIR` makes a non-repo report true** against someone else's repository
  (G 384-386).
- `dirname(common-dir)` is not a checkout for a bare repo's worktree (G 204-206).

**Launcher.** A worktree control with "create" and "use existing"; a branch field defaulted from
the session name; the base per O1. Branch prefix `pptmstr/`, which is also what cleanup filters
on.

**Under AUTONOMOUS in a git directory, the requirement is "cwd is a linked worktree"** — created
by this launch or already one. This is D2 applied. Its relation to `launcher.refusal_for`
(`launcher.py:359-383`), having read it:

- `refusal_for` asks one question — is the resolved cwd inside pptmstr's own checkout
  (`tree.lies_inside_checkout` against `OWN_CHECKOUT`) — and the autonomous record says of it
  "**not** the resolution… closes nothing else" (l.1323-1326). The worktree requirement is the
  wider rule that sentence was waiting for, and it is a second predicate, not a change to the
  first.
- The two do not compose by themselves. A sibling worktree of pptmstr's own repository is outside
  the checkout by path, so `refusal_for` permits it (run 2026-10-07: `None` for
  `/home/wreel/Source/pptmstr-worktrees/x`). With D2 that is the intended answer — the files are a
  separate copy of `approval.py` — but the git directory is not separate, and D2's table is what
  an agent there can do to pptmstr's own repository.
- A refusal from `refusal_for` does not stop the launch: `granted_policy` (`launcher.py:686-698`)
  falls back to `STRICT`. A CLI below the floor does: `launch_hold` (`launcher.py:731`). Which of
  the two the worktree requirement follows is O4.
- A directory that is not a git checkout is not covered by this requirement at all and stays as
  §12 leaves it: writable in full under the tick, no clone, no snapshot.

**Creation** goes in the launch path, before `AgentSession` is constructed. Verified ordering in
`app._launch.go` (`app.py:500-565`): `AgentSession(cwd=…, session_base=…)` at l.522, then
`_seed_brief` (slug from cwd) at l.557, then `pool.submit` at l.565, which announces. Three things
read cwd before any subprocess exists, the same shape
[`2026-08-31`](2026-08-31-sessions-can-be-resumed-and-bookmarked.md) D2 records for the session
id. Two consequences:

- The *path* must be final when `LauncherState.spec` builds the `LaunchSpec`, because `spec`
  resolves `cwd` and `session_base` there (`launcher.py:786`, `:819`) — on the draw thread, before
  the directory exists. `tree._resolved` tolerates a missing component by design. The *directory*
  is created in `go()`, which already awaits one blocking subprocess off both hot threads
  (`cli_version.check_installed_cli`, `app.py:518`).
- `SessionPool` queues over cap, so a worktree created at launch can exist well before its session
  runs.

Pre-flights instead of stderr matching: `git check-ref-format --branch`, `git show-ref --verify`,
and the path being absent (G 762-781). Child environment: `LC_ALL=C`, `GIT_DIR` unset.

**A failure must be visible in the launcher with the draft kept — and today it would be neither.**
Verified: the modal calls `launch(state.spec())` and on the same frame clears the task, the
policy, the cap and the pick, and closes (`launcher.py:1273-1295`); `_launch` hands a coroutine to
the bridge and returns. The one refusal that exists on that path is `LOG.error(...)` and `return`
(`app.py:518-521`). A failed `git worktree add` placed beside it would be a log line under a
closed modal with the draft gone. Making a launch report back is new plumbing and is in step 1
because resume needs it too.

### Git traps to design for

All git 2.39.5.

| Trap | Evidence |
|---|---|
| **A failed `worktree add -b` can leave the branch behind** — when the failure comes after branch creation: path exists, is a file, is non-empty, parent unwritable, or path is missing-but-registered. A bad base ref or bad name leaves none. The retry then dies "a branch named X already exists", masking the first cause. | G 605-611, 628-642, 860-866 |
| **SIGKILL of the parent does not stop the checkout.** `kill -9` at 30 ms: 80 files at the kill, 20,001 one second later, entry left `locked initializing` with a clean status. That is also what a healthy in-flight add looks like, so porcelain cannot tell dead from in progress; `prune` skips it and only `remove -f -f` or unlock-then-force clears it. | lead re-run, n=1, in the session transcript only; the probe's own kill runs (G 1415-1585) were contaminated by this and are superseded |
| **Exit codes do not partition causes and messages are localised.** "Already checked out" is 128, "branch exists" 255, bad base ref 255 with `-b` and 128 without. `add` has no `--porcelain`. With `LANGUAGE=de` the refusal is German. | G 522-564, 729-757 |
| **Plain `worktree remove` deletes ignored files.** Modified, staged and untracked-not-ignored block it; an ignored directory and file went with rc 0. A session's `.venv` or `.env` is gone on an ordinary clean remove. | G 1146-1170, n=1 |
| **`gc --prune=now` under concurrent commits corrupts the repository** — refs left at deleted objects, "could not parse HEAD". 4 of 4 loops; one was preceded by a reflog expiry (a confound), three were clean, one of those had commits in the main checkout only, so it is the prune race and not worktrees — worktrees make concurrent writers the normal state. Default `gc` under the same load: fsck clean, **one run**, with four "cannot lock ref" errors and exit 0. Auto-gc: inconclusive, never fired. | G 1795-1820, 2042-2153, 2208 |
| **Config writes and fetches collide across worktrees.** One `config` file, one lock: 102 of 200 concurrent `git config` writes failed. Fetch: above, O1. `worktree add <path> origin/<branch>` writes config and failed the same way once. | G 1711-1741, 2163-2184 |
| **`update-ref` moves a branch another worktree has checked out, without complaint**; `checkout --ignore-other-worktrees` and `worktree add --force` bypass the one-branch-one-worktree guard. | G 2289-2296 |

**Timings, warm cache only** (192-file clone of this repository): `worktree add -b` 67-106 ms
(n=10); `remove` 22-60 ms (n=10); `worktree list --porcelain -z` 4-7 ms with eleven worktrees;
`status` 5-25 ms. A 20,000-file repo: add 0.94 s and 1.81 s across two runs, remove 0.87 s. A
`post-checkout` hook that sleeps two seconds makes `add` take 2.09 s — hooks are shared, so the
operator's hooks run on pptmstr's launch path. Cold cache, NFS, macOS, sparse checkout, LFS and
partial clones were not measured.

### Use

The session's cwd is the worktree, so region, brief and `touches` units are unchanged — consistent
as long as worktree root == cwd == `session_base`, which the launcher's `spec` guarantees.

**Resume must launch with the session's recorded cwd, and refuse visibly when the worktree is
gone. This is a live defect today for hand-made worktrees.** Verified:

- `scan_sessions` (`launcher.py:97-119`) lists through the SDK's worktree fold on purpose, and the
  picker captions itself "and its git worktrees" (l.912). So a worktree's sessions are offered
  from the main checkout.
- `LauncherState.spec` takes `cwd` from the text field (l.786) and `resume` from the pick (l.822).
  `SessionRow.cwd` is read nowhere in the launcher; `SessionPicker.select` (l.309-326) is passed
  the *launch's* cwd by design.
- So picking a worktree session from the default launcher launches `resume=<id>` with cwd = the
  main checkout. The identity check compares ids only. Measured on 0.2.164 / 2.1.292: the CLI
  accepts it and runs in the launch cwd (D3). The agent continues in the wrong tree, `select`
  derives a different brief slug, and `_seed_brief` writes a fresh brief under it —
  `2026-08-31` D6's guarantee, silently broken.
- `scan_sessions`' docstring says of the two filters "This repo has no worktrees, so the two have
  never been observed to disagree" (l.109-111). They now have been: 5 rows against 1 (S 1391-1398),
  and as of 2026-10-07 this repo has a worktree.

Two further measured facts the fix has to hold: the fold follows git's *current* registration, so
once a worktree is removed its sessions drop out of the directory-scoped listing while the
transcripts stay on disk (S 1498 vs 1391-1396; still reachable with `directory=<removed path>` or
`None`); and resuming a session whose worktree is gone continues in the launch cwd with only a
stderr notice. The recovery picker therefore loses exactly the finished-feature sessions, and D8
means that is the operator's doing — the removal confirmation should say so.

This is **step 1**, and it is shared with the sibling record, whose O1 is the same defect met from
the other side.

### Land

- The agent commits on its own branch. Under AUTONOMOUS that works for `add <path>` and `commit`
  (n=4) and see O2. Under `STRICT` and `PERMISSIVE` every mutating git command parks at the
  operator — `shellscan._GIT_READ_SUBCOMMANDS` (`shellscan.py:386`) is eight read-only
  subcommands.
- **Nothing pushes.** Denied by design: the allowlist "denies `git push`" (autonomous §8 l.276) and
  `sandbox.py:108` declines `excludedCommands` for git. Measured: a push to a local bare remote
  failed too (n=2). Landing is the operator's merge or PR from the main checkout.
- **End-of-session summary:** branch, ahead count against the base, dirty state, and a conflict
  pre-check with `git merge-tree --write-tree`, which touches no working tree (G 2383-2391, ran on
  2.39.5; "needs git ≥ 2.38" is the probe author's recall and was not checked against release
  notes). The transcript's own `gitBranch` field is not a source: it read `main` for the first
  fourteen records of a worktree session (S 1286).
- **The ref sensor** — the answer to D2's accepted risk. Snapshot `git for-each-ref` at launch and
  at session end; flag any ref that moved, appeared or vanished outside the session's own branch.

**How the sensor relates to the autonomous record's git sensor, having read it.** They share a
name and little else. §2's sensor measures *working-tree writes* — `git status` and
`git diff --numstat` against cwd — to compare with declared `touches`, sampled between tool calls
and attributed per node. The ref sensor measures *refs*, twice per session, attributed to nobody.
Two things do carry over: §2's placement rule ("Running git is IO… it must not go on the frame
path", l.79-81), and its warning that the trigger must not assume a clean close. What does not
exist to build on: §11 says `tree.py:measure_writes` is built, and it is not in the tree (below).

**What the sensor does not see, stated because it is the mitigation for D2:** `HEAD` of the main
checkout (rewritten, n=2), the index (n=1), sequencer state (n=1), object deletion (n=1), stray
files in the git dir (n=2), and another worktree's admin dir (n=1). Of D2's ten rows it flags
three: the two ref moves and the ref deletion. The commit is on the session's own branch. With
several sessions live it cannot say which one moved a ref. Whether it should also snapshot
`HEAD` and a listing of the git dir is a design question this record leaves with O3.

### Clean up

Per D8, operator-initiated only.

- **Derived, not indexed.** The list is `git worktree list --porcelain -z` filtered on the
  `pptmstr/` branch prefix; pptmstr keeps no registry. `2026-08-31` D1 is the precedent and its
  reason transfers whole: "An index written by this application has the same failure mode as this
  application." `-z` because a path may contain a newline (G 848-857).
- **Order:** remove the worktree, then delete the branch. `worktree remove` does not delete the
  branch (G 1181-1190). After a squash-merge on the remote with the remote branch deleted,
  `branch -d` refuses a branch whose tree is identical to `main` ("not fully merged", rc 1,
  G 2455-2473, n=1); with the upstream still present it succeeds with a warning. So "unmerged"
  as git reports it is not "unlanded", in the direction that asks for a `-D`.
- **Confirmations** for dirty and for unmerged — and the confirmation for a clean tree still has
  to say that ignored files go with it (trap table).
- **Residue a contained session leaves on the host**, outside its own tree (run 4, n=1, P
  1351-1392): a 0-byte `config.worktree` in `R/.git`, in the session's admin dir **and in every
  sibling worktree's admin dir**; an empty `R/.claude/.cc-writes/` in the main working tree; and
  `.claude/.cc-writes/` in cwd. In a clone arm only `C/.git/config.worktree` and
  `C/.claude/.cc-writes/` appear and `R` is untouched. What creates the stubs was not established
  (sandbox mount points is the builder's inference). Unmeasured: a repository that has
  `extensions.worktreeConfig` enabled and a real `config.worktree`.
- pptmstr must never run `gc --prune=now` or `git prune`, and cannot stop an agent doing so.

---

## Sequence

(R), including the order.

1. **Resume launches in the session's recorded cwd and refuses visibly when it is gone; a launch
   can report failure back to the launcher with the draft kept.** Independent of everything else
   here, fixes a defect that exists today, and shared with the sibling record.
2. Detection, the launcher control, creation and placement.
3. The missing-paths notice.
4. The end-of-session summary and the ref sensor.
5. Removal.

O1 has to be answered before step 2 and O4 before the AUTONOMOUS requirement in it. O3's first
item bears on step 4.

## Verification boundary

**Run for this record, 2026-10-07:** `pip show` / `claude --version` / `git --version` for the
three versions; `launcher.refusal_for`, `projects._derive`, `brief.session_dir` and the SDK's
`_sanitize_path` against a sibling path; `git worktree list` in this repository;
`git log --all -S"measure_writes"`.

**Read against raw output, not re-run:** every P line cited; the G and S ranges cited. The S
ranges for the lock sweep, the guest join and `activeWorktreeSession` were taken from the reviews'
citations and not opened individually.

**Not run:** any probe. No live session. The lead's two re-runs are quoted from the session
transcript, n=1 each, and exist nowhere else.

**Not verified:** the CLI ≥ 2.1.223 floor for cross-directory resume and the git ≥ 2.38 floor for
`merge-tree --write-tree` (one from fetched docs, one from recall; neither checked here); which
CLI SDK 0.2.158 bundles; anything on another host, CLI or git version.

## Records this one corrects or leaves in tension

None of these was edited.

- [`2026-09-03-a-dangerously-autonomous-mode.md`](2026-09-03-a-dangerously-autonomous-mode.md)
  - **§8 l.307, §11 l.733, §12 l.1321** — "The sandbox's writable region *is* cwd." False for a
    linked-worktree cwd on CLI 2.1.292 (D2). Three docstrings repeat it: `tree.py:112`,
    `launcher.py:363`, `driver.py:1624`.
  - **§8a.6 and §12 "still not built"** — answered by D2 for git directories only, with a
    substitute the record's own §8e.1 (l.497) says "does zero containment". §8e is arguing against
    a worktree *instead of* a sandbox; D2 is a worktree *under* one, and the measurement is that
    under it the worktree widens the region where a clone does not. Both stand; the reader needs
    both.
  - **§11 l.619** — says `pptmstr/tree.py:measure_writes` exists with tests. `tree.__all__` is
    `lies_inside_checkout`, `session_base`, `source_tree_of`; the name appears nowhere under
    `pptmstr/`, `tests/` or `scripts/`; and `git log --all -S` finds it in one commit, `e3a5e7a`,
    whose diff to `tree.py` and `tests/test_tree.py` does not contain it. Stale, and on this
    evidence it never landed on any ref in this clone.
  - **§12 l.1330** — "`strict_mcp_config=True` (§8a.4) is also unbuilt. Nothing in the tree sets
    it." `driver._options` sets it on every session (`driver.py:2098`).
  - §8e.1 l.497 attributes the zero-containment conclusion to §4; §4 does not mention worktrees.
- **`session_base` infers nothing from `.git`, and no planning record says so.**
  [`2026-08-10-layout-proposals.md`](2026-08-10-layout-proposals.md) l.269 and
  [`2026-08-10-research-sessions-under-the-inbox.md`](2026-08-10-research-sessions-under-the-inbox.md)
  l.115 specify the project key as "enclosing git root";
  [`2026-09-04-two-mainline-defects-the-autonomous-mode-exposed.md`](2026-09-04-two-mainline-defects-the-autonomous-mode-exposed.md)
  l.72 proposes `git rev-parse --show-toplevel` and leaves it "not chosen". The code chose the
  opposite and argues it in `tree.session_base`'s docstring and two tests. **Flagged, not
  resolved.** D5 is built on the code's answer and would have to be revisited if a record ever
  restores the other one.
- [`2026-09-03-orca-made-the-opposite-bet-on-the-same-tradeoff.md`](2026-09-03-orca-made-the-opposite-bet-on-the-same-tradeoff.md)
  l.240-243 and
  [`2026-08-15-what-the-board-does-not-carry.md`](2026-08-15-what-the-board-does-not-carry.md)
  l.468-471 — not corrected. D1 is a different granularity from the first and does not deliver the
  second.
- [`2026-08-31-sessions-can-be-resumed-and-bookmarked.md`](2026-08-31-sessions-can-be-resumed-and-bookmarked.md)
  D6 — "a resumed session… derives the directory it already owns" holds only when the resume is
  launched in the session's original cwd. Step 1.
- `notes/2026-10-07-a-linked-worktree-as-the-clone-substitute.md` (staged, uncommitted) — its §4
  says the containment probe's result "is recorded nowhere in this repository"; D2 is that record.
  Its `~/.pptmstr/worktrees/` convention is ruled out by D4. Its open item 2 is O4 here.
- `launcher.scan_sessions`' docstring, l.109-111 — "This repo has no worktrees… a choice made on
  the reasoning rather than on a measurement." Both halves are now out of date.
