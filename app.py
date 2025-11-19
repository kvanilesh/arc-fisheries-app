import streamlit as st
import pdfplumber
import pandas as pd
import re
import io
import plotly.express as px
import firebase_admin
from firebase_admin import credentials, firestore
import json

# --- PAGE CONFIGURATION ---
st.set_page_config(page_title="ARC Sales Dashboard", page_icon="📈", layout="wide")

# --- 1. DATABASE CONNECTION (Firebase) ---
def init_firebase():
    # Check if Firebase is already initialized to avoid errors on reload
    if not firebase_admin._apps:
        # Load the secret key from Streamlit Settings
        # You must add this in the Streamlit Cloud dashboard under "Secrets"
        if "textkey" in st.secrets:
            key_dict = json.loads(st.secrets["textkey"])
            cred = credentials.Certificate(key_dict)
            firebase_admin.initialize_app(cred)
        else:
            return None
    return firestore.client()

# Attempt connection
db = None
try:
    db = init_firebase()
    if db:
        st.sidebar.success("Database Connected! 🟢")
    else:
        st.sidebar.warning("Database Secrets Missing 🔴")
except Exception as e:
    st.sidebar.error(f"DB Error: {e}")

# --- 2. PDF EXTRACTION LOGIC (Fixed Relative) ---
def extract_data(uploaded_file):
    extracted_data = []
    
    # Regex Patterns
    date_pattern = re.compile(r'\d{2}\.\d{2}\.\d{2}')
    amount_check_pattern = re.compile(r'[\d]')

    with pdfplumber.open(uploaded_file) as pdf:
        total_pages = len(pdf.pages)
        my_bar = st.progress(0, text="Scanning PDF...")

        for i, page in enumerate(pdf.pages):
            my_bar.progress(int((i / total_pages) * 100))
            
            table = page.extract_table()
            if not table: continue

            for row in table:
                # Clean row
                clean_row = [str(cell).strip().replace('\n', ' ') if cell is not None else "" for cell in row]
                
                # Skip junk
                if not any(clean_row) or "Total" in str(clean_row):
                    continue
                
                # Need at least 8 columns for relative indexing to work safely
                if len(clean_row) < 8:
                    continue

                # --- LOGIC: Fixed Relative Positions ---
                
                # 1. AMOUNT (3rd column before last -> Index -4)
                amount_raw = clean_row[-4]
                
                # Fallback check: look at neighbors if -4 is empty/text
                if not amount_check_pattern.search(amount_raw):
                    if len(clean_row) >= 5 and amount_check_pattern.search(clean_row[-5]):
                        amount_raw = clean_row[-5]
                    elif amount_check_pattern.search(clean_row[-3]):
                        amount_raw = clean_row[-3]

                # 2. VEHICLE NO (7th column before last -> Index -8)
                vehicle_raw = clean_row[-8]

                # 3. VOUCHER & DATE (First columns)
                voucher_raw = clean_row[0]
                date_raw = clean_row[1]

                # 4. DATE FILL-DOWN
                # We track current_date within the loop, but since we restart per page in this simple version, 
                # robust fill-down requires tracking across rows. 
                # For simplicity in this block, we assume date is on the line or we take voucher's date if present.
                final_date = date_raw 
                # (Note: In a full persistent script, we'd declare current_date outside loop. 
                # If your PDF has blank dates for many rows, uncomment the global tracker approach)

                # Save valid rows
                if amount_raw or vehicle_raw:
                    extracted_data.append({
                        "Vehicle No": vehicle_raw,
                        "Voucher No": voucher_raw,
                        "Date": final_date,
                        "Amount (Rs)": amount_raw
                    })
        my_bar.empty()
    return extracted_data

def clean_dataframe(data):
    if not data: return pd.DataFrame()
    df = pd.DataFrame(data)
    
    # Clean Amount (remove commas, ensure float)
    def clean_amt(x):
        if not x: return 0.0
        # Keep only digits and dots
        s = re.sub(r'[^\d\.]', '', str(x))
        try:
            return float(s)
        except:
            return 0.0
            
    df['Amount (Rs)'] = df['Amount (Rs)'].apply(clean_amt)

    # Clean Date: Forward Fill (FFILL) to handle missing dates in PDF
    df['Date'] = df['Date'].replace('', pd.NA).ffill()
    
    # Create a standard Date Object for sorting/database
    df['Date_Obj'] = pd.to_datetime(df['Date'], format='%d.%m.%y', errors='coerce')
    # Create a display string
    df['Date'] = df['Date_Obj'].dt.strftime('%d/%m/%Y')
    
    # Filter bad rows (Amount must be > 0)
    return df[df['Amount (Rs)'] > 0]

# --- 3. DATABASE HELPER FUNCTIONS ---
def save_to_firestore(df):
    if db is None: return 0
    collection_ref = db.collection('sales_data')
    count = 0
    
    for index, row in df.iterrows():
        # Create Unique ID: Voucher + Date + Amount (Prevents duplicates)
        # Using Amount in ID helps distinguish multiple items on same bill
        safe_date = str(row['Date']).replace('/', '')
        safe_amt = str(int(row['Amount (Rs)']))
        doc_id = f"{row['Voucher No']}_{safe_date}_{safe_amt}"
        
        data = row.to_dict()
        # Firestore needs basic python types, not pandas Timestamp
        data['Date_Obj'] = row['Date_Obj'].isoformat() if pd.notnull(row['Date_Obj']) else None
        
        collection_ref.document(doc_id).set(data)
        count += 1
    return count

def load_history():
    if db is None: return pd.DataFrame()
    # Get all documents
    docs = db.collection('sales_data').stream()
    data = [doc.to_dict() for doc in docs]
    return pd.DataFrame(data)

# --- 4. MAIN UI ---
def main():
    st.title("🐟 ARC Fisheries Manager")
    
    # Tabs for different functions
    tab1, tab2, tab3 = st.tabs(["📄 Upload", "💾 Save Data", "📊 Dashboard"])

    # --- TAB 1: UPLOAD ---
    with tab1:
        uploaded_file = st.file_uploader("Upload PDF Report", type="pdf")
        
        if uploaded_file:
            raw_data = extract_data(uploaded_file)
            if raw_data:
                df = clean_dataframe(raw_data)
                st.success(f"Extracted {len(df)} rows successfully!")
                
                # Save to session state so it's available in Tab 2
                st.session_state['current_df'] = df
                st.dataframe(df.drop(columns=['Date_Obj'], errors='ignore')) 
            else:
                st.error("No data found in PDF.")

    # --- TAB 2: SAVE ---
    with tab2:
        st.header("Review and Save")
        
        if 'current_df' in st.session_state:
            df_to_save = st.session_state['current_df']
            st.write("Data ready to be saved to database:")
            st.dataframe(df_to_save.head())
            
            if st.button("✅ Save Records to Database"):
                if db:
                    with st.spinner("Saving..."):
                        count = save_to_firestore(df_to_save)
                    st.balloons()
                    st.success(f"Saved {count} records to the Cloud Database!")
                else:
                    st.error("Database connection missing. Please add 'textkey' to Streamlit Secrets.")
        else:
            st.info("Please upload a file in the 'Upload' tab first.")

    # --- TAB 3: DASHBOARD ---
    with tab3:
        st.header("Sales Analytics")
        if st.button("🔄 Refresh Dashboard"):
            st.rerun()
            
        if db:
            history_df = load_history()
            
            if not history_df.empty:
                # Convert string date back to datetime for charts
                history_df['Date_Obj'] = pd.to_datetime(history_df['Date_Obj'])
                history_df = history_df.sort_values('Date_Obj')
                
                # Summary Metrics
                total_sales = history_df['Amount (Rs)'].sum()
                txns = len(history_df)
                
                m1, m2 = st.columns(2)
                m1.metric("Total Revenue", f"₹ {total_sales:,.2f}")
                m2.metric("Total Transactions", txns)
                
                st.divider()
                
                # Charts
                c1, c2 = st.columns(2)
                with c1:
                    # Daily Trend
                    daily = history_df.groupby('Date_Obj')['Amount (Rs)'].sum().reset_index()
                    fig = px.bar(daily, x='Date_Obj', y='Amount (Rs)', title="Daily Sales Trend")
                    st.plotly_chart(fig, use_container_width=True)
                    
                with c2:
                    # Top Vehicles
                    veh = history_df.groupby('Vehicle No')['Amount (Rs)'].sum().reset_index()
                    veh = veh.sort_values('Amount (Rs)', ascending=False).head(10)
                    fig2 = px.bar(veh, x='Amount (Rs)', y='Vehicle No', orientation='h', title="Top 10 Vehicles")
                    st.plotly_chart(fig2, use_container_width=True)
                
                # Data Table
                with st.expander("View All Database Records"):
                    st.dataframe(history_df.drop(columns=['Date_Obj'], errors='ignore'))
            else:
                st.info("Database is empty. Upload and save some data first!")
        else:
            st.warning("Database not connected.")

if __name__ == "__main__":
    main()
