# V1 Feature: Wallet Analysis Payment & Reporting System

## Overview
Build a complete flow where users pay $25 in USDC/USDT via Solana Pay to receive comprehensive crypto tax reports for a wallet address.

**Key Principles:**
- Start simple, iterate quickly
- We work together - collaborative implementation
- Test as we go
- Build in phases that can be tested independently

---

## Phase 1: Core Infrastructure Setup
**Goal:** Get async task processing working

- [ ] Install dependencies (Django Q2, Solana libraries, Dune client)
- [ ] Configure Django Q2 (uses SQLite as queue, no Redis needed)
- [ ] Set up `.env` file for secrets management
- [ ] Test that Django Q2 can execute a simple async task

**Note:** Django Q2 is a lightweight task queue that uses your existing database (SQLite) as the queue backend. Perfect for simple deployments on small VPS servers. No Redis, no extra services.

---

## Phase 2: Database Models
**Goal:** Define data structure

Create Django app `wallet_analysis` with models:

### WalletAnalysisOrder
- user (FK to User)
- wallet_address (EVM address to analyze)
- status (pending_payment, payment_received, processing, completed, partial_complete, failed)
- payment_amount_usd (Decimal, default 25)
- created_at, updated_at

**Status Flow:**
- `pending_payment` → user created order, waiting for payment
- `payment_received` → payment confirmed on blockchain
- `processing` → Dune queries running
- `completed` → all queries successful, all reports ready
- `partial_complete` → some queries succeeded, some failed (needs review)
- `failed` → critical failure (payment issue, all queries failed, etc.)

### SolanaPayment
- order (FK to WalletAnalysisOrder)
- payment_url (Solana Pay URL)
- reference (unique UUID for tracking)
- recipient_address (your Solana wallet)
- amount_expected (in USDC/USDT lamports)
- token_mint (USDC or USDT mint address)
- transaction_signature (once paid)
- status (pending, confirmed, finalized, failed)
- created_at, confirmed_at

### DuneQueryJob
- order (FK to WalletAnalysisOrder)
- query_name (e.g., "defi_trades", "lp_events", "transfers")
- dune_query_id (your Dune query ID)
- dune_execution_id (from Dune API)
- status (queued, running, completed, failed, failed_needs_review)
- error_message
- error_type (query_error, network_error, rate_limit, service_outage, auth_error)
- retry_count (default 0, track manual retry attempts)
- started_at, completed_at

### ReportFile
- order (FK to WalletAnalysisOrder)
- file_name
- file_path (relative to MEDIA_ROOT)
- file_type (e.g., "defi_trades")
- file_size
- created_at

---

## Phase 3: Solana Pay Integration
**Goal:** Accept crypto payments

### Payment Generation
- Create view to generate Solana Pay transfer request
- Generate unique reference (UUID) for each payment
- Support USDC and USDT SPL tokens
- Return payment URL and QR code data

### Payment Monitoring
- Django Q2 scheduled task (runs every 30 seconds) OR cron job running management command
- Check Solana blockchain for transactions matching reference
- Verify: correct amount, correct token, correct recipient
- Update payment status: pending → confirmed → finalized
- Trigger Dune queries once finalized

**Implementation Options:**
1. **Django Q2 Schedule** - Define a scheduled task that checks pending payments
2. **Cron Job** - Run `python manage.py check_pending_payments` every 30 seconds via systemd timer or cron

### Key Resources
- Solana Pay Spec: https://github.com/solana-labs/solana-pay
- SPL Token Mints:
  - USDC: `EPjFWdd5AufqSSqeM2qN1xzybapC8G4wEGGkZwyTDt1v`
  - USDT: `Es9vMFrzaCERmJfrF4H2FYD4KCoNkY11McCe8BenwNYB`

---

## Phase 4: Dune Query Execution
**Goal:** Run queries and generate CSVs

### Configuration
- Store Dune API key in `.env`
- Store your Dune query IDs in settings or database

### Async Function: `execute_wallet_analysis(order_id)`
1. Get all Dune query IDs for the wallet address
2. Submit each query to Dune API
3. Create DuneQueryJob records for tracking
4. Poll for completion (Dune queries can take minutes)
5. Download CSV results
6. Save to `media/reports/{user_id}/{order_id}/`
7. Create ReportFile records
8. Update order status to completed
9. Send email notification

**Implementation Options:**
1. **Django Q2 Async Task** - Queue the task with `async_task('execute_wallet_analysis', order_id)`
2. **Background Thread** - Spawn a Python thread that polls Dune and updates DB in background

### Error Handling
**CRITICAL:** Dune queries are expensive! No automatic retries to avoid burning credits.

#### Smart Error Classification
Categorize errors to determine if retry is appropriate:

**NEVER Retry (mark as failed_needs_review):**
- `query_error` - Query syntax errors, invalid query structure
- `auth_error` - Dune API authentication failures (API key issues)
- Invalid query ID (query doesn't exist)

**Retryable (with manual admin approval only):**
- `network_error` - Network timeouts, connection failures
- `rate_limit` - Dune API rate limits (backoff required)
- `service_outage` - Dune service unavailable

#### Failure Workflow
1. Catch exception during query execution
2. Classify error type and save to `error_type` field
3. Set status to `failed_needs_review`
4. Send admin notification (email/Slack) with error details
5. Admin reviews error in Django admin
6. Admin manually triggers retry via admin action (if appropriate)
7. Increment `retry_count` on manual retry

#### Pre-flight Validation
Catch issues BEFORE running expensive queries:
- Validate wallet address format (client + server side)
- Verify Dune API key is configured
- Check query IDs exist in configuration
- Optional: Test Dune API health endpoint first

#### Partial Success Handling
Track each DuneQueryJob independently:
- If 3 of 5 queries succeed, don't re-run successful ones
- Check for existing completed queries before executing:
  ```python
  if DuneQueryJob.objects.filter(
      order=order,
      query_name='defi_trades',
      status='completed'
  ).exists():
      skip  # Already have results
  ```
- User receives partial results while failures are reviewed
- Order status reflects partial completion

#### Cost Protection
- Track total Dune credits used (optional monitoring)
- Set daily/weekly budget alerts
- Admin can pause new orders if approaching credit limits
- Log all query executions with timestamps for cost auditing

#### Admin Actions in Django Admin
- **Retry Query** - Manual retry for specific failed query
- **Retry All Failed** - Retry all failed queries for an order
- **Mark as Permanent Failure** - Give up and refund user
- View retry history and error logs

---

## Phase 5: User Interface
**Goal:** Let users submit wallets and track orders

### Pages Needed

#### 1. New Analysis Page (`/analysis/new/`)
- Form with wallet address input
- Client-side validation (valid EVM address)
- Submit → creates order → redirects to payment

#### 2. Payment Page (`/analysis/order/<uuid>/payment/`)
- Display Solana Pay QR code
- Show payment instructions
- Real-time status updates (JavaScript polling every 5 seconds)
- Auto-redirect to dashboard when payment finalized

#### 3. Dashboard (`/dashboard/`)
- List all user's orders
- Show status badges (pending, processing, completed)
- Download buttons for completed reports
- "Analyze New Wallet" button

#### 4. Order Detail Page (`/analysis/order/<uuid>/`)
- Order details (wallet, date, payment status)
- Query execution progress
- List of available reports with download links

### Email Notifications
- Payment received: "We've received your payment!"
- Processing started: "Your reports are being generated..."
- Completed: "Your reports are ready!" (with download links)

---

## Phase 6: File Storage & Downloads
**Goal:** Secure file handling

- Configure MEDIA_ROOT and MEDIA_URL in settings
- Create directory structure: `media/reports/{user_id}/{order_id}/`
- Download view with permission check (users can only download their own files)
- Serve files with proper content-disposition headers

---

## Phase 7: Admin & Monitoring
**Goal:** Manage the system

### Django Admin
- Register all models with good list displays
- Add filters (status, created date, user, error_type)
- Add search (wallet address, user email, transaction signature)
- Custom admin actions (see Phase 4 Error Handling for query retry actions)
- Display error_type and retry_count in DuneQueryJob list view

### Monitoring
- Django Q2 admin integration (view task queue in Django admin)
- Structured logging for all payment verifications and query executions
- Admin can see failed orders and retry

---

## Technical Stack Summary
- **Payments:** Solana Pay (USDC/USDT on Solana)
- **Async:** Django Q2 (uses SQLite as queue, no Redis needed)
- **Dune:** Dune API client
- **Storage:** Local filesystem (media directory)
- **DB:** SQLite (handles scale well for this use case)
- **Email:** Console (dev) → SMTP/SendGrid (prod)
- **Deployment:** Single VPS server ($5 Hetzner), systemd for services

---

## Implementation Order (Suggested)
1. **Infrastructure:** Install Django Q2, configure with SQLite, test simple async task
2. **Models:** Create Django app, define models, migrations
3. **Dune Integration:** Test Dune API connection, execute a query manually
4. **Solana Pay:** Generate payment URL, test on devnet first
5. **Payment Monitoring:** Django Q2 scheduled task or management command to check payments
6. **Query Execution:** Django Q2 async task to run Dune queries
7. **UI:** Build pages one by one, test each flow
8. **Polish:** Emails, error handling, admin improvements

---

## Testing Strategy
- Test each phase independently before moving on
- Use Solana devnet for payment testing initially
- Start with 1 Dune query, then expand to all
- Manual testing of full flow before production
- Keep test wallet addresses handy for E2E testing

---

## Future Enhancements (Post-V1)
- WebSocket for real-time updates (instead of polling)
- Bulk wallet analysis (discount for multiple wallets)
- Report preview (show sample data before download)
- Analytics dashboard for admin
- Automated report deletion after 30 days
- Support for more blockchains (Solana wallets, Bitcoin, etc.)
- API for programmatic access
