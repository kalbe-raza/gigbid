# 3-minute demo video: script

Record at 1440×900, browser zoom 100%, the live link (`scripts/live_demo.sh`, or `NO_TUNNEL=1 scripts/live_demo.sh` for localhost). Log in as the reviewer account
(persona: full-stack developer, synthetic). Keep Phoenix open in a second tab. Speak over the screen; the times are
targets, not limits.

| Time | Screen | What to do | What to say |
|---|---|---|---|
| 0:00-0:15 | Inbox | Show the inbox with fresh GigSim jobs, fit and risk badges | "GigBid watches my saved searches and decides which jobs are worth a bid. Nothing is sent without my click." |
| **Job → approved proposal** | | | |
| 0:15-0:35 | Inbox → job | Open a fresh job and click **Analyse now** (the scout also analyses new jobs on its own) | "Three agents run in parallel: the analyst reads the post, the risk assessor checks for scams and injected instructions, the fit matcher finds evidence in my portfolio." |
| 0:35-0:55 | Job page | Watch the live agent progress (SSE): analyst, risk, fit, decide, pricing, writer, verifier, critic | "The writer may only use cited evidence; the claim verifier removes anything my portfolio cannot prove; the critic sends weak drafts back, at most twice." |
| 0:55-1:20 | **Open proposal studio** | Hover two citations to show the evidence; point at price and Connects cost (dry run); edit one sentence; **Approve & send** | "This is exactly what will be sent and what it costs. I edit one line, approve, and it is submitted once, even if the worker crashes right now." |
| 1:20-1:30 | Phoenix tab | Click the trace link on the run | "Every step, tool call and token is traced." |
| **Scam caught** | | | |
| 1:30-1:50 | Inbox | Open a job marked **skip / scam** (unpaid "test", Telegram contact or upfront fee) | "This one asks me to move to Telegram and send bank details. Patterns caught it before any text was written, so it costs nothing." |
| **Injected instruction ignored** | | | |
| 1:50-2:15 | Job with injection badge | Open the post, scroll to the planted "ignore previous instructions…" line; open the draft | "The post tries to make the agent add a magic word and reveal its prompt. The post reaches the model only as untrusted data, the compliance guard checks the output, and the draft contains neither." |
| **Client reply handled** | | | |
| 2:15-2:45 | Messages | Open a thread where a GigSim client asked for an interview; show the intent, proposed slots from the calendar, the drafted reply; **Approve & send** | "When a client answers, the liaison reads the intent, offers slots from my calendar and drafts a reply under the same approval gate." |
| 2:45-3:00 | Analytics | Show funnel, reply rate by category, the A/B panel | "Analytics shows what works: reply rate by category and the two proposal strategies I am testing. Code, results and the report are on GitHub." |

## Before recording
- Run the scout once (Inbox → **Scan now**) so the inbox has fresh jobs, and analyse one injected job and one scam
  in advance so their screens load instantly.
- To get a client reply quickly, GigSim's admin endpoint `POST /admin/fast_forward?seconds=3600` (with
  `x-admin-token`) delivers due replies; the pipeline sync (every 2 minutes) brings them in, and the Messages page refreshes itself every few seconds.
- Close other tabs and notifications; turn on "show clicks" in the screen recorder.
