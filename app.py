import json
import os
import shutil
import struct
import io
import threading
import time
from datetime import datetime
from flask import Flask, request, send_file, jsonify, render_template
from flask_cors import CORS
import pandas as pd
from fpdf import FPDF, XPos, YPos

# Import signature configurations from sign.py
from sign import SIGNATORIES

# Initialize Flask app and CORS
app = Flask(__name__, template_folder='templates')
CORS(app)

# Configure paths and folders in root directory
UPLOAD_FOLDER = 'uploads'
GEN_FOLDER = 'gens'
SIGN_FOLDER = 'sign'

app.config['UPLOAD_FOLDER'] = UPLOAD_FOLDER
app.config['GEN_FOLDER'] = GEN_FOLDER
app.config['SIGN_FOLDER'] = SIGN_FOLDER

# Ensure folders exist
os.makedirs(UPLOAD_FOLDER, exist_ok=True)
os.makedirs(GEN_FOLDER, exist_ok=True)
os.makedirs(SIGN_FOLDER, exist_ok=True)

# Status variable and lock for thread safety
status = {"message": ""}
status_lock = threading.Lock()

def update_status(message):
    with status_lock:
        status['message'] = message
        print(f"Status update: {message}")

def read_dbf_to_df(file_stream_or_path):
    """
    Reads a DBF file from a stream or path and returns a Pandas DataFrame.
    """
    if isinstance(file_stream_or_path, str):
        f = open(file_stream_or_path, 'rb')
    else:
        f = file_stream_or_path

    try:
        header = f.read(32)
        if len(header) < 32:
            raise ValueError("Invalid DBF file: Header is too short")
        
        # Unpack dBase III header
        version, yy, mm, dd, num_records, header_len, record_len = struct.unpack('<BBBBLHH', header[:12])
        
        fields = []
        while True:
            marker = f.read(1)
            if not marker:
                break
            if marker == b'\r' or marker == b'\x0d':
                break
            field_data = marker + f.read(31)
            if len(field_data) < 32:
                break
            name = field_data[:11].strip(b'\x00').decode('ascii', errors='ignore')
            field_type = chr(field_data[11])
            length = field_data[16]
            decimals = field_data[17]
            fields.append((name, field_type, length, decimals))
            
        f.seek(header_len)
        records = []
        for _ in range(num_records):
            record_data = f.read(record_len)
            if len(record_data) < record_len:
                break
            del_flag = chr(record_data[0])
            if del_flag == '*':
                continue
            record_dict = {}
            offset = 1
            for name, field_type, length, decimals in fields:
                val_bytes = record_data[offset:offset+length]
                val = val_bytes.decode('ascii', errors='ignore').strip()
                if field_type == 'N':
                    try:
                        val = float(val) if '.' in val else int(val)
                    except ValueError:
                        pass
                record_dict[name] = val
                offset += length
            records.append(record_dict)
            
        return pd.DataFrame(records)
    finally:
        if isinstance(file_stream_or_path, str):
            f.close()

def load_uploaded_files(files_list):
    """
    Identifies which of the uploaded files is the MARKS file and which is the RESULTS file.
    Parses them accordingly (DBF or CSV) and returns (marks_df, results_df).
    """
    marks_df = None
    results_df = None
    
    for f in files_list:
        filename = f.filename.lower()
        if not filename:
            continue
            
        # Read file into dataframe
        if filename.endswith('.dbf'):
            file_bytes = f.read()
            f.seek(0)
            df = read_dbf_to_df(io.BytesIO(file_bytes))
        elif filename.endswith('.csv'):
            df = pd.read_csv(f)
        else:
            raise ValueError(f"Unsupported file type for {f.filename}. Only DBF and CSV are supported.")
            
        # Classification logic based on filename first, then column headers
        if 'mark' in filename:
            marks_df = df
        elif 'result' in filename:
            results_df = df
        else:
            # Analyze column names
            cols = [c.upper() for c in df.columns]
            if 'MARKS_OBT' in cols or 'C_NAME' in cols or 'MAX' in cols:
                marks_df = df
            elif 'CGPA' in cols or 'GPA' in cols or 'REMARK' in cols:
                results_df = df
                
    if marks_df is None or results_df is None:
        raise ValueError("Could not distinguish or find both MARKS and RESULTS files. Ensure you upload one of each (DBF or CSV).")
        
    return marks_df, results_df

def process_results(results_df):
    """
    Processes the results DataFrame. Filters passing students and formats headers/fields.
    """
    rdf = results_df.copy()
    rdf.columns = [c.upper() for c in rdf.columns]
    
    # Fill empty strings for remarks
    rdf['FREM'] = rdf['FREM'].fillna('').astype(str).str.strip()
    rdf['RES'] = rdf['RES'].fillna('').astype(str).str.strip()
    
    # Match result codes
    if 'RSLT' in rdf.columns:
        passing_cond = (rdf['RSLT'].str.upper() == 'P')
    elif 'REMARK' in rdf.columns:
        passing_cond = (rdf['REMARK'].str.upper() == 'SUCCESSFUL')
    else:
        passing_cond = pd.Series(True, index=rdf.index)
        
    # Exclude failed categories
    dataT = rdf[
        passing_cond & 
        (rdf['FREM'].isin(['', 'null', 'nan', 'NaN'])) & 
        (rdf['RES'].isin(['', 'null', 'nan', 'NaN']))
    ].copy()
    
    # Map Gender based on SEX code
    if 'SEX' in dataT.columns:
        dataT['Gender'] = dataT['SEX'].apply(lambda x: 'MALE' if str(x).strip() in ['1', '1.0'] else 'FEMALE' if str(x).strip() in ['2', '2.0'] else 'N/A')
    else:
        dataT['Gender'] = 'N/A'
        
    # Sort and pad numbers
    if 'COLL_NO' in dataT.columns:
        dataT = dataT.sort_values(by='COLL_NO', ascending=True)
        dataT['pno'] = dataT.groupby('COLL_NO').cumcount() + 1
        dataT['COLL_NO'] = dataT['COLL_NO'].apply(lambda x: str(int(float(x))).zfill(4) if pd.notnull(x) and str(x).strip() != '' else '0000')
        dataT['pno'] = dataT['pno'].apply(lambda x: str(x).zfill(4))
    else:
        dataT['COLL_NO'] = '0000'
        dataT['pno'] = '0001'
        
    return dataT

@app.route('/', methods=['GET'])
def home():
    """
    Renders the dashboard. Displays available signatures configured in sign.py.
    """
    signatures = ["NO SIGN"] + list(SIGNATORIES.keys())
    return render_template('index.html', signatures=signatures)

@app.route('/status', methods=['GET'])
def get_status():
    with status_lock:
        return jsonify(status)

@app.route('/generate-certificates', methods=['POST'])
def generate_certificates():
    global status
    update_status("Uploading files...")
    
    uploaded_files = request.files.getlist('files')
    if len(uploaded_files) < 2:
        return "You must upload both MARKS and RESULTS files (.dbf or .csv).", 400
        
    signature_option = request.form.get('signature', 'NO SIGN')
    designation = request.form.get('designation', 'DIRECTOR')
    display_sign = request.form.get('display_sign') == 'true'
    is_revaluation = request.form.get('revaluation') == 'true'
    print_rd_date = request.form.get('print_rd_date') == 'true'
    passing_type = request.form.get('passing_type', 'BLACK')
    
    try:
        update_status("Parsing database files...")
        marks_df, results_df = load_uploaded_files(uploaded_files)
        
        update_status("Processing results information...")
        dataT = process_results(results_df)
        
        # Auto-extract parameters
        course_name = "BACHELOR OF MANAGEMENT STUDIES"
        for df in [results_df, marks_df]:
            df.columns = [c.upper() for c in df.columns]
            if 'COURSE' in df.columns:
                valid_courses = df['COURSE'].dropna().tolist()
                if valid_courses:
                    extracted = str(valid_courses[0]).split('(')[0].strip()
                    if extracted:
                        course_name = extracted
                        break
                        
        year = datetime.now().strftime("%Y")
        exam_month = datetime.now().strftime("%B").upper()
        for df in [results_df, marks_df]:
            df.columns = [c.upper() for c in df.columns]
            if 'PERIOD' in df.columns:
                valid_periods = df['PERIOD'].dropna().tolist()
                if valid_periods:
                    parts = str(valid_periods[0]).split()
                    if len(parts) >= 2:
                        exam_month = parts[0].strip().title()
                        year = parts[1].strip()
                        break
                    elif len(parts) == 1:
                        exam_month = parts[0].strip().title()
                        break

        update_status(f"Generating certificates for {len(dataT)} passing students...")
        output_pdf_path = os.path.abspath(os.path.join(app.config['GEN_FOLDER'], "certificates.pdf"))
        
        # Run FPDF generator
        generate_certificate_pdf(
            dataT=dataT, 
            output_pdf_path=output_pdf_path, 
            year=year, 
            exam_month=exam_month, 
            course_name=course_name, 
            signature_option=signature_option, 
            designation=designation,
            display_sign=display_sign,
            is_revaluation=is_revaluation,
            print_rd_date=print_rd_date,
            passing_type=passing_type
        )
        
        response = send_file(output_pdf_path, as_attachment=True, download_name="certificates.pdf")
        update_status("Completed")
        return response
        
    except Exception as e:
        print(f"Error executing generation: {e}")
        update_status(f"Failed: {str(e)}")
        return f"Error executing generation: {str(e)}", 500

def generate_certificate_pdf(dataT, output_pdf_path, year, exam_month, course_name, signature_option, designation, display_sign, is_revaluation, print_rd_date, passing_type):
    dataT = dataT.drop_duplicates(subset=['SEAT_NO'])
    
    # Easily tunable layout and spacing parameters
    signature_right_offset = -5  # Distance from the right boundary. Negative value shifts box more right (default was 20)
    signature_y_offset = 45     # Higher values shift signature box further up (default was 25)
    clg_seat_left_x = 55        # X coordinate for College Code and Seat No (shifted left from 65)
    clg_seat_top_y = 65         # Y coordinate for College Code line (shifted down from 95)
    line_height_multiline = 22  # Line height for wrapped lines (Student Name & Exam Name)
    
    # Gap sizes between text blocks (Strict Rules to avoid overlaps)
    certify_y_start = 110       # Initial Y coordinate for "I Certify that" block
    gap_certify_to_name = 14    # Gap between Certify line and Student Name
    gap_name_to_exam = 14       # Gap between Student Name and Exam Name
    gap_exam_to_third = 14      # Gap between Exam Name and Third Line
    gap_third_to_date = 14      # Gap between Third Line and Date/CGPA line
    
    pdf = FPDF(unit='pt', format='A4')
    margin = 105   # Margin padding
    desired_width = 390
    desired_height = 375
    
    pdf.set_auto_page_break(auto=False, margin=0)
    y_positions = [20, 445]  # Two certificates per page
    current_y_index = 0
    
    pdf.set_font("Times", size=11)
    gray_color = (50, 50, 50)
    black_color = (0, 0, 0)
    
    # 1. Determine prefix for the college certificate serial number
    if is_revaluation:
        prefix = "CCFRV"
    else:
        if signature_option == "NO SIGN":
            prefix = "CCFR"
        else:
            prefix = "CCF"
            
    # 2. Determine designate title below signature
    director_title = designation
        
    board_title = "BOARD OF EXAMINATIONS & EVALUATION"
    current_date = datetime.now().strftime("%B %d, %Y")
    
    for index, student in dataT.iterrows():
        if current_y_index == 0:
            pdf.add_page()
            
        y_position = y_positions[current_y_index]
        current_y_index = (current_y_index + 1) % 2
        
        # Student information details
        certify = "I Certify that"
        name = str(student['NAME']).strip() if pd.notnull(student['NAME']) else 'N/A'
        coll_no = str(student['COLL_NO'])
        pno = str(student['pno'])
        
        # Certificate serial display
        ccf_line = f"{prefix} : {coll_no} : {pno}"
        seat_no = "NO : " + str(student['SEAT_NO']).strip() if pd.notnull(student['SEAT_NO']) else 'N/A'
        gender_suffix = "/ - FEMALE" if pd.notnull(student['Gender']) and student['Gender'] == 'FEMALE' else ''
        name_with_gender = f"/ {name}" if gender_suffix else name
        if passing_type == 'RED':
            thirdline = "(Three Year Degree Course) Examination held by the University of Mumbai in the month of"
        else:
            thirdline = "held by the University of Mumbai in the month of "
        
        # Check CGPA/CGRADE
        cgpa_val = student.get('CGPA')
        cgrade_val = student.get('FINALGRADE') if 'FINALGRADE' in student else student.get('CGRADE')
        
        # Look for ordinance columns: 'GCGPA', 'CLS_43', 'TOT_43'
        ordinance_val = None
        for col_name in ['GCGPA', 'CLS_43', 'TOT_43']:
            if col_name in student:
                val = student[col_name]
                if pd.notnull(val) and str(val).strip() != '':
                    val_clean = "".join(c for c in str(val) if c.isdigit() or c == '.')
                    try:
                        ordinance_val = float(val_clean)
                        break
                    except ValueError:
                        pass

        if pd.notnull(cgpa_val) and str(cgpa_val).strip() != '':
            cgpa_str = str(cgpa_val).strip()
            cgpa_clean = "".join(c for c in cgpa_str if c.isdigit() or c == '.')
            if ordinance_val is not None:
                try:
                    cgpa_float = float(cgpa_clean)
                    total_cgpa = cgpa_float + ordinance_val
                    cgpa = f"{total_cgpa:.2f}"
                except ValueError:
                    cgpa = cgpa_str
            else:
                cgpa = cgpa_str
            course_semester_text = f"PASSED THE {course_name} (CBCGS) EXAMINATION"
            date_text = f"{exam_month.upper()} {year} WITH {cgpa} CGPI"
        elif pd.notnull(cgrade_val) and str(cgrade_val).strip() != '':
            grade = str(cgrade_val).strip()
            course_semester_text = f"PASSED THE {course_name} (CBSGS) EXAMINATION"
            date_text = f"{exam_month.upper()} {year} AND WAS PLACED IN THE {grade} GRADE"
        else:
            course_semester_text = f"PASSED THE {course_name} (CBCGS) EXAMINATION"
            date_text = f"{exam_month.upper()} {year} WITH N/A CGPI"
            
        # Draw dynamic signature image if selected
        signature_width = 100
        signature_height = 40
        signature_x = margin + desired_width - signature_width - signature_right_offset
        signature_y = y_position + desired_height - signature_height - signature_y_offset
        
        if display_sign and signature_option != "NO SIGN" and signature_option in SIGNATORIES:
            sig_file_path = SIGNATORIES[signature_option].get("image_path")
            if sig_file_path and os.path.exists(sig_file_path):
                pdf.image(sig_file_path, x=signature_x, y=signature_y, w=signature_width, h=signature_height)
                
        # Center-align the designation labels with the center of the signature image (Make them bold)
        label_width = 200
        pdf.set_font("Times", style='B', size=11)
        pdf.set_text_color(*black_color)
        
        label_x = signature_x + (signature_width / 2) - (label_width / 2)
        
        # Center "DIRECTOR" or "I/C DIRECTOR" within the block
        pdf.set_xy(label_x, signature_y + signature_height + 2)
        pdf.cell(label_width, 10, director_title, align='C')
        
        # Center "BOARD OF EXAMINATIONS & EVALUATION" within the block
        pdf.set_xy(label_x, signature_y + signature_height + 18)
        pdf.cell(label_width, 10, board_title, align='C')
        
        # Draw certificate text
        pdf.set_text_color(*gray_color)
        pdf.set_font("Times", size=12)
        
        # CCF serial line & seat number (using clg_seat_left_x and clg_seat_top_y parameters)
        pdf.set_xy(clg_seat_left_x, y_position + clg_seat_top_y)
        pdf.cell(0, 40, ccf_line)
        
        pdf.set_xy(clg_seat_left_x, y_position + clg_seat_top_y + 15)
        pdf.cell(0, 40, seat_no)
        
        # 1. Certify text center-aligned (Make it bold)
        pdf.set_font("Times", style='B', size=13)
        pdf.set_text_color(*black_color)
        pdf.set_xy(margin, y_position + certify_y_start)
        pdf.multi_cell(desired_width, 20, certify, align='C')
        
        # 2. Student Name center-aligned
        pdf.set_font("Times", size=12)
        pdf.set_text_color(*gray_color)
        name_y = pdf.get_y() + gap_certify_to_name
        pdf.set_xy(margin, name_y)
        pdf.multi_cell(desired_width, line_height_multiline, name_with_gender, align='C')
            
        # 3. Exam Name center-aligned below Name
        pdf.set_font("Times", size=12)
        pdf.set_text_color(*gray_color)
        exam_y = pdf.get_y() + gap_name_to_exam
        pdf.set_xy(margin, exam_y)
        pdf.multi_cell(desired_width, line_height_multiline, course_semester_text, align='C')
            
        # 4. Third line center-aligned (Make it bold and wrap if it exceeds desired_width)
        pdf.set_font("Times", style='B', size=13)
        pdf.set_text_color(*black_color)
        third_y = pdf.get_y() + gap_exam_to_third
        pdf.set_xy(margin, third_y)
        
        third_lh = 18 if passing_type == 'RED' else 24
        pdf.multi_cell(desired_width, third_lh, thirdline, align='C')
            
        # 5. Year with CGPA center-aligned
        pdf.set_font("Times", size=12)
        pdf.set_text_color(*gray_color)
        date_y = pdf.get_y() + gap_third_to_date
        pdf.set_xy(margin, date_y)
        pdf.multi_cell(desired_width, 20, date_text, align='C')
        
        # Gender tag & Current date
        pdf.set_xy(130, y_position + 320)
        pdf.cell(0, 40, gender_suffix)
        
        # Conditionally print Result Declared Date
        if print_rd_date:
            date_to_print = str(student['RSLT_DATE']).strip() if ('RSLT_DATE' in student and pd.notnull(student['RSLT_DATE']) and str(student['RSLT_DATE']).strip() != '') else current_date
            pdf.set_xy(120, y_position + 340)
            pdf.cell(0, 40, date_to_print)
        
        # Load and draw gothic header
        if 'OldLondon' not in pdf.fonts:
            pdf.add_font('OldLondon', '', r'OldLondon.ttf')
        
        pdf.set_font("OldLondon", size=28)
        pdf.set_text_color(*black_color)
        pdf.set_xy(194, y_position + 3)
        
        pdf.set_font("Times", style='', size=12)
        
    pdf.output(output_pdf_path)

if __name__ == '__main__':
    app.run(debug=True)
