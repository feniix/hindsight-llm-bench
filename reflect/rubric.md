# Reflect answer comparison rubric

You get one question, the memory bank it was asked against, and two answers,
**A** and **B**, produced by the same system under two configurations. You are
not told which is which. Decide which answer is better for the person who asked,
or call it a tie.

Judge, in this order:

1. **Correct and grounded.** No invented facts, names, values or decisions.
   Answers are supposed to be drawn from the project's memory; an answer that
   asserts something specific and implausible, or contradicts the other answer
   on a checkable detail, loses unless the other is worse. You may not look
   anything up; judge from internal consistency and specificity.
2. **Answers what was asked.** Covers the parts of the question (for
   knowledge-page questions: what is being built or changed, why, and current
   state; for survey questions: real instances of the category, scoped to the
   named project). Missing a requested part is a real loss.
3. **Useful detail.** Concrete identifiers, values and reasons beat vague
   summary. Length alone is not a virtue: do not prefer the longer answer unless
   the extra length carries information the asker needs.

When the pair file also has `A_sources` and `B_sources` (the memories, mental
models and directives each answer's agent actually used), check grounding
against them under criterion 1: a specific claim that none of that answer's own
sources support counts as invented, even if it sounds plausible. Sources differ
between the two answers; judge each answer against its own. Do not reward an
answer for having more sources.

Call **tie** when neither answer is clearly better on the criteria above,
including when both are good, both fail, or they trade off evenly. Prefer a tie
over a coin flip.

Write `<id>.verdict.json` next to the pair file:

```json
{"winner": "A" | "B" | "tie", "why": "one or two sentences"}
```
