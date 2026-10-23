# Under the Rug

**Find the errors your AI swept under the rug.**

A Claude Code plugin for Python. It finds the places where your code quietly hides
errors, breaks each one on purpose to show what really happens, and tells you in plain
English.

> **Status:** early, Python only.

## The problem: AI code doesn't crash, it lies

When an AI writes code, its goal is code that runs. So when it isn't sure something will
work, it adds a safety net:

```python
def get_price(product_id):
    try:
        return prices[product_id]
    except Exception:
        return 0        # product missing? just say it's free
```

The app never crashes, so it looks finished. But the error didn't go away. It got swept
under the rug:

- a product with no price is sold for **free**
- a failed AI call becomes an **empty report** that says "done"
- a failed payment, upload or email looks like it **worked**

You don't find out until a user does. And if you didn't write the code line by line,
you don't know where the rugs are.

This isn't rare. From a [Claude Code bug report](https://github.com/anthropics/claude-code/issues/31890):

> "Claude Code adds non-sensical fallbacks and swallows errors very frequently even
> having CLAUDE.md explicitly forbidding this behaviour."

## What Under the Rug does

Run `/rug:lift` and it:

1. **Finds the rugs.** Every `try/except` that carries on, every `with suppress(...)`,
   and every default value (`.get(key, default)`, `os.getenv(name, default)`) that could
   hide missing data.
2. **Lifts them.** For each one, it writes a throwaway test that makes it fail on
   purpose, and checks the test really reached that line.
3. **Tells you what was under it.** When it happens, what the code does instead of
   failing, and what that means for your app today.
4. **Fixes only when you ask.** Nothing in your project changes until you say `fix #N`.
   It asks how you want it fixed, then re-runs the test to prove the fix didn't just
   move the rug.

### Example report

```
Under the Rug: 2 problems found in examples/pricing.py (2 fallbacks tested)

#1 pricing.py:13  get_price
   When: a product isn't in the price list (for example a typo like 'bananna').
   What happens: it returns 0 instead of an error, and nothing is logged.
   Consequence: total() adds 0 for that item, so the customer is charged nothing for
     it: total(['apple', 'bananna']) -> 30.
   Proof: ran it · line check: ran · faked: nothing

#2 pricing.py:25  fetch_exchange_rate
   When: the network is down or the rate service doesn't answer.
   What happens: it returns None, and nothing is logged.
   Consequence: nothing in the project calls it yet, so no effect today. A future
     caller would get None with no reason why.
   Proof: ran it · line check: ran · faked: nothing (closed local port)

Say "fix #N" to fix one. Nothing has been changed.
```

## Bugs it has found in real projects

| Project | What was under the rug | Status |
|---|---|---|
| [gpt-researcher](https://github.com/assafelovic/gpt-researcher/issues/2218) | When the AI call fails, you get an empty report and the app still logs "Report written" | Issue open |
| [agno](https://github.com/agno-agi/agno/issues/11196) | A workflow step that hits a `TypeError` gets run a second time | Issue open, labelled bug |
| [crewAI](https://github.com/crewAIInc/crewAI/pull/8308) | A failed replan left the wrong task description behind | Fix merged |

## When to use it

- **While the AI is working, or before you open a PR.** Run `/rug:lift` with no target.
  It checks what you haven't committed yet. If everything is committed, it asks which
  branch you started from and checks only what changed since then. A fix can sweep a
  new error under the rug too.
- **When something "sometimes" comes out empty or wrong** and there's no error to go on.
- **On a codebase you didn't write yourself**, to find out where the errors are hidden.

**Not every rug is a bug.** Sometimes carrying on is the right call (a missing profile
picture shouldn't crash the app). The plugin shows you what happens; you decide.

## Install

Inside Claude Code, run these two commands (one at a time):

```
/plugin marketplace add reubenjrouse/undertherug
/plugin install rug@undertherug
```

Then start a new session and run `/rug:lift`.

**Getting updates:** run `claude plugin update rug@undertherug`, or turn on auto-update
for the `undertherug` marketplace in `/plugin` so you get fixes automatically.

**For development:** load a local copy for one session with
`claude --plugin-dir path/to/undertherug`.

## Usage

| Command | What it checks |
|---|---|
| `/rug:lift` | Lines you haven't committed yet (if none, it asks which branch to compare with) |
| `/rug:lift app/pricing.py` | One file, or every `.py` file in a folder |
| `/rug:lift the payments code` | Files that match the description (it shows you which) |
| `/rug:lift everything` | The whole project, skipping virtualenvs and build output |

After the report:

- `fix #3`: fix that one problem. It offers a few ways to fix it (and recommends one),
  makes the change you pick, then re-runs the test and checks the fix didn't add a new
  hiding spot before calling it done.
- `test next 10`: test the next 10 by score.

## Good to know

- **It's careful with your project.** Tests run in a temp folder, outside your code.
  They only fake the outside world (network, files), never your own code, and every
  finding lists what was faked.
- **It spends where it matters.** Finding candidates is a plain script (free). A small,
  cheap model scores each one, and only the 10 most dangerous get tested.
- **It needs your project's Python.** It tries the project's `.venv` / `venv`, then your
  active virtualenv or conda environment, and only uses one that exists and can import
  your project. If none can, it tells you what it tried and asks.

## How it works

```
/rug:lift <target>
   ├─ 1. Pick files       path, description, everything, or recent Git changes
   ├─ 2. Scan             a script finds every except block and default value,
   │                      with a short snippet of each and the code that calls it
   ├─ 3. Score            a small model scores each 0–100: "does a failure turn into
   │                      a believable wrong answer?" (skipped for 10 or fewer)
   ├─ 4. Lift each rug    helper agents test the top 10: each test makes it fail on
   │                      purpose (network failures hit a local server that fails instantly)
   ├─ 5. Line check       a fixed script confirms the test really ran that except block
   └─ 6. Report           worst first: when, what happens, consequence, proof
```

The line check matters because an AI writing its own tests can "prove" a bug without
ever reaching the real code. If the except block didn't run, the finding goes under
"Couldn't prove" instead of being reported.

## Plugin layout

```
.claude-plugin/plugin.json        plugin name and description
.claude-plugin/marketplace.json   lets people install it with /plugin install
skills/lift/SKILL.md              the /rug:lift command
agents/rug-scorer.md              the small model that scores each candidate
agents/fallback-auditor.md        the helper agent that writes and runs the tests
scripts/scan.py                   finds candidates and cuts out snippets
scripts/fail_server.py            a local server that fails instantly, for network tests
scripts/line_check.py             confirms a test really ran the except block
examples/pricing.py               a small file with two planted rugs to try it on
```

Check the plugin with `claude plugin validate .`
