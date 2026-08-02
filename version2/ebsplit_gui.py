# -*- coding: utf-8 -*-
"""
EBSPLIT - graphical front end (English UI)
=========================================

Two previews:

  Layout tab  - shows how the magnified drawing falls across the sheets.
                Drag the drawing to translate it; the sheet count follows.
  ROI tab     - shows the source page. Drag a rectangle to magnify only that
                region.

Everything physical (scale, tiling, overlap cut lines) is computed by
pdf_split_scale.compute_layout, the same code the renderer uses, so the preview
and the printed result cannot disagree.
"""

import os
import sys
import traceback

import tkinter as tk
from tkinter import filedialog, messagebox, ttk

import fitz

import ebsplit_config as cfgmod
import pdf_split_scale as engine

MM = engine.MM
MAX_PREVIEW_PIXELS = 4_000_000     # thumbnail render guard
CANVAS_MIN = 40


# --------------------------------------------------------------------------- #
# helpers
# --------------------------------------------------------------------------- #
def resource_path(name):
    """Path to a bundled resource, works inside a PyInstaller one-file EXE."""
    base = getattr(sys, "_MEIPASS", None)
    if base:
        p = os.path.join(base, name)
        if os.path.exists(p):
            return p
    here = os.path.dirname(os.path.abspath(__file__))
    return os.path.join(here, name)


def photo_from_pixmap(pix):
    """tk.PhotoImage from a fitz Pixmap without needing PIL."""
    try:
        return tk.PhotoImage(data=pix.tobytes("ppm"))
    except Exception:
        return tk.PhotoImage(data=pix.tobytes("png"))


def mm_str(v):
    return "%.1f" % v


class Field(ttk.Frame):
    """Label + entry + inline range hint, with validation against config bounds."""

    def __init__(self, master, caption, key, value, width=8, hint=True):
        super().__init__(master)
        self.key = key
        ttk.Label(self, text=caption, width=17, anchor="e").grid(
            row=0, column=0, sticky="e", padx=(0, 6))
        self.var = tk.StringVar(value=("%g" % value))
        self.entry = ttk.Entry(self, textvariable=self.var, width=width)
        self.entry.grid(row=0, column=1, sticky="w")
        if hint and key in cfgmod.BOUNDS:
            lo, hi, _ = cfgmod.BOUNDS[key]
            ttk.Label(self, text="(%g - %g)" % (lo, hi),
                      foreground="#666").grid(row=0, column=2, sticky="w", padx=4)

    def get(self):
        """Return (value, message_or_None) clamped to the allowed range."""
        return cfgmod.clamp(self.key, self.var.get())

    def set(self, v):
        """Show a value. Anything unparsable falls back to the default."""
        try:
            self.var.set("%g" % float(v))
        except (TypeError, ValueError):
            self.var.set("%g" % cfgmod.DEFAULTS.get(self.key, 0))


# --------------------------------------------------------------------------- #
# main window
# --------------------------------------------------------------------------- #
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("EBSPLIT - Electron Block: Scaled Poster Layout In Tiles")
        self.geometry("1180x760")
        self.minsize(900, 560)

        self.cfg, cfg_msgs = cfgmod.load()

        self.doc = None
        self.doc_path = None
        self.page_index = 0
        self.roi = None                 # fitz.Rect in source pt, or None
        self.offset_mm = [0.0, 0.0]
        self.layout = None
        self._thumb = None              # keep PhotoImage alive
        self._page_img = None
        self._view = None               # (scale, ox, oy) for layout canvas
        self._roi_view = None
        self._fit_needed = True
        self._drag = None
        self._recommended = None

        self._build_ui()
        self._set_icon()

        if cfg_msgs:
            self.status("Settings adjusted: " + " | ".join(cfg_msgs))
        else:
            self.status("Open a PDF to begin.")

    # ---------------------------------------------------------------- icon
    def _set_icon(self):
        for name in ("ebsplit.ico",):
            p = resource_path(name)
            if os.path.exists(p):
                try:
                    self.iconbitmap(default=p)
                    return
                except Exception:
                    pass

    # ------------------------------------------------------------------ ui
    def _build_ui(self):
        top = ttk.Frame(self, padding=(10, 8))
        top.pack(fill="x")
        ttk.Button(top, text="Open PDF...", command=self.on_open).pack(side="left")
        self.path_var = tk.StringVar(value="(no file)")
        ttk.Label(top, textvariable=self.path_var, foreground="#333").pack(
            side="left", padx=10)

        # Packed last on purpose - see the note at the end of this method.
        body = ttk.Frame(self, padding=(10, 0))

        # ---- previews -------------------------------------------------
        self.nb = ttk.Notebook(body)
        self.nb.pack(side="left", fill="both", expand=True)

        lay_tab = ttk.Frame(self.nb)
        self.nb.add(lay_tab, text="Layout / Move")
        bar = ttk.Frame(lay_tab, padding=(4, 4))
        bar.pack(fill="x")
        ttk.Label(bar, text="Drag the drawing to move it across the sheets.",
                  foreground="#444").pack(side="left")
        ttk.Button(bar, text="Fit view", command=self.refit).pack(side="right")
        ttk.Button(bar, text="Center", command=self.center_content).pack(
            side="right", padx=4)
        ttk.Button(bar, text="Reset move", command=self.reset_offset).pack(
            side="right")
        self.canvas = tk.Canvas(lay_tab, background="#9aa0a6",
                                highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<ButtonPress-1>", self.on_lay_press)
        self.canvas.bind("<B1-Motion>", self.on_lay_move)
        self.canvas.bind("<ButtonRelease-1>", self.on_lay_release)
        self.canvas.bind("<Configure>", lambda e: self.refresh_layout())

        roi_tab = ttk.Frame(self.nb)
        self.nb.add(roi_tab, text="ROI / Region")
        rbar = ttk.Frame(roi_tab, padding=(4, 4))
        rbar.pack(fill="x")
        ttk.Label(rbar, text="Drag a rectangle to magnify only that region.",
                  foreground="#444").pack(side="left")
        ttk.Button(rbar, text="Clear ROI", command=self.clear_roi).pack(side="right")
        ttk.Button(rbar, text="Use whole page", command=self.clear_roi).pack(
            side="right", padx=4)
        self.roi_canvas = tk.Canvas(roi_tab, background="#9aa0a6",
                                    highlightthickness=0)
        self.roi_canvas.pack(fill="both", expand=True)
        self.roi_canvas.bind("<ButtonPress-1>", self.on_roi_press)
        self.roi_canvas.bind("<B1-Motion>", self.on_roi_move)
        self.roi_canvas.bind("<ButtonRelease-1>", self.on_roi_release)
        self.roi_canvas.bind("<Configure>", lambda e: self.refresh_roi())

        # ---- controls (scrollable: the panel is taller than a small screen) --
        wrap = ttk.Frame(body)
        wrap.pack(side="right", fill="y")
        sc = tk.Canvas(wrap, width=312, highlightthickness=0, borderwidth=0)
        vsb = ttk.Scrollbar(wrap, orient="vertical", command=sc.yview)
        sc.configure(yscrollcommand=vsb.set)
        vsb.pack(side="right", fill="y")
        sc.pack(side="left", fill="both", expand=True)
        inner = ttk.Frame(sc, padding=(12, 4))
        win = sc.create_window((0, 0), window=inner, anchor="nw")

        self._panel_resizing = False

        def _resize(_ev=None):
            # itemconfigure() resizes `inner`, which fires <Configure> on inner,
            # which lands back here, which calls itemconfigure() again. Without a
            # guard the two feed each other and the panel relayouts continuously;
            # a frame caught mid-cycle shows sections drawn twice at different
            # scroll offsets. Re-entrancy guard + only act on real changes.
            if self._panel_resizing:
                return
            self._panel_resizing = True
            try:
                bbox = sc.bbox("all")
                if bbox:
                    region = "%d %d %d %d" % bbox
                    if str(sc.cget("scrollregion")) != region:
                        sc.configure(scrollregion=region)
                width = sc.winfo_width()
                if width > 1:
                    try:
                        current = int(float(sc.itemcget(win, "width") or 0))
                    except (TypeError, ValueError):
                        current = -1
                    if current != width:
                        sc.itemconfigure(win, width=width)
            finally:
                self._panel_resizing = False

        inner.bind("<Configure>", _resize)
        sc.bind("<Configure>", _resize)

        def _wheel(ev):
            # Scrolling a canvas whose content already fits just shifts the view
            # and leaves the panel looking displaced. Do nothing in that case.
            first, last = sc.yview()
            if first <= 0.0 and last >= 1.0:
                return "break"
            if ev.delta:
                sc.yview_scroll(-1 if ev.delta > 0 else 1, "units")
            return "break"

        try:
            sc.configure(background=ttk.Style().lookup("TFrame", "background"))
        except Exception:
            pass
        self._build_controls(inner)

        # Bind the wheel on this subtree only. bind_all/unbind_all would install
        # and then wipe every MouseWheel binding in the application, and a missed
        # <Leave> (window deactivated with the pointer inside) would leave the
        # global binding in place so the wheel scrolled the panel from anywhere.
        def _bind_wheel(widget):
            widget.bind("<MouseWheel>", _wheel)
            for child in widget.winfo_children():
                _bind_wheel(child)

        sc.bind("<MouseWheel>", _wheel)
        _bind_wheel(inner)

        # ---- action bar (always visible, never scrolled away) ---------
        act = ttk.Frame(self, padding=(10, 6))
        self.sheets_var = tk.StringVar(value="Sheets: -")
        ttk.Label(act, textvariable=self.sheets_var,
                  font=("", 10, "bold")).pack(side="left")
        ttk.Button(act, text="Convert...", command=self.on_convert).pack(
            side="right")
        ttk.Button(act, text="Restore defaults",
                   command=self.on_restore_config).pack(side="right", padx=6)
        ttk.Button(act, text="Save as defaults",
                   command=self.on_save_config).pack(side="right")

        # ---- status ---------------------------------------------------
        self.status_var = tk.StringVar()
        sb = ttk.Frame(self, padding=(10, 6))
        ttk.Label(sb, textvariable=self.status_var, foreground="#222").pack(
            side="left")
        self.prog = ttk.Progressbar(sb, length=160, mode="determinate")
        self.prog.pack(side="right")

        # Pack order decides who loses space when the window is too short.
        # Reserve the status bar and the action bar at the bottom FIRST, then
        # let the preview area absorb whatever is left. Packing `body` with
        # expand=True before these would push Convert off the bottom edge on a
        # small screen.
        sb.pack(side="bottom", fill="x")
        ttk.Separator(self, orient="horizontal").pack(side="bottom", fill="x")
        act.pack(side="bottom", fill="x")
        body.pack(side="top", fill="both", expand=True)

    def _build_controls(self, side):
        r = 0

        def section(text):
            nonlocal r
            ttk.Separator(side).grid(row=r, column=0, sticky="ew", pady=(10, 2))
            r += 1
            ttk.Label(side, text=text, font=("", 9, "bold")).grid(
                row=r, column=0, sticky="w")
            r += 1

        def add(w, pady=2):
            nonlocal r
            w.grid(row=r, column=0, sticky="ew", pady=pady)
            r += 1

        # --- scale / paper
        section("Scale and paper")
        self.f_scale = Field(side, "Scale", "scale", self.cfg["scale"])
        add(self.f_scale)

        pr = ttk.Frame(side)
        ttk.Label(pr, text="Paper", width=17, anchor="e").grid(
            row=0, column=0, padx=(0, 6))
        self.paper_var = tk.StringVar(value=self.cfg["paper"])
        cb = ttk.Combobox(pr, textvariable=self.paper_var, values=list(cfgmod.PAPERS),
                          width=6, state="readonly")
        cb.grid(row=0, column=1, sticky="w")
        cb.bind("<<ComboboxSelected>>", lambda e: self.params_changed())
        add(pr)

        self.autopaper_var = tk.BooleanVar(
            value=self.cfg["auto_paper_from_source"])
        add(ttk.Checkbutton(
            side, text="Auto-select paper when a PDF is opened",
            variable=self.autopaper_var), pady=0)
        self.warn_var = tk.BooleanVar(value=self.cfg["warn_on_paper_mismatch"])
        add(ttk.Checkbutton(
            side, text="Warn if paper differs from the recommendation",
            variable=self.warn_var), pady=0)

        self.rec_var = tk.StringVar(value="")
        self.rec_lbl = ttk.Label(side, textvariable=self.rec_var,
                                 foreground="#8a5000", wraplength=250,
                                 justify="left")
        add(self.rec_lbl, pady=(0, 2))

        orow = ttk.Frame(side)
        ttk.Label(orow, text="Orientation", width=17, anchor="e").grid(
            row=0, column=0, padx=(0, 6))
        self.orient_var = tk.StringVar(value=self.cfg["orientation"])
        ocb = ttk.Combobox(orow, textvariable=self.orient_var,
                           values=list(cfgmod.ORIENTATIONS), width=10,
                           state="readonly")
        ocb.grid(row=0, column=1, sticky="w")
        ocb.bind("<<ComboboxSelected>>", lambda e: self.params_changed())
        add(orow)

        # --- joining
        section("Joining (physical)")
        self.f_overlap = Field(side, "Overlap mm", "overlap_mm",
                               self.cfg["overlap_mm"])
        add(self.f_overlap)
        self.f_margin = Field(side, "Margin mm", "margin_mm", self.cfg["margin_mm"])
        add(self.f_margin)
        ttk.Label(side, text=("Overlap is printed on both neighbours; the cut "
                              "line sits at its exact centre."),
                  foreground="#666", wraplength=250, justify="left").grid(
            row=r, column=0, sticky="w")
        r += 1

        # --- label
        section("Label")
        inst = ttk.Frame(side)
        ttk.Label(inst, text="Institution", width=17, anchor="e").grid(
            row=0, column=0, padx=(0, 6))
        self.inst_var = tk.StringVar(value=self.cfg["institution"])
        ttk.Entry(inst, textvariable=self.inst_var, width=22).grid(
            row=0, column=1, sticky="w")
        add(inst)

        lp = ttk.Frame(side)
        ttk.Label(lp, text="Position", width=17, anchor="e").grid(
            row=0, column=0, padx=(0, 6))
        self.lpos_var = tk.StringVar(value=self.cfg["label_position"])
        lcb = ttk.Combobox(lp, textvariable=self.lpos_var,
                           values=list(cfgmod.LABEL_POSITIONS), width=13,
                           state="readonly")
        lcb.grid(row=0, column=1, sticky="w")
        lcb.bind("<<ComboboxSelected>>", lambda e: self.refresh_layout())
        add(lp)

        self.f_lsize = Field(side, "Font size pt", "label_fontsize",
                             self.cfg["label_fontsize"])
        add(self.f_lsize)
        self.f_lgray = Field(side, "Ink 0=black", "label_gray",
                             self.cfg["label_gray"])
        add(self.f_lgray)
        ttk.Label(side, text=("0.55 reads as a watermark. The scale bar stays "
                              "dark whatever this is: a light hairline "
                              "halftones on a laser printer and cannot be "
                              "measured accurately."),
                  foreground="#666", wraplength=250, justify="left").grid(
            row=r, column=0, sticky="w")
        r += 1

        self.lbg_var = tk.BooleanVar(value=self.cfg["label_background"])
        add(ttk.Checkbutton(side, text="White backing box behind label",
                            variable=self.lbg_var))

        # --- move / roi readout
        section("Move and region")
        self.off_var = tk.StringVar()
        ttk.Label(side, textvariable=self.off_var).grid(row=r, column=0, sticky="w")
        r += 1
        self.roi_var = tk.StringVar()
        ttk.Label(side, textvariable=self.roi_var, wraplength=250,
                  justify="left").grid(row=r, column=0, sticky="w")
        r += 1

        # --- settings persistence (buttons live in the always-visible action bar)
        section("Settings")
        ttk.Label(side, text=("Save as defaults stores everything above - scale, "
                              "paper, joining and label - and restores it "
                              "the next time the program starts. Region and Move "
                              "are per-drawing and are not stored."),
                  foreground="#666", wraplength=250, justify="left").grid(
            row=r, column=0, sticky="w")
        r += 1
        self.cfgpath_var = tk.StringVar(value="File: %s" % cfgmod.config_path())
        ttk.Label(side, textvariable=self.cfgpath_var, foreground="#888",
                  wraplength=250, justify="left").grid(row=r, column=0,
                                                       sticky="w", pady=(2, 8))
        r += 1

        # recompute when a numeric entry loses focus or Enter is pressed
        for f in (self.f_scale, self.f_overlap, self.f_margin, self.f_lsize,
                  self.f_lgray):
            f.entry.bind("<FocusOut>", lambda e: self.params_changed())
            f.entry.bind("<Return>", lambda e: self.params_changed())

    # -------------------------------------------------------------- status
    def status(self, text):
        self.status_var.set(text)
        self.update_idletasks()

    # ------------------------------------------------------------ settings
    def read_params(self):
        """Collect validated parameters. Returns (dict, messages)."""
        msgs = []
        vals = {}
        for f in (self.f_scale, self.f_overlap, self.f_margin, self.f_lsize,
                  self.f_lgray):
            v, m = f.get()
            vals[f.key] = v
            if m:
                msgs.append(m)
                f.set(v)
        inst = self.inst_var.get().strip()
        if not inst:
            inst = cfgmod.DEFAULTS["institution"]
            self.inst_var.set(inst)
            msgs.append("Institution was empty; restored the default.")
        vals.update({
            "institution": inst,
            "paper": self.paper_var.get(),
            "orientation": self.orient_var.get(),
            "label_position": self.lpos_var.get(),
            "label_background": bool(self.lbg_var.get()),
            "auto_paper_from_source": bool(self.autopaper_var.get()),
            "warn_on_paper_mismatch": bool(self.warn_var.get()),
        })
        vals["scale"] = round(vals["scale"], 2)
        return vals, msgs

    def apply_config(self, cfg):
        """Push a settings dict into the widgets."""
        self.f_scale.set(cfg["scale"])
        self.f_overlap.set(cfg["overlap_mm"])
        self.f_margin.set(cfg["margin_mm"])
        self.f_lsize.set(cfg["label_fontsize"])
        self.f_lgray.set(cfg["label_gray"])
        self.inst_var.set(cfg["institution"])
        self.paper_var.set(cfg["paper"])
        self.orient_var.set(cfg["orientation"])
        self.lpos_var.set(cfg["label_position"])
        self.lbg_var.set(cfg["label_background"])
        self.autopaper_var.set(cfg["auto_paper_from_source"])
        self.warn_var.set(cfg["warn_on_paper_mismatch"])
        self.params_changed()

    def on_save_config(self):
        vals, msgs = self.read_params()
        cfg = dict(self.cfg)
        for k in cfgmod.DEFAULTS:
            if k in vals:
                cfg[k] = vals[k]
        try:
            path, more = cfgmod.save(cfg)
        except OSError as exc:
            messagebox.showerror(
                "Cannot save settings",
                "%s\n\nThe folder may be read-only. Settings were not saved."
                % exc)
            return
        self.cfg, _ = cfgmod.load(path)
        self.cfgpath_var.set("File: %s" % path)
        extra = msgs + more
        self.status("Settings saved - they will be restored on the next start.%s"
                    % (("  " + " | ".join(extra)) if extra else ""))

    def on_restore_config(self):
        """Reset every setting to the built-in defaults and forget the file."""
        if not messagebox.askokcancel(
                "Restore defaults",
                "Reset all settings to the built-in defaults?\n\n"
                "This also deletes the saved settings file, so the defaults "
                "will be used on the next start."):
            return
        path = cfgmod.config_path()
        removed = True
        if os.path.isfile(path):
            try:
                os.remove(path)
            except OSError as exc:
                removed = False
                self.status("Could not delete %s (%s); values were reset in the "
                            "window only." % (path, exc))
        self.cfg, _ = cfgmod.load()
        self.apply_config(self.cfg)
        self.cfgpath_var.set("File: %s" % cfgmod.config_path())
        if removed:
            self.status("Settings restored to the built-in defaults.")

    # ---------------------------------------------------------------- file
    def on_open(self):
        path = filedialog.askopenfilename(
            title="Open PDF", filetypes=[("PDF files", "*.pdf"), ("All files", "*.*")])
        if not path:
            return
        self.load_pdf(path)

    def load_pdf(self, path):
        password = None
        while True:
            try:
                doc = engine.open_pdf(path, password)
                break
            except engine.PasswordRequired:
                from tkinter import simpledialog
                prompt = ("This PDF is encrypted. Enter the password:"
                          if password is None else
                          "Wrong password. Try again:")
                password = simpledialog.askstring(
                    "Password required", prompt, show="*", parent=self)
                if password is None:
                    self.status("Open cancelled.")
                    return
                continue
            except engine.LayoutError as exc:
                messagebox.showerror("Cannot open PDF", str(exc))
                return
            except Exception as exc:
                messagebox.showerror("Cannot open PDF", str(exc))
                return

        if self.doc is not None:
            try:
                self.doc.close()
            except Exception:
                pass
        self.doc = doc
        self.doc_path = path
        self.doc_password = password
        self.page_index = 0
        self.roi = None
        self.offset_mm = [0.0, 0.0]
        self._fit_needed = True
        self.path_var.set("%s   (%d page%s)"
                          % (os.path.basename(path), doc.page_count,
                             "" if doc.page_count == 1 else "s"))

        # paper recommendation from the source page size
        pr = doc[0].rect
        rec, why = cfgmod.recommend_paper(pr.width / MM, pr.height / MM)
        self._recommended = rec
        if self.autopaper_var.get():
            self.paper_var.set(rec)
            kept = ""
        else:
            kept = "\nAuto-select is off, so your saved choice (%s) is kept." \
                   % self.paper_var.get()
        self.rec_var.set("Recommended paper: %s (%s).\nThe paper you pick must be "
                         "the paper actually loaded in the printer.%s"
                         % (rec, why, kept))
        self.params_changed()
        self.status("Loaded %s" % os.path.basename(path))

    def cur_page(self):
        if not self.doc:
            return None
        return self.doc[min(self.page_index, self.doc.page_count - 1)]

    def base_rect(self):
        page = self.cur_page()
        if page is None:
            return None
        try:
            return engine._base_rect(page, self.roi)
        except engine.LayoutError:
            self.roi = None
            return page.rect

    def total_sheets(self, vals):
        """Sheets for the whole document, plus the count of skipped pages.

        Pages can differ in size, so multiplying this page's count by the page
        count is wrong - every page has to be laid out on its own.
        """
        total = 0
        skipped = 0
        for pno in range(self.doc.page_count):
            base = engine._base_rect(self.doc[pno], self.roi, strict=False)
            if base is None:
                skipped += 1
                continue
            try:
                total += engine.compute_layout(
                    base.width, base.height, vals["scale"], vals["paper"],
                    vals["orientation"], vals["overlap_mm"], vals["margin_mm"],
                    tuple(self.offset_mm))["sheets"]
            except engine.LayoutError:
                skipped += 1
        return total, skipped

    # ------------------------------------------------------------- compute
    def params_changed(self):
        self._fit_needed = True
        self.refresh_layout()
        self.refresh_roi()

    def compute(self):
        """Recompute the layout for the current settings. Returns messages."""
        self.layout = None
        if not self.doc:
            self.sheets_var.set("Sheets: -")
            return []
        vals, msgs = self.read_params()
        base = self.base_rect()
        try:
            self.layout = engine.compute_layout(
                base.width, base.height, vals["scale"], vals["paper"],
                vals["orientation"], vals["overlap_mm"], vals["margin_mm"],
                tuple(self.offset_mm))
        except engine.LayoutError as exc:
            self.sheets_var.set("Sheets: -")
            self.status("Layout not possible: %s" % exc)
            return msgs
        lay = self.layout
        per = lay["sheets"]
        total, skipped = self.total_sheets(vals)
        note = ""
        if skipped:
            note = ", %d page%s outside the region" % (
                skipped, "" if skipped == 1 else "s")
        self.sheets_var.set("Sheets: %d for this page  (%d total, %s %s%s)"
                            % (per, total, vals["paper"], lay["orientation"],
                               note))
        self.off_var.set("Move: X %s mm, Y %s mm"
                         % (mm_str(self.offset_mm[0]), mm_str(self.offset_mm[1])))
        if self.roi is None:
            self.roi_var.set("Region: whole page (%.1f x %.1f mm)"
                             % (base.width / MM, base.height / MM))
        else:
            self.roi_var.set("Region: ROI %.1f x %.1f mm at (%.1f, %.1f) mm"
                             % (base.width / MM, base.height / MM,
                                base.x0 / MM, base.y0 / MM))
        if msgs:
            self.status(" | ".join(msgs))
        return msgs

    # -------------------------------------------------------- layout canvas
    def grid_extent(self, lay):
        """Bounding box of all sheets in grid space (pt)."""
        m = lay["margin"]
        x0 = lay["i0"] * lay["step_w"] - m
        y0 = lay["j0"] * lay["step_h"] - m
        x1 = (lay["i0"] + lay["cols"] - 1) * lay["step_w"] + lay["printable_w"] + m
        y1 = (lay["j0"] + lay["rows"] - 1) * lay["step_h"] + lay["printable_h"] + m
        # keep the drawing visible even if it sticks out
        x0 = min(x0, lay["tx"]); y0 = min(y0, lay["ty"])
        x1 = max(x1, lay["tx"] + lay["SW"]); y1 = max(y1, lay["ty"] + lay["SH"])
        return x0, y0, x1, y1

    def refit(self):
        self._fit_needed = True
        self.refresh_layout()

    def refresh_layout(self):
        self.compute()
        cv = self.canvas
        cv.delete("all")
        self._thumb = None
        lay = self.layout
        if lay is None:
            return
        W = max(cv.winfo_width(), CANVAS_MIN)
        H = max(cv.winfo_height(), CANVAS_MIN)

        if self._fit_needed or self._view is None:
            # fit with a little head-room so a moderate drag does not need a refit
            x0, y0, x1, y1 = self.grid_extent(lay)
            pad_x = lay["step_w"] * 0.25
            pad_y = lay["step_h"] * 0.25
            ew = (x1 - x0) + 2 * pad_x
            eh = (y1 - y0) + 2 * pad_y
            vs = min((W - 20) / ew, (H - 20) / eh)
            vs = max(vs, 1e-4)
            ox = (W - ew * vs) / 2.0 - (x0 - pad_x) * vs
            oy = (H - eh * vs) / 2.0 - (y0 - pad_y) * vs
            self._view = (vs, ox, oy)
            self._fit_needed = False
        vs, ox, oy = self._view

        def px(x, y):
            return ox + x * vs, oy + y * vs

        m = lay["margin"]
        # 1) sheets (white)
        for j in range(lay["rows"]):
            for i in range(lay["cols"]):
                gx = (lay["i0"] + i) * lay["step_w"] - m
                gy = (lay["j0"] + j) * lay["step_h"] - m
                a = px(gx, gy)
                b = px(gx + lay["PW"], gy + lay["PH"])
                cv.create_rectangle(a[0], a[1], b[0], b[1], fill="#ffffff",
                                    outline="#5f6368", width=1)

        # 2) the drawing itself
        self._draw_thumb(cv, px, vs, lay)

        # 3) printable frame, overlap bands, cut lines, sheet ids (on top)
        for j in range(lay["rows"]):
            for i in range(lay["cols"]):
                gx = (lay["i0"] + i) * lay["step_w"]
                gy = (lay["j0"] + j) * lay["step_h"]
                a = px(gx, gy)
                b = px(gx + lay["printable_w"], gy + lay["printable_h"])
                cv.create_rectangle(a[0], a[1], b[0], b[1], outline="#1a73e8",
                                    dash=(2, 3))
                cv.create_text(a[0] + 4, a[1] + 4, anchor="nw",
                               text="R%d C%d" % (j + 1, i + 1),
                               fill="#1a73e8", font=("", 8))
        # cut lines at the exact centre of each overlap band
        O = lay["overlap"]
        gy0 = lay["j0"] * lay["step_h"] - m
        gy1 = (lay["j0"] + lay["rows"] - 1) * lay["step_h"] + lay["printable_h"] + m
        for i in range(lay["cols"] - 1):
            gx = (lay["i0"] + i + 1) * lay["step_w"]
            aa = px(gx, gy0); bb = px(gx + O, gy1)
            cv.create_rectangle(aa[0], aa[1], bb[0], bb[1], outline="",
                                fill="#f6c6c6", stipple="gray50")
            c0 = px(gx + O / 2.0, gy0); c1 = px(gx + O / 2.0, gy1)
            cv.create_line(c0[0], c0[1], c1[0], c1[1], fill="#d81717",
                           dash=(4, 3))
        gx0 = lay["i0"] * lay["step_w"] - m
        gx1 = (lay["i0"] + lay["cols"] - 1) * lay["step_w"] + lay["printable_w"] + m
        for j in range(lay["rows"] - 1):
            gy = (lay["j0"] + j + 1) * lay["step_h"]
            aa = px(gx0, gy); bb = px(gx1, gy + O)
            cv.create_rectangle(aa[0], aa[1], bb[0], bb[1], outline="",
                                fill="#f6c6c6", stipple="gray50")
            c0 = px(gx0, gy + O / 2.0); c1 = px(gx1, gy + O / 2.0)
            cv.create_line(c0[0], c0[1], c1[0], c1[1], fill="#d81717",
                           dash=(4, 3))

        # 4) label footprint preview
        self._draw_label_preview(cv, px, lay)

    def _draw_thumb(self, cv, px, vs, lay):
        page = self.cur_page()
        base = self.base_rect()
        if page is None or base is None:
            return
        a = px(lay["tx"], lay["ty"])
        b = px(lay["tx"] + lay["SW"], lay["ty"] + lay["SH"])
        w_px = max(int(round(b[0] - a[0])), 1)
        h_px = max(int(round(b[1] - a[1])), 1)
        if w_px * h_px > MAX_PREVIEW_PIXELS:
            k = (MAX_PREVIEW_PIXELS / float(w_px * h_px)) ** 0.5
            w_px = max(int(w_px * k), 1)
            h_px = max(int(h_px * k), 1)
        # Re-rendering on every mouse-move would make dragging sluggish; the
        # thumbnail only depends on the source region and the pixel size.
        key = (self.doc_path, self.page_index, tuple(round(v, 3) for v in base),
               w_px, h_px)
        img = None
        if getattr(self, "_thumb_key", None) == key:
            img = getattr(self, "_thumb_cache", None)
        if img is None:
            try:
                zx = w_px / base.width
                zy = h_px / base.height
                pix = page.get_pixmap(matrix=fitz.Matrix(zx, zy), clip=base,
                                      alpha=False)
                img = photo_from_pixmap(pix)
            except Exception:
                cv.create_rectangle(a[0], a[1], b[0], b[1], outline="#202124",
                                    width=2)
                return
            self._thumb_key = key
            self._thumb_cache = img
        self._thumb = img
        cv.create_image(a[0], a[1], anchor="nw", image=img)
        cv.create_rectangle(a[0], a[1], b[0], b[1], outline="#202124", width=1)

    def _draw_label_preview(self, cv, px, lay):
        """Show where the label block will land on each sheet."""
        pos = self.lpos_var.get()
        m = lay["margin"]
        aw = lay["PW"] - 2 * m
        ah = lay["PH"] - 2 * m
        try:
            fs, _ = self.f_lsize.get()
        except Exception:
            return
        bw = 40 * MM
        bh = fs + 6
        bw = min(bw, aw)
        bh = min(bh, ah)
        for j in range(lay["rows"]):
            for i in range(lay["cols"]):
                ox = (lay["i0"] + i) * lay["step_w"]
                oy = (lay["j0"] + j) * lay["step_h"]
                if pos == "top-left":
                    lx, ly = ox, oy
                elif pos == "top-right":
                    lx, ly = ox + aw - bw, oy
                elif pos == "bottom-right":
                    lx, ly = ox + aw - bw, oy + ah - bh
                elif pos == "bottom-left":
                    lx, ly = ox, oy + ah - bh
                else:
                    lx, ly = ox + (aw - bw) / 2.0, oy + (ah - bh) / 2.0
                a = px(lx, ly); b = px(lx + bw, ly + bh)
                cv.create_rectangle(a[0], a[1], b[0], b[1], outline="#0b8043",
                                    fill="#e6f4ea", stipple="gray25")

    # ------------------------------------------------------------- dragging
    def on_lay_press(self, ev):
        if self.layout is None:
            return
        self._drag = (ev.x, ev.y, list(self.offset_mm))
        self.canvas.configure(cursor="fleur")

    def on_lay_move(self, ev):
        if self._drag is None or self._view is None:
            return
        vs = self._view[0]
        x0, y0, base_off = self._drag
        self.offset_mm[0] = base_off[0] + (ev.x - x0) / vs / MM
        self.offset_mm[1] = base_off[1] + (ev.y - y0) / vs / MM
        self.refresh_layout()

    def on_lay_release(self, ev):
        self._drag = None
        self.canvas.configure(cursor="")
        # snap to 0.1 mm so the numbers stay readable
        self.offset_mm = [round(v, 1) for v in self.offset_mm]
        self.refresh_layout()

    def reset_offset(self):
        self.offset_mm = [0.0, 0.0]
        self._fit_needed = True
        self.refresh_layout()

    def center_content(self):
        """Place the drawing centred inside the minimum sheet grid."""
        if not self.doc:
            return
        vals, _ = self.read_params()
        base = self.base_rect()
        try:
            lay0 = engine.compute_layout(base.width, base.height, vals["scale"],
                                         vals["paper"], vals["orientation"],
                                         vals["overlap_mm"], vals["margin_mm"],
                                         (0.0, 0.0))
        except engine.LayoutError as exc:
            self.status("Cannot centre: %s" % exc)
            return
        grid_w = (lay0["cols"] - 1) * lay0["step_w"] + lay0["printable_w"]
        grid_h = (lay0["rows"] - 1) * lay0["step_h"] + lay0["printable_h"]
        self.offset_mm = [round((grid_w - lay0["SW"]) / 2.0 / MM, 1),
                          round((grid_h - lay0["SH"]) / 2.0 / MM, 1)]
        self._fit_needed = True
        self.refresh_layout()

    # ---------------------------------------------------------- roi canvas
    def refresh_roi(self):
        cv = self.roi_canvas
        cv.delete("all")
        self._page_img = None
        page = self.cur_page()
        if page is None:
            return
        W = max(cv.winfo_width(), CANVAS_MIN)
        H = max(cv.winfo_height(), CANVAS_MIN)
        pr = page.rect
        vs = min((W - 24) / pr.width, (H - 24) / pr.height)
        vs = max(vs, 1e-4)
        ox = (W - pr.width * vs) / 2.0
        oy = (H - pr.height * vs) / 2.0
        self._roi_view = (vs, ox, oy)
        try:
            pix = page.get_pixmap(matrix=fitz.Matrix(vs, vs), alpha=False)
            self._page_img = photo_from_pixmap(pix)
            cv.create_image(ox, oy, anchor="nw", image=self._page_img)
        except Exception:
            cv.create_rectangle(ox, oy, ox + pr.width * vs, oy + pr.height * vs,
                                fill="white", outline="#202124")
        cv.create_rectangle(ox, oy, ox + pr.width * vs, oy + pr.height * vs,
                            outline="#202124")
        if self.roi is not None:
            r = self.roi
            cv.create_rectangle(ox + r.x0 * vs, oy + r.y0 * vs,
                                ox + r.x1 * vs, oy + r.y1 * vs,
                                outline="#1a73e8", width=2, tags="roirect")
            cv.create_text(ox + r.x0 * vs + 4, oy + r.y0 * vs - 8, anchor="sw",
                           text="%.1f x %.1f mm" % (r.width / MM, r.height / MM),
                           fill="#1a73e8", font=("", 8))

    def _roi_to_pt(self, x, y):
        vs, ox, oy = self._roi_view
        page = self.cur_page()
        pr = page.rect
        return (min(max((x - ox) / vs, 0.0), pr.width),
                min(max((y - oy) / vs, 0.0), pr.height))

    def on_roi_press(self, ev):
        if self._roi_view is None or self.cur_page() is None:
            return
        self._roi_start = self._roi_to_pt(ev.x, ev.y)
        self.roi_canvas.delete("rubber")

    def on_roi_move(self, ev):
        if getattr(self, "_roi_start", None) is None:
            return
        vs, ox, oy = self._roi_view
        x0, y0 = self._roi_start
        x1, y1 = self._roi_to_pt(ev.x, ev.y)
        self.roi_canvas.delete("rubber")
        self.roi_canvas.create_rectangle(
            ox + min(x0, x1) * vs, oy + min(y0, y1) * vs,
            ox + max(x0, x1) * vs, oy + max(y0, y1) * vs,
            outline="#1a73e8", width=2, dash=(4, 3), tags="rubber")
        self.status("Region: %.1f x %.1f mm"
                    % (abs(x1 - x0) / MM, abs(y1 - y0) / MM))

    def on_roi_release(self, ev):
        start = getattr(self, "_roi_start", None)
        self._roi_start = None
        if start is None:
            return
        x0, y0 = start
        x1, y1 = self._roi_to_pt(ev.x, ev.y)
        rect = fitz.Rect(min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1))
        if rect.width < 5 * MM or rect.height < 5 * MM:
            self.roi_canvas.delete("rubber")
            self.status("Region too small (needs at least 5 x 5 mm); ignored.")
            self.refresh_roi()
            return
        self.roi = rect
        self.offset_mm = [0.0, 0.0]
        self._fit_needed = True
        self.refresh_roi()
        self.refresh_layout()
        self.status("Region set: %.1f x %.1f mm. Only this region will be output."
                    % (rect.width / MM, rect.height / MM))

    def clear_roi(self):
        self.roi = None
        self.offset_mm = [0.0, 0.0]
        self._fit_needed = True
        self.refresh_roi()
        self.refresh_layout()
        self.status("Region cleared: the whole page will be output.")

    # ------------------------------------------------------------- convert
    def on_convert(self):
        if not self.doc or not self.doc_path:
            messagebox.showinfo("No file", "Open a PDF first.")
            return
        vals, msgs = self.read_params()
        if self.layout is None:
            self.compute()
        if self.layout is None:
            messagebox.showerror(
                "Cannot convert",
                "The current settings do not produce a valid layout. Check "
                "scale, overlap and margin.")
            return

        # paper mismatch warning
        if (self.warn_var.get() and self._recommended
                and vals["paper"] != self._recommended):
            ok = messagebox.askokcancel(
                "Paper differs from the recommendation",
                "The source page suits %s, but the output is set to %s.\n\n"
                "This is allowed and the scale stays exact - but you must print "
                "on %s paper. Printing %s pages on any other size lets the "
                "printer driver rescale them and the scale will be wrong.\n\n"
                "Continue with %s?"
                % (self._recommended, vals["paper"], vals["paper"],
                   vals["paper"], vals["paper"]))
            if not ok:
                return

        total, _skipped = self.total_sheets(vals)
        if total > 200:
            if not messagebox.askokcancel(
                    "Large job",
                    "This will produce %d sheets. Continue?" % total):
                return

        base, _ = os.path.splitext(self.doc_path)
        suggested = "%s_x%.2f_%s.pdf" % (os.path.basename(base), vals["scale"],
                                         vals["paper"])
        out = filedialog.asksaveasfilename(
            title="Save tiled PDF", defaultextension=".pdf",
            initialfile=suggested, initialdir=os.path.dirname(self.doc_path),
            filetypes=[("PDF files", "*.pdf")])
        if not out:
            return
        if os.path.abspath(out) == os.path.abspath(self.doc_path):
            messagebox.showerror("Cannot overwrite source",
                                 "Choose a different name from the source PDF.")
            return

        self.prog.configure(maximum=max(total, 1), value=0)

        def on_progress(done, tot):
            self.prog.configure(maximum=max(tot, 1), value=done)
            self.status("Rendering sheet %d of %d..." % (done, tot))
            self.update_idletasks()

        warnings = []
        try:
            sheets = engine.make_tiled_pdf(
                self.doc_path, out, vals["scale"],
                paper=vals["paper"], orientation=vals["orientation"],
                overlap_mm=vals["overlap_mm"], margin_mm=vals["margin_mm"],
                label_prefix="%s EB split" % vals["institution"],
                label_position=vals["label_position"],
                label_fontsize=vals["label_fontsize"],
                label_background=vals["label_background"],
                label_gray=vals["label_gray"],
                roi=(tuple(self.roi) if self.roi is not None else None),
                offset_mm=tuple(self.offset_mm),
                password=getattr(self, "doc_password", None),
                progress=on_progress,
                on_warning=warnings.append,
            )
        except engine.LayoutError as exc:
            self.prog.configure(value=0)
            messagebox.showerror("Cannot convert", str(exc))
            return
        except PermissionError:
            self.prog.configure(value=0)
            messagebox.showerror(
                "Cannot write file",
                "'%s' could not be written.\nIt may be open in a PDF viewer."
                % out)
            return
        except MemoryError:
            self.prog.configure(value=0)
            messagebox.showerror(
                "Out of memory",
                "The job is too large. Reduce the scale or use a larger paper.")
            return
        except Exception as exc:
            self.prog.configure(value=0)
            messagebox.showerror("Unexpected error",
                                 "%s\n\n%s" % (exc, traceback.format_exc(limit=3)))
            return

        self.prog.configure(value=self.prog["maximum"])
        note = ""
        if warnings:
            note += "\n\n" + "\n".join(warnings)
        messagebox.showinfo(
            "Done",
            "Created:\n%s\n\n%d sheet%s\n\nPrint at Actual size / 100%% "
            "(never 'Fit to page') on %s paper.%s"
            % (out, sheets, "" if sheets == 1 else "s", vals["paper"], note))
        self.status("Created %s (%d sheets)" % (out, sheets))


def run():
    app = App()
    app.mainloop()


if __name__ == "__main__":
    run()
