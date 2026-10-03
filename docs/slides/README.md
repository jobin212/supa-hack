# Songbot mobile slides

Three portrait slides, exported at 1206 × 2622 pixels for viewing on a phone.

1. [Opening slide](01-songbot-opening.png)
2. [Simple data flow](02-songbot-data-flow.png)
3. [Closing slide](03-songbot-closing.png)

[Download the editable PowerPoint deck](songbot-mobile.pptx).

The diagram shows the logical data flow. The Python worker on Supabase Compute
makes the calls to Ando, Gemini, Lyria, and Storage. Postgres stores job progress.
The diagram text and arrows are editable in the deck.

The opening and closing slides share the generated background artwork in
`assets/songbot-wave.png`. Slide text remains editable.
