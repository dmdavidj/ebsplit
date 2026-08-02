# -*- coding: utf-8 -*-
"""
Yonsei Cancer Center - PDF EB Split
===================================

PDF를 원하는 배율(소수점 둘째 자리)로 확대하여 A4/A3 여러 장에 나눠 배치한
새 PDF를 만든다. 각 장에는 이어붙일 위치를 점선(가이드라인)으로 표시하고,
라벨을 찍는다. 이 PDF를 '실제 크기(100%)'로 인쇄하면
배율이 적용된 원본이 그대로 출력된다.

핵심 원리 (실측 검증된 물리 기하 - 변경 금지)
---------------------------------------------
- 배율 S를 적용한 전체 이미지 크기 = 원본 * S
- 이 큰 이미지를 용지(A4/A3)의 인쇄 가능 영역 단위로 잘라 여러 장(타일)에 배치
- 인접한 장끼리 overlap(겹침) 폭만큼 같은 그림을 중복 인쇄
- 그 겹침의 '정중앙'에 점선을 그림 -> 인접한 두 장의 점선이 완전히 같은 위치가 되어,
  각 장을 점선대로 잘라 맞대면(butt join) 소실/중복 없이 정확히 이어짐
- show_pdf_page 를 쓰므로 벡터 품질 그대로 유지(래스터화 안 함)

추가 기능 (기하에는 영향 없음)
------------------------------
- ROI      : 원본의 특정 영역만 배율 적용해 출력
- offset   : 내용을 용지 격자에 대해 평행이동(장수가 자동으로 늘거나 줄어듦)
- label    : 기관명/위치(5곳)/글자크기 설정 가능

의존성:  pip install pymupdf
사용법(명령줄):
    python pdf_split_scale.py 입력.pdf --scale 1.75 --paper A4
인자 없이 실행하면 GUI가 뜬다.
"""

import math
import os
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("PyMuPDF is required.  Install it with:  pip install pymupdf")

MM = 72.0 / 25.4  # 1mm = 몇 pt (PDF 좌표 단위는 pt = 1/72 inch)

# 용지 크기 (세로 기준, 단위 mm)
PAPERS = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
}

LABEL_POSITIONS = ("top-left", "top-right", "bottom-right", "bottom-left", "center")



class LayoutError(ValueError):
    """용지/여백/겹침 조합이 물리적으로 불가능할 때.

    메시지는 GUI 대화상자에 그대로 표시되므로 영어로 쓴다(GUI 는 영어 전용).
    """


class PasswordRequired(LayoutError):
    """암호로 보호된 PDF. 호출자가 암호를 받아 다시 시도할 수 있게 구분한다."""


# --------------------------------------------------------------------------- #
# 폰트 처리 (기관명에 비 라틴 문자가 들어갈 수 있다)
# --------------------------------------------------------------------------- #
def _font_for(text):
    """텍스트를 렌더할 수 있는 내장 폰트 이름."""
    try:
        text.encode("latin-1")
        return "helv"
    except (UnicodeEncodeError, AttributeError):
        return "china-ss"   # PyMuPDF 내장 CJK 폰트


def _text_length(text, fontname, fontsize):
    """폰트 폭. 내장 폰트 메트릭이 없으면 보수적으로 추정한다."""
    try:
        return fitz.get_text_length(text, fontname=fontname, fontsize=fontsize)
    except Exception:
        # CJK 는 대략 전각, 라틴은 대략 0.55em
        wide = sum(1 for ch in text if ord(ch) > 0x2000)
        return (wide * 1.0 + (len(text) - wide) * 0.55) * fontsize


def _insert_text(page, point, text, fontsize, color=(0, 0, 0)):
    """폰트를 자동 선택해 텍스트를 넣는다. 실패해도 렌더를 중단시키지 않는다."""
    font = _font_for(text)
    try:
        page.insert_text(point, text, fontname=font, fontsize=fontsize, color=color)
        return font
    except Exception:
        if font != "helv":
            ascii_text = text.encode("ascii", "replace").decode("ascii")
            try:
                page.insert_text(point, ascii_text, fontname="helv",
                                 fontsize=fontsize, color=color)
                return "helv"
            except Exception:
                pass
    return None


# --------------------------------------------------------------------------- #
# 타일 격자 계산
# --------------------------------------------------------------------------- #
def _tile_count(total, printable, step):
    """길이 total 을 printable 폭짜리 타일로, step 씩 전진하며 덮는 데 필요한 장 수."""
    if total <= printable + 1e-6:
        return 1
    return 1 + int(math.ceil((total - printable - 1e-6) / step))


def _tile_range(total, printable, step, off):
    """평행이동을 포함한 타일 인덱스 범위 (k0, k1) 를 구한다 (양끝 포함).

    격자는 좌표 0 에 고정되어 있고 타일 k 는 [k*step, k*step+printable] 을 덮는다.
    내용은 [off, off+total] 에 놓인다. off == 0 이면 결과는 _tile_count 와 동일하다.
    """
    k0 = int(math.floor((off + 1e-9) / step))
    k1 = int(math.ceil((off + total - printable - 1e-6) / step))
    if k1 < k0:
        k1 = k0
    return k0, k1


def compute_layout(src_w, src_h, scale, paper="A4", orientation="auto",
                   overlap_mm=10.0, margin_mm=5.0, offset_mm=(0.0, 0.0)):
    """한 페이지의 타일 배치를 계산한다. 렌더러와 GUI 미리보기가 공유한다.

    src_w/src_h : 배율을 적용할 원본 영역 크기 (pt). ROI 를 쓰면 ROI 크기.
    반환 dict 는 pt 단위이며, offset (0,0) 일 때 원본 구현과 완전히 동일한
    장수/좌표를 낸다.
    """
    scale = round(float(scale), 2)
    if scale <= 0:
        raise LayoutError("Scale must be greater than 0.")
    if paper not in PAPERS:
        raise LayoutError("Paper must be 'A4' or 'A3'.")
    if src_w <= 0 or src_h <= 0:
        raise LayoutError("The region to magnify has zero size.")

    O = float(overlap_mm) * MM
    margin = float(margin_mm) * MM
    if O <= 0:
        raise LayoutError("Overlap must be greater than 0 mm.")
    if margin < 0:
        raise LayoutError("Margin cannot be negative.")

    SW, SH = src_w * scale, src_h * scale
    tx = float(offset_mm[0]) * MM
    ty = float(offset_mm[1]) * MM

    pw_mm, ph_mm = PAPERS[paper]
    orient_options = []
    if orientation in ("portrait", "auto"):
        orient_options.append(("portrait", pw_mm * MM, ph_mm * MM))
    if orientation in ("landscape", "auto"):
        orient_options.append(("landscape", ph_mm * MM, pw_mm * MM))
    if not orient_options:
        raise LayoutError("Orientation must be portrait, landscape or auto.")

    best = None
    for name, PW, PH in orient_options:
        printable_w = PW - 2 * margin
        printable_h = PH - 2 * margin
        if printable_w <= O or printable_h <= O:
            raise LayoutError("Margin and overlap are larger than the paper. Reduce the margin or the overlap.")
        step_w = printable_w - O
        step_h = printable_h - O
        i0, i1 = _tile_range(SW, printable_w, step_w, tx)
        j0, j1 = _tile_range(SH, printable_h, step_h, ty)
        cols = i1 - i0 + 1
        rows = j1 - j0 + 1
        n = cols * rows
        if best is None or n < best["sheets"]:
            best = {
                "orientation": name, "sheets": n,
                "PW": PW, "PH": PH,
                "printable_w": printable_w, "printable_h": printable_h,
                "step_w": step_w, "step_h": step_h,
                "cols": cols, "rows": rows,
                "i0": i0, "j0": j0,
                "overlap": O, "margin": margin,
                "SW": SW, "SH": SH, "tx": tx, "ty": ty, "scale": scale,
            }
    return best


def tile_rects(lay):
    """레이아웃의 각 타일에 대해 좌표를 계산한다.

    yield (r, c, grid_x0, grid_y0, cx0, cy0, cx1, cy1)
      grid_* : 타일이 담당하는 격자 좌표 시작 (배율 적용 좌표계, pt)
      c*     : 그 타일에서 실제로 내용이 존재하는 구간 (배율 적용 좌표계, pt)
    """
    tx, ty, SW, SH = lay["tx"], lay["ty"], lay["SW"], lay["SH"]
    for r in range(lay["rows"]):
        gy0 = (lay["j0"] + r) * lay["step_h"]
        cy0 = max(gy0, ty)
        cy1 = min(gy0 + lay["printable_h"], ty + SH)
        for c in range(lay["cols"]):
            gx0 = (lay["i0"] + c) * lay["step_w"]
            cx0 = max(gx0, tx)
            cx1 = min(gx0 + lay["printable_w"], tx + SW)
            if cx1 - cx0 <= 1e-6 or cy1 - cy0 <= 1e-6:
                continue
            yield r, c, gx0, gy0, cx0, cy0, cx1, cy1


# --------------------------------------------------------------------------- #
# 라벨
# --------------------------------------------------------------------------- #
def _stamp_label(page, lay, text, position, fontsize,
                 background=True, label_gray=0.55):
    """라벨을 인쇄 가능 영역 안쪽 지정 위치에 찍는다."""
    margin = lay["margin"]
    pw, ph = lay["PW"], lay["PH"]
    avail_w = pw - 2 * margin
    avail_h = ph - 2 * margin
    fontsize = float(fontsize)

    font = _font_for(text)
    tlen = _text_length(text, font, fontsize)
    pad = 3.0

    block_w = tlen
    block_h = fontsize + 2.0

    box_w = min(block_w + 2 * pad, avail_w)
    box_h = min(block_h + 2 * pad, avail_h)

    if position == "top-left":
        bx, by = margin, margin
    elif position == "top-right":
        bx, by = margin + avail_w - box_w, margin
    elif position == "bottom-right":
        bx, by = margin + avail_w - box_w, margin + avail_h - box_h
    elif position == "bottom-left":
        bx, by = margin, margin + avail_h - box_h
    elif position == "center":
        bx = margin + (avail_w - box_w) / 2.0
        by = margin + (avail_h - box_h) / 2.0
    else:
        bx, by = margin, margin

    if background:
        page.draw_rect(fitz.Rect(bx, by, bx + box_w, by + box_h),
                       color=None, fill=(1, 1, 1))

    # 라벨은 워터마크처럼 연하게 찍는다.
    g = min(max(float(label_gray), 0.0), 0.95)
    _insert_text(page, (bx + pad, by + pad + fontsize), text, fontsize,
                 color=(g, g, g))


# --------------------------------------------------------------------------- #
# 본체
# --------------------------------------------------------------------------- #
def make_tiled_pdf(
    input_path,
    output_path,
    scale,
    paper="A4",
    orientation="auto",          # portrait / landscape / auto
    overlap_mm=10.0,             # 이어붙일 때 겹치는 폭
    margin_mm=5.0,               # 용지 가장자리 여백(프린터 인쇄 불가 영역 대비)
    label_prefix="Yonsei Cancer Center EB split",
    guide_rgb=(0.85, 0.1, 0.1),  # 점선 색 (빨강 계열)
    # --- 아래는 추가 기능. 기본값은 원본 동작을 그대로 재현한다. ---
    label_position="top-left",
    label_fontsize=7.0,
    label_background=False,
    label_gray=0.55,          # 0=검정, 클수록 연함(워터마크)
    roi=None,                    # (x0,y0,x1,y1) pt, 원본 표시 좌표계. None = 전체
    offset_mm=(0.0, 0.0),        # 내용 평행이동 (mm). (0,0) = 원본 동작
    password=None,
    progress=None,               # callable(done_sheets, total_sheets)
    on_warning=None,             # callable(message) - 중단은 아니지만 알려야 할 일
):
    """PDF를 배율 적용하여 타일 PDF로 만든다. 만들어진 장 수를 반환."""

    scale = round(float(scale), 2)
    if scale <= 0:
        raise LayoutError("배율(scale)은 0보다 커야 합니다.")
    if paper not in PAPERS:
        raise LayoutError("paper 는 'A4' 또는 'A3' 여야 합니다.")
    if label_position not in LABEL_POSITIONS:
        raise LayoutError("label_position must be one of: %s"
                          % ", ".join(LABEL_POSITIONS))
    if not os.path.isfile(input_path):
        raise FileNotFoundError(input_path)
    if os.path.abspath(input_path) == os.path.abspath(output_path):
        raise LayoutError("The output path is the same as the input file. Choose a different name.")

    src = open_pdf(input_path, password)
    out = fitz.open()
    try:
        if src.page_count == 0:
            raise LayoutError("The PDF has no pages.")

        total_sheets = 0
        done = 0
        skipped = []
        # 진행률용 총 장수 선계산.
        # 페이지마다 크기가 다를 수 있으므로 반드시 페이지별로 더해야 한다.
        if progress:
            total = 0
            for pno in range(src.page_count):
                b = _base_rect(src[pno], roi, strict=False)
                if b is None:
                    continue
                total += compute_layout(b.width, b.height, scale, paper,
                                        orientation, overlap_mm, margin_mm,
                                        offset_mm)["sheets"]
            total = max(total, 1)
        else:
            total = 1

        for pno in range(src.page_count):
            page = src[pno]
            # ROI 가 이 페이지와 겹치지 않으면 그 페이지만 건너뛴다.
            # 예전에는 여기서 예외가 나 문서 전체 변환이 중단됐다.
            base = _base_rect(page, roi, strict=False)
            if base is None:
                skipped.append(pno + 1)
                continue
            lay = compute_layout(base.width, base.height, scale, paper,
                                 orientation, overlap_mm, margin_mm, offset_mm)
            PW, PH = lay["PW"], lay["PH"]
            margin = lay["margin"]
            step_w, step_h = lay["step_w"], lay["step_h"]
            half = lay["overlap"] / 2.0
            cols, rows = lay["cols"], lay["rows"]

            for r, c, gx0, gy0, cx0, cy0, cx1, cy1 in tile_rects(lay):
                newpage = out.new_page(width=PW, height=PH)

                # 대상 사각형: 격자 좌표를 용지 좌표로 옮긴 것
                target = fitz.Rect(margin + (cx0 - gx0), margin + (cy0 - gy0),
                                   margin + (cx1 - gx0), margin + (cy1 - gy0))
                # 원본에서 가져올 영역 = 위 좌표를 배율로 되돌린 것
                clip = fitz.Rect(
                    base.x0 + (cx0 - lay["tx"]) / scale,
                    base.y0 + (cy0 - lay["ty"]) / scale,
                    base.x0 + (cx1 - lay["tx"]) / scale,
                    base.y0 + (cy1 - lay["ty"]) / scale,
                )
                clip = clip & page.rect          # 수치오차로 페이지를 넘지 않게
                if clip.is_empty or clip.width <= 0 or clip.height <= 0:
                    pass
                else:
                    # 벡터 그대로 배치 (배율 = target/clip = scale)
                    try:
                        newpage.show_pdf_page(target, src, pno, clip=clip)
                    except LayoutError:
                        raise
                    except Exception as exc:
                        # 손상되거나 잘린 PDF 는 열 때는 통과했다가 여기서
                        # MuPDF 예외를 낸다. 그대로 새어 나가면 트레이스백이
                        # 그대로 사용자에게 보인다.
                        raise LayoutError(
                            "Page %d of the source PDF could not be read; the "
                            "file looks damaged or incomplete. (%s)"
                            % (pno + 1, exc))

                # ---- 이어붙임 가이드(점선) : 이웃이 있는 방향의 겹침 경계 ----
                # 자르는 선은 '겹침 구간의 정중앙'에 그린다.
                # -> 인접한 두 장의 점선이 정확히 같은 내용 좌표가 되어,
                #    양쪽을 각각 점선대로 자르고 맞대면 소실 없이 딱 맞는다.
                dash = "[3 3] 0"
                gw = 0.6
                y_top, y_bot = target.y0, target.y1
                x_left, x_right = target.x0, target.x1
                if c > 0:                        # 왼쪽 이웃 -> 왼쪽 겹침의 중앙
                    x = margin + half
                    if x_left - 1e-6 <= x <= x_right + 1e-6:
                        newpage.draw_line((x, y_top), (x, y_bot),
                                          color=guide_rgb, width=gw, dashes=dash)
                if c < cols - 1:                 # 오른쪽 이웃
                    x = margin + step_w + half
                    if x_left - 1e-6 <= x <= x_right + 1e-6:
                        newpage.draw_line((x, y_top), (x, y_bot),
                                          color=guide_rgb, width=gw, dashes=dash)
                if r > 0:                        # 위쪽 이웃
                    y = margin + half
                    if y_top - 1e-6 <= y <= y_bot + 1e-6:
                        newpage.draw_line((x_left, y), (x_right, y),
                                          color=guide_rgb, width=gw, dashes=dash)
                if r < rows - 1:                 # 아래쪽 이웃
                    y = margin + step_h + half
                    if y_top - 1e-6 <= y <= y_bot + 1e-6:
                        newpage.draw_line((x_left, y), (x_right, y),
                                          color=guide_rgb, width=gw, dashes=dash)

                # ---- 라벨 + 검산 눈금 ----
                label = "%s x%.2f %s [P%d R%d/%d C%d/%d]" % (
                    label_prefix, scale, paper, pno + 1, r + 1, rows, c + 1, cols)
                _stamp_label(newpage, lay, label, label_position,
                             label_fontsize, background=label_background,
                             label_gray=label_gray)

                total_sheets += 1
                done += 1
                if progress:
                    progress(done, total)

        if total_sheets == 0:
            if skipped:
                raise LayoutError(
                    "The region does not overlap any page, so nothing was "
                    "produced. Clear the region or pick one inside the page.")
            raise LayoutError("No sheets were produced. Check the region and "
                              "the move values.")
        if skipped and on_warning:
            shown = ", ".join(str(p) for p in skipped[:10])
            if len(skipped) > 10:
                shown += ", ..."
            on_warning("The region does not overlap %d page(s), which were "
                       "left out: %s" % (len(skipped), shown))

        out.set_metadata({
            "title": "%s x%.2f %s" % (label_prefix, scale, paper),
            "subject": "print at 100%% actual size on %s paper" % paper,
            "creator": "PDF EB Split",
        })
        try:
            out.save(output_path, deflate=True, garbage=3)
        except LayoutError:
            raise
        except Exception as exc:
            # PyMuPDF 는 쓰기 실패를 PermissionError 가 아니라 자체 예외로 낸다
            # (FzErrorSystem: cannot remove file ... Permission denied).
            # 출력 PDF 를 뷰어에 열어 둔 채 다시 변환하는 것은 흔한 상황이므로
            # 알아볼 수 있는 메시지로 바꿔 준다.
            text = str(exc).lower()
            if ("permission" in text or "denied" in text
                    or "cannot remove" in text or "cannot open" in text
                    or isinstance(exc, (PermissionError, OSError))):
                raise LayoutError(
                    "Cannot write '%s'. It may be open in a PDF viewer, or the "
                    "folder may be read-only. Close the file and try again."
                    % output_path)
            raise LayoutError("Could not save the output PDF: %s" % exc)
    finally:
        out.close()
        src.close()
    return total_sheets


def _base_rect(page, roi, strict=True):
    """배율을 적용할 원본 영역. roi 는 표시 좌표계(page.rect)의 사각형.

    strict=False 면 ROI 가 이 페이지와 겹치지 않을 때 예외 대신 None 을 준다.
    (여러 페이지 문서에서 한 페이지가 작다고 전체 변환이 실패하면 안 된다.)
    """
    prect = page.rect
    if roi is None:
        return prect
    r = fitz.Rect(roi) & prect
    if r.is_empty or r.width < 1.0 or r.height < 1.0:
        if strict:
            raise LayoutError("The region is too small or lies outside the page.")
        return None
    return r


def open_pdf(path, password=None):
    """PDF 열기. 암호/손상 파일을 명확한 메시지로 바꿔 준다."""
    try:
        doc = fitz.open(path)
    except Exception as exc:
        raise LayoutError("Could not open the PDF: %s" % exc)
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise PasswordRequired("This PDF is encrypted. A password is required.")
    if doc.page_count == 0:
        doc.close()
        raise LayoutError("빈 PDF입니다.")
    return doc


# --------------------------------------------------------------------------- #
# 명령줄 인터페이스
# --------------------------------------------------------------------------- #
def _cli(argv=None):
    import argparse
    try:
        import ebsplit_config as cfgmod
        cfg, _ = cfgmod.load()
    except Exception:
        cfg = {}

    p = argparse.ArgumentParser(
        description="PDF를 배율 적용해 A4/A3 여러 장으로 나누고 이어붙임 가이드(점선)를 넣는다.")
    p.add_argument("input", help="입력 PDF 경로")
    p.add_argument("-o", "--output", help="출력 PDF 경로(생략 시 원본명_x배율.pdf)")
    p.add_argument("-s", "--scale", type=float, required=True, help="배율 (예: 1.75)")
    p.add_argument("-p", "--paper", choices=["A4", "A3"],
                   default=cfg.get("paper", "A4"))
    p.add_argument("--orientation", choices=["portrait", "landscape", "auto"],
                   default=cfg.get("orientation", "auto"))
    p.add_argument("--overlap", type=float, default=cfg.get("overlap_mm", 10.0),
                   help="겹침 폭 mm (기본 10)")
    p.add_argument("--margin", type=float, default=cfg.get("margin_mm", 5.0),
                   help="용지 여백 mm (기본 5)")
    p.add_argument("--institution", default=cfg.get("institution",
                                                    "Yonsei Cancer Center"))
    p.add_argument("--label-pos", choices=list(LABEL_POSITIONS),
                   default=cfg.get("label_position", "top-left"))
    p.add_argument("--label-size", type=float,
                   default=cfg.get("label_fontsize", 7.0))
    p.add_argument("--label-gray", type=float,
                   default=cfg.get("label_gray", 0.55),
                   help="라벨 농도 0=검정 ~ 0.9=아주 연함 (기본 0.55)")
    p.add_argument("--roi", nargs=4, type=float, metavar=("X0", "Y0", "X1", "Y1"),
                   help="ROI (원본 좌표 mm)")
    p.add_argument("--offset", nargs=2, type=float, metavar=("DX", "DY"),
                   default=(0.0, 0.0), help="내용 평행이동 mm")
    p.add_argument("--password", help="암호로 보호된 PDF의 암호")
    args = p.parse_args(argv)

    out = args.output
    if not out:
        base, _ = os.path.splitext(args.input)
        out = "%s_x%.2f_%s.pdf" % (base, round(args.scale, 2), args.paper)

    roi = None
    if args.roi:
        roi = tuple(v * MM for v in args.roi)

    warnings = []
    try:
        sheets = make_tiled_pdf(
            args.input, out, args.scale,
            paper=args.paper, orientation=args.orientation,
            overlap_mm=args.overlap, margin_mm=args.margin,
            label_prefix="%s EB split" % args.institution,
            label_position=args.label_pos, label_fontsize=args.label_size,
            label_gray=args.label_gray,
            roi=roi, offset_mm=tuple(args.offset), password=args.password,
            on_warning=warnings.append,
        )
    except FileNotFoundError as exc:
        sys.exit("Error: input file not found: %s" % exc)
    except LayoutError as exc:
        sys.exit("Error: %s" % exc)
    except PermissionError:
        sys.exit("Error: cannot write '%s'. It may be open in a PDF viewer." % out)

    for w in warnings:
        print("Warning: %s" % w)
    print("Done: %s  (%d sheet%s)" % (out, sheets, "" if sheets == 1 else "s"))
    print("Print at Actual size / 100%. Never use 'Fit to page'.")
    print("Load %s paper. Printing on any other size lets the driver rescale "
          "the page and the scale will be wrong." % args.paper)


def _prepare_console_output():
    """Windows 에서 CLI 출력이 보이고 한글이 깨지지 않도록 표준 출력을 정리한다.

    두 가지를 처리한다.

    1) --noconsole 로 빌드된 EXE 는 자기 콘솔이 없다. 터미널에서 실행된 경우
       sys.stdout 이 None 일 수 있으므로 부모 콘솔에 붙여 출력을 되살린다.
    2) 인코딩. PyInstaller 로 얼린 프로그램의 기본 출력 인코딩은 UTF-8 인데
       한국어 Windows 콘솔은 cp949 라, 그대로 두면 '완료'가 '�Ϸ�' 로 깨진다.
       콘솔이 실제로 쓰는 코드페이지에 맞춰 다시 설정한다.

    어떤 단계가 실패해도 조용히 넘어간다(파일/파이프로 리디렉션한 경우 등).
    """
    if os.name != "nt":
        return
    try:
        import ctypes
        k32 = ctypes.windll.kernel32
    except Exception:
        return

    # 콘솔이 없으면(윈도우 모드 EXE) 호출한 터미널에 붙는다.
    if sys.stdout is None or sys.stderr is None:
        try:
            k32.AttachConsole(-1)          # ATTACH_PARENT_PROCESS
        except Exception:
            pass

    enc = None
    try:
        cp = k32.GetConsoleOutputCP()
        if cp == 65001:
            # Python 3.8 에서 'cp65001' 코덱이 제거되었으므로 직접 매핑해야 한다.
            # ("cp%d" % 65001) 로 만들면 LookupError 가 나서 조용히 무시된다.
            enc = "utf-8"
        elif cp:
            enc = "cp%d" % cp
        if enc:
            "".encode(enc)                 # 실제로 쓸 수 있는 인코딩인지 확인
    except Exception:
        enc = None

    for name in ("stdout", "stderr"):
        stream = getattr(sys, name)
        if stream is None:
            # 스트림 자체가 없다: 위에서 붙인 콘솔에 직접 쓴다.
            # 콘솔에 쓰는 것이므로 콘솔 코드페이지를 그대로 따른다.
            try:
                setattr(sys, name, open("CONOUT$", "w", encoding=enc or "utf-8",
                                        errors="replace", buffering=1))
            except OSError:
                pass
            continue
        # 화면에 직접 쓸 때와 리디렉션될 때의 올바른 인코딩이 서로 다르다.
        #   - 진짜 콘솔  : 콘솔 코드페이지여야 화면에 제대로 찍힌다.
        #   - 파이프/파일 : 받는 쪽은 콘솔 코드페이지를 알 수 없다. 얼린 EXE 의
        #     기본값은 Windows ANSI 코드페이지(한국어면 cp949)라서, UTF-8 로
        #     읽는 도구에서는 한글 경로가 깨진다. UTF-8 로 고정해 예측 가능하게 한다.
        # (메시지는 모두 ASCII 영어이므로 실제로 영향을 받는 것은 파일 경로뿐이다.)
        try:
            target = enc if stream.isatty() else "utf-8"
        except Exception:
            target = enc
        if not target:
            continue
        try:
            stream.reconfigure(encoding=target, errors="replace")
        except Exception:
            pass


def _report_startup_failure(exc):
    """콘솔이 없을 수 있으므로 오류를 대화상자로도 보여 준다."""
    msg = ("EBSPLIT could not start.\n\n%s\n\n"
           "Command line still works:\n"
           "  EBSPLIT.exe input.pdf --scale 1.75 --paper A4" % exc)
    print(msg)
    try:
        import tkinter as tk
        from tkinter import messagebox
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror("EBSPLIT", msg)
        root.destroy()
    except Exception:
        pass


def main():
    if len(sys.argv) > 1:
        _prepare_console_output()
        _cli()
        return
    try:
        import ebsplit_gui
        ebsplit_gui.run()
    except Exception as exc:
        _report_startup_failure(exc)


if __name__ == "__main__":
    main()
