#!/usr/bin/env python3
import yfinance as yf
import pandas as pd
import numpy as np
import os
import smtplib
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from datetime import date

# ----------------------------
# CONFIGURATION
# ----------------------------
top_stocks = [
    "AAPL", "MSFT", "AMZN", "TSLA", "GOOGL", "NVDA", "META",
    "BRK-B", "UNH", "V", "JNJ", "KO", "PEP", "PG", "DIS"
]

spy_symbol = "SPY"

# Pullback thresholds (% drop)
pullback_thresholds_stocks = {1: 0.0005, 7: 0.10, 15: 0.20, 30: 0.30}  # daily + multi-day
pullback_thresholds_spy = {1: 0.025, 7: 0.051, 15: 0.10, 30: 0.15}    # daily + multi-day

# LEAP expiry in months
leap_expiry_months_stocks = {1: 12, 7: 12, 15: 18, 30: 24}
leap_expiry_months_spy = {1: 12, 7: 12, 15: 18, 30: 24}

# Recovery assumptions
recovery_map_stocks = {1: 0.10, 7: 0.20, 15: 0.40, 30: 0.60}
recovery_map_spy = {1: 0.05, 7: 0.10, 15: 0.20, 30: 0.30}

# ----------------------------
# FUNCTIONS
# ----------------------------
def calculate_pullback(data, period):
    if period == 1:
        # daily pullback: previous close -> today close
        pct_drop = (data['Close'].shift(1) - data['Close']) / data['Close'].shift(1)
    else:
        max_price = data['Close'].rolling(window=period, min_periods=1).max()
        pct_drop = (max_price - data['Close']) / max_price
    return pct_drop

def suggest_leap_strike(current_price, pct_drop, is_spy=False):
    if is_spy:
        if pct_drop < 0.05:
            return round(current_price, 1)
        elif pct_drop < 0.10:
            return round(current_price * 1.02, 1)
        else:
            return round(current_price * 1.05, 1)
    else:
        if pct_drop < 0.10:
            return round(current_price, 1)
        elif pct_drop < 0.20:
            return round(current_price * 1.05, 1)
        else:
            return round(current_price * 1.10, 1)

def suggest_expiry(period, is_spy=False):
    return (leap_expiry_months_spy if is_spy else leap_expiry_months_stocks).get(period, 12)

def estimate_payoff(current_price, strike, recovery_pct):
    target_price = current_price * (1 + recovery_pct)
    intrinsic_value = max(0, target_price - strike)
    return intrinsic_value

def send_email_report(df, sender_email, sender_password, receiver_email=None):
    if not receiver_email:
        receiver_email = sender_email  # fallback to self

    msg = MIMEMultipart()
    msg["From"] = sender_email
    msg["To"] = receiver_email
    msg["Subject"] = "Daily Pullback Alert"

    body = df.to_string(index=False)
    msg.attach(MIMEText(body, "plain"))

    server = smtplib.SMTP("smtp.gmail.com", 587)
    server.starttls()
    server.login(sender_email, sender_password)
    server.sendmail(sender_email, receiver_email, msg.as_string())
    server.quit()

    print(f"📧 Email successfully sent to {receiver_email}")


def process_symbol(symbol, thresholds, is_spy=False):
    try:
        data = yf.download(symbol, period="1y", auto_adjust=True, progress=False)
    except Exception:
        return []
    data.dropna(inplace=True)
    if data.empty:
        return []

    alerts = []

    for period, threshold in thresholds.items():
        data[f'pullback_{period}d'] = calculate_pullback(data, period)
        # take the latest value
        try:
            current_pullback = float(data[f'pullback_{period}d'].iloc[-1])
        except Exception:
            continue

        # ensure pullback is non-negative (some edge cases)
        current_pullback = max(0.0, current_pullback)

        if current_pullback >= threshold:
            current_price = float(data['Close'].iloc[-1])
            strike = suggest_leap_strike(current_price, current_pullback, is_spy)
            expiry = suggest_expiry(period, is_spy)
            payoff = estimate_payoff(
                current_price,
                strike,
                recovery_map_spy.get(period, 0.2) if is_spy else recovery_map_stocks.get(period, 0.2)
            )
            alerts.append({
                "Symbol": symbol,
                "Current Price": round(current_price, 2),
                "Pullback %": round(current_pullback * 100, 2),
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
    all_alerts = []

    # Process SPY
    all_alerts.extend(process_symbol(spy_symbol, pullback_thresholds_spy, is_spy=True))

    # Process top stocks
    for stock in top_stocks:
        all_alerts.extend(process_symbol(stock, pullback_thresholds_stocks, is_spy=False))

    alerts_df = pd.DataFrame(all_alerts)

    if alerts_df.empty:
        print("No pullback alerts triggered today.")
        return

    # Sort so daily (1-day) alerts appear first, then 7, 15, 30 — and group by symbol
    alerts_df.sort_values(by=["Period (days)", "Symbol"], ascending=[True, True], inplace=True)
    # Reindex for clean output
    alerts_df.reset_index(drop=True, inplace=True)

    print(alerts_df)

    # Send email using environment variables (set in GitHub Actions secrets)
    sender_email = os.environ.get("EMAIL_USER")
    sender_password = os.environ.get("EMAIL_PASS")
    receiver_email = os.getenv("RECEIVER_EMAIL")  # may be None

    if not sender_email or not sender_password:
        print("EMAIL_USER and EMAIL_PASS environment variables must be set to send email.")
        return

    send_email_report(alerts_df, sender_email, sender_password, receiver_email)
    print("Email sent to", receiver_email)

if __name__ == "__main__":
    main()
