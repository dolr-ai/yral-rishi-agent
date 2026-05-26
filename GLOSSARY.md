# Glossary — Plain English for Every Technical Term

## Architecture words
- **API** — the "menu" your app offers. Other apps (like mobile) read the menu and order from it.
- **Endpoint** — one item on the menu. Example: "POST /api/v1/chat/conversations/{id}/messages" is the endpoint for sending a message.
- **Route** — same as endpoint. The path a request takes to reach the right code.
- **Middleware** — a checkpoint every request passes through before reaching your code. Like airport security before your gate.
- **CORS** — a browser safety rule. "Which websites are allowed to call our API?" We say "*" (everyone) because mobile apps don't have this restriction.
- **Monolith** — one service does everything. Opposite of microservices. chat-ai is a monolith. We're keeping it that way.

## Database words
- **Postgres** — the database software. Like a giant spreadsheet that's really fast and never loses data.
- **Patroni** — software that makes Postgres survive crashes. If one copy dies, another takes over automatically.
- **WAL** — Write-Ahead Log. Every change is written to a log BEFORE it's applied. If the server crashes mid-write, the log recovers it.
- **WAL-G** — streams that log to cloud storage (S3) continuously. This is your streaming backup.
- **pg_dump** — takes a complete snapshot of the entire database at one moment. Like photographing every page of a book.
- **Migration** — a script that changes the database structure (adds a table, adds a column). Like remodeling a room in a house.
- **Schema** — the structure/blueprint of your database. What tables exist, what columns they have, what types the columns are.
- **ETL** — Extract, Transform, Load. Copying data from one database to another, changing its shape along the way.
- **asyncpg** — a Python library for talking to Postgres without blocking other requests.
- **Connection pool** — a set of pre-opened database connections. Instead of opening a new one per request (slow), you borrow from the pool.

## Python / FastAPI words
- **FastAPI** — the web framework. Takes HTTP requests and sends them to your code.
- **Pydantic** — a library that validates data shapes. If the mobile app sends wrong data, Pydantic catches it.
- **DTO** — Data Transfer Object. The exact shape of JSON the mobile app sends/receives. Defined in models.py.
- **Router** — a FastAPI object that groups related endpoints together. Like a section of the menu.
- **Lifespan** — code that runs when the app starts (open database connections) and when it stops (close them).
- **async/await** — Python's way of saying "while I wait for the database/AI/network, handle other requests."

## AI / LLM words
- **LLM** — Large Language Model. The AI brain (Gemini, Claude, GPT). Takes text in, generates text out.
- **Gemini** — Google's AI model. Our primary model for chat responses.
- **OpenRouter** — a proxy service that routes to various AI models. We use it for NSFW influencers.
- **TTFT** — Time To First Token. How long until the AI starts responding. Lower = feels faster.
- **SSE** — Server-Sent Events. A way to stream the AI's response word by word instead of waiting for the full reply.
- **System prompt** — instructions given to the AI before the user's message. Defines the bot's personality.
- **Soul File** — YRAL's product term for system prompt. Has 4 layers (global, archetype, per-influencer, per-user-segment).
- **Token** — a chunk of text (roughly 4 characters). AI models charge per token.
- **Langfuse** — software that records every AI call with timing, cost, and content.

## Infrastructure words
- **Docker** — packages your app + dependencies into a "container" that runs the same way everywhere.
- **Swarm** — Docker's built-in tool for running containers across multiple servers with auto-recovery.
- **Caddy** — a web server that handles HTTPS. Routes traffic to the right service.
- **Redis** — an in-memory database. Super fast. Used for caching, real-time messaging.
- **Sentinel** — Redis's failover manager. If the primary Redis dies, Sentinel promotes a replica.
- **Health check** — a URL the server pings every few seconds to ask "are you alive?"
- **Rollback** — reverting to the previous version. One command.

## Security words
- **JWT** — JSON Web Token. A small encrypted pass the mobile app sends with every request to prove who the user is.
- **Bearer token** — the JWT sent in the "Authorization: Bearer xyz123..." header.
- **Presigned URL** — a temporary link to a private S3 file. Expires after 15 minutes.

## Process words
- **PR** — Pull Request. A proposal to merge code changes.
- **CI** — Continuous Integration. Automated tests + builds that run on every PR.
- **Sentry** — error tracking software. When the app crashes, Sentry records it.
- **GHCR** — GitHub Container Registry. Where Docker images are stored after building.

## YRAL-specific words
- **AI Influencer** — an AI personality users chat with on the YRAL app.
- **Soul File** — the AI influencer's personality definition.
- **Chat as Human** — a mode where the influencer's creator takes over and replies as the bot.
- **H2H** — Human to Human chat. Direct messages between two real users, no AI involved.
- **Principal ID** — the user's unique identity from the Internet Computer blockchain (YRAL's auth system).
- **Tara** — a specific AI influencer with a hand-crafted Soul File. The benchmark for quality.
