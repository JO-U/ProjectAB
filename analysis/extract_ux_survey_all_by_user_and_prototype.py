import os
import json
import csv

LOGS_DIR = os.path.join('data', 'test', 'logs')
OUTPUT_CSV = os.path.join('analysis', 'ux_survey_all_by_user_and_prototype.csv')

def get_log_files(logs_dir):
    return [os.path.join(logs_dir, f) for f in os.listdir(logs_dir) if f.endswith('.json')]

def extract_ux_survey(log_file):
    with open(log_file, encoding='utf-8') as f:
        data = json.load(f)
    session_id = data.get('session_id', os.path.basename(log_file))
    prototipo = data.get('prototype', '')
    tipo = 'AI' if 'ai' in session_id.lower() else 'umano'
    ux = data.get('prototype_ux_survey', {})
    if not isinstance(ux, dict):
        return None
    feature_satisfaction = ux.get('feature_satisfaction', '')
    ease_of_use = ux.get('ease_of_use', '')
    if feature_satisfaction == '' and ease_of_use == '':
        return None
    return {
        'session_id': session_id,
        'tipo': tipo,
        'prototipo': str(prototipo).upper(),
        'feature_satisfaction': feature_satisfaction,
        'ease_of_use': ease_of_use
    }

def main():
    log_files = get_log_files(LOGS_DIR)
    all_results = []
    for log_file in log_files:
        row = extract_ux_survey(log_file)
        if row:
            all_results.append(row)
    with open(OUTPUT_CSV, 'w', newline='', encoding='utf-8') as csvfile:
        fieldnames = ['session_id', 'tipo', 'prototipo', 'feature_satisfaction', 'ease_of_use']
        writer = csv.DictWriter(csvfile, fieldnames=fieldnames)
        writer.writeheader()
        for row in all_results:
            writer.writerow(row)
    print(f'Completato. File salvato in {OUTPUT_CSV}')

if __name__ == '__main__':
    main()
