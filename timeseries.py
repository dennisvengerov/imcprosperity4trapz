import pandas as pd
from statsmodels.tsa.arima.model import ARIMA
import warnings

# Suppress harmless statsmodels warnings
warnings.filterwarnings("ignore")

def train_tomato_arima():
    print("Loading data...")
    # 1. Load both days of data
    df_day_minus_2 = pd.read_csv('trades_round_0_day_-2.csv', sep=';')
    df_day_minus_1 = pd.read_csv('trades_round_0_day_-1.csv', sep=';')
    
    # Combine them into one sequence
    df = pd.concat([df_day_minus_2, df_day_minus_1])
    
    # Filter only for TOMATOES
    df = df[df['symbol'] == 'TOMATOES']
    
    # 2. Clean the data
    # There can be multiple trades at the exact same timestamp. 
    # We group by timestamp and take the mean price to get a clean time series.
    df = df.groupby('timestamp')['price'].mean().reset_index()
    df = df.sort_values('timestamp')
    
    prices = df['price'].values
    
    print(f"Training ARIMA(3,0,0) on {len(prices)} ticks. This may take a few seconds...")
    
    # 3. Fit the ARIMA model
    # order=(p, d, q) -> p=3 (Look back 3 steps), d=0 (No differencing), q=0 (No moving average of errors)
    # This forms a standard Auto-Regressive AR(3) model.
    model = ARIMA(prices, order=(3, 0, 0))
    results = model.fit()
    
    # 4. Extract and print the coefficients
    print("\n==================================================")
    print("                MODEL SUMMARY                     ")
    print("==================================================")
    print(results.summary())
    
    const = results.params[0]
    ar1 = results.params[1]
    ar2 = results.params[2]
    ar3 = results.params[3]
    
    print("\n==================================================")
    print("   COPY & PASTE THESE INTO YOUR TRADER CLASS!     ")
    print("==================================================")
    print(f"CONST = {const:.4f}")
    print(f"AR1   = {ar1:.4f}")
    print(f"AR2   = {ar2:.4f}")
    print(f"AR3   = {ar3:.4f}")
    print("==================================================")

if __name__ == "__main__":
    train_tomato_arima()