import pandas as pd
import numpy as np
import matplotlib.pyplot as plt
import glob
import os

def load_clean_data():
    path = os.path.join(os.path.dirname(__file__), "ROUND_2", "prices_round_2_day_*.csv")
    all_files = glob.glob(path)
    
    if not all_files:
        print(f"No price files found at {path}")
        return pd.DataFrame()
        
    df_list = []
    for filename in all_files:
        df_list.append(pd.read_csv(filename, sep=";"))
        
    full_df = pd.concat(df_list, ignore_index=True)
    full_df = full_df.sort_values(by=["day", "timestamp"]).reset_index(drop=True)
    
    price_cols = [c for c in full_df.columns if 'price' in c]
    for col in price_cols:
        full_df[col] = full_df[col].replace(0.0, np.nan)
        full_df[col] = full_df.groupby('product')[col].ffill().bfill()
        
    full_df['spread'] = full_df['ask_price_1'] - full_df['bid_price_1']
    full_df['volume_imbalance'] = full_df['bid_volume_1'].fillna(0) - full_df['ask_volume_1'].fillna(0)
    
    return full_df

def visualize_data():
    print("Loading data...")
    df = load_clean_data()
    if df.empty:
        print("Dataframe is empty.")
        return
        
    products = df['product'].unique()
    n_products = len(products)
    print(f"Found products: {products}")
    
    fig, axes = plt.subplots(3, n_products, figsize=(8 * n_products, 12), squeeze=False)
    fig.suptitle('IMC Prosperity - Round 2 Backtest Visualizations', fontsize=18, y=0.98)
    
    for i, product in enumerate(products):
        print(f"Processing visualizations for {product}...")
        prod_df = df[df['product'] == product].copy()
        prod_df['continuous_time'] = range(len(prod_df))
        
        # Calculate Moving Averages for scale and trend check
        prod_df['SMA_50'] = prod_df['mid_price'].rolling(window=50, min_periods=1).mean()
        prod_df['SMA_200'] = prod_df['mid_price'].rolling(window=200, min_periods=1).mean()
        
        # Calculate Cumulative Imbalance
        prod_df['cum_imbalance'] = prod_df['volume_imbalance'].cumsum()
        
        # --- 1. Price with Moving Averages ---
        ax1 = axes[0, i]
        ax1.plot(prod_df['continuous_time'], prod_df['mid_price'], color='lightgray', alpha=0.8, label='Mid Price')
        ax1.plot(prod_df['continuous_time'], prod_df['SMA_50'], color='blue', linewidth=1, label='Fast SMA (50)')
        ax1.plot(prod_df['continuous_time'], prod_df['SMA_200'], color='red', linewidth=2, label='Slow SMA (200)')
        ax1.set_title(f'{product} - Trend & Scale', fontweight='bold')
        ax1.legend()
        ax1.grid(True, alpha=0.3)
        
        # --- 2. Spread Distribution (Histogram) ---
        ax2 = axes[1, i]
        spreads = prod_df['spread'].dropna()
        # Handle cases with weird or wide spread values dynamically, ensuring positive bins
        min_spread = max(0, spreads.min())
        max_spread = spreads.max()
        bins = np.arange(min_spread, max_spread + 2, 1) - 0.5 if not spreads.empty else 10
        ax2.hist(spreads, bins=bins, color='purple', alpha=0.7, edgecolor='black')
        ax2.set_title(f'{product} - Spread Size Distribution', fontweight='bold')
        ax2.set_xlabel('Spread Size (Ask - Bid)')
        ax2.set_ylabel('Frequency (Ticks)')
        ax2.grid(True, alpha=0.3)
        
        # --- 3. Cumulative Order Book Pressure ---
        ax3 = axes[2, i]
        ax3.plot(prod_df['continuous_time'], prod_df['cum_imbalance'], color='teal', linewidth=2)
        ax3.axhline(0, color='black', linestyle='--', alpha=0.5)
        ax3.set_title(f'{product} - Cumulative Volume Imbalance', fontweight='bold')
        ax3.set_xlabel('Continuous Time Steps')
        ax3.set_ylabel('Net Buying/Selling Pressure')
        ax3.grid(True, alpha=0.3)

    plt.tight_layout(rect=[0, 0.03, 1, 0.95])
    
    output_path = os.path.join(os.path.dirname(__file__), "round2_visualizations.png")
    plt.savefig(output_path, dpi=300)
    print(f"Visualizations saved successfully to {output_path}")

if __name__ == "__main__":
    visualize_data()
