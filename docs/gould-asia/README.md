# Gould's *Birds of Asia*: how the folio was pinned (W-871)

`server/scripts/folios/gould_asia.yaml` is the source of truth. `crosswalk.csv`
is the working record behind it: every one of the 530 plates, with the
survey's reading (`docs/gould-survey/asia-plates.csv`, W-869), the Kansas
catalogue's, and what was decided and why.

## Finding the plates

The Smithsonian copy (`BirdsAsiaJohnGo{I..VII}Goul` on BHL / Internet Archive)
is bound in the order of each volume's List of Plates: plate n is at leaf
`28 + 4(n−1)` in vol. I and `12 + 4(n−1)` in the others. A plate's engraved
caption is too small for the IA OCR, but the text leaf bound right after it
opens with the species' name, so every pairing was checked there
(`text_leaf`): all 530 have Gould's genus on the next leaf, and all but a few
where the OCR dropped a word have the epithet too.

## Identification

The survey matched each plate's printed Latin to a BirdNET V2.4 label. The
Kansas catalogue (University of Kansas Spencer Library, Ellis Aves H120,
`ku-gould:15300–17700`: 530 plate records, one per plate) gives a modern name
for each. As with *Europe*, KU's names were matched from the printed Latin,
not the birds, so they are a check, not an authority: it sends the Asian
trogons to New World ones and the Spoon-billed Sandpiper to Lady Amherst's
Pheasant. Where it gives an older genus or the pre-split parent, the pin
stands. Where it named a different bird, the plate was looked at:

- **Wrong in the survey:** IV.32 is Jerdon's Bushchat (not in BirdNET), not
  the Pied Bushchat; V.53 is the Sri Lanka Blue-Magpie, not the Common
  Green-Magpie; VII.63 is the Black-winged Pratincole and V.30 the Mongolian
  Finch, neither in BirdNET.
- **Forms**, left out like the survey's 86: II.75, III.16, III.53, IV.43,
  IV.70, V.35.
- **I.35** is printed *Merops viridis* but shows the Green Bee-eater (Wells).
- **VII.39** *Phasianus torquatus* is pinned as the Ring-necked Pheasant, the
  one form pinned on purpose (Wells); VII.34, the nominate, is left out.

Four rows (`pinned (review)`) were settled by the plate where KU and the
survey disagreed, and are on Wells's review page: V.6 is the White-capped
Bunting and V.11 the Gray-necked (the survey had them swapped), VI.62 the
Spotted Sandgrouse, VII.60 the Far Eastern Curlew (not the Eurasian).

## Cutting

No composites. 38 plates are bound sideways (most of vol. VII, the gamebirds
of VI, four in IV, one in V), each with its caption down the right edge:
`rotate: 270`, whole plate kept. Vols. I, IV and V show the page stack at the
left, so they carry a 0.08 left margin (`volume_margins`; turned to the top
for their sideways plates). An upright plate's bottom margin sits just above
its caption and artist line, from the OCR's own line boxes.
