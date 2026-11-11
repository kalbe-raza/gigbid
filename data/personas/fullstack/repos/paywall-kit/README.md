# paywall-kit

Subscription billing for a SaaS product. Built 2024. (Synthetic portfolio repository.)

## Architecture
Implemented plans, trials, proration and dunning with webhook-driven state.

## Tech
- Next.js
- TypeScript
- Stripe
- SaaS Development
- Prisma
- PostgreSQL
- Webhooks

## Layout
- `src/index.ts`
- `prisma/schema.prisma`
- `db.py`
- `app/api/stripe/route.ts`
- `payments.php`

## Running
```bash
cp .env.example .env
make run
```
