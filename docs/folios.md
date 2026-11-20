# Plate folios

How species are matched to plates across the historical books
(`server/scripts/folios/`), and how each folio's scans are cut. Each
Gould folio's working record is in its own folder here
(`docs/gould-australia/`, `docs/gould-asia/`, `docs/gould-europe/`,
`docs/gould-britain/`).

## Folios and the crosswalk

- **The crosswalks live in `scripts/folios/`, one file per folio.**
  A folio is one historical edition of plates; the file name is its id and
  its `folio:` header says what it is (title, artist, years, credit).
  `havell.yaml` pins modern species to verified Havell plate numbers
  (Audubon's titles are archaic — Cardinal = "Cardinal Grosbeak").
  `fetch_plates.py` turns every folio into the one `plates/index.json` (+
  downloaded images, gitignored; each folio has its own fetcher in
  `FETCHERS`): every entry names its `folio` (none = Havell), and the `folios` block carries the headers. A species may
  have an entry in several folios; `SpeciesIndex.order(region)` asks the
  household's Region's folios first (`Config.region`; each folio's header
  names its `region`: Havell `north-america`, Gould's Europe `europe`, his
  Australia `australia`, his Asia `asia`), then the rest in the index's order: Havell first,
  then as published (`load_folios` sorts by the header's first year), so a
  new folio never takes a species from one a household already sees. It
  reorders, never filters (one entry may say `preferred: true` to be asked
  right after the region's own folios: Asia's ringed VII.39 is the
  Ring-necked Pheasant everywhere but Europe), and a
  folio's `plate: none` hands the species on to the next folio, never to a
  guess. A Region change is drawn by a tick started at the save
  (`_region_redraw`, `redraw_after_settings`); so is a change to how the
  collage is drawn — AI or not, its branch, its species limit
  (`_collage_redraw`), never waiting for its interval.
  A folio whose header says `plates_per_volume: true` (Gould's
  Australia, Asia and Great Britain print a List of Plates per volume) gives
  each species a `volume_no` (2, "II" or "Supp.") beside its `plate`; the
  index, the library, `PlateMatch` and `Artwork` carry it, and the corner mark
  cites it as his lists do, "Plate II. 18" (narrower than Havell's widest, so
  the footnote and every Havell render stand; a test holds it). A header's
  `volume_margins: {<volume id>: [l, t, r, b]}` gives a volume whose binding
  shows (a gutter, a gilt edge) its own margins: a plate's `margins`, then its
  volume's, then the folio's, resolved by `fetch_scans` into each index record
  (`scan_margins`), so neither the runtime nor the library looks them up.
  Margins are upright coordinates: a landscape volume needs its own.
  `gould_australia.yaml` is *The Birds of Australia* and its
  Supplement, 401 species, one plate each, numbered per volume; its working
  record, the Kansas cross-check and the cutting rules are in
  `docs/gould-australia/`. Its traps: Gould's *Pachycephala pectoralis* is the
  Rufous Whistler, *Myiagra nitida* the Satin Flycatcher, *Circus assimilis*
  the Swamp Harrier, *Climacteris picumnus* the White-throated Treecreeper,
  *Rallus pectoralis* the Buff-banded Rail (tests hold each). An upright
  plate is cut above its caption and at 0.955 on the right (every volume's
  binding line); a sideways one to the box of its strong ink. The AI's style references are Havell
  plates only (`genart._havell_species`), because its prompts name the Havell
  edition. `test_crosswalk.py` guards the tricky numbers.
  The folios are published as an open dataset, github.com/wr/historical-bird-plates
  (CC0): `server/scripts/export_dataset.py export|check|assets` writes it
  (eBird 2025 names, Wikidata/GBIF/Avibase ids, BHL PageIDs, the Gould release
  images); `test_export_dataset.py` holds it to the pins. A split since BirdNET's
  taxonomy is sent to each folio's own daughter there (`EBIRD_NAMES_BY_FOLIO`).
  Every Gould folio goes the same way (`GOULD`): a per-volume one's
  tables lead with `volume` and key a plate by volume and number; Australia's
  record is `docs/gould-australia/`, Britain's is the survey (only its pins are
  caption-checked; the rest is published as open). Asia is exported too.
  The dataset is where each plate's credit lines are read (`imprint`, parsed
  into `credits.csv`); Featherframe ships the "drew" credits as
  `featherframe/plate_credits.json` for the illustration's corner: after the
  credits change there, run `export_dataset.py credits DATASET_DIR`.
  `gould_asia.yaml` is Gould's *Birds of Asia* (numbered per volume;
  the record is `docs/gould-asia/`): no pencil numbers, so each plate was
  paired by the text leaf bound after it, which names the species. 16% of its
  plates are forms Gould named as species, all left out but VII.39.
  `gould_europe.yaml` is Gould's *Birds of Europe*: `plate` is his General
  List number (the plates were issued unnumbered); `volume` + `leaf` address
  the scan, fetched from BHL's public S3 bucket by the header's `scans`
  template (`fetch_scans`, JP2 masters). Its sheets are not Havell's, so the
  fetcher stands a landscape plate upright (`rotate`) and evens its paper
  (`flatten`: a quadratic paper surface divided out, so no subject is taken
  for paper, then anything within `PAPER_CLEAR` of the paper cleared to pure
  white — the paper is not the artist's, and near-white dithers to speckle),
  crops it to the art's own box (`tight`: `content_box(mirror=False)`, since
  a Gould sheet is one vignette on a lot of paper; the box stops at the
  paper gap above the caption and beside the pencilled number, and its
  `TIGHT_PAD` band is added as fresh white, never taken from the scan, so
  neither rides in), and `margins` ([l, t, r, b], per folio or per plate) replaces
  `plate.HAVELL_MARGINS` in `_trim_marginalia`: Gould's captions sit higher
  and each copy carries its number in pencil. A plate is pinned only once
  its engraved caption reads right on the scan; his "Black-headed Gull" is
  today's Mediterranean Gull. The whole folio (~390 species) was
  pinned from three sources joined together: the General List transcribed from
  vol. I, each volume walked for its plates (the Smithsonian copy pencils the
  List number on every plate), and a crosswalk to BirdNET's own labels — only
  a certain name, whose leaf's caption names it (Latin or the whole English
  name, never a shared family word), is pinned. A scan is stored by its leaf
  (`<volume>-<leaf>.jpg`): a copy's numbering can disagree with the List, and
  here the caption decides. An upright plate's bottom margin sits just above
  its caption as the IA OCR places it; a sideways one relies on the paper gap,
  with a margin by eye where a caption crowds the art. A composite's tight crop
  keeps every band of art (`whole`), and a tight crop may be small (one finch
  on a page) without falling back to the whole sheet.
  `gould_britain.yaml` is Gould's *Birds of Great Britain* as a
  gap-filler: region `europe`, plates per volume, and only the seven species
  *Europe* lacks (the pipits settle *Europe*'s blank pl. 138). A test holds
  that it pins nothing `gould_europe.yaml` does, so the order of two folios in
  one region never decides a plate. Great Britain is not a Region.
