<!--
Distilled from nakamura001, "Codexの作成する技術ドキュメントを読みやすくする" (2026-10-09), a write-up
of what makes npaka's technical articles easy to follow, and adapted for this repository.
Source: https://nakamura001.hatenablog.com/entry/2026/10/09/Codexの作成する技術ドキュメントを読みやすくする
-->

# Document structure and procedure steps (both languages)

These rules govern the layout of a whole document and the writing of a procedure, in both
languages. They matter most in a document a reader works through while acting or learning: a how-to,
a setup guide, or an explanation of a feature. A roadmap item, whose prose argues a case, still
follows the rules on titles and openings, on heading order, and on how terms relate.

## Leave the reader nothing to guess

Readability does not come from short sentences. It comes from how little the reader must fill in
alone, and from always knowing where to read next, what to do next, and what result to check. After
drafting, check that a reader never has to guess any of these four points:

- the order to read in, and the order to work in
- how the terms relate to each other
- the concrete operation and the concrete input
- the condition that means the step succeeded

Readability is not the same as brevity, nor as writing for beginners. A document with long code, or
one that assumes prior knowledge, still reads well when it leaves none of the four to guess. Do not
cut a prerequisite or a way to confirm success to make a document shorter. The rule against spelling
out steps a reader can fill in alone covers intermediate reasoning; an operation, an input, and a
success condition are never treated as something the reader can fill in.

## Titles and openings

- **Narrow the question in the title.** State the task, the environment or tool it uses, and the method.
  Weak: "About Playwright". Strong: "Install Playwright's Chromium on Linux". A reader can tell what
  the document delivers before opening it, and the body stays on the purpose the title names.
- **Keep the preamble short.** Skip greetings and long background; state the subject, what the author tried,
  and the environment it assumes, then start.
- **Gather the prerequisites in one place.** Put versions, the operating system, and any setup the reader must finish
  beforehand in a section of their own instead of scattering them through the body, so a reader can
  decide at once whether the document applies to their environment.

## Heading order

Each language layer covers how to word a single heading. This section governs the order the
headings come in.

- **Follow the reader's order.** Arrange the headings in the order the reader works, or in the order
  the reader's questions arise.
- **Explain the parts before the procedure that uses them.** Never make the reader carry the name of
  an unexplained component through a sequence of steps.
- **Start from a standard shape.**
  - *How-to:* what the reader can do, prerequisites and environment, the shortest procedure, checking
    the result, then variations and notes.
  - *Explanation:* what problem exists, what the technique does, its components and their roles,
    then examples of use.

## Writing one procedure step

Build each step from five units. Not every step needs all five; add a unit whenever its absence would
force the reader to guess.

- **Purpose:** what this step accomplishes.
- **Operation:** where to act (which screen, file, or location) and what to do there.
- **Input:** the code, the command, or the setting value.
- **Check:** what output or generated artifact means the step succeeded.
- **Note:** a reason or caution needed at this point, and nothing more.

When writing those units, follow three rules.

- **Put the instruction next to what the reader uses verbatim.** Do not stop at "install the
  package"; give the command, the file name to create, or the setting value directly below the
  instruction.
- **Give each self-contained operation its own step.** Installing, logging in, and confirming the
  login become three steps, each with its own explanation and code block, so a reader in the middle
  of the work never loses their place.
- **Show the result of success, not the input alone.** Include the expected log, the generated file,
  the screen, or the measured number. Do not leave the reader wondering whether a run that printed no
  error actually worked.

## How terms relate

- **Explain components along one shared axis.** Do not stop at listing names. State each
  component's role and how the components relate, along the same axis for every component. Weak: listing
  the host, the client, and the server of the Model Context Protocol (MCP) like glossary entries.
  Strong: separating the three by role and arranging their capabilities by which side provides them
  to which.
- **Keep a name once chosen.** Repeat the same name instead of varying it; the anaphora rules in
  [`SKILL.md`](../SKILL.md#minimize-anaphora-both-languages) cover the rest.

## The main path and the extras

- **Make the main path a single road.** Run it end to end in one environment with one
  configuration. Move other environments, other models, and fine-grained parameter tuning to a later
  section or an appendix.
- **Serve both kinds of reader.** A first-time reader follows the single road to the end; a reader
  returning to look something up jumps straight to the section they need.

## Visual breaks and semantic breaks

- **Give each element one job.** Headings, short explanations, lists, code blocks, and images each
  do different work, and a visual break falls where the meaning breaks.
- **Place an image or an output right after the sentence that describes it.** Never make the reader
  match a sentence against a screenshot or a command's output placed somewhere else.
