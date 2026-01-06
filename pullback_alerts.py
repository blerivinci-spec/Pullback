#!/usr/bin/env python3
import yfinance as yf
import pandas as pd
import numpy as np
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart

# ----------------------------
# CONFIGURATION
# ----------------------------

top_stocks = [
    "AAPL", "MSFT", "AMZN", "TSLA", "GOOGL", "NVDA", "META",
    "BRK-B", "UNH", "V", "JNJ", "KO", "PEP", "PG", "DIS"
]

spy_symbol = "SPY"

# Pullback thresholds (% drop)
pullback_thresholds_stocks = {
    1: 0.005,   # daily
    7: 0.010,
    15: 0.120,
    30: 0.130
}

pullback_thresholds_spy = {
    1: 0.025,  # daily
    7: 0.051,
    15: 0.10,
    30: 0.15
}

# LEAP expiry (months)
leap_expiry_months_stocks = {1: 12, 7: 12, 15: 18, 30: 24}
leap_expiry_months_spy = {1: 12, 7: 12, 15: 18, 30: 24}

# Recovery assumptions
recovery_map_stocks = {1: 0.10, 7: 0.20, 15: 0.40, 30: 0.60}
recovery_map_spy = {1: 0.05, 7: 0.10, 15: 0.20, 30: 0.30}

# ----------------------------
# DATA HELPERS
# ----------------------------

def get_sp500_symbols():
    """Fetch current S&P 500 constituents. If parser missing, return empty list."""
    try:
        table = pd.read_html(
            "https://en.wikipedia.org/wiki/List_of_S%26P_500_companies",
            flavor="lxml"  # specify parser
        )
        df = table[0]
        symbols = df["Symbol"].tolist()
        symbols = [s.replace(".", "-") for s in symbols]
        return symbols
    except (ImportError, ValueError) as e:
        print("⚠️ Could not fetch S&P 500 list (missing parser or network issue). Skipping S&P 500 scan.")
        return []

def calculate_pullback(data, period):
    if period == 1:
        return (data["Close"].shift(1) - data["Close"]) / data["Close"].shift(1)
    max_price = data["Close"].rolling(window=period, min_periods=1).max()
    return (max_price - data["Close"]) / max_price

def suggest_leap_strike(price, pullback, is_spy):
    if is_spy:
        return round(price * (1.02 if pullback >= 0.05 else 1.0), 1)
    if pullback < 0.10:
        return round(price, 1)
    elif pullback < 0.20:
        return round(price * 1.05, 1)
    return round(price * 1.10, 1)

def suggest_expiry(period, is_spy):
    return (leap_expiry_months_spy if is_spy else leap_expiry_months_stocks)[period]

def estimate_payoff(price, strike, recovery):
    return max(0, price * (1 + recovery) - strike)

# ----------------------------
# EMAIL
# ----------------------------

def send_email_report(df, sender, password, receiver=None):
    if receiver is None:
        receiver = sender

    # Convert DataFrame to HTML WITHOUT borders
    html_table = df.to_html(index=False, justify="center")

    # Add custom CSS: single-line border + shaded header row
    html = f"""
    <html>
    <head>
        <style>
            table {{
                border-collapse: collapse;
                width: 100%;
                font-family: Arial, sans-serif;
                font-size: 13px;
            }}
            th {{
                border: 1px solid #999;
                padding: 6px 8px;
                text-align: center;
                background-color: #f2f2f2;
                font-weight: bold;
            }}
            td {{
                border: 1px solid #999;
                padding: 6px 8px;
                text-align: center;
            }}
        </style>
    </head>
    <body>
        {html_table}
    </body>
    </html>
    """

    msg = MIMEMultipart("alternative")
    msg["Subject"] = "📉 Daily Pullback Alert"
    msg["From"] = sender
    msg["To"] = receiver
    msg.attach(MIMEText(html, "html"))

    with smtplib.SMTP("smtp.gmail.com", 587) as server:
        server.starttls()
        server.login(sender, password)
        server.sendmail(sender, receiver, msg.as_string())

# ----------------------------
# CORE SCAN
# ----------------------------

def process_symbol(symbol, thresholds, group, is_spy=False):
    try:
        data = yf.download(symbol, period="1y", auto_adjust=True, progress=False)
    except Exception:
        return []

    data.dropna(inplace=True)
    if data.empty:
        return []

    alerts = []
    price = data["Close"].iloc[-1].item()

    for period, threshold in thresholds.items():
        pullback_series = calculate_pullback(data, period)
        if pullback_series.empty:
            continue

        pullback = pullback_series.iloc[-1].item()

        if np.isnan(pullback) or pullback < threshold:
            continue

        strike = suggest_leap_strike(price, pullback, is_spy)
        expiry = suggest_expiry(period, is_spy)
        recovery = (recovery_map_spy if is_spy else recovery_map_stocks)[period]
        payoff = estimate_payoff(price, strike, recovery)

        alerts.append({
            "Group": group,
            "Symbol": symbol,
            "Current Price": round(price, 2),
            "Pullback %": round(pullback * 100, 2),
            "Period (days)": period,
            "Suggested LEAP Strike": strike,
            "Suggested Expiry (months)": expiry,
            "Estimated Payoff (1yr recovery)": round(payoff, 2)
        })

    return alerts

# ----------------------------
# MAIN
# ----------------------------

def main():
    alerts = []

    # SPY
    alerts += process_symbol(spy_symbol, pullback_thresholds_spy, "SPY", is_spy=True)

    # Top 15
    for s in top_stocks:
        alerts += process_symbol(s, pullback_thresholds_stocks, "Top 15")

    # S&P 500
    sp500_symbols = set(get_sp500_symbols()) - set(top_stocks) - {spy_symbol}
    for s in sp500_symbols:
        alerts += process_symbol(s, pullback_thresholds_stocks, "S&P 500")

    # Create DataFrame
    df = pd.DataFrame(alerts)
    if df.empty:
        df = pd.DataFrame([{
            "Group": "-",
            "Symbol": "-",
            "Current Price": "-",
            "Pullback %": "-",
            "Period (days)": "-",
            "Suggested LEAP Strike": "-",
            "Suggested Expiry (months)": "-",
            "Estimated Payoff (1yr recovery)": "No alerts today"
        }])

    df.sort_values(["Period (days)", "Group", "Symbol"], inplace=True)
    df.reset_index(drop=True, inplace=True)

    # Send email
    send_email_report(
        df,
        os.environ.get("EMAIL_USER"),
        os.environ.get("EMAIL_PASS"),
        os.getenv("RECEIVER_EMAIL")
    )

    print("✅ Daily pullback scan completed")

if __name__ == "__main__":
    main()
