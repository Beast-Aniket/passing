# Passing Certificate Generator

This project is a Python-based web application that generates PDF certificates for students from uploaded database files (`MARKS.DBF` / `.csv` and `RESULTS.DBF` / `.csv`).

The application automatically identifies files, parses their content natively, maps appropriate certificate number prefixes, and draws authorized signatures. No database is required to run the code locally or in production.

## Features

- **Unified Python Application**: Standard Flask application rendering a modern, responsive web dashboard directly.
- **Drag & Drop Multi-file Upload**: Select and upload both database files simultaneously.
- **Custom Native DBF Parser**: Native binary DBF file reader in Python (no external dBase libraries required).
- **Auto-Extraction of Parameters**: Course titles, exam year, and month are extracted directly from the records.
- **Audit Logs & Progress Panel**: Real-time feedback console showing generation steps.
- **Configurable Signatures (`sign.py`)**: You can customize signatory names, designations, and paths by editing the `sign.py` file.
- **Smart Prefix Coding**:
  - `CCFRV`: Revaluation checkbox is checked.
  - `CCFR`: Revaluation is unchecked AND signature is "NO SIGN".
  - `CCF`: Revaluation is unchecked AND signature is selected.

---

## Installation & Setup

### 1. Prerequisites
- Python 3.7+

### 2. Setup Dependencies
From the project root directory, run:
```bash
pip install -r requirements.txt
```

---

## Running the Application

1. Start the Flask server:
   ```bash
   python app.py
   ```
2. Open your web browser and go to:
   ```
   http://127.0.0.1:5000/
   ```
3. Drag & drop or select both files (`MARKS.DBF` and `RESULTS.DBF` or their `.csv` counterparts) together, configure your signature and revaluation preferences, and click **Generate Certificates**.

---

## Folder Structure

- **app.py**: Flask backend, native DBF parser, routing, and FPDF generator logic.
- **sign.py**: Configuration file mapping dropdown keys to signature image paths and designations.
- **OldLondon.ttf**: Gothic font utilized for the University header.
- **templates/**: Contains `index.html` (the responsive dashboard template).
- **sign/**: Holds available signature image files (e.g. `DIRECTOR POOJA RAUNDALE.png`).
- **uploads/**: Temporary workspace for uploads.
- **gens/**: Output folder for generated certificate PDFs.
- **marks n result/**: Sample database files (`MARKS.DBF`, `RESULTS.DBF`).
