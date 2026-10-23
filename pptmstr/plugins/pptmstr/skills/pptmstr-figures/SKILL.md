---
name: pptmstr-figures
description: Rules for drawing in a pptmstr reply. Load before writing any diagram, flowchart, dependency graph, sequence or state diagram, chart, plot or mathematical formula, so it is written as a mermaid, svg or math fence the reply pane draws.
---

# Drawing in a pptmstr reply

The session prompt names the fences pptmstr draws in this session. A fence below that it does not name is shown as code, so do not write it.

## The fences

- ```svg: one SVG image. It must be self-contained: no <script>, no event attributes, no <image>, no <foreignObject>, no filters or patterns, and no reference outside the document (url(#id) and href="#id" only). Give width and height, or a viewBox. End every font-family with serif, sans-serif or monospace.
- ```mermaid: one Mermaid diagram. Flowchart, sequence, class, state and ER diagrams are the kinds checked.
- ```math: one display formula in TeX, without $ or $$ around it. Lines are joined into one, so there is no line break or alignment. Use: _ and ^, \frac, \binom, \sqrt{x}, \sum \prod \int \lim with limits, \left( ... \right), \overline, \underline, Greek letters, the usual relations, arrows and operators, \text{}, \mathrm \mathbf \mathbb \mathcal \operatorname, \sin \cos \log \exp \max \min. Refused: \begin{...} environments (aligned, cases, matrices), \\ and &, accents (\hat \vec \bar \tilde \dot), \displaystyle \dfrac \tfrac \mathit \boldsymbol \color, \big and friends (use \left \right), \sqrt[n], \over \choose, \newcommand \def.

## How to use them

- Write the fence; the pane is the display. Unless asked for an export, do not render to a file, look for a converter, or preview with other tools.
- For nodes and edges (dependencies, flows, sequences, states, entities) use ```mermaid, which lays them out; ```svg is for what Mermaid cannot draw. Never compute a graph layout by hand.
- Figures sit on a near-white card, scaled down to fit the pane: design for white, with literal colours, text of 11 px or more, and at most about 600 px wide.
- These rules win over a skill's general advice.
