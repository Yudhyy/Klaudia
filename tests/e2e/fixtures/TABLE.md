<!--
  THIS FILE IS A FIXTURE, NOT ONLY DOCUMENTATION.

  tests/e2e/sheet_guard.py parses the blocks below and writes them into the test
  user's spreadsheet before the behavior suite runs, then repairs them after
  every mutating case. Adding a block here CREATES A TAB in that spreadsheet.

  The parser reacts only to lines beginning `Index:` or `Name:`, and to a line
  that is exactly `Table:`. Rows are tab-separated and a block ends at the first
  blank line. Prose above the first block is ignored, which is why this preamble
  is safe. Do not start a prose line with `Index:` or `Name:`.

  tests/unit/test_table_md_fixture.py pins the parse: 7 tabs, exact names, exact
  row and column counts. Edit the data and that test tells you what moved.
-->

# Buku Belanja 2026 — the Tier 1 sandbox fixture

One spreadsheet, seven tabs, roughly 90 rows of household purchases. This is the
smallest realistic environment in the sandbox and the only fixture written by
hand; every harder tier generates its own data and its own answers in code.

**Spreadsheet view**

| # | Sheet | Shape | Columns | What the suite asks of it |
|---|---|---|---|---|
| 0 | `Rangkuman Total` | 6 rows x 2 | Bulan, Total Pembelian (Rp) | monthly roll-up; the tab a write case must keep consistent |
| 1 | `Jun` | 35 rows x 7 | Tanggal, Toko, Item, Jumlah, Metode Pembayaran, Satuan Harga, Total | the busy month: reads, appends, receipt extraction target |
| 2 | `Mei` | 10 rows x 7 | same | cross-sheet comparison against `Jun` |
| 3 | `Apr` | 10 rows x 7 | same | month lookup |
| 4 | `Mar` | 10 rows x 7 | same | month lookup |
| 5 | `Feb` | 10 rows x 7 | same | month lookup |
| 6 | `Jan` | 10 rows x 7 | same | month lookup |

Amounts appear in mixed shapes on purpose — dotted (`2.164.500`), bare (`7000`),
and blank payment methods in the `sy_beautyskin` rows. That is real data as
typed, and the suite compares digits rather than strings so both forms round-trip.

**Where this fixture sits**

| Tier | Environment | Fixture source |
|---|---|---|
| **T1 Intern** | **this spreadsheet, 7 tabs, ~90 rows** | **this file** |
| T2 Clerk | 1 spreadsheet, dirty values | `tests/e2e/synthetic.py` |
| T3 Bookkeeper | multi-sheet, 100s of rows | `synthetic.py` + `synthetic_finance.py` |
| T4 Analyst | multi-sheet, 1000+ rows | `synthetic_finance.py` |
| T5 Controller | several spreadsheets per tenant | `synthetic.py::build_branch_ledgers` |
| T6 Auditor | adversarial by construction | `synthetic.py` |

**Do not add the newer fixtures to this file.** Sheets like `Faktur Juli`,
`Piutang Usaha`, `Jurnal Umum`, `Buku Besar 2026`, and the two `Penjualan Juni`
tabs that live in *separate* spreadsheets are generated from a seed, with their
totals computed alongside them and frozen by unit tests. They belong to other
tenants and, in the multi-spreadsheet case, to other spreadsheets entirely —
something this single-spreadsheet format cannot express. Writing them here would
seed them into the Tier 1 user and break the isolation the tiers depend on.

Update this file when the Tier 1 spreadsheet itself gains or loses a tab, a
column, or rows. Everything above Tier 1 changes in the generators.

---

Index: 0,
Name: "Rangkuman Total",
Table:
Bulan	Total Pembelian (Rp)
Januari	2.164.500
Februari	2.163.500
Maret	2.809.000
April	2.357.000
Mei	2.163.500
Juni	1.501.900

Index: 1,
Name: Jun,
Table:
Tanggal	Toko	Item	Jumlah	Metode Pembayaran	Satuan Harga (IDR)	Total (IDR)
2026-06-19	PARIS MART	TANGO NEW CHOCOLATE 176 G	1	Kartu Debit	12.000	12.000
2026-06-19	PARIS MART	OREO WAFER CHOCO VANILA	1	Kartu Debit	9.000	9.000
2026-06-19	PARIS MART	SALTCHEESE COMBO 175GR	1	Kartu Debit	10.000	10.000
2026-06-19	PARIS MART	NARAYA ROKA ISI 80 PCS	1	Kartu Debit	63.000	63.000
2026-06-19	PARIS MART	SELAMAT CHOCOLATE 198GR	1	Kartu Debit	15.000	15.000
2026-06-19	PARIS MART	NICE 250 SHEETS	1	Kartu Debit	12.000	12.000
2026-06-19	PARIS MART	INDOMIE GORENG RASA AYAM	5	Kartu Debit	4.000	20.000
2026-06-19	PARIS MART	SUSU FISIAN FLAG PUREFARM	1	Kartu Debit	7000	7000
2026-06-19	PARIS MART	MILO 110 ML	1	Kartu Debit	4000	4000
2026-06-19	PARIS MART	SUSU STRAWBERY UHT KIDS	1	Kartu Debit	3000	3000
2026-06-19	PARIS MART	NESTLE COK UHT 110ML	1	Kartu Debit	3000	3000
2026-06-19	PARIS MART	IR SUSU ULTRA MILK RASA C	1	Kartu Debit	4000	4000
2026-06-19	PARIS MART	CIMORY YOGURT DRINK BUAH	1	Kartu Debit	9000	9000
2026-06-19	PARIS MART	MAYORA TEH PUCUK 250ML	1	Kartu Debit	3000	3000
2026-06-19	PARIS MART	UNI VASELINE PERECT10 20	1	Kartu Debit	36000	36000
2026-06-19	PARIS MART	OHAYO	1	Kartu Debit	6000	6000
2026-06-19	PARIS MART	AICE MIKI MIKI	5	Kartu Debit	2000	10000
2026-06-23	ALFAMART STA.KARET	SARI ROTI SW CK	1	E-Wallet	4500	4500
2026-06-23	ALFAMART STA.KARET	SARI ROTI SW CK	1	E-Wallet	4500	4500
2026-06-25	Alfamart	Telur	20	Cash	2000	40000
2026-06-25	JUANDA MANSION SDJ	SEDAAP KOREA87G	1	Cash	2600	2600
2026-06-25	JUANDA MANSION SDJ	XL 100K (Adj.)	1	Cash	96900	96900
2026-06-27	TLOGOMAS 44 MALANG	POP MIE AYAM 75G	1	TUNAI	4900	4900
2026-06-27	TLOGOMAS 44 MALANG	POP MIE PD.DWR AYM75	1	TUNAI	5400	5400
2026-06-27	TLOGOMAS 44 MALANG	NESTLE PURE LIFE 600	2	TUNAI	3600	7200
2026-06-27	TLOGOMAS 44 MALANG	LE MINERALE 600ML	2	TUNAI	3500	7000
2026-06-27	TLOGOMAS 44 MALANG	ULTRA KCNG HIJAU 250	1	TUNAI	4900	4900
2026-06-27	TLOGOMAS 44 MALANG	NUTRIJEL PWD.STRW.15	2	TUNAI	6600	13200
2026-06-27	TLOGOMAS 44 MALANG	KNZLER SNGLES KJU 65	1	TUNAI	8700	8700
2026-06-27	TLOGOMAS 44 MALANG	KNZLER SNGLES HOT 65	1	TUNAI	8700	8700
2026-06-27	TLOGOMAS 44 MALANG	KANZLR BAKSO ORI 48G	2	TUNAI	8700	17400
2026-06-27	sy_beautyskin	Ms Glow acne	2		300000	600000
2026-06-27	sy_beautyskin	serum lifting ms glow	1		150000	150000
2026-06-27	sy_beautyskin	eye treatment serum	1		125000	125000
2026-06-27	sy_beautyskin	waiteu	1		175000	175000

Index: 2,
Name: Mei,
Table:
Tanggal	Toko	Item	Jumlah	Metode Pembayaran	Satuan Harga (IDR)	Total (IDR)
2026-05-01	Indomaret	Cooking Oil 2L	5	Ovo	38.000	190.000
2026-05-02	Indomaret	Sugar 1kg	10	Ovo	17.500	175.000
2026-05-05	Pasar Sentral	Wheat Flour 25kg	2	Tunai	285.000	570.000
2026-05-06	Toko Kemasan Jaya	Plastic Packaging	500	E-Wallet	350	175.000
2026-05-08	Hypermart	Eggs	20	Kartu Debit	3.000	60.000
2026-05-09	Pasar Sentral	Butter	10	Kartu Debit	95.000	950.000
2026-05-18	BreadTalk	Bread Butter Pudding	1	Gopay	11500	11500
2026-05-20	BreadTalk	Cream Bruille	1	Gopay	14000	14000
2026-05-22	BreadTalk	Choco Croissant	1	Gopay	10500	10500
2026-05-22	BreadTalk	Bank Of Chocolat	1	Gopay	7500	7500

Index: 3,
Name: Apr,
Table:
Tanggal	Toko	Item	Jumlah	Metode Pembayaran	Satuan Harga (IDR)	Total (IDR)
2026-04-01	Indomaret	Greek Yogurt 1kg	5	Ovo	42.000	210.000
2026-04-02	Indomaret	Organic Chia Seeds 500g	10	Ovo	25.000	250.000
2026-04-03	Alfamart	Soy Milk 1L	2	Tunai	32.000	64.000
2026-04-04	Alfamart	Paper Napkins 100s	500	E-Wallet	400	200.000
2026-04-05	Hypermart	Fresh Tuna Steak	20	Kartu Debit	45.000	900.000
2026-04-07	Toko Keira	Parmesan Cheese	10	Kartu Debit	60.000	600.000
2026-04-15	Hypermart	Raspberry Tart	1	Gopay	28.000	28.000
2026-04-16	Indomaret	Pain au Chocolat	1	Gopay	22.000	22.000
2026-04-21	Indomaret	Matcha Green Tea Bags	1	Gopay	35.000	35.000
2026-04-27	Indomaret	Premium Ruby Chocolate	1	Gopay	48.000	48.000

Index: 4,
Name: Mar,
Table:
Tanggal	Toko	Item	Jumlah	Metode Pembayaran	Satuan Harga (IDR)	Total (IDR)
2026-03-04	Alfamart	Avocado Oil 1L	5	Ovo	45.000	225.000
2026-03-05	Hypermart	Coconut Sugar 1kg	10	Ovo	22.000	220.000
2026-03-07	Alfamart	Oat Milk 1L	2	Tunai	35.000	70.000
2026-03-08	Alfamart	Aluminum Foil Roll	500	E-Wallet	500	250.000
2026-03-09	Toyib Jaya	Fresh Cod Fillet	20	Kartu Debit	55.000	1.100.000
2026-03-10	Toko Keira	Gouda Cheese	10	Kartu Debit	80.000	800.000
2026-03-12	Hypermart	Lemon Meringue Tart	1	Gopay	32.000	32.000
2026-03-13	Alfamart	Cinnamon Raisin Danish	1	Gopay	24.000	24.000
2026-03-14	Indomaret	Earl Grey Tea Bags	1	Gopay	38.000	38.000
2026-03-23	Indomaret	Premium Gold Chocolate	1	Gopay	50.000	50.000

Index: 5,
Name: Feb,
Table:
Tanggal	Toko	Item	Jumlah	Metode Pembayaran	Satuan Harga (IDR)	Total (IDR)
2026-02-02	Alfamart	Fresh Milk 1L	5	Ovo	38.000	190.000
2026-02-05	Hypermart	Organic Brown Rice 5kg	10	Ovo	17.500	175.000
2026-02-11	Alfamart	Wheat Flour 25kg	2	Tunai	285.000	570.000
2026-02-12	Alfamart	Whole Wheat Bread	500	E-Wallet	350	175.000
2026-02-19	Toyib Jaya	Tissue Pack 250s	20	Kartu Debit	3.000	60.000
2026-02-20	Toko Keira	Chicken Breast 1kg	10	Kartu Debit	95.000	950.000
2026-02-21	Hypermart	Cheddar Cheese	1	Gopay	11500	11500
2026-02-22	Alfamart	Strawberry Cheesecake	1	Gopay	14000	14000
2026-02-23	Indomaret	Almond Croissant	1	Gopay	10500	10500
2026-02-27	Indomaret	Matcha Latte Powder	1	Gopay	7500	7500

Index: 6,
Name: Jan
Table:
Tanggal	Toko	Item	Jumlah	Metode Pembayaran	Satuan Harga (IDR)	Total (IDR)
2026-01-01	Alfamart	Organic Honey 500g	5	Ovo	38.000	190.000
2026-01-02	Hypermart	Rolled Oats 1kg	10	Ovo	17.500	175.000
2026-01-03	Alfamart	Almond Milk 1L	2	Tunai	285.000	570.000
2026-01-04	Alfamart	Kitchen Towel Roll	500	E-Wallet	350	175.000
2026-01-13	Toyib Jaya	Fresh Salmon Fillet	20	Kartu Debit	3.000	60.000
2026-01-14	Toko Keira	Mozzarella Cheese	10	Kartu Debit	95.000	950.000
2026-01-15	Hypermart	Blueberry Muffin	1	Gopay	11500	11500
2026-01-16	Alfamart	Cinnamon Roll	1	Gopay	14000	14000
2026-01-17	Indomaret	Hot Chocolate Mix	1	Gopay	10500	10500
2026-01-19	Indomaret	Premium White Chocolate	1	Gopay	8500	8500