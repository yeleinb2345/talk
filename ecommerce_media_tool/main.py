from __future__ import annotations

import csv
import hashlib
import json
import os
import queue
import re
import shutil
import threading
import urllib.error
import urllib.request
from dataclasses import dataclass, asdict
from pathlib import Path
import tkinter as tk
from tkinter import filedialog, messagebox, ttk

from PIL import Image, ImageOps, ImageTk, UnidentifiedImageError

APP_NAME = "商家素材管家"
VERSION = "1.1.0"
IMAGE_EXTS = {".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tif", ".tiff"}
VIDEO_EXTS = {".mp4", ".mov", ".mkv", ".avi", ".webm"}
ALL_EXTS = IMAGE_EXTS | VIDEO_EXTS
SETTINGS_PATH = Path.home() / ".ecommerce_media_tool.json"


@dataclass
class MediaItem:
    path: str
    name: str
    ext: str
    size_mb: float
    width: int = 0
    height: int = 0
    sku: str = ""
    duplicate: bool = False
    duplicate_group: str = ""


def safe_name(text: str) -> str:
    text = re.sub(r'[\\/:*?"<>|]+', "_", text.strip())
    text = re.sub(r"\s+", "_", text)
    return text[:120] or "item"


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    i = 1
    while True:
        candidate = path.with_name(f"{path.stem}_{i}{path.suffix}")
        if not candidate.exists():
            return candidate
        i += 1


def sha256_file(path: Path, chunk_size: int = 1024 * 1024) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        while chunk := f.read(chunk_size):
            h.update(chunk)
    return h.hexdigest()


def guess_sku(filename: str) -> str:
    stem = Path(filename).stem
    patterns = [
        r"(?i)sku[-_ ]*([a-z0-9][a-z0-9_-]{2,})",
        r"(?i)([a-z]{1,5}\d{3,}[a-z0-9_-]*)",
        r"(?i)(\d{5,}[a-z0-9_-]*)",
    ]
    for pat in patterns:
        m = re.search(pat, stem)
        if m:
            return m.group(1).strip("_-").upper()
    return ""


class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title(f"{APP_NAME} v{VERSION}")
        self.geometry("1260x800")
        self.minsize(1020, 680)
        self.items: list[MediaItem] = []
        self.root_dir: Path | None = None
        self.worker_q: queue.Queue = queue.Queue()
        self._preview_photo = None
        self._cancel = threading.Event()
        self.settings = self._load_settings()
        self._build_ui()
        self.after(120, self._poll_queue)
        self.protocol("WM_DELETE_WINDOW", self._on_close)

    def _load_settings(self) -> dict:
        try:
            data = json.loads(SETTINGS_PATH.read_text("utf-8"))
            return data if isinstance(data, dict) else {}
        except Exception:
            return {}

    def _save_settings(self):
        data = {
            "max_edge": int(self.max_edge.get()),
            "out_format": self.out_format.get(),
            "quality": int(self.quality.get()),
            "keep_structure": bool(self.keep_structure.get()),
            "prefix": self.prefix_var.get(),
            "ai_model": self.ai_model.get(),
        }
        try:
            SETTINGS_PATH.write_text(json.dumps(data, ensure_ascii=False, indent=2), "utf-8")
        except OSError:
            pass

    def _on_close(self):
        self._save_settings()
        self.destroy()

    def _build_ui(self):
        style = ttk.Style(self)
        try:
            style.theme_use("vista")
        except tk.TclError:
            pass

        top = ttk.Frame(self, padding=12)
        top.pack(fill="x")
        ttk.Label(top, text=APP_NAME, font=("Microsoft YaHei UI", 18, "bold")).pack(side="left")
        ttk.Label(top, text="本地素材整理 · 批处理 · 去重 · SKU归档", foreground="#666").pack(side="left", padx=10)
        ttk.Button(top, text="重新扫描", command=self.scan_folder).pack(side="right", padx=(8, 0))
        ttk.Button(top, text="选择素材文件夹", command=self.choose_folder).pack(side="right")

        body = ttk.Panedwindow(self, orient="horizontal")
        body.pack(fill="both", expand=True, padx=12, pady=(0, 8))
        left = ttk.Frame(body, padding=8)
        right = ttk.Frame(body, padding=8)
        body.add(left, weight=3)
        body.add(right, weight=2)

        filter_bar = ttk.Frame(left)
        filter_bar.pack(fill="x", pady=(0, 8))
        self.search_var = tk.StringVar()
        ttk.Label(filter_bar, text="筛选").pack(side="left")
        ent = ttk.Entry(filter_bar, textvariable=self.search_var)
        ent.pack(side="left", fill="x", expand=True, padx=8)
        ent.bind("<KeyRelease>", lambda _e: self.refresh_table())
        self.stats_var = tk.StringVar(value="未扫描")
        ttk.Label(filter_bar, textvariable=self.stats_var).pack(side="right")

        table_wrap = ttk.Frame(left)
        table_wrap.pack(fill="both", expand=True)
        cols = ("name", "size", "dims", "sku", "dup")
        self.tree = ttk.Treeview(table_wrap, columns=cols, show="headings", selectmode="extended")
        headings = {"name": "文件名", "size": "大小(MB)", "dims": "尺寸", "sku": "SKU", "dup": "重复"}
        widths = {"name": 350, "size": 86, "dims": 100, "sku": 130, "dup": 65}
        for c in cols:
            self.tree.heading(c, text=headings[c])
            self.tree.column(c, width=widths[c], anchor="w")
        yscroll = ttk.Scrollbar(table_wrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=yscroll.set)
        self.tree.pack(side="left", fill="both", expand=True)
        yscroll.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", lambda _e: self._update_preview())
        self.tree.bind("<Double-1>", lambda _e: self._open_selected_folder())

        preview_box = ttk.LabelFrame(left, text="预览 / 路径", padding=8)
        preview_box.pack(fill="x", pady=(8, 0))
        self.preview_label = ttk.Label(preview_box, text="选择一张图片可预览", anchor="center")
        self.preview_label.pack(side="left", ipadx=6)
        self.path_var = tk.StringVar(value="")
        ttk.Label(preview_box, textvariable=self.path_var, wraplength=650).pack(side="left", fill="x", expand=True, padx=10)

        ttk.Label(right, text="批处理", font=("Microsoft YaHei UI", 13, "bold")).pack(anchor="w")

        group = ttk.LabelFrame(right, text="图片输出", padding=10)
        group.pack(fill="x", pady=7)
        row1 = ttk.Frame(group); row1.pack(fill="x", pady=3)
        ttk.Label(row1, text="最长边").pack(side="left")
        self.max_edge = tk.IntVar(value=int(self.settings.get("max_edge", 1600)))
        ttk.Spinbox(row1, from_=200, to=8000, increment=100, textvariable=self.max_edge, width=8).pack(side="left", padx=6)
        ttk.Label(row1, text="格式").pack(side="left", padx=(12, 0))
        self.out_format = tk.StringVar(value=self.settings.get("out_format", "WEBP"))
        ttk.Combobox(row1, textvariable=self.out_format, values=["WEBP", "JPG", "PNG"], state="readonly", width=8).pack(side="left", padx=6)
        ttk.Label(row1, text="质量").pack(side="left", padx=(12, 0))
        self.quality = tk.IntVar(value=int(self.settings.get("quality", 88)))
        ttk.Spinbox(row1, from_=40, to=100, textvariable=self.quality, width=6).pack(side="left", padx=6)
        self.keep_structure = tk.BooleanVar(value=bool(self.settings.get("keep_structure", True)))
        ttk.Checkbutton(group, text="保留原文件夹结构", variable=self.keep_structure).pack(anchor="w", pady=4)
        ttk.Button(group, text="批量压缩 / 改尺寸 / 转格式", command=self.batch_convert).pack(fill="x", pady=4)

        rename_box = ttk.LabelFrame(right, text="批量改名", padding=10)
        rename_box.pack(fill="x", pady=7)
        self.prefix_var = tk.StringVar(value=self.settings.get("prefix", "SKU"))
        ttk.Entry(rename_box, textvariable=self.prefix_var).pack(fill="x", pady=(0, 6))
        row = ttk.Frame(rename_box); row.pack(fill="x")
        ttk.Button(row, text="预览改名", command=self.preview_rename).pack(side="left", fill="x", expand=True, padx=(0, 3))
        ttk.Button(row, text="执行改名", command=self.batch_rename).pack(side="left", fill="x", expand=True, padx=(3, 0))

        organize = ttk.LabelFrame(right, text="整理与清单", padding=10)
        organize.pack(fill="x", pady=7)
        ttk.Button(organize, text="检测精确重复文件", command=self.find_duplicates).pack(fill="x", pady=3)
        ttk.Button(organize, text="复制重复文件到隔离目录", command=self.copy_duplicates).pack(fill="x", pady=3)
        ttk.Button(organize, text="按 SKU 自动归档", command=self.organize_by_sku).pack(fill="x", pady=3)
        ttk.Button(organize, text="导出 CSV 素材清单", command=self.export_csv).pack(fill="x", pady=3)

        ai = ttk.LabelFrame(right, text="OpenAI 智能命名（可选）", padding=10)
        ai.pack(fill="x", pady=7)
        self.api_key = tk.StringVar(value=os.getenv("OPENAI_API_KEY", ""))
        ttk.Entry(ai, textvariable=self.api_key, show="•").pack(fill="x", pady=(0, 6))
        self.ai_model = tk.StringVar(value=self.settings.get("ai_model", "gpt-5.6-luna"))
        ttk.Entry(ai, textvariable=self.ai_model).pack(fill="x", pady=(0, 6))
        ttk.Label(ai, text="Key 不写入本地配置；只把所选文件名发送给 API。", foreground="#666").pack(anchor="w")
        ttk.Button(ai, text="为选中文件生成命名建议", command=self.ai_suggest_names).pack(fill="x", pady=(6, 0))
        self.ai_result = tk.Text(ai, height=5, wrap="word")
        self.ai_result.pack(fill="x", pady=(6, 0))

        log_box = ttk.LabelFrame(right, text="任务日志", padding=6)
        log_box.pack(fill="both", expand=True, pady=7)
        self.log_text = tk.Text(log_box, height=7, wrap="word", state="disabled")
        self.log_text.pack(fill="both", expand=True)

        bottom = ttk.Frame(self, padding=(12, 0, 12, 10))
        bottom.pack(fill="x")
        self.progress = ttk.Progressbar(bottom, mode="determinate")
        self.progress.pack(side="left", fill="x", expand=True)
        self.status_var = tk.StringVar(value="就绪 · 默认离线")
        ttk.Label(bottom, textvariable=self.status_var).pack(side="right", padx=(10, 0))

    def _log(self, text: str):
        self.log_text.configure(state="normal")
        self.log_text.insert("end", text.rstrip() + "\n")
        self.log_text.see("end")
        self.log_text.configure(state="disabled")

    def choose_folder(self):
        folder = filedialog.askdirectory(title="选择电商素材文件夹")
        if folder:
            self.root_dir = Path(folder)
            self.scan_folder()

    def scan_folder(self):
        if not self.root_dir:
            return
        self.status_var.set("扫描中…")
        self.progress.configure(value=0, maximum=100)
        threading.Thread(target=self._scan_worker, daemon=True).start()

    def _scan_worker(self):
        try:
            paths = [p for p in self.root_dir.rglob("*") if p.is_file() and p.suffix.lower() in ALL_EXTS]
        except OSError as e:
            self.worker_q.put(("error", f"扫描失败：{e}")); return
        items: list[MediaItem] = []
        total = max(1, len(paths))
        for i, p in enumerate(paths, 1):
            width = height = 0
            if p.suffix.lower() in IMAGE_EXTS:
                try:
                    with Image.open(p) as im:
                        width, height = im.size
                except Exception:
                    pass
            try:
                size_mb = p.stat().st_size / 1024 / 1024
            except OSError:
                size_mb = 0
            items.append(MediaItem(str(p), p.name, p.suffix.lower(), round(size_mb, 2), width, height, guess_sku(p.name)))
            if i % 20 == 0 or i == len(paths):
                self.worker_q.put(("progress", i / total * 100, f"扫描 {i}/{len(paths)}"))
        self.worker_q.put(("scan_done", items))

    def refresh_table(self):
        q = self.search_var.get().strip().lower()
        self.tree.delete(*self.tree.get_children())
        shown = 0
        for idx, item in enumerate(self.items):
            hay = f"{item.name} {item.sku}".lower()
            if q and q not in hay:
                continue
            dims = f"{item.width}×{item.height}" if item.width else "-"
            self.tree.insert("", "end", iid=str(idx), values=(item.name, f"{item.size_mb:.2f}", dims, item.sku or "-", "是" if item.duplicate else "否"))
            shown += 1
        dup_count = sum(1 for x in self.items if x.duplicate)
        total_mb = sum(x.size_mb for x in self.items)
        self.stats_var.set(f"{shown}/{len(self.items)} · {total_mb:.1f} MB · 重复 {dup_count}")

    def _selected_items(self) -> list[MediaItem]:
        ids = self.tree.selection()
        if not ids:
            return self.items.copy()
        return [self.items[int(i)] for i in ids]

    def _update_preview(self):
        ids = self.tree.selection()
        if not ids:
            return
        item = self.items[int(ids[0])]
        self.path_var.set(item.path)
        self.preview_label.configure(image="", text="无预览")
        self._preview_photo = None
        if item.ext not in IMAGE_EXTS:
            return
        try:
            with Image.open(item.path) as im:
                im = ImageOps.exif_transpose(im).convert("RGB")
                im.thumbnail((120, 90), Image.Resampling.LANCZOS)
                self._preview_photo = ImageTk.PhotoImage(im.copy())
            self.preview_label.configure(image=self._preview_photo, text="")
        except Exception:
            pass

    def _open_selected_folder(self):
        ids = self.tree.selection()
        if not ids:
            return
        path = Path(self.items[int(ids[0])].path)
        try:
            os.startfile(str(path.parent))
        except Exception:
            pass

    def batch_convert(self):
        targets = [x for x in self._selected_items() if x.ext in IMAGE_EXTS]
        if not targets:
            messagebox.showinfo(APP_NAME, "没有可处理的图片。")
            return
        out = filedialog.askdirectory(title="选择输出文件夹")
        if not out:
            return
        self._save_settings()
        threading.Thread(target=self._convert_worker, args=(targets, Path(out)), daemon=True).start()

    def _convert_worker(self, targets: list[MediaItem], out_dir: Path):
        fmt = self.out_format.get().upper()
        ext_map = {"WEBP": ".webp", "JPG": ".jpg", "PNG": ".png"}
        max_edge = max(200, int(self.max_edge.get()))
        quality = min(100, max(40, int(self.quality.get())))
        total = len(targets)
        success = failed = 0
        for i, item in enumerate(targets, 1):
            src = Path(item.path)
            try:
                rel_parent = src.parent.relative_to(self.root_dir) if self.keep_structure.get() and self.root_dir else Path()
            except ValueError:
                rel_parent = Path()
            dst_dir = out_dir / rel_parent
            dst_dir.mkdir(parents=True, exist_ok=True)
            dst = unique_path(dst_dir / (src.stem + ext_map[fmt]))
            try:
                with Image.open(src) as im:
                    im = ImageOps.exif_transpose(im)
                    im.thumbnail((max_edge, max_edge), Image.Resampling.LANCZOS)
                    if fmt == "JPG":
                        if im.mode in ("RGBA", "LA", "P"):
                            rgba = im.convert("RGBA")
                            bg = Image.new("RGB", rgba.size, "white")
                            bg.paste(rgba, mask=rgba.getchannel("A"))
                            im = bg
                        else:
                            im = im.convert("RGB")
                        im.save(dst, "JPEG", quality=quality, optimize=True)
                    elif fmt == "WEBP":
                        im.save(dst, "WEBP", quality=quality, method=6)
                    else:
                        im.save(dst, "PNG", optimize=True)
                success += 1
            except (OSError, UnidentifiedImageError) as e:
                failed += 1
                self.worker_q.put(("log", f"跳过 {src.name}: {e}"))
            self.worker_q.put(("progress", i / total * 100, f"处理 {i}/{total}"))
        self.worker_q.put(("done", f"图片处理完成：成功 {success}，失败 {failed}。输出：{out_dir}"))

    def preview_rename(self):
        targets = self._selected_items()
        if not targets:
            return
        prefix = safe_name(self.prefix_var.get())
        lines = []
        for i, item in enumerate(targets[:50], 1):
            lines.append(f"{item.name}  →  {prefix}_{i:04d}{Path(item.path).suffix.lower()}")
        if len(targets) > 50:
            lines.append(f"…另有 {len(targets)-50} 个文件")
        messagebox.showinfo("改名预览", "\n".join(lines))

    def batch_rename(self):
        targets = self._selected_items()
        if not targets:
            return
        prefix = safe_name(self.prefix_var.get())
        if not messagebox.askyesno(APP_NAME, f"将重命名 {len(targets)} 个文件。建议先点‘预览改名’。继续？"):
            return
        success = 0
        for i, item in enumerate(targets, 1):
            src = Path(item.path)
            dst = unique_path(src.with_name(f"{prefix}_{i:04d}{src.suffix.lower()}"))
            try:
                src.rename(dst)
                item.path = str(dst); item.name = dst.name; item.sku = guess_sku(dst.name)
                success += 1
            except OSError as e:
                self._log(f"重命名失败：{src.name} - {e}")
        self.refresh_table()
        self.status_var.set(f"批量改名完成：{success}/{len(targets)}")

    def find_duplicates(self):
        if not self.items:
            return
        threading.Thread(target=self._dupe_worker, daemon=True).start()

    def _dupe_worker(self):
        size_groups: dict[int, list[MediaItem]] = {}
        for item in self.items:
            try:
                size_groups.setdefault(Path(item.path).stat().st_size, []).append(item)
            except OSError:
                pass
        candidates = [group for group in size_groups.values() if len(group) > 1]
        hashes: dict[str, list[MediaItem]] = {}
        flat = [x for g in candidates for x in g]
        total = max(1, len(flat))
        for i, item in enumerate(flat, 1):
            try:
                h = sha256_file(Path(item.path))
                hashes.setdefault(h, []).append(item)
            except OSError:
                pass
            self.worker_q.put(("progress", i / total * 100, f"去重 {i}/{len(flat)}"))
        for item in self.items:
            item.duplicate = False; item.duplicate_group = ""
        group_no = 0
        for digest, group in hashes.items():
            if len(group) > 1:
                group_no += 1
                gid = digest[:10]
                for pos, item in enumerate(group):
                    item.duplicate_group = gid
                    if pos > 0:
                        item.duplicate = True
        self.worker_q.put(("refresh",))
        self.worker_q.put(("done", f"检测完成：{group_no} 组重复，{sum(1 for x in self.items if x.duplicate)} 个冗余文件。"))

    def copy_duplicates(self):
        targets = [x for x in self.items if x.duplicate]
        if not targets:
            messagebox.showinfo(APP_NAME, "请先检测重复文件。")
            return
        out = filedialog.askdirectory(title="选择重复文件隔离目录")
        if not out:
            return
        out_dir = Path(out)
        copied = 0
        for item in targets:
            src = Path(item.path)
            try:
                dst = unique_path(out_dir / safe_name(item.duplicate_group or "重复") / src.name)
                dst.parent.mkdir(parents=True, exist_ok=True)
                shutil.copy2(src, dst); copied += 1
            except OSError as e:
                self._log(f"隔离复制失败：{src.name} - {e}")
        messagebox.showinfo(APP_NAME, f"已复制 {copied} 个重复文件到隔离目录；原文件未删除。")

    def organize_by_sku(self):
        targets = self._selected_items()
        if not targets:
            return
        out = filedialog.askdirectory(title="选择 SKU 归档输出目录")
        if not out:
            return
        out_dir = Path(out)
        copied = 0
        for item in targets:
            sku = item.sku or "未识别SKU"
            sku_dir = out_dir / safe_name(sku)
            sku_dir.mkdir(parents=True, exist_ok=True)
            src = Path(item.path)
            dst = unique_path(sku_dir / src.name)
            try:
                shutil.copy2(src, dst); copied += 1
            except OSError as e:
                self._log(f"归档失败：{src.name} - {e}")
        messagebox.showinfo(APP_NAME, f"已归档 {copied} 个文件；使用复制模式，不破坏原素材。")

    def export_csv(self):
        if not self.items:
            return
        path = filedialog.asksaveasfilename(title="保存 CSV", defaultextension=".csv", filetypes=[("CSV", "*.csv")])
        if not path:
            return
        with open(path, "w", newline="", encoding="utf-8-sig") as f:
            w = csv.DictWriter(f, fieldnames=list(asdict(self.items[0]).keys()))
            w.writeheader(); w.writerows(asdict(item) for item in self.items)
        self.status_var.set(f"CSV 已导出：{path}")

    def ai_suggest_names(self):
        targets = self._selected_items()[:20]
        key = self.api_key.get().strip()
        if not targets:
            return
        if not key:
            messagebox.showinfo(APP_NAME, "请先填写 OpenAI API Key；该功能为可选，不影响离线功能。")
            return
        names = [x.name for x in targets]
        self.ai_result.delete("1.0", "end"); self.ai_result.insert("end", "生成中…")
        self._save_settings()
        threading.Thread(target=self._ai_worker, args=(key, names), daemon=True).start()

    def _ai_worker(self, key: str, names: list[str]):
        payload = {
            "model": self.ai_model.get().strip() or "gpt-5.6-luna",
            "input": "你是电商素材命名助手。只根据文件名本身，为每个文件给出简短、可搜索、适合商品素材库的中文命名建议。不要推断或编造文件名中不存在的品牌、颜色、材质、规格。逐行输出：原文件名 -> 建议名。\n" + "\n".join(names),
            "max_output_tokens": 800,
            "reasoning": {"effort": "none"},
        }
        req = urllib.request.Request(
            "https://api.openai.com/v1/responses",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=45) as r:
                data = json.loads(r.read().decode("utf-8"))
            text = data.get("output_text")
            if not text:
                chunks = []
                for out in data.get("output", []):
                    for c in out.get("content", []):
                        if c.get("type") == "output_text":
                            chunks.append(c.get("text", ""))
                text = "\n".join(chunks) or "未返回文本。"
            self.worker_q.put(("ai", text))
        except urllib.error.HTTPError as e:
            try:
                detail = e.read().decode("utf-8", errors="replace")[:1200]
            except Exception:
                detail = str(e)
            self.worker_q.put(("ai", f"AI 请求失败（HTTP {e.code}）：{detail}"))
        except (urllib.error.URLError, TimeoutError) as e:
            self.worker_q.put(("ai", f"AI 请求失败：{e}"))

    def _poll_queue(self):
        try:
            while True:
                msg = self.worker_q.get_nowait()
                kind = msg[0]
                if kind == "progress":
                    self.progress.configure(value=msg[1]); self.status_var.set(msg[2])
                elif kind == "scan_done":
                    self.items = msg[1]; self.refresh_table(); self.progress.configure(value=100); self.status_var.set("扫描完成")
                    self._log(f"扫描完成：{len(self.items)} 个素材")
                elif kind == "refresh":
                    self.refresh_table()
                elif kind == "done":
                    self.progress.configure(value=100); self.status_var.set(msg[1]); self._log(msg[1]); messagebox.showinfo(APP_NAME, msg[1])
                elif kind == "ai":
                    self.ai_result.delete("1.0", "end"); self.ai_result.insert("end", msg[1]); self.status_var.set("AI 建议完成")
                elif kind == "log":
                    self._log(msg[1]); self.status_var.set(msg[1])
                elif kind == "error":
                    self.status_var.set(msg[1]); self._log(msg[1]); messagebox.showerror(APP_NAME, msg[1])
        except queue.Empty:
            pass
        self.after(120, self._poll_queue)


if __name__ == "__main__":
    App().mainloop()
