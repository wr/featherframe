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
