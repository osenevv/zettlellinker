from __future__ import annotations

import csv
import os
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import replace
from pathlib import Path
from tkinter import filedialog, messagebox, ttk
from typing import Any

from .config import (
    CONFIG_NAME,
    VaultConfig,
    get_dark_mode,
    get_last_vault_path,
    load_config,
    save_config,
    save_dark_mode,
    save_last_vault_path,
)

# Dark colors only. Light mode keeps the window's original clam colors
# and the black console with green text.
THEME_DARK = {
    "bg": "#15181d",
    "card": "#1d2229",
    "raised": "#232a33",
    "soft": "#272e37",
    "text": "#f0f3f7",
    "muted": "#aab4c1",
    "line": "#37414d",
    "log_bg": "#0f1217",
    "log_fg": "#b7f7d1",
    "selected": "#31436f",
}
LIGHT_LOG_BG = "#000"
LIGHT_LOG_FG = "#00ff66"
# Clam draws entry and tree interiors from -window, and does not store that
# color on TEntry. A dark fieldbackground therefore survives the toggle.
LIGHT_FIELD = "#ffffff"
SUN = "☀"
MOON = "☾"


def _hex_luminance(color: str) -> float:
    value = str(color or "").lstrip("#")
    if len(value) != 6:
        return 1.0
    try:
        red, green, blue = int(value[0:2], 16), int(value[2:4], 16), int(value[4:6], 16)
    except ValueError:
        return 1.0
    return (0.2126 * red + 0.7152 * green + 0.0722 * blue) / 255


def contrast_fg(background: str) -> str:
    """Pick light or dark label paint so button text stays readable."""
    return "#f4f6f8" if _hex_luminance(background) < 0.55 else "#141820"


from .vault import discover_notes
from .workflow import run_note_suggest, run_vault_scan
from .writer import LinkWriter, undo_last


class ZettelLinkerApp:
    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title("ZettelLinker — Desktop Vault Linker")
        self.root.geometry("820x680")
        self.root.minsize(700, 500)

        # Style configuration
        self.style = ttk.Style()
        self.style.theme_use("clam")

        self.current_suggestions: list[Any] = []
        self.current_findings: list[Any] = []
        self.current_engine: Any | None = None
        self.sort_column_name: str = "score"
        self.sort_reverse: bool = True
        self.vault_path_var = tk.StringVar(value="")
        self.sim_threshold_var = tk.DoubleVar(value=0.60)
        self.inline_threshold_var = tk.DoubleVar(value=0.72)
        self.limit_var = tk.IntVar(value=5)
        self.auto_write_var = tk.StringVar(value="off")
        self.heading_var = tk.StringVar(value="## Connections")
        self.exclude_names_var = tk.StringVar(value="")
        self.exclude_files_var = tk.StringVar(value="")
        self.suggest_note_var = tk.StringVar(value="")
        self.cached_note_names: list[str] = []
        self.suggest_popup: tk.Toplevel | None = None
        self.suggest_listbox: tk.Listbox | None = None
        self.dark_mode = get_dark_mode()
        self._native_root_bg = self.root.cget("bg")
        self.root.title("ZettelLinker - Obsidian Semantic Linker")
        self.root.geometry("900x680")
        self.root.minsize(760, 560)

        self._build_ui()
        self._load_initial_config()

    def _toggle_theme(self) -> None:
        self.dark_mode = not self.dark_mode
        self._apply_theme()
        save_dark_mode(self.dark_mode)

    def _style_theme_button(self, background: str, foreground: str) -> None:
        self.style.configure(
            "Zettel.Theme.TButton",
            background=background,
            foreground=foreground,
            bordercolor=background,
            lightcolor=background,
            darkcolor=background,
            padding=(1, 0),
            font=("TkDefaultFont", 12),
            relief="flat",
            borderwidth=0,
            anchor="center",
        )
        self.style.map(
            "Zettel.Theme.TButton",
            background=[("pressed", background), ("active", background)],
            foreground=[("pressed", foreground), ("active", foreground)],
        )

    def _snapshot_light_styles(self) -> None:
        tracked = {
            ".": ("background", "foreground"),
            "TFrame": ("background",),
            "TLabel": ("background", "foreground"),
            "TLabelframe": ("background", "foreground", "bordercolor", "lightcolor", "darkcolor"),
            "TLabelframe.Label": ("background", "foreground"),
            "TEntry": ("fieldbackground", "foreground", "bordercolor", "lightcolor", "darkcolor"),
            "TButton": ("background", "foreground", "bordercolor", "lightcolor", "darkcolor", "padding", "relief"),
            "Treeview": ("background", "fieldbackground", "foreground", "bordercolor"),
            "Treeview.Heading": ("background", "foreground", "bordercolor"),
            "Vertical.TScrollbar": ("background", "troughcolor", "bordercolor", "arrowcolor"),
        }
        self._light_styles = {
            name: {option: self.style.lookup(name, option) for option in options}
            for name, options in tracked.items()
        }
        self._light_maps = {
            name: self.style.map(name) for name in ("TButton", "Treeview", "Treeview.Heading")
        }

    def _restore_light_styles(self) -> None:
        for name, options in self._light_styles.items():
            values = {option: value for option, value in options.items() if value}
            if values:
                self.style.configure(name, **values)
        for name, spec in self._light_maps.items():
            if spec:
                self.style.map(name, **spec)
        self.style.configure("TEntry", fieldbackground=LIGHT_FIELD)
        if not self._light_styles["Treeview"].get("fieldbackground"):
            self.style.configure("Treeview", fieldbackground=LIGHT_FIELD)

    def _apply_theme(self) -> None:
        """Dark mode repaints the window. Light mode restores the original colors."""
        self.style.theme_use("clam")
        if not hasattr(self, "_light_styles"):
            self._snapshot_light_styles()
        self.theme_button.configure(text=MOON if self.dark_mode else SUN)
        self.theme_tip.text = "Use light mode" if self.dark_mode else "Use dark mode"
        if not self.dark_mode:
            self._restore_light_styles()
            self.root.configure(bg=self._native_root_bg)
            self.root._zettel_palette = None
            button_bg = self._light_styles["TButton"].get("background") or self._native_root_bg
            button_fg = self._light_styles["TButton"].get("foreground") or "#000000"
            self._style_theme_button(button_bg, button_fg)
            self.log_text.configure(bg=LIGHT_LOG_BG, fg=LIGHT_LOG_FG, insertbackground=LIGHT_LOG_FG)
            self._paint_suggest_popup()
            return

        palette = THEME_DARK
        self.root._zettel_palette = palette
        self.root.configure(bg=palette["bg"])
        style = self.style
        button_fg = contrast_fg(palette["card"])
        style.configure(".", background=palette["bg"], foreground=palette["text"])
        style.configure("TFrame", background=palette["bg"])
        style.configure("TLabel", background=palette["bg"], foreground=palette["text"])
        style.configure(
            "TLabelframe",
            background=palette["bg"],
            foreground=palette["text"],
            bordercolor=palette["line"],
            lightcolor=palette["line"],
            darkcolor=palette["line"],
        )
        style.configure("TLabelframe.Label", background=palette["bg"], foreground=palette["muted"])
        style.configure(
            "TEntry",
            fieldbackground=palette["raised"],
            foreground=palette["text"],
            bordercolor=palette["line"],
            lightcolor=palette["line"],
            darkcolor=palette["line"],
        )
        style.configure(
            "TButton",
            background=palette["card"],
            foreground=button_fg,
            bordercolor=palette["line"],
            lightcolor=palette["line"],
            darkcolor=palette["line"],
        )
        style.map(
            "TButton",
            background=[("disabled", palette["soft"]), ("pressed", palette["soft"]), ("active", palette["soft"])],
            foreground=[
                ("disabled", contrast_fg(palette["soft"])),
                ("pressed", button_fg),
                ("active", button_fg),
            ],
        )
        self._style_theme_button(palette["bg"], palette["text"])
        style.configure(
            "Treeview",
            background=palette["card"],
            fieldbackground=palette["card"],
            foreground=palette["text"],
            bordercolor=palette["line"],
        )
        style.map(
            "Treeview",
            background=[("selected", palette["selected"])],
            foreground=[("selected", palette["text"])],
        )
        style.configure(
            "Treeview.Heading",
            background=palette["soft"],
            foreground=palette["muted"],
            bordercolor=palette["line"],
        )
        style.map("Treeview.Heading", background=[("active", palette["soft"])])
        style.configure(
            "Vertical.TScrollbar",
            background=palette["soft"],
            troughcolor=palette["bg"],
            bordercolor=palette["line"],
            arrowcolor=palette["muted"],
        )
        self.log_text.configure(
            bg=palette["log_bg"],
            fg=palette["log_fg"],
            insertbackground=palette["log_fg"],
            highlightbackground=palette["line"],
            highlightcolor=palette["line"],
        )
        self._paint_suggest_popup()

    def _paint_suggest_popup(self) -> None:
        box = self.suggest_listbox
        if box is None:
            return
        try:
            if not box.winfo_exists():
                return
        except tk.TclError:
            return
        if self.dark_mode:
            palette = THEME_DARK
            box.configure(
                bg=palette["raised"],
                fg=palette["text"],
                selectbackground=palette["selected"],
                selectforeground=palette["text"],
                highlightbackground=palette["line"],
            )
            if self.suggest_popup is not None:
                self.suggest_popup.configure(bg=palette["line"])
            return
        box.configure(
            bg="#FFFFFF",
            fg="#000000",
            selectbackground="#007ACC",
            selectforeground="#FFFFFF",
            highlightbackground="#FFFFFF",
        )

    def _menu(self) -> tk.Menu:
        if not self.dark_mode:
            return tk.Menu(self.root, tearoff=0)
        palette = THEME_DARK
        return tk.Menu(
            self.root,
            tearoff=0,
            bg=palette["card"],
            fg=palette["text"],
            activebackground=palette["selected"],
            activeforeground=palette["text"],
        )

    def _build_ui(self) -> None:
        bar = ttk.Frame(self.root)
        bar.pack(fill=tk.X)
        self.theme_button = ttk.Button(
            bar,
            text=SUN,
            command=self._toggle_theme,
            style="Zettel.Theme.TButton",
            width=2,
            cursor="hand2",
        )
        self.theme_button.pack(side=tk.RIGHT, padx=(0, 8), pady=(4, 0))
        self.theme_tip = ToolTip(self.theme_button, "Use dark mode")

        main = ttk.Frame(self.root, padding=10)
        main.pack(fill=tk.BOTH, expand=True)

        # 1. Vault Location Frame
        vault_frame = ttk.LabelFrame(main, text=" Vault Location ", padding=6)
        vault_frame.pack(fill=tk.X, pady=(0, 6))

        ttk.Label(vault_frame, text="Folder Path:").pack(side=tk.LEFT, padx=(0, 4))
        self.vault_entry = ttk.Entry(vault_frame, textvariable=self.vault_path_var)
        self.vault_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 6))

        ttk.Button(vault_frame, text="Browse", command=self.on_browse, width=8).pack(side=tk.LEFT, padx=(0, 4))
        ttk.Button(vault_frame, text="Load", command=self.on_load_config, width=8).pack(side=tk.LEFT)

        # 2. Actions Frame
        actions_frame = ttk.LabelFrame(main, text=" Actions ", padding=6)
        actions_frame.pack(fill=tk.X, pady=(0, 6))

        btn_box = ttk.Frame(actions_frame)
        btn_box.pack(fill=tk.X, pady=(0, 4))

        self.scan_btn = ttk.Button(btn_box, text="Scan Whole Vault", command=self.on_scan, width=16)
        self.scan_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.apply_btn = ttk.Button(btn_box, text="Apply Connections", command=self.on_apply_connections, width=16)
        self.apply_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.undo_btn = ttk.Button(btn_box, text="Undo", command=self.on_undo, width=10)
        self.undo_btn.pack(side=tk.LEFT, padx=(0, 4))

        self.export_menu_btn = ttk.Button(btn_box, text="Export ▼", command=self.on_show_export_menu, width=10)
        self.export_menu_btn.pack(side=tk.LEFT)

        suggest_box = ttk.Frame(actions_frame)
        suggest_box.pack(fill=tk.X)

        suggest_lbl = ttk.Label(suggest_box, text="Inspect Note:")
        suggest_lbl.pack(side=tk.LEFT, padx=(0, 6))
        self.suggest_ent = ttk.Entry(suggest_box, textvariable=self.suggest_note_var, width=20)
        self.suggest_ent.pack(side=tk.LEFT, padx=(0, 6))
        self.suggest_ent.bind("<Return>", lambda _e: (self._hide_suggest_dropdown(), self.on_suggest()))
        self.suggest_ent.bind("<KeyRelease>", self._on_suggest_keyrelease)
        self.suggest_ent.bind("<FocusOut>", lambda _e: self.root.after(200, self._hide_suggest_dropdown))

        suggest_tip = "Type a note name and press Enter to see link suggestions."
        ToolTip(suggest_lbl, suggest_tip)
        ToolTip(self.suggest_ent, suggest_tip)

        # Global Undo Keyboard Shortcuts (Ctrl+Z / Cmd+Z)
        self.root.bind("<Control-z>", lambda _e: self.on_undo())
        self.root.bind("<Command-z>", lambda _e: self.on_undo())
        self.root.bind("<Meta-z>", lambda _e: self.on_undo())

        # 3. Settings Frame (Spacious 3-Row Layout)
        settings_frame = ttk.LabelFrame(main, text=" Settings ", padding=8)
        settings_frame.pack(fill=tk.X, pady=(0, 8))

        grid = ttk.Frame(settings_frame)
        grid.pack(fill=tk.X)

        # Row 0: Numerical thresholds & Heading
        sim_lbl = ttk.Label(grid, text="Similarity Threshold:")
        sim_lbl.grid(row=0, column=0, sticky=tk.W, padx=(4, 2), pady=4)
        sim_ent = ttk.Entry(grid, textvariable=self.sim_threshold_var, width=5)
        sim_ent.grid(row=0, column=1, sticky=tk.W, padx=(0, 14), pady=4)

        inline_lbl = ttk.Label(grid, text="Inline Threshold:")
        inline_lbl.grid(row=0, column=2, sticky=tk.W, padx=(4, 2), pady=4)
        inline_ent = ttk.Entry(grid, textvariable=self.inline_threshold_var, width=5)
        inline_ent.grid(row=0, column=3, sticky=tk.W, padx=(0, 14), pady=4)

        limit_lbl = ttk.Label(grid, text="Suggestions Limit:")
        limit_lbl.grid(row=0, column=4, sticky=tk.W, padx=(4, 2), pady=4)
        limit_ent = ttk.Entry(grid, textvariable=self.limit_var, width=4)
        limit_ent.grid(row=0, column=5, sticky=tk.W, padx=(0, 14), pady=4)

        heading_lbl = ttk.Label(grid, text="Connections Heading:")
        heading_lbl.grid(row=0, column=6, sticky=tk.W, padx=(4, 2), pady=4)
        heading_ent = ttk.Entry(grid, textvariable=self.heading_var, width=14)
        heading_ent.grid(row=0, column=7, sticky=tk.W, pady=4)

        sim_tip = "How similar notes must be to connect (0–1). Higher = stricter."
        ToolTip(sim_lbl, sim_tip)
        ToolTip(sim_ent, sim_tip)

        inline_tip = "Minimum match score to insert a link directly inside sentence text."
        ToolTip(inline_lbl, inline_tip)
        ToolTip(inline_ent, inline_tip)

        limit_tip = "Max link suggestions per note."
        ToolTip(limit_lbl, limit_tip)
        ToolTip(limit_ent, limit_tip)

        heading_tip = "Links are appended under this heading. Created at note bottom if missing."
        ToolTip(heading_lbl, heading_tip)
        ToolTip(heading_ent, heading_tip)

        # Row 1: Exclude Substrings
        exclude_sub_lbl = ttk.Label(grid, text="Exclude Substrings:")
        exclude_sub_lbl.grid(row=1, column=0, sticky=tk.W, padx=(4, 2), pady=4)
        exclude_sub_ent = ttk.Entry(grid, textvariable=self.exclude_names_var, width=24)
        exclude_sub_ent.grid(row=1, column=1, columnspan=5, sticky=tk.W, padx=(0, 4), pady=4)

        exclude_sub_tip = "Skip notes whose name or folder contains these words (comma-separated)."
        ToolTip(exclude_sub_lbl, exclude_sub_tip)
        ToolTip(exclude_sub_ent, exclude_sub_tip)

        # Row 2: Exclude Files (with inline Browse button) & Save Settings button
        exclude_file_lbl = ttk.Label(grid, text="Exclude Specific Files:")
        exclude_file_lbl.grid(row=2, column=0, sticky=tk.W, padx=(4, 2), pady=4)

        ex_file_box = ttk.Frame(grid)
        ex_file_box.grid(row=2, column=1, columnspan=5, sticky=tk.W, padx=(0, 12), pady=4)

        exclude_files_ent = ttk.Entry(ex_file_box, textvariable=self.exclude_files_var, width=24)
        exclude_files_ent.pack(side=tk.LEFT, padx=(0, 4))
        ex_browse_btn = ttk.Button(ex_file_box, text="Browse...", command=self.on_browse_exclude_file, width=8)
        ex_browse_btn.pack(side=tk.LEFT)

        exclude_file_tip = "Skip specific files. Browse to select or type relative paths."
        ToolTip(exclude_file_lbl, exclude_file_tip)
        ToolTip(exclude_files_ent, exclude_file_tip)
        ToolTip(ex_browse_btn, exclude_file_tip)

        grid.columnconfigure(7, weight=1)

        ttk.Button(grid, text="Save Settings", command=self.on_save_settings, width=14).grid(row=2, column=7, sticky=tk.E, pady=4)

        # 3. Results Suggestions Frame (Treeview Table)
        results_frame = ttk.LabelFrame(main, text=" Link Suggestions ", padding=6)
        results_frame.pack(fill=tk.BOTH, expand=True, pady=(0, 6))

        columns = ("source", "target", "score")
        self.tree = ttk.Treeview(results_frame, columns=columns, show="headings", height=8, selectmode="extended")
        self.tree.heading("source", text="Source Note ⇅", command=lambda: self.sort_column("source"))
        self.tree.heading("target", text="Target Connection ⇅", command=lambda: self.sort_column("target"))
        self.tree.heading("score", text="Similarity Score ▼", command=lambda: self.sort_column("score"))

        self.tree.column("source", width=300)
        self.tree.column("target", width=300)
        self.tree.column("score", width=120, anchor=tk.CENTER)

        self.tree.bind("<Double-1>", self.on_item_double_click)
        self.tree.bind("<Return>", self.on_item_double_click)
        self.tree.bind("<Button-2>", self.on_right_click)
        self.tree.bind("<Button-3>", self.on_right_click)

        tree_scroll = ttk.Scrollbar(results_frame, orient=tk.VERTICAL, command=self.tree.yview)
        self.tree.configure(yscrollcommand=tree_scroll.set)

        self.tree.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        tree_scroll.pack(side=tk.RIGHT, fill=tk.Y)

        # 4. Log Frame (Console Log - height=8)
        log_frame = ttk.LabelFrame(main, text=" Console Log ", padding=6)
        log_frame.pack(fill=tk.X)

        self.log_text = tk.Text(log_frame, height=8, font=("Courier", 11), bg=LIGHT_LOG_BG, fg=LIGHT_LOG_FG)
        self._apply_theme()
        log_scroll = ttk.Scrollbar(log_frame, orient=tk.VERTICAL, command=self.log_text.yview)
        self.log_text.configure(yscrollcommand=log_scroll.set)

        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        log_scroll.pack(side=tk.RIGHT, fill=tk.Y)

    def _check_vault_permission(self, vault: Path) -> bool:
        if not vault.exists():
            self.log(f"[ERROR] Vault directory does not exist: {vault}")
            return False
        try:
            list(vault.iterdir())
            return True
        except PermissionError as exc:
            if sys.platform == "darwin":
                guide = (
                    "macOS System Settings Action Required:\n"
                    "1. Open System Settings -> Privacy & Security.\n"
                    "2. Go to Files and Folders (or Full Disk Access).\n"
                    "3. Enable access for Terminal / ZettelLinker."
                )
            elif sys.platform == "win32":
                guide = (
                    "Windows Security Action Required:\n"
                    "1. Right-click the folder -> Properties -> Security tab.\n"
                    "2. Ensure your Windows user account has Read & Write permissions.\n"
                    "3. Or run ZettelLinker as Administrator."
                )
            else:
                guide = (
                    "Linux File Permission Action Required:\n"
                    "1. Check directory permissions (chmod +r / chown).\n"
                    "2. Verify AppArmor / SELinux / Flatpak desktop read privileges."
                )

            self.log(f"[PERMISSION DENIED] OS blocked access to '{vault}' ({exc}).\n  -> {guide}")
            messagebox.showerror(
                "Permission Denied by OS",
                f"ZettelLinker cannot read the vault directory:\n{vault}\n\n"
                f"Reason: OS Permission Denied ({exc})\n\n{guide}"
            )
            return False
        except Exception as exc:
            self.log(f"[READ ERROR] Cannot access vault '{vault}': {exc}")
            return False

        self.log("ZettelLinker Desktop App ready.")

    def log(self, msg: str) -> None:
        self.log_text.insert(tk.END, msg + "\n")
        self.log_text.see(tk.END)

    def get_vault_path(self) -> Path:
        raw = self.vault_path_var.get().strip()
        return Path(raw).expanduser().resolve() if raw else Path.cwd()

    def _load_initial_config(self) -> None:
        try:
            last_vault = get_last_vault_path()
            if last_vault and Path(last_vault).exists():
                self.vault_path_var.set(last_vault)
            else:
                default_path = Path.cwd().resolve()
                self.vault_path_var.set(str(default_path))
            self.on_load_config()
        except Exception as exc:
            self.log(f"Initial config load note: {exc}")

    def on_browse_exclude_file(self) -> None:
        try:
            vault = self.get_vault_path()
        except Exception:
            vault = Path.home()
        filepath = filedialog.askopenfilename(
            title="Select Note File to Exclude from Search",
            initialdir=vault,
            filetypes=[("Markdown files", "*.md"), ("Markdown files", "*.markdown"), ("All files", "*.*")],
            parent=self.root,
        )
        if not filepath:
            return
        target_p = Path(filepath)
        try:
            rel_str = target_p.relative_to(vault).as_posix()
        except ValueError:
            rel_str = target_p.name

        current = [s.strip() for s in self.exclude_files_var.get().split(",") if s.strip()]
        if rel_str not in current:
            current.append(rel_str)
            self.exclude_files_var.set(", ".join(current))
            self.log(f"Added file to exclusion list: {rel_str}")

    def on_browse(self) -> None:
        folder = filedialog.askdirectory(title="Select Obsidian Vault Folder", parent=self.root)
        if folder:
            self.vault_path_var.set(folder)
            self.on_load_config()

    def on_load_config(self) -> None:
        vault = self.get_vault_path()
        if not self._check_vault_permission(vault):
            return
        self.log(f"Loading config for: {vault}")
        try:
            config = load_config(vault)
            save_last_vault_path(vault)
            self.vault_path_var.set(str(vault))
            self.sim_threshold_var.set(config.semantic.threshold)
            self.inline_threshold_var.set(config.auto_write.anchor_threshold)
            self.limit_var.set(config.semantic.limit)
            self.auto_write_var.set("on" if config.auto_write.enabled else "off")
            self.heading_var.set(config.auto_write.connections_heading)
            self.exclude_names_var.set(", ".join(config.exclude_name_contains))
            self.exclude_files_var.set(", ".join(config.exclude_files))
            self.log(f"Vault loaded successfully: {vault}")
        except Exception as exc:
            self.log(f"Error: {exc}")

    def on_save_settings(self) -> None:
        vault = self.get_vault_path()
        self.log("Saving settings...")
        try:
            config = load_config(vault)
            save_last_vault_path(vault)
            config.semantic.threshold = float(self.sim_threshold_var.get())
            config.auto_write.anchor_threshold = float(self.inline_threshold_var.get())
            config.semantic.limit = int(self.limit_var.get())
            config.auto_write.enabled = self.auto_write_var.get() == "on"
            config.auto_write.connections_heading = self.heading_var.get().strip()

            raw_excludes = self.exclude_names_var.get()
            config.exclude_name_contains = [s.strip() for s in raw_excludes.split(",") if s.strip()]

            raw_ex_files = self.exclude_files_var.get()
            config.exclude_files = [s.strip() for s in raw_ex_files.split(",") if s.strip()]

            config.validate()
            save_config(vault / CONFIG_NAME, config)
            self.log("Settings saved to .zettellinker.yml")
            messagebox.showinfo("Saved", "Settings saved successfully!")
        except Exception as exc:
            self.log(f"Error: {exc}")
            messagebox.showerror("Error", str(exc))

    def sort_column(self, col: str) -> None:
        if not self.current_suggestions:
            return
        if self.sort_column_name == col:
            self.sort_reverse = not self.sort_reverse
        else:
            self.sort_column_name = col
            self.sort_reverse = (col == "score")

        if col == "source":
            self.current_suggestions.sort(key=lambda s: s.source.casefold(), reverse=self.sort_reverse)
        elif col == "target":
            self.current_suggestions.sort(key=lambda s: s.target.casefold(), reverse=self.sort_reverse)
        elif col == "score":
            self.current_suggestions.sort(key=lambda s: s.score, reverse=self.sort_reverse)

        arrow = " ▼" if self.sort_reverse else " ▲"
        self.tree.heading("source", text="Source Note" + (arrow if col == "source" else " ⇅"), command=lambda: self.sort_column("source"))
        self.tree.heading("target", text="Target Connection" + (arrow if col == "target" else " ⇅"), command=lambda: self.sort_column("target"))
        self.tree.heading("score", text="Similarity Score" + (arrow if col == "score" else " ⇅"), command=lambda: self.sort_column("score"))

        self._render_tree_items()

    def _render_suggestions(self, suggestions: list[Any]) -> None:
        self.current_suggestions = list(suggestions)
        if self.sort_column_name == "score":
            self.current_suggestions.sort(key=lambda s: s.score, reverse=self.sort_reverse)
        elif self.sort_column_name == "source":
            self.current_suggestions.sort(key=lambda s: s.source.casefold(), reverse=self.sort_reverse)
        elif self.sort_column_name == "target":
            self.current_suggestions.sort(key=lambda s: s.target.casefold(), reverse=self.sort_reverse)

        self._render_tree_items()

    def _render_tree_items(self) -> None:
        for item in self.tree.get_children():
            self.tree.delete(item)

        if not self.current_suggestions:
            self.log("No connections met the similarity threshold.")
            return

        for s in self.current_suggestions:
            score_pct = f"{s.score * 100:.1f}%"
            self.tree.insert("", tk.END, values=(s.source, s.target, score_pct))

    def on_item_double_click(self, event: tk.Event | None = None) -> None:
        selected_item = self.tree.focus()
        if not selected_item:
            return
        values = self.tree.item(selected_item, "values")
        if not values or len(values) < 2:
            return

        source_name, target_name = values[0], values[1]
        vault = self.get_vault_path()

        clicked_note = target_name if (event and event.x > 300) else source_name
        target_path = self._resolve_note_file(vault, clicked_note) or self._resolve_note_file(vault, source_name) or self._resolve_note_file(vault, target_name)

        if not target_path or not target_path.exists():
            self.log(f"[NOTE OPEN ERROR] Could not locate file for '{clicked_note}' in vault.")
            messagebox.showwarning("File Not Found", f"Could not locate markdown file for note:\n'{clicked_note}'")
            return

        self._open_file_in_os(target_path)

    def _resolve_note_file(self, vault: Path, note_identity: str) -> Path | None:
        candidates = [
            vault / f"{note_identity}.md",
            vault / note_identity,
            vault / f"{note_identity}.markdown",
        ]
        for c in candidates:
            if c.exists() and c.is_file():
                return c

        target_clean = note_identity.casefold().rstrip(".md")
        try:
            for root, _, files in os.walk(vault):
                for f in files:
                    f_path = Path(root) / f
                    if f_path.suffix.lower() in {".md", ".markdown", ".mdown", ".mkdn", ".txt"}:
                        rel_id = f_path.relative_to(vault).as_posix()
                        if rel_id.casefold() == target_clean or f_path.stem.casefold() == target_clean or rel_id.casefold().endswith("/" + target_clean):
                            return f_path
        except Exception:
            pass
        return None

    def on_right_click(self, event: tk.Event) -> None:
        row_id = self.tree.identify_row(event.y)
        selected_ids = list(self.tree.selection())
        if row_id and row_id not in selected_ids:
            self.tree.selection_set(row_id)
            selected_ids = [row_id]

        if not selected_ids:
            return

        selected_suggestions: list[Any] = []
        for item_id in selected_ids:
            vals = self.tree.item(item_id, "values")
            if vals and len(vals) >= 2:
                src, tgt = vals[0], vals[1]
                for s in self.current_suggestions:
                    if s.source == src and s.target == tgt:
                        selected_suggestions.append(s)
                        break

        menu = self._menu()

        apply_label = f"Apply {len(selected_suggestions)} Selected Connection{'s' if len(selected_suggestions) > 1 else ''}"
        menu.add_command(
            label=apply_label,
            command=lambda: self.on_apply_selected_connections(selected_suggestions),
            state=tk.NORMAL if selected_suggestions and self.current_engine else tk.DISABLED,
        )
        menu.add_separator()

        if len(selected_ids) == 1:
            vals = self.tree.item(selected_ids[0], "values")
            source_name, target_name = vals[0], vals[1]
            vault = self.get_vault_path()

            source_path = self._resolve_note_file(vault, source_name)
            target_path = self._resolve_note_file(vault, target_name)

            menu.add_command(
                label=f"Open Source Note ('{source_name}')",
                command=lambda: self._open_file_in_os(source_path) if source_path else None,
                state=tk.NORMAL if source_path else tk.DISABLED,
            )
            menu.add_command(
                label=f"Open Target Note ('{target_name}')",
                command=lambda: self._open_file_in_os(target_path) if target_path else None,
                state=tk.NORMAL if target_path else tk.DISABLED,
            )
            menu.add_separator()

            menu.add_command(
                label="Open Source Note with Specific App...",
                command=lambda: self._open_with_specific_app(source_path) if source_path else None,
                state=tk.NORMAL if source_path else tk.DISABLED,
            )
            menu.add_command(
                label="Open Target Note with Specific App...",
                command=lambda: self._open_with_specific_app(target_path) if target_path else None,
                state=tk.NORMAL if target_path else tk.DISABLED,
            )
            menu.add_command(
                label="Reveal in File Manager",
                command=lambda: self._reveal_in_file_manager(target_path or source_path),
                state=tk.NORMAL if (target_path or source_path) else tk.DISABLED,
            )
            menu.add_separator()

            menu.add_command(
                label=f"Copy Source Name ('{source_name}')",
                command=lambda: self._copy_to_clipboard(source_name),
            )
            menu.add_command(
                label=f"Copy Target Name ('{target_name}')",
                command=lambda: self._copy_to_clipboard(target_name),
            )
            menu.add_command(
                label=f"Copy Wikilink ('[[{target_name}]]')",
                command=lambda: self._copy_to_clipboard(f"[[{target_name}]]"),
            )

        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()

    def on_apply_selected_connections(self, suggestions: list[Any]) -> None:
        if not suggestions or not self.current_engine:
            messagebox.showwarning("No Data", "No valid selected connections to apply.")
            return

        confirm = messagebox.askyesno(
            "Confirm Apply Selected",
            f"Are you sure you want to write {len(suggestions)} selected link connection(s) to your Obsidian Vault notes?\n\n"
            "This will inline or append wikilinks for the selected pairs only."
        )
        if not confirm:
            return

        self.apply_btn.config(state=tk.DISABLED)
        self.log(f"Applying {len(suggestions)} selected connection(s) to vault notes...")

        def _work():
            try:
                tx = LinkWriter(self.current_engine).apply(suggestions)
                self.root.after(0, lambda: self._on_apply_complete(tx))
            except Exception as exc:
                self.root.after(0, lambda: self.log(f"Apply Error: {exc}"))

        threading.Thread(target=_work, daemon=True).start()

    def _open_with_specific_app(self, path: Path | None) -> None:
        if not path or not path.exists():
            return
        if sys.platform == "darwin":
            app_path = filedialog.askopenfilename(
                title="Select Text Editor or Application",
                initialdir="/Applications",
                filetypes=[("Applications", "*.app"), ("All files", "*.*")],
                parent=self.root,
            )
            if app_path:
                self.log(f"Opening '{path.name}' with application: {Path(app_path).name}")
                subprocess.run(["open", "-a", app_path, str(path)])
        elif sys.platform == "win32":
            self.log(f"Opening Windows 'Open With' dialog for: {path.name}")
            subprocess.run(f'rundll32.exe shell32.dll,OpenAs_RunDLL "{path}"', shell=True)
        else:
            self.log(f"Opening Linux app chooser for: {path.name}")
            subprocess.run(["xdg-open", str(path)])

    def _reveal_in_file_manager(self, path: Path | None) -> None:
        if not path or not path.exists():
            return
        self.log(f"Revealing in file manager: {path.name}")
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", "-R", str(path)])
            elif sys.platform == "win32":
                subprocess.run(f'explorer /select,"{path}"')
            else:
                subprocess.run(["xdg-open", str(path.parent)])
        except Exception as exc:
            self.log(f"Reveal error: {exc}")

    def _copy_to_clipboard(self, text: str) -> None:
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.log(f"Copied to clipboard: {text}")

    def _open_file_in_os(self, path: Path) -> None:
        self.log(f"Opening note in OS: {path.name}")
        try:
            if sys.platform == "darwin":
                subprocess.run(["open", str(path)], check=True)
            elif sys.platform == "win32":
                os.startfile(str(path))
            else:
                subprocess.run(["xdg-open", str(path)], check=True)
        except Exception as exc:
            self.log(f"Error opening file '{path.name}': {exc}")
            messagebox.showerror("Error Opening File", f"Could not open file:\n{path}\n\nError: {exc}")

    def on_show_export_menu(self) -> None:
        if not self.current_suggestions:
            messagebox.showwarning("No Data", "No link suggestions available to export. Run a scan first.")
            return

        menu = self._menu()
        menu.add_command(label="Export to CSV (.csv)", command=self.on_export_csv)
        menu.add_command(label="Export to Markdown (.md)", command=self.on_export_md)

        x = self.export_menu_btn.winfo_rootx()
        y = self.export_menu_btn.winfo_rooty() + self.export_menu_btn.winfo_height()
        try:
            menu.tk_popup(x, y)
        finally:
            menu.grab_release()

    def on_export_csv(self) -> None:
        if not self.current_suggestions:
            messagebox.showwarning("No Data", "No link suggestions available to export. Run a scan first.")
            return

        filepath = filedialog.asksaveasfilename(
            title="Export Suggestions to CSV",
            defaultextension=".csv",
            filetypes=[("CSV files", "*.csv"), ("All files", "*.*")],
            parent=self.root,
        )
        if not filepath:
            return

        try:
            path = Path(filepath)
            with path.open("w", encoding="utf-8", newline="") as f:
                writer = csv.writer(f)
                writer.writerow(["Source", "Target", "Score"])
                for s in self.current_suggestions:
                    writer.writerow([s.source, s.target, f"{s.score:.4f}"])

            self.log(f"Exported {len(self.current_suggestions)} suggestions to CSV: {path.name}")
            messagebox.showinfo("Export Complete", f"Successfully exported to {path.name}")
        except Exception as exc:
            self.log(f"Export Error: {exc}")
            messagebox.showerror("Export Error", str(exc))

    def on_export_md(self) -> None:
        if not self.current_suggestions:
            messagebox.showwarning("No Data", "No link suggestions available to export. Run a scan first.")
            return

        filepath = filedialog.asksaveasfilename(
            title="Export Suggestions to Markdown",
            defaultextension=".md",
            filetypes=[("Markdown files", "*.md"), ("All files", "*.*")],
            parent=self.root,
        )
        if not filepath:
            return

        try:
            path = Path(filepath)
            lines = [
                "# ZettelLinker Suggestions Report",
                "",
                f"**Vault Path**: `{self.get_vault_path()}`",
                f"**Total Suggestions**: {len(self.current_suggestions)}",
                "",
                "| Source Note | Target Connection | Score |",
                "| :--- | :--- | :--- |",
            ]
            for s in self.current_suggestions:
                lines.append(f"| `{s.source}` | `[[{s.target}]]` | {s.score * 100:.1f}% |")

            path.write_text("\n".join(lines) + "\n", encoding="utf-8")
            self.log(f"Exported {len(self.current_suggestions)} suggestions to Markdown: {path.name}")
            messagebox.showinfo("Export Complete", f"Successfully exported to {path.name}")
        except Exception as exc:
            self.log(f"Export Error: {exc}")
            messagebox.showerror("Export Error", str(exc))

    def _get_current_config(self) -> tuple[Path, VaultConfig]:
        vault = self.get_vault_path()
        config = load_config(vault)
        try:
            config.semantic.threshold = float(self.sim_threshold_var.get())
        except (ValueError, TypeError):
            pass
        try:
            config.auto_write.anchor_threshold = float(self.inline_threshold_var.get())
        except (ValueError, TypeError):
            pass
        try:
            config.semantic.limit = int(self.limit_var.get())
        except (ValueError, TypeError):
            pass
        config.auto_write.enabled = self.auto_write_var.get() == "on"
        if self.heading_var.get().strip():
            config.auto_write.connections_heading = self.heading_var.get().strip()

        raw_excludes = self.exclude_names_var.get()
        config.exclude_name_contains = [s.strip() for s in raw_excludes.split(",") if s.strip()]

        raw_ex_files = self.exclude_files_var.get()
        config.exclude_files = [s.strip() for s in raw_ex_files.split(",") if s.strip()]

        config.validate()
        return vault, config

    def on_scan(self) -> None:
        vault = self.get_vault_path()
        if not self._check_vault_permission(vault):
            return

        self.scan_btn.config(state=tk.DISABLED)

        def _work():
            try:
                vault, config = self._get_current_config()
                preview = _preview_config(config)
                self.root.after(0, lambda v=vault, c=preview: self.log(f"Scanning vault '{v.name}' (threshold={c.semantic.threshold}, limit={c.semantic.limit})..."))
                progress_cb = lambda msg: self.root.after(0, lambda m=msg: self.log(m))
                state = run_vault_scan(vault, preview, progress=progress_cb)
                suggestions = state.get("suggestions", [])
                findings = state.get("audit_findings", [])
                stats = state.get("index_stats")
                self.current_engine = state.get("engine")

                self.root.after(0, lambda: self._on_scan_complete(suggestions, stats, findings))
            except Exception as exc:
                self.root.after(0, lambda: self._on_scan_error(str(exc)))

        threading.Thread(target=_work, daemon=True).start()

    def _on_scan_complete(self, suggestions: list[Any], stats: Any, findings: list[Any] | None = None) -> None:
        self.scan_btn.config(state=tk.NORMAL)
        embedded = stats.embedded if stats else 0
        reused = stats.reused if stats else 0
        self.log(
            f"Scan complete: {len(suggestions)} suggestions found "
            f"({embedded} embedded, {reused} reused). "
            "Review suggestions below and click 'Apply Connections' to write links."
        )
        self._log_findings(findings or [])
        self._render_suggestions(suggestions)

    def _log_findings(self, findings: list[Any]) -> None:
        self.current_findings = list(findings)
        if not findings:
            self.log("Audit: no ghost links, ambiguous links, or orphan notes.")
            return
        self.log(f"Audit findings: {len(findings)}")
        for finding in findings:
            target = f" -> {finding.target}" if finding.target else ""
            self.log(f"  {finding.kind}: {finding.source}{target}")

    def on_apply_connections(self) -> None:
        if not self.current_suggestions or not self.current_engine:
            messagebox.showwarning("No Scan Data", "No link suggestions available to apply. Run 'Scan Whole Vault' first.")
            return

        confirm = messagebox.askyesno(
            "Confirm Apply Connections",
            f"Are you sure you want to write {len(self.current_suggestions)} link suggestions to your Obsidian Vault notes?\n\n"
            "This will edit target notes and add wikilinks under your configured Connections Heading.\n"
            "You can click 'Undo Write' anytime to revert this action."
        )
        if not confirm:
            return

        self.apply_btn.config(state=tk.DISABLED)
        self.log(f"Applying {len(self.current_suggestions)} connection links to vault notes...")

        def _work():
            try:
                tx = LinkWriter(self.current_engine).apply(self.current_suggestions)
                self.root.after(0, lambda: self._on_apply_complete(tx))
            except Exception as exc:
                self.root.after(0, lambda: self.log(f"Apply Error: {exc}"))

        threading.Thread(target=_work, daemon=True).start()

    def _on_apply_complete(self, tx: Any) -> None:
        self.apply_btn.config(state=tk.NORMAL)
        if tx and tx.transaction:
            self.log(
                f"Successfully applied connections. Transaction {tx.transaction} "
                f"({len(tx.modified)} files modified, {tx.links_added} links)."
            )
            messagebox.showinfo(
                "Apply Complete",
                f"Successfully applied connections.\nModified {len(tx.modified)} files.\n\n"
                "Click 'Undo Write' anytime to revert.",
            )
        else:
            self.log("No new links were written (notes already contain these connections).")
            messagebox.showinfo("Apply Complete", "No new links were written (all suggested connections already exist in notes).")

    def _on_scan_error(self, err_msg: str) -> None:
        self.scan_btn.config(state=tk.NORMAL)
        self.log(f"Scan Error: {err_msg}")
        if "Permission" in err_msg or "permission" in err_msg:
            messagebox.showerror(
                "Permission Error",
                f"{err_msg}\n\n"
                "System Settings Action Required:\n"
                "• macOS: Go to System Settings -> Privacy & Security -> Files and Folders (or Full Disk Access).\n"
                "• Windows/Linux: Ensure your user account has read permissions for the target folder."
            )
        else:
            messagebox.showerror("Scan Error", err_msg)

    def on_suggest(self) -> None:
        note_name = self.suggest_note_var.get().strip()
        if not note_name:
            messagebox.showwarning("Input Required", "Please enter a note name to inspect.")
            return

        vault = self.get_vault_path()
        if not self._check_vault_permission(vault):
            return

        self.log(f"Analyzing candidate links for note: {note_name}")

        def _work():
            try:
                vault, config = self._get_current_config()
                preview = _preview_config(config)
                progress_cb = lambda msg: self.root.after(0, lambda m=msg: self.log(m))
                state = run_note_suggest(vault, preview, note_name, progress=progress_cb)
                self.current_engine = state.get("engine")
                suggestions = state.get("suggestions", [])
                identity = state.get("note", note_name)
                self.root.after(0, lambda: self._on_suggest_complete(suggestions, identity))
            except Exception as exc:
                self.root.after(0, lambda: self.log(f"Suggest error: {exc}"))

        threading.Thread(target=_work, daemon=True).start()

    def _on_suggest_complete(self, suggestions: list[Any], name: str) -> None:
        self.log(f"Found {len(suggestions)} suggestions for '{name}'.")
        self._render_suggestions(suggestions)

    def _on_suggest_keyrelease(self, event: tk.Event) -> None:
        if event.keysym in ("Return", "Up", "Down", "Escape", "Tab"):
            if event.keysym == "Down" and hasattr(self, "suggest_listbox") and self.suggest_listbox:
                try:
                    self.suggest_listbox.focus_set()
                    if self.suggest_listbox.size() > 0:
                        self.suggest_listbox.selection_clear(0, tk.END)
                        self.suggest_listbox.selection_set(0)
                        self.suggest_listbox.activate(0)
                except Exception:
                    pass
            return

        query = self.suggest_note_var.get().strip().casefold()
        if not query:
            self._hide_suggest_dropdown()
            return

        if not hasattr(self, "cached_note_names") or not self.cached_note_names:
            try:
                vault, config = self._get_current_config()
                notes = discover_notes(vault, config)
                self.cached_note_names = [n.basename for n in notes]
            except Exception:
                self.cached_note_names = []

        matches = [name for name in self.cached_note_names if query in name.casefold()]
        if not matches:
            self._hide_suggest_dropdown()
            return

        self._show_suggest_dropdown(matches[:8])

    def _show_suggest_dropdown(self, matches: list[str]) -> None:
        if not hasattr(self, "suggest_popup") or not self.suggest_popup or not self.suggest_popup.winfo_exists():
            parent = self.suggest_ent.winfo_toplevel()
            self.suggest_popup = tw = tk.Toplevel(parent)
            tw.transient(parent)
            tw.wm_overrideredirect(True)
            self.suggest_listbox = tk.Listbox(
                tw,
                font=("sans-serif", 10),
                bg="#FFFFFF",
                fg="#000000",
                selectbackground="#007ACC",
                selectforeground="#FFFFFF",
                height=min(len(matches), 8),
                relief=tk.SOLID,
                borderwidth=1,
            )
            self.suggest_listbox.pack(fill=tk.BOTH, expand=True)
            self._paint_suggest_popup()
            self.suggest_listbox.bind("<ButtonRelease-1>", self._on_select_suggest_item)
            self.suggest_listbox.bind("<Return>", self._on_select_suggest_item)
            self.suggest_listbox.bind("<Escape>", lambda _e: self._hide_suggest_dropdown())

        x = self.suggest_ent.winfo_rootx()
        y = self.suggest_ent.winfo_rooty() + self.suggest_ent.winfo_height() + 2
        w = max(self.suggest_ent.winfo_width(), 220)
        self.suggest_popup.wm_geometry(f"{w}x{min(len(matches), 8)*22+4}+{x}+{y}")
        self.suggest_popup.lift()

        self.suggest_listbox.delete(0, tk.END)
        for m in matches:
            self.suggest_listbox.insert(tk.END, m)

    def _on_select_suggest_item(self, _event: tk.Event | None = None) -> None:
        if hasattr(self, "suggest_listbox") and self.suggest_listbox:
            sel = self.suggest_listbox.curselection()
            if sel:
                val = self.suggest_listbox.get(sel[0])
                self.suggest_note_var.set(val)
                self._hide_suggest_dropdown()
                self.on_suggest()

    def _hide_suggest_dropdown(self, _event: tk.Event | None = None) -> None:
        if hasattr(self, "suggest_popup") and self.suggest_popup:
            try:
                self.suggest_popup.destroy()
            except Exception:
                pass
            self.suggest_popup = None
            self.suggest_listbox = None

    def on_undo(self) -> None:
        vault = self.get_vault_path()
        self.log("Executing undo...")
        try:
            tx = undo_last(vault)
            if tx.transaction:
                self.log(f"Restored {len(tx.modified)} files from transaction {tx.transaction}")
                messagebox.showinfo("Undo Complete", f"Restored {len(tx.modified)} files.")
            else:
                self.log("No transaction available to undo.")
                messagebox.showinfo("Undo", "No previous write transaction to undo.")
        except Exception as exc:
            self.log(f"Undo error: {exc}")


class ToolTip:
    """Non-intrusive in-window hover tooltip for Tkinter widgets."""

    def __init__(self, widget: tk.Widget, text: str):
        self.widget = widget
        self.text = text
        self.tip_label: tk.Label | None = None
        self.widget.bind("<Enter>", self.show_tip)
        self.widget.bind("<Leave>", self.hide_tip)

    def show_tip(self, _event: tk.Event | None = None) -> None:
        if self.tip_label or not self.text:
            return
        try:
            parent = self.widget.winfo_toplevel()
            x = self.widget.winfo_rootx() - parent.winfo_rootx() + 10
            y = self.widget.winfo_rooty() - parent.winfo_rooty() + self.widget.winfo_height() + 2

            palette = getattr(parent, "_zettel_palette", None)
            tip_bg = palette["raised"] if palette else "#2D2D2D"
            tip_fg = palette["text"] if palette else "#FFFFFF"
            self.tip_label = tk.Label(
                parent,
                text=self.text,
                justify=tk.LEFT,
                background=tip_bg,
                foreground=tip_fg,
                relief=tk.SOLID,
                borderwidth=1,
                font=("sans-serif", 9, "normal"),
                padx=6,
                pady=4,
                wraplength=260,
            )
            self.tip_label.place(x=x, y=y)
            self.tip_label.lift()
        except Exception:
            self.tip_label = None

    def hide_tip(self, _event: tk.Event | None = None) -> None:
        if self.tip_label:
            try:
                self.tip_label.destroy()
            except Exception:
                pass
            self.tip_label = None


def _preview_config(config: VaultConfig) -> VaultConfig:
    """Scan and suggest in the window stay previews. Apply is the only writer."""
    return replace(config, auto_write=replace(config.auto_write, enabled=False))


def run_gui_app() -> None:
    root = tk.Tk()
    _app = ZettelLinkerApp(root)
    root.mainloop()


if __name__ == "__main__":
    run_gui_app()
