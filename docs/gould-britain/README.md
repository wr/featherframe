# Gould's *Birds of Great Britain*: the seven pins (W-872)

`server/scripts/folios/gould_britain.yaml` is the source of truth. This is the
record behind it.

## Why only seven

The W-869 survey (`docs/plate-sources.md`, `docs/gould-survey/britain-plates.csv`)
paired all 367 plates to their leaves in the Smithsonian copy
(`birdsgreatbrita{1..5}goul`). 328 of the book's 339 species are in BirdNET, and
317 of those are already pinned from *Birds of Europe*. For those shared
species, *Britain*'s plates are worse on the glass: half are landscape, about
29% show nests or chicks, vols. IV–V have fully painted backgrounds, and the
scans are about 265 ppi and lossy.

So *Britain* only fills gaps. Its header names the Europe region, and a test
(`test_britain_never_pins_what_europe_does`) holds that it pins no species
`gould_europe.yaml` pins. The two folios share a region, so a shared species
would be decided by the index's file order. With no shared species, that can
never happen. *Great Britain* is not a Region: a UK owner picks Europe.

## The pins

Each pin was checked against the engraved caption on its scan (25 Sep 2026).

| Species | Vol. / pl. | Leaf | Caption | Orientation |
|---|---|---|---|---|
| Yellow-browed Warbler | II. 68 | 276 | REGULOIDES SUPERCILIOSUS | Upright |
| Rock Pipit | III. 10 | 46 | ANTHUS OBSCURUS | Upright |
| Water Pipit | III. 11 | 50 | ANTHUS SPINOLETTA | Upright |
| Little Bunting | III. 25 | 106 | EMBERIZA PUSILLA, Pall. | Upright |
| Pallas's Sandgrouse | IV. 11 | 50 | SYRRHAPTES PARADOXUS | Landscape, `rotate: 270` |
| Pink-footed Goose | V. 3 | 20 | ANSER BRACHYRHYNCHUS, Baill. | Landscape, `rotate: 270` |
| Ross's Gull | V. 63 | 260 | RHODOSTETHIA ROSSII | Landscape, `rotate: 270` |

The two pipits also settle *Europe*'s pl. 138, "Rock or Shore Pipit, *Anthus
aquaticus*". That plate stays unpinned, because the two pipits were one species
then and it could show either.

## Cutting

The captions sit low, at about 0.93 of the sheet. When a sideways sheet is
stood up, the page edge throws a grey band with a hard line along its foot, at
about 0.94. One folio margin, `[0.02, 0.02, 0.98, 0.92]`, stops above both, and
the tight crop finds the paper gap under the art. The landscape plates keep the
whole plate. None of the seven needed a per-plate or per-volume margin.

## Traps, for anyone who pins more

- **II.62:** printed *Curruca hortensis*, which is BirdNET's exact label for the
  Western Orphean Warbler. The bird is the Garden Warbler (*Sylvia borin*).
  II.61, *Curruca orphea*, is the Orphean.
- **V.71 and V.72:** Gould's *Sterna paradisea* (V.71) is the Roseate Tern, and
  his *S. macrura* (V.72) is the Arctic Tern, today's *S. paradisaea*. KU labels
  V.71 "*Sturnus paradisaea*".
- **Still unpinned:** Spotted Eagle (I.3) and Bean-Goose (V.2), for the reasons
  *Europe* left them out.

## The concordance

Sharpe's *Analytical Index to the Works of the late John Gould* (1893,
`analyticalindext00shar_0`) gives both folios' plates on one line, e.g.
"Aberdevine . Europe, iii. pl. 197; Gt. Brit. iii. pl. 37". Its OCR holds 332
*Britain* plate numbers, and 889 of its lines cite both folios. Start there to
map a *Britain* plate to its *Europe* counterpart.
