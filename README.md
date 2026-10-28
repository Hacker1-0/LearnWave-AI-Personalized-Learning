# LearnWave: AI-Based Personalized Learning Content Generator

A Streamlit application for generating explanations, revision notes, questions and flashcards at a learner-selected level. A recent quiz score can move the recommended level one step simpler or harder. It uses either an existing local Qwen3 model through Ollama or Google's Gemini API; no training is required.

## Project structure

```text
personalized-learning-generator/
├── app.py                         # Simple Streamlit launch entry point
├── frontend/
│   └── app.py                     # Dashboard, pages, forms, and styling
├── backend/
│   ├── learning_engine.py         # Prompt design, difficulty, provider selection, retries
│   └── local_ai.py                # Offline model, streamed text, concurrency limit
├── assets/                        # Dashboard image assets
├── docs/
│   ├── PROJECT_REPORT.md          # Report-ready project documentation
│   ├── PROMPT_AND_SAMPLES.md      # Prompt and three-level sample outputs
│   └── DEPLOYMENT_SECURITY.md     # Google login and public-launch checklist
├── scripts/
│   └── check_gemini_key.py         # Optional terminal key check
├── .streamlit/
│   └── secrets.toml.example       # Safe local key setup example
└── requirements.txt
```

### What to inspect for grading

- **Interface and navigation:** `frontend/app.py`
- **Prompt, provider selection, recovery and score-based difficulty:** `backend/learning_engine.py`
- **Offline AI, streaming, one inference at a time:** `backend/local_ai.py`
- **Problem statement, design, and sample outputs:** `docs/`

## Run on Windows

1. Install Python 3.9 or later.
2. Open PowerShell in this folder and create a virtual environment:

   ```powershell
   py -m venv .venv
   .\.venv\Scripts\Activate.ps1
   pip install -r requirements.txt
   ```

3. Start the app:

   ```powershell
   streamlit run app.py
   ```

4. To connect Gemini locally, open **Settings** in LearnWave, paste your key into the password field, and click **Save & check Gemini key**. That local-only setup stores it in `.streamlit/secrets.toml`, which is ignored by Git. For a public deployment, add `GEMINI_API_KEY` in the hosting provider's server-side Secrets or Environment Variables settings; never put it in source code. The model defaults to `gemini-flash-latest`, so it follows Gemini's current Flash alias.

To check a key privately from the terminal, run `python -m scripts.check_gemini_key`. The key is entered with hidden input and is not saved. A successful check confirms Google recognizes the key, but it does not guarantee content generation access or model availability.

The default AI source is **Automatic (offline AI first)**. It uses the installed local model when available, otherwise Gemini. **Offline AI (no internet)** never calls Gemini. If neither service is ready, the app reports that honestly and preserves the previous answer. Successful answers are cached only in that learner's session (up to 20), with a visible saved-answer label.

## Offline AI and phone demonstrations

One-time offline model setup (downloads official Ollama and Qwen3 4B Instruct onto D:):

```powershell
.\scripts\setup_local_ai.ps1
```

Start the app and warm the model before presenting:

```powershell
.\Start-LearnWave.ps1
```

The first model load can take around 30 seconds. Keep the laptop plugged in. Later requests stream text as it is generated; complete-answer time depends on length and hardware. The model is pre-trained and can generate new answers for new topics, but accuracy still needs review.

For a phone on the same trusted Wi-Fi/hotspot, use `.\Start-LearnWave.ps1 -Phone` and open its Network URL. For a different location/network, see [the remote-demo guide](docs/REMOTE_DEMO.md). The phone renders the interface; inference runs on the laptop/server. A phone at another location needs internet access even when the server uses a local model.

Run tests with `.\.venv\Scripts\python.exe -m unittest discover -s tests -v`. Run real offline checks with `.\.venv\Scripts\python.exe scripts\smoke_offline.py` (no cloud key needed).

## Google login and Creator mode

LearnWave supports secure Google sign-in through Streamlit OIDC. The app never asks for, receives, or stores a Google password. When the deployment is configured, everyone signs in through Google before the dashboard loads.

The verified email addresses listed in the private app setting creator_emails receive the Premium badge and Creator Console. That console is for safe app controls and status only. Code, Gemini keys, Google OAuth secrets, and deployment settings are not editable in a public browser; change them in VS Code and the hosting provider's private dashboard.

Before publishing, use the values in the secrets template as a private guide, set app.require_login to true, add the public app's exact HTTPS redirect URI ending in /oauth2callback in Google Cloud, and put the real values only in the hosting provider's Secrets manager. See docs/DEPLOYMENT_SECURITY.md for the full checklist.

## How personalization works

The student selects a self-assessed starting level. If a score is available, the app adjusts that level by at most one step:

| Latest quiz score | Adjustment |
|---|---|
| Below 50% | Move down one level |
| 50–79% | Keep the self-assessed level |
| 80–100% | Move up one level |

The level changes the model prompt's vocabulary, explanation depth, examples, and question difficulty. Scores are kept in the current Streamlit session and are not written to a database.

## Technology

- Python and Streamlit for the interface
- Google Gen AI Python SDK and Gemini API for text generation
- Ollama with Qwen3 4B Instruct for local text generation without cloud requests
- A transparent rule-based score adjustment; no model training

## References

- [Gemini API getting started guide](https://ai.google.dev/gemini-api/docs/get-started)
- [Gemini content generation API](https://ai.google.dev/api/generate-content)
- [Streamlit documentation](https://docs.streamlit.io/)

## Submission files

- [Project presentation](submission/LearnWave_AI_Project_Presentation.pptx)
- [Demo video](submission/LearnWave_AI_Demo.mp4)
