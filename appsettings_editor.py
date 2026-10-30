"""
AppSettings Editor (enhanced)
=============================

A native Windows desktop app (Tkinter -- no third-party deps) for finding,
editing and comparing appsettings*.json files across a directory tree.

Features
--------
1. JSON "LSP"-style editing
   - Syntax highlighting (keys / strings / numbers / literals / punctuation)
   - Live diagnostics: parse errors reported with line:col, error line
     highlighted, red line number in the gutter, F8 jumps to it
   - Bracket match highlighting
   - Auto-indent on Enter, auto-close brackets and quotes
   - Auto-format (pretty-print) and Minify
   - Line-number gutter, Ln/Col readout
   - Zoom in / out / reset (Ctrl +, Ctrl -, Ctrl 0, Ctrl+MouseWheel)

2. Tree-view sidebar
   - Directory hierarchy of every match under the root
   - Toggleable filter: ALL appsettings*.json  <->  exact "appsettings.json"
   - Live counters (shown / exact / total)

3. Compare
   - Compare the open file against any JSON file from any other directory
   - Side-by-side aligned line diff with synced scrolling
   - Semantic "key differences" view: flattens both documents to config
     paths and shows added / removed / changed settings

Run:
    python appsettings_editor.py

Requires Python 3.8+ with Tkinter (standard on python.org Windows installs).
"""

import os
import re
import json
import difflib
import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox, ttk


# --------------------------------------------------------------------------
# Theme / constants
# --------------------------------------------------------------------------

COLORS = {
    "bg":          "#ffffff",
    "fg":          "#1f1f1f",
    "gutter_bg":   "#f3f3f3",
    "gutter_fg":   "#8a8a8a",
    "gutter_cur":  "#1f1f1f",
    "gutter_err":  "#d32f2f",
    "cur_line":    "#f5f8ff",
    "key":         "#0451a5",
    "string":      "#a31515",
    "number":      "#098658",
    "keyword":     "#0000c0",
    "punct":       "#555555",
    "error_bg":    "#ffe5e5",
    "bracket":     "#bfe3ff",
    "diff_add":    "#e3f7e3",
    "diff_del":    "#fde3e3",
    "diff_chg":    "#fff4d6",
    "diff_pad":    "#f0f0f0",
}

MIN_FONT, MAX_FONT, DEFAULT_FONT = 7, 32, 10
MAX_HIGHLIGHT_CHARS = 400_000  # skip colouring absurdly large files

SKIP_DIRS = {".git", ".vs", ".idea", "node_modules", "bin", "obj", "__pycache__"}

JSON_TOKEN_RE = re.compile(
    r"""
      (?P<string>"(?:\\.|[^"\\])*")
    | (?P<number>-?\b\d+(?:\.\d+)?(?:[eE][+-]?\d+)?\b)
    | (?P<keyword>\b(?:true|false|null)\b)
    | (?P<punct>[{}\[\],:])
    """,
    re.VERBOSE,
)


# --------------------------------------------------------------------------
# Helpers
# --------------------------------------------------------------------------

def flatten_json(obj, prefix=""):
    """Flatten a JSON document into {config:path: scalar} (ASP.NET style keys)."""
    out = {}
    if isinstance(obj, dict):
        if not obj and prefix:
            out[prefix] = "{}"
        for k, v in obj.items():
            key = f"{prefix}:{k}" if prefix else str(k)
            out.update(flatten_json(v, key))
    elif isinstance(obj, list):
        if not obj and prefix:
            out[prefix] = "[]"
        for i, v in enumerate(obj):
            out.update(flatten_json(v, f"{prefix}[{i}]"))
    else:
        out[prefix or "(root)"] = obj
    return out


def fmt_value(v):
    if v is None:
        return "null"
    if isinstance(v, bool):
        return "true" if v else "false"
    return str(v)


# --------------------------------------------------------------------------
# Text widget that reports content/cursor changes
# --------------------------------------------------------------------------

class EventedText(tk.Text):
    """tk.Text that fires <<Change>> whenever content scrolls or the cursor moves."""

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        self._orig = self._w + "_orig"
        self.tk.call("rename", self._w, self._orig)
        self.tk.createcommand(self._w, self._proxy)

    def _proxy(self, *args):
        try:
            result = self.tk.call((self._orig,) + args)
        except tk.TclError:
            return None
        if (
            args[0] in ("insert", "replace", "delete")
            or args[0:3] == ("mark", "set", "insert")
            or args[0:2] in (("xview", "moveto"), ("yview", "moveto"),
                             ("xview", "scroll"), ("yview", "scroll"))
        ):
            self.event_generate("<<Change>>", when="tail")
        return result


# --------------------------------------------------------------------------
# Line-number gutter
# --------------------------------------------------------------------------

class LineGutter(tk.Canvas):
    def __init__(self, master, textwidget, font, **kwargs):
        super().__init__(master, bg=COLORS["gutter_bg"], highlightthickness=0,
                         width=48, **kwargs)
        self.text = textwidget
        self.font = font
        self.error_lines = set()

    def redraw(self, *_):
        self.delete("all")
        if self.text is None:
            return
        try:
            cur_line = int(self.text.index("insert").split(".")[0])
        except tk.TclError:
            cur_line = -1

        i = self.text.index("@0,0")
        while True:
            info = self.text.dlineinfo(i)
            if info is None:
                break
            y = info[1]
            lineno = int(str(i).split(".")[0])
            if lineno in self.error_lines:
                fill = COLORS["gutter_err"]
            elif lineno == cur_line:
                fill = COLORS["gutter_cur"]
            else:
                fill = COLORS["gutter_fg"]
            self.create_text(self.winfo_width() - 6, y, anchor="ne",
                             text=str(lineno), fill=fill, font=self.font)
            nxt = self.text.index(f"{i}+1line")
            if nxt == i:
                break
            i = nxt


# --------------------------------------------------------------------------
# JSON editor pane (text + gutter + language features)
# --------------------------------------------------------------------------

class JsonEditor(ttk.Frame):
    def __init__(self, master, on_status=None, on_dirty=None):
        super().__init__(master)
        self.on_status = on_status or (lambda *_: None)
        self.on_dirty = on_dirty or (lambda *_: None)

        self.font_size = DEFAULT_FONT
        self.font = tkfont.Font(family="Consolas", size=self.font_size)
        self._analysis_job = None
        self._suppress_dirty = False
        self._position = (1, 1)
        self.dirty = False
        self.last_error = None  # (lineno, colno, msg) or None

        self.gutter = LineGutter(self, None, self.font)
        self.text = EventedText(
            self, wrap="none", undo=True, maxundo=-1, autoseparators=True,
            font=self.font, bg=COLORS["bg"], fg=COLORS["fg"],
            insertbackground=COLORS["fg"], selectbackground="#cfe3ff",
            selectforeground=COLORS["fg"],
            borderwidth=0, highlightthickness=0, padx=6,
        )
        self.gutter.text = self.text

        ysb = ttk.Scrollbar(self, orient="vertical", command=self._on_yscroll)
        xsb = ttk.Scrollbar(self, orient="horizontal", command=self.text.xview)
        self.text.configure(yscrollcommand=ysb.set, xscrollcommand=xsb.set)

        self.gutter.grid(row=0, column=0, sticky="ns")
        self.text.grid(row=0, column=1, sticky="nsew")
        ysb.grid(row=0, column=2, sticky="ns")
        xsb.grid(row=1, column=1, sticky="ew")
        self.rowconfigure(0, weight=1)
        self.columnconfigure(1, weight=1)

        self._configure_tags()
        self._bind_events()

    # ---------------------------------------------------------------- tags
    def _configure_tags(self):
        t = self.text
        t.tag_configure("cur_line", background=COLORS["cur_line"])
        t.tag_configure("tok_key", foreground=COLORS["key"])
        t.tag_configure("tok_string", foreground=COLORS["string"])
        t.tag_configure("tok_number", foreground=COLORS["number"])
        t.tag_configure("tok_keyword", foreground=COLORS["keyword"])
        t.tag_configure("tok_punct", foreground=COLORS["punct"])
        t.tag_configure("error_line", background=COLORS["error_bg"])
        t.tag_configure("bracket", background=COLORS["bracket"])
        t.tag_lower("cur_line")
        t.tag_raise("sel")

    # -------------------------------------------------------------- events
    def _bind_events(self):
        t = self.text
        t.bind("<<Change>>", self._on_change)
        t.bind("<Configure>", lambda e: self.gutter.redraw())
        for ch in ("{", "[", '"'):
            t.bind(ch, self._on_open_pair)
        for ch in ("}", "]"):
            t.bind(ch, self._on_close_pair)
        t.bind("<Return>", self._on_return)
        t.bind("<BackSpace>", self._on_backspace)
        t.bind("<Control-plus>", lambda e: (self.zoom(+1), "break")[1])
        t.bind("<Control-equal>", lambda e: (self.zoom(+1), "break")[1])
        t.bind("<Control-minus>", lambda e: (self.zoom(-1), "break")[1])
        t.bind("<Control-0>", lambda e: (self.zoom_reset(), "break")[1])
        t.bind("<Control-MouseWheel>", self._on_ctrl_wheel)

    def _on_yscroll(self, *args):
        self.text.yview(*args)
        self.gutter.redraw()

    def _on_change(self, _event=None):
        self.gutter.redraw()
        self._highlight_current_line()
        self._update_position()
        if not self._suppress_dirty and not self.dirty:
            self.dirty = True
            self.on_dirty(True)
        self._schedule_analysis()

    def _on_ctrl_wheel(self, event):
        self.zoom(+1 if event.delta > 0 else -1)
        return "break"

    # ---------------------------------------------------------- "language"
    def _schedule_analysis(self, _event=None):
        if self._analysis_job:
            self.after_cancel(self._analysis_job)
        self._analysis_job = self.after(130, self.analyze)

    def analyze(self):
        self._analysis_job = None
        self.highlight_syntax()
        self.validate()
        self._match_brackets()

    def highlight_syntax(self):
        t = self.text
        content = t.get("1.0", "end-1c")
        for tag in ("tok_key", "tok_string", "tok_number", "tok_keyword", "tok_punct"):
            t.tag_remove(tag, "1.0", "end")
        if len(content) > MAX_HIGHLIGHT_CHARS:
            return
        for m in JSON_TOKEN_RE.finditer(content):
            kind = m.lastgroup
            start, end = m.start(), m.end()
            if kind == "string":
                rest = content[end:end + 40].lstrip()
                tag = "tok_key" if rest.startswith(":") else "tok_string"
            else:
                tag = f"tok_{kind}"
            t.tag_add(tag, f"1.0+{start}c", f"1.0+{end}c")

    def validate(self):
        t = self.text
        t.tag_remove("error_line", "1.0", "end")
        self.gutter.error_lines = set()
        content = t.get("1.0", "end-1c")

        if not content.strip():
            self.last_error = None
            self.on_status("empty", "Empty")
            self.gutter.redraw()
            return True

        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            self.last_error = (e.lineno, e.colno, e.msg)
            t.tag_add("error_line", f"{e.lineno}.0", f"{e.lineno}.end")
            self.gutter.error_lines = {e.lineno}
            self.on_status("error", f"Line {e.lineno}, col {e.colno}: {e.msg}")
            self.gutter.redraw()
            return False

        self.last_error = None
        self.on_status("ok", "Valid JSON")
        self.gutter.redraw()
        return True

    def format_json(self, indent=2):
        content = self.text.get("1.0", "end-1c")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            messagebox.showerror(
                "Cannot format",
                f"Fix the JSON first.\n\nLine {e.lineno}, col {e.colno}: {e.msg}")
            return False
        self._replace_all(json.dumps(data, indent=indent, ensure_ascii=False))
        return True

    def minify_json(self):
        content = self.text.get("1.0", "end-1c")
        try:
            data = json.loads(content)
        except json.JSONDecodeError as e:
            messagebox.showerror(
                "Cannot minify",
                f"Fix the JSON first.\n\nLine {e.lineno}, col {e.colno}: {e.msg}")
            return False
        self._replace_all(json.dumps(data, separators=(",", ":"), ensure_ascii=False))
        return True

    def _replace_all(self, new_text):
        pos = self.text.index("insert")
        self.text.replace("1.0", "end-1c", new_text)
        try:
            self.text.mark_set("insert", pos)
            self.text.see("insert")
        except tk.TclError:
            pass
        self.analyze()

    # ---------------------------------------------------------- edit helpers
    def _on_return(self, _event):
        t = self.text
        before = t.get("insert linestart", "insert")
        indent = re.match(r"[ \t]*", before).group(0)
        after = t.get("insert", "insert lineend").lstrip()
        extra = "  " if before.rstrip().endswith(("{", "[")) else ""

        if extra and after[:1] in ("}", "]"):
            t.insert("insert", f"\n{indent}{extra}\n{indent}")
            t.mark_set("insert", "insert-1line lineend")
        else:
            t.insert("insert", f"\n{indent}{extra}")
        t.see("insert")
        return "break"

    def _on_open_pair(self, event):
        pairs = {"{": "}", "[": "]", '"': '"'}
        ch = event.char
        if ch not in pairs:
            return None
        t = self.text
        if t.tag_ranges("sel"):
            return None
        nxt = t.get("insert", "insert+1c")
        if ch == '"':
            prev = t.get("insert-1c", "insert")
            if nxt == '"':
                t.mark_set("insert", "insert+1c")
                return "break"
            if prev == "\\" or (nxt and nxt.isalnum()):
                return None
        t.insert("insert", ch + pairs[ch])
        t.mark_set("insert", "insert-1c")
        return "break"

    def _on_close_pair(self, event):
        t = self.text
        if t.get("insert", "insert+1c") == event.char:
            t.mark_set("insert", "insert+1c")
            return "break"
        return None

    def _on_backspace(self, _event):
        t = self.text
        if t.tag_ranges("sel"):
            return None
        pair = t.get("insert-1c", "insert") + t.get("insert", "insert+1c")
        if pair in ("{}", "[]", '""'):
            t.delete("insert-1c", "insert+1c")
            return "break"
        return None

    def _match_brackets(self):
        t = self.text
        t.tag_remove("bracket", "1.0", "end")
        for probe in ("insert-1c", "insert"):
            try:
                ch = t.get(probe, f"{probe}+1c")
            except tk.TclError:
                continue
            if ch in "{[":
                other = self._find_match(probe, ch, forward=True)
            elif ch in "}]":
                other = self._find_match(probe, ch, forward=False)
            else:
                continue
            if other:
                t.tag_add("bracket", probe, f"{probe}+1c")
                t.tag_add("bracket", other, f"{other}+1c")
                return

    def _find_match(self, index, ch, forward):
        t = self.text
        pairs = {"{": "}", "[": "]", "}": "{", "]": "["}
        target = pairs[ch]
        depth = 0
        step = "+1c" if forward else "-1c"
        cur = t.index(index)
        for _ in range(20000):
            c = t.get(cur, f"{cur}+1c")
            if not c and forward:
                return None
            if c == ch:
                depth += 1
            elif c == target:
                depth -= 1
                if depth == 0:
                    return cur
            nxt = t.index(f"{cur}{step}")
            if nxt == cur:
                return None
            cur = nxt
        return None

    def _highlight_current_line(self):
        t = self.text
        t.tag_remove("cur_line", "1.0", "end")
        t.tag_add("cur_line", "insert linestart", "insert lineend+1c")

    def _update_position(self):
        try:
            line, col = self.text.index("insert").split(".")
            self._position = (int(line), int(col) + 1)
        except tk.TclError:
            self._position = (1, 1)

    @property
    def position(self):
        return self._position

    # ------------------------------------------------------------- zooming
    def zoom(self, delta):
        new = max(MIN_FONT, min(MAX_FONT, self.font_size + delta))
        if new == self.font_size:
            return
        self.font_size = new
        self.font.configure(size=new)
        self.gutter.configure(width=max(40, int(new * 4.0)))
        self.after(20, self.gutter.redraw)

    def zoom_reset(self):
        self.font_size = DEFAULT_FONT
        self.font.configure(size=DEFAULT_FONT)
        self.gutter.configure(width=48)
        self.after(20, self.gutter.redraw)

    def set_wrap(self, enabled):
        self.text.configure(wrap="word" if enabled else "none")
        self.gutter.redraw()

    # ------------------------------------------------------------- content
    def set_content(self, content):
        self._suppress_dirty = True
        self.text.replace("1.0", "end-1c", content)
        self.text.edit_reset()
        self.text.mark_set("insert", "1.0")
        self.text.see("1.0")
        self._suppress_dirty = False
        self.dirty = False
        self.on_dirty(False)
        self.analyze()

    def get_content(self):
        return self.text.get("1.0", "end-1c")

    def mark_clean(self):
        self.dirty = False
        self.on_dirty(False)

    def goto_error(self):
        if not self.last_error:
            return
        line, col, _ = self.last_error
        self.text.mark_set("insert", f"{line}.{max(0, col - 1)}")
        self.text.see("insert")
        self.text.focus_set()


# --------------------------------------------------------------------------
# Compare window
# --------------------------------------------------------------------------

class CompareWindow(tk.Toplevel):
    def __init__(self, master, left_name, left_text, right_name, right_text):
        super().__init__(master)
        self.title("Compare")
        self.geometry("1250x720")
        self.left_name, self.left_text = left_name, left_text
        self.right_name, self.right_text = right_name, right_text

        self.normalize = tk.BooleanVar(value=True)
        self.only_diff = tk.BooleanVar(value=True)
        self.font = tkfont.Font(family="Consolas", size=DEFAULT_FONT)
        self._syncing = False

        self._build()
        self.refresh()

    # ----------------------------------------------------------------- UI
    def _build(self):
        bar = ttk.Frame(self, padding=6)
        bar.pack(side="top", fill="x")
        ttk.Checkbutton(bar, text="Normalize (pretty-print + sort keys before diffing)",
                        variable=self.normalize, command=self.refresh).pack(side="left")
        ttk.Checkbutton(bar, text="Only differences (key view)",
                        variable=self.only_diff,
                        command=self._fill_keys).pack(side="left", padx=12)
        ttk.Button(bar, text="Zoom -", width=8,
                   command=lambda: self._zoom(-1)).pack(side="right", padx=2)
        ttk.Button(bar, text="Zoom +", width=8,
                   command=lambda: self._zoom(+1)).pack(side="right", padx=2)

        nb = ttk.Notebook(self)
        nb.pack(side="top", fill="both", expand=True, padx=6, pady=(0, 6))

        # --- side by side ---
        sbs = ttk.Frame(nb)
        nb.add(sbs, text="Side by side")

        header = ttk.Frame(sbs)
        header.pack(side="top", fill="x")
        ttk.Label(header, text="LEFT (open file):  " + self.left_name, anchor="w",
                  font=("Segoe UI", 8, "bold")).pack(side="left", expand=True, fill="x", padx=4)
        ttk.Label(header, text="RIGHT (compared):  " + self.right_name, anchor="w",
                  font=("Segoe UI", 8, "bold")).pack(side="left", expand=True, fill="x", padx=4)

        self.diff_status = ttk.Label(sbs, anchor="w", padding=4, relief="sunken")
        self.diff_status.pack(side="bottom", fill="x")

        body = ttk.Frame(sbs)
        body.pack(side="top", fill="both", expand=True)

        self.vsb = ttk.Scrollbar(body, orient="vertical", command=self._yview_both)
        self.vsb.pack(side="right", fill="y")

        self.left_box = self._make_pane(body)
        self.right_box = self._make_pane(body)
        self.left_box.pack(side="left", fill="both", expand=True, padx=(0, 2))
        self.right_box.pack(side="left", fill="both", expand=True, padx=(2, 0))

        for box in (self.left_box, self.right_box):
            box.txt.configure(yscrollcommand=self._on_scroll)
            box.txt.bind("<MouseWheel>", self._on_wheel)

        # --- key differences ---
        keys = ttk.Frame(nb)
        nb.add(keys, text="Key differences")

        cols = ("status", "path", "left", "right")
        self.tree = ttk.Treeview(keys, columns=cols, show="headings")
        for c, w, txt in (("status", 90, "Status"), ("path", 380, "Setting path"),
                          ("left", 280, "Left value"), ("right", 280, "Right value")):
            self.tree.heading(c, text=txt)
            self.tree.column(c, width=w, anchor="w")
        tsb = ttk.Scrollbar(keys, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")

        self.tree.tag_configure("added", background=COLORS["diff_add"])
        self.tree.tag_configure("removed", background=COLORS["diff_del"])
        self.tree.tag_configure("changed", background=COLORS["diff_chg"])

    def _make_pane(self, master):
        frame = ttk.Frame(master)
        txt = tk.Text(frame, wrap="none", font=self.font, bg=COLORS["bg"],
                      fg=COLORS["fg"], borderwidth=0, highlightthickness=0,
                      padx=4, state="disabled")
        hsb = ttk.Scrollbar(frame, orient="horizontal", command=txt.xview)
        txt.configure(xscrollcommand=hsb.set)
        txt.pack(side="top", fill="both", expand=True)
        hsb.pack(side="bottom", fill="x")
        txt.tag_configure("add", background=COLORS["diff_add"])
        txt.tag_configure("del", background=COLORS["diff_del"])
        txt.tag_configure("chg", background=COLORS["diff_chg"])
        txt.tag_configure("pad", background=COLORS["diff_pad"], foreground="#b0b0b0")
        txt.tag_configure("num", foreground=COLORS["gutter_fg"])
        frame.txt = txt
        return frame

    # ------------------------------------------------------------ scrolling
    def _yview_both(self, *args):
        for box in (self.left_box, self.right_box):
            box.txt.yview(*args)

    def _on_scroll(self, first, last):
        if self._syncing:
            return
        self._syncing = True
        try:
            self.vsb.set(first, last)
            for box in (self.left_box, self.right_box):
                box.txt.yview_moveto(first)
        finally:
            self._syncing = False

    def _on_wheel(self, event):
        self._yview_both("scroll", -1 * int(event.delta / 120), "units")
        return "break"

    def _zoom(self, d):
        self.font.configure(size=max(MIN_FONT, min(MAX_FONT, self.font.cget("size") + d)))

    # ---------------------------------------------------------------- diff
    def _prepared(self, raw):
        if not self.normalize.get():
            return raw
        try:
            return json.dumps(json.loads(raw), indent=2,
                              ensure_ascii=False, sort_keys=True)
        except json.JSONDecodeError:
            return raw

    def refresh(self):
        self._fill_sidebyside(self._prepared(self.left_text).splitlines(),
                              self._prepared(self.right_text).splitlines())
        self._fill_keys()

    def _fill_sidebyside(self, lhs, rhs):
        sm = difflib.SequenceMatcher(None, lhs, rhs, autojunk=False)
        left_rows, right_rows = [], []
        stats = {"equal": 0, "add": 0, "del": 0, "chg": 0}

        for op, i1, i2, j1, j2 in sm.get_opcodes():
            if op == "equal":
                for k in range(i2 - i1):
                    left_rows.append((i1 + k + 1, lhs[i1 + k], None))
                    right_rows.append((j1 + k + 1, rhs[j1 + k], None))
                stats["equal"] += i2 - i1
            elif op == "delete":
                for k in range(i1, i2):
                    left_rows.append((k + 1, lhs[k], "del"))
                    right_rows.append((None, "", "pad"))
                stats["del"] += i2 - i1
            elif op == "insert":
                for k in range(j1, j2):
                    left_rows.append((None, "", "pad"))
                    right_rows.append((k + 1, rhs[k], "add"))
                stats["add"] += j2 - j1
            else:  # replace
                for k in range(max(i2 - i1, j2 - j1)):
                    left_rows.append((i1 + k + 1, lhs[i1 + k], "chg")
                                     if i1 + k < i2 else (None, "", "pad"))
                    right_rows.append((j1 + k + 1, rhs[j1 + k], "chg")
                                      if j1 + k < j2 else (None, "", "pad"))
                stats["chg"] += max(i2 - i1, j2 - j1)

        self._render_rows(self.left_box.txt, left_rows)
        self._render_rows(self.right_box.txt, right_rows)
        self.diff_status.configure(
            text=(f"{stats['equal']} identical  |  {stats['chg']} changed  |  "
                  f"{stats['add']} only in right  |  {stats['del']} only in left")
        )

    @staticmethod
    def _render_rows(txt, rows):
        txt.configure(state="normal")
        txt.delete("1.0", "end")
        for lineno, content, tag in rows:
            num = f"{lineno:>5} " if lineno else "      "
            start = txt.index("end-1c")
            txt.insert("end", num, ("num",))
            txt.insert("end", content + "\n", (tag,) if tag else ())
            if tag:
                txt.tag_add(tag, start, f"{start} lineend+1c")
        txt.configure(state="disabled")

    def _fill_keys(self):
        self.tree.delete(*self.tree.get_children())
        try:
            left = flatten_json(json.loads(self.left_text))
        except json.JSONDecodeError:
            left = None
        try:
            right = flatten_json(json.loads(self.right_text))
        except json.JSONDecodeError:
            right = None

        if left is None or right is None:
            which = "left" if left is None else "right"
            self.tree.insert("", "end",
                             values=("n/a", f"The {which} file is not valid JSON "
                                            f"-- key comparison unavailable", "", ""))
            return

        only = self.only_diff.get()
        for path in sorted(set(left) | set(right)):
            in_l, in_r = path in left, path in right
            lv = fmt_value(left.get(path)) if in_l else ""
            rv = fmt_value(right.get(path)) if in_r else ""
            if in_l and in_r:
                if left[path] == right[path]:
                    if only:
                        continue
                    status, tag = "same", ""
                else:
                    status, tag = "changed", "changed"
            elif in_l:
                status, tag = "only left", "removed"
            else:
                status, tag = "only right", "added"
            self.tree.insert("", "end", values=(status, path, lv, rv),
                             tags=(tag,) if tag else ())


# --------------------------------------------------------------------------
# Main application
# --------------------------------------------------------------------------

class AppSettingsEditorApp(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AppSettings Editor")
        self.geometry("1280x760")
        self.minsize(900, 560)

        self.root_path = tk.StringVar()
        self.exact_only = tk.BooleanVar(value=False)
        self.word_wrap = tk.BooleanVar(value=False)
        self.status_text = tk.StringVar(value="Pick a root folder to begin.")
        self.counter_text = tk.StringVar(value="No scan yet")
        self.pos_text = tk.StringVar(value="Ln 1, Col 1")
        self.diag_text = tk.StringVar(value="")

        self.all_files = []
        self.shown_files = []
        self.item_to_path = {}
        self.current_file = None

        self._build_ui()
        self._build_menu()
        self.protocol("WM_DELETE_WINDOW", self.on_close)
        self._tick_position()

    # ----------------------------------------------------------------- UI
    def _build_ui(self):
        top = ttk.Frame(self, padding=(8, 8, 8, 4))
        top.pack(side="top", fill="x")
        ttk.Label(top, text="Root folder:").pack(side="left")
        entry = ttk.Entry(top, textvariable=self.root_path)
        entry.pack(side="left", fill="x", expand=True, padx=6)
        entry.bind("<Return>", lambda e: self.scan())
        ttk.Button(top, text="Browse...", command=self.browse_folder).pack(side="left", padx=2)
        ttk.Button(top, text="Scan", command=self.scan).pack(side="left", padx=2)

        paned = ttk.Panedwindow(self, orient="horizontal")
        paned.pack(side="top", fill="both", expand=True, padx=8, pady=(0, 4))

        # ---- sidebar ----
        side = ttk.Frame(paned)
        filt = ttk.Frame(side)
        filt.pack(side="top", fill="x", pady=(0, 2))
        ttk.Checkbutton(filt, text='Exact "appsettings.json" only',
                        variable=self.exact_only,
                        command=self.apply_filter).pack(side="left")
        ttk.Label(side, textvariable=self.counter_text,
                  foreground="#555").pack(side="top", anchor="w", pady=(0, 4))

        treewrap = ttk.Frame(side)
        treewrap.pack(side="top", fill="both", expand=True)
        self.tree = ttk.Treeview(treewrap, show="tree")
        tsb = ttk.Scrollbar(treewrap, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=tsb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        tsb.pack(side="right", fill="y")
        self.tree.bind("<<TreeviewSelect>>", self.on_tree_select)
        self.tree.bind("<Button-3>", self.on_tree_right_click)
        self.tree.tag_configure("folder", foreground="#333")
        self.tree.tag_configure("exact", foreground="#0451a5")
        paned.add(side, weight=1)

        # ---- editor ----
        right = ttk.Frame(paned)
        bar = ttk.Frame(right)
        bar.pack(side="top", fill="x")
        self.file_label = ttk.Label(bar, text="(no file selected)",
                                    font=("Segoe UI", 9, "bold"), anchor="w")
        self.file_label.pack(side="left", padx=4, fill="x", expand=True)
        ttk.Button(bar, text="Save", command=self.save_file).pack(side="right", padx=2)
        ttk.Button(bar, text="Format", command=self.format_json).pack(side="right", padx=2)
        ttk.Button(bar, text="Compare with...",
                   command=self.compare_with_file).pack(side="right", padx=2)
        ttk.Button(bar, text="Reload", command=self.reload_file).pack(side="right", padx=2)

        self.editor = JsonEditor(right, on_status=self.on_diag, on_dirty=self.on_dirty)
        self.editor.pack(side="top", fill="both", expand=True, pady=(4, 0))
        paned.add(right, weight=4)

        # ---- status bar ----
        status = ttk.Frame(self, relief="sunken")
        status.pack(side="bottom", fill="x")
        ttk.Label(status, textvariable=self.status_text, anchor="w",
                  padding=4).pack(side="left", fill="x", expand=True)
        self.diag_label = ttk.Label(status, textvariable=self.diag_text, padding=4)
        self.diag_label.pack(side="right")
        ttk.Label(status, textvariable=self.pos_text, padding=4,
                  width=18, anchor="e").pack(side="right")

    def _build_menu(self):
        menubar = tk.Menu(self)

        m_file = tk.Menu(menubar, tearoff=0)
        m_file.add_command(label="Open root folder...    Ctrl+O", command=self.browse_folder)
        m_file.add_command(label="Rescan    F5", command=self.scan)
        m_file.add_separator()
        m_file.add_command(label="Save    Ctrl+S", command=self.save_file)
        m_file.add_command(label="Reload from disk", command=self.reload_file)
        m_file.add_separator()
        m_file.add_command(label="Compare with file...    Ctrl+D", command=self.compare_with_file)
        m_file.add_separator()
        m_file.add_command(label="Exit", command=self.on_close)
        menubar.add_cascade(label="File", menu=m_file)

        m_edit = tk.Menu(menubar, tearoff=0)
        m_edit.add_command(label="Undo    Ctrl+Z", command=lambda: self._safe_undo(True))
        m_edit.add_command(label="Redo    Ctrl+Y", command=lambda: self._safe_undo(False))
        m_edit.add_separator()
        m_edit.add_command(label="Format JSON    Ctrl+Shift+F", command=self.format_json)
        m_edit.add_command(label="Minify JSON", command=self.editor.minify_json)
        m_edit.add_command(label="Go to error    F8", command=self.editor.goto_error)
        menubar.add_cascade(label="Edit", menu=m_edit)

        m_view = tk.Menu(menubar, tearoff=0)
        m_view.add_command(label="Zoom in    Ctrl++", command=lambda: self.editor.zoom(+1))
        m_view.add_command(label="Zoom out    Ctrl+-", command=lambda: self.editor.zoom(-1))
        m_view.add_command(label="Reset zoom    Ctrl+0", command=self.editor.zoom_reset)
        m_view.add_separator()
        m_view.add_checkbutton(label="Word wrap", variable=self.word_wrap,
                               command=lambda: self.editor.set_wrap(self.word_wrap.get()))
        m_view.add_checkbutton(label='Exact "appsettings.json" only',
                               variable=self.exact_only, command=self.apply_filter)
        m_view.add_separator()
        m_view.add_command(label="Expand all", command=lambda: self._expand(True))
        m_view.add_command(label="Collapse all", command=lambda: self._expand(False))
        menubar.add_cascade(label="View", menu=m_view)

        self.config(menu=menubar)

        self.bind_all("<Control-o>", lambda e: self.browse_folder())
        self.bind_all("<Control-s>", lambda e: self.save_file())
        self.bind_all("<Control-d>", lambda e: self.compare_with_file())
        self.bind_all("<Control-Shift-F>", lambda e: self.format_json())
        self.bind_all("<F5>", lambda e: self.scan())
        self.bind_all("<F8>", lambda e: self.editor.goto_error())

    def _safe_undo(self, undo=True):
        try:
            self.editor.text.edit_undo() if undo else self.editor.text.edit_redo()
        except tk.TclError:
            pass

    # ------------------------------------------------------------ scanning
    def browse_folder(self):
        folder = filedialog.askdirectory(title="Select root folder")
        if folder:
            self.root_path.set(folder)
            self.scan()

    def scan(self):
        root = self.root_path.get().strip().strip('"')
        if not root or not os.path.isdir(root):
            messagebox.showerror("Invalid folder", "Please choose a valid, existing folder.")
            return
        if not self._confirm_discard():
            return

        found = []
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d.lower() not in SKIP_DIRS]
            for name in filenames:
                low = name.lower()
                if low.startswith("appsettings") and low.endswith(".json"):
                    found.append(os.path.join(dirpath, name))

        found.sort(key=lambda p: os.path.relpath(p, root).lower())
        self.all_files = found
        self.current_file = None
        self.editor.set_content("")
        self.file_label.configure(text="(no file selected)")
        self.apply_filter()
        self.status_text.set(f"Scanned: {root}")

    def apply_filter(self):
        root = self.root_path.get().strip().strip('"')
        exact = [p for p in self.all_files
                 if os.path.basename(p).lower() == "appsettings.json"]
        self.shown_files = exact if self.exact_only.get() else list(self.all_files)
        if root and os.path.isdir(root):
            self._build_tree(root, self.shown_files)
        self.counter_text.set(
            f"Shown: {len(self.shown_files)}   |   exact: {len(exact)}   |   total: {len(self.all_files)}"
        )

    def _build_tree(self, root, paths):
        self.tree.delete(*self.tree.get_children())
        self.item_to_path.clear()
        nodes = {"": ""}
        for p in paths:
            rel = os.path.relpath(p, root)
            parts = rel.split(os.sep)
            cur = ""
            for part in parts[:-1]:
                nxt = os.path.join(cur, part) if cur else part
                if nxt not in nodes:
                    nodes[nxt] = self.tree.insert(nodes[cur], "end", text="[+] " + part,
                                                  open=True, tags=("folder",))
                cur = nxt
            is_exact = parts[-1].lower() == "appsettings.json"
            iid = self.tree.insert(nodes[cur], "end", text="   " + parts[-1],
                                   tags=("exact",) if is_exact else ())
            self.item_to_path[iid] = p

    def _expand(self, state):
        def walk(item):
            for child in self.tree.get_children(item):
                self.tree.item(child, open=state)
                walk(child)
        walk("")

    # -------------------------------------------------------------- files
    def on_tree_select(self, _event=None):
        sel = self.tree.selection()
        if not sel:
            return
        path = self.item_to_path.get(sel[0])
        if not path or path == self.current_file:
            return
        if not self._confirm_discard():
            for iid, p in self.item_to_path.items():
                if p == self.current_file:
                    self.tree.selection_set(iid)
                    break
            return
        self.load_file(path)

    def on_tree_right_click(self, event):
        iid = self.tree.identify_row(event.y)
        if not iid or iid not in self.item_to_path:
            return
        path = self.item_to_path[iid]
        menu = tk.Menu(self, tearoff=0)
        menu.add_command(label="Open", command=lambda: self.load_file(path))
        menu.add_command(label="Compare with current file",
                         command=lambda: self.compare_with_path(path))
        menu.add_separator()
        menu.add_command(label="Copy full path",
                         command=lambda: (self.clipboard_clear(),
                                          self.clipboard_append(path)))
        menu.tk_popup(event.x_root, event.y_root)

    def load_file(self, path):
        try:
            with open(path, "r", encoding="utf-8-sig") as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror("Error reading file", f"{path}\n\n{e}")
            return
        self.editor.set_content(content)
        self.current_file = path
        self.file_label.configure(text=path)
        self.status_text.set(f"Loaded: {path}")

    def reload_file(self):
        if not self.current_file:
            return
        if self.editor.dirty and not messagebox.askyesno(
                "Discard changes?", "Reloading discards unsaved changes. Continue?"):
            return
        self.load_file(self.current_file)

    def save_file(self):
        if not self.current_file:
            messagebox.showinfo("No file selected", "Select a file from the tree first.")
            return
        content = self.editor.get_content()
        if not self.editor.validate():
            line, col, msg = self.editor.last_error
            if not messagebox.askyesno(
                    "Invalid JSON", f"Line {line}, col {col}: {msg}\n\nSave anyway?"):
                return
        try:
            with open(self.current_file, "w", encoding="utf-8", newline="") as f:
                f.write(content)
        except Exception as e:
            messagebox.showerror("Error saving", f"{self.current_file}\n\n{e}")
            return
        self.editor.mark_clean()
        self.file_label.configure(text=self.current_file)
        self.status_text.set(f"Saved: {self.current_file}")

    def format_json(self):
        if self.editor.format_json():
            self.status_text.set("Formatted (2-space indent).")

    # ------------------------------------------------------------ compare
    def compare_with_file(self):
        if not self.current_file:
            messagebox.showinfo("Nothing to compare", "Open a file first.")
            return
        other = filedialog.askopenfilename(
            title="Pick a JSON file to compare against (any folder)",
            filetypes=[("JSON files", "*.json"), ("All files", "*.*")],
        )
        if other:
            self.compare_with_path(other)

    def compare_with_path(self, other_path):
        if not self.current_file:
            messagebox.showinfo("Nothing to compare", "Open a file first.")
            return
        try:
            with open(other_path, "r", encoding="utf-8-sig") as f:
                other_text = f.read()
        except Exception as e:
            messagebox.showerror("Error reading file", f"{other_path}\n\n{e}")
            return
        CompareWindow(self, self.current_file, self.editor.get_content(),
                      other_path, other_text)

    # ------------------------------------------------------------- status
    def on_diag(self, kind, message):
        self.diag_text.set(message)
        self.diag_label.configure(
            foreground={"ok": "#2e7d32", "error": "#d32f2f",
                        "empty": "#777777"}.get(kind, "#333"))

    def on_dirty(self, is_dirty):
        if self.current_file:
            self.file_label.configure(
                text=self.current_file + ("  *" if is_dirty else ""))

    def _tick_position(self):
        line, col = self.editor.position
        self.pos_text.set(f"Ln {line}, Col {col}")
        self.after(150, self._tick_position)

    def _confirm_discard(self):
        if not self.editor.dirty:
            return True
        return messagebox.askyesno("Unsaved changes",
                                   "You have unsaved changes. Discard them and continue?")

    def on_close(self):
        if not self._confirm_discard():
            return
        self.destroy()


if __name__ == "__main__":
    AppSettingsEditorApp().mainloop()
