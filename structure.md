ProjectAB/                          
├── .gitignore                      # esclude venv/, .env
├── README.md                       
├── requirements.txt                # pip install -r requirements.txt
├── .env                            # API key, NUM_PROFILES...
├── venv/                           
├── cache/                          # Per Figma JSON 
├── data/
│   └── personas_data/              
│       └── personas.json
├── config/
│   ├── llm_providers.py            # ollama/openrouter/gemini
│   └── env.py                      # fetch NUM_PROFILES ecc.
└── src/
    ├── __init__.py                 
    ├── llm_client.py               # client HTTP
    └── personas/
        ├── __init__.py            
        ├── filter_personas.py       # personas
        ├── download_dataset.py      # Da Kaggle 
        └── target.yaml              # Config target
    └── ab_testing/
        ├── __init__.py             
        ├── figma_test.py           # Test Figma 
        ├── live_test.py            # Test live site 
        └── shared.py               # Prompt, parse metrics
