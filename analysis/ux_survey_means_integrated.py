import pandas as pd
import os
import json

LOGS_DIR = os.path.join('data', 'test', 'logs')
FORM_CSV = os.path.join('analysis', 'results', 'human_form.csv')
OUT_CSV = os.path.join('analysis', 'results', 'ux_survey_means_integrated.csv')

def extract_ai_surveys(logs_dir):
    records = []
    for fname in os.listdir(logs_dir):
        if not fname.endswith('.json'):
            continue
        fpath = os.path.join(logs_dir, fname)
        with open(fpath, encoding='utf-8') as f:
            data = json.load(f)
        session_id = data.get('session_id', fname)
        prototipo = data.get('prototype', '')
        tipo = 'AI'
        ux = data.get('prototype_ux_survey', {})
        if not isinstance(ux, dict):
            continue
        feature_satisfaction = ux.get('feature_satisfaction', '')
        ease_of_use = ux.get('ease_of_use', '')
        if feature_satisfaction == '' and ease_of_use == '':
            continue
        records.append({
            'tipo': tipo,
            'prototipo': str(prototipo).upper(),
            'feature_satisfaction': pd.to_numeric(feature_satisfaction, errors='coerce'),
            'ease_of_use': pd.to_numeric(ease_of_use, errors='coerce'),
            'fonte': 'log_json'
        })
    return pd.DataFrame(records)

def extract_human_form(form_csv):
    df = pd.read_csv(form_csv, header=0, skiprows=[1])
    df.columns = [c.replace('"','').replace('\n',' ').strip() for c in df.columns]
    # Colonne target
    a_fs = 'A feature satisfaction'
    a_eu = 'A ease of use'
    b_fs = 'B feature satisfaction'
    b_eu = 'B ease of use'
    # Unisci in formato compatibile
    records = []
    for idx, row in df.iterrows():
        # Prototipo A
        records.append({
            'tipo': 'umano',
            'prototipo': 'A',
            'feature_satisfaction': pd.to_numeric(row[a_fs], errors='coerce'),
            'ease_of_use': pd.to_numeric(row[a_eu], errors='coerce'),
            'fonte': 'form_csv'
        })
        # Prototipo B
        records.append({
            'tipo': 'umano',
            'prototipo': 'B',
            'feature_satisfaction': pd.to_numeric(row[b_fs], errors='coerce'),
            'ease_of_use': pd.to_numeric(row[b_eu], errors='coerce'),
            'fonte': 'form_csv'
        })
    return pd.DataFrame(records)

if __name__ == '__main__':
    df_ai = extract_ai_surveys(LOGS_DIR)
    df_human = extract_human_form(FORM_CSV)
    df = pd.concat([df_ai, df_human], ignore_index=True)
    # Calcola le medie per tipo e prototipo
    means = df.groupby(['tipo', 'prototipo', 'fonte'])[['feature_satisfaction', 'ease_of_use']].mean().reset_index()
    means.to_csv(OUT_CSV, index=False)
    print(f'Medie integrate salvate in: {OUT_CSV}')
