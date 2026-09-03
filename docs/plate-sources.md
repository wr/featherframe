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
referenced with `species.yaml`. Confidence is about species identity, not
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

### Shape of a second provider (follow-up, not built here)

- Generalise the crosswalk: a `species.yaml` entry gains `source:` (default
  `havell`) so one species can say `{source: gould_europe, plate: 217}`;
  `fetch_plates.py` grows a per-source fetcher and writes one
  `plates/<source>/index.json` each. Existing entries are untouched.
- `Artwork.audubon_plate` becomes `source` + `plate`; the caption's credit
  line and the `Nº` mark already key off the species ordinal, so only the
  small-print credit changes per source.
- Chain order stays Havell → other folios → AI, so a real plate always beats a
  generated one and the never-a-wrong-bird contract is unchanged.
