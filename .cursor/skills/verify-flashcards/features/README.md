# Feature map

The primary surface is the hosted web dashboard. Drive these in the order a person meets them. Sign-in is the only feature this skill's helper drives by itself. The others are part of the same map; a sign-in proof does not cover them.

| Feature | File | Reaches it from |
| --- | --- | --- |
| Sign in | [sign-in.md](sign-in.md) | Logged-out dashboard |
| Sign out | [sign-out.md](sign-out.md) | Signed-in dashboard |
| Connect NotebookLM | [connect-notebooklm.md](connect-notebooklm.md) | Signed-in dashboard, Companion on port 8766 |
| Generate flashcards | [generate-flashcards.md](generate-flashcards.md) | Connected NotebookLM |
| Download CSV | [download-csv.md](download-csv.md) | A finished generation |

Harness for every feature: Playwright against the verification server from `../SKILL.md`.
