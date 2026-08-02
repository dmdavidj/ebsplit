# EBSPLIT - Electron Block: Scaled Poster Layout In Tiles

Enlarge (or reduce) a PDF to a chosen scale, split it across several A4/A3 sheets, and
write a new PDF with a dotted cut guide, a label, and a calibration ruler on every sheet.
Printed at **100% (actual size)**, the scale is reproduced exactly.

This folder is a *portable EXE creation package*: it only has to be built once on a
hospital PC. The required install files (`.whl` wheels) are bundled so the build works
even when internet access is blocked.

---

## How the join works

- Total size after scale `S` = source size x `S`.
- That large image is cut into tiles the size of the paper's printable area.
- Neighbouring sheets print an `overlap` strip of the **same** content twice.
- The dotted line is drawn at the **exact centre of that overlap**, so the dotted line on
  two adjacent sheets is the same content coordinate. Cut both sheets along the dotted
  line and butt the cut edges together (do **not** overlap them) for a perfect fit with
  no loss and no duplication.
- Placement uses `show_pdf_page`, so vector artwork stays vector - it is never rasterised.

Scanned/raster PDFs work identically; only the effective resolution changes
(a 150 DPI scan at x1.75 prints at ~86 DPI, so scan at `target_DPI x scale`).

---

## Contents

| File | Purpose |
| --- | --- |
| `pdf_split_scale.py` | Engine + command line. Entry point of the EXE. |
| `ebsplit_gui.py` | Preview GUI (English): Layout/Move and ROI/Region tabs. |
| `ebsplit_config.py` | Persistent settings with hard min/max guard-rails. |
| `make_icon.py` | Generates `ebsplit.ico` (no imaging library needed). |
| `build_exe.bat` | Double-click build script (offline first). |
| `*.whl` | Offline install packages. **Do not delete.** |

---

## Build (3 steps)

1. Install Python 3.10-3.13, 64-bit Windows, from <https://www.python.org/downloads/>
   and tick **"Add python.exe to PATH"**. (Microsoft Store "Python 3.12" also works.)
2. Copy this whole folder to the PC, then double-click **`build_exe.bat`** (a few minutes).
3. `dist\EBSPLIT.exe` appears. Copying that single EXE to any PC is enough - no
   Python required.

---

## Using the GUI

Double-click the EXE.

**Layout / Move tab** - shows how the magnified drawing falls across the sheets: paper
outlines, printable frames, overlap bands and the red cut lines. **Drag the drawing** to
translate it; the sheet count updates live, so you can slide the drawing to avoid an
awkward join or to trade one more sheet for a better position. `Reset move`, `Center` and
`Fit view` are on the tab's toolbar.

**ROI / Region tab** - drag a rectangle over the source page to magnify **only that
region**. The output PDF then contains just that region, scaled. `Clear ROI` returns to
the whole page. The region is applied to every page, intersected with each page's size.

**Right-hand panel**

| Group | What it does |
| --- | --- |
| Scale and paper | Scale (2 decimals), paper, orientation. The recommended paper is derived from the source page size. |
| Joining (physical) | Overlap 5-30 mm, margin 0-20 mm. |
| Label | Institution name, position (top-left / top-right / bottom-right / bottom-left / center), font size 5-24 pt, ink 0-0.9 (0.55 reads as a watermark), optional white backing box. |
| Scale bar | On/off, length, and what the divisions mean - see below. |
| Result | Sheets per page and total, plus the chosen paper and orientation. |

`Save as defaults` writes the settings to `ebsplit_config.json` (next to the EXE, or
`%APPDATA%\EBSPLIT\` if that folder is read-only). Values outside the allowed range
are clamped and reported - never applied silently.

---

## Command line

```
EBSPLIT.exe input.pdf --scale 1.75 --paper A4
```

| Option | Meaning |
| --- | --- |
| `-s, --scale` | Magnification, e.g. `1.75`. Required. |
| `-p, --paper` | `A4` or `A3`. |
| `--orientation` | `auto` / `portrait` / `landscape`. |
| `--overlap` | Overlap in mm (default 10). |
| `--margin` | Paper margin in mm (default 5). |
| `--institution` | Label text. |
| `--label-pos` | `top-left` / `top-right` / `bottom-right` / `bottom-left` / `center`. |
| `--label-size` | Label font size in pt. |
| `--ruler` / `--no-ruler` | Scale bar on/off. |
| `--ruler-length` | Bar length in mm (source mm in `source` mode). |
| `--ruler-mode` | `source` or `paper` - see below. |
| `--label-gray` | Label ink, 0 = black, 0.55 = watermark. |
| `--roi X0 Y0 X1 Y1` | Region to magnify, in mm from the page's top-left. |
| `--offset DX DY` | Translate the content, in mm. |
| `--password` | Password for an encrypted PDF. |

---

## The scale bar

Every sheet carries a bar that you measure with a real ruler after printing. It can be
graduated two ways.

**`source` (default)** - divisions are **original drawing millimetres**, so the bar is
printed `length x scale` long. At x1.48 a 10 mm division measures **14.8 mm**, exactly like
the drawing itself. Measuring it checks the magnification *and* the printer in one go, and
it matches the grid of the source drawing. The bar is captioned
`SOURCE mm x1.48 - MEASURE 148.0 mm`.

**`paper`** - divisions are **true paper millimetres**: 10 mm always measures 10 mm
whatever the scale. This only tells you whether the printer rescaled the page. Captioned
`PAPER mm - MEASURE 100 mm AT 100%`.

If the bar does not measure what its caption says, either the printer rescaled the page or
the scale is not what you think - fix it and reprint. An A3-to-A4 "fit to page" shrinks
everything by 29.3 %, which is impossible to miss on a 148 mm bar.

The bar is always drawn dark, even when the label is set to a light watermark grey: a light
hairline halftones on a laser printer and cannot be measured accurately.

## Print and assemble

1. Print at **Actual size / 100%**. Never "Fit to page" or "Shrink oversized pages".
2. **Load the paper you selected.** The output pages are exactly A4 or A3; printing A3
   pages on A4 (or the reverse) lets the driver rescale them and the scale will be wrong.
3. **Check the scale bar with a real ruler.** It is drawn to exact physical size and is
   independent of the label font size.
4. Order the sheets by the label: `[P<page> R<row>/<rows> C<col>/<cols>]`.
5. Cut both adjacent sheets along the red dotted lines, then align the cut edges without
   overlapping and tape them. Because the dotted lines mark the exact centre of the
   overlap - the same location on both sheets - cutting and butting fits perfectly.
   Leaving the dotted lines and overlapping the sheets instead loses image by the width
   of the overlap, so always use the cut-and-butt method.

---

## Notes

- On 32-bit Windows, or with a Python outside 3.10-3.13, the offline wheels may not
  match. Connect to the internet and run `build_exe.bat`; it falls back to an online
  install automatically.
- The EXE is built with `--noconsole`, so double-clicking it shows only the GUI - no black
  console window. Command-line use still prints normally: when started from a terminal the
  app re-attaches to that console. If you need a console for debugging, remove
  `--noconsole` from the PyInstaller line in `build_exe.bat` and rebuild.
- The physical geometry (scale, tiling, overlap cut lines) is unchanged from the
  field-measured version: the defaults reproduce it exactly.
- Annotations, form fields and links are not copied - only page content. Flatten the
  source PDF first if a required outline lives in an annotation layer.
- At strong reductions the label block (about 60 mm wide at 7 pt) can be wider than the
  drawing. Shorten the institution name, reduce the font size, or move the label.

---

## Screens

Layout / Move - drag the drawing, sheet count follows:

<img width="900" alt="layout" src="docs/gui_layout.png" />

ROI / Region - magnify only the selected rectangle:

<img width="900" alt="roi" src="docs/gui_roi.png" />

The label and calibration ruler as printed on every sheet. The label carries the scale and
the paper size; the bar must measure exactly 100 mm:

<img width="900" alt="label and ruler" src="docs/label_ruler.png" />

before

<img width="473" height="593" alt="image" src="https://github.com/user-attachments/assets/c1241bc1-4ead-466c-993f-9aa44a1cb6a1" />

after

<img width="703" height="721" alt="image" src="https://github.com/user-attachments/assets/d968ce65-e2b0-4fcc-9380-aa4923a10944" />
