import sys
import os
sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
import multiprocessing
import asyncio
import os
from dotenv import load_dotenv
load_dotenv()

TASK_1 = os.getenv("TASK_1", "")
TASK_2 = os.getenv("TASK_2", "")
TASK_3 = os.getenv("TASK_3", "")
PROTOTYPE_URL_A = os.getenv("PROTOTYPE_URL_A")
PROTOTYPE_URL_B = os.getenv("PROTOTYPE_URL_B")


def run_test(prototype_name, prototype_url, tasks, persona):
    import sys
    sys.path.append(os.path.join(os.path.dirname(__file__), '..'))
    from ab_testing.singular_test import test_prototype
    #ogni processo ha la sua sessione Playwright
    asyncio.run(test_prototype(persona, tasks, prototype_name, prototype_url))

if __name__ == "__main__":
    from ab_testing.singular_test import load_next_persona
    persona_a = load_next_persona()
    while persona_a is None:
        persona_a = load_next_persona()
    persona_b = load_next_persona()
    while persona_b is None or persona_b.get('persona_id') == persona_a.get('persona_id'):
        persona_b = load_next_persona()
    tasks = [TASK_1, TASK_2, TASK_3]
    tasks = [t for t in tasks if t]

    url_a = PROTOTYPE_URL_A
    url_b = PROTOTYPE_URL_B

    proc_a = multiprocessing.Process(target=run_test, args=("A", url_a, tasks, persona_a))
    proc_b = multiprocessing.Process(target=run_test, args=("B", url_b, tasks, persona_b))

    proc_a.start()
    proc_b.start()

    proc_a.join()
    proc_b.join()
