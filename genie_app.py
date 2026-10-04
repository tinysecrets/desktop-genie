#!/usr/bin/env python3
from __future__ import annotations
import json
import math
import re
import os
import sys
import tempfile
import threading
import subprocess
import shutil
from datetime import datetime
from pathlib import Path
from tkinter import (
    BOTH, END, LEFT, RIGHT, X, BooleanVar, StringVar, Tk, Text, Canvas,
    Frame, Scrollbar, Label, Button, filedialog
)
from contextlib import redirect_stdout
from io import BytesIO, StringIO

import requests
from PIL import Image, ImageTk

GENIE_DIR = Path.home() / ".config" / "genie"
STATE_DIR = GENIE_DIR / "state"
HISTORY_FILE = STATE_DIR / "desktop-chat.json"
STATE_DIR.mkdir(parents=True, exist_ok=True)

sys.path.insert(0, str(GENIE_DIR))
import genie

MAX_HISTORY = 30
CONTEXT_TURNS = 12

# Desktop GENIE follows the visual language of the user's web GENIE:
# midnight black, rich gold, warm cream, editorial serif + clean sans.
INK = "#0B0B0E"
INK_DEEP = "#050507"
SURFACE = "#141417"
SURFACE_2 = "#1C1C20"
SURFACE_WARM = "#1F1A12"
BORDER = "#2A2420"
BORDER_BRIGHT = "#3A3220"
GOLD = "#D4AF37"
GOLD_BRIGHT = "#F0CB58"
GOLD_PALE = "#F5E9C8"
GOLD_DEEP = "#8A7642"
GOLD_MUTE = "#5A4A28"
TEAL = "#31E6D2"
TEAL_DEEP = "#0B6E68"
CREAM = "#F5E9C8"
CREAM_DIM = "#C5B689"
MUTED = "#9A8868"
WHITE = "#FFFFFF"
DANGER = "#E5715E"

FONT_HEAD = ("Noto Serif", 30, "bold")
FONT_HEAD_SMALL = ("Noto Serif", 18, "bold")
FONT_BODY = ("Noto Sans", 10)
FONT_BODY_BOLD = ("Noto Sans", 9, "bold")
FONT_AI = ("Noto Serif", 16)
FONT_MONO = ("DejaVu Sans Mono", 8)

class GenieApp:
    """Premium desktop front end around the real GENIE engine."""

    def __init__(self, root: Tk):
        self.root = root
        self.root.title("GENIE • Command Center")
        self.root.geometry("1280x820")
        self.root.minsize(980, 680)
        self.root.configure(bg=INK)
        self.root.protocol("WM_DELETE_WINDOW", self.close)

        self.history = self.load_history()
        self.busy = False
        self.phase = 0.0
        self.status = StringVar(value="GENIE is ready")
        self.auto_context = BooleanVar(value=True)
        self.voice_enabled = BooleanVar(value=True)
        self.listening = False
        self.last_answer = ""
        self.mode_window = None
        self.mode_status = None
        self.mode_preview = None
        self.mode_photo = None

        self.build_ui()
        self.render_history()
        self.update_system_status()
        self.input.focus_set()
        self.animate()

    def build_ui(self):
        """Visual-only premium redesign. Engine, actions, policy, and data flow stay unchanged."""
        self.ui_bg="#05060A"; self.ui_panel="#0B0E17"; self.ui_panel2="#111625"; self.ui_panel3="#182033"
        self.ui_line="#273149"; self.ui_text="#F7FAFF"; self.ui_dim="#91A0B8"; self.ui_violet="#8D6BFF"
        self.ui_violet2="#BCA9FF"; self.ui_mint="#54F1D0"; self.ui_gold="#FFD66B"; self.ui_blue="#5BB8FF"
        self.root.title("GENIE — Personal Intelligence"); self.root.geometry("1520x920"); self.root.minsize(1180,760); self.root.configure(bg=self.ui_bg)
        self.scene=Canvas(self.root,bg=self.ui_bg,highlightthickness=0,bd=0); self.scene.pack(fill=BOTH,expand=True)
        self.scene.bind("<Configure>",lambda _e:self.draw_scene())

        self.header=Frame(self.scene,bg=self.ui_bg); self.header.place(relx=.035,rely=.025,relwidth=.93,height=58)
        Label(self.header,text="GENIE",bg=self.ui_bg,fg=self.ui_text,font=("Noto Serif",30,"bold")).pack(side=LEFT)
        Label(self.header,text="PERSONAL INTELLIGENCE",bg=self.ui_bg,fg=self.ui_violet2,font=("Noto Sans",10,"bold")).pack(side=LEFT,padx=(18,0),pady=(9,0))
        self.clock_label=Label(self.header,text=datetime.now().strftime("%I:%M:%S %p  ·  %a, %b %d"),bg=self.ui_bg,fg=self.ui_gold,font=("DejaVu Sans Mono",11,"bold")); self.clock_label.pack(side=RIGHT,padx=(0,18),pady=8)
        self.live_badge=Label(self.header,text="●  ONLINE  ·  GUARDED",bg="#10182A",fg=self.ui_mint,padx=14,pady=7,font=("Noto Sans",9,"bold")); self.live_badge.pack(side=RIGHT,pady=6)

        self.sidebar=Frame(self.scene,bg=self.ui_panel,highlightthickness=1,highlightbackground=self.ui_line); self.sidebar.place(relx=.035,rely=.105,relwidth=.18,relheight=.855)
        Label(self.sidebar,text="WISH SHELF",bg=self.ui_panel,fg=self.ui_dim,font=("Noto Sans",8,"bold")).pack(anchor="w",padx=18,pady=(20,9))
        self.new_button=Button(self.sidebar,text="＋  NEW WISH",command=self.new_chat,bg=self.ui_violet,fg="#090711",activebackground=self.ui_violet2,activeforeground="#090711",relief="flat",bd=0,padx=12,pady=11,font=("Noto Sans",10,"bold"),cursor="hand2"); self.new_button.pack(fill=X,padx=12,pady=(0,14))
        def wish(label,prompt,accent):
            b=Button(self.sidebar,text=label,command=lambda p=prompt:self.use_prompt(p),bg=self.ui_panel2,fg=self.ui_text,activebackground=self.ui_panel3,activeforeground=accent,relief="flat",bd=0,padx=13,pady=10,anchor="w",cursor="hand2",font=("Noto Sans",9,"bold")); b.pack(fill=X,padx=10,pady=3)
        wish("⚡  Computer health","Check my computer health and tell me what needs attention.",self.ui_gold)
        wish("✦  Fix something","Inspect my computer for obvious problems and fix what is safe to fix.",self.ui_violet2)
        wish("◉  Open browser","Open my browser and get it ready for me.",self.ui_blue)
        wish("▣  Check devices","Check my connected computers and Android devices and report their status.",self.ui_mint)
        Label(self.sidebar,text="CONTROL",bg=self.ui_panel,fg=self.ui_dim,font=("Noto Sans",8,"bold")).pack(anchor="w",padx=18,pady=(24,8))
        Button(self.sidebar,text="✦  GENIE MODE",command=self.open_genie_mode,bg="#17142A",fg=self.ui_violet2,activebackground="#211B3C",activeforeground=self.ui_text,relief="flat",bd=0,padx=13,pady=10,anchor="w",cursor="hand2",font=("Noto Sans",9,"bold")).pack(fill=X,padx=10,pady=3)
        Button(self.sidebar,text="◌  TALK TO GENIE",command=self.quick_talk,bg=self.ui_panel2,fg=self.ui_text,activebackground=self.ui_panel3,activeforeground=self.ui_mint,relief="flat",bd=0,padx=13,pady=10,anchor="w",cursor="hand2",font=("Noto Sans",9,"bold")).pack(fill=X,padx=10,pady=3)
        Button(self.sidebar,text="▤  SEE MY SCREEN",command=self.see_screen,bg=self.ui_panel2,fg=self.ui_text,activebackground=self.ui_panel3,activeforeground=self.ui_blue,relief="flat",bd=0,padx=13,pady=10,anchor="w",cursor="hand2",font=("Noto Sans",9,"bold")).pack(fill=X,padx=10,pady=3)
        Button(self.sidebar,text="◈  LIVE WORKSPACE",command=self.watch_genie,bg="#102B2C",fg=self.ui_mint,activebackground="#174544",activeforeground=self.ui_text,relief="flat",bd=0,padx=13,pady=10,anchor="w",cursor="hand2",font=("Noto Sans",9,"bold")).pack(fill=X,padx=10,pady=(8,3))
        self.context_button=Button(self.sidebar,text="CONTEXT  ·  ON",command=self.toggle_context,bg="#080B12",fg=self.ui_mint,activebackground="#0D1320",activeforeground=self.ui_mint,relief="flat",bd=0,padx=13,pady=9,anchor="w",cursor="hand2",font=("Noto Sans",8,"bold")); self.context_button.pack(fill=X,padx=10,pady=(22,3))
        self.status_side=Label(self.sidebar,text="GENIE\n\nPreparing system pulse…",bg=self.ui_panel,fg=self.ui_dim,justify=LEFT,anchor="nw",padx=18,pady=12,font=("DejaVu Sans Mono",8)); self.status_side.pack(fill=X,padx=10,pady=(10,0))

        self.center=Frame(self.scene,bg=self.ui_bg); self.center.place(relx=.235,rely=.105,relwidth=.51,relheight=.855)
        self.hero=Frame(self.center,bg=self.ui_panel,highlightthickness=1,highlightbackground=self.ui_line); self.hero.pack(fill=X)
        Label(self.hero,text="MAKE SOMETHING HAPPEN",bg=self.ui_panel,fg=self.ui_dim,font=("Noto Sans",8,"bold")).pack(anchor="w",padx=22,pady=(18,2))
        Label(self.hero,text="Your command.\nGENIE's action.",bg=self.ui_panel,fg=self.ui_text,justify=LEFT,font=("Noto Serif",32,"bold")).pack(anchor="w",padx=22)
        self.orb=Canvas(self.hero,width=500,height=255,bg=self.ui_panel,highlightthickness=0,bd=0); self.orb.pack(fill=X,padx=2,pady=(0,2))

        self.composer=Frame(self.center,bg=self.ui_panel2,highlightthickness=1,highlightbackground="#34415D"); self.composer.pack(fill=X,pady=(10,9))
        self.input=Text(self.composer,height=3,wrap="word",bg=self.ui_panel2,fg=self.ui_text,insertbackground=self.ui_violet2,selectbackground="#3B315E",relief="flat",bd=0,padx=16,pady=13,font=("Noto Sans",13)); self.input.pack(side=LEFT,fill=BOTH,expand=True,padx=(5,0),pady=5); self.input.bind("<Control-Return>",self.send_event)
        self.send_button=Button(self.composer,text="SEND  →",command=self.send_or_stop,bg=self.ui_violet,fg="#090711",activebackground=self.ui_violet2,activeforeground="#090711",relief="flat",bd=0,padx=18,pady=12,font=("Noto Sans",9,"bold"),cursor="hand2"); self.send_button.pack(side=RIGHT,padx=9,pady=9)
        self.bottom=Frame(self.center,bg=self.ui_bg); self.bottom.pack(fill=X)
        self.status_label=Label(self.bottom,textvariable=self.status,bg=self.ui_bg,fg=self.ui_mint,font=("Noto Sans",9,"bold")); self.status_label.pack(side=LEFT,padx=3,pady=4)
        Button(self.bottom,text="COPY LAST",command=self.copy_last_answer,bg=self.ui_panel2,fg=self.ui_dim,activebackground=self.ui_panel3,activeforeground=self.ui_text,relief="flat",bd=0,padx=10,pady=4,font=("Noto Sans",8,"bold"),cursor="hand2").pack(side=RIGHT,padx=3,pady=3)
        Button(self.bottom,text="EXPORT",command=self.export_chat,bg=self.ui_panel2,fg=self.ui_dim,activebackground=self.ui_panel3,activeforeground=self.ui_text,relief="flat",bd=0,padx=10,pady=4,font=("Noto Sans",8,"bold"),cursor="hand2").pack(side=RIGHT,padx=3,pady=3)
        Label(self.bottom,text="CTRL + ENTER  /  SEND",bg=self.ui_bg,fg=self.ui_dim,font=("Noto Sans",8)).pack(side=RIGHT,padx=3,pady=4)
        self.chat_frame=Frame(self.center,bg=self.ui_panel,highlightthickness=1,highlightbackground=self.ui_line); self.chat_frame.pack(fill=BOTH,expand=True); self.chat_frame.rowconfigure(0,weight=1); self.chat_frame.columnconfigure(0,weight=1)
        self.chat=Text(self.chat_frame,wrap="word",state="disabled",bg=self.ui_panel,fg=self.ui_text,insertbackground=self.ui_violet2,selectbackground="#3B315E",relief="flat",bd=0,padx=20,pady=16,font=("Noto Sans",12),spacing3=9)
        self.scroll=Scrollbar(self.chat_frame,command=self.chat.yview,bg="#3C315F",troughcolor=self.ui_panel,activebackground=self.ui_violet2,width=7); self.chat.configure(yscrollcommand=self.scroll.set); self.chat.grid(row=0,column=0,sticky="nsew"); self.scroll.grid(row=0,column=1,sticky="ns")

        self.activity=Frame(self.scene,bg=self.ui_panel,highlightthickness=1,highlightbackground=self.ui_line); self.activity.place(relx=.765,rely=.105,relwidth=.20,relheight=.855)
        Label(self.activity,text="SYSTEM PULSE",bg=self.ui_panel,fg=self.ui_text,font=("Noto Serif",14,"bold")).pack(anchor="w",padx=18,pady=(20,2))
        Label(self.activity,text="LIVE · LOCAL · GUARDED",bg=self.ui_panel,fg=self.ui_mint,font=("Noto Sans",9,"bold")).pack(anchor="w",padx=18,pady=(0,14))
        self.system_lines=[]
        for title,value,accent in [("GENIE CORE","READY",self.ui_mint),("DESKTOP","GUARDED",self.ui_gold),("TOOLS","loading",self.ui_blue),("ROUTER","AUTO",self.ui_violet2)]:
            card=Frame(self.activity,bg=self.ui_panel2,highlightthickness=1,highlightbackground="#202A40"); card.pack(fill=X,padx=10,pady=4)
            Label(card,text=title,bg=self.ui_panel2,fg=self.ui_dim,font=("Noto Sans",7,"bold")).pack(anchor="w",padx=11,pady=(9,1))
            val=Label(card,text=value,bg=self.ui_panel2,fg=accent,font=("Noto Sans",10,"bold")); val.pack(anchor="w",padx=11,pady=(0,9)); self.system_lines.append(val)
        Label(self.activity,text="ACTIVITY STREAM",bg=self.ui_panel,fg=self.ui_violet2,font=("Noto Sans",8,"bold")).pack(anchor="w",padx=18,pady=(20,8))
        self.activity_box=Text(self.activity,height=12,wrap="word",state="disabled",bg="#070A10",fg=self.ui_dim,relief="flat",bd=0,padx=11,pady=11,font=("DejaVu Sans Mono",8)); self.activity_box.pack(fill=BOTH,expand=True,padx=10,pady=(0,10))

    def draw_scene(self):
        if not hasattr(self,"scene"): return
        w=max(self.scene.winfo_width(),1000); h=max(self.scene.winfo_height(),700); self.scene.delete("bg")
        for i in range(12):
            inset=i*32
            self.scene.create_oval(w*.26-inset,h*.05-inset*.15,w*.74+inset,h*.95+inset*.12,outline=("#0A0E18" if i%2 else "#080C14"),width=1,tags="bg")
        self.scene.create_oval(w*.44,-h*.20,w*.78,h*.28,fill="#090A18",outline="",tags="bg")
        self.scene.create_oval(w*.08,h*.72,w*.42,h*1.12,fill="#07131A",outline="",tags="bg")
        self.scene.create_line(w*.035,h*.095,w*.965,h*.095,fill="#171D2A",width=1,tags="bg")
        try:
            if not hasattr(self,"wah_lah_bg_photo"):
                img=Image.open(GENIE_DIR / "wah-lah-genie.png").convert("RGBA")
                img.thumbnail((520,520), Image.Resampling.LANCZOS)
                self.wah_lah_bg_photo=ImageTk.PhotoImage(img)
            self.scene.create_image(w*.87,h*.58,image=self.wah_lah_bg_photo,anchor="center",tags="bg")
        except Exception:
            pass
        self.scene.tag_lower("bg")

    def _draw_mascot(self):
        if not hasattr(self, "orb"):
            return
        self.orb.delete("all")
        w=max(self.orb.winfo_width(),500); h=max(self.orb.winfo_height(),255)
        try:
            if not hasattr(self, "wah_lah_genie_photo"):
                asset=GENIE_DIR / "wah-lah-genie.png"
                img=Image.open(asset).convert("RGBA")
                img.thumbnail((330, 330), Image.Resampling.LANCZOS)
                self.wah_lah_genie_photo=ImageTk.PhotoImage(img)
            x=w*.50; y=h*.47
            self.orb.create_oval(x-145,y-112,x+145,y+112,fill="#0D0B15",outline="#33294A",width=1)
            self.orb.create_oval(x-130,y-98,x+130,y+98,fill="#15101D",outline="#6B3D70",width=1)
            self.orb.create_image(x,y,image=self.wah_lah_genie_photo,anchor="center")
            self.orb.create_text(x,h-10,text="WAH-LAH GENIE  •  READY TO MAKE IT HAPPEN",fill=self.ui_gold,font=("Noto Sans",8,"bold"))
        except Exception:
            self.orb.create_text(w*.5,h*.45,text="GENIE",fill=self.ui_gold,font=("Noto Sans",26,"bold"))
            self.orb.create_text(w*.5,h*.65,text="READY",fill=self.ui_mint,font=("Noto Sans",9,"bold"))

    def build_sidebar(self):
        self.sidebar = Frame(self.main, bg=SURFACE, bd=0,
                             highlightthickness=1, highlightbackground=BORDER)
        self.sidebar.grid(row=0, column=0, sticky="nsw", padx=(0, 10))
        self.sidebar.configure(width=220)
        self.sidebar.pack_propagate(False)

        Label(
            self.sidebar, text="GENIE", bg=SURFACE, fg=CREAM,
            font=FONT_HEAD_SMALL
        ).pack(anchor="w", padx=18, pady=(18, 2))
        Label(
            self.sidebar, text="THE MAGIC IS IN THE ACTION", bg=SURFACE,
            fg=MUTED, font=FONT_BODY_BOLD
        ).pack(anchor="w", padx=18, pady=(0, 18))

        self.new_button = Button(
            self.sidebar, text="✦  New Wish", command=self.new_chat,
            bg=GOLD, fg=INK, activebackground=GOLD_BRIGHT,
            activeforeground=INK, relief="flat", bd=0, padx=12, pady=10,
            font=FONT_BODY_BOLD, cursor="hand2"
        )
        self.new_button.pack(fill=X, padx=14, pady=(0, 16))

        Label(
            self.sidebar, text="QUICK WISHES", bg=SURFACE, fg=GOLD_BRIGHT,
            font=FONT_BODY_BOLD
        ).pack(anchor="w", padx=18, pady=(0, 8))

        actions = [
            ("⚡  Check my computer", "Check my computer health and tell me what needs attention."),
            ("🧰  Fix something", "Inspect my computer for obvious problems and fix what is safe to fix."),
            ("🌐  Open browser", "Open my browser and get it ready for me."),
            ("📱  Check devices", "Check my connected computers and Android devices and report their status."),
        ]
        for label, prompt in actions:
            Button(
                self.sidebar, text=label, command=lambda p=prompt: self.use_prompt(p),
                bg=SURFACE_2, fg=CREAM, activebackground=SURFACE_WARM,
                activeforeground=GOLD_BRIGHT, relief="flat", bd=0,
                padx=10, pady=9, anchor="w", cursor="hand2",
                font=FONT_BODY_BOLD
            ).pack(fill=X, padx=10, pady=3)

        Label(self.sidebar, text="GENIE MODE", bg=SURFACE, fg=TEAL,
              font=FONT_BODY_BOLD).pack(anchor="w", padx=18, pady=(22, 7))
        Button(self.sidebar, text="GENIE MODE", command=self.open_genie_mode,
               bg=GOLD_MUTE, fg=GOLD_PALE, activebackground=GOLD_DEEP,
               activeforeground=WHITE, relief="flat", bd=0, padx=10, pady=9,
               anchor="w", cursor="hand2", font=FONT_BODY_BOLD).pack(fill=X, padx=10, pady=3)
        Button(self.sidebar, text="TALK TO GENIE", command=self.quick_talk,
               bg=SURFACE_2, fg=CREAM, activebackground=SURFACE_WARM,
               activeforeground=GOLD_BRIGHT, relief="flat", bd=0, padx=10, pady=9,
               anchor="w", cursor="hand2", font=FONT_BODY_BOLD).pack(fill=X, padx=10, pady=3)
        Button(self.sidebar, text="SEE MY SCREEN", command=self.see_screen,
               bg=SURFACE_2, fg=CREAM, activebackground=SURFACE_WARM,
               activeforeground=GOLD_BRIGHT, relief="flat", bd=0, padx=10, pady=9,
               anchor="w", cursor="hand2", font=FONT_BODY_BOLD).pack(fill=X, padx=10, pady=3)

        Button(self.sidebar, text="LIVE WORKSPACE", command=self.watch_genie,
               bg=TEAL_DEEP, fg=CREAM, activebackground=TEAL,
               activeforeground=INK, relief="flat", bd=0, padx=10, pady=9,
               anchor="w", cursor="hand2", font=FONT_BODY_BOLD).pack(fill=X, padx=10, pady=(8, 3))

        Label(
            self.sidebar, text="SESSION", bg=SURFACE, fg=MUTED,
            font=FONT_BODY_BOLD
        ).pack(anchor="w", padx=18, pady=(22, 7))

        self.context_button = Button(
            self.sidebar, text="Context: ON", command=self.toggle_context,
            bg=INK, fg=CREAM_DIM, activebackground=INK,
            activeforeground=WHITE, relief="flat", bd=0,
            padx=10, pady=8, anchor="w", cursor="hand2",
            font=FONT_BODY_BOLD
        )
        self.context_button.pack(fill=X, padx=10, pady=3)

        self.status_side = Label(
            self.sidebar, text="", bg=SURFACE, fg=MUTED,
            justify=LEFT, anchor="nw", padx=18, pady=12,
            font=FONT_MONO
        )
        self.status_side.pack(fill=X, padx=10, pady=(18, 0))

    def build_center(self):
        self.center = Frame(self.main, bg=INK, bd=0)
        self.center.grid(row=0, column=1, sticky="nsew")
        self.center.rowconfigure(1, weight=1)
        self.center.columnconfigure(0, weight=1)

        self.hero = Frame(
            self.center, bg=INK_DEEP, bd=0,
            highlightthickness=1, highlightbackground=BORDER_BRIGHT
        )
        self.hero.grid(row=0, column=0, sticky="ew", pady=(0, 10))
        self.hero.columnconfigure(1, weight=1)

        self.orb = Canvas(
            self.hero, width=190, height=170, bg=INK_DEEP,
            highlightthickness=0, bd=0
        )
        self.orb.grid(row=0, column=0, rowspan=2, padx=(8, 0), pady=8)

        Label(
            self.hero, text="Welcome to your GENIE.", bg=INK_DEEP,
            fg=CREAM, font=("Noto Serif", 26, "bold")
        ).grid(row=0, column=1, sticky="sw", padx=(4, 20), pady=(34, 0))
        Label(
            self.hero,
            text="Think it. Say it. Wish it. GENIE handles the work.",
            bg=INK_DEEP, fg=CREAM_DIM, font=FONT_AI
        ).grid(row=1, column=1, sticky="nw", padx=(4, 20), pady=(2, 28))

        self.chat_frame = Frame(
            self.center, bg=SURFACE, bd=0,
            highlightthickness=1, highlightbackground=BORDER
        )
        self.chat_frame.grid(row=1, column=0, sticky="nsew")
        self.chat_frame.rowconfigure(0, weight=1)
        self.chat_frame.columnconfigure(0, weight=1)

        self.chat = Text(
            self.chat_frame, wrap="word", state="disabled",
            bg=SURFACE, fg=CREAM, insertbackground=GOLD_BRIGHT,
            selectbackground=GOLD_MUTE, relief="flat", bd=0,
            padx=22, pady=18, font=FONT_BODY, spacing3=5
        )
        self.scroll = Scrollbar(
            self.chat_frame, command=self.chat.yview,
            bg=GOLD_MUTE, troughcolor=INK, activebackground=GOLD,
            width=10
        )
        self.chat.configure(yscrollcommand=self.scroll.set)
        self.chat.grid(row=0, column=0, sticky="nsew")
        self.scroll.grid(row=0, column=1, sticky="ns")

        self.composer = Frame(
            self.center, bg=INK_DEEP, bd=0,
            highlightthickness=1, highlightbackground=BORDER_BRIGHT
        )
        self.composer.grid(row=2, column=0, sticky="ew", pady=(10, 0))
        self.composer.columnconfigure(0, weight=1)

        self.input = Text(
            self.composer, height=3, wrap="word",
            bg=INK_DEEP, fg=CREAM, insertbackground=GOLD_BRIGHT,
            selectbackground=GOLD_MUTE, relief="flat", bd=0,
            padx=14, pady=11, font=FONT_BODY
        )
        self.input.grid(row=0, column=0, sticky="nsew", padx=(8, 0), pady=8)
        self.input.bind("<Control-Return>", self.send_event)

        self.send_button = Button(
            self.composer, text="✦  SEND WISH", command=self.send,
            bg=GOLD, fg=INK, activebackground=GOLD_BRIGHT,
            activeforeground=INK, relief="flat", bd=0, padx=18, pady=12,
            font=FONT_BODY_BOLD, cursor="hand2"
        )
        self.send_button.grid(row=0, column=1, padx=8, pady=8)

        self.bottom = Frame(self.center, bg=INK, bd=0)
        self.bottom.grid(row=3, column=0, sticky="ew")
        self.status_label = Label(
            self.bottom, textvariable=self.status, bg=INK, fg=GOLD_BRIGHT,
            font=FONT_BODY_BOLD
        )
        self.status_label.pack(side=LEFT, padx=4, pady=8)
        Label(
            self.bottom, text="Ctrl+Enter to send", bg=INK, fg=MUTED,
            font=FONT_BODY
        ).pack(side=RIGHT, padx=4, pady=8)

    def build_activity(self):
        self.activity = Frame(
            self.main, bg=SURFACE, bd=0,
            highlightthickness=1, highlightbackground=BORDER
        )
        self.activity.grid(row=0, column=2, sticky="nse", padx=(10, 0))
        self.activity.configure(width=235)
        self.activity.pack_propagate(False)

        Label(
            self.activity, text="LIVE SYSTEMS", bg=SURFACE, fg=GOLD_BRIGHT,
            font=FONT_BODY_BOLD
        ).pack(anchor="w", padx=16, pady=(18, 10))

        self.system_lines = []
        for title, value in [
            ("GENIE ENGINE", "READY"),
            ("COMMANDER", "AUTO"),
            ("TOOLS", "loading"),
            ("MODEL ROUTER", "AUTO"),
        ]:
            card = Frame(
                self.activity, bg=SURFACE_2, bd=0,
                highlightthickness=1, highlightbackground=BORDER
            )
            card.pack(fill=X, padx=10, pady=4)
            Label(
                card, text=title, bg=SURFACE_2, fg=MUTED,
                font=FONT_BODY_BOLD, anchor="w"
            ).pack(fill=X, padx=10, pady=(8, 1))
            val = Label(
                card, text=value, bg=SURFACE_2, fg=CREAM,
                font=FONT_BODY_BOLD, anchor="w"
            )
            val.pack(fill=X, padx=10, pady=(0, 8))
            self.system_lines.append(val)

        Label(
            self.activity, text="ACTIVITY", bg=SURFACE, fg=TEAL,
            font=FONT_BODY_BOLD
        ).pack(anchor="w", padx=16, pady=(20, 7))

        self.activity_box = Text(
            self.activity, height=11, wrap="word", state="disabled",
            bg=INK_DEEP, fg=CREAM_DIM, relief="flat", bd=0,
            padx=10, pady=10, font=FONT_MONO
        )
        self.activity_box.pack(fill=BOTH, expand=True, padx=10, pady=(0, 10))

    def load_history(self):
        try:
            data = json.loads(HISTORY_FILE.read_text())
            if isinstance(data, list):
                return [x for x in data if isinstance(x, dict)][-MAX_HISTORY:]
        except Exception:
            pass
        return []

    def save_history(self):
        try:
            HISTORY_FILE.write_text(json.dumps(self.history[-MAX_HISTORY:], indent=2))
        except Exception:
            pass

    def commander_running(self):
        try:
            return subprocess.run(
                ["pgrep", "-u", str(__import__("os").getuid()), "-f", "desktop-commander.*remote"],
                stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=2
            ).returncode == 0
        except Exception:
            return False

    def update_system_status(self):
        tools = len(getattr(genie, "TOOLS", []))
        running = self.commander_running()
        self.system_lines[0].configure(text="READY", fg=TEAL)
        self.system_lines[1].configure(
            text="CONNECTED" if running else "STARTING",
            fg=TEAL if running else GOLD_BRIGHT
        )
        self.system_lines[2].configure(text=f"{tools} registered", fg=CREAM)
        self.system_lines[3].configure(text="AUTO", fg=GOLD_BRIGHT)
        self.status_side.configure(
            text=f"GENIE\n\n{tools} tools active\nCommander: {'connected' if running else 'starting'}\nModel: AUTO"
        )
        self.root.after(1800, self.update_system_status)

    def render_history(self):
        self.chat.configure(state="normal")
        self.chat.delete("1.0", END)
        self.chat.tag_configure("you", foreground=GOLD_BRIGHT,
                                font=FONT_BODY_BOLD, spacing1=8)
        self.chat.tag_configure("genie", foreground=TEAL,
                                font=FONT_BODY_BOLD, spacing1=8)
        self.chat.tag_configure("body", foreground=CREAM, font=FONT_AI,
                                lmargin1=4, lmargin2=4)
        self.chat.tag_configure("divider", foreground=GOLD_MUTE,
                                font=FONT_MONO)

        for item in self.history:
            role = item.get("role")
            text = item.get("content", "")
            stamp = item.get("time", "")
            if role == "user":
                self.chat.insert(END, f"YOU   {stamp}\n", "you")
            else:
                self.chat.insert(END, f"GENIE   {stamp}\n", "genie")
            self.chat.insert(END, text + "\n", "body")
            self.chat.insert(END, "─" * 70 + "\n\n", "divider")

        if not self.history:
            self.chat.insert(END, "GENIE\n", "genie")
            self.chat.insert(
                END,
                "Your command center is ready. Start with a wish, a question, "
                "or a problem you want solved.\n\n",
                "body"
            )
            self.chat.insert(
                END,
                "GENIE can use the same workstation tools and model routing as the terminal assistant.",
                "body"
            )

        self.chat.see(END)
        self.chat.configure(state="disabled")

    def append_message(self, role, content):
        stamp = datetime.now().strftime("%H:%M")
        self.history.append({"role": role, "content": content, "time": stamp})
        self.history = self.history[-MAX_HISTORY:]
        self.save_history()
        self.render_history()

    def append_activity(self, text):
        self.activity_box.configure(state="normal")
        self.activity_box.insert(END, f"{datetime.now().strftime('%H:%M:%S')}  {text}\n")
        lines = int(self.activity_box.index("end-1c").split(".")[0])
        if lines > 12:
            self.activity_box.delete("1.0", "3.0")
        self.activity_box.see(END)
        self.activity_box.configure(state="disabled")

    def use_prompt(self, prompt):
        if self.busy:
            return
        self.input.delete("1.0", END)
        self.input.insert("1.0", prompt)
        self.input.focus_set()
        self.status.set("Wish prepared • send when ready")
        self.append_activity("wish prepared")

    def toggle_context(self):
        self.auto_context.set(not self.auto_context.get())
        self.context_button.configure(
            text="Context: ON" if self.auto_context.get() else "Context: OFF",
            fg=CREAM_DIM if self.auto_context.get() else DANGER
        )

    def export_chat(self):
        if not self.history:
            self.status.set("Nothing to export yet")
            return
        default = f"genie-chat-{datetime.now().strftime('%Y%m%d-%H%M')}.md"
        path = filedialog.asksaveasfilename(defaultextension=".md", initialfile=default, filetypes=[("Markdown", "*.md"), ("Text", "*.txt")])
        if not path:
            return
        lines = ["# GENIE conversation", "", f"Exported {datetime.now().strftime('%Y-%m-%d %H:%M')}", ""]
        for item in self.history:
            who = "You" if item.get("role") == "user" else "GENIE"
            lines.extend([f"**{who}**", "", item.get("content", ""), "", "---", ""])
        Path(path).write_text("\n".join(lines), encoding="utf-8")
        self.status.set(f"Exported {len(self.history)} messages")
        self.append_activity("chat exported")

    def copy_last_answer(self):
        if not self.last_answer:
            self.status.set("No answer to copy yet")
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(self.last_answer)
        self.status.set("Last answer copied")
        self.append_activity("answer copied")

    def new_chat(self):
        if self.busy:
            return
        self.history = []
        self.save_history()
        self.render_history()
        self.status.set("New wish • GENIE ready")
        self.append_activity("session cleared")

    def send_event(self, _event=None):
        self.send()
        return "break"

    def build_context(self):
        if not self.auto_context.get():
            return ""
        recent = self.history[-CONTEXT_TURNS:]
        return "\n".join(
            f"{'User' if x.get('role') == 'user' else 'GENIE'}: {x.get('content', '')}"
            for x in recent
        )

    def send_or_stop(self):
        if self.busy:
            genie.CANCEL_REQUESTED = True
            self.status.set("Stopping GENIE…")
            self.append_activity("stop requested")
            return
        self.send()

    def send(self):
        if self.busy:
            return
        prompt = self.input.get("1.0", END).strip()
        if not prompt:
            return
        context_history = None
        if self.auto_context.get():
            context_history = [
                {"role": x.get("role"), "content": x.get("content", "")}
                for x in self.history[-CONTEXT_TURNS:]
                if x.get("role") in ("user", "assistant")
            ]
        self.input.delete("1.0", END)
        self.append_message("user", prompt)
        self.busy = True
        genie.CANCEL_REQUESTED = False
        self.send_button.configure(state="normal", text="■  STOP", bg=self.ui_violet2, activebackground=self.ui_violet)
        self.status.set("GENIE is working your wish…")
        self.live_badge.configure(text="◉  GENIE WORKING", bg=GOLD_MUTE)
        self.append_activity("GENIE started")
        threading.Thread(target=self.run_genie, args=(prompt, context_history), daemon=True).start()

    def run_genie(self, prompt, context_history):
        task = prompt
        try:
            class LiveCapture(StringIO):
                def write(inner, chunk):
                    written = super().write(chunk)
                    for line in str(chunk).splitlines():
                        line = line.strip()
                        if line.startswith("[tool]") or line.startswith("[action]") or line.startswith("Model:"):
                            self.root.after(0, lambda msg=line: self.append_activity(msg))
                    return written
            captured = LiveCapture()
            genie.POLICY_APPROVED = bool(re.search(r"\b(i\s+approve|approve(?:d)?|explicitly\s+approve)\b", prompt, re.I))
            with redirect_stdout(captured):
                try:
                    import inspect
                    supports_history = "history" in inspect.signature(genie.run_agent).parameters
                except Exception:
                    supports_history = False
                if context_history and supports_history:
                    result = genie.run_agent(prompt, history=context_history)
                elif context_history:
                    context = "\n".join(f"{'User' if x['role'] == 'user' else 'GENIE'}: {x['content']}" for x in context_history)
                    result = genie.run_agent("Use this recent conversation as context.\n\n" + context + "\n\nCURRENT REQUEST:\n" + prompt)
                else:
                    result = genie.run_agent(task)
            genie.POLICY_APPROVED = False
            raw = captured.getvalue()
            answer = (result or raw).strip() or "GENIE did not return a usable response."
            tools_used = [line.strip() for line in raw.splitlines() if line.strip().startswith("[tool]")]
            self.root.after(0, lambda: self.finish(answer, None, tools_used))
        except Exception as exc:
            genie.POLICY_APPROVED = False
            message = str(exc)
            self.root.after(0, lambda: self.finish("", message, []))

    def finish(self, answer, error, tools_used):
        self.busy = False
        self.send_button.configure(state="normal", text="✦  SEND WISH", bg=self.ui_violet, activebackground=self.ui_violet2)
        self.live_badge.configure(text="●  GENIE ONLINE", bg="#10182A")
        if error:
            if error == "CANCELLED":
                self.append_message("assistant", "Wish cancelled — GENIE stopped before finishing.")
                self.status.set("Wish cancelled")
                self.append_activity("wish cancelled")
            else:
                self.append_message("assistant", f"GENIE error: {error}")
                self.status.set("GENIE paused • error")
                self.append_activity("request failed")
        else:
            self.last_answer = answer
            self.append_message("assistant", answer)
            self.status.set("GENIE ready • wish completed")
            self.speak_answer(answer)
            self.append_activity(f"completed • {len(tools_used)} tool calls")
            for tool in tools_used[-4:]:
                self.append_activity(tool.replace("[tool] ", ""))
        self.input.focus_set()

    def voice_request(self, audio_path):
        try:
            with open(audio_path, "rb") as fh:
                response = requests.post(
                    "http://127.0.0.1:8766/v1/audio/transcriptions",
                    files={"file": ("genie.wav", fh, "audio/wav")},
                    data={"model": "whisper-1", "language": "en"},
                    timeout=90,
                )
            response.raise_for_status()
            return str(response.json().get("text") or "").strip()
        except Exception as exc:
            self.append_activity(f"voice input failed: {exc}")
            return ""

    def capture_voice(self):
        if self.listening or self.busy:
            return
        self.listening = True
        self.status.set("Listening for your wish...")
        self.append_activity("microphone active")
        def worker():
            path = Path(tempfile.gettempdir()) / "genie-voice.wav"
            mic_started = False
            try:
                # GENIE voice is intentionally push-to-talk: activate the S22
                # microphone bridge only for this capture, then shut it down.
                mic = subprocess.run(
                    ["/home/justin/bin/s22-mic", "start"],
                    capture_output=True, text=True, timeout=15,
                )
                if mic.returncode != 0:
                    raise RuntimeError(mic.stderr.strip() or mic.stdout.strip() or "S22 microphone unavailable")
                mic_started = True
                self.root.after(0, lambda: self.append_activity("S22 microphone active"))
                proc = subprocess.run(
                    ["arecord", "-q", "-D", "default", "-d", "5",
                     "-f", "S16_LE", "-r", "16000", "-c", "1", str(path)],
                    capture_output=True, text=True, timeout=12,
                )
                if proc.returncode != 0:
                    raise RuntimeError(proc.stderr.strip() or "microphone capture failed")
                text = self.voice_request(path)
                self.root.after(0, lambda: self.finish_voice(text))
            except Exception as exc:
                self.root.after(0, lambda: self.finish_voice("", str(exc)))
            finally:
                if mic_started:
                    try:
                        subprocess.run(
                            ["/home/justin/bin/s22-mic", "stop"],
                            capture_output=True, text=True, timeout=15,
                        )
                    except Exception:
                        pass
                try: path.unlink()
                except Exception: pass
        threading.Thread(target=worker, daemon=True).start()

    def finish_voice(self, text, error=None):
        self.listening = False
        if error:
            self.status.set("Voice paused")
            self.append_activity("voice unavailable")
            return
        if not text:
            self.status.set("I didn't catch that")
            return
        self.input.delete("1.0", END)
        self.input.insert("1.0", text)
        self.status.set("Voice captured - GENIE ready")
        self.append_activity("voice captured")
        self.send()

    def speak_answer(self, text):
        if not self.voice_enabled.get() or not text:
            return
        def worker():
            path = Path(tempfile.gettempdir()) / "genie-speech.wav"
            try:
                r = requests.post(
                    "http://127.0.0.1:8766/v1/audio/speech",
                    json={"input": text[:6000], "voice": "default", "response_format": "wav"},
                    timeout=120,
                )
                r.raise_for_status()
                path.write_bytes(r.content)
                subprocess.run(["paplay", str(path)], capture_output=True, timeout=120)
            except Exception as exc:
                self.append_activity(f"voice output unavailable: {exc}")
            finally:
                try: path.unlink()
                except Exception: pass
        threading.Thread(target=worker, daemon=True).start()

    def quick_talk(self):
        self.open_genie_mode()
        self.root.after(350, self.capture_voice)

    def show_chat_workspace(self):
        if getattr(self, "live_workspace", None) is not None:
            try:
                self.live_workspace.pack_forget()
            except Exception:
                pass
        self.hero.pack(fill=X)
        self.chat_frame.pack(fill=BOTH, expand=True)
        self.composer.pack(fill=X, pady=(10, 9))
        self.bottom.pack(fill=X)
        self.input.focus_set()
        self.status.set("GENIE ready • wish workspace")
        self.append_activity("returned to chat workspace")

    def watch_genie(self):
        self.show_live_workspace()

    def show_live_workspace(self):
        if getattr(self, "live_workspace", None) is None:
            self.build_live_workspace()
        for widget in (self.hero, self.chat_frame, self.composer, self.bottom):
            widget.pack_forget()
        self.live_workspace.pack(fill=BOTH, expand=True)
        self.refresh_live_sites()
        self.start_live_capture_loop()
        self.status.set("GENIE LIVE • command center workspace")
        self.append_activity("live workspace opened")

    def build_live_workspace(self):
        self.live_workspace = Frame(
            self.center, bg=INK_DEEP, bd=0,
            highlightthickness=1, highlightbackground=BORDER
        )
        self.live_workspace.pack(fill=BOTH, expand=True)
        self.live_workspace.rowconfigure(1, weight=1)
        self.live_workspace.columnconfigure(1, weight=1)

        top = Frame(self.live_workspace, bg=INK_DEEP)
        top.grid(row=0, column=0, columnspan=2, sticky="ew")
        top.columnconfigure(1, weight=1)
        Label(
            top, text="✦  LIVE WORKSPACE", bg=INK_DEEP, fg=GOLD_BRIGHT,
            font=("Noto Serif", 22, "bold"), padx=16, pady=10
        ).grid(row=0, column=0, sticky="w")
        Label(
            top, text="WATCH GENIE • OPEN LOCAL SITES • FOLLOW THE ACTION",
            bg=INK_DEEP, fg=MUTED, font=FONT_BODY_BOLD
        ).grid(row=0, column=1, sticky="w")

        Button(
            top, text="CHAT", command=self.show_chat_workspace,
            bg=SURFACE_2, fg=CREAM, activebackground=SURFACE_WARM,
            activeforeground=GOLD_BRIGHT, relief="flat", bd=0,
            padx=12, pady=8, font=FONT_BODY_BOLD, cursor="hand2"
        ).grid(row=0, column=2, padx=(6, 8), pady=8)
        Button(
            top, text="REFRESH", command=self.refresh_live_sites,
            bg=GOLD, fg=INK, activebackground=GOLD_BRIGHT,
            relief="flat", bd=0, padx=12, pady=8,
            font=FONT_BODY_BOLD, cursor="hand2"
        ).grid(row=0, column=3, padx=(0, 14), pady=8)

        rail = Frame(
            self.live_workspace, bg=SURFACE, bd=0,
            highlightthickness=1, highlightbackground=BORDER
        )
        rail.grid(row=1, column=0, sticky="nsew", padx=(10, 8), pady=(0, 10))
        rail.configure(width=255)
        rail.grid_propagate(False)

        Label(
            rail, text="LOCAL SITES", bg=SURFACE, fg=TEAL,
            font=FONT_BODY_BOLD
        ).pack(anchor="w", padx=14, pady=(14, 4))
        Label(
            rail, text="Everything Genie can reach here.",
            bg=SURFACE, fg=MUTED, font=FONT_BODY
        ).pack(anchor="w", padx=14, pady=(0, 10))

        site_scroll = Scrollbar(rail, orient="vertical")
        site_scroll.pack(side=RIGHT, fill="y", padx=(0, 4), pady=(0, 10))
        self.live_sites_box = Canvas(
            rail, bg=SURFACE, highlightthickness=0, bd=0,
            yscrollcommand=site_scroll.set
        )
        self.live_sites_box.pack(fill=BOTH, expand=True, padx=(8, 0), pady=(0, 10))
        site_scroll.configure(command=self.live_sites_box.yview)
        self.live_sites_inner = Frame(self.live_sites_box, bg=SURFACE)
        self.live_sites_window = self.live_sites_box.create_window(
            (0, 0), window=self.live_sites_inner, anchor="nw"
        )
        self.live_sites_inner.bind(
            "<Configure>",
            lambda e: self.live_sites_box.configure(
                scrollregion=self.live_sites_box.bbox("all")
            )
        )
        self.live_sites_box.bind(
            "<Configure>",
            lambda e: self.live_sites_box.itemconfigure(
                self.live_sites_window, width=e.width
            )
        )

        viewer = Frame(
            self.live_workspace, bg=SURFACE, bd=0,
            highlightthickness=1, highlightbackground=BORDER
        )
        viewer.grid(row=1, column=1, sticky="nsew", padx=(0, 10), pady=(0, 10))
        viewer.rowconfigure(1, weight=1)
        viewer.columnconfigure(0, weight=1)

        self.live_target_label = Label(
            viewer, text="GENIE DESKTOP", bg=SURFACE, fg=CREAM,
            font=("Noto Serif", 19, "bold"), anchor="w"
        )
        self.live_target_label.grid(row=0, column=0, sticky="ew", padx=14, pady=(12, 0))
        self.live_target_status = Label(
            viewer, text="LIVE • desktop", bg=SURFACE, fg=TEAL,
            font=FONT_BODY_BOLD, anchor="w"
        )
        self.live_target_status.grid(row=0, column=1, sticky="e", padx=14, pady=(12, 0))

        screen = Frame(
            viewer, bg=INK_DEEP, bd=0,
            highlightthickness=1, highlightbackground=BORDER
        )
        screen.grid(row=1, column=0, columnspan=2, sticky="nsew", padx=10, pady=10)
        screen.rowconfigure(0, weight=1)
        screen.columnconfigure(0, weight=1)

        self.live_image = Label(
            screen, text="GENIE LIVE\n\nCapturing the desktop…",
            bg=INK_DEEP, fg=MUTED, justify="center", font=FONT_BODY
        )
        self.live_image.grid(row=0, column=0, sticky="nsew", padx=8, pady=8)

        self.live_capture_status = Label(
            viewer, text="GENIE LIVE • desktop view",
            bg=SURFACE, fg=MUTED, font=FONT_BODY_BOLD, anchor="w"
        )
        self.live_capture_status.grid(row=2, column=0, sticky="ew", padx=14, pady=(0, 6))

        Label(
            viewer, text="Click trail appears in LIVE SYSTEMS at right.",
            bg=SURFACE, fg=GOLD_DEEP, font=FONT_MONO, anchor="w"
        ).grid(row=2, column=1, sticky="e", padx=14, pady=(0, 6))

        self.live_sites = []
        self.live_target = {"kind": "desktop", "label": "GENIE DESKTOP", "url": None}
        self.live_capture_busy = False
        self.live_capture_photo = None
        self.live_poll_id = None
        self.live_refresh_id = None

    def discover_live_sites(self):
        labels = {
            8799: ("D-A-I Headquarters", "DAI"),
            8765: ("Agent-S Worker", "Agents"),
            8766: ("Voice Bridge", "Voice"),
            11435: ("Model Router", "AI"),
            11434: ("Ollama", "AI"),
            8384: ("Syncthing", "System"),
            631: ("CUPS", "System"),
            18789: ("OpenClaw", "Agents"),
            20241: ("Local Service", "Local"),
        }
        found = []
        try:
            proc = subprocess.run(
                ["ss", "-ltnH"], capture_output=True, text=True, timeout=5
            )
            for line in proc.stdout.splitlines():
                parts = line.split()
                if len(parts) < 4:
                    continue
                match = re.search(r":(\d+)$", parts[3])
                if not match:
                    continue
                port = int(match.group(1))
                if port <= 0 or port > 65535 or port == 22:
                    continue
                default_label, group = labels.get(
                    port, (f"Localhost {port}", "Local")
                )
                reachable = False
                title = ""
                scheme = "http"
                for candidate in (
                    f"http://127.0.0.1:{port}",
                    f"https://127.0.0.1:{port}",
                ):
                    try:
                        response = requests.get(
                            candidate + "/",
                            timeout=0.8,
                            allow_redirects=True,
                            verify=False,
                            headers={"User-Agent": "GENIE-Live-Workspace/2.0"},
                        )
                        if response.status_code < 500:
                            reachable = True
                            scheme = candidate.split(":", 1)[0]
                            title_match = re.search(
                                r"<title[^>]*>(.*?)</title>",
                                response.text[:12000],
                                re.I | re.S,
                            )
                            if title_match:
                                title = re.sub(
                                    r"\s+", " ", title_match.group(1)
                                ).strip()
                            break
                    except Exception:
                        continue
                # Known local services can be intentionally non-HTML/404 at /.
                if not reachable:
                    reachable = port in labels
                if not reachable:
                    continue
                found.append({
                    "port": port,
                    "url": f"{scheme}://127.0.0.1:{port}",
                    "label": title or default_label,
                    "group": group,
                })
        except Exception as exc:
            self.append_activity(f"site discovery failed: {exc}")
        seen = set()
        result = []
        for site in sorted(found, key=lambda x: x["port"]):
            if site["port"] in seen:
                continue
            seen.add(site["port"])
            result.append(site)
        return result

    def refresh_live_sites(self):
        if getattr(self, "live_sites_box", None) is None:
            return
        self.live_sites = self.discover_live_sites()
        for child in self.live_sites_inner.winfo_children():
            child.destroy()

        Button(
            self.live_sites_inner, text="◉  GENIE DESKTOP",
            command=lambda: self.select_live_target({
                "kind": "desktop", "label": "GENIE DESKTOP", "url": None
            }),
            bg=SURFACE_WARM if self.live_target.get("kind") == "desktop" else SURFACE_2,
            fg=GOLD_BRIGHT if self.live_target.get("kind") == "desktop" else CREAM,
            activebackground=SURFACE_WARM, activeforeground=GOLD_BRIGHT,
            relief="flat", bd=0, anchor="w", padx=10, pady=9,
            font=FONT_BODY_BOLD, cursor="hand2"
        ).pack(fill=X, pady=3)

        groups = {}
        for site in self.live_sites:
            groups.setdefault(site["group"], []).append(site)

        for group, sites in groups.items():
            Label(
                self.live_sites_inner, text=group.upper(), bg=SURFACE,
                fg=MUTED, font=FONT_MONO
            ).pack(anchor="w", padx=4, pady=(10, 4))
            for site in sites:
                Button(
                    self.live_sites_inner,
                    text=f"●  {site['label']}   :{site['port']}",
                    command=lambda s=site: self.select_live_target(s),
                    bg=(SURFACE_WARM
                        if self.live_target.get("url") == site["url"]
                        else SURFACE_2),
                    fg=(
                        GOLD_BRIGHT
                        if self.live_target.get("url") == site["url"]
                        else CREAM
                    ),
                    activebackground=SURFACE_WARM,
                    activeforeground=GOLD_BRIGHT,
                    relief="flat", bd=0, anchor="w",
                    padx=10, pady=8, font=FONT_BODY_BOLD,
                    cursor="hand2"
                ).pack(fill=X, pady=2)

        if not self.live_sites:
            Label(
                self.live_sites_inner,
                text="No HTTP localhost sites detected right now.\nGENIE DESKTOP remains live.",
                bg=SURFACE, fg=MUTED, justify="left",
                font=FONT_BODY, wraplength=210
            ).pack(anchor="w", padx=6, pady=14)

        if getattr(self, "live_refresh_id", None) is not None:
            try:
                self.root.after_cancel(self.live_refresh_id)
            except Exception:
                pass
        self.live_refresh_id = self.root.after(5000, self.refresh_live_sites)

    def select_live_target(self, target):
        self.live_target = dict(target)
        self.live_target_label.configure(text=target["label"])
        if target.get("url"):
            self.live_target_status.configure(text="LIVE SITE • localhost")
            self.live_capture_status.configure(
                text=f"GENIE LIVE • {target['url']}"
            )
            self.append_activity(
                f"local site selected: {target['label']}:{target.get('port', '')}"
            )
            def navigate():
                try:
                    result = genie.execute_tool(
                        "browser",
                        {
                            "action": "navigate",
                            "url": target["url"],
                            "headless": True,
                        },
                    )
                    ok = isinstance(result, dict) and result.get("ok", False)
                    self.root.after(
                        0,
                        lambda: self.live_capture_status.configure(
                            text=(
                                f"GENIE LIVE • {'connected' if ok else 'browser error'}"
                                f" • {target['url']}"
                            )
                        ),
                    )
                    self.root.after(0, self.refresh_live_capture)
                except Exception as exc:
                    self.root.after(
                        0,
                        lambda: self.live_capture_status.configure(
                            text=f"GENIE LIVE • navigation error: {exc}"
                        )
                    )
            threading.Thread(target=navigate, daemon=True).start()
        else:
            self.live_target_status.configure(text="LIVE • desktop")
            self.live_capture_status.configure(text="GENIE LIVE • desktop view")
            self.append_activity("GENIE desktop selected")
        self.refresh_live_capture()
        self.refresh_live_sites()

    def start_live_capture_loop(self):
        if getattr(self, "live_poll_id", None) is None:
            self.live_poll_id = self.root.after(150, self.live_capture_poll)

    def live_capture_poll(self):
        self.live_poll_id = None
        if getattr(self, "live_workspace", None) is None:
            return
        try:
            if not self.live_workspace.winfo_ismapped():
                return
        except Exception:
            return
        self.refresh_live_capture()
        self.live_poll_id = self.root.after(1100, self.live_capture_poll)

    def refresh_live_capture(self):
        if getattr(self, "live_capture_busy", False):
            return
        self.live_capture_busy = True
        target = dict(self.live_target)

        def worker():
            try:
                path = STATE_DIR / "genie-live-workspace.jpg"
                if target.get("kind") == "desktop":
                    result = genie.execute_tool(
                        "screenshot", {"path": str(path)}
                    )
                else:
                    result = genie.execute_tool(
                        "browser",
                        {
                            "action": "screenshot",
                            "path": str(path),
                            "full_page": False,
                        },
                    )
                if not isinstance(result, dict) or not result.get("ok", False):
                    raise RuntimeError(
                        (result or {}).get("error", "capture failed")
                    )
                img = Image.open(path).convert("RGB")
                img.thumbnail((900, 600))
                buf = BytesIO()
                img.save(buf, format="PNG")
                image_bytes = buf.getvalue()

                def show():
                    if getattr(self, "live_image", None) is not None:
                        photo = ImageTk.PhotoImage(
                            Image.open(BytesIO(image_bytes)).convert("RGB")
                        )
                        self.live_capture_photo = photo
                        self.live_image.configure(image=photo, text="")
                        self.live_capture_status.configure(
                            text=(
                                "GENIE LIVE • desktop view"
                                if target.get("kind") == "desktop"
                                else f"GENIE LIVE • {target.get('url')}"
                            )
                        )

                self.root.after(0, show)
            except Exception as exc:
                self.root.after(
                    0,
                    lambda: self.live_capture_status.configure(
                        text=f"GENIE LIVE • waiting for capture ({exc})"
                    ),
                )
            finally:
                self.live_capture_busy = False

        threading.Thread(target=worker, daemon=True).start()

    def see_screen(self):
        self.status.set("GENIE is looking at your screen...")
        self.append_activity("screen capture requested")
        def worker():
            path = Path(tempfile.gettempdir()) / "genie-live-screen.png"
            try:
                if shutil.which("gnome-screenshot"):
                    cmd=["gnome-screenshot","-f",str(path)]
                else:
                    cmd=["scrot",str(path)]
                subprocess.run(cmd, check=True, capture_output=True, text=True, timeout=30)
                def show_preview():
                    if self.mode_preview is not None:
                        try:
                            img=Image.open(path).convert("RGB")
                            img.thumbnail((430,300))
                            self.mode_photo=ImageTk.PhotoImage(img)
                            self.mode_preview.configure(image=self.mode_photo, text="")
                        except Exception:
                            pass
                self.root.after(0, show_preview)
                answer=genie.run_agent(
                    "Inspect my current desktop screenshot. The screenshot is at "
                    + str(path) +
                    ". Describe exactly what is visible, identify relevant windows "
                    "or errors, and tell me what action would help next."
                )
                self.root.after(0, lambda: self.finish(answer or "Screen inspected.", None, []))
            except Exception as exc:
                self.root.after(0, lambda: self.finish("", str(exc), []))
        threading.Thread(target=worker, daemon=True).start()

    def run_agent_goal(self, goal):
        goal=goal.strip()
        if not goal or self.busy:
            return
        self.busy=True
        self.status.set("GENIE agent is working...")
        self.append_activity("agent goal started")
        def worker():
            try:
                answer=genie.run_agent(
                    "Complete this goal as an autonomous GENIE mission. Follow the closed loop "
                    "OBSERVE -> DECIDE -> EXECUTE -> VERIFY -> RECOVER -> REPORT. Inspect live "
                    "state first, act directly with available browser/workstation/device tools, "
                    "verify every meaningful change, and if verification fails diagnose and retry "
                    "with a bounded recovery. Do not merely give instructions. Return a concise "
                    "result stating what changed and how it was verified. Goal: " + goal
                )
                self.root.after(0, lambda: self.finish(answer or "Agent completed.", None, []))
            except Exception as exc:
                self.root.after(0, lambda: self.finish("", str(exc), []))
        threading.Thread(target=worker, daemon=True).start()

    def open_genie_mode(self):
        if self.mode_window is not None and self.mode_window.winfo_exists():
            self.mode_window.lift()
            return
        win=__import__("tkinter").Toplevel(self.root)
        self.mode_window=win
        win.title("GENIE - Genie Mode")
        win.geometry("920x680")
        win.minsize(760,560)
        win.configure(bg=INK)
        win.protocol("WM_DELETE_WINDOW", lambda: (setattr(self,"mode_window",None), win.destroy()))
        top=Frame(win,bg=INK_DEEP,highlightthickness=1,highlightbackground=BORDER)
        top.pack(fill=X,padx=16,pady=16)
        Label(top,text="GENIE MODE",bg=INK_DEEP,fg=GOLD_BRIGHT,font=("Noto Serif",22,"bold")).pack(side=LEFT,padx=18,pady=12)
        Label(top,text="VOICE  SCREEN  AGENT",bg=INK_DEEP,fg=MUTED,font=FONT_BODY_BOLD).pack(side=LEFT)
        body=Frame(win,bg=INK); body.pack(fill=BOTH,expand=True,padx=16,pady=(0,16))
        left=Frame(body,bg=SURFACE,highlightthickness=1,highlightbackground=BORDER)
        left.pack(side=LEFT,fill=BOTH,expand=True,padx=(0,8))
        orb=Canvas(left,width=260,height=250,bg=SURFACE,highlightthickness=0); orb.pack(pady=10)
        self.mode_status=Label(left,text="Ready when you are.",bg=SURFACE,fg=CREAM,font=("Noto Serif",19,"bold"))
        self.mode_status.pack(pady=(0,12))
        buttons=Frame(left,bg=SURFACE); buttons.pack(pady=6)
        Button(buttons,text="TALK",command=self.capture_voice,bg=GOLD,fg=INK,activebackground=GOLD_BRIGHT,relief="flat",bd=0,padx=18,pady=11,font=FONT_BODY_BOLD,cursor="hand2").pack(side=LEFT,padx=4)
        Button(buttons,text="SEE SCREEN",command=self.see_screen,bg=SURFACE_2,fg=CREAM,activebackground=SURFACE_WARM,relief="flat",bd=0,padx=14,pady=11,font=FONT_BODY_BOLD,cursor="hand2").pack(side=LEFT,padx=4)
        Button(buttons,text="CLOSE",command=win.destroy,bg=INK_DEEP,fg=MUTED,activebackground=INK,relief="flat",bd=0,padx=14,pady=11,font=FONT_BODY_BOLD,cursor="hand2").pack(side=LEFT,padx=4)
        right=Frame(body,bg=SURFACE,highlightthickness=1,highlightbackground=BORDER)
        right.pack(side=RIGHT,fill=BOTH,expand=True,padx=(8,0))
        self.mode_preview=Label(right,text="Screen preview appears here.",bg=INK_DEEP,fg=MUTED,font=FONT_BODY,pady=8)
        self.mode_preview.pack(fill=X,padx=10,pady=10)
        Label(right,text="AGENT GOAL",bg=SURFACE,fg=TEAL,font=FONT_BODY_BOLD).pack(anchor="w",padx=14,pady=(6,5))
        goal=Text(right,height=3,wrap="word",bg=INK_DEEP,fg=CREAM,insertbackground=GOLD_BRIGHT,relief="flat",bd=0,padx=10,pady=8,font=FONT_BODY)
        goal.pack(fill=X,padx=10,pady=(0,6))
        Button(right,text="RUN WITH GENIE HANDS",command=lambda:self.run_agent_goal(goal.get("1.0",END)),bg=GOLD_MUTE,fg=GOLD_PALE,activebackground=GOLD_DEEP,relief="flat",bd=0,padx=12,pady=9,font=FONT_BODY_BOLD,cursor="hand2").pack(anchor="e",padx=10)
        Label(right,text="GENIE uses the same local tools, browser control, devices, and model routing as the main assistant.",bg=SURFACE,fg=CREAM_DIM,font=FONT_BODY,wraplength=360,justify=LEFT).pack(fill=X,padx=14,pady=14)
        self._mode_orb=orb
        def animate_mode():
            if self.mode_window is None or not self.mode_window.winfo_exists():
                return
            orb.delete("all")
            cx,cy=130,115
            pulse=1+0.07*math.sin(self.phase)
            for radius,color in ((74,GOLD_MUTE),(61,GOLD_DEEP),(49,GOLD),(37,GOLD_BRIGHT)):
                rr=radius*pulse
                orb.create_oval(cx-rr,cy-rr,cx+rr,cy+rr,outline=color,width=2)
            orb.create_oval(cx-22,cy-22,cx+22,cy+22,fill=GOLD_BRIGHT,outline=CREAM,width=2)
            orb.create_oval(cx-9,cy-9,cx+9,cy+9,fill=WHITE,outline="")
            if self.listening: self.mode_status.configure(text="Listening...")
            elif self.busy: self.mode_status.configure(text="GENIE is working...")
            else: self.mode_status.configure(text="Ready when you are.")
            self.phase+=0.12
            win.after(70,animate_mode)
        animate_mode()

    def watch_genie_window(self):
        if getattr(self, "watch_window", None) is not None:
            try:
                if self.watch_window.winfo_exists():
                    self.watch_window.lift()
                    return
            except Exception:
                pass

        tk = __import__("tkinter")
        win = tk.Toplevel(self.root)
        self.watch_window = win
        win.title("GENIE - Live")
        win.geometry("1040x760")
        win.minsize(820,620)
        win.configure(bg=INK)
        win.protocol("WM_DELETE_WINDOW", lambda: self.close_watch_window())

        top=Frame(win,bg=INK_DEEP,highlightthickness=1,highlightbackground=BORDER)
        top.pack(fill=X,padx=14,pady=14)
        Label(top,text="GENIE - LIVE",bg=INK_DEEP,fg=GOLD_BRIGHT,
              font=("Noto Serif",22,"bold")).pack(side=LEFT,padx=16,pady=10)
        Label(top,text="WATCH THE BROWSER AS GENIE WORKS",
              bg=INK_DEEP,fg=MUTED,font=FONT_BODY_BOLD).pack(side=LEFT)

        controls=Frame(win,bg=INK); controls.pack(fill=X,padx=14,pady=(0,10))
        self.watch_url=StringVar(value="http://127.0.0.1:8799")
        entry=__import__("tkinter").Entry(controls,textvariable=self.watch_url,
             bg=INK_DEEP,fg=CREAM,insertbackground=GOLD_BRIGHT,
             relief="flat",bd=0,font=FONT_BODY)
        entry.pack(side=LEFT,fill=X,expand=True,padx=(0,7),ipady=8)
        Button(controls,text="SHOW SITE",command=self.watch_show_site,
               bg=GOLD,fg=INK,activebackground=GOLD_BRIGHT,
               relief="flat",bd=0,padx=12,pady=8,font=FONT_BODY_BOLD,
               cursor="hand2").pack(side=LEFT,padx=3)
        Button(controls,text="REFRESH",command=self.watch_refresh,
               bg=SURFACE_2,fg=CREAM,activebackground=SURFACE_WARM,
               relief="flat",bd=0,padx=12,pady=8,font=FONT_BODY_BOLD,
               cursor="hand2").pack(side=LEFT,padx=3)

        frame=Frame(win,bg=SURFACE,highlightthickness=1,highlightbackground=BORDER)
        frame.pack(fill=BOTH,expand=True,padx=14,pady=(0,14))
        self.watch_image=Label(frame,text="Launching GENIE visible browser...",
                               bg=INK_DEEP,fg=MUTED,font=FONT_BODY)
        self.watch_image.pack(fill=BOTH,expand=True,padx=8,pady=8)

        self.watch_status=Label(win,text="GENIE LIVE - browser will remain visible while tools work",
                               bg=INK,fg=TEAL,font=FONT_BODY_BOLD)
        self.watch_status.pack(fill=X,padx=16,pady=(0,10))

        threading.Thread(target=self.watch_start_browser,daemon=True).start()
        self.watch_poll()

    def close_watch_window(self):
        try:
            self.watch_window.destroy()
        except Exception:
            pass
        self.watch_window=None
        self.watch_image=None

    def watch_start_browser(self):
        try:
            result=genie.execute_tool("browser", {"action":"start","headless":True})
            self.root.after(0, lambda: self.append_activity("visible browser ready"))
            if isinstance(result,dict) and not result.get("ok",True):
                self.root.after(0, lambda: self.watch_status.configure(
                    text="GENIE LIVE - browser start reported an error"
                ))
        except Exception as exc:
            self.root.after(0, lambda: self.watch_status.configure(
                text=f"GENIE LIVE - browser error: {exc}"
            ))

    def watch_show_site(self):
        url=self.watch_url.get().strip()
        if not url:
            return
        self.watch_status.configure(text="GENIE LIVE - opening "+url)
        def worker():
            try:
                result=genie.execute_tool("browser", {"action":"navigate","url":url,"headless":True})
                msg="site opened"
                if isinstance(result,dict) and not result.get("ok",True):
                    msg="site error"
                self.root.after(0, lambda: self.append_activity("visible browser: "+msg))
                self.root.after(0, self.watch_refresh)
            except Exception as exc:
                self.root.after(0, lambda: self.watch_status.configure(
                    text=f"GENIE LIVE - navigation error: {exc}"
                ))
        threading.Thread(target=worker,daemon=True).start()

    def watch_refresh(self):
        if getattr(self, "watch_window", None) is None:
            return
        def worker():
            path=STATE_DIR / "genie-live-browser.jpg"
            try:
                result=genie.execute_tool("browser", {
                    "action":"screenshot","path":str(path),"full_page":False
                })
                if not isinstance(result,dict) or not result.get("ok"):
                    raise RuntimeError((result or {}).get("error","browser screenshot failed"))
                img=Image.open(path).convert("RGB")
                img.thumbnail((980,610))
                buf=BytesIO()
                img.save(buf, format="PNG")
                image_bytes=buf.getvalue()
                def show():
                    if getattr(self,"watch_image",None) is not None:
                        photo=ImageTk.PhotoImage(
                            Image.open(BytesIO(image_bytes)).convert("RGB")
                        )
                        self.watch_image.configure(image=photo,text="")
                        self.watch_image.image=photo
                        self.watch_status.configure(text="GENIE LIVE - browser view updated")
                self.root.after(0,show)
            except Exception as exc:
                self.root.after(0,lambda: self.watch_status.configure(
                    text=f"GENIE LIVE - waiting for browser ({exc})"
                ))
        threading.Thread(target=worker,daemon=True).start()

    def watch_poll(self):
        if getattr(self,"watch_window",None) is None:
            return
        try:
            if not self.watch_window.winfo_exists():
                self.watch_window=None
                return
        except Exception:
            self.watch_window=None
            return
        self.watch_refresh()
        self.root.after(850,self.watch_poll)

    def animate(self):
        if not hasattr(self,"orb"): return
        self.phase += .08
        if hasattr(self,"clock_label"):
            self.clock_label.configure(text=datetime.now().strftime("%I:%M:%S %p  ·  %a, %b %d"))
        self._draw_mascot()
        self.root.after(55,self.animate)

    def close(self):
        self.save_history()
        self.root.destroy()

def main():
    root = Tk()
    GenieApp(root)
    root.mainloop()

if __name__ == "__main__":
    main()
