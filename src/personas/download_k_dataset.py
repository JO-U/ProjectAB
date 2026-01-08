import os
from dotenv import load_dotenv
import pandas as pd
load_dotenv()
os.environ['KAGGLE_USERNAME'] = os.getenv('KAGGLE_USERNAME')
os.environ['KAGGLE_KEY'] = os.getenv('KAGGLE_API_TOKEN')

import kagglehub
path = kagglehub.dataset_download("anthonytherrien/website-traffic")
csv_file = [f for f in os.listdir(path) if f.endswith('.csv')][0]
df = pd.read_csv(os.path.join(path, csv_file))

print(f"Shape: {df.shape}")
print("Columns:", df.columns.tolist())
print(df.head(3))

# Salva progetto-local
df.to_json("data/K_dataset.json", orient='records')
print("Salvato: data/K_dataset.json")