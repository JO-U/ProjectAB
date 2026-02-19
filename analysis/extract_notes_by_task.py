import os
import json
import csv

LOGS_DIR = os.path.join('data', 'test', 'logs')
OUTPUT_CSV = os.path.join('analysis', 'notes_by_task.csv')

def get_log_files(logs_dir):
    return [os.path.join(logs_dir, f) for f in os.listdir(logs_dir) if f.endswith('.json')]

def extract_notes(log_file):
    with open(log_file, encoding='utf-8') as f:
        data = json.load(f)
    session_id = data.get('session_id', os.path.basename(log_file))
    results = []
    tasks = data.get('tasks_completed', {})
    for task_key, task_info in tasks.items():
        notes = task_info.get('notes', '')
        results.append({
            'session_id': session_id,
            'task_key': task_key,
            'notes': notes
        })
    # Estrai anche notes da prototype_ux_survey se presente
    ux_survey = data.get('prototype_ux_survey', {})
    if isinstance(ux_survey, dict) and 'notes' in ux_survey and ux_survey['notes']:
        results.append({
            'session_id': session_id,
            'task_key': 'prototype_ux_survey',
            'notes': ux_survey['notes']
        })
    return results

def main():
    log_files = get_log_files(LOGS_DIR)
    all_results = []
    for log_file in log_files:
        all_results.extend(extract_notes(log_file))
    # Filtra solo note non vuote o non solo spazi
    filtered = [row for row in all_results if str(row['notes']).strip()]
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['session_id', 'task_key', 'notes']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in filtered:
            writer.writerow(row)
    print(f'Completato. File salvato in {OUTPUT_CSV}')

if __name__ == '__main__':
    main()
