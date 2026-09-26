import os
import sys
import json
import math
import queue
import shutil
import tempfile
import threading
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

APP_NAME = "LuminaGrade 本地调色"
APP_VERSION = "1.0.0"


def bundled_tool(name: str) -> str:
    exe_name = name + (".exe" if os.name == "nt" else "")
    if getattr(sys, "frozen", False) and hasattr(sys, "_MEIPASS"):
        candidate = os.path.join(sys._MEIPASS, exe_name)
        if os.path.exists(candidate):
            return candidate
    found = shutil.which(exe_name) or shutil.which(name)
    if found:
        return found
    raise FileNotFoundError(f"找不到 {exe_name}。正式 EXE 会内置 FFmpeg。")


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


class LuminaGradeApp(tk.Tk):
    PRESETS = {
        "剪辑圈亮透": {
            "exposure": 0.08, "contrast": 1.12, "saturation": 1.08,
            "sharpen": 1.15, "glow": 0.26, "radius": 10.0, "warmth": -0.02,
            "highlights": 0.10,
        },
        "蓝调清透": {
            "exposure": 0.04, "contrast": 1.15, "saturation": 1.04,
            "sharpen": 1.25, "glow": 0.22, "radius": 9.0, "warmth": -0.10,
            "highlights": 0.08,
        },
        "暖白发光": {
            "exposure": 0.10, "contrast": 1.08, "saturation": 1.02,
            "sharpen": 0.85, "glow": 0.34, "radius": 13.0, "warmth": 0.10,
            "highlights": 0.12,
        },
        "柔和高光": {
            "exposure": 0.05, "contrast": 1.04, "saturation": 0.98,
            "sharpen": 0.55, "glow": 0.40, "radius": 17.0, "warmth": 0.03,
            "highlights": 0.16,
        },
    }

    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME}  v{APP_VERSION}")
        self.geometry("1180x760")
        self.minsize(1040, 680)
        self.configure(bg="#111318")

        self.input_path = tk.StringVar()
        self.output_path = tk.StringVar()
        self.preset_name = tk.StringVar(value="剪辑圈亮透")
        self.status_text = tk.StringVar(value="请选择视频。所有处理均在本机完成，不使用 AI 修复。")
        self.progress_value = tk.DoubleVar(value=0)
        self.preview_time = tk.DoubleVar(value=1.0)
        self.duration = 0.0
        self.preview_original = None
        self.preview_processed = None
        self._after_id = None
        self._worker_queue = queue.Queue()
        self._busy = False

        self.vars = {
            "exposure": tk.DoubleVar(),
            "contrast": tk.DoubleVar(),
            "saturation": tk.DoubleVar(),
            "sharpen": tk.DoubleVar(),
            "glow": tk.DoubleVar(),
            "radius": tk.DoubleVar(),
            "warmth": tk.DoubleVar(),
            "highlights": tk.DoubleVar(),
        }

        self._setup_style()
        self._build_ui()
        self.apply_preset()
        self.after(100, self._poll_worker)

    def _setup_style(self):
        style = ttk.Style(self)
        try:
            style.theme_use("clam")
        except tk.TclError:
            pass
        style.configure("TFrame", background="#111318")
        style.configure("Card.TFrame", background="#191c23")
        style.configure("TLabel", background="#111318", foreground="#e9edf4", font=("Microsoft YaHei UI", 10))
        style.configure("Muted.TLabel", background="#111318", foreground="#9da6b5", font=("Microsoft YaHei UI", 9))
        style.configure("Card.TLabel", background="#191c23", foreground="#e9edf4", font=("Microsoft YaHei UI", 10))
        style.configure("Title.TLabel", background="#111318", foreground="#ffffff", font=("Microsoft YaHei UI", 18, "bold"))
        style.configure("TButton", font=("Microsoft YaHei UI", 10), padding=(12, 8))
        style.configure("Accent.TButton", font=("Microsoft YaHei UI", 10, "bold"), padding=(16, 9))
        style.configure("TCombobox", padding=5)
        style.configure("Horizontal.TProgressbar", troughcolor="#242833", background="#8fb9ff")

    def _build_ui(self):
        root = ttk.Frame(self, padding=16)
        root.pack(fill="both", expand=True)

        header = ttk.Frame(root)
        header.pack(fill="x", pady=(0, 12))
        ttk.Label(header, text="LuminaGrade", style="Title.TLabel").pack(side="left")
        ttk.Label(header, text="  本地画质调色 / Glow / 锐化 · 非 AI", style="Muted.TLabel").pack(side="left", pady=(8, 0))

        top = ttk.Frame(root, style="Card.TFrame", padding=12)
        top.pack(fill="x", pady=(0, 12))
        ttk.Label(top, text="输入视频", style="Card.TLabel").grid(row=0, column=0, sticky="w", padx=(0, 8))
        entry = ttk.Entry(top, textvariable=self.input_path)
        entry.grid(row=0, column=1, sticky="ew", padx=(0, 8))
        ttk.Button(top, text="选择视频", command=self.choose_video).grid(row=0, column=2)
        ttk.Label(top, text="预设", style="Card.TLabel").grid(row=0, column=3, sticky="w", padx=(18, 8))
        combo = ttk.Combobox(top, textvariable=self.preset_name, state="readonly", width=14, values=list(self.PRESETS.keys()))
        combo.grid(row=0, column=4)
        combo.bind("<<ComboboxSelected>>", lambda e: self.apply_preset())
        top.columnconfigure(1, weight=1)

        body = ttk.Frame(root)
        body.pack(fill="both", expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)

        preview_card = ttk.Frame(body, style="Card.TFrame", padding=12)
        preview_card.grid(row=0, column=0, sticky="nsew", padx=(0, 10))
        controls = ttk.Frame(body, style="Card.TFrame", padding=14)
        controls.grid(row=0, column=1, sticky="nsew")

        prev_header = ttk.Frame(preview_card, style="Card.TFrame")
        prev_header.pack(fill="x", pady=(0, 8))
        ttk.Label(prev_header, text="预览（左：原片 / 右：处理）", style="Card.TLabel").pack(side="left")
        ttk.Button(prev_header, text="刷新预览", command=self.refresh_preview).pack(side="right")

        self.canvas = tk.Canvas(preview_card, bg="#0b0d12", highlightthickness=0)
        self.canvas.pack(fill="both", expand=True)
        self.canvas.bind("<Configure>", lambda e: self._draw_preview())

        time_row = ttk.Frame(preview_card, style="Card.TFrame")
        time_row.pack(fill="x", pady=(10, 0))
        ttk.Label(time_row, text="取样时间", style="Card.TLabel").pack(side="left")
        self.time_scale = ttk.Scale(time_row, from_=0, to=10, variable=self.preview_time, command=lambda _v: self._schedule_preview())
        self.time_scale.pack(side="left", fill="x", expand=True, padx=10)
        self.time_label = ttk.Label(time_row, text="1.0s", style="Card.TLabel", width=8)
        self.time_label.pack(side="right")

        ttk.Label(controls, text="画面参数", style="Card.TLabel", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w", pady=(0, 10))
        self._add_slider(controls, "曝光", "exposure", -0.30, 0.30, 0.01, "模拟提亮，不做 AI 补细节")
        self._add_slider(controls, "对比度", "contrast", 0.80, 1.35, 0.01, "增加通透和层次")
        self._add_slider(controls, "饱和度", "saturation", 0.70, 1.40, 0.01, "控制综合色彩强度")
        self._add_slider(controls, "锐化", "sharpen", 0.00, 2.00, 0.05, "Unsharp Mask，本地传统锐化")
        self._add_slider(controls, "Glow 强度", "glow", 0.00, 0.60, 0.01, "类似蓝宝石发光的屏幕混合观感")
        self._add_slider(controls, "Glow 半径", "radius", 2.0, 24.0, 0.5, "控制光晕扩散范围")
        self._add_slider(controls, "冷暖", "warmth", -0.20, 0.20, 0.01, "负值偏冷，正值偏暖")
        self._add_slider(controls, "高光亮度", "highlights", 0.00, 0.25, 0.01, "抬高亮部观感")

        actions = ttk.Frame(controls, style="Card.TFrame")
        actions.pack(fill="x", pady=(14, 0))
        ttk.Button(actions, text="恢复当前预设", command=self.apply_preset).pack(side="left")
        ttk.Button(actions, text="导出 MP4", style="Accent.TButton", command=self.export_video).pack(side="right")

        bottom = ttk.Frame(root, padding=(0, 10, 0, 0))
        bottom.pack(fill="x")
        ttk.Progressbar(bottom, variable=self.progress_value, maximum=100).pack(fill="x", side="left", expand=True, padx=(0, 12))
        ttk.Label(bottom, textvariable=self.status_text, style="Muted.TLabel").pack(side="right")

    def _add_slider(self, parent, label, key, lo, hi, step, hint):
        block = ttk.Frame(parent, style="Card.TFrame")
        block.pack(fill="x", pady=5)
        line = ttk.Frame(block, style="Card.TFrame")
        line.pack(fill="x")
        ttk.Label(line, text=label, style="Card.TLabel").pack(side="left")
        value_label = ttk.Label(line, text="", style="Card.TLabel", width=7)
        value_label.pack(side="right")

        def on_change(_value=None):
            value_label.config(text=f"{self.vars[key].get():.2f}")
            self._schedule_preview()

        scale = ttk.Scale(block, from_=lo, to=hi, variable=self.vars[key], command=on_change)
        scale.pack(fill="x")
        ttk.Label(block, text=hint, style="Muted.TLabel").pack(anchor="w")
        self.vars[key].trace_add("write", lambda *_args, lbl=value_label, k=key: lbl.config(text=f"{self.vars[k].get():.2f}"))

    def apply_preset(self):
        preset = self.PRESETS[self.preset_name.get()]
        for k, v in preset.items():
            self.vars[k].set(v)
        self._schedule_preview(delay=80)

    def choose_video(self):
        path = filedialog.askopenfilename(
            title="选择视频",
            filetypes=[("视频文件", "*.mp4 *.mov *.mkv *.avi *.m4v *.webm"), ("所有文件", "*.*")],
        )
        if not path:
            return
        self.input_path.set(path)
        stem = str(Path(path).with_suffix(""))
        self.output_path.set(stem + "_LuminaGrade.mp4")
        try:
            self.duration = self._probe_duration(path)
        except Exception:
            self.duration = 10.0
        end = max(0.1, self.duration)
        self.time_scale.configure(to=end)
        self.preview_time.set(min(max(0.5, end * 0.18), max(0.1, end - 0.1)))
        self.status_text.set(f"已加载：{Path(path).name} · {self.duration:.1f}s")
        self.refresh_preview()

    def _probe_duration(self, path):
        ffprobe = bundled_tool("ffprobe")
        cmd = [ffprobe, "-v", "error", "-show_entries", "format=duration", "-of", "json", path]
        creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
        out = subprocess.check_output(cmd, text=True, encoding="utf-8", errors="ignore", creationflags=creationflags)
        data = json.loads(out)
        return float(data["format"]["duration"])

    def _schedule_preview(self, delay=280):
        self.time_label.config(text=f"{self.preview_time.get():.1f}s")
        if not self.input_path.get() or self._busy:
            return
        if self._after_id:
            self.after_cancel(self._after_id)
        self._after_id = self.after(delay, self.refresh_preview)

    def _filter_graph(self):
        exposure = self.vars["exposure"].get()
        brightness = clamp(exposure, -0.30, 0.30)
        contrast = clamp(self.vars["contrast"].get(), 0.5, 2.0)
        saturation = clamp(self.vars["saturation"].get(), 0.0, 3.0)
        sharpen = clamp(self.vars["sharpen"].get(), 0.0, 2.0)
        glow = clamp(self.vars["glow"].get(), 0.0, 1.0)
        radius = clamp(self.vars["radius"].get(), 0.5, 40.0)
        warmth = clamp(self.vars["warmth"].get(), -0.30, 0.30)
        highlights = clamp(self.vars["highlights"].get(), 0.0, 0.30)

        rs = clamp(warmth * 0.45, -0.25, 0.25)
        bs = clamp(-warmth * 0.45, -0.25, 0.25)
        base_brightness = clamp(brightness + highlights * 0.10, -0.35, 0.35)
        glow_brightness = clamp(-0.18 + highlights * 0.35, -0.25, 0.08)
        glow_contrast = 1.55 + highlights * 0.9

        graph = (
            f"[0:v]eq=brightness={base_brightness:.4f}:contrast={contrast:.4f}:saturation={saturation:.4f},"
            f"colorbalance=rs={rs:.4f}:bs={bs:.4f},"
            f"unsharp=5:5:{sharpen:.4f}:5:5:0,split=2[base][glow];"
            f"[glow]eq=brightness={glow_brightness:.4f}:contrast={glow_contrast:.4f},"
            f"gblur=sigma={radius:.3f}[halo];"
            f"[base][halo]blend=all_mode=screen:all_opacity={glow:.4f}[outv]"
        )
        return graph

    def refresh_preview(self):
        if self._busy:
            return
        path = self.input_path.get()
        if not path or not os.path.exists(path):
            return
        self._busy = True
        self.status_text.set("正在生成本地预览…")
        params = self._filter_graph()
        t = float(self.preview_time.get())
        threading.Thread(target=self._preview_worker, args=(path, t, params), daemon=True).start()

    def _preview_worker(self, path, t, graph):
        tmpdir = tempfile.mkdtemp(prefix="luminagrade_preview_")
        try:
            ffmpeg = bundled_tool("ffmpeg")
            before = os.path.join(tmpdir, "before.jpg")
            after = os.path.join(tmpdir, "after.jpg")
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            common = [ffmpeg, "-hide_banner", "-loglevel", "error", "-y", "-ss", f"{t:.3f}", "-i", path]
            subprocess.check_call(common + ["-frames:v", "1", "-q:v", "2", before], creationflags=creationflags)
            subprocess.check_call(common + ["-filter_complex", graph, "-map", "[outv]", "-frames:v", "1", "-q:v", "2", after], creationflags=creationflags)
            img_a = Image.open(before).convert("RGB").copy()
            img_b = Image.open(after).convert("RGB").copy()
            self._worker_queue.put(("preview_ok", img_a, img_b))
        except Exception as e:
            self._worker_queue.put(("error", f"预览失败：{e}"))
        finally:
            shutil.rmtree(tmpdir, ignore_errors=True)

    def _draw_preview(self):
        if self.preview_original is None or self.preview_processed is None:
            self.canvas.delete("all")
            w = max(1, self.canvas.winfo_width())
            h = max(1, self.canvas.winfo_height())
            self.canvas.create_text(w/2, h/2, text="选择视频后，这里会显示原片 / 处理后对比", fill="#7f8898", font=("Microsoft YaHei UI", 13))
            return
        self.canvas.delete("all")
        w = max(10, self.canvas.winfo_width())
        h = max(10, self.canvas.winfo_height())
        gap = 8
        cell_w = int((w - gap) / 2)
        cell_h = h

        def fit(img):
            copy = img.copy()
            copy.thumbnail((cell_w, cell_h), Image.Resampling.LANCZOS)
            return copy

        a = fit(self.preview_original)
        b = fit(self.preview_processed)
        self._tk_a = ImageTk.PhotoImage(a)
        self._tk_b = ImageTk.PhotoImage(b)
        ax = cell_w // 2
        bx = cell_w + gap + cell_w // 2
        cy = cell_h // 2
        self.canvas.create_image(ax, cy, image=self._tk_a)
        self.canvas.create_image(bx, cy, image=self._tk_b)
        self.canvas.create_text(12, 12, anchor="nw", text="原片", fill="white", font=("Microsoft YaHei UI", 10, "bold"))
        self.canvas.create_text(cell_w + gap + 12, 12, anchor="nw", text="处理后", fill="white", font=("Microsoft YaHei UI", 10, "bold"))

    def export_video(self):
        if self._busy:
            return
        path = self.input_path.get()
        if not path or not os.path.exists(path):
            messagebox.showinfo(APP_NAME, "请先选择一个视频。")
            return
        initial = self.output_path.get() or str(Path(path).with_suffix("")) + "_LuminaGrade.mp4"
        out = filedialog.asksaveasfilename(
            title="导出 MP4",
            defaultextension=".mp4",
            initialfile=Path(initial).name,
            initialdir=str(Path(initial).parent),
            filetypes=[("MP4 视频", "*.mp4")],
        )
        if not out:
            return
        self.output_path.set(out)
        self._busy = True
        self.progress_value.set(0)
        self.status_text.set("正在导出，本地 FFmpeg 处理中…")
        graph = self._filter_graph()
        duration = max(self.duration, 0.01)
        threading.Thread(target=self._export_worker, args=(path, out, graph, duration), daemon=True).start()

    def _export_worker(self, path, out, graph, duration):
        try:
            ffmpeg = bundled_tool("ffmpeg")
            creationflags = subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0
            cmd = [
                ffmpeg, "-hide_banner", "-y", "-i", path,
                "-filter_complex", graph,
                "-map", "[outv]", "-map", "0:a?",
                "-c:v", "libx264", "-preset", "medium", "-crf", "16",
                "-pix_fmt", "yuv420p",
                "-c:a", "aac", "-b:a", "192k",
                "-movflags", "+faststart",
                "-progress", "pipe:1", "-nostats", out,
            ]
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, encoding="utf-8", errors="ignore",
                creationflags=creationflags,
            )
            assert proc.stdout is not None
            for line in proc.stdout:
                line = line.strip()
                if line.startswith("out_time_ms="):
                    try:
                        micros = float(line.split("=", 1)[1])
                        pct = clamp((micros / 1_000_000.0) / duration * 100.0, 0.0, 99.5)
                        self._worker_queue.put(("progress", pct))
                    except ValueError:
                        pass
            stderr = proc.stderr.read() if proc.stderr else ""
            code = proc.wait()
            if code != 0:
                raise RuntimeError(stderr[-2500:] or f"FFmpeg 退出码 {code}")
            self._worker_queue.put(("export_ok", out))
        except Exception as e:
            self._worker_queue.put(("error", f"导出失败：{e}"))

    def _poll_worker(self):
        try:
            while True:
                msg = self._worker_queue.get_nowait()
                kind = msg[0]
                if kind == "preview_ok":
                    self.preview_original, self.preview_processed = msg[1], msg[2]
                    self._busy = False
                    self.status_text.set("预览已刷新。参数变化后会自动重算。")
                    self._draw_preview()
                elif kind == "progress":
                    self.progress_value.set(msg[1])
                    self.status_text.set(f"正在导出… {msg[1]:.0f}%")
                elif kind == "export_ok":
                    self._busy = False
                    self.progress_value.set(100)
                    self.status_text.set("导出完成。")
                    messagebox.showinfo(APP_NAME, f"导出完成：\n{msg[1]}")
                elif kind == "error":
                    self._busy = False
                    self.status_text.set("处理失败。")
                    messagebox.showerror(APP_NAME, msg[1])
        except queue.Empty:
            pass
        self.after(100, self._poll_worker)


if __name__ == "__main__":
    app = LuminaGradeApp()
    app.mainloop()
