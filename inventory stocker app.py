import sqlite3
import streamlit as st
from pathlib import Path
import csv

# Set up the web page layout
st.set_page_config(page_title="Repair Parts Inventory", layout="wide")

def get_db_path():
    # In cloud hosting, saving to the current directory is safest
    return Path(".") / "inventory.db"

DB_PATH = get_db_path()
STATUSES = ["Working", "Test needed", "For parts"]

# ---------------------------------------------------------------- database
class DB:
    def __init__(self, path):
        self.conn = sqlite3.connect(path, check_same_thread=False)
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
        if "status" not in cols:  
            self.conn.execute("ALTER TABLE items ADD COLUMN status TEXT NOT NULL DEFAULT ''")
        self.conn.commit()

    def by_barcode(self, code):
        return self.conn.execute("SELECT * FROM items WHERE barcode=?", (code,)).fetchone()

    def search(self, text="", device="All", location="All", status="All"):
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
        
        rows = self.conn.execute(sql, params).fetchall()
        if device != "All":
            want = device.lower()
            rows = [r for r in rows if want in [d.strip().lower() for d in r["device"].split(",")]]
        return rows

    def add(self, d):
        try:
            self.conn.execute(
                "INSERT INTO items(barcode,name,device,status,location,qty,min_qty,notes) VALUES(?,?,?,?,?,?,?,?)",
                (d["barcode"], d["name"], d["device"], d["status"], d["location"], d["qty"], d["min_qty"], d["notes"]),
            )
            self.conn.commit()
            return True
        except sqlite3.IntegrityError:
            return False

    def adjust(self, item_id, delta):
        self.conn.execute("UPDATE items SET qty=MAX(qty+?,0) WHERE id=?", (delta, item_id))
        self.conn.commit()

    def locations(self):
        rows = self.conn.execute("SELECT DISTINCT location FROM items WHERE location<>'' ORDER BY location").fetchall()
        return [r[0] for r in rows]

    def devices(self):
        seen = {}
        for r in self.conn.execute("SELECT device FROM items WHERE device<>''"):
            for part in r[0].split(","):
                part = part.strip()
                if part: 
                    seen.setdefault(part.lower(), part)
        return sorted(seen.values(), key=str.lower)


# Initialize our database connection
db = DB(DB_PATH)

# ---------------------------------------------------------------- Web Interface
st.title("🔧 Repair Parts Inventory")

# Sidebar for Barcode Scanner
with st.sidebar:
    st.header("Scan Station")
    scan_mode = st.radio("Scan Action", ["Add Stock (+1)", "Remove Stock (-1)"])
    
    # Text input for barcode scanners
    barcode_input = st.text_input("Scan Box (Keep cursor focused here)", key="scanner", value="")
    
    if barcode_input:
        item = db.by_barcode(barcode_input)
        if item:
            delta = 1 if scan_mode == "Add Stock (+1)" else -1
            db.adjust(item["id"], delta)
            st.success(f"Updated {item['name']}! New Qty: {max(item['qty'] + delta, 0)}")
        else:
            st.warning(f"Barcode '{barcode_input}' not found. Use the manual form to add it.")

# Search and Filters Layout
col1, col2, col3, col4 = st.columns(4)
with col1:
    search_text = st.text_input("Search parts...")
with col2:
    loc_filter = st.selectbox("Location Filter", ["All"] + db.locations())
with col3:
    dev_filter = st.selectbox("Device Filter", ["All"] + db.devices())
with col4:
    stat_filter = st.selectbox("Status Filter", ["All"] + STATUSES)

# Add New Item Dropdown Form
with st.expander("➕ Add New Item Manually"):
    with st.form("add_item_form", clear_on_submit=True):
        new_barcode = st.text_input("Barcode (Leave blank if none)", value=barcode_input if barcode_input and not db.by_barcode(barcode_input) else "")
        new_name = st.text_input("Part Name *")
        new_device = st.text_input("Device(s) (separate multiple with commas)")
        new_status = st.selectbox("Status", [""] + STATUSES)
        new_location = st.text_input("Location")
        new_qty = st.number_input("Starting Quantity", min_value=0, step=1)
        new_min = st.number_input("Low-stock Alert Qty", min_value=0, step=1)
        new_notes = st.text_area("Notes")
        
        submitted = st.form_submit_button("Save Item")
        if submitted:
            if not new_name:
                st.error("Part Name is required!")
            else:
                success = db.add({
                    "barcode": new_barcode if new_barcode else None,
                    "name": new_name, "device": new_device, "status": new_status,
                    "location": new_location, "qty": new_qty, "min_qty": new_min, "notes": new_notes
                })
                if success:
                    st.success("Item added successfully!")
                    st.rerun()
                else:
                    st.error("This barcode already exists in your inventory!")

# Fetch and display items matching filters
items = db.search(text=search_text, device=dev_filter, location=loc_filter, status=stat_filter)

if items:
    # Build data array for clean presentation table
    table_data = []
    for r in items:
        alert = "⚠️ LOW STOCK" if r["qty"] <= r["min_qty"] else "✅ OK"
        table_data.append({
            "ID": r["id"], "Part Name": r["name"], "Device": r["device"], 
            "Status": r["status"], "Location": r["location"], 
            "Quantity": r["qty"], "Alert": alert, "Barcode": r["barcode"]
        })
    
    st.dataframe(table_data, use_container_width=True, hide_index=True)
    
    # Manual Quick Adjustment Tools
    st.subheader("Quick Quantity Adjustment Panel")
    t1, t2, t3 = st.columns(3)
    with t1:
        target_id = st.number_input("Enter Item ID from table", min_value=1, step=1)
    with t2:
        action = st.selectbox("Action", ["+1", "-1"])
    with t3:
        st.write("") # blank alignment space
        if st.button("Apply Adjustment", use_container_width=True):
            delta = 1 if action == "+1" else -1
            db.adjust(target_id, delta)
            st.success(f"Adjusted item ID {target_id}!")
            st.rerun()
else:
    st.info("No items found matching the selected filters.")

