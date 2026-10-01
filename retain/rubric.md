# Retain extraction comparison rubric

You get one source document (a coding-agent session transcript, commit history
or note from a project's memory bank), its context, and two lists of facts,
**A** and **B**, that the same memory system extracted from it under two
configurations. You are not told which is which. Decide which fact list would
serve the project's memory better, or call it a tie.

The pair file gives the retain `mission`, `extraction_mode` and any
`custom_instructions` both extractions were given (the bank's retain strategy
for this kind of document). Judge against that mission. In `concise` mode fewer,
denser facts are intended; in `verbose` mode more detail per fact is expected.

Judge, in this order:

1. **Faithful.** Every fact must be supported by the document. A fact that
   invents or distorts a name, value, file, decision or outcome, or attributes
   something to the wrong actor or time, is a serious error. Check facts against
   the document text.
2. **Covers what the mission asks for.** What the mission asks to keep (for
   example decisions and their rationale, conventions, tooling, gotchas, exact
   identifiers and issue references) should be captured when the document
   has it. Missing a significant decision or its reason is a real loss;
   capturing transient chatter (retries, typos, intermediate dead ends that
   were not decisions) is a small loss.
3. **Useful as memory.** Each fact should stand on its own when read later
   without the document: concrete identifiers, values and reasons, not vague
   summaries or pronouns without referents. Redundant facts that restate each
   other are a small loss. More facts is not better in itself.

Call **tie** when neither list is clearly better on the criteria above,
including when both are good, both fail, or they trade off evenly. Prefer a tie
over a coin flip.

Write `<id>.verdict.json` next to the pair file:

```json
{"winner": "A" | "B" | "tie", "why": "one or two sentences"}
```
