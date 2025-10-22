# Solana Pay Integration Plan

## Overview
Integrate Solana Pay to accept USDC/USDT payments for wallet analysis orders. Uses Django backend + CDN JavaScript for frontend wallet interaction.

**Strategy:** Django + CDN JavaScript (no build process)
**Payment Verification:** Immediate verification when user completes payment

---

## Architecture

```
User Browser                    Django Backend              Solana Blockchain
├── Loads payment page     →    ├── Creates order
├── Detects Solana wallet        ├── Generates payment URL
├── Triggers wallet approval     ├── Stores payment record
├── Sends tx signature     →    ├── Verifies signature →    ├── Query transaction
└── Shows confirmation           └── Starts Dune queries     └── Confirm payment
```

**Desktop Flow:**
1. User clicks "Pay with Solana"
2. Phantom/Solflare browser extension popup appears
3. User approves transaction
4. JavaScript sends signature to Django
5. Django verifies on-chain and starts processing

**Mobile Flow:**
1. User clicks "Pay with Solana"
2. Deep link opens Phantom/Solflare mobile app
3. User approves transaction in app
4. Returns to browser
5. JavaScript sends signature to Django
6. Django verifies on-chain and starts processing

---

## Phase 1: Python Utilities (Backend)

### File: `wallet_analysis/solana_utils.py`

**Functions to create:**

#### 1. `generate_solana_pay_url(recipient, amount_usd, token_type='USDC')`
- Converts USD to lamports (USDC/USDT have 6 decimals)
- Generates unique reference UUID
- Builds Solana Pay URL: `solana:<recipient>?amount=X&spl-token=MINT&reference=UUID`
- Returns: `(payment_url, reference_uuid)`

#### 2. `verify_transaction_on_chain(signature, recipient, expected_amount, token_mint, reference)`
- Uses `solana-py` library
- Connects to Solana RPC endpoint
- Fetches transaction by signature
- Verifies:
  - Transaction is confirmed/finalized
  - Recipient address matches
  - Token mint matches (USDC or USDT)
  - Amount matches expected amount
  - Reference UUID is included
- Returns: `True` if valid, `False` otherwise

#### 3. `get_solana_rpc_client()`
- Returns configured Solana RPC client
- Uses RPC URL from environment variable

---

## Phase 2: Views & API Endpoints

### File: `wallet_analysis/views.py`

#### 1. `create_order_view(request)` - Create Order & Payment
- **URL:** `/analysis/new/`
- **Method:** POST
- **Flow:**
  1. Validate wallet address (EVM format: 0x + 40 hex chars)
  2. Create `WalletAnalysisOrder` with status `pending_payment`
  3. Generate Solana Pay URL via `generate_solana_pay_url()`
  4. Create `SolanaPayment` record with:
     - payment_url
     - reference UUID
     - recipient address (from env)
     - amount_expected (25 USDC = 25,000,000 lamports)
     - token_type and token_mint
  5. Redirect to payment page

#### 2. `payment_page_view(request, order_id)` - Display Payment Page
- **URL:** `/analysis/order/<uuid:order_id>/payment/`
- **Method:** GET
- **Flow:**
  1. Fetch order and payment
  2. Check order belongs to logged-in user
  3. Render template with payment details
  4. JavaScript handles wallet interaction

#### 3. `verify_payment_api(request)` - API Endpoint for Payment Verification
- **URL:** `/api/payment-verify/`
- **Method:** POST
- **Request Body:** `{"order_id": "uuid", "signature": "tx_signature"}`
- **Flow:**
  1. Fetch SolanaPayment by order_id
  2. Check payment is still pending
  3. Call `verify_transaction_on_chain()` with signature
  4. If valid:
     - Update payment status to `confirmed`
     - Set `transaction_signature` and `confirmed_at`
     - Update order status to `payment_received`
     - Queue Dune query execution: `async_task('wallet_analysis.tasks.execute_wallet_analysis', order.id)`
  5. Return JSON: `{"success": true/false, "message": "..."}`

#### 4. `payment_status_api(request, order_id)` - Check Payment Status
- **URL:** `/api/payment-status/<uuid:order_id>/`
- **Method:** GET
- **Returns:** `{"status": "pending/confirmed", "order_status": "..."}`
- Used for polling if immediate verification fails

---

## Phase 3: Frontend JavaScript (CDN)

### File: `wallet_analysis/templates/wallet_analysis/payment.html`

**CDN Libraries to load:**
```html
<script src="https://cdn.jsdelivr.net/npm/@solana/web3.js@1.87.0/lib/index.iife.min.js"></script>
```

**Page Elements:**
- Payment amount display ($25 USDC)
- Recipient address (truncated)
- "Pay with Solana" button
- QR code fallback (if no wallet detected)
- Status messages (connecting, confirming, success/error)

**JavaScript Logic:**

1. **Detect Wallet:**
   ```javascript
   const wallet = window.solana || window.phantom?.solana;
   if (!wallet) {
       // Show QR code fallback
   }
   ```

2. **Connect Wallet:**
   ```javascript
   await wallet.connect();
   ```

3. **Create & Send Transaction:**
   ```javascript
   // Build SPL token transfer transaction
   const transaction = new Transaction().add(
       createTransferCheckedInstruction(
           sourceAccount,
           tokenMint,
           destinationAccount,
           owner,
           amount,
           decimals
       )
   );

   // Add reference as instruction (for tracking)
   transaction.add(
       new TransactionInstruction({
           keys: [{ pubkey: referencePublicKey, isSigner: false, isWritable: false }],
           programId: new PublicKey("MemoSq4gqABAXKb96qnH8TysNcWxMyWCqXgDLGmfcHr"),
           data: Buffer.from(""),
       })
   );

   // Request signature from wallet
   const { signature } = await wallet.signAndSendTransaction(transaction);
   ```

4. **Send to Backend for Verification:**
   ```javascript
   const response = await fetch('/api/payment-verify/', {
       method: 'POST',
       headers: { 'Content-Type': 'application/json' },
       body: JSON.stringify({
           order_id: '{{ order.id }}',
           signature: signature
       })
   });

   const result = await response.json();
   if (result.success) {
       // Redirect to dashboard
       window.location.href = '/dashboard/';
   }
   ```

5. **Mobile Deep Link Fallback:**
   ```javascript
   // If mobile and no injected wallet, create deep link
   if (isMobile && !wallet) {
       const deepLink = `https://phantom.app/ul/v1/signAndSendTransaction?...`;
       window.location.href = deepLink;
   }
   ```

---

## Phase 4: Background Task (Fallback)

### File: `wallet_analysis/tasks.py`

#### 1. `check_pending_payments()` - Scheduled Task
- **Schedule:** Every 30 seconds via Django Q2
- **Purpose:** Catch payments that frontend didn't verify (network issues, user closed browser, etc.)
- **Flow:**
  1. Query all `SolanaPayment` with status `pending` and created > 2 minutes ago
  2. For each payment, search blockchain for transactions with matching reference
  3. If found, verify and update status
  4. Trigger Dune queries if valid

**Setup in Django shell:**
```python
from django_q.models import Schedule

Schedule.objects.create(
    name='Check Pending Payments',
    func='wallet_analysis.tasks.check_pending_payments',
    schedule_type=Schedule.MINUTES,
    minutes=0.5  # Every 30 seconds
)
```

#### 2. `execute_wallet_analysis(order_id)` - Async Task
- **Trigger:** When payment is confirmed
- **Flow:**
  1. Update order status to `processing`
  2. Create `DuneQueryJob` records for each query
  3. Execute Dune queries (covered in Phase 6 - Dune Integration)
  4. Download CSV results
  5. Create `ReportFile` records
  6. Update order status to `completed`
  7. Send email notification

---

## Phase 5: Settings & Configuration

### File: `cryptotax/settings.py`

**Add:**
```python
# Media files (user uploads and generated reports)
MEDIA_ROOT = BASE_DIR / 'media'
MEDIA_URL = '/media/'

# Solana Configuration
SOLANA_RPC_URL = os.getenv('SOLANA_RPC_URL', 'https://api.mainnet-beta.solana.com')
SOLANA_RECIPIENT_ADDRESS = os.getenv('SOLANA_RECIPIENT_ADDRESS')
```

### File: `.env`

**Add:**
```bash
SOLANA_RPC_URL=https://api.mainnet-beta.solana.com
SOLANA_RECIPIENT_ADDRESS=YourSolanaWalletAddressHere
```

### File: `cryptotax/urls.py`

**Add:**
```python
from django.conf import settings
from django.conf.urls.static import static

urlpatterns = [
    # ... existing patterns
    path('', include('wallet_analysis.urls')),
]

if settings.DEBUG:
    urlpatterns += static(settings.MEDIA_URL, document_root=settings.MEDIA_ROOT)
```

---

## Phase 6: URL Configuration

### File: `wallet_analysis/urls.py` (new)

```python
from django.urls import path
from . import views

app_name = 'wallet_analysis'

urlpatterns = [
    path('analysis/new/', views.create_order_view, name='create_order'),
    path('analysis/order/<uuid:order_id>/payment/', views.payment_page_view, name='payment_page'),
    path('api/payment-verify/', views.verify_payment_api, name='verify_payment'),
    path('api/payment-status/<uuid:order_id>/', views.payment_status_api, name='payment_status'),
]
```

---

## Additional Dependencies

**Python packages (already installed):**
- ✅ `solana>=0.36.0`
- ✅ `django-q2>=1.8.0`

**Optional (for QR code fallback):**
```bash
pip install qrcode[pil]
```

---

## Testing Strategy

### 1. Solana Devnet Testing
- Use devnet RPC: `https://api.devnet.solana.com`
- Get devnet USDC from faucets
- Test full payment flow without real money

### 2. Frontend Testing
- Test on desktop with Phantom browser extension
- Test on mobile with Phantom mobile app
- Test fallback when no wallet detected

### 3. Payment Verification Testing
- Test valid payments
- Test invalid amounts
- Test wrong token
- Test wrong recipient

---

## Security Considerations

1. **Transaction Verification:**
   - Always verify on-chain, never trust frontend
   - Check amount, token, recipient, reference
   - Ensure transaction is finalized (not just confirmed)

2. **User Authorization:**
   - Verify user owns the order before showing payment page
   - CSRF protection on API endpoints
   - Rate limiting on verification endpoint

3. **Error Handling:**
   - Handle RPC failures gracefully
   - Retry logic for network errors
   - Log all verification attempts

---

## Files to Create/Modify

**New files:**
- ✅ `docs/solana-pay-integration.md` (this file)
- `wallet_analysis/solana_utils.py`
- `wallet_analysis/urls.py`
- `wallet_analysis/tasks.py`
- `wallet_analysis/templates/wallet_analysis/payment.html`
- `wallet_analysis/templates/wallet_analysis/create_order.html`

**Modify:**
- `wallet_analysis/views.py`
- `cryptotax/settings.py`
- `cryptotax/urls.py`
- `.env`

---

## Next Steps After Solana Pay

1. **Dune Analytics Integration** - Execute queries and download CSVs
2. **User Dashboard** - Display orders and download reports
3. **Email Notifications** - Payment received, processing, completed
4. **Admin Interface** - Manage orders, retry failed queries
5. **Testing** - End-to-end testing on devnet

---

## Estimated Timeline

- Phase 1-2 (Backend): ~2-3 hours
- Phase 3 (Frontend): ~2-3 hours
- Phase 4 (Background task): ~1 hour
- Phase 5-6 (Config): ~30 minutes
- Testing: ~2 hours

**Total:** ~8-10 hours of development
