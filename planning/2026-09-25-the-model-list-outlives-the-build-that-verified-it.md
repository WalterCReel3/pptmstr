# The model list outlives the build that verified it

**Dated:** 2026-09-25 ·
**Status:** built on `main`; `make check` green (2569 passed, 3 xfailed) ·
**Origin:** an operator request (2026-09-25) — *"New Opus just dropped. Can we dynamically
probe models in the task launcher?"* ·
**Supersedes:** `orchestrator-design.md` §7 trap 9's *"verify model ID strings at build
time"*, on the freshness half only — see §5 ·
**Measured against:** the live `GET /v1/models`, 2026-09-25

---

## 1. The claim, and why the old one stopped holding

`ui/launcher.py` carried a four-entry tuple under the comment *"verified against the
model-config docs at build time"*. That comment is accurate and it is the problem: a
build-time check cannot see a model released after the build. `claude-opus-5-5` and
`claude-fable-5-1` were both live on the day of the request and neither was reachable
from the launcher, with nothing on screen to say so.

The staleness is silent in both directions — the list does not become *wrong*, it becomes
*short*. Every id in it still resolved. Nothing failed. The operator simply could not
pick the model they had come to pick.

## 2. What was measured, not assumed

`GET /v1/models` returned 12 models. Three findings decided the design:

- **The response is concrete ids only.** There is no `opus` or `sonnet` in it. Aliases,
  had we offered them, would be a second list no probe could ever refresh — the exact
  failure being removed. Aliases are therefore **not offered**; the API's ids are.
- **Capabilities come with it** — `max_input_tokens`, `max_tokens`, and a tree carrying
  `effort.{low..max}` and `thinking.types.{adaptive,enabled}`. `claude-opus-4-5-20251101`
  and `claude-haiku-4-5-20251001` report no `effort` and no adaptive thinking, while
  `claude-opus-5-5` reports the full ladder. **Not consumed yet**; recorded because it is
  what a future effort or thinking control would gate on, and because it is the argument
  against ever reducing the probe to a list of strings on the wire.
- **The order is newest-first.** Taken as-is: the model that just shipped is the one the
  operator is scanning for. Nothing sorts it.

## 3. Auth, which is not what a reader expects

There is no `ANTHROPIC_API_KEY` in a normal install and no `ant` profile. Auth is the
bundled Claude Code CLI's, stored as an OAuth token in `~/.claude/.credentials.json`.
Consequences, all verified against the live endpoint:

- `anthropic.Anthropic()` with no arguments **raises** here. The token is passed as
  `auth_token`, which puts it on `Authorization: Bearer` where an OAuth token belongs —
  not `api_key`, which would send `x-api-key` and 401.
- The `oauth-2025-04-20` beta header is **not required** by `/v1/models`; tested both
  ways, both return 12. It is sent anyway, because the same token on `/v1/messages` does
  need it and this call is one refactor away from moving.
- Refreshing that token is Claude Code's job. An expired one is a failed probe.

`sandbox.DENIED_CREDENTIAL_FILES` lists this same path. That is not in tension with the
above and the record says so explicitly to stop the question being re-asked: the deny
list exists because the *sub-agent* is untrusted, and the host process is the thing that
writes it. A program reading a credential to make its own API call is not the hazard §8
describes.

## 4. The index became the interesting part

The launcher held `model_index: int` and `spec()` returned `MODELS[self.model_index]`.
With a fixed list that is safe. With a probed one it has two defects, and only the second
survives the "probe once at startup" constraint:

- **Not reachable here:** a list that changes while a draft is open re-aims a stored
  index at a different model. The probe answers once, on a worker, and the list is
  replaced in a single assignment, so a draft can in principle straddle it — but the
  window is one frame and the fallback and probed lists share a prefix. Recorded as the
  reason the fix is cheap insurance rather than the reason to make it.
- **Reachable, and asked for:** *"users may select specific minor versions."* An index
  has no way to name a model the current list does not contain. Pin `claude-opus-5-5`,
  have the next run's probe fail, and the list falls back to four entries — the selection
  must be silently rewritten to one of them. `--model` has always accepted such a string
  (`app.py`), so the CLI path and the UI path disagreed about what a selection *is*.

So the draft holds `model: str`, the combo resolves a position at draw time, and a pick
the list lacks is prepended rather than dropped. This is the same call
`LauncherState.policy` already made for `Policy`, for the same stated reason.

`DEFAULT_MODEL` is a **name**, not `models[0]`. The probed order is chosen elsewhere and
can change without notice; a positional default would hand the choice of default model —
and the 5×/1.7× price step between tiers — to whatever shipped most recently.

## 5. What this supersedes, and what it does not

Trap 9 said *"verify model ID strings at build time"*. Its **reasoning still holds** —
the tuple is still listed rather than free-text so a typo cannot become a session that
fails on first turn — and the guarantee is now stronger, because a probed id came from
the API rather than from a doc read. What is superseded is the implied *sufficiency* of
a build-time check. Trap 9 should be read as: the list is validated, and the build is no
longer the only moment it can be.

The probe is an **override, not a dependency**. `FALLBACK_MODELS` is what the launcher
offers before the probe answers, when it fails, and when it answers with nothing. A
machine with no network launches exactly as it did.

## 6. What is deliberately not built

- **No setting, no toggle, no flag.** Probing is a built-in startup path. A checkbox
  would put a network call behind a control that gets ticked while the operator reads
  the sentence beside it — `VersionGate`'s recorded reasoning, reused.
- **No persistence.** The probed list is not written to disk. It is world, not operator
  (`settings.py`), and a cached list on disk would need an expiry policy, which is more
  machinery than a 5-second call at startup costs.
- **No capability gating yet.** See §2. The launcher can currently offer a model whose
  `effort` the UI has no control for; that is today's behaviour and is not made worse.
- **No visible "this list is stale" marker.** `Catalog.detail` carries the reason a probe
  failed for the log, and the fallback and probed lists are indistinguishable on screen.
  Adding a marker means deciding what an operator should *do* about it, and the honest
  answer today is nothing.

## 7. Verification

Every guarantee below was mutation-tested — the line was broken deliberately and the
named test confirmed to fail, per STYLE.md §2:

| Broken | Test that caught it |
|---|---|
| Expiry read as seconds, not milliseconds | `test_an_expiry_is_read_as_milliseconds_not_seconds` |
| Empty probe result taken literally | `test_a_probe_that_answered_with_nothing_falls_back` |
| Pre-flight expiry check removed | `test_an_expired_credential_is_refused_without_a_network_call` |
| `DEFAULT_MODEL` absent from the offered list | `test_the_default_model_is_one_of_the_models_offered` |
| Unlisted pick not prepended before `index()` | `test_a_pinned_model_the_offered_list_lacks_still_draws` |
| `spec()` ignoring the draft's model | `test_a_pick_the_offered_list_does_not_contain_survives_to_the_spec` |

The expiry cases are worth naming twice: with the guard removed the suite still passed
*except* for that one test, and the run time went from 0.04s to ~1.2s — the probe
reaching the network to learn what was already on disk.

`scripts/mock_cards.py` carried its own copy of the model list and **had already drifted**
(no `claude-fable-5`, an undated haiku). It now imports `FALLBACK_MODELS`. This is the
smell STYLE.md §3 names — unpinned duplication — caught in the wild rather than in theory.
