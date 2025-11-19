import streamlit as st
import pdfplumber
import pandas as pd
import re
import io

# --- CONFIGURATION ---
st.set_page_config(page_title="ARC PDF Extractor", layout="wide")

def extract_data_from_pdf(uploaded_file):
    """
    Extracts Vehicle, Voucher, Date, and Amount using Relative Column Positioning.
    Logic: 
    - Amount is 4th column from the end (Index -4)
    - Vehicle is 8th column from the end (Index -8)
    """
    extracted_data = []
    current_date = None
    
    # Regex patterns
    amount_pattern = re.compile(r'^-?[\d,]+\.?\d{0,2}$') 
    date_pattern = re.compile(r'\d{2}\.\d{2}\.\d{2}')

    with pdfplumber.open(uploaded_file) as pdf:
        total_pages = len(pdf.pages)
        progress_bar = st.progress(0)
        
        for i, page in enumerate(pdf.pages):
            # Update progress bar
            progress_bar.progress((i + 1) / total_pages)
            
            table = page.extract_table()
            if not table: continue

            for row in table:
                # Clean row: remove None and whitespace
                clean_row = [str(cell).strip().replace('\n', ' ') if cell is not None else "" for cell in row]
                
                # Skip empty/total rows
                if not any(clean_row) or "Total" in str(clean_row):
                    continue
                
                # Ensure row has enough columns to apply negative indexing (need at least 8)
                if len(clean_row) < 8:
                    continue

                # --- 1. EXTRACT AMOUNT (Index -4) ---
                # Logic: [..., Amount, Freight, Paid, Balance] -> Amount is -4
                amount_raw = clean_row[-4]
                
                # Fallback validation logic similar to previous script
                if not amount_pattern.search(amount_raw) or len(amount_raw) < 2:
                    if len(clean_row) >= 5 and amount_pattern.search(clean_row[-5]):
                        amount_raw = clean_row[-5] 
                    elif amount_pattern.search(clean_row[-3]):
                        amount_raw = clean_row[-3] 
                
                # --- 2. EXTRACT VEHICLE NO (Index -8) ---
                # Logic: [Vehicle, Count, Wt, Rate, Amount, Freight, Paid, Balance] -> Vehicle is -8
                vehicle_raw = clean_row[-8]

                # --- 3. EXTRACT VOUCHER & DATE (First columns) ---
                voucher_raw = clean_row[0]
                date_raw = clean_row[1]

                # --- 4. DATE FILL-DOWN LOGIC ---
                if date_pattern.search(date_raw):
                    current_date = date_raw
                elif date_pattern.search(voucher_raw): 
                    current_date = voucher_raw
                
                final_date = date_raw if date_pattern.search(date_raw) else current_date

                # --- SAVE DATA ---
                if amount_raw or vehicle_raw:
                    extracted_data.append({
                        "Vehicle No": vehicle_raw,
                        "Voucher No": voucher_raw,
                        "Date": final_date,
                        "Amount (Rs)": amount_raw
                    })
                    
    return extracted_data

def main():
    st.title("📄 ARC Fisheries PDF to Excel")
    st.write("Upload your PDF ledger report below. The app will extract Vehicle No, Voucher, Date, and Amount.")

    # File Uploader
    uploaded_file = st.file_uploader("Choose a PDF file", type="pdf")

    if uploaded_file is not None:
        st.write("Processing...")
        
        try:
            data = extract_data_from_pdf(uploaded_file)
            
            if not data:
                st.error("No data found. Please check if the PDF format matches the ARC Ledger Report.")
            else:
                # Convert to DataFrame
                df = pd.DataFrame(data)

                # --- CLEANING ---
                # Clean Amount
                def clean_amount_str(val):
                    if not val: return 0.0
                    s = re.sub(r'[^\d\.]', '', str(val))
                    try:
                        return float(s)
                    except:
                        return 0.0

                df['Amount (Rs)'] = df['Amount (Rs)'].apply(clean_amount_str)

                # Format Date
                df['Date'] = pd.to_datetime(df['Date'], format='%d.%m.%y', errors='coerce')
                df['Date'] = df['Date'].dt.strftime('%d/%m/%Y')

                # Filter bad rows (Amount > 0)
                df = df[df['Amount (Rs)'] > 0]

                # Display Success and Data
                st.success(f"Successfully extracted {len(df)} rows!")
                
                # Show Summary stats
                total_amount = df['Amount (Rs)'].sum()
                st.metric(label="Total Amount Extracted", value=f"₹ {total_amount:,.2f}")

                # Show Data Preview
                st.dataframe(df)

                # --- DOWNLOAD BUTTON ---
                # Create an Excel file in memory
                buffer = io.BytesIO()
                with pd.ExcelWriter(buffer, engine='openpyxl') as writer:
                    df.to_excel(writer, index=False, sheet_name='Data')
                
                st.download_button(
                    label="📥 Download as Excel",
                    data=buffer.getvalue(),
                    file_name="ARC_Extracted_Data.xlsx",
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
                )

        except Exception as e:
            st.error(f"An error occurred: {e}")

if __name__ == "__main__":
    main()