# Beyond Audubon: other public-domain plates for species coverage

Research pass, 2 Sep 2026 (W-701). Featherframe renders from the 435 Havell
plates of *The Birds of America*, now fully cached by `make plates-all`. This
note surveys the other historical, public-domain folios that could cover what
Audubon never painted, and where each species the house has actually heard
could come from. Nothing here is wired in yet; a second `ArtProvider` is a
follow-up.

The ground rule carries over unchanged: **never a wrong bird.** Every plate
below was checked against its own caption or the host library's catalogue
record, and anything that only *probably* shows the species is marked so. A
composite plate (several species on one sheet) is shown whole, as today.

## What the house has heard that Audubon never painted

From BirdNET-Go's all-time list on CT 113 (150 species, 2 Sep 2026), cross-
referenced with `species.yaml` (since W-702, `folios/havell.yaml`). Confidence is about species identity, not
scan quality.

### Birds

| Species | Best plate | Scan | Size (px) | Confidence | Notes |
|---|---|---|---|---|---|
| European Starling | Gould & Richter, *Birds of Asia* IV, *Sturnus vulgaris* (1867–72) | [Commons](https://commons.wikimedia.org/wiki/File:SturnusVulgarisGould.jpg) | 4059 × 5170 | High | Single bird, hand-coloured litho. Alt: Naumann Taf. 62 on Commons (1948 × 3272) |
| House Sparrow | Gould, *Birds of Great Britain* III pl. 32 (1862–73) | [KU Libraries](https://digital.lib.ku.edu/ku-gould/8801) (TIFF via `/islandora/object/ku-gould%3A8801/datastream/OBJ/download`) | ~4000 × 5500 (75 MB TIFF, not stated) | High | Catalogued *Passer domesticus* |
| Rock Pigeon | Gould & Richter, *Birds of Asia* VI, "*Columba livia intermedia*" | [Commons](https://commons.wikimedia.org/wiki/File:BirdsAsiaJohnGoVIGoul_0232.jpg) | 4465 × 6990 | High (species) | Indian subspecies, but a standard wild-type Rock Dove. Avoid the Kuhnert dove composite |
| Mute Swan | Gould, *The Birds of Europe* V (1837) | [Commons](https://commons.wikimedia.org/wiki/File:The_birds_of_Europe_(1837)_(14563988068).jpg) | 5552 × 3718 (landscape) | High | Lear/Gould attribution unconfirmed. Alt: Naumann Taf. 295 |
| Ring-necked Pheasant | Gould & Richter, *Birds of Asia* VII, *Phasianus colchicus* | [Commons](https://commons.wikimedia.org/wiki/File:BirdsAsiaJohnGoVIIGoul_0144.jpg) | 6992 × 4524 (landscape) | High | Nominate race, no neck ring. For a ringed bird: *Birds of Great Britain* IV pl. 12, [KU](https://digital.lib.ku.edu/ku-gould/8571). Other vol. VII pheasant files are other taxa |
| European Goldfinch | Gould, *Birds of Great Britain* III pl. 36 | [KU Libraries](https://digital.lib.ku.edu/ku-gould/8785) | ~4000 × 5500 (75 MB TIFF) | High | **Do not** use Commons `BirdsAsiaJohnGoVGoul_0076.jpg`: that is the grey-headed *caniceps* and looks wrong |
| Black-headed Gull | Gould, *Birds of Great Britain* V pl. 64 | [KU Libraries](https://digital.lib.ku.edu/ku-gould/7993) | ~4000 × 5500 (69 MB TIFF) | High | Catalogued *Larus ridibundus*. Thorburn's 1915 gull plate is an 8-species composite, avoid |
| Caspian Tern | Fuertes, Eaton *Birds of New York* I pl. 8 (1910) | [Commons](https://commons.wikimedia.org/wiki/File:Birds_of_New_York_(Plate_8)_BHL14746651.jpg) | 3168 × 2273 | High, composite | Caspian + Royal + Black Tern: whole plate only |
| Least Flycatcher | Fuertes, Eaton *Birds of New York* II pl. 68 (1914) | [Commons](https://commons.wikimedia.org/wiki/File:Annual_report_(1912)_(18243188319).jpg) | 2316 × 3032 | Medium, composite | Five look-alike *Empidonax* + pewee on one sheet; whole plate only, never crop |
| Yellow-bellied Flycatcher | same plate 68 | same | 2316 × 3032 | Medium, composite | |
| Nelson's Sparrow | Fuertes, Eaton *Birds of New York* II pl. 61 | [Commons](https://commons.wikimedia.org/wiki/File:Annual_report_(1912)_(18241602688).jpg) | 2388 × 2914 | Medium, composite | Nine sparrows. Wilson's "Sharp-tailed Finch" and Havell 149 are the Saltmarsh Sparrow (Nelson's was described 1875): **wrong bird** |
| N. Rough-winged Swallow | Audubon/Bowen, *Birds of America* royal octavo I pl. 51 "Rough-winged Swallow" (1840) | [BHL item 124833](https://www.biodiversitylibrary.org/item/124833), [archive.org](https://archive.org/details/birdsofamericafr01audu) | ~3000 × 2000 est. (300 ppi) | High | Audubon's own type illustration, post-Havell so absent from the mirror. Alt: Eaton pl. 88 swallows composite |
| Veery | Havell pl. 164 "Tawny Thrush, *Turdus wilsonii*" | [Commons](https://commons.wikimedia.org/wiki/File:164_Tawny_Thrush.jpg) (and already in `plates/img`) | 11096 × 16048 | Disputed | Traditionally the Veery, but Halley (2018) argues the painted bird is not; the Aug 2026 pass pinned `plate: none` on purpose. Leave it unless Wells wants the traditional reading |
| Red Junglefowl | G. E. Lodge, Beebe *Monograph of the Pheasants* II (1921) | [Commons](https://commons.wikimedia.org/wiki/File:Red_Junglefowl_by_George_Edward_Lodge.png) | 2600 × 1916 | High | US-PD (pre-1930); Lodge d. 1954 so not PD under life+70. Alt: 1879 *Trans. Zool. Soc.* "Gallus ferrugineus" (Iconographia IZ17000081) |

### Mammals

Audubon & Bachman, *The Viviparous Quadrupeds of North America* (1845–48),
is the obvious sibling folio: same hand, same Bowen lithography, same paper.

| Species | Plate | Scan | Confidence | Notes |
|---|---|---|---|---|
| Eastern Gray Squirrel | pl. 7 "Carolina Grey Squirrel" (1845) | [UMich](https://quod.lib.umich.edu/s/sclaudubon/x-b6719889/29377_0009), [NYPL](https://digitalcollections.nypl.org/collections/the-viviparous-quadrupeds-of-north-america) | High | Captioned *Sciurus carolinensis*. UMich/NYPL block scripted fetches; the octavo on [archive.org](https://archive.org/details/quadrupedsofnort02audu) (300 ppi) is the verified downloadable fallback |
| Eastern Chipmunk | pl. 8 "Chipping Squirrel, Hackee, *Tamias lysteri*" | UMich collection (plate id unconfirmed), NYPL | High | *T. lysteri* = *T. striatus*. Commons copy is only 565 × 763 |
| Coyote | pl. 71 "Prairie Wolf, *Canis latrans*" (1846, J. W. Audubon) | UMich `x-b6719890/29376_0041` (unverified), NYPL | High | Alt: Lizars, Jardine's *Naturalist's Library* (Iconographia IZ22200391, 2515 × 3264) |
| Southeastern Myotis | none | | | Named 1897; no pre-1930 illustration exists. Stays with the AI provider |

### Insects

Nothing clean. The pre-1930 orthoptera and cicada literature is line art in
composite figure plates, and the crickets are either absent or a different
species (Blatchley 1920 figures *Hapithus agitator*, not *saltator*; the
19th-century "*Microcentrum retinerve*" figures are ambiguous with
*rhombifolium*). Best of a poor set:

| Species | Source | Scan | Verdict |
|---|---|---|---|
| Oblong-winged Katydid | Lutz, *Field Book of Insects* pl. XIX (1918) | [Commons](https://commons.wikimedia.org/wiki/File:Field_book_of_insects_(6244366674).jpg), 1784 × 3280 | Labelled, but a composite line-art plate |
| Swamp Cicada | Joutel, "Insects Affecting Oak" pl. 16 (1902) | [Flickr/IA](https://www.flickr.com/photos/internetarchivebookimages/19176060470/), 2090 × 3057 | One figure on a multi-insect plate |
| Dog-day Cicada, Greater Anglewing, the three crickets | none usable | | Keep on the AI provider |

Conclusion for insects: the AI provider (already caching 32 species) remains
the right answer; a historical source would not reach "never a wrong bird".

## The collections

All are public domain in the US (published before 1930). "Scans" means the
best programmatic, high-resolution source found; "Index" is whether plate →
species exists in machine-readable form or has to be built.

| Collection | Plates | Covers | Scans | Index | Style fit with Havell |
|---|---|---|---|---|---|
| **Gould (& Lear), *The Birds of Europe*, 1832–37** | 448 hand-coloured lithographs, 5 vols | Every European species, so all the introductions: Starling, House Sparrow, Rock Dove, Mute Swan, Pheasant, Goldfinch, Black-headed Gull (all confirmed in vol. 1's General List of Plates) | Smithsonian 450 ppi on BHL/IA: `birdsEuropeIGoul` … `birdsEuropeVGoul`; Commons category *The Birds of Europe (Gould)* has ~577 files, some at 5552 × 3718 | Half-built: the General List is in the IA OCR (`birdsEuropeIGoul_djvu.txt`), one parse gives plate → English + Latin; plate → leaf number needs one walk of each volume | **Best.** One species per plate, life-size, light habitat vignette, engraved caption in the same place. Lear's plates are Audubon's equal |
| Gould, *The Birds of Great Britain*, 1862–73 | 367 lithographs (Wolf, Richter, Hart) | British species incl. all seven introductions | Smithsonian 300 ppi `birdsgreatbrita1goul` … `5goul`; KU Libraries Islandora TIFFs (~70 MB each) | Per-volume plate lists in OCR; plates unnumbered in the book | Very good; heavier painted backgrounds than *Europe* |
| Dresser, *A History of the Birds of Europe*, 1871–96 | 723 lithographs, 678 by Keulemans | Complete Western Palearctic | Smithsonian 300 ppi quarto `historyofbirdsof12dres` … `19dres` (~2700 × 3600 page) | None found; plate lists in each volume's OCR; plates are bound by family, not number | Very good (Keulemans pairs with vignette); smallest page of the folios |
| Naumann, *Naturgeschichte der Vögel Mitteleuropas*, 1897–1905 | ~449 chromolithographs, 12 vols | Central Europe, all introductions | Smithsonian 450 ppi `Naumann1Naum` … `Naumann12Naum`; Commons category names files by species but at ~650 × 855 (use as the index only) | Half-built via Commons descriptions (Band, Tafel) | Fair: flatter "textbook" chromolithographs, but tonal backgrounds convert well to gray |
| **Forbush, *Birds of Massachusetts*, 1925–29 (Fuertes, Brooks)** | 93 plates (91 original paintings online) | Essentially every New England species incl. Starling, House Sparrow, Ring-necked Pheasant. No Mute Swan, Rock Dove, Goldfinch, Black-headed Gull | **Digital Commonwealth** IIIF, scans of the original paintings at 3878 × 5127, "no known copyright restrictions": `https://iiif.digitalcommonwealth.org/iiif/2/<id>/full/full/0/default.jpg`; one JSON search call lists all 91 | **Free:** the item titles are the index ("Plate 62: Rusty Blackbird, Starling, Purple Grackle, Bronzed Grackle"). Typos in titles; plates 38 and 43 missing | Poor as a drop-in: 1920s watercolour/gouache, every sheet a 3–7 species composite. Excellent as a whole-plate fallback tier |
| Eaton, *Birds of New York*, 1910–14 (Fuertes) | 106 colour plates, ~300 birds | Every regular New York species | Smithsonian 300 ppi `birdsofnewyork11eato` (pt. 1); Cornell `cu31924090314828` (pt. 2); Commons has pt. 1 at 3168 × 2273 but pt. 2 only at 1215 × 1608 | "Explanation of plates" in the OCR, parseable | Colour halftone (screen dots visible at 16 levels), composites |
| Wilson, *American Ornithology*, 1808–14 (+ Bonaparte 1825–33) | 76 (+27) hand-coloured engravings | ~260 Eastern species | Smithsonian 405 ppi `Americanornitho1Wils` … `9Wils`; better: Brown's 1835 Edinburgh re-engraving `IllustrationsAm00Brow`, all plates on Commons at 4339 × 5819 | None; build by hand (an afternoon) | Same era and medium, but every plate is a 3–8 species composite with small, stiff birds |
| Catesby, *Natural History of Carolina…*, 1731–43 | 220 etchings, ~109 birds | Southeastern species | Smithsonian 450 ppi `naturalhistoryCc1v2Cate`; Commons BHL pages at 4033 × 6629; NGA prints CC0 | None; a 2013 identification paper is the crosswalk | Outlier: flat, naive, bird-plus-plant. Avoid Royal Collection Trust scans (own terms) |
| **Audubon & Bachman, *Viviparous Quadrupeds of North America*, 1845–48** | 150 hand-coloured lithographs (J. T. Bowen) | Eastern mammals: chipmunk (8), gray squirrel (7), coyote "Prairie Wolf" (71), raccoon (61), opossum (66), woodchuck (2), red fox (6), skunk (42), cottontail (22), deer (81). **No bats** (excluded on Bachman's advice) | Wellcome 350 ppi `b22014421_0001/2/3`; UMich hi-res set at quod.lib.umich.edu (free with attribution, blocks scripted fetches); Commons category at 2500 px, files named by species | Complete 150-row plate list exists online; Commons names double as an index | **Same studio as the octavo *Birds*: the only true style match on this list** |
| Insects (Marlatt 1907 cicada plate, Holland's *Butterfly/Moth Book*, Brehm, Lutz, Blatchley) | few | Not the yard's cicadas, katydids or crickets as single subjects | IA/BHL | n/a | Line art or pinned-specimen halftones; not usable |

### Fetching in bulk

There is no GitHub mirror like `nathanbuchar/audubon-bird-plates` for any of
these; plan on a small fetcher of our own. Two findings make that cheap:

- **BHL page images sit on a public S3 bucket, no key needed.**
  `https://www.biodiversitylibrary.org/pageimage/<pageID>` redirects to
  `https://bhl-open-data.s3.us-east-2.amazonaws.com/web/<ia_id>/<ia_id>_<seq:04d>_full.webp`,
  and the documented full-resolution path in the same bucket is
  `images/<ia_id>/<ia_id>_<seq:04d>.jp2`. `<seq>` is the Internet Archive leaf
  number from 0001 with no gaps, so any BHL-scanned volume is addressable by
  (IA id, leaf). Verified: Gould *Birds of Europe* plate 433 `_full.webp` is
  3658 × 5561; the JP2 should be roughly 6000 × 9500 (450 ppi imperial folio,
  not verified). `aws s3 ls --no-sign-request s3://bhl-open-data/` lists it;
  `/data/` holds TSV exports (`pagename.txt.gz` maps page IDs to the taxon
  names OCR found on each page, a noisy but free plate → species index).
  Docs: github.com/gbhl/bhl-open-data, registry.opendata.aws/bhl-open-data.
- **archive.org's on-the-fly JPEG is downscaled** (about one third: 1864 × 2785
  for a 450 ppi folio page). For full resolution use the `_jp2.zip`
  (200–600 MB per volume) or the BHL S3 path above.
- **Digital Commonwealth (Forbush)** is plain IIIF: `/full/full/0/default.jpg`.

### Recommendation

Ranked by coverage of what this frame actually hears per unit of effort:

1. **Gould, *The Birds of Europe*** for the introductions. House Sparrow is the
   single most-detected species at the house (38,729 detections, no plate) and
   Starling is 32 more; both are single-bird, hand-coloured lithographs that
   will hang beside a Havell plate without reading as a different frame. Seven
   species, one OCR parse of the plate list, one leaf walk per volume.
2. **Audubon & Bachman, *Quadrupeds*** for squirrel, chipmunk and coyote (and
   the raccoon, fox, skunk, opossum and deer BirdNET will eventually claim to
   hear). Same lithographer as the octavo *Birds*, single subjects, index
   ready-made, ~150 plates.
3. **Forbush via Digital Commonwealth** as a whole-plate fallback tier for the
   remaining birds (the *Empidonax* flycatchers, Nelson's Sparrow, Caspian
   Tern): the best scans on the list and a free index, at the cost of
   composite sheets in a 1920s painterly style. Eaton and Wilson add nothing
   Forbush lacks; Dresser and Naumann are the fallbacks if Gould's plate list
   turns out harder to parse than expected.

Northern Rough-winged Swallow is the one bird best served by Audubon himself:
royal octavo plate 51 (1840), outside the Havell mirror but on BHL item 124833.

### Shape of a second provider (built as folios in W-702; see AGENTS.md)

- Generalise the crosswalk: a `species.yaml` entry gains `source:` (default
  `havell`) so one species can say `{source: gould_europe, plate: 217}`;
  `fetch_plates.py` grows a per-source fetcher and writes one
  `plates/<source>/index.json` each. Existing entries are untouched.
- `Artwork.audubon_plate` becomes `source` + `plate`; the caption's credit
  line changes per source, and the corner's "Plate CLIX" mark (the Havell
  number, W-821) would need that folio's own numbering or nothing.
- Chain order stays Havell → other folios → AI, so a real plate always beats a
  generated one and the never-a-wrong-bird contract is unchanged.

## Gould's other folios: *Australia*, *Asia*, *Great Britain* (W-869)

This is a survey pass made on 25 Sep 2026, before any of the three is added the way W-702 added
*The Birds of Europe*. The working files are in `docs/gould-survey/`:

- `<folio>-plates.csv` holds every plate, with its volume, number, IA leaf, the name as printed, a
  draft modern name and BirdNET V2.4 label, and whether Havell or *Europe* already has it.
- `<folio>-contact.jpg` shows five or six plates through the *Europe* pipeline, each as raw scan,
  flattened sheet and extracted crop.

The crosswalks in the CSVs are drafts from one pass, and the leaf pairings were checked by
thumbnail and OCR, not caption by caption. No row is ready to pin.

The three folios share five findings, and each one changes the W-702 method:

- **Smithsonian holds a complete copy of all three on BHL,** and the JP2 masters are on the
  public `bhl-open-data` S3 bucket. The folio header's `scans` template from *Europe* works
  unchanged. The scans are about 265–320 ppi, against *Europe*'s 450, but that is still about 3×
  the panel's height.
- **Plates are numbered per volume.** The printed List of Plates in each volume numbers them 1–N,
  and a plate is cited as "Australia, ii. pl. 18". The sheet itself carries only the engraved
  Latin caption, with no number and no English name. *Europe*'s `plate:` was one running number,
  so these folios need a volume in the plate key, and the corner mark would read "Plate II. 18".
- **There are no pencilled numbers.** None were found in any folio. None are needed, though:
  each Smithsonian copy is bound in List order at a fixed stride (a plate, then its text leaves),
  so the walk that took the longest for *Europe* is a script over IA's scandata here.
- **Much more of each folio is landscape:** 28% of *Australia*, 17% of *Asia* and 50% of
  *Britain*, against a handful in *Europe*. `rotate: 270` handles every one of them, and the
  painted backgrounds survive `flatten_paper` well: none was eaten, and the sky clears to white.
  The open question is layout: a 3:2 scene contain-fitted on a 3:4 panel leaves the bird small.
- **The binding shows.** The page stack, the gilt board edge or the gutter shadow survives the
  2% margin on some volumes. When it does, the tight crop either keeps a grey sliver or fails
  outright and keeps the whole sheet with its caption. This needs per-volume margins, or a
  book-edge detector.

### *The Birds of Australia* (1840–48) and *Supplement* (1851–69)

**Scans.** There are 681 plates in 8 volumes, all held by the Smithsonian:
`birdsAustraliav1Goul` … `birdsAustraliav7Goul` and `birdsAustraliasSuppGoul`. The per-volume
plate counts are 36, 104, 97, 104, 92, 82, 85 and 81. Each count matches that volume's List of
Plates exactly once the endpapers are dropped.

- A single sheet is about 3700 × 5770 px. For example,
  `images/birdsAustraliav2Goul/birdsAustraliav2Goul_0012.jp2` is pl. II.1, the Owlet-nightjar.
- Other copies:
  - The State Library of NSW (`birdsaustralia{1,2,3,5,6}goul`, `…4goula`,
    `birdsaustraliasuppgoula`, about 336 ppi) is incomplete, missing vols 4 and 7. It includes
    two colourist's copies.
  - Florence (`IT-F10098-TO013793{54..64}_Images`).
  - Commons (815 files, named inconsistently).

**Numbering.**

- Each volume prints a List of Plates in systematic order. Gutenberg has clean transcriptions
  of vols I–V (65002, 60302, 60646, 60833, 62524).
- Vol. I's Introduction has a synoptical table of all 681 species, with a "Vol. N, Pl. M"
  reference for each.
- Plates follow every 4 leaves, in List order. No pencil marks were found on II.18, VI.48 or
  VII.6.

**Identification.** The Kansas Ellis Collection holds all 8 volumes (`ku-gould:15183`, `14749`,
`14343`, `13905`, `13519`, `13173`, `12815`, `12471`). It has one Dublin Core record per plate,
each giving the modern name. For example, `ku-gould:14150` is "Yellow-faced Honeyeater. 45.
plate", *Lichenostomus chrysops*, printed as *Ptilotis chrysops*.

- The site search is gone (404), so getting every record means walking about 3,400 contiguous
  ids.
- KU matched its modern names from the printed Latin, as it did for *Europe*, so it makes the
  same kind of mistakes: IV.82 is put on the Mauritius Olive White-eye.
- Sharpe's *Analytical Index to the Works of the late John Gould* (1893,
  `analyticalindext00shar_0`) cites every plate, but by Gould's names only.
- Not yet read: Mathews, HANZAB, Sauer, and Gould's own *Handbook* (1865,
  `handbooktobirdso01gou`/`02gou`).

**Coverage.** 641 plates resolve to 565 modern taxa.

- **In BirdNET V2.4:** 459 plates, covering 391 species (360 on high-confidence rows only).
- **New:** 362 of those species are in neither Havell nor *Europe*.
- **Names have moved a long way:** only 125 of Gould's printed binomials are still a BirdNET
  label verbatim, and 427 rows are a hand crosswalk that has not been checked yet.
- **No label (174 taxa):** examples are Wedge-tailed Eagle, Emu, both spoonbills, every
  *Neophema* and *Amytornis*, and most petrels.
- **Not Australian:** the Supplement and the main List include New Zealand and Pacific birds
  (kiwis, Kākāpō, Takahē, Huia, *Didunculus*).

**Cutting.** No sheet is a composite; each shows one species, with male, female and young.

- About 190 plates are sideways: all of VII, most of VI, V.63–92, and part of the Supplement.
- About 210 have heavy painted backgrounds.
- There are 3 double-page fold-outs (IV.8 and IV.10, the bowers, and Supp. 76). The fold runs
  through the art, so these are unusable.
- Pipeline results (`gould-survey/australia-contact.jpg`):
  - The Black Swan (VII.6, rotated) and the Lyrebird are excellent.
  - The Kookaburra keeps a sliver of the binding gutter on the right. The folio's right margin
    should be about 0.96.
  - The Brolga's ground runs into its caption, so the whole caption block rides in (it needs a
    bottom margin of about 0.93).
  - The Brush-turkey's yellow wattle goes nearly white in gray.

**Artists and credit.**

- Every plate is signed "J. Gould and H. C. Richter del. et lith.". The printers were
  Hullmandel, then Hullmandel & Walton, and Walter for the *Supplement*.
- Elizabeth Gould drew 84 plates. Richter drew about 595, most of them lithographed from her
  drawings. Lear and B. W. Hawkins drew one each.
- Artist line: "John and Elizabeth Gould, with H. C. Richter". The credit line is *Europe*'s.

### *The Birds of Asia* (1850–83)

**Scans.** There are 530 plates in 7 volumes, all held by the Smithsonian:
`BirdsAsiaJohnGo{I,II,III,IV,V,VI,VII}Goul`. The per-volume plate counts are 76, 75, 78, 72,
83, 75 and 71, and each matches its List.

- A sheet is 4465–5111 × 6990–7666 px, about 320 ppi. For example,
  `BirdsAsiaJohnGoVGoul_0076.jp2` is 4849 × 7285. IA's own ppi field is wrong for these
  volumes.
- The other copies are not usable: the `india.history.resource.1005xx` PDFs are low-resolution,
  and the 1969 reprint is restricted. The Commons files are the same Smithsonian scans.

**Numbering.**

- Each volume prints a List of Plates (vol. I, leaves 25–26; the others, leaves 9–10). It says
  plates "may be quoted by" its numbers.
- Plate n is at leaf `first + 4(n−1)`, with `first` = 28 for vol. I and 12 for the others. This
  was checked by caption on V.17, VI.56, IV.22, VII.34 and VII.69.
- No pencil marks were found.

**Identification.**

- KU holds this folio too (Ellis Aves H120). Its plate records sit around
  `ku-gould:15300–17700`, e.g. `15841` = VI.61 and `16147` = V.72. It also holds Gould's
  preparatory drawings (around 18000–20400).
- Sharpe's 1893 index cites every plate as "Asia, iv. pl. 49".

**Coverage.**

- **In BirdNET:** 254 plates, 99 as exact binomials and 155 as probable renames.
- **New:** 242 of them are in neither Havell nor *Europe*.
- **Overlap:** 12 are in *Europe* (e.g. Red-throated Pipit, Rustic Bunting, Siberian
  Rubythroat) and 1 is in Havell (the pheasant).
- **Not in BirdNET:** 121 plates.
- **Unplaced:** 68 plates.
- **The trap:** 87 plates (16%) show a *form* Gould named as a species that is now a subspecies,
  so they are the caniceps trap at scale. Examples:
  - V.17 *Carduelis orientalis* (the grey-headed goldfinch);
  - *Pica bactriana*;
  - *Pyrrhula* forms;
  - *Cinclus leucogaster*;
  - *Sturnus humii*;
  - *Falco babylonicus*;
  - four *Phasianus* races.

  These must never stand in for the nominate bird. Pin only exact and probable rows, and check
  the exact ones too: I.35 *Merops viridis* may be Green, not Blue-throated, Bee-eater.
- **Worth a decision:** VII.39 *Phasianus torquatus* (leaf 164) is the ringed stock introduced to
  North America. It would be a better Ring-necked Pheasant than any plate we have, but formally
  it is a form.

**Cutting.** There are no composites.

- About 90 plates are sideways: nearly all of VII, about 17 in VI, 8 in IV and 3 in V.
- Vols. I–V are light vignettes, like *Europe*. VI–VII, and IV's dippers and chats, are full
  painted ovals.
- Pipeline results (`gould-survey/asia-contact.jpg`):
  - The pigeon, dipper, pheasant and Mandarin plates cut well, with their landscapes kept.
  - **V.17 fails:** the page stack at the left survives the 2% margin, so the tight crop keeps
    the whole sheet and its caption. A left margin of 0.08 fixes it. Vols. I and IV look the
    same.
  - Dark plumage (dippers, the pheasant) crushes toward black in gray.

**Artists and credit.** Gould & H. C. Richter drew the 1850s–60s parts, Wolf & Richter many of
the gamebirds, and Gould & W. Hart the 1870s–83 parts. The printers were Hullmandel & Walton,
then Walter. R. B. Sharpe finished the text after Gould died in 1881. Artist line: "John Gould,
with H. C. Richter, Joseph Wolf and William Hart".

### *The Birds of Great Britain* (1862–73)

**Scans.** There are 367 plates in 5 volumes, all held by the Smithsonian:
`birdsgreatbrita{1..5}goul`. The per-volume plate counts are 37, 78, 76, 90 and 86.

- A sheet is about 3600 × 5760 px (III.32, the House Sparrow, is 3572 × 5781). That is about
  265 ppi, and the JP2s are lossy: the lowest resolution of the three folios.
- KU holds a second copy (book objects `ku-gould:9261`, `8935`, `8625`, `8257`, `7895`). A page's
  id is the next book's id minus its IA leaf; for example, III.32 = `8801`. Each is an
  uncompressed 4374 × 6034 TIFF of the open book, so it would have to be cropped to the sheet.

**Numbering.**

- Each volume prints a List of Plates (vol. I at leaf 159, the others at leaves 9–10).
- The binding follows the List. All 367 plates are already paired to leaves, and OCR confirms
  350 of the pairs. One exception: a species with several plates can put its text after a
  plate (e.g. IV.8).
- No pencil marks were found.

**Identification.**

- Gould's *Introduction* (vol. I leaves 46–158; also `introductiontobi00goul`) lists every
  species with its Vol./Pl.
- Sharpe's 1893 index is a ready-made Britain↔*Europe* plate concordance: its OCR holds 332
  Britain plate numbers, 889 of its lines cite both folios, and it gives lines like "Aberdevine
  . Europe, iii. pl. 197; Gt. Brit. iii. pl. 37".
- KU's records for vols. II–V give modern names but have errors. For example, V.71 is labelled
  *"Sturnus paradisaea"*, and the plate is the Roseate Tern.

**Coverage.** 339 species, 328 of them in BirdNET. 317 are already pinned in *Europe* and 116 are
in Havell.

- **Certain additions:** only 7 species are in neither: Yellow-browed Warbler (II.68), Rock
  Pipit (III.10), Water Pipit (III.11), Little Bunting (III.25), Pallas's Sandgrouse (IV.11),
  Pink-footed Goose (V.3) and Ross's Gull (V.63).
- **Uncertain:** 2 more, Spotted Eagle and Bean-Goose, which are uncertain for the same reasons
  *Europe* left them out.
- **Europe gaps:** *Britain* gives Rock and Water Pipit separate plates, which settles *Europe*'s
  blank pl. 138. Its text also supports *Europe*'s pl. 149 = Pallas's Leaf Warbler.
- **Traps:**
  - II.62, printed *Curruca hortensis*, is an exact BirdNET label (Western Orphean Warbler), but
    the bird is the Garden Warbler.
  - Gould's *Sterna paradisea* is the Roseate Tern, and his *S. macrura* is the Arctic Tern.

**Cutting.** There are no composites.

- **Sideways:** 182 plates need `rotate: 270`, nearly all of vols. IV–V.
- **Nests and young:** about 105 plates (29%) show a nest, eggs or chicks, and this is the
  folio's character. Almost every wader, gamebird, gull and tern has its brood.
- **Backgrounds:** vols. IV–V are full painted landscapes. Only vol. II's small birds are bare
  sprays like *Europe*'s.
- **Pipeline results** (`gould-survey/britain-contact.jpg`):
  - The Rock Pipit seascape, Yellow-browed Warbler and Black-headed Gull colony cut cleanly.
  - On rotated sheets, the page-edge shadow leaves a grey band with a hard line at the foot, so
    landscape plates need a bottom margin of about 0.94.
  - The House Sparrow plate is three-quarters nest, with a small bird on it.
  - The Lapwing goes dark bird on dark ground.
- **Verdict:** it looks worse on the glass than *Europe* for the species they share.

**Artists and credit.** Gould made the sketches. H. C. Richter drew and lithographed the earlier
parts. W. Hart painted the plates and drew them on stone from part 22. J. Wolf drew some vol. I
raptors. The printers were Walter, then Walter & Cohn. Artist line: "John Gould, with H. C.
Richter, W. Hart and J. Wolf".

### Recommended order

1. ***Australia*.**
   - **Why:** 362 new BirdNET species, most of them light vignettes that cut like *Europe*'s.
   - **The long pole:** the crosswalk. Walk KU's plate records first and let the plate decide
     every disagreement, as for *Europe*.
   - **Also needs:** per-volume margins for the gutter, and the 3 fold-outs excluded.
   - **Effort:** about equal to *Europe*'s slice 3.
2. ***Asia*.**
   - **Why:** 242 new species. The walk is a formula.
   - **The work:** the crosswalk and the form gate. Pin only exact and probable rows, add V.17
     to the traps comment, and decide VII.39.
   - **Also needs:** per-volume left margins (I, IV, V) for the page stack.
   - **Effort:** about the same as *Europe*, spent mostly on the crosswalk.
   - **Value:** mostly for owners in Asia, through Region.
3. ***Great Britain*, as a gap-filler only.**
   - **What:** pin the 7 certain species (1–2 hours) behind *Europe*.
   - **Region:** the "Great Britain" Region should put *Europe* first rather than *Britain*. For
     the 317 shared species, *Britain* would give a rotated, nest-heavy, lower-resolution plate
     in place of a better one.
   - **Why not a Region folio of its own:** a British look alone does not justify one.

Before starting any of them, three changes to the method, all shared:

- a volume-scoped plate key and corner mark;
- per-volume margins, or a book-edge detector;
- a call on how a landscape scene sits on a portrait panel.
