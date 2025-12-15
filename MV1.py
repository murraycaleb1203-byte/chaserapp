import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
from datetime import timedelta

# ==========================================
# 1. CONFIGURATION
# ==========================================
print("--- MOBILE CHASER V6: ROBUST SERVER VERSION ---")

BENCHMARK_TICKER = 'SPY' 
SMA_PERIOD = 300          
START_CAPITAL = 10000

# Constants for Synthetic Backfill
# We use a static Risk Free Rate (4%) to prevent download errors with ^IRX
FIXED_RISK_FREE_RATE = 0.04 
BROKER_SPREAD = 0.005 / 252   
DAILY_EXPENSE = 0.011 / 252   

# ==========================================
# 2. ROBUST DATA ENGINE
# ==========================================
def fetch_hybrid_data():
    print("1. Downloading Data Separately (Safer for Automation)...")
    
    # Helper to download a single ticker safely
    def get_clean_series(ticker):
        try:
            print(f"   Fetching {ticker}...")
            # Download single ticker (returns simple DataFrame, no MultiIndex confusion)
            d = yf.download(ticker, start='2000-01-01', progress=False, auto_adjust=False)
            
            # Handle cases where yfinance returns a MultiIndex columns
            if isinstance(d.columns, pd.MultiIndex):
                d.columns = d.columns.get_level_values(0)
            
            # Prefer 'Adj Close', fallback to 'Close'
            if 'Adj Close' in d.columns:
                return d['Adj Close']
            elif 'Close' in d.columns:
                return d['Close']
            else:
                return d.iloc[:, 0] # Grab first column if all else fails
        except Exception as e:
            print(f"   Error fetching {ticker}: {e}")
            return pd.Series(dtype=float)

    # 1. Fetch Series Individually
    spy_price = get_clean_series(BENCHMARK_TICKER)
    sso_price = get_clean_series('SSO')
    upro_price = get_clean_series('UPRO')
    
    # Drop rows where SPY is missing (Market holidays etc)
    spy_price = spy_price.dropna()

    # Build Core DataFrame
    df = pd.DataFrame(index=spy_price.index)
    df['Price'] = spy_price
    
    # Use fixed risk free rate (safer than downloading ^IRX)
    df['RiskFree_Rate'] = FIXED_RISK_FREE_RATE / 252
    
    # 2. Calculate Signals on BENCHMARK (SPY)
    df['SMA'] = df['Price'].rolling(window=SMA_PERIOD).mean()
    df['Signal'] = np.where(df['Price'].shift(1) > df['SMA'].shift(1), 1, 0)
    
    # 3. Calculate Returns
    df['SPY_Ret'] = df['Price'].pct_change().fillna(0)
    df['SSO_Ret_Real'] = sso_price.pct_change()
    df['UPRO_Ret_Real'] = upro_price.pct_change()
    
    # 4. Generate Synthetic Data (For Backfill)
    cost_of_money = df['RiskFree_Rate'] + BROKER_SPREAD
    df['SSO_Ret_Synth'] = (df['SPY_Ret'] * 2) - (cost_of_money * 1) - DAILY_EXPENSE
    df['UPRO_Ret_Synth'] = (df['SPY_Ret'] * 3) - (cost_of_money * 2) - DAILY_EXPENSE
    
    # 5. SPLICE: Real combined with Synthetic
    df['SSO_Final'] = df['SSO_Ret_Real'].combine_first(df['SSO_Ret_Synth'])
    df['UPRO_Final'] = df['UPRO_Ret_Real'].combine_first(df['UPRO_Ret_Synth'])
    
    # 6. Apply Strategy
    df['Strat_2x'] = np.where(df['Signal'] == 1, df['SSO_Final'], df['RiskFree_Rate'])
    df['Strat_3x'] = np.where(df['Signal'] == 1, df['UPRO_Final'], df['RiskFree_Rate'])
    
    # Equity Curves
    df['Eq_2x'] = START_CAPITAL * (1 + df['Strat_2x']).cumprod()
    df['Eq_3x'] = START_CAPITAL * (1 + df['Strat_3x']).cumprod()
    df['Eq_Bench'] = START_CAPITAL * (1 + df['SPY_Ret']).cumprod()
    
    # Clean up any remaining NaNs at the start
    df = df.dropna(subset=['Eq_2x', 'Eq_3x'])
    
    return df

# ==========================================
# 3. GENERATE APP
# ==========================================
try:
    df = fetch_hybrid_data()
    # Check if data is valid
    if df.empty or np.isnan(df['Price'].iloc[-1]):
        print("CRITICAL ERROR: Data fetch resulted in empty or NaN values.")
        exit(1) # Fail the build so we know
except Exception as e:
    print(f"Error: {e}")
    exit(1)

# Data for Chart
lookback = 750 
mini_df = df.iloc[-lookback:].copy()

# Metrics
current_price = df['Price'].iloc[-1]
current_sma = df['SMA'].iloc[-1]
current_date = df.index[-1].strftime('%b %d, %Y')
distance_pct = ((current_price / current_sma) - 1) * 100
is_bullish = current_price > current_sma

signal_text = "BUY / HOLD" if is_bullish else "SELL / CASH"
color_green = "#00FF9D"
color_red = "#FF0055"
signal_color = color_green if is_bullish else color_red
status_msg = f"Price is {abs(distance_pct):.1f}% {'ABOVE' if is_bullish else 'BELOW'} the {SMA_PERIOD}-Day SMA."

# YTD Calculation
current_year = df.index[-1].year
last_year = current_year - 1
last_year_data = df[df.index.year == last_year]

if not last_year_data.empty:
    base_idx = last_year_data.index[-1]
    ytd_2x = ((df['Eq_2x'].iloc[-1] / df.loc[base_idx, 'Eq_2x']) - 1) * 100
    ytd_3x = ((df['Eq_3x'].iloc[-1] / df.loc[base_idx, 'Eq_3x']) - 1) * 100
    ytd_bench = ((df['Eq_Bench'].iloc[-1] / df.loc[base_idx, 'Eq_Bench']) - 1) * 100
else:
    ytd_2x = ytd_3x = ytd_bench = 0.0

# JSON
chart_data = {
    "dates": mini_df.index.strftime('%Y-%m-%d').tolist(),
    "price": np.round(mini_df['Price'].values, 2).tolist(),
    "sma": np.round(mini_df['SMA'].values, 2).tolist(),
}
json_str = json.dumps(chart_data)

# HTML Template
html_template = """
<!DOCTYPE html>
<html>
<head>
    <title>Chaser Hybrid</title>
    <meta name="viewport" content="width=device-width, initial-scale=1.0, maximum-scale=1.0, user-scalable=no">
    <meta name="apple-mobile-web-app-capable" content="yes">
    <script src="https://cdn.plot.ly/plotly-2.24.1.min.js"></script>
    <link href="https://fonts.googleapis.com/css2?family=Roboto+Mono:wght@400;500;700&display=swap" rel="stylesheet">
    <style>
        :root {
            --bg: #050505; --card: #0a0a0a; --border: #333333; --text: #e0e0e0;
            --green: #00FF9D; --red: #FF0055; --accent: __ACCENT_COLOR__; 
        }
        body { background-color: var(--bg); color: var(--text); font-family: 'Roboto Mono', monospace; margin: 0; padding: 15px; }
        
        .signal-card {
            background: var(--card); border-radius: 8px; padding: 25px 15px; text-align: center;
            border: 1px solid var(--border); box-shadow: 0 0 20px rgba(0,0,0,0.5); margin-bottom: 20px;
            position: relative; overflow: hidden;
        }
        .signal-card::before {
            content: ''; position: absolute; top: 0; left: 0; right: 0; height: 3px;
            background: var(--accent); box-shadow: 0 0 10px var(--accent);
        }
        .sig-val { font-size: 36px; font-weight: 700; color: var(--accent); margin: 0; line-height: 1.1; }
        .sig-msg { color: #ccc; font-size: 12px; margin-top: 10px; }
        .sig-date { color: #444; font-size: 10px; margin-top: 20px; text-transform: uppercase; }

        .chart-container {
            background: var(--card); border-radius: 8px; border: 1px solid var(--border);
            height: 300px; overflow: hidden; margin-bottom: 20px;
        }
        
        .section-title { font-size: 12px; color: #888; margin-bottom: 8px; text-transform: uppercase; letter-spacing: 1px; border-bottom: 1px solid #222; padding-bottom: 5px; }
        .stats-grid { display: grid; grid-template-columns: 1fr 1fr; gap: 10px; margin-bottom: 20px; }
        .stat-box { background: var(--card); padding: 10px; border-radius: 6px; border: 1px solid var(--border); text-align: center; }
        .stat-label { color: #666; font-size: 9px; text-transform: uppercase; margin-bottom: 4px; font-weight:bold; }
        .stat-num { font-size: 18px; font-weight: 700; color: #fff; }
        .val-green { color: var(--green); }
        .val-red { color: var(--red); }
    </style>
</head>
<body>

    <div class="signal-card">
        <div style="font-size:10px; color:#666; letter-spacing:1px; margin-bottom:5px;">CURRENT SIGNAL</div>
        <div class="sig-val">__SIGNAL_TEXT__</div>
        <div class="sig-msg">__STATUS_MSG__</div>
        <div class="sig-date">Data: __CURRENT_DATE__</div>
    </div>

    <div class="section-title">Trend (__SMA_PERIOD__ SMA)</div>
    <div id="chart" class="chart-container"></div>

    <div class="section-title">YTD Performance</div>
    <div class="stats-grid">
        <div class="stat-box">
            <div class="stat-label">2x (SSO)</div>
            <div class="stat-num __C2X__">__YTD2X__%</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">3x (UPRO)</div>
            <div class="stat-num __C3X__">__YTD3X__%</div>
        </div>
    </div>
    
    <div class="section-title">Market Context</div>
    <div class="stats-grid">
        <div class="stat-box">
            <div class="stat-label">Benchmark YTD</div>
            <div class="stat-num __CBENCH__">__YTDBENCH__%</div>
        </div>
        <div class="stat-box">
            <div class="stat-label">Last Price</div>
            <div class="stat-num">__PRICE__</div>
        </div>
    </div>

    <script>
        var data = __DATA_JSON__;
        var trace1 = { x: data.dates, y: data.price, name: 'Price', line: {color: '#666', width: 1.5} };
        var trace2 = { x: data.dates, y: data.sma, name: 'SMA', line: {color: '__ACCENT_COLOR__', width: 2} };
        var layout = {
            paper_bgcolor: "#0a0a0a", plot_bgcolor: "#0a0a0a",
            margin: { l: 30, r: 10, t: 30, b: 30 }, showlegend: false,
            xaxis: { gridcolor: '#222', showgrid: false },
            yaxis: { gridcolor: '#222', showgrid: true },
            dragmode: false
        };
        Plotly.newPlot('chart', [trace1, trace2], layout, { displayModeBar: false, responsive: true });
    </script>
</body>
</html>
"""

# Injection
html_final = html_template.replace("__DATA_JSON__", json_str)
html_final = html_final.replace("__ACCENT_COLOR__", signal_color)
html_final = html_final.replace("__SIGNAL_TEXT__", signal_text)
html_final = html_final.replace("__STATUS_MSG__", status_msg)
html_final = html_final.replace("__CURRENT_DATE__", current_date)
html_final = html_final.replace("__SMA_PERIOD__", str(SMA_PERIOD))
html_final = html_final.replace("__PRICE__", f"{current_price:.2f}")

# YTD
html_final = html_final.replace("__YTD2X__", f"{ytd_2x:+.1f}").replace("__C2X__", "val-green" if ytd_2x >= 0 else "val-red")
html_final = html_final.replace("__YTD3X__", f"{ytd_3x:+.1f}").replace("__C3X__", "val-green" if ytd_3x >= 0 else "val-red")
html_final = html_final.replace("__YTDBENCH__", f"{ytd_bench:+.1f}").replace("__CBENCH__", "val-green" if ytd_bench >= 0 else "val-red")

output_file = "index.html"
with open(output_file, "w", encoding="utf-8") as f:
    f.write(html_final)

print(f"\nSUCCESS! App generated: {output_file}")
# Note: webbrowser.open is REMOVED because servers don't have screens.
