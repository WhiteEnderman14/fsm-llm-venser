# Machine file format

A machine is one YAML file. This page goes through every key, then shows how a turn runs and
which checks `validate` makes. If you only want to get going, copy the smallest machine below and
grow it from there. The machine in `examples/narrative_medicine_interview/machine.yaml` is the default example. For
one that uses every feature, with a comment on each, read `examples/support_ticket/machine.yaml`;
the [feature map](#where-to-see-each-feature) says where each one is.

Contents: [smallest machine](#the-smallest-machine) | [reading rules](#reading-rules) |
[top level](#top-level) | [events](#events) | [rules](#rules) | [states](#states) |
[transitions](#transitions) | [guards and actions](#guards-and-actions) |
[nested machines](#nested-machines) | [from_any_state](#from_any_state) |
[final states](#final-states) | [prompts](#prompts) | [what happens in a turn](#what-happens-in-a-turn) |
[feature map](#where-to-see-each-feature) | [checks](#checks) | [common mistakes](#common-mistakes)

## The smallest machine

Two questions and a goodbye. It runs with `run-word` (no model) and with `run` (the model writes
the questions and picks the events).

```yaml
name: tiny
initial: ASK_NAME

events:
  ANSWER: "The person gives a usable answer."
  BRIEF: "The reply is empty, off-topic or too vague."

states:
  ASK_NAME:
    goal: "Ask the person's first name."
    transitions:
      ANSWER: ASK_MOOD
      BRIEF: ASK_NAME
    fallback:
      BRIEF: ANSWER
    on_invalid: BRIEF

  ASK_MOOD:
    goal: "Ask how the person feels today."
    transitions:
      ANSWER: DONE
      BRIEF: ASK_MOOD
    fallback:
      BRIEF: ANSWER
    on_invalid: BRIEF

finals:
  DONE:
    say: {mode: static, text: "Thanks, that is all."}

prompts:
  classifier_system: "You map a reply to exactly one of the allowed events. You never talk to the person."
  classifier_user: |-
    State: {state}
    Goal: {goal}
    Allowed events:
    {events}
    Question: {question}
    Reply: {reply}
  question_system: "You ask one short, friendly question at a time."
  question_user: |-
    Last thing the person said: {last_reply}
    {task}
  question_task: |-
    Goal of this step: {goal}
    {clarify_hint}Ask your next question.
```

Without `--plugin` the machine can still run, because it uses no guards or actions of its own.

## Reading rules

The file is read with a strict loader, so a mistake is reported instead of guessed.

- Only `true` and `false` are booleans. `NO`, `yes` or `off` stay strings, so a state called `NO`
  is fine.
- A repeated key is an error, and so is any key the format does not know (a typo in `transtions`
  is caught).
- Names of states, events, finals, actions and guards are identifiers: letters, digits and
  underscore, not starting with a digit.
- A state inside a nested machine is called by its path, `EMOTIONS.INVITE`. Inside the YAML you
  write its short name; the path shows up in tables, traces and in `state` filters of plugins.
- Strings that contain a colon, a `#` or start with a quote or a bracket need quotes. When in doubt,
  quote.

## Top level

| Key | Required | Meaning |
|---|---|---|
| `name` | yes | Name of the machine. Used in messages and as the default name of the drawing. |
| `initial` | yes | The state the run starts in. It must be one of `states`. |
| `max_retries` | no | How many times a state may repeat itself before the `fallback` event takes over. Default 2. |
| `events` | yes | The alphabet. Shared by every level of nesting. |
| `rules` | no | Regular expressions checked before the LLM is called. |
| `from_any_state` | no | `EVENT: TARGET` pairs that are valid from every non-final state. |
| `on_any_transition` | no | Actions run after every transition. |
| `states` | yes | The states. At least one. |
| `finals` | yes | The final states. At least one. A run ends when it reaches one. |
| `prompts` | no | Prompt templates. Needed to run with an LLM, not for `run-word`. |

A name cannot be both a state and a final.

## Events

The alphabet of the machine. An event is something the person can "do" with a reply: agree,
decline, ask to stop. The LLM picks one of them for every reply, so the descriptions matter more
than anything else in the file.

```yaml
events:
  CONFIRM: "The person agrees or confirms (yes / ok / that is right)."   # short form
  ANSWER:
    description: "The person gives a usable answer, even a short one."
    examples:
      - "It started last spring."
      - "Mostly tired, I think."
```

| Key | Required | Meaning |
|---|---|---|
| `description` | yes | What the event means, written for the model. The short form `EVENT: "text"` sets only this. |
| `examples` | no | Sample replies shown to the classifier under the description. Worth adding with small models. |

The classifier only sees the events that the current state accepts, not the whole alphabet. An
event that no state uses is allowed but useless; an event used in a transition and missing here is
an error.

## Rules

A rule is a safety net that does not depend on the model. If the reply matches one of its regular
expressions, the event is forced and the LLM is not called at all.

```yaml
rules:
  - event: DISTRESS
    ignore_case: true                  # default
    match:
      - '\bwant to die\b'
      - '\bvoglio morire\b'
```

| Key | Required | Meaning |
|---|---|---|
| `event` | yes | The event to force. It must be declared in `events`. |
| `match` | yes | One or more regular expressions (Python syntax), searched anywhere in the reply. |
| `ignore_case` | no | Default `true`. |

Rules run in file order and the first match wins. A rule is skipped in a state that does not
accept its event, which is why a `DISTRESS` rule is normally paired with `from_any_state`, so
that every state accepts it. Use single quotes for patterns: backslashes are then kept as they are.

## States

```yaml
STATE_NAME:
  goal: "What this question is for."
  say:
    mode: llm
    fallback: "Text used if the model cannot write the question."
  transitions:
    ANSWER: NEXT
    BRIEF: STATE_NAME
  fallback:
    BRIEF: SKIP
  on_invalid: BRIEF
  entry: [start_timer]
  exit: [stop_timer]
```

| Key | Meaning |
|---|---|
| `goal` | What the question should achieve. Required when the model writes the question (the default). It fills `{goal}` in the prompts. |
| `say` | What the machine says here. See below. Optional: without it the model writes the question. |
| `transitions` | Event to target. The accepted events of the state are exactly its keys, plus the ones from `from_any_state`. There is no separate list. |
| `fallback` | `EVENT: OTHER_EVENT`. When the state has already repeated itself `max_retries` times and `EVENT` would send it back to itself again, `OTHER_EVENT` is used instead. This is what stops a run from looping forever. |
| `on_invalid` | An event used when the model fails or answers with an event the state does not accept. Required to run with an LLM. Normally the "try again" event (`BRIEF`). |
| `entry`, `exit` | Actions run when the state is entered or left. |

A state needs at least one transition, otherwise it is a dead end and `validate` says so.

### say

| Key | Meaning |
|---|---|
| `mode` | `llm` (default): the model writes the question from the prompts. `static`: the machine says `text`, word for word. |
| `text` | The fixed text. Required when `mode` is `static`. May use `{placeholders}`. |
| `clarify` | Static mode only. Said instead of `text` when the state is being repeated, because the last reply was not usable. |
| `fallback` | LLM mode. The text said if the model fails to write a question. Without it, a failure stops the run. |
| `task` | LLM mode. The name of an entry in `prompts` that replaces `question_task` for this state, for a state that needs a different kind of question (the example uses it for the summary). |

Use `static` for consent texts, legal wording and anything that must not change.

## Transitions

```yaml
transitions:
  ANSWER: SUMMARY                  # 1. just a target
  CONFIRM:                         # 2. one object
    to: DONE
    do: [mark_complete]
  SKIP:                            # 3. alternatives, tried in order
    - to: NO_CONTENT
      guard: {when: "ANSWER == 0"}
    - to: SUMMARY                  # default branch: last, no guard
      do:
        - flag: skipped
```

| Key | Meaning |
|---|---|
| `to` | The target: a state of the same level, or a final of the machine. |
| `guard` | A call that must return true for this alternative to apply. |
| `do` | Actions run when the alternative is taken. |

With several alternatives the first one whose guard holds is taken. The last one must have no
guard: it is the default, and `validate` reports a missing one.

A target is looked up in the level where the arrow is written. Inside a nested machine it can be
a sibling state or one of the `finals` of that nested machine, never a state of the outer level;
leaving the nested machine is what its `finals` mapping is for. An arrow that targets a composite
state goes to that state's `initial`.

An arrow to the state itself is a self-loop. It leaves and enters nothing, and it is the only
kind of arrow that counts as a retry.

## Guards and actions

Both are written as calls. A call is a bare name, or a one-key mapping whose value is one argument
or a list of arguments:

```yaml
guard: long_interview                       # no argument
guard: {when: "BRIEF < 2 and turns < 10"}   # one argument
do:
  - log                                     # no argument
  - inc: answers                            # one argument
  - record_note: [ANSWER, REVISE]           # several arguments, passed in order
```

A guard answers a yes/no question and may only read. An action may change the variables of the
run. Their code lives in a plugin, not in the YAML: the file only names them.

### Guards that come with the project

These two work in every machine, with no plugin. They live in `src/fsm_llm_venser/plugins/builtin.py`.

| Guard | True when |
|---|---|
| `when: "EXPR"` | The counter expression holds. Allowed: comparisons (`==`, `!=`, `<`, `<=`, `>`, `>=`), `and`, `or`, `not`, whole numbers, and these names: any event of the machine (how many times it already happened, this turn not counted), `retries` (turns spent in the current state), `turns` (turns completed in the run). Anything else is an error. |
| `reply_matches: "REGEX"` | The latest reply of the person matches the regular expression, ignoring case. |

```yaml
ANSWER:
  - to: URGENT
    guard: {reply_matches: '\b(pain|bleeding)\b'}
  - to: NEXT
BRIEF:
  - to: OPENING
    guard: {when: "BRIEF < 2 and turns < 10"}
  - to: SKIPPED
```

For anything these two cannot express, write your own guard or action in a plugin. The plugin
guide is [plugins.md](plugins.md): how to write them, what `ctx` holds, hooks and listeners. A
guard of your own with the same name as a built-in one replaces it.

## Nested machines

A state can hold a whole machine of its own. Use it to keep a stage with several steps as one
box instead of a long chain of states.

```yaml
EMOTIONS:
  fsm:
    initial: INVITE
    states:
      INVITE:
        goal: "Invite the person to name how they felt."
        transitions: {ANSWER: DEEPEN, SKIP: NOTHING_TO_SHARE, BRIEF: INVITE}
        fallback: {BRIEF: SKIP}
        on_invalid: BRIEF
      DEEPEN: {...}
    finals:                          # final of the nested machine -> state to continue in
      FEELINGS_NAMED: MEANING        # MEANING is a sibling of EMOTIONS
      NOTHING_TO_SHARE: MEANING
    from_any_state: {...}            # optional, same meaning as at the top level
    on_any_transition: [...]         # optional
  entry: [...]                       # run once when EMOTIONS is entered
  exit: [...]                        # run once when EMOTIONS is left
```

| Key of `fsm` | Required | Meaning |
|---|---|---|
| `initial` | yes | First state of the nested machine. |
| `states` | yes | Its states, written inline. They may be composite as well, with no depth limit. |
| `finals` | yes | Each nested final state mapped to the state to go to in the outer level. These states are not real states: reaching one means "leave the box and go there". |
| `from_any_state`, `on_any_transition` | no | Apply to this box and to anything inside it. |

A composite state is left only through its `finals`, so it cannot have `transitions`, `fallback`,
`say` or `on_invalid` of its own. It can have `entry` and `exit`.

When a move crosses boxes, `exit` actions run innermost first and `entry` actions outermost
first, as in a UML statechart. Internally the machine is flattened into one table (the `table`
command prints it), which is why nested states appear as `EMOTIONS.INVITE`.

## from_any_state

```yaml
from_any_state:
  STOP: STOPPED
  DISTRESS: ESCALATED
```

Shortcuts valid from every non-final state at that level and below. They are the right place for
"stop", "help" and safety events. The rule is: a state's own transition for an event wins; if it
has none, the nearest enclosing level that has one is used, up to the root. A target here must be
a state or a final of the level where the block is written.

## Final states

```yaml
finals:
  DONE:
    say: {mode: static, text: "Thank you."}
    entry: [build_card]         # run when the machine ends here
  STOPPED:                      # a final without body is fine
```

| Key | Meaning |
|---|---|
| `say` | The closing text. Same keys as in a state; `static` is the usual choice. |
| `entry` | Actions run when the run ends in this final. Good place to build a result. |

Reaching a final ends the run. The result holds the transcript, the variables and the final text.

## Prompts

Named templates, only needed to run with an LLM.

| Name | Required | Used for |
|---|---|---|
| `classifier_system` | yes | System message of the classifier: who it is, what it must never do. |
| `classifier_user` | yes | The classification request: state, goal, allowed events, question, reply. |
| `question_system` | yes | System message of the question writer: tone and rules for the questions. |
| `question_user` | yes | The request for the next question. Put `{task}` in it, or the task of the state never reaches the model. |
| `question_task` | yes | The default task. Gets `{goal}` and `{clarify_hint}`. |
| `clarify_hint` | no | A sentence added to the task when the previous reply was unusable. |
| any other name | no | A task selected by a state with `say.task`. |

Placeholders are written `{name}`. Use `{{` and `}}` for a literal brace; any other brace is an
error, so JSON examples in a prompt need doubled braces.

| Placeholder | Available in | Meaning |
|---|---|---|
| `{state}`, `{goal}`, `{last_reply}` | every prompt and every text | Current state (qualified), its goal, the latest reply (`(none yet)` at the start). |
| `{events}`, `{question}`, `{reply}` | `classifier_user` | Allowed events with descriptions and examples; the question just asked; the reply. |
| `{task}` | `question_user` | The task of the state, already filled. |
| `{clarify_hint}` | `question_task` and `say.task` prompts | The `clarify_hint` prompt on a retry, empty otherwise. |
| any other name | everywhere | A variable of the plugin (`{heard}`). It must exist, or the run stops with a message. |

## What happens in a turn

1. The state says something: its `text` if `static`, otherwise the model writes it from
   `question_system`, `question_user` and the task. If the model fails, `say.fallback` is used.
2. The person replies.
3. The reply becomes an event:
   a. the `rules` are tried in order; a match decides, with source `rule`;
   b. otherwise the model picks one of the allowed events (source `llm`);
   c. if the model fails, or picks an event the state does not accept, `on_invalid` is used
      instead (source `error` or `guard`).
4. If the chosen arrow is a self-loop and the state has already retried `max_retries` times, the
   `fallback` event for it is used instead.
5. The first alternative whose guard holds is taken. Then, in this order: `exit` actions
   (innermost first), the `do` of the alternative, `on_any_transition` (innermost level first),
   `entry` actions (outermost first).
6. If the target is a final, its `entry` runs, it speaks, and the run ends. Otherwise go to 1.

`run --max-turns N` stops a run after N turns even if no final was reached.

## Where to see each feature

All of these appear in `examples/support_ticket/machine.yaml`.

| Feature | Where in the example |
|---|---|
| Short and long form of an event, `examples` | `events`: `BRIEF` and `ANSWER` |
| `rules` | `HUMAN` rule, no model call when it matches |
| `from_any_state` at the root and in a box | root: `STOP`, `HUMAN`; in `TROUBLESHOOT`: `SKIP` |
| `on_any_transition`, at the root and in a box | `log_step`, `remember: [ANSWER, REVISE]`; `bump: steps` |
| `max_retries`, `fallback`, `on_invalid` | `WELCOME` (BRIEF becomes STOP after two retries) |
| `say` static with `clarify`; `say` llm with `fallback`; `say.task` | `WELCOME`; `ISSUE`; `TRY_FIX` |
| Transition forms: target, object, alternatives | `ISSUE`; `SECOND_TRY`; `PRIORITY` and `TRY_FIX` |
| Built-in guards `when`, `reply_matches` | `TRY_FIX` (`NOT_SOLVED < 1`); `PRIORITY` |
| Guard of the plugin, with an argument | `PRIORITY`: `attempts_at_least: 2` |
| Actions: no argument, one argument, a list | `log_step`; `bump: attempts`; `remember: [...]` |
| `entry` / `exit` of a state and of a box | `ISSUE`; `TROUBLESHOOT` |
| Nested machine, and one inside another | `TROUBLESHOOT` and `DEVICE` |
| `finals` of a box going to a state, or to a final of the machine | `NOT_FIXED: PRIORITY`, `FIXED: SOLVED_BY_FIX` |
| Final states: text, `entry`, empty | `TICKET_CREATED`, `HANDED_OFF`, `CANCELLED` |
| Prompts, `clarify_hint`, extra task prompts, plugin variables as placeholders | `prompts`: `suggest_fix`, `ticket_summary`, `{heard}`, `{ticket_id}` |

The plugin of the example, `examples/support_ticket/plugin.py`, has the matching Python side:
`vars_model`, `analysis_model`, one guard, six actions and five listeners.

## Checks

`validate` reads the structure first (types, unknown keys, bad regular expressions, bad braces in
prompts), then compiles the machine and checks:

1. every target exists, and every event used anywhere is declared in `events`;
2. no state is a dead end;
3. the last alternative of every event has no guard;
4. to run with an LLM: the five core prompts exist, every state has `on_invalid`, every state
   whose question is written by the model has a `goal`, and each `say.task` names a prompt.

Every error found is reported at once, with the state it belongs to. A guard or action that the
plugin does not define, or a placeholder with no value, is only found when that part runs. Use
`run-word` to walk through the paths of a machine without a model before trying the LLM.

## Common mistakes

| Message or symptom | Cause |
|---|---|
| `unknown key` | A typo, or a key in the wrong place (`transitions` inside `fsm`, for example). |
| `event 'X' is not declared in events` | A transition, rule or fallback uses an event that is not in the alphabet. |
| `the last alternative of an event must have no guard` | Add a default alternative at the end of the list. |
| `needs on_invalid to run with an LLM` | Add `on_invalid: BRIEF` (or your retry event) to the state. `validate` without an LLM does not need it, `run` does. |
| The run loops in one state | The state has a self-loop but no `fallback` for it. |
| The model keeps picking a wrong event | Improve the event description, add `examples`, and check that the state really lists that event. |
| `[guard]` in the output | The model chose an event the state does not accept; `on_invalid` replaced it. A few are normal with small models. |
