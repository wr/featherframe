# Gould's *Birds of Europe*: how the folio was pinned (W-702)

`server/scripts/folios/gould_europe.yaml` is the source of truth. These files are
the working record behind it, kept so a pin can be traced and the rows left out
can be revisited:

- `general-list.csv`: the General List of Plates, transcribed from vol. I
  (leaves 23–26) by reading the page images. There are 449 numbers; some plates
  carry two or three species.
- `plate-leaves.csv`: every plate leaf in the five volumes, with its pencilled
  List number, its engraved caption and its orientation, read from the scans of
  the Smithsonian copy (`birdsEurope{I..V}Goul` on BHL / Internet Archive).
- `crosswalk.csv`: each List row mapped to BirdNET V2.4's own label, with a
  confidence and the taxonomic reason.

A row was pinned only when its crosswalk is `high` and its leaf's caption names
it (by Latin epithet or whole English name). The `medium` and `low` rows are the
open questions: most can be settled by looking at the plate.

## Cross-check against the Kansas catalogue (25 Sep 2026)

`ku-catalogue.csv` is the University of Kansas Spencer Library's Ellis
Collection copy (`ku-gould:11233`, `10819`, `10429`, `9999`, `9571`): each
General List plate with its printed name and the modern scientific name in its
Dublin Core record. Those modern names were matched from the printed Latin, not
from the birds, so they are a check, not an authority. 409 of the 410 pins match
a KU plate. About 370 agree outright, or KU gives the pre-split parent. Every
disagreement was resolved in favour of the pins:

- **KU errors:** 14, 50, 65, 67, 75, 86, 108, 137, 149, 205, 360 and 442. Some
  are unrelated species (plate 360, the Shoveler, is labelled a warbler); some
  are Latin look-alikes.
- **Crossed names:** 219 and 391. The plate decides: 219 has the Red-billed
  Chough's long, curved red bill, and 391 has the Black-necked (Eared) Grebe's
  dark neck and fanned ear plumes. Gould's Latin names have since moved to the
  other species, which is why KU matched them the other way.
- **133:** pinned to both Icterine and Melodious, as decided.
- **Composites:** on the brace plates, KU's subject field names only one figure.
