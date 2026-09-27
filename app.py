import os
import sys
import json
import queue
import shutil
import tempfile
import threading
import subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

APP_NAME = "LuminaGrade Ultra 本地调色"
APP_VERSION = "2.0.0"
IMAGE_EXTS = {'.png', '.jpg', '.jpeg', '.bmp', '.webp', '.tif', '.tiff'}


def bundled_tool(name: str) -> str:
    exe = name + ('.exe' if os.name == 'nt' else '')
    if getattr(sys, 'frozen', False) and hasattr(sys, '_MEIPASS'):
        p = os.path.join(sys._MEIPASS, exe)
        if os.path.exists(p):
            return p
    p = shutil.which(exe) or shutil.which(name)
    if p:
        return p
    raise FileNotFoundError(f'找不到 {exe}。正式 EXE 会内置 FFmpeg/FFprobe。')


def clamp(v, lo, hi):
    return max(lo, min(hi, v))


def run_checked(cmd):
    flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
    p = subprocess.run(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding='utf-8', errors='ignore', creationflags=flags
    )
    if p.returncode:
        detail = (p.stderr or p.stdout or '').strip()[-3500:]
        raise RuntimeError(detail or f'FFmpeg 退出码 {p.returncode}')
    return p.stdout


def video_info(path):
    ffprobe = bundled_tool('ffprobe')
    out = run_checked([
        ffprobe, '-v', 'error', '-select_streams', 'v:0',
        '-show_entries', 'stream=width,height:format=duration',
        '-of', 'json', path
    ])
    data = json.loads(out)
    stream = (data.get('streams') or [{}])[0]
    fmt = data.get('format') or {}
    return {
        'width': int(stream.get('width') or 1920),
        'height': int(stream.get('height') or 1080),
        'duration': float(fmt.get('duration') or 10.0),
    }


def threshold_expr(threshold_255: float, power: float = 1.15) -> str:
    t = clamp(threshold_255, 0.0, 252.0)
    return (
        f"if(lt(val\\,{t:.3f})\\,0\\,"
        f"pow((val-{t:.3f})/(255-{t:.3f})\\,{power:.4f})*255)"
    )


def high_threshold_expr(t: float = 185.0, gain: float = 3.2) -> str:
    return f"if(lt(val\\,{t:.3f})\\,0\\,(val-{t:.3f})*{gain:.4f})"


def build_filter_graph(p, width, height, src_idx=0, bg_idx=None, matte_idx=None):
    W = max(16, int(width))
    H = max(16, int(height))
    m = min(W, H)

    exposure = clamp(p['exposure'], -0.35, 0.35)
    contrast = clamp(p['contrast'], 0.75, 1.90)
    saturation = clamp(p['saturation'], 0.45, 1.60)
    temperature = clamp(p['temperature'], -1.0, 1.0)
    tint = clamp(p['tint'], -1.0, 1.0)
    shadows = clamp(p['shadows'], -0.30, 0.30)
    highlights = clamp(p['highlights'], -0.25, 0.35)
    edge = clamp(p['edge_enhance'], 0.0, 1.0)

    d = contrast - 1.0
    y10 = clamp(0.10 - 0.05 * d + 0.20 * shadows, 0.0, 0.18)
    y20 = clamp(0.20 + 0.05 * d + 0.10 * shadows, y10 + 0.02, 0.35)
    y35 = clamp(0.35 + 0.18 * d + 0.12 * highlights, y20 + 0.03, 0.65)
    y65 = clamp(0.65 + 0.25 * d + 0.18 * highlights, y35 + 0.05, 0.95)

    sat_internal = saturation * 0.74
    rs = temperature * 0.08
    bs = -temperature * 0.08
    gm = tint * 0.08

    brightness = clamp(p['glow_brightness'], 0.0, 4.0)
    threshold = clamp(p['threshold'], 0.0, 1.0)
    glow_width = clamp(p['glow_width'], 0.0, 2.0)
    falloff = clamp(p['glow_falloff'], -2.0, 2.0)
    bias = clamp(p['glow_bias'], -3.0, 3.0)
    width_x = clamp(p['width_x'], 0.0, 3.0)
    width_y = clamp(p['width_y'], 0.0, 3.0)
    width_r = clamp(p['width_r'], 0.0, 3.0)
    width_g = clamp(p['width_g'], 0.0, 3.0)
    width_b = clamp(p['width_b'], 0.0, 3.0)
    color_r = clamp(p['color_r'], 0.0, 2.0)
    color_g = clamp(p['color_g'], 0.0, 2.0)
    color_b = clamp(p['color_b'], 0.0, 2.0)

    after_amount = clamp(p['after_glow'], 0.0, 1.5)
    after_width = clamp(p['after_width'], 0.0, 2.0)
    after_x = clamp(p['after_stretch_x'], 0.0, 3.0)
    after_y = clamp(p['after_stretch_y'], 0.0, 3.0)
    highlight_pop = clamp(p['highlight_pop'], 0.0, 1.5)
    atmosphere = clamp(p['atmosphere'], 0.0, 1.0)
    glow_mix = clamp(p['glow_mix'], 0.0, 1.0)

    thr = clamp(threshold - bias * 0.035, 0.0, 0.985)
    texpr = threshold_expr(thr * 255.0, power=1.15 + max(0.0, -bias) * 0.08)

    sigma = max(0.25, glow_width * m * 0.060)
    sr = max(0.20, sigma * max(width_x, 0.03) * max(width_r, 0.03))
    srv = max(0.20, sigma * max(width_y, 0.03) * max(width_r, 0.03))
    sg = max(0.20, sigma * max(width_x, 0.03) * max(width_g, 0.03))
    sgv = max(0.20, sigma * max(width_y, 0.03) * max(width_g, 0.03))
    sb = max(0.20, sigma * max(width_x, 0.03) * max(width_b, 0.03))
    sbv = max(0.20, sigma * max(width_y, 0.03) * max(width_b, 0.03))

    far_mult = max(1.0, 1.80 + falloff * 1.20)
    far_opacity = clamp(0.18 + falloff * 0.22, 0.0, 0.72)
    after_sigma = max(0.20, after_width * m * 0.055)
    after_opacity = clamp(after_amount * 0.70, 0.0, 0.90)
    glow_gain = brightness * 0.42
    hi_opacity = clamp(highlight_pop * 0.60, 0.0, 0.85)
    combine_mode = {'Screen': 'screen', 'Add': 'addition', 'Overlay': 'overlay'}.get(p['combine'], 'screen')

    parts = []
    src = f'[{src_idx}:v]'
    parts.append(
        f"{src}scale={W}:{H}:flags=lanczos,format=rgb24,"
        f"curves=all='0/0 0.10/{y10:.4f} 0.20/{y20:.4f} 0.35/{y35:.4f} 0.65/{y65:.4f} 1/1',"
        f"eq=brightness={exposure:.4f}:saturation={sat_internal:.4f},"
        f"colorbalance=rs={rs:.4f}:gm={gm:.4f}:bs={bs:.4f},"
        f"split=3[graded_base][glow_source][highlight_source]"
    )

    if bg_idx is not None:
        source_opacity = clamp(p['source_opacity'], 0.0, 1.0)
        parts.append(
            f"[{bg_idx}:v]scale={W}:{H}:force_original_aspect_ratio=decrease:flags=lanczos,"
            f"pad={W}:{H}:(ow-iw)/2:(oh-ih)/2:color=black,format=rgb24[bg]"
        )
        parts.append(f"[graded_base][bg]blend=all_mode=normal:all_opacity={source_opacity:.4f}[base_pre]")
    else:
        parts.append("[graded_base]null[base_pre]")

    parts.append(f"[base_pre]cas=strength={edge:.4f}[base]")

    glow_input = 'glow_source'
    if matte_idx is not None and not p['bypass_mask']:
        resize = clamp(p['mask_resize'], 0.10, 2.00)
        relx = clamp(p['mask_resize_x'], 0.10, 2.00)
        rely = clamp(p['mask_resize_y'], 0.10, 2.00)
        sw = max(2, int(W * resize * relx))
        sh = max(2, int(H * resize * rely))
        cw, ch = min(sw, W), min(sh, H)
        shiftx = int(clamp(p['mask_shift_x'], -W, W))
        shifty = int(clamp(p['mask_shift_y'], -H, H))
        cropx = max(0, min(max(0, sw - cw), (sw - cw)//2 - shiftx))
        cropy = max(0, min(max(0, sh - ch), (sh - ch)//2 - shifty))
        padx = max(0, min(W - cw, (W - cw)//2 + shiftx))
        pady = max(0, min(H - ch, (H - ch)//2 + shifty))
        blur = clamp(p['mask_blur'], 0.0, 60.0)
        opacity = clamp(p['mask_opacity'], 0.0, 1.0)
        invert = ',negate' if p['invert_mask'] else ''
        parts.append(
            f"[{matte_idx}:v]scale={sw}:{sh}:flags=lanczos,crop={cw}:{ch}:{cropx}:{cropy},"
            f"pad={W}:{H}:{padx}:{pady}:color=black,format=gray"
            f"{',gblur=sigma='+format(blur,'.3f') if blur > 0.01 else ''}{invert},"
            f"format=rgb24,lutrgb=r='val*{opacity:.4f}':g='val*{opacity:.4f}':b='val*{opacity:.4f}'[maskrgb]"
        )
        parts.append("[glow_source][maskrgb]blend=all_mode=multiply[glow_masked]")
        glow_input = 'glow_masked'
        if p['show_mask_only']:
            parts.append("[maskrgb]null[outv]")
            parts.append("[base]nullsink")
            parts.append("[highlight_source]nullsink")
            parts.append("[glow_masked]nullsink")
            return ';'.join(parts)

    parts.append(
        f"[{glow_input}]lutrgb=r='{texpr}':g='{texpr}':b='{texpr}',"
        f"lutrgb=r='val*{glow_gain*color_r:.4f}':g='val*{glow_gain*color_g:.4f}':b='val*{glow_gain*color_b:.4f}',"
        f"split=4[thr_r][thr_g][thr_b][threshold_view]"
    )

    parts.append(f"[thr_r]colorchannelmixer=gg=0:bb=0,gblur=sigma={sr:.3f}:sigmaV={srv:.3f}:steps=2[glow_r]")
    parts.append(f"[thr_g]colorchannelmixer=rr=0:bb=0,gblur=sigma={sg:.3f}:sigmaV={sgv:.3f}:steps=2[glow_g]")
    parts.append(f"[thr_b]colorchannelmixer=rr=0:gg=0,gblur=sigma={sb:.3f}:sigmaV={sbv:.3f}:steps=2[glow_b]")
    parts.append("[glow_r][glow_g]blend=all_mode=addition[glow_rg]")
    parts.append("[glow_rg][glow_b]blend=all_mode=addition[near_glow]")

    parts.append("[near_glow]split=2[near_keep][far_src]")
    parts.append(f"[far_src]gblur=sigma={sigma*far_mult:.3f}:sigmaV={sigma*far_mult:.3f}:steps=2[far_glow]")
    parts.append(f"[near_keep][far_glow]blend=all_mode=screen:all_opacity={far_opacity:.4f}[primary_glow]")

    parts.append("[primary_glow]split=2[primary_keep][after_src]")
    parts.append(
        f"[after_src]gblur=sigma={max(.20, after_sigma*max(after_x,.03)):.3f}:"
        f"sigmaV={max(.20, after_sigma*max(after_y,.03)):.3f}:steps=2[after_blur]"
    )
    parts.append(f"[primary_keep][after_blur]blend=all_mode=screen:all_opacity={after_opacity:.4f}[glow_pre_atm]")

    if atmosphere > 0.001:
        noise_amt = 2.0 + atmosphere * 18.0
        parts.append(
            f"[glow_pre_atm]noise=alls={noise_amt:.3f}:allf=t+u,"
            f"gblur=sigma={0.15 + atmosphere*0.60:.3f}:steps=1[glow]"
        )
    else:
        parts.append("[glow_pre_atm]null[glow]")

    hexpr = high_threshold_expr(185.0, 3.2)
    parts.append(
        f"[highlight_source]lutrgb=r='{hexpr}':g='{hexpr}':b='{hexpr}',"
        f"gblur=sigma=0.70:steps=1[highlights]"
    )
    parts.append(f"[base][highlights]blend=all_mode=screen:all_opacity={hi_opacity:.4f}[base_hi]")

    show = p.get('show_mode', 'Result')
    if show == 'Threshold':
        parts.append("[threshold_view]null[outv]")
        parts.append("[base_hi]nullsink")
        parts.append("[glow]nullsink")
    elif show == 'Glow Only':
        parts.append("[glow]null[outv]")
        parts.append("[threshold_view]nullsink")
        parts.append("[base_hi]nullsink")
    else:
        parts.append("[threshold_view]nullsink")
        parts.append(f"[base_hi][glow]blend=all_mode={combine_mode}:all_opacity={glow_mix:.4f}[outv]")
    return ';'.join(parts)


class ScrollFrame(ttk.Frame):
    def __init__(self, master, **kw):
        super().__init__(master, **kw)
        self.canvas = tk.Canvas(self, bg='#191c23', highlightthickness=0, width=380)
        self.bar = ttk.Scrollbar(self, orient='vertical', command=self.canvas.yview)
        self.inner = ttk.Frame(self.canvas, style='Card.TFrame')
        self.window = self.canvas.create_window((0, 0), window=self.inner, anchor='nw')
        self.canvas.configure(yscrollcommand=self.bar.set)
        self.canvas.pack(side='left', fill='both', expand=True)
        self.bar.pack(side='right', fill='y')
        self.inner.bind('<Configure>', lambda e: self.canvas.configure(scrollregion=self.canvas.bbox('all')))
        self.canvas.bind('<Configure>', lambda e: self.canvas.itemconfigure(self.window, width=e.width))
        self.canvas.bind_all('<MouseWheel>', self._wheel)

    def _wheel(self, event):
        try:
            self.canvas.yview_scroll(int(-event.delta / 120), 'units')
        except Exception:
            pass


class App(tk.Tk):
    PRESETS = {
        '例图·Ultra清晰亮透': {
            'exposure': 0.00, 'contrast': 1.55, 'saturation': 1.00, 'temperature': -0.22,
            'tint': 0.03, 'shadows': -0.08, 'highlights': 0.18, 'edge_enhance': 0.72,
            'glow_brightness': 1.30, 'threshold': 0.56, 'glow_width': 0.371,
            'glow_falloff': 0.35, 'glow_bias': 0.0, 'width_x': 1.0, 'width_y': 1.0,
            'width_r': 1.0, 'width_g': 1.08, 'width_b': 1.18,
            'color_r': 1.0, 'color_g': 1.0, 'color_b': 1.0,
            'after_glow': 0.22, 'after_width': 0.808, 'after_stretch_x': 0.30,
            'after_stretch_y': 0.10, 'highlight_pop': 0.12, 'atmosphere': 0.0,
            'glow_mix': 0.72, 'source_opacity': 1.0,
        },
        'S_UltraGlow 默认': {
            'exposure': 0.0, 'contrast': 1.0, 'saturation': 1.0, 'temperature': 0.0,
            'tint': 0.0, 'shadows': 0.0, 'highlights': 0.0, 'edge_enhance': 0.20,
            'glow_brightness': 1.8, 'threshold': 0.4, 'glow_width': 0.371,
            'glow_falloff': 0.35, 'glow_bias': 0.0, 'width_x': 1.0, 'width_y': 1.0,
            'width_r': 1.0, 'width_g': 1.0, 'width_b': 1.0,
            'color_r': 1.0, 'color_g': 1.0, 'color_b': 1.0,
            'after_glow': 0.15, 'after_width': 0.808, 'after_stretch_x': 0.30,
            'after_stretch_y': 0.10, 'highlight_pop': 0.0, 'atmosphere': 0.0,
            'glow_mix': 0.75, 'source_opacity': 1.0,
        },
        '冷调硬朗': {
            'exposure': -0.01, 'contrast': 1.48, 'saturation': 0.92, 'temperature': -0.42,
            'tint': 0.02, 'shadows': -0.10, 'highlights': 0.15, 'edge_enhance': 0.82,
            'glow_brightness': 1.0, 'threshold': 0.62, 'glow_width': 0.26,
            'glow_falloff': 0.05, 'glow_bias': -0.1, 'width_x': 1.0, 'width_y': 1.0,
            'width_r': 0.92, 'width_g': 1.05, 'width_b': 1.22,
            'color_r': 0.96, 'color_g': 1.0, 'color_b': 1.08,
            'after_glow': 0.08, 'after_width': 0.60, 'after_stretch_x': 0.40,
            'after_stretch_y': 0.08, 'highlight_pop': 0.15, 'atmosphere': 0.0,
            'glow_mix': 0.60, 'source_opacity': 1.0,
        },
        '柔光MV': {
            'exposure': 0.03, 'contrast': 1.25, 'saturation': 0.95, 'temperature': 0.08,
            'tint': 0.03, 'shadows': -0.02, 'highlights': 0.12, 'edge_enhance': 0.36,
            'glow_brightness': 1.75, 'threshold': 0.44, 'glow_width': 0.58,
            'glow_falloff': 0.65, 'glow_bias': 0.25, 'width_x': 1.15, 'width_y': 0.90,
            'width_r': 0.95, 'width_g': 1.10, 'width_b': 1.25,
            'color_r': 1.02, 'color_g': 1.0, 'color_b': 1.05,
            'after_glow': 0.40, 'after_width': 1.05, 'after_stretch_x': 0.55,
            'after_stretch_y': 0.18, 'highlight_pop': 0.08, 'atmosphere': 0.08,
            'glow_mix': 0.82, 'source_opacity': 1.0,
        },
    }

    NUMERIC_DEFAULTS = {
        'exposure': 0.0, 'contrast': 1.0, 'saturation': 1.0, 'temperature': 0.0, 'tint': 0.0,
        'shadows': 0.0, 'highlights': 0.0, 'edge_enhance': 0.2,
        'glow_brightness': 1.8, 'threshold': 0.4, 'glow_width': 0.371,
        'glow_falloff': 0.35, 'glow_bias': 0.0,
        'width_x': 1.0, 'width_y': 1.0, 'width_r': 1.0, 'width_g': 1.0, 'width_b': 1.0,
        'color_r': 1.0, 'color_g': 1.0, 'color_b': 1.0,
        'after_glow': 0.15, 'after_width': 0.808, 'after_stretch_x': 0.3, 'after_stretch_y': 0.1,
        'highlight_pop': 0.0, 'atmosphere': 0.0, 'glow_mix': 0.75, 'source_opacity': 1.0,
        'mask_blur': 0.0, 'mask_opacity': 1.0, 'mask_resize': 1.0, 'mask_resize_x': 1.0,
        'mask_resize_y': 1.0, 'mask_shift_x': 0.0, 'mask_shift_y': 0.0,
    }

    def __init__(self):
        super().__init__()
        self.title(f'{APP_NAME}  v{APP_VERSION}')
        self.geometry('1280x820')
        self.minsize(1100, 720)
        self.configure(bg='#111318')

        self.source = tk.StringVar()
        self.background = tk.StringVar()
        self.matte = tk.StringVar()
        self.preset = tk.StringVar(value='例图·Ultra清晰亮透')
        self.combine = tk.StringVar(value='Screen')
        self.show_mode = tk.StringVar(value='Result')
        self.status = tk.StringVar(value='请选择视频。全部处理在本机完成，不使用 AI 修复。')
        self.progress = tk.DoubleVar(value=0.0)
        self.preview_time = tk.DoubleVar(value=1.0)
        self.invert_mask = tk.BooleanVar(value=False)
        self.bypass_mask = tk.BooleanVar(value=False)
        self.show_mask_only = tk.BooleanVar(value=False)

        self.vars = {k: tk.DoubleVar(value=v) for k, v in self.NUMERIC_DEFAULTS.items()}
        self.info = {'width': 1920, 'height': 1080, 'duration': 10.0}
        self.busy = False
        self.q = queue.Queue()
        self.orig_img = None
        self.proc_img = None
        self.after_id = None

        self._style()
        self._ui()
        self.apply_preset()
        self.after(100, self._poll)

    def _style(self):
        s = ttk.Style(self)
        try:
            s.theme_use('clam')
        except tk.TclError:
            pass
        s.configure('TFrame', background='#111318')
        s.configure('Card.TFrame', background='#191c23')
        s.configure('TLabel', background='#111318', foreground='#e9edf4', font=('Microsoft YaHei UI', 10))
        s.configure('Card.TLabel', background='#191c23', foreground='#e9edf4', font=('Microsoft YaHei UI', 10))
        s.configure('Muted.TLabel', background='#111318', foreground='#9da6b5', font=('Microsoft YaHei UI', 9))
        s.configure('MutedCard.TLabel', background='#191c23', foreground='#9da6b5', font=('Microsoft YaHei UI', 9))
        s.configure('Title.TLabel', background='#111318', foreground='white', font=('Microsoft YaHei UI', 19, 'bold'))
        s.configure('TButton', font=('Microsoft YaHei UI', 10), padding=(10, 7))
        s.configure('Accent.TButton', font=('Microsoft YaHei UI', 10, 'bold'), padding=(16, 9))
        s.configure('TNotebook', background='#191c23', borderwidth=0)
        s.configure('TNotebook.Tab', padding=(12, 7), font=('Microsoft YaHei UI', 9))

    def _ui(self):
        root = ttk.Frame(self, padding=14)
        root.pack(fill='both', expand=True)
        head = ttk.Frame(root)
        head.pack(fill='x', pady=(0, 10))
        ttk.Label(head, text='LuminaGrade Ultra', style='Title.TLabel').pack(side='left')
        ttk.Label(head, text='  S_UltraGlow 风格 · 本地确定性调色 · 非 AI', style='Muted.TLabel').pack(side='left', pady=(9, 0))

        inputs = ttk.Frame(root, style='Card.TFrame', padding=10)
        inputs.pack(fill='x', pady=(0, 10))
        self._path_row(inputs, 0, 'Source', self.source, self._choose_source)
        self._path_row(inputs, 1, 'Background（可选）', self.background, lambda: self._choose_optional(self.background, '选择 Background'))
        self._path_row(inputs, 2, 'Matte / Mask（可选）', self.matte, lambda: self._choose_optional(self.matte, '选择 Matte / Mask'))
        inputs.columnconfigure(1, weight=1)
        preset_row = ttk.Frame(inputs, style='Card.TFrame')
        preset_row.grid(row=0, column=3, rowspan=3, sticky='nsew', padx=(14, 0))
        ttk.Label(preset_row, text='Preset', style='Card.TLabel').pack(anchor='w')
        combo = ttk.Combobox(preset_row, textvariable=self.preset, state='readonly', width=18, values=list(self.PRESETS))
        combo.pack(fill='x', pady=(4, 6))
        combo.bind('<<ComboboxSelected>>', lambda e: self.apply_preset())
        br = ttk.Frame(preset_row, style='Card.TFrame')
        br.pack(fill='x')
        ttk.Button(br, text='载入', command=self.load_preset).pack(side='left')
        ttk.Button(br, text='保存', command=self.save_preset).pack(side='left', padx=(6, 0))
        ttk.Button(br, text='刷新预览', command=self.refresh_preview).pack(side='right')

        body = ttk.Frame(root)
        body.pack(fill='both', expand=True)
        body.columnconfigure(0, weight=3)
        body.columnconfigure(1, weight=2)
        body.rowconfigure(0, weight=1)
        preview_card = ttk.Frame(body, style='Card.TFrame', padding=10)
        preview_card.grid(row=0, column=0, sticky='nsew', padx=(0, 10))
        settings_card = ttk.Frame(body, style='Card.TFrame', padding=8)
        settings_card.grid(row=0, column=1, sticky='nsew')
        ph = ttk.Frame(preview_card, style='Card.TFrame')
        ph.pack(fill='x', pady=(0, 7))
        ttk.Label(ph, text='Before / After', style='Card.TLabel', font=('Microsoft YaHei UI', 11, 'bold')).pack(side='left')
        ttk.Label(ph, text='左：原片  ·  右：Ultra处理', style='MutedCard.TLabel').pack(side='left', padx=(10, 0))
        self.canvas = tk.Canvas(preview_card, bg='#090b10', highlightthickness=0)
        self.canvas.pack(fill='both', expand=True)
        self.canvas.bind('<Configure>', lambda e: self._draw_preview())
        tr = ttk.Frame(preview_card, style='Card.TFrame')
        tr.pack(fill='x', pady=(8, 0))
        ttk.Label(tr, text='取样时间', style='Card.TLabel').pack(side='left')
        self.time_scale = ttk.Scale(tr, from_=0, to=10, variable=self.preview_time, command=lambda _v: self._schedule_preview())
        self.time_scale.pack(side='left', fill='x', expand=True, padx=10)
        self.time_label = ttk.Label(tr, text='1.0s', style='Card.TLabel', width=8)
        self.time_label.pack(side='right')

        tabs = ttk.Notebook(settings_card)
        tabs.pack(fill='both', expand=True)
        self.tab_grade = ScrollFrame(tabs, style='Card.TFrame')
        self.tab_glow = ScrollFrame(tabs, style='Card.TFrame')
        self.tab_mask = ScrollFrame(tabs, style='Card.TFrame')
        tabs.add(self.tab_grade, text='调色 / 清晰')
        tabs.add(self.tab_glow, text='UltraGlow')
        tabs.add(self.tab_mask, text='Matte / Mask')
        self._grade_controls(self.tab_grade.inner)
        self._glow_controls(self.tab_glow.inner)
        self._mask_controls(self.tab_mask.inner)

        bottom = ttk.Frame(root, padding=(0, 10, 0, 0))
        bottom.pack(fill='x')
        ttk.Progressbar(bottom, variable=self.progress, maximum=100).pack(side='left', fill='x', expand=True, padx=(0, 10))
        ttk.Label(bottom, textvariable=self.status, style='Muted.TLabel').pack(side='left')
        ttk.Button(bottom, text='导出 MP4', style='Accent.TButton', command=self.export).pack(side='right', padx=(12, 0))

    def _path_row(self, parent, row, label, var, command):
        ttk.Label(parent, text=label, style='Card.TLabel', width=18).grid(row=row, column=0, sticky='w', pady=3)
        ttk.Entry(parent, textvariable=var).grid(row=row, column=1, sticky='ew', padx=(0, 7), pady=3)
        ttk.Button(parent, text='选择', command=command).grid(row=row, column=2, pady=3)

    def _section(self, parent, text, hint=''):
        ttk.Label(parent, text=text, style='Card.TLabel', font=('Microsoft YaHei UI', 12, 'bold')).pack(anchor='w', pady=(10, 2), padx=8)
        if hint:
            ttk.Label(parent, text=hint, style='MutedCard.TLabel', wraplength=330).pack(anchor='w', padx=8, pady=(0, 5))

    def _slider(self, parent, label, key, lo, hi, decimals=2, hint=''):
        box = ttk.Frame(parent, style='Card.TFrame')
        box.pack(fill='x', padx=8, pady=4)
        row = ttk.Frame(box, style='Card.TFrame')
        row.pack(fill='x')
        ttk.Label(row, text=label, style='Card.TLabel').pack(side='left')
        val = ttk.Label(row, text='', style='Card.TLabel', width=8)
        val.pack(side='right')
        fmt = f'{{:.{decimals}f}}'
        def changed(_=None):
            val.config(text=fmt.format(self.vars[key].get()))
            self._schedule_preview()
        ttk.Scale(box, from_=lo, to=hi, variable=self.vars[key], command=changed).pack(fill='x')
        if hint:
            ttk.Label(box, text=hint, style='MutedCard.TLabel', wraplength=330).pack(anchor='w')
        self.vars[key].trace_add('write', lambda *_a: val.config(text=fmt.format(self.vars[key].get())))
        val.config(text=fmt.format(self.vars[key].get()))

    def _grade_controls(self, parent):
        self._section(parent, 'Base Grade', '例图的关键不是整体提亮，而是压黑位 + 抬高光 + 保持中间调。')
        self._slider(parent, 'Exposure', 'exposure', -0.35, 0.35)
        self._slider(parent, 'Contrast / S Curve', 'contrast', 0.75, 1.90)
        self._slider(parent, 'Saturation', 'saturation', 0.45, 1.60)
        self._slider(parent, 'Temperature', 'temperature', -1.0, 1.0)
        self._slider(parent, 'Tint', 'tint', -1.0, 1.0)
        self._slider(parent, 'Shadows', 'shadows', -0.30, 0.30)
        self._slider(parent, 'Highlights', 'highlights', -0.25, 0.35)
        self._section(parent, 'Detail', '使用 FFmpeg CAS（对比度自适应锐化），比普通 Unsharp 更不容易出白边。')
        self._slider(parent, 'Edge Enhance / CAS', 'edge_enhance', 0.0, 1.0)

    def _glow_controls(self, parent):
        self._section(parent, 'S_UltraGlow Core', '默认值参考 Sapphire UltraGlow；算法为开源/自写近似，不包含 Sapphire 代码。')
        self._slider(parent, 'Brightness', 'glow_brightness', 0.0, 4.0)
        self._slider(parent, 'Threshold', 'threshold', 0.0, 1.0)
        self._slider(parent, 'Glow Width', 'glow_width', 0.0, 1.5, 3)
        self._slider(parent, 'Glow Falloff', 'glow_falloff', -2.0, 2.0)
        self._slider(parent, 'Glow Bias', 'glow_bias', -3.0, 3.0)
        self._slider(parent, 'Glow Mix', 'glow_mix', 0.0, 1.0)
        self._section(parent, 'Width XY / RGB', 'RGB 宽度不同会产生很轻的彩色扩散边缘；例图预设让蓝色扩散略宽。')
        self._slider(parent, 'Width X', 'width_x', 0.0, 3.0)
        self._slider(parent, 'Width Y', 'width_y', 0.0, 3.0)
        self._slider(parent, 'Width Red', 'width_r', 0.0, 3.0)
        self._slider(parent, 'Width Green', 'width_g', 0.0, 3.0)
        self._slider(parent, 'Width Blue', 'width_b', 0.0, 3.0)
        self._section(parent, 'Glow Color')
        self._slider(parent, 'Color R', 'color_r', 0.0, 2.0)
        self._slider(parent, 'Color G', 'color_g', 0.0, 2.0)
        self._slider(parent, 'Color B', 'color_b', 0.0, 2.0)
        self._section(parent, 'After Glow', '主光晕完成后再做一次更宽、更扁的二次扩散。')
        self._slider(parent, 'After Glow Amount', 'after_glow', 0.0, 1.5)
        self._slider(parent, 'After Glow Width', 'after_width', 0.0, 2.0, 3)
        self._slider(parent, 'After Stretch X', 'after_stretch_x', 0.0, 3.0)
        self._slider(parent, 'After Stretch Y', 'after_stretch_y', 0.0, 3.0)
        self._section(parent, 'Advanced')
        self._slider(parent, 'Highlights', 'highlight_pop', 0.0, 1.5, hint='只增强最亮的小面积高光，不抬整个画面。')
        self._slider(parent, 'Atmospheric Noise', 'atmosphere', 0.0, 1.0, hint='只作用于 Glow 层，默认关闭。')
        line = ttk.Frame(parent, style='Card.TFrame')
        line.pack(fill='x', padx=8, pady=6)
        ttk.Label(line, text='Combine', style='Card.TLabel').pack(side='left')
        cb = ttk.Combobox(line, textvariable=self.combine, state='readonly', values=['Screen', 'Add', 'Overlay'], width=11)
        cb.pack(side='right')
        cb.bind('<<ComboboxSelected>>', lambda e: self._schedule_preview(80))
        line2 = ttk.Frame(parent, style='Card.TFrame')
        line2.pack(fill='x', padx=8, pady=6)
        ttk.Label(line2, text='Show', style='Card.TLabel').pack(side='left')
        sh = ttk.Combobox(line2, textvariable=self.show_mode, state='readonly', values=['Result', 'Threshold', 'Glow Only'], width=11)
        sh.pack(side='right')
        sh.bind('<<ComboboxSelected>>', lambda e: self._schedule_preview(80))
        self._slider(parent, 'Source Opacity', 'source_opacity', 0.0, 1.0, hint='仅在提供 Background 时有意义。')

    def _mask_controls(self, parent):
        self._section(parent, 'Matte / Mask Controls', '这是导入 Matte 的本地控制，不伪装成 Boris Mocha 跟踪器。可用 AE/Mocha 导出的黑白遮罩视频或图片。')
        self._slider(parent, 'Blur Mask', 'mask_blur', 0.0, 60.0, 1)
        self._slider(parent, 'Mask Opacity', 'mask_opacity', 0.0, 1.0)
        self._slider(parent, 'Resize Mask', 'mask_resize', 0.10, 2.00)
        self._slider(parent, 'Resize Rel X', 'mask_resize_x', 0.10, 2.00)
        self._slider(parent, 'Resize Rel Y', 'mask_resize_y', 0.10, 2.00)
        self._slider(parent, 'Shift X (px)', 'mask_shift_x', -500.0, 500.0, 0)
        self._slider(parent, 'Shift Y (px)', 'mask_shift_y', -500.0, 500.0, 0)
        for text, var in [('Invert Mask', self.invert_mask), ('Bypass Mask', self.bypass_mask), ('Show Mask Only', self.show_mask_only)]:
            ttk.Checkbutton(parent, text=text, variable=var, command=lambda: self._schedule_preview(80)).pack(anchor='w', padx=8, pady=4)
        ttk.Label(parent, text='Matte 会在发光产生前乘到 Source 上，因此不会把生成后的光晕硬裁掉。', style='MutedCard.TLabel', wraplength=330).pack(anchor='w', padx=8, pady=8)

    def _choose_source(self):
        path = filedialog.askopenfilename(title='选择 Source 视频', filetypes=[('视频文件', '*.mp4 *.mov *.mkv *.avi *.m4v *.webm'), ('所有文件', '*.*')])
        if not path:
            return
        self.source.set(path)
        try:
            self.info = video_info(path)
        except Exception as e:
            self.info = {'width': 1920, 'height': 1080, 'duration': 10.0}
            self.status.set(f'读取视频信息失败，将使用默认参数：{e}')
        end = max(0.1, self.info['duration'])
        self.time_scale.configure(to=end)
        self.preview_time.set(min(max(0.5, end * 0.18), max(0.1, end - 0.1)))
        self.status.set(f"已加载 {Path(path).name} · {self.info['width']}×{self.info['height']} · {self.info['duration']:.1f}s")
        self.refresh_preview()

    def _choose_optional(self, var, title):
        path = filedialog.askopenfilename(title=title, filetypes=[('媒体文件', '*.mp4 *.mov *.mkv *.avi *.m4v *.webm *.png *.jpg *.jpeg *.bmp *.webp'), ('所有文件', '*.*')])
        if path:
            var.set(path)
            self._schedule_preview(80)

    def collect_params(self):
        p = {k: v.get() for k, v in self.vars.items()}
        p.update({'combine': self.combine.get(), 'show_mode': self.show_mode.get(), 'invert_mask': bool(self.invert_mask.get()), 'bypass_mask': bool(self.bypass_mask.get()), 'show_mask_only': bool(self.show_mask_only.get())})
        return p

    def apply_preset(self):
        data = self.PRESETS.get(self.preset.get())
        if not data:
            return
        for k, val in data.items():
            if k in self.vars:
                self.vars[k].set(val)
        self.combine.set('Screen')
        self.show_mode.set('Result')
        self._schedule_preview(80)

    def save_preset(self):
        path = filedialog.asksaveasfilename(title='保存 LuminaGrade Preset', defaultextension='.json', filetypes=[('JSON Preset', '*.json')])
        if not path:
            return
        data = {'version': APP_VERSION, 'params': self.collect_params()}
        Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding='utf-8')
        self.status.set(f'Preset 已保存：{Path(path).name}')

    def load_preset(self):
        path = filedialog.askopenfilename(title='载入 LuminaGrade Preset', filetypes=[('JSON Preset', '*.json'), ('所有文件', '*.*')])
        if not path:
            return
        try:
            data = json.loads(Path(path).read_text(encoding='utf-8'))
            params = data.get('params', data)
            for k, v in params.items():
                if k in self.vars:
                    self.vars[k].set(float(v))
            if params.get('combine') in ('Screen', 'Add', 'Overlay'):
                self.combine.set(params['combine'])
            if params.get('show_mode') in ('Result', 'Threshold', 'Glow Only'):
                self.show_mode.set(params['show_mode'])
            for k, var in [('invert_mask', self.invert_mask), ('bypass_mask', self.bypass_mask), ('show_mask_only', self.show_mask_only)]:
                if k in params:
                    var.set(bool(params[k]))
            self._schedule_preview(80)
        except Exception as e:
            messagebox.showerror(APP_NAME, f'Preset 读取失败：\n{e}')

    def _input_args(self, preview_time=None):
        args = []
        indices = {'src': 0, 'bg': None, 'matte': None}
        files = [('src', self.source.get()), ('bg', self.background.get()), ('matte', self.matte.get())]
        idx = 0
        for key, path in files:
            if not path:
                continue
            if key != 'src' and not os.path.exists(path):
                continue
            ext = Path(path).suffix.lower()
            if ext in IMAGE_EXTS:
                args += ['-loop', '1', '-i', path]
            else:
                if preview_time is not None:
                    args += ['-ss', f'{preview_time:.3f}']
                args += ['-i', path]
            indices[key] = idx
            idx += 1
        return args, indices

    def _schedule_preview(self, delay=300):
        self.time_label.config(text=f'{self.preview_time.get():.1f}s')
        if not self.source.get() or self.busy:
            return
        if self.after_id:
            try:
                self.after_cancel(self.after_id)
            except Exception:
                pass
        self.after_id = self.after(delay, self.refresh_preview)

    def refresh_preview(self):
        src = self.source.get()
        if self.busy or not src or not os.path.exists(src):
            return
        self.busy = True
        self.status.set('正在生成 UltraGlow 本地预览…')
        p = self.collect_params()
        t = float(self.preview_time.get())
        threading.Thread(target=self._preview_worker, args=(p, t), daemon=True).start()

    def _preview_worker(self, p, t):
        d = tempfile.mkdtemp(prefix='luminagrade_ultra_')
        try:
            ff = bundled_tool('ffmpeg')
            before = os.path.join(d, 'before.png')
            after = os.path.join(d, 'after.png')
            run_checked([ff, '-hide_banner', '-loglevel', 'error', '-y', '-ss', f'{t:.3f}', '-i', self.source.get(), '-frames:v', '1', before])
            in_args, idx = self._input_args(preview_time=t)
            graph = build_filter_graph(p, self.info['width'], self.info['height'], idx['src'], idx['bg'], idx['matte'])
            cmd = [ff, '-hide_banner', '-loglevel', 'error', '-y'] + in_args + ['-filter_complex', graph, '-map', '[outv]', '-frames:v', '1', after]
            run_checked(cmd)
            a = Image.open(before).convert('RGB').copy()
            b = Image.open(after).convert('RGB').copy()
            self.q.put(('preview', a, b))
        except Exception as e:
            self.q.put(('error', f'预览失败：\n{e}'))
        finally:
            shutil.rmtree(d, ignore_errors=True)

    def _draw_preview(self):
        self.canvas.delete('all')
        w = max(10, self.canvas.winfo_width())
        h = max(10, self.canvas.winfo_height())
        if self.orig_img is None:
            self.canvas.create_text(w/2, h/2, text='选择 Source 后显示 Before / After', fill='#7f8898', font=('Microsoft YaHei UI', 13))
            return
        cw = max(1, (w - 8)//2)
        def fit(im):
            x = im.copy()
            x.thumbnail((cw, h), Image.Resampling.LANCZOS)
            return x
        self.tk_before = ImageTk.PhotoImage(fit(self.orig_img))
        self.tk_after = ImageTk.PhotoImage(fit(self.proc_img))
        self.canvas.create_image(cw//2, h//2, image=self.tk_before)
        self.canvas.create_image(cw + 8 + cw//2, h//2, image=self.tk_after)
        self.canvas.create_line(cw + 4, 0, cw + 4, h, fill='#343947')

    def export(self):
        src = self.source.get()
        if self.busy:
            return
        if not src or not os.path.exists(src):
            messagebox.showinfo(APP_NAME, '请先选择 Source 视频。')
            return
        out = filedialog.asksaveasfilename(title='导出 MP4', defaultextension='.mp4', initialfile=Path(src).stem + '_LuminaGradeUltra.mp4', filetypes=[('MP4 视频', '*.mp4')])
        if not out:
            return
        self.busy = True
        self.progress.set(0)
        self.status.set('正在导出 UltraGlow…')
        threading.Thread(target=self._export_worker, args=(self.collect_params(), out), daemon=True).start()

    def _export_worker(self, p, out):
        try:
            ff = bundled_tool('ffmpeg')
            in_args, idx = self._input_args(preview_time=None)
            graph = build_filter_graph(p, self.info['width'], self.info['height'], idx['src'], idx['bg'], idx['matte'])
            flags = subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0
            cmd = [ff, '-hide_banner', '-y'] + in_args + ['-filter_complex', graph, '-map', '[outv]', '-map', f"{idx['src']}:a?", '-c:v', 'libx264', '-preset', 'medium', '-crf', '15', '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-b:a', '192k', '-movflags', '+faststart', '-t', f"{self.info['duration']:.6f}", '-progress', 'pipe:1', '-nostats', out]
            proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding='utf-8', errors='ignore', creationflags=flags)
            dur = max(0.01, self.info['duration'])
            for line in proc.stdout or []:
                if line.startswith('out_time_ms='):
                    try:
                        pct = clamp(float(line.split('=', 1)[1]) / 1e6 / dur * 100.0, 0.0, 99.5)
                        self.q.put(('progress', pct))
                    except Exception:
                        pass
            err = proc.stderr.read() if proc.stderr else ''
            code = proc.wait()
            if code:
                raise RuntimeError(err[-3500:] or f'FFmpeg 退出码 {code}')
            self.q.put(('done', out))
        except Exception as e:
            self.q.put(('error', f'导出失败：\n{e}'))

    def _poll(self):
        try:
            while True:
                msg = self.q.get_nowait()
                kind = msg[0]
                if kind == 'preview':
                    self.orig_img, self.proc_img = msg[1], msg[2]
                    self.busy = False
                    self.status.set('预览已刷新。')
                    self._draw_preview()
                elif kind == 'progress':
                    self.progress.set(msg[1])
                    self.status.set(f'正在导出… {msg[1]:.0f}%')
                elif kind == 'done':
                    self.busy = False
                    self.progress.set(100)
                    self.status.set('导出完成。')
                    messagebox.showinfo(APP_NAME, f'导出完成：\n{msg[1]}')
                elif kind == 'error':
                    self.busy = False
                    self.status.set('处理失败。')
                    messagebox.showerror(APP_NAME, msg[1])
        except queue.Empty:
            pass
        self.after(100, self._poll)


if __name__ == '__main__':
    App().mainloop()
