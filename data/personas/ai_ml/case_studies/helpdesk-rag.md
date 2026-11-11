---
project: helpdesk-rag
title: Retrieval-augmented support assistant
client_industry: B2B SaaS
date: 2024-04-15
skills: [LangChain, OpenAI API, Retrieval-Augmented Generation, Python, FastAPI]
synthetic: true
---

# Retrieval-augmented support assistant (2024)

- **Challenge:** support agents spent hours searching a 3,000-page knowledge base.
- **Solution:** chunked the docs by heading, embedded them into a vector index, cached frequent answers and forced the assistant to cite the article it used.
- **Result:** 38% of tier-1 tickets were resolved without a human.
- **Client sector:** B2B SaaS.

Code: see the `helpdesk-rag` repository.
