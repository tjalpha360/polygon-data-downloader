# --- IMPORTS ---
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
import datetime
import pytz
import time
import os
import threading
import configparser # Added import

# --- DEPENDENCY CHECK & INSTRUCTIONS ---
try:
    from polygon import RESTClient
    from polygon.exceptions import BadResponse
except ImportError:
    # This will run if the script is executed without the library installed.
    messagebox.showerror(
        "Missing Dependency",
        "The 'polygon-python-client' library is not installed.\n\n"
        "Please install it by running this command in your terminal:\n"
        "pip install polygon-python-client pandas pytz"
    )
    exit()

# --- CONFIGURATION ---
# Polygon.io free tier allows 5 API calls per minute. 60 / 5 = 12 seconds. Add a buffer.
API_CALL_DELAY_SECONDS = 12.2
CONFIG_FILE = 'config.ini' # Added config file name

# --- API KEY MANAGEMENT ---

def save_api_key(api_key):
    """Saves the API key to the config file."""
    config = configparser.ConfigParser()
    config['POLYGON'] = {'api_key': api_key}
    with open(CONFIG_FILE, 'w') as configfile:
        config.write(configfile)

def load_api_key():
    """Loads the API key from the config file."""
    if not os.path.exists(CONFIG_FILE):
        return None
    config = configparser.ConfigParser()
    config.read(CONFIG_FILE)
    return config.get('POLYGON', 'api_key', fallback=None)

# --- DATA PROVIDER: POLYGON.IO ---

def fetch_historical_data_polygon(client, ticker, interval, start_date, end_date, status_callback):
    """
    Orchestrates fetching historical aggregate data from Polygon.io by iterating day-by-day.
    This approach is robust against the 50,000 item limit per request for high-frequency data
    and respects the 5 calls/minute rate limit.
    """
    # Map GUI interval to Polygon API timespan and multiplier
    interval_map = {
        '1min': ('minute', 1), '5min': ('minute', 5), '10min': ('minute', 10),
        '15min': ('minute', 15), '30min': ('minute', 30), '60min': ('hour', 1)
    }
    if interval not in interval_map:
        raise ValueError(f"Unsupported interval: {interval}. Supported intervals are {list(interval_map.keys())}")
    
    timespan, multiplier = interval_map[interval]

    # Generate the list of all days that need to be queried
    days_to_fetch = pd.date_range(start=start_date, end=end_date, freq='D').tolist()
    
    all_data_frames = []
    total_days = len(days_to_fetch)

    for i, day in enumerate(days_to_fetch):
        day_str = day.strftime('%Y-%m-%d')
        status_callback(f"Fetching day {i+1}/{total_days}: {day_str}...")
        
        try:
            # Fetch aggregates for the entire day. Polygon handles market hours for stocks
            # and 24/7 for crypto automatically.
            aggs = client.get_aggs(
                ticker=ticker,
                multiplier=multiplier,
                timespan=timespan,
                from_=day_str,
                to=day_str,
                limit=50000  # Max limit per request
            )

            if aggs:
                df = pd.DataFrame(aggs)
                all_data_frames.append(df)
                print(f"Successfully fetched {len(aggs)} bars for {day_str}")
            else:
                print(f"No trading data returned for {day_str}, skipping.")

        except BadResponse as e:
            error_message = f"API error for {day_str}: {e}"
            print(error_message)
            # Allow user to continue or cancel on error
            if not messagebox.askyesno("API Error", f"Failed to fetch data for {day_str}.\nError: {e}\n\nDo you want to continue fetching the other days?"):
                 raise Exception("User cancelled operation due to API error.") from e
        
        except Exception as e:
            # Catch other potential errors (network, etc.)
            print(f"An unexpected error occurred while fetching {day_str}. Reason: {e}")
            if not messagebox.askyesno("Error", f"An unexpected error occurred for {day_str}.\nError: {e}\n\nDo you want to continue?"):
                 raise Exception("User cancelled operation due to an unexpected error.") from e

        # Wait between API calls to respect rate limits, except for the very last call
        if i < total_days - 1:
            for j in range(API_CALL_DELAY_SECONDS, 0, -1):
                status_callback(f"Fetching day {i+1}/{total_days}. Next call in {j}s...")
                time.sleep(1)

    if not all_data_frames:
        raise Exception(f"No data could be retrieved for ticker '{ticker}' in the specified date range. Please check the ticker symbol, asset type, and your API key.")

    status_callback("Combining all daily data...")
    full_df = pd.concat(all_data_frames, ignore_index=True)
    
    # --- Process the combined DataFrame ---
    # Rename columns to a standard format
    full_df.rename(columns={'o': 'Open', 'h': 'High', 'l': 'Low', 'c': 'Close', 'v': 'Volume', 't': 'timestamp'}, inplace=True)
    
    # Polygon timestamps are in milliseconds UTC. Convert to timezone-aware datetime objects.
    full_df['timestamp'] = pd.to_datetime(full_df['timestamp'], unit='ms', utc=True)
    full_df.set_index('timestamp', inplace=True)
    
    print(f"Successfully loaded a total of {len(full_df)} rows.")
    return full_df


def validate_api_key(api_key):
    """Basic validation of Polygon.io API key format"""
    if not api_key or len(api_key.strip()) < 20: return False, "API key appears too short."
    # Polygon keys can contain underscores, so we check for alphanumeric + underscore
    if not all(c.isalnum() or c == '_' for c in api_key.strip()): return False, "API key contains invalid characters."
    return True, "OK"

# --- MAIN APPLICATION LOGIC ---

def fetch_and_save_data():
    """
    This function contains the core logic for fetching, processing, and saving data.
    It's designed to be run in a background thread to keep the GUI responsive.
    """
    try:
        # --- Get User Inputs from GUI ---
        asset_type = asset_type_var.get()
        ticker_symbol = ticker_entry.get().strip().upper()
        start_date_str = start_date_entry.get().strip()
        end_date_str = end_date_entry.get().strip()
        interval_str = interval_combobox.get()
        api_key = polygon_api_key_entry.get().strip()

        def status_update_callback(message):
            # Use root.after to safely update GUI from this thread
            root.after(0, lambda: status_label.config(text=message))

        # --- Validation ---
        if not api_key:
            # Try to load from config if entry is empty, though test_api_key_action should handle most direct uses
            loaded_key = load_api_key()
            if loaded_key:
                api_key = loaded_key
                root.after(0, lambda: polygon_api_key_entry.insert(0, api_key)) # Update GUI
                root.after(0, lambda: api_key_status_label.config(text="Loaded key from config for fetch.", foreground="blue"))
            else:
                messagebox.showerror("API Key Required", "Polygon.io API key is required in entry or config.")
                root.after(0, lambda: api_key_status_label.config(text="API Key is missing.", foreground="red"))
                return

        is_valid, validation_msg = validate_api_key(api_key)
        if not is_valid:
            messagebox.showerror("Invalid API Key", f"API key validation failed: {validation_msg}")
            root.after(0, lambda: api_key_status_label.config(text=f"Invalid API Key: {validation_msg}", foreground="red"))
            return
        if not ticker_symbol:
            messagebox.showerror("Ticker Required", "Please enter a ticker symbol.")
            return

        # Validate and format ticker based on asset type
        if asset_type == "Crypto":
            if not ticker_symbol.startswith("X:"):
                # Auto-correct common crypto format for user convenience
                corrected_ticker = f"X:{ticker_symbol}"
                if messagebox.askyesno("Ticker Format", f"Crypto tickers should be prefixed with 'X:'.\n\nDo you want to use '{corrected_ticker}'?"):
                    ticker_symbol = corrected_ticker
                    # Update the entry box in the GUI thread-safely
                    root.after(0, lambda: (ticker_entry.delete(0, tk.END), ticker_entry.insert(0, ticker_symbol)))
                else:
                    status_update_callback("Ticker format incorrect for Crypto. Aborting.")
                    return
        
        try:
            start_date = datetime.datetime.strptime(start_date_str, '%Y-%m-%d').date()
            end_date = datetime.datetime.strptime(end_date_str, '%Y-%m-%d').date()
        except ValueError as e:
            messagebox.showerror("Invalid Date Format", f"Please use YYYY-MM-DD format. Error: {e}")
            return

        if start_date > end_date:
            messagebox.showerror("Invalid Date Range", "Start date must be on or before the end date.")
            return
        if end_date > datetime.date.today():
            messagebox.showwarning("Future Date", "End date is in the future. Data will be fetched up to the current date.")
            end_date = datetime.date.today() # Cap end date to today
        
        # --- Fetch Data using Polygon.io ---
        status_update_callback(f"Preparing to fetch history for {ticker_symbol}...")
        
        client = RESTClient(api_key)
        
        all_data = fetch_historical_data_polygon(
            client, ticker_symbol, interval_str, start_date, end_date, status_update_callback
        )
        
        status_update_callback("Filtering data to your exact start/end times...")
        
        # Data from Polygon is already in UTC. Create timezone-aware start/end for precise filtering.
        start_ts = pd.Timestamp(start_date, tz='UTC')
        # end_ts should be exclusive, so data up to the end of the selected end_date.
        # Example: if end_date is 2023-10-05, we want data up to 2023-10-05 23:59:59.999...
        # So, using pd.Timestamp(end_date) + pd.Timedelta(days=1) is correct.
        end_ts = pd.Timestamp(end_date, tz='UTC') + pd.Timedelta(days=1)
        
        # Filter the data to the precise range.
        # For stocks, this includes pre/post market data if available. For crypto, this covers the full 24-hour period.
        filtered_data = all_data[(all_data.index >= start_ts) & (all_data.index < end_ts)]
        
        if filtered_data.empty:
            messagebox.showwarning("No Data in Range", f"No data found for {ticker_symbol} between {start_date_str} and {end_date_str}.")
            return
        
        status_update_callback("Processing and cleaning data...")
        filtered_data = filtered_data[~filtered_data.index.duplicated(keep='first')].sort_index()

        # Convert to target timezone (America/Los_Angeles for PST/PDT) for the final output file.
        target_tz = pytz.timezone('America/Los_Angeles')
        filtered_data.index = filtered_data.index.tz_convert(target_tz)

        default_filename = f"{ticker_symbol.replace(':', '')}_{interval_str}_backtest_{start_date_str}_to_{end_date_str}.csv"
        final_filepath = filedialog.asksaveasfilename(defaultextension=".csv", initialfile=default_filename, filetypes=[("CSV files", "*.csv")])
        
        if not final_filepath:
            status_update_callback("Save cancelled by user.")
            return
        
        # --- Prepare final data: ONLY the 'Open' column ---
        if 'Open' in filtered_data.columns:
            output_df = filtered_data[['Open']].copy()
        else:
            messagebox.showerror("Error", "Required 'Open' column not found in the downloaded data.")
            return
        
        # Save the final DataFrame to CSV
        output_df.to_csv(final_filepath)
        save_api_key(api_key) # Save working API key
        
        data_summary = (f"✅ Data successfully saved!\n\nFile: {os.path.basename(final_filepath)}\nTicker: {ticker_symbol}\n"
                        f"Interval: {interval_str}\nRows: {len(output_df):,}\nColumns: Open\n"
                        f"Date range: {filtered_data.index.min().strftime('%Y-%m-%d %H:%M')} to {filtered_data.index.max().strftime('%Y-%m-%d %H:%M')}")
        
        status_update_callback(f"✅ Success! Saved {len(output_df):,} rows to CSV")
        messagebox.showinfo("Download Complete", data_summary)
        
    except Exception as e:
        error_msg = str(e)
        print(f"Full error details: {e}") # Keep this for detailed debugging
        if isinstance(e.__cause__, BadResponse): # Check underlying cause for API errors
            if e.__cause__.status == 401 or e.__cause__.status == 403:
                # Update API key status label from the main thread
                root.after(0, lambda: api_key_status_label.config(text="Invalid key (during fetch).", foreground="red"))
                error_msg = "Invalid or unauthorized API key during data fetch. Please check your key."
            else:
                error_msg = f"API error during data fetch: {e.__cause__.status}"
        elif isinstance(e, BadResponse): # Direct BadResponse (e.g. from test_api_key if not caught there)
             if e.status == 401 or e.status == 403:
                root.after(0, lambda: api_key_status_label.config(text="Invalid key (during fetch).", foreground="red"))
                error_msg = "Invalid or unauthorized API key. Please check your key."
             else:
                error_msg = f"API error: {e.status}"

        messagebox.showerror("Error", f"An error occurred:\n\n{error_msg}")
        status_update_callback(f"❌ Error: {error_msg[:100]}...") # Show a snippet of the error
    finally:
        # Re-enable the fetch button in the GUI thread
        root.after(0, lambda: fetch_button.config(state="normal"))


def start_fetch_thread():
    """
    Starts the data fetching process in a new thread to prevent the GUI from freezing.
    """
    # Disable the button to prevent multiple clicks
    fetch_button.config(state="disabled")
    # Create and start the thread
    thread = threading.Thread(target=fetch_and_save_data)
    thread.daemon = True  # Allows main window to exit even if thread is running
    thread.start()

# --- API Key Test Action ---
def test_api_key_action():
    """Tests the API key entered in the GUI."""
    api_key = polygon_api_key_entry.get().strip()
    if not api_key:
        api_key_status_label.config(text="Key is empty.", foreground="red")
        return

    is_valid, validation_msg = validate_api_key(api_key)
    if not is_valid:
        api_key_status_label.config(text=validation_msg, foreground="red")
        return

    api_key_status_label.config(text="Testing...", foreground="blue")
    root.update_idletasks() # Ensure label updates before blocking call

    try:
        client = RESTClient(api_key) # Ensure RESTClient is imported
        client.get_ticker_details("AAPL") # Test call with a common ticker
        api_key_status_label.config(text="Key is valid!", foreground="green")
        save_api_key(api_key)
    except BadResponse as e: # Ensure BadResponse is imported
        if e.status == 401 or e.status == 403: # Unauthorized
            api_key_status_label.config(text="Invalid or unauthorized key.", foreground="red")
        else: # Other API errors
            api_key_status_label.config(text=f"Test API error: {e.status}", foreground="red")
    except Exception as e: # Network errors, etc.
        # Show first 30 chars of error to avoid overly long messages
        api_key_status_label.config(text=f"Test connection error: {str(e)[:30]}", foreground="red")

# --- GUI SETUP ---
root = tk.Tk()
root.title("Polygon.io Data Downloader for Backtesting")
root.geometry("600x600")
root.resizable(True, True)

style = ttk.Style()
style.configure("TLabel", padding=5)
style.configure("TEntry", padding=5)
style.configure("TButton", padding=10)
style.configure("TRadiobutton", padding=5)

main_frame = ttk.Frame(root, padding="15 15 15 15")
main_frame.grid(row=0, column=0, sticky="nsew")

title_label = ttk.Label(main_frame, text="📈 Data Downloader for Backtesting (Polygon.io)", font=("Arial", 14, "bold"))
title_label.grid(row=0, column=0, columnspan=3, pady=(0, 15))

row_num = 1
# --- Asset Type Selection ---
ttk.Label(main_frame, text="Asset Type:", font=("Arial", 10, "bold")).grid(row=row_num, column=0, sticky="w")
asset_type_frame = ttk.Frame(main_frame)
asset_type_frame.grid(row=row_num, column=1, columnspan=2, sticky="ew", padx=(10, 0))
asset_type_var = tk.StringVar(value="Stocks")

def update_ticker_example(*args):
    if asset_type_var.get() == "Stocks":
        ticker_entry.delete(0, tk.END)
        ticker_entry.insert(0, "AAPL")
    else:
        ticker_entry.delete(0, tk.END)
        ticker_entry.insert(0, "X:BTC-USD")

stock_radio = ttk.Radiobutton(asset_type_frame, text="Stocks", variable=asset_type_var, value="Stocks", command=update_ticker_example)
stock_radio.pack(side="left", padx=5)
crypto_radio = ttk.Radiobutton(asset_type_frame, text="Crypto", variable=asset_type_var, value="Crypto", command=update_ticker_example)
crypto_radio.pack(side="left", padx=5)
row_num += 1

# --- Ticker Entry ---
ttk.Label(main_frame, text="Ticker Symbol:", font=("Arial", 10, "bold")).grid(row=row_num, column=0, sticky="w")
ticker_entry = ttk.Entry(main_frame, width=30, font=("Arial", 10))
ticker_entry.grid(row=row_num, column=1, columnspan=2, sticky="ew", padx=(10, 0))
ticker_entry.insert(0, "AAPL") # Default to stock example
row_num += 1

# --- API Key Entry ---
api_key_label = ttk.Label(main_frame, text="Polygon.io API Key:", font=("Arial", 10, "bold"))
api_key_label.grid(row=row_num, column=0, sticky="w")

polygon_api_key_entry = ttk.Entry(main_frame, width=30, show="*", font=("Arial", 10))
# polygon_api_key_entry.grid(row=row_num, column=1, columnspan=2, sticky="ew", padx=(10, 0)) # Old
polygon_api_key_entry.grid(row=row_num, column=1, sticky="ew", padx=(10, 0)) # New: col 1, no columnspan

test_key_button = ttk.Button(main_frame, text="Test Key", command=test_api_key_action)
test_key_button.grid(row=row_num, column=2, sticky="e", padx=(5,0))
row_num += 1

# --- API Key Status Label ---
api_key_status_label = ttk.Label(main_frame, text="Enter API key and test or load from config.", font=("Arial", 9))
api_key_status_label.grid(row=row_num, column=0, columnspan=3, sticky="ew", pady=(0,10), padx=(0,0)) # Spans 3 columns now
row_num += 1


# --- Date Entries ---
end_date_default = datetime.date.today()
start_date_default = end_date_default - datetime.timedelta(days=30) 

ttk.Label(main_frame, text="Start Date (YYYY-MM-DD):", font=("Arial", 10, "bold")).grid(row=row_num, column=0, sticky="w")
start_date_entry = ttk.Entry(main_frame, width=30, font=("Arial", 10))
start_date_entry.grid(row=row_num, column=1, columnspan=2, sticky="ew", padx=(10, 0))
start_date_entry.insert(0, start_date_default.strftime('%Y-%m-%d'))
row_num += 1

ttk.Label(main_frame, text="End Date (YYYY-MM-DD):", font=("Arial", 10, "bold")).grid(row=row_num, column=0, sticky="w")
end_date_entry = ttk.Entry(main_frame, width=30, font=("Arial", 10))
end_date_entry.grid(row=row_num, column=1, columnspan=2, sticky="ew", padx=(10, 0))
end_date_entry.insert(0, end_date_default.strftime('%Y-%m-%d'))
row_num += 1

# --- Interval Selection ---
ttk.Label(main_frame, text="Time Interval:", font=("Arial", 10, "bold")).grid(row=row_num, column=0, sticky="w")
interval_combobox = ttk.Combobox(main_frame, values=["1min", "5min", "10min", "15min", "30min", "60min"], width=27, state="readonly", font=("Arial", 10))
interval_combobox.grid(row=row_num, column=1, columnspan=2, sticky="ew", padx=(10, 0))
interval_combobox.set("30min")
row_num += 1

# --- Fetch Button ---
fetch_button = ttk.Button(main_frame, text="🚀 Fetch and Save Data", command=start_fetch_thread)
fetch_button.grid(row=row_num, column=0, columnspan=3, pady=20, sticky="ew")
row_num += 1

# --- Status Label ---
status_label = ttk.Label(main_frame, text="Enter API key and parameters, then click fetch.", relief="sunken", anchor="w", wraplength=550, font=("Arial", 9))
status_label.grid(row=row_num, column=0, columnspan=3, sticky="ew", pady=5)
row_num += 1

# --- Instructions ---
instructions_frame = ttk.LabelFrame(main_frame, text="Instructions", padding="10")
instructions_frame.grid(row=row_num, column=0, columnspan=3, sticky="ew", pady=10)

instructions_text = (
    "1. Get a FREE API key from polygon.io (up to 5 calls/minute).\n"
    "2. Select Asset Type: 'Stocks' (e.g., AAPL, TSLA) or 'Crypto' (e.g., X:BTC-USD).\n"
    "3. Enter a ticker symbol. The script will suggest the correct format.\n"
    "4. Choose a date range and time interval.\n"
    "5. Click 'Fetch and Save' to download the CSV for backtesting.\n\n"
    "⚠️ A long date range with a small interval may take several minutes due to API limits."
)
instructions_label = ttk.Label(instructions_frame, text=instructions_text, wraplength=550, justify="left", font=("Arial", 9))
instructions_label.pack()

main_frame.columnconfigure(1, weight=1)
root.columnconfigure(0, weight=1)
root.rowconfigure(0, weight=1)

def on_enter_key(event):
    if fetch_button['state'] == 'normal':
        start_fetch_thread()

root.bind('<Return>', on_enter_key)
root.bind('<KP_Enter>', on_enter_key)
ticker_entry.focus()

# Load API key on startup
initial_api_key = load_api_key()
if initial_api_key:
    polygon_api_key_entry.insert(0, initial_api_key)
    api_key_status_label.config(text="Loaded from config. Test or use.", foreground="blue")
else:
    api_key_status_label.config(text="Enter key to test or use.", foreground="black")


if __name__ == "__main__":
    print("Starting Polygon.io Data Downloader...")
    print("Make sure you have a valid Polygon.io API key and required libraries installed.")
    print("--> pip install polygon-python-client pandas pytz configparser") # Added configparser to instructions
    root.mainloop()