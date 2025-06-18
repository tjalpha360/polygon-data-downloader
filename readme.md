# Polygon.io Data Downloader for Backtesting

A user-friendly Python GUI application to download historical stock and cryptocurrency data from Polygon.io. The script is designed to fetch intraday data for a specified ticker, date range, and interval, and save it as a CSV file containing only the 'Open' prices, perfect for backtesting trading strategies.

## Features

- **Dual Asset Support:** Download data for both Stocks and Cryptocurrencies.
- **User-Friendly GUI:** Simple interface built with Tkinter for easy operation.
- **Customizable Queries:** Specify ticker, date range, and time interval (1min, 5min, 15min, 30min, 60min).
- **Rate Limit Handling:** Automatically waits between API calls to respect Polygon.io's free tier limits (5 calls/minute).
- **Responsive Interface:** Data fetching runs in a background thread to prevent the GUI from freezing.
- **Robust Error Handling:** Provides clear feedback and allows continuing on minor API errors.
- **Backtest-Ready Output:** Saves a clean CSV file with a single 'Open' price column and a timezone-aware timestamp index (UTC+2).

## Requirements

- Python 3.6+
- A free API key from [Polygon.io](https://polygon.io/dashboard)

## Installation

1.  **Clone the repository:**
    ```bash
    git clone https://github.com/YOUR_USERNAME/polygon-data-downloader.git
    cd polygon-data-downloader
    ```

2.  **Install the required Python libraries:**
    It's highly recommended to use a virtual environment.
    ```bash
    # Create and activate a virtual environment (optional but recommended)
    python -m venv venv
    source venv/bin/activate  # On Windows, use `venv\Scripts\activate`

    # Install dependencies
    pip install -r requirements.txt
    ```

## How to Use

1.  **Run the script:**
    ```bash
    python backtest.py
    ```
2.  **Enter your Polygon.io API Key.**
3.  **Select the Asset Type** (Stocks or Crypto).
4.  **Enter the Ticker Symbol** (e.g., `AAPL` for stocks, `X:BTC-USD` for crypto).
5.  **Set the Start and End Dates** in `YYYY-MM-DD` format.
6.  **Choose the Time Interval.**
7.  **Click "🚀 Fetch and Save Data".**
8.  A dialog will appear to save the resulting `.csv` file.

## License

This project is licensed under the MIT License. See the [LICENSE](LICENSE) file for details.