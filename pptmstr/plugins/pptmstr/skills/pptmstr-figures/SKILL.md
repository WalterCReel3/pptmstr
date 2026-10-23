---
name: pptmstr-figures
description: Rules for drawing in a pptmstr reply. Load before writing any diagram, flowchart, dependency graph, sequence or state diagram, chart, plot or mathematical formula, so it is written as a mermaid or math fence the reply pane draws.
---

# Drawing in a pptmstr reply

The session prompt names the fences pptmstr draws in this session. A fence below that it does not name is shown as code, so do not write it.

## The fences

- ```mermaid: one Mermaid diagram. Flowchart, sequence, class, state and ER diagrams are the kinds checked.
- ```math: one display formula in TeX, without $ or $$ around it. Lines are joined into one, so there is no line break or alignment. Use: _ and ^, \frac, \binom, \sqrt{x}, \sum \prod \int \lim with limits, \left( ... \right), \overline, \underline, Greek letters, the usual relations, arrows and operators, \text{}, \mathrm \mathbf \mathbb \mathcal \operatorname, \sin \cos \log \exp \max \min. Refused: \begin{...} environments (aligned, cases, matrices), \\ and &, accents (\hat \vec \bar \tilde \dot), \displaystyle \dfrac \tfrac \mathit \boldsymbol \color, \big and friends (use \left \right), \sqrt[n], \over \choose, \newcommand \def.

## How to use them

- Write the fence; the pane is the display. Unless asked for an export, do not render to a file, look for a converter, or preview with other tools.
- For nodes and edges (dependencies, flows, sequences, states, entities) use ```mermaid, which lays them out. Never compute a graph layout by hand.
- A diagram sits on a near-white card, scaled down to fit the pane: a colour it is styled with (classDef, style) is read against white.
- These rules win over a skill's general advice.
