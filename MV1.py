import yfinance as yf
import pandas as pd
import numpy as np
import json
import os
import webbrowser
from datetime import timedelta

# ==========================================
# 1. CONFIGURATION
# ==========================================
print("--- MOBILE CHASER V5: HYBRID ACCURACY ---")
print("(Using Real SSO/UPRO Data + Synthetic Backfill)")

BENCHMARK_TICKER = 'SPY' 
SMA_PERIOD = 300          
START_CAPITAL = 10000

# Constants for Synthetic Backfill (Pre-2009)
BROKER_SPREAD = 0.005 / 252   
DAILY_EXPENSE = 0.011 / 252   

# ==========================================
# 2. HYBRID DATA ENGINE
# ==========================================
def fetch_hybrid_data():
    print("1. Downloading Data (SPY, SSO, UPRO, IRX)...")
    
    # We download EVERYTHING now
    tickers = [BENCHMARK_TICKER, 'SSO', 'UPRO', '^IRX']
    raw_df = yf.download(tickers, start='2000-01-01', progress=False, group_by='ticker', auto_adjust=False)
    
    # Helper to safely grab 'Adj Close'
    def get_adj_close(ticker):
        try:
            if isinstance(raw_df.columns, pd.MultiIndex):
                return raw_df[ticker]['Adj Close']
            else:
                # If single level (unlikely with multiple tickers, but safe)
                col = f"{ticker}" if ticker in raw_df.columns else f"Adj Close"
                return raw_df[col]
        except KeyError:
            print(f"Warning: Could not find data for {ticker}")
            return pd.Series(np.nan, index=raw_df.index)

    # Extract Series
    spy_price = get_adj_close(BENCHMARK_TICKER)
    sso_price = get_adj_close('SSO')
    upro_price = get_adj_close('UPRO')
    irx_price = get_adj_close('^IRX')

    # Build Core DataFrame
    df = pd.DataFrame(index=spy_price.index)
    df['Price'] = spy_price
    df['RiskFree_Rate'] = (irx_price / 100) / 252
    df['RiskFree_Rate'] = df['RiskFree_Rate'].fillna(0.02/252)
    
    # 1. Calculate Signals on BENCHMARK (SPY)
    df['SMA'] = df['Price'].rolling(window=SMA_PERIOD).mean()
    df['Signal'] = np.where(df['Price'].shift(1) > df['SMA'].shift(1), 1, 0)
    
    # 2. Calculate Returns
    df['SPY_Ret'] = df['Price'].pct_change().fillna(0)
    df['SSO_Ret_Real'] = sso_price.pct_change()
    df['UPRO_Ret_Real'] = upro_price.pct_change()
    
    # 3. Generate Synthetic Data (For Backfill)
    # Cost to borrow for synthetic calc
    cost_of_money = df['RiskFree_Rate'] + BROKER_SPREAD
    
    # Synthetic 2x: (SPY * 2) - Cost
    df['SSO_Ret_Synth'] = (df['SPY_Ret'] * 2) - (cost_of_money * 1) - DAILY_EXPENSE
    
    # Synthetic 3x: (SPY * 3) - Cost
    df['UPRO_Ret_Synth'] = (df['SPY_Ret'] * 3) - (cost_of_money * 2) - DAILY_EXPENSE
    
    # 4. SPLICE: Real combined with Synthetic
    # "Use Real if available, otherwise use Synthetic"
    df['SSO_Final'] = df['SSO_Ret_Real'].combine_first(df['SSO_Ret_Synth'])
    df['UPRO_Final'] = df['UPRO_Ret_Real'].combine_first(df['UPRO_Ret_Synth'])
    
    # 5. Apply Strategy
    # If Signal is 1 (Bull): Get the FINAL Leveraged Return (Real or Synth)
    # If Signal is 0 (Bear): Get Risk Free Rate
    df['Strat_2x'] = np.where(df['Signal'] == 1, df['SSO_Final'], df['RiskFree_Rate'])
    df['Strat_3x'] = np.where(df['Signal'] == 1, df['UPRO_Final'], df['RiskFree_Rate'])
    
    # Equity Curves
    df['Eq_2x'] = START_CAPITAL * (1 + df['Strat_2x']).cumprod()
    df['Eq_3x'] = START_CAPITAL * (1 + df['Strat_3x']).cumprod()
    df['Eq_Bench'] = START_CAPITAL * (1 + df['SPY_Ret']).cumprod()
    
    # Clean up start
    df = df.dropna(subset=['Eq_2x', 'Eq_3x'])
    
    return df

# ==========================================
# 3. GENERATE APP
# ==========================================
try:
    df = fetch_hybrid_data()
except Exception as e:
    print(f"Error: {e}")
    exit()

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

# YTD Calculation (Prior Year Close Method)
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

print(f"\nSUCCESS! App generated: {os.path.abspath(output_file)}")