# A linked worktree as the clone substitute

**Dated:** 2026-10-07 · **Status:** a pilot and what was measured setting it up; nothing
here is decided · **Origin:** creating the first worktree pptmstr is meant to launch
sessions into, as a stand-in for the mandatory clone of
`planning/2026-09-03-a-dangerously-autonomous-mode.md` §8a.6, and as a pattern another
team can copy for research work

**This is not a planning record.** It says how to use the worktree that exists and what
is and is not known about it. Whether a linked worktree *is* an acceptable clone
substitute is the open question in §4, and this note does not answer it.

Tags follow `notes/2026-09-03`: **[V]** verified by execution this session, command
given; **[R]** read from a file in this repository; **[I]** inference.

---

## 1. What exists

```
git worktree add -b adv-text-experiment \
    ~/.pptmstr/worktrees/pptmstr/adv-text-experiment main
```

| | |
|---|---|
| Path | `~/.pptmstr/worktrees/pptmstr/adv-text-experiment` |
| Branch | `adv-text-experiment`, created at `851978e` (`main`). Local only; not pushed. |
| Convention | `~/.pptmstr/worktrees/<repo>/<name>`, `<name>` equal to the branch |

**[V]** `git worktree list` shows both trees at `851978e`. `~/.pptmstr` did not exist
before this; nothing in `pptmstr/` or `scripts/` reads or writes that directory, so the
convention is enforced by nothing and pptmstr does not know these trees are its own.

`<repo>` is a basename. Two repositories with the same basename collide under this
convention; nothing has needed to solve that yet.

## 2. What a linked worktree is, in the respect that matters here

**[V]** `git -C <worktree> rev-parse --git-path <x>`:

| Lives in the worktree's own admin dir (`<main>/.git/worktrees/adv-text-experiment/`) | Shared with the main checkout (`<main>/.git/`) |
|---|---|
| `index`, `HEAD` | `objects`, refs, `config`, `hooks` |

None of either column is inside the worktree directory. Its `.git` is a one-line file
pointing at the admin dir. So:

- A commit made in the worktree writes to `/home/wreel/Source/pptmstr/.git`. A session
  whose writable region is strictly its cwd cannot commit from here; a session that can
  commit from here can reach the main checkout's git directory. Which of those the
  sandbox produces is §4.
- Hooks and config are the main checkout's. A hook installed there runs for commits made
  here, and the reverse.
- A branch can be checked out in one tree at a time. `main` is taken.

## 3. Using it

### Setting the tree up

The worktree is a checkout of committed files and nothing else.

- **No `.venv`.** Run `./bootstrap.sh` inside it before `make` anything; every `make`
  target that matters is behind `venv-check`. Not run this session.
- **Uncommitted work on `main` is absent.** `scripts/verify_worktree_containment.py` is
  staged on `main` and uncommitted, so it is not in the worktree **[V]**.
- `*.ini`, caches and `shot.png` are ignored and will accumulate per tree.

### Launching a session into it from the main checkout's pptmstr

Set the launcher's cwd to the worktree path. This is the "clone substitute" direction.

**[V]** `launcher.refusal_for(<worktree>)` with `OWN_CHECKOUT=/home/wreel/Source/pptmstr`
returns `None`: the unattended mode is **permitted** for this directory. The check is
`tree.lies_inside_checkout`, a resolved-path containment test **[R]**; the worktree is
outside the checkout by path, so it passes.

**[I]** That is the check answering the question it was written for — is the cwd the tree
this process runs out of — and it has no opinion on a tree that is outside by path and
shares a git directory. The files an agent edits here are a separate copy of
`approval.py`, not the running one. The git directory is not separate. Whether that
matters is §4.

### Running pptmstr itself out of the worktree

**[V]** With the worktree as `OWN_CHECKOUT`, `refusal_for` refuses the worktree and
**permits `/home/wreel/Source/pptmstr`**. Symmetric with the above and consistent with
the check's design, but worth knowing before running a second pptmstr from here: each
instance protects only its own tree, and the two trees are one repository.

### Where a worktree session's state lands

**[V]** For this path the CLI's project slug and pptmstr's brief slug differ:

```
claude_agent_sdk._internal.sessions._sanitize_path  -> -home-wreel--pptmstr-worktrees-pptmstr-adv-text-experiment
pptmstr.brief.session_dir(...).parent.parent.name   -> -home-wreel-.pptmstr-worktrees-pptmstr-adv-text-experiment
```

The SDK replaces every non-alphanumeric with `-`; `brief.session_dir` replaces only the
path separator. `brief.session_dir`'s docstring says the slug "is the CLI's own" and
that briefs sit beside the transcripts; for any cwd containing `.` or `_` they do not.
`~/.claude/projects/-home-walter.reel-Source-example-project/` already shows it: it
holds `briefs/` and no transcript **[V]**. Every tree under `~/.pptmstr/` hits this
because of the leading dot.

Briefs are written and read through the same function, so **[I]** this is a broken
co-location claim rather than a lost brief. Not fixed here; no test was run against it.

**[R]** `launcher.scan_sessions` and `sessions.enumerate_sessions` say the SDK folds a
directory's worktrees into its listing, so the picker opened on the main checkout should
show sessions run here. **Not verified**: no session has been run in this worktree, and
`launcher.py:109` records that the claim was written when the repo had no worktrees.
This tree is the first chance to check it.

### Removing it

```
git worktree remove ~/.pptmstr/worktrees/pptmstr/adv-text-experiment
git branch -d adv-text-experiment        # refuses if unmerged, which is the point
```

`worktree remove` refuses a dirty tree. Deleting the directory by hand leaves the admin
dir behind; `git worktree prune` clears it.

## 4. What is not established: containment

§8 of the autonomous-mode record: *"the clone must be mandatory or this is not
containment"*, argued from the sandbox's writable region being cwd **[R]**. §2 above is
why a worktree is not obviously a clone for that argument.

`scripts/verify_worktree_containment.py` is the probe for exactly this — worktree arm
against clone arm against an unsandboxed host leg **[R]**. Its state as found:

- staged on `main`, uncommitted;
- it has been run: four `-tmp-pptmstr-wt-containment-*` project directories exist under
  `~/.claude/projects/`, and one fixture, `/tmp/pptmstr-wt-containment-s3r9ax_8`, is
  still on disk **[V]**;
- **no result is recorded anywhere in this repository** **[V]** (`grep` for the script
  name and fixture prefix finds only the script).

It was not run this session and its transcripts were not read. Until its result is
written down, treat a session launched into this worktree under the unattended mode as
**able to reach the main checkout's `.git`** — hooks, config, refs, the objects of every
branch. That is the conservative reading, not a measured one.

Under the ordinary gate none of this is new exposure: every `Bash` call is still
classified and asked about.

## 5. For a team copying this

What transfers as-is: the path convention, the `git worktree add -b` line, §2, the
removal steps.

What does not transfer without thought:

- §3's refusal behaviour is about pptmstr's own source. A research repo that is not
  pptmstr is never refused on that ground, in either tree.
- §4 applies in full. A worktree gives a research session its own files and its own
  branch; it does not give it its own repository. If the property wanted is "this session
  cannot touch my real repo", a `git clone` is the object that has it by construction
  and a worktree is the object under test.
- The Orca record's argument against a worktree *per agent* — it undercuts `touches`
  **[R]** — is about agents inside one team sharing a tree. One worktree per team or per
  experiment, with the team sharing it, is not the thing that record argues against.

## 6. Open

1. The probe's result, recorded. Everything in §4 hangs on it.
2. Whether `refusal_for` should know about linked worktrees of `OWN_CHECKOUT`, in either
   direction. Depends on 1.
3. Whether `brief.session_dir` should use the SDK's sanitiser, or its docstring should
   stop claiming the CLI's slug.
4. Whether pptmstr should own `~/.pptmstr/worktrees/` — create, list, remove — or the
   operator does, as now.
5. The picker's worktree fold, observed once a session has run here.
