# -*- coding: utf-8 -*-
"""
EB Split - persistent settings
==============================

User-editable settings are stored as JSON so the portable EXE keeps its
configuration between runs.  Every numeric setting has a hard [min, max]
guard-rail: values outside the range are clamped and reported, never applied
silently.

The physical/geometric defaults (overlap 10 mm, margin 5 mm) are the values the
original field-measured build used, so a fresh config reproduces exactly the
output that was validated on paper.
"""

import copy
import json
import os
import sys

APP_NAME = "EBSPLIT"
CONFIG_BASENAME = "ebsplit_config.json"

# --------------------------------------------------------------------------- #
# Allowed values
# --------------------------------------------------------------------------- #
LABEL_POSITIONS = ("top-left", "top-right", "bottom-right", "bottom-left", "center")
PAPERS = ("A4", "A3")
ORIENTATIONS = ("auto", "portrait", "landscape")

# key -> (minimum, maximum, guidance shown in the GUI)
BOUNDS = {
    "overlap_mm": (
        5.0, 30.0,
        "Width of the strip printed on both neighbouring sheets. Below 5 mm "
        "there is not enough material to cut and butt accurately; above 30 mm "
        "you waste paper and may need extra sheets.",
    ),
    "margin_mm": (
        0.0, 20.0,
        "Unprintable edge reserve. Most laser printers cannot print within "
        "4-5 mm of the paper edge; use 0 only on a borderless-capable device.",
    ),
    "label_fontsize": (
        5.0, 24.0,
        "Label text height in points.",
    ),
    "label_gray": (
        0.0, 0.9,
        "Label ink: 0 is black, higher is lighter. Around 0.55 reads as a "
        "watermark.",
    ),
    "scale": (
        0.01, 20.0,
        "Magnification applied to the source. Rounded to 2 decimals.",
    ),
}

DEFAULTS = {
    # --- job ---------------------------------------------------------------
    # Remembered so a department that always works at one magnification does
    # not have to retype it. Always visible in the GUI and printed on every
    # sheet's label, so a stale value cannot go unnoticed.
    "scale": 1.0,
    # --- label -------------------------------------------------------------
    "institution": "Yonsei Cancer Center",
    "label_position": "top-left",
    "label_fontsize": 7.0,
    "label_gray": 0.55,
    # Watermark look by default: light grey text and no opaque box behind it.
    "label_background": False,
    # --- physical (field-validated defaults - change with care) ------------
    "overlap_mm": 10.0,
    "margin_mm": 5.0,
    # --- workflow ----------------------------------------------------------
    "paper": "A4",
    "orientation": "auto",
    "auto_paper_from_source": True,
    "warn_on_paper_mismatch": True,
}


# --------------------------------------------------------------------------- #
# Location
# --------------------------------------------------------------------------- #
def _candidate_dirs():
    """Preferred config locations, most portable first."""
    dirs = []
    if getattr(sys, "frozen", False):
        dirs.append(os.path.dirname(sys.executable))
    else:
        dirs.append(os.path.dirname(os.path.abspath(__file__)))
    appdata = os.environ.get("APPDATA") or os.path.expanduser("~")
    dirs.append(os.path.join(appdata, APP_NAME))
    return dirs


def config_path():
    """Path of the config file that will be read.

    Returns the first existing file; otherwise the first writable location.
    """
    cands = _candidate_dirs()
    for d in cands:
        p = os.path.join(d, CONFIG_BASENAME)
        if os.path.isfile(p):
            return p
    for d in cands:
        try:
            os.makedirs(d, exist_ok=True)
            probe = os.path.join(d, ".ebsplit_write_test")
            with open(probe, "w"):
                pass
            os.remove(probe)
            return os.path.join(d, CONFIG_BASENAME)
        except OSError:
            continue
    # Nothing writable: still return a path so callers can report the failure.
    return os.path.join(cands[-1], CONFIG_BASENAME)


# --------------------------------------------------------------------------- #
# Validation
# --------------------------------------------------------------------------- #
# Fallback used when a bounded value cannot be parsed at all. Falling back to
# the minimum would silently turn a typo into e.g. a 100x reduction.
_FALLBACK = dict(DEFAULTS)


def clamp(key, value):
    """Clamp a bounded numeric setting. Returns (value, message_or_None)."""
    lo, hi, _ = BOUNDS[key]
    fallback = _FALLBACK.get(key, lo)
    try:
        v = float(value)
    except (TypeError, ValueError):
        return fallback, ("%s: %r is not a number, using %s"
                          % (key, value, fallback))
    if v != v:  # NaN
        return fallback, "%s: not a number, using %s" % (key, fallback)
    if v < lo:
        return lo, "%s: %g is below the minimum, raised to %g" % (key, v, lo)
    if v > hi:
        return hi, "%s: %g is above the maximum, lowered to %g" % (key, v, hi)
    return v, None


def validate(cfg):
    """Return (clean_config, list_of_messages). Never raises."""
    out = copy.deepcopy(DEFAULTS)
    msgs = []
    if not isinstance(cfg, dict):
        return out, ["Settings file is not a JSON object; defaults restored."]

    for key in DEFAULTS:
        if key not in cfg:
            continue
        val = cfg[key]
        if key in BOUNDS:
            v, m = clamp(key, val)
            out[key] = v
            if m:
                msgs.append(m)
        elif key == "label_position":
            if val in LABEL_POSITIONS:
                out[key] = val
            else:
                msgs.append("label_position: %r is unknown, using %s"
                            % (val, out[key]))
        elif key == "paper":
            if val in PAPERS:
                out[key] = val
            else:
                msgs.append("paper: %r is unknown, using %s" % (val, out[key]))
        elif key == "orientation":
            if val in ORIENTATIONS:
                out[key] = val
            else:
                msgs.append("orientation: %r is unknown, using %s"
                            % (val, out[key]))
        elif key == "institution":
            s = ("" if val is None else str(val)).strip()
            if not s:
                msgs.append("institution: empty, using %s" % out[key])
            elif len(s) > 80:
                out[key] = s[:80]
                msgs.append("institution: truncated to 80 characters")
            else:
                out[key] = s
        else:  # booleans
            out[key] = bool(val)

    out["label_fontsize"] = round(out["label_fontsize"], 1)
    unknown = [k for k in cfg if k not in DEFAULTS] if isinstance(cfg, dict) else []
    if unknown:
        msgs.append("Ignored unknown setting(s): %s" % ", ".join(sorted(unknown)))
    return out, msgs


def load(path=None):
    """Load settings. Returns (config, messages). Never raises."""
    p = path or config_path()
    if not os.path.isfile(p):
        return copy.deepcopy(DEFAULTS), []
    try:
        with open(p, "r", encoding="utf-8") as fh:
            raw = json.load(fh)
    except (OSError, ValueError, UnicodeDecodeError) as exc:
        return copy.deepcopy(DEFAULTS), [
            "Could not read %s (%s). Defaults restored." % (p, exc)]
    cfg, msgs = validate(raw)
    return cfg, msgs


def save(cfg, path=None):
    """Validate then write settings. Returns (path, messages).

    Raises OSError if the file cannot be written.
    """
    p = path or config_path()
    clean, msgs = validate(cfg)
    os.makedirs(os.path.dirname(p) or ".", exist_ok=True)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        json.dump(clean, fh, indent=2, ensure_ascii=False, sort_keys=True)
    os.replace(tmp, p)
    return p, msgs


def bounds_text(key):
    """Human-readable range + rationale, for GUI tooltips/labels."""
    lo, hi, why = BOUNDS[key]
    return "Allowed %g - %g. %s" % (lo, hi, why)


# --------------------------------------------------------------------------- #
# Paper recommendation from the source page size
# --------------------------------------------------------------------------- #
# Nominal sizes in mm, portrait.
PAPER_MM = {"A4": (210.0, 297.0), "A3": (297.0, 420.0)}
_FIT_TOLERANCE_MM = 2.0  # absorbs 595.276 pt vs 210 mm rounding, scanner drift


def _fits(w, h, pw, ph, tol=_FIT_TOLERANCE_MM):
    """Does a w x h page fit pw x ph in either orientation?"""
    return ((w <= pw + tol and h <= ph + tol)
            or (w <= ph + tol and h <= pw + tol))


def recommend_paper(width_mm, height_mm):
    """Suggest an output paper size from the source page size.

    Returns (paper, reason). The smallest standard sheet the source itself
    fits on at 1:1 is recommended, which is what people expect when they say
    "this is an A4 drawing". Note the output paper must still match whatever
    is actually loaded in the printer.
    """
    w, h = float(width_mm), float(height_mm)
    for name in ("A4", "A3"):
        pw, ph = PAPER_MM[name]
        if _fits(w, h, pw, ph):
            return name, "source is %.0f x %.0f mm, which fits %s" % (w, h, name)
    return "A3", ("source is %.0f x %.0f mm, larger than A3; A3 keeps the sheet "
                  "count down" % (w, h))
