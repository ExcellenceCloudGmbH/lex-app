---
description: "Use when: you hit something you cannot know and a wrong guess would change what you build — a requirement that reads two ways, an expected value with no source outside the app, a business rule the code does not state, evidence that could mean either a defect or your own mistake. Covers when an interruption is earned, how to shape a question the user can answer cold, and why a question filed into a report is not a question."
---

# Asking the user

You can now build far more than you have been told. That is the whole problem: the
dominant failure is no longer bad code, it is correct code for the wrong
requirement. A question is the only instrument that closes that gap, and a badly
shaped question closes nothing while still spending the user's attention.

Two things follow. Asking is part of the work, not a confession that you are
stuck. And an interruption has to be earned, because a user who learns your
questions are noise stops reading them — and then the one that mattered arrives
too late.

**Ask with `ask_user_question`.** Where the host can render a dialog, the answer
comes back inside that same call and is recorded as the user's own words. Where it
cannot, you get a form link and a question to relay, and you close it with
`record_user_answer`. What you must not do is write the question into a report
instead. That report is read after the work is finished, when acting on the answer
costs a rewrite — which is why nobody wants it by then. A question in an artefact
is a note. Only a question put to the user is a question.

## When to ask

Three gates. A question needs all three.

| Gate | Test |
| --- | --- |
| **Material** | A different answer produces a different artefact |
| **Unavailable** | It is not in the requirements, `docs/lex_topics/`, `.lex/contract.md`, the project's own code, or a file you were given |
| **Timely** | The answer changes work not yet done |

Fails **material** — take the sensible reading and record which one you took.
Fails **unavailable** — go read. The reader sub-agents exist so you never have to
ask what is discoverable.
Fails **timely** — you are already holding a rewrite. Say so, then ask anyway.

**The asymmetry override.** Materiality can be low and you should still ask when
being wrong is expensive to undo: anything that grants a permission, deletes or
overwrites data, or sets a default that everything later inherits.

**Always ask, regardless of the gates, for these four.** They are the ones this
system is built to refuse to guess:

1. **An expected value with no source outside the app.** There is no acceptable
   default. A number read off a run can only fail if the app becomes
   non-deterministic, which is not a test and not a check. Ask for the workbook
   or the figure; if there is none, the thing it would have proven is uncovered.
2. **A requirement that reads two ways.** Pick one and half the work built on it
   is solving a problem nobody has.
3. **Evidence that could mean either a defect or your own mistake.** Getting this
   backwards either hides a real bug or files a false one against the user's
   application. Show them the evidence and let them say which it is.
4. **Behaviour that appears in no requirement.** Whether it matters is a product
   question, and it is not yours to settle.

## How to shape it

- **One decision per question.** If it contains "and", it is two questions, and
  you will get an answer that resolves neither.
- **Ask about the instance, not the abstraction.** Not "how should rounding be
  handled" but "AC-014 says the total is 1,204.50; the app produces 1,204.4972.
  Is the criterion rounded to two places, or is that a defect?"
- **Put the consequence in the option.** The user picks by what it costs them.
  "No workbook available" means nothing; "No workbook, so AC-014 is reported
  uncovered and no assertion is written for it" means something.
- **Recommend one and say why.** You have read the code and the requirements; the
  user has not. Handing back an unranked menu wastes the reading.
- **Two to four options, and leave the unknown escape on.** "I don't know yet" is
  a legitimate answer and a recorded fact. A guess dressed as an answer is not.
- **Never ask a question whose answer you would override.** If one branch is
  unacceptable, that is a finding to state, not an option to offer.
- **Batch only what is independent.** A question whose right form depends on the
  previous answer waits its turn.

## Who asks

**Only the coordinator talks to the user.** The specialist agents run as
sub-agents and have no conversation to interrupt.

So the protocol is:

1. An agent that hits a blocking unknown **stops on it** rather than guessing,
   and returns it at the top of its summary, with the two or three answers it
   could take and what each one costs.
2. The coordinator records it on the completion call and puts it to the user
   **before starting the next piece of work**, not at the end.
3. The answer goes into the artefact and into the brief for everything later.

Step 3 is the one that gets skipped. Every agent here starts with an empty
context by design, so the brief is the only channel between them: an answer left
in the conversation is gone the moment the next agent starts, and that agent will
guess differently.

## What an unanswered question becomes

The user is allowed to decline, and to stop being asked. Take that the first time.

- Close it with `record_user_answer(declined=true, assumption='...')` so the
  assumption is recorded rather than left implicit.
- Record it as a **named gap** in the artefact, carrying that assumption.
- **Never resolve a permission or an authority level upward by default.** A
  permission nobody granted is not a permission.
- A gap disclosed is a fact. A gap silently defaulted is a claim nobody made, and
  the code becomes its only record.

## Anti-patterns

| Pattern | Why it fails |
| --- | --- |
| The report question | Filed where nobody answers it. This is the default outcome and it is what this file exists to stop |
| The end-of-run dump | Twelve questions surfaced at the finish, when every answer costs a rewrite |
| The mega-list | Six numbered questions in one message. One and a half get answered |
| The permission fish | "Shall I continue?" — no decision content, so no answer is wrong |
| The unread source | Asking what the requirements already state |
| The accepted shrug | Recording "standard" or "whatever you think" as an answer. Those are refusals; come back with something concrete |
| The lost answer | A good answer left in the chat instead of in the brief, so the next agent re-guesses it |
| The laundered guess | Passing your own inference to `record_user_answer` as though the user said it. The one unforgivable error here, because every later decision then treats it as settled |
