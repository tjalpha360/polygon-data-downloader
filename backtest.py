# --- IMPORTS ---
import tkinter as tk
from tkinter import ttk, filedialog, messagebox
import pandas as pd
from datetime import datetime # Specific import for strptime
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

# --- GLOBAL VARIABLES for Simulator ---
simulator_price_data_df = None
simulator_csv_filepath = None
# final_filepath from downloader logic will also be used globally for "last fetched"
# use_last_fetched_var is defined in GUI setup and will be accessed globally
pst_tz = pytz.timezone('America/Los_Angeles') # Define PST timezone for trade parsing

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
        
        if not final_filepath: # User cancelled save dialog
            status_update_callback("Save cancelled by user.")
            # Do not enable the checkbox if save was cancelled
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
        
        # Enable the "Use last fetched data" checkbox on the simulator tab
        if 'use_last_fetched_checkbutton' in globals() or 'use_last_fetched_checkbutton' in locals(): # Check if UI element exists
            root.after(0, lambda: use_last_fetched_checkbutton.config(state=tk.NORMAL))

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

# --- TRADE LIST PARSING LOGIC ---
def parse_trade_list(trade_list_string):
    """
    Parses a multi-line string of trades into a list of trade dictionaries.
    Returns a tuple: (parsed_trades, parsing_errors)
    """
    parsed_trades = []
    parsing_errors = []

    lines = trade_list_string.splitlines()
    for idx, line_content in enumerate(lines):
        line_num = idx + 1
        line = line_content.strip()
        if not line:
            continue

        parts = line.split(',')
        if len(parts) != 3:
            parsing_errors.append({
                'line_number': line_num, 'line': line,
                'error': 'Invalid format: Expected 3 comma-separated values (MM/DD/YY,HH:MM,Buy/Sell).'
            })
            continue

        date_str = parts[0].strip()
        time_str = parts[1].strip()
        action_str = parts[2].strip().lower()

        if action_str not in ['buy', 'sell']:
            parsing_errors.append({
                'line_number': line_num, 'line': line,
                'error': "Invalid action: Must be 'Buy' or 'Sell'."
            })
            continue

        try:
            # Attempt to parse date and time
            naive_dt = datetime.strptime(f"{date_str} {time_str}", "%m/%d/%y %H:%M")
            # Localize to PST/PDT
            # is_dst=None will raise AmbiguousTimeError or NonExistentTimeError if applicable
            localized_dt = pst_tz.localize(naive_dt, is_dst=None)
        except ValueError:
            parsing_errors.append({
                'line_number': line_num, 'line': line,
                'error': 'Invalid date/time format. Expected MM/DD/YY and HH:MM.'
            })
            continue
        except (pytz.exceptions.AmbiguousTimeError, pytz.exceptions.NonExistentTimeError) as e:
            parsing_errors.append({
                'line_number': line_num, 'line': line,
                'error': f'Timezone localization error (PST/PDT): {e}. This can happen around DST changes.'
            })
            continue
        except Exception as e: # Catch any other unexpected errors during datetime processing
             parsing_errors.append({
                'line_number': line_num, 'line': line,
                'error': f'Unexpected date/time processing error: {e}'
            })
             continue

        parsed_trades.append({
            'datetime': localized_dt,
            'action': action_str,
            'original_line': line
        })

    return parsed_trades, parsing_errors

# --- TRADE TIMESTAMP MATCHING LOGIC ---
def match_trades_to_data(parsed_trades, price_data_df):
    """
    Matches parsed trades to the nearest available timestamps in the price data.
    Returns a tuple: (matched_trades, matching_errors)
    """
    matched_trades = []
    matching_errors = []

    # Input Validation
    if price_data_df is None or price_data_df.empty:
        matching_errors.append({'trade_info': 'General', 'error': 'Price data is missing or empty. Cannot match trades.'})
        return matched_trades, matching_errors

    if not isinstance(price_data_df.index, pd.DatetimeIndex):
        matching_errors.append({'trade_info': 'General', 'error': 'Price data index is not a DatetimeIndex. Cannot match trades.'})
        return matched_trades, matching_errors

    if 'Open' not in price_data_df.columns:
        matching_errors.append({'trade_info': 'General', 'error': "Price data must contain an 'Open' column."})
        return matched_trades, matching_errors

    for trade_dict in parsed_trades:
        user_trade_datetime = trade_dict['datetime']
        try:
            # Find the integer index of the nearest row
            # Note: .index.asof(user_trade_datetime) might be an alternative if exact or earlier is needed,
            # but 'nearest' is often better for user-entered times that might not align perfectly.
            location_index = price_data_df.index.get_loc(user_trade_datetime, method='nearest')

            # Get the actual matched timestamp from the DataFrame's index
            matched_market_datetime = price_data_df.index[location_index]

            # Get the 'Open' price at that matched timestamp
            matched_open_price = price_data_df.loc[matched_market_datetime, 'Open']

            processed_trade = {
                'user_datetime': user_trade_datetime,
                'action': trade_dict['action'],
                'original_line': trade_dict['original_line'],
                'matched_market_datetime': matched_market_datetime,
                'matched_open_price': matched_open_price,
                'time_difference': abs(user_trade_datetime - matched_market_datetime)
            }
            matched_trades.append(processed_trade)

        except (KeyError, IndexError) as e: # get_loc can raise KeyError if time is totally out of bounds
            matching_errors.append({
                'trade_info': trade_dict, # Include original trade info for context
                'error': f"Could not match trade to market data. Trade time {user_trade_datetime.strftime('%m/%d/%y %H:%M %Z')} may be too far from available data range. Details: {str(e)}"
            })
        except Exception as e: # Catch any other unexpected errors during matching
            matching_errors.append({
                'trade_info': trade_dict,
                'error': f"Unexpected error matching trade scheduled for {user_trade_datetime.strftime('%m/%d/%y %H:%M %Z')}: {str(e)}"
            })

    return matched_trades, matching_errors

# --- SIMULATION RESULTS DISPLAY ---
def format_and_display_simulation_results(starting_capital_str, final_portfolio_value,
                                          portfolio_history, parsing_errors=None, matching_errors=None,
                                          critical_errors_list=None): # Added critical_errors_list
    """
    Formats the simulation results, errors, and trade log, then displays them
    in the simulation_results_text widget.
    """
    output_lines = []

    # Display Critical Errors First
    if critical_errors_list:
        output_lines.append("--- CRITICAL ERRORS ---")
        for err_msg in critical_errors_list:
            output_lines.append(err_msg)
        output_lines.append("---")
        # If critical errors, other sections might be skipped or altered by the calling function's logic,
        # but this function will still try to display whatever it's given.

    # Handle Starting Capital Conversion & Potential Error (if not already a critical error)
    # This is still useful for P/L calculation if the simulation ran despite a recoverable capital string issue.
    try:
        starting_capital = float(starting_capital_str)
    except ValueError:
        if not critical_errors_list or not any("Invalid starting capital" in crit_err for crit_err in critical_errors_list):
             output_lines.append("ERROR: Invalid starting capital format. Using $0.00 for P/L calculation.")
        starting_capital = 0.0 # Default for calculation if conversion fails

    # Display Parsing Errors
    if parsing_errors:
        output_lines.append("--- Trade List Parsing Errors ---")
        for err in parsing_errors:
            output_lines.append(f"L{err['line_number']}: '{err['line']}' -> {err['error']}")
        output_lines.append("-" * 50)

    # Display Matching Errors
    if matching_errors:
        output_lines.append("--- Trade Timestamp Matching Errors ---")
        for err in matching_errors:
            trade_line_info = err['trade_info']['original_line'] if isinstance(err['trade_info'], dict) else str(err['trade_info'])
            output_lines.append(f"Trade: '{trade_line_info}' -> {err['error']}")
        output_lines.append("-" * 50)

    # If there were critical errors and no trades, indicate simulation might not have run
    has_critical_errors = bool(parsing_errors or (matching_errors and any(err['trade_info'] == 'General' for err in matching_errors)))

    if not portfolio_history and has_critical_errors:
        output_lines.append("Simulation did not proceed or no trades were processed due to critical errors listed above.")
    elif not portfolio_history and not has_critical_errors : # No errors, but also no trades in history (e.g. empty trade list input)
        output_lines.append("No trades were submitted or processed.")

    # Summary Section (only if portfolio_history is not empty or if it's empty but there were no critical errors)
    # This ensures summary is shown even if user submits empty trade list but data was fine.
    if portfolio_history or not has_critical_errors:
        pnl = final_portfolio_value - starting_capital
        pnl_percent = (pnl / starting_capital) * 100 if starting_capital != 0 else 0 # Avoid division by zero

        output_lines.append("--- Simulation Summary ---")
        output_lines.append(f"Starting Capital:          ${starting_capital:,.2f}")
        output_lines.append(f"Final Portfolio Value:     ${final_portfolio_value:,.2f}")
        output_lines.append(f"Profit/Loss (P/L):         ${pnl:,.2f} ({pnl_percent:.2f}%)")
        output_lines.append("-" * 50)

    # Trade Log Section
    if portfolio_history:
        output_lines.append("--- Trade Log ---")
        for record in portfolio_history:
            ts_str = record['timestamp'].strftime('%Y-%m-%d %H:%M:%S %Z')
            output_lines.append(f"[{ts_str}] REF: '{record['original_line']}'")
            output_lines.append(f"  ACTION: {record['action'].upper():<4s}, PRICE: ${record['price']:>9,.2f}, SHARES_TRADED: {record['shares_transacted']:>10.4f}")
            output_lines.append(f"  STATUS: Cash: ${record['cash_after_trade']:>10,.2f}, Shares Held: {record['shares_held_after_trade']:>10.4f}, Portfolio Value: ${record['portfolio_value_after_trade']:>12,.2f}")
            output_lines.append("-" * 50)

    # Update the GUI Text widget
    # Ensure simulation_results_text is accessible (global or passed appropriately)
    if 'simulation_results_text' in globals() or 'simulation_results_text' in locals():
        simulation_results_text.config(state=tk.NORMAL)
        simulation_results_text.delete('1.0', tk.END)
        simulation_results_text.insert(tk.END, "\n".join(output_lines))
        simulation_results_text.config(state=tk.DISABLED)
    else:
        print("Error: simulation_results_text widget not found for displaying results.")
        # Fallback to console if GUI element isn't available for some reason
        print("\n".join(output_lines))


# --- SIMULATION ORCHESTRATION & GUI ---
def orchestrate_simulation():
    """
    Orchestrates the entire simulation process from GUI inputs to displaying results.
    This function is triggered by the 'Run Backtest Simulation' button.
    """
    # 1. Get Inputs & Initial Checks
    if simulator_price_data_df is None or simulator_price_data_df.empty:
        format_and_display_simulation_results(
            starting_capital_str="0", # Dummy value
            final_portfolio_value=0,
            portfolio_history=[],
            critical_errors_list=["Error: No price data (CSV) loaded for simulation."]
        )
        return

    trade_list_str = trade_list_text.get("1.0", tk.END).strip()
    if not trade_list_str:
        format_and_display_simulation_results(
            starting_capital_str=starting_capital_entry.get(), # Pass along for consistency if needed
            final_portfolio_value=0, # No simulation run
            portfolio_history=[],
            critical_errors_list=["Error: Trade list is empty."]
        )
        return

    starting_capital_str = starting_capital_entry.get()
    try:
        current_starting_capital_float = float(starting_capital_str)
    except ValueError:
        format_and_display_simulation_results(
            starting_capital_str=starting_capital_str,
            final_portfolio_value=0, # No simulation run
            portfolio_history=[],
            critical_errors_list=[f"Critical Error: Invalid starting capital value '{starting_capital_str}'."]
        )
        return

    # 2. Call Processing Functions
    parsed_trades, parsing_errors = parse_trade_list(trade_list_str)

    matched_trades = []
    matching_errors = []
    portfolio_history = []
    final_portfolio_value = current_starting_capital_float # Default if simulation doesn't run

    if parsed_trades: # Only proceed if there are trades to match
        matched_trades, matching_errors = match_trades_to_data(parsed_trades, simulator_price_data_df)
        if matched_trades: # Only run simulation if trades were successfully matched
            final_portfolio_value, portfolio_history = run_simulation_engine(
                matched_trades, current_starting_capital_float
            )
        # If no matched_trades but there were parsed_trades, it implies all failed matching.
        # matching_errors will be displayed.
    # If no parsed_trades, parsing_errors will be displayed.

    # 3. Display Results
    format_and_display_simulation_results(
        starting_capital_str,
        final_portfolio_value,
        portfolio_history,
        parsing_errors,
        matching_errors
        # critical_errors_list is handled by early exits for now for these specific checks.
    )

# --- SIMULATION ENGINE ---
def run_simulation_engine(matched_trades, starting_capital):
    """
    Runs the trade simulation based on matched trades and starting capital.
    Returns a tuple: (final_portfolio_value, portfolio_history)
    """
    cash = float(starting_capital)
    shares_held = 0.0
    portfolio_history = []
    # current_portfolio_value = cash # Initial value before any trades

    # Sort trades by their matched market execution time
    sorted_trades = sorted(matched_trades, key=lambda x: x['matched_market_datetime'])

    for trade in sorted_trades:
        price = trade['matched_open_price']
        action = trade['action']
        timestamp = trade['matched_market_datetime']
        original_line = trade['original_line'] # For history record

        shares_transacted_in_step = 0.0

        if action == 'buy':
            # Check for valid price and if there's cash to spend (epsilon for float comparison)
            if cash > 1e-6 and price > 1e-9:
                shares_to_buy = cash / price
                shares_held += shares_to_buy
                shares_transacted_in_step = shares_to_buy
                cash = 0.0  # All cash used to buy
            # else: Buy skipped (not enough cash or price is zero/negligible)

        elif action == 'sell':
            # Check if shares are held and price is valid
            if shares_held > 1e-9 and price > 1e-9:
                cash_from_sale = shares_held * price
                cash += cash_from_sale
                shares_transacted_in_step = -shares_held # Negative for selling all shares
                shares_held = 0.0 # All shares sold
            # else: Sell skipped (no shares to sell or price is zero/negligible)

        # Valuation after this trade attempt
        current_portfolio_value = cash + (shares_held * price)

        portfolio_history.append({
            'timestamp': timestamp,
            'action': action, # The intended action for this step
            'price': price, # Market price at which transaction (or valuation) occurred
            'shares_transacted': shares_transacted_in_step, # Actual shares bought/sold
            'cash_after_trade': cash,
            'shares_held_after_trade': shares_held,
            'portfolio_value_after_trade': current_portfolio_value,
            'original_line': original_line # Helps trace back to user input
        })

    # Determine final portfolio value
    if portfolio_history:
        final_portfolio_value = portfolio_history[-1]['portfolio_value_after_trade']
    else:
        # No trades executed, value is still starting capital
        final_portfolio_value = float(starting_capital)
        # Optionally, add an initial state to portfolio_history if desired for consistency
        # when no trades occur, but the spec implies history is only for trades.

    return final_portfolio_value, portfolio_history

# --- SIMULATOR CSV LOADING LOGIC ---
def do_manual_load_csv():
    global simulator_price_data_df, simulator_csv_filepath

    filepath = filedialog.askopenfilename(
        title="Select Price Data CSV",
        filetypes=[("CSV files", "*.csv"), ("All files", "*.*")]
    )
    if not filepath:
        # User cancelled dialog
        # If nothing was ever successfully loaded, ensure label reflects "No CSV loaded"
        if simulator_csv_filepath is None:
            loaded_csv_label.config(text="No CSV loaded.")
        # Otherwise, label continues to show the previously loaded file
        return

    try:
        temp_df = pd.read_csv(filepath, index_col='timestamp', parse_dates=True)
        if 'Open' not in temp_df.columns:
            messagebox.showerror("CSV Error", "The selected CSV must contain an 'Open' column.")
            return # Keep existing loaded_csv_label text or "No CSV loaded" if first attempt

        simulator_price_data_df = temp_df
        simulator_csv_filepath = filepath
        loaded_csv_label.config(text=f"Loaded: {os.path.basename(simulator_csv_filepath)}")
        use_last_fetched_var.set(False) # Uncheck the "use last fetched" if a manual load is successful
    except Exception as e:
        messagebox.showerror("CSV Load Error", f"Failed to load or parse CSV:\n{e}")
        # If a previous file was loaded, its name remains in the label. If not, it's "No CSV loaded".

def handle_use_last_fetched_toggle():
    global simulator_price_data_df, simulator_csv_filepath, final_filepath # final_filepath is from downloader

    if use_last_fetched_var.get(): # Checkbox is checked
        if final_filepath and os.path.exists(final_filepath):
            try:
                temp_df = pd.read_csv(final_filepath, index_col='timestamp', parse_dates=True)
                if 'Open' not in temp_df.columns:
                    messagebox.showerror("CSV Error", "The last fetched CSV must contain an 'Open' column.")
                    use_last_fetched_var.set(False) # Uncheck due to error
                    return

                simulator_price_data_df = temp_df
                simulator_csv_filepath = final_filepath
                loaded_csv_label.config(text=f"Using last fetched: {os.path.basename(simulator_csv_filepath)}")
            except Exception as e:
                messagebox.showerror("CSV Load Error", f"Failed to load or parse last fetched CSV:\n{e}")
                use_last_fetched_var.set(False) # Uncheck due to error
        else:
            messagebox.showwarning("No Data", "No data has been fetched in the current session, or the file is missing.")
            use_last_fetched_var.set(False) # Uncheck as there's nothing to use
    else: # Checkbox is unchecked
        # If the currently loaded data IS the "last fetched" data, then clear it.
        if simulator_csv_filepath and final_filepath and simulator_csv_filepath == final_filepath:
            simulator_price_data_df = None
            simulator_csv_filepath = None
            loaded_csv_label.config(text="No CSV loaded.")
        # If a manually loaded CSV was active, or no CSV was active, unchecking does nothing to the current data.
        # The label will either show the manually loaded file or "No CSV loaded".


# --- GUI SETUP ---
root = tk.Tk()
root.title("Polygon.io Data Downloader & Trade Simulator") # Updated title
root.geometry("750x750") # Adjusted size for notebook
root.resizable(True, True)

style = ttk.Style()
style.configure("TLabel", padding=5)
style.configure("TEntry", padding=5)
style.configure("TButton", padding=5) # Reduced padding slightly for denser UI on simulator
style.configure("TRadiobutton", padding=5)
style.configure("TNotebook.Tab", padding=(10, 5)) # Padding for tab labels

# --- Main Notebook ---
notebook = ttk.Notebook(root)
notebook.grid(row=0, column=0, sticky="nsew")

root.columnconfigure(0, weight=1)
root.rowconfigure(0, weight=1)

# --- Tab 1: Data Downloader ---
main_frame = ttk.Frame(notebook, padding="15 15 15 15")
notebook.add(main_frame, text='Data Downloader')

title_label = ttk.Label(main_frame, text="📈 Data Downloader (Polygon.io)", font=("Arial", 14, "bold")) # Shortened title
title_label.grid(row=0, column=0, columnspan=3, pady=(0, 15))

# Initialize row_num for main_frame (downloader tab)
downloader_row_num = 1
# --- Asset Type Selection ---
ttk.Label(main_frame, text="Asset Type:", font=("Arial", 10, "bold")).grid(row=downloader_row_num, column=0, sticky="w")
asset_type_frame = ttk.Frame(main_frame)
asset_type_frame.grid(row=downloader_row_num, column=1, columnspan=2, sticky="ew", padx=(10, 0))
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
downloader_row_num += 1

# --- Ticker Entry ---
ttk.Label(main_frame, text="Ticker Symbol:", font=("Arial", 10, "bold")).grid(row=downloader_row_num, column=0, sticky="w")
ticker_entry = ttk.Entry(main_frame, width=30, font=("Arial", 10))
ticker_entry.grid(row=downloader_row_num, column=1, columnspan=2, sticky="ew", padx=(10, 0))
ticker_entry.insert(0, "AAPL") # Default to stock example
downloader_row_num += 1

# --- API Key Entry ---
api_key_label = ttk.Label(main_frame, text="Polygon.io API Key:", font=("Arial", 10, "bold"))
api_key_label.grid(row=downloader_row_num, column=0, sticky="w")

polygon_api_key_entry = ttk.Entry(main_frame, width=30, show="*", font=("Arial", 10))
polygon_api_key_entry.grid(row=downloader_row_num, column=1, sticky="ew", padx=(10, 0))

test_key_button = ttk.Button(main_frame, text="Test Key", command=test_api_key_action)
test_key_button.grid(row=downloader_row_num, column=2, sticky="e", padx=(5,0))
downloader_row_num += 1

# --- API Key Status Label ---
api_key_status_label = ttk.Label(main_frame, text="Enter API key and test or load from config.", font=("Arial", 9))
api_key_status_label.grid(row=downloader_row_num, column=0, columnspan=3, sticky="ew", pady=(0,10), padx=(0,0))
downloader_row_num += 1


# --- Date Entries ---
end_date_default = datetime.date.today()
start_date_default = end_date_default - datetime.timedelta(days=30) 

ttk.Label(main_frame, text="Start Date (YYYY-MM-DD):", font=("Arial", 10, "bold")).grid(row=downloader_row_num, column=0, sticky="w")
start_date_entry = ttk.Entry(main_frame, width=30, font=("Arial", 10))
start_date_entry.grid(row=downloader_row_num, column=1, columnspan=2, sticky="ew", padx=(10, 0))
start_date_entry.insert(0, start_date_default.strftime('%Y-%m-%d'))
downloader_row_num += 1

ttk.Label(main_frame, text="End Date (YYYY-MM-DD):", font=("Arial", 10, "bold")).grid(row=downloader_row_num, column=0, sticky="w")
end_date_entry = ttk.Entry(main_frame, width=30, font=("Arial", 10))
end_date_entry.grid(row=downloader_row_num, column=1, columnspan=2, sticky="ew", padx=(10, 0))
end_date_entry.insert(0, end_date_default.strftime('%Y-%m-%d'))
downloader_row_num += 1

# --- Interval Selection ---
ttk.Label(main_frame, text="Time Interval:", font=("Arial", 10, "bold")).grid(row=downloader_row_num, column=0, sticky="w")
interval_combobox = ttk.Combobox(main_frame, values=["1min", "5min", "10min", "15min", "30min", "60min"], width=27, state="readonly", font=("Arial", 10))
interval_combobox.grid(row=downloader_row_num, column=1, columnspan=2, sticky="ew", padx=(10, 0))
interval_combobox.set("30min")
downloader_row_num += 1

# --- Fetch Button ---
fetch_button = ttk.Button(main_frame, text="🚀 Fetch and Save Data", command=start_fetch_thread)
fetch_button.grid(row=downloader_row_num, column=0, columnspan=3, pady=20, sticky="ew")
downloader_row_num += 1

# --- Status Label ---
status_label = ttk.Label(main_frame, text="Enter API key and parameters, then click fetch.", relief="sunken", anchor="w", wraplength=550, font=("Arial", 9))
status_label.grid(row=downloader_row_num, column=0, columnspan=3, sticky="ew", pady=5)
downloader_row_num += 1

# --- Instructions ---
instructions_frame = ttk.LabelFrame(main_frame, text="Instructions", padding="10")
instructions_frame.grid(row=downloader_row_num, column=0, columnspan=3, sticky="ew", pady=10)

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

main_frame.columnconfigure(1, weight=1) # Allow ticker entry etc. to expand a bit

# --- Tab 2: Trade Simulator ---
simulator_frame = ttk.Frame(notebook, padding="15 15 15 15")
notebook.add(simulator_frame, text='Trade Simulator')

sim_row_num = 0

# CSV Input Section
load_csv_button = ttk.Button(simulator_frame, text="Load Price Data CSV", command=do_manual_load_csv)
load_csv_button.grid(row=sim_row_num, column=0, padx=5, pady=10, sticky="w")
loaded_csv_label = ttk.Label(simulator_frame, text="No CSV loaded.") # Initial text
loaded_csv_label.grid(row=sim_row_num, column=1, padx=5, pady=10, sticky="ew", columnspan=2)
sim_row_num += 1

use_last_fetched_var = tk.BooleanVar(value=False) # Explicitly False initially
use_last_fetched_checkbutton = ttk.Checkbutton(
    simulator_frame,
    text="Use last fetched data (if available)",
    variable=use_last_fetched_var,
    state=tk.DISABLED,  # Initial state
    command=handle_use_last_fetched_toggle
)
use_last_fetched_checkbutton.grid(row=sim_row_num, column=0, columnspan=3, padx=5, pady=5, sticky="w")
sim_row_num += 1

# Trade List Input Section
trade_list_label = ttk.Label(simulator_frame, text="Paste Trade List (MM/DD/YY,HH:MM,Buy/Sell):")
trade_list_label.grid(row=sim_row_num, column=0, columnspan=3, padx=5, pady=5, sticky="w")
sim_row_num += 1

trade_list_text = tk.Text(simulator_frame, height=10, width=60, relief="sunken", borderwidth=1)
trade_list_text.grid(row=sim_row_num, column=0, columnspan=3, padx=5, pady=5, sticky="nsew")
trade_list_scrollbar = ttk.Scrollbar(simulator_frame, orient="vertical", command=trade_list_text.yview)
trade_list_scrollbar.grid(row=sim_row_num, column=3, sticky="ns", pady=5)
trade_list_text.config(yscrollcommand=trade_list_scrollbar.set)
sim_row_num += 1

# Starting Capital Section
starting_capital_label = ttk.Label(simulator_frame, text="Starting Portfolio ($):")
starting_capital_label.grid(row=sim_row_num, column=0, padx=5, pady=10, sticky="w")
starting_capital_entry = ttk.Entry(simulator_frame, width=15)
starting_capital_entry.insert(0, "5000")
starting_capital_entry.grid(row=sim_row_num, column=1, padx=5, pady=10, sticky="w")
sim_row_num += 1

# Action Button
run_simulation_button = ttk.Button(simulator_frame, text="🚀 Run Backtest Simulation", command=orchestrate_simulation)
run_simulation_button.grid(row=sim_row_num, column=0, columnspan=3, padx=20, pady=15, sticky="ew")
sim_row_num += 1

# Results Display Section
results_label = ttk.Label(simulator_frame, text="Simulation Results:")
results_label.grid(row=sim_row_num, column=0, columnspan=3, padx=5, pady=5, sticky="w")
sim_row_num += 1

simulation_results_text = tk.Text(simulator_frame, height=15, width=80, state="disabled", relief="sunken", borderwidth=1)
simulation_results_text.grid(row=sim_row_num, column=0, columnspan=3, padx=5, pady=5, sticky="nsew")
simulation_results_scrollbar = ttk.Scrollbar(simulator_frame, orient="vertical", command=simulation_results_text.yview)
simulation_results_scrollbar.grid(row=sim_row_num, column=3, sticky="ns", pady=5)
simulation_results_text.config(yscrollcommand=simulation_results_scrollbar.set)
sim_row_num += 1

# Configure column/row weights for simulator_frame
simulator_frame.columnconfigure(1, weight=1) # Allow loaded_csv_label to expand
simulator_frame.columnconfigure(2, weight=1) # Allow space for scrollbars correctly
# simulator_frame.columnconfigure(0, weight=0) # default
# simulator_frame.columnconfigure(3, weight=0) # scrollbar column

simulator_frame.rowconfigure(sim_row_num - 5, weight=1) # trade_list_text (currently row 3 from its definition)
simulator_frame.rowconfigure(sim_row_num - 1, weight=1) # simulation_results_text (currently row 7 from its definition)


# --- Global Bindings & Startup ---
def on_enter_key(event):
    # Only trigger fetch if downloader tab is active and button is normal
    # This check might need to be more robust if more tabs get enter key actions
    if notebook.index(notebook.select()) == 0 and fetch_button['state'] == 'normal':
        start_fetch_thread()

root.bind('<Return>', on_enter_key)
root.bind('<KP_Enter>', on_enter_key)
ticker_entry.focus() # Focus on ticker entry in downloader tab initially

# Load API key on startup (downloader tab)
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