# Generated tables (graph.py)

## Headline

- unreachable_from_entry_point: ['requests', 'ui.splash', 'ui.splash_art', 'ui.splash_view']
- modules: 97
- loc: 103758
- import_edges: 445
- import_edges_by_kind: {'eager': 424, 'lazy': 19, 'typing': 6}
- lazy_only_edges: 15
- refs_total: 18443
- refs_cross_module: 6844
- refs_unresolved: 1
- weighted_module_edges: 473
- modularity: {'louvain_with_app': 0.409, 'louvain_without_app': 0.423, 'prefix_partition_without_app': 0.079, 'louvain_without_app_shuffled_runs': [0.404, 0.423], 'louvain_without_app_or_hubs': 0.596}

## SCCs

- eager: 0 SCC(s); sizes []
- eager+lazy: 3 SCC(s); sizes [4, 2, 11]
  - approval, board, model, templates
  - bus, commandbus
  - figures.fences, figures.host, figures.render, theme, ui.typeset, ui.typeset.diffs, ui.typeset.find, ui.typeset.gate, ui.typeset.parts, ui.typeset.themes, ui.typeset.views
- all (incl. TYPE_CHECKING): 3 SCC(s); sizes [5, 12, 3]
  - approval, board, command, model, templates
  - figures.fences, figures.host, figures.render, theme, ui.typeset, ui.typeset.diffs, ui.typeset.find, ui.typeset.gate, ui.typeset.parts, ui.typeset.syntax, ui.typeset.themes, ui.typeset.views
  - bus, commandbus, driver

## Fan-in / fan-out (top 15 by weighted in)

| module | loc | fan_in | fan_out | w_in | w_out | community |
|---|---|---|---|---|---|---|
| model | 5175 | 42 | 4 | 2147 | 26 | C1 |
| theme | 1510 | 29 | 2 | 524 | 67 | C0 |
| command | 930 | 14 | 3 | 455 | 3 | C5 |
| ui.widgets | 1301 | 20 | 3 | 436 | 41 | C2 |
| intents | 1528 | 15 | 5 | 374 | 108 | C3 |
| ui.typeset.roles | 636 | 6 | 0 | 237 | 0 | C0 |
| board | 960 | 15 | 2 | 197 | 61 | C1 |
| approval | 1952 | 22 | 6 | 172 | 29 | C2 |
| effects | 648 | 9 | 5 | 170 | 33 | C1 |
| boardlog | 2002 | 9 | 2 | 152 | 37 | C3 |
| log | 74 | 7 | 1 | 144 | 1 | C3 |
| transcript | 584 | 12 | 1 | 125 | 2 | C4 |
| ui.typeset.parts | 313 | 3 | 2 | 85 | 10 | C0 |
| ui.launcher | 4390 | 5 | 14 | 84 | 292 | C2 |
| ui.typeset.views | 2993 | 6 | 11 | 84 | 206 | C0 |

## Prefix clusters (edges touching app/__main__ removed)

| cluster | n | internal | out | in | cohesion | purity | top partners |
|---|---|---|---|---|---|---|---|
| board*+digest | 6 | 95 | 270 | 289 | 0.145 | 0.5 | model:200, bus:119, ui.board_pane:78, intents:61, channels:39 |
| command*+req/appr/shellscan/sandbox | 9 | 242 | 126 | 401 | 0.315 | 0.67 | ui.command_view:109, model:82, store:58, bus:46, ui.inbox:40 |
| session*/fleet*/links/channels | 5 | 31 | 164 | 38 | 0.133 | 0.8 | intents:66, boardlog:41, model:38, ui.launcher:25, approval:7 |
| ui *_pane | 5 | 41 | 646 | 21 | 0.058 | 0.6 | theme:141, board:81, model:77, ui.widgets:74, transcript:48 |
| ui *_view | 4 | 2 | 328 | 39 | 0.005 | 0.5 | model:93, command:88, theme:57, ui.widgets:39, ui.inbox:22 |
| ui *_form | 2 | 0 | 162 | 0 | 0.0 | 0.5 | ui.launcher:64, ui.widgets:58, approval:11, model:9, ui.session_menu:4 |
| ui *_menu | 3 | 16 | 122 | 20 | 0.101 | 0.67 | model:72, approval:28, ui.rail:12, theme:8, ui.widgets:6 |

## Louvain communities (without app/__main__)

| id | n | loc | cohesion | prefix mix | members |
|---|---|---|---|---|---|
| C0 | 25 | 22399 | 0.448 | {'ui (other)': 16, 'core (other)': 5, 'ui *_pane': 3, 'ui *_view': 1} | bridge, brief, drawable, settings, theme, ui.board_pane, ui.brief_pane, ui.compose, ui.detail, ui.digest_pane, ui.inbox, ui.review, ui.settings_dialog, ui.splash, ui.splash_art, ui.splash_view, ui.tabs, ui.typeset, ui.typeset.diffs, ui.typeset.gate, ui.typeset.parts, ui.typeset.roles, ui.typeset.syntax, ui.typeset.themes, ui.typeset.views |
| C1 | 16 | 25419 | 0.512 | {'core (other)': 4, 'board*+digest': 3, 'ui (other)': 3, 'ui *_view': 2, 'ui *_menu': 2, 'command*+req/appr/shellscan/sandbox': 1, 'ui *_form': 1} | board, boardgraph, bus, digest, effects, model, requests, store, ui.decision_view, ui.fleet_view, ui.focus, ui.link_form, ui.main_menu, ui.projects, ui.rail, ui.session_menu |
| C2 | 14 | 14153 | 0.455 | {'core (other)': 5, 'ui (other)': 4, 'command*+req/appr/shellscan/sandbox': 2, 'session*/fleet*/links/channels': 1, 'ui *_menu': 1, 'ui *_form': 1} | approval, cli_version, model_catalog, sandbox, sessions, templates, tree, ui.gate_menu, ui.health, ui.launcher, ui.resume_form, ui.session_details, ui.widgets, worktree |
| C3 | 12 | 12230 | 0.308 | {'core (other)': 5, 'session*/fleet*/links/channels': 4, 'board*+digest': 3} | boardfacts, boardlog, boardsync, channels, driver, fleetsync, intents, links, log, plugins, pool, sessionfile |
| C4 | 9 | 7066 | 0.375 | {'ui (other)': 5, 'core (other)': 2, 'ui *_pane': 2} | fake_driver, transcript, ui.blocks, ui.inline, ui.rich_pane, ui.span_layout, ui.tool_block, ui.transcript_pane, ui.typeset.find |
| C5 | 7 | 8634 | 0.448 | {'command*+req/appr/shellscan/sandbox': 6, 'ui *_view': 1} | command, commandbus, commandfile, commandrun, commandtext, shellscan, ui.command_view |
| C6 | 6 | 6438 | 0.684 | {'core (other)': 6} | figures.displaylist, figures.fences, figures.host, figures.mermaid, figures.render, figures.sanitise |
| C7 | 2 | 1013 | 0.746 | {'core (other)': 2} | pairing, version |

## Prefix cluster -> community spread

- **board*+digest** (purity 0.5): C3: boardfacts, boardlog, boardsync; C1: board, boardgraph, digest
- **command*+req/appr/shellscan/sandbox** (purity 0.67): C5: command, commandbus, commandfile, commandrun, commandtext, shellscan; C2: approval, sandbox; C1: requests
- **session*/fleet*/links/channels** (purity 0.8): C3: channels, fleetsync, links, sessionfile; C2: sessions
- **ui *_pane** (purity 0.6): C0: ui.board_pane, ui.brief_pane, ui.digest_pane; C4: ui.rich_pane, ui.transcript_pane
- **ui *_view** (purity 0.5): C1: ui.decision_view, ui.fleet_view; C5: ui.command_view; C0: ui.splash_view
- **ui *_form** (purity 0.5): C1: ui.link_form; C2: ui.resume_form
- **ui *_menu** (purity 0.67): C1: ui.main_menu, ui.session_menu; C2: ui.gate_menu

## Louvain without app/__main__ and hubs (model, theme, log, ui.widgets)

- C0: approval, cli_version, model_catalog, plugins, sandbox, sessions, shellscan, templates, tree, ui.gate_menu, ui.health, ui.launcher, ui.link_form, ui.main_menu, ui.projects, ui.resume_form, ui.session_details, ui.session_menu, worktree
- C1: brief, settings, ui.brief_pane, ui.settings_dialog, ui.typeset, ui.typeset.diffs, ui.typeset.gate, ui.typeset.parts, ui.typeset.roles, ui.typeset.syntax, ui.typeset.themes, ui.typeset.views
- C2: boardfacts, boardlog, boardsync, channels, driver, fleetsync, intents, pool, sessionfile, store
- C3: bridge, drawable, ui.compose, ui.decision_view, ui.detail, ui.fleet_view, ui.focus, ui.inbox, ui.rail, ui.review
- C4: fake_driver, transcript, ui.blocks, ui.inline, ui.rich_pane, ui.span_layout, ui.tool_block, ui.transcript_pane, ui.typeset.find
- C5: board, boardgraph, bus, digest, effects, ui.board_pane, ui.digest_pane
- C6: command, commandbus, commandfile, commandrun, commandtext, ui.command_view
- C7: figures.displaylist, figures.fences, figures.host, figures.mermaid, figures.render, figures.sanitise
- C8: ui.splash, ui.splash_art, ui.splash_view
- C9: pairing, version

## Same-prefix pairs split in >= 70% of 30 shuffled runs

- board*+digest: board / boardfacts together 0.2
- board*+digest: board / boardlog together 0.0
- board*+digest: board / boardsync together 0.0
- board*+digest: boardfacts / digest together 0.2
- board*+digest: boardgraph / boardlog together 0.0
- board*+digest: boardgraph / boardsync together 0.0
- board*+digest: boardlog / digest together 0.0
- board*+digest: boardsync / digest together 0.0
- command*+req/appr/shellscan/sandbox: approval / command together 0.0
- command*+req/appr/shellscan/sandbox: approval / commandbus together 0.0
- command*+req/appr/shellscan/sandbox: approval / commandfile together 0.0
- command*+req/appr/shellscan/sandbox: approval / commandrun together 0.0
- command*+req/appr/shellscan/sandbox: approval / commandtext together 0.0
- command*+req/appr/shellscan/sandbox: approval / requests together 0.0
- command*+req/appr/shellscan/sandbox: approval / shellscan together 0.03
- command*+req/appr/shellscan/sandbox: command / requests together 0.0
- command*+req/appr/shellscan/sandbox: command / sandbox together 0.0
- command*+req/appr/shellscan/sandbox: commandbus / requests together 0.0
- command*+req/appr/shellscan/sandbox: commandbus / sandbox together 0.0
- command*+req/appr/shellscan/sandbox: commandfile / requests together 0.0
- command*+req/appr/shellscan/sandbox: commandfile / sandbox together 0.0
- command*+req/appr/shellscan/sandbox: commandrun / requests together 0.0
- command*+req/appr/shellscan/sandbox: commandrun / sandbox together 0.0
- command*+req/appr/shellscan/sandbox: commandtext / requests together 0.0
- command*+req/appr/shellscan/sandbox: commandtext / sandbox together 0.0
- command*+req/appr/shellscan/sandbox: requests / sandbox together 0.0
- command*+req/appr/shellscan/sandbox: requests / shellscan together 0.0
- command*+req/appr/shellscan/sandbox: sandbox / shellscan together 0.03
- session*/fleet*/links/channels: channels / sessions together 0.0
- session*/fleet*/links/channels: fleetsync / sessions together 0.0
- session*/fleet*/links/channels: links / sessions together 0.0
- session*/fleet*/links/channels: sessionfile / sessions together 0.0
- ui *_pane: ui.board_pane / ui.transcript_pane together 0.1
- ui *_pane: ui.brief_pane / ui.rich_pane together 0.07
- ui *_pane: ui.brief_pane / ui.transcript_pane together 0.27
- ui *_pane: ui.digest_pane / ui.transcript_pane together 0.1
- ui *_view: ui.command_view / ui.decision_view together 0.0
- ui *_view: ui.command_view / ui.fleet_view together 0.0
- ui *_view: ui.command_view / ui.splash_view together 0.0
- ui *_view: ui.decision_view / ui.splash_view together 0.0
- ui *_view: ui.fleet_view / ui.splash_view together 0.0
- ui *_form: ui.link_form / ui.resume_form together 0.03
- ui *_menu: ui.gate_menu / ui.main_menu together 0.03
- ui *_menu: ui.gate_menu / ui.session_menu together 0.03

## Cross-prefix pairs together in >= 90% of runs (direct edge)

- approval (command*+req/appr/shellscan/sandbox) + templates (-) 1.0 w=12.0
- approval (command*+req/appr/shellscan/sandbox) + ui.gate_menu (ui *_menu) 1.0 w=9.0
- approval (command*+req/appr/shellscan/sandbox) + ui.health (-) 0.97 w=6.0
- approval (command*+req/appr/shellscan/sandbox) + ui.launcher (-) 1.0 w=27.0
- approval (command*+req/appr/shellscan/sandbox) + ui.resume_form (ui *_form) 1.0 w=11.0
- approval (command*+req/appr/shellscan/sandbox) + ui.session_details (-) 1.0 w=1.0
- approval (command*+req/appr/shellscan/sandbox) + ui.widgets (-) 1.0 w=9.0
- boardfacts (board*+digest) + intents (-) 1.0 w=58.0
- boardgraph (board*+digest) + bus (-) 1.0 w=21.0
- boardgraph (board*+digest) + effects (-) 1.0 w=1.0
- boardgraph (board*+digest) + model (-) 1.0 w=53.0
- boardgraph (board*+digest) + store (-) 1.0 w=1.0
- boardlog (board*+digest) + channels (session*/fleet*/links/channels) 1.0 w=39.0
- command (command*+req/appr/shellscan/sandbox) + ui.command_view (ui *_view) 1.0 w=87.0
- commandfile (command*+req/appr/shellscan/sandbox) + ui.command_view (ui *_view) 1.0 w=2.0
- commandrun (command*+req/appr/shellscan/sandbox) + ui.command_view (ui *_view) 1.0 w=1.0
- commandtext (command*+req/appr/shellscan/sandbox) + ui.command_view (ui *_view) 1.0 w=19.0
- fleetsync (session*/fleet*/links/channels) + intents (-) 1.0 w=63.0
- links (session*/fleet*/links/channels) + log (-) 1.0 w=5.0
- sandbox (command*+req/appr/shellscan/sandbox) + ui.launcher (-) 1.0 w=3.0
- sessions (session*/fleet*/links/channels) + ui.launcher (-) 1.0 w=23.0
- sessions (session*/fleet*/links/channels) + ui.resume_form (ui *_form) 1.0 w=2.0
- ui.board_pane (ui *_pane) + ui.detail (-) 0.97 w=2.0
- ui.brief_pane (ui *_pane) + ui.typeset (-) 1.0 w=1.0
- ui.brief_pane (ui *_pane) + ui.typeset.parts (-) 1.0 w=5.0
- ui.brief_pane (ui *_pane) + ui.typeset.roles (-) 1.0 w=4.0
- ui.brief_pane (ui *_pane) + ui.typeset.views (-) 1.0 w=3.0
- ui.decision_view (ui *_view) + ui.rail (-) 1.0 w=2.0
- ui.digest_pane (ui *_pane) + ui.inbox (-) 0.97 w=7.0
- ui.fleet_view (ui *_view) + ui.rail (-) 1.0 w=1.0
- ui.gate_menu (ui *_menu) + ui.health (-) 0.97 w=1.0
- ui.link_form (ui *_form) + ui.session_menu (ui *_menu) 1.0 w=4.0
- ui.main_menu (ui *_menu) + ui.rail (-) 0.97 w=1.0
- ui.resume_form (ui *_form) + ui.widgets (-) 1.0 w=54.0
- ui.rich_pane (ui *_pane) + ui.span_layout (-) 1.0 w=5.0
- ui.transcript_pane (ui *_pane) + ui.typeset.find (-) 1.0 w=2.0

## Monoliths

| module | loc | units | edges | components | largest | singletons | communities | Q | cut |
|---|---|---|---|---|---|---|---|---|---|
| app | 6193 | 244 | 460 | 2 | 243 | 1 | 14 | 0.617 | 141/562 |
| bus | 6076 | 211 | 525 | 6 | 206 | 5 | 13 | 0.515 | 173/687 |
| model | 5175 | 215 | 359 | 30 | 179 | 24 | 43 | 0.67 | 117/509 |
| store | 4723 | 149 | 144 | 31 | 78 | 25 | 38 | 0.815 | 12/157 |
| driver | 3524 | 116 | 147 | 10 | 84 | 5 | 14 | 0.718 | 21/173 |

### app

expanded: []

| c | units | lines | hubs | uses |
|---|---|---|---|---|
| 0 | 47 | 1312 | AppState, _panels, _card_actions, _command_rows | model:33, intents:15, log:15, command:12 |
| 3 | 22 | 733 | _launch, LaunchRefused, _launch_ended, LaunchOutcome | log:18, boardlog:9, ui.launcher:6, cli_version:5 |
| 4 | 18 | 533 | CommandFiles, _start, CommandFileReport, startup_shortfalls | log:19, commandfile:16, command:6, commandrun:6 |
| 2 | 22 | 483 | main, guarded, begin_frame, _keep_gc_settled | log:10, ui.detail:10, theme:8, settings:6 |
| 6 | 17 | 387 | _shut_down, _read_channel_reports, _read_outcome, _read_sweep | channels:18, log:17, fleetsync:6, commandrun:3 |
| 5 | 17 | 367 | apply_intents, _send_wakes, _read_board_reports, _hold_wake | effects:15, model:10, log:6, commandfile:5 |
| 1 | 29 | 296 | _not_done, _approve_command, _run_command, _refuse_command | command:15, log:7, intents:5, model:5 |
| 8 | 14 | 288 | _apply_settings, _perform, _menu_facts, _menus | ui.main_menu:26, settings:4, ui.typeset.gate:3, ui.typeset.roles:3 |
| 7 | 14 | 271 | _handle_layout_keys, FOCUS, TRIAGE, _status_bar | theme:7, model:3, ui.focus:2, ui.review:1 |
| 11 | 9 | 240 | _draw_overlays, _draw_not_done, _resume_ends, _form_up | ui.launcher:3, ui.settings_dialog:3, ui.resume_form:3, theme:1 |

### bus

expanded: ['build_server (1718 lines -> 24 units)']

| c | units | lines | hubs | uses |
|---|---|---|---|---|
| 0 | 57 | 2129 | _text, build_server.declare_task, build_server.raise_decision, build_server.amend_task | model:101, effects:32, intents:15, boardgraph:4 |
| 1 | 57 | 1118 | _board_line, _task_lines, _part_text, _quoted | board:61, model:24, digest:10, effects:2 |
| 2 | 33 | 731 | _named, _decisions_text, _chain_lines, _dry_run_text | model:48, boardgraph:17, effects:13, drawable:1 |
| 5 | 13 | 411 | _refusal_text, build_server.release_task, _declare_refusal_text, _duplicate_id_text | model:50, board:3, intents:1 |
| 3 | 14 | 299 | _one_line, peer_wake_line, wake_line, wake_message | effects:17, model:11, board:3 |
| 6 | 12 | 261 | build_server.read_task, _digest_text, _MAX_BOARD_CHARS, _bounded_digest_text | model:12, digest:11, board:3, intents:2 |
| 4 | 14 | 228 | _decision_whole, _decision_listed, _headline, _argument | model:19 |
| 7 | 6 | 42 | qualified, offered_tools, BUS_TOOLS, COMMAND_TOOLS |  |
| 12 | 1 | 19 | build_server.withdrawn_row | model:3, effects:2, board:2, intents:1 |
| 10 | 1 | 13 | build_server.board_ids | model:2, effects:2, intents:1 |

### model

expanded: ['Task (414 lines -> 11 units)', 'Snapshot (1179 lines -> 47 units)']

| c | units | lines | hubs | uses |
|---|---|---|---|---|
| 2 | 18 | 634 | Task, TaskId, ApprovedWrites, approved_write |  |
| 6 | 11 | 593 | AgentRecord, ContextSnapshot, ContextPressure, Recalled | approval:6, transcript:2 |
| 1 | 24 | 525 | Concern, ConcernState, Snapshot.cited_question, _thread_mark |  |
| 0 | 26 | 505 | NodeId, ObligationKind, Obligation, PeerMailHeld | command:3 |
| 4 | 14 | 419 | Snapshot, ConcernId, _empty_map, DecisionId | command:3 |
| 3 | 16 | 372 | Snapshot.peer_index, ChannelId, Snapshot.thread, PeerIndex | approval:2 |
| 9 | 9 | 228 | CommandRefusal, _COMMAND_REFUSAL_SENTENCES, Snapshot.detach_refusal, Snapshot.command_approval_refusal | command:9 |
| 5 | 13 | 205 | Channel, PeerRefusal, Snapshot.peer_route, Snapshot.peer_inbox_of |  |
| 8 | 9 | 185 | Decision, DecisionOutcome, Snapshot.decisions_readable_by, Answered |  |
| 7 | 11 | 171 | Charter, Provenance, stated_provenance, kept_provenance |  |

### store

expanded: ['_apply (1358 lines -> 55 units)']

| c | units | lines | hubs | uses |
|---|---|---|---|---|
| 1 | 15 | 1699 | _apply, Store, Observer, _drawable | model:46, intents:24, command:9, effects:7 |
| 0 | 16 | 693 | _restored, _restored_task, _stated, _said_id | model:152, boardlog:8, drawable:7, intents:3 |
| 7 | 10 | 628 | _peer_waking, _wakeable, _to_tell, _unheard | model:57, intents:16, effects:11, approval:1 |
| 2 | 15 | 285 | _asker_fate, _decision_summary, _command_summary, _blocks_text | model:54, command:3, board:2, intents:1 |
| 5 | 12 | 219 | _settled, _withdrawer_refusal, _apply[case TaskReleased], _may_take_claim | model:53, intents:5, effects:2 |
| 8 | 8 | 196 | _apply[case TaskDeclared], _would_cycle, _already_behind, _as_declared | model:47, intents:1 |
| 4 | 13 | 121 | _FINAL_STATES, _peer_rows, _peer_row, _apply[case ApprovalRequested] | model:32, intents:7, approval:5, effects:4 |
| 3 | 14 | 113 | _with_command, _apply[case CommandWithdrawn], _apply[case CommandRequested], _apply[case CommandEnded] | command:32, model:14, intents:10, effects:10 |
| 10 | 3 | 104 | _pick_claim, _claim_refusal, _apply[case TaskClaimRequested] | model:22, intents:1, effects:1 |
| 6 | 11 | 85 | _apply[case DecisionRaised], _filed, _decided, _ended_by_operator | model:28, effects:12, intents:8 |

### driver

expanded: ['Translator (301 lines -> 13 units)', 'AgentSession (2444 lines -> 62 units)']

| c | units | lines | hubs | uses |
|---|---|---|---|---|
| 1 | 20 | 705 | AgentSession._gate_tool_use, AgentSession._park, _deny, AgentSession._write_region | approval:14, model:6, log:4, bus:4 |
| 3 | 18 | 531 | AgentSession.run, AgentSession._check_identity, AgentSession._poll_context, SessionIdentityError | model:13, intents:10, log:6, cli_version:5 |
| 0 | 20 | 510 | AgentSession._options, AgentSession._pre_tool_use, AgentSession._subagent_start, AgentSession._note_subagent_activity | intents:6, bus:2, transcript:2, board:1 |
| 5 | 13 | 419 | AgentSession.policy, AgentSession.contained, AgentSession._own_checkout_refusal, AgentSession._uncontained_autonomy | approval:6, tree:2, figures.fences:2, templates:2 |
| 4 | 13 | 316 | AgentSession._await_subagents, AgentSession._settle_silent_subagents, AgentSession._settle_subagents_at_run_end, AgentSession._settle_subagent | model:8, intents:4, log:3, transcript:2 |
| 2 | 19 | 278 | _text_arg, Translator._assistant, Translator._task_update_topic, Translator.handle | intents:18, model:10, transcript:7 |
| 7 | 2 | 221 | AgentSession.__init__, DEFAULT_SUBAGENT_CAP | approval:2, templates:2, transcript:2, model:1 |
| 6 | 4 | 57 | AgentSession.role_status, AgentSession._addresses_in_role, _split_address, AgentSession.known_roles | model:1 |
| 12 | 1 | 26 | AgentSession.role_of | model:2 |
| 13 | 1 | 26 | AgentSession.wake | transcript:1, intents:1, log:1, model:1 |

## Tests

- test_files: 146
- test_functions: 7296
- dotted_string_patches: 53
- tests_with_dotted_string_patch: 28
- module_object_setattr_patches_total: 859
- module_object_setattr_patches: {'pptmstr.ui.widgets': 61, 'pptmstr.ui.inbox': 58, 'pptmstr.app': 55, 'pptmstr.ui.launcher': 44, 'pptmstr.ui.typeset.gate': 41, 'pptmstr.ui.typeset': 38, 'pptmstr.ui.board_pane': 35, 'pptmstr.ui.transcript_pane': 34, 'pptmstr.commandrun': 34, 'pptmstr.ui.command_view': 28, 'pptmstr.ui.detail': 27, 'pptmstr.figures.mermaid': 27, 'pptmstr.boardlog': 24, 'pptmstr.settings': 20, 'pptmstr.pairing': 19, 'pptmstr.commandfile': 16, 'pptmstr.ui.link_form': 16, 'pptmstr.ui.typeset.diffs': 15, 'pptmstr.version': 15, 'pptmstr.brief': 14, 'pptmstr.ui.rail': 14, 'pptmstr.worktree': 13, 'pptmstr.ui.typeset.views': 12, 'pptmstr.figures.host': 12, 'pptmstr.ui.session_details': 10, 'pptmstr.ui.review': 10, 'pptmstr.sessionfile': 9, 'pptmstr.theme': 9, 'pptmstr.ui.digest_pane': 9, 'pptmstr.driver': 9, 'pptmstr.requests': 9, 'pptmstr.ui.typeset.roles': 9, 'pptmstr.ui.settings_dialog': 8, 'pptmstr.ui.tool_block': 8, 'pptmstr.boardsync': 7, 'pptmstr.ui.tabs': 7, 'pptmstr.ui.compose': 7, 'pptmstr.ui.session_menu': 7, 'pptmstr.ui.decision_view': 7, 'pptmstr.ui.splash_view': 7, 'pptmstr.ui.main_menu': 7, 'pptmstr.ui.typeset.syntax': 7, 'pptmstr.cli_version': 6, 'pptmstr.figures.fences': 6, 'pptmstr.ui.health': 5, 'pptmstr.fleetsync': 4, 'pptmstr.ui.resume_form': 4, 'pptmstr.ui.brief_pane': 4, 'pptmstr.boardfacts': 2, 'pptmstr.approval': 2, 'pptmstr.figures.render': 2, 'pptmstr.channels': 1, 'pptmstr.model_catalog': 1, 'pptmstr.templates': 1, 'pptmstr.commandtext': 1, 'pptmstr.ui.splash': 1, 'pptmstr.ui.typeset.themes': 1}

| source module | dotted patches | test modules importing |
|---|---|---|
| driver | 39 | 24 |
| settings | 7 | 33 |
| app | 2 | 29 |
| cli_version | 2 | 7 |
| model | 1 | 71 |
| ui.typeset | 1 | 30 |
| ui.widgets | 1 | 35 |

## Dynamic dispatch census

- match on class (static: seen): total 82 — {'app': 13, 'ui.launcher': 10, 'boardlog': 7, 'ui.resume_form': 6, 'requests': 5, 'store': 5, 'ui.detail': 4, 'bus': 3}
- match on string literal: total 1 — {'app': 1}
- getattr(computed name): total 36 — {'ui.settings_dialog': 5, 'theme': 4, 'ui.transcript_pane': 4, 'ui.typeset.views': 4, 'app': 3, 'ui.board_pane': 3, 'drawable': 2, 'ui.rich_pane': 2}
- @tool (MCP, dispatched by name string): total 20 — {'bus': 20}
- getattr(literal name): total 17 — {'ui.typeset.views': 9, 'figures.host': 3, 'bus': 1, 'driver': 1, 'figures.mermaid': 1, 'pairing': 1, 'ui.typeset.gate': 1}
- hasattr(literal name): total 2 — {'drawable': 1, 'figures.host': 1}
- importlib.import_module: total 3 — {'version': 3}

## Layer violations (non-ui importing ui, excluding app/__main__)

- figures.host -> ui.typeset (lazy)
- figures.render -> ui.typeset.views (lazy)
- theme -> ui.typeset (lazy)

imgui importers outside ui/: ['app', 'figures.host', 'theme']

SDK importers: ['bus', 'cli_version', 'driver', 'sessions', 'ui.session_details']

## Cycles closed only by a lazy import

- approval -> board (back: board -> templates -> approval)
- bus -> commandbus (back: commandbus -> bus)
- figures.render -> ui.typeset.views (back: ui.typeset.views -> figures.render)
- theme -> ui.typeset (back: ui.typeset -> ui.typeset.diffs -> theme)
- ui.typeset.views -> ui.typeset.diffs (back: ui.typeset.diffs -> ui.typeset.views)
