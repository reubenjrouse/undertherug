---
description: Audit silent fallbacks (try/except, defaults, swallowed errors) in Python code and warn what each one hides.
argument-hint: [file, folder, part of the codebase, or "everything"]
disable-model-invocation: true
---

# Under the Rug: lift

Target: $ARGUMENTS

Python only. Finding fallbacks is cheap; testing them is expensive. Do the cheap search
yourself, and spend the auditor's work only on what the user agreed to test.

Leave running the project's code to the auditors. If you must run Python yourself, run
it with `-B` from outside the project, so no `__pycache__` is written into it.

## 1. Work out which files to audit

- **A path** (file or folder): use its `.py` files.
- **A description** ("the payments part", "the API layer"): search the codebase for
  matching `.py` files. List the files you chose so the user can see the pick.
- **"everything"** or similar: all `.py` files in the project, skipping virtualenvs,
  `site-packages`, and build output.
- **Empty**: audit recent changes. If this is a Git repository, use the lines changed
  since the last commit (`git diff HEAD -U0 -- '*.py'`) plus new untracked `.py` files
  (`git ls-files --others --exclude-standard -- '*.py'`), and only keep fallbacks on
  those lines. If nothing has changed, say so and stop. If it is not a Git
  repository, ask the user what to audit and stop until they answer.

If no `.py` files match, say so and stop.

## 2. Cheap search

Use Grep over the chosen files (with a few lines of context) to find candidates:
- `except` clauses
- `.get(key, default)` and `getattr(obj, name, default)` with a default

Drop handlers whose body re-raises (`raise`). Rank what's left, riskiest first:
1. A bare `except:` or `except Exception` / `BaseException` around the project's own
   code or data
2. Other handlers and defaults around the project's own code or data
3. Handlers for failures from outside (network, file I/O on user-supplied paths, user
   input, third-party APIs)

## 3. Ask before big audits

- **20 or fewer candidates:** go straight to step 4 with all of them.
- **More than 20:** do not test yet. Show the total, how many files, and the top 20 by
  rank as `file:line  function  (why it's risky)`. Then ask: test these 20, name a part
  to focus on, or stop. Wait for the answer.

Never test more than 20 candidates in one round.

## 4. Find the project's Python

Trigger tests import the project's code, so they must run with the project's own
Python, not the system one. Check in this order and use the first that exists:
1. An activated environment: the `VIRTUAL_ENV` environment variable.
2. A `.venv` or `venv` folder at the project root.

The interpreter is `Scripts\python.exe` inside the environment on Windows,
`bin/python` elsewhere. If neither is found, ask the user which Python to use and
wait. Never fall back to the system Python silently.

## 5. Run the auditors in parallel

Split the approved candidates into groups so several auditors can work at once:
- Keep candidates from the same file in the same group, so each file is read once.
- Aim for about 4 candidates per group, and at most 5 groups. Merge small files into
  one group; split a file with many candidates across groups.

Launch one `rug:fallback-auditor` subagent per group, **all in the same message**
so they run at the same time, and wait for every one to finish before reporting. Give
each one the project root's absolute path, the Python interpreter's absolute path, and
its group of candidates (file:line and function for each; for `except` candidates,
the line of the `except` clause).

When all have returned, merge their findings. Auditors can't see each other's work, so
check consistency across all of them: findings with the same failure pattern must be
described the same way and judged equally serious. If two disagree, re-read both and
correct the one that doesn't fit its evidence.

## 6. Report

Plain language, no labels or tags. Number every problem and show it in this shape:

```
Under the Rug: 2 problems found in examples/pricing.py (2 fallbacks tested)
Python: `C:\...\.venv\Scripts\python.exe`

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

Not a problem: none
Couldn't prove: none

If a problem is intended behavior, ignore it.
```

Rules for the report:
- Keep the file name and line number exactly as in the code.
- **When** is the real situation that sets it off. **What happens** is what the code
  does instead of failing. **Consequence** is what that does to the program today,
  following the value to where it ends up (who calls it, what the user sees).
- If the consequence was worked out by reading code instead of running it, say so:
  `Consequence (from reading the code): ...`.
- Each problem cites its proof: the line check and what was faked. If the auditor could
  not trigger a fallback, or the line check says the test never reached it, list it
  under `Couldn't prove` with the reason, instead of guessing what it does.
- List fallbacks that are handled fine (the failure is logged or raised, and not
  reported as success) or can't happen under `Not a problem`, one line each with why.
- Put the most serious problems first: wrong data or money before a failure reported
  as success, before missing output, before slower or worse results, before cosmetic.
- Put the Python path in backticks, so the terminal doesn't swallow backslashes.
- If candidates were left untested, add: `<M> more found, not tested. Say "test next 20" to continue.`
- End with: `Say "fix #N" to fix one. Nothing has been changed.`

"test next 20" runs steps 5–6 on the next 20 by rank, continuing the numbering.

## 7. Fixes, only on request

Do not edit any code until the user asks for a specific fix. When they do:
- Change only that fallback. Never delete files.
- If the failure comes from the project's own code or data: remove the fallback so the
  error surfaces, or raise a clear error that names the missing thing.
- If it comes from outside (network, AI services, third-party APIs, user input): keep
  handling the failure, but make it log or surface the problem instead of staying
  silent or reporting success.
- If it catches both (a broad `except` around an outside call that can also hide the
  project's own bugs): narrow it to the outside errors, and log those.
- Show the exact before/after of what changed.
