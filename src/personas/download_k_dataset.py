import os
from dotenv import load_dotenv
import pandas as pd

load_dotenv()

#check per None 
username = os.getenv('KAGGLE_USERNAME')
if username is not None:
    os.environ['KAGGLE_USERNAME'] = username

key = os.getenv('KAGGLE_API_TOKEN')
if key is not None:
    os.environ['KAGGLE_KEY'] = key

import kagglehub

path = kagglehub.dataset_download("anthonytherrien/website-traffic")

csv_file = [f for f in os.listdir(path) if f.endswith('.csv')][0]

df = pd.read_csv(os.path.join(path, csv_file))

print(f"Shape: {df.shape}")
print("Columns:", df.columns.tolist())
print(df.head(3))

df.to_json("data/K_dataset.json", orient='records')
print("Salvato: data/K_dataset.json")
