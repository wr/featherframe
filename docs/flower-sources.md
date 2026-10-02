# A flower edition: the source and the plates

Research pass, 2 Oct 2026 (W-945). A flower edition would show the plants
flowering near the owner as historical botanical plates, the way the bird
frame shows what a detector hears as Audubon's. This note measures the one
candidate source, iNaturalist, month by month in five places, and the public-domain
plate collections that could illustrate what it finds. Nothing here is built.
The species lists are in `docs/flower-survey/`.

## Summary

- **Observers' own flowering marks are too sparse to drive a frame.** Near
  Hartford, research-grade observations marked *Flowers* within 25 km name
  0–35 species in a two-week window, and only 9% of plant observations carry
  any flower or fruit mark.
- **A flowering calendar works.** A species counts as in flower near you if it
  was observed within 25 km in the last year and observers across the region
  have marked it *Flowers* in this month in past years. That gives 91–167
  species a month near Hartford from April to September, and 172–398 near
  London.
- **Cold winters run dry.** From mid-November to February, Hartford and
  Minneapolis have 0–2 species in flower even within 100 km. The same calendar
  finds 16–36 species *in fruit* each winter month (winterberry, partridgeberry,
  staghorn sumac), and most of them have a plate that shows the fruit.
- **North America:** Walcott's *North American Wild Flowers* (400 colour
  plates, 1925) is the natural lead. It covers 16% of what flowers near
  Hartford, which is 26–39 species a month in spring and 10–22 in summer.
  Britton & Brown's line drawings cover 75%.
- **Europe:** *Flora Danica* (3,240 hand-coloured engravings, 1761–1883) is the
  natural lead. It covers 57% of what flowers near London, or 72% when
  weighted by how often each species is observed.
- **California and Australia** are not covered by anything surveyed (13–19%).
  They need their own survey.
- **The Gould pipeline cuts these plates** once paper flattening is on. Each
  collection needs a mask for its own marks: KB's scale bar and stamp,
  Britton & Brown's scale fractions.

## 1. The source, month by month

All counts are from the iNaturalist API, research grade, plants only
(`taxon_id=47126`), species rank, within 25 km of the city centre, in the
window 8–21 of each month, Oct 2025 – Sep 2026. iNaturalist's annotation for
this is *Flowers and Fruits* (`term_id=12`): *Flowers* (13), *Fruits or Seeds*
(14), *Flower Buds* (15).

**Species marked *Flowers*, two-week window, 25 km**

| Place | Oct | Nov | Dec | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Hartford CT | 3 | 5 | 0 | 0 | 0 | 1 | 18 | 35 | 12 | 9 | 24 | 12 |
| Minneapolis MN | 15 | 6 | 0 | 0 | 0 | 1 | 21 | 82 | 103 | 108 | 111 | 74 |
| San Francisco CA | 57 | 46 | 37 | 61 | 91 | 187 | 174 | 133 | 124 | 89 | 71 | 25 |
| London | 51 | 21 | 28 | 18 | 24 | 68 | 90 | 80 | 95 | 53 | 47 | 26 |
| Melbourne | 77 | 59 | 63 | 28 | 28 | 30 | 32 | 24 | 21 | 26 | 60 | 87 |

**Plant species observed at all, same windows**

| Place | Oct | Nov | Dec | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Hartford CT | 143 | 122 | 98 | 24 | 19 | 78 | 148 | 218 | 275 | 230 | 270 | 218 |
| Minneapolis MN | 255 | 121 | 27 | 14 | 44 | 83 | 155 | 325 | 478 | 431 | 470 | 376 |
| San Francisco CA | 280 | 272 | 291 | 263 | 413 | 580 | 591 | 574 | 558 | 396 | 372 | 289 |
| London | 396 | 271 | 175 | 211 | 268 | 314 | 420 | 448 | 513 | 368 | 331 | 248 |
| Melbourne | 469 | 414 | 404 | 307 | 262 | 315 | 312 | 342 | 306 | 329 | 368 | 428 |

The share of plant observations with any *Flowers and Fruits* mark, over the
year: Hartford 9%, Minneapolis 13%, San Francisco 14%, London 15%, Melbourne
13%. Most observers never mark flowering, so the live marks undercount it
several times over, and Hartford's iNaturalist community is small to begin
with (4,018 research-grade plant observations in the twelve windows, against
14,742 near Minneapolis).

**The calendar instead.** For each place: every species observed within 25 km
in the year (978 near Hartford, 1,575 near Melbourne), kept in a month if the
whole region (Connecticut, Minnesota, the Bay Area, the United Kingdom,
Victoria) has marked it *Flowers* in that month in any year at least 3 times,
and that month holds at least 8% of its marks. Species in flower near you:

| Place | Oct | Nov | Dec | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Hartford CT | 28 | 2 | 0 | 0 | 1 | 12 | 91 | 167 | 152 | 148 | 144 | 106 |
| Minneapolis MN | 28 | 1 | 0 | 0 | 1 | 10 | 66 | 226 | 344 | 324 | 300 | 177 |
| San Francisco CA | 62 | 29 | 26 | 69 | 176 | 401 | 611 | 566 | 396 | 290 | 198 | 129 |
| London | 67 | 20 | 12 | 19 | 59 | 132 | 275 | 361 | 398 | 384 | 276 | 172 |
| Melbourne | 371 | 316 | 282 | 195 | 113 | 97 | 110 | 58 | 53 | 76 | 166 | 359 |

A month is coarse: the calendar should run by week of year
(`/v1/observations/histogram?interval=week_of_year`, per species) before it
ships. The lists are dominated by what people photograph most, which near
Hartford includes garlic mustard, mugwort and poison ivy. A flower edition
needs a selection rule, and a curated collection like Walcott's is one.

Gardens are invisible: research grade excludes cultivated plants, and marked
captive flowering species within 25 km run 0–2 per window near Hartford and
2–39 near London. An owner's own garden would need its own list.

## 2. Winter

The coldest window (8–21 Jan 2026), species marked *Flowers*:

| Place | 25 km | 50 km | 100 km | 25 km, whole month | 100 km, whole month |
|---|---:|---:|---:|---:|---:|
| Hartford CT | 0 | 0 | 2 | 0 | 2 |
| Minneapolis MN | 0 | 0 | 0 | 0 | 0 |
| San Francisco CA | 61 | 109 | 159 | 96 | 227 |
| London | 18 | 20 | 36 | 47 | 75 |
| Melbourne (July) | 26 | 74 | 98 | 61 | 154 |

A wider radius does not help where nothing flowers. The calendar's winter months
near Hartford are witch hazel (Nov–Dec) and skunk cabbage (late Feb), then
snowdrop, winter aconite and bloodroot in March.

**Fruit fills it.** The same calendar with *Fruits or Seeds*:

| Month | Hartford: in fruit (≥ 3 marks) | Minneapolis: in fruit (≥ 3 marks) |
|---|---:|---:|
| Dec | 26 | 36 |
| Jan | 26 | 29 |
| Feb | 16 | 25 |

Near Hartford in January: Oriental bittersweet, partridgeberry, spotted
wintergreen, winterberry, wintergreen, shinleaf, multiflora rose. Near
Minneapolis: staghorn sumac, American bittersweet, common buckthorn,
highbush cranberry. Of Hartford's 26 January species, 21 have a Britton &
Brown drawing and 4 a Walcott plate. Walcott painted 20 species twice, once
in flower and once in fruit (winterberry is plate 54). Other answers for the
dry months: the species that flower first, counted down from late February,
or the year's flowers as one collage.

## 3. The plates

Coverage is the share of the year's in-flower species (section 1's calendar)
that have a plate. The figure in brackets is weighted by local observations,
so it is closer to how often a frame picking by abundance would find one.
Names are matched through GBIF's accepted species (section 4).

| Collection | Hartford | Minneapolis | London | San Francisco | Melbourne |
|---|---:|---:|---:|---:|---:|
| Walcott, plates | 13% (17%) | 9% (12%) | 1% (2%) | 2% (5%) | 0% |
| Walcott, plates + originals | 16% (22%) | 11% (14%) | 2% (3%) | 5% (9%) | 0% |
| Britton & Brown (USDA) | 75% (77%) | 73% (80%) | | | |
| Meehan | 7% (8%) | 4% (7%) | 1% | 1% | 0% |
| *Flora Danica* | 24% (27%) | 22% (28%) | 57% (72%) | 14% (14%) | 12% (13%) |
| Sturm | 21% (22%) | 17% (23%) | 47% (62%) | 12% (14%) | 9% (12%) |
| Lindman | 15% (21%) | 12% (21%) | 33% (50%) | 7% (9%) | 6% (8%) |
| Thomé | 14% (15%) | 12% (15%) | 32% (42%) | 7% (7%) | 5% (5%) |
| Sowerby (partial list) | 11% (11%) | 10% (11%) | 32% (39%) | 9% (10%) | 6% (7%) |
| All North American | 79% (82%) | 79% (85%) | 22% (29%) | 13% (17%) | 6% (6%) |
| All European | 28% (29%) | 24% (31%) | 69% (82%) | 19% (22%) | 15% (16%) |

Britton & Brown was looked up on USDA PLANTS only for the Hartford and
Minneapolis species, so it has no figure elsewhere. In-flower species over the
year: Hartford 458, Minneapolis 713, London 790, San Francisco 957, Melbourne
780.

Species with a plate, month by month (Oct → Sep):

| Place, collection | Oct | Nov | Dec | Jan | Feb | Mar | Apr | May | Jun | Jul | Aug | Sep |
|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| Hartford, Walcott | 2 | 1 | 0 | 0 | 1 | 3 | 26 | 39 | 26 | 22 | 16 | 10 |
| Hartford, Britton & Brown | 16 | 1 | 0 | 0 | 1 | 8 | 66 | 127 | 125 | 124 | 108 | 75 |
| Minneapolis, Walcott | 2 | 1 | 0 | 0 | 0 | 4 | 21 | 42 | 45 | 31 | 21 | 12 |
| London, *Flora Danica* | 25 | 10 | 3 | 9 | 28 | 65 | 154 | 225 | 260 | 250 | 172 | 88 |
| London, all European | 33 | 12 | 4 | 12 | 33 | 81 | 190 | 272 | 308 | 287 | 199 | 108 |

What no North American collection covers near Hartford is mostly the Asian
shrubs that spread after 1913: autumn olive, burning bush, multiflora rose,
Japanese barberry, Oriental bittersweet, Japanese knotweed. Near London it is
garden escapes: green alkanet, buddleja, three-cornered garlic, Mexican
fleabane.

Unlike a detection, a flower frame chooses which in-flower species to show,
so a gap in coverage costs variety and never shows a wrong species. Walcott
alone gives 26–39 species a month near Hartford from April to June, and
10–22 from July.

### North America

**Mary Vaux Walcott, *North American Wild Flowers*** (Smithsonian, 1925–28,
5 vols, 400 plates). `flower-survey/walcott.csv`, complete: 401 rows (plate 4
holds two ladies'-tresses), 379 printed names, about 360 species. Colour
reproductions of her watercolours, one species a plate; 20 species have a
flower plate and a fruit plate. The plates print no name, only a small number
and her monogram; names come from each volume's contents and the index in
vol. 5. Scans: BHL title 67774 (Quarto: items 135496, 135963, 135969, 135967,
135968; IA `NorthAmericanwiIWalc` … `NorthAmericanwiVWalcA`), about
5000 × 6700 px a leaf, through `biodiversitylibrary.org/pageimage/<id>`.
Use the Quarto, whose pages BHL labels "Plate N". Public domain in the US
(1925–28) and under life + 70 (she died in 1940). Traps: there are no plates
78–80 (vol. 1 has 24a, 31a, 56a instead); the names are the 1925 American
Code (*Azalea lutea*, *Castalia odorata*), and GBIF files *Azalea lutea* under
the wrong azalea, so every name needs a hand check. *Wild Flowers of America*
(Crown, 1953) reuses her paintings and is still in copyright.

**Walcott's originals** at the Smithsonian American Art Museum: 791 records,
790 CC0, about 2100 × 3000 px. 615 are named with a Latin binomial
(`flower-survey/walcott_saam.csv`, 552 names). They add 15 species near
Hartford beyond the plates. They are the watercolours themselves, on her own
paper.

**Britton & Brown, *An Illustrated Flora of the Northern United States,
Canada and the British Possessions*** (2nd ed., 1913, 3 vols, 4,666 species).
Outline line drawings, uncoloured, one species a figure, with a scale fraction
and no name. USDA PLANTS serves them per species: `GET
plantsservices.sc.egov.usda.gov/api/PlantSearch?searchText=<Latin>`, then
`/api/PlantImages?plantId=<Id>` and the image credited "Britton, N.L., and A.
Brown"; the original is a 300 dpi gray TIFF, about 1250–1570 × 2000 px.
Of 918 Hartford and Minneapolis species (in flower, or in fruit in
January), 673 have one
(`flower-survey/britton_brown_usda.csv`), and 35 more were not found because
USDA uses an older name (*Anemone canadensis* for iNaturalist's
*Anemonastrum canadense*). Wikimedia Commons holds about 1,670 species, some
at higher resolution (median 1572 × 2000). Public domain; USDA marks them not
copyrighted.

**Thomas Meehan, *The Native Flowers and Ferns of the United States***
(1878–80, 4 vols, 192 chromolithographs). Partial list only (135 of 192), at
about 2600–3000 × 4000–4800 px (Boston Public Library on IA,
`nativeflowersfer11meeh` etc.). Too few plates to lead.

***Curtis's Botanical Magazine*** (1787 on). Vols 1–146 (to 1920) are on BHL
from Missouri Botanical Garden at about 1800 × 3100 px, but there is no
species index to match against: the 1906 index (IA `mobot31753002722434`)
would have to be read from OCR, about 6,100 of 7,900 plates on a first pass.
Its plates are garden and exotic plants, so it would serve a garden list or
houseplants. Later artists (Lilian Snelling,
d. 1972) are in copyright under life + 70.

**Redouté**, *Les Liliacées* (486 plates) and *Les Roses* (169). No usable
list: the Missouri Botanical Garden OCR of *Les Liliacées* is unreadable, and
150 of the 169 roses were read from the Les Roses OCR. Garden plants again;
public domain. Rawpixel's "enhanced" Commons copies are CC BY-SA and out of
plate order.

### Europe

***Flora Danica*** (1761–1883, 51 Hefte + 3 supplements, 3,240 hand-coloured
copper engravings). `flower-survey/flora_danica.csv`, complete, from the Royal
Danish Library's own index (`loar.kb.dk/handle/1902/49102`,
`Index_FloraDanica.xlsx`): 2,073 vascular plates, 1,825 modern names (as of
about 2010, so still through GBIF), the rest fungi, mosses, algae, lichens.
Every main-series plate is on a IIIF server,
`kb-images.kb.dk/DAMJP2/online_master_arkiv/non-archival/DUP/floradanica/h<HH>/floradanica_<NNNN>/full/full/0/native.jpg`,
about 1900–2300 × 3000–3450 px; the 180 supplement plates are only in the
TIFF zips. KB says the scans are free of copyright, commercial use included.
Each plate prints only "Flora Danica Tab. N". Traps: KB's 0–5 cm scale bar
and its red library credit are inside the picture; 625 plates carry 2–4
figures, but for flowering plants they are almost always the same species.

**Sturm, *Flora von Deutschland in Abbildungen nach der Natur*** (2nd ed.,
Lutz, 1900–07, 882 colour plates). `flower-survey/sturm.csv`: biolib's index
(`biolib.de/sturm/floraNN/`), 1,487 names on 1,222 pages, which include text
spreads, so it still has to be reduced to plates. 1278 × 1920 px a plate on biolib;
NYBG's 400–500 ppi copies are on IA (`jsturmsfloravond01stur` …). Each plate
prints "Tafel N", the German and Latin names, a frame line and lettered
dissections.

**Thomé, *Flora von Deutschland, Österreich und der Schweiz*** (1885–1905,
4 vols, 572 chromolithographs). `flower-survey/thome.csv`: 571 plates, 697
names (biolib's index; `tafel` numbers are biolib's, not the printed ones).
About 1460 × 2320 px on biolib and Commons, larger from the BHL copies on
Commons. Dense: lettered dissections and printed names in the corners. 116
plates hold 2–4 species.

**Lindman, *Bilder ur Nordens Flora*** (1901–05, 1917–26). `flower-survey/lindman.csv`:
581 plates, 673 names. The best scans are the Danish edition's BHL pages on
Commons, 2147 × 3498 px, for 421 plates; the rest are small.

**Sowerby, *English Botany*** (1790–1814, 2,592 plates). Partial: the
letterpress OCR of the Wellcome scans (IA `b28775557_0001` … `_0036`, about
2600 × 4600 px) gave 73% of the plates, a third of them lichens and mosses.
The 1814 general index exists (IA `generalindexesto00sowe`) but its OCR is
too corrupt to read without work.

### Australia

Ellis Rowan's 919 watercolours at the National Library of Australia (about
two thirds Australian wildflowers, named with binomials) are the obvious
candidate, but the catalogue sits behind a Trove API key and a bot check, so
they were not counted. Rowan died in 1922. Mueller's and Maiden's
monographs on IA are probably uncoloured. Melbourne's coverage from
everything surveyed is 6–16%.

## 4. Names

iNaturalist's names are current; the books' are a century or two old. Of
the matches above, the share that needed a synonym (the printed name is not
the iNaturalist name):

| Collection | Hartford | London |
|---|---:|---:|
| Walcott | 21 of 59 | 5 of 9 |
| *Flora Danica* (KB's modern names) | 10 of 112 | 57 of 453 |
| Sturm | 40 of 95 | 128 of 368 |
| Thomé | 4 of 63 | 25 of 250 |
| Sowerby | 13 of 49 | 99 of 252 |

GBIF's name match (`api.gbif.org/v1/species/match`, accepted species key)
resolves most of them, but by spelling alone, so a misapplied old name can
land on the wrong species (Walcott's *Azalea lutea*), and the indexes
themselves err (biolib lists Thomé's printed *Veronica latifolia* as
*V. urticifolia*). "Never a wrong bird" carries over:
every pin is checked against the plate's own caption or the book's index, as
for Gould.

## 5. How the plates render

Ten samples through today's pipeline (`plate.extract`, `tight=True`, then
`pipeline.render_single`), gray (EE03) left and colour (EE02) right:
`flower-survey/render-walcott-thome.jpg` and
`flower-survey/render-bb-floradanica-sturm.jpg`.

- **Every scan needs the Gould paper flattening** (`fetch_plates.store_scan`
  with `flatten=True`). Without it, Walcott's cream paper renders as a gray
  field on the EE03 and an ochre one on the EE02, and her pale watercolour
  becomes silhouettes.
- **Walcott** is the most beautiful on the colour kit. On the gray kit, red
  flowers and berries go nearly black (winterberry, columbine); a tone curve
  for watercolour may be worth trying.
- ***Flora Danica*** renders on the gray kit the way Havell does: engraved
  shading holds at 16 grays. KB's scale bar and stamp survive the crop and
  need a mask (the `mask` boxes of W-883); the top corner's "Tab." line is
  lifted already.
- **Britton & Brown** renders cleanly in black line on both kits, but it
  reads as a field-guide drawing, and its scale fractions (½, ⅗) need
  masking. A full-bleed drawing is cover-cropped.
- **Sturm and Thomé** keep their frame lines and lettered dissections; they
  read as textbook pages.

## Recommendation

1. **The source is the calendar**: species observed within 25 km in the
   last year whose region marks them in flower this week. A species marked
   *Flowers* near you for the first time this year is the news, the way a
   new species today is for the bird frame.
2. **Winter shows fruit.** Where nothing flowers (Hartford, Minneapolis:
   mid-November to February), the frame shows what is in fruit near you by
   the same calendar, with a fruit plate where the collection has one. In
   late February it can turn to the first species to flower. The year's
   collage is the alternative.
3. **North America starts with Walcott**, both her plates and her CC0
   originals: complete list, 5000 × 6700 px scans, public domain everywhere,
   and a curated set of showy wildflowers, which is the selection rule the
   calendar lacks. Whether Britton & Brown's line drawings belong beside
   her, to cover the rest, is a look to decide on a frame.
4. **Europe starts with *Flora Danica***: the highest coverage, a complete
   modern-name index, one IIIF URL per plate and an explicit licence. Sturm
   is second.
5. **California and Australia** need their own survey before they are a
   Region. Rowan needs a Trove key.
6. **Method**: the Gould pipeline (flatten, tight crop) with a per-collection
   mask; names through GBIF and then checked by hand, starting with every
   synonym and fuzzy match.

Open questions for Wells: one picture a day, or more in a rich month? Fruit,
the first to flower, or the collage in winter? Britton & Brown beside
Walcott, or Walcott only? Is a garden list (and *Curtis's* for it) part of the
first version?
