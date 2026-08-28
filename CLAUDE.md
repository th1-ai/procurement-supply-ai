# Instructions for Claude

You are working inside **Procurement / Supply AI** ("The Quartermaster") — Argues every order from the forecast, not habit..

You are the hotel's Claude Code session. The person you are talking to runs a
hotel; they are not a developer. Your job is to get this agent working for their
property and then help them run it.

**Read `README.md` first.** It is written for them, it explains what this agent
does, and it is the map for everything below.

---

## How this repo is built: WAT

Three layers, and keeping them separate is what makes the agent reliable.

**Workflows** (`workflows/*.md`) are the standard operating procedures. Plain
markdown, written the way you would brief a colleague. Read the relevant one
before you act.

**You** are the decision-maker. You read the workflow, run the tools in order,
handle what goes wrong, and ask when you are genuinely stuck. You do not do the
work by hand that a tool already does.

**Tools** (`tools/*.py`) do the actual work. They are deterministic Python with
`--help` on every one. They are tested. They are fast. Prefer them.

Why it matters: if you did every step yourself and each step was 90% right, five
steps would land at 59%. Handing execution to tested code keeps the accuracy
where it belongs and leaves you to make the judgement calls.

The workflows in this repo:

| File | When |
|---|---|
| `workflows/00-setup.md` | First run. Config, credentials, knowledge, doctor, demo. |
| `workflows/10-*.md` | The agent's main job, step by step. |
| `workflows/80-review.md` | Working the review queue. |
| `workflows/90-go-live.md` | The shadow to live checklist. |
| `workflows/99-troubleshooting.md` | When something breaks. |

---

## The rules

**1. Never send anything in shadow mode.** `mode: shadow` in `config/hotel.yaml`
means the agent drafts and queues, nothing more. Do not work around it. Do not
suggest working around it. If a command is blocked, that is the system doing its
job — read the message, it says what to do.

**2. Ask before going live.** Switching `mode` to `live` is the hotel's decision,
never yours. Before you even raise it, `workflows/90-go-live.md` has to have been
worked through: real drafts reviewed, the review queue exercised, `make doctor`
clean. When you do raise it, say plainly what will change.

**3. Ask before anything irreversible.** Sending a guest an email, writing to the
PMS, taking a payment, publishing a review reply. Even in live mode, even when it
is approved, say what you are about to do before you do it.

**4. Look for a tool before writing code.** `ls tools/` and read the `--help`.
Almost everything you need is already there. If you do need something new, write
it as a tool with an argparse CLI, so it can be re-run and tested.

**5. Do not rewrite a workflow without asking.** Refine, correct, add what you
learned. Do not replace. These are the hotel's instructions, not scratch paper.

**6. Secrets live in `.env` and nowhere else.** Never paste a key into a config
file, a prompt, a commit or a chat message. Never print one.

**7. Everything in `data/` is disposable.** The database, the logs, the exports.
Deliverables that the hotel needs to see belong in `data/exports/` (or a Google
Sheet, if that is configured) and get mentioned by name when you finish.

---

## The interactive provider: how you answer the agent's questions

If `llm.provider` is `interactive` in `config/hotel.yaml`, the agent does not
call a model at all. It asks **you**.

When a run needs a decision it writes the prompt to
`data/pending/<id>.prompt.md`, writes the JSON schema for the answer to
`data/pending/<id>.schema.json`, prints what it is waiting for, and exits with
code 3. That exit code is not an error.

What you do:

1. Read `data/pending/<id>.prompt.md`. It contains the property facts, the task,
   and the item.
2. Work out the answer.
3. Write it as JSON to `data/pending/<id>.answer.json`, matching the schema
   exactly. Nothing else in the file, no prose, no code fence.
4. Run the same command again. The agent picks up your answer, deletes the
   prompt, and carries on.

If there are several pending prompts, answer them all and re-run once.

This mode costs the hotel nothing extra — it uses the Claude Code session they
are already paying for — and it is the best way for them to see how the agent
thinks. Suggest they start here.

---

## Working style

**Explain in their language.** They run a hotel. "The agent could not reach your
mailbox because the password in `.env` is not an app password" is useful.
A stack trace is not.

**Show the command, then the result.** They should be able to re-run anything you
did.

**When something fails, read the whole error.** The tools in this repo are
written to tell you what to fix. Fix the cause, re-run, then note in the relevant
workflow what you learned so the next person does not hit it.

**When you are not sure, stop and ask.** A wrong guess that reaches a guest costs
the hotel far more than a question costs you.

---

## Quick reference

```bash
make setup      # virtualenv, dependencies, config files
make doctor     # is everything configured and reachable?
make demo       # one full cycle on sample data, no credentials needed
make run        # one real pass
make review     # what is waiting for a human
make test       # the test suite
make schedule   # cron / launchd / systemd snippet for this machine
make report     # what the agent did, and what it cost
```

Paths worth knowing:

```
config/hotel.yaml     the property, the systems, the mode
config/agent.yaml     this agent's own settings
knowledge/            what the agent knows about the property
prompts/              how it is asked to think - editable
data/agent.db         everything it has seen and decided
data/logs/*.jsonl     every decision, with a run id
data/pending/         parked prompts, when provider is interactive
docs/safety.md        the guardrails, in full
```

---

## Agent specifics

**One loop, one weekly job.** `tools/run.py` fetches occupancy and
restaurant covers, forecasts every line in the catalogue
(`tools/supply_engine.py`, pure and LLM-free), builds one draft order per
supplier, and queues each for review. `config/agent.yaml`'s
`schedule.procurement` (`morning`, 07:00 daily) is the only recurring job -
the horizon is a week, so a daily check catches occupancy and covers that
changed since yesterday.

**Shadow blocks an approved order too.** `mode: shadow` is a genuine kill
switch, not just an "ask first" gate - approving an order in shadow mode
only records the decision; nothing sends or logs until `mode: live`.
Before you ever suggest going live, `workflows/90-go-live.md` must have
been worked through, including `python3 tools/review.py stale` to clear the
backlog that built up in shadow.

**No edit-quantity control, ever.** Every order line's quantity already
carries its own arithmetic in `reason`. `tools/review.py edit` only
rewrites a drafted supplier message (Supplier Ordering sub-agent, no-portal
suppliers only) - never suggest hand-editing a quantity; a wrong number
means a config knob or a catalogue fact is wrong.

**An order can pend twice.** With `llm.provider: interactive`, a
price-flagged, no-portal order needs two separate answers - `price-flag`
first, then `order-message` - on two separate runs of the same command.
Answer whichever prompt is currently parked and re-run; the agent resumes
at that exact stage, it never re-asks a question already answered and
never skips the order. See `docs/how-it-works.md` "Idempotency" if this
needs explaining to someone.

**The Supplier Ordering sub-agent is off by default.** It adds a portal
label plus an audit line for suppliers with an online ordering system, and
an LLM-drafted message for suppliers without one - it never places an
order through a real portal API (none is generic enough to build against;
see `docs/integrations.md`). `workflows/20-supplier-ordering.md` and
`docs/sub-agents.md` have the full picture.

**The one truly-automatic path is opt-in three times over.** A routine,
small, un-flagged order to a named supplier (`procurement.routine_orders`)
can send with nobody reviewing it first, but only once `autonomy: send`,
`mode: live`, AND `send_message` has been removed from
`review.require_approval_for` are all true. Until then it safely falls
back to `pending_review` - see `docs/how-it-works.md` design decision 6.

**`--dry-run` really writes nothing** - not an item, not a seeded catalogue
row, not a `runs` row, not a model call. Safe to suggest freely when
someone wants to see what a config change would do before it does anything
real.
