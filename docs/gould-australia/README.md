# Gould's *Birds of Australia*: how the folio was pinned (W-870)

`server/scripts/folios/gould_australia.yaml` is the source of truth. These files are
the working record behind it, kept so a pin can be traced and the rows left out
can be revisited. The scans are the Smithsonian copy on BHL / Internet Archive
(`birdsAustraliav1Goul` … `birdsAustraliav7Goul`, `birdsAustraliasSuppGoul`); the
W-869 survey (`docs/plate-sources.md`, `docs/gould-survey/australia-plates.csv`)
found it and drafted the list.

- `plate-leaves.csv`: all 681 plates (volume, per-volume number, IA leaf), each
  leaf read against its engraved caption. 660 read as the listed plate; 20
  sideways captions are lost in the binding (the text leaf after each names the
  species); IV.80 is engraved *Myzantha viridis*, Gould's plate name for the
  Bell-bird. Also each plate's orientation and, for an upright one, where its
  caption starts (`caption_top`), from which its bottom margin is set.
- `crosswalk.csv`: every plate mapped to BirdNET V2.4's label, with a confidence
  and the reason, made from Gould's own text and synonymy.
- `ku-catalogue.csv`: the Kansas Ellis Collection's record per plate (books
  `ku-gould:15183` I … `12471` Supplement), walked from the contiguous object ids.
- `ku-check.csv`: the 69 plates where Kansas and the crosswalk disagree beyond
  a subspecies, an ending or a genus change, each settled: 34 the same bird
  under an older name or a lump, 31 Kansas errors (it matched the printed Latin,
  not the bird: it swaps V.15/V.16 and IV.93/IV.98), 1 crosswalk error (III.41,
  now left out) and 3 doubtful.
- `doubtful-decisions.csv`: the 24 plates first held back, researched one by
  one against the figure, Gould's text and the literature (`decision` is the
  research's call, `final` what was pinned). 15 pinned: the nine whose caption
  is lost in the binding (the figure and the text leaf after it agree), and
  IV.18, IV.80, V.10, V.84, VI.55 and Supp. 44. Left out: I.33 (the Tasmanian
  Boobook), IV.2 (the Banda Sea Pitta, no label), IV.8 and IV.10 (the fold),
  VI.2 (kiwi skins with no locality), VI.19 (Lesser and Greater Sand-Plover
  mixed), VII.21 (the skua's name is the Great Skua's), VII.51 (Gould's Petrel,
  not Cook's) and VII.83 (Northern or Southern Rockhopper).
- `review.csv`: the plates still left out of the pins, and why (second plates
  of a species already pinned among them).

A plate was pinned only when its crosswalk is `high`, its caption agrees, it is
no fold-out (IV.8, IV.10, Supp. 76), and Kansas agrees or its disagreement was
settled for the pin, or the doubtful research pinned it. One plate per
species: the one bearing its own name, else the first in the List. 401 species.

## Cutting

- **Upright plates** stop just above their engraved caption (`caption_top`
  less 0.006) and at 0.955 on the right: every volume shows a binding line at
  0.963–0.99 of the width.
- **Sideways plates** (97 pinned; all of VII, most of VI, V.63–92, some of the
  Supplement) are turned 270, or 90 for the three with the caption up the left
  edge (Supp. 73, 75, 79). Their margins are the box of the strong ink, padded
  1.2%: the caption and the binding's shadow along the bottom are faint on the
  cleared paper and fall outside it. Where the art runs into the shadow, the
  bottom was set by eye (0.955, or 0.94 where a faint gradient still showed).

Every pin's crop was reviewed on contact sheets.
