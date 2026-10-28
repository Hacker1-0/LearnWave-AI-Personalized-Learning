# Project Report: AI-Based Personalized Learning Content Generator

## 1. Abstract

LearnWave is a lightweight learning assistant that creates an explanation, revision notes, flashcards, or practice questions for a student-selected topic. It personalizes the generated response using a self-assessed proficiency level and, when available, a recent quiz score. A simple rule adjusts the level upward after a strong score or downward after a low score. The application uses an existing Gemini generative AI API rather than training a language model. A Streamlit interface makes the prototype easy to demonstrate.

## 2. Problem statement

Most learning materials present the same explanation and exercises to every learner. Students may need a simpler explanation when they are confused, or more challenging material when they have mastered the basics. This project explores a small applied-AI solution that adapts educational content to the learner's current understanding.

## 3. Objectives

1. Accept a topic and a self-assessed level from the student.
2. Use a recent quiz score to adjust difficulty.
3. Generate explanations, revision notes, flashcards, or practice questions.
4. Provide a simple interface for requesting content immediately.
5. Document prompts and show outputs at three proficiency levels.

## 4. Scope and tools

The prototype uses Python, Streamlit, the Google Gen AI SDK, and the Gemini API. The API key is read from server-side secrets or environment settings; it is not entered into the public student interface. It does not train a model, store learner records in a database, or claim to replace a teacher. If an API key is not configured, a clearly labeled rule-based template preview keeps the interface usable for a classroom demo.

## 5. System design

```text
Student topic + self-assessed level + optional quiz score + content type
                               │
                               ▼
                     Difficulty adjustment
                               │
                               ▼
                     Prompt construction
                               │
                   Gemini API (if key set)
                               │
                               ▼
               Personalized content in Streamlit
```

### Inputs

- Topic: free-text topic, up to 120 characters
- Self-assessment: Beginner, Intermediate, or Advanced
- Latest quiz score: optional integer from 0 to 100
- Content type: explanation, revision notes, or practice questions

### Output

Markdown content with vocabulary, detail, examples, and question challenge matched to the recommended level.

## 6. Difficulty adjustment

The self-assessment is the baseline level. The latest quiz score then applies this rule:

```text
score < 50:  move one level simpler
50–79:       keep the self-assessed level
score ≥ 80:  move one level harder
```

The level is capped at Beginner and Advanced. For example, an Intermediate learner scoring 35% moves to Beginner, while an Intermediate learner scoring 90% moves to Advanced. A score of 70% keeps the learner at Intermediate. If no quiz score is supplied, the self-assessment is used unchanged.

This is a transparent heuristic, not a trained student model. A future version could use multiple quiz results, question-level performance, or teacher review.

## 7. Content generation logic

1. Validate the requested content type and level.
2. Build a prompt from the topic, content type, recommended level, and score context.
3. Use the Google Gen AI SDK to send the prompt to the configured Gemini model.
4. Show the generated Markdown response in the app.
5. When no API key is available, show an explicitly labeled local template preview.

The same topic can produce different vocabulary, scaffolding, examples, and challenge depending on the level. For practice questions, the prompt asks the model to order five questions from easier to harder and place the answer key after them.

## 8. Prompt design

The prompt identifies the model's role as a patient tutor, names the learner's level, provides level-specific teaching guidance, requests a content type, and asks for an accurate, focused response. It treats the topic as subject matter rather than instructions. The full reusable prompt is in `PROMPT_AND_SAMPLES.md` and implemented in `../backend/learning_engine.py`.

## 9. User interface

The student uses the sidebar to provide a confidence level and optional score. The main page accepts a topic and content type, then displays the selected difficulty and generated content. The interface also explains the adjustment rule so the student can understand why content was selected.

## 10. Limitations and responsible use

- Generative responses can contain factual errors; students should verify important material with course resources or an instructor.
- A single score is only a rough estimate of understanding and can be affected by quiz difficulty or guessing.
- The offline preview uses templates and is not a substitute for API-generated subject-specific content.
- The prototype does not save scores or provide formal grading.
- API use is subject to the provider's access, rate limits, and terms.

## 11. Public deployment and creator access

For a public deployment, LearnWave uses Google OpenID Connect sign-in. Google handles the sign-in page; the app does not ask for or store Google passwords. A server-side allowlist identifies the creator account. Only that verified Google account receives the Premium Creator badge and can view the Creator Console.

Gemini keys, Google OAuth client secrets, source code, and deployment configuration remain in private server-side secrets, the private repository, and VS Code. They are never displayed or edited through the student-facing app. The project currently stores student activity only in the active browser session, so it has no shared learner database. The public-launch checklist is in DEPLOYMENT_SECURITY.md.

## 12. Future improvements

- Store opt-in learning history and use trends rather than one score.
- Add a short quiz and automatically score its answer key.
- Let teachers select curriculum and grade-level constraints.
- Add feedback buttons to improve the next content request.
- Evaluate factual quality and difficulty with teacher-reviewed examples.

## 13. Conclusion

LearnWave demonstrates how a generative AI API and a small, explainable adaptation rule can produce learning material at different levels. The prototype is intentionally limited to concepts suitable for an introductory AIML project and leaves model training out of scope.
