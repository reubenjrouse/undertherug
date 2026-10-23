---
description: Audit silent fallbacks (try/except, defaults, swallowed errors) in Python code and warn what each one hides.
argument-hint: [file, folder, part of the codebase, or "everything"]
disable-model-invocation: true
---

# Under the Rug: lift

Target: $ARGUMENTS

Python only. Finding fallbacks is free (a script), scoring them is cheap (small
scorer agents), testing them is expensive (auditors). Spend the expensive part only on
the ones that score highest.

Leave running the project's code to the auditors. Don't read the scan's batch files
yourself; the scorers do. Run scripts with `-B` so no `__pycache__` is written.

## 1. Work out which files to audit

- **A path** (file or folder): use its `.py` files.
- **A description** ("the payments part", "the API layer"): search the codebase for
  matching `.py` files. List the files you chose so the user can see the pick.
- **"everything"** or similar: the whole project (step 3 uses `--all`).
- **Empty**: audit uncommitted changes. If this is a Git repository, use the files and
  lines changed since the last commit (`git diff HEAD -U0 -- '*.py'`) plus new untracked
  `.py` files (`git ls-files --others --exclude-standard -- '*.py'`). If it is not a Git
  repository, ask the user what to audit and stop until they answer.

  If nothing is uncommitted, the work may already be committed. Don't guess where it
  started; ask: `Everything's committed. Check the changes since which branch?
  (for example main)` and wait. When they name one, check it exists
  (`git rev-parse --verify --quiet <branch>`), then use the files and lines from
  `git diff $(git merge-base <branch> HEAD) -U0 -- '*.py'` plus new untracked `.py`
  files. That's only what changed since this branch split off from theirs, committed or
  not. If the branch doesn't exist, say so and ask again.

If no `.py` files match, say so and stop.

## 2. Find the project's Python

Trigger tests import the project's code, so they must run with the project's own
Python, not the system one. Try these in order:
1. A `.venv` or `venv` folder at the project root.
2. An activated virtualenv: the `VIRTUAL_ENV` environment variable.
3. An activated conda environment: the `CONDA_PREFIX` environment variable.

Where the interpreter is inside an environment:
- virtualenv: `Scripts\python.exe` on Windows, `bin/python` elsewhere
- conda: `python.exe` at the environment's root on Windows, `bin/python` elsewhere

Accept a candidate only if **both** checks pass, run from the project root:
- the file exists, and
- it can import the project: `<candidate> -B -c "import <package>"`, where `<package>`
  is the top-level package of the audited files (the first folder in their path that
  has an `__init__.py`, e.g. `gradio` for `gradio/workflow.py`; for a `src/` layout,
  the folder under `src/`). Skip this check only if the files aren't in a package.

If a candidate fails, try the next. If none passes, tell the user what you tried and
why each failed, and ask which Python to use. Never use one that failed a check, and
never fall back to the system Python silently. `<python>` below means the one that
passed.

## 3. Scan

```bash
<python> -B "${CLAUDE_PLUGIN_ROOT}/scripts/scan.py" --root <project root> <files...>
```

- "everything": use `--all` instead of listing files.
- Uncommitted changes, or changes since a branch: list the changed files, and add `--lines <file>:<a>-<b>` for each
  changed hunk (from the `@@ ... +a,count @@` headers; `b = a + count - 1`). For each
  new untracked file, add `--lines <file>:1-999999` so the whole file counts.

It prints a short JSON summary: how many candidates, the batch files (25 candidates
each), and the index file. If it lists `unreadable` or `callers_not_searched` files,
tell the user which ones and why. If `template_files_skipped` is above 0, mention it in
one line (`5 template files skipped`); don't list them. If `candidates` is 0, say no
fallbacks were found and stop.

## 4. Score

- **10 or fewer candidates:** skip scoring and test all of them.
- **More than 10:** launch `rug:rug-scorer` agents, each with up to 2 batch file paths,
  **at most 6 in the same message** (send the next 6 when they finish). Each returns
  `{id, score, reason}` for every candidate in its batches.

Check every candidate id from the index came back exactly once. If a batch's ids are
missing, send that batch to one scorer again. If they are still missing, list those
candidates as "not scored" in the report; never drop them silently.

Sort by score, highest first. Pick the **top 10 with a score of 20 or more**. Don't ask
the user first; testing 10 is the default.

## 5. Run the auditors in parallel

Split the picked candidates into groups so several auditors can work at once:
- Keep candidates from the same file in the same group, so each file is read once.
- Aim for about 4 candidates per group, and at most 5 groups.

Launch one `rug:fallback-auditor` subagent per group, **all in the same message**,
and wait for every one to finish before reporting. Give each one the project root's
absolute path, the Python interpreter's absolute path, the plugin's scripts folder as
an absolute path (`${CLAUDE_PLUGIN_ROOT}/scripts`, expanded), and its candidates (file:line, function, and the
scorer's reason; for `except` candidates, the line of the `except` clause).

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
   Proof: ran it · line check: ran · faked: the rate service (local server that fails instantly)

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
- If candidates were scored, add `Score: <n> (<reason>)` under each problem's header
  line, so the user can see why it was picked. Scores come from a short snippet, so
  they decide what gets tested, not how serious a problem is; order problems by what
  the tests showed (below), not by score. Say this once, on the line after the
  `Python:` line: `Scores pick what to test; they don't rank how serious it is.`
- Each problem cites its proof: the line check and what was faked. If the auditor could
  not trigger a fallback, or the line check says the test never reached it, list it
  under `Couldn't prove` with the reason, instead of guessing what it does.
- List fallbacks that are handled fine (the failure is logged or raised, and not
  reported as success) or can't happen under `Not a problem`, one line each with why.
- Put the most serious problems first: wrong data or money before a failure reported
  as success, before missing output, before slower or worse results, before cosmetic.
- Put the Python path in backticks, so the terminal doesn't swallow backslashes.
- If scored candidates were left untested, add:
  `<M> more scored 20 or above, not tested. Say "test next 10" to continue.`
  and, if any scored under 20: `<K> scored under 20 and were skipped (likely fine).`
- End with: `Say "fix #N" to fix one. Nothing has been changed.`

"test next 10" runs steps 5–6 on the next 10 by score, continuing the numbering.

## 7. Fixes, only on request

Do not edit any code until the user asks for a specific fix. Keep each problem's
trigger test path from the auditor's findings; the fix check below re-runs it.

### 7a. Ask how to fix it

Only the owner knows whether the fallback was intended, so ask first. Offer 2-3
options, recommend one with a short reason, and let them describe their own:

```
Fix #1 pricing.py:13 get_price: how should it handle a missing product?
  1. Raise an error naming the product  (recommended: a missing price is a bug)
  2. Log a warning and still return 0
  3. Leave it: 0 is intended (adds a comment saying so)
  Or describe your own.
```

Base the recommendation on where the failure comes from:
- The project's own code or data: remove the fallback so the error surfaces, or raise
  a clear error that names the missing thing.
- Outside (network, AI services, third-party APIs, user input): keep handling the
  failure, but log it or surface it instead of staying silent or reporting success.
- Both (a broad `except` around an outside call that can also hide the project's own
  bugs): narrow it to the outside errors, and log those.

Wait for the answer.

### 7b. Fix it

Make the change they chose, and only that change. Never delete files.

### 7c. Check the fix didn't sweep anything new under the rug

1. Re-run that problem's trigger test, for an `except` through the line check as the
   auditor did:
   `<python> -B "${CLAUDE_PLUGIN_ROOT}/scripts/line_check.py" --file <file> --line <except line> <test>`
   (for other candidates, `<python> -B <test>`). The behavior must now match their
   choice: for example, it raises, or it logs. If the except line moved or was removed,
   use the new line, or run the test directly. If the test file no longer exists, say
   you couldn't re-run it; never report the check as passed.
2. Scan only the lines you changed:
   `<python> -B "${CLAUDE_PLUGIN_ROOT}/scripts/scan.py" --root <project root> --lines <file>:<a>-<b> <file>`
   Any candidate there, other than the original fallback when the user chose to keep
   it, is a new hiding spot.

### 7d. Report

Show the exact before/after of the code, then the check:

```
Fixed #1 pricing.py:13 get_price
  Before: get_price('bananna') -> 0, nothing logged
  After:  get_price('bananna') -> KeyError: 'bananna'   (line check: ran)
  New hiding spots in the fix: none
```

Say it's done only if both checks pass. If one fails, say exactly what's wrong (the
test still returns 0, or the fix added a new hiding spot at a line) and offer to try
again. Never undo the user's code on your own. If the project has tests for that file,
suggest the command to run them; don't run the whole test suite yourself.
