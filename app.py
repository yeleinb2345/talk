import os, sys, json, queue, shutil, tempfile, threading, subprocess
from pathlib import Path
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
from PIL import Image, ImageTk

APP_NAME='LuminaGrade 本地调色'; APP_VERSION='1.0.1'

def tool(name):
    exe=name+('.exe' if os.name=='nt' else '')
    if getattr(sys,'frozen',False) and hasattr(sys,'_MEIPASS'):
        p=os.path.join(sys._MEIPASS,exe)
        if os.path.exists(p): return p
    p=shutil.which(exe) or shutil.which(name)
    if p: return p
    raise FileNotFoundError(f'找不到 {exe}')

def clamp(v,a,b): return max(a,min(b,v))

def run(cmd):
    flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0
    p=subprocess.run(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='ignore',creationflags=flags)
    if p.returncode:
        detail=(p.stderr or p.stdout or '').strip()[-2200:]
        raise RuntimeError(detail or f'FFmpeg 退出码 {p.returncode}')
    return p.stdout

class App(tk.Tk):
    presets={
      '剪辑圈亮透':(.08,1.12,1.08,1.15,.26,10,-.02,.10),
      '蓝调清透':(.04,1.15,1.04,1.25,.22,9,-.10,.08),
      '暖白发光':(.10,1.08,1.02,.85,.34,13,.10,.12),
      '柔和高光':(.05,1.04,.98,.55,.40,17,.03,.16),
    }
    keys=('exposure','contrast','saturation','sharpen','glow','radius','warmth','highlights')
    def __init__(self):
        super().__init__(); self.title(f'{APP_NAME} v{APP_VERSION}'); self.geometry('1180x760'); self.minsize(1000,650)
        self.configure(bg='#111318'); self.input=tk.StringVar(); self.preset=tk.StringVar(value='剪辑圈亮透')
        self.status=tk.StringVar(value='请选择视频。所有处理均在本机完成，不使用 AI 修复。'); self.progress=tk.DoubleVar(); self.pt=tk.DoubleVar(value=1)
        self.duration=0.; self.busy=False; self.q=queue.Queue(); self.orig=self.proc=None; self.after_id=None
        self.v={k:tk.DoubleVar() for k in self.keys}; self.style(); self.ui(); self.apply(); self.after(100,self.poll)
    def style(self):
        s=ttk.Style(self)
        try:s.theme_use('clam')
        except:pass
        s.configure('TFrame',background='#111318'); s.configure('Card.TFrame',background='#191c23')
        s.configure('TLabel',background='#111318',foreground='#e9edf4',font=('Microsoft YaHei UI',10)); s.configure('Card.TLabel',background='#191c23',foreground='#e9edf4')
        s.configure('Muted.TLabel',background='#111318',foreground='#9da6b5'); s.configure('Title.TLabel',background='#111318',foreground='white',font=('Microsoft YaHei UI',18,'bold'))
        s.configure('TButton',padding=(12,8)); s.configure('Accent.TButton',padding=(16,9),font=('Microsoft YaHei UI',10,'bold'))
    def ui(self):
        r=ttk.Frame(self,padding=16); r.pack(fill='both',expand=True)
        h=ttk.Frame(r); h.pack(fill='x',pady=(0,12)); ttk.Label(h,text='LuminaGrade',style='Title.TLabel').pack(side='left'); ttk.Label(h,text='  本地画质调色 / Glow / 锐化 · 非 AI',style='Muted.TLabel').pack(side='left',pady=(8,0))
        t=ttk.Frame(r,style='Card.TFrame',padding=12); t.pack(fill='x',pady=(0,12)); ttk.Label(t,text='输入视频',style='Card.TLabel').grid(row=0,column=0,padx=(0,8)); ttk.Entry(t,textvariable=self.input).grid(row=0,column=1,sticky='ew',padx=(0,8)); ttk.Button(t,text='选择视频',command=self.choose).grid(row=0,column=2); ttk.Label(t,text='预设',style='Card.TLabel').grid(row=0,column=3,padx=(18,8)); c=ttk.Combobox(t,textvariable=self.preset,state='readonly',width=14,values=list(self.presets)); c.grid(row=0,column=4); c.bind('<<ComboboxSelected>>',lambda e:self.apply()); t.columnconfigure(1,weight=1)
        b=ttk.Frame(r); b.pack(fill='both',expand=True); b.columnconfigure(0,weight=3); b.columnconfigure(1,weight=2); b.rowconfigure(0,weight=1)
        pc=ttk.Frame(b,style='Card.TFrame',padding=12); pc.grid(row=0,column=0,sticky='nsew',padx=(0,10)); ctl=ttk.Frame(b,style='Card.TFrame',padding=14); ctl.grid(row=0,column=1,sticky='nsew')
        ph=ttk.Frame(pc,style='Card.TFrame'); ph.pack(fill='x',pady=(0,8)); ttk.Label(ph,text='预览（左：原片 / 右：处理）',style='Card.TLabel').pack(side='left'); ttk.Button(ph,text='刷新预览',command=self.preview).pack(side='right')
        self.canvas=tk.Canvas(pc,bg='#0b0d12',highlightthickness=0); self.canvas.pack(fill='both',expand=True); self.canvas.bind('<Configure>',lambda e:self.draw())
        tr=ttk.Frame(pc,style='Card.TFrame'); tr.pack(fill='x',pady=(10,0)); ttk.Label(tr,text='取样时间',style='Card.TLabel').pack(side='left'); self.scale=ttk.Scale(tr,from_=0,to=10,variable=self.pt,command=lambda v:self.schedule()); self.scale.pack(side='left',fill='x',expand=True,padx=10); self.tlabel=ttk.Label(tr,text='1.0s',style='Card.TLabel',width=8); self.tlabel.pack(side='right')
        ttk.Label(ctl,text='画面参数',style='Card.TLabel',font=('Microsoft YaHei UI',13,'bold')).pack(anchor='w',pady=(0,10))
        specs=[('曝光','exposure',-.3,.3),('对比度','contrast',.8,1.35),('饱和度','saturation',.7,1.4),('锐化','sharpen',0,2),('Glow 强度','glow',0,.6),('Glow 半径','radius',2,24),('冷暖','warmth',-.2,.2),('高光亮度','highlights',0,.25)]
        for label,k,a,z in specs:self.slider(ctl,label,k,a,z)
        ar=ttk.Frame(ctl,style='Card.TFrame'); ar.pack(fill='x',pady=(14,0)); ttk.Button(ar,text='恢复当前预设',command=self.apply).pack(side='left'); ttk.Button(ar,text='导出 MP4',style='Accent.TButton',command=self.export).pack(side='right')
        bt=ttk.Frame(r,padding=(0,10,0,0)); bt.pack(fill='x'); ttk.Progressbar(bt,variable=self.progress,maximum=100).pack(fill='x',side='left',expand=True,padx=(0,12)); ttk.Label(bt,textvariable=self.status,style='Muted.TLabel').pack(side='right')
    def slider(self,p,label,k,a,z):
        f=ttk.Frame(p,style='Card.TFrame'); f.pack(fill='x',pady=5); row=ttk.Frame(f,style='Card.TFrame'); row.pack(fill='x'); ttk.Label(row,text=label,style='Card.TLabel').pack(side='left'); val=ttk.Label(row,text='',style='Card.TLabel',width=7); val.pack(side='right')
        ttk.Scale(f,from_=a,to=z,variable=self.v[k],command=lambda x,kk=k,vv=val:(vv.config(text=f'{self.v[kk].get():.2f}'),self.schedule())).pack(fill='x'); self.v[k].trace_add('write',lambda *_ ,kk=k,vv=val:vv.config(text=f'{self.v[kk].get():.2f}'))
    def apply(self):
        for k,x in zip(self.keys,self.presets[self.preset.get()]):self.v[k].set(x)
        self.schedule(80)
    def choose(self):
        p=filedialog.askopenfilename(title='选择视频',filetypes=[('视频文件','*.mp4 *.mov *.mkv *.avi *.m4v *.webm'),('所有文件','*.*')])
        if not p:return
        self.input.set(p)
        try:self.duration=float(json.loads(run([tool('ffprobe'),'-v','error','-show_entries','format=duration','-of','json',p]))['format']['duration'])
        except Exception as e:self.duration=10.; self.status.set(f'时长读取失败，将按 10 秒预览：{e}')
        end=max(.1,self.duration); self.scale.configure(to=end); self.pt.set(min(max(.5,end*.18),max(.1,end-.1))); self.preview()
    def graph(self):
        e=clamp(self.v['exposure'].get(),-.3,.3); c=clamp(self.v['contrast'].get(),.5,2); s=clamp(self.v['saturation'].get(),0,3); sh=clamp(self.v['sharpen'].get(),0,2); g=clamp(self.v['glow'].get(),0,1); r=clamp(self.v['radius'].get(),.5,40); w=clamp(self.v['warmth'].get(),-.3,.3); hi=clamp(self.v['highlights'].get(),0,.3)
        rs=clamp(w*.45,-.25,.25); bs=clamp(-w*.45,-.25,.25); bb=clamp(e+hi*.1,-.35,.35); gb=clamp(-.18+hi*.35,-.25,.08); gc=1.55+hi*.9
        return f'[0:v]eq=brightness={bb:.4f}:contrast={c:.4f}:saturation={s:.4f},colorbalance=rs={rs:.4f}:bs={bs:.4f},unsharp=5:5:{sh:.4f}:5:5:0,split=2[base][glow];[glow]eq=brightness={gb:.4f}:contrast={gc:.4f},gblur=sigma={r:.3f}[halo];[base][halo]blend=all_mode=screen:all_opacity={g:.4f}[outv]'
    def schedule(self,d=280):
        self.tlabel.config(text=f'{self.pt.get():.1f}s')
        if not self.input.get() or self.busy:return
        if self.after_id:self.after_cancel(self.after_id)
        self.after_id=self.after(d,self.preview)
    def preview(self):
        p=self.input.get()
        if self.busy or not p or not os.path.exists(p):return
        self.busy=True; self.status.set('正在生成本地预览…'); threading.Thread(target=self.preview_worker,args=(p,float(self.pt.get()),self.graph()),daemon=True).start()
    def preview_worker(self,p,t,g):
        d=tempfile.mkdtemp(prefix='luminagrade_preview_')
        try:
            a=os.path.join(d,'before.png'); b=os.path.join(d,'after.png'); ff=tool('ffmpeg'); common=[ff,'-hide_banner','-loglevel','error','-y','-i',p,'-ss',f'{t:.3f}','-an','-sn','-dn']
            run(common+['-frames:v','1',a]); run(common+['-filter_complex',g,'-map','[outv]','-frames:v','1',b]); self.q.put(('preview',Image.open(a).convert('RGB').copy(),Image.open(b).convert('RGB').copy()))
        except Exception as e:self.q.put(('error',f'预览失败：\n{e}'))
        finally:shutil.rmtree(d,ignore_errors=True)
    def draw(self):
        self.canvas.delete('all'); w=max(10,self.canvas.winfo_width()); h=max(10,self.canvas.winfo_height())
        if self.orig is None:self.canvas.create_text(w/2,h/2,text='选择视频后显示原片 / 处理后',fill='#7f8898',font=('Microsoft YaHei UI',13)); return
        cw=(w-8)//2
        def fit(i):x=i.copy(); x.thumbnail((cw,h),Image.Resampling.LANCZOS); return x
        self.ta=ImageTk.PhotoImage(fit(self.orig)); self.tb=ImageTk.PhotoImage(fit(self.proc)); self.canvas.create_image(cw//2,h//2,image=self.ta); self.canvas.create_image(cw+8+cw//2,h//2,image=self.tb)
    def export(self):
        p=self.input.get()
        if self.busy:return
        if not p or not os.path.exists(p):messagebox.showinfo(APP_NAME,'请先选择视频。'); return
        out=filedialog.asksaveasfilename(title='导出 MP4',defaultextension='.mp4',initialfile=Path(p).stem+'_LuminaGrade.mp4',filetypes=[('MP4 视频','*.mp4')])
        if not out:return
        self.busy=True; self.progress.set(0); self.status.set('正在导出…'); threading.Thread(target=self.export_worker,args=(p,out,self.graph(),max(self.duration,.01)),daemon=True).start()
    def export_worker(self,p,out,g,dur):
        try:
            ff=tool('ffmpeg'); flags=subprocess.CREATE_NO_WINDOW if os.name=='nt' else 0; cmd=[ff,'-hide_banner','-y','-i',p,'-filter_complex',g,'-map','[outv]','-map','0:a?','-c:v','libx264','-preset','medium','-crf','16','-pix_fmt','yuv420p','-c:a','aac','-b:a','192k','-movflags','+faststart','-progress','pipe:1','-nostats',out]
            x=subprocess.Popen(cmd,stdout=subprocess.PIPE,stderr=subprocess.PIPE,text=True,encoding='utf-8',errors='ignore',creationflags=flags)
            for line in x.stdout or []:
                if line.startswith('out_time_ms='):
                    try:self.q.put(('progress',clamp(float(line.split('=',1)[1])/1e6/dur*100,0,99.5)))
                    except:pass
            err=x.stderr.read() if x.stderr else ''; code=x.wait()
            if code:raise RuntimeError(err[-2200:] or f'FFmpeg 退出码 {code}')
            self.q.put(('done',out))
        except Exception as e:self.q.put(('error',f'导出失败：\n{e}'))
    def poll(self):
        try:
            while 1:
                m=self.q.get_nowait(); k=m[0]
                if k=='preview':self.orig,self.proc=m[1],m[2]; self.busy=False; self.status.set('预览已刷新。'); self.draw()
                elif k=='progress':self.progress.set(m[1]); self.status.set(f'正在导出… {m[1]:.0f}%')
                elif k=='done':self.busy=False; self.progress.set(100); self.status.set('导出完成。'); messagebox.showinfo(APP_NAME,f'导出完成：\n{m[1]}')
                elif k=='error':self.busy=False; self.status.set('处理失败。'); messagebox.showerror(APP_NAME,m[1])
        except queue.Empty:pass
        self.after(100,self.poll)

if __name__=='__main__': App().mainloop()
