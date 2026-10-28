# Public deployment: Google login and Creator mode

This document describes the security setup required before publishing LearnWave. The development app can run without Google sign-in, but a public deployment must enable it.

## What the app protects

- Gemini API keys and Google OAuth client secrets stay in the host's private Secrets manager.
- Backend and frontend Python source runs on the server and is not shown in the LearnWave interface.
- Only an email listed in the server-side creator allowlist receives the Premium tag and Creator Console.
- Student activity currently lives only in the current browser session. It is not saved to a shared database.
- Uploaded notes are limited to 1 MB and are sent to Gemini only after the student presses Generate.

Creator mode deliberately does not provide a browser code editor or a way to view or replace API keys. Use VS Code to edit source, then deploy the update through your hosting provider.

## 1. Install the authentication dependency

Install the updated project requirements before testing Google login:

    pip install -r requirements.txt

The project uses Streamlit's built-in OIDC support and needs the Authlib package supplied by streamlit[auth].

## 2. Create Google OAuth credentials

1. Open Google Cloud Console and select or create a project.
2. Open Google Auth Platform, then complete Branding for the consent screen.
3. Open Audience and add your own Google email as a test user while the app is still in Testing.
4. Open Clients and create a Web application client.
5. Add this local redirect URI:

       http://localhost:8501/oauth2callback

6. After deployment, add the exact public redirect URI:

       https://your-public-domain/oauth2callback

7. Copy the Client ID and Client secret into a password manager. Do not put them in source code, a screenshot, or chat.

Google handles the email and password screen. LearnWave should never contain fields that ask a user to enter a Google password.

## 3. Configure local development

Copy .streamlit/secrets.toml.example to .streamlit/secrets.toml. Fill in the Gemini key, Client ID, Client secret, and a long random cookie secret. Set app.require_login to true after all Google fields are complete. Set app.creator_emails to the Google email address that should receive Creator mode.

The real secrets file is ignored by Git. Check that it stays ignored before creating a repository or pushing code.

## 4. Configure the public host

In the hosting provider's private Secrets or Environment Variables page, add the same values. Change auth.redirect_uri to the exact public HTTPS URL with /oauth2callback. Set app.require_login to true. Do not create a public page for entering API keys or OAuth credentials.

Keep the repository private if its source is not intended to be public. Give host and repository access only to people who should be able to deploy or view source code.

## 5. Verify roles before launch

1. Sign in with the email listed in app.creator_emails.
2. Confirm that the top-right profile shows the Premium badge and that Creator Console appears.
3. Sign in with a different Google email.
4. Confirm that it can use learning features but cannot open Creator Console or see AI connection controls.
5. Confirm that Settings has a Logout button and login is required after logout.

## 6. Before publishing

- Replace any key that was ever pasted into chat, committed, or exposed in a screenshot.
- Restrict the Gemini key in Google Cloud to the required Generative Language API and your server environment where possible.
- Configure quotas and request limits on the host before inviting many users.
- If you later add a database, use the authenticated Google subject claim, named sub, as the record owner and enforce that ownership on every server-side read and write.

References:

- Streamlit Google authentication tutorial: https://docs.streamlit.io/develop/tutorials/authentication/google
- Streamlit login API: https://docs.streamlit.io/develop/api-reference/user/st.login
