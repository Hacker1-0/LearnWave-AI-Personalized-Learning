# LearnWave remote phone demo

## Same Wi-Fi or hotspot

Run `Start-LearnWave.ps1 -Phone` and open the terminal's Network URL on the phone. The laptop does the AI work. This address is only for devices that can reach that local network.

## Different locations or networks

`Start-Remote-Demo.ps1` starts a separate app server on loopback port 8503 and a temporary Cloudflare Quick Tunnel. The app asks for a private LearnWave passcode before showing the dashboard. Do not use `-AllowedEmail`: Cloudflare's email-protected Quick Tunnel currently expires access before Streamlit's widget scripts load ([upstream issue](https://github.com/cloudflare/cloudflared/issues/1752)).

The Streamlit version is pinned in `requirements.txt` because its browser widgets load versioned JavaScript files. Keep the project's `.venv` on that pinned version. The passcode is generated once and stored only in the git-ignored `.local-ai/remote-demo-passcode.txt`. The server reads it from a process environment variable; it is never sent to Gemini. Share the HTTPS URL and passcode only with your partner. Anyone with both can access the demo.

```powershell
cd "D:\SEM 3\personalized-learning-generator"
.\Start-Remote-Demo.ps1
```

The script prints the HTTPS URL and the LearnWave passcode. It does not require sharing a Google password. Anyone with both the link and passcode can access this temporary demo.

The script downloads the official connector only if necessary and verifies its release checksum. API-key setup controls are disabled for this remote-demo process. Gemini keys stay server-side; local AI uses a private loopback service.

Keep the laptop awake, the terminal open, and the laptop connected to the internet. The phone also needs an internet connection. Ctrl+C closes the tunnel and stops only the app process created by the script. Restarting creates a new URL.

This is a temporary demo, not permanent public hosting. Quick Tunnels have no uptime guarantee. For a permanent app, deploy an authenticated backend to a server that can run the local model or use a cloud AI provider. A normal Streamlit cloud deployment cannot access the Ollama server on your laptop through `localhost`.

## Demonstration flow

1. Open **Generate New Content**.
2. Select **Offline AI (no internet)** to ensure generation does not call Gemini.
3. Enter **Binary search** and choose **Explanation**.
4. In **Settings**, change Starting confidence and optionally the quiz score; return and generate again.
5. Try **Revision notes**, **Practice questions**, or **Flashcards**.

Repeated identical requests can show a labelled saved answer from that browser session. Changing topic, content type, level, score or AI source creates a different request. Different users' session caches are separate. A failed request keeps the previous answer with an explicit notice.

## References

- [Ollama on Windows](https://docs.ollama.com/windows)
- [Qwen3 model](https://ollama.com/library/qwen3:4b-instruct)
- [Cloudflare protected Quick Tunnels](https://developers.cloudflare.com/tunnel/get-started/quick-tunnels/)
- [Gemini retry recommendations](https://ai.google.dev/gemini-api/docs/troubleshooting)
