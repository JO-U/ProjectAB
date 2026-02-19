import os
import json
import csv

LOGS_DIR = os.path.join('data', 'test', 'logs')
OUTPUT_CSV = os.path.join('analysis', 'logs_ai.csv')

def get_log_files(logs_dir):
    return [os.path.join(logs_dir, f) for f in os.listdir(logs_dir) if f.endswith('.json')]

def extract_task_data(log_file):
    with open(log_file, encoding='utf-8') as f:
        data = json.load(f)
    session_id = data.get('session_id', os.path.basename(log_file))
    prototipo = data.get('prototype', None)
    if not prototipo:
        import re
        match = re.search(r'prototype_([AB])', session_id)
        prototipo = match.group(1) if match else ''
    tasks_completed = data.get('tasks_completed', {})
    results = []
    for task_key, task_info in tasks_completed.items():
        action_types = task_info.get('action_types', {})
        clicks = action_types.get('click', 0) + action_types.get('back', 0)
        scrolls = action_types.get('scroll', 0)
        azioni = clicks + scrolls
        completed = task_info.get('completed', False)
        selected_recipe = task_info.get('selected_recipe', '')
        duration = task_info.get('duration_seconds', None)
        errors = task_info.get('errors', None)
        if isinstance(errors, list):
            errori = len(errors)
        elif isinstance(errors, (int, float)):
            errori = errors
        elif errors is not None:
            try:
                errori = int(errors)
            except Exception:
                errori = 0
        else:
            errori = 0
        results.append({
            'session_id': session_id,
            'prototipo': prototipo,
            'task_key': task_key,
            'task': task_info.get('task', ''),
            'clicks': clicks,
            'scrolls': scrolls,
            'azioni': azioni,
            'completed': completed and bool(selected_recipe),
            'duration_seconds': duration,
            'selected_recipe': selected_recipe,
            'errori': errori
        })
    return results

def main():
    log_files = get_log_files(LOGS_DIR)
    all_results = []
    for log_file in log_files:
        all_results.extend(extract_task_data(log_file))
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['session_id', 'prototipo', 'task_key', 'task', 'clicks', 'scrolls', 'azioni', 'completed', 'duration_seconds', 'selected_recipe', 'errori']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_results:
            writer.writerow(row)
    print(f'Completato. File salvato in {OUTPUT_CSV}')

if __name__ == '__main__':
    main()
