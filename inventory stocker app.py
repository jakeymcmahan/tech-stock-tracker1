"""
Repair Parts Inventory
----------------------
Run:  python inventory.py

- Handheld scanner (USB/keyboard mode): just keep the cursor in the Scan box.
  * Known barcode  -> item found, stock +1 (Add mode) or -1 (Remove mode)
  * Unknown barcode -> Add-item form opens with the barcode filled in
- No barcode? Click "Add item" and fill it in by hand.
- Filter/sort by location and device. Click a column header to sort.
- Separate multiple devices with commas, e.g.  iPhone 12, iPhone 12 Pro
- Data lives in inventory.db next to this file.
"""
import csv
import sqlite3
import tkinter as tk
from pathlib import Path
from tkinter import filedialog, messagebox, ttk

def get_db_path():
    """Save next to the script if possible, otherwise in a folder in your home directory."""
    try:
        folder = Path(__file__).resolve().parent
        test = folder / ".write_test"
        test.touch()
        test.unlink()
    except Exception:
        folder = Path.home() / "RepairInventory"
        folder.mkdir(parents=True, exist_ok=True)
    return folder / "inventory.db"


DB_PATH = get_db_path()

STATUSES = ["Working", "Test needed", "For parts"]

COLUMNS = [
    ("name", "Part", 220),
    ("device", "For device", 160),
    ("status", "Status", 100),
    ("location", "Location", 110),
    ("qty", "Qty", 55),
    ("min_qty", "Alert at", 60),
    ("barcode", "Barcode", 140),
]


# ---------------------------------------------------------------- database
class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute(
            """CREATE TABLE IF NOT EXISTS items(
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                barcode TEXT UNIQUE,
                name TEXT NOT NULL,
                device TEXT NOT NULL DEFAULT '',
                location TEXT NOT NULL DEFAULT '',
                qty INTEGER NOT NULL DEFAULT 0,
                min_qty INTEGER NOT NULL DEFAULT 0,
                notes TEXT NOT NULL DEFAULT '')"""
        )
        cols = [r[1] for r in self.conn.execute("PRAGMA table_info(items)")]
        if "status" not in cols:  # upgrade older databases
            self.conn.execute("ALTER TABLE items ADD COLUMN status TEXT NOT NULL DEFAULT ''")
        self.conn.commit()

    def by_barcode(self, code):
        return self.conn.execute("SELECT * FROM items WHERE barcode=?", (code,)).fetchone()

    def get(self, item_id):
        return self.conn.execute("SELECT * FROM items WHERE id=?", (item_id,)).fetchone()

    def search(self, text="", device="All", location="All", sort_col=None, reverse=False, status="All"):
        sql = "SELECT * FROM items WHERE 1=1"
        params = []
        if text:
            sql += " AND (name LIKE ? OR barcode LIKE ? OR notes LIKE ? OR device LIKE ?)"
            params += [f"%{text}%"] * 4
        if location != "All":
            sql += " AND location=? COLLATE NOCASE"
            params.append(location)
        if status != "All":
            sql += " AND status=?"
            params.append(status)

        direction = "DESC" if reverse else "ASC"
        if sort_col in ("qty", "min_qty"):
            order = f"{sort_col} {direction}"
        elif sort_col in ("name", "device", "status", "location", "barcode"):
            order = f"{sort_col} COLLATE NOCASE {direction}"
        else:
            order = "location COLLATE NOCASE, name COLLATE NOCASE"
        sql += f" ORDER BY {order}, name COLLATE NOCASE"

        rows = self.conn.execute(sql, params).fetchall()
        if device != "All":
            want = device.lower()
            rows = [r for r in rows if want in [d.strip().lower() for d in r["device"].split(",")]]
        return rows

    def add(self, d):
        cur = self.conn.execute(
            "INSERT INTO items(barcode,name,device,status,location,qty,min_qty,notes) VALUES(?,?,?,?,?,?,?,?)",
            (d["barcode"], d["name"], d["device"], d["status"], d["location"], d["qty"], d["min_qty"], d["notes"]),
        )
        self.conn.commit()
        return cur.lastrowid

    def update(self, item_id, d):
        self.conn.execute(
            "UPDATE items SET barcode=?,name=?,device=?,status=?,location=?,qty=?,min_qty=?,notes=? WHERE id=?",
            (d["barcode"], d["name"], d["device"], d["status"], d["location"], d["qty"], d["min_qty"], d["notes"], item_id),
        )
        self.conn.commit()

    def adjust(self, item_id, delta):
        self.conn.execute("UPDATE items SET qty=MAX(qty+?,0) WHERE id=?", (delta, item_id))
        self.conn.commit()

    def delete(self, item_id):
        self.conn.execute("DELETE FROM items WHERE id=?", (item_id,))
        self.conn.commit()

    def locations(self):
        rows = self.conn.execute(
            "SELECT DISTINCT location FROM items WHERE location<>'' ORDER BY location COLLATE NOCASE"
        ).fetchall()
        return [r[0] for r in rows]

    def devices(self):
        seen = {}
        for r in self.conn.execute("SELECT device FROM items WHERE device<>''"):
            for part in r[0].split(","):
                part = part.strip()
                if part:
                    seen.setdefault(part.lower(), part)
        return sorted(seen.values(), key=str.lower)


# ------------------------------------------------------------- item dialog
class ItemDialog(tk.Toplevel):
    FIELDS = [
        ("barcode", "Barcode (blank if none)"),
        ("name", "Part name *"),
        ("device", "For device(s)"),
        ("status", "Status"),
        ("location", "Location"),
        ("qty", "Quantity"),
        ("min_qty", "Low-stock alert at"),
        ("notes", "Notes"),
    ]

    def __init__(self, parent, db, item=None, barcode="", qty=0):
        super().__init__(parent)
        self.db = db
        self.item = item
        self.result = None
        self.title("Edit item" if item else "Add item")
        self.transient(parent)
        self.resizable(False, False)

        self.vars = {k: tk.StringVar() for k, _ in self.FIELDS}
        if item:
            for k, _ in self.FIELDS:
                self.vars[k].set("" if item[k] is None else str(item[k]))
        else:
            self.vars["barcode"].set(barcode)
            self.vars["qty"].set(str(qty))
            self.vars["min_qty"].set("0")

        frm = ttk.Frame(self, padding=14)
        frm.grid()
        first = None
        for row, (key, label) in enumerate(self.FIELDS):
            ttk.Label(frm, text=label).grid(row=row, column=0, sticky="w", pady=4, padx=(0, 10))
            if key == "device":
                w = ttk.Combobox(frm, textvariable=self.vars[key], values=db.devices(), width=34)
            elif key == "status":
                w = ttk.Combobox(frm, textvariable=self.vars[key], values=[""] + STATUSES,
                                 state="readonly", width=34)
            elif key == "location":
                w = ttk.Combobox(frm, textvariable=self.vars[key], values=db.locations(), width=34)
            else:
                w = ttk.Entry(frm, textvariable=self.vars[key], width=36)
            w.grid(row=row, column=1, sticky="ew", pady=4)
            if key == "barcode":
                # Scanner sends Enter after the code; move on instead of saving.
                w.bind("<Return>", lambda e: (self.name_entry.focus_set(), "break")[1])
            if key == "name":
                self.name_entry = w
            if first is None:
                first = w

        btns = ttk.Frame(frm)
        btns.grid(row=len(self.FIELDS), column=0, columnspan=2, sticky="e", pady=(10, 0))
        ttk.Button(btns, text="Cancel", command=self.destroy).pack(side="right", padx=(6, 0))
        ttk.Button(btns, text="Save", command=self.save).pack(side="right")

        self.bind("<Return>", lambda e: self.save())
        self.bind("<Escape>", lambda e: self.destroy())
        self.grab_set()
        (self.name_entry if barcode or item else first).focus_set()
        self.wait_window(self)

    def save(self):
        d = {k: v.get().strip() for k, v in self.vars.items()}
        if not d["name"]:
            messagebox.showwarning("Missing name", "Give the part a name.", parent=self)
            return
        try:
            d["qty"] = max(int(d["qty"] or 0), 0)
            d["min_qty"] = max(int(d["min_qty"] or 0), 0)
        except ValueError:
            messagebox.showwarning("Numbers only", "Quantity and alert level must be whole numbers.", parent=self)
            return
        d["barcode"] = d["barcode"] or None
        try:
            if self.item:
                self.db.update(self.item["id"], d)
                self.result = self.item["id"]
            else:
                self.result = self.db.add(d)
        except sqlite3.IntegrityError:
            messagebox.showwarning("Duplicate barcode", "Another item already uses that barcode.", parent=self)
            return
        self.destroy()


# ---------------------------------------------------------------- main app
class App(tk.Tk):
    def __init__(self):
        super().__init__()
        self.title("Repair Parts Inventory")
        self.geometry("960x600")
        self.db = DB(DB_PATH)
        self.sort_col = None
        self.sort_rev = False

        self.scan_var = tk.StringVar()
        self.mode = tk.StringVar(value="add")
        self.search_var = tk.StringVar()
        self.dev_filter = tk.StringVar(value="All")
        self.loc_filter = tk.StringVar(value="All")
        self.cond_filter = tk.StringVar(value="All")
        self.status = tk.StringVar(value="Ready. Scan a barcode.")

        self.build_ui()
        self.refresh()
        self.scan_entry.focus_set()

    def build_ui(self):
        top = ttk.Frame(self, padding=(10, 10, 10, 4))
        top.pack(fill="x")
        ttk.Label(top, text="SCAN:", font=("TkDefaultFont", 12, "bold")).pack(side="left")
        self.scan_entry = ttk.Entry(top, textvariable=self.scan_var, font=("TkDefaultFont", 14), width=24)
        self.scan_entry.pack(side="left", padx=8)
        self.scan_entry.bind("<Return>", self.on_scan)
        for val, label in (("add", "Add stock (+1)"), ("remove", "Use part (-1)"), ("lookup", "Look up only")):
            ttk.Radiobutton(top, text=label, value=val, variable=self.mode,
                            command=self.scan_entry.focus_set).pack(side="left", padx=6)

        self.status_lbl = ttk.Label(self, textvariable=self.status, padding=(12, 0, 10, 6))
        self.status_lbl.pack(fill="x")

        flt = ttk.Frame(self, padding=(10, 0, 10, 6))
        flt.pack(fill="x")
        ttk.Label(flt, text="Device:").pack(side="left")
        self.dev_box = ttk.Combobox(flt, textvariable=self.dev_filter, state="readonly", width=22)
        self.dev_box.pack(side="left", padx=(4, 12))
        ttk.Label(flt, text="Location:").pack(side="left")
        self.loc_box = ttk.Combobox(flt, textvariable=self.loc_filter, state="readonly", width=16)
        self.loc_box.pack(side="left", padx=(4, 12))
        ttk.Label(flt, text="Status:").pack(side="left")
        self.cond_box = ttk.Combobox(flt, textvariable=self.cond_filter, state="readonly",
                                     values=["All"] + STATUSES, width=12)
        self.cond_box.pack(side="left", padx=(4, 12))
        self.cond_box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        ttk.Label(flt, text="Search:").pack(side="left")
        se = ttk.Entry(flt, textvariable=self.search_var, width=22)
        se.pack(side="left", padx=4)
        ttk.Button(flt, text="Clear", command=self.clear_filters).pack(side="left", padx=6)
        self.dev_box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.loc_box.bind("<<ComboboxSelected>>", lambda e: self.refresh())
        self.search_var.trace_add("write", lambda *a: self.refresh())

        mid = ttk.Frame(self, padding=(10, 0))
        mid.pack(fill="both", expand=True)
        self.tree = ttk.Treeview(mid, columns=[c[0] for c in COLUMNS], show="headings", selectmode="browse")
        for key, label, width in COLUMNS:
            self.tree.heading(key, text=label, command=lambda k=key: self.sort_by(k))
            self.tree.column(key, width=width, anchor="center" if key in ("qty", "min_qty") else "w")
        self.tree.tag_configure("low", background="#ffd9d9")
        self.tree.tag_configure("out", background="#ffb3b3")
        sb = ttk.Scrollbar(mid, orient="vertical", command=self.tree.yview)
        self.tree.configure(yscrollcommand=sb.set)
        self.tree.pack(side="left", fill="both", expand=True)
        sb.pack(side="right", fill="y")
        self.tree.bind("<Double-1>", lambda e: self.edit_selected())

        bot = ttk.Frame(self, padding=10)
        bot.pack(fill="x")
        ttk.Button(bot, text="Add item (no barcode)", command=self.add_manual).pack(side="left")
        ttk.Button(bot, text="Edit", command=self.edit_selected).pack(side="left", padx=6)
        ttk.Button(bot, text="+1", width=4, command=lambda: self.bump(1)).pack(side="left")
        ttk.Button(bot, text="-1", width=4, command=lambda: self.bump(-1)).pack(side="left", padx=4)
        ttk.Button(bot, text="Delete", command=self.delete_selected).pack(side="left", padx=6)
        ttk.Button(bot, text="Export CSV", command=self.export_csv).pack(side="right")
        self.count_lbl = ttk.Label(bot, text="")
        self.count_lbl.pack(side="right", padx=12)

    # ----- display
    def refresh(self, select_id=None):
        devs = ["All"] + self.db.devices()
        locs = ["All"] + self.db.locations()
        self.dev_box["values"] = devs
        self.loc_box["values"] = locs
        if self.dev_filter.get() not in devs:
            self.dev_filter.set("All")
        if self.loc_filter.get() not in locs:
            self.loc_filter.set("All")

        rows = self.db.search(self.search_var.get().strip(), self.dev_filter.get(),
                              self.loc_filter.get(), self.sort_col, self.sort_rev,
                              self.cond_filter.get())
        prev = select_id or self.selected_id()
        self.tree.delete(*self.tree.get_children())
        for r in rows:
            tag = ()
            if r["qty"] == 0:
                tag = ("out",)
            elif r["qty"] <= r["min_qty"]:
                tag = ("low",)
            self.tree.insert("", "end", iid=str(r["id"]), tags=tag,
                             values=[r["name"], r["device"], r["status"], r["location"],
                                     r["qty"], r["min_qty"], r["barcode"] or ""])
        if prev and self.tree.exists(str(prev)):
            self.tree.selection_set(str(prev))
            self.tree.see(str(prev))
        total = sum(r["qty"] for r in rows)
        self.count_lbl.config(text=f"{len(rows)} items, {total} units")

        for key, label, _ in COLUMNS:
            arrow = ""
            if key == self.sort_col:
                arrow = " ▼" if self.sort_rev else " ▲"
            self.tree.heading(key, text=label + arrow)

    def sort_by(self, col):
        if self.sort_col == col:
            self.sort_rev = not self.sort_rev
        else:
            self.sort_col, self.sort_rev = col, False
        self.refresh()

    def clear_filters(self):
        self.dev_filter.set("All")
        self.loc_filter.set("All")
        self.cond_filter.set("All")
        self.search_var.set("")
        self.refresh()
        self.scan_entry.focus_set()

    def selected_id(self):
        sel = self.tree.selection()
        return int(sel[0]) if sel else None

    def say(self, msg):
        self.status.set(msg)

    # ----- scanning
    def on_scan(self, _event=None):
        code = self.scan_var.get().strip()
        self.scan_var.set("")
        if not code:
            return
        item = self.db.by_barcode(code)
        mode = self.mode.get()

        if item:
            if mode == "add":
                self.db.adjust(item["id"], 1)
            elif mode == "remove":
                if item["qty"] <= 0:
                    self.bell()
                    self.say(f"⚠ {item['name']} is already at 0 — nothing to remove.")
                    self.refresh(item["id"])
                    return
                self.db.adjust(item["id"], -1)
            item = self.db.get(item["id"])
            where = f" | {item['location']}" if item["location"] else ""
            dev = f" | for {item['device']}" if item["device"] else ""
            self.say(f"✔ {item['name']}{dev}{where} | now {item['qty']} in stock")
            if item["qty"] <= item["min_qty"]:
                self.say(self.status.get() + "  (LOW)")
            self.clear_filters_if_hidden(item)
            self.refresh(item["id"])
        else:
            if mode == "remove":
                self.bell()
                self.say(f"⚠ Unknown barcode {code}. Switch to Add mode to register it.")
                return
            self.say(f"New barcode {code}. Fill in the details.")
            dlg = ItemDialog(self, self.db, barcode=code, qty=1 if mode == "add" else 0)
            if dlg.result:
                self.say("✔ Saved new item.")
                self.clear_filters_if_hidden(self.db.get(dlg.result))
                self.refresh(dlg.result)
        self.scan_entry.focus_set()

    def clear_filters_if_hidden(self, item):
        """If a filter is hiding the scanned item, reset filters so it shows."""
        ids = {r["id"] for r in self.db.search(self.search_var.get().strip(),
                                               self.dev_filter.get(), self.loc_filter.get(),
                                               status=self.cond_filter.get())}
        if item["id"] not in ids:
            self.dev_filter.set("All")
            self.loc_filter.set("All")
            self.cond_filter.set("All")
            self.search_var.set("")

    # ----- manual actions
    def add_manual(self):
        dlg = ItemDialog(self, self.db)
        if dlg.result:
            self.refresh(dlg.result)
            self.say("✔ Item added.")
        self.scan_entry.focus_set()

    def edit_selected(self):
        item_id = self.selected_id()
        if not item_id:
            return
        dlg = ItemDialog(self, self.db, item=self.db.get(item_id))
        if dlg.result:
            self.refresh(dlg.result)
        self.scan_entry.focus_set()

    def bump(self, delta):
        item_id = self.selected_id()
        if item_id:
            self.db.adjust(item_id, delta)
            self.refresh(item_id)
        self.scan_entry.focus_set()

    def delete_selected(self):
        item_id = self.selected_id()
        if not item_id:
            return
        item = self.db.get(item_id)
        if messagebox.askyesno("Delete", f"Delete '{item['name']}' permanently?", parent=self):
            self.db.delete(item_id)
            self.refresh()
        self.scan_entry.focus_set()

    def export_csv(self):
        path = filedialog.asksaveasfilename(defaultextension=".csv", initialfile="inventory.csv",
                                            filetypes=[("CSV", "*.csv")])
        if not path:
            return
        rows = self.db.search(sort_col=None)
        with open(path, "w", newline="", encoding="utf-8") as f:
            w = csv.writer(f)
            w.writerow(["Part", "Device", "Status", "Location", "Qty", "Alert at", "Barcode", "Notes"])
            for r in rows:
                w.writerow([r["name"], r["device"], r["status"], r["location"], r["qty"],
                            r["min_qty"], r["barcode"] or "", r["notes"]])
        self.say(f"Exported {len(rows)} items to {path}")
        self.scan_entry.focus_set()


if __name__ == "__main__":
    App().mainloop()
