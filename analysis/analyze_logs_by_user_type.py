import pandas as pd
import os
from collections import Counter

def load_and_label(path, user_type):
    df = pd.read_csv(path)
    df['user_type'] = user_type
    return df

def main():
    # Percorsi
    base_dir = os.path.dirname(__file__)
    ai_path = os.path.join(base_dir, 'logs_ai.csv')
    human_path = os.path.join(base_dir, 'human_logs.csv')

    dfs = []
    if os.path.exists(ai_path):
        dfs.append(load_and_label(ai_path, 'ai'))
    if os.path.exists(human_path):
        dfs.append(load_and_label(human_path, 'umano'))
    if not dfs:
        print('Nessun file di log trovato.')
        return
    df = pd.concat(dfs, ignore_index=True)

    is_ai = df['user_type'] == 'ai'
    is_human = df['user_type'] == 'umano'

    #uniforma la colonna task/task_key
    if 'task_key' not in df.columns and 'task' in df.columns:
        df['task_key'] = df['task']
    elif 'task_key' in df.columns and 'task' not in df.columns:
        pass
    elif 'task_key' not in df.columns and 'task' not in df.columns:
        df['task_key'] = ''

    if 'prototipo' not in df.columns:
        if 'Prototipo' in df.columns:
            df['prototipo'] = df['Prototipo']
        elif 'session_id' in df.columns:
            df['prototipo'] = df['session_id'].str.extract(r'prototype_([AB])', expand=False)
        else:
            df['prototipo'] = ''
    df['prototipo'] = df['prototipo'].astype(str).str.strip().str.upper().replace({'A':'A','B':'B'})


    #Task key: normalizza per AI e UMANI
    if 'task_key' not in df.columns or df['task_key'].isnull().all() or (df['task_key'] == '').all():
        if 'task' in df.columns:
            df['task_key'] = df['task']
        elif 'Task' in df.columns:
            df['task_key'] = df['Task']
        else:
            df['task_key'] = ''
    if is_ai.any():
        df.loc[is_ai, 'task_key'] = df.loc[is_ai, 'task_key'].astype(str).str.strip().str.lower().str.replace(' ', '_')
    if is_human.any():
        df.loc[is_human, 'task_key'] = df.loc[is_human, 'task_key'].astype(str).str.strip().str.lower()
        mask = is_human & ~df['task_key'].str.match(r'task_\d+', na=False)
        df.loc[mask, 'task_key'] = df.loc[mask, 'task_key'].str.extract(r'(\d+)', expand=False).apply(lambda x: f'task_{x}' if pd.notnull(x) else '')


    if 'clicks' not in df.columns:
        df['clicks'] = 0
    if is_human.any():
        if 'tipologia azioni' in df.columns:
            df.loc[is_human, 'clicks'] = df.loc[is_human, 'tipologia azioni'].astype(str).str.lower().apply(
                lambda x: sum([x.count('click'), x.count('back'), x.count('click filtro')])
            )
        else:
            df.loc[is_human, 'clicks'] = 0

    if 'scrolls' not in df.columns:
        df['scrolls'] = 0
    if is_human.any():
        #conta scrolls (umani)
        if 'tipologia azioni' in df.columns:
            df.loc[is_human, 'scrolls'] = df.loc[is_human, 'tipologia azioni'].astype(str).str.lower().str.count('scroll')
        else:
            df.loc[is_human, 'scrolls'] = 0
    if is_ai.any():
        if 'scrolls' in df.columns:
            df.loc[is_ai, 'scrolls'] = pd.to_numeric(df.loc[is_ai, 'scrolls'], errors='coerce').fillna(0).astype(float)
        else:
            df.loc[is_ai, 'scrolls'] = 0

    if is_human.any():
        if 'azioni' in df.columns:
            df.loc[is_human, 'azioni'] = pd.to_numeric(df.loc[is_human, 'azioni'], errors='coerce').fillna(0).astype(float)
        else:
            df.loc[is_human, 'azioni'] = 0
    if is_ai.any():
        if 'azioni' in df.columns:
            df.loc[is_ai, 'azioni'] = pd.to_numeric(df.loc[is_ai, 'azioni'], errors='coerce').fillna(0).astype(float)
        else:
            df.loc[is_ai, 'azioni'] = 0

    df['Tempo_sec'] = 0.0
    if is_ai.any() and 'duration_seconds' in df.columns:
        df.loc[is_ai, 'Tempo_sec'] = pd.to_numeric(df.loc[is_ai, 'duration_seconds'], errors='coerce').fillna(0).astype(float)
    if is_human.any():
        col_tempo = None
        if 'tempo min' in df.columns:
            col_tempo = 'tempo min'
        elif 'tempo' in df.columns:
            col_tempo = 'tempo'
        if col_tempo:
            df.loc[is_human, 'Tempo_sec'] = pd.to_numeric(df.loc[is_human, col_tempo], errors='coerce').fillna(0).astype(float) * 60

    df['success'] = 0
    if is_ai.any() and 'completed' in df.columns:
        df.loc[is_ai, 'success'] = df.loc[is_ai, 'completed'].astype(str).str.lower().map({'true': 1, 'false': 0, '1': 1, '0': 0}).fillna(0).astype(int)
    if is_human.any() and 'successo' in df.columns:
        df.loc[is_human, 'success'] = df.loc[is_human, 'successo'].astype(str).str.lower().map({'si': 1, 'no': 0, 'true': 1, 'false': 0, '1': 1, '0': 0}).fillna(0).astype(int)

    if 'Errori' not in df.columns:
        if 'errori' in df.columns:
            df['Errori'] = df['errori']
        else:
            df['Errori'] = 0
    df['Errori'] = pd.to_numeric(df['Errori'], errors='coerce')

    if 'selected_recipe' not in df.columns:
        df['selected_recipe'] = ''
    if is_ai.any() and 'selected_recipe' in df.columns:
        df.loc[is_ai, 'selected_recipe'] = df.loc[is_ai, 'selected_recipe']
    if is_human.any() and 'outcome' in df.columns:
        df.loc[is_human, 'selected_recipe'] = df.loc[is_human, 'outcome']

    if 'Partecipante' not in df.columns:
        if 'partecipante' in df.columns:
            df['Partecipante'] = df['partecipante']
        else:
            df['Partecipante'] = df['session_id'] if 'session_id' in df.columns else ''

    group_cols = ['user_type', 'prototipo', 'task_key']
    stats = df.groupby(group_cols).agg(
        clicks_mean = ('clicks', 'mean'),
        scrolls_mean = ('scrolls', 'mean'),
        azioni_mean = ('azioni', 'mean'),
        time_mean = ('Tempo_sec', 'mean'),
        errors_mean = ('Errori', 'mean'),
        success_rate = ('success', 'mean'),
        n = ('Partecipante', 'nunique')
    ).reset_index()

    unique_combos = df[group_cols].drop_duplicates()
    stats = pd.merge(unique_combos, stats, on=group_cols, how='left')
    stats = stats[stats['task_key'].notnull() & (stats['task_key'] != '') & (stats['task_key'].str.lower() != 'nan')]

    def top_recipes_counter(x):
        filtered = [r for r in x if pd.notnull(r) and str(r).strip() != '' and str(r).lower() != 'nan']
        return Counter(filtered).most_common(5)
    recipe_rank = (
        df.groupby(['user_type', 'prototipo', 'task_key'])['selected_recipe']
        .apply(top_recipes_counter)
        .reset_index()
        .rename(columns={'selected_recipe': 'top_recipes'})
    )

    print('--- STATISTICHE PER UTENTE, PROTOTIPO, TASK ---')
    print(stats)
    print('\n--- RANKING RICETTE PER TASK ---')
    print(recipe_rank)

    stats_out = os.path.join(base_dir, 'logs_stats_by_user_type.csv')
    recipe_out = os.path.join(base_dir, 'logs_recipe_ranking_by_user_type.csv')
    stats.to_csv(stats_out, index=False)
    recipe_rank.to_csv(recipe_out, index=False)
    print(f'\nFile esportati: {stats_out} e {recipe_out}')

if __name__ == '__main__':
    main()
