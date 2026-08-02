# -*- coding: utf-8 -*-
"""
Yonsei Cancer Center - PDF EB Split
===================================

PDF를 원하는 배율(소수점 둘째 자리)로 확대하여 A4/A3 여러 장에 나눠 배치한
새 PDF를 만든다. 각 장에는 이어붙일 위치를 점선(가이드라인)으로 표시하고,
좌측 최상단에 라벨을 찍는다. 이 PDF를 '실제 크기(100%)'로 인쇄하면
배율이 적용된 원본이 그대로 출력된다.

핵심 원리
---------
- 배율 S를 적용한 전체 이미지 크기 = 원본 * S
- 이 큰 이미지를 용지(A4/A3)의 인쇄 가능 영역 단위로 잘라 여러 장(타일)에 배치
- 인접한 장끼리 overlap(겹침) 폭만큼 같은 그림을 중복 인쇄
- 그 겹침의 '정중앙'에 점선을 그림 -> 인접한 두 장의 점선이 완전히 같은 위치가 되어,
  각 장을 점선대로 잘라 맞대면(butt join) 소실/중복 없이 정확히 이어짐
- show_pdf_page 를 쓰므로 벡터 품질 그대로 유지(래스터화 안 함)

의존성:  pip install pymupdf
사용법(명령줄):
    python pdf_split_scale.py 입력.pdf --scale 1.75 --paper A4
인자 없이 실행하면 간단한 GUI(파일 선택창)가 뜬다.
"""

import math
import os
import sys

try:
    import fitz  # PyMuPDF
except ImportError:
    sys.exit("PyMuPDF가 필요합니다.  pip install pymupdf  로 설치하세요.")

MM = 72.0 / 25.4  # 1mm = 몇 pt (PDF 좌표 단위는 pt = 1/72 inch)

# 용지 크기 (세로 기준, 단위 mm)
PAPERS = {
    "A4": (210.0, 297.0),
    "A3": (297.0, 420.0),
}


def _tile_count(total, printable, step):
    """길이 total 을 printable 폭짜리 타일로, step 씩 전진하며 덮는 데 필요한 장 수."""
    if total <= printable + 1e-6:
        return 1
    return 1 + int(math.ceil((total - printable - 1e-6) / step))


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
):
    """PDF를 배율 적용하여 타일 PDF로 만든다. 만들어진 (장수, 페이지수)를 반환."""

    scale = round(float(scale), 2)
    if scale <= 0:
        raise ValueError("배율(scale)은 0보다 커야 합니다.")
    if paper not in PAPERS:
        raise ValueError("paper 는 'A4' 또는 'A3' 여야 합니다.")
    if not os.path.isfile(input_path):
        raise FileNotFoundError(input_path)

    O = overlap_mm * MM
    margin = margin_mm * MM

    # 용지 방향 후보(가로/세로) 구성
    pw_mm, ph_mm = PAPERS[paper]
    orient_options = []
    if orientation in ("portrait", "auto"):
        orient_options.append(("portrait", pw_mm * MM, ph_mm * MM))
    if orientation in ("landscape", "auto"):
        orient_options.append(("landscape", ph_mm * MM, pw_mm * MM))
    if not orient_options:
        raise ValueError("orientation 은 portrait/landscape/auto 중 하나입니다.")

    src = fitz.open(input_path)
    if src.page_count == 0:
        raise ValueError("빈 PDF입니다.")
    out = fitz.open()

    total_sheets = 0

    for pno in range(src.page_count):
        page = src[pno]
        srect = page.rect                 # 원본 페이지 영역(회전 반영된 표시 좌표)
        sw, sh = srect.width, srect.height
        SW, SH = sw * scale, sh * scale   # 배율 적용 후 전체 크기(pt)

        # 방향별로 필요한 장 수 계산 -> 가장 적은 방향 선택
        best = None
        for name, PW, PH in orient_options:
            printable_w = PW - 2 * margin
            printable_h = PH - 2 * margin
            if printable_w <= O or printable_h <= O:
                raise ValueError("여백/겹침이 용지보다 큽니다. margin/overlap 값을 줄이세요.")
            step_w = printable_w - O
            step_h = printable_h - O
            cols = _tile_count(SW, printable_w, step_w)
            rows = _tile_count(SH, printable_h, step_h)
            n = cols * rows
            if best is None or n < best[0]:
                best = (n, PW, PH, printable_w, printable_h, step_w, step_h, cols, rows)

        _, PW, PH, printable_w, printable_h, step_w, step_h, cols, rows = best

        for r in range(rows):
            for c in range(cols):
                # 이 타일이 담당하는 '배율 적용 좌표' 영역 (마지막 행/열은 잘라서 clamp)
                cx0 = c * step_w
                cy0 = r * step_h
                cx1 = min(cx0 + printable_w, SW)
                cy1 = min(cy0 + printable_h, SH)
                tw = cx1 - cx0
                th = cy1 - cy0

                newpage = out.new_page(width=PW, height=PH)

                # 대상 사각형(용지 위 위치) = 여백 안쪽 좌상단부터 실제 배율 크기
                target = fitz.Rect(margin, margin, margin + tw, margin + th)
                # 원본에서 가져올 영역 = 위 좌표를 배율로 되돌린 것
                clip = fitz.Rect(
                    srect.x0 + cx0 / scale,
                    srect.y0 + cy0 / scale,
                    srect.x0 + cx1 / scale,
                    srect.y0 + cy1 / scale,
                )
                # 벡터 그대로 배치 (배율 = target/clip = scale)
                newpage.show_pdf_page(target, src, pno, clip=clip)

                # ---- 이어붙임 가이드(점선) : 이웃이 있는 방향의 겹침 경계에 표시 ----
                dash = "[3 3] 0"
                gw = 0.6
                y_top, y_bot = margin, margin + th
                x_left, x_right = margin, margin + tw
                # 자르는 선은 '겹침 구간의 정중앙'에 그린다.
                # -> 인접한 두 장의 점선이 정확히 같은 내용 좌표가 되어,
                #    양쪽을 각각 점선대로 자르고 맞대면 소실 없이 딱 맞는다.
                half = O / 2.0
                if c > 0:  # 왼쪽 이웃 -> 왼쪽 겹침의 중앙
                    x = margin + half
                    newpage.draw_line((x, y_top), (x, y_bot),
                                      color=guide_rgb, width=gw, dashes=dash)
                if c < cols - 1:  # 오른쪽 이웃 -> 오른쪽 겹침의 중앙
                    x = margin + step_w + half
                    newpage.draw_line((x, y_top), (x, y_bot),
                                      color=guide_rgb, width=gw, dashes=dash)
                if r > 0:  # 위쪽 이웃
                    y = margin + half
                    newpage.draw_line((x_left, y), (x_right, y),
                                      color=guide_rgb, width=gw, dashes=dash)
                if r < rows - 1:  # 아래쪽 이웃
                    y = margin + step_h + half
                    newpage.draw_line((x_left, y), (x_right, y),
                                      color=guide_rgb, width=gw, dashes=dash)

                # ---- 좌측 최상단 라벨 ----
                label = "%s x%.2f  [P%d R%d/%d C%d/%d]" % (
                    label_prefix, scale, pno + 1, r + 1, rows, c + 1, cols
                )
                fs = 7
                tlen = fitz.get_text_length(label, fontname="helv", fontsize=fs)
                box = fitz.Rect(margin, margin, margin + tlen + 6, margin + fs + 4)
                newpage.draw_rect(box, color=None, fill=(1, 1, 1))  # 흰 배경으로 가독성 확보
                newpage.insert_text((margin + 3, margin + fs + 1), label,
                                    fontname="helv", fontsize=fs, color=(0, 0, 0))

        total_sheets += cols * rows

    out.set_metadata({"title": "%s x%.2f" % (label_prefix, scale)})
    out.save(output_path, deflate=True, garbage=3)
    out.close()
    src.close()
    return total_sheets


# --------------------------------------------------------------------------- #
# 명령줄 인터페이스
# --------------------------------------------------------------------------- #
def _cli():
    import argparse
    p = argparse.ArgumentParser(
        description="PDF를 배율 적용해 A4/A3 여러 장으로 나누고 이어붙임 가이드(점선)를 넣는다.")
    p.add_argument("input", help="입력 PDF 경로")
    p.add_argument("-o", "--output", help="출력 PDF 경로(생략 시 원본명_x배율.pdf)")
    p.add_argument("-s", "--scale", type=float, required=True, help="배율 (예: 1.75)")
    p.add_argument("-p", "--paper", choices=["A4", "A3"], default="A4")
    p.add_argument("--orientation", choices=["portrait", "landscape", "auto"], default="auto")
    p.add_argument("--overlap", type=float, default=10.0, help="겹침 폭 mm (기본 10)")
    p.add_argument("--margin", type=float, default=5.0, help="용지 여백 mm (기본 5)")
    args = p.parse_args()

    out = args.output
    if not out:
        base, _ = os.path.splitext(args.input)
        out = "%s_x%.2f_%s.pdf" % (base, round(args.scale, 2), args.paper)

    sheets = make_tiled_pdf(
        args.input, out, args.scale,
        paper=args.paper, orientation=args.orientation,
        overlap_mm=args.overlap, margin_mm=args.margin,
    )
    print("완료 : %s  (총 %d장)" % (out, sheets))
    print("인쇄할 때 반드시 '실제 크기 / 100% / Actual size' 로 출력하세요 (맞춤/축소 금지).")


# --------------------------------------------------------------------------- #
# 인자 없이 실행하면 간단한 GUI
# --------------------------------------------------------------------------- #
def _gui():
    import tkinter as tk
    from tkinter import filedialog, messagebox, ttk

    root = tk.Tk()
    root.title("Yonsei Cancer Center - PDF EB Split")
    root.geometry("460x300")

    path_var = tk.StringVar()
    scale_var = tk.StringVar(value="1.00")
    paper_var = tk.StringVar(value="A4")
    orient_var = tk.StringVar(value="auto")

    def pick():
        f = filedialog.askopenfilename(filetypes=[("PDF", "*.pdf")])
        if f:
            path_var.set(f)

    def run():
        try:
            scale = round(float(scale_var.get()), 2)
        except ValueError:
            messagebox.showerror("오류", "배율은 숫자여야 합니다. 예: 1.75")
            return
        inp = path_var.get()
        if not inp:
            messagebox.showerror("오류", "PDF 파일을 선택하세요.")
            return
        base, _ = os.path.splitext(inp)
        out = "%s_x%.2f_%s.pdf" % (base, scale, paper_var.get())
        try:
            sheets = make_tiled_pdf(inp, out, scale,
                                    paper=paper_var.get(),
                                    orientation=orient_var.get())
        except Exception as e:  # noqa
            messagebox.showerror("실패", str(e))
            return
        messagebox.showinfo(
            "완료",
            "생성 완료\n%s\n총 %d장\n\n인쇄 시 '실제 크기(100%%)'로 출력하세요." % (out, sheets))

    pad = {"padx": 8, "pady": 6}
    frm = ttk.Frame(root, padding=12)
    frm.pack(fill="both", expand=True)

    ttk.Button(frm, text="PDF 선택", command=pick).grid(row=0, column=0, **pad)
    ttk.Entry(frm, textvariable=path_var, width=42).grid(row=0, column=1, columnspan=2, **pad)

    ttk.Label(frm, text="배율(예 1.75)").grid(row=1, column=0, sticky="e", **pad)
    ttk.Entry(frm, textvariable=scale_var, width=10).grid(row=1, column=1, sticky="w", **pad)

    ttk.Label(frm, text="용지").grid(row=2, column=0, sticky="e", **pad)
    ttk.Combobox(frm, textvariable=paper_var, values=["A4", "A3"],
                 width=8, state="readonly").grid(row=2, column=1, sticky="w", **pad)

    ttk.Label(frm, text="방향").grid(row=3, column=0, sticky="e", **pad)
    ttk.Combobox(frm, textvariable=orient_var, values=["auto", "portrait", "landscape"],
                 width=10, state="readonly").grid(row=3, column=1, sticky="w", **pad)

    ttk.Button(frm, text="변환 실행", command=run).grid(row=4, column=0, columnspan=3, pady=16)

    root.mainloop()


if __name__ == "__main__":
    if len(sys.argv) > 1:
        _cli()
    else:
        try:
            _gui()
        except Exception:
            print("GUI를 열 수 없습니다. 명령줄로 사용하세요:")
            print("  python pdf_split_scale.py 입력.pdf --scale 1.75 --paper A4")
