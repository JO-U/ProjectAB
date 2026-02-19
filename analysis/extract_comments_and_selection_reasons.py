import os
import json
import csv

LOGS_DIR = os.path.join('data', 'test', 'logs')
OUTPUT_CSV = os.path.join('analysis', 'comments_and_selection_reasons.csv')

def get_log_files(logs_dir):
    return [os.path.join(logs_dir, f) for f in os.listdir(logs_dir) if f.endswith('.json')]

def extract_comments_and_reasons(log_file):
    with open(log_file, encoding='utf-8') as f:
        data = json.load(f)
    session_id = data.get('session_id', os.path.basename(log_file))
    results = []
    #find tasks_completed 
    tasks = data.get('tasks_completed', {})
    for task_key, task_info in tasks.items():
        notes = task_info.get('notes', '')
        selection_reason = task_info.get('selection_reason', '')
        results.append({
            'session_id': session_id,
            'task_key': task_key,
            'notes': notes,
            'selection_reason': selection_reason
        })
    return results

def main():
    log_files = get_log_files(LOGS_DIR)
    all_results = []
    for log_file in log_files:
        all_results.extend(extract_comments_and_reasons(log_file))
    #filter (notes and selection_reason not empty)
    filtered = [row for row in all_results if str(row['notes']).strip() or str(row['selection_reason']).strip()]
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['session_id', 'task_key', 'notes', 'selection_reason']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in filtered:
            writer.writerow(row)
    print(f'Completato. File salvato in {OUTPUT_CSV}')

if __name__ == '__main__':
    main()
