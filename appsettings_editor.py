"""
AppSettings Editor
-------------------
A simple native Windows desktop app (Tkinter, ships with standard Python
on Windows — no extra installs needed) that:

  1. Lets you pick a root folder.
  2. Recursively scans it for any appsettings*.json files
     (appsettings.json, appsettings.Development.json, appsettings.Production.json, etc.)
  3. Lists them all.
  4. Clicking one loads its content into a text editor pane.
  5. Edit it, then Save (Ctrl+S or the Save button) writes it back to disk.

Run it with:
    python appsettings_editor.py

(Requires Python 3.8+. Tkinter is included by default on Windows installs
from python.org — if it's missing, reinstall Python and check the
"tcl/tk and IDLE" option.)
"""

import os
import json
import tkinter as tk
from tkinter import filedialog, messagebox, ttk


class AppSettingsEditor(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("AppSettings Editor")
        self.geometry("1000x650")
        self.minsize(700, 450)

        self.root_path = tk.StringVar()
        self.status_text = tk.StringVar(value="Pick a root folder to begin.")
        self.file_paths = []          # full paths, index-matched to listbox
        self.current_file = None      # currently loaded full path
        self.dirty = False            # unsaved changes flag

        self._build_ui()
        self._bind_shortcuts()

    # ---------------------------------------------------------------- UI ---
    def _build_ui(self):
        # Top bar: root path picker
        top = ttk.Frame(self, padding=8)
        top.pack(side=tk.TOP, fill=tk.X)

        ttk.Label(top, text="Root folder:").pack(side=tk.LEFT)
        entry = ttk.Entry(top, textvariable=self.root_path)
        entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=6)
        entry.bind("<Return>", lambda e: self.scan())

        ttk.Button(top, text="Browse...", command=self.browse_folder).pack(side=tk.LEFT, padx=2)
        ttk.Button(top, text="Scan", command=self.scan).pack(side=tk.LEFT, padx=2)

        # Main split: file list (left) | editor (right)
        main = ttk.Panedwindow(self, orient=tk.HORIZONTAL)
        main.pack(side=tk.TOP, fill=tk.BOTH, expand=True, padx=8, pady=(0, 8))

        # Left: listbox
        left = ttk.Frame(main)
        self.listbox = tk.Listbox(left, activestyle="dotbox", exportselection=False)
        self.listbox.pack(side=tk.TOP, fill=tk.BOTH, expand=True)
        self.listbox.bind("<<ListboxSelect>>", self.on_select_file)
        main.add(left, weight=1)

        # Right: editor + toolbar
        right = ttk.Frame(main)
        toolbar = ttk.Frame(right)
        toolbar.pack(side=tk.TOP, fill=tk.X)

        self.file_label = ttk.Label(toolbar, text="(no file selected)", font=("Segoe UI", 9, "bold"))
        self.file_label.pack(side=tk.LEFT, padx=4)

        ttk.Button(toolbar, text="Save (Ctrl+S)", command=self.save_file).pack(side=tk.RIGHT, padx=2)
        ttk.Button(toolbar, text="Validate JSON", command=self.validate_json).pack(side=tk.RIGHT, padx=2)
        ttk.Button(toolbar, text="Reload", command=self.reload_file).pack(side=tk.RIGHT, padx=2)

        text_frame = ttk.Frame(right)
        text_frame.pack(side=tk.TOP, fill=tk.BOTH, expand=True, pady=(4, 0))

        self.text = tk.Text(text_frame, wrap="none", undo=True, font=("Consolas", 10))
        yscroll = ttk.Scrollbar(text_frame, orient=tk.VERTICAL, command=self.text.yview)
        xscroll = ttk.Scrollbar(text_frame, orient=tk.HORIZONTAL, command=self.text.xview)
        self.text.configure(yscrollcommand=yscroll.set, xscrollcommand=xscroll.set)

        self.text.grid(row=0, column=0, sticky="nsew")
        yscroll.grid(row=0, column=1, sticky="ns")
        xscroll.grid(row=1, column=0, sticky="ew")
        text_frame.rowconfigure(0, weight=1)
        text_frame.columnconfigure(0, weight=1)

        self.text.bind("<<Modified>>", self.on_text_modified)

        main.add(right, weight=3)

        # Status bar
        status = ttk.Label(self, textvariable=self.status_text, relief=tk.SUNKEN, anchor="w", padding=4)
        status.pack(side=tk.BOTTOM, fill=tk.X)

    def _bind_shortcuts(self):
        self.bind_all("<Control-s>", lambda e: self.save_file())
        self.protocol("WM_DELETE_WINDOW", self.on_close)

    # ----------------------------------------------------------- actions ---
    def browse_folder(self):
        folder = filedialog.askdirectory(title="Select root folder")
        if folder:
            self.root_path.set(folder)
            self.scan()

    def scan(self):
        root = self.root_path.get().strip()
        if not root or not os.path.isdir(root):
            messagebox.showerror("Invalid folder", "Please choose a valid, existing folder.")
            return

        if not self._confirm_discard_changes():
            return

        found = []
        for dirpath, _dirnames, filenames in os.walk(root):
            for fname in filenames:
                if fname.lower().startswith("appsettings") and fname.lower().endswith(".json"):
                    found.append(os.path.join(dirpath, fname))

        found.sort(key=lambda p: os.path.relpath(p, root).lower())

        self.file_paths = found
        self.listbox.delete(0, tk.END)
        for p in found:
            rel = os.path.relpath(p, root)
            self.listbox.insert(tk.END, rel)

        self.current_file = None
        self.dirty = False
        self.text.delete("1.0", tk.END)
        self.file_label.config(text="(no file selected)")

        if found:
            self.status_text.set(f"Found {len(found)} appsettings*.json file(s) under: {root}")
        else:
            self.status_text.set(f"No appsettings*.json files found under: {root}")

    def on_select_file(self, _event=None):
        selection = self.listbox.curselection()
        if not selection:
            return
        index = selection[0]

        # If switching away from a dirty file, confirm first.
        if self.current_file and self.dirty:
            if not self._confirm_discard_changes():
                # revert listbox selection back to current file
                if self.current_file in self.file_paths:
                    prev_index = self.file_paths.index(self.current_file)
                    self.listbox.selection_clear(0, tk.END)
                    self.listbox.selection_set(prev_index)
                return

        path = self.file_paths[index]
        self._load_file(path)

    def _load_file(self, path):
        try:
            with open(path, "r", encoding="utf-8") as f:
                content = f.read()
        except Exception as e:
            messagebox.showerror("Error reading file", f"Could not read:\n{path}\n\n{e}")
            return

        self.text.delete("1.0", tk.END)
        self.text.insert("1.0", content)
        self.text.edit_modified(False)
        self.dirty = False
        self.current_file = path
        self.file_label.config(text=path)
        self.status_text.set(f"Loaded: {path}")

    def reload_file(self):
        if not self.current_file:
            return
        if self.dirty and not messagebox.askyesno(
            "Discard changes?", "Reloading will discard unsaved changes. Continue?"
        ):
            return
        self._load_file(self.current_file)

    def on_text_modified(self, _event=None):
        if self.text.edit_modified():
            self.dirty = True
            if self.current_file:
                self.file_label.config(text=self.current_file + "  *")
            self.text.edit_modified(False)  # reset flag so event fires again next edit

    def validate_json(self):
        content = self.text.get("1.0", tk.END)
        try:
            json.loads(content)
            messagebox.showinfo("Valid JSON", "This file's content is valid JSON.")
            return True
        except json.JSONDecodeError as e:
            messagebox.showerror("Invalid JSON", f"JSON error: {e}")
            return False

    def save_file(self):
        if not self.current_file:
            messagebox.showinfo("No file selected", "Select a file from the list first.")
            return

        content = self.text.get("1.0", tk.END)
        # Strip the single trailing newline Tkinter always adds
        if content.endswith("\n"):
            content = content[:-1]

        # Warn (but don't block) on invalid JSON
        try:
            json.loads(content)
        except json.JSONDecodeError as e:
            if not messagebox.askyesno(
                "Invalid JSON",
                f"This doesn't parse as valid JSON:\n{e}\n\nSave anyway?",
            ):
                return

        try:
            with open(self.current_file, "w", encoding="utf-8") as f:
                f.write(content)
        except Exception as e:
            messagebox.showerror("Error saving file", f"Could not save:\n{self.current_file}\n\n{e}")
            return

        self.dirty = False
        self.file_label.config(text=self.current_file)
        self.status_text.set(f"Saved: {self.current_file}")

    def _confirm_discard_changes(self):
        """Returns True if it's OK to proceed (no unsaved changes, or user confirmed discard)."""
        if not self.dirty:
            return True
        return messagebox.askyesno(
            "Unsaved changes", "You have unsaved changes. Discard them and continue?"
        )

    def on_close(self):
        if self.dirty and not self._confirm_discard_changes():
            return
        self.destroy()


if __name__ == "__main__":
    app = AppSettingsEditor()
    app.mainloop()
