import pandas as pd
import matplotlib.pyplot as plt

def plot_trade_prices(csv_filepath):
    # 1. Load the dataset (notice the sep=';' since the data is semicolon-separated)
    df = pd.read_csv(csv_filepath, sep=';')
    
    # 2. Identify the unique symbols in the data
    symbols = df['symbol'].dropna().unique()
    
    # 3. Create a figure with subplots (one for each symbol)
    fig, axes = plt.subplots(nrows=len(symbols), ncols=1, figsize=(12, 5 * len(symbols)))
    
    # Handle the case where there is only one symbol (axes is not an array)
    if len(symbols) == 1:
        axes = [axes]
        
    # 4. Plot the data for each symbol
    for i, symbol in enumerate(symbols):
        # Filter data for the current symbol
        subset = df[df['symbol'] == symbol].copy()
        
        # Sort by timestamp just in case the data is out of order
        subset = subset.sort_values('timestamp')
        
        # Plotting
        axes[i].plot(subset['timestamp'], subset['price'], 
                     marker='.', linestyle='-', linewidth=1, markersize=5, 
                     color='tab:green' if symbol == 'EMERALDS' else 'tab:red')
        
        # Formatting the subplot
        axes[i].set_title(f'Price of {symbol} over Time', fontsize=14, fontweight='bold')
        axes[i].set_xlabel('Timestamp', fontsize=12)
        axes[i].set_ylabel('Price', fontsize=12)
        axes[i].grid(True, linestyle='--', alpha=0.7)
        axes[i].legend([symbol])

    # 5. Adjust layout and show the plot
    plt.tight_layout()
    plt.show()

if __name__ == "__main__":
    # Replace with the actual name of your CSV file if it's different
    file_name = 'trades_round_0_day_-1.csv'
    plot_trade_prices(file_name)