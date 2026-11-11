import json

from document_intelligence.schema import EXTRACTION_SCHEMA


EXTRACTION_RULES = """
EXTRACTION RULES (read carefully):
- Format output as JSON matching the schema exactly — no extra keys, no missing keys.
- EXACT COPY (CASE SENSITIVE): Copy all text exactly as written. Do not autocorrect or normalize.
- Zero and Placeholder Values: If a label exists but its value is "0", "0.00", or "-", copy it exactly. Do NOT treat as empty.
- Extract ONLY text explicitly written in the receipt. Never calculate, sum, or infer.
- Numbers: copy EXACTLY as written ("193.00", "5.00%", "0.00") — no conversion.
- Clean Numbers: For all price/amount fields, extract ONLY the numeric value. NEVER include currency symbols (Rp, $, RM, €, etc.).
- Strings not found: use "" (empty string).
- Arrays not found: use [] (empty array). Do NOT put empty placeholder objects inside arrays.

receipt_info:
- receipt_id: look for Receipt No, Invoice No, Bill No, Ref No, or similar.
- tax_id: is the store's tax registration number (e.g. NPWP, GST ID).

store_name & store_location:
- store_name: extract the official name of the store as printed on the receipt. This is often at the top and sometimes at the bottom. Also be aware with store include the branch name in the store name (e.g., "ALFAMART Kebayoran Lama"). It make sure included, cause that's not location store.
- store_location: extract the full address of the store as printed on the receipt. This may include street name, number, city, postal code, and other location details. If the receipt only has a general location (e.g., "Jakarta"), copy that as is.

store_contacts:
- Extract the label (e.g., "Tel", "Tel.", "Phone", "Fax", "WA", "Website", etc.) exactly as written into "type".
- If there is NO label for a contact value, "type" MUST be "".
- DO NOT classify or guess the type. No label = empty type, always.
- Example: "Tel.: 07-123" → {"type": "Tel.", "value": "07-123"}
- Example: "Fax: -" (no value) → {"type": "Fax", "value": "-"}
- Example: "www.store.com" (no "Website" label) → {"type": "", "value": "www.store.com"}
- Example: "Fb/Ig: @ayam_kicau" (multi type) → {"type": "Fb", "value": "@ayam_kicau"}, {"type": "Ig", "value": "@ayam_kicau"}

payment_time & time_unit:
- payment_time: Copy only numbers/colon (e.g., "15:34:15", "02:44").
- time_unit: Copy only the unit IF written (e.g., "AM", "PM", "WIB", "UTC", "+07:00", "+08:00"). Otherwise "".
- If multiple times exist, select the chronologically latest time, or pick the one closest to "TOTAL".

items:
- item_name: extract ONLY the descriptive product text. DO NOT include internal codes, SKUs, barcodes, or symbols bullet points (e.g., •, -, *). Group a main item and its sub-menu items into a single item_name ONLY IF the sub-menu items do not have individual prices. Separate sub-menu items with a single space into distinct, individual item_name entries IF they have their own explicit prices.
- quantity: the number of units. Copy only the number — never include "x", "@", "PCS", "*".
- unit_price: price for ONE single unit.
- total_price: final extended price for that line.
- discount_label: copy exact text of any per-item discount label (e.g., "DISKON 20%", "Member Disc."). Use "" if none.
- discount_price: copy the number printed next to/below discount_label. Include "-" if written. Use "" if none.
- tax_label: tax code or category on the item line (e.g., "SR", "ZRL", "6%"). Use "" if not shown per item.

payment summary:
- total_items: fill ONLY if explicitly written without self-counting, prioritizing "Total Qty" (total pieces) if available, or falling back to "Total Item" (row count) if "Total Qty" is missing.
- subtotal_price: look for "Subtotal", "Total Sales (Excl. GST)", "Net Amount", "Total (Excl. Tax)".
- currency: symbol ($, Rp, RM, €, etc.) ONLY if printed on receipt. Do not infer from location. Do not inclue the symbol (,.).
- discount labels and amounts: for each line item that reduces the total price (e.g., 'Discount 35%', 'KOTA HEMAT', 'Voucher', 'Promo Code', 'Total Diskon', 'Total Voucher', or similar), create a separate entry with the exact label as 'discount_name' and the numeric value as 'amount'. Include the negative sign '-' if written.
- taxes: each tax line — exact label → "tax_name", numeric value → "amount" (e.g. tax_name: "GST 6%", amount: "5000", etc) also include total tax if writen.
- additional_charges: service charge, delivery fee, etc. — exact label → "charge_name", value → "amount" (e.g. charge_name: "Biaya perngiriman", amount: "16,000", etc). also include total charge if writen.
- grand_total: final amount paid. Copy exactly as printed. Do NOT calculate.
- payment_method: copy label exactly as written. Do NOT translate (keep "Tunai", not "Cash").
- tendered: amount handed over by customer.
- rounding: include "+" or "-" sign if explicitly written.
- change: amount returned to customer.

DATA PRIVACY: Skip personal names (cashier/customer) and card numbers.
""".strip()

FEW_SHOT_RECEIPT_TEXT = """SUNRISE TRADING SDN BHD
No. 12, Jalan Bahagia 3, Taman Sejahtera
81300 Johor Bahru, Johor
Tel.: 07-3881234
Fax: 07-3885678
ryuk@gmail.com
GST ID: 001234567890
------------------------------
TAX INVOICE
INVOICE NO : INV-20190115-0042
DATE       : 15/01/2019 14:35:22 +08:00
------------------------------
DESCRIPTION           QTY   PRICE   DISC        AMOUNT
SAFETY SHOES KWD       2   95.00   MEMBER -9.50  180.50 SR
WORK GLOVES            3    2.50                   7.50
------------------------------
Total Qty   : 5
SUBTOTAL    : 188.00
MEMBER DISC : -5.00
DISC TOTAL  : -5.00
SST 6%      : 11.28
SERVICE CHARGE 5% : 9.40
ROUNDING    : +0.02
TOTAL       : 203.70
VISA CARD   : 203.70"""

FEW_SHOT_OUTPUT = {
    "info": {
        "store_name": "SUNRISE TRADING SDN BHD",
        "store_location": "No. 12, Jalan Bahagia 3, Taman Sejahtera 81300 Johor Bahru, Johor",
        "store_contacts": [
            {"type": "Tel.", "value": "07-3881234"},
            {"type": "Fax", "value": "07-3885678"},
            {"type": "", "value": "ryuk@gmail.com"},
        ],
        "tax_id": "001234567890",
        "receipt_id": "INV-20190115-0042",
        "payment_date": "15/01/2019",
        "payment_time": "14:35:22",
        "time_unit": "+08:00",
    },
    "items": [
        {
            "item_name": "SAFETY SHOES KWD",
            "quantity": "2",
            "unit_price": "95.00",
            "discount_label": "MEMBER",
            "discount_price": "-9.50",
            "tax_label": "SR",
            "total_price": "180.50",
        },
        {
            "item_name": "WORK GLOVES",
            "quantity": "3",
            "unit_price": "2.50",
            "discount_label": "",
            "discount_price": "",
            "tax_label": "",
            "total_price": "7.50",
        },
    ],
    "payment": {
        "total_items": "5",
        "currency": "",
        "subtotal_price": "188.00",
        "discounts": [
            {"discount_name": "MEMBER DISC", "amount": "-5.00"},
            {"discount_name": "DISC TOTAL", "amount": "-5.00"},
        ],
        "taxes": [{"tax_name": "SST 6%", "amount": "11.28"}],
        "additional_charges": [{"charge_name": "SERVICE CHARGE 5%", "amount": "9.40"}],
        "grand_total": "203.70",
        "rounding": "+0.02",
        "payment_method": "VISA CARD",
        "tendered": "203.70",
        "change": "",
    },
}


def build_extraction_prompt() -> str:
    """Build the shared zero-shot receipt extraction prompt."""
    schema_json = json.dumps(EXTRACTION_SCHEMA, ensure_ascii=False, indent=2)
    few_shot_json = json.dumps(FEW_SHOT_OUTPUT, ensure_ascii=False, indent=2)
    return (
        "You are a receipt Key-Information-Extraction (KIE) assistant.\n"
        "Output ONLY a JSON object that strictly matches the schema below.\n\n"
        f"{EXTRACTION_RULES}\n\n"
        "## JSON SCHEMA (shape) — your output MUST match these keys exactly:\n"
        f"```json\n{schema_json}\n```\n\n"
        "## FEW-SHOT EXAMPLE\n"
        "Receipt (text representation):\n"
        f"{FEW_SHOT_RECEIPT_TEXT}\n\n"
        "Expected JSON output:\n"
        f"```json\n{few_shot_json}\n```"
    )
